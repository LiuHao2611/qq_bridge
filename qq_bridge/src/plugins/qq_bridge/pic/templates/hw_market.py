# -*- coding: utf-8 -*-
"""pic/templates/hw_market.py —— DIY 硬件行情卡（两视图：总览 / 单型号）。

数据来自上游 frocloud-DIYPCmarket 的**月度**档案（`hardware/vendored_frocloud`），
不是日内实时报价 —— 卡面必须写清口径与数据截止日，禁止用「今日 / 实时」误导。

两视图共用一套骨架：
  * ``view="overview"``：三个分类涨跌面板 + 全市场涨幅榜 / 跌幅榜 + 口径页脚；
  * ``view="model"``：单价 + 环比 + 历史区间 + 走势折线 + 天梯/跑分。

所有文本先测量再落笔（``Canvas.text_size``），``build(boxes=...)`` 把每个叶子的
真实包围盒登记给 ``tools/check_hw_market_layout.py`` 做溢出/重叠/间距核对。
布局先算坐标再画（``_overview_layout`` / ``_model_layout``），保证高度与内容同源，
不靠"画完再看会不会裁"。

颜色遵循 A 股习惯：涨 = 红（``t.err``）、跌 = 绿（``t.ok``）、平 = 弱文字。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme
from .. import decor, widgets as w

FS_TITLE = 40
FS_SUB = 17
FS_SEC = 21
FS_ROW = 21
FS_PCT = 23
FS_PRICE = 17
FS_FOOT = 14
FS_PANEL_NAME = 18
FS_NUM = 30
FS_BIG = 46
FS_METRIC = 18

PAD = 24
GAP_M = 12
GAP_L = 20
PANEL_H = 116
ROW_H = 44
SEC_TO_ROW = 26
CHART_H = 188

#: 口径文案（唯一来源）：页脚必须同时出现「月度」与「非日内」，禁止「今日/实时行情」。
FOOT_OVERVIEW = ("来源 frocloud-DIYPCmarket（数据 CC BY 4.0 · 脚本 MIT）"
                 "· 月度档案，非日内行情")
FOOT_MODEL = ("来源 frocloud-DIYPCmarket（CC BY 4.0）"
              "· 月度档案，非日内行情 · 数据截止 %s")
#: 每日行情（ZOL 公开报价）：口径必须写「每日抓取 + 仅供参考」，不许写成实时成交价
FOOT_DAILY = "来源 ZOL 中关村在线公开报价 · 每日 %s 自动抓取 · 仅供参考"
FOOT_DAILY_MODEL = "来源 ZOL 公开报价 · %s · 当日样本最低价 · 仅供参考"
#: 卡面「口径必现词」与「严禁词」（门禁读实际绘制文本核对）
MUST_SAY = ("月度", "非日内")
FORBID = ("今日", "实时行情", "当天行情", "盘中")


def _tb(pos, text, size, role="regular", anchor="lm"):
    """文本真实包围盒（与绘制同源度量）。"""
    tw, th = Canvas.text_size(str(text), size, role)
    x, y = pos
    if anchor == "lm":
        return (x, y - th // 2, x + tw, y + th // 2)
    if anchor == "rm":
        return (x - tw, y - th // 2, x, y + th // 2)
    if anchor == "mm":
        return (x - tw // 2, y - th // 2, x + tw // 2, y + th // 2)
    return (x, y, x + tw, y + th)  # la


def _rec(boxes, key, box):
    if boxes is not None:
        boxes.append((key, int(box[0]), int(box[1]), int(box[2]), int(box[3])))


def _money(v, unit="元"):
    """价格文本：元 → ¥4,750；元/GB → ¥70.30/GB（单位必须跟着走，绝不省略）。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if str(unit).startswith("元/GB"):
        return "¥%.2f/GB" % v
    return "¥" + ("{:,.0f}".format(v) if float(v).is_integer() else "{:,.2f}".format(v))


