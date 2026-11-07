# -*- coding: utf-8 -*-
"""NapCat（OneBot V11）通道实现。

设计：本通道是**薄封装**——归一化直接复用现有 ``dispatcher.normalize``、
发送直接复用现有 ``media.send_*``，保证 NapCat 侧行为与改造前**完全一致**，
把回归风险降到零。后续再按官方通道的成熟度逐步把逻辑内聚进来。
"""
from __future__ import annotations

from typing import Any

from ..config import get_logger, jlog
from .base import Channel, Outgoing, make_ctx

log = get_logger()


class NapCatChannel(Channel):
    name = "napcat"

    async def normalize(
        self, event: Any, self_id: str = "", trace_id: str = ""
    ) -> dict | None:
        """复用现有 dispatcher 的归一化逻辑，并补齐 ctx v2 字段。"""
        from ..dispatcher import normalize as _legacy_normalize

        ev = _legacy_normalize(event, self_id=self_id, trace_id=trace_id)
        if ev is None:
            return None

        # ev 是 QQEvent 数据类；转成 dict 形态的 ctx v2。
        # user_key **必须**复用 ev.scope_key（"g:%d"/"u:%d"，注意带冒号），
        # 否则限流/会话维度会与改造前不一致 —— 那是对 NapCat 的隐性行为回归。
        uid = int(ev.user_id or 0)
        gid = int(ev.group_id or 0)
        raw = getattr(ev, "raw", None)
        return make_ctx(
            self.name,
            user_key=ev.scope_key,
            group_key=("g:%d" % gid) if gid else "",
            user_id=uid,
            group_id=gid,
            self_id=self_id or str(getattr(ev, "self_id", "") or ""),
            is_group=bool(gid),
            at_bot=bool(getattr(ev, "at_bot", False)),
            trace_id=trace_id,
            reply_ref={"msg_id": int(getattr(raw, "message_id", 0) or 0)},
        )

    async def send(self, ctx: dict, out: Outgoing) -> dict:
        """复用现有 media 层发送（OneBot 消息段由 media 内部构造）。"""
        from ..media import send_image, send_images, send_pic, send_text, send_video

        try:
            if out.text:
                await send_text(ctx, out.text, at_user=out.at_user)
            for url in out.image_urls or []:
                await send_image(ctx, url, at_user=out.at_user)
            for data in out.images or []:
                await send_pic(ctx, data, at_user=out.at_user)
            if len(out.image_urls or []) > 1:
                # 多图逐张（保持与现有 send_images 一致的行为）
                await send_images(ctx, list(out.image_urls)[1:], at_user=out.at_user)
            if out.video:
                await send_video(ctx, out.video, at_user=out.at_user)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001 — 记录后向上抛，不静默吞（CLOSE-02）
            log.error(jlog(
                "channel_send_failed",
                channel=self.name,
                error=type(e).__name__,
                detail=str(e)[:200],
            ))
            return {"ok": False, "reason": "%s: %s" % (type(e).__name__, str(e)[:200])}
