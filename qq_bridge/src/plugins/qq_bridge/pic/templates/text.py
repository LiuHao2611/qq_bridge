# -*- coding: utf-8 -*-
"""pic/templates/text.py —— 短文本卡片。

适用：机器人的所有「短确认/短反馈」（主题切换、模式切换、ping、问候等），
让任何 @机器人 回复都尽量带图片，体验一致。

设计要点：
  * 标题可选（如「主题已切换」），body 自适应行高（Canvas.wrap 自动换行）；
  * 左侧主色小色条（accent）暗示「这是机器人的回复」，与生图图/进度图视觉协调；
  * 不超过 3 行体量时画一个淡彩小图标，超过则不画（避免与长文本抢眼）。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme
from .. import decor, icons, widgets as w

# 短回复图标（按文本长度/特征挑；默认 wand 与 机器人/人设契合）
DEFAULT_ICON = "wand"


def build(
    t: Theme,
    body: str = "",
    title: str | None = None,
    icon: str | None = DEFAULT_ICON,
    width: int | None = None,
    source: str | None = None,
) -> Canvas:
    body = str(body or "").strip()
    # 宽度优先取主题的 t.width（编辑器可调），调用方显式传入时以传入为准
    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2
    inner = width - pad_in * 2  # 卡片内左右各 pad_in
    line_h = t.fs(28)
    font_size = t.fs(20)
    lines = Canvas.wrap(body, font_size, "regular", inner) if body else ["（无内容）"]
    # 限高避免误用：超过 12 行只取前 12 行 + 省略号
    if len(lines) > 12:
        lines = lines[:12]
        if Canvas.text_width(lines[-1] + "…", font_size, "regular") <= inner:
            lines[-1] += "…"
        else:
            while lines[-1] and Canvas.text_width(lines[-1] + "…", font_size, "regular") > inner:
                lines[-1] = lines[-1][:-1]
            lines[-1] = lines[-1] + "…"

    title_h = 40 if title else 0
    # 高度：上下边距 pad×2 + 标题区 40 + 行数×line_h + 短回复图标小尾巴 + 来源小字
    h = pad * 2 + title_h + max(1, len(lines)) * line_h + 16 + 24 + (26 if source else 0)
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="text")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    y = pad + 28  # 顶部内边距
    # 左侧主色色条
    c.rounded_rect((pad, y - 12, pad + 6, h - pad - 12), radius=3, fill=t.accent)
    c.add_region("accent", [pad, y - 12, pad + 6, h - pad - 12],
                 label="左侧强调条", group="主色", tip="点击修改主色")

    # 可选图标（短文时画）
    icon_x = pad + 32
    if icon and len(lines) <= 4:
        isize = t.fs(28)
        icons.draw(c, icon, icon_x, y, isize, t.accent)
        c.add_region("accent",
                     [icon_x - isize // 2, y - isize // 2, icon_x + isize // 2, y + isize // 2],
                     shape="circle", label="短文图标", group="主色", tip="点击修改主色")
        icon_x += 44

    title_color = t.text_sub
    if title:
        tw = Canvas.text_width(title, t.fs(18), "bold")
        c.text((icon_x, y), title, size=t.fs(18), color=title_color, role="bold", anchor="lm")
        c.add_region("text_sub", [icon_x, y - t.fs(18) // 2, icon_x + tw, y + t.fs(18) // 2],
                     label="卡片标题", group="文字", tip="点击修改标题/次级文字颜色")
        y += 28

    if body:
        y += 8
        for line in lines:
            lw = Canvas.text_width(line, font_size, "regular")
            c.text((icon_x, y), line, size=font_size, color=t.text, anchor="lm")
            c.add_region("text", [icon_x, y - font_size // 2, icon_x + lw, y + font_size // 2],
                         label="正文", group="文字", tip="点击修改正文颜色")
            y += line_h

    # 右下角来源小字（信息获取平台 + 时间戳）
    if source:
        c.text((width - pad_in, h - pad - 4), source, size=t.fs(12), color=t.text_sub, anchor="rm")

    decor.corner(c, t)
    return c