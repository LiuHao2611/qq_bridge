# -*- coding: utf-8 -*-
"""riven_trend —— 紫卡趋势 / 词条价差卡（2026-11-05 P2）。

card-design 设计前分析：

* 用途：趋势卡主信息 = 当前挂牌中位价与较首点涨跌；价差卡主信息 =
  某正面词条相对基准池的同分位溢价排行。
* 层级：标题 28 bold > 核心数值 30 bold > 表头/行 17 > 弱口径 14 灰。
* 尺寸：固定宽 600；高度**内容驱动**（padding + Σ行高 + gap）。
* 主题：亮色（QQ 阅读友好），页面 #FDFCFD，卡片 #FFFFFF，边框 #D3DAE4。
* 语义色（中国习惯）：涨/正溢价 **红 #C0392B**，跌/负溢价 **绿 #1E8449**，
  零变化灰；强调色只用在数值，不铺大面积。
* 列网格：L=48 / R=552 固定；表格列起点 48(名称) / 300(中位价) /
  410(溢价或样本) / 右锚定到 R；任何一行不因前文变长而改列起点。
* 间距 token：PAD=24、GAP_S=8、GAP_M=12、GAP_L=16、GAP_XL=24；同层统一。
* 动态范围：武器名/词条名/数值长度可变 → 全部先 `Canvas.text_width` 度量，
  超宽 ellipsis 截断；点数可变 → 折线按点数均分 x，表格最多 6 行。
* 无 emoji；口径文字按最大宽 wrap 后重算高度。

排版只有一个文本入口 ``emit``：先度量收集 ops，再一次性绘制并登记真实 bbox；
``build(boxes=...)`` 把包围盒交给 tools/check_riven_trend_layout.py 自检。
"""
from __future__ import annotations

import time

from ..canvas import Canvas
from ..theme import Theme

W = 600
PAGE = "#FDFCFD"
CARD = "#FFFFFF"
BORDER = "#D3DAE4"
C_TITLE = "#1F4E79"
C_TEXT = "#2B3A4A"
C_DIM = "#8A94A6"
C_UP = "#C0392B"      # 涨 / 正溢价（中国习惯：红）
C_DOWN = "#1E8449"    # 跌 / 负溢价（绿）
C_FLAT = "#8A94A6"
C_LINE = "#E3E8EF"
C_CHART = "#1F6F9B"

PAD = 24
CARD_TOP = PAD
L = 48                                   # content_left（固定列网格起点）
R = W - L                             # content_right = 552
GAP_S, GAP_M, GAP_L, GAP_XL = 8, 12, 16, 24

FS_TITLE, FS_SUB = 28, 15
FS_BIG, FS_CHANGE = 30, 18
FS_HEAD, FS_ROW, FS_FOOT = 16, 17, 14
ROW_H = 30
CHART_H = 96
MAX_TABLE_ROWS = 6

#: 表格固定列起点（不随内容变化）
COL_NAME, COL_MED, COL_EXTRA = L, 300, 410


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _ellipsis(s: str, size: int, role: str, max_w: int) -> str:
    s = str(s or "")
    if _w(s, size, role) <= max_w:
        return s
    while s and _w(s + "…", size, role) > max_w:
        s = s[:-1]
    return s + "…" if s else ""


def _price(v) -> str:
    try:
        return "%g" % float(v)
    except (TypeError, ValueError):
        return "—"


def _pct(v) -> str:
    try:
        return "%+.1f%%" % float(v)
    except (TypeError, ValueError):
        return "—"


