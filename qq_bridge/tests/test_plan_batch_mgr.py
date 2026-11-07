# -*- coding: utf-8 -*-
"""
test_plan_batch_mgr.py —— QQ 任务中心「计划管理」验收（2026-09-12）

机器判定：pytest assert + 非零退出码。纯逻辑，免起真实后端 / bridge。

覆盖：
  A. 批次聚合（batches_summary）：按 batch_id 分组、记录通道、真实进度百分比
  B. 批次详情（batch_detail）：_public 含 batch_id/plan_index/channel
  C. 计划级暂停（batch_cancel）：剩余 queued → canceled + _paused
  D. 计划级恢复（batch_resume）：只复活 _paused 条目，幂等
  E. 计划草稿 CRUD（PlanStore）：save/list/get/delete/mark_run

运行：cd qq_bridge && python -m pytest tests/test_plan_batch_mgr.py -v
"""
from __future__ import annotations

import json

import pytest

from src.plugins.qq_bridge.queue import (  # noqa: E402
    ST_DONE, ST_QUEUED, TaskQueue,
)
from src.plugins.qq_bridge.planstore import PlanStore  # noqa: E402

PLAN = json.dumps([
    {"序号": 1, "正面提示词": "a cat", "图片名称": "cat1"},
    {"序号": 2, "prompt": "a dog", "name": "dog2"},
    {"序号": 3, "prompt": "a bird"},
])


@pytest.mark.anyio
async def test_batch_summary_and_detail(tmp_path):
    q = TaskQueue(path=tmp_path / "queue.json")
    res = await q.enqueue_batch(json.loads(PLAN), "local", 100)
    assert res["ok"], "批量下发应成功"
    bid = res["batch_id"]
    assert res["accepted"] == 3

    summary = q.batches_summary()
    assert len(summary) == 1, "应聚合出 1 个批次"
    b = summary[0]
    assert b["batch_id"] == bid
    assert b["channel"] == "local", "批次应记录通道"
    assert b["total"] == 3
    assert b["queued"] == 3
    assert b["done"] == 0
    assert b["progress"] == 0.0

    detail = q.batch_detail(bid)
    assert detail["ok"] and detail["total"] == 3
    items = detail["items"]
    assert items[0]["batch_id"] == bid, "_public 应含 batch_id"
    assert items[0]["channel"] == "local", "_public 应含 channel"
    assert items[0]["plan_index"] == 1


@pytest.mark.anyio
async def test_batch_cancel_then_resume(tmp_path):
    q = TaskQueue(path=tmp_path / "queue.json")
    res = await q.enqueue_batch(json.loads(PLAN), "local", 100)
    bid = res["batch_id"]

    # 模拟第 1 条已完成
    first = next(j for j in q._jobs if j.get("batch_id") == bid)
    first["state"] = ST_DONE
    first["images"] = ["http://x/done.png"]

    # 暂停：剩余 2 条 queued → canceled
    c = await q.batch_cancel(bid)
    assert c["canceled"] == 2, "应取消剩余 2 条"
    s = q.batches_summary()[0]
    assert s["done"] == 1 and s["canceled"] == 2
    assert s["paused"] is True
    assert abs(s["progress"] - 33.3) < 0.5, "进度=1/3≈33.3，实际 %s" % s["progress"]

    # 恢复：2 条回到 queued
    r = await q.batch_resume(bid)
    assert r["resumed"] == 2
    s2 = q.batches_summary()[0]
    assert s2["queued"] == 2 and s2["paused"] is False

    # 幂等：_paused 已清，再 resume 不应复活任何
    r2 = await q.batch_resume(bid)
    assert r2["resumed"] == 0


@pytest.mark.anyio
async def test_batch_detail_missing(tmp_path):
    q = TaskQueue(path=tmp_path / "queue.json")
    d = q.batch_detail("nope")
    assert not d["ok"], "不存在的批次应报错"


@pytest.mark.anyio
async def test_plan_store_crud(tmp_path):
    ps = PlanStore(path=tmp_path / "plans.json")
    r = ps.save("我的计划", "online", PLAN)
    assert r["ok"] and r["plan_id"]
    pid = r["plan_id"]

    lst = ps.list()
    assert len(lst) == 1 and lst[0]["name"] == "我的计划" and lst[0]["channel"] == "online"

    got = ps.get(pid)
    assert got is not None and json.loads(got["json_text"]), "草稿内容应原样保存"

    ps.mark_run(pid, "batchABC")
    assert ps.get(pid)["last_run_batch"] == "batchABC"

    assert ps.delete(pid) is True
    assert ps.get(pid) is None
    assert ps.delete(pid) is False, "删除不存在应返回 False"
