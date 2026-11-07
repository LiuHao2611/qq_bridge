# -*- coding: utf-8 -*-
"""budget 包（2026-10-21）—— AI 调用预算闸。

零主包依赖、可裸单测。语义见 manager.BudgetManager docstring。
"""
from .manager import BudgetManager, budget_manager
from .store import BudgetError, BudgetStore, budget_store

__all__ = [
    "BudgetError", "BudgetStore", "budget_store",
    "BudgetManager", "budget_manager",
]
