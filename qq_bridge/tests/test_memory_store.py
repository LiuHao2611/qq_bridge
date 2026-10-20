# -*- coding: utf-8 -*-
"""M1 记忆存储验收（2026-10-20，memory/store.py）。

覆盖：
  1. add_event：类型校验（fact/event/summary）、空文本拒绝、超长截断；
  2. search：FTS 命中 + 时间衰减打分（新记忆权重高于老记忆）；
     查询词空/无命中 → 空列表（不报错）；
  3. recent：最近记忆（kind 过滤）；
  4. 软删 + restore（A-Memorix recycle_bin 范式）：删后搜不到，恢复后回来；
  5. 画像：get/set_facts（合并）/set_override（override 优先 view）/
     drop_override/drop_fact/set_relation；facts 超上限显式拒绝；
  6. 群档案：get/update（upsert 合并）；
  7. gc：超期软删 + 总数超限删最旧（幂等）。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_memory_store.py -v
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.memory.store import (  # noqa: E402
    KIND_EVENT, KIND_FACT, KIND_SUMMARY, MAX_FACTS_PER_USER, MemoryError,
    MemoryStore)


@pytest.fixture()
def store(tmp_path):
    return MemoryStore(str(tmp_path / "qq_memory.db"))


# ---------------- 1. add_event ----------------
def test_add_event_validation(store):
    ev = store.add_event("他说明天要考试", "g:1", user_id=42, kind=KIND_EVENT)
    assert ev["kind"] == KIND_EVENT and ev["deleted_at"] == 0
    assert ev["id"] and ev["created_at"] > 0
    with pytest.raises(MemoryError):
        store.add_event("  ", "g:1")
    with pytest.raises(MemoryError):
        store.add_event("x", "g:1", kind="bogus")
    long_ev = store.add_event("长" * 800, "g:1")
    assert len(long_ev["text"]) == 500  # 截断


# ---------------- 2. search ----------------
def test_search_hits_and_decay(store):
    now = time.time()
    store.add_event("他喜欢喝咖啡", "g:1", now=now - 1)
    store.add_event("他喜欢喝茶", "g:1", now=now - 400 * 86400)  # 400 天前
    hits = store.search("咖啡", "g:1", now=now)
    assert hits and "咖啡" in hits[0]["text"]
    # 时间衰减：新记忆打分高于老记忆（同权重）
    hits2 = store.search("喜欢", "g:1", now=now)
    assert hits2[0]["text"] == "他喜欢喝咖啡"  # 新的排前面
    # 空查询/无命中 → 空列表（不报错）
    assert store.search("", "g:1") == []
    assert store.search("不存在的词xyz", "g:1") == []


def test_search_scope_isolation(store):
    store.add_event("群1的事", "g:1")
    store.add_event("群2的事", "g:2")
    assert len(store.search("事", "g:1")) == 1
    assert store.search("事", "g:1")[0]["text"] == "群1的事"


def test_search_fts_injection_safe(store):
    store.add_event("正常内容", "g:1")
    # 引号/运算符不炸（显式转义）
    assert store.search('" OR ""', "g:1") == []


# ---------------- 3. recent ----------------
def test_recent_and_kind_filter(store):
    store.add_event("事件A", "g:1", kind=KIND_EVENT)
    store.add_event("摘要B", "g:1", kind=KIND_SUMMARY)
    assert len(store.recent("g:1", limit=10)) == 2
    only = store.recent("g:1", limit=10, kind=KIND_SUMMARY)
    assert len(only) == 1 and only[0]["kind"] == KIND_SUMMARY


# ---------------- 4. 软删/恢复 ----------------
def test_soft_delete_and_restore(store):
    ev = store.add_event("要删的记忆", "g:1")
    assert store.search("要删", "g:1")
    assert store.soft_delete(ev["id"], "g:1") is True
    assert store.search("要删", "g:1") == []       # 删后搜不到
    assert store.soft_delete(ev["id"], "g:1") is False  # 幂等：重复删 False
    assert store.restore(ev["id"], "g:1") is True
    assert store.search("要删", "g:1")               # 恢复后回来


def test_soft_delete_cross_scope(store):
    ev = store.add_event("x", "g:1")
    assert store.soft_delete(ev["id"], "g:2") is False  # 跨 scope 删不掉


# ---------------- 5. 画像 ----------------
def test_profile_facts_merge(store):
    store.set_facts("g:1", 42, {"喜好": "猫"})
    store.set_facts("g:1", 42, {"职业": "程序员"})
    prof = store.get_profile("g:1", 42)
    assert set(prof["facts"]) == {"喜好", "职业"}
    # 同键覆盖（合并语义）
    store.set_facts("g:1", 42, {"喜好": "狗"})
    prof2 = store.get_profile("g:1", 42)
    assert prof2["view"]["喜好"] == "狗" and prof2["facts"]["喜好"]["value"] == "狗"


def test_profile_override_priority(store):
    store.set_facts("g:1", 42, {"喜好": "自动抽的猫"})
    store.set_override("g:1", 42, "喜好", "用户更正的狗", by=42)
    prof = store.get_profile("g:1", 42)
    # view 里 override 优先；facts 原值仍在（可回溯）
    assert prof["view"]["喜好"] == "用户更正的狗"
    assert prof["facts"]["喜好"]["value"] == "自动抽的猫"
    assert prof["overrides"]["喜好"]["by"] == 42
    # 删 override → 回落到自动快照
    store.drop_override("g:1", 42, "喜好")
    assert store.get_profile("g:1", 42)["view"]["喜好"] == "自动抽的猫"


def test_profile_drop_fact_and_relation(store):
    store.set_facts("g:1", 42, {"A": "1", "B": "2"})
    store.drop_fact("g:1", 42, "A")
    assert set(store.get_profile("g:1", 42)["facts"]) == {"B"}
    store.set_relation("g:1", 42, "熟人")
    assert store.get_profile("g:1", 42)["relation"] == "熟人"


def test_profile_facts_cap(store):
    with pytest.raises(MemoryError) as ei:
        store.set_facts("g:1", 42,
                        {"k%d" % i: "v" for i in range(MAX_FACTS_PER_USER + 1)})
    assert "上限" in str(ei.value)


def test_profile_empty_facts_rejected(store):
    with pytest.raises(MemoryError):
        store.set_facts("g:1", 42, {})


# ---------------- 6. 群档案 ----------------
def test_group_profile_upsert(store):
    gp = store.update_group_profile("g:1", vibe="欢乐摸鱼", topics=["生图", "游戏"])
    assert gp["vibe"] == "欢乐摸鱼" and "生图" in gp["topics"]
    # 再更新只改传入字段（合并）
    gp2 = store.update_group_profile("g:1", regulars=[42])
    assert gp2["vibe"] == "欢乐摸鱼" and gp2["regulars"] == [42]


# ---------------- 7. gc ----------------
def test_gc_aged_and_cap(store):
    old = Config.MEMORY_KEEP_DAYS
    Config.MEMORY_KEEP_DAYS = 30.0
    try:
        now = time.time()
        store.add_event("老记忆", "g:1", now=now - 40 * 86400)   # 40 天前
        store.add_event("新记忆", "g:1", now=now)
        n = store.gc(now=now)
        assert n >= 1
        assert store.search("老记忆", "g:1", now=now) == []
        assert store.search("新记忆", "g:1", now=now)
        # 幂等：再跑一遍无副作用
        assert store.gc(now=now) == 0
    finally:
        Config.MEMORY_KEEP_DAYS = old


def test_gc_cap_drops_oldest(store):
    now = time.time()
    for i in range(5):
        store.add_event("记忆%d" % i, "g:1", now=now + i)
    old_max = store.MAX_EVENTS if hasattr(store, "MAX_EVENTS") else None
    import qq_bridge.memory.store as st_mod
    old_cap = st_mod.MAX_EVENTS
    st_mod.MAX_EVENTS = 3
    try:
        n = store.gc(now=now + 10)
        assert n == 2  # 5-3=2 条最旧被软删
        remain = store.recent("g:1", limit=10)
        assert [r["text"] for r in remain] == ["记忆4", "记忆3", "记忆2"]
    finally:
        st_mod.MAX_EVENTS = old_cap
        _ = old_max
