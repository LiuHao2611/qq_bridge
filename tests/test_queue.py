# -*- coding: utf-8 -*-
"""
test_queue.py —— QQ 侧持久有界任务队列验收（2026-09-08）

覆盖用户拍板的关键行为：
  1. 有界队列：QUEUE_MAX 满 → 明确拒绝并告知排队数（不无限堆积）
  2. 幂等：窗口内同 (user, prompt, params) 重复下发 → 返回原任务，不入第二条
  3. 持久化：落 data/queue.json，重建 TaskQueue 后队列恢复
  4. **平台忙退避**：队首遇 409 IMG_BUSY → 退避重试 QUEUE_BUSY_RETRY 次
     （用户拍板 3 次 × 5 分钟），用完后**通知用户并弹出队列**（state=failed）
  5. 正常流程：提交 → running → 轮询到 succeeded → done + 抽到图片
  6. 权限：普通用户只能取消自己的；插队/清空仅主人

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_queue.py -v
"""
from __future__ import annotations

import asyncio
import json
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
from src.plugins.qq_bridge.queue import (  # noqa: E402
    ST_DONE, ST_FAILED, ST_QUEUED, TaskQueue,
)

CFG = bridge_config.Config


# --------------------------------------------------------------------------
# MockTransport：免起真实后端，按 path 分派响应
# --------------------------------------------------------------------------
def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data, "message": "ok"})


def _busy(task_id: str = "busytask") -> httpx.Response:
    """平台忙：/qq/image-generate 返回 409 + IMG_BUSY（主项目实际行为）。"""
    return httpx.Response(
        409,
        json={"code": 409, "data": {"error": "IMG_BUSY"}, "message": "IMG_BUSY"},
    )


def make_transport(submit_handler, task_handler=None):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/qq/image-generate"):
            return submit_handler()
        if "/t2i/tasks/" in p and p.rstrip("/").count("/") >= 3:
            if p.endswith("/stop"):
                return _ok({"stopped": True})
            return (task_handler or (lambda: _ok({"status": "succeeded",
                                                  "result": {"images": ["http://x/1.png"]}})))()
        return _ok({})
    return httpx.MockTransport(handler)


def new_queue(tmp_path, transport, notifier=None, **cfg_override):
    """建一个隔离的 TaskQueue（每个用例独立文件 + 独立配置）。"""
    for k, v in cfg_override.items():
        setattr(CFG, k, v)
    q = TaskQueue(
        path=tmp_path / "queue.json",
        api_base="http://127.0.0.1:7788/api/v1",
        transport=transport,
        notifier=notifier,
    )
    return q


# --------------------------------------------------------------------------
# 1) 有界队列
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_bounded_queue_rejects_overflow(tmp_path):
    """QUEUE_MAX 满 → 拒绝入队并告知排队数（借鉴 botatobot bounded queue）。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=3, QUEUE_IDEMPOTENT_TTL=0)
    for i in range(3):
        r = await q.enqueue(100 + i, 0, "private", "prompt-%d" % i, {})
        assert r["ok"], "前 %d 条应入队成功：%s" % (i + 1, r.get("error"))
    r = await q.enqueue(999, 0, "private", "overflow", {})
    assert not r["ok"], "队列已满时必须拒绝（有界）"
    assert "3/3" in (r.get("error") or ""), (
        "拒绝文案应含当前排队数，实际：%s" % r.get("error")
    )
    assert q.snapshot()["queued_count"] == 3, "队列不得超过 QUEUE_MAX"


# --------------------------------------------------------------------------
# 2) 幂等
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_idempotent_dedup_within_ttl(tmp_path):
    """幂等窗口内同 user+prompt+params → 返回原任务，不产生第二条。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=60)
    r1 = await q.enqueue(100, 0, "private", "一只猫", {"count": 2})
    r2 = await q.enqueue(100, 0, "private", "一只猫", {"count": 2})
    assert r1["ok"] and r2["ok"]
    assert r2.get("duplicate") is True, "第二次应识别为重复（幂等）"
    assert r2["job"]["job_id"] == r1["job"]["job_id"], "应返回第一次那条任务"
    assert q.snapshot()["queued_count"] == 1, "幂等窗口内只应有 1 条"

    # 不同参数 → 不算重复
    r3 = await q.enqueue(100, 0, "private", "一只猫", {"count": 4})
    assert not r3.get("duplicate"), "参数不同不应判重复"
    assert q.snapshot()["queued_count"] == 2


