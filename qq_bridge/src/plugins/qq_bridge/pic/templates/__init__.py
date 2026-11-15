# -*- coding: utf-8 -*-
"""pic/templates/ —— 场景模板（拼装 widgets 成完整图片）。

每个模板导出 `build(t: Theme, **params) -> Canvas`，由 `pic.render()` 统一调度。
"""
from . import (menu, progress, quota, doupo, doupo_menu, doupo_list, doupo_skills,  # noqa: F401
               doupo_faction, riven_analysis)

__all__ = ["menu", "progress", "quota", "doupo", "doupo_menu", "doupo_list",
           "doupo_skills", "doupo_faction", "riven_analysis"]
