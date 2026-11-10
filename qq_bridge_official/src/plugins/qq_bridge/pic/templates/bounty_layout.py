# -*- coding: utf-8 -*-
"""bounty_layout.py —— 赏金任务一览卡：两列 4 行网格 + 内容驱动高度。

参考图结构（用户 2026-11-08，只抄内容不抄背景）：
    居中标题「赏金任务一览」+ 贯通分割线
    两列 × 4 行独立圆角卡，行高 = max(左卡, 右卡)，同列起点固定
    卡内：蓝色标题 / 橙色轮换标题 / 两列奖励物品 / 绿色「赏金N」徽章 /
          Lv 徽章 / 蓝色任务类型 / 目标行 / 右下「...」
    底部：剩余时间进度条 / 脚注 / 平台 + 来源

@skill:card-design 落地方式：
  * 单一排版入口 bounty_atoms.draw_tokens（多色混排显式 gap）；
  * 固定列网格 Columns，行尾元素右锚定；
  * 行计划 _plan() 同时供 measure 与 draw 使用 → 高度与绘制零漂移；
  * 所有长文本按实测宽度 ellipsis，绝不溢出/裁切；
  * 渲染后 check_boxes 逐卡自检，违规显式记录（不静默吞错），测试里 strict gate。

本模块只负责画；数据 → 内容模型见 bounty_model.py；薄壳入口见 bounty.py。
"""
from __future__ import annotations

import logging

from .. import decor, icons
from ..canvas import Canvas
from . import bounty_atoms as A
from . import bounty_icons as BI
from .bounty_atoms import gate_icons  # noqa: F401 — 保持 L.gate_icons 契约
from .bounty_model import build_model

log = logging.getLogger("qq_bridge.pic.bounty")

# ---------------- 配色（浅色卡，不抄参考图背景）----------------
C_PAGE = "#F5F7FA"
C_CARD = "#FFFFFF"
C_BORDER = "#BFD6E8"
C_LINE = "#D8E3EE"
C_TITLE = "#1F5C99"       # 卡片标题 / 主标题蓝
C_BLUE = "#1668B3"        # 类型词 / 节点名 / 目标行
C_TEXT = "#2B3A4A"        # 正文
C_DIM = "#6B7C8D"         # 弱文字
C_ORANGE = "#E07800"      # 橙色轮换标题 / 深矿 Lv 徽章
C_PURPLE = "#6B3FA0"      # 奖励物品名
C_GREEN = "#3E8E41"       # 绿色赏金徽章
C_GREEN_BG = "#F1F8EC"
C_GREEN_LINE = "#C9E3BC"
C_FOOT_BAR = "#E8EEF5"
C_FOOT_FILL = "#1E6E96"

# 旧版 bounty.py 支持 ``theme.bg_overrides["bounty"]`` 配色覆盖。重做后保留该
# 契约（不静默降级），字段沿用旧 _pal() 的 key，映射到新语义色。
_THEME_COLOR_MAP = {
    "page_color": "C_PAGE", "card_color": "C_CARD", "border_color": "C_BORDER",
    "text_color": "C_TEXT", "dim_color": "C_DIM", "title_color": "C_TITLE",
    "line_color": "C_LINE", "accent_color": "C_PURPLE",
}
#: 模块加载时的默认配色快照：每次 apply_theme 先复位，避免上一次带覆盖的
#: 渲染污染后续所有渲染（模块级常量是进程共享的）。
_DEFAULT_COLORS = {var: globals()[var] for var in _THEME_COLOR_MAP.values()}


def apply_theme(t) -> None:
    """把主题里的 bounty 配色覆盖应用到本模块常量（无覆盖则保持参考图配色）。"""
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("bounty") if isinstance(ov.get("bounty"), dict) else {}
    g = globals()
    for var, d in _DEFAULT_COLORS.items():
        g[var] = d
    for key, var in _THEME_COLOR_MAP.items():
        s = v.get(key)
        if isinstance(s, str) and s:
            g[var] = s

# ---------------- 间距 token（4 的倍数，同层统一）----------------
PAD = 16                  # 页面外边距
CARD_GAP = 12             # 列间距
ROW_GAP = 12              # 行间距
CARD_PAD = 16             # 卡内边距
GAP = 8                   # 元素/token 间距
ICON_GAP = 8
ICON_W = 26
CARD_R = 14

