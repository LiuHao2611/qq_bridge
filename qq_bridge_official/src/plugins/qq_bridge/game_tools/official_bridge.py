# -*- coding: utf-8 -*-
"""QQ 官方通道 → warframe 游戏工具箱 协议适配器（2026-11-10）。

背景（为什么需要这一层）
------------------------
warframe 全部指令（赏金/裂隙/紫卡/仲裁/市价…）实现在 ``game_tools/__init__.py``
的 ``_handle_safe(bot: Bot, event: MessageEvent)`` 里，``Bot``/``MessageEvent``
是 **OneBot V11 具体类型**。QQ 官方群事件（nonebot-adapter-qq 的
``GroupAtMessageCreateEvent`` / ``GroupMessageCreateEvent``）类型不匹配，NoneBot
依赖注入会抛 ``TypeMisMatch`` → warframe matcher 静默不执行。所以官方群里
warframe 指令「零响应」。

设计约束（用户最高限制）
------------------------
* **原 NapCat 代码只读**：本文件与所有改动只在 ``qq_bridge_official/`` 副本；
  ``game_tools/__init__.py``（136KB）与 ``warframe.py``（280KB）**一行不改**。
* 复用 warframe 已有的全部命令分发 / 渲染 / 降级逻辑，靠「适配器」把官方事件
  伪装成 OneBot 风格契约，而不是重写几百行业务。
* 复用已有 ``OfficialChannel.send`` 出图/出文，不新造发送通道。
* 确定性互斥：官方 warframe matcher（priority=5, block=True）命中即阻断
  priority=10 的 ``official_matcher``（_route AI 对话），杜绝双回复。

适配器要补平的三个协议差异（已在容器 nonebot-adapter-qq 1.7.2 实测）
--------------------------------------------------------------------
1. 段类型：官方 ``mention_user``/``text``/``image``，data 键 ``user_id``/``text``/``url``；
   OneBot 侧要 ``at``/``text``/``image``，at 的 data 键是 ``qq``。
2. 身份：官方只有 openid（``group_openid``/``member_openid``，字符串），无 int QQ 号；
   用 ``channel.official.pseudo_id`` 映射为稳定**负数 int**（与 dispatcher 的
   ``to_legacy_event`` 同源，保证会话 scope 与主流程 ``g:<pseudo>`` 一致）。
3. 能力缺口：官方无 ``delete_msg``（降级 no-op）、官方 Message 无 ``get_plaintext``
   （适配器自实现）、OneBot Event 有 ``get_matcher``（给 no-op stub）。
"""
from __future__ import annotations

from typing import Any

from nonebot import on_message
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import Event as QQEvent
from nonebot.adapters.qq import (
    C2CMessageCreateEvent,
    GroupAtMessageCreateEvent,
    GroupMessageCreateEvent,
)
from nonebot.rule import Rule

from ..config import Config, get_logger, jlog
from ..channel.base import (
    attach_official_ctx,
    detach_official_ctx,
    current_official_ctx,
)
from ..channel.official import GROUP_PREFIX, USER_PREFIX, pseudo_id

log = get_logger()

# 官方专用 matcher 单例（幂等注册）
_official_warframe_matcher = None


# ---------------------------------------------------------------------------
# OneBot 风格段 / 消息：给 warframe 的 _event_segments / _plain_text /
# _extract_images / _at_bot 消费（它们只读 .type / .data / 迭代）
# ---------------------------------------------------------------------------
class _OBState:
    __slots__ = ("type", "data")

    def __init__(self, seg_type: str, data: dict):
        self.type = seg_type
        self.data = data


class _OBMessage(list):
    """可被迭代、可索引的段序列，兼容 OneBot Message 的最小契约。"""

    def get_plaintext(self) -> str:
        return "".join(
            str(s.data.get("text", "") or "")
            for s in self if getattr(s, "type", "") == "text"
        ).strip()


