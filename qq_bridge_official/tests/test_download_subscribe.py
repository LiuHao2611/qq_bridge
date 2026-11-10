# -*- coding: utf-8 -*-
"""M7 订阅监控验收（2026-10-20，download/subscribe.py）。

覆盖（StreamVault 范式：UP/收藏夹更新 → 自动下载 → 通知）：
  1. SubscriptionStore：增/删/查、同链接去重、每 scope 上限、due 间隔判定、
     mark_checked 滚动 id 记忆；
  2. SubscriptionMonitor.check_one：flat playlist 提取 → 新条目判定
     （id 不在 last_ids）→ mark_checked；空列表/检查失败显式处理；
  3. 自动下载：DL_SUB_AUTO_DOWNLOAD 开 → submit 每个新条目；关 → 只通知；
  4. on_new 回调通知（含标题摘要）；单条失败不阻其它；
  5. start/stop 幂等。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_download_subscribe.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.download.subscribe import (  # noqa: E402
    SubscriptionError, SubscriptionMonitor, SubscriptionStore)


class FakeYDL:
    """假 yt_dlp.YoutubeDL（鸭子类型）。"""

    def __init__(self, opts=None):
        self.opts = opts or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        entries = getattr(self, "_entries", [])
        return {"_type": "playlist", "entries": entries}


@pytest.fixture()
def store(tmp_path):
    return SubscriptionStore(tmp_path / "subscriptions.json")


# ---------------- 1. store ----------------
def test_add_list_remove(store):
    s = store.add("https://space.bilibili.com/1/favlist", "g:1", title="收藏夹")
    assert s["enabled"] is True
    assert len(store.list("g:1")) == 1
    assert store.remove(s["id"], "g:1")["id"] == s["id"]
    assert store.list("g:1") == []


def test_add_dedup_and_cap(store):
    store.add("https://x.com/a", "g:1")
    with pytest.raises(SubscriptionError) as ei:
        store.add("https://x.com/a", "g:1")
    assert "订阅过" in str(ei.value)
    old = Config.DL_SUB_MAX_PER_SCOPE
    Config.DL_SUB_MAX_PER_SCOPE = 2
    try:
        store.add("https://x.com/b", "g:1")
        with pytest.raises(SubscriptionError) as ei2:
            store.add("https://x.com/c", "g:1")
        assert "最多订阅 2" in str(ei2.value)
    finally:
        Config.DL_SUB_MAX_PER_SCOPE = old


def test_cross_scope_isolation(store):
    s = store.add("https://x.com/a", "g:1")
    with pytest.raises(SubscriptionError):
        store.remove(s["id"], "g:2")
    assert store.list("g:2") == []


def test_due_interval(store):
    s = store.add("https://x.com/a", "g:1")
    s["last_check_at"] = 0.0  # 从未检查 → 立即到期
    assert len(store.due()) == 1
    s["last_check_at"] = __import__("time").time()  # 刚查过 → 未到期
    assert len(store.due()) == 0
    s["enabled"] = False
    assert len(store.due()) == 0


def test_mark_checked_rolling_memory(store):
    s = store.add("https://x.com/a", "g:1")
    store.mark_checked(s["id"], 2, ["id1", "id2"])
    assert store.get(s["id"])["last_new_count"] == 2
    store.mark_checked(s["id"], 0, ["id3"])
    ids = store.get(s["id"])["last_ids"]
    assert ids[0] == "id3" and set(ids) == {"id1", "id2", "id3"}


# ---------------- 2/3/4. monitor ----------------
def _monitor(store, entries, submit_calls, notify_calls):
    FakeYDL._entries = entries
    m = SubscriptionMonitor(store=store, ydl_cls=FakeYDL)
    if submit_calls is not None:
        async def submit(url, scope, user_id=0, group_id=0, channel="napcat"):
            submit_calls.append(url)
            return {"id": "d1"}
        m.submit = submit
    if notify_calls is not None:
        async def on_new(sub, items):
            notify_calls.append((sub["id"], len(items)))
        m.on_new = on_new
    return m


@pytest.mark.anyio
async def test_check_one_detects_new(store):
    store.add("https://x.com/a", "g:1")
    sub = store.list("g:1")[0]
    m = _monitor(store, [
        {"id": "v1", "title": "新视频1", "url": "https://x.com/v1"},
        {"id": "v2", "title": "新视频2", "url": "https://x.com/v2"},
    ], submit_calls=None, notify_calls=None)
    new = await m.check_one(sub)
    assert len(new) == 2
    # 检查过之后 id 入记忆 → 再查无新条目
    new2 = await m.check_one(sub)
    assert new2 == []


@pytest.mark.anyio
async def test_check_one_no_entries(store):
    store.add("https://x.com/a", "g:1")
    sub = store.list("g:1")[0]
    m = _monitor(store, [], submit_calls=None, notify_calls=None)
    assert await m.check_one(sub) == []


@pytest.mark.anyio
async def test_tick_auto_download_and_notify(store):
    from qq_bridge.config import Config
    old = Config.DL_SUB_AUTO_DOWNLOAD
    Config.DL_SUB_AUTO_DOWNLOAD = True
    try:
        store.add("https://x.com/a", "g:1")
        store.list("g:1")[0]["last_check_at"] = 0.0
        submits, notifies = [], []
        m = _monitor(store, [
            {"id": "v1", "title": "甲", "url": "https://x.com/v1"},
            {"id": "v2", "title": "乙", "url": "https://x.com/v2"},
        ], submit_calls=submits, notify_calls=notifies)
        await m._tick()
        assert submits == ["https://x.com/v1", "https://x.com/v2"]  # 自动下载
        assert notifies == [(store.list("g:1")[0]["id"], 2)]       # 通知计数
    finally:
        Config.DL_SUB_AUTO_DOWNLOAD = old


@pytest.mark.anyio
async def test_tick_no_auto_download_only_notify(store):
    from qq_bridge.config import Config
    old = Config.DL_SUB_AUTO_DOWNLOAD
    Config.DL_SUB_AUTO_DOWNLOAD = False
    try:
        store.add("https://x.com/a", "g:1")
        store.list("g:1")[0]["last_check_at"] = 0.0
        submits, notifies = [], []
        m = _monitor(store, [{"id": "v1", "title": "甲",
                              "url": "https://x.com/v1"}],
                     submit_calls=submits, notify_calls=notifies)
        await m._tick()
        assert submits == []            # 不自动下
        assert notifies and notifies[0][1] == 1
    finally:
        Config.DL_SUB_AUTO_DOWNLOAD = old


@pytest.mark.anyio
async def test_tick_submit_failure_does_not_block(store):
    store.add("https://x.com/a", "g:1")
    store.list("g:1")[0]["last_check_at"] = 0.0
    m = _monitor(store, [
        {"id": "v1", "title": "甲", "url": "https://x.com/v1"},
        {"id": "v2", "title": "乙", "url": "https://x.com/v2"},
    ], submit_calls=None, notify_calls=None)

    async def bad_submit(url, scope, **kw):
        raise RuntimeError("提交炸了")

    m.submit = bad_submit
    await m._tick()  # 不抛（单条失败记日志继续）


@pytest.mark.anyio
async def test_start_stop_idempotent(store):
    m = SubscriptionMonitor(store=store, ydl_cls=FakeYDL)
    await m.start()
    assert m._task is not None
    await m.start()   # 幂等
    await m.stop()
    assert m._task is None
    await m.stop()    # 幂等
