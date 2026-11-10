# -*- coding: utf-8 -*-
"""M3 二维情感 + 好感度 + 关系阶段验收（2026-10-20，emotion/mood.py）。

覆盖（契约 2026-10-20 实读 MaiBot/MaiMBot Mood System 验证）：
  1. clamp：valence ∈[−1,1]、arousal ∈[0,1] 边界钳制；
  2. mood_text：按 valence/arousal 就近匹配象限文本（正向高唤醒=兴奋类、
     负向低唤醒=疲倦/悲伤、中性=平静）；
  3. decay：valence→0、arousal→0.5 指数衰减；半衰期生效；幂等（同刻
     重复调用不变）；
  4. apply_delta：先衰减后叠加 + 钳制；
  5. apply_emotion：查表插值（强度权重）；未知情感名 → 显式 EmotionError；
  6. bump_favor：0~100 钳制；非法 delta 显式错误；
  7. relation_stage：阈值派生（陌生人/点头之交/熟人/朋友/挚友）；
  8. mood_prompt/relation_prompt：注入文本含情绪/亲疏且**不硬编码语气**。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_emotion_mood.py -v
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.emotion.mood import (  # noqa: E402
    A_MAX, A_MIN, A_NEUTRAL, EMOTION_MAP, FAV_MAX, FAV_MIN, V_MAX, V_MIN,
    MoodEngine, MoodState, EmotionError, clamp_arousal, clamp_valence,
    mood_text, relation_stage)


# ---------------- 1. 边界钳制 ----------------
def test_clamp_bounds():
    assert clamp_valence(5.0) == V_MAX and clamp_valence(-5.0) == V_MIN
    assert clamp_arousal(9.0) == A_MAX and clamp_arousal(-1.0) == A_MIN
    assert clamp_valence(0.3) == 0.3 and clamp_arousal(0.4) == 0.4


# ---------------- 2. 象限文本 ----------------
def test_mood_text_quadrants():
    assert mood_text(0.5, 0.7) in ("兴奋", "快乐", "满足")
    assert mood_text(-0.5, 0.3) in ("悲伤", "疲倦", "困倦")
    assert mood_text(0.0, 0.5) in ("平静", "安宁", "放松", "满足")
    assert mood_text(-0.6, 0.75) in ("愤怒", "焦虑", "烦躁")


# ---------------- 3. 衰减 ----------------
def test_decay_toward_neutral():
    eng = MoodEngine()
    now = time.time()
    st = MoodState(valence=0.8, arousal=0.9, text="兴奋", updated_at=now - 600)
    eng.decay(st, now=now, half_life=600.0)
    assert abs(st.valence - 0.4) < 0.01      # 一个半衰期 → 减半
    assert abs(st.arousal - (0.5 + 0.4 * 0.5)) < 0.01  # 向 0.5 收敛
    assert st.updated_at == now
    assert st.text  # 文本同步更新


def test_decay_long_time_goes_neutral():
    eng = MoodEngine()
    now = time.time()
    st = MoodState(valence=-0.9, arousal=1.0, text="愤怒", updated_at=now - 100000)
    eng.decay(st, now=now, half_life=600.0)
    assert abs(st.valence) < 0.01
    assert abs(st.arousal - 0.5) < 0.01
    assert st.text in ("平静", "安宁", "放松", "满足")


def test_decay_idempotent_same_time():
    eng = MoodEngine()
    now = time.time()
    st = MoodState(valence=0.6, arousal=0.7, text="", updated_at=now)
    eng.decay(st, now=now)
    v1, a1 = st.valence, st.arousal
    eng.decay(st, now=now)
    assert (st.valence, st.arousal) == (v1, a1)  # 同刻重复调用不继续衰减


def test_decay_uses_config_half_life():
    eng = MoodEngine()
    old = Config.EMO_DECAY_HALFLIFE
    Config.EMO_DECAY_HALFLIFE = 100.0
    try:
        now = time.time()
        st = MoodState(valence=1.0, arousal=1.0, text="", updated_at=now - 100)
        eng.decay(st, now=now)  # 不传 half_life → 用配置
        assert abs(st.valence - 0.5) < 0.01
    finally:
        Config.EMO_DECAY_HALFLIFE = old


# ---------------- 4. delta 叠加 ----------------
def test_apply_delta_clamped():
    eng = MoodEngine()
    now = time.time()
    st = MoodState(valence=0.9, arousal=0.9, text="", updated_at=now)
    eng.apply_delta(st, 5.0, 5.0, now=now)     # 溢出 → 钳到边界
    assert st.valence == V_MAX and st.arousal == A_MAX
    st2 = MoodState(valence=-0.9, arousal=0.1, text="", updated_at=now)
    eng.apply_delta(st2, -5.0, -5.0, now=now)
    assert st2.valence == V_MIN and st2.arousal == A_MIN


# ---------------- 5. 情感名更新 ----------------
def test_apply_emotion_interpolation():
    eng = MoodEngine()
    now = time.time()
    st = MoodState(valence=0.0, arousal=0.5, text="", updated_at=now)
    eng.apply_emotion(st, "开心", 1.0, now=now)   # 强度 1 → 直接到目标坐标
    assert abs(st.valence - EMOTION_MAP["开心"][0]) < 0.01
    assert abs(st.arousal - EMOTION_MAP["开心"][1]) < 0.01


def test_emotion_map_and_mood_text_consistent():
    """映射表与命名表必须自洽：EMOTION_MAP 由 MOOD_TEXTS 派生，故
    apply_emotion(主名) 后的坐标必然叫回同名（杜绝「设成开心却叫兴奋」）。
    别名（生气）归一到主名（愤怒）后同样自洽。"""
    from qq_bridge.emotion.mood import normalize_emotion
    eng = MoodEngine()
    now = time.time()
    for name in ("开心", "兴奋", "愤怒", "悲伤", "平静", "疲倦"):
        st = MoodState(valence=0.0, arousal=0.5, text="", updated_at=now)
        eng.apply_emotion(st, name, 1.0, now=now)
        assert st.text == name, "%s → 坐标 (%s,%s) 被叫成 %s" % (
            name, st.valence, st.arousal, st.text)
    # 别名归一：设「生气」→ 主名「愤怒」，文本与坐标一致
    st2 = MoodState(valence=0.0, arousal=0.5, text="", updated_at=now)
    eng.apply_emotion(st2, "生气", 1.0, now=now)
    assert normalize_emotion("生气") == "愤怒"
    assert st2.text == "愤怒"


def test_apply_emotion_unknown_raises():
    eng = MoodEngine()
    with pytest.raises(EmotionError) as ei:
        eng.apply_emotion(MoodState(), "不存在的情感")
    assert "不认识" in str(ei.value)


# ---------------- 6. 好感度 ----------------
def test_bump_favor_clamped():
    eng = MoodEngine()
    assert eng.bump_favor(95, 50) == FAV_MAX
    assert eng.bump_favor(5, -50) == FAV_MIN
    assert eng.bump_favor(30, 7) == 37
    with pytest.raises(EmotionError):
        eng.bump_favor(30, "abc")


# ---------------- 7. 关系阶段 ----------------
def test_relation_stage_thresholds():
    assert relation_stage(0) == "陌生人"
    assert relation_stage(25) == "点头之交"
    assert relation_stage(45) == "熟人"
    assert relation_stage(65) == "朋友"
    assert relation_stage(90) == "挚友"


# ---------------- 8. 注入文本 ----------------
def test_prompts_contain_facts():
    eng = MoodEngine()
    st = MoodState(valence=0.7, arousal=0.8, text="兴奋")
    mp = eng.mood_prompt(st)
    assert "兴奋" in mp and "0.70" in mp
    rp = eng.relation_prompt(70)
    assert "70" in rp and "朋友" in rp
    rp2 = eng.relation_prompt(10, "小跟班")   # 手动关系标签优先
    assert "小跟班" in rp2


def test_prompt_no_hardcoded_tone():
    """personification 原则：注入文本给事实，不硬编码「对谁说什么话」。"""
    eng = MoodEngine()
    rp = eng.relation_prompt(90)
    for banned in ("亲爱的", "宝贝", "滚开", "讨厌你"):
        assert banned not in rp
    assert "把握语气" in rp