def _to_ob_segments(event: Any) -> _OBMessage:
    """官方 Message 段 → OneBot 风格段（at/text/image）。

    * mention_user(data.user_id) → at(data.qq=user_id)
    * text(data.text)            → text(data.text)
    * image(data.url)            → image(data.url)  ← _extract_images 读 url
    * 其它（表情/文件/arK）      → 忽略，不喂给 warframe 的文本/取图逻辑
    """
    out = _OBMessage()
    try:
        for seg in event.get_message():
            t = str(getattr(seg, "type", "") or "")
            d = dict(getattr(seg, "data", {}) or {})
            if t in ("mention_user", "mention_everyone", "at"):
                out.append(_OBState("at", {"qq": str(d.get("user_id") or d.get("qq") or "all")}))
            elif t == "text":
                out.append(_OBState("text", {"text": str(d.get("text") or "")}))
            elif t == "image":
                # 官方图片 url 常为临时签名链接；warframe 紫卡 OCR 走 httpx 裸 GET，
                # 可能 403 → 该能力在官方通道必然降级（见方案说明），不伪造 path。
                out.append(_OBState("image", {
                    "url": str(d.get("url") or ""),
                    "file": str(d.get("url") or ""),
                }))
    except Exception as e:  # noqa: BLE001 — 段提取失败返回空，绝不冒到事件循环
        log.warning(jlog("gt_official_segments_failed", error=type(e).__name__))
    return out


class OfficialLikeEvent:
    """把官方 QQ 事件伪装成 OneBot ``MessageEvent`` 的最小契约对象。

    warframe 业务代码只从 event 读下列属性/方法，全部在此补齐；不继承任何
    NoneBot 类型（不是给依赖注入用的，是 ``_handle_safe`` 内部直接用的普通对象）。
    """

    channel = "official"

    def __init__(self, event: Any, full_ctx: dict, bot_self_id: str):
        self._raw = event
        self._ctx = full_ctx
        self._self_id = str(bot_self_id or "")

        is_group = bool(full_ctx.get("is_group"))
        self.message_type = "group" if is_group else "private"
        # pseudo 负数 int：与 dispatcher.to_legacy_event 同源，scope 不串号
        self.group_id = int(full_ctx.get("group_id") or 0) or pseudo_id(
            str(full_ctx.get("group_key") or "")
        )
        self.user_id = int(full_ctx.get("user_id") or 0) or pseudo_id(
            str(full_ctx.get("user_key") or "")
        )
        self.self_id = self._self_id
        # 段序列：original_message 优先（warframe 注释：nonebot 会改写 .message 的 at）
        self._segments = _to_ob_segments(event)
        self.original_message = self._segments
        self.message = self._segments
        # 保留官方事件引用，便于 warframe 未来做 channel-aware 扩展
        self.raw = event

    def get_plaintext(self) -> str:
        return self._segments.get_plaintext()

    def get_message(self):
        return self._segments

    def get_user_id(self) -> int:
        return self.user_id

    def to_me(self) -> bool:
        return bool(getattr(self._raw, "to_me", False)) or isinstance(
            self._raw, GroupAtMessageCreateEvent
        )

    def get_matcher(self):
        """warframe 紫卡分析成功后调 stop_propagation()——这里是独立 matcher
        内直接调用，无需真的阻断，给 no-op stub 即可（GT:1772 已被 try 包住）。"""
        return _NoopMatcher()

    def __getattr__(self, name: str):
        """未知属性透传给官方原事件（如 id/author/group_openid），便于扩展。"""
        try:
            return getattr(self._raw, name)
        except AttributeError:
            raise AttributeError(name) from None


class _NoopMatcher:
    def stop_propagation(self) -> None:  # noqa: D401 — 刻意 no-op
        return None


