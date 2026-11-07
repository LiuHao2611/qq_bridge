# -*- coding: utf-8 -*-
"""端到端口令测试：真实 nonebot matcher + 真实事件对象，只拦截出站 API。

不加载 qq_bridge 主插件（避免触发 LLM 调用，遵守"禁止自动调用模型"红线）。
同时加载 xiuxian，验证两个插件**不互相抢答**（这是 dp 前缀存在的全部理由）。
数据全部落临时目录，不污染线上存档。

验收点：
  * 静态：matcher 数 == 菜单条数，全部 priority=5 + block=True
  * 每条口令恰好 1 条回包
  * 闲聊不误伤；修仙的 突破/背包/排行榜/商店/任务/炼丹 不触发斗破
  * 前缀兼容：@机器人 / # / 空格 / CQ 码在口令前都能命中
  * 私聊可用（需求：把斗破开到私聊里测试）
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

ROOT = r"D:\AI\manju_agent\qq_bridge"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="doupo_e2e_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(TMP, "doupo.json")
os.environ["XIUXIAN_DATA_PATH"] = os.path.join(TMP, "xiuxian.json")
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
    elif expect_image and any(has_image(s) for s in SENT):
        pass  # 图片回复：关键字不一定在纯文本里，发图即视为成功
    elif expect_kw and not any(expect_kw in o for o in out):
        ok = False
        detail = f"未见关键字 {expect_kw!r} 实际={out!r}"
    RESULTS.append((label, ok, (out[0] if out else "")[:100] if ok else detail))


async def fire_silent(label, event):
    """断言"不该有回包"（闲聊 / 别的插件的口令）。"""
    SENT.clear()
    err = None
    try:
        await handle_event(BOT, event)
    except Exception:
        err = traceback.format_exc(limit=3)
    out = [text_of(s) for s in SENT]
    # 修仙也被加载了：只要回包里没有任何斗破特征词，就算斗破没抢答
    doupo_hit = any(("斗气大陆" in o) or ("斗破" in o) or ("玄重尺" in o) for o in out)
    ok = err is None and not doupo_hit
    RESULTS.append((label, ok, "" if ok else f"斗破抢答了：{out!r}"))


async def main():
    A, B = 10001, 10002
    p_mod = sys.modules["src.plugins.doupo"]
    cmds = sys.modules["src.plugins.doupo.commands"]

    # 出图限流（_throttle）已在 test_throttle.py 单测覆盖；本 e2e 只验证口令路由，
    # 故把限流打成直通，避免连续出图被 USER_GAP 掐掉导致断言失真。
    p_mod.throttle.check = lambda *, user, group: (True, "ok")
    p_mod.throttle.jitter_delay = lambda: 0.0

    # --- 静态：matcher 优先级 / 阻断 / 数量 ---
    mats = [v for v in vars(p_mod).values()
            if hasattr(v, "priority") and hasattr(v, "block")]
    pri_ok = all(m.priority == 5 and m.block is True for m in mats)
    want = cmds.menu_total()
    RESULTS.append(("STATIC matcher 数量 = 菜单条数", len(mats) == want,
                    f"count={len(mats)} menu_total={want}"))
    RESULTS.append(("STATIC 全部 priority=5 + block=True", pri_ok,
                    f"n={len(mats)} pri_ok={pri_ok}"))

    # --- 唯一不带 dp 的口令：「斗破菜单」必须能触发，且改发图片卡（与 dp人物 同款降级链路）---
    await fire("斗破菜单（无前缀）", mk_group(A, "斗破菜单"), None, expect_image=True)
    await fire("dp菜单", mk_group(A, "dp菜单"), None, expect_image=True)
    await fire("斗破口令", mk_group(A, "斗破口令"), None, expect_image=True)

    # --- 前缀兼容：@机器人 / # / 前导空格 ---
    await fire("前缀 @机器人", mk_group(A, "dp人物", prefix_at_bot=True), "斗气大陆")
    await fire("前缀 #", mk_group(A, "#dp人物"), "斗气大陆")
    await fire("前缀 空格", mk_group(A, "  dp人物"), "斗气大陆")

    # --- 未入道：所有需要角色的口令统一回 NOT_ENTERED ---
    await fire("未入道 dp人物", mk_group(A, "dp人物"), "尚未踏入")
    await fire("未入道 dp战斗", mk_group(A, "dp战斗"), "尚未踏入")

    # --- 入道 → 全流程 ---
    await fire("dp入道", mk_group(A, "dp入道"), "玄重尺")
    await fire("dp人物", mk_group(A, "dp人物"), None, expect_image=True)
    await fire("dp修炼", mk_group(A, "dp修炼"), "修为")
    await fire("dp突破（修为不足）", mk_group(A, "dp突破"), "修为不足")
    await fire("dp战斗", mk_group(A, "dp战斗"), "遭遇")
    await fire("dp战斗（CD 中）", mk_group(A, "dp战斗"), "调息")
    await fire("dp强化", mk_group(A, "dp强化"), "炎星石")
    await fire("dp斗技", mk_group(A, "dp斗技"), None, expect_image=True)
    await fire("dp升级 龙吟碎", mk_group(A, "dp升级 龙吟碎"), "焚血散")
    await fire("dp异火", mk_group(A, "dp异火"), None, expect_image=True)
    await fire("dp养火 青莲地心火", mk_group(A, "dp养火 青莲地心火"), "药渣")
    await fire("dp宝库", mk_group(A, "dp宝库"), None, expect_image=True)
    await fire("dp买 血灵草 2", mk_group(A, "dp买 血灵草 2"), "血灵草")
    await fire("dp炼药", mk_group(A, "dp炼药"), "回灵丹")
    # 回灵丹需要 血灵草×2 + 紫叶兰×1，先把材料补齐再开炉
    await fire("dp买 紫叶兰 1", mk_group(A, "dp买 紫叶兰 1"), "紫叶兰")
    # 成功/失败两种回包都带「成功率」，用它做关键字
    await fire("dp炼制 回灵丹", mk_group(A, "dp炼制 回灵丹"), "成功率")
    await fire("dp背包", mk_group(A, "dp背包"), None, expect_image=True)
    await fire("dp服丹 回灵丹", mk_group(A, "dp服丹 回灵丹"), "回灵丹")
    await fire("dp任务", mk_group(A, "dp任务"), "主线")
    await fire("dp接任务", mk_group(A, "dp接任务"), "支线")
    await fire("dp交任务", mk_group(A, "dp交任务"), "完成")
    await fire("dp领主线", mk_group(A, "dp领主线"), "主线")
    await fire("dp排行", mk_group(A, "dp排行"), None, expect_image=True)
    # 抢/送 需要一个已入道的对象
    await fire("B 入道", mk_group(B, "dp入道"), "玄重尺")
    await fire("dp抢 @某人", mk_group(A, "dp抢", at=B), "金币")
    await fire("dp送 100 @某人", mk_group(A, "dp送 100", at=B), "金币")
    await fire("dp帮助", mk_group(A, "dp帮助"), "暴击")

    # --- 私有会话现在可用（需求：把斗破开到私聊里测试）---
    C = 10003
    await fire("私聊 dp入道", mk_private(C, "dp入道"), "玄重尺")
    await fire("私聊 dp人物", mk_private(C, "dp人物"), None, expect_image=True)
    await fire("私聊 dp修炼", mk_private(C, "dp修炼"), "修为")
    await fire("私聊 dp战斗", mk_private(C, "dp战斗"), "遭遇")
    await fire("私聊 dp帮助", mk_private(C, "dp帮助"), "暴击")
    # 私聊没有 @对象：抢/送 应优雅回用法提示而非崩溃
    await fire("私聊 dp抢（无对象）", mk_private(C, "dp抢"), "用法")
    await fire("私聊 dp排行（仅自己）", mk_private(C, "dp排行"), None, expect_image=True)

    # --- 闲聊 / 修仙口令：斗破一律不抢答 ---
    await fire_silent("闲聊不误伤", mk_group(A, "今天天气不错啊"))
    for kw in ("突破", "背包", "排行榜", "商店", "任务", "炼丹", "坊市"):
        await fire_silent(f"修仙口令「{kw}」不触发斗破", mk_group(A, kw))
    await fire_silent("「斗破」二字单独出现不触发", mk_group(A, "我想看斗破苍穹动漫"))


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