@pytest.mark.anyio
async def test_idempotent_ttl_expired(tmp_path):
    """超过 TTL → 不再判重复（幂等窗口过期）。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=1)
    r1 = await q.enqueue(100, 0, "private", "一只猫", {})
    # 手动把入队时间提前 10s，模拟已过期
    r1["job"]["enqueued_at"] = time.time() - 10
    r2 = await q.enqueue(100, 0, "private", "一只猫", {})
    assert not r2.get("duplicate"), "超过幂等窗口应允许重新入队"


# --------------------------------------------------------------------------
# 3) 持久化
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_persistence_survives_restart(tmp_path):
    """落盘 → 重建 TaskQueue 后队列恢复（bridge 重启不丢任务）。"""
    path = tmp_path / "queue.json"
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    await q.enqueue(100, 0, "private", "重启前投的图", {})
    await q.enqueue(200, 0, "private", "第二条", {})
    assert path.exists(), "队列应已落盘"

    q2 = TaskQueue(path=path, api_base="http://127.0.0.1:7788/api/v1",
                   transport=make_transport(lambda: _ok({"task_id": "t1"})))
    snap = q2.snapshot()
    assert snap["queued_count"] == 2, "重启后应恢复 2 条，实际 %s" % snap["queued_count"]
    prompts = [j["prompt"] for j in snap["queued"]]
    assert "重启前投的图" in prompts, "重启后提示词应完整恢复"


@pytest.mark.anyio
async def test_restart_marks_interrupted_running_as_failed(tmp_path):
    """重启时 running 任务无轮询上下文 → 标 failed（不静默丢失）。"""
    path = tmp_path / "queue.json"
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    await q.enqueue(100, 0, "private", "跑到一半的任务", {})
    for j in q._jobs:
        j["state"] = "running"
    q._save()

    q2 = TaskQueue(path=path, api_base="http://127.0.0.1:7788/api/v1",
                   transport=make_transport(lambda: _ok({"task_id": "t1"})))
    for j in q2._jobs:
        assert j["state"] == ST_FAILED, "重启后 running 应标 failed，实际 %s" % j["state"]
        assert "重启" in (j.get("error") or ""), "错误文案应说明是重启导致"


# --------------------------------------------------------------------------
# 4) 平台忙退避（用户拍板：3 次 × 5 分钟 → 通知并出队）
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_busy_retry_then_give_up_and_pop(tmp_path):
    """核心验收：队首遇 409 IMG_BUSY → 退避重试 3 次；仍忙 → 通知 + 弹出队列。

    真实配置是 3 次 × 300s = 15 分钟；测试把 backoff 压到 0.01s 保证秒级完成，
    但**次数语义必须一致**：busy_retries 累加到 >3 才放弃。
    """
    events: list[tuple] = []

    async def notifier(event, job):
        events.append((event, job.get("seq")))

    q = new_queue(tmp_path, make_transport(lambda: _busy()), notifier=notifier,
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0,
                  QUEUE_BUSY_RETRY=3, QUEUE_BUSY_BACKOFF=0.01,
                  QUEUE_POLL_INTERVAL=0.01)
    await q.enqueue(100, 0, "private", "平台忙时的任务", {})
    await q.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.02)
            if any(e == "failed" for e, _ in events):
                break
    finally:
        await q.stop()

    busy_events = [e for e, _ in events if e == "busy"]
    failed_events = [e for e, _ in events if e == "failed"]
    assert len(busy_events) == 3, (
        "应退避重试 3 次（用户拍板），实际 busy 通知 %d 次" % len(busy_events)
    )
    assert len(failed_events) == 1, (
        "重试用完后必须通知失败 1 次，实际 %d 次" % len(failed_events)
    )
    assert q.snapshot()["queued_count"] == 0, "失败后必须弹出队列，不能堵住后面的人"
    h = q.snapshot()["history"]
    assert h and h[0]["state"] == ST_FAILED
    assert "繁忙" in (h[0].get("error") or ""), "错误文案应说明平台繁忙：%s" % h[0].get("error")


@pytest.mark.anyio
async def test_busy_keeps_head_position(tmp_path):
    """退避期间**不弹出队**：队首位置保持，后面的任务不能插到前面。"""
    q = new_queue(tmp_path, make_transport(lambda: _busy()),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0,
                  QUEUE_BUSY_RETRY=99, QUEUE_BUSY_BACKOFF=0.01,
                  QUEUE_POLL_INTERVAL=0.01)
    await q.enqueue(100, 0, "private", "队首（会一直忙）", {})
    await q.enqueue(200, 0, "private", "第二位", {})
    await q.start()
    try:
        for _ in range(20):
            await asyncio.sleep(0.02)
            if int(q._jobs[0].get("busy_retries") or 0) >= 2:
                break
    finally:
        await q.stop()
    snap = q.snapshot()
    assert snap["queued_count"] == 2, "退避期间两条都还在队列里"
    assert snap["queued"][0]["prompt"] == "队首（会一直忙）", "队首位置必须保持不变"


# --------------------------------------------------------------------------
# 5) 正常流程
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_normal_flow_done_with_images(tmp_path):
    """提交 → running → 轮询 succeeded → done + 抽到图片 URL。"""
    events: list[str] = []

    async def notifier(event, job):
        events.append(event)

    q = new_queue(
        tmp_path,
        make_transport(
            lambda: _ok({"task_id": "abc123"}),
            task_handler=lambda: _ok({"status": "succeeded",
                                      "result": {"images": ["http://x/1.png", "http://x/2.png"]}}),
        ),
        notifier=notifier,
        QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0, QUEUE_POLL_INTERVAL=0.01,
        POLL_INTERVAL=0.01,
    )
    await q.enqueue(100, 0, "private", "一只猫", {"count": 2})
    await q.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.02)
            if "done" in events:
                break
    finally:
        await q.stop()

    assert "started" in events, "提交成功应通知 started"
    assert "done" in events, "任务完成应通知 done"
    h = q.snapshot()["history"]
    assert h and h[0]["state"] == ST_DONE, "最终状态应为 done"
    assert h[0]["images"] == ["http://x/1.png", "http://x/2.png"], (
        "应抽到结果图 URL，实际 %s" % h[0].get("images")
    )
    assert q.snapshot()["queued_count"] == 0, "完成后出队"


@pytest.mark.anyio
async def test_submit_error_fails_job(tmp_path):
    """提交报非 IMG_BUSY 错误（如 IMG_NO_TEMPLATE）→ 直接失败出队，不退避。"""
    def submit():
        return httpx.Response(422, json={"code": 422,
                                         "data": {"error": "IMG_NO_TEMPLATE"},
                                         "message": "IMG_NO_TEMPLATE"})
    events: list[str] = []

    async def notifier(event, job):
        events.append(event)

    q = new_queue(tmp_path, make_transport(submit), notifier=notifier,
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0, QUEUE_POLL_INTERVAL=0.01)
    await q.enqueue(100, 0, "private", "没模板的任务", {})
    await q.start()
    try:
        for _ in range(50):
            await asyncio.sleep(0.02)
            if "failed" in events:
                break
    finally:
        await q.stop()
    assert "failed" in events, "非忙错误应立即失败（不退避 5 分钟）"
    assert q.snapshot()["history"][0]["error"].find("IMG_NO_TEMPLATE") >= 0


# --------------------------------------------------------------------------
# 6) 权限：取消 / 插队 / 清空
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_cancel_permission(tmp_path):
    """普通用户只能取消自己的；主人可取消任意。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    r = await q.enqueue(100, 0, "private", "别人的图", {})
    seq = str(r["job"]["seq"])

    bad = await q.cancel(seq, user_id=666, is_owner=False)
    assert not bad["ok"], "非本人（非主人）不能取消别人的任务"
    assert "不是你的任务" in (bad.get("error") or "")

    ok = await q.cancel(seq, user_id=666, is_owner=True)
    assert ok["ok"], "主人应能取消任意任务"
    assert q.snapshot()["queued_count"] == 0


