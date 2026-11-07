# -*- coding: utf-8 -*-
"""pic/templates/checkin.py —— 每日签到卡片（xAI / Grok 风 Brutalism 重做）。

设计语言（对标 xAI · Grok · X 深色模式 —— 年轻人喜欢的"反消费主义"冷峻极客风）：
  * **粗野主义 + 极简**：深灰底(#1b1d22) + 纯白字 + 等宽字体(数字/ASCII) + 锐利直角(无圆角)；
  * **赛博 / 真实感**：无插画、无渐变、无萌系图标，用方括号 / 斜杠 / 方块进度条等终端符号；
  * 终端机 / 编辑器气质：行首 `>` `//` 提示符、`[ OK ]` 状态标签、`+` 奖励前缀、`█░` 进度条、
    7 列等宽月历；圆润可爱消费级 App 一律不要。
  * 中文用系统无衬线(微软雅黑，本身中性不萌)，拉丁/数字/符号用 Consolas 等宽——
    逐字符择字体，一行内中英混排也等宽对齐。

逻辑层(连签 / 累计 / 里程碑 / 补签 / 排行榜)沿用 manager，未改动。
"""
from __future__ import annotations

import math
from datetime import datetime
from PIL import ImageDraw, ImageFont

from ..canvas import Canvas, rgb
from .. import decor, fonts as _fonts

_MILESTONES = ((3, 30), (7, 80), (15, 200), (30, 500))
_MILE_MAX = 30
_MILE_BONUS = dict(_MILESTONES)


# ------------------------------------------------------------------ 字体
# 等宽字体仅覆盖 ASCII/数字；中文回退系统无衬线。逐字符择字体实现中英混排等宽对齐。
_MONO_CACHE: dict = {}


def _mono_font(size: int, bold: bool = False):
    key = (int(size), bool(bold))
    f = _MONO_CACHE.get(key)
    if f is not None:
        return f
    name = "consolab.ttf" if bold else "consola.ttf"
    try:
        f = ImageFont.truetype(str(_fonts._SYSTEM_DIR / name), int(size))
    except Exception:
        f = _fonts.load("bold" if bold else "regular", int(size))
    _MONO_CACHE[key] = f
    return f


def _cjk_font(size: int, bold: bool = False):
    return _fonts.load("bold" if bold else "regular", int(size))


def _mt(c: Canvas, x: int, y: int, text: str, size: int, color: str,
        anchor: str = "la", bold: bool = False) -> None:
    """逐字符择字体绘制（ASCII/数字→Consolas 等宽，中文→系统无衬线）。

    y 为文字**顶部**；anchor 仅控制水平：'la'(左) / 'mm'(中) / 'ra'(右)。
    """
    mono = _mono_font(size, bold)
    cjk = _cjk_font(size, bold)
    runs = []
    for ch in text:
        f = mono if ord(ch) < 128 else cjk
        runs.append((ch, f))
    total = sum(f.getlength(ch) for ch, f in runs)
    if anchor == "mm":
        cur = x - total / 2
    elif anchor == "ra":
        cur = x - total
    else:
        cur = x
    for ch, f in runs:
        c.draw.text((cur, y), ch, font=f, fill=rgb(color) + (255,))
        cur += f.getlength(ch)


def _mtw(text: str, size: int, bold: bool = False) -> int:
    """_mt 渲染后的总宽度（用于右接元素定位）。"""
    mono = _mono_font(size, bold)
    cjk = _cjk_font(size, bold)
    return int(round(sum((mono if ord(ch) < 128 else cjk).getlength(ch) for ch in text)))


def _vtop(size: int, cy: int) -> int:
    """给定垂直中心 cy，返回混排文字的 top y（按中文行高居中）。"""
    asc, desc = _cjk_font(size).getmetrics()
    return int(cy - (asc + desc) / 2)


def _month_matrix(year: int, month: int):
    first_wd = datetime(year, month, 1).weekday()      # Mon=0
    offset = (first_wd + 1) % 7                         # 转周日为首列
    if month != 12:
        nxt = datetime(year, month + 1, 1)
    else:
        nxt = datetime(year + 1, 1, 1)
    dim = (nxt - datetime(year, month, 1)).days
    return offset, dim


