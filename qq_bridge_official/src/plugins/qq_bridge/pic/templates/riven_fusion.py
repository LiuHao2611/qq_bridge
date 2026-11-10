# -*- coding: utf-8 -*-
"""riven_fusion —— 紫卡融合（Riven Fusion）卡（2026-11-05 P1）。

card-design 设计前分析：

* 用途：主信息 = 紫卡融合机制 + 融合费用表（真实公式）；辅以该武器当前
  真实 rank8 中位价作价值参考（WFM 无 fuse 字段，不编造融合溢价行情）。
* 层级：标题 28 bold > 核心数值 30 bold > 表头/行 17 > 弱口径 14 灰。
* 尺寸：固定宽 600；高度**内容驱动**（padding + Σ行高 + gap）。
* 主题：亮色（与 P2 紫卡卡一致），页面 #FDFCFD，卡片 #FFFFFF，边框 #D3DAE4。
* 语义色：费用/数值中性深字；无涨跌（不适用涨红跌绿）；强调色只用在数值。
* 列网格：L=48 / R=552 固定；表格列起点 48(融合次数) / 200(单次费用) /
  右锚定到 R(累计费用)；任何一行不因前文变长而改列起点。
* 间距 token：PAD=24、GAP_S=8、GAP_M=12、GAP_L=16；同层统一。
* 动态范围：武器名/数值长度可变 → 全部先 `Canvas.text_width` 度量，超宽 ellipsis；
  费用表固定 8 行。
* 无 emoji；口径文字按最大宽 wrap 后重算高度。

排版只有一个文本入口 ``emit``：先度量收集 ops，再一次性绘制并登记真实 bbox；
``build(boxes=...)`` 把包围盒交给 tools/check_riven_fusion_layout.py 自检。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme

W = 600
PAGE = "#FDFCFD"
CARD = "#FFFFFF"
BORDER = "#D3DAE4"
C_TITLE = "#6B3FA0"     # 紫卡主题：紫
C_TEXT = "#2B3A4A"
C_DIM = "#8A94A6"
C_ACCENT = "#6B3FA0"
C_LINE = "#E3E8EF"
C_VALUE = "#1F4E79"

PAD = 24
CARD_TOP = PAD
L = 48                                   # content_left（固定列网格起点）
R = W - L                                # content_right = 552
GAP_S, GAP_M, GAP_L = 8, 12, 16

FS_TITLE, FS_SUB = 28, 15
FS_BIG = 30
FS_HEAD, FS_ROW, FS_FOOT = 16, 17, 14
ROW_H = 30

#: 表格固定列起点（不随内容变化）
COL_FUSE = L                      # 融合次数（左对齐）
COL_INC_R = 340                  # 单次费用 右锚定边界
COL_CUM_R = R                    # 累计费用 右锚定边界（= 552）


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


def _num(v) -> str:
    try:
        return "{:,}".format(int(round(float(v))))
    except (TypeError, ValueError):
        return "—"


def build(t: Theme, data: dict | None = None, width: int | None = None,
          boxes=None, **kw) -> Canvas:
    """紫卡融合卡。data 来自 game_tools.riven_fusion.fusion_data()。"""
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
    title = "紫卡融合 · " + _ellipsis(data.get("weapon_zh") or "通用指南",
                                     FS_TITLE, "bold", 360)
    emit("title", L, y + FS_TITLE // 2, title, FS_TITLE, C_TITLE, "bold")
    y += FS_TITLE + GAP_S
    sub = data.get("weapon_en") or "Riven Fusion · 同名重复紫卡融合"
    emit("sub", L, y + FS_SUB // 2, sub, FS_SUB, C_DIM)
    y += FS_SUB + GAP_L
    rule("div.head", "hline", (L, y, R))

    y += GAP_L

    # ---- 真实中位价（价值参考，WFM price_asc rank8 非成交价）----
    median = data.get("median")
    if median is not None:
        emit("m.label", L, y + FS_BIG // 2, "当前中位价", 18, C_DIM)
        emit("m.big", L + 150, y + FS_BIG // 2,
             _price(median) + " P", FS_BIG, C_VALUE, "bold")
        nw = _w("rank8 · %s 条" % data.get("n", "—"), FS_ROW)
        emit("m.n", R - nw, y + FS_BIG // 2,
             "rank8 · %s 条" % data.get("n", "—"), FS_ROW, C_DIM)
        y += FS_BIG + GAP_S
        note = ("融合底子在此基础上有溢价；但 WFM 拍卖不暴露 fuse 字段，"
                "本卡不编造按融合等级的实时市价（见脚注）")
        for ln in Canvas.wrap(note, FS_FOOT, "regular", R - L):
            emit("m.note", L, y + FS_FOOT // 2, ln, FS_FOOT, C_DIM)
            y += FS_FOOT + GAP_S
    else:
        emit("m.none", L, y + FS_ROW // 2,
             "未指定武器 · 融合费用对所有紫卡一致（紫卡按 Rare 稀有度计费）",
             FS_ROW, C_DIM)
        y += FS_ROW + GAP_S
    y += GAP_M

    # ---- 融合费用表（核心）----
    emit("c.head", L, y + FS_HEAD // 2, "融合费用（每次 +1 融合 = +1 次重洗机会）",
         FS_HEAD, C_TITLE, "bold")
    y += FS_HEAD + GAP_S
    emit("th.fuse", COL_FUSE, y + FS_HEAD // 2, "融合次数", FS_HEAD, C_DIM, "bold")
    emit("th.inc", COL_INC_R - _w("单次费用", FS_HEAD, "bold"), y + FS_HEAD // 2,
         "单次费用", FS_HEAD, C_DIM, "bold")
    emit("th.cum", COL_CUM_R - _w("累计费用", FS_HEAD, "bold"), y + FS_HEAD // 2,
         "累计费用", FS_HEAD, C_DIM, "bold")
    y += FS_HEAD + GAP_S
    rule("div.table", "hline", (L, y, R))
    y += GAP_S
    for i, r in enumerate(data.get("cost_rows") or []):
        emit("cr%d.fuse" % i, COL_FUSE, y + ROW_H // 2, "第%s次" % r["fuse"],
             FS_ROW, C_TEXT)
        inc = "%sCr/%s内融" % (_num(r["inc_credit"]), _num(r["inc_endo"]))
        emit("cr%d.inc" % i, COL_INC_R - _w(inc, FS_ROW), y + ROW_H // 2, inc,
             FS_ROW, C_TEXT, "bold")
        cum = "%sCr/%s内融" % (_num(r["cum_credit"]), _num(r["cum_endo"]))
        emit("cr%d.cum" % i, COL_CUM_R - _w(cum, FS_ROW), y + ROW_H // 2, cum,
             FS_ROW, C_ACCENT, "bold")
        y += ROW_H + GAP_S

    # ---- 机制说明（wrap 后重算高度）----
    mech = ("机制：同名武器重复紫卡相互融合，每次 +1 融合(vein)，即 +1 次重洗(re-roll)机会；"
            "融合只增加可洗次数、不改词条，故高融合底子更保值。"
            "融合需消耗重复紫卡 + 信用点 + 内融核心，上限受手头重复紫卡数量与游戏内融合上限约束。")
    y += GAP_M
    rule("div.mech", "hline", (L, y, R))
    y += GAP_M
    for ln in Canvas.wrap(mech, FS_FOOT, "regular", R - L):
        emit("mech", L, y + FS_FOOT // 2, ln, FS_FOOT, C_DIM)
        y += FS_FOOT + GAP_S

    # ---- 口径脚注（wrap 后重算高度）----
    foot = str(data.get("source") or "")
    lines = Canvas.wrap(foot, FS_FOOT, "regular", R - L) if foot else []
    if lines:
        y += GAP_M
        rule("div.foot", "hline", (L, y, R))
        y += GAP_M
        for i, ln in enumerate(lines):
            emit("foot%d" % i, L, y + FS_FOOT // 2, ln, FS_FOOT, C_DIM)
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
    for label, x, y_top, text, size, color, role, anchor in ops:
        ww, hh = Canvas.text_size(text, size, role)
        c.text((x, y_top + hh // 2), text, size=size, color=color,
               role=role, anchor=anchor)
        if boxes is not None:
            boxes.append((label, x, y_top, x + ww, y_top + hh))
    return c
