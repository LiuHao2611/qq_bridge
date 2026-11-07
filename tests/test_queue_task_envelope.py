# -*- coding: utf-8 -*-
"""
test_queue_task_envelope.py —— 回归：_wait_task 必须认得住主项目 /t2i/tasks/{id}
的**真实响应信封** {code,data:{task:{...}}}（2026-09-15 事故）。

背景（事故）：主项目 routers.py t2i_task_detail 返回 ok({"task": {...}})，即
HTTP body = {"code":0,"data":{"task":{"status":"succeeded",...}}}。queue._unwrap()
去掉外层后得到 {"task": {...}}，而 _wait_task 旧代码读 data.get("status") → 恒为
None → 判不出终态 → 明明平台 3~6 分钟就出图了，桥接却一直轮询到 TASK_TIMEOUT(7200s)
才判「任务超时（超过 7200 秒未出图）」。表现为：任务中心里任务长时间「生成中」，
批次里全部「失败/重试失败」。

既有 tests/test_queue.py 的 mock 用的是**扁平** {"status": "succeeded"}，与真实信封
不符，所以bug长期未暴露。本测试刻意按**真实嵌套信封**断言。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_queue_task_envelope.py -v
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge import config as bridge_config  # noqa: E402
from src.plugins.qq_bridge.queue import ST_DONE, ST_FAILED, TaskQueue  # noqa: E402

CFG = bridge_config.Config


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data, "message": "ok"})


def make_transport(submit_handler, task_handler):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/qq/image-generate"):
            return submit_handler()
        if "/t2i/tasks/" in p and not p.endswith("/stop"):
            return task_handler()
        return _ok({})
    return httpx.MockTransport(handler)


def new_queue(tmp_path, transport, notifier=None, **cfg_override):
    for k, v in cfg_override.items():
        setattr(CFG, k, v)
    return TaskQueue(
        path=tmp_path / "queue.json",
        api_base="http://127.0.0.1:7788/api/v1",
        transport=transport,
        notifier=notifier,
    )


async def _run_batch(q, events, want, timeout=3.0):
    await q.enqueue(100, 0, "private", "一只猫", {"count": 2})
    t0 = time.time()
    await q.start()
    try:
        for _ in range(300):
            await asyncio.sleep(0.02)
            if want in events:
                break
    finally:
        await q.stop()
    return time.time() - t0


# --------------------------------------------------------------------------
# 1) 真实嵌套信封 + succeeded → 必须很快判 done（不是拖到超时）
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_wait_task_nested_envelope_success(tmp_path):
    events: list[str] = []

    async def notifier(event, job):
        events.append(event)

    real_body = _ok({"task": {                       # ← 主项目真实形态
        "task_id": "abc123", "status": "succeeded", "progress": 1,
        "result": {"images": ["http://x/1.png", "http://x/2.png"]},
    }})
    q = new_queue(
        tmp_path,
        make_transport(lambda: _ok({"task_id": "abc123"}), lambda: real_body),
        notifier=notifier,
        QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0, QUEUE_POLL_INTERVAL=0.01,
        POLL_INTERVAL=0.01,
    )
    elapsed = await _run_batch(q, events, "done")

    assert "done" in events, "嵌套信封下也必须识别 succeeded 为完成，实际事件=%s" % events
    h = q.snapshot()["history"]
    assert h and h[0]["state"] == ST_DONE, "最终状态应为 done，实际 %s" % (h and h[0]["state"])
    assert h[0]["images"] == ["http://x/1.png", "http://x/2.png"], (
        "应从 data.task.result.images 抽到图片，实际 %s" % h[0].get("images")
    )
    assert elapsed < 2.0, "应在秒级判完成而非空等 TASK_TIMEOUT，实际耗时 %.2fs" % elapsed
    assert q.snapshot()["queued_count"] == 0, "完成后出队"


# --------------------------------------------------------------------------
# 2) 真实嵌套信封 + failed → 必须识别失败并带出 error_message
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_wait_task_nested_envelope_failed(tmp_path):
    events: list[str] = []

    async def notifier(event, job):
        events.append(event)

    real_body = _ok({"task": {
        "task_id": "abc123", "status": "failed", "progress": 0,
        "error_message": "本地 ComfyUI 未启动（http://127.0.0.1:8188）",
    }})
    q = new_queue(
        tmp_path,
        make_transport(lambda: _ok({"task_id": "abc123"}), lambda: real_body),
        notifier=notifier,
        QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0, QUEUE_POLL_INTERVAL=0.01,
        POLL_INTERVAL=0.01,
    )
    elapsed = await _run_batch(q, events, "failed")

    assert "failed" in events, "嵌套信封下必须识别 failed，实际事件=%s" % events
    h = q.snapshot()["history"]
    assert h and h[0]["state"] == ST_FAILED
    assert "ComfyUI 未启动" in (h[0].get("error") or ""), (
        "应带出 data.task.error_message，实际 %s" % h[0].get("error")
    )
    assert elapsed < 2.0, "失败也应秒级判出，实际 %.2fs" % elapsed


# --------------------------------------------------------------------------
# 3) 向后兼容：扁平信封（旧/其他端点）仍要能识别成功
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_wait_task_flat_envelope_backward_compat(tmp_path):
    events: list[str] = []

    async def notifier(event, job):
        events.append(event)

    flat_body = _ok({"status": "succeeded", "result": {"images": ["http://x/9.png"]}})
    q = new_queue(
        tmp_path,
        make_transport(lambda: _ok({"task_id": "t9"}), lambda: flat_body),
        notifier=notifier,
        QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0, QUEUE_POLL_INTERVAL=0.01,
        POLL_INTERVAL=0.01,
    )
    await _run_batch(q, events, "done")
    assert "done" in events, "扁平信封必须保持兼容，实际=%s" % events
    assert q.snapshot()["history"][0]["images"] == ["http://x/9.png"]