def build(
    t: "object",
    name: str = "你",
    res: dict | None = None,
    prof: dict | None = None,
    cal: dict | None = None,
    rank: int | None = None,
    width: int | None = None,
) -> Canvas:
    res = res or {}
    prof = prof or {}
    cal = cal or {}
    name = (name or "你").strip() or "你"
    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2
    inner = width - pad_in * 2

    streak = int(prof.get("streak") or 0)
    total = int(prof.get("total_days") or 0)
    points = int(prof.get("points") or 0)
    gain = int(res.get("gain") or 0)
    already = bool(prof.get("already")) or res.get("status") == "already"
    nxt = prof.get("next_milestone")   # (mday, mbonus, remaining)

    today_for_cal = str(cal.get("today") or "")
    cal_year = cal.get("year")
    cal_month = cal.get("month")
    days = cal.get("days") or {}
    has_cal = bool(cal_year and cal_month)
    in_month = has_cal and today_for_cal.startswith(
        "%04d-%02d-" % (int(cal_year), int(cal_month)))
    today_day = int(today_for_cal.split("-")[2]) if today_for_cal else -1

    # 奖励明细
    reward_items = []
    if gain > 0:
        daily = int(res.get("daily") or 0)
        sb = int(res.get("streak_bonus") or 0)
        mb = int(res.get("milestone_bonus") or 0)
        reward_items.append(("每日基础签到", "+%d" % daily, False))
        if sb > 0:
            reward_items.append(("连签阶梯加成", "+%d" % sb, False))
        if mb > 0:
            reward_items.append(("里程碑达成", "+%d" % mb, True))
    else:
        if already:
            reward_items.append(("今日已领取，明天再来", "", False))
        else:
            reward_items.append(("完成 /签到 即可领取喵币", "", False))
    n_reward = len(reward_items)

    # 月历行数
    rows = 0
    if has_cal:
        off, dim = _month_matrix(int(cal_year), int(cal_month))
        rows = (off + dim + 6) // 7

    # ===== 布局高度预算（单一来源）=====
    HDR = 80
    GAP = 16
    STATUS = 32
    STAT = 96
    REWARD_H = 26 + n_reward * 28 + 6
    MST = 110
    CAL_H = 30 + 22 + rows * 26 + (8 if rows else 0) if has_cal else 0
    FOOTER = 26
    content_bottom = (pad + HDR + GAP + STATUS + GAP + STAT + GAP + REWARD_H
                      + GAP + MST + GAP + CAL_H + GAP + FOOTER)
    footer_y = content_bottom + 14
    h = footer_y + 48

    # 配色（来自主题，保证可被主题编辑器调）
    bg = t.bg
    card = t.card
    border = t.card_border
    text = t.text
    sub = t.text_sub
    weak = t.text_weak
    accent = t.accent
    track = t.track
    div = t.divider
    ok = t.ok
    warn = t.warn

    c = Canvas(width, h, bg, theme=t)
    decor.background(c, t, tpl="checkin")
    # 粗野主义：一张主卡片框（锐利直角），无背景水印、无圆角萌系装饰
    c.rounded_rect((pad, pad, width - pad, h - pad), radius=0, fill=card, outline=border, width=1)
    c.add_region("card", [pad, pad, width - pad, h - pad], label="卡片底色", group="背景",
                 tip="点击修改卡片底色")

    y = pad

    # ============ 头部终端框 ============
    hb = (pad, y, width - pad, y + HDR)
    c.rounded_rect(hb, radius=0, outline=border, width=1)
    title_cy = y + 32
    _mt(c, pad + 16, _vtop(30, title_cy), "> 每日签到", 30, text, anchor="la", bold=True)
    c.add_region("text", [pad + 16, title_cy - 18, pad + 16 + _mtw("> 每日签到", 30, True),
                          title_cy + 18],
                 label="标题", group="文字", tip="点击修改正文色")
    sub_cy = y + 62
    _mt(c, pad + 16, _vtop(16, sub_cy), "// %s 的签到小屋" % name, 16, sub, anchor="la")
    if rank:
        ry = _vtop(20, y + 40)
        _mt(c, width - pad - 16, ry, "RANK #%02d" % int(rank), 20, accent, anchor="ra", bold=True)
        c.add_region("accent", [width - pad - 16 - _mtw("RANK #%02d" % int(rank), 20, True),
                                ry, width - pad - 16, ry + 20],
                     label="右上角名次", group="主色", tip="点击修改主色")
    y += HDR + GAP

    # ============ 状态行（终端状态标签）============
    if gain > 0:
        tag, msg, tagcol = "[ OK ]", "签到成功 · 今日 +%d 喵币" % gain, ok
    elif already:
        tag, msg, tagcol = "[ OK ]", "今日已签到 · 明日再来喵", text
    else:
        tag, msg, tagcol = "[ .. ]", "尚未签到 · 速去 /签到 领奖励", warn
    sy = _vtop(20, y + STATUS / 2)
    _mt(c, pad + 16, sy, tag, 20, tagcol, anchor="la", bold=True)
    _mt(c, pad + 16 + _mtw(tag, 20, True) + 10, sy, msg, 20, text, anchor="la")
    y += STATUS + GAP

    # ============ 数据三联表（锐利边框 + 等宽数字）============
    sy = y
    table_box = (pad_in, sy, width - pad_in, sy + STAT)
    c.rounded_rect(table_box, radius=0, outline=border, width=1)
    col_w = inner / 3.0
    for i in (1, 2):
        xx = pad_in + col_w * i
        c.line((xx, sy, xx, sy + STAT), fill=border, width=1)
    cells = [
        ("连签", "%03d" % streak, "DAYS"),
        ("累计", "%03d" % total, "DAYS"),
        ("喵币", "%04d" % points, ("+%d" % gain) if gain > 0 else "----"),
    ]
    for i, (lab, num, sub_txt) in enumerate(cells):
        cx = pad_in + col_w * (i + 0.5)
        _mt(c, cx, sy + 14, lab, 16, sub, anchor="mm")
        _mt(c, cx, _vtop(34, sy + 52), num, 34, text, anchor="mm", bold=True)
        c.add_region("text", [cx - col_w / 2 + 6, sy + 34, cx + col_w / 2 - 6, sy + 70],
                     label="数值", group="文字", tip="点击修改正文色")
        scol = ok if (i == 2 and gain > 0) else weak
        _mt(c, cx, sy + 78, sub_txt, 14, scol, anchor="mm", bold=(i == 2 and gain > 0))
    y += STAT + GAP

    # ============ 今日奖励（终端列表）============
    _mt(c, pad + 16, _vtop(16, y), "// 今日奖励", 16, text, anchor="la", bold=True)
    ry0 = y + 26
    for i, (lab, txt, rare) in enumerate(reward_items):
        ry_i = ry0 + i * 28
        col = warn if rare else ok
        _mt(c, pad + 16, _vtop(17, ry_i + 14), "+", 17, col, anchor="la", bold=True)
        _mt(c, pad + 16 + 20, _vtop(17, ry_i + 14), lab, 17, text, anchor="la")
        if rare:
            lw = _mtw(lab, 17)
            _mt(c, pad + 16 + 20 + lw + 10, _vtop(14, ry_i + 14), "[RARE]", 14, col,
                 anchor="la", bold=True)
        if txt:
            _mt(c, width - pad - 16, _vtop(17, ry_i + 14), txt, 17, col, anchor="ra", bold=True)
    y += REWARD_H + GAP

    # ============ 里程碑（方块进度条 + 终端图例）============
    _mt(c, pad + 16, _vtop(16, y), "// 里程碑  %d/%d" % (min(total, _MILE_MAX), _MILE_MAX),
        16, text, anchor="la", bold=True)
    bar_x0 = pad_in
    bar_x1 = width - pad_in
    bar_y = y + 36
    bar_h = 14
    c.rounded_rect((bar_x0, bar_y, bar_x1, bar_y + bar_h), radius=0, fill=track)
    frac = min(total / _MILE_MAX, 1.0)
    if frac > 0:
        c.rounded_rect((bar_x0, bar_y, bar_x0 + (bar_x1 - bar_x0) * frac, bar_y + bar_h),
                       radius=0, fill=accent)
    # 里程碑刻度
    for m, _ in _MILESTONES:
        tx = bar_x0 + (m / _MILE_MAX) * (bar_x1 - bar_x0)
        c.line((tx, bar_y - 4, tx, bar_y + bar_h + 4), fill=div, width=1)
    # 当前位置标记（粗野主义：竖线指针，非萌系小猫）
    px = bar_x0 + (min(total, _MILE_MAX) / _MILE_MAX) * (bar_x1 - bar_x0)
    c.line((px, bar_y - 7, px, bar_y + bar_h + 7), fill=accent, width=2)
    # 图例
    leg_y = bar_y + bar_h + 18
    lx = pad_in
    for m, bonus in _MILESTONES:
        reached = total >= m
        is_next = (not reached) and (nxt and m == int(nxt[0]))
        token = ("*%d:+%d" % (m, bonus)) if reached else (
            ">%d:+%d" % (m, bonus) if is_next else "%d:+%d" % (m, bonus))
        col = ok if reached else (accent if is_next else weak)
        _mt(c, lx, _vtop(14, leg_y), token, 14, col, anchor="la", bold=is_next)
        lx += _mtw(token, 14, is_next) + 14
    if nxt:
        mday, mbn, rem = nxt
        _mt(c, width - pad - 16, _vtop(14, leg_y), "还差%d天 +%d" % (rem, mbn), 14, accent,
            anchor="ra")
    y += MST + GAP

    # ============ 本月月历（7 列等宽终端日历，替代萌系圆点）============
    if has_cal:
        _mt(c, pad + 16, _vtop(16, y), "// 本月足迹", 16, text, anchor="la", bold=True)
        off, dim = _month_matrix(int(cal_year), int(cal_month))
        cal_top = y + 30
        cell_w = inner / 7.0
        cell_h = 22
        # 星期表头
        for wd in range(7):
            cx = pad_in + cell_w * (wd + 0.5)
            _mt(c, cx, cal_top, "日一二三四五六"[wd], 14, weak, anchor="mm")
        for d in range(1, dim + 1):
            idx = off + (d - 1)
            col = idx % 7
            row = idx // 7
            cx = pad_in + cell_w * (col + 0.5)
            cy_top = cal_top + 22 + row * cell_h
            daykey = "%04d-%02d-%02d" % (int(cal_year), int(cal_month), d)
            checked = daykey in days
            is_today = in_month and d == today_day
            box = (cx - cell_w / 2 + 3, cy_top, cx + cell_w / 2 - 3, cy_top + cell_h)
            if is_today and checked:
                c.rounded_rect(box, radius=0, fill=text)      # 反白：白底深字
                c.rounded_rect(box, radius=0, outline=accent, width=2)
                _mt(c, cx, _vtop(15, cy_top + cell_h / 2), "%02d" % d, 15, bg,
                    anchor="mm", bold=True)
            elif is_today:
                c.rounded_rect(box, radius=0, outline=accent, width=2)
                _mt(c, cx, _vtop(15, cy_top + cell_h / 2), "%02d" % d, 15, accent,
                    anchor="mm", bold=True)
            elif checked:
                c.rounded_rect(box, radius=0, fill=text)      # 反白：白底深字
                _mt(c, cx, _vtop(15, cy_top + cell_h / 2), "%02d" % d, 15, bg,
                    anchor="mm", bold=True)
            elif in_month and d < today_day:
                c.rounded_rect(box, radius=0, outline=sub, width=1)   # 漏签：清晰中灰描边
                _mt(c, cx, _vtop(15, cy_top + cell_h / 2), "%02d" % d, 15, sub, anchor="mm")
            else:
                _mt(c, cx, _vtop(15, cy_top + cell_h / 2), "%02d" % d, 15, weak, anchor="mm")
        y += CAL_H + GAP

    # ============ 页脚 ============
    _mt(c, pad + 16, _vtop(15, footer_y),
        "> 连续签到别断签 · 漏签 /签到 补 耗喵币", 15, weak, anchor="la")
    return c
