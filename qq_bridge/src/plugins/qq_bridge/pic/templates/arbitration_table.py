# -*- coding: utf-8 -*-
"""pic/templates/arbitration_table.py —— 仲裁时间表专属卡片（对照 Warframe Rabbit「仲裁时间表」）。

版式（逐像素对齐 Rabbit 卡片）：
  * 居中标题「仲裁时间表」+ 贯通分割线（对齐 Rabbit 卡片头条）；
  * 引言行「仲裁逐时轮换班次如下：」（对齐 Rabbit「仲裁无底洞如下：」）；
  * 5 列表格：时间 / 任务 / 地图名 / 生息效率 / 评级；表头绿色粗体、列竖线 + 行间细线；
  * 评级列用**圆角胶囊徽章**（S=粉红底深红字 / A=蓝 / B=橙 / C·F=灰），与 Rabbit 色阶一致；
  * 末尾：生息效率脚注 + 绿色「PC」胶囊 + 来源小字；`page`/`hint` 为空时不画页码徽章（长图模式默认如此）。
列宽比例：时间 0.18 / 任务 0.30 / 地图名 0.22 / 生息效率 0.15 / 评级 0.15。
主题：随 PIC_THEME 热生效；卡片用主题 card/bg，深浅主题都可读（徽章用浅底深字恒可读）。
"""
from __future__ import annotations

import re

from .. import decor
from ..canvas import Canvas
from ..theme import Theme

PAD = 28
FS_TITLE = 34
FS_TBL = 24
ROW_H = FS_TBL + 34
HEAD_H = FS_TBL + 22
PAD_IN = PAD * 2
BUB_PAD_X = 14
FS_FOOT = 16
BADGE_FS = FS_TBL - 2
BADGE_PAD_X = 12

#: 评级 → 胶囊配色（浅底 + 深字，深浅主题恒可读；色阶对齐 Rabbit：S粉红/A蓝/B橙/C·F灰）
_TIER_STYLE = {
    "S": ("#F6C9D4", "#C0392B"), "S+": ("#F6C9D4", "#C0392B"),
    "A": ("#D6E4F5", "#2C5BA8"), "A+": ("#D6E4F5", "#2C5BA8"),
    "B": ("#FBE3C8", "#C9760E"), "B+": ("#FBE3C8", "#C9760E"),
    "C": ("#E4E7EC", "#6B7280"), "C+": ("#E4E7EC", "#6B7280"),
    "F": ("#E4E7EC", "#6B7280"),
}
_BEIGE_FILL, _BEIGE_TEXT = "#D6EFDD", "#176B3A"   # 绿色胶囊（页码 / PC 徽章，对齐 Rabbit；深绿字保证对比度）


def _tier_style(tier: str) -> tuple[str, str]:
    return _TIER_STYLE.get((tier or "").strip(), ("#E4E7EC", "#6B7280"))


def _pill_width(text: str, fs: int = BADGE_FS) -> int:
    return Canvas.text_width(text, fs, "bold") + 2 * BADGE_PAD_X


def _fit(text: str, fs: int, max_w: int) -> str:
    """文字超宽时按像素宽度截断并加省略号，杜绝列内横向溢出/叠字。"""
    if max_w <= 0 or Canvas.text_width(text, fs) <= max_w:
        return text
    ell = "…"
    while text and Canvas.text_width(text + ell, fs) > max_w:
        text = text[:-1]
    return text + ell


def _draw_pill(c: Canvas, cx: int, cy: int, text: str, fill: str, text_color: str,
               fs: int = BADGE_FS) -> int:
    """画一个居中的圆角胶囊徽章，返回胶囊宽度。"""
    pw = _pill_width(text, fs)
    ph = fs + 12
    x0 = cx - pw // 2
    y0 = cy - ph // 2
    c.rounded_rect((x0, y0, x0 + pw, y0 + ph), radius=ph // 2,
                   fill=fill, outline=text_color, width=2)
    c.text((cx, cy), text, size=fs, color=text_color, role="bold", anchor="mm")
    return pw


def _ratios() -> list[float]:
    return [0.18, 0.30, 0.22, 0.15, 0.15]


