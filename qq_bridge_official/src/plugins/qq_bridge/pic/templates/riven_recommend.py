# -*- coding: utf-8 -*-
"""pic/templates/riven_recommend.py —— 紫卡洗卡词条推荐卡（照 Rabbit 参考卡逐区复刻）。

参考卡 1195×1920（宽高比≈0.62）；本模板设计空间 BASE_W=600、按 width 等比缩放，
使版式比例与参考卡一致。配色（实测）：
    底色 #F3F1ED · 主文字 #1A191C · 金 #9B7414 · 品牌紫 #735787
    表头蓝 #3A6EA5 · 警示红 #C0392B / 底 #FBE9E7 · 溢价绿 #3FA34D · 次级灰 #7F7E7A

版式：
    [标题]   紫色品牌标 + 「紫卡洗卡词条推荐」（居中）
    [警示]   浅红底红框红字「【注意】洗卡先问大佬……」
    [面板]   「凤殁 可能是暴击触发双修武器（灵化）」+ 右灰「基础面板·未装MOD·未计进化」；
             蓝字表头 模式/暴击率/暴击倍率/触发率/射速/弹丸/发/基础伤害/发；普通/灵化两行；
             下方伤害构成。
    [击杀]   魔法棒图标 + 标题；左「正面词条」(词条|收益) 前4，右「推荐负面词条」前3。
    [市场]   三栏：左「正面词条」[T0/T1/T2]+高价/底价出现率；中「推荐负面」+「普通负面（存疑）」；
             右「致命项」（红）。同排「左词条 + 右数值」，长词条换行、数值恒首行右对齐。
    [说明]   注意事项。
    [页脚]   「PC Warframe Rabbit · 需用右键/长按提取文字」+ 右 Q 版插画。

数据纪律：击杀效率来自仓库 riven_card DPS 模型（词条满 roll 边际贡献）；
市场数值来自 Warframe.Market 挂牌分层（高价层 / 底价层各约 100 条）的**出现率**，
不是价差（该接口无全量分页，价差推不出来，硬算等于编数）。接口失败/样本不足如实标注。
"""
from __future__ import annotations

import logging

from .. import fonts
from ..canvas import Canvas
from ..theme import Theme

logger = logging.getLogger("qq_bridge")

BASE_W = 600.0          # 设计空间宽度（参考卡 1195 宽的等比压缩）
DESIGN_H = 2400.0

C_BG = "#F3F1ED"
C_INK = "#1A191C"
C_GOLD = "#9B7414"
C_BRAND = "#735787"
C_BLUE = "#3A6EA5"
C_RED = "#C0392B"
C_REDBG = "#FBE9E7"
C_GREEN = "#3FA34D"
C_GRAY = "#7F7E7A"
C_SUB = "#9C9890"

PAD = 16.0


def _font(role: str, size: int):
    return fonts.load(role, max(4, int(size)))


def _fmt_rate(it: dict) -> str:
    """高价层/底价层出现率（真实统计值）：`73% / 18%`。"""
    return "%d%% / %d%%" % (int(it.get("hi", 0)), int(it.get("lo", 0)))


