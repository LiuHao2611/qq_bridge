# -*- coding: utf-8 -*-
"""pic/templates/xiuxian_help.py —— 文字修仙 · 玩法细则卡。

「修仙菜单」只回答"有哪些口令"，这张卡回答"规则怎么算"：修为怎么涨、
突破成功率、灵石怎么被抢、宗门战怎么判、Boss 怎么打。

版式（单列，圆点要点 + 自动折行）：
  * 与 xiuxian.py / xiuxian_menu.py 同色系、同标题样式（手绘清单字形）；
  * 每个分组：4px 青绿竖条 + 分组名；
  * 每条要点：6px 圆点 + 「要点：细则」，超宽自动折行并悬挂缩进；
  * 底部提示：回「修仙菜单」看口令。

为什么单列而不是两列：细则动辄 40+ 字（如秘境六种节点的收益区间），
两列会挤成 12px 且频繁截断；单列折行能把数值完整说完 —— 帮助卡的价值就在"说全"。

输入（build 的 params）：
  title   标题，默认「修仙玩法」
  groups  [{"name": 分组名, "rows": [[要点, 细则], ...]}, ...]
  tip     底部提示（可留空）
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与状态卡/菜单卡共用配色与度量（同包复用，避免第三份 _pal 漂移）
from .xiuxian import _card_alpha, _fit, _pal, _w

# ---- 排版 ----
PAD = 26
FS_TITLE = 30
FS_GROUP = 16
FS_BODY = 16
LINE_H = 26          # 单行行高（折行后每行 +26）
ITEM_GAP = 6         # 要点之间留白
GROUP_GAP = 18       # 分组之间留白
BULLET_INDENT = 18   # 圆点后文字缩进（折行后的悬挂缩进同样用它）
ICON_STYLE = "line"

#: 编辑器预览示例（pic 包不 import 插件层，故内置少量示例避免预览空白）
_DEMO = [
    {"name": "成长 · 修为与境界",
     "rows": [["修为", "按真实时间自动累积，离线照样涨"],
              ["闭关", "修为产出 ×2，出关才结算"]]},
    {"name": "对抗 · 灵石怎么算",
     "rows": [["切磋", "胜者夺对方 5% 灵石；输了同样赔 5% 给对面"]]},
]


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


def _list_glyph(c: Canvas, cx: int, cy: int, color: str) -> None:
    """手绘「清单」字形（三条不等长圆头横线）；icons 的 book 在 line 风格下是空心方框。"""
    for i, w in enumerate((16, 16, 10)):
        yy = cy - 6 + i * 6
        c.rounded_rect((cx - 8, yy - 1, cx - 8 + w, yy + 1), radius=1, fill=color)


def _wrap(s: str, max_w: int, size: int) -> list[str]:
    """按像素宽度逐字折行（中文无空格分词，逐字累加最稳）。"""
    s = str(s)
    if max_w <= 0:
        return [s]
    lines: list[str] = []
    cur = ""
    for ch in s:
        if _w(cur + ch, size, "regular") <= max_w:
            cur += ch
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur or not lines:
        lines.append(cur)
    return lines


def _layout(groups: list, content_w: int) -> list[tuple[str, object, int]]:
    """排版块 [(kind, payload, height)] —— 测高与绘制共用同一份，保证卡底留白不歪。"""
    text_w = content_w - BULLET_INDENT
    blocks: list[tuple[str, object, int]] = []
    for g in groups:
        blocks.append(("group", str(g.get("name") or ""), FS_GROUP + 14))
        for row in _rows_of(g):
            name = str(row[0]) if len(row) > 0 else ""
            body = str(row[1]) if len(row) > 1 else ""
            label = (name + "：") if name else ""
            lines = _wrap(label + body, text_w, FS_BODY)
            blocks.append(("item", lines, LINE_H * len(lines) + ITEM_GAP))
        blocks.append(("gap", "", max(0, GROUP_GAP - ITEM_GAP)))
    return blocks


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)] or _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    blocks = _layout(groups, content_w)

    h = PAD + FS_TITLE + 8 + 1 + 18
    for _, _, bh in blocks:
        h += bh
    h += -4 + 1 + 12 + FS_BODY + PAD

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    title = str(data.get("title") or "修仙玩法")
    tw = _w(title, FS_TITLE, "light")
    ico_w = 16 + 12
    tx = x0 + (content_w - tw - ico_w) // 2
    _list_glyph(c, tx + 8, y + FS_TITLE // 2, p["title_color"])
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 分组 + 要点 ----
    for kind, payload, bh in blocks:
        if kind == "group":
            c.rounded_rect((x0 + 1, y + 3, x0 + 5, y + FS_GROUP - 3), radius=2,
                           fill=p["accent_color"])
            c.text((x0 + 14, y + FS_GROUP // 2), str(payload), size=FS_GROUP,
                   color=p["accent_color"], role="regular", anchor="lm")
        elif kind == "item":
            lines = payload
            for i, line in enumerate(lines):
                cy = y + i * LINE_H + LINE_H // 2
                if i == 0:
                    # 圆点对齐首行中线；折行部分不重复画点，只做悬挂缩进
                    c.rounded_rect((x0 + 3, cy - 3, x0 + 9, cy + 3), radius=3,
                                   fill=p["dim_color"])
                color = p["text_color"] if i == 0 else p["dim_color"]
                c.text((x0 + BULLET_INDENT, cy), line, size=FS_BODY,
                       color=color, role="regular", anchor="lm")
        y += bh

    # ---- 底部提示 ----
    y -= 4
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip is None:
        tip = "发「修仙菜单」看全部口令 · 数值改动以本条为准"
    if tip:
        icons.draw(c, "bolt", x0 + 8, y + FS_BODY // 2, 14, p["accent_color"], style=ICON_STYLE)
        c.text((x0 + 26, y + FS_BODY // 2), _fit(str(tip), content_w - 26, FS_BODY),
               size=FS_BODY, color=p["dim_color"], anchor="lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")

    return c
