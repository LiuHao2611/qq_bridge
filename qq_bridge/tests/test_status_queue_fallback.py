# -*- coding: utf-8 -*-
"""2026-09-11：/status 队列通道兜底（修复「后台明明有任务，群里问却说闲着呢」）。

背景：本地生图全走队列通道（queue_t2i → queue 消费者提交/轮询主项目）后，
任务不经 taskmgr，全局任务簿 _current_tasks 永远空 → 群里 @机器人 问进度
被回「闲着呢，没活儿干」，但后台队列明明有任务在跑（用户截图实证）。

修复：queue.Manager 新增 running_status()（返回与任务簿同构的 dict + queued_behind）；
__STATUS__ 分支查簿为空时兜底调 queue_mgr.running_status()。

覆盖：
  1) running_status：有 running 任务返回同构 dict（task_id 截 8 位、queued_behind 正确）
  2) running_status：无 running 返 None；started_at 缺失用 enqueued_at 兜底
  3) _handle_reply 端到端：簿空 + 队列有 running → 发 progress 进度卡（prompt 来自队列任务）
  4) _handle_reply 端到端：簿空 + 队列空 → 仍是「闲着呢」text 卡
  5) 旁观者文案：进度卡渲染失败回落文本时，含「群友」与排队数后缀

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_status_queue_fallback.py -v
"""
from __future__ import annotations

import asyncio
import time

import pytest

import src.plugins.qq_bridge as M
from src.plugins.qq_bridge.dispatcher import QQEvent
from src.plugins.qq_bridge.queue import queue_mgr
from src.plugins.qq_bridge.session import Session


def _run(coro):
    return asyncio.run(coro)


def _ev(user_id: int = 10001, group_id: int = 20001) -> QQEvent:
    return QQEvent(user_id=user_id, group_id=group_id,
                   message_type="group", raw_text="")


class _Rec:
    def __init__(self) -> None:
        self.pics: list[bytes] = []
        self.texts: list[str] = []

    async def pic(self, ctx, data, at_user=False):
        self.pics.append(data)

    async def text(self, ctx, text, at_user=False):
        self.texts.append(text)


@pytest.fixture
def rec(monkeypatch):
    r = _Rec()
    monkeypatch.setattr(M, "send_pic", r.pic)
    monkeypatch.setattr(M, "send_text", r.text)
    monkeypatch.setattr(M, "maybe_send_sticker", lambda ctx: asyncio.sleep(0))
    return r


@pytest.fixture
def jobs_guard():
    """保存/恢复 queue_mgr._jobs：用例直接改内存列表，绝不落盘。"""
    orig = list(queue_mgr._jobs)
    yield
    queue_mgr._jobs[:] = orig


def _job(seq=13, state="running", user_id=900000002, task_id="abcd1234ef56",
         prompt="便利店门口 vlog 自拍", started=None, enqueued=None):
    return {
        "seq": seq, "job_id": "j%d" % seq, "task_id": task_id,
        "user_id": user_id, "group_id": 0, "scene": "web",
        "prompt": prompt, "params": {}, "state": state,
        "enqueued_at": enqueued if enqueued is not None else time.time() - 300,
        "started_at": started, "finished_at": None,
        "error": None, "busy_retries": 0, "images": [],
    }


# ================ running_status 单元 ================

def test_running_status_returns_book_shaped_dict(jobs_guard):
    """running 任务存在：返回与 taskmgr 任务簿同构的 dict，queued_behind 正确。"""
    queue_mgr._jobs[:] = [
        _job(seq=13, state="running", started=time.time() - 120),
        _job(seq=14, state="queued"),
        _job(seq=15, state="queued"),
    ]
    info = queue_mgr.running_status()
    assert info is not None
    assert info["task_id"] == "abcd1234"[:8] == info["trace_id"]  # 截 8 位
    assert info["kind"] == "t2i"
    assert info["user_id"] == 900000002
    assert info["prompt"] == "便利店门口 vlog 自拍"
    assert info["queued_behind"] == 2
    assert 100 < info["started_at"] < time.time() + 1


def test_running_status_none_when_idle(jobs_guard):
    queue_mgr._jobs[:] = [_job(seq=14, state="queued"), _job(seq=12, state="done")]
    assert queue_mgr.running_status() is None


def test_running_status_falls_back_to_enqueued_at(jobs_guard):
    """刚提交还没轮询首轮（started_at 空）：用入队时间兜底，不抛 KeyError。"""
    queue_mgr._jobs[:] = [_job(seq=13, state="running", started=None)]
    info = queue_mgr.running_status()
    assert info is not None
    assert 250 < time.time() - info["started_at"] < 350  # ≈ enqueued_at（300s 前）


def test_pending_status_returns_oldest_queued(jobs_guard):
    """无 running 但有排队：返回最早排队任务 + 排队数（busy 退避期间的真相）。"""
    now = time.time()
    queue_mgr._jobs[:] = [
        _job(seq=14, state="queued", enqueued=now - 400),  # 最早入队
        _job(seq=15, state="queued", enqueued=now - 200),
        _job(seq=17, state="queued", enqueued=now - 100),
    ]
    info = queue_mgr.pending_status()
    assert info is not None
    assert info["state"] == "queued"
    assert info["seq"] == 14, "应取 seq 最小的（最早入队）那条：%r" % info
    assert info["queued_behind"] == 3
    assert 350 < time.time() - info["started_at"] < 450  # ≈ 最早入队时刻


