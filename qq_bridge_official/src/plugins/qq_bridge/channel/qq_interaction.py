# -*- coding: utf-8 -*-
"""QQ 官方「消息按钮点击」回调（2026-11-11，仅官方副本）。

流程（腾讯官方文档 bot.q.qq.com/.../msg-btn.html + overview.html）：
  1. 机器人下发带 keyboard 的消息（见 qq_keyboard.py / official.py::send）；
  2. 用户点按钮 → 平台推 ``INTERACTION_CREATE`` 事件，``data.resolved.button_data``
     带回我们预设的口令（如 "赏金"）；
  3. 本模块捕获事件 → 用 button_data 构造与「用户手打口令」完全相同的 ctx +
     伪事件 → 复用 ``official_bridge.dispatch_normalized`` 走同一套 warframe
     /二游/小游戏分发与出图 → 按钮点击与手打口令行为零分叉。

⚠️ 官方文档：指令回调类场景需在 3 秒内 ``PUT /interactions/{id}`` 响应，否则超时。
   本模块先尽力 ack（best-effort，适配器若有 ack 方法），再异步跑业务；即便 ack
   不可用也不影响消息照常发出（用户仍能看到按钮触发的回复，只是客户端 loading 略久）。
   ack 失败只记日志，绝不阻断。

降级：回调解析/分发任何异常都只记日志（CLOSE-02 记全不静默吞），绝不让一个坏
按钮拖垮官方 Bot 的事件循环。
"""
from __future__ import annotations

import time
from typing import Any

from ..config import get_logger, jlog

log = get_logger()

_INTERACTION_AVAILABLE = False
QQBot = None       # type: ignore[assignment,misc]
QQEvent = None     # type: ignore[assignment,misc]
try:
    from nonebot.adapters.qq import Bot as QQBot       # noqa: PLC0415
    from nonebot.adapters.qq import Event as QQEvent   # noqa: PLC0415
    from nonebot.adapters.qq.event import InteractionCreateEvent as _InteractionEvent

    _INTERACTION_AVAILABLE = True
except Exception:  # noqa: BLE001 — 适配器缺该事件时整个模块静默不注册
    _InteractionEvent = None  # type: ignore[assignment,misc]
    QQBot = None
    QQEvent = None

_matcher = None


class _PseudoAuthor:
    """交互事件 -> 伪 author（OfficialLikeEvent/normalize 只读 member_openid）。"""

    def __init__(self, member_openid: str, user_openid: str = ""):
        self.member_openid = member_openid
        self.user_openid = user_openid or member_openid


class _PseudoMessageEvent:
    """交互事件 -> 伪官方 GroupMessageCreateEvent 最小契约。

    ``OfficialLikeEvent`` 只依赖：get_message()/to_me/__getattr__ 透传
    (id/author/group_openid)。这里补齐，让按钮点击复用同一适配器，零业务分叉。
    """

    channel = "official"
    _gt_source = "button"  # 供 dispatch_normalized 日志区分来源

    def __init__(self, cmd: str, group_openid: str, member_openid: str,
                 msg_id: str, self_id: str):
        from ..game_tools.official_bridge import _OBMessage, _OBState

        segs = _OBMessage()
        segs.append(_OBState("text", {"text": cmd}))
        self._segments = segs
        self.group_openid = group_openid
        self.author = _PseudoAuthor(member_openid)
        self.id = msg_id or ""
        self.self_id = str(self_id or "")
        self.to_me = True
        # 供 _to_ob_segments / _official_text 迭代 get_message()
        self._msg = segs

    def get_message(self):
        return self._msg

    def get_plaintext(self) -> str:
        return self._segments.get_plaintext()

    def is_tome(self) -> bool:
        return True

    def get_type(self) -> str:
        return "message"

    def get_event_name(self) -> str:
        return "ButtonInteractionAsMessage"


