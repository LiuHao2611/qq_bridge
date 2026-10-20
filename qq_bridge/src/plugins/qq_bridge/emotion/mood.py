# -*- coding: utf-8 -*-
"""emotion.mood —— 二维情感 + 好感度 + 关系阶段（M3，2026-10-20）。

契约来源（2026-10-20 实读 MaiBot/MaiMBot Mood System 文档验证）：
  - **valence-arousal 环状情感模型**：valence 愉悦度 −1.0(负)~+1.0(正)；
    arousal 唤醒度 0.0(平静)~1.0(兴奋)；MoodState{valence, arousal, text}；
  - 情感→坐标映射（happy 0.8/0.6、angry −0.7/0.7、sad −0.6/0.3、
    surprised 0.4/0.8、neutral 0/0.5 …）；象限→文本（兴奋/快乐/满足；
    愤怒/焦虑/烦躁；悲伤/疲倦；平静/安宁/放松）；
  - **时间衰减**：valence → 0.0、arousal → 0.5（指数衰减，速率可配）；
  - personification 原则：**语气由 LLM 决定，代码只注入事实**
    （好感度/关系/情绪），**不用关键词表硬编码「对谁说什么话」**。

本地化取舍（个人单机）：
  - 情感状态 **per scope**（机器人对每个群/私聊心情不同，比全局更像人）；
  - 好感度 **per scope per user**，0~100 整数，钳制不出界；
  - 关系阶段由好感度阈值派生（陌生人/点头之交/熟人/朋友/挚友）；
  - 动态态度 = 把「情绪 + 好感度 + 关系阶段」注入 system 提示，语气交给
    模型（personification 同款原则）。

纪律：纯逻辑 + 显式边界；失败显式 EmotionError；不 import nonebot /
不 import 主包：可裸单测。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ..config import Config, get_logger, jlog

log = get_logger()

#: valence 边界（−1 负 ~ +1 正）与中性值
V_MIN, V_MAX, V_NEUTRAL = -1.0, 1.0, 0.0
#: arousal 边界（0 平静 ~ 1 兴奋）与中性值
A_MIN, A_MAX, A_NEUTRAL = 0.0, 1.0, 0.5

#: 象限 → 情绪文本（按 valence/arousal 就近匹配）——**唯一事实源**；
#: 情感名 → 坐标表 EMOTION_MAP 由本表派生（下方），保证「设成 X 就叫 X」。
#
# 锚点必须与 EMOTION_MAP 自洽：apply_emotion("开心") 会把坐标设成 (0.8,0.6)，
# 若象限表里没有该锚点，mood_text 会把它就近叫成「兴奋」——名字与设定打架。
# 故高愉悦中等唤醒区补「开心/愉快」锚点（2026-10-20 修，单测覆盖）。
MOOD_TEXTS: list[tuple[float, float, str]] = [
    (0.8, 0.6, "开心"), (0.7, 0.5, "愉快"), (0.5, 0.7, "兴奋"),
    (0.3, 0.8, "快乐"), (0.2, 0.65, "满足"),
    (0.2, 0.45, "平静"), (0.3, 0.4, "安宁"), (0.5, 0.3, "放松"),
    (-0.5, 0.7, "愤怒"), (-0.3, 0.8, "焦虑"), (-0.2, 0.65, "烦躁"),
    (-0.5, 0.3, "悲伤"), (-0.3, 0.35, "疲倦"), (-0.4, 0.15, "困倦"),
]

#: 情感名 → (valence, arousal)：**由 MOOD_TEXTS 派生**（单一事实源，杜绝
#: 「设成开心却叫兴奋」这类坐标表与命名表打架的 bug）
EMOTION_MAP: dict[str, tuple[float, float]] = {
    name: (v, a) for v, a, name in MOOD_TEXTS
}

#: 口语别名 → 主名（用户输入「生气」归一到象限表里的「愤怒」）
EMOTION_ALIASES: dict[str, str] = {
    "生气": "愤怒", "气": "愤怒", "火大": "愤怒",
    "快乐": "开心", "高兴": "开心", "嗨": "兴奋", "激动": "兴奋",
    "难过": "悲伤", "失落": "悲伤", "伤心": "悲伤",
    "害怕": "焦虑", "怕": "焦虑", "紧张": "焦虑",
    "无聊": "疲倦", "累": "疲倦", "困": "困倦",
    "中性": "平静", "一般": "平静",
}


def normalize_emotion(name: str) -> str:
    """情感名/别名 → 主名（不在表里的原样返回，由调用方报错）。"""
    n = (name or "").strip()
    return EMOTION_ALIASES.get(n, n)

#: 好感度边界与关系阶段阈值（阶段由好感度派生，单一事实源）
FAV_MIN, FAV_MAX, FAV_DEFAULT = 0, 100, 30
RELATION_STAGES: list[tuple[int, str]] = [
    (80, "挚友"), (60, "朋友"), (40, "熟人"), (20, "点头之交"), (0, "陌生人"),
]


class EmotionError(Exception):
    """显式化的情感/关系计算失败。"""


@dataclass
class MoodState:
    """二维情感状态（per scope）。"""
    valence: float = V_NEUTRAL
    arousal: float = A_NEUTRAL
    text: str = "平静"
    updated_at: float = 0.0

    def as_dict(self) -> dict:
        return {"valence": round(self.valence, 3),
                "arousal": round(self.arousal, 3),
                "text": self.text, "updated_at": self.updated_at}


def clamp_valence(v: float) -> float:
    return max(V_MIN, min(V_MAX, float(v)))


def clamp_arousal(a: float) -> float:
    return max(A_MIN, min(A_MAX, float(a)))


def mood_text(valence: float, arousal: float) -> str:
    """按欧氏距离找最近的情感标签（象限表）。"""
    best, best_d = "平静", 1e9
    for v, a, name in MOOD_TEXTS:
        d = math.hypot(valence - v, arousal - a)
        if d < best_d:
            best, best_d = name, d
    return best


def relation_stage(favor: int) -> str:
    """好感度 → 关系阶段文本。"""
    f = int(favor)
    for threshold, name in RELATION_STAGES:
        if f >= threshold:
            return name
    return "陌生人"


class MoodEngine:
    """二维情感 + 好感度 + 关系阶段的计算核心（存储由调用方注入）。"""

    def __init__(self, store=None) -> None:
        self.store = store

    # ---------------- 二维情感 ----------------
    def decay(self, state: MoodState, now: float | None = None,
              half_life: float | None = None) -> MoodState:
        """时间衰减：valence → 0.0、arousal → 0.5（指数，半衰期可配）。

        幂等：同一时刻重复调用结果相同（按 elapsed 计算）。
        """
        now = time.time() if now is None else float(now)
        raw = getattr(Config, "EMO_DECAY_HALFLIFE", 600.0)
        hl = 600.0 if raw is None else float(raw) if half_life is None else float(half_life)
        if hl <= 0 or not state.updated_at:
            state.updated_at = now
            state.text = mood_text(state.valence, state.arousal)
            return state
        elapsed = max(0.0, now - float(state.updated_at))
        if elapsed <= 0:
            return state
        factor = 0.5 ** (elapsed / hl)
        state.valence = clamp_valence(state.valence * factor)
        state.arousal = clamp_arousal(
            A_NEUTRAL + (state.arousal - A_NEUTRAL) * factor)
        state.updated_at = now
        state.text = mood_text(state.valence, state.arousal)
        return state

    def apply_delta(self, state: MoodState, dv: float, da: float,
                    now: float | None = None) -> MoodState:
        """叠加情感增量（先衰减再叠加，钳制边界）。"""
        now = time.time() if now is None else float(now)
        self.decay(state, now=now)
        state.valence = clamp_valence(state.valence + float(dv))
        state.arousal = clamp_arousal(state.arousal + float(da))
        state.updated_at = now
        state.text = mood_text(state.valence, state.arousal)
        return state

    def apply_emotion(self, state: MoodState, emotion: str,
                      intensity: float = 0.5, now: float | None = None) -> MoodState:
        """按情感名更新（别名先归一；查 EMOTION_MAP；强度作插值权重）。"""
        name = normalize_emotion(emotion)
        if name not in EMOTION_MAP:
            raise EmotionError("不认识的情感「%s」（可选：%s）"
                               % (emotion, "、".join(list(EMOTION_MAP)[:8]) + "…"))
        now = time.time() if now is None else float(now)
        w = max(0.0, min(1.0, float(intensity)))
        tv, ta = EMOTION_MAP[name]
        self.decay(state, now=now)
        # 向目标情感插值：强度越大越贴近目标坐标
        state.valence = clamp_valence(state.valence * (1 - w) + tv * w)
        state.arousal = clamp_arousal(state.arousal * (1 - w) + ta * w)
        state.updated_at = now
        state.text = mood_text(state.valence, state.arousal)
        return state

    # ---------------- 好感度 ----------------
    def bump_favor(self, favor: int, delta: int) -> int:
        """好感度增减（钳制 0~100）。"""
        try:
            d = int(delta)
        except (TypeError, ValueError):
            raise EmotionError("好感度变化量必须是整数")
        return max(FAV_MIN, min(FAV_MAX, int(favor) + d))

    # ---------------- 注入文本（动态态度） ----------------
    def mood_prompt(self, state: MoodState) -> str:
        """当前情绪 → 注入 system 提示的描述（语气由 LLM 决定）。"""
        return ("你现在的情绪：%s（愉悦度 %.2f，兴奋度 %.2f）——按这个心情说话，"
                "但别直接把数值说出来。" % (state.text, state.valence, state.arousal))

    def relation_prompt(self, favor: int, relation: str = "") -> str:
        """好感度/关系 → 注入描述（态度由 LLM 决定，不用关键词表）。"""
        stage = relation or relation_stage(favor)
        return ("你对这个用户的好感度 %d/100，关系：%s——按这个亲疏程度把握语气，"
                "别提数值。" % (int(favor), stage))


#: 模块级单例（单测各自 new 隔离）
mood_engine = MoodEngine()
