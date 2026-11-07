# -*- coding: utf-8 -*-
"""
test_queue_upscale.py —— 图片批量放大（upscale）队列任务验收（2026-09-12）

覆盖：
  1. 计划解析：中文键「类型/图片/倍数」→ kind=upscale；缺图片 → 拒绝并给原因
  2. 倍数收敛：1→2、3→2、4→4、7→4、非法→2；通道强制 local
  3. 执行链：下载原图(/file?name=) → multipart POST /t2i/edit(mode=upscale)
     → 轮询 succeeded → done + 抽到结果图
  4. 失败路径：下载 404 → failed + error 落 job（CLOSE-02 不静默）
  5. 端到端：enqueue_batch(["类型":"放大",...]) → 消费者自动跑完 → done

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_queue_upscale.py -v
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

# 让 `src.plugins.qq_bridge.queue` 可导入（queue 不依赖 nonebot，可裸跑）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge import config as bridge_config  # noqa: E402
from src.plugins.qq_bridge.queue import ST_DONE, ST_FAILED, TaskQueue  # noqa: E402

CFG = bridge_config.Config


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data, "message": "ok"})


def make_upscale_transport(calls: list, *, download_status=200, task_status="succeeded"):
    """按 path 分派：/file 下载原图、/t2i/edit 提交、/t2i/tasks/{id} 轮询。

    calls 用于断言请求事实（multipart 字段 / 下载路径）。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/file":
            calls.append({"op": "download", "name": request.url.params.get("name")})
            if download_status != 200:
                return httpx.Response(download_status, text="nope")
            return httpx.Response(200, content=b"\x89PNG-fake-bytes")
        if p.endswith("/t2i/edit"):
            body = request.read()
            calls.append({"op": "edit", "multipart": b'name="mode"' in body,
                          "mode_upscale": b'name="mode"\r\n\r\nupscale' in body,
                          "is_multipart": b"multipart/form-data" in request.headers.get(
                              "content-type", "").encode()})
            return _ok({"task_id": "up1", "mode": "upscale"})
        if "/t2i/tasks/" in p:
            return _ok({"status": task_status, "task_id": "up1",
                        "result": {"images": [{"url": "/file?name=up_result.png",
                                               "width": 2048, "height": 2048}]}})
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


