# -*- coding: utf-8 -*-
"""group_style 包（2026-10-21）—— 群风格学习。

零主包依赖、可裸单测（LLM 延迟注入，与 memory/emotion 同款）。
"""
from .learn import LearnError, StyleLearner, style_learner
from .manager import GroupStyleManager, group_style_manager
from .store import GroupStyleError, GroupStyleStore, group_style_store

__all__ = [
    "GroupStyleError", "GroupStyleStore", "group_style_store",
    "LearnError", "StyleLearner", "style_learner",
    "GroupStyleManager", "group_style_manager",
]
