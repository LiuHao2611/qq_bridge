# -*- coding: utf-8 -*-
"""端到端口令测试：真实 nonebot matcher + 真实事件对象，只拦截出站 API。

不加载 qq_bridge 主插件（避免触发 LLM 调用，遵守"禁止自动调用模型"红线）。
同时加载 xiuxian + game_router，验证：
  * 斗破/修仙 现在都用纯中文口令，靠「按用户记录的当前模式」分流
  * 谁发 修仙菜单 → 他后续口令走修仙；发 斗破菜单 → 走斗破（菜单是唯一切换口）
  * 没选模式打口令 → 兜底层提示先发菜单；打错游戏的口令 → 提示切换
  * 两游戏互不抢答（模式对了才命中对应游戏）

数据全部落临时目录，不污染线上存档。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

# 跨平台推导仓库根：本文件位于 <root>/src/plugins/doupo/test_e2e.py，向上 4 级即 root。
# Windows 宿主（D:\AI\manju_agent\qq_bridge）与容器内（/app）通用，避免硬编码路径导致容器里跑不起来。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="doupo_e2e_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(TMP, "doupo.json")
os.environ["XIUXIAN_DATA_PATH"] = os.path.join(TMP, "xiuxian.json")
os.environ["GAME_MODE_PATH"] = os.path.join(TMP, "game_mode.json")
# 日志改道到临时目录：本脚本自己 nonebot.init 且不加载 qq_bridge 插件，
# 没有群聊闸门；若沿用生产 logs/ 会把伪造群事件写进线上 qq_bridge.log。
os.environ["QQ_LOG_DIR"] = os.path.join(TMP, "logs")

import nonebot  # noqa: E402

nonebot.init(driver="~fastapi", onebot_access_token="e2e", log_level="CRITICAL")

from nonebot.adapters.onebot.v11 import Adapter, Bot, Message, MessageSegment  # noqa: E402
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, PrivateMessageEvent  # noqa: E402
from nonebot.message import handle_event  # noqa: E402

driver = nonebot.get_driver()
driver.register_adapter(Adapter)
adapter = next(iter(driver._adapters.values()))

nonebot.load_plugin("src.plugins.xiuxian")
nonebot.load_plugin("src.plugins.doupo")
nonebot.load_plugin("src.plugins.game_router")

SENT = []


class CapBot(Bot):
    async def call_api(self, api, **data):
        SENT.append({"api": api, "data": data})
        return {"message_id": 1}


BOT_ID = "3949742250"
BOT = CapBot(adapter, BOT_ID)
# 必须是**不可能出现在 GROUP_WHITELIST 里**的假群号。这里原本写的是真实群
# 1075851417，导致脚本跑起来时的假事件在日志里和真群消息无法区分
# （2026-11-05 排查时误判成群聊总闸失效）。
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
    try:
        m = sent["data"]["message"]
        return m.extract_plain_text() if hasattr(m, "extract_plain_text") else str(m)
    except Exception:
        return str(sent["data"])


def has_image(sent):
    """回包是否含图片段（自绘卡片走 MessageSegment.image，纯文本提取为空）。"""
    m = (sent.get("data") or {}).get("message")
    if not m:
        return False
    for seg in m:
        if getattr(seg, "type", None) == "image":
            return True
    return False


def image_blobs(sent):
    """取出回包里所有图片段的原始字节（OneBot 用 base64:// 承载）。"""
    import base64

    out = []
    m = (sent.get("data") or {}).get("message")
    if not m:
        return out
    for seg in m:
        if getattr(seg, "type", None) != "image":
            continue
        f = (seg.data or {}).get("file", "")
        if isinstance(f, str) and f.startswith("base64://"):
            try:
                out.append(base64.b64decode(f[len("base64://"):]))
            except Exception:  # noqa: BLE001
                out.append(b"")
        elif isinstance(f, (bytes, bytearray)):
            out.append(bytes(f))
    return out


async def fire(label, event, expect_kw=None, expect_count=1, expect_image=False):
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
    elif expect_image:
        # ⚠️ 不能写成「有图就 pass、没图继续往下走」：expect_kw=None 时那等于
        # 没出图也判成功（假通过）。这里必须显式要求真的发了图片段。
        if not any(has_image(s) for s in SENT):
            ok = False
            detail = f"期望出图但只收到纯文本 {out!r}"
    elif expect_kw and not any(expect_kw in o for o in out):
        ok = False
        detail = f"未见关键字 {expect_kw!r} 实际={out!r}"
    RESULTS.append((label, ok, (out[0] if out else "")[:100] if ok else detail))


