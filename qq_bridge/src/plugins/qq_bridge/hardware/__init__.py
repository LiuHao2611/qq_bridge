# -*- coding: utf-8 -*-
"""hardware —— DIY 电脑主机硬件行情（月度档案，出图口令）。

口令（纯中文，无需 # 前缀）：
  * ``硬件行情`` / ``装机行情`` / ``硬件价格`` / ``电脑硬件`` → 本期涨跌总览卡；
  * ``硬件行情 5090`` / ``硬件行情 7800x3d`` / ``硬件行情 DDR5 32G`` → 单型号明细卡。

为什么是「月度」而不是「当天」：上游 frocloud-DIYPCmarket 是**人工维护的月度档案**
（数据截止日写在 `UPSTREAM.json`）。本项目**不伪造日内数据**，卡面固定写
「月度档案 · 非日内行情」并给出距今天数；谁需要日内报价，得换数据源，不是改文案。

铁律：
  * 出图口令必须出图 —— 渲染失败只回**显式错误**（含异常类型），绝不降级成纯文字冒充成功；
  * 数据只读 —— 只读 `vendored_frocloud/data/*.csv`，任何写盘都要走搬运工具的显式刷新；
  * 查不到就如实说查不到，并给出「共跟踪多少款 / 换个写法」；命中多个就列出来让用户选，
    绝不擅自挑一个当答案。
"""
from __future__ import annotations

import asyncio
import logging
import re

from nonebot import on_message, on_regex
from nonebot.adapters.onebot.v11 import MessageEvent

# QQ 官方通道（nonebot-adapter-qq）：NapCat 副本里可能没装，装不到就只注册 OneBot matcher。
try:
    from nonebot.adapters.qq import Bot as QQBot
    from nonebot.adapters.qq import Event as QQEvent
    from nonebot.adapters.qq import C2CMessageCreateEvent, GroupMessageCreateEvent
except Exception:  # noqa: BLE001 — 官方适配器可选，不影响 NapCat
    QQBot = None
    QQEvent = object
    C2CMessageCreateEvent = GroupMessageCreateEvent = ()

from . import dataset as ds
from . import market

logger = logging.getLogger("qq_bridge.hardware")

PRI = 5
#: 触发词（长词在前，避免「硬件价格」被更短的词抢先匹配）
WORDS = ("硬件行情", "装机行情", "硬件价格", "电脑硬件", "diy行情")
_ALT = "|".join(re.escape(w) for w in WORDS)
#: matcher 正则：允许前置 CQ 码 / 空白 / # 前缀（与 doupo 同一套写法）
PAT = r"^(?:\[CQ:[^\]]*\]|\s)*#?\s*(?:" + _ALT + r")\s*(.*)$"
_ARG_RE = re.compile(r"^(?:" + _ALT + r")\s*(.*)$", re.I)

matcher = None
_official_matcher = None


def _arg_of(event: MessageEvent) -> str:
    """取口令后的参数（用纯文本，不用 CQ 串；@机器人 段已被适配器剔出文本）。"""
    try:
        text = event.get_plaintext()
    except Exception:  # noqa: BLE001 — 极老事件无该方法时退回 str(event)
        text = str(event.get_message())
    text = re.sub(r"^[#＃/\s]+", "", str(text or "").strip())
    m = _ARG_RE.match(text)
    return (m.group(1) if m else "").strip()


def _tracked_line(dataset) -> str:
    counts = {b: len(dataset.by_bucket(b)) for b in ("gpu", "cpu", "mem")}
    return "共跟踪 %d 款（显卡 %d / CPU %d / 内存存储 %d）" % (
        len(dataset.instruments), counts["gpu"], counts["cpu"], counts["mem"])


def build_payload(arg: str) -> tuple[dict | None, str]:
    """返回 (卡面 payload, 纯文字提示)。二者恰有一个非空。

    纯文字只用于「查不到 / 命中多个」这类**检索结果说明**，不是出图的降级路径。
    """
    dataset = ds.load()
    if not arg:
        return market.overview(dataset), ""
    hits = market.search(arg, dataset)
    if not hits:
        return None, ("档案里没有「%s」。%s。\n发「硬件行情」看本期涨跌总览，"
                      "或换个写法（例：硬件行情 5090 / 7800x3d / DDR5 32G）。"
                      % (arg, _tracked_line(dataset)))
    if len(hits) > 1:
        rows = ["「%s」命中 %d 个标的，说具体一点：" % (arg, len(hits))]
        for i in hits[:6]:
            rows.append("· %s · %s（%s %s）" % (
                i.bucket_name, i.label, i.latest_month, _fmt(i)))
        rows.append("例：硬件行情 %s" % hits[0].label)
        return None, "\n".join(rows)
    return market.detail(hits[0], dataset), ""