def build(t: Theme, data: dict | None = None, width: int | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 800) or 800)
    S = width / BASE_W
    c = Canvas(width, int(DESIGN_H * S), C_BG, theme=t)
    d = c.draw

    def P(v: float) -> int:
        return int(round(v * S))

    def T(x, y, text, size, color, role="regular", anchor="ls"):
        d.text((P(x), P(y)), str(text), font=_font(role, size * S), fill=color, anchor=anchor)

    def tw(text, size, role="regular") -> float:
        return c.text_width(str(text), size * S, role) / S

    def wrap(text, size, role, max_w):
        return c.wrap(str(text), size * S, role, P(max_w))

    bot = [0.0]

    def mark(y):
        bot[0] = max(bot[0], float(y))

    # ---------- 错误态 ----------
    if data.get("status") != "ok":
        msg = data.get("msg") or "生成失败"
        T(PAD, 40, "紫卡洗卡词条推荐", 22, C_INK, "bold")
        c.rounded_rect((P(PAD), P(64), P(BASE_W - PAD), P(128)), radius=P(8),
                       fill=C_REDBG, outline=C_RED, width=max(1, P(1.2)))
        for i, ln in enumerate(wrap(msg, 13, "bold", BASE_W - 2 * PAD - 24)[:3]):
            T(PAD + 12, 88 + i * 20, ln, 13, C_RED, "bold")
        mark(140)
        _finish(c, P, bot, width)
        return c

    # ---------- [标题] ----------
    title = "紫卡洗卡词条推荐"
    icon_s, tsize = 22.0, 23
    total_w = icon_s + 9 + tw(title, tsize, "bold")
    sx = (BASE_W - total_w) / 2
    c.rounded_rect((P(sx), P(13), P(sx + icon_s), P(13 + icon_s)), radius=P(4), fill=C_BRAND)
    d.rectangle((P(sx + icon_s * 0.30), P(13 + icon_s * 0.42),
                 P(sx + icon_s * 0.70), P(13 + icon_s)), fill=C_BG)
    T(sx + icon_s + 9, 13 + icon_s * 0.5 + 8, title, tsize, C_INK, "bold")
    mark(46)

    # ---------- [警示] ----------
    alert = ("【注意】洗卡先问大佬，不要直接按推荐顺序洗卡，搭配的战甲、武器流派不同，"
             "词条选择都会不同。")
    al = wrap(alert, 13, "bold", BASE_W - 2 * PAD - 26)
    ah = 16.0 + len(al) * 20.0 + 14.0
    ay = 56.0
    c.rounded_rect((P(PAD), P(ay), P(BASE_W - PAD), P(ay + ah)), radius=P(8),
                   fill=C_REDBG, outline=C_RED, width=max(1, P(1.2)))
    for i, ln in enumerate(al):
        T(PAD + 13, ay + 15 + i * 20, ln, 13, C_RED, "bold")
    mark(ay + ah + 14)

    # ---------- [武器面板] ----------
    py = bot[0] + 4
    is_inc = bool(data.get("is_incarnon"))
    panel = data.get("panel") or {}
    normal = panel.get("normal") or {}
    inc = panel.get("incarnon")

    wtxt = data.get("weapon") or "武器"
    desc = data.get("weapon_desc") or ""
    ttl = wtxt + (" " + desc if desc else "") + ("（灵化）" if is_inc else "")
    T(PAD, py + 17, ttl, 15, C_INK, "bold")
    T(BASE_W - PAD, py + 17, "基础面板 · 未装 MOD · 未计进化", 10.5, C_GRAY, "regular", anchor="rs")

    hy = py + 36
    cols = ["模式", "暴击率", "暴击倍率", "触发率", "射速", "弹丸/发", "基础伤害/发"]
    mode_w = 62.0
    val_l = PAD + mode_w
    colw = (BASE_W - PAD - val_l) / 6.0
    for i, h in enumerate(cols):
        if i == 0:
            T(PAD, hy, h, 10.5, C_BLUE, "bold")
        else:
            T(val_l + colw * (i - 1) + colw / 2, hy, h, 10, C_BLUE, "bold", anchor="ms")
    ry = hy + 21

    def _row(label, vals):
        nonlocal ry
        T(PAD, ry, label, 12.5, C_INK, "regular")
        for i, v in enumerate(vals):
            T(val_l + colw * i + colw / 2, ry, v, 12, C_INK, "regular", anchor="ms")
        ry += 26

    _row("普通", [normal.get("cc", "—"), normal.get("cm", "—"), normal.get("status", "—"),
                  normal.get("fr", "—"), normal.get("pellets", "—"), normal.get("base", "—")])
    if is_inc:
        if inc:
            _row("灵化", [inc.get("cc", "—"), inc.get("cm", "—"), inc.get("status", "—"),
                          inc.get("fr", "—"), inc.get("pellets", "—"), inc.get("base", "—")])
        else:
            _row("灵化", ["未收录"] * 6)

    cy = ry + 3
    T(PAD, cy, "普通：" + (normal.get("comp") or "—"), 10.5, C_GRAY, "regular")
    if is_inc:
        T(PAD, cy + 16, "灵化：" + ((inc or {}).get("comp") if inc else "未收录"), 10.5, C_GRAY, "regular")
        cy += 16
    mark(cy + 15)

    # ---------- [击杀效率] ----------
    ky = bot[0] + 10
    _block_title(T, P, ky, "基于击杀效率计算的推荐词条")
    kb = ky + 30
    ke = data.get("kill_eff") or {}
    pos = ke.get("pos") or []
    neg = ke.get("neg") or []

    T(PAD, kb, "正面词条", 12.5, C_GOLD, "bold")
    T(PAD, kb + 18, "词条", 10, C_GRAY, "regular")
    T(BASE_W * 0.45, kb + 18, "收益", 10, C_GRAY, "regular", anchor="rs")
    ly = kb + 36
    for i, it in enumerate(pos[:4]):
        T(PAD, ly, "[%d]%s" % (i + 1, it.get("zh", "")), 12, C_INK, "regular")
        T(BASE_W * 0.45, ly, "+%.1f%%" % float(it.get("gain", 0.0)), 11.5, C_GREEN, "bold", anchor="rs")
        ly += 23
    if not pos:
        # 无紫卡类别（不在 riven_disp 表内的新武器等）：如实写原因，不留空白列表
        for ln in wrap(ke.get("na") or "该武器暂无可用词条数据", 10.5, "regular",
                       BASE_W * 0.48)[:2]:
            T(PAD, ly, ln, 10.5, C_GRAY, "regular")
            ly += 16

    rx = BASE_W * 0.55
    T(rx, kb, "推荐负面词条", 12.5, C_GOLD, "bold")
    T(rx, kb + 18, "词条", 10, C_GRAY, "regular")
    ny = kb + 36
    for i, z in enumerate(neg[:3]):
        T(rx, ny, "[%d]%s" % (i + 1, z), 12, C_INK, "regular")
        ny += 23
    mark(max(ly, ny) + 8)

    # ---------- [市场挂牌分层] ----------
    my = bot[0] + 12
    _block_title(T, P, my, "基于市场挂牌分层的推荐词条")
    mb = my + 30
    market = data.get("market") or {}

    col1_x, col1_w = PAD, 190.0
    col2_x, col2_w = 226.0, 170.0
    col3_x, col3_w = 416.0, 168.0
    fs_name, fs_price, fs_conf, lh = 11.0, 10.0, 9.5, 18.0

    def mkt_item(cx, cw, y, name, rate, name_color, rate_color):
        """同排「左词条 + 右出现率」；词条过长换行，出现率恒首行右对齐。"""
        pw = tw(rate, fs_price, "bold")
        name_max = max(cw * 0.40, cw - pw - 6)
        lines = wrap(name, fs_name, "regular", name_max) or [name]
        T(cx + cw, y, rate, fs_price, rate_color, "bold", anchor="rs")
        for j, ln in enumerate(lines[:2]):
            T(cx, y + j * lh, ln, fs_name, name_color, "regular")
        return max(1, min(2, len(lines))) * lh

    # 左栏 正面词条
    _has_rows = any(market.get(k) for k in ("mpos", "rec_neg", "ord_neg", "deadly"))
    T(col1_x, mb, "正面词条", 12.5, C_GOLD, "bold")
    T(col1_x, mb + 18, "词条", 10, C_GRAY, "regular")
    T(col1_x + col1_w, mb + 18, "高价/底价", 10, C_GRAY, "regular", anchor="rs")
    y1 = mb + 38
    if not market.get("ok"):
        for ln in wrap(market.get("err") or "暂无行情", 10.5, "regular", col1_w * 3)[:2]:
            T(col1_x, y1 + 4, ln, 10.5, C_GRAY, "regular")
            y1 += 16
        y1 += 10
    elif not _has_rows:
        # 有行情但两片分布接近：如实说明，不留三个空表头
        for ln in wrap("该武器高价／底价两片挂牌的词条构成接近（无显著分层差异），"
                       "本维度暂无推荐；行情见下方样本行", 10.5, "regular", col1_w * 3)[:2]:
            T(col1_x, y1 + 4, ln, 10.5, C_GRAY, "regular")
            y1 += 16
        y1 += 10
    else:
        for it in market.get("mpos") or []:
            name = "[%s]%s" % (it.get("tier", ""), it.get("zh", ""))
            y1 += mkt_item(col1_x, col1_w, y1, name, _fmt_rate(it), C_GOLD, C_INK)

    # 中栏 推荐负面 + 普通负面
    y2 = y1
    if _has_rows:
        T(col2_x, mb, "推荐负面", 12.5, C_GOLD, "bold")
        T(col2_x, mb + 18, "词条", 10, C_GRAY, "regular")
        T(col2_x + col2_w, mb + 18, "高价/底价", 10, C_GRAY, "regular", anchor="rs")
        y2 = mb + 38
        for it in market.get("rec_neg") or []:
            y2 += mkt_item(col2_x, col2_w, y2, "-" + it.get("zh", ""), _fmt_rate(it), C_INK, C_INK)
        if market.get("ord_neg"):
            T(col2_x, y2 + 6, "普通负面（存疑）", 11.5, C_GRAY, "bold")
            y2 += 26
            T(col2_x, y2, "词条", 10, C_GRAY, "regular")
            T(col2_x + col2_w, y2, "高价/底价", 10, C_GRAY, "regular", anchor="rs")
            y2 += 18
            for it in (market.get("ord_neg") or [])[:4]:
                y2 += mkt_item(col2_x, col2_w, y2, "-" + it.get("zh", ""), _fmt_rate(it), C_INK, C_INK)
                T(col2_x, y2, "(置信度 %d%%)" % int(it.get("conf", 0)), fs_conf, C_SUB, "regular")
                y2 += 14

    # 右栏 致命项
    y3 = y1
    if _has_rows:
        T(col3_x, mb, "致命项", 12.5, C_RED, "bold")
        T(col3_x, mb + 18, "词条", 10, C_GRAY, "regular")
        T(col3_x + col3_w, mb + 18, "高价/底价", 10, C_GRAY, "regular", anchor="rs")
        y3 = mb + 38
        for it in (market.get("deadly") or [])[:6]:
            name = "%s%s" % (it.get("sign", "-"), it.get("zh", ""))
            y3 += mkt_item(col3_x, col3_w, y3, name, _fmt_rate(it), C_RED, C_RED)
    mark(max(y1, y2, y3) + 8)

    # ---------- [说明] ----------
    ny2 = bot[0] + 10
    for note in data.get("notes") or []:
        for ln in wrap(note, 10.5, "regular", BASE_W - 2 * PAD)[:2]:
            T(PAD, ny2, ln, 10.5, C_GRAY, "regular")
            ny2 += 15
        ny2 += 3
    mark(ny2 + 2)

    # ---------- [页脚] ----------
    fy = bot[0] + 6
    T(PAD, fy, "PC Warframe Rabbit · 需用右键/长按提取文字", 10.5, C_GRAY, "regular")
    _q_version(c, P, BASE_W - PAD - 46, fy - 24, 46)
    mark(fy + 30)

    _finish(c, P, bot, width)
    return c


