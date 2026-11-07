# -*- coding: utf-8 -*-
"""M9 通知中枢 + 仓库巡检验收（2026-10-20，ops/notify.py + ops/watchdog.py）。

notify 覆盖：
  1. push 全链路：事件 → 级别过滤 → 目标路由 → 转述 → 发送；
  2. 级别过滤：低于 min_level 只记日志不推送；
  3. 未配目标/send 缺失 → 不推送（显式记日志）；
  4. 限流：同类事件冷却期内累计，到点带计数补发（detail 附注）；
  5. 转述失败：原文 + 「（人格化转述失败，原文照发）」附注——不静默、
     不冒充转述成功（禁止兜底）；转述空结果同路径；
  6. 发送失败：记日志返回 False，不重试（防重复打扰）；
  7. history 环容量。

watchdog 覆盖：
  8. 变化检测：脏改动从无到有 → repo_dirty 事件（warn）；存量首轮只记录；
  9. 未推送从无到有 → repo_unpushed；
  10. git 查询失败 → repo_query_failed（error 级）+ 快照复位；
  11. 自检命令失败 → check_failed（error 级）；恢复后不重复告警；
  12. start/stop 幂等。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_ops_notify.py -v
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.ops.gitinfo import GitInfo, GitInfoError  # noqa: E402
from qq_bridge.ops.notify import (  # noqa: E402
    LEVEL_ERROR, LEVEL_INFO, LEVEL_WARN, NotificationHub, NotifyEvent)
from qq_bridge.ops.watchdog import RepoWatchdog  # noqa: E402


def _hub(**kw):
    sent = []

    async def send(text, event):
        sent.append((text, event))

    kw.setdefault("target", "123")
    kw.setdefault("send", send)
    hub = NotificationHub(**kw)
    hub._sent = sent  # 观察口
    return hub


def _ev(kind="test", title="t", level=LEVEL_INFO, detail=""):
    return NotifyEvent(kind=kind, title=title, detail=detail, level=level)


# ---------------- 1. 全链路 ----------------
@pytest.mark.anyio
async def test_push_full_chain():
    hub = _hub(rephrase=lambda e: _rephrase_ok("转述：%s" % e.title))
    ok = await hub.push(_ev(title="仓库脏了"))
    assert ok is True
    text, ev = hub._sent[0]
    assert "转述：仓库脏了" in text


async def _rephrase_ok(text):
    return text


# ---------------- 2. 级别过滤 ----------------
@pytest.mark.anyio
async def test_level_filter():
    hub = _hub(min_level="warn")
    assert await hub.push(_ev(title="info事件", level=LEVEL_INFO)) is False
    assert await hub.push(_ev(title="warn事件", level=LEVEL_WARN)) is True
    assert await hub.push(_ev(title="error事件", level=LEVEL_ERROR)) is True
    assert len(hub._sent) == 2


# ---------------- 3. 无目标 ----------------
@pytest.mark.anyio
async def test_no_target_suppressed():
    hub = _hub(target="")
    assert await hub.push(_ev()) is False
    assert hub._sent == []


@pytest.mark.anyio
async def test_disabled_suppressed():
    hub = _hub(enabled=False)
    assert await hub.push(_ev()) is False


# ---------------- 4. 限流 ----------------
@pytest.mark.anyio
async def test_cooldown_merges():
    hub = _hub(cooldown=300.0)
    assert await hub.push(_ev(kind="a", title="x")) is True
    # 冷却期内同类 → 累计不推
    for _ in range(3):
        assert await hub.push(_ev(kind="a", title="x")) is False
    assert len(hub._sent) == 1
    # 到期后同 kind+title → 带计数补发
    hub._last_sent["a::x"] = time.time() - 301
    assert await hub.push(_ev(kind="a", title="x", detail="详情")) is True
    text, ev = hub._sent[-1]
    assert "另有 3 次同类事件" in ev.detail  # 合并计数透出


# ---------------- 5. 转述失败 ----------------
@pytest.mark.anyio
async def test_rephrase_failure_sends_original_with_note():
    async def boom(e):
        raise RuntimeError("模型限流")

    hub = _hub(rephrase=boom)
    ok = await hub.push(_ev(title="自检挂了", detail="pytest 3 failed"))
    assert ok is True
    text, _ = hub._sent[0]
    assert "自检挂了" in text and "pytest 3 failed" in text
    assert "人格化转述失败" in text  # 诚实附注，不冒充转述成功


@pytest.mark.anyio
async def test_rephrase_empty_sends_original():
    async def empty(e):
        return "   "

    hub = _hub(rephrase=empty)
    ok = await hub.push(_ev(title="空转述"))
    assert ok is True
    text, _ = hub._sent[0]
    assert "空转述" in text and "人格化转述失败" in text


# ---------------- 6. 发送失败 ----------------
@pytest.mark.anyio
async def test_send_failure_returns_false():
    async def bad_send(text, event):
        raise ConnectionError("ws 断了")

    hub = _hub(send=bad_send)
    ok = await hub.push(_ev())
    assert ok is False  # 不重试（防重复打扰），事件留 history


# ---------------- 7. history ----------------
@pytest.mark.anyio
async def test_history_ring():
    hub = _hub()
    for i in range(60):
        await hub.push(_ev(title="e%d" % i))
    assert len(hub.history(100)) == 50  # 环容量
    assert hub.history(3)[-1].title == "e59"


# ---------------- watchdog ----------------
class FakeGit(GitInfo):
    def __init__(self, dirty=0, unpushed=0, raises=None):
        super().__init__(repo="D:/fake")
        self._dirty = dirty
        self._unpushed = unpushed
        self._raises = raises

    async def status(self):
        if self._raises:
            raise GitInfoError(self._raises)
        return {"repo": "D:/fake", "branch": "master",
                "dirty_count": self._dirty, "dirty_files": [],
                "unpushed": self._unpushed, "last_commit": "abc test"}


def _wd(git, **kw):
    events = []

    async def on_event(ev):
        events.append(ev)

    wd = RepoWatchdog(git=git, on_event=on_event, **kw)
    wd._events = events  # 观察口
    return wd


@pytest.mark.anyio
async def test_dirty_from_zero_emits():
    wd = _wd(FakeGit(dirty=0))
    await wd.check_once()  # 基线
    assert wd._events == []
    wd.git._dirty = 5
    await wd.check_once()
    kinds = [e.kind for e in wd._events]
    assert "repo_dirty" in kinds
    ev = next(e for e in wd._events if e.kind == "repo_dirty")
    assert ev.level == LEVEL_WARN and "5" in ev.title


@pytest.mark.anyio
async def test_dirty_stable_no_repeat():
    wd = _wd(FakeGit(dirty=3))
    await wd.check_once()  # 首轮存量只记录
    await wd.check_once()  # 没变化
    assert wd._events == []


@pytest.mark.anyio
async def test_unpushed_from_zero_emits():
    wd = _wd(FakeGit(dirty=0, unpushed=0))
    await wd.check_once()
    wd.git._unpushed = 2
    await wd.check_once()
    assert any(e.kind == "repo_unpushed" for e in wd._events)


@pytest.mark.anyio
async def test_git_failure_emits_error():
    wd = _wd(FakeGit(raises="仓库目录不存在：Z:/x"))
    snap = await wd.check_once()
    assert snap["ok"] is False
    assert any(e.kind == "repo_query_failed" and e.level == LEVEL_ERROR
               for e in wd._events)


@pytest.mark.anyio
async def test_check_command_failure_and_recovery():
    wd = _wd(FakeGit(dirty=0))
    old = Config.OPS_WATCH_CHECKS
    Config.OPS_WATCH_CHECKS = "false"  # 退出码 1 的命令（POSIX false）
    try:
        await wd.check_once()
        assert any(e.kind == "check_failed" and e.level == LEVEL_ERROR
                   for e in wd._events)
        n = len(wd._events)
        # 再次失败（未恢复）→ 不重复告警
        await wd.check_once()
        assert len(wd._events) == n
    finally:
        Config.OPS_WATCH_CHECKS = old


@pytest.mark.anyio
async def test_start_stop_idempotent():
    wd = _wd(FakeGit(), interval=3600.0)
    await wd.start()
    assert wd._task is not None
    await wd.start()  # 幂等
    await wd.stop()
    assert wd._task is None
    await wd.stop()   # 幂等