def _pct(p):
    return "—" if p is None else "%+.1f%%" % p


def _tone(p, t):
    if p is None or abs(p) < 0.05:
        return t.text_sub
    return t.err if p > 0 else t.ok


def _arrow(p):
    if p is None or abs(p) < 0.05:
        return "·"
    return "▲" if p > 0 else "▼"


# ---------------------------------------------------------------------------
# 布局（先算坐标 → 再画；高度与内容同源）
# ---------------------------------------------------------------------------
def _overview_layout(pad: int, n1: int, n2: int, notes: bool) -> dict:
    y = pad + 42
    L = {"title": y, "h": 0}
    y += 38
    L["sub"] = y
    y += 20 + GAP_M
    L["panels"] = y
    y += PANEL_H + GAP_L
    L["sec1"] = y
    L["rows1"] = y + SEC_TO_ROW
    y = L["rows1"] + n1 * ROW_H + 20
    L["sec2"] = y
    L["rows2"] = y + SEC_TO_ROW
    y = L["rows2"] + n2 * ROW_H
    L["note"] = y + 14 if notes else None
    if notes:
        y += 30
    L["foot"] = y + 16
    L["h"] = L["foot"] + 28 + pad
    return L


def _model_layout(pad: int, has_tier: bool) -> dict:
    y = pad + 42
    L = {"title": y}
    y += 38
    L["sub"] = y
    y += 20 + GAP_M
    L["price"] = y
    L["price_note"] = y + 58
    L["metric"] = y + 96
    L["chart"] = L["metric"] + 62 + GAP_L
    L["tier"] = L["chart"] + CHART_H + GAP_L
    L["foot"] = L["tier"] + (64 if has_tier else 40) + GAP_L
    L["h"] = L["foot"] + 28 + pad
    return L


# ---------------------------------------------------------------------------
# 视图一：总览
# ---------------------------------------------------------------------------
def _panel(c, t, box, b, boxes, idx):
    x0, y0, x1, y1 = box
    c.rounded_rect(box, radius=t.r_ctrl, fill=t.track)
    _rec(boxes, "stat.%d" % idx, box)
    name = "%s · %s" % (b["name"], b["latest"])
    c.text((x0 + 14, y0 + 24), name, size=t.fs(FS_PANEL_NAME), color=t.accent,
           role="bold", anchor="lm")
    _rec(boxes, "stat.%d.name" % idx,
         _tb((x0 + 14, y0 + 24), name, t.fs(FS_PANEL_NAME), "bold"))

    cx, ny = x0 + 14, y0 + 60
    for num, tag, color in ((b["up"], "涨", t.err), (b["down"], "跌", t.ok),
                            (b["flat"], "平", t.text_sub)):
        s = str(num)
        c.text((cx, ny), s, size=t.fs(FS_NUM), color=color, role="bold", anchor="lm")
        _rec(boxes, "stat.%d.num.%s" % (idx, tag), _tb((cx, ny), s, t.fs(FS_NUM), "bold"))
        cx += Canvas.text_width(s, t.fs(FS_NUM), "bold") + 2
        c.text((cx, ny + 8), tag, size=t.fs(15), color=t.text_sub, anchor="lm")
        _rec(boxes, "stat.%d.tag.%s" % (idx, tag),
             _tb((cx, ny + 8), tag, t.fs(15)))
        cx += Canvas.text_width(tag, t.fs(15)) + 12

    cover = "%s→%s · %d/%d 可比" % (b["prev"][5:], b["latest"][5:],
                                    b["matched"], b["active"])
    c.text((x0 + 14, y1 - 18), cover, size=t.fs(13), color=t.text_weak, anchor="lm")
    _rec(boxes, "stat.%d.cover" % idx,
         _tb((x0 + 14, y1 - 18), cover, t.fs(13)))


