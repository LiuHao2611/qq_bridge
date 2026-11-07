# -*- coding: utf-8 -*-
"""checkin —— 每日签到模块（激励 / 奖励 / 排行榜）。

零主包依赖（仅 `..config` / `..history` 延迟引用），可裸单测。
"""
from __future__ import annotations

from .manager import CheckinManager, MILESTONES
from .store import CheckinError, CheckinStore, _resolve_db_path

#: 模块级单例（与 budget/store、emotion/store 同款模式，bot 启动时建库）。
checkin_store = CheckinStore()
checkin_manager = CheckinManager(checkin_store)

__all__ = [
    "CheckinError", "CheckinManager", "CheckinStore",
    "MILESTONES", "checkin_manager", "checkin_store", "_resolve_db_path",
]