# ---------------- 字号与行高（对齐参考图 800px 宽的视觉比例）----------------
FS_MAIN = 28              # 页面主标题
FS_CARD = 24              # 卡片标题
FS_SECT = 21              # 橙色轮换标题
FS_ITEM = 18              # 奖励物品
FS_JOB = 17               # 任务名 / 类型词
FS_BADGE = 16             # 徽章
FS_TINY = 14              # 脚注 / 来源
FS_FOOT = 16              # 剩余时间

BADGE_PAD_Y = 6
BADGE_H = FS_BADGE + BADGE_PAD_Y * 2      # 28
TITLE_H = 38
SECT_H = 30
ITEM_H = 30
TARGET_H = 26
EMPTY_H = 28
MORE_H = 16
HEADER_H = 50

# 任务类型 → 几何图标语义见 bounty_icons.type_icon（与真实物品图标同模块归口）。
# ------------------------------------------------------------ 行计划 ----
def _plan(card: dict) -> list[dict]:
    """行计划：measure 与 draw 共用同一份，杜绝高度漂移。"""
    rows: list[dict] = [{"k": "title", "h": TITLE_H, "text": card["title"]}]
    if card["section"]:
        rows.append({"k": "section", "h": SECT_H, "text": card["section"]})
    items = card["items"] or []
    # 参考图口径：≤2 个奖励（合一众轮换 / 隔离库）单列整行显示；
    # MOD / 债券这类 5–8 项才走两列网格。统一两列会把「Caliban 机体 蓝图」
    # 这类长名截成「Caliban 机体 …」，属于内容遗漏。
    ncols = 1 if len(items) <= 2 else 2
    for i in range(0, len(items), ncols):
        rows.append({"k": "items", "h": ITEM_H,
                     "pair": items[i:i + ncols], "ncols": ncols})
    if card["empty"]:
        rows.append({"k": "empty", "h": EMPTY_H, "text": card["empty"]})
    hero = card["hero"]
    if hero:
        if hero["lvl"] or hero["stages"]:
            rows.append({"k": "hero_badge", "h": BADGE_H + 2,
                         "lvl": hero["lvl"], "stages": hero["stages"]})
        # 深矿「最终任务」由 bounty_deep 从官方 Expiry 推导，必须整行展示；
        # 无数据时 final 为空自然省略，绝不把 detail 原文重复画成任务名。
        if hero["final"]:
            rows.append({"k": "hero_final", "h": ITEM_H,
                         "final": hero["final"], "icon": hero["icon"]})
    for j in card["jobs"] or []:
        if j["layout"] == "pair":
            rows.append({"k": "pair1", "h": BADGE_H, "job": j})
            rows.append({"k": "pair2", "h": BADGE_H, "job": j})
        else:
            rows.append({"k": "triple1", "h": BADGE_H, "job": j})
            rows.append({"k": "triple2", "h": ITEM_H, "job": j})
            if j["objective"]:
                rows.append({"k": "triple3", "h": TARGET_H, "job": j})
    if card["more"]:
        rows.append({"k": "more", "h": MORE_H})
    return rows


def _rows_h(rows: list[dict]) -> int:
    return CARD_PAD * 2 + sum(r["h"] for r in rows) + GAP * max(0, len(rows) - 1)


# -------------------------------------------------------------- 绘制 ----
def _add(boxes: list, label: str, b) -> None:
    boxes.append((label,) + tuple(int(v) for v in b))


def _badge_green(c: Canvas, x: int, y: int, text: str, label: str,
                 boxes: list) -> int:
    b, w, _ = A.badge(c, x, y, text, size=FS_BADGE, fill=C_GREEN_BG,
                      color=C_GREEN, outline=C_GREEN_LINE, dot=True,
                      pad_x=8, pad_y=BADGE_PAD_Y)
    _add(boxes, label, b)
    return w


def _badge_lv(c: Canvas, x: int, y: int, text: str, fill: str,
              label: str, boxes: list) -> int:
    b, w, _ = A.badge(c, x, y, text, size=FS_BADGE, fill=fill,
                      color="#FFFFFF", notch=True, pad_x=9, pad_y=BADGE_PAD_Y)
    _add(boxes, label, b)
    return w