class OfficialLikeBot:
    """把 OneBot ``Bot.send`` / ``Bot.call_api`` 两处依赖映射到官方通道。

    * ``send(event, msg)``：warframe 的文本回复（24 处）→ OfficialChannel.send
      发一条纯文本；返回值包成 ``{"message_id": int}`` 兼容 GT:1754/1808 的撤回队列。
    * ``call_api("delete_msg", ...)``：官方无撤回 API → 记日志 + no-op（GT:80）。
    * ``self_id``：_at_bot 与 mention 段比对用。
    """

    def __init__(self, bot: Any, full_ctx: dict):
        self._bot = bot
        self._ctx = full_ctx
        self.self_id = str(getattr(bot, "self_id", "") or "")

    async def send(self, event: Any, msg: Any, **kwargs) -> dict:
        from ..channel.base import Outgoing
        from ..channel import get_channel, OFFICIAL

        text = self._msg_to_text(msg)
        if not text:
            return {"message_id": 0}
        # 用 normalize 出的完整 ctx（含 openid/reply_ref），不依赖 warframe 3 字段 ctx
        ret = await get_channel(OFFICIAL).send(self._ctx, Outgoing(text=text))
        mid = 0
        if isinstance(ret, dict):
            ids = ret.get("msg_ids") or []
            if ids:
                # 官方 msg_id 是字符串；映射成稳定 int 仅为撤回队列占位（撤回已降级）
                try:
                    mid = int(str(ids[0])[:12], 16) if str(ids[0]).isalnum() else 0
                except (ValueError, TypeError):
                    mid = 0
        return {"message_id": mid}

    @staticmethod
    def _msg_to_text(msg: Any) -> str:
        if msg is None:
            return ""
        if isinstance(msg, str):
            return msg
        # 万一 warframe 传了 Message/MessageSegment：尽力抽 text 段
        try:
            parts = []
            for seg in msg:
                if str(getattr(seg, "type", "")) == "text":
                    parts.append(str((getattr(seg, "data", {}) or {}).get("text") or ""))
            return "".join(parts) or str(msg)
        except Exception:  # noqa: BLE001
            return str(msg)

    async def call_api(self, api: str, **data) -> Any:
        # 官方通道无 delete_msg：显式记日志降级，不静默（CLOSE-02 的精神：记全）
        if api == "delete_msg":
            log.info(jlog("gt_official_recall_unsupported", api=api,
                          message_id=str(data.get("message_id") or "")))
            return {"ok": False, "reason": "official_recall_unsupported"}
        log.warning(jlog("gt_official_call_api_unsupported", api=api))
        return {"ok": False, "reason": "unsupported_on_official"}


# ---------------------------------------------------------------------------
# 命令识别 rule：只把「明确的 warframe/游戏工具命令」路由到 warframe matcher，
# 其余放行给 official_matcher 走 AI 对话，杜绝误吞闲聊。
# ---------------------------------------------------------------------------
def _official_text(event: Any) -> str:
    """从官方事件抽纯文本（官方段里 at 已被单独拆出，此处只拼 text 段）。"""
    out = []
    try:
        for seg in event.get_message():
            if str(getattr(seg, "type", "")) == "text":
                out.append(str((getattr(seg, "data", {}) or {}).get("text") or ""))
    except Exception:  # noqa: BLE001
        pass
    return "".join(out).strip()


def _has_image(event: Any) -> bool:
    try:
        return any(
            str(getattr(s, "type", "")) == "image" for s in event.get_message()
        )
    except Exception:  # noqa: BLE001
        return False


def _is_warframe_command(event: QQEvent) -> bool:
    """warframe 命令判定（仅官方事件）：前缀命令 / 游戏命令词 / 带图。

    ⚠️ ``on_message(type=...)`` 无法区分 adapter（官方与 OneBot 的 get_type()
    都返回 "message"），所以 rule **第一道就是 adapter 守卫**：非
    ``nonebot.adapters.qq`` 事件一律返回 False，绝不让 NapCat 的 OneBot 事件
    被本官方 matcher 误吞（NapCat 仍走它自己的 game_tools matcher）。

    * ``#``/``＃`` 前缀：一律视为游戏工具显式命令（与 GT.PREFIXES 一致）；
      但 ``/`` 前缀是主流程命令（/ai /提醒 /dl…），**必须排除**，否则会吞掉。
    * 免前缀：复用 game_tools 的 ``is_game_command``（NATURAL_CMDS 显式词表）。
    * 带图：**仅**「@机器人 + 图」（紫卡 OCR）或「C2C 私聊 + 图」才放行。
      2026-11-11 防刷屏：非 AT 群消息若仅凭「有人发了张图」就触发 warframe，
      群里任何图片（表情包/截图/闲图）都会刷屏回复，故非 AT 纯图必须静默。
      关键字（文字口令）触发不受此限制——这正是用户要的「非@关键字触发」。
    """
    from . import is_game_command  # 延迟导入，避免与 game_tools/__init__ 循环

    # adapter 守卫：本 matcher 只服务官方通道，NapCat 事件立即放行
    if not type(event).__module__.startswith("nonebot.adapters.qq"):
        return False
    text = _official_text(event)
    if text.startswith(("#", "＃")):
        return True
    if text.startswith("/"):
        return False  # 主流程斜杠命令，交给 official_matcher 的 _route
    if text and is_game_command(text):
        return True
    # 带图门禁（防刷屏）：只有 @机器人的群消息 / C2C 私聊才允许走 OCR 旁路；
    # 非 AT 群里的纯图片一律静默，避免群里随便发图就机器人乱回。
    if _has_image(event):
        is_at_group = isinstance(event, GroupAtMessageCreateEvent)
        is_c2c = isinstance(event, C2CMessageCreateEvent)
        if is_at_group or is_c2c:
            return True
        return False
    return False