def build(t: Theme, title: str = "仲裁时间表", intro: str = "仲裁逐时轮换班次如下：",
          rows: list | None = None, footer: str = "", page: str = "", hint: str = "",
          source: str = "数据源 api.warframe.com + WFCD solNodes · 善用右键/长按提取文字", width: int | None = None,
          **kw) -> Canvas:
    rows = rows or []
    width = int(width or getattr(t, "width", 800) or 800)
    content_w = width - PAD_IN
    x0 = PAD

    headers = ["时间", "任务", "地图名", "生息效率", "评级"]
    ratios = _ratios()
    col_w = [content_w * r for r in ratios]
    col_x = [x0]
    for w in col_w:
        col_x.append(col_x[-1] + w)

    # ---- 高度预估 ----
    y = PAD
    y += FS_TITLE + 14 + 14                       # 标题 + 分割线区
    if intro:
        y += FS_FOOT + 10
    y += HEAD_H                                   # 表头
    y += ROW_H * len(rows)
    y += 14
    foot_lines = _wrap_foot(footer, content_w) if footer else []
    if foot_lines:
        y += len(foot_lines) * (FS_FOOT + 4)
    # 页码胶囊 + 用法提示 一行
    if page or hint:
        y += (BADGE_FS + 12) + 8
    # PC 胶囊 + 来源 一行
    if source:
        y += (BADGE_FS + 12) + 8
    y += PAD + FOOT_H_OFFSET()
    h = y

    c = Canvas(width, h, t.bg, theme=t)
    # 卡片壳
    c.rounded_rect((14, 14, width - 14, h - 14), radius=24, fill=t.card, outline=t.divider, width=2)

    y = PAD
    # ---- 标题居中 + 分割线 ----
    tw = Canvas.text_width(title, FS_TITLE, "bold")
    tx = (width - tw) // 2
    c.text((tx, y + FS_TITLE // 2), title, size=FS_TITLE, color=t.accent, role="bold", anchor="lm")
    y += FS_TITLE + 14
    c.line((x0, y, x0 + content_w, y), fill=t.divider, width=2)
    y += 14

    # ---- 引言行 ----
    if intro:
        c.text((x0, y + FS_FOOT // 2), intro, size=FS_FOOT, color=t.text_sub, anchor="lm")
        y += FS_FOOT + 10

    # ---- 表头 ----
    hy0 = y
    c.rounded_rect((x0, hy0, x0 + content_w, hy0 + HEAD_H), radius=10, fill=t.accent, fill_alpha=22 * 255 // 100)
    cy = hy0 + HEAD_H // 2
    for ci, hd in enumerate(headers):
        if ci == 4:   # 评级居中
            cx = (col_x[ci] + col_x[ci + 1]) // 2
            c.text((cx, cy), hd, size=FS_TBL, color=t.accent, role="bold", anchor="mm")
        else:
            c.text((col_x[ci] + BUB_PAD_X, cy), hd, size=FS_TBL, color=t.accent, role="bold", anchor="lm")
    y = hy0 + HEAD_H

    # ---- 数据行 ----
    for ri, r in enumerate(rows):
        ry0 = y
        if ri > 0:
            c.line((x0 + 6, ry0, x0 + content_w - 6, ry0), fill=t.divider, width=1)
        ry = ry0 + ROW_H // 2
        cells = [str(r.get("time", "")), str(r.get("task", "")),
                 str(r.get("node", "")), str(r.get("eff", "")), str(r.get("tier", ""))]
        # 任务列 / 地图名列按列宽截断，防止超长文本横向溢出到相邻列
        cells[1] = _fit(cells[1], FS_TBL, col_w[1] - 2 * BUB_PAD_X)
        cells[2] = _fit(cells[2], FS_TBL, col_w[2] - 2 * BUB_PAD_X)
        for ci, cell in enumerate(cells):
            if ci == 3:   # 生息效率 右对齐到该列右侧
                c.text((col_x[ci + 1] - BUB_PAD_X, ry), cell, size=FS_TBL, color=t.text, anchor="rm")
            elif ci == 4:  # 评级 居中胶囊徽章
                cx = (col_x[ci] + col_x[ci + 1]) // 2
                fill, tcol = _tier_style(r.get("tier", ""))
                _draw_pill(c, cx, ry, cell, fill, tcol)
            else:
                c.text((col_x[ci] + BUB_PAD_X, ry), cell, size=FS_TBL, color=t.text, anchor="lm")
        y = ry0 + ROW_H

    y += 14
    # ---- 脚注（实测均值提示）----
    for ln in foot_lines:
        c.text((x0, y + FS_FOOT // 2), ln, size=FS_FOOT, color=t.text_sub, anchor="lm")
        y += FS_FOOT + 4
    # ---- 页码胶囊 + 用法提示 ----
    if page or hint:
        py = y + (BADGE_FS + 12) // 2
        px = x0
        if page:
            m = re.search(r"(\d+)\s*/\s*(\d+)", page)
            pill_txt = ("页码：%s/%s" % (m.group(1), m.group(2))) if m else page
            pw = _draw_pill(c, px + _pill_width(pill_txt) // 2, py, pill_txt,
                            _BEIGE_FILL, _BEIGE_TEXT)
            px += pw + 10
        if hint:
            c.text((px, py), hint, size=FS_FOOT, color=t.text_sub, anchor="lm")
        y += (BADGE_FS + 12) + 8
    # ---- PC 胶囊 + 来源 ----
    if source:
        py = y + (BADGE_FS + 12) // 2
        pw = _draw_pill(c, x0 + _pill_width("PC") // 2, py, "PC", _BEIGE_FILL, _BEIGE_TEXT)
        c.text((x0 + pw + 10, py), source, size=FS_FOOT, color=t.text_sub, anchor="lm")
        y += (BADGE_FS + 12) + 8

    decor.corner(c, t)
    return c


def FOOT_H_OFFSET() -> int:
    return 10


def _wrap_foot(text: str, content_w: int, fs: int = FS_FOOT) -> list[str]:
    """脚注按实际像素宽度折行（避免中文按固定字数误断行留孤字）。"""
    out: list[str] = []
    line = ""
    for ch in (text or ""):
        if line and Canvas.text_width(line + ch, fs) > content_w:
            out.append(line)
            line = ch
        else:
            line += ch
    if line:
        out.append(line)
    return out or [""]