async def fire_silent(label, event):
    """断言"不该有斗破特征回包"（闲聊 / 别的游戏的口令 / 错模式）。"""
    SENT.clear()
    err = None
    try:
        await handle_event(BOT, event)
    except Exception:
        err = traceback.format_exc(limit=3)
    out = [text_of(s) for s in SENT]
    # 只认斗破专属特征词，避免兜底层"发送 斗破菜单 查看口令"这种提示被误判为斗破抢答
    doupo_hit = any(
        ("玄重尺" in o) or ("斗气大陆" in o) or ("斗破苍穹" in o) for o in out
    )
    ok = err is None and not doupo_hit
    RESULTS.append((label, ok, "" if ok else f"斗破抢答了：{out!r}"))


async def main():
    A, B, C = 10001, 10002, 10003
    p_mod = sys.modules["src.plugins.doupo"]
    cmds = sys.modules["src.plugins.doupo.commands"]

    # ⚠️ 斗破出图口令**不做任何限流、不做纯文本降级**
    #    （2026-11-07 21:17 用户明确要求：「原本能输出图片的口令，不要降级为纯文字」）。
    #    `_throttle.py` 已从斗破链路移除，这里也不再注入任何 Throttle 替身。
    #    末尾「连续出图」专项用例会在真实 matcher 上连发多条出图口令，每条都必须
    #    真的带 image 段 —— 谁把限流/降级接回来，测试立刻红。

    # --- 静态：matcher 优先级 / 阻断 / 数量 ---
    mats = [v for v in vars(p_mod).values()
            if hasattr(v, "priority") and hasattr(v, "block")]
    pri_ok = all(m.priority == 5 and m.block is True for m in mats)
    want = cmds.menu_total()
    RESULTS.append(("STATIC matcher 数量 = 菜单条数", len(mats) == want,
                    f"count={len(mats)} menu_total={want}"))
    RESULTS.append(("STATIC 全部 priority=5 + block=True", pri_ok,
                    f"n={len(mats)} pri_ok={pri_ok}"))

    # ===== 斗破（群聊，用户 A）=====
    # 先发 斗破菜单 设模式（菜单是唯一切换口；同时出菜单卡）
    await fire("斗破菜单（设模式+菜单）", mk_group(A, "斗破菜单"), None, expect_image=True)
    # 前缀兼容：@机器人 / # / 前导空格（纯中文口令前仍可带这些）。
    # 此刻 A 还没入道，「尚未踏入」恰好证明带前缀的口令确实命中了斗破 matcher
    # ——若 _pat 没吃掉前缀，消息会落到兜底路由，回的是「当前为斗破模式…未识别」。
    await fire("前缀 @机器人（未入道仍命中斗破）", mk_group(A, "人物", prefix_at_bot=True), "尚未踏入")
    await fire("前缀 #（未入道仍命中斗破）", mk_group(A, "#人物"), "尚未踏入")
    await fire("前缀 空格（未入道仍命中斗破）", mk_group(A, "  人物"), "尚未踏入")

    # 未入道：需要角色的口令统一回 NOT_ENTERED
    await fire("未入道 人物", mk_group(A, "人物"), "尚未踏入")
    await fire("未入道 战斗", mk_group(A, "战斗"), "尚未踏入")

    # 入道 → 全流程
    await fire("入道", mk_group(A, "入道"), "玄重尺")
    await fire("人物", mk_group(A, "人物"), None, expect_image=True)
    # 入道后带前缀再走一遍真实业务，确认 _pat 前缀在「有角色」路径下同样生效
    await fire("入道后 前缀 @机器人 → 人物卡", mk_group(A, "人物", prefix_at_bot=True), None, expect_image=True)
    await fire("入道后 前缀 # → 人物卡", mk_group(A, "#人物"), None, expect_image=True)
    await fire("入道后 前缀 空格 → 人物卡", mk_group(A, "  人物"), None, expect_image=True)
    await fire("修炼", mk_group(A, "修炼"), "修为")
    await fire("突破（修为不足）", mk_group(A, "突破"), "修为不足")
    await fire("战斗", mk_group(A, "战斗"), "遭遇")
    await fire("战斗（CD 中）", mk_group(A, "战斗"), "调息")
    await fire("强化", mk_group(A, "强化"), "炎星石")
    await fire("斗技", mk_group(A, "斗技"), None, expect_image=True)
    await fire("升级 龙吟碎", mk_group(A, "升级 龙吟碎"), "焚血散")
    await fire("异火", mk_group(A, "异火"), None, expect_image=True)
    await fire("养火 青莲地心火", mk_group(A, "养火 青莲地心火"), "药渣")
    await fire("宝库", mk_group(A, "宝库"), None, expect_image=True)
    await fire("买 血灵草 2", mk_group(A, "买 血灵草 2"), "血灵草")
    await fire("炼药", mk_group(A, "炼药"), "回灵丹")
    await fire("买 紫叶兰 1", mk_group(A, "买 紫叶兰 1"), "紫叶兰")
    await fire("炼制 回灵丹", mk_group(A, "炼制 回灵丹"), "成功率")
    await fire("背包", mk_group(A, "背包"), None, expect_image=True)
    await fire("服丹 回灵丹", mk_group(A, "服丹 回灵丹"), "回灵丹")
    await fire("任务", mk_group(A, "任务"), "主线")
    await fire("接任务", mk_group(A, "接任务"), "支线")
    await fire("交任务", mk_group(A, "交任务"), "完成")
    await fire("领主线", mk_group(A, "领主线"), "主线")
    await fire("排行", mk_group(A, "排行"), None, expect_image=True)
    # 抢/送 需要一个已入道的对象
    await fire("B 入道", mk_group(B, "入道"), "玄重尺")
    await fire("抢 @某人", mk_group(A, "抢", at=B), "金币")
    await fire("送 100 @某人", mk_group(A, "送 100", at=B), "金币")
    await fire("帮助", mk_group(A, "帮助"), "暴击")

    # prove-it 自举：fire(expect_image=True) 必须真的拦住「只回纯文本却声称出图」。
    # 把 fire 放宽成旧写法，下面这条 STATIC 就会 FAIL —— 防止「假通过」悄悄回归。
    await fire("SELF 假出图", mk_group(A, "帮助"), None, expect_image=True)
    _label, _ok, _detail = RESULTS.pop()
    RESULTS.append(("STATIC expect_image 会拒绝纯文本回复",
                    (not _ok) and "期望出图" in _detail,
                    f"ok={_ok} detail={_detail[:70]}"))

    # ===== 模式切换：A 切到修仙，验证斗破口令不再命中 =====
    await fire("修仙菜单（切到修仙）", mk_group(A, "修仙菜单"), None, expect_image=True)
    await fire("修仙 闭关（切模式后）", mk_group(A, "闭关"), None)  # 应走修仙，非斗破
    # 错模式打斗破口令：当前修仙模式，斗破的「人物」不应命中斗破
    await fire_silent("修仙模式下 人物 不触发斗破", mk_group(A, "人物"))

    # ===== 私聊（用户 C，纯中文）=====
    await fire("私聊 斗破菜单（设模式）", mk_private(C, "斗破菜单"), None, expect_image=True)
    await fire("私聊 入道", mk_private(C, "入道"), "玄重尺")
    await fire("私聊 人物", mk_private(C, "人物"), None, expect_image=True)
    await fire("私聊 修炼", mk_private(C, "修炼"), "修为")
    await fire("私聊 战斗", mk_private(C, "战斗"), "遭遇")
    await fire("私聊 帮助", mk_private(C, "帮助"), "暴击")
    await fire("私聊 抢（无对象）", mk_private(C, "抢"), "用法")
    await fire("私聊 排行（仅自己）", mk_private(C, "排行"), None, expect_image=True)

    # ===== 兜底路由 =====
    await fire("无模式打 闭关 → 提示先选菜单",
               mk_group(10009, "闭关"), "请先发送")
    # 重新给 10009 设斗破模式，再打修仙专属口令 → 提示切换
    await fire("设斗破模式", mk_group(10009, "斗破菜单"), None, expect_image=True)
    await fire("斗破模式下打 闭关（修仙专属） → 提示切换",
               mk_group(10009, "闭关"), "当前为斗破模式")

    # ===== 闲聊 / 修仙口令：斗破一律不抢答（A 当前修仙模式）=====
    await fire_silent("闲聊不误伤", mk_group(A, "今天天气不错啊"))
    for kw in ("闭关", "背包", "排行榜", "商店", "任务", "炼丹", "坊市"):
        await fire_silent(f"修仙口令「{kw}」不触发斗破", mk_group(A, kw))
    await fire_silent("「斗破」二字单独出现不触发", mk_group(A, "我想看斗破苍穹动漫"))

    # ===== 出图口令必须出图：连续多条，任何一条降级纯文字都算 FAIL =====
    # 用户 2026-11-07 21:17：「原本能输出图片的口令，不要降级为纯文字」。
    # 21:16:08 实测：距上一条出图 18 秒命中 25s 冷却，卡片渲染了却只发文字。
    # 下面整串口令全部落在旧 USER_GAP 窗口内，且用户先建号（有角色才能出人物卡），
    # 因此只要有人把限流 / 降级接回链路，这些断言就会立刻失败。
    E = 10021
    await fire("建号 入道 必须出图", mk_private(E, "入道"), None, expect_image=True)
    for kw in ("人物", "斗技", "宝库", "背包", "排行", "异火"):
        SENT.clear()
        await handle_event(BOT, mk_private(E, kw))
        imgs = sum(1 for s in SENT if has_image(s))
        outs = [text_of(s) for s in SENT]
        RESULTS.append((
            f"出图口令 {kw} 必须真的出图（禁止降级纯文字）",
            len(SENT) == 1 and imgs == 1,
            f"回包={len(SENT)} 图片段={imgs} 文本={outs!r}",
        ))

    # ===== 人物卡：只发 1 张「内嵌静态头像」的 PNG，禁止再夹带动图小人 =====
    # 用户 2026-11-07：「人物卡片里不用放动态人物小人了，截图一个头像就行，
    # 放在卡片里」。谁把 avatar_for() 的 GIF 接回 panel、或把头像从卡片里拿掉，
    # 下面这组断言立刻红（不只是看 has_image，而是解出真实图片字节 + 数像素）。
    import io
    import shutil

    from PIL import Image

    # ⚠️ 必须先清 pic 磁盘缓存（cache.py::parents[3] = <root>/src）。
    # 否则有人把模板改成「不画头像」时，render_panel 会命中上一轮全绿留下的
    # 旧 PNG 缓存，像素断言假通过。注入验证 D 实测过这个坑。
    shutil.rmtree(os.path.join(ROOT, "src", "data", "pic_cache"), ignore_errors=True)

    def _head_pixels(png):
        raw = Image.open(io.BytesIO(png)).convert("RGB").crop((28, 96, 124, 192)).tobytes()
        return sum(1 for i in range(0, len(raw), 3)
                   if raw[i] < 120 and raw[i + 1] < 120 and raw[i + 2] < 140)

    for i in range(3):
        SENT.clear()
        await handle_event(BOT, mk_private(E, "人物"))
        blobs = [b for s in SENT for b in image_blobs(s)]
        heads = [b[:6] for b in blobs]
        ok = (len(SENT) == 1 and len(blobs) == 1
              and blobs[0][:8] == b"\x89PNG\r\n\x1a\n"
              and blobs[0][:6] not in (b"GIF87a", b"GIF89a"))
        RESULTS.append((
            f"连续人物 #{i + 1}：只发 1 张 PNG 卡片（不夹带动图）",
            ok,
            f"回包={len(SENT)} 图片数={len(blobs)} 图片头={heads!r}",
        ))
        n = _head_pixels(blobs[0]) if len(blobs) == 1 and blobs[0][:8] == b"\x89PNG\r\n\x1a\n" else 0
        RESULTS.append((
            f"连续人物 #{i + 1}：头像真的画在卡片里（像素级）",
            n > 1500,
            f"头像框深色像素={n}（无头像时应为 0）",
        ))


def _run():
    asyncio.run(main())
    ok = 0
    for name, passed, detail in RESULTS:
        print(("PASS  " if passed else "FAIL  ") + name + ("" if passed else f"  -> {detail}"))
        ok += 1 if passed else 0
    print("=" * 70)
    print(f"E2E_TOTAL={len(RESULTS)} PASSED={ok} FAILED={len(RESULTS) - ok}")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(_run())