def test_pending_status_none_when_nothing_queued(jobs_guard):
    queue_mgr._jobs[:] = [_job(seq=13, state="running", started=time.time() - 10)]
    assert queue_mgr.pending_status() is None


# ================ _handle_reply 端到端 ================

def _stub_book_empty(monkeypatch):
    monkeypatch.setattr(M.task_mgr, "get_active_for", lambda uid: None)
    monkeypatch.setattr(M.task_mgr, "get_any_active", lambda **kw: None)


def test_status_reports_queue_running_as_progress_pic(rec, monkeypatch):
    """核心回归：簿空 + 队列有 running → 发 progress 进度卡（prompt 来自队列），不说闲着。"""
    _stub_book_empty(monkeypatch)
    monkeypatch.setattr(queue_mgr, "running_status",
                        lambda: {"task_id": "abcd1234", "user_id": 900000002,
                                 "group_id": 0, "kind": "t2i", "prompt": "便利店 vlog",
                                 "trace_id": "abcd1234", "channel": "queue",
                                 "started_at": time.time() - 60, "queued_behind": 3})
    seen = {}
    capture_calls = {}

    async def _capture(template, theme_name=None, **params):
        seen["template"] = template
        seen.update(params)
        return b"\x89PNG\r\n\x1a\n"

    capture_calls["fn"] = _capture
    monkeypatch.setattr(M.pic_mod, "render", _capture)

    _run(M._handle_reply(_ev(user_id=10001), Session(10001), "__STATUS__", 0, 50))

    assert seen.get("template") == "progress", "应发进度卡而非闲着卡：%r" % seen
    assert seen.get("prompt") == "便利店 vlog"
    assert rec.texts == [], "进度卡发送成功就不该回落文本"
    assert len(rec.pics) == 1


def test_status_reports_queued_backlog_instead_of_idle(rec, monkeypatch):
    """核心回归 2：簿空 + 无 running + 有排队 → 报排队实况，绝不说「闲着呢」。"""
    _stub_book_empty(monkeypatch)
    monkeypatch.setattr(queue_mgr, "running_status", lambda: None)
    monkeypatch.setattr(queue_mgr, "pending_status",
                        lambda: {"seq": 14, "task_id": "", "user_id": 900000002,
                                 "group_id": 0, "kind": "t2i", "prompt": "便利店 vlog",
                                 "trace_id": "", "channel": "queue", "state": "queued",
                                 "started_at": time.time() - 180, "queued_behind": 39})
    seen = {}

    async def _capture(template, theme_name=None, **params):
        seen["template"] = template
        seen["body"] = params.get("body") or ""
        return b"\x89PNG\r\n\x1a\n"

    monkeypatch.setattr(M.pic_mod, "render", _capture)
    _run(M._handle_reply(_ev(), Session(10001), "__STATUS__", 0, 50))

    assert seen.get("template") == "text"
    body = seen.get("body", "")
    assert "闲着呢" not in body, "有 39 条排队却回闲着呢：%r" % body
    assert "39 条排队" in body
    assert "900000002" in body
    assert "已等 180 秒" in body


def test_status_idle_still_idle_text_card(rec, monkeypatch):
    """簿空 + 队列也空 → 仍是「闲着呢」text 卡（不误报在跑）。"""
    _stub_book_empty(monkeypatch)
    monkeypatch.setattr(queue_mgr, "running_status", lambda: None)
    monkeypatch.setattr(queue_mgr, "pending_status", lambda: None)
    seen = {}

    async def _capture(template, theme_name=None, **params):
        seen["template"] = template
        seen["body"] = params.get("body") or ""
        return b"\x89PNG\r\n\x1a\n"

    monkeypatch.setattr(M.pic_mod, "render", _capture)
    _run(M._handle_reply(_ev(), Session(10001), "__STATUS__", 0, 50))
    assert seen.get("template") == "text"
    assert "闲着呢" in seen.get("body", "")


def test_status_bystander_text_mentions_owner_and_queue_depth(rec, monkeypatch):
    """旁观者 + 进度卡渲染失败回落文本：文案含「群友」+ 排队数后缀。"""
    _stub_book_empty(monkeypatch)
    monkeypatch.setattr(queue_mgr, "running_status",
                        lambda: {"task_id": "abcd1234", "user_id": 900000002,
                                 "group_id": 0, "kind": "t2i", "prompt": "便利店 vlog",
                                 "trace_id": "abcd1234", "channel": "queue",
                                 "started_at": time.time() - 60, "queued_behind": 3})

    async def _boom(*a, **kw):
        raise RuntimeError("Pillow 炸了")

    monkeypatch.setattr(M.pic_mod, "render", _boom)
    _run(M._handle_reply(_ev(user_id=10001), Session(10001), "__STATUS__", 0, 50))

    assert len(rec.texts) == 1
    t = rec.texts[0]
    assert "群友 900000002" in t
    assert "生图" in t
    assert "前面还排着 3 条" in t
