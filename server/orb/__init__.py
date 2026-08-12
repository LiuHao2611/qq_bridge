# -*- coding: utf-8 -*-
"""DirectorOrb v2 —— 导演 Agent 前台化身（悬浮球）后端核心。

设计要点（见《设计方案》）：
  - 悬浮球没有独立大脑：所有判断/建议/告警都来自导演 Agent 层已有的数据（DAG 状态、
    品控结果、意图翻译降级、资产质量标记）。
  - 本包只做三件事：翻译(技术→人话)、聚合(分散 trace→全局视图)、代理(用户指令→接口)。
  - 建议去重节流：同一类问题 5 分钟内只提醒一次（dedup）。
  - 灰度开关：module_flags.director_orb_v2 默认关闭。

所有 WebSocket 推送复用既有频道 director:<project_id>，事件名加 orb_ 前缀区分。
"""
from .dedup import DedupLRU, dedup
from .suggestion_engine import OrbSuggestion, build_key, emit, batch_emit

__all__ = [
    "OrbSuggestion", "DedupLRU", "dedup", "build_key", "emit", "batch_emit",
]
