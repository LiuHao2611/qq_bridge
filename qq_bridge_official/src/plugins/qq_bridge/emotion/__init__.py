# -*- coding: utf-8 -*-
"""emotion —— M3 情感 + 日程 + 关系（2026-10-20）。

契约来源（2026-10-20 实读验证）：
  - **MaiBot / MaiMBot Mood System**：valence(−1~+1 愉悦度) ×
    arousal(0~1 唤醒度) 二维环状情感模型；情感→坐标映射表；时间衰减
    （valence→0、arousal→0.5）；象限→情绪文本；get_prompt() 注入 LLM；
  - **personification**：好感度 + inner state + 关系图 + **动态态度**
    （语气由 LLM 决定，代码只注入事实，不用关键词表）。

组成：
  mood.py     二维情感状态机 + 好感度 + 关系阶段 + 注入文本（纯逻辑）
  store.py    SQLite 三表：moods（per scope 情感）/ affections（per
              scope+user 好感度+关系标签）/ schedules（日程，软删+gc）
  schedule.py 日程 LLM 抽取（未来安排）+ 到期判定辅助
  manager.py  门面：prompt（注入）/ bump（情感+好感度更新）/
              update_from_chat（抽日程）/ /情感 命令族服务

与既有能力的分工（避免重复）：
  - 短期心情：本模块（per scope 二维情感）；
  - 提醒：M2 reminders（用户主动设，到点回传原话）——不受影响；
  - 日程：本模块（机器人自己从对话里听到并记下，到点主动关心）；
  - 记忆：M1 memory（事实/画像/群档案）——情感是另一维，互不覆盖。

纪律：同步 sqlite3 + busy_timeout（调用方 to_thread 包）；全失败路径
显式（EmotionError / ScheduleError）；不 import nonebot / 不 import
主包：可裸单测。
"""
from .mood import (EMOTION_MAP, FAV_DEFAULT, FAV_MAX, FAV_MIN, MoodEngine,
                   MoodState, EmotionError, clamp_arousal, clamp_valence,
                   mood_engine, mood_text, relation_stage)
from .store import EmotionStore, emotion_store
from .schedule import ScheduleError, ScheduleExtractor, format_due_line, schedule_extractor
from .manager import EmotionManager, emotion_manager

__all__ = [
    "MoodEngine", "MoodState", "EmotionError", "EMOTION_MAP",
    "clamp_valence", "clamp_arousal", "mood_text", "relation_stage",
    "FAV_MIN", "FAV_MAX", "FAV_DEFAULT", "mood_engine",
    "EmotionStore", "emotion_store",
    "ScheduleExtractor", "ScheduleError", "schedule_extractor", "format_due_line",
    "EmotionManager", "emotion_manager",
]
