# -*- coding: utf-8 -*-
"""M3 二期 情感自动判定验收（2026-10-21，emotion/manager.py::auto_judge）。

背景：一期不自动判情感（再调一次 LLM 成本高）。二期复用 AI 每轮自表达的
[表情:分类] marker（免费信号，chat() 已剥离进 ai_chatter.last_mood[scope]），
零额外 LLM 调用落到 per-scope 情感状态。

覆盖（禁止降级契约）：
  1. 认识的情感名（如 开心）/ 口语别名（如 快乐→开心）→ 按强度插值更新并
     落库；返回 {"mood": ...}；store 往返一致；
  2. 不认识的分类（如 委屈）→ 返回 None，不落库、不伪造（诚实跳过）；
  3. None / 空字符串 → 返回 None，不动库（正常态）；
  4. 已知心情后再喂不认识分类 → 心情不变（不污染已存状态）；
  5. 不动好感度（情绪表达 ≠ 关系变化）；
  6. 强度生效：默认 0.6 部分插值；强度 1.0 直接落到目标坐标。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_emotion_manager.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.emotion.manager import EmotionManager  # noqa: E402
from qq_bridge.emotion.mood import MoodEngine  # noqa: E402
from qq_bridge.emotion.store import EmotionStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return EmotionStore(str(tmp_path / "qq_emotion.db"))


@pytest.fixture()
def mgr(store):
    return EmotionManager(store=store, engine=MoodEngine())


# ---------------- 1. 认识的情感 → 更新并落库 ----------------
def test_auto_judge_recognized_updates_and_persists(mgr):
    scope = "g:known"
    out = mgr.auto_judge(scope, "开心")  # 开心 → (0.8, 0.6)
    assert out is not None
    assert "mood" in out
    # 从存储往返读出，确认已落库且与返回一致
    saved = mgr.store.get_mood(scope)
    assert saved.valence > 0.0           # 朝正向（愉悦）移动
    assert saved.arousal > 0.5          # 朝高唤醒（0.6）移动
    assert out["mood"]["valence"] == saved.valence
    assert out["mood"]["arousal"] == saved.arousal


# ---------------- 2. 口语别名 → 归一后识别 ----------------
def test_auto_judge_alias_normalized(mgr):
    # 「快乐」是「开心」的别名，应被识别并移动正向
    scope = "g:alias"
    out = mgr.auto_judge(scope, "快乐")
    assert out is not None
    assert mgr.store.get_mood(scope).valence > 0.0


# ---------------- 3. 不认识的分类 → 返回 None，不落库 ----------------
def test_auto_judge_unknown_returns_none(mgr):
    scope = "g:unknown"
    # 先设一个已知心情（愤怒 valence ≈ -0.7）
    mgr.auto_judge(scope, "愤怒")
    saved = mgr.store.get_mood(scope)
    assert saved.valence < 0.0
    # 喂一个不在 EMOTION_MAP / 别名表里的分类
    out = mgr.auto_judge(scope, "委屈")
    assert out is None
    after = mgr.store.get_mood(scope)
    assert after.valence == saved.valence   # 未改动（不伪造）
    assert after.text == saved.text


# ---------------- 4. None / 空 → 返回 None，不动库 ----------------
def test_auto_judge_none_and_empty_return_none(mgr):
    scope = "g:none"
    assert mgr.auto_judge(scope, None) is None
    assert mgr.auto_judge(scope, "") is None
    # 库里应保持默认中性（未被写脏）
    saved = mgr.store.get_mood(scope)
    assert saved.valence == 0.0


# ---------------- 5. 不动好感度 ----------------
def test_auto_judge_does_not_touch_favor(mgr):
    scope = "g:favor"
    user_id = 123456
    fav_before = mgr.store.get_favor(scope, user_id)["favor"]
    mgr.auto_judge(scope, "开心")
    fav_after = mgr.store.get_favor(scope, user_id)["favor"]
    assert fav_before == fav_after


# ---------------- 6. 强度生效 ----------------
def test_auto_judge_intensity_partial_by_default(mgr):
    # 默认强度 0.6：从中性起只部分插值，达不到满目标 0.8
    out = mgr.auto_judge("g:def", "开心")  # intensity 默认 0.6
    m = out["mood"]
    assert 0.0 < m["valence"] < 0.8


def test_auto_judge_full_intensity_hits_target(mgr):
    # 强度 1.0：直接落到目标坐标（开心 0.8 / 0.6）
    out = mgr.auto_judge("g:full", "开心", intensity=1.0)
    m = out["mood"]
    assert abs(m["valence"] - 0.8) < 1e-6
    assert abs(m["arousal"] - 0.6) < 1e-6
