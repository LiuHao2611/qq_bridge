# -*- coding: utf-8 -*-
"""
test_queue_groups.py —— QQ 任务中心「5 状态页签」聚合验收（2026-09-15）

机器判定：pytest assert + 非零退出码。纯逻辑，免起真实后端 / bridge。

覆盖：
  A. 计划名称 / 计划日期 / 定时日期 / 计划开始日期 随批次落库，并在聚集中返回
  B. 5 页签归类（TaskQueue._bucket_of）：
     queued（排队中）/ running（运行中）/ done（已完成）/ failed（失败）/ partial（已执行未完成）
     —— 「一个计划 30 条明细只执行了 4 条」= partial，**无论那 4 条成功还是失败**
  C. 非批次单条任务各自成组（kind=single），保证「任何执行过的任务都能在页签里查到」
  D. 明细级日期字段（started_at / finished_at）齐备，支撑 4 个日期列

运行：cd qq_bridge && python -m pytest tests/test_queue_groups.py -v
"""
from __future__ import annotations

import pytest

from src.plugins.qq_bridge.queue import (
    ST_CANCELED, ST_DONE, ST_FAILED, ST_QUEUED, ST_RUNNING, TaskQueue,
)



def _items(n: int, tag: str = "it"):
    return [{"序号": i + 1, "正面提示词": f"{tag}-{i}", "图片名称": f"{tag}-{i}.png"}
            for i in range(n)]


def _set_states(q: TaskQueue, bid: str, mapping: dict) -> None:
    """按 plan_index（1-based）改写批次内任务状态，并补上执行/完成时间。"""
    for j in q._jobs:
        if j.get("batch_id") != bid:
            continue
        st = mapping.get(int(j.get("plan_index") or 0))
        if not st:
            continue
        j["state"] = st
        if st in (ST_RUNNING, ST_DONE, ST_FAILED):
            j["started_at"] = 1700000000.0
        if st in (ST_DONE, ST_FAILED):
            j["finished_at"] = 1700000100.0


@pytest.mark.anyio
async def test_group_meta_recorded(tmp_path):
    """A：计划名称 / 计划日期 / 定时日期 / 计划开始日期 落库 + 可读。"""
    q = TaskQueue(path=tmp_path / "queue.json")
    res = await q.enqueue_batch(_items(3, "meta"), "local", 100,
                                name="夜跑生图计划", plan_id="pl-abc",
                                scheduled_at=1700000000.0)
    assert res["ok"], "批量下发应成功"

    g = q.batches_summary()[0]
    assert g["kind"] == "batch"
    assert g["name"] == "夜跑生图计划", "计划名称应随批次落库"
    assert g["plan_id"] == "pl-abc", "来源草稿计划 id 应落库"
    assert g["scheduled_at"] == 1700000000.0, "定时日期（定时触发时间）应落库"
    assert g["created_at"] > 0, "计划日期（最早入队时间）应有值"
    assert g["started_at"] == 0, "还没跑 → 计划开始日期为空"
    assert g["finished_at"] == 0, "还没跑 → 执行完成日期为空"

    it = q.batch_detail(res["batch_id"])["items"][0]
    assert it["batch_name"] == "夜跑生图计划"
    assert it["plan_id"] == "pl-abc"
    assert it["scheduled_at"] == 1700000000.0


