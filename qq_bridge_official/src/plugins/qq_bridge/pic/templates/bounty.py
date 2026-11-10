# -*- coding: utf-8 -*-
"""pic/templates/bounty.py —— Warframe 赏金任务一览卡（薄壳入口）。

2026-11-08 重做：按用户提供的参考图，从「单列长条」改为「两列 4 行、8 张独立卡」，
背景不抄（樱花/立绘一律不用），只抄内容架构，全量对齐、禁止遗漏。

2026-11-08 续：用户指出「奖励内容呢，而且任务内容和别人机器人的也不一样」，
查证为 ``warframe.py::_wfwiki_payload`` 反解 RSC 字符串失败且静默吞异常，
``bountyBoard`` 恒空 → MOD / 债券奖励整段消失；``groups`` 被硬编码清单取代 →
任务名与目标行对不上。因 ``warframe.py`` 280KB 受 20KB 门禁约束不能改大，
改由本入口调用 ``bounty_live.enrich(data)`` 在**渲染侧**用同一份已抓好的 RSC
payload + WFA 词库补回真实内容。这样 ``pic/__init__.py`` 的注册无需改动，
文本降级链路 ``bounties_text()`` 也不受影响。

为什么拆成薄壳：上一版单文件 19429B，距 20KB 硬门禁只剩 1KB；两列网格 +
度量优先 + 自检再也塞不进去。按 20KB 门禁要求按功能拆成：

  * ``bounty.py``          —— 本薄壳，保持 ``build(t, data, width, source, **kw)``
                              签名不变，``pic/__init__.py`` 的注册无需改动；
  * ``bounty_live.py``     —— 正确解码 RSC + WFA 词库，补齐生产数据；
  * ``bounty_model.py``    —— 实时数据 → 参考图内容模型（8 张卡 + 页脚）；
  * ``bounty_icons.py``    —— 中文名 → DE 官方 CDN 真实物品图标；
  * ``bounty_atoms.py``    —— 度量优先排版原语（draw_tokens / Columns / 徽章 / 自检）；
  * ``bounty_layout.py``   —— 两列网格、行计划、内容驱动高度、渲染与自检。

数据源 ``game_tools/warframe.py``（280KB）与 ``game_tools/__init__.py``（134KB）
本轮**完全未改动**：这两个存量超大文件受 20KB 门禁约束，任何新增字段都会被拦。
"""
from __future__ import annotations

from . import bounty_live
from .bounty_layout import build as _build

__all__ = ["build"]


def build(t, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw):
    """注册入口：先用实时 RSC 补齐数据，再交给两列网格渲染。

    ``enrich`` 在离线/测试（无缓存）时显式告警并原样返回，绝不编造奖励，
    因此 ``render_sync`` / ``render_raw`` / ``render`` 三条链路都安全。
    """
    return _build(t, bounty_live.enrich(data), width, source, **kw)
