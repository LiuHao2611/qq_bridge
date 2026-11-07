# -*- coding: utf-8 -*-
"""taskmgr 验收测试（设计文档 §3.4 / §7.1 / §7.3 / §7.4）。

用 httpx.MockTransport 模拟主项目后端 7788，不依赖真实平台与网络，
覆盖：在线同步通道、本地异步轮询、4xx 不重试、5xx 退避重试、
视频端点未开通的诚实回传、每用户并发 ≤1、源图缺失。
"""
import asyncio

import httpx
import pytest

from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.dispatcher import QQEvent
from src.plugins.qq_bridge.intent import Route
from src.plugins.qq_bridge.session import Session
from src.plugins.qq_bridge.taskmgr import TaskMgr, _abs_url

from conftest import run

API = "http://manju.test/api/v1"


def make_handler(state=None):
    state = state if state is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        state.setdefault("calls", []).append(p)

        if p.endswith("/t2i/online/generate"):
            return httpx.Response(200, json={
                "code": 0,
                "data": {"task_id": "onl_1", "images": [{"url": "/file?name=a.png"}]},
            })

        if p.endswith("/t2i/online/edit"):
            return httpx.Response(200, json={
                "code": 0,
                "data": {"task_id": "onl_2", "images": [{"url": "/file?name=b.png"}]},
            })

        if p.endswith("/qq/image-generate"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "loc_1"}})

        if p.endswith("/t2i/edit"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "loc_2", "mode": "upscale"}})

        if "/t2i/tasks/" in p:
            state["poll"] = state.get("poll", 0) + 1
            if state["poll"] == 1:
                return httpx.Response(200, json={"code": 0, "data": {
                    "task": {"status": "running", "progress": 10, "result": {}}}})
            return httpx.Response(200, json={"code": 0, "data": {
                "task": {"status": "succeeded", "progress": 100,
                         "result": {"images": [{"url": "/file?name=c.png"}]}}}})

        if p.endswith("/qq/video-generate"):
            return httpx.Response(404, json={"detail": "Not Found"})

        if p.startswith("/api/v1/img/"):
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nFAKE")

        return httpx.Response(404, json={"detail": "Not Found"})

    return handler


def mgr_with(handler):
    return TaskMgr(api_base=API, channel="online", transport=httpx.MockTransport(handler))


def mk_ev(user_id=10001, group=False):
    return QQEvent(
        user_id=user_id,
        group_id=20001 if group else 0,
        message_type="group" if group else "private",
        raw_text="一只赛博朋克猫",
        trace_id="trace-test",
    )


def mk_sess(pending=None):
    s = Session(user_id=10001)
    s.pending_image = pending
    return s


@pytest.fixture(autouse=True)
def _env():
    Config.MANJU_API_BASE = API
    # _abs_url 用 Config.MANJU_FILE_BASE 拼绝对地址（非构造函数注入的 api_base），
    # 不同步置成测试基址会让用例读到 .env 里的 127.0.0.1:7788 而假失败。
    Config.MANJU_FILE_BASE = API
    Config.POLL_INTERVAL = 0.01
    yield


# ---------------- §7.1 在线通道（默认，同步返回）----------------

