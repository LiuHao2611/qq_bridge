# -*- coding: utf-8 -*-
"""pic/templates/ —— 场景模板（拼装 widgets 成完整图片）。

每个模板导出 `build(t: Theme, **params) -> Canvas`，由 `pic.render()` 统一调度。
"""
from . import menu, progress, quota, doupo, doupo_menu, doupo_list, riven_analysis  # noqa: F401

__all__ = ["menu", "progress", "quota", "doupo", "doupo_menu", "doupo_list",
           "riven_analysis"]
