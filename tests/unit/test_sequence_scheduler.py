# -*- coding: utf-8 -*-
"""SequenceSchedulerAgent 单元测试（手册 §3.2 / 设计逻辑「分镜编排调度 Agent」）。

覆盖（纯逻辑 / 轻量 Mock，不依赖真实生成服务或外部模型）：
  - 本地视频 URL 解析与存在性判断（_local_path / _local_exists）
  - 任务终态跟踪（_track_tasks）：经 WS 透出 per-segment 进度，终态即退出不空转
director.enqueue.fetch_task_statuses 通过 sys.modules 注入（与 test_script_parser 同风格）。
"""
import os
import sys
import types

import pytest

from director.agents.sequence_scheduler import SequenceSchedulerAgent


def _inject_enqueue(fetch_statuses):
    fake = types.ModuleType("director.enqueue")
    fake.fetch_task_statuses = fetch_statuses
    saved = sys.modules.get("director.enqueue")
    sys.modules["director.enqueue"] = fake
    return saved


def _agent():
    return SequenceSchedulerAgent()


def _seq_mod():
    return __import__("director.agents.sequence_scheduler", fromlist=["OUTPUTS"])


# ---- 本地视频 URL 解析 ------------------------------------------------
def test_local_path_parses_name(tmp_path):
    mod = _seq_mod()
    saved = mod.OUTPUTS
    mod.OUTPUTS = str(tmp_path)
    try:
        p = SequenceSchedulerAgent._local_path("http://host/v.mp4?name=seg1.mp4")
        assert p == os.path.join(str(tmp_path), "seg1.mp4")
        # 无 name 参数 → 无法定位本地文件 → None
        assert SequenceSchedulerAgent._local_path("http://host/v.mp4") is None
    finally:
        mod.OUTPUTS = saved


def test_local_exists(tmp_path):
    mod = _seq_mod()
    saved = mod.OUTPUTS
    mod.OUTPUTS = str(tmp_path)
    try:
        f = tmp_path / "seg2.mp4"
        f.write_text("x")
        ok_url = "http://host/v.mp4?name=seg2.mp4"
        assert _agent()._local_exists(ok_url) is True
        # 不存在的文件
        miss_url = "http://host/v.mp4?name=seg3.mp4"
        assert _agent()._local_exists(miss_url) is False
        # 无 name
        assert _agent()._local_exists("http://host/v.mp4") is False
    finally:
        mod.OUTPUTS = saved


# ---- 任务终态跟踪（_track_tasks） -------------------------------------
def test_track_tasks_terminal_publishes():
    task_ids = [("segA", "t1"), ("segB", "t2")]
    seg_of = {tid: sid for sid, tid in task_ids}

    def fetch(pending):
        return {tid: {"status": "succeeded", "progress": 100,
                      "result_url": "u", "segment_id": seg_of[tid]}
                for tid in pending}

    saved = _inject_enqueue(fetch)
    calls = []
    try:
        _agent()._track_tasks(
            task_ids, "pid", "trace",
            lambda ch, kind, payload: calls.append((ch, kind, payload)))
    finally:
        if saved is None:
            sys.modules.pop("director.enqueue", None)
        else:
            sys.modules["director.enqueue"] = saved

    # 每个任务终态都经 WS 透出一次，且未进入空转 sleep
    assert len(calls) == 2
    kinds = {(c[1], c[2]["segment_id"], c[2]["status"]) for c in calls}
    assert kinds == {("segment", "segA", "succeeded"),
                     ("segment", "segB", "succeeded")}


def test_track_tasks_failed_status():
    task_ids = [("segA", "t1"), ("segB", "t2")]
    seg_of = {tid: sid for sid, tid in task_ids}

    def fetch(pending):
        return {tid: {"status": "failed", "progress": 100,
                      "result_url": None, "segment_id": seg_of[tid]}
                for tid in pending}

    saved = _inject_enqueue(fetch)
    calls = []
    try:
        _agent()._track_tasks(
            task_ids, "pid", "trace",
            lambda ch, kind, payload: calls.append(payload))
    finally:
        if saved is None:
            sys.modules.pop("director.enqueue", None)
        else:
            sys.modules["director.enqueue"] = saved

    statuses = {c["segment_id"]: c["status"] for c in calls}
    assert statuses == {"segA": "failed", "segB": "failed"}