def _button_cmd(event: Any) -> str:
    """从 INTERACTION_CREATE 事件取按钮口令（data.resolved.button_data）。"""
    try:
        data = getattr(event, "data", None)
        resolved = getattr(data, "resolved", None) if data is not None else None
        return str(getattr(resolved, "button_data", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def is_interaction_event(event: QQEvent) -> bool:
    """rule：只处理官方 INTERACTION_CREATE，NapCat/其它事件立即放行。

    ⚠️ 参数注解必须是官方 `Event` 具体类（不能是 `Any`）：NoneBot 的 Rule 靠
    参数类型注入事件实例，`Any` 会抛 `ValueError: Unknown parameter`（与
    official_bridge._is_warframe_command 同款坑，2026-11-11 实测）。
    注解为官方 Event 基类后，NapCat 的 OneBot 事件因类型不匹配被自动跳过，
    天然隔离，无需再手写 adapter 守卫。
    """
    if not _INTERACTION_AVAILABLE:
        return False
    return isinstance(event, _InteractionEvent)


async def _ack_interaction(bot: Any, event: Any) -> None:
    """在 3 秒内 ack 平台交互（PUT /interactions/{id}）；失败记日志不静默吞。

    ⚠️ 2026-11-11 真实群实测踩坑：nonebot-adapter-qq 1.7.2 的
    ``Bot.put_interaction(interaction_id, code)`` 的 ``code`` 是**必填**
    关键字参数（``Literal[0..5]``）。旧代码只传 ``interaction_id`` →
    抛 ``TypeError: missing 1 required keyword-only argument: 'code'`` →
    ACK 从未发出 → 客户端显示「请求第三方失败」。``code=0`` = 回执成功。
    """
    iid = str(getattr(event, "id", "") or "")
    if not iid:
        log.warning(jlog("official_interaction_ack_no_id"))
        return
    fn = getattr(bot, "put_interaction", None)
    if not callable(fn):
        # 1.7.2 已有 put_interaction；若换成没有该 API 的适配器，记全后仍继续发业务
        log.warning(jlog("official_interaction_ack_unsupported",
                         note="adapter has no put_interaction; send still proceeds"))
        return
    try:
        await fn(interaction_id=iid, code=0)
        log.info(jlog("official_interaction_ack_ok", interaction_id=iid[:16]))
    except Exception as e:  # noqa: BLE001 — ACK 失败不阻断业务回复
        log.warning(jlog("official_interaction_ack_failed", api="put_interaction",
                         error=type(e).__name__, detail=str(e)[:160]))


async def handle_interaction(bot: QQBot, event: QQEvent) -> None:  # type: ignore[valid-type]
    """按钮点击入口：解析口令 → 构造完整 ctx → 复用 warframe 分发 → 出图/回复。

    ⚠️ 参数注解必须是官方 Bot/Event 具体类（不能是 `Any`）：NoneBot 靠类型
    注入，`Any` 会抛 `ValueError: Unknown parameter bot`，handler 直接注册失败。
    与 official_bridge._handle_official_warframe 的注解完全一致。
    """
    from ..channel import OFFICIAL, get_channel
    from ..game_tools.official_bridge import dispatch_normalized

    cmd = _button_cmd(event)
    if not cmd:
        log.warning(jlog("official_button_empty_data", event_id=str(getattr(event, "id", ""))))
        return

    # 交互事件字段（官方模型 ButtonInteraction）：chat_type=1 群、2 单聊
    chat_type = int(getattr(event, "chat_type", 0) or 0)
    group_openid = str(getattr(event, "group_openid", "") or "")
    member_openid = str(getattr(event, "group_member_openid", "") or "")
    user_openid = str(getattr(event, "user_openid", "") or "")
    is_group = chat_type == 1 and bool(group_openid)
    if not is_group:
        # 群按钮是主场景；单聊按钮暂只记日志不分发（避免 C2C ctx 构造走错分支）
        log.info(jlog("official_button_non_group_skip", chat_type=chat_type, cmd=cmd))
        return

    # ack 尽力而为（不 await 业务前的长耗时逻辑）
    await _ack_interaction(bot, event)

    # 构造与 normal 群消息一致的完整 ctx（GROUP_PREFIX/USER_PREFIX 与 official.py 同源）
    from .base import make_ctx  # noqa: PLC0415
    from .official import GROUP_PREFIX, USER_PREFIX  # noqa: PLC0415

    # 2026-11-11 修复 40034105「主动消息失败，无权限」：INTERACTION_CREATE
    # 没有原消息 msg_id，旧代码塞 event.id 当 msg_id 且 ts=0 → passive_ok=False
    # → 按钮触发的回复走主动消息 → 个人主体无主动发言权限被拒。官方正确做法：
    # 用交互事件自带的 event_id（形如 INTERACTION_CREATE:<uuid>）做**被动回复**，
    # 适配器 send_to_group/send_to_c2c 原生支持 event_id 参数（透传给
    # POST /v2/groups/{openid}/messages）。事件 id 不是 msg_id，不再误填。
    event_id = str(getattr(event, "event_id", "") or "")
    full_ctx = make_ctx(
        OFFICIAL,
        user_key=USER_PREFIX + member_openid,
        group_key=GROUP_PREFIX + group_openid,
        user_id=0,
        group_id=0,
        self_id=str(getattr(bot, "self_id", "") or ""),
        is_group=True,
        at_bot=True,
        reply_ref={
            "event_id": event_id,   # 被动回复凭据（按钮点击的官方正确通道）
            "msg_id": "",            # 交互事件无原消息 msg_id，不再误填
            "ts": time.time(),      # 让 passive_ok=True，走被动回复不占主动额度
        },
    )
    pseudo = _PseudoMessageEvent(
        cmd=cmd, group_openid=group_openid, member_openid=member_openid,
        msg_id=str(getattr(event, "id", "") or ""),
        self_id=str(getattr(bot, "self_id", "") or ""),
    )
    # reply_ref.ts=0 → passive_ok=False → 按钮触发的回复走主动消息（占日额度），
    # 这是平台限制：INTERACTION_CREATE 没有原消息 msg_id 可做被动回复。
    log.info(jlog("official_button_dispatch", cmd=cmd, group_openid=bool(group_openid)))
    await dispatch_normalized(bot, full_ctx, pseudo, cmd)


def register() -> None:
    """注册按钮回调 matcher（幂等；仅在官方适配器可用时）。"""
    global _matcher
    if _matcher is not None:
        return
    if not _INTERACTION_AVAILABLE:
        log.info(jlog("official_interaction_skip", reason="adapter_has_no_interaction_event"))
        return
    try:
        from nonebot import on_notice  # noqa: PLC0415
        from nonebot.rule import Rule  # noqa: PLC0415

        _matcher = on_notice(rule=Rule(is_interaction_event))
        _matcher.handle()(handle_interaction)
        # ⚠️ jlog(event, **fields) 首形参名就是 event，不能再用 event= 关键字（会撞名
        # 抛 TypeError 导致 matcher 注册失败，2026-11-11 运行期实测踩坑）。字段名改 evt_type。
        log.info(jlog("official_interaction_registered", evt_type="INTERACTION_CREATE"))
    except Exception as e:  # noqa: BLE001 — 注册失败只记日志，绝不影响主流程
        _matcher = None
        log.error(jlog("official_interaction_register_failed",
                       error=type(e).__name__, detail=str(e)[:200]))
