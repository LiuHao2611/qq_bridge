# -*- coding: utf-8 -*-
"""实时推送网关（KB §03 §18 + §09 STR-01/02）。

原生 FastAPI WebSocket 实现，协议对齐知识库《万能短剧 AI 创作平台·开发手册》：
- 连接：GET /ws?token=<jwt>（本地单用户：token 仅记录，不强制校验；云端应返回 4401）
- 客户端→服务端：{"action":"subscribe"|"unsubscribe"|"ping","payload":{"channels":[...]}}
- 服务端→客户端：{"channel":"task:{id}","event":"status_update"|"progress"|"log"|"completed"|"failed",
                  "data":{...},"ts":<unix>}
- 心跳：客户端每 30s 发 ping；服务端 60s 无消息断开（OPS 心跳兜底）
- 通道：task:{task_id} / video:{gen_id} / notifications / credits

REDUN-01：WebSocket 不可用时，前端回退轮询（见 src/ws.js / ProductionStage.vue）。
publish() 线程安全，可从 worker 后台线程调用（内部以 run_coroutine_threadsafe 投递到事件循环）。
"""
import asyncio
import json
import logging
import threading
import time

logger = logging.getLogger("manju.realtime")

# 连接注册表：websocket -> {"channels": set, "loop": asyncio.AbstractEventLoop, "last_seen": float}
_CONNS = {}
_LOCK = threading.Lock()


def _now():
    return int(time.time())


def publish(channel, event, data=None):
    """向所有订阅了 channel 的连接推送一条事件（线程安全）。"""
    msg = {"channel": channel, "event": event, "data": data or {}, "ts": _now()}
    text = json.dumps(msg, ensure_ascii=False)
    with _LOCK:
        targets = [(ws, meta["loop"]) for ws, meta in _CONNS.items() if channel in meta["channels"]]
    for ws, loop in targets:
        try:
            asyncio.run_coroutine_threadsafe(_send(ws, text), loop)
        except Exception as e:  # 循环已关闭等
            logger.warning("[WS] publish schedule failed: %s", e)
            _drop(ws)


async def _send(ws, text):
    try:
        await ws.send_text(text)
    except Exception:
        _drop(ws)


def _drop(ws):
    with _LOCK:
        _CONNS.pop(ws, None)


async def handle_ws(websocket):
    """/ws 连接处理器：鉴权、订阅管理、心跳。"""
    # 必须显式完成握手（Starlette 1.4+ 要求），否则 uvicorn 报
    # "ASGI callable returned without completing handshake" → 500。
    await websocket.accept()
    token = websocket.query_params.get("token")
    # 本地单用户：记录 token，不强制校验；云端应在此校验 JWT 并返回 4401。
    loop = asyncio.get_running_loop()
    with _LOCK:
        _CONNS[websocket] = {"channels": set(), "loop": loop, "last_seen": _now()}
    try:
        await websocket.send_text(json.dumps({
            "channel": "system", "event": "connected",
            "data": {"hint": "subscribe to task:{id} / video:{genId} / notifications / credits"},
            "ts": _now()}))
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                break  # 60s 无消息 → 断开（OPS 心跳兜底）
            try:
                m = json.loads(raw)
            except Exception:
                continue
            action = (m.get("action") or "").lower()
            payload = m.get("payload") or {}
            channels = payload.get("channels") or []
            if action == "subscribe":
                with _LOCK:
                    _CONNS[websocket]["channels"].update(channels)
                await websocket.send_text(json.dumps({
                    "channel": "system", "event": "subscribed",
                    "data": {"channels": list(_CONNS[websocket]["channels"])}, "ts": _now()}))
            elif action == "unsubscribe":
                with _LOCK:
                    _CONNS[websocket]["channels"].difference_update(channels)
            elif action == "ping":
                with _LOCK:
                    if websocket in _CONNS:
                        _CONNS[websocket]["last_seen"] = _now()
                await websocket.send_text(json.dumps({"action": "pong", "timestamp": _now()}))
            with _LOCK:
                if websocket in _CONNS:
                    _CONNS[websocket]["last_seen"] = _now()
    except Exception:
        pass
    finally:
        _drop(websocket)


def connection_count():
    with _LOCK:
        return len(_CONNS)


# ----------------------------------------------------------------------
# 导演 Agent · M3 悬浮球前端化（手册 §4.1）：三类导演事件 + translator 频道
# 以下封装均为薄包装，复用既有 publish()，非破坏性；WS 不可用时 publish 自动 no-op。
# ----------------------------------------------------------------------

def publish_director_notice(project_id, data=None):
    """director.notice：导演主动告知（发现的问题 / 下一步动作）。"""
    publish("director:%s" % project_id, "notice", data or {})


def publish_director_insight(project_id, data=None):
    """director.insight：导演判断 / 品控结论（可回看）。"""
    publish("director:%s" % project_id, "insight", data or {})


# translator 选型可视化频道事件名（契约：须与前端 composables/useDirectorOrb._onTranslator 严格对齐）
EVENT_TRANSLATOR_SELECTION = "selection"


def publish_translator_selection(project_id, trace_id, task_type, selected):
    """translator:{project_id}：推送「当前使用的模型/节点/参数」（决策可视化，设计 §7.3）。

    :param selected: 字典，建议含 checkpoint / node / loras / seed 等选型字段。
    """
    publish("translator:%s" % project_id, EVENT_TRANSLATOR_SELECTION, {
        "trace_id": trace_id,
        "task_type": task_type,
        "selected": selected or {},
        "timestamp": _now(),
    })