async def _wait_idle(q: TaskQueue, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = q.snapshot()
        if not snap["queued_count"] and not snap["running_count"]:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("任务未在 %.0fs 内收敛" % timeout)


# ---------------------------------------------------------------------------
# 1) 计划解析
# ---------------------------------------------------------------------------
def test_normalize_upscale_plan_item():
    norm, why = TaskQueue._normalize_plan_item(
        {"类型": "放大", "图片": "/file?name=t2i_a.png", "倍数": 4, "图片名称": "测试图"},
        "local",
    )
    assert norm is not None, "合法放大项不应被拒：%s" % why
    p = norm["params"]
    assert p["kind"] == "upscale"
    assert p["channel"] == "local", "放大通道必须强制 local（/t2i/edit 是本地端点）"
    assert p["image"] == "/file?name=t2i_a.png"
    assert p["scale"] == 4
    assert norm["prompt"] == "测试图", "prompt 缺省应回退 图片名称"
    # 英文别名 + 倍数缺省
    norm2, _ = TaskQueue._normalize_plan_item({"kind": "upscale", "image": "x.png"}, "local")
    assert norm2["params"]["scale"] == 2, "倍数缺省应为 2"


def test_normalize_upscale_reject_missing_image():
    norm, why = TaskQueue._normalize_plan_item({"类型": "放大", "倍数": 2}, "local")
    assert norm is None, "缺 图片 必须拒绝"
    assert "图片" in why, "拒绝原因要提到 图片，实际：%s" % why


@pytest.mark.parametrize("raw,expect", [(1, 2), (2, 2), (3, 2), (4, 4), (7, 4), ("x", 2), (None, 2)])
def test_normalize_upscale_scale_clamp(raw, expect):
    norm, _ = TaskQueue._normalize_plan_item(
        {"类型": "放大", "图片": "a.png", "倍数": raw}, "local")
    assert norm["params"]["scale"] == expect


def test_normalize_t2i_plan_unaffected():
    """回归保护：原「文生图计划」解析行为不变（无 kind 字段 = t2i）。"""
    norm, why = TaskQueue._normalize_plan_item({"正面提示词": "一只猫", "宽": 512}, "local")
    assert norm is not None, why
    assert "kind" not in norm["params"], "存量 t2i 计划不应新增 kind 字段（兼容旧 queue.json）"


# ---------------------------------------------------------------------------
# 2) 执行链（MockTransport 端到端，消费者自动跑）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_process_upscale_happy_path(tmp_path):
    calls: list = []
    events: list = []

    def _note(event, job):
        events.append((event, job))

    q = new_queue(tmp_path, make_upscale_transport(calls), _note,
                  POLL_INTERVAL=0.05, TASK_TIMEOUT=5, HTTP_TIMEOUT_IMG=30,
                  QUEUE_IDEMPOTENT_TTL=0)
    res = await q.enqueue_batch(
        [{"类型": "放大", "图片": "/file?name=t2i_a.png", "倍数": 2, "图片名称": "A"}],
        "local", 100)
    assert res["ok"] and res["accepted"] == 1, res
    await _wait_idle(q)
    assert calls, "应至少发出一次 HTTP 调用"
    dl = [c for c in calls if c["op"] == "download"]
    assert dl and dl[0]["name"] == "t2i_a.png", "应按 /file?name= 下载原图"
    ed = [c for c in calls if c["op"] == "edit"]
    assert ed and ed[0]["is_multipart"] and ed[0]["multipart"] and ed[0]["mode_upscale"], \
        "提交 /t2i/edit 必须是 multipart 且 mode=upscale，实际：%s" % ed
    hist = q.snapshot()["history"]
    assert hist and hist[0]["state"] == ST_DONE, "任务应 done，实际：%s" % (hist or [{}])[0].get("state")
    assert hist[0]["task_id"] == "up1"
    # 2026-09-20 起队列侧 _abs_url 会把 /file?name= 相对路径拼成绝对 URL
    # （NapCat 无法解析相对路径，retcode=1200）——期望值按当前配置动态计算，
    # 不硬编码，避免被 .env 的 MANJU_FILE_BASE 干扰。
    _base = str(getattr(CFG, "MANJU_FILE_BASE", "") or "http://127.0.0.1:7788").rstrip("/")
    assert hist[0]["images"] == [_base + "/file?name=up_result.png"], \
        "应抽到放大结果图（绝对化后），实际：%s" % (hist[0]["images"],)
    kinds = [ev for ev, j in events if (j.get("params") or {}).get("kind") == "upscale"]
    assert kinds, "通知事件应带 kind=upscale，实际：%s" % events


@pytest.mark.anyio
async def test_process_upscale_download_404_fails_job(tmp_path):
    """下载失败 → job failed + error 落库（CLOSE-02 禁止静默吞）。"""
    calls: list = []
    q = new_queue(tmp_path, make_upscale_transport(calls, download_status=404), None,
                  POLL_INTERVAL=0.05, TASK_TIMEOUT=5, QUEUE_IDEMPOTENT_TTL=0)
    res = await q.enqueue_batch([{"类型": "放大", "图片": "/file?name=ghost.png"}],
                                "local", 100)
    assert res["ok"] and res["accepted"] == 1
    await _wait_idle(q)
    hist = q.snapshot()["history"]
    assert hist and hist[0]["state"] == ST_FAILED
    assert "下载原图失败" in (hist[0]["error"] or ""), "错误要写明下载失败，实际：%s" % hist[0]["error"]