def _delta_color(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return C_FLAT
    return C_UP if f > 0.005 else (C_DOWN if f < -0.005 else C_FLAT)


def _ts(ts) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "—"


def build(t: Theme, data: dict | None = None, mode: str = "trend",
          width: int | None = None, boxes=None, **kw) -> Canvas:
    """mode='trend' 画趋势卡；mode='spread' 画词条价差卡。"""
    data = data or {}
    ops: list[tuple] = []      # (label, x, y_top, text, size, color, role, anchor)
    rules: list[tuple] = []    # (label, kind, args)

    def emit(label, x, y_cy, text, size, color, role="regular", anchor="lm"):
        """唯一文本入口：度量 + 收集，返回 (宽, 高)。"""
        w, h = Canvas.text_size(text, size, role)
        ops.append((label, int(x), int(y_cy - h // 2), text, size, color, role, anchor))
        return w, h

    def rule(label, kind, args):
        rules.append((label, kind, args))
        if boxes is not None and kind == "hline":
            x0, y, x1 = args[0], args[1], args[2]
            boxes.append((label, x0, y, x1, y + 1))

    y = CARD_TOP + 24
    title = ("紫卡趋势" if mode == "trend" else "紫卡词条价差") + " · " + \
        _ellipsis(data.get("weapon_zh") or "—", FS_TITLE, "bold", 340)
    _, h = emit("title", L, y + FS_TITLE // 2, title, FS_TITLE, C_TITLE, "bold")
    y += FS_TITLE + GAP_S
    sub = data.get("weapon_en") or ""
    _, h = emit("sub", L, y + FS_SUB // 2, sub, FS_SUB, C_DIM)
    y += FS_SUB + GAP_L
    rule("div.head", "hline", (L, y, R))

    y += GAP_L

    if mode == "trend":
        cur = data.get("current") or {}
        chg = data.get("change_pct") or 0.0
        # 核心数值行：标签 + 大数字 + 涨跌（右锚定，不随数字长度漂移）
        _, bh = emit("k.label", L, y + FS_BIG // 2, "当前中位价", 18, C_DIM)
        big = _price(cur.get("median")) + " P"
        emit("k.big", L + 150, y + FS_BIG // 2, big, FS_BIG, C_TEXT, "bold")
        chg_txt = "较首点 " + (_pct(chg) if data.get("enough_for_trend") else "—")
        cw = _w(chg_txt, FS_CHANGE, "bold")
        emit("k.change", R - cw, y + FS_BIG // 2, chg_txt, FS_CHANGE,
             _delta_color(chg) if data.get("enough_for_trend") else C_FLAT, "bold")
        y += FS_BIG + GAP_M
        info = "本次挂牌 %s – %s P　同等级 rank8 有效挂牌 %s 条" % (
            _price(cur.get("lo")), _price(cur.get("hi")), cur.get("n", "—"))
        _, h = emit("k.range", L, y + FS_ROW // 2, info, FS_ROW, C_TEXT)
        y += FS_ROW + GAP_L

        pts = list(data.get("points") or [])
        # ---- 折线图（≥2 点才画；1 点如实显示「采样起点」，不画假曲线）----
        chart_y0 = y
        chart_y1 = y + CHART_H
        if len(pts) >= 2:
            vals = [float(p["median"]) for p in pts]
            vmin, vmax = min(vals), max(vals)
            span = (vmax - vmin) or 1.0
            xs = [L + 12 + (R - L - 24) * i / (len(pts) - 1) for i in range(len(pts))]
            ys = [chart_y1 - 14 - (v - vmin) / span * (CHART_H - 28) for v in vals]
            for i in range(len(pts) - 1):
                rule("chart.seg%d" % i, "line",
                     ((int(xs[i]), int(ys[i])), (int(xs[i + 1]), int(ys[i + 1])), C_CHART, 2))
            for i in range(len(pts)):
                rule("chart.dot%d" % i, "dot", (int(xs[i]), int(ys[i]), 4, C_CHART))
            # 刻度只放一行在图表顶部，避免底部数字挤到下方表格表头
            scale = "历史中位价 %s – %s P" % (_price(vmin), _price(vmax))
            emit("chart.scale", L, chart_y0 + FS_FOOT // 2, scale, FS_FOOT, C_DIM)
        else:
            _msg = str(data.get("error") or
                       "第 1 个采样点 · 后台每 2 小时采样，趋势需时间积累")
            emit("chart.empty", L, (chart_y0 + chart_y1) // 2,
                 _ellipsis(_msg, FS_ROW, "regular", R - L), FS_ROW,
                 C_UP if data.get("error") else C_DIM)
        y = chart_y1 + GAP_M

        # ---- 采样点表（固定列网格）----
        _, hh = emit("th.time", L, y + FS_HEAD // 2, "采样时间", FS_HEAD, C_DIM, "bold")
        _, hh = emit("th.med", COL_MED, y + FS_HEAD // 2, "中位价", FS_HEAD, C_DIM, "bold")
        tw = _w("样本", FS_HEAD, "bold")
        emit("th.n", R - tw, y + FS_HEAD // 2, "样本", FS_HEAD, C_DIM, "bold")
        y += FS_HEAD + GAP_S
        rule("div.table", "hline", (L, y, R))
        y += GAP_S
        for i, p in enumerate(pts[-MAX_TABLE_ROWS:]):
            emit("tr%d.time" % i, L, y + ROW_H // 2, _ts(p.get("ts")), FS_ROW, C_TEXT)
            med = _price(p.get("median")) + " P"
            emit("tr%d.med" % i, COL_MED, y + ROW_H // 2, med, FS_ROW, C_TEXT, "bold")
            nt = str(p.get("n") or "—")
            nw = _w(nt, FS_ROW)
            emit("tr%d.n" % i, R - nw, y + ROW_H // 2, nt, FS_ROW, C_DIM)
            y += ROW_H + GAP_S

    else:  # spread
        base = _price(data.get("base_median")) + " P"
        _, hh = emit("s.label", L, y + FS_BIG // 2, "基准中位价", 18, C_DIM)
        emit("s.base", L + 150, y + FS_BIG // 2, base, FS_BIG, C_TEXT, "bold")
        bw = _w("rank8 · %s 条" % data.get("base_n", "—"), FS_ROW)
        emit("s.n", R - bw, y + FS_BIG // 2,
             "rank8 · %s 条" % data.get("base_n", "—"), FS_ROW, C_DIM)
        y += FS_BIG + GAP_L

        _, hh = emit("th.attr", COL_NAME, y + FS_HEAD // 2, "正面词条", FS_HEAD, C_DIM, "bold")
        emit("th.med", COL_MED, y + FS_HEAD // 2, "词条池中位价", FS_HEAD, C_DIM, "bold")
        pw = _w("同分位溢价", FS_HEAD, "bold")
        emit("th.pct", R - pw, y + FS_HEAD // 2, "同分位溢价", FS_HEAD, C_DIM, "bold")
        y += FS_HEAD + GAP_S
        rule("div.table", "hline", (L, y, R))
        y += GAP_S
        rows = list(data.get("rows") or [])[:MAX_TABLE_ROWS]
        if not rows:
            _msg = str(data.get("error") or
                       "当前挂单没有足够样本的同分位词条池，拒绝估算价差")
            emit("s.empty", L, y + ROW_H // 2,
                 _ellipsis(_msg, FS_ROW, "regular", R - L), FS_ROW,
                 C_UP if data.get("error") else C_DIM)
            y += ROW_H + GAP_S
        for i, r in enumerate(rows):
            emit("sr%d.name" % i, COL_NAME, y + ROW_H // 2,
                 _ellipsis(r.get("zh") or r.get("attr") or "—", FS_ROW, "regular",
                           COL_MED - COL_NAME - GAP_M), FS_ROW, C_TEXT)
            emit("sr%d.med" % i, COL_MED, y + ROW_H // 2,
                 _price(r.get("median")) + " P", FS_ROW, C_TEXT, "bold")
            ptxt = _pct(r.get("pct"))
            pwtxt = _w(ptxt, FS_ROW, "bold")
            emit("sr%d.pct" % i, R - pwtxt, y + ROW_H // 2, ptxt, FS_ROW,
                 _delta_color(r.get("pct")), "bold")
            y += ROW_H + GAP_S

    # ---- 口径脚注（wrap 后重算高度）----
    foot = str(data.get("source") or "")
    lines = Canvas.wrap(foot, FS_FOOT, "regular", R - L) if foot else []
    if lines:
        y += GAP_M
        rule("div.foot", "hline", (L, y, R))
        y += GAP_M
        for i, ln in enumerate(lines):
            _, hh = emit("foot%d" % i, L, y + FS_FOOT // 2, ln, FS_FOOT, C_DIM)
            y += FS_FOOT + GAP_S
    y += PAD
    h = y + PAD

    # ---------------- 一次性绘制 ----------------
    c = Canvas(W, h, PAGE, theme=t)
    c.rounded_rect((PAD, CARD_TOP, W - PAD, h - PAD), radius=16,
                   fill=CARD, outline=BORDER, width=2)
    for label, kind, args in rules:
        if kind == "hline":
            x0, yy, x1 = args
            c.line((x0, yy, x1, yy), fill=C_LINE, width=1)
        elif kind == "line":
            (x0, y0), (x1, y1), color, wd = args
            c.line((x0, y0, x1, y1), fill=color, width=wd)
        elif kind == "dot":
            cx, cy, r, color = args
            c.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    for label, x, y_top, text, size, color, role, anchor in ops:
        ww, hh = Canvas.text_size(text, size, role)
        c.text((x, y_top + hh // 2), text, size=size, color=color,
               role=role, anchor=anchor)
        if boxes is not None:
            boxes.append((label, x, y_top, x + ww, y_top + hh))

    # 图形元素登记（折线段/点），供 check_boxes 查溢出
    if boxes is not None:
        for label, kind, args in rules:
            if kind == "line":
                (x0, y0), (x1, y1), _, _ = args
                boxes.append((label, min(x0, x1), min(y0, y1),
                              max(x0, x1), max(y0, y1)))
            elif kind == "dot":
                cx, cy, r, _ = args
                boxes.append((label, cx - r, cy - r, cx + r, cy + r))
    return c
