# -*- coding: utf-8 -*-
"""pic/templates/hw.py —— 本地硬件占用图。

用**环形色块**（深浅色）直观表达每个硬件的占用率：
  * 环的填充长度 = 占用百分比；
  * 环的颜色 = 占用高低（绿=空闲 → 黄=中等 → 红=高占用），一眼可辨；
  * 环心显示百分比数字；右侧补充规格/读数（核数、显存、温度、功耗等）。

对比 `text` 模板的纯文字，本模板把「高占用」从一串数字变成「红圈」，
群聊里扫一眼就知道哪块吃紧（用户 2026-09-14 需求）。
"""
from __future__ import annotations

import colorsys
import time

from ..canvas import Canvas
from ..theme import Theme
from .. import decor, widgets as w

RING_R = 30
RING_T = 11
ROW_H = 84

# 占用率分档（决定颜色档位，给 footer 文案用）
GREEN_MAX = 50   # <=50% 绿（空闲）
YELLOW_MAX = 80  # 51~80% 黄（中等）


def _fmt_bytes(b) -> str:
    """字节 → 人类可读（GB/MB）。复制自业务层纯函数，保持 qq_bridge 与主项目隔离。"""
    if b is None:
        return "—"
    try:
        n = float(b)
    except (TypeError, ValueError):
        return "—"
    g = n / 1024 / 1024 / 1024
    if g >= 1:
        return "%.1f GB" % g
    return "%.1f MB" % (n / 1024 / 1024)


def _to_pct(v) -> int:
    """任意值 → 0~100 整数。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0
    if v != v:  # NaN
        return 0
    return max(0, min(100, int(round(v))))


def load_color(p: int) -> str:
    """占用率 → 颜色：绿(0%) → 黄(50%) → 红(100%)，连续色相。

    用 HSV 色相从 120°(绿) 线性降到 0°(红)，中高饱和度 + 高亮度，
    保证深浅一致、只靠色相区分高低，群里一眼可辨。
    """
    p = max(0, min(100, int(p)))
    hue = (120 - 120 * (p / 100.0)) / 360.0  # 0%→120°(绿), 100%→0°(红)
    r, g, b = colorsys.hsv_to_rgb(hue, 0.72, 0.95)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _collect(snap: dict) -> list[tuple[str, str, int]]:
    """快照 → [(label, sub, percent), ...]，顺序 CPU/内存/磁盘/GPU。"""
    items: list[tuple[str, str, int]] = []
    cpu = snap.get("cpu") or {}
    if cpu:
        items.append((
            "CPU",
            "%s 核 %s 线程" % (cpu.get("count_physical") or "—", cpu.get("count_logical") or "—"),
            _to_pct(cpu.get("percent")),
        ))
    mem = snap.get("mem") or {}
    if mem:
        items.append((
            "内存",
            "%s / %s" % (_fmt_bytes(mem.get("used")), _fmt_bytes(mem.get("total"))),
            _to_pct(mem.get("percent")),
        ))
    disk = snap.get("disk") or {}
    if disk:
        items.append((
            "磁盘 %s" % (disk.get("path") or ""),
            "%s / %s" % (_fmt_bytes(disk.get("used")), _fmt_bytes(disk.get("total"))),
            _to_pct(disk.get("percent")),
        ))
    gpus = snap.get("gpus") or []
    if gpus:
        for g in gpus:
            sub = "%s / %s · %s°C · %.1f W" % (
                _fmt_bytes(g.get("mem_used")),
                _fmt_bytes(g.get("mem_total")),
                g.get("temperature") or "—",
                g.get("power_draw") or 0,
            )
            items.append((
                "GPU%d %s" % (g.get("index", 0), g.get("name") or ""),
                sub,
                _to_pct(g.get("utilization")),
            ))
    elif cpu or mem or disk:
        items.append(("GPU", "未检测到（或没有 NVIDIA 显卡）", 0))
    return items


def build(t: Theme, snap: dict | None = None, width: int | None = None) -> Canvas:
    snap = snap or {}
    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2

    items = _collect(snap)
    if not items:
        # 极端空快照：给一行提示
        items = [("硬件", "主项目返回了空的硬件快照。", 0)]

    header_h = 56
    footer_h = 40
    h = pad * 2 + header_h + len(items) * ROW_H + footer_h
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="hw")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    ts = snap.get("ts")
    right = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
    y = w.header(c, t, pad_in, pad + 24, "本地硬件占用", right=right)

    cx = pad_in + RING_R + 8
    text_x = cx + RING_R + 14
    for idx, (label, sub, p) in enumerate(items):
        ry = y + idx * ROW_H
        cy = ry + RING_R + 6
        col = load_color(p)
        w.ring_progress(c, t, cx, cy, RING_R, RING_T, p,
                        color_start=col, color_end=col)
        # 环心百分比
        c.text((cx, cy), "%d%%" % p, size=t.fs(15), color=col, role="bold", anchor="mm")
        # 右侧文字
        c.text((text_x, cy - 14), label, size=t.fs(20), color=t.text, role="bold", anchor="lm")
        c.text((text_x, cy + 12), sub, size=t.fs(15), color=t.text_sub, anchor="lm")

    w.footer(c, t, pad_in, h - pad - 28,
             "环形颜色：绿=空闲 · 黄=中等 · 红=高占用")
    decor.corner(c, t)
    return c
