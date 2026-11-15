# -*- coding: utf-8 -*-
"""斗破菜单【全口令】严格验收：真实 matcher + 真实事件，只拦出站 API。

为什么需要这一套（与 test_e2e.py 的分工）
------------------------------------------------------------------
test_e2e.py 验的是分流 / 优先级 / 出图不降级，但它的文本断言偏松：
例如「交任务」只要求回包含「完成」，而「尚未完成」四个字里就有「完成」，
失败路径会假通过。本文件把菜单里的每一条口令都当成独立用例，要求：

  1. 恰好 1 条回包（不静默、不重复）；
  2. 回包是**该功能的正向结果**（不是「用法：」/「不足」/「未识别」/兜底提示）；
  3. 文本无格式异常（异常栈、未替换占位符、None/nan、乱码、超长行、空回复）；
  4. 出图口令必须真的发图片，且图片是合法的、非空白的 PNG/GIF；
     纯文本口令绝不能夹带图片段；
  5. 13 阶原著境界名各渲染一张人物卡，验证长名字不会画崩。

为满足第 2 点，每条口令执行前会注入「刚好够成功」的最小前置状态（富养），
让 买/炼制/服丹/升级/养火/突破/交任务/领主线 都走成功分支；
随机的胜败 / 炼丹成败仍如实接受两种结果，但必须是明确的结果播报。

出图红线（用户 2026-11-07 21:17）：原本出图的口令禁止降级纯文字、禁止限流、
禁止静默。_throttle.py 已停用，不得重新接回链路。

数据全部改道临时目录，不污染线上存档与日志。卡片导出到 <root>/tmp/doupo_menu_cards/
供视觉核对（容器内 /app/tmp 即宿主 ./tmp）。exit 0 = 全绿。
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="doupo_menu_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(TMP, "doupo.json")
os.environ["XIUXIAN_DATA_PATH"] = os.path.join(TMP, "xiuxian.json")
os.environ["GAME_MODE_PATH"] = os.path.join(TMP, "game_mode.json")
os.environ["QQ_LOG_DIR"] = os.path.join(TMP, "logs")
# 机遇是概率事件，会让严格回包断言变 flaky；专项验收见 test_chance.py
os.environ["DOUPO_CHANCE_RATE"] = "0"

OUT_DIR = os.path.join(ROOT, "tmp", "doupo_menu_cards")
os.makedirs(OUT_DIR, exist_ok=True)

import nonebot  # noqa: E402

nonebot.init(driver="~fastapi", onebot_access_token="e2e", log_level="CRITICAL")

from nonebot.adapters.onebot.v11 import Adapter, Bot, Message, MessageSegment  # noqa: E402
from nonebot.adapters.onebot.v11.event import GroupMessageEvent  # noqa: E402
from nonebot.message import handle_event  # noqa: E402
from PIL import Image  # noqa: E402

driver = nonebot.get_driver()
driver.register_adapter(Adapter)
adapter = next(iter(driver._adapters.values()))
nonebot.load_plugin("src.plugins.xiuxian")
nonebot.load_plugin("src.plugins.doupo")
nonebot.load_plugin("src.plugins.game_router")

cfg = sys.modules["src.plugins.doupo.config"]
core = sys.modules["src.plugins.doupo.core"]
cmds = sys.modules["src.plugins.doupo.commands"]
card = sys.modules["src.plugins.doupo.card"]
p_mod = sys.modules["src.plugins.doupo"]
store = p_mod.store

SENT = []
RESULTS = []
REPORT = []

BOT_ID = "900000009"
BOT = None
GID = 900000001
A, B = 10001, 10002

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGICS = (b"GIF87a", b"GIF89a")


class CapBot(Bot):
    async def call_api(self, api, **data):
        SENT.append({"api": api, "data": data})
        return {"message_id": 1}


def mk_group(uid, text, at=None):
    msg = Message()
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
        to_me=False,
    )


def text_of(sent):
    try:
        m = sent["data"]["message"]
        return m.extract_plain_text() if hasattr(m, "extract_plain_text") else str(m)
    except Exception:  # noqa: BLE001
        return str(sent["data"])


def image_blobs(sent):
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


# ---------------------------------------------------------------------------
# 文本格式检查
# ---------------------------------------------------------------------------
BAD_RULES = (
    ("异常栈", re.compile(r"Traceback|KeyError|TypeError|AttributeError|NameError|ValueError|IndexError")),
    ("None", re.compile(r"\bNone\b")),
    ("未替换占位符", re.compile(r"\{[^{}]{1,80}\}")),
    ("printf 残留", re.compile(r"%[sdrf]\b")),
    ("乱码替换符", re.compile("\ufffd")),
    ("HTML 残留", re.compile(r"</?[A-Za-z][A-Za-z0-9]*>")),
    ("nan/inf", re.compile(r"\bnan\b|\binf\b")),
    ("兜底/降级提示", re.compile(r"未识别该口令|请先发送|渲染异常|降级纯文本")),
)

# ⚠️「用法：」不能进全局禁用：炼药 / 宝库 / 斗技 / 帮助 / 菜单这类**展示型**口令
#    末尾本来就带一行「用法：xxx」的正常引导，不是失败提示。它只对「带参数且已备好
#    资源」的用例禁用（见 run_case），否则会把正确回复误判成失败。
GLOBAL_BAN = ("未识别", "请先发送", "尚未踏入", "无此", "不足：", "没有在做的")


def check_text(txt):
    """返回格式异常列表。空文本单独由调用方按用例判定。"""
    errs = []
    if "\r" in txt:
        errs.append("含 CR 行尾")
    for name, rx in BAD_RULES:
        if rx.search(txt):
            errs.append(name)
    for ln in txt.split("\n"):
        if len(ln) > 160:
            errs.append(f"超长行({len(ln)}字)")
            break
    if "\n\n\n\n" in txt:
        errs.append("连续 4 空行")
    return errs


def inspect_image(blob, kind):
    """返回 (错误列表, 描述字符串)。"""
    errs = []
    if kind == "png" and blob[:8] != PNG_MAGIC:
        errs.append("PNG 魔数不对")
    if kind == "gif" and blob[:6] not in GIF_MAGICS:
        errs.append("GIF 魔数不对")
    if kind not in ("png", "gif") and not (blob[:8] == PNG_MAGIC or blob[:6] in GIF_MAGICS):
        errs.append("非法图片头")
    desc = f"{kind} {len(blob)}B"
    try:
        im = Image.open(io.BytesIO(blob))
        im.load()
        w, h = im.size
        desc = f"{kind} {w}x{h} {len(blob)}B"
        if w < 60 or h < 60:
            errs.append(f"尺寸过小 {w}x{h}")
        if kind == "png" and w < 560:
            errs.append(f"卡片宽度异常 {w}")
        if kind == "png" and h < 240:
            errs.append(f"卡片高度异常 {h}")
        small = im.convert("RGB").resize((48, 48))
        raw = small.tobytes()
        colors = len({raw[i:i + 3] for i in range(0, len(raw), 3)})
        desc += f" colors={colors}"
        if colors < 12:
            errs.append(f"疑似空白卡 colors={colors}")
    except Exception as e:  # noqa: BLE001
        errs.append(f"PIL 打不开: {type(e).__name__}: {e}")
    return errs, desc


def _pair_gaps(blocks):
    """从文字行块里挑出「主标题块 + 紧随其后的说明块」，返回两者间的空白行数。"""
    gaps = []
    for a, b in zip(blocks, blocks[1:]):
        # 主标题 FS_ITEM=20（约 18~20 行墨迹），说明 FS_SMALL=15（约 13~15 行）
        if a[2] >= 16 and 10 <= b[2] <= 16:
            gaps.append(b[0] - a[1])
    return gaps


def check_list_spacing(png_path):
    """像素级验证：列表卡主标题与副行说明之间必须真的有空白行，不能粘成一块。

    2026-11-14 视觉验收发现修复前 cy 只比标题底部多约 0.5px，斗技/宝库卡上
    两行字几乎连在一起。这里逐行统计左侧文本区的深色像素，找出文字块并量Gap。
    """
    im = Image.open(png_path).convert("RGB")
    px = im.load()
    w, h = im.size
    ink = [sum(1 for x in range(40, 320) if max(px[x, y]) < 150) for y in range(h)]
    blocks = []
    y = 0
    while y < h:
        if ink[y] > 2:
            s = y
            while y < h and ink[y] > 2:
                y += 1
            if y - s >= 8 and s > 105:  # 只看内容区，过滤 1~2px 的分割线
                blocks.append((s, y, y - s))
        else:
            y += 1
    gaps = _pair_gaps(blocks)
    ok = len(gaps) >= 4 and min(gaps) >= 6
    return ok, f"配对数={len(gaps)} 标题/说明空白行={gaps}"


# ---------------------------------------------------------------------------
# 前置状态注入：让每条口令都走在「能成功」的分支上
# ---------------------------------------------------------------------------
def _p_break(p):
    p["exp"] = cfg.exp_need(int(p["lv"])) + 100


def _p_fight(p):
    p["last_fight"] = 0


def _p_enhance(p):
    core.add_item(p, "炎星石", 500)


def _p_skillup(p):
    core.add_item(p, cfg.SKILL_UP_ITEM, 500)


def _p_feed(p):
    core.add_item(p, "药渣", 100000)


def _p_buy(p):
    p["gold"] = 1000000


def _p_craft(p):
    p["last_craft"] = 0
    for k, v in cfg.RECIPE_BY_NAME["回灵丹"]["need"].items():
        core.add_item(p, k, v * 3)


def _p_use(p):
    core.add_item(p, "回灵丹", 2)
    p["hp"] = 1


def _p_taccept(p):
    p["task"] = None


def _p_tsubmit(p):
    if not p.get("task"):
        core.task_accept(p, rng=lambda: 0.0)
    p["task"]["progress"] = int(p["task"]["target"])


def _p_main(p):
    m = core.current_main(p)
    if m:
        p["main_progress"] = int(m["target"])


def _p_rob(p):
    p["gold"] = max(int(p.get("gold", 0)), 100000)


def _p_give(p):
    p["gold"] = 1000000


PREPS = {
    "break": _p_break, "fight": _p_fight, "enhance": _p_enhance,
    "skillup": _p_skillup, "feed": _p_feed, "buy": _p_buy,
    "craft": _p_craft, "use": _p_use, "taccept": _p_taccept,
    "tsubmit": _p_tsubmit, "main": _p_main, "rob": _p_rob, "give": _p_give,
}


# ---------------------------------------------------------------------------
# 菜单 29 条：逐条定义「正确回复」的语义
#   img  = png / gif / None（None = 必须是纯文本）
#   must = 至少命中一个（正向结果词，不用「完成」这类会被失败路径命中的模糊词）
#   ban  = 一个都不能出现
# ---------------------------------------------------------------------------
CASES = [
    dict(id="01_menu", text="斗破菜单", img="png", must=[], ban=[]),
    dict(id="02_enter", text="入道", img="gif", must=["玄重尺"], ban=[]),
    dict(id="03_panel", text="人物", img="png", must=[], ban=[]),
    dict(id="04_help", text="帮助", img=None, must=["暴击", "强化"], ban=[]),
    dict(id="05_cult", text="修炼", img=None, must=["修为"], ban=[]),
    dict(id="06_break", text="突破", img=None, must=["突破成功", "境界突破", "成就【斗帝】"],
         ban=["修为不足"], prep="break"),
    dict(id="07_fight", text="战斗", img=None, must=["遭遇"], ban=["调息"], prep="fight"),
    dict(id="08_enh", text="强化", img=None, must=["强化成功"], ban=["炎星石不足"], prep="enhance"),
    dict(id="09_skills", text="斗技", img="png", must=[], ban=[]),
    dict(id="10_skillup", text="升级 龙吟碎", img=None, must=["升至"],
         ban=["不足", "已至"], prep="skillup"),
    dict(id="11_flames", text="异火", img="png", must=[], ban=[]),
    dict(id="12_feed", text="养火 青莲地心火", img=None, must=["晋为", "投入"],
         ban=["没有药渣"], prep="feed"),
    dict(id="13_shop", text="宝库", img="png", must=[], ban=[]),
    dict(id="14_buy", text="买 血灵草 2", img=None, must=["购入"],
         ban=["金币不足", "没有【"], prep="buy"),
    dict(id="15_recipes", text="炼药", img=None, must=["炼药师", "丹药"], ban=[]),
    dict(id="16_craft", text="炼制 回灵丹", img=None, must=["丹成", "炼制失败"],
         ban=["材料不足", "丹炉尚温"], prep="craft"),
    dict(id="17_bag", text="背包", img="png", must=[], ban=[]),
    dict(id="18_use", text="服丹 回灵丹", img=None, must=["服下"],
         ban=["你没有", "无法服用"], prep="use"),
    dict(id="19_tasks", text="任务", img=None, must=["【主线"], ban=[]),
    dict(id="20_taccept", text="接任务", img=None, must=["接取支线"],
         ban=["手上还有", "未交"], prep="taccept"),
    dict(id="21_tsubmit", text="交任务", img=None, must=["支线【"],
         ban=["尚未完成", "没有在做"], prep="tsubmit"),
    dict(id="22_main", text="领主线", img=None, must=["主线【"],
         ban=["主线未完成"], prep="main"),
    dict(id="23_rank", text="排行", img="png", must=[], ban=[]),
    dict(id="24_rob", text="抢", at=B, img=None, must=["夺得", "反被击退"],
         ban=["囊中空空", "对方尚未入道"], prep="rob"),
    dict(id="25_give", text="送 100", at=B, img=None, must=["赠予"],
         ban=["对方尚未入道", "金币不足"], prep="give"),
    # 26/27 是 2026-11-15 新增：斗技详情（完整说明）与四槽上阵
    dict(id="26_skillinfo", text="斗技详情 狂狮怒罡", img=None,
         must=["出处：", "不属于你的宗门"], ban=[]),
    dict(id="27_loadout", text="上阵", img=None, must=["当前上阵"], ban=[]),
    # 28/29：宗门查看与转宗（2026-11-15 补，进阶池/隐世池 17 派的入口）
    # 28 同日卡片化（doupo_faction 模板）：宗门是基本固定内容，必须出卡片，
    # 断言改为「返回 PNG 图片」；纯文本只作为渲染异常时的降级兜底。
    dict(id="28_sect", text="宗门", img="png", must=[], ban=[]),
    dict(id="29_switch", text="转宗 云岚宗", img=None,
         must=["已转投", "你已在", "没有这个宗门", "尚无"], ban=[]),
]


async def prepare(case):
    p = await store.get_player(str(A))
    if not p:
        return
    fn = PREPS.get(case.get("prep"))
    if fn:
        fn(p)
        await store.put_player(p)


def record(name, ok, detail):
    RESULTS.append((name, ok, detail))
    REPORT.append(("PASS  " if ok else "FAIL  ") + name + ("  | " + detail if detail else ""))


async def run_case(case):
    label = f"{case['id']} {case['text']}"
    await prepare(case)
    SENT.clear()
    err = None
    try:
        await handle_event(BOT, mk_group(A, case["text"], at=case.get("at")))
    except Exception:  # noqa: BLE001
        err = traceback.format_exc(limit=3)

    if err:
        record(label, False, "EXC " + err.strip().splitlines()[-1])
        return
    if len(SENT) != 1:
        record(label, False, f"回包数={len(SENT)}（应恰好 1 条）")
        return

    sent = SENT[0]
    blobs = image_blobs(sent)
    txt = text_of(sent)
    errs = []
    desc = []

    # --- 图片形态 ---
    if case["img"]:
        if len(blobs) != 1:
            errs.append(f"出图口令图片段数={len(blobs)}（应为 1，禁止降级纯文字）")
        else:
            ie, d = inspect_image(blobs[0], case["img"])
            errs += ie
            desc.append(d)
            ext = "png" if case["img"] == "png" else "gif"
            with open(os.path.join(OUT_DIR, case["id"] + "." + ext), "wb") as f:
                f.write(blobs[0])
    elif blobs:
        errs.append(f"纯文本口令夹带了 {len(blobs)} 个图片段")

    # --- 文本形态 ---
    if not case["img"] and not txt.strip():
        errs.append("纯文本回包为空")
    if txt:
        errs += ["文本" + e for e in check_text(txt)]
    bans = list(GLOBAL_BAN) + list(case["ban"])
    if case.get("prep"):
        # 带参数且已注入成功前置状态的口令，不该回落到「用法：」提示。
        bans.append("用法：")
    for word in bans:
        if word in txt:
            errs.append(f"出现兜底/失败提示 {word!r}")
    if case["must"] and not any(w in txt for w in case["must"]):
        errs.append(f"未见正向结果词 {case['must']!r}")

    detail = " ".join(desc) + (" | " if desc else "") + txt.replace("\n", " / ")[:150]
    record(label, not errs, ("；".join(errs) + " || " if errs else "") + detail)
    REPORT.append("    REPLY: " + (txt.replace("\n", " / ") or "<无文字，仅图片>")[:220])


async def static_checks():
    # 菜单唯一源 = 29 条，且每条口令都要出现在纯文本菜单里（格式无缺失）
    menu_txt = cmds.menu_text()
    total = cmds.menu_total()
    ok = total == len(CASES)
    record("STATIC 菜单条数 = 验收用例数", ok, f"menu_total={total} cases={len(CASES)}")
    fe = check_text(menu_txt)
    record("STATIC 菜单纯文本格式", not fe, "；".join(fe) or f"{len(menu_txt)} 字")
    missing = [c["text"].split()[0] for c in CASES
               if c["text"].split()[0] not in menu_txt and c["text"] not in menu_txt]
    record("STATIC 每条口令都写进了菜单", not missing, f"缺失={missing}")

    # out picture commands must stay unthrottled
    record("STATIC 未接回 _throttle（出图红线）",
           "_throttle" not in open(os.path.join(ROOT, "src", "plugins", "doupo", "__init__.py"),
                                   encoding="utf-8").read(),
           "_throttle.py 不得重新接入游戏口令链路")

    # 13 阶改造后的文案漂移：菜单/帮助不许再写旧数值，否则「回复内容不正确」
    stale = ("一星斗者，赠", "阶 =(等级-1)/9", "28 级冲斗帝", "28 级 -> 29 级")
    blob = cmds.menu_text() + "\n" + cmds.cmd_help()
    hit = [s for s in stale if s in blob]
    record("STATIC 菜单/帮助无 13 阶遗留旧文案", not hit, f"残留={hit}")

    # lv82 是「一转斗尊巅峰」，形象说明不能写成「斗帝」
    avatar_mod = sys.modules["src.plugins.doupo.avatar"]
    cap82 = avatar_mod.headshot_caption(82)
    record("STATIC 头像称号不冒充斗帝", "斗帝" not in cap82, f"lv82 caption={cap82!r}")


async def realm_cards():
    """13 阶原著境界名各渲染一张人物卡：长名字不能画崩、不能出空白卡。"""
    base = await store.get_player(str(A))
    if not base:
        record("REALM 13 阶人物卡", False, "没有基础玩家")
        return
    for i, (name, cnt, subs, fmt) in enumerate(cfg.REALM_TABLE):
        lv = sum(t[1] for t in cfg.REALM_TABLE[:i]) + 1
        p2 = dict(base)
        p2.update({"lv": lv, "exp": 0, "lv_ver": 3, "name": "测试道友"})
        # 只改 lv 不改气血会让导出卡看着像「残血」，那是测试数据假象；
        # 按新等级补满，视觉核对才是在看真正的排版问题。
        p2["hp"] = core.stats(p2)["体力"]
        p2["mp"] = core.mp_max(p2)
        label = f"REALM {i:02d} lv{lv} {cfg.full_realm(lv)}"
        try:
            png = await card.render_panel(p2)
        except Exception as e:  # noqa: BLE001
            record(label, False, f"渲染抛异常 {type(e).__name__}: {e}")
            continue
        if not png:
            record(label, False, "render_panel 返回 None")
            continue
        ie, d = inspect_image(png, "png")
        with open(os.path.join(OUT_DIR, f"realm_{i:02d}.png"), "wb") as f:
            f.write(png)
        record(label, not ie, "；".join(ie) or d)


async def main():
    global BOT
    BOT = CapBot(adapter, BOT_ID)

    # ⚠️ 卡片磁盘缓存必须改道到临时目录：否则改了模板/文案后 render 会命中上一轮
    # 全绿留下的旧 PNG，像素与视觉断言全部假通过（test_e2e.py 也踩过这个坑）。
    # 不能直接删 /app/data/pic_cache —— 那是线上 bind 挂载的运行态目录。
    import pathlib

    pic = card._load_pic()
    if pic is not None:
        pc = pic.pic_cache
        pc.dir = pathlib.Path(TMP) / "pic_cache"
        pc._loaded = False
        pc._index = {}

    await static_checks()

    # 抢/送 需要一个已入道的对象；这条不是菜单用例，单独准备。
    SENT.clear()
    await handle_event(BOT, mk_group(B, "入道"))
    SENT.clear()

    for case in CASES:
        await run_case(case)

    # 列表卡排版：像素级机器证据（与人工视觉确认互为双验收）
    shop_png = os.path.join(OUT_DIR, "13_shop.png")
    if os.path.isfile(shop_png):
        ok, d = check_list_spacing(shop_png)
        record("PIXEL 列表卡标题与说明间距", ok, d)
    else:
        record("PIXEL 列表卡标题与说明间距", False, "缺少商店卡产物")
    # prove-it 自举：把间隔压回 1 行，检测器必须判不合格，防止断言恒真。
    fake = [(117, 136, 19), (137, 151, 14)]
    record("SELF 间距检测能拒绝粘连", min(_pair_gaps(fake)) < 6,
           f"模拟粘连 gap={_pair_gaps(fake)}（应 <6）")

    await realm_cards()


def _run():
    asyncio.run(main())
    ok = sum(1 for _, p, _ in RESULTS if p)
    bad = len(RESULTS) - ok
    # 控制台只用 ASCII，避免 Windows 控制台编码把中文打乱；完整中文报告落文件。
    for name, passed, detail in RESULTS:
        safe = name.encode("ascii", "backslashreplace").decode("ascii")
        print(("PASS  " if passed else "FAIL  ") + safe + ("" if passed else f"  -> {detail[:160]}"))
    with open(os.path.join(OUT_DIR, "report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT))
        f.write(f"\n\nMENU_CASES={len(CASES)} TOTAL={len(RESULTS)} PASSED={ok} FAILED={bad}\n")
    print("=" * 70)
    print(f"MENU_CASES={len(CASES)} TOTAL={len(RESULTS)} PASSED={ok} FAILED={bad}")
    print("REPORT=" + os.path.join(OUT_DIR, "report.txt"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