def test_online_t2i_returns_absolute_image_urls():
    state = {}
    mgr = mgr_with(make_handler(state))
    r = Route(kind="t2i", api="/t2i/online/generate", payload={"prompt": "一只赛博朋克猫"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is True
    assert out["images"] == ["http://manju.test/api/v1/file?name=a.png"]
    assert any(p.endswith("/t2i/online/generate") for p in state["calls"])


def test_online_i2i_uploads_source_image():
    state = {}
    mgr = mgr_with(make_handler(state))
    r = Route(kind="i2i", api="/t2i/online/edit", payload={"prompt": "改成水彩风", "mode": "img2img"})
    ev = mk_ev()
    ev.images = [API + "/img/source.png"]
    out = run(mgr.run(r, ev, mk_sess(pending=API + "/img/source.png")))

    assert out["ok"] is True
    assert out["images"] == ["http://manju.test/api/v1/file?name=b.png"]


def test_i2i_without_source_image_reports_clear_error():
    mgr = mgr_with(make_handler())
    r = Route(kind="i2i", api="/t2i/online/edit", payload={"prompt": "改成水彩风"})
    out = run(mgr.run(r, mk_ev(), mk_sess(pending=None)))

    assert out["ok"] is False
    assert "请先发送一张图片" in out["error"]


# ---------------- §7.1 本地通道（异步轮询 + 串行）----------------

def test_local_t2i_polls_until_succeeded():
    state = {}
    mgr = TaskMgr(api_base=API, channel="local", transport=httpx.MockTransport(make_handler(state)))
    r = Route(kind="t2i", api="/t2i/generate", payload={"prompt": "猫"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is True
    assert out["images"] == ["http://manju.test/api/v1/file?name=c.png"]
    assert state["poll"] == 2                      # 第一次 running、第二次 succeeded
    assert any(p.endswith("/qq/image-generate") for p in state["calls"])


def test_local_task_failure_is_reported():
    def handler(request):
        p = request.url.path
        if p.endswith("/qq/image-generate"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "loc_1"}})
        if "/t2i/active-task" in p:
            # 提交前空闲检查必须返回「无进行中任务」：否则 _wait_local_idle 会把 failed 误判为
            # 仍占用队列 → while True 无限等 → 卡死（只能靠 --timeout 杀掉）。
            return httpx.Response(200, json={"code": 0, "data": {"task": None}})
        return httpx.Response(200, json={"code": 0, "data": {
            "task": {"status": "failed", "error_message": "ComfyUI OOM"}}})

    mgr = TaskMgr(api_base=API, channel="local", transport=httpx.MockTransport(handler))
    r = Route(kind="t2i", api="/t2i/generate", payload={"prompt": "猫"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is False
    assert "ComfyUI OOM" in out["error"]


# ---------------- REDUN-02：4xx 不重试 / 5xx 退避重试 ----------------

def test_business_error_is_returned_without_retry(monkeypatch):
    state = {}

    def handler(request):
        state.setdefault("calls", []).append(request.url.path)
        return httpx.Response(200, json={
            "code": 400, "data": {"error": "未配置API Key，请在设置中填写"}})

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    mgr = mgr_with(handler)
    r = Route(kind="t2i", api="/t2i/online/generate", payload={"prompt": "猫"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is False
    assert "API Key" in out["error"]
    assert len(state["calls"]) == 1        # 4xx 语义错误：不重试


def test_server_error_retries_three_times_then_fails(monkeypatch):
    state = {}

    def handler(request):
        state.setdefault("calls", []).append(request.url.path)
        return httpx.Response(500, json={"detail": "boom"})

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    mgr = mgr_with(handler)
    r = Route(kind="t2i", api="/t2i/online/generate", payload={"prompt": "猫"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is False
    assert len(state["calls"]) == 3        # REDUN-02：最多 3 次


def test_transient_error_then_success(monkeypatch):
    state = {}

    def handler(request):
        state.setdefault("calls", []).append(request.url.path)
        if len(state["calls"]) == 1:
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(200, json={"code": 0, "data": {
            "task_id": "onl_1", "images": [{"url": "/file?name=a.png"}]}})

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    mgr = mgr_with(handler)
    r = Route(kind="t2i", api="/t2i/online/generate", payload={"prompt": "猫"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is True
    assert len(state["calls"]) == 2


# ---------------- §3.4 每用户并发 ≤1 ----------------

def test_same_user_cannot_run_two_tasks():
    mgr = mgr_with(make_handler())
    mgr._running_users.add(10001)          # 模拟该用户已有任务在跑
    r = Route(kind="t2i", api="/t2i/online/generate", payload={"prompt": "猫"})
    out = run(mgr.run(r, mk_ev(user_id=10001), mk_sess()))

    assert out["ok"] is False
    assert "正在进行中" in out["error"]


def test_different_users_can_run_concurrently():
    mgr = mgr_with(make_handler())
    mgr._running_users.add(10002)
    r = Route(kind="t2i", api="/t2i/online/generate", payload={"prompt": "猫"})
    out = run(mgr.run(r, mk_ev(user_id=10001), mk_sess()))
    assert out["ok"] is True


# ---------------- §7.4 视频端点未开通时的诚实回传 ----------------

def test_video_endpoint_missing_reports_honestly():
    """§7.4 方案 B 端点尚未在主项目实现 → 明确回传"未开通"，不假装能生成。"""
    mgr = mgr_with(make_handler())
    r = Route(kind="video", api="/qq/video-generate", payload={"prompt": "航拍城市"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is False
    assert "尚未开通" in out["error"]
    assert "/api/v1/qq/video-generate" in out["error"]


def test_video_endpoint_success_returns_url():
    def handler(request):
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "v1", "video_url": "/file?name=v.mp4"}})

    mgr = mgr_with(handler)
    r = Route(kind="video", api="/qq/video-generate", payload={"prompt": "航拍城市"})
    out = run(mgr.run(r, mk_ev(), mk_sess()))

    assert out["ok"] is True
    assert out["video_url"] == "http://manju.test/api/v1/file?name=v.mp4"


# ---------------- 工具函数 ----------------

def test_abs_url_conversion():
    assert _abs_url("/file?name=a.png") == API + "/file?name=a.png"
    assert _abs_url("https://cdn.x/a.png") == "https://cdn.x/a.png"
    assert _abs_url("base64://AAAA") == "base64://AAAA"
    assert _abs_url("") == ""


def test_unsupported_route_kind():
    mgr = mgr_with(make_handler())
    r = Route(kind="weird", api="", payload={})
    out = run(mgr.run(r, mk_ev(), mk_sess()))
    assert out["ok"] is False
    assert "未支持的路由" in out["error"]
