# -*- coding: utf-8 -*-
"""PostProductionAgent 单元测试（手册 §4 / 设计逻辑「后期合成 Agent」）。

覆盖（N06 重试闭环 / 本地文件判断，不依赖 insightface / ffmpeg / 真实生成服务）：
  - 本地视频存在性判断（_local_exists）
  - 单任务轮询（_poll_task）：终态即返回，不空转
  - N06 重试闭环（_remediate_failed_shots）：
      · 人脸相似度首次达标（≥0.8）→ 恢复（移出失败列表 / 不标记 / 不告警）
      · 两次重试仍 <0.8 → 标记 segment 失败 + 清空 video_url + 告警 + 站内信（REMEDIATE_MAX_RETRY=2）
      · 人脸引擎缺失（engine_missing）→ 按原失败处置
director.enqueue（fetch_task_statuses / rerun_segment）与 db（push_message）经 sys.modules 注入；
_check_face_sim / _poll_task / _mark_segment_failed 以实例桩替换，隔离真实副作用。
"""
import os
import sys
import types

import pytest

from director.agents.post_production import (
    PostProductionAgent, FACE_SIM_THRESHOLD, REMEDIATE_MAX_RETRY,
)


def _inject_enqueue(fetch_statuses=None, rerun_segment=None):
    fake = types.ModuleType("director.enqueue")
    fake.fetch_task_statuses = fetch_statuses or (lambda pending: {})
    fake.rerun_segment = rerun_segment or (lambda *a, **k: ("fake_task", None, None))
    saved = sys.modules.get("director.enqueue")
    sys.modules["director.enqueue"] = fake
    return saved


def _inject_db_raises():
    """让 db.get_conn 抛错，使 _remediate 内 push_message 块被 except 吞掉（无真实写入）。"""
    fake = types.ModuleType("db")

    def _get_conn():
        raise RuntimeError("test: no real db")
    fake.get_conn = _get_conn
    saved = sys.modules.get("db")
    sys.modules["db"] = fake
    return saved


def _agent():
    return PostProductionAgent()


def _pp_mod():
    return __import__("director.agents.post_production", fromlist=["OUTPUTS"])


# ---- 本地视频存在性 ------------------------------------------------
def test_local_exists(tmp_path):
    mod = _pp_mod()
    saved = mod.OUTPUTS
    mod.OUTPUTS = str(tmp_path)
    try:
        f = tmp_path / "seg9.mp4"
        f.write_text("x")
        assert PostProductionAgent._local_exists("http://h/v.mp4?name=seg9.mp4") is True
        assert PostProductionAgent._local_exists("http://h/v.mp4?name=segX.mp4") is False
        assert PostProductionAgent._local_exists("http://h/v.mp4") is False
    finally:
        mod.OUTPUTS = saved


# ---- 单任务轮询（_poll_task） ---------------------------------------
def test_poll_task_terminal():
    def fetch(pending):
        return {tid: {"status": "succeeded", "progress": 100,
                      "result_url": "u", "segment_id": "seg1"}
                for tid in pending}
    saved = _inject_enqueue(fetch_statuses=fetch)
    calls = []
    try:
        st = _agent()._poll_task(
            "t1", "pid", "trace",
            lambda ch, kind, payload: calls.append(payload))
    finally:
        if saved is None:
            sys.modules.pop("director.enqueue", None)
        else:
            sys.modules["director.enqueue"] = saved
    assert st == "succeeded"
    assert len(calls) == 1
    assert calls[0]["status"] == "succeeded"


# ---- N06 重试闭环（_remediate_failed_shots） -----------------------
def _run_remediate(face_sim_sequence, engine_missing=False):
    """face_sim_sequence: 每次 _check_face_sim 返回的 sim 列表（按 attempt 顺序消费）。"""
    agent = _agent()
    ws_calls = []
    marked = []

    agent._check_face_sim = lambda seg_id: (face_sim_sequence.pop(0), engine_missing)
    agent._poll_task = lambda *a, **k: "succeeded"
    agent._mark_segment_failed = lambda seg_id: marked.append(seg_id)

    saved_enq = _inject_enqueue()
    saved_db = _inject_db_raises()
    try:
        qc = {
            "reports": [
                {"segment_id": "s1", "face_sim": 0.5},   # <0.8 → 进入重试
                {"segment_id": "s2", "face_sim": 0.9},   # ≥0.8 → 不重试
            ],
            "failed_shot_list": ["s1", "s2"],
        }
        new_failed, alerts = agent._remediate_failed_shots(
            "pid", "trace", qc,
            lambda ch, kind, payload: ws_calls.append((kind, payload)))
    finally:
        if saved_enq is None:
            sys.modules.pop("director.enqueue", None)
        else:
            sys.modules["director.enqueue"] = saved_enq
        if saved_db is None:
            sys.modules.pop("db", None)
        else:
            sys.modules["db"] = saved_db
    return new_failed, alerts, marked, ws_calls


def test_remediate_recovers_on_first_attempt():
    new_failed, alerts, marked, ws = _run_remediate([0.9])
    assert "s1" not in new_failed          # 已恢复 → 移出失败列表
    assert alerts == []                    # 无告警
    assert marked == []                     # 未标记 segment 失败
    assert ws == []                         # 无告警推送


def test_remediate_exhausted_marks_failed():
    # 两次重试均 <0.8 → 用尽 REMEDIATE_MAX_RETRY
    new_failed, alerts, marked, ws = _run_remediate([0.5, 0.5])
    assert "s1" in new_failed              # 仍保留在失败列表（N08 据此跳过）
    assert alerts == ["s1"]                # 触发告警
    assert marked == ["s1"]                # 标记 segment 失败 + 清空 video_url
    assert any(k == "alert" for k, _ in ws)  # WS 告警推送
    assert REMEDIATE_MAX_RETRY == 2


def test_remediate_engine_missing():
    # 人脸引擎缺失 → 按原失败处置（等同用尽重试）
    new_failed, alerts, marked, ws = _run_remediate([0.5], engine_missing=True)
    assert "s1" in new_failed
    assert alerts == ["s1"]
    assert marked == ["s1"]


def test_threshold_contract():
    # 手册 §3.4.5：人脸相似度校验阈值 ≥0.8
    assert FACE_SIM_THRESHOLD == 0.8
