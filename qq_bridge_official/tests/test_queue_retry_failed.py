# -*- coding: utf-8 -*-
"""
test_queue_retry_failed.py —— 失败计划/任务的重跑能力验收（2026-09-12 补）

覆盖（铁律：机器判定 assert + 非零退出码）：
  1. batch_retry_failed：批次内所有 failed 任务重置回 queued 并移回活跃队列队尾；
     终态 done 不被误动；幂等（无失败 retried=0 仍 ok）。
  2. retry（单任务）：failed → queued 重新入队；非 failed（done）拒绝并给原因；
     重置时清空 error/task_id/finished_at，移出 _history 回到 _jobs。

不依赖运行中的 bridge / ComfyUI：TaskQueue 用 __new__ 裸构造 + mock _save / ensure_consumer。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_queue_retry_failed.py -v
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge.queue import ST_DONE, ST_FAILED, ST_QUEUED, TaskQueue  # noqa: E402


def make_mgr() -> TaskQueue:
    m = TaskQueue.__new__(TaskQueue)
    m._jobs = []
    m._history = []
    m._seq = 0
    m._save = lambda: None  # 真实 _save 为同步写盘，mock 成空函数
    m.ensure_consumer = AsyncMock(return_value=False)
    return m


def _job(bid: str, idx: int, state: str) -> dict:
    return {
        "job_id": f"j{idx}",
        "batch_id": bid,
        "state": state,
        "seq": idx,
        "prompt": f"p{idx}",
        "params": {"channel": "local"},
        "error": "平台返回 500" if state == ST_FAILED else None,
        "task_id": "abc123" if state == ST_FAILED else None,
        "finished_at": 1.0 if state in (ST_DONE, ST_FAILED) else None,
    }


async def _case_batch_retry_failed():
    m = make_mgr()
    m._lock = asyncio.Lock()
    m._history = [_job("B1", 1, ST_FAILED), _job("B1", 2, ST_FAILED), _job("B1", 3, ST_DONE)]
    m._jobs = [_job("B1", 4, ST_QUEUED)]

    res = await m.batch_retry_failed("B1")
    assert res["ok"], res
    assert res["retried"] == 2, res

    b1 = [j for j in m._jobs if j["batch_id"] == "B1"]
    assert len(b1) == 3, b1  # 原本 1 条 queued + 2 条重试
    assert all(j["state"] == ST_QUEUED for j in b1)
    assert all(j.get("error") is None for j in b1)
    assert all(j.get("task_id") is None for j in b1)
    # 终态 done 不受影响
    assert any(j["state"] == ST_DONE for j in m._jobs + m._history)
    # 重试任务已从 _history 移回 _jobs
    assert all(j["batch_id"] != "B1" or j["state"] != ST_FAILED for j in m._history)


async def _case_batch_retry_failed_idempotent():
    m = make_mgr()
    m._lock = asyncio.Lock()
    m._history = [_job("B2", 1, ST_DONE)]
    m._jobs = [_job("B2", 2, ST_QUEUED)]
    res = await m.batch_retry_failed("B2")
    assert res["ok"] and res["retried"] == 0, res


async def _case_retry_single_failed():
    m = make_mgr()
    m._lock = asyncio.Lock()
    m._history = [_job("B1", 1, ST_FAILED)]
    res = await m.retry("j1", 0, is_owner=True)
    assert res["ok"], res
    assert m._jobs[0]["state"] == ST_QUEUED
    assert m._jobs[0].get("error") is None
    assert m._jobs[0].get("task_id") is None
    assert m._history == []  # 移出历史回到活跃队列


async def _case_retry_rejects_non_failed():
    m = make_mgr()
    m._lock = asyncio.Lock()
    m._history = [_job("B1", 2, ST_DONE)]
    res = await m.retry("j2", 0, is_owner=True)
    assert not res["ok"]
    assert "只有失败" in res["error"]


def test_all():
    asyncio.run(_case_batch_retry_failed())
    asyncio.run(_case_batch_retry_failed_idempotent())
    asyncio.run(_case_retry_single_failed())
    asyncio.run(_case_retry_rejects_non_failed())
    print("QUEUE_RETRY_FAILED_OK")


if __name__ == "__main__":
    test_all()