@pytest.mark.anyio
async def test_cancel_own_task(tmp_path):
    """本人可以取消自己的任务。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    r = await q.enqueue(100, 0, "private", "我自己的图", {})
    ok = await q.cancel(str(r["job"]["seq"]), user_id=100, is_owner=False)
    assert ok["ok"], "本人应能取消自己的任务"


@pytest.mark.anyio
async def test_promote_owner_only(tmp_path):
    """插队仅主人可用。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    await q.enqueue(100, 0, "private", "第一条", {})
    r2 = await q.enqueue(200, 0, "private", "第二条", {})
    seq2 = str(r2["job"]["seq"])

    bad = await q.promote(seq2, is_owner=False)
    assert not bad["ok"], "非主人不能插队"

    ok = await q.promote(seq2, is_owner=True)
    assert ok["ok"], "主人应能插队"
    snap = q.snapshot()
    assert snap["queued"][0]["prompt"] == "第二条", "插队后应排到队首"


@pytest.mark.anyio
async def test_clear_owner_only(tmp_path):
    """清空队列仅主人可用，且只清 queued（不动 running）。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    await q.enqueue(100, 0, "private", "a", {})
    await q.enqueue(100, 0, "private", "b", {})
    q._jobs[0]["state"] = "running"

    bad = await q.clear(is_owner=False)
    assert not bad["ok"], "非主人不能清空"

    ok = await q.clear(is_owner=True)
    assert ok["ok"] and ok.get("cleared") == 1, "只应清掉 1 条 queued，实际 %s" % ok
    left = [j for j in q._jobs if j["state"] == "running"]
    assert len(left) == 1, "running 的任务不能被清空（避免打断正在跑的）"


# --------------------------------------------------------------------------
# 7) 查找与查询
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_find_by_seq_jobid_prompt(tmp_path):
    """按 seq / job_id 前缀 / 提示词片段都能找到任务。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    r = await q.enqueue(100, 0, "private", "赛博朋克城市夜景", {})
    job = r["job"]
    assert q.get(str(job["seq"])) is not None, "按 seq 应能找到"
    assert q.get(job["job_id"][:8]) is not None, "按 job_id 前缀应能找到"
    assert q.get("赛博朋克") is not None, "按提示词片段应能找到"
    assert q.get("不存在的东西") is None, "查不到应返回 None"


@pytest.mark.anyio
async def test_recent_filters_by_user(tmp_path):
    """recent(user_id) 只看该用户的任务。"""
    q = new_queue(tmp_path, make_transport(lambda: _ok({"task_id": "t1"})),
                  QUEUE_MAX=10, QUEUE_IDEMPOTENT_TTL=0)
    await q.enqueue(100, 0, "private", "甲的图", {})
    await q.enqueue(200, 0, "private", "乙的图", {})
    mine = q.recent(user_id=100)
    assert len(mine) == 1 and mine[0]["prompt"] == "甲的图"
    assert len(q.recent()) == 2, "不传 user_id 应返回全部"
