# -*- coding: utf-8 -*-
"""pic/templates/quota.py —— 今日配额图。

用**环形进度**（单一核心占比 → 环形比条形更醒目，2026 主流惯例），
中心放大数字「已用 / 总量」，右侧补充剩余与百分比。
"""
from __future__ import annotations

from ..canvas import Canvas, pct
from ..theme import Theme
from .. import decor, widgets as w

RING_R = 56
RING_T = 14


def build(
    t: Theme,
    used: int = 0,
    quota: int = 50,
    hint: str = "每天零点重置，省着点用。",
    user_label: str = "",
    width: int | None = None,
) -> Canvas:
    used = max(0, int(used))
    quota = max(0, int(quota))
    p = pct(used, quota) if quota > 0 else 0
    # 用量越高越"危险"：>=80% 转红色，>=60% 转黄色
    color_state = t.err if p >= 80 else (t.warn if p >= 60 else None)
    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2

    h = 272
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="quota")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    y = w.header(c, t, pad_in, 64, "今日额度", subtitle=user_label or None,
                 right="%s / %s" % (used, "不限" if quota <= 0 else quota))

    cy = y + 8 + RING_R
    w.ring_progress(
        c, t, pad_in + RING_R, cy, RING_R, RING_T, p,
        color_start=color_state or t.fill_start,
        color_end=t.fill_end if not color_state else color_state,
    )
    # 环心大数字
    c.text((pad_in + RING_R, cy), "%d%%" % p, size=t.fs(28), color=t.text, role="bold", anchor="mm")
    c.add_region("text",
                 [pad_in + RING_R - t.fs(28), cy - t.fs(28) // 2, pad_in + RING_R + t.fs(28), cy + t.fs(28) // 2],
                 label="环心数字", group="文字", tip="点击修改正文颜色")

    # 右侧两个读数
    tx = pad_in + RING_R * 2 + 40
    remain = "不限" if quota <= 0 else str(max(0, quota - used))
    w.stat_block(c, t, tx, cy - 26, 200, remain, "剩余次数",
                 color=color_state or t.accent)
    w.stat_block(c, t, tx + 200, cy - 26, 200, str(used), "今日已用")

    w.footer(c, t, pad_in, h - pad - 28, hint)
    decor.corner(c, t)
    return c
