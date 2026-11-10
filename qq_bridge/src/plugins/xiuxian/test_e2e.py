# -*- coding: utf-8 -*-
"""端到端口令测试：真实 nonebot matcher + 真实 bot.py 同款配置，仅拦截出站 API。

不加载 qq_bridge 主插件（避免触发 LLM 调用，遵守"禁止自动调用模型"红线），
priority/block 竞争关系用静态断言验证。
数据落临时目录，不污染线上 xiuxian/data。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

# 跨平台推导仓库根：本文件位于 <root>/src/plugins/xiuxian/test_e2e.py，向上 4 级即 root。
# Windows 宿主（D:\AI\...）与容器内（/app）通用，禁止硬编码盘符路径。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="xiuxian_e2e_")
os.environ["XIUXIAN_DATA_PATH"] = os.path.join(TMP, "xiuxian.json")
# 模式也必须隔离：xiuxian 入口会 set_mode，不改道会写进生产 data/game_mode.json。
os.environ["GAME_MODE_PATH"] = os.path.join(TMP, "game_mode.json")
# 日志改道到临时目录：本脚本自己 nonebot.init 且不加载 qq_bridge 插件，
# 没有群聊闸门；若沿用生产 logs/ 会把伪造群事件写进线上 qq_bridge.log。
os.environ["QQ_LOG_DIR"] = os.path.join(TMP, "logs")

import nonebot

nonebot.init(driver="~fastapi", onebot_access_token="e2e", log_level="CRITICAL")

from nonebot.adapters.onebot.v11 import Adapter, Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, PrivateMessageEvent
from nonebot.message import handle_event

driver = nonebot.get_driver()
driver.register_adapter(Adapter)
adapter = next(iter(driver._adapters.values()))

nonebot.load_plugin("src.plugins.xiuxian")
nonebot.load_plugin("src.plugins.game_router")

SENT = []


class CapBot(Bot):
    async def call_api(self, api, **data):
        SENT.append({"api": api, "data": data})
        return {"message_id": 1}


BOT_ID = "900000009"
BOT = CapBot(adapter, BOT_ID)
# 必须是**不可能出现在 GROUP_WHITELIST 里**的假群号（AGENTS.md §51）。
# 2026-11-05 事故：这里曾误用真实群号，脚本跑起来时假事件在日志里和真群消息
# 无法区分，排查时误判成群聊总闸失效。现改为 9xxxxxxx 假号，真实号只在 .env。
GID = 900000001

RESULTS = []


def mk_group(uid, text, at=None, prefix_at_bot=False):
    msg = Message()
    if prefix_at_bot:
        msg += MessageSegment.at(int(BOT_ID))
        msg += MessageSegment.text(" ")
    if at:
        msg += MessageSegment.at(int(at))
    msg += MessageSegment.text(text)
    return GroupMessageEvent(
        time=1792999900,
        self_id=int(BOT_ID),
        post_type="message",
        sub_type="normal",
        user_id=uid,
        message_type="group",
        message_id=1,
        message=msg,
        original_message=msg,
        raw_message=text,
        font=0,
        sender={"user_id": uid, "nickname": f"道友{uid % 10000}", "card": ""},
        group_id=GID,
        to_me=prefix_at_bot,
    )


def mk_private(uid, text):
    msg = Message(MessageSegment.text(text))
    return PrivateMessageEvent(
        time=1792999900,
        self_id=int(BOT_ID),
        post_type="message",
        sub_type="friend",
        user_id=uid,
        message_type="private",
        message_id=1,
        message=msg,
        original_message=msg,
        raw_message=text,
        font=0,
        sender={"user_id": uid, "nickname": f"道友{uid % 10000}"},
        to_me=True,
    )


def text_of(sent):
    """取发出消息的纯文本。"""
    try:
        m = sent["data"]["message"]
        return m.extract_plain_text() if hasattr(m, "extract_plain_text") else str(m)
    except Exception:
        return str(sent["data"])


def img_file(sent):
    """取发出消息里第一个图片段的 file 字段（无图返回 None）。

    状态卡 / 菜单卡走 MessageSegment.image(bytes)，onebot v11 适配器会把它转成
    ``base64://...``；取不到或过短即视为"没真发图"。
    """
    try:
        for seg in sent["data"]["message"]:
            if getattr(seg, "type", "") == "image":
                return str(seg.data.get("file") or "")
        return None
    except Exception:
        return None


async def fire(label, event, expect_kw=None, expect_count=1):
    SENT.clear()
    err = None
    try:
        await handle_event(BOT, event)
    except Exception:
        err = traceback.format_exc(limit=3)
    out = [text_of(s) for s in SENT]
    ok = err is None
    detail = ""
    if not ok:
        detail = "EXC: " + (err or "").strip().splitlines()[-1]
    elif len(out) != expect_count:
        ok = False
        detail = f"SENT={len(out)} 期望 {expect_count}"
    elif expect_kw and not any(expect_kw in o for o in out):
        ok = False
        detail = f"未见关键字 {expect_kw!r} 实际={out!r}"
    RESULTS.append((label, ok, (out[0] if out else "")[:110] if ok else detail))


async def fire_img(label, event):
    """图片类回复：断言恰好 1 条消息，且真的带了一张非空图片。

    状态卡 / 菜单卡优先发图（渲染失败才回纯文本），所以这里不能再用关键字断言
    ——改成"有没有真发图、图是不是空的"，比原来更贴近真实链路。
    """
    SENT.clear()
    err = None
    try:
        await handle_event(BOT, event)
    except Exception:
        err = traceback.format_exc(limit=3)
    if err is not None:
        RESULTS.append((label, False, "EXC: " + err.strip().splitlines()[-1]))
        return
    if len(SENT) != 1:
        RESULTS.append((label, False, f"SENT={len(SENT)} 期望 1"))
        return
    f = img_file(SENT[0])
    if not f:
        RESULTS.append((label, False, f"未发出图片，实际文本={text_of(SENT[0])!r}"))
    elif len(f) < 4096:
        RESULTS.append((label, False, f"图片过小({len(f)}B)，疑似空图"))
    else:
        RESULTS.append((label, True, f"image {len(f)}B"))


async def main():
    A, B = 10001, 10002
    p_mod = sys.modules["src.plugins.xiuxian"]
    store = p_mod.store

    # --- 静态：matcher 优先级与阻断 ---
    mats = [v for v in vars(p_mod).values() if hasattr(v, "priority") and hasattr(v, "block")]
    pri_ok = all(m.priority == 5 and m.block is True for m in mats)
    # matcher 数量必须等于菜单里的口令条数（加了口令却忘了注册/忘了写菜单都会红）
    want = sys.modules["src.plugins.xiuxian.commands"].menu_total()
    RESULTS.append(("STATIC matcher 数量/优先级", pri_ok and len(mats) == want,
                    f"count={len(mats)} menu_total={want} all_pri5_blockTrue={pri_ok}"))

    # 菜单卡与玩法卡必须是两张不同的图（防止有人把两个口令指到同一模板）
    card_mod = sys.modules["src.plugins.xiuxian.card"]
    bm = await card_mod.render_menu()
    bh = await card_mod.render_help()
    RESULTS.append(("STATIC 菜单卡≠玩法卡", bool(bm) and bool(bh) and bm != bh,
                    f"menu={len(bm or b'')}B help={len(bh or b'')}B"))

    # 修为溢出（exp > need）必须被识别成「可突破」；进度条渐变必须「左浅右深」。
    # 后者只能靠像素验证 —— 2026-10-26 用户截图报障：满格时条形右端变浅，看着像没填满，
    # 根因是 canvas.linear_gradient(horizontal=True) 实际「左=c2、右=c1」（见 widgets.py 坑 1）。
    from io import BytesIO
    from PIL import Image

    def _bar_ends(png):
        im = Image.open(BytesIO(png)).convert("RGB")
        w, h = im.size
        yy = max(range(int(h * 0.28), int(h * 0.45)),
                 key=lambda y: max(im.getpixel((40, y))) - min(im.getpixel((40, y))))
        return im.getpixel((40, yy)), im.getpixel((w - 40, yy))

    cfgx = sys.modules["src.plugins.xiuxian.config"]
    base_p = {"qq": "1", "name": "测试", "realm": 2, "root": "单灵根", "root_mult": 1.3,
              "ling_shi": 50, "age": 16, "life": 399, "atk": 73, "def": 62,
              "wins": 0, "losses": 0, "sect": None, "inv": {}, "secluding": False,
              "level_up_cd": 0, "bt_bonus": 0.0}
    # 突破门槛 = 下一境界（realm 3）的修为需求；满格数据取 need+40，中间态取 need/4
    need_next = cfgx.REALMS[3]["exp_to_next"]
    d_full = card_mod.build_data(dict(base_p, exp=need_next + 40))
    d_mid = card_mod.build_data(dict(base_p, exp=need_next // 4))
    RESULTS.append(("STATIC 修为溢出=可突破",
                    d_full["can_break"] and not d_mid["can_break"]
                    and d_full["next_realm"] == cfgx.REALMS[3]["name"],
                    f"can_break={d_full['can_break']}/{d_mid['can_break']} "
                    f"next={d_full['next_realm']} need={need_next}"))
    png_full = await card_mod._render_card("xiuxian", d_full, "自检")
    lc, rc = _bar_ends(png_full)
    RESULTS.append(("STATIC 进度条左浅右深", sum(lc) > sum(rc) + 10,
                    f"left={lc} right={rc}"))

    # --- 1 入道 ---
    await fire_img("我要修仙(首次)", mk_group(A, "我要修仙"))
    await fire_img("我要修仙(重复)", mk_group(A, "我要修仙"))
    # --- 2 状态 ---
    await fire_img("我的修仙", mk_group(A, "我的修仙"))
    await fire("我的修仙(未入道)", mk_group(99999, "我的修仙"), "尚未入道")
    # --- 2b 菜单（口令索引）vs 玩法（规则细则）：两张卡，别名各归各的 ---
    await fire_img("修仙菜单", mk_group(A, "修仙菜单"))
    await fire_img("修仙口令(别名)", mk_group(99999, "修仙口令"))
    # 帮助类口令已按「只有菜单做区分」改成纯中文，别名不再带「修仙」前缀
    await fire_img("帮助", mk_group(A, "帮助"))
    await fire_img("玩法(别名·@bot)", mk_group(A, "玩法", prefix_at_bot=True))
    await fire_img("攻略(别名)", mk_group(A, "攻略"))
    # --- 3 闭关/出关 ---
    await fire("闭关", mk_group(A, "闭关"), "盘膝入定")
    await fire("闭关(重复)", mk_group(A, "闭关"), "已在闭关中")
    await fire("出关", mk_group(A, "出关"), "出关！")
    await fire("出关(未闭关)", mk_group(A, "出关"), "并未闭关")
    # --- 4 突破（修为不足）---
    await fire("突破(修为不足)", mk_group(A, "突破"), "修为不足")
    # --- 5 机缘 ---
    await fire("领取机缘", mk_group(A, "领取机缘"), "灵石")
    await fire("领取机缘(冷却)", mk_group(A, "领取机缘"), "机缘未到")
    # --- 6 秘境（限时秘境：先开启，再报名自动探索 / 查进度）---
    await store.set_world(str(GID), {"secret": p_mod.core.secret_new()})
    await fire("秘境(报名自动探索)", mk_group(A, "秘境"), "自动探索")
    await fire("秘境(查进度)", mk_group(A, "秘境"), "探索中")
    # --- 6b 悬赏（搬开源悬赏令：看榜 / 接取 / 未到期 / 结算 / 无任务）---
    await fire("悬赏(榜单)", mk_group(A, "悬赏"), "悬赏榜")
    await fire("接悬赏(不存在)", mk_group(A, "接悬赏 没有这个"), "没有叫")
    await fire("接悬赏 玄冰花", mk_group(A, "接悬赏 玄冰花"), "玄冰花")
    await fire("接悬赏(占用中)", mk_group(A, "接悬赏 寻找玉髓芝"), "只能接一个")
    await fire("交悬赏(未到期)", mk_group(A, "交悬赏"), "还没到期")
    # 「交悬赏」必须精确匹配，不能被「悬赏」抢走（否则永远只回榜单）
    await fire("悬赏(不吃掉交悬赏)", mk_group(A, "#交悬赏"), "还没到期")
    # --- 7 B 入道 ---
    await fire_img("我要修仙(玩家B)", mk_group(B, "我要修仙"))
    # --- 8 切磋/抢/偷/送 ---
    await fire("切磋@B", mk_group(A, "切磋", at=B), "战力")
    await fire("切磋(无@)", mk_group(A, "切磋"), "需 @")
    await fire("抢灵石@B", mk_group(A, "抢灵石", at=B), "灵石")
    await fire("抢灵石(无@)", mk_group(A, "抢灵石"), "需 @")
    await fire("偷灵石@B", mk_group(A, "偷灵石", at=B), "灵石")
    await fire("偷灵石(无@)", mk_group(A, "偷灵石"), "需 @")
    await fire("送灵石 10 @B", mk_group(A, "送灵石 10", at=B), "赠予")
    await fire("送灵石(无数额)", mk_group(A, "送灵石", at=B), "用法")
    # --- 8b @机器人 + @对方 混合（真实群常见用法）---
    await fire("@机器人 抢灵石@B", mk_group(A, "抢灵石", at=B, prefix_at_bot=True), "灵石")
    await fire("@机器人 送灵石 5 @B", mk_group(A, "送灵石 5", at=B, prefix_at_bot=True), "赠予")
    # --- 9 排行榜 ---
    await fire("排行榜", mk_group(A, "排行榜"), "战力榜")
    # --- 10 宗门 ---
    await fire("创建宗门 青云门", mk_group(A, "创建宗门 青云门"), "青云门")
    await fire("创建宗门(重复)", mk_group(A, "创建宗门 二号门"), "已有宗门")
    await fire("宗门(信息)", mk_group(A, "宗门"), "成员")
    await fire("加入宗门 青云门(B)", mk_group(B, "加入宗门 青云门"), "加入宗门")
    await fire("加入宗门(重复B)", mk_group(B, "加入宗门 青云门"), "已有宗门")
    await fire("宗门战(敌方不存在)", mk_group(A, "宗门战 不存在门"), "未找到")
    await fire("宗门战(无参数)", mk_group(A, "宗门战"), "用法")
    # --- 11 世界Boss ---
    await fire("世界boss(无Boss)", mk_group(A, "世界boss"), "无世界Boss")
    import importlib
    cfg_mod = sys.modules["src.plugins.xiuxian.config"]
    core_mod = sys.modules["src.plugins.xiuxian.core"]
    world = await store.get_world(str(GID))
    world["boss"] = core_mod.spawn_boss(2)
    # 夹具固定血量：旧写法「固定打 30 次赌随机伤害能击杀」是 flaky
    # （2026-11-07 实测 30 次后 Boss 仍剩 44 血，连带下一条断言也跟着错）。
    # 血量设 1 → 一次讨伐必走真实的「击杀 → 瓜分奖池 → 清空 Boss」链路，不赌随机数。
    world["boss"]["hp"] = 1
    await store.set_world(str(GID), world)
    await fire("世界boss(有Boss)", mk_group(A, "世界boss"), "气血剩余")
    await fire("讨伐boss(击杀→瓜分奖池)", mk_group(A, "讨伐boss"), "伏诛")
    await fire("讨伐boss(无Boss)", mk_group(A, "讨伐boss"), "暂无世界Boss")
    # --- 12 前缀兼容 ---
    await fire_img("# 前缀 #我要修仙", mk_group(B, "#我要修仙"))
    await fire_img("@机器人 我要修仙", mk_group(B, "我要修仙", prefix_at_bot=True))
    await fire_img("@机器人 #我要修仙", mk_group(B, "#我要修仙", prefix_at_bot=True))
    # --- 13 私聊拒绝 ---
    await fire("私聊·我的修仙", mk_private(A, "我的修仙"), "群聊")
    # --- 14 模式隔离：菜单是唯一切换口 ---
    # 没选过模式的人打口令，兜底路由应提示先发菜单；发过「修仙菜单」后同一口令正常走修仙。
    await fire("无模式打 排行榜 → 提示先选菜单", mk_group(77777, "排行榜"), "请先发送")
    await fire_img("发 修仙菜单 设模式", mk_group(77777, "修仙菜单"))
    await fire("设模式后 排行榜 正常走修仙", mk_group(77777, "排行榜"), "战力榜")
    # --- 15 闲聊不劫持 ---
    await fire("闲聊(不应命中)", mk_group(A, "今天天气不错"), expect_count=0)

    # ---- 汇总 ----
    print("\n" + "=" * 78)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for label, ok, info in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {label:26s} | {info}")
    print("=" * 78)
    print(f"E2E_TOTAL={len(RESULTS)} PASSED={passed} FAILED={len(RESULTS) - passed}")
    print(f"DATA_PATH={os.environ['XIUXIAN_DATA_PATH']}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