def _fmt(i) -> str:
    return ("¥%.2f/GB" % i.latest) if i.unit == "元/GB" else "¥{:,.0f}".format(i.latest)


async def _handle(event: MessageEvent) -> None:
    from .. import pic as pic_mod
    from ..media import send_pic

    arg = _arg_of(event)
    payload, note = build_payload(arg)
    if note:
        await _say(event, note)
        return
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    png = await asyncio.wait_for(
        pic_mod.render("hw_market", card="DIY 硬件行情", payload=payload), timeout=30.0)
    await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)


async def _say(event: MessageEvent, text: str) -> None:
    """发纯文字（检索结果说明用，不含行情数据）。"""
    from ..media import send_text

    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    await send_text(ctx, text, at_user=True)


async def _handle_safe(event: MessageEvent) -> None:
    """异常落文件日志 + 显式报错（绝不静默，也绝不拿文字卡冒充行情卡）。"""
    try:
        await _handle(event)
    except Exception as e:  # noqa: BLE001 — 出图链路任何异常都要让用户看见
        logger.exception("hw_market_failed: %s: %s", type(e).__name__, e)
        try:
            await _say(event, "行情卡出图失败：%s: %s（已记录日志）"
                       % (type(e).__name__, str(e)[:120]))
        except Exception:  # noqa: BLE001 — 连报错都发不出去就只留日志
            logger.exception("hw_market_error_report_failed")


# ---------------------------------------------------------------------------
# QQ 官方通道：OneBot 类型注入不到官方事件（TypeMisMatch），必须另开 matcher。
# 复用 game_tools.official_bridge 的「官方事件 → OneBot 风格事件」适配器，
# 再把完整 ctx attach 到 ContextVar，让 OfficialChannel.send 拿到 openid/reply_ref。
# ---------------------------------------------------------------------------
def _official_text_of(event) -> str:
    """官方事件只拼 text 段（at 段已被适配器拆走）。"""
    parts = []
    try:
        for seg in event.get_message():
            if str(getattr(seg, "type", "")) == "text":
                parts.append(str((getattr(seg, "data", {}) or {}).get("text") or ""))
    except Exception:  # noqa: BLE001 — 取不到就当非命令，交给主流程
        return ""
    return "".join(parts).strip()


def is_official_command(event) -> bool:
    """官方事件是否是本插件的口令（先 adapter 守卫，绝不吞 NapCat / 闲聊）。"""
    if QQBot is None:
        return False
    if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)):
        return False
    text = _official_text_of(event)
    return bool(text and re.match(PAT, text, re.I))


async def _handle_official(bot, event) -> None:
    from ..channel import OFFICIAL, get_channel
    from ..channel.base import attach_official_ctx, detach_official_ctx
    from ..game_tools.official_bridge import OfficialLikeEvent

    ch = get_channel(OFFICIAL)
    full = await ch.normalize(event, self_id=str(getattr(bot, "self_id", "") or ""),
                              trace_id="")
    if not full:
        return
    full["keyboard_cmd"] = _official_text_of(event)
    token = attach_official_ctx(full)
    try:
        wrapped = OfficialLikeEvent(event, full, str(getattr(bot, "self_id", "") or ""))
        await _handle_safe(wrapped)
    finally:
        detach_official_ctx(token)


def register_official():
    """注册官方通道 matcher（幂等；未配官方通道时直接跳过）。"""
    global _official_matcher
    if _official_matcher is not None:
        return _official_matcher
    if QQBot is None:
        return None
    try:
        from ..config import Config
        if not Config.official_configured():
            return None
    except Exception:  # noqa: BLE001 — 配置读不到就不注册，NapCat 不受影响
        return None

    async def _rule(event: QQEvent) -> bool:
        return is_official_command(event)

    _official_matcher = on_message(priority=PRI, block=True, rule=_rule)
    _official_matcher.handle()(_handle_official)
    logger.info("hardware official matcher registered")
    return _official_matcher


def register():

    """注册行情口令 matcher（幂等）。priority=5 + block=True：命中即阻断后续 LLM 回复。"""
    global matcher
    if matcher is not None:
        return matcher
    matcher = on_regex(PAT, priority=PRI, block=True, flags=re.I)
    matcher.handle()(_handle_safe)
    logger.info("hardware market registered (words=%s)", "/".join(WORDS))
    register_official()
    return matcher
