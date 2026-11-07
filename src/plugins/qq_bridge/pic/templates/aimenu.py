# -*- coding: utf-8 -*-
"""pic/templates/aimenu.py —— 指令总览双列加长图（2026-10-29）。

把全部命令族排进**一张**两列加长卡片：分组标题（整行）+ 每组内两列网格。
每个格子 = 图标 + 口令(粗，最多 2 行折行) + 一句话说明(单行，超宽省略)。
布局随主题（accent/text/accent_soft/圆角/底），与 menu 模板观感一致。

适用：指令极多、又不想发一堆碎图时，用一张图塞下全部口令，手机端竖屏可整屏看。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme
from .. import decor, widgets as w
from .. import icons as _icons  # noqa: F401（_cell 内按需调用）


def _wrap(text: str, size: int, role: str, max_w: int, max_lines: int = 2) -> list:
    """按像素宽度把中文/混排文本折行，最多 max_lines 行；最后一行超宽则截断加「…」。"""
    lines: list = []
    cur = ""
    for ch in (text or ""):
        if cur and Canvas.text_width(cur + ch, size, role) > max_w:
            lines.append(cur)
            cur = ch
            if len(lines) >= max_lines:
                break
        else:
            cur += ch
    if cur and len(lines) < max_lines:
        lines.append(cur)
    # 最后一行兜底截断（防超宽）
    if lines:
        last = lines[-1]
        while Canvas.text_width(last, size, role) > max_w and len(last) > 1:
            last = last[:-1]
        if last != lines[-1]:
            lines[-1] = last + "…"
    return lines


def _cell(c: Canvas, t: Theme, x: int, y: int, col_w: int,
          icon: str, cmd: str, desc: str,
          ICON_G: int, CELL_H: int, FS_CMD: int, FS_DESC: int) -> None:
    """单个指令格子（图标 + 口令 + 说明）。

    图标：SpaceX 式 monoline（统一线宽/圆角连接/端点圆头/单色极简，见 pic/icons.py line 分支），
    **直接落在卡片上、不上色块徽章**——避免浅色徽章把主色冲淡、降低对比（浅色主题下尤其明显），
    也让线性图标更纯粹、工程感更强。
    """
    cy = y + CELL_H // 2
    try:
        _icons.draw(c, icon, x + ICON_G // 2, cy, ICON_G, t.accent, style="line")
    except Exception:  # noqa: BLE001 — 图标名非法时静默跳过，不崩整图
        pass
    tx = x + ICON_G + 14
    tw = col_w - ICON_G - 14
    # 口令（粗，最多 2 行）
    cmd_lines = _wrap(cmd, t.fs(FS_CMD), "bold", tw, 2)
    yy = y + 14
    for ln in cmd_lines:
        c.text((tx, yy), ln, size=t.fs(FS_CMD), color=t.text, role="bold", anchor="lm")
        yy += 22
    # 说明（单行，超宽省略）
    dd = Canvas.ellipsis(desc, t.fs(FS_DESC), "regular", tw)
    c.text((tx, yy + 2), dd, size=t.fs(FS_DESC), color=t.text_sub, anchor="lm")


def build(t: Theme, groups=None, title: str = "指令菜单", subtitle: str = "",
          footer: str = "", owner: bool = False, width: int | None = None) -> Canvas:
    """渲染双列加长指令菜单图。

    :param groups: 分组列表，每项为 {"name": 分组名, "items": [(icon, 口令, 说明), ...]}。
    """
    if groups is None:
        groups = []
    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2
    inner_w = width - pad_in * 2
    gap = 20
    col_w = (inner_w - gap) // 2
    col_x = [pad_in, pad_in + col_w + gap]

    ICON_G = 30  # monoline 图标本体尺寸（直接落卡、不套徽章）
    CELL_H = 84
    FS_CMD = 17
    FS_DESC = 14

    def rowcount(n: int) -> int:
        return (n + 1) // 2

    header_block = 100
    footer_block = 40
    body_h = sum(28 + rowcount(len(g["items"])) * CELL_H for g in groups)
    h = pad + header_block + body_h + footer_block + pad

    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="menu")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    n_total = sum(len(g["items"]) for g in groups)
    right = ("%d 项" % n_total) + (" · 主人" if owner else "")
    y = w.header(c, t, pad_in, pad + 24, title, subtitle=subtitle, right=right)
    y += 8

    for g in groups:
        y = w.section_label(c, t, pad_in, y + 6, g["name"])
        items = g["items"]
        i = 0
        while i < len(items):
            for _col in range(2):
                if i >= len(items):
                    break
                icon, cmd, desc = items[i]
                _cell(c, t, col_x[_col], y, col_w, icon, cmd, desc,
                      ICON_G, CELL_H, FS_CMD, FS_DESC)
                i += 1
            y += CELL_H
        y += 4

    w.footer(c, t, pad_in, h - pad - 20, footer)
    decor.corner(c, t)
    return c