def _draw_row(c: Canvas, row: dict, x: int, y: int, cols: A.Columns,
              label: str, boxes: list, missing: list[str]) -> None:
    """按行计划绘制。所有 x 推进都来自 A.tw / bbox 实测值 + 显式 GAP。"""
    k, h, cy = row["k"], row["h"], y + row["h"] // 2
    right = cols.right
    if k == "title":
        _add(boxes, label + ":title",
             A.text_box(c, x, cy, row["text"], FS_CARD, C_TITLE, "bold"))
        c.line((x, y + h - 5, right, y + h - 5), fill=C_LINE, width=1)
    elif k == "section":
        _add(boxes, label + ":section",
             A.text_box(c, x, cy, A.ellipsis(row["text"], FS_SECT, "bold", cols.width),
                        FS_SECT, C_ORANGE, "bold"))
    elif k == "empty":
        _add(boxes, label + ":empty",
             A.text_box(c, x, cy, row["text"], FS_ITEM, C_DIM))
    elif k == "items":
        ncols = row.get("ncols", 2)
        col_w = (cols.width - GAP * (ncols - 1)) // ncols
        for ci, it in enumerate(row["pair"]):
            ix = x + ci * (col_w + GAP)
            icy = cy
            ok = A.item_image(c, it.get("image"), ix, icy - ICON_W // 2, ICON_W)
            if not ok:
                # 几何方块只作为**异常兜底**：必须显式登记 + 告警，测试里
                # gate_icons(strict=True) 会直接拦下，禁止再拿它当默认交差。
                missing.append(f"{label}:{it.get('text', '')}")
                A.icon(c, "disk", ix + ICON_W // 2, icy, ICON_W - 2, C_PURPLE)
            _add(boxes, label + f":icon{ci}",
                 (ix, icy - ICON_W // 2, ix + ICON_W, icy + ICON_W // 2))
            tx = ix + ICON_W + ICON_GAP
            maxw = max(0, col_w - ICON_W - ICON_GAP)
            _add(boxes, label + f":item{ci}",
                 A.text_box(c, tx, icy,
                            A.ellipsis(it.get("text", ""), FS_ITEM, "regular", maxw),
                            FS_ITEM, C_PURPLE))
    elif k == "hero_badge":
        lv = f"Lv{row['lvl']}" if row["lvl"] else ""
        w = _badge_lv(c, x, y + (h - BADGE_H) // 2, lv or "Lv—",
                      C_ORANGE, label + ":hero_lv", boxes) if lv else 0
        if row["stages"]:
            tx = x + w + GAP if w else x
            _add(boxes, label + ":stages",
                 A.text_box(c, tx, cy, row["stages"], FS_JOB, C_BLUE, "bold"))
    elif k == "hero_final":
        b1 = A.text_box(c, x, cy, "最终任务：", FS_JOB, C_DIM)
        _add(boxes, label + ":final_label", b1)
        ix = b1[2] + GAP
        A.icon(c, BI.type_icon(row["final"], row["icon"]), ix + ICON_W // 2, cy,
               ICON_W - 2, C_BLUE)
        _add(boxes, label + ":final",
             A.text_box(c, ix + ICON_W + GAP, cy,
                        A.ellipsis(row["final"], FS_JOB, "bold",
                                   max(0, right - (ix + ICON_W + GAP))),
                        FS_JOB, C_BLUE, "bold"))
    elif k == "pair1":
        j = row["job"]
        w = _badge_green(c, x, y + (h - BADGE_H) // 2, j["tier"],
                         label + ":tier", boxes)
        tx = x + w + GAP
        _add(boxes, label + ":name",
             A.text_box(c, tx, cy,
                        A.ellipsis(j["name"], FS_JOB, "regular", max(0, right - tx)),
                        FS_JOB, C_TEXT))
    elif k == "pair2":
        j = row["job"]
        w = 0
        if j["lvl"]:
            w = _badge_lv(c, x, y + (h - BADGE_H) // 2, f"Lv{j['lvl']}",
                          j["lvl_color"], label + ":lv", boxes)
        ix = x + w + GAP if w else x
        A.icon(c, BI.type_icon(j["type"], j["icon"]), ix + ICON_W // 2, cy,
               ICON_W - 2, C_BLUE)
        tx = ix + ICON_W + GAP
        _add(boxes, label + ":type",
             A.text_box(c, tx, cy,
                        A.ellipsis(j["type"], FS_JOB, "bold", max(0, right - tx)),
                        FS_JOB, C_BLUE, "bold"))
    elif k == "triple1":
        j = row["job"]
        w = _badge_green(c, x, y + (h - BADGE_H) // 2, j["tier"],
                         label + ":tier", boxes)
        tx = x + w + GAP
        _add(boxes, label + ":node",
             A.text_box(c, tx, cy,
                        A.ellipsis(j["name"], FS_JOB, "bold", max(0, right - tx)),
                        FS_JOB, C_BLUE, "bold"))
    elif k == "triple2":
        j = row["job"]
        A.icon(c, BI.type_icon(j["type"], j["icon"]), x + ICON_W // 2, cy,
               ICON_W - 2, C_BLUE)
        tx = x + ICON_W + GAP
        typ = j["type"]
        room = max(0, right - tx - (A.tw(typ, FS_JOB, "bold") + GAP if typ else 0))
        mission = A.ellipsis(j["mission"], FS_JOB, "regular", room)
        tokens = [(t, col, role) for t, col, role in
                  ((typ, C_BLUE, "bold"), (mission, C_TEXT, "regular")) if t]
        _, bs = A.draw_tokens(c, tx, cy, tokens, FS_JOB, gap=GAP)
        for n, b in enumerate(bs):
            _add(boxes, label + f":triple2_{n}", b)
    elif k == "triple3":
        j = row["job"]
        c.line((x + 2, y + 2, x + 2, cy), fill=C_BLUE, width=1)
        c.line((x + 2, cy, x + 14, cy), fill=C_BLUE, width=1)
        tx = x + 20
        label_txt = "目标："
        room = max(0, right - tx - A.tw(label_txt, FS_TINY) - GAP)
        obj = A.ellipsis(j["objective"], FS_TINY, "regular", room)
        tokens = [(label_txt, C_BLUE, "regular"), (obj, C_BLUE, "regular")]
        _, bs = A.draw_tokens(c, tx, cy, tokens, FS_TINY, gap=GAP)
        for n, b in enumerate(bs):
            _add(boxes, label + f":target_{n}", b)
    elif k == "more":
        s = "..."
        _add(boxes, label + ":more",
             A.text_box(c, cols.tail_x(s, FS_TINY, "bold"), cy,
                        s, FS_TINY, C_DIM, "bold"))


def _draw_card(c: Canvas, card: dict, x0: int, y0: int, card_w: int,
               card_h: int, label: str) -> tuple[list[tuple], list[str]]:
    c.rounded_rect((x0, y0, x0 + card_w, y0 + card_h), radius=CARD_R,
                   fill=C_CARD, outline=C_BORDER, width=1)
    left = x0 + CARD_PAD
    right = x0 + card_w - CARD_PAD
    cols = A.Columns(left, right, GAP)
    boxes: list[tuple] = []
    missing: list[str] = []
    y = y0 + CARD_PAD
    for row in _plan(card):
        _draw_row(c, row, left, y, cols, label, boxes, missing)
        y += row["h"] + GAP
    return boxes, missing


# -------------------------------------------------------------- 页脚 ----
def _footer_h() -> int:
    return 12 + 32 + GAP + 28 + 12 + 1 + GAP + 26


def _draw_footer(c: Canvas, f: dict, x0: int, y0: int, width: int,
                 boxes: list[tuple]) -> None:
    right = width - PAD
    cols = A.Columns(x0, right, GAP)
    # 剩余时间：标签 + 进度条 + 橙色时间文字（条内右侧）
    y = y0 + 12
    lab = "剩余时间："
    b = A.text_box(c, x0, y + 15, lab, FS_FOOT, C_TEXT)
    _add(boxes, "foot:remain_label", b)
    bar_x = b[2] + GAP
    bar_w = 240
    bar_h = 26
    c.rounded_rect((bar_x, y + 2, bar_x + bar_w, y + 2 + bar_h),
                   radius=bar_h // 2, fill=C_FOOT_BAR)
    fill_w = max(bar_h, int(bar_w * float(f["progress"])))
    c.rounded_rect((bar_x, y + 2, bar_x + min(fill_w, bar_w), y + 2 + bar_h),
                   radius=bar_h // 2, fill=C_FOOT_FILL)
    if f["remain"]:
        _add(boxes, "foot:remain",
             A.text_box(c, bar_x + bar_w - A.tw(f["remain"], FS_FOOT, "bold") - 12,
                        y + 15, f["remain"], FS_FOOT, C_ORANGE, "bold"))
    y += 32 + GAP
    _, bs = A.draw_tokens(c, x0, y + 14, f["hint"], 17, gap=0)
    # 脚注是一句**连续**的话（多色 token 只是其中几个词），按一个视觉元素
    # 登记合并 bbox；否则自检会把句内相邻 token 误判成「间距失控」。
    if bs:
        merged = (min(b[0] for b in bs), min(b[1] for b in bs),
                  max(b[2] for b in bs), max(b[3] for b in bs))
        _add(boxes, "foot:hint", merged)
    y += 28 + 12
    c.line((x0, y, right, y), fill=C_LINE, width=1)
    y += 1 + GAP
    # 平台胶囊 + 真实来源 + 操作提示
    b, w, _ = A.badge(c, x0, y + 1, f["platform"], size=FS_TINY,
                      fill="#FFFFFF", color="#3E8E41", outline="#9FCBAE",
                      pad_x=8, pad_y=3)
    _add(boxes, "foot:platform", b)
    tx = x0 + w + GAP
    if f["source"]:
        src = A.ellipsis(f["source"], FS_TINY, "regular",
                         max(0, right - tx - A.tw(f["tip"], FS_TINY) - GAP))
        _add(boxes, "foot:source",
             A.text_box(c, tx, y + 12, src, FS_TINY, C_DIM))
        tx += A.tw(src, FS_TINY) + GAP
    _add(boxes, "foot:tip",
         A.text_box(c, cols.tail_x(f["tip"], FS_TINY), y + 12,
                    f["tip"], FS_TINY, C_DIM))


# -------------------------------------------------------------- 入口 ----
def build(t, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    """渲染赏金卡；返回 Canvas（附带 layout_problems 供自检/测试读取）。"""
    w = int(width or getattr(t, "width", 800) or 800)
    apply_theme(t)
    card_w = (w - PAD * 2 - CARD_GAP) // 2
    model = build_model(data, source)
    cards = model["cards"]

    plans = [_plan(c) for c in cards]
    heights = [_rows_h(p) for p in plans]
    row_h = [max(heights[i], heights[i + 1]) for i in range(0, len(cards), 2)]
    total_h = (PAD + HEADER_H + GAP + sum(row_h)
               + ROW_GAP * (len(row_h) - 1) + _footer_h() + PAD)

    c = Canvas(w, total_h, C_PAGE, theme=t)
    decor.background(c, t, tpl="bounty", only_override=True)

    all_boxes: list[tuple] = []
    problems: list[str] = []
    all_missing: list[str] = []

    # 主标题：居中（图标 + 文字，按实测总宽反推起点）+ 贯通分割线
    title = model["title"]
    icon_w = FS_MAIN + 4
    tokens = [(title, C_TITLE, "bold")]
    title_w = A.tw(title, FS_MAIN, "bold")
    start_x = (w - title_w - icon_w - GAP) // 2 + icon_w + GAP
    A.icon(c, "globe", start_x - icon_w - GAP + icon_w // 2,
           PAD + HEADER_H // 2 - 4, FS_MAIN, C_TITLE)
    _, bs = A.draw_tokens(c, start_x, PAD + HEADER_H // 2 - 4,
                          tokens, FS_MAIN, gap=GAP)
    for b in bs:
        _add(all_boxes, "head:title", b)
    hy = PAD + HEADER_H - 6
    c.line((PAD, hy, w - PAD, hy), fill=C_LINE, width=1)

    y = PAD + HEADER_H + GAP
    for ri in range(len(row_h)):
        for ci in (0, 1):
            idx = ri * 2 + ci
            x0 = PAD + ci * (card_w + CARD_GAP)
            cb, cm = _draw_card(c, cards[idx], x0, y, card_w, row_h[ri],
                                f"c{idx}")
            all_boxes += cb
            all_missing += cm
            safe = (x0 + CARD_PAD, y + CARD_PAD,
                    x0 + card_w - CARD_PAD, y + row_h[ri] - CARD_PAD)
            problems += A.check_boxes(safe, cb, min_gap=2)
        y += row_h[ri] + ROW_GAP

    foot_y = y - ROW_GAP
    foot_boxes: list[tuple] = []
    _draw_footer(c, model["footer"], PAD, foot_y, w, foot_boxes)
    all_boxes += foot_boxes
    problems += A.check_boxes((PAD, foot_y, w - PAD, total_h - PAD),
                              foot_boxes, min_gap=2)
    problems += A.check_boxes((PAD, 0, w - PAD, total_h), all_boxes, min_gap=2)

    # 禁止静默吞错：违规显式落日志 + 挂到 Canvas 供测试严格断言
    c.layout_problems = problems
    c.layout_boxes = all_boxes
    c.missing_icons = all_missing
    if problems:
        log.warning("bounty_layout_check_failed count=%d first=%s",
                    len(problems), problems[:3])
    if all_missing:
        log.warning("bounty_missing_item_icons count=%d first=%s",
                    len(all_missing), all_missing[:5])
    return c
