# -*- coding: utf-8 -*-
"""计划草稿「定时执行」存储层验收（2026-09-13）。

覆盖：
  1. 新计划自带完整定时字段；老 plans.json（无这些键）读取时自动补齐，前端不会拿到 undefined
  2. set_schedule 校验：时间必须晚于现在 / repeat 只认 none|daily / 计划不存在报错 / 取消定时复位
  3. due_plans 只挑「已启用且到点」的计划
  4. claim_due 抢单：一次性 → 触发后自动关闭且不可重复抢；daily → 推进到未来同一时刻
     （系统休眠漏多个周期也不会连续补跑）
  5. mark_schedule_result 回填批次号

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_plan_schedule.py -v
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge.planstore import DAY_SECONDS, PlanStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return PlanStore(tmp_path / "plans.json")


def _mk(store, name="计划A"):
    return store.save(name, "local", json.dumps([{"序号": 1, "提示词": "海边"}]))["plan"]


def _force_due(store, pid, at):
    """把某计划的触发时刻改成 at（set_schedule 拒绝过去时间，故用直接落盘模拟"已到点"）。"""
    p = store.get(pid)
    p["schedule_at"] = at
    store._save()


def test_new_plan_has_schedule_fields(store):
    p = _mk(store)
    assert p["schedule_enabled"] is False
    assert p["schedule_at"] == 0
    assert p["schedule_repeat"] == "none"
    assert p["schedule_last_fired_at"] == 0
    assert p["schedule_last_batch"] == ""


def test_legacy_plan_gets_schedule_defaults(tmp_path):
    """老 plans.json 无定时键 → 读取后必须补齐（前端直接读 p.schedule_enabled）。"""
    f = tmp_path / "plans.json"
    f.write_text(json.dumps([{
        "plan_id": "old1", "name": "老计划", "channel": "local",
        "json_text": "[]", "created_at": 1,
    }]), encoding="utf-8")
    s = PlanStore(f)
    p = s.get("old1")
    assert p["schedule_enabled"] is False and p["schedule_at"] == 0
    assert "schedule_enabled" in s.list()[0]


def test_set_schedule_validates(store):
    pid = _mk(store, "计划B")["plan_id"]
    now = time.time()

    r = store.set_schedule(pid, now - 10, "none")
    assert r["ok"] is False and "晚于当前" in r["error"]

    r = store.set_schedule(pid, now + 600, "weekly")
    assert r["ok"] is False and "重复方式" in r["error"]

    assert store.set_schedule("nope", now + 600, "none")["ok"] is False

    at = now + 600
    r = store.set_schedule(pid, at, "daily")
    assert r["ok"] is True and r["plan"]["schedule_enabled"] is True
    assert abs(r["plan"]["schedule_at"] - at) < 0.01
    assert r["plan"]["schedule_repeat"] == "daily"


def test_due_and_claim_once(store):
    pid = _mk(store, "计划C")["plan_id"]
    store.set_schedule(pid, time.time() + 600, "none")
    _force_due(store, pid, time.time() - 1)

    assert [d["plan_id"] for d in store.due_plans()] == [pid]
    assert store.claim_due(pid) is True        # 抢到
    assert store.claim_due(pid) is False       # 已移出 due，绝不能重复下发

    after = store.get(pid)
    assert after["schedule_enabled"] is False  # 一次性 → 自动关闭
    assert after["schedule_last_fired_at"] > 0
    assert store.due_plans() == []

    store.mark_schedule_result(pid, "batch123")
    assert store.get(pid)["schedule_last_batch"] == "batch123"


def test_claim_daily_advances_and_skips_missed_cycles(store):
    pid = _mk(store, "计划D")["plan_id"]
    store.set_schedule(pid, time.time() + 600, "daily")
    # 模拟系统休眠漏了 3 天：推进后必须是未来时刻，且只推一格（不连续补跑历史周期）
    _force_due(store, pid, time.time() - DAY_SECONDS * 3)

    assert store.claim_due(pid) is True
    after = store.get(pid)
    assert after["schedule_enabled"] is True
    assert after["schedule_at"] > time.time()
    assert after["schedule_at"] - time.time() <= DAY_SECONDS
    assert store.due_plans() == []


def test_cancel_schedule(store):
    pid = _mk(store, "计划E")["plan_id"]
    store.set_schedule(pid, time.time() + 600, "daily")

    r = store.set_schedule(pid, 0, "none", enabled=False)
    assert r["ok"] is True and r.get("canceled") is True
    after = store.get(pid)
    assert after["schedule_enabled"] is False and after["schedule_at"] == 0
    assert after["schedule_repeat"] == "none"
    assert store.due_plans() == []