# ---------------------------------------------------------------------------
# 局部绘制
# ---------------------------------------------------------------------------
def _finish(c: Canvas, P, bot, width: int) -> None:
    c.img = c.img.crop((0, 0, width, max(20, P(bot[0]) + 12)))


def _block_title(T, P, y: float, text: str) -> None:
    """区块标题：金色魔法棒图标（✦）+ 金色加粗标题。"""
    T(PAD, y + 11, "✦", 15, C_GOLD, "bold")
    T(PAD + 19, y + 11, text, 15, C_GOLD, "bold")


def _q_version(c: Canvas, P, x: float, y: float, sz: float) -> None:
    """右下角 Q 版插画占位（棕发粉裙、坐在粉色坐垫上，几何近似）。"""
    s = sz
    c.rounded_rect((P(x), P(y + s * 0.74), P(x + s), P(y + s)), radius=P(7), fill="#E9BFD2")
    c.ellipse((P(x + s * 0.16), P(y + s * 0.16), P(x + s * 0.38), P(y + s * 0.62)), fill="#8A5A3B")
    c.ellipse((P(x + s * 0.62), P(y + s * 0.16), P(x + s * 0.84), P(y + s * 0.62)), fill="#8A5A3B")
    c.ellipse((P(x + s * 0.26), P(y + s * 0.44), P(x + s * 0.74), P(y + s * 0.90)), fill="#E6A6C2")
    c.ellipse((P(x + s * 0.32), P(y + s * 0.10), P(x + s * 0.68), P(y + s * 0.48)), fill="#F6DFCB")
    c.ellipse((P(x + s * 0.30), P(y + s * 0.06), P(x + s * 0.70), P(y + s * 0.30)), fill="#8A5A3B")
    c.ellipse((P(x + s * 0.42), P(y + s * 0.28), P(x + s * 0.47), P(y + s * 0.33)), fill="#3A2A33")
    c.ellipse((P(x + s * 0.53), P(y + s * 0.28), P(x + s * 0.58), P(y + s * 0.33)), fill="#3A2A33")
