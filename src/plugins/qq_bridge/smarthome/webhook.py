# -*- coding: utf-8 -*-
"""smarthome.webhook —— Webhook 反向告警接收器（M8 智能家居中枢，2026-10-21）。

需求（roadmap §M8，HAQBot 同构）：HA 自动化（温度超标/漏水/门磁/人在）→
推送 QQ 群，摄像头事件附截图/视频片段。

链路：HA automation → rest_command POST 本端口 /ha/webhook → 校验 token →
解析事件 → 回调注入的发送出口（on_event）→ QQ 群。

设计：
  - **FastAPI app 工厂化**：create_app(on_event, token) 纯函数，可裸单测
    （TestClient 直接打，不起真端口）；真运行时由 start_receiver 起 uvicorn。
  - **token 校验**：HA 侧在 URL query 或 body 带 SMARTHOME_WEBHOOK_TOKEN，
    不符 403（防局域网扫描器乱触发告警）。
  - **禁止静默**：解析失败/回调失败都显式 500 + 日志，让 HA 侧能看到失败
    （HA automation 的 rest_command 失败会进 HA 日志，可排查）。
  - 依赖 nonebot2[fastapi] 已装的 FastAPI/uvicorn；不 import nonebot 本身。
"""
from __future__ import annotations

from typing import Awaitable, Callable

from ..config import get_logger, jlog
from .ha import HaError, HaEvent

log = get_logger()

#: on_event 回调签名：async (HaEvent) -> None（发送出口由 __init__.py 注入）
EventCallback = Callable[[HaEvent], Awaitable[None]]


def parse_event(payload: dict) -> HaEvent:
    """把 HA webhook payload 解析成 HaEvent。

    接受的字段（全部可选，按需组合）：
      event / event_type   事件类型（temperature_high / leak / motion / door…）
      entity_id            触发设备
      message              HA 侧写好的人话（优先透传）
      camera_entity        需附图的摄像头 entity
      level                info / warn / error
    字段缺失时给默认值，不抛错——HA 自动化模板各写各的，宽容解析。
    """
    p = payload if isinstance(payload, dict) else {}
    event_type = str(p.get("event_type") or p.get("event") or "ha_event").strip()
    message = str(p.get("message") or "").strip()
    return HaEvent(
        event_type=event_type,
        entity_id=str(p.get("entity_id") or "").strip(),
        message=message,
        camera_entity=str(p.get("camera_entity") or p.get("camera") or "").strip(),
        level=str(p.get("level") or "info").strip().lower() or "info",
    )


def format_event_text(ev: HaEvent) -> str:
    """事件 → QQ 群人话（无 message 时按事件类型转述，禁干巴日志）。"""
    if ev.message:
        base = ev.message
    else:
        base = "智能家居事件：%s" % ev.event_type
        if ev.entity_id:
            base += "（%s）" % ev.entity_id
    mark = {"error": "🚨", "warn": "⚠️"}.get(ev.level, "📢")
    return "%s %s" % (mark, base)


def create_app(on_event: EventCallback | None = None, token: str = ""):
    """FastAPI app 工厂（可裸单测：TestClient(create_app(...))）。

    POST /ha/webhook?token=xxx   body = HA 事件 JSON。
    token 为空串 = 不校验（仅当没配 SMARTHOME_WEBHOOK_TOKEN 时，日志警告）。
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    import sys as _sys

    # 本模块顶部有 `from __future__ import annotations`——端点注解 request: Request
    # 会被写成字符串，FastAPI 靠模块 globals 解析。这里把 Request 注入模块 globals，
    # 否则解析失败会把 request 当成 body 参数 → 一切请求 422。
    _me = _sys.modules[__name__]
    if not hasattr(_me, "Request"):
        _me.Request = Request  # type: ignore[attr-defined]

    app = FastAPI(title="qq_bridge smarthome webhook", docs_url=None, redoc_url=None)
    if not token:
        log.warning(jlog("smarthome_webhook_no_token",
                         detail="未配 SMARTHOME_WEBHOOK_TOKEN，webhook 不校验"))

    @app.post("/ha/webhook")
    async def webhook(request: Request) -> JSONResponse:
        got = (request.query_params.get("token")
               or request.headers.get("x-webhook-token") or "")
        if token and got != token:
            return JSONResponse({"ok": False, "error": "token 不符"},
                                status_code=403)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": "body 不是 JSON"},
                                status_code=400)
        try:
            ev = parse_event(payload)
        except HaError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        if on_event is None:
            # 未接发送出口：如实 503（不假装成功——HA 侧能看到并报警）
            return JSONResponse({"ok": False, "error": "发送出口未接线"},
                                status_code=503)
        try:
            await on_event(ev)
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("smarthome_webhook_callback_failed",
                             event_type=ev.event_type, error=type(e).__name__))
            return JSONResponse({"ok": False, "error": "发送失败：%s" % e},
                                status_code=500)
        return JSONResponse({"ok": True, "event": ev.event_type})

    @app.get("/ha/health")
    async def health() -> dict:
        return {"ok": True, "service": "smarthome-webhook"}

    return app


class WebhookReceiver:
    """真运行时接收器：uvicorn 起服务（host/port/token 可注入）。

    单测不碰它（打 create_app 的 TestClient 即可）；生产中由
    _on_startup 调 start()，关闭时调 stop()。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8090,
                 token: str = "", on_event: EventCallback | None = None) -> None:
        self.host = host
        self.port = int(port)
        self.token = token
        self.on_event = on_event
        self._server = None

    async def start(self) -> None:
        import uvicorn

        config = uvicorn.Config(create_app(self.on_event, self.token),
                                host=self.host, port=self.port,
                                log_level="warning")
        self._server = uvicorn.Server(config)
        log.info(jlog("smarthome_webhook_started", host=self.host,
                      port=self.port))
        # await serve 会阻塞到 shutdown——由调用方 ensure_future 托管
        await self._server.serve()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