@pytest.mark.anyio
async def test_bucket_classification(tmp_path):
    """B：5 个页签的归类规则（含用户举例：30 条只跑 4 条 → 已执行未完成）。"""
    q = TaskQueue(path=tmp_path / "queue.json")

    b1 = (await q.enqueue_batch(_items(5, "q1"), "local", 1, name="全排队"))["batch_id"]

    b2 = (await q.enqueue_batch(_items(3, "r2"), "local", 1, name="有运行中"))["batch_id"]
    _set_states(q, b2, {1: ST_DONE, 2: ST_RUNNING})

    b3 = (await q.enqueue_batch(_items(2, "d3"), "local", 1, name="全成功"))["batch_id"]
    _set_states(q, b3, {1: ST_DONE, 2: ST_DONE})

    b4 = (await q.enqueue_batch(_items(2, "f4"), "local", 1, name="全失败"))["batch_id"]
    _set_states(q, b4, {1: ST_FAILED, 2: ST_FAILED})

    b5 = (await q.enqueue_batch(_items(30, "mix5"), "local", 1, name="跑完但部分失败"))["batch_id"]
    _set_states(q, b5, {**{i: ST_DONE for i in range(1, 21)},
                        **{i: ST_FAILED for i in range(21, 31)}})

    # ★用户举例：一个计划 30 条明细只执行了 4 条（4 条失败、其余取消）
    b6 = (await q.enqueue_batch(_items(30, "half6"), "local", 1, name="只跑了4条"))["batch_id"]
    _set_states(q, b6, {**{i: ST_FAILED for i in range(1, 5)},
                        **{i: ST_CANCELED for i in range(5, 31)}})

    # 对偶：30 条里 4 条成功、其余还在排队 → 也算已执行未完成
    b7 = (await q.enqueue_batch(_items(30, "half7"), "local", 1, name="跑了一半还在排"))["batch_id"]
    _set_states(q, b7, {i: ST_DONE for i in range(1, 5)})

    by = {g["batch_id"]: g for g in q.batches_summary()}
    assert by[b1]["bucket"] == "queued", "一条都没跑完且还有排队 → 排队中"
    assert by[b2]["bucket"] == "running", "有运行中 → 运行中"
    assert by[b3]["bucket"] == "done", "全部成功 → 已完成"
    assert by[b4]["bucket"] == "failed", "全部失败 → 失败"
    assert by[b5]["bucket"] == "failed", "跑完了但存在失败 → 失败"
    assert by[b6]["bucket"] == "partial", "30 条只执行 4 条 → 已执行未完成"
    assert by[b7]["bucket"] == "partial", "执行了一部分、其余仍排队 → 已执行未完成"

    # 量化口径：已执行 / 已结束 / 未结束
    assert by[b6]["total"] == 30
    assert by[b6]["executed"] == 4, "已执行过（成功或失败）的条数"
    assert by[b6]["finished"] == 4, "已结束（成功+失败）的条数"
    assert by[b6]["unfinished"] == 26, "未结束（排队/运行/取消）的条数"
    assert by[b6]["executed"] > 0, "已执行未完成必须真的执行过"
    assert by[b3]["unfinished"] == 0 and by[b3]["progress"] == 100.0

    # 页签计数（前端 tabCounts 同口径）
    counts = {}
    for g in by.values():
        counts[g["bucket"]] = counts.get(g["bucket"], 0) + 1
    assert counts == {"queued": 1, "running": 1, "done": 1, "failed": 2, "partial": 2}


@pytest.mark.anyio
async def test_single_task_forms_own_group(tmp_path):
    """C/D：非批次的单条任务各自成组（kind=single），并带明细详情与日期。"""
    q = TaskQueue(path=tmp_path / "queue.json")
    q._jobs.append({
        "job_id": "singlex1234", "seq": 1, "user_id": 100, "group_id": 0, "scene": "qq",
        "prompt": "单条任务", "params": {}, "state": ST_DONE,
        "enqueued_at": 1700000000.0, "started_at": 1700000005.0, "finished_at": 1700000100.0,
        "batch_id": "", "batch_name": "", "plan_id": "", "scheduled_at": 0,
        "channel": "local", "images": ["http://x/a.png"], "error": "",
    })
    gs = q.batches_summary()
    assert len(gs) == 1, "单条任务应各自成组，不被丢掉"
    g = gs[0]
    assert g["kind"] == "single" and g["batch_id"] == ""
    assert g["bucket"] == "done" and g["total"] == 1
    assert g["started_at"] == 1700000005.0 and g["finished_at"] == 1700000100.0
    assert g["job"] and g["job"]["job_id"] == "singlex1234", "单条组应内联任务详情"
    assert g["job"]["state"] == "done", "明细状态可支撑「成功日期」列"

    # 单条排队中的任务 → 排队中；单条失败 → 失败
    q._jobs[0]["state"] = ST_QUEUED
    assert q.batches_summary()[0]["bucket"] == "queued"
    q._jobs[0]["state"] = ST_FAILED
    assert q.batches_summary()[0]["bucket"] == "failed"
