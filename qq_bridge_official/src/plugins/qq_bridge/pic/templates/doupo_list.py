# -*- coding: utf-8 -*-
"""pic/templates/doupo_list.py —— 斗破·通用列表卡。

用于群聊「dp斗技 / dp异火 / dp宝库 / dp背包 / dp排行」等列表型口令的卡片化回复，
替代原先的多行纯文本（逐项扫描不便）。

版式（羊皮纸米 / 朱红 / 暗金玄幻观感，与 doupo.py 人物卡、doupo_menu.py 菜单卡同一套调色）：
  * 顶部居中标题 + 图标 + 贯通分割线；
  * 可选副标题（金币 / 药渣 / 进度 等概览）；
  * 列表行：可选左侧 badge（键位 / 名次）+ 主标题（左）+ 右侧对齐信息（价格 / 战力 / 状态）
    + 可选副行（说明，过长自动折行）+ 可选等级渐变条；
  * 底部提示 tip。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * **不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色复用 ``Theme.bg_overrides["doupo"]``（与人物卡 / 菜单卡同一卡片主题）。

输入（build 的 params，均为 JSON 友好类型，便于编辑器预览）：
  icon      标题图标名（icons.ICONS 之一，缺省/非法回退 sword）
  title     标题
  subtitle  副标题（可留空）
  rows      [{"badge","title","right","sub","bar":{"cur","max"},"accent"}]
  tip      底部提示（可留空）
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与人物卡 / 菜单卡共用配色/度量/进度条工具（同包内复用，避免两份 _pal 各自漂移）
from .doupo import _bar, _card_alpha, _fit, _pal, _w

# ---- 排版 ----
PAD = 28
FS_TITLE = 34
FS_SUB = 15
FS_ITEM = 20
FS_SMALL = 15
FS_TIP = 14
FS_BADGE = 14
ICON = 22
BAR_H = 12
BADGE_W = 40
ROW_PAD = 14
ROW_MIN = 58
DESC_LH = FS_SMALL + 4
ICON_STYLE = "line"

#: 编辑器预览用的示例数据（pic 包不 import 插件层，故内置示例）。
_DEMO = {
    "icon": "bolt",
    "title": "斗技",
    "subtitle": "快捷键 1/3/7/9 · 升一级伤害 +20%",
    "rows": [
        {"badge": "1", "title": "龙吟碎　3/5", "sub": "升一级伤害 +20%，将目标击晕几率 +20%",
         "bar": {"cur": 3, "max": 5}},
        {"badge": "3", "title": "天火三玄变　1/5", "sub": "焚炎附体，三段火劲贯体",
         "bar": {"cur": 1, "max": 5}},
    ],
    "tip": "用法：dp升级 龙吟碎 · 现有 焚血散 ×3",
}


def _rows_of(data) -> list:
    return [r for r in (data.get("rows") or []) if isinstance(r, dict)]


def _row_h(item: dict, content_w: int) -> int:
    """单行高度：主标题 + 可选副行（折行）+ 可选等级条。"""
    h = FS_ITEM + 12
    sub = item.get("sub") or ""
    if sub:
        lines = Canvas.wrap(sub, FS_SMALL, "regular", content_w - BADGE_W - 14)
        h += max(1, len(lines)) * DESC_LH
    if item.get("bar"):
        h += BAR_H + 10
    return max(ROW_MIN, h)


def _measure(data: dict, content_w: int) -> int:
    """总高度（与 build 里的 y 步进严格一致，否则卡底留白会歪）。"""
    y = PAD + FS_TITLE + 8 + 1 + 18
    if data.get("subtitle"):
        y += FS_SUB + 10
    for r in _rows_of(data):
        y += _row_h(r, content_w) + ROW_PAD
    y += -ROW_PAD + 1 + 12 + FS_TIP + PAD
    return max(300, y)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    h = _measure(data, content_w)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    icon = str(data.get("icon") or "sword")
    if not icons.exists(icon):
        icon = "sword"
    title = str(data.get("title") or "斗破")
    tw = _w(title, FS_TITLE, "light")
    ico_w = FS_TITLE + 10
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, icon, tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE,
               p["title_color"], style=ICON_STYLE)
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 副标题 ----
    sub = data.get("subtitle")
    if sub:
        c.text((x0, y + FS_SUB // 2), _fit(str(sub), content_w, FS_SUB),
               size=FS_SUB, color=p["dim_color"], anchor="lm")
        y += FS_SUB + 10

    # ---- 列表行 ----
    for item in _rows_of(data):
        rh = _row_h(item, content_w)
        badge = item.get("badge")
        if badge:
            btext = p["accent_color"] if item.get("accent") else p["chip_text"]
            bcolor = p["accent_color"] if item.get("accent") else p["chip_line"]
            by = y + (rh - (FS_BADGE + 12)) // 2
            c.rounded_rect((x0, by, x0 + BADGE_W - 8, by + FS_BADGE + 12), radius=8,
                           fill=p["chip_bg"], outline=bcolor, width=1)
            c.text((x0 + (BADGE_W - 8) // 2, by + (FS_BADGE + 12) // 2),
                   _fit(str(badge), BADGE_W - 16, FS_BADGE), size=FS_BADGE,
                   color=btext, anchor="mm")
            title_x = x0 + BADGE_W + 4
        else:
            title_x = x0 + 14

        # 右侧信息测量（用于给标题让位，避免与右侧文字重叠）
        right = item.get("right")
        right_w = _w(str(right), FS_ITEM, "regular") + 14 if right else 0

        # 主标题
        tcolor = p["accent_color"] if item.get("accent") else p["text_color"]
        ty = y + FS_ITEM // 2 + 2
        title_max = (x0 + content_w) - title_x - 8 - right_w
        c.text((title_x, ty), _fit(str(item.get("title") or ""), title_max, FS_ITEM),
               size=FS_ITEM, color=tcolor, anchor="lm")
        # 右侧信息
        if right:
            c.text((x0 + content_w, ty), _fit(str(right), (x0 + content_w) - title_x, FS_ITEM),
                   size=FS_ITEM, color=p["dim_color"], anchor="rm")

        # 副行（折行）
        cy = ty + FS_ITEM // 2 + 8
        sub = item.get("sub") or ""
        if sub:
            lines = Canvas.wrap(sub, FS_SMALL, "regular", (x0 + content_w) - title_x - 8)
            for i, ln in enumerate(lines):
                c.text((title_x, cy + i * DESC_LH), ln, size=FS_SMALL,
                       color=p["dim_color"], anchor="lm")
            cy += len(lines) * DESC_LH
        # 等级渐变条
        bar = item.get("bar")
        if bar:
            by = y + rh - BAR_H - 8
            cur = int(bar.get("cur", 0))
            mx = max(1, int(bar.get("max", 1)))
            _bar(c, title_x, by, (x0 + content_w) - title_x, int(cur * 100 / mx),
                 p["bar_from"], p["bar_to"], p["track_color"])

        y += rh + ROW_PAD

    # ---- 底部提示 ----
    y -= ROW_PAD
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip:
        c.text((x0, y + FS_TIP // 2), _fit(str(tip), content_w, FS_TIP),
               size=FS_TIP, color=p["dim_color"], anchor="lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
