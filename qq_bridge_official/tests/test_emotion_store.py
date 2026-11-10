# -*- coding: utf-8 -*-
"""M3 情感存储 + 日程 + 门面验收（2026-10-20，emotion/store.py +
schedule.py + manager.py）。

store 覆盖：
  1. moods：默认中性 → 存取往返；per scope 隔离；
  2. affections：默认好感度 → 存取 + 钳制；set_relation 手动标签；
     top_favors 排行降序；
  3. schedules：add（空标题/坏时间显式错）/list（未完成优先+按到期升序）/
     mark_done/mark_notified（幂等）/soft_delete/due_schedules（窗口内
     未通知）/gc（已完成+过期清理、幂等）。

schedule 覆盖：
  4. validate：结构校验、过去时间剔除、超 1 年剔除、条数上限；
  5. extract：JSON → 条目；坏 JSON/LLM 失败 → 显式 ScheduleError；
     围栏剥壳；空输入拒绝；
  6. format_due_line：人话行（已到期/分钟后/小时后/日期）。

manager 覆盖：
  7. prompt：三段组装（情绪+亲疏+安排）；失败不炸返回空串；
  8. bump：情感+好感度一次更新并落库；
  9. update_from_chat：抽取落库；开关关闭时不动；抽取失败静默返回空；
  10. status_text：人话状态（心情/好感/安排）。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_emotion_store.py -v
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.emotion.manager import EmotionManager  # noqa: E402
from qq_bridge.emotion.mood import (  # noqa: E402
    A_NEUTRAL, FAV_DEFAULT, FAV_MAX, V_NEUTRAL, MoodEngine, MoodState)
from qq_bridge.emotion.schedule import (  # noqa: E402
    ScheduleError, ScheduleExtractor, format_due_line)
from qq_bridge.emotion.store import EmotionStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return EmotionStore(str(tmp_path / "qq_emotion.db"))


# ---------------- 1. moods ----------------
def test_mood_default_and_roundtrip(store):
    st = store.get_mood("g:new")
    assert st.valence == V_NEUTRAL and st.arousal == A_NEUTRAL
    st.valence, st.arousal, st.text, st.updated_at = 0.6, 0.8, "兴奋", time.time()
    store.save_mood("g:1", st)
    got = store.get_mood("g:1")
    assert abs(got.valence - 0.6) < 1e-6 and got.text == "兴奋"
    assert store.get_mood("g:2").valence == V_NEUTRAL  # scope 隔离


# ---------------- 2. affections ----------------
def test_favor_default_and_save(store):
    assert store.get_favor("g:1", 42)["favor"] == FAV_DEFAULT
    store.save_favor("g:1", 42, 70)
    assert store.get_favor("g:1", 42)["favor"] == 70
    store.save_favor("g:1", 42, 999)   # 钳制
    assert store.get_favor("g:1", 42)["favor"] == FAV_MAX
    assert store.get_favor("g:2", 42)["favor"] == FAV_DEFAULT  # scope 隔离


def test_relation_override_and_ranking(store):
    store.save_favor("g:1", 1, 20)
    store.save_favor("g:1", 2, 80)
    store.save_favor("g:1", 3, 50)
    rows = store.top_favors("g:1", limit=3)
    assert [r["user_id"] for r in rows] == [2, 3, 1]  # 降序
    store.set_relation("g:1", 1, "小跟班")
    assert store.get_favor("g:1", 1)["relation"] == "小跟班"
    assert store.get_favor("g:1", 1)["favor"] == 20   # 改关系不动好感度


# ---------------- 3. schedules ----------------
def test_schedule_crud(store):
    now = time.time()
    it = store.add_schedule("面试", now + 3600, "g:1", user_id=42)
    assert it["title"] == "面试" and it["done"] == 0
    with pytest.raises(Exception):
        store.add_schedule("   ", now + 60, "g:1")
    with pytest.raises(Exception):
        store.add_schedule("x", "not-a-timestamp", "g:1")
    assert len(store.list_schedules("g:1")) == 1
    assert store.mark_done(it["id"], "g:1")
    assert store.list_schedules("g:1") == []            # 默认不含已完成
    assert len(store.list_schedules("g:1", include_done=True)) == 1
    assert store.soft_delete_schedule(it["id"], "g:1")
    assert store.list_schedules("g:1", include_done=True) == []


def test_schedule_order_and_scope(store):
    now = time.time()
    store.add_schedule("晚的", now + 7200, "g:1")
    store.add_schedule("早的", now + 600, "g:1")
    store.add_schedule("别的群", now + 60, "g:2")
    items = store.list_schedules("g:1")
    assert [i["title"] for i in items] == ["早的", "晚的"]  # 按到期升序


def test_due_schedules_window_and_idempotent(store):
    now = time.time()
    soon = store.add_schedule("快到了", now + 60, "g:1", user_id=42)
    later = store.add_schedule("还早", now + 7200, "g:1")
    due = store.due_schedules("g:1", now=now, window=3600.0)
    assert [d["id"] for d in due] == [soon["id"]]   # 窗口内的才到期
    # 标记通知后不再出现（幂等去重）
    assert store.mark_notified(soon["id"], "g:1")
    assert store.due_schedules("g:1", now=now, window=3600.0) == []
    assert store.mark_notified(soon["id"], "g:1") is False  # 重复标记 False


def test_gc_cleans_done_and_expired(store):
    now = time.time()
    store.add_schedule("已完成", now + 60, "g:1")
    done = store.list_schedules("g:1")[0]
    store.mark_done(done["id"], "g:1")
    old = store.add_schedule("过期很久", now - 90 * 86400, "g:1")
    n = store.gc(now=now)
    assert n >= 2
    assert store.list_schedules("g:1", include_done=True) == []
    assert store.gc(now=now) == 0  # 幂等


# ---------------- 4/5. schedule extract ----------------
def test_validate_filters():
    now = time.time()
    out = ScheduleExtractor.validate({
        "items": [
            {"title": "面试", "due_ts": now + 3600},
            {"title": "过去的事", "due_ts": now - 3600},     # 剔除
            {"title": "太远", "due_ts": now + 400 * 86400},  # 剔除
            {"title": "", "due_ts": now + 60},               # 剔除
            "坏条目",
        ]}, now=now)
    assert len(out) == 1 and out[0]["title"] == "面试"
    with pytest.raises(ScheduleError):
        ScheduleExtractor.validate("not dict", now=now)
    with pytest.raises(ScheduleError):
        ScheduleExtractor.validate({"items": "x"}, now=now)


class _FakeLLM:
    def __init__(self, reply, raises=None):
        self.reply, self.raises = reply, raises

    async def chat(self, messages, tools=None, temperature=None):
        if self.raises:
            raise self.raises
        return {"role": "assistant", "content": self.reply, "tool_calls": []}


@pytest.mark.anyio
async def test_extract_ok():
    now = time.time()
    plan = {"items": [{"title": "面试", "due_ts": now + 3600}]}
    ex = ScheduleExtractor(_FakeLLM(json.dumps(plan)))
    items = await ex.extract("我明天下午三点有面试", now=now)
    assert items and items[0]["title"] == "面试"


@pytest.mark.anyio
async def test_extract_bad_json_and_llm_error():
    ex = ScheduleExtractor(_FakeLLM("我觉得你该记一下"))
    with pytest.raises(ScheduleError) as ei:
        await ex.extract("明天有事")
    assert "JSON" in str(ei.value)
    from qq_bridge.tools.llm import LLMError
    ex2 = ScheduleExtractor(_FakeLLM(None, raises=LLMError("no_key")))
    with pytest.raises(ScheduleError) as ei2:
        await ex2.extract("明天有事")
    assert "no_key" in str(ei2.value)


@pytest.mark.anyio
async def test_extract_empty_input():
    with pytest.raises(ScheduleError):
        await ScheduleExtractor(_FakeLLM("{}")).extract("  ")


def test_format_due_line():
    now = time.time()
    assert "已到期" in format_due_line({"title": "x", "due_at": now - 60}, now=now)
    assert "分钟后" in format_due_line({"title": "x", "due_at": now + 600}, now=now)
    assert "小时后" in format_due_line({"title": "x", "due_at": now + 7200}, now=now)
    assert "✅" in format_due_line({"title": "x", "due_at": now + 60, "done": 1}, now=now)


# ---------------- 7/8/9/10. manager ----------------
@pytest.fixture()
def mgr(tmp_path):
    return EmotionManager(store=EmotionStore(str(tmp_path / "e.db")),
                          engine=MoodEngine())


def test_manager_prompt_assembly(mgr):
    now = time.time()
    mgr.store.save_mood("g:1", MoodState(0.7, 0.8, "兴奋", now))
    mgr.store.save_favor("g:1", 42, 75)
    mgr.store.add_schedule("面试", now + 3600, "g:1", user_id=42)
    text = mgr.prompt("g:1", 42, now=now)
    assert "兴奋" in text          # 情绪
    assert "75" in text            # 好感度
    assert "朋友" in text          # 关系阶段
    assert "面试" in text          # 安排


def test_manager_prompt_failure_returns_empty(mgr, monkeypatch):
    def boom(*a, **kw):
        from qq_bridge.emotion.mood import EmotionError
        raise EmotionError("库炸了")

    monkeypatch.setattr(mgr.store, "get_mood", boom)
    assert mgr.prompt("g:1", 42) == ""   # 不炸，本轮无情感注入


def test_manager_bump(mgr):
    now = time.time()
    snap = mgr.bump("g:1", 42, dv=0.5, da=0.2, dfavor=8, now=now)
    assert snap["mood"]["valence"] > 0.4
    assert snap["favor"]["favor"] == FAV_DEFAULT + 8
    # 二次调用会先衰减再叠加（状态持续）
    mgr.bump("g:1", 42, dv=-9.0, dfavor=-100, now=now)
    assert mgr.store.get_favor("g:1", 42)["favor"] == 0   # 钳到下限


def test_manager_bump_by_emotion(mgr):
    now = time.time()
    snap = mgr.bump("g:1", 42, emotion="生气", intensity=1.0, now=now)
    assert snap["mood"]["valence"] < 0


@pytest.mark.anyio
async def test_manager_update_from_chat(mgr):
    now = time.time()
    plan = {"items": [{"title": "考试", "due_ts": now + 7200}]}

    async def fake_extract(text, now=None):
        return ScheduleExtractor.validate(plan, now=now)

    mgr.extractor.extract = fake_extract
    out = await mgr.update_from_chat("g:1", 42, "我后天考试", now=now)
    assert len(out["schedules"]) == 1
    assert mgr.store.list_schedules("g:1")[0]["title"] == "考试"


@pytest.mark.anyio
async def test_manager_update_disabled_or_failed(mgr):
    old = Config.EMO_SCHEDULE_EXTRACT
    Config.EMO_SCHEDULE_EXTRACT = False
    try:
        assert await mgr.update_from_chat("g:1", 42, "明天面试") == {"schedules": []}
    finally:
        Config.EMO_SCHEDULE_EXTRACT = old

    async def boom(text, now=None):
        raise ScheduleError("模型没按协议输出")

    mgr.extractor.extract = boom
    assert await mgr.update_from_chat("g:1", 42, "明天面试") == {"schedules": []}


def test_manager_status_text(mgr):
    now = time.time()
    # 坐标与标签一致（"开心" = EMOTION_MAP 的 0.8/0.6）——decay 按坐标重算
    # 标签，坐标与标签要自洽，否则文本会被就近重命名为别的情绪
    mgr.store.save_mood("g:1", MoodState(0.8, 0.6, "开心", now))
    mgr.store.save_favor("g:1", 42, 65)
    mgr.store.add_schedule("出差", now + 86400, "g:1", user_id=42)
    text = mgr.status_text("g:1", 42)
    assert "开心" in text and "65" in text and "朋友" in text and "出差" in text
