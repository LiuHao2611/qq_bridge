# -*- coding: utf-8 -*-
"""2026-09-11：队列通知出口 _queue_notifier 的健壮性验收。

背景（日志实证）：
    2026-09-11 01:44:52 WARNING [qq_bridge] queue_notify_failed event=busy
        error=TypeError detail=jlog() got multiple values for argument 'event'

根因：`jlog(event, **fields)` 的第一个位置参数就叫 event，而 except 里写成
`jlog("queue_notifier_failed", event=event, ...)` → 同名冲突抛 TypeError，
**真正的原因（如 onebot 未连接）被这条假错误盖掉**（CLOSE-02 禁止静默/失真）。

覆盖：
  1) 通知发送失败时：异常被吞并落日志，绝不外抛（队列推进不受影响）
  2) 失败日志携带 notify_event 与真实 error，且**不含** jlog 自己的 TypeError
  3) busy 文案：含重试次数与退避分钟数（用户可以据此知道还要等多久）

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_queue_notifier.py -v
"""
from __future__ import annotations

import asyncio

import pytest

import src.plugins.qq_bridge as M
from src.plugins.qq_bridge.config import Config


def _run(coro):
    return asyncio.run(coro)


def _job(seq=15, busy_retries=1):
    return {
        "seq": seq, "job_id": "j15", "task_id": "abcd1234ef56",
        "user_id": 900000002, "group_id": 0, "state": "running",
        "prompt": "便利店门口 vlog 自拍", "images": [], "error": None,
        "busy_retries": busy_retries,
    }


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg, *a, **kw):
        self.warnings.append(str(msg))

    # 兜底：其它级别不至于让用例挂掉
    def __getattr__(self, name):
        return lambda *a, **kw: None


@pytest.fixture
def logcap(monkeypatch):
    lg = _Log()
    monkeypatch.setattr(M, "log", lg)
    return lg


def test_notify_failure_is_logged_not_raised(logcap, monkeypatch):
    """通知发送失败：吞异常 + 落日志，绝不把 TypeError 抛给队列。"""

    async def _boom(*a, **kw):
        raise RuntimeError("onebot 未连接：没有在线的 bot 实例")

    monkeypatch.setattr(M, "_reply", _boom)
    _run(M._queue_notifier("busy", _job()))  # 不抛异常即通过


def test_notify_failure_log_keeps_real_error(logcap, monkeypatch):
    """失败日志必须带 notify_event 与真实 error；不能是 jlog 自己的 TypeError。"""

    async def _boom(*a, **kw):
        raise RuntimeError("onebot 未连接")

    monkeypatch.setattr(M, "_reply", _boom)
    _run(M._queue_notifier("busy", _job()))

    assert logcap.warnings, "通知失败必须落一条日志（禁止静默吞掉）"
    line = logcap.warnings[-1]
    assert "queue_notifier_failed" in line
    assert "notify_event=busy" in line
    assert "RuntimeError" in line
    assert "TypeError" not in line, "不该是 jlog 参数冲突的假错误：%s" % line
    assert "onebot 未连接" in line


def test_busy_notice_mentions_retry_and_backoff(monkeypatch):
    """busy 文案：让用户知道这是第几次等待、还要等几分钟。"""
    seen = {}

    async def _capture(ctx, sess, text, **kw):
        seen["text"] = text
        seen["title"] = kw.get("title")

    monkeypatch.setattr(M, "_reply", _capture)
    # 显式钉住两个值：全量跑时其它用例可能改过 Config，不能依赖全局残留
    monkeypatch.setattr(Config, "QUEUE_BUSY_BACKOFF", 300.0)
    monkeypatch.setattr(Config, "QUEUE_BUSY_RETRY", 3)
    _run(M._queue_notifier("busy", _job(seq=15, busy_retries=1)))

    assert "第 1/3 次等待" in seen["text"], seen
    assert "5 分钟后" in seen["text"], seen
    assert seen["title"] == "平台忙 · 排队中"