def _mover_row(c, t, x, y, width, m, up, boxes, key):
    color = t.err if up else t.ok
    c.rounded_rect((x, y - 12, x + 4, y + 12), radius=2, fill=color)
    _rec(boxes, key + ".bar", (x, y - 12, x + 4, y + 12))

    max_w = int(width * 0.44)
    label = Canvas.ellipsis(str(m["label"]), t.fs(FS_ROW), "bold", max_w)
    c.text((x + 16, y), label, size=t.fs(FS_ROW), color=t.text, role="bold", anchor="lm")
    _rec(boxes, key + ".label", _tb((x + 16, y), label, t.fs(FS_ROW), "bold"))

    pct = "%s %s" % (_arrow(m["pct"]), _pct(m["pct"]))
    px = x + int(width * 0.60)
    c.text((px, y), pct, size=t.fs(FS_PCT), color=color, role="bold", anchor="rm")
    _rec(boxes, key + ".pct", _tb((px, y), pct, t.fs(FS_PCT), "bold", "rm"))

    trail = "%s → %s" % (_money(m["prev"], m["unit"]), _money(m["latest"], m["unit"]))
    c.text((x + width, y), trail, size=t.fs(FS_PRICE), color=t.text_sub, anchor="rm")
    _rec(boxes, key + ".price", _tb((x + width, y), trail, t.fs(FS_PRICE), anchor="rm"))


def _section(c, t, x, y, width, text, boxes, key):
    c.rounded_rect((x, y - 9, x + 4, y + 9), radius=2, fill=t.accent)
    c.text((x + 12, y), text, size=t.fs(FS_SEC), color=t.accent, role="bold", anchor="lm")
    _rec(boxes, key + ".title", _tb((x + 12, y), text, t.fs(FS_SEC), "bold"))
    lx = x + 12 + Canvas.text_width(text, t.fs(FS_SEC), "bold") + 12
    c.line((lx, y, x + width, y), fill=t.divider, width=1)
    _rec(boxes, key + ".line", (lx, y - 1, x + width, y + 1))


def build_overview(t: Theme, payload: dict, width: int, boxes=None) -> Canvas:
    pad = int(getattr(t, "pad", PAD) or PAD)
    x0, x1 = pad * 2, width - pad * 2
    cw = x1 - x0
    buckets = payload["buckets"]
    gainers = payload.get("gainers") or []
    losers = payload.get("losers") or []
    notes = payload.get("notes") or []
    L = _overview_layout(pad, len(gainers), len(losers), bool(notes))
    h = L["h"]
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="hw")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    title = "DIY 硬件行情"
    c.text((x0, L["title"]), title, size=t.fs(FS_TITLE), color=t.text, role="bold", anchor="lm")
    _rec(boxes, "hdr.title", _tb((x0, L["title"]), title, t.fs(FS_TITLE), "bold"))
    month = str(payload.get("head_month") or "")
    c.text((x1, L["title"]), month, size=t.fs(24), color=t.accent, role="bold", anchor="rm")
    _rec(boxes, "hdr.month", _tb((x1, L["title"]), month, t.fs(24), "bold", "rm"))

    sub = "上游月度档案 · 非日内行情 · 数据截止 %s（%d 天前）" % (payload["asof"], payload["days_old"])
    c.text((x0, L["sub"]), sub, size=t.fs(FS_SUB), color=t.text_sub, anchor="lm")
    _rec(boxes, "hdr.sub", _tb((x0, L["sub"]), sub, t.fs(FS_SUB)))

    gap = GAP_M
    pw = (cw - gap * (len(buckets) - 1)) // len(buckets)
    for i, b in enumerate(buckets):
        bx = x0 + i * (pw + gap)
        _panel(c, t, (bx, L["panels"], bx + pw, L["panels"] + PANEL_H), b, boxes, i)

    _section(c, t, x0, L["sec1"], cw, "涨幅榜 · 最新一期对比上一期", boxes, "sec.gain")
    for i, m in enumerate(gainers):
        _mover_row(c, t, x0, L["rows1"] + i * ROW_H + ROW_H // 2, cw, m, True,
                   boxes, "gain.%d" % i)

    _section(c, t, x0, L["sec2"], cw, "跌幅榜 · 最新一期对比上一期", boxes, "sec.lose")
    for i, m in enumerate(losers):
        _mover_row(c, t, x0, L["rows2"] + i * ROW_H + ROW_H // 2, cw, m, False,
                   boxes, "lose.%d" % i)

    if notes and L["note"] is not None:
        note = Canvas.ellipsis("观察：" + "；".join(notes), t.fs(15), "bold", cw)
        c.text((x0, L["note"] + 8), note, size=t.fs(15), color=t.accent,
               role="bold", anchor="lm")
        _rec(boxes, "note", _tb((x0, L["note"] + 8), note, t.fs(15), "bold"))

    foot = FOOT_OVERVIEW
    c.text((x0, L["foot"]), foot, size=t.fs(FS_FOOT), color=t.text_weak, anchor="lm")
    _rec(boxes, "footer", _tb((x0, L["foot"]), foot, t.fs(FS_FOOT)))

    # 本卡信息密度高、内容占满整宽，不画右下角大贴纸（会压住末行价格/页脚）。
    return c


