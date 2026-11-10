# -*- coding: utf-8 -*-
"""pic/templates/progress.py —— 任务进度图（**按需绘制**，用户问 /status 时才画）。

用**线性进度条**（多条/有起终点/需要读数 → 线性优于环形，2026 主流惯例）。

进度口径（诚实原则）：本地 ComfyUI 拿不到真实百分比，用「已耗时 / 看门狗超时」
估算，并在图上标注为「已等待」而非「已完成 x%」，避免误导用户。
"""
from __future__ import annotations

from ..canvas import Canvas, human_duration, pct
from ..theme import Theme
from .. import decor, widgets as w

KIND_LABEL = {"t2i": "生图", "i2i": "改图", "video": "视频"}
STATE_COLOR = {"ok": "accent", "warn": "warn", "err": "err"}


def build(
    t: Theme,
    percent: int = 0,
    elapsed: float = 0.0,
    kind: str = "t2i",
    prompt: str = "",
    state: str = "ok",
    eta: float | None = None,
    trace_id: str = "",
    width: int | None = None,
) -> Canvas:
    p = max(0, min(100, int(percent)))
    kind_zh = KIND_LABEL.get(kind, "任务")
    color = getattr(t, STATE_COLOR.get(state, "accent"))
    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2
    inner_w = width - pad_in * 2

    # 高度必须装下：header(96) + bar(18+32) + 2×kv(2×44) + 8 + 提示词标题(24) + 2 行 wrap(2×26) + footer(28) + 上下边距
    h = 360
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="progress")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    y = w.header(c, t, pad_in, 64, "%s进行中" % kind_zh, right="%d%%" % p)
    bar_h = w.progress_bar(c, t, pad_in, y, inner_w, p)
    y = bar_h + 32

    # 关键读数：已等待（主色强调） + 可选预计剩余
    y = w.kv_row(c, t, pad_in, y, inner_w, "已等待", human_duration(elapsed), value_color=color)
    if eta is not None:
        y = w.kv_row(c, t, pad_in, y, inner_w, "预计还需", human_duration(eta))

    if prompt:
        y += 8
        c.text((pad_in, y), "提示词", size=t.fs(w.FS_DESC), color=t.text_weak, anchor="lm")
        y += 24
        for line in Canvas.wrap(prompt, t.fs(w.FS_BODY), "regular", inner_w)[:2]:
            c.text((pad_in, y), Canvas.ellipsis(line, t.fs(w.FS_BODY), "regular", inner_w),
                   size=t.fs(w.FS_BODY), color=t.text_sub, anchor="lm")
            y += 26

    foot = "画完本喵会叫你，别催。" if state == "ok" else "这单有点悬，盯紧点。"
    if trace_id:
        foot += "（trace=%s）" % trace_id[:8]
    w.footer(c, t, pad_in, h - pad - 28, foot)
    decor.corner(c, t)
    return c


def from_elapsed(elapsed: float, timeout: float) -> int:
    """按「已耗时 / 看门狗超时」估算百分比（拿不到真实进度时的诚实估算）。"""
    return pct(elapsed, timeout)
