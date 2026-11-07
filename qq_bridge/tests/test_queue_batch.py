# -*- coding: utf-8 -*-
"""
test_queue_batch.py —— 生图计划批量下发（2026-09-11）

验收：
  1. 批量导入 JSON 数组 → 每条成为独立队列任务，单消费者**依次执行**（顺序）
  2. local 通道走 /qq/image-generate + /t2i/tasks 轮询 → done + 抽图
  3. online 通道走 /t2i/online/generate 同步返回 → done + 抽图
  4. 缺 正面提示词 的条目被拒，其余正常入队（不阻塞整批）
  5. 中文字段 / 英文字段别名都能正确解析（宽/width、图片名称/name…）
  6. 计划内任务严格顺序执行：前一条 done 后一条才 started

机器判定：pytest assert + 非零退出码。免起真实后端（MockTransport）。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_queue_batch.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge import config as bridge_config  # noqa: E402
from src.plugins.qq_bridge.queue import (  # noqa: E402
    ST_DONE, ST_FAILED, ST_QUEUED, TaskQueue,
)

CFG = bridge_config.Config

PLAN_SAMPLE = json.dumps([
    {"序号": 1, "正面提示词": "a cat", "负面提示词": "blur", "图片名称": "cat1", "宽": 720, "高": 1280},
    {"序号": 2, "prompt": "a dog", "name": "dog2"},
    {"正面提示词": "", "图片名称": "bad"},  # 缺提示词 → 被拒
])


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data, "message": "ok"})


def local_transport():
    """本地通道：/qq/image-generate 给 task_id；/t2i/tasks/{id} 轮询到 succeeded。"""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/qq/image-generate"):
            return _ok({"task_id": "L1"})
        if "/t2i/tasks/" in p:
            if p.endswith("/stop"):
                return _ok({"stopped": True})
            return _ok({"status": "succeeded", "result": {"images": ["http://x/local.png"]}})
        return _ok({})
    return httpx.MockTransport(handler)


def online_transport():
    """在线通道：/t2i/online/generate 同步返回 images。"""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/t2i/online/generate"):
            return _ok({"task_id": "O1", "images": ["http://cdn/online.png"]})
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


def parse_plan(text: str):
    items = json.loads(text)
    return [TaskQueue._normalize_plan_item(it, "local") for it in items]


@pytest.mark.anyio
async def test_normalize_plan_fields(tmp_path):
    """中文键 + 英文别名都能解析；宽/高转 int；缺提示词判失败。"""
    res = parse_plan(PLAN_SAMPLE)
    a = res[0][0]
    assert a is not None, "第 1 条应解析成功"
    assert a["prompt"] == "a cat"
    assert a["params"]["negative"] == "blur"
    assert a["params"]["name"] == "cat1"
    assert a["params"]["width"] == 720 and a["params"]["height"] == 1280
    assert a["params"]["channel"] == "local"

    d = res[1][0]
    assert d["prompt"] == "a dog" and d["params"]["name"] == "dog2"

    bad = res[2]
    assert bad[0] is None, "缺提示词应判失败"
    assert "正面提示词" in (bad[1] or "")


@pytest.mark.anyio
async def test_batch_local_sequential_and_done(tmp_path):
    """local 通道：批量入队 → 单消费者依次执行 → 全部 done + 抽图。"""
    events: list[tuple] = []

    async def notifier(event, job):
        events.append((event, job.get("seq")))

    q = new_queue(tmp_path, local_transport(), notifier=notifier,
                  QUEUE_MAX=50, QUEUE_IDEMPOTENT_TTL=0,
                  POLL_INTERVAL=0.01, QUEUE_POLL_INTERVAL=0.01)
    res = await q.enqueue_batch(json.loads(PLAN_SAMPLE), "local", 100)
    assert res["ok"]
    assert res["accepted"] == 2, "有效条目应 2 条"
    assert res["rejected"] == 1, "缺提示词应被拒 1 条"
    assert res["errors"], "被拒条目应有错误文案"

    await q.start()
    try:
        for _ in range(200):
            await asyncio.sleep(0.02)
            if q.snapshot()["queued_count"] == 0 and len(q.snapshot()["history"]) >= 2:
                break
    finally:
        await q.stop()

    snap = q.snapshot()
    assert snap["queued_count"] == 0, "全部执行完队列应清空"
    hist = snap["history"]
    assert len(hist) == 2, "应 2 条完成，实际 %s" % len(hist)
    assert all(h["state"] == ST_DONE for h in hist), "都应 done"
    assert all(h["images"] == ["http://x/local.png"] for h in hist), "应抽到本地图"

    # 顺序执行：第 1 条 done 事件早于第 2 条 started
    started_seqs = [s for (e, s) in events if e == "started"]
    first_started_seq = min(started_seqs)
    last_started_seq = max(started_seqs)
    done1 = next(i for i, (e, s) in enumerate(events)
                 if e == "done" and s == first_started_seq)
    started2 = next(i for i, (e, s) in enumerate(events)
                   if e == "started" and s == last_started_seq)
    assert done1 < started2, "前一条 done 必须早于后一条 started（严格顺序）"


@pytest.mark.anyio
async def test_batch_online_sequential_and_done(tmp_path):
    """online 通道：批量入队 → 走 /t2i/online/generate 同步返回 → 全部 done + 抽图。"""
    events: list[tuple] = []

    async def notifier(event, job):
        events.append((event, job.get("seq")))

    q = new_queue(tmp_path, online_transport(), notifier=notifier,
                  QUEUE_MAX=50, QUEUE_IDEMPOTENT_TTL=0,
                  POLL_INTERVAL=0.01, QUEUE_POLL_INTERVAL=0.01)
    res = await q.enqueue_batch(json.loads(PLAN_SAMPLE), "online", 100)
    assert res["ok"]
    assert res["accepted"] == 2

    await q.start()
    try:
        for _ in range(200):
            await asyncio.sleep(0.02)
            if q.snapshot()["queued_count"] == 0 and len(q.snapshot()["history"]) >= 2:
                break
    finally:
        await q.stop()

    snap = q.snapshot()
    hist = snap["history"]
    assert len(hist) == 2, "在线通道也应执行完 2 条"
    assert all(h["state"] == ST_DONE for h in hist)
    assert all(h["images"] == ["http://cdn/online.png"] for h in hist), "应抽到在线图"
    # 在线通道不参与本地轮询，但仍是单消费者顺序执行
    assert "started" in [e for e, _ in events]
    assert "done" in [e for e, _ in events]


@pytest.mark.anyio
async def test_batch_rejects_non_array(tmp_path):
    """非法计划（非数组 / 解析失败）应明确报错，不静默。"""
    q = new_queue(tmp_path, local_transport(), QUEUE_MAX=50, QUEUE_IDEMPOTENT_TTL=0)
    r1 = await q.enqueue_batch({"a": 1}, "local", 100)
    assert not r1["ok"] and "数组" in (r1.get("error") or "")
    # enqueue_batch 接受 list，但 item 不是 dict
    r2 = await q.enqueue_batch(["不是对象"], "local", 100)
    assert r2["accepted"] == 0 and r2["rejected"] == 1


@pytest.mark.anyio
async def test_batch_hard_cap(tmp_path):
    """单次批量超过硬上限（BATCH_HARD_CAP）的条目被拒并附文案。"""
    q = new_queue(tmp_path, local_transport(), QUEUE_MAX=50, QUEUE_IDEMPOTENT_TTL=0)
    items = [{"正面提示词": "p%d" % i} for i in range(600)]
    res = await q.enqueue_batch(items, "local", 100)
    assert res["accepted"] == 500, "应截断到硬上限 500，实际 %s" % res["accepted"]
    assert res["rejected"] == 100, "超出部分应被拒"


# ---- 生图计划模板（2026-09-13 修复：文生图项此前读不出「模板」字段）----
@pytest.mark.anyio
async def test_t2i_plan_item_keeps_template_for_local():
    """单元：本地通道的文生图项必须把「模板」写进 params。"""
    it, err = TaskQueue._normalize_plan_item(
        {"正面提示词": "a cat", "模板": "tpl_abc"}, "local")
    assert err is None
    assert it["params"]["template_id"] == "tpl_abc"
    # 英文别名同样支持
    it2, _ = TaskQueue._normalize_plan_item(
        {"prompt": "a dog", "template_id": "tpl_xyz"}, "local")
    assert it2["params"]["template_id"] == "tpl_xyz"


@pytest.mark.anyio
async def test_t2i_plan_item_ignores_template_for_online():
    """单元：在线通道没有模板概念，带模板也应忽略（避免平台报错）。"""
    it, err = TaskQueue._normalize_plan_item(
        {"prompt": "a cat", "模板": "tpl_abc"}, "online")
    assert err is None
    assert "template_id" not in it["params"]


@pytest.mark.anyio
async def test_batch_t2i_template_is_sent_to_platform(tmp_path):
    """端到端：计划里的模板必须透传到主项目 /qq/image-generate。"""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/qq/image-generate"):
            seen.append(json.loads(request.content.decode("utf-8")))
            return _ok({"task_id": "TPL1"})
        if "/t2i/tasks/" in p:
            return _ok({"status": "succeeded", "result": {"images": ["http://x/tpl.png"]}})
        return _ok({})

    q = new_queue(tmp_path, httpx.MockTransport(handler), QUEUE_MAX=50,
                  QUEUE_IDEMPOTENT_TTL=0, POLL_INTERVAL=0.01, QUEUE_POLL_INTERVAL=0.01)
    res = await q.enqueue_batch([{"正面提示词": "a cat", "模板": "tpl_abc"}], "local", 100)
    assert res["ok"] and res["accepted"] == 1
    await q.start()
    try:
        for _ in range(200):
            await asyncio.sleep(0.02)
            if seen:
                break
    finally:
        await q.stop()
    assert seen, "应提交到 /qq/image-generate"
    assert seen[0].get("template_id") == "tpl_abc", "模板必须透传给主项目"
