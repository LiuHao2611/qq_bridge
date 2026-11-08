# -*- coding: utf-8 -*-
"""pic/templates/bounty.py —— Warframe 赏金任务一览卡（薄壳入口）。

2026-11-08 重做：按用户提供的参考图，从「单列长条」改为「两列 4 行、8 张独立卡」，
背景不抄（樱花/立绘一律不用），只抄内容架构，全量对齐、禁止遗漏。

为什么拆成薄壳：上一版单文件 19429B，距 20KB 硬门禁只剩 1KB；两列网格 +
度量优先 + 自检再也塞不进去。按 20KB 门禁要求按功能拆成：

  * ``bounty.py``          —— 本薄壳，保持 ``build(t, data, width, source, **kw)``
                              签名不变，``pic/__init__.py`` 的注册无需改动；
  * ``bounty_model.py``    —— 实时数据 → 参考图内容模型（8 张卡 + 页脚）；
  * ``bounty_atoms.py``    —— 度量优先排版原语（draw_tokens / Columns / 徽章 / 自检）；
  * ``bounty_layout.py``   —— 两列网格、行计划、内容驱动高度、渲染与自检。

数据源 ``game_tools/warframe.py``（280KB）与 ``game_tools/__init__.py``（134KB）
本轮**完全未改动**：这两个存量超大文件受 20KB 门禁约束，任何新增字段都会被拦。
因此新增的 ``tier`` / ``objective`` / ``mission`` 以**可选直读**方式在模型层预留，
数据源日后补齐即可自动生效，无需改渲染层。
"""
from __future__ import annotations

from .bounty_layout import build  # noqa: F401  —— 保持模板注册契约

__all__ = ["build"]
