# -*- coding: utf-8 -*-
"""pic/templates/hw_market_daily.py —— 今日行情视图（ZOL 每日快照）。

从 ``hw_market.py`` 按功能拆出（守 20KB 文件门禁）：本模块只画「今日」总览，
共用主模板的度量/分区/行渲染原语，避免两套版式漂移。

两种形态：
  * **首日基线**（还没有上一份快照）：不画空的涨跌榜，改画「今日在售」价格表；
  * **有对比**：涨幅榜 / 跌幅榜（今日 vs 上一份快照）。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme
from .. import decor, widgets as w
from .hw_market import (  # noqa: F401 — 共用原语
    FS_TITLE, FS_SUB, FS_SEC, FS_PANEL_NAME, FS_NUM, FS_FOOT,
    PAD, GAP_M, GAP_L, PANEL_H, ROW_H, SEC_TO_ROW, FOOT_DAILY,
    _money, _rec, _tb, _section, _mover_row,
)


def _layout(pad: int, sections: int, rows: int, note: bool) -> dict:
    y = pad + 42
    L = {"title": y}
    y += 38
    L["sub"] = y
    y += 20 + GAP_M
    L["panels"] = y
    y += PANEL_H + GAP_L
    L["sec0"] = y
    y += sections * SEC_TO_ROW + rows * ROW_H + max(0, sections - 1) * 20
    L["note"] = y + 14 if note else None
    if note:
        y += 30
    L["foot"] = y + 16
    L["h"] = L["foot"] + 28 + pad
    return L


def _panel_daily(c, t, box, b, boxes, idx):
    x0, y0, x1, y1 = box
    c.rounded_rect(box, radius=t.r_ctrl, fill=t.track)
    _rec(boxes, "stat.%d" % idx, box)
    name = str(b["name"])
    c.text((x0 + 14, y0 + 24), name, size=t.fs(FS_PANEL_NAME), color=t.accent,
           role="bold", anchor="lm")
    _rec(boxes, "stat.%d.name" % idx, _tb((x0 + 14, y0 + 24), name, t.fs(FS_PANEL_NAME), "bold"))
    cx, ny = x0 + 14, y0 + 60
    for num, tag, color in ((b["up"], "涨", t.err), (b["down"], "跌", t.ok),
                            (b["flat"], "平", t.text_sub)):
        s = str(num)
        c.text((cx, ny), s, size=t.fs(FS_NUM), color=color, role="bold", anchor="lm")
        _rec(boxes, "stat.%d.num.%s" % (idx, tag), _tb((cx, ny), s, t.fs(FS_NUM), "bold"))
        cx += Canvas.text_width(s, t.fs(FS_NUM), "bold") + 2
        c.text((cx, ny + 8), tag, size=t.fs(15), color=t.text_sub, anchor="lm")
        _rec(boxes, "stat.%d.tag.%s" % (idx, tag), _tb((cx, ny + 8), tag, t.fs(15)))
        cx += Canvas.text_width(tag, t.fs(15)) + 12
    cover = "在售 %d · 可比 %d" % (b["tracked"], b["compared"])
    c.text((x0 + 14, y1 - 18), cover, size=t.fs(13), color=t.text_weak, anchor="lm")
    _rec(boxes, "stat.%d.cover" % idx, _tb((x0 + 14, y1 - 18), cover, t.fs(13)))


def _price_row(c, t, x, y, width, item, boxes, key):
    """首日基线的价格行：型号 + 样本数（左），当日最低价（右）。"""
    label = Canvas.ellipsis(str(item["label"]), t.fs(21), "bold", int(width * 0.56))
    c.text((x + 16, y), label, size=t.fs(21), color=t.text, role="bold", anchor="lm")
    _rec(boxes, key + ".label", _tb((x + 16, y), label, t.fs(21), "bold"))
    sub = "%d 条样本" % int(item.get("samples") or 0)
    c.text((x + int(width * 0.64), y), sub, size=t.fs(15), color=t.text_weak, anchor="rm")
    _rec(boxes, key + ".sub", _tb((x + int(width * 0.64), y), sub, t.fs(15), anchor="rm"))
    price = _money(item["price"])
    c.text((x + width, y), price, size=t.fs(24), color=t.accent, role="bold", anchor="rm")
    _rec(boxes, key + ".price", _tb((x + width, y), price, t.fs(24), "bold", "rm"))


def build_daily(t: Theme, payload: dict, width: int, boxes=None) -> Canvas:
    pad = int(getattr(t, "pad", PAD) or PAD)
    x0, x1 = pad * 2, width - pad * 2
    cw = x1 - x0
    buckets = payload["buckets"]
    baseline = not payload.get("prev_date")
    gainers = [] if baseline else (payload.get("gainers") or [])
    losers = [] if baseline else (payload.get("losers") or [])
    samples = (payload.get("samples") or []) if baseline else []
    sections = 1 if baseline else 2
    rows = len(samples) if baseline else len(gainers) + len(losers)
    note_text = str(payload.get("note") or "")
    if baseline:
        note_text = ("首日基线：共采到 %d 个显卡系列，明日起显示「今日 vs 昨日」"
                     % payload.get("gpu_n", 0))
    L = _layout(pad, sections, rows, bool(note_text))
    h = L["h"]
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="hw")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    title = "DIY 硬件行情 · 今日"
    c.text((x0, L["title"]), title, size=t.fs(FS_TITLE), color=t.text, role="bold", anchor="lm")
    _rec(boxes, "hdr.title", _tb((x0, L["title"]), title, t.fs(FS_TITLE), "bold"))
    day = str(payload.get("date") or "")
    c.text((x1, L["title"]), day, size=t.fs(24), color=t.accent, role="bold", anchor="rm")
    _rec(boxes, "hdr.month", _tb((x1, L["title"]), day, t.fs(24), "bold", "rm"))

    if baseline:
        sub = "ZOL 公开报价 · %s 抓取 · 首日基线" % payload.get("fetched_at", "")
    else:
        sub = "ZOL 公开报价 · %s 抓取 · 对比 %s" % (payload.get("fetched_at", ""),
                                                  payload.get("prev_date", ""))
    sub = Canvas.ellipsis(sub, t.fs(FS_SUB), "regular", cw)
    c.text((x0, L["sub"]), sub, size=t.fs(FS_SUB), color=t.text_sub, anchor="lm")
    _rec(boxes, "hdr.sub", _tb((x0, L["sub"]), sub, t.fs(FS_SUB)))

    gap = GAP_M
    pw = (cw - gap * (len(buckets) - 1)) // max(1, len(buckets))
    for i, b in enumerate(buckets):
        bx = x0 + i * (pw + gap)
        _panel_daily(c, t, (bx, L["panels"], bx + pw, L["panels"] + PANEL_H), b, boxes, i)

    y = L["sec0"]
    if baseline:
        _section(c, t, x0, y, cw, "今日在售 · %d 个显卡系列" % len(samples), boxes, "sec.list")
        y += SEC_TO_ROW
        for i, item in enumerate(samples):
            _price_row(c, t, x0, y + i * ROW_H + ROW_H // 2, cw, item, boxes, "list.%d" % i)
    else:
        _section(c, t, x0, y, cw, "今日涨幅榜", boxes, "sec.gain")
        y += SEC_TO_ROW
        for i, m in enumerate(gainers):
            _mover_row(c, t, x0, y + i * ROW_H + ROW_H // 2, cw, m, True, boxes, "gain.%d" % i)
        y += len(gainers) * ROW_H + 20
        _section(c, t, x0, y, cw, "今日跌幅榜", boxes, "sec.lose")
        y += SEC_TO_ROW
        for i, m in enumerate(losers):
            _mover_row(c, t, x0, y + i * ROW_H + ROW_H // 2, cw, m, False, boxes, "lose.%d" % i)

    if L["note"] is not None:
        note = Canvas.ellipsis(note_text, t.fs(15), "bold", cw)
        c.text((x0, L["note"] + 8), note, size=t.fs(15), color=t.accent,
               role="bold", anchor="lm")
        _rec(boxes, "note", _tb((x0, L["note"] + 8), note, t.fs(15), "bold"))

    foot = FOOT_DAILY % payload.get("at", "10:10")
    c.text((x0, L["foot"]), foot, size=t.fs(FS_FOOT), color=t.text_weak, anchor="lm")
    _rec(boxes, "footer", _tb((x0, L["foot"]), foot, t.fs(FS_FOOT)))
    return c
