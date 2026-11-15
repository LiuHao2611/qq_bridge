# -*- coding: utf-8 -*-
"""pic/templates/doupo_faction.py —— 斗破·宗门卡（2026-11-15）。

为什么单独一张模板
------------------
用户要求「把宗门优化成卡片展示，注意设计美化风格」。宗门信息是**分区型**
（宗门概览 / 本门斗技统计 / 代表人物 / 传承异火 / 可转宗门），
塞进通用列表卡 ``doupo_list`` 的「标题 + 右侧 + 说明」三列行式版式会：
① 把异火/宝库/背包/排行一起改坏；② 代表人物、异火这类长段落没法折行。
因此另开专用模板，``doupo_list`` 保持原样（无创原则）。

版式（羊皮纸米 / 朱红 / 暗金，与人物卡、斗技卡同源）
------------------------------------------------
  * 顶部：大标题「宗门」+ globe 细描边图标 + 贯通分割线；
  * 宗门概览：宗门名(30px 粗体朱红) + 右端境界 + 两枚 pill（据点 / 卡池）
    + 一行「境界 lvX · 已开放卡池」小字；
  * 本门斗技统计：3 格大数字（普通 / 高阶 / 特殊），数字按阶位上色；
  * 代表人物：星标分区 + 自动折行段落；
  * 传承异火：bolt 分区 + 自动折行段落；
  * 可转宗门：list 分区，按卡池分组（每组的可转宗门与门数）；
  * 底部：分割线 + 用法提示。

设计约束（card-design 红线）
--------------------------
  * **度量优先**：所有宽度用 ``_w`` 实测、超宽用 ``_fit`` 截断；
    段落用 ``Canvas.wrap`` 折行（不估算字宽、不用空格凑间距）；
  * 间距统一走 ``BLOCK_GAP`` / ``LINE_H`` 常量，不写魔法偏移；
  * ``_measure`` 与 ``build`` 用**同一套步进**，卡片高度由内容撑开，
    底部不会被裁（测试断言 height == _measure）；
  * ``build(..., boxes=[...])`` 可收集全部文本包围盒，供 `check_boxes`
    自检（溢出 / 重叠 / 相邻间距），把红线拦在交差前；
  * 纯 Pillow 自绘；不使用 emoji；配色读 ``Theme.bg_overrides["doupo"]``。

输入（build 的 data）：
  name/pool/base/npcs/flames/tier_counts/total_skills/open_pools/lv
  groups: [{"pool": "进阶池", "names": ["云岚宗 9门", ...]}]
  switch_cost/tip
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与人物卡 / 斗技卡共用同一套配色与度量工具
from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

# ---- 排版 ----
PAD = 28
FS_TITLE = 36
FS_NAME = 30            # 宗门名
FS_BODY = 20
FS_SUB = 17             # 段落正文（清晰下限）
FS_LABEL = 15
FS_STAT = 36            # 统计大数字
FS_TIP = 16

ICON = 22
ICON_STYLE = "line"

PILL_H = 32
STAT_CELL_H = 86
LINE_H = FS_SUB + 8
BLOCK_GAP = 12
SECTION_H = FS_SUB + 12     # 分区标题行高

H_TITLE = FS_TITLE + 26
H_HEADER = FS_NAME + 12 + PILL_H + 14 + FS_LABEL + 8
H_STATS = SECTION_H + STAT_CELL_H + BLOCK_GAP
H_TIP = 12 + FS_TIP + 8

#: 三阶统计的数字色（普通/高阶/特殊 逐级升温，一眼看出阶位）
TIER_COLORS = ("text_color", "accent_color", "title_color")


def _pack(items, sep: str, width: int) -> list[str]:
    """按条目打包折行：优先整条不拆，单条超宽才字符折行。

    为什么不用 Canvas.wrap 直接折：中文按字符折会把「血宗 6门」拆成
    「…血宗 6」/「门」，读起来像断字。这里以「、」「 · 」为界整条搬运。
    """
    items = [str(x).strip() for x in (items or []) if str(x).strip()]
    out: list[str] = []
    cur = ""
    for it in items:
        cand = it if not cur else cur + sep + it
        if _w(cand, FS_SUB, "regular") <= width:
            cur = cand
            continue
        if cur:
            out.append(cur)
        if _w(it, FS_SUB, "regular") > width:
            out.extend(Canvas.wrap(it, FS_SUB, "regular", width))
            cur = ""
        else:
            cur = it
    if cur:
        out.append(cur)
    return out


def _list_lines(items, sep: str, width: int) -> list[str]:
    """条目列表 → 折行后的行列表（空列表表示无内容，由调用方画占位）。"""
    return _pack(items, sep, width)


def _measure(data: dict, width: int) -> int:
    """卡片总高。步进与 build 完全一致（否则底部会裁/留白）。"""
    content_w = max(400, width - PAD * 2)
    wrap_w = content_w - 40
    y = PAD + H_TITLE + H_HEADER + H_STATS

    npc_lines = _list_lines(data.get("npcs"), "、", wrap_w)
    y += SECTION_H + max(1, len(npc_lines)) * LINE_H + BLOCK_GAP
    flame_lines = _list_lines(data.get("flames"), "、", wrap_w)
    y += SECTION_H + max(1, len(flame_lines)) * LINE_H + BLOCK_GAP

    groups = data.get("groups") or []
    if not groups:
        y += SECTION_H + LINE_H + BLOCK_GAP
    else:
        for g in groups:
            gl = _list_lines(g.get("names"), " · ", wrap_w)
            y += SECTION_H + max(1, len(gl)) * LINE_H + BLOCK_GAP

    y += H_TIP
    return max(420, y + PAD)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    wrap_w = content_w - 40
    h = _measure(data, width)

    #: 可选：收集文本包围盒（供 check_boxes 自检；默认 None 不记录开销）
    boxes = kw.pop("boxes", None)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    def rec(label: str, txt: str, x: int, y: int, size: int, role: str,
            anchor: str) -> None:
        """记录文本包围盒（绘制与度量同 role，锚点换算与 Canvas 一致）。"""
        if boxes is None:
            return
        w = _w(txt, size, role)
        hh = int(size)
        if anchor == "lm":
            bb = (x, y - hh // 2, x + w, y + hh // 2)
        elif anchor == "rm":
            bb = (x - w, y - hh // 2, x, y + hh // 2)
        elif anchor == "mm":
            bb = (x - w // 2, y - hh // 2, x + w // 2, y + hh // 2)
        else:                                  # la
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

    def ktext(label: str, x: int, y: int, txt, size: int, color: str,
              anchor: str = "lm", max_w: int | None = None) -> None:
        """重点文字：>=22px 真粗体，小字降级为清晰常规字（doupo_style 规范）。"""
        bold = int(size) >= S.MIN_BOLD_FS
        role = "bold" if bold else "regular"
        t = _fit(str(txt), max_w, size, role) if max_w is not None else str(txt)
        c.text((x, y), t, size=int(size), color=color, role=role,
               bold=bold, anchor=anchor)
        rec(label, t, x, y, size, role, anchor)

    def stext(label: str, x: int, y: int, txt, size: int, color: str,
              anchor: str = "lm", max_w: int | None = None) -> None:
        """次要文字：明确不加粗，只靠字号与颜色分层。"""
        t = _fit(str(txt), max_w, size, "regular") if max_w is not None else str(txt)
        c.text((x, y), t, size=int(size), color=color, role="regular",
               bold=False, anchor=anchor)
        rec(label, t, x, y, size, "regular", anchor)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    title = str(data.get("title") or "宗门")
    tw = _w(title, FS_TITLE, "light")
    ico_w = FS_TITLE + 10
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, "globe", tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE,
               p["title_color"], style=ICON_STYLE)
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    rec("title", title, tx + ico_w, y + FS_TITLE // 2, FS_TITLE, "light", "lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 宗门概览 ----
    name = str(data.get("name") or "无宗门")
    lv = int(data.get("lv") or 1)
    lv_text = "Lv.%d" % lv
    lv_w = _w(lv_text, FS_SUB, "bold")
    ktext("faction_name", x0, y + FS_NAME // 2, name, FS_NAME,
          p["title_color"], max_w=content_w - lv_w - 16)
    ktext("lv", x0 + content_w, y + FS_NAME // 2, lv_text, FS_SUB,
          p["chip_text"], anchor="rm")
    y += FS_NAME + 12

    # 两枚 pill：据点 / 卡池（宽度实测，从左往右排，显式 10px gap）
    pills = [("globe", "据点", str(data.get("base") or "")),
             ("tag", "卡池", str(data.get("pool") or ""))]
    px = x0
    for icon, label, val in pills:
        if not val:
            continue
        txt = "%s：%s" % (label, val)
        pw = ICON + 10 + _w(txt, FS_SUB, "bold") + 28
        pw = min(pw, x0 + content_w - px)          # 极端长名不越界
        c.rounded_rect((px, y, px + pw, y + PILL_H), radius=PILL_H // 2,
                       fill=p["pill_bg"], outline=p["pill_line"], width=1)
        icons.draw(c, icon, px + 16, y + PILL_H // 2, ICON,
                   p["pill_text"], style=ICON_STYLE)
        ktext("pill_" + label, px + 16 + ICON + 2, y + PILL_H // 2, txt, FS_SUB,
              p["pill_text"], max_w=max(20, pw - (ICON + 18) - 12))
        px += pw + 10
    y += PILL_H + 14

    # 开放卡池一行
    open_pools = data.get("open_pools") or []
    pools_txt = "境界 lv%d · 已开放卡池：%s" % (lv, " ".join(open_pools) or "（无）")
    stext("open_pools", x0, y + FS_LABEL // 2, pools_txt, FS_LABEL,
          p["dim_color"], max_w=content_w)
    y += FS_LABEL + 8

    # ---- 本门斗技统计 ----
    total = int(data.get("total_skills") or 0)
    y = _section(c, x0, y, content_w, p, "sword",
                 "本门斗技统计 · 共 %d 门" % total, boxes)
    tiers = ("普通", "高阶", "特殊")
    cell_gap = 12
    cell_w = (content_w - cell_gap * 2) // 3
    counts = data.get("tier_counts") or {}
    for i, tier in enumerate(tiers):
        cx = x0 + i * (cell_w + cell_gap)
        c.rounded_rect((cx, y, cx + cell_w, y + STAT_CELL_H), radius=12,
                       fill=p["cell_bg"], outline=p["cell_line"], width=1)
        ktext("stat_%s" % tier, cx + cell_w // 2, y + 34,
              str(int(counts.get(tier, 0))), FS_STAT, p[TIER_COLORS[i]], anchor="mm")
        stext("stat_label_%s" % tier, cx + cell_w // 2, y + STAT_CELL_H - 20,
              tier + "斗技", FS_LABEL, p["dim_color"], anchor="mm")
    y += STAT_CELL_H + BLOCK_GAP

    # ---- 代表人物 ----
    y = _section(c, x0, y, content_w, p, "star", "代表人物", boxes)
    npc_lines = _list_lines(data.get("npcs"), "、", wrap_w)
    for i, ln in enumerate(npc_lines or ["（未记载）"]):
        stext("npc_%d" % i, x0 + 20, y + LINE_H // 2, ln, FS_SUB, p["text_color"])
        y += LINE_H
    y += BLOCK_GAP

    # ---- 传承异火 ----
    y = _section(c, x0, y, content_w, p, "bolt", "传承异火", boxes)
    flame_lines = _list_lines(data.get("flames"), "、", wrap_w)
    for i, ln in enumerate(flame_lines or ["（未记载）"]):
        stext("flame_%d" % i, x0 + 20, y + LINE_H // 2, ln, FS_SUB, p["text_color"])
        y += LINE_H
    y += BLOCK_GAP

    # ---- 可转宗门（按卡池分组）----
    cost = int(data.get("switch_cost") or 0)
    groups = data.get("groups") or []
    if not groups:
        y = _section(c, x0, y, content_w, p, "list", "可转宗门", boxes)
        stext("no_group", x0 + 20, y + LINE_H // 2, "当前境界暂无可转宗门",
              FS_SUB, p["dim_color"])
        y += LINE_H + BLOCK_GAP
    else:
        for gi, g in enumerate(groups):
            label = "可转宗门 · %s（需 %d 金币）" % (g.get("pool") or "", cost)
            y = _section(c, x0, y, content_w, p, "list", label, boxes)
            glines = _list_lines(g.get("names"), " · ", wrap_w)
            for i, ln in enumerate(glines or ["（无）"]):
                stext("group_%d_%d" % (gi, i), x0 + 20, y + LINE_H // 2, ln,
                      FS_SUB, p["text_color"])
                y += LINE_H
            y += BLOCK_GAP

    # ---- 底部提示 ----
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip:
        stext("tip", x0, y + FS_TIP // 2, str(tip), FS_TIP,
              p["dim_color"], max_w=content_w)

    if source:
        stext("source", width - PAD, h - PAD + 6, str(source), 14,
              p["dim_color"], anchor="rb")
    return c


def _section(c: Canvas, x: int, y: int, w: int, p: dict, icon: str, label: str,
             boxes=None) -> int:
    """分区标题：monoline 图标 + 标签 + 右侧贯通细线。返回下一行 y。"""
    icons.draw(c, icon, x + ICON // 2, y + FS_SUB // 2, ICON,
               p["accent_color"], style=ICON_STYLE)
    t = _fit(label, w - ICON - 10, FS_SUB, "regular")
    c.text((x + ICON + 10, y + FS_SUB // 2), t, size=FS_SUB,
           color=p["text_color"], role="regular", bold=False, anchor="lm")
    if boxes is not None:
        lw = _w(t, FS_SUB, "regular")
        boxes.append(("section_" + label, x + ICON + 10, y + FS_SUB // 2 - FS_SUB // 2,
                      x + ICON + 10 + lw, y + FS_SUB // 2 + FS_SUB // 2))
    lx = x + ICON + 10 + _w(t, FS_SUB, "regular") + 12
    if lx < x + w:
        c.line((lx, y + FS_SUB // 2, x + w, y + FS_SUB // 2),
               fill=p["line_color"], width=1)
    return y + SECTION_H