async def dispatch_normalized(bot: Any, full_ctx: dict, raw_event: Any, text: str) -> None:
    """复用核心：完整 ctx + 原始事件 + 口令文本 → warframe 全链路（含按钮 ctx）。

    被两处复用：① 正常群/C2C 消息入口 ``_handle_official_warframe``；
    ② 消息按钮点击回调 ``qq_interaction``（用伪事件携带按钮口令）。
    这样「按钮点击」与「用户手打口令」走完全相同的分发/渲染/降级，行为零分叉。
    """
    from . import _handle_safe

    # 2026-11-11：当前口令写进完整 ctx，OfficialChannel.send 据此在每条回复
    # 卡片后追加快捷命令按钮（文本/图片链路共用同一 ctx，一处写入全量覆盖）。
    full_ctx["keyboard_cmd"] = text
    token = attach_official_ctx(full_ctx)
    try:
        ob_event = OfficialLikeEvent(raw_event, full_ctx, str(getattr(bot, "self_id", "") or ""))
        ob_bot = OfficialLikeBot(bot, full_ctx)
        log.info(jlog("gt_official_dispatch",
                      is_group=bool(full_ctx.get("is_group")),
                      source=str(getattr(raw_event, "_gt_source", "message")),
                      text_len=len(text)))
        await _handle_safe(ob_bot, ob_event)  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001 — CLOSE-02：记全，不静默吞
        log.error(jlog("gt_official_handler_failed", error=type(e).__name__,
                       detail=str(e)[:200]))
    finally:
        detach_official_ctx(token)


async def _handle_official_warframe(bot: QQBot, event: QQEvent) -> None:
    """官方 warframe 入口（priority=5, block=True）。

    流程：normalize 出完整 ctx → attach 到 ContextVar → 造适配器 → 复用
    ``_handle_safe`` 原有全部命令分发/渲染/降级 → finally detach。
    """
    from ..channel import OFFICIAL, get_channel

    # 只处理消息类官方事件（群 @ / 群普通 / C2C 私聊）
    if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)):
        return
    ch = get_channel(OFFICIAL)
    try:
        full_ctx = await ch.normalize(event, self_id=str(getattr(bot, "self_id", "") or ""))
    except Exception as e:  # noqa: BLE001
        log.error(jlog("gt_official_normalize_failed", error=type(e).__name__,
                       detail=str(e)[:200]))
        return
    if not full_ctx:
        return
    await dispatch_normalized(bot, full_ctx, event, _official_text(event))


def register() -> None:
    """注册官方专用 warframe matcher。幂等；仅在官方通道可用时注册。

    挂 ``priority=5, block=True``：命中 warframe 命令后阻断后续 priority=10 的
    ``official_matcher``（_route AI 对话），确定性避免双回复。未命中则 rule 返回
    False，事件照常流到 official_matcher 走 AI。
    """
    global _official_warframe_matcher
    if _official_warframe_matcher is not None:
        return
    if QQBot is None or not Config.official_configured():
        log.info(jlog("gt_official_bridge_skip", reason="adapter_missing_or_not_configured"))
        return
    try:
        _official_warframe_matcher = on_message(
            priority=5, block=True, rule=Rule(_is_warframe_command)
        )
        _official_warframe_matcher.handle()(_handle_official_warframe)
        log.info(jlog("gt_official_bridge_registered", priority=5, block=True))
    except Exception as e:  # noqa: BLE001 — 注册失败绝不能拖垮 NapCat / 主流程
        _official_warframe_matcher = None
        log.error(jlog("gt_official_bridge_register_failed",
                       error=type(e).__name__, detail=str(e)[:200]))