# ---------------------------------------------------------------------------
# 视图二：单型号
# ---------------------------------------------------------------------------
def _chart(c, t, box, chart, color, boxes):
    x0, y0, x1, y1 = box
    c.rounded_rect(box, radius=t.r_ctrl, fill=t.track)
    _rec(boxes, "chart", box)
    inner_x0, inner_x1 = x0 + 16, x1 - 16
    inner_y0, inner_y1 = y0 + 30, y1 - 26
    if len(chart) < 2:
        msg = "历史不足 2 天 · 明日起显示走势"
        c.text(((x0 + x1) // 2, (y0 + y1) // 2), msg, size=t.fs(16),
               color=t.text_weak, anchor="mm")
        _rec(boxes, "chart.empty", _tb(((x0 + x1) // 2, (y0 + y1) // 2), msg, t.fs(16), "mm"))
        return
    prices = [p["p"] for p in chart] or [0.0]
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1.0
    for k in (0.0, 0.5, 1.0):
        gy = int(inner_y1 - k * (inner_y1 - inner_y0))
        c.line((inner_x0, gy, inner_x1, gy), fill=t.divider, width=1)

    def pt(p):
        gx = inner_x0 + int(p["t"] * (inner_x1 - inner_x0))
        gy = inner_y1 - int((p["p"] - lo) / span * (inner_y1 - inner_y0))
        return gx, gy

    prev = None
    for p in chart:
        cur = pt(p)
        if prev is not None:
            c.line((prev[0], prev[1], cur[0], cur[1]), fill=color, width=3)
        prev = cur
    if chart:
        lx, ly = pt(chart[-1])
        c.ellipse((lx - 5, ly - 5, lx + 5, ly + 5), fill=color)

    lab_hi = _money(hi)
    lab_lo = _money(lo)
    c.text((inner_x0, y0 + 16), lab_hi, size=t.fs(13), color=t.text_weak, anchor="lm")
    c.text((inner_x0, inner_y1 + 14), lab_lo, size=t.fs(13), color=t.text_weak, anchor="lm")
    _rec(boxes, "chart.hi", _tb((inner_x0, y0 + 16), lab_hi, t.fs(13)))
    _rec(boxes, "chart.lo", _tb((inner_x0, inner_y1 + 14), lab_lo, t.fs(13)))
    if chart:
        rng = "%s → %s" % (chart[0]["m"], chart[-1]["m"])
        c.text((inner_x1, inner_y1 + 14), rng, size=t.fs(13), color=t.text_weak, anchor="rm")
        _rec(boxes, "chart.range", _tb((inner_x1, inner_y1 + 14), rng, t.fs(13), anchor="rm"))


def _metric(c, t, x, y, width, label, value, color, boxes, key, size=None):
    size = size or t.fs(FS_METRIC)
    c.text((x, y), label, size=t.fs(14), color=t.text_weak, anchor="lm")
    _rec(boxes, key + ".label", _tb((x, y), label, t.fs(14)))
    value = Canvas.ellipsis(value, size, "bold", width - 8)
    c.text((x, y + 24), value, size=size, color=color, role="bold", anchor="lm")
    _rec(boxes, key + ".value", _tb((x, y + 24), value, size, "bold"))


def build_model(t: Theme, payload: dict, width: int, boxes=None) -> Canvas:
    pad = int(getattr(t, "pad", PAD) or PAD)
    x0, x1 = pad * 2, width - pad * 2
    cw = x1 - x0
    tier = payload.get("tier") or {}
    chart = payload.get("chart") or []
    L = _model_layout(pad, bool(tier))
    h = L["h"]
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="hw")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    c.text((x0, L["title"]), "DIY 硬件行情", size=t.fs(FS_TITLE), color=t.text,
           role="bold", anchor="lm")
    _rec(boxes, "hdr.title", _tb((x0, L["title"]), "DIY 硬件行情", t.fs(FS_TITLE), "bold"))
    bucket = str(payload["bucket_name"])
    c.text((x1, L["title"]), bucket, size=t.fs(24), color=t.accent, role="bold", anchor="rm")
    _rec(boxes, "hdr.bucket", _tb((x1, L["title"]), bucket, t.fs(24), "bold", "rm"))

    sub = "%s%s" % (payload["label"], (" · " + payload["sub"]) if payload.get("sub") else "")
    sub = Canvas.ellipsis(sub, t.fs(FS_SUB), "regular", cw)
    c.text((x0, L["sub"]), sub, size=t.fs(FS_SUB), color=t.text_sub, anchor="lm")
    _rec(boxes, "hdr.sub", _tb((x0, L["sub"]), sub, t.fs(FS_SUB)))

    price = _money(payload["latest"], payload["unit"])
    c.text((x0, L["price"]), price, size=t.fs(FS_BIG), color=t.text, role="bold", anchor="la")
    _rec(boxes, "price", _tb((x0, L["price"]), price, t.fs(FS_BIG), "bold", "la"))
    if str(payload["unit"]) != "元" and "/" not in price:
        unit = str(payload["unit"])
        uw = Canvas.text_width(price, t.fs(FS_BIG), "bold") + 10
        c.text((x0 + uw, L["price"] + 26), unit, size=t.fs(18), color=t.text_sub, anchor="la")
        _rec(boxes, "price.unit", _tb((x0 + uw, L["price"] + 26), unit, t.fs(18), anchor="la"))

    tail = "%s 最新" % payload["latest_month"]
    if payload.get("kit_price"):
        tail += " · %dGB 整条 ≈ %s（按元/GB 折算）" % (
            int(payload["kit_gb"]), _money(payload["kit_price"]))
    tail = Canvas.ellipsis(tail, t.fs(15), "regular", cw)
    c.text((x0, L["price_note"]), tail, size=t.fs(15), color=t.text_sub, anchor="lm")
    _rec(boxes, "price.note", _tb((x0, L["price_note"]), tail, t.fs(15)))

    pct = payload.get("pct")
    pcol = _tone(pct, t)
    col_w = cw // 3
    _metric(c, t, x0, L["metric"], col_w, "环比上一期（%s）" % (payload.get("prev_month") or "—"),
            _pct(pct), pcol, boxes, "metric.pct")
    thin = payload.get("hist_days") is not None and payload["hist_days"] < 2
    low_tag = ("较档案最低点（%s）" % payload["low_month"]) if payload.get("low_month") else "较档案最低点"
    _metric(c, t, x0 + col_w, L["metric"], col_w, low_tag,
            "—" if thin else _pct(payload.get("vs_low_pct")), pcol, boxes, "metric.vslow")
    if thin:
        _metric(c, t, x0 + col_w * 2, L["metric"], col_w, "档案区间 高 / 低", "—（明日起）",
                t.text_sub, boxes, "metric.range", size=t.fs(16))
    else:
        _metric(c, t, x0 + col_w * 2, L["metric"], col_w, "档案区间 高 / 低",
                "%s / %s" % (_money(payload.get("high"), payload["unit"]),
                             _money(payload.get("low"), payload["unit"])),
                t.text, boxes, "metric.range", size=t.fs(16))

    _chart(c, t, (x0, L["chart"], x1, L["chart"] + CHART_H), chart, pcol, boxes)

    if tier:
        if payload["bucket"] == "gpu":
            line1 = "跑分 · %s" % (tier.get("vendor") or "GPU")
        else:
            line1 = "天梯 · %s" % (tier.get("tier") or "上游未分级")
        c.text((x0, L["tier"] + 10), line1, size=t.fs(20), color=t.accent,
               role="bold", anchor="lm")
        _rec(boxes, "tier.title", _tb((x0, L["tier"] + 10), line1, t.fs(20), "bold"))
        bits = ([(tier.get("extra") or "")] if payload["bucket"] == "gpu"
                else [tier.get("vendor") or "", tier.get("extra") or ""])
        if tier.get("score_main"):
            bits.append(("CPUMark " if payload["bucket"] == "cpu" else "TimeSpy ")
                        + "{:,.0f}".format(float(tier["score_main"])))
        if tier.get("score_sub"):
            bits.append(("单核 " if payload["bucket"] == "cpu" else "总分 ")
                        + "{:,.0f}".format(float(tier["score_sub"])))
        if tier.get("note"):
            bits.append(tier["note"])
        line2 = Canvas.ellipsis(" · ".join([b for b in bits if b]), t.fs(15), "regular", cw)
        c.text((x0, L["tier"] + 40), line2, size=t.fs(15), color=t.text_sub, anchor="lm")
        _rec(boxes, "tier.detail", _tb((x0, L["tier"] + 40), line2, t.fs(15)))
    else:
        line1 = ("天梯 · 上游档案未提供该品类天梯/跑分" if payload["bucket"] == "mem"
                 else "天梯 · 上游天梯表未收录该型号")
        c.text((x0, L["tier"] + 10), line1, size=t.fs(18), color=t.text_sub, anchor="lm")
        _rec(boxes, "tier.title", _tb((x0, L["tier"] + 10), line1, t.fs(18)))

    if payload.get("source") == "daily":
        foot = FOOT_DAILY_MODEL % payload["asof"]
    else:
        foot = FOOT_MODEL % payload["asof"]
    c.text((x0, L["foot"]), foot, size=t.fs(FS_FOOT), color=t.text_weak, anchor="lm")
    _rec(boxes, "footer", _tb((x0, L["foot"]), foot, t.fs(FS_FOOT)))

    # 本卡信息密度高、内容占满整宽，不画右下角大贴纸（会压住末行价格/页脚）。
    return c



def build(t: Theme, payload: dict | None = None, width: int | None = None, boxes=None) -> Canvas:
    """统一入口：``payload.view`` 决定视图。缺 payload 直接报错，绝不画空卡。"""
    if not payload or not payload.get("view"):
        raise ValueError("hw_market.build 需要 payload（含 view=overview|model）")
    width = int(width or getattr(t, "width", 800) or 800)
    if payload["view"] == "overview":
        return build_overview(t, payload, width, boxes=boxes)
    if payload["view"] == "daily":
        from .hw_market_daily import build_daily  # 懒导入：拆分后守 20KB 门禁
        return build_daily(t, payload, width, boxes=boxes)
    if payload["view"] == "model":
        return build_model(t, payload, width, boxes=boxes)
    raise ValueError("未知 view：%r" % (payload.get("view"),))
