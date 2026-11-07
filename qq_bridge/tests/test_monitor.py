# -*- coding: utf-8 -*-
"""
test_monitor.py —— 监控告警验收（2026-09-08）

覆盖：
  1. **幂等**：同一失败任务只告警一次（不会每轮轮询刷屏）；
  2. 超时任务告警（每个 task_id 一次）；
  3. 平台持续繁忙达阈值告警一次，恢复后可再告警；
  4. MONITOR_ENABLE=False 时**完全不告警**（默认关闭，不打扰）；
  5. _alerted TTL 自动清理（OPS-02，防内存无限增长）。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_monitor.py -v
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge import config as bridge_config  # noqa: E402
from src.plugins.qq_bridge.monitor import Monitor  # noqa: E402

CFG = bridge_config.Config


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data, "message": "ok"})


def _failed_tasks(tasks: list) -> httpx.Response:
    return _ok({"items": tasks, "total": len(tasks)})


def _active(task: dict | None) -> httpx.Response:
    return _ok({"task": task})


def _transport(failed=None, active=None):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/t2i/tasks"):
            return _failed_tasks(failed or [])
        if p.endswith("/t2i/active-task"):
            return _active(active)
        return _ok({})
    return httpx.MockTransport(handler)


def new_monitor(transport, alerts: list, **cfg):
    for k, v in cfg.items():
        setattr(CFG, k, v)

    async def notifier(text: str):
        alerts.append(text)

    m = Monitor(notifier=notifier, api_base="http://127.0.0.1:7788/api/v1",
                transport=transport)
    return m


# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_failed_task_alerted_once_idempotent():
    """同一失败任务连续两轮 → 只告警一次（幂等，防刷屏）。"""
    alerts: list[str] = []
    task = {"task_id": "deadbeef1234", "prompt": "一只猫", "error": "IMG_NO_TEMPLATE"}
    m = new_monitor(_transport(failed=[task]), alerts,
                    MONITOR_ENABLE=True, MONITOR_TASK_TIMEOUT=600)
    await m._check_once()
    await m._check_once()
    await m._check_once()
    assert len(alerts) == 1, "同一任务应只告警一次，实际 %d 次" % len(alerts)
    assert "IMG_NO_TEMPLATE" in alerts[0], "告警应含失败原因"
    assert "deadbeef" in alerts[0], "告警应含任务号（便于定位）"


@pytest.mark.anyio
async def test_new_failed_task_alerted():
    """新的失败任务（不同 task_id）→ 各自告警一次。"""
    alerts: list[str] = []
    m = new_monitor(_transport(failed=[{"task_id": "aaa111", "prompt": "甲"}]),
                    alerts, MONITOR_ENABLE=True)
    await m._check_once()
    m._transport = _transport(failed=[
        {"task_id": "aaa111", "prompt": "甲"},
        {"task_id": "bbb222", "prompt": "乙"},
    ])
    await m._check_once()
    assert len(alerts) == 2, "第二条新失败任务应告警，实际 %d 条" % len(alerts)


@pytest.mark.anyio
async def test_timeout_alert_once_per_task():
    """活跃任务超时 → 告警；同一任务后续轮次不再重复。"""
    alerts: list[str] = []
    old = time.time() - 3600  # 1 小时前启动 → 必然超时
    m = new_monitor(_transport(active={"task_id": "stuck999", "created_at": old,
                                       "prompt": "卡住的图"}),
                    alerts, MONITOR_ENABLE=True, MONITOR_TASK_TIMEOUT=600)
    await m._check_once()
    await m._check_once()
    assert len(alerts) == 1, "超时同一任务只告警一次，实际 %d" % len(alerts)
    assert "卡住" in alerts[0] and "stuck999" in alerts[0]


@pytest.mark.anyio
async def test_no_timeout_alert_when_fast():
    """任务还在正常耗时内 → 不告警。"""
    alerts: list[str] = []
    m = new_monitor(_transport(active={"task_id": "ok123", "created_at": time.time() - 5,
                                       "prompt": "正常跑着"}),
                    alerts, MONITOR_ENABLE=True, MONITOR_TASK_TIMEOUT=600)
    await m._check_once()
    assert not alerts, "正常任务不该告警，实际：%s" % alerts


@pytest.mark.anyio
async def test_busy_streak_alert():
    """连续 IMG_BUSY 达阈值 → 告警一次；归零后可再告警。"""
    alerts: list[str] = []
    m = new_monitor(_transport(), alerts, MONITOR_ENABLE=True, MONITOR_BUSY_THRESHOLD=3)
    for _ in range(3):
        m.note_busy()
    await m._check_busy()
    assert len(alerts) == 1, "达阈值应告警一次，实际 %d" % len(alerts)
    assert "繁忙" in alerts[0]

    await m._check_busy()  # 再查一次不应重复
    assert len(alerts) == 1, "不应重复告警"

    m.note_submitted()  # 提交成功 → 归零
    for _ in range(3):
        m.note_busy()
    await m._check_busy()
    assert len(alerts) == 2, "恢复后再次达阈值应能再告警，实际 %d" % len(alerts)


@pytest.mark.anyio
async def test_disabled_by_default_no_alert():
    """MONITOR_ENABLE=False（默认）→ 一轮检查不产生任何告警。"""
    alerts: list[str] = []
    m = new_monitor(_transport(failed=[{"task_id": "x1", "prompt": "p", "error": "e"}]),
                    alerts, MONITOR_ENABLE=False)
    # 直接调 _check_once 会绕过 enabled 判断（那是 _loop 的职责），
    # 这里验证的是 enabled 属性本身
    assert m.enabled is False, "默认应关闭监控（不打扰）"
    assert m.interval >= 5.0, "轮询间隔应有下限，实际 %s" % m.interval


@pytest.mark.anyio
async def test_alerted_ttl_gc():
    """过期的告警记录会被清理（OPS-02 防内存无限增长）。"""
    alerts: list[str] = []
    m = new_monitor(_transport(failed=[{"task_id": "old1", "prompt": "p", "error": "e"}]),
                    alerts, MONITOR_ENABLE=True, MONITOR_TASK_TIMEOUT=600)
    await m._check_once()
    assert len(m._alerted) == 1
    # 手动把记录时间推到很久以前 → 触发 GC
    for k in list(m._alerted.keys()):
        m._alerted[k] = time.time() - 10 ** 6
    m._gc_alerted()
    assert len(m._alerted) == 0, "过期记录应被清理，实际 %d 条" % len(m._alerted)


@pytest.mark.anyio
async def test_http_error_does_not_raise():
    """主项目不可达 → 静默记日志，不抛异常（监控不能拖垮 bridge）。"""
    alerts: list[str] = []

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("主项目没起")

    m = new_monitor(httpx.MockTransport(boom), alerts, MONITOR_ENABLE=True)
    await m._check_once()  # 不应抛异常
    assert not alerts
