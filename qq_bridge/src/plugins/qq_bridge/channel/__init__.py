# -*- coding: utf-8 -*-
"""通道注册表（docs/qq_official_bot_migration_plan.md §3.3）。

用法：
    from .channel import get_channel
    ch = get_channel(ctx["channel"])     # "napcat" / "official"
    await ch.send(ctx, Outgoing(text="..."))

设计要点：
  * 注册表**惰性加载**：只有真正用到某通道时才 import 它的实现模块，
    官方适配器缺失也不会影响 NapCat（隔离 + 可独立崩溃）；
  * 未知通道名统一回退到 NapCat，绝不抛异常打断业务（CLOSE-02 记而不崩）。
"""
from __future__ import annotations

from typing import Any

from ..config import get_logger, jlog
from .base import Channel, Outgoing, make_ctx  # noqa: F401  （对外暴露契约）

log = get_logger()

NAP_CAT = "napcat"
OFFICIAL = "official"

_registry: dict[str, Channel] = {}
_fallback_warned: set[str] = set()


def _build(name: str) -> Channel | None:
    if name == NAP_CAT:
        from .napcat import NapCatChannel  # noqa: PLC0415

        return NapCatChannel()
    if name == OFFICIAL:
        from .official import OfficialChannel  # noqa: PLC0415

        return OfficialChannel()
    return None


def get_channel(name: str) -> Channel:
    """取通道实例（缓存）。未知名称回退 NapCat 并告警一次。"""
    key = (name or "").strip().lower()
    ch = _registry.get(key)
    if ch is not None:
        return ch

    ch = _build(key)
    if ch is None:
        if key not in _fallback_warned:
            _fallback_warned.add(key)
            log.warning(jlog("channel_unknown_fallback", name=key))
        key2 = NAP_CAT
        ch = _registry.get(key2) or _build(key2)
        if ch is None:  # pragma: no cover — NapCat 通道理论上必然可用
            raise RuntimeError("napcat channel unavailable")
        _registry[key2] = ch
        return ch

    _registry[key] = ch
    return ch


def _official_bot_cls():
    """取官方 Bot 类；适配器未安装时返回 None（NapCat 不受影响）。

    抽成函数而非内联 import：便于单测注入假类，避免测试依赖真实适配器实例。
    """
    try:
        from nonebot.adapters.qq import Bot as QQBot  # noqa: PLC0415

        return QQBot
    except Exception:  # 适配器未安装 → 只能是 NapCat
        return None


def channel_of_bot(bot: Any) -> str:
    """根据 bot 实例判断所属通道名（用于入口分发）。"""
    cls = _official_bot_cls()
    if cls is not None and isinstance(bot, cls):
        return OFFICIAL
    return NAP_CAT
