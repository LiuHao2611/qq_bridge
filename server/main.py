# -*- coding: utf-8 -*-
"""万能短剧 AI 创作平台 · 本地后端（personal_local_only）。

监听 127.0.0.1:7788（SCHEMA_INDEX §1 api_base）。
纯 API 服务：只提供 /api/v1、/file、/docs，不承载任何前端页面。
前端 SPA 由 web_app.py 在 8099 统一托管并反代 /api/v1。

CLOSE-03（日志）：请求中间件记录 method/path/status/耗时/request_id；
未捕获异常与已知 ApiError 均写入 operation_logs(status='failed') 并打印结构化日志，
杜绝静默吞错。
"""
import os
import time
import uuid

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from db import init_db, DB_PATH, get_conn, log_operation, uid, SINGLE_USER_ID
from common import ApiError, envelope, setup_logging, LOG_DIR
import routers
import memoir_routes
from worker import start as worker_start, stop as worker_stop
from points_engine import reconcile_points
from realtime import handle_ws

logger = setup_logging("backend")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(BASE, "outputs")

app = FastAPI(title="万能短剧 AI 创作平台 · 本地服务", version="1.4-agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(routers.api)
app.include_router(memoir_routes.api)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """KB §03 §18 实时推送网关入口（任务/视频/通知/积分通道）。"""
    await handle_ws(websocket)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """CLOSE-03：每个请求绑定 request_id，记录 method/path/status/耗时。"""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = rid
    start = time.time()
    try:
        response = await call_next(request)
    except Exception as e:
        dur = (time.time() - start) * 1000
        logger.error("[REQ] %s %s -> 500 req_id=%s %.1fms err=%s"
                     % (request.method, request.url.path, rid, dur, e))
        raise
    dur = (time.time() - start) * 1000
    response.headers["x-request-id"] = rid
    logger.info("[REQ] %s %s -> %d req_id=%s %.1fms"
                % (request.method, request.url.path, response.status_code, rid, dur))
    return response


def _log_api_error(path, code, message):
    """CLOSE-03：将失败写入 operation_logs，便于审计追溯。"""
    try:
        conn = get_conn()
        try:
            log_operation(conn, "api_error", "request", None, object_name=path,
                          status="failed", diff={"code": code, "message": str(message)[:300]})
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    rid = getattr(request.state, "request_id", "")
    logger.error("[ERR] ApiError path=%s code=%s msg=%s req_id=%s"
                 % (request.url.path, exc.code, exc.message, rid))
    _log_api_error(request.url.path, exc.code, exc.message)
    return JSONResponse(status_code=exc.http_status,
                        content=envelope(exc.code, None, exc.message, rid))


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "")
    logger.error("[ERR] unhandled path=%s err=%s req_id=%s"
                 % (request.url.path, exc, rid))
    _log_api_error(request.url.path, 40001, "系统内部错误: %s" % str(exc)[:200])
    return JSONResponse(status_code=500,
                        content=envelope(40001, None, "系统内部错误: %s" % str(exc)[:200], rid))


@app.on_event("startup")
def on_startup():
    init_db()
    reconcile_points()   # CLOSE-04 启动对账：历史 estimated 行翻为 deducted，防误退膨胀
    worker_start()       # 内部含 CLOSE-01 崩溃恢复（running -> queued）


@app.on_event("shutdown")
def on_shutdown():
    worker_stop()


# 输出文件（视频/图）：result_url = /file?name=xxx
@app.get("/file")
async def serve_file(name: str, request: Request):
    # 防目录穿越
    safe = os.path.basename(name)
    p = os.path.join(OUTPUTS, safe)
    # 兼容 routers_compiled 等以 server/ 为 BASE 写入 server/outputs/ 的历史文件
    if not os.path.isfile(p):
        p = os.path.join(BASE, "server", "outputs", safe)
    if not os.path.isfile(p):
        p = os.path.join(BASE, "server", ".uploads", safe)
    if not os.path.isfile(p):
        logger.warning("[FILE] 404 name=%s tried=%s ua=%s" % (safe, p, request.headers.get("User-Agent", "-")))
        return JSONResponse(status_code=404, content=envelope(40001, None, "文件不存在"))
    logger.info("[FILE] 200 name=%s path=%s bytes=%d ua=%s" % (
        safe, p, os.path.getsize(p), request.headers.get("User-Agent", "-")))
    return FileResponse(p)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7788, log_level="info")
