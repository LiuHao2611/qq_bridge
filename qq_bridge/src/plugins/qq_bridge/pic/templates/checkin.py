# -*- coding: utf-8 -*-
"""每日签到卡（浅色 Rabbit 风 + 黄金图标 + 分层字体）。

浅色主卡 +「签到」+ 三态横幅 + 2×2 胶囊 + 7 列月历 + 里程碑条 + 奖励 + 页脚。
只展示 manager 真实字段（连签/累计/喵币/名次/里程碑），绝不编造 Rabbit 专属字段
（群段位/经验值/群萝卜/抽中礼品盒）；配色圆角全走主题，brutalist 向后兼容。
图标走 checkin_gold_icons 真实黄金 PNG，字体走 checkin_font 分层（三款风格字+
昵称回退雅黑），绘制度量同源；build(boxes=...) 登记叶子盒供门禁查溢出/重叠/间距。
视觉规范（字号层级/间距 token/数值右对齐/0 进度竖线）见 AGENTS.md §15:0x。
"""
from __future__ import annotations

from ..canvas import Canvas
from .. import decor
# 版式常量/度量辅助拆到 parts（主模板守 <20KB 棘轮）
from .checkin_parts import (
    PAD, GAP, CONTENT_PAD, HDR_H, FS_TITLE, FS_SUB, FS_RANK,
    BANNER_H, BANNER_BAR, FS_BANNER_T, FS_BANNER_M,
    FS_SEC, SEC_TITLE_H, STATUS_CARD_PAD, PILL_H, PILL_GAP_X, PILL_GAP_Y,
    FS_PILL_LAB, FS_PILL_VAL, PILL_ICON,
    MILE_BAR_H, MILE_LEGEND_H, MILE_LEGEND_GAP, FS_LEGEND,
    REWARD_PILL_H, REWARD_PILL_GAP, FS_REWARD,
    WD_H, DAY_H, FS_WD, FS_DAY,
    NOTE_LINE_H, NOTE_GAP, FS_NOTE, FOOTER_H, FS_FOOT, PC_W, PC_H,
    _MILESTONES, _MILE_MAX,
    _blend, _w, _rec, _tb, _month_matrix,
)
# 黄金图标（真实 PNG，缺图 gate）+ 分层字体（wrap 后 c.text 走风格字，绘制度量同源）
from . import checkin_font as _cf, checkin_gold_icons as _gold


def _pill(c: Canvas, t, box, label: str, value: str, icon: str,
          accent: str, boxes, key: str) -> None:
    """画一枚圆角胶囊：浅底 + 几何图标 + 标签 + 右对齐数值。

    登记三类真实包围盒供门禁量化「不贴边」：
      * ``<key>``            整枚胶囊（父子容器不参与重叠比对，见门禁 label 过滤）
      * ``<key>.label``      标签文字
      * ``<key>.value``      数值文字（右基线 x1-PAD_VAL，实测与右边框留白）
    """
    PAD_VAL = 16                      # 数值距胶囊右边缘留白（4 倍数 token，>8px 防贴边）
    PAD_IN = 12                       # 胶囊左内边距（统一 token）
    x0, y0, x1, y1 = box
    cy = (y0 + y1) / 2
    fill = _blend(accent, t.card, 0.88)
    c.rounded_rect(box, radius=(y1 - y0) // 2, fill=fill, outline=accent, width=1)
    ix = x0 + PAD_IN + PILL_ICON / 2
    ibox = _gold.draw(c, icon, ix, cy, PILL_ICON)
    if boxes is not None:
        boxes.append(("pill.icon." + key.rsplit(".", 1)[-1], *ibox))
    lab_x = x0 + PAD_IN + PILL_ICON + 8
    c.text((lab_x, cy), label, size=FS_PILL_LAB, color=t.text_sub, role="regular", anchor="lm")
    # 数值统一 lm 基准 + 手动右对齐：Pillow ra=right-ascender 在 24px bold 时下缘
    # 下沉压胶囊底边（肉眼核对抓到，20px 时不显）。数值盒用实测半高，不按 FS//2 估算。
    val_right = x1 - PAD_VAL
    val_left = val_right - _w(value, FS_PILL_VAL, "bold")
    c.text((val_left, cy), value, size=FS_PILL_VAL, color=t.text, role="bold", anchor="lm")
    if boxes is not None:
        vh = _cf.size(value, FS_PILL_VAL, "bold")[1]
        boxes.append((key, x0, y0, x1, y1))
        boxes.append((key + ".value", val_left, cy - vh // 2, val_right, cy + vh // 2))


def build(
    t: "object",
    name: str = "你",
    res: dict | None = None,
    prof: dict | None = None,
    cal: dict | None = None,
    rank: int | None = None,
    width: int | None = None,
    boxes: list | None = None,
) -> Canvas:
    res = res or {}
    prof = prof or {}
    cal = cal or {}
    name = (name or "你").strip() or "你"
    width = int(width or getattr(t, "width", 800) or 800)
    radius = int(getattr(t, "radius", 22) or 0)
    r_ctrl = int(getattr(t, "r_ctrl", 8) or 8)

    streak = int(prof.get("streak") or 0)
    total = int(prof.get("total_days") or 0)
    points = int(prof.get("points") or 0)
    gain = int(res.get("gain") or 0)
    already = bool(prof.get("already")) or res.get("status") == "already"
    ok_state = gain > 0 and not already
    nxt = prof.get("next_milestone")   # (mday, mbonus, remaining)

    # ---- 奖励明细（真实拆分，来自 do_checkin）----
    reward_items = []
    if ok_state:
        daily = int(res.get("daily") or 0)
        sb = int(res.get("streak_bonus") or 0)
        mb = int(res.get("milestone_bonus") or 0)
        reward_items.append(("每日签到", "+%d" % daily, False))
        if sb > 0:
            reward_items.append(("连签加成", "+%d" % sb, False))
        if mb > 0:
            reward_items.append(("里程碑达成", "+%d" % mb, True))
    elif already:
        reward_items.append(("今日已领", "明天再来", False))
    else:
        reward_items.append(("尚未签到", "发 /签到 领取", False))
    n_reward = len(reward_items)
    reward_rows = 1                     # 最多 3 枚，横向一排放得下（宽度校验见绘制）

    # ---- 月历几何（度量与绘制同源）----
    today_for_cal = str(cal.get("today") or "")
    cal_year = cal.get("year")
    cal_month = cal.get("month")
    days = cal.get("days") or {}
    has_cal = bool(cal_year and cal_month)
    in_month = has_cal and today_for_cal.startswith(
        "%04d-%02d-" % (int(cal_year), int(cal_month)))
    today_day = int(today_for_cal.split("-")[2]) if today_for_cal else -1
    rows = 0
    if has_cal:
        off, dim = _month_matrix(int(cal_year), int(cal_month))
        rows = (off + dim + 6) // 7
    cal_h = (SEC_TITLE_H + 8 + WD_H + rows * DAY_H + 8) if has_cal else 0

    # ---- 高度预算（单一来源，逐分区累加）----
    status_h = (SEC_TITLE_H + 6 + STATUS_CARD_PAD * 2
                + 2 * PILL_H + PILL_GAP_Y)
    mile_h = SEC_TITLE_H + 10 + MILE_BAR_H + 12 + MILE_LEGEND_H
    reward_h = SEC_TITLE_H + 8 + REWARD_PILL_H
    note_lines = 2
    note_h = note_lines * NOTE_LINE_H + (note_lines - 1) * NOTE_GAP
    content_bottom = (PAD + HDR_H + GAP + BANNER_H + GAP + status_h + GAP
                      + mile_h + GAP + reward_h + GAP + cal_h + GAP + note_h
                      + GAP + FOOTER_H + PAD)
    h = int(content_bottom)

    # ---- 配色（全部走主题）----
    bg = t.bg
    card = t.card
    border = t.card_border
    text = t.text
    sub = t.text_sub
    weak = t.text_weak
    accent = t.accent
    ok = t.ok
    warn = t.warn
    track = t.track
    div = t.divider
    accent_soft = getattr(t, "accent_soft", _blend(accent, card, 0.84))

    cx0 = PAD + CONTENT_PAD
    cx1 = width - PAD - CONTENT_PAD
    content_w = cx1 - cx0

    _gold.gate(strict=True)   # 缺真实黄金 PNG 直接 SystemExit，禁止几何兜底当交付
    _cf.gate(strict=True)     # 缺风格字体同样拦（动态昵称另有 vendored 雅黑回退）
    c = _cf.wrap(Canvas(width, h, bg, theme=t))    # 分层字体代理：c.text 走风格字，绘制度量同源
    decor.background(c, t, tpl="checkin")          # 主题贴纸背景（替代参考图兔子水印）
    # 主卡（圆角走主题；double_border 时加内圈细线）
    c.rounded_rect((PAD, PAD, width - PAD, h - PAD),
                   radius=radius, fill=card, outline=border, width=1)
    if getattr(t, "double_border", False):
        c.rounded_rect((PAD + 5, PAD + 5, width - PAD - 5, h - PAD - 5),
                       radius=max(0, radius - 5), outline=_blend(border, text, 0.18), width=1)
    c.add_region("card", [PAD, PAD, width - PAD, h - PAD], label="卡片底色", group="背景",
                 tip="点击修改卡片底色")

    y = PAD

    # ============ 顶部：闹钟 + 「签到」居中 + 副标题 + 分隔线 ============
    title_cy = y + FS_TITLE // 2 + 4
    tw = _w("签到", FS_TITLE, "bold")
    icon_sz = 30
    group_w = icon_sz + 14 + tw
    gx0 = (width - group_w) / 2
    ibox = _gold.draw(c, "clock", gx0 + icon_sz / 2, title_cy, icon_sz)
    if boxes is not None:
        boxes.append(("hdr.icon", *ibox))
    c.text((gx0 + icon_sz + 14, title_cy), "签到", size=FS_TITLE, color=accent,
           role="bold", anchor="lm")
    _rec(boxes, "hdr.title", (gx0, title_cy - FS_TITLE // 2,
                              gx0 + group_w, title_cy + FS_TITLE // 2))
    c.text((width / 2, y + HDR_H - 40), "%s 的签到小屋" % name,
           size=FS_SUB, color=sub, role="regular", anchor="mm")
    if rank:
        c.text((cx1, title_cy), "RANK #%02d" % int(rank), size=FS_RANK, color=accent,
               role="bold", anchor="ra")
        _rec(boxes, "hdr.rank", _tb((cx1, title_cy), "RANK #%02d" % int(rank),
                                    FS_RANK, "bold", "ra"))
    c.add_region("accent", [gx0, title_cy - FS_TITLE // 2, gx0 + group_w, title_cy + FS_TITLE // 2],
                 label="标题/主色", group="主色", tip="点击修改主色")
    c.line((cx0, y + HDR_H - 12, cx1, y + HDR_H - 12), fill=div, width=1)
    y += HDR_H + GAP

    # ============ 结果横幅（绿色左边框 + 浅绿底 + 三态）============
    banner_fill = _blend(ok, card, 0.90)
    bbox = (cx0, y, cx1, y + BANNER_H)
    c.rounded_rect(bbox, radius=r_ctrl, fill=banner_fill, outline=_blend(ok, card, 0.55), width=1)
    c.rounded_rect((cx0, y + 2, cx0 + BANNER_BAR, y + BANNER_H - 2),
                   radius=BANNER_BAR // 2, fill=ok)
    if ok_state:
        b_title = "签到成功！"
        b_msg = "今日 +%d 喵币 · 已连签 %d 天，继续加油～" % (gain, streak)
        b_tcol = ok
    elif already:
        b_title = "今天已签到"
        b_msg = "明天再来，连签别断哦～"
        b_tcol = text
    else:
        b_title = "还没签到"
        b_msg = "发 /签到 领今日喵币～"
        b_tcol = warn
    # 横幅文字：x 左内边距统一 16 token；y 按放大后字号（24/17）重排，上下呼吸对称
    BANNER_PAD_X = 16
    c.text((cx0 + BANNER_PAD_X, y + 30), b_title, size=FS_BANNER_T, color=b_tcol, role="bold", anchor="lm")
    c.text((cx0 + BANNER_PAD_X, y + 66), b_msg, size=FS_BANNER_M, color=text, role="regular", anchor="lm")
    c.add_region("ok", [cx0, y, cx0 + 6, y + BANNER_H], label="成功色", group="语义色",
                 tip="点击修改成功色")
    _rec(boxes, "banner", bbox)
    y += BANNER_H + GAP

    # ============ 签到状态（分区标题 + 卡片 + 2×2 胶囊）============
    c.text((cx0, y + SEC_TITLE_H / 2), "签到状态", size=FS_SEC, color=accent,
           role="bold", anchor="lm")
    _rec(boxes, "status.title", _tb((cx0, y + SEC_TITLE_H / 2), "签到状态",
                                    FS_SEC, "bold", "lm"))
    y += SEC_TITLE_H + 6
    card_box = (cx0, y, cx1, y + STATUS_CARD_PAD * 2 + 2 * PILL_H + PILL_GAP_Y)
    c.rounded_rect(card_box, radius=r_ctrl, fill=_blend(card, border, 0.30),
                   outline=border, width=1)   # 容器：不登记（避免与子胶囊父子重叠）
    pill_w = (content_w - 2 * STATUS_CARD_PAD - PILL_GAP_X) / 2
    px0 = cx0 + STATUS_CARD_PAD
    py0 = y + STATUS_CARD_PAD
    if nxt:
        fourth = ("距里程碑", "还差%d天" % int(nxt[2]), "arrow_up", accent)
    else:
        fourth = ("群内名次", ("#%d" % int(rank)) if rank else "已圆满", "star", accent)
    pills = [
        ("连签次数", "%d天" % streak, "bolt", ok),
        ("累计签到", "%d天" % total, "check", accent),
        ("当前喵币", ("%d" % points) + ((" +%d" % gain) if ok_state else ""), "diamond", warn if ok_state else accent),
        fourth,
    ]
    for i, (lab, val, ico, pcol) in enumerate(pills):
        col, row = i % 2, i // 2
        bx0 = px0 + col * (pill_w + PILL_GAP_X)
        by0 = py0 + row * (PILL_H + PILL_GAP_Y)
        _pill(c, t, (bx0, by0, bx0 + pill_w, by0 + PILL_H), lab, val, ico,
              pcol, boxes, "status.pill.%d" % i)
    y += card_box[3] - card_box[1] + GAP

    # ============ 里程碑进度条（项目强项，保留）============
    c.text((cx0, y + SEC_TITLE_H / 2), "里程碑  %d/%d" % (min(total, _MILE_MAX), _MILE_MAX),
           size=FS_SEC, color=accent, role="bold", anchor="lm")
    _rec(boxes, "mile.title", _tb((cx0, y + SEC_TITLE_H / 2),
                                  "里程碑  %d/%d" % (min(total, _MILE_MAX), _MILE_MAX),
                                  FS_SEC, "bold", "lm"))
    bar_y = y + SEC_TITLE_H + 10
    bar_box = (cx0, bar_y, cx1, bar_y + MILE_BAR_H)
    c.rounded_rect(bar_box, radius=MILE_BAR_H // 2, fill=track)
    frac = min(total / _MILE_MAX, 1.0)
    if frac > 0:
        c.rounded_rect((cx0, bar_y, cx0 + (cx1 - cx0) * frac, bar_y + MILE_BAR_H),
                       radius=MILE_BAR_H // 2, fill=accent)
    for m, _ in _MILESTONES:
        tx = cx0 + (m / _MILE_MAX) * (cx1 - cx0)
        c.line((tx, bar_y - 3, tx, bar_y + MILE_BAR_H + 3), fill=div, width=1)
    # 0 进度不画当前位置竖线：否则一根 2px 蓝线孤立贴在轨道最左外缘（观感瑕疵）
    if total > 0:
        px = cx0 + (min(total, _MILE_MAX) / _MILE_MAX) * (cx1 - cx0)
        c.line((px, bar_y - 6, px, bar_y + MILE_BAR_H + 6), fill=accent, width=2)
    c.add_region("track", [cx0, bar_y, cx1, bar_y + MILE_BAR_H], label="进度条轨道",
                 group="进度条", tip="点击修改进度条")
    _rec(boxes, "mile.bar", bar_box)
    leg_y = bar_y + MILE_BAR_H + 12 + MILE_LEGEND_H / 2
    ly0, ly1 = leg_y - FS_LEGEND // 2, leg_y + FS_LEGEND // 2
    lx = cx0
    # 逐 token 登记真实盒，check_boxes 量化相邻间距（整条父盒查不出粘连）
    for i, (m, bonus) in enumerate(_MILESTONES):
        reached = total >= m
        is_next = (not reached) and (nxt and m == int(nxt[0]))
        # 风格字体缺 ✓/▶ 字形，改「文字+颜色+字重」区分三态，杜绝豆腐框
        token = ("已达成 %d天 +%d" % (m, bonus)) if reached else (
            "下一档 %d天 +%d" % (m, bonus) if is_next else "%d天 +%d" % (m, bonus))
        col = ok if reached else (accent if is_next else weak)
        role = "bold" if (reached or is_next) else "regular"
        c.text((lx, leg_y), token, size=FS_LEGEND, color=col, role=role, anchor="lm")
        tw = _w(token, FS_LEGEND, role)
        _rec(boxes, "mile.legend.%d" % i, (lx, ly0, lx + tw, ly1))
        lx += tw + MILE_LEGEND_GAP
    if nxt:
        mday, mbn, rem = nxt
        nt = "还差%d天 +%d" % (rem, mbn)
        nw = _w(nt, FS_LEGEND, "bold")
        c.text((cx1, leg_y), nt, size=FS_LEGEND, color=accent, role="bold", anchor="ra")
        _rec(boxes, "mile.legend.next", (cx1 - nw, ly0, cx1, ly1))
    _rec(boxes, "mile.legend", (cx0, leg_y - FS_LEGEND, cx1, leg_y + FS_LEGEND))
    y += mile_h + GAP

    # ============ 本次签到奖励（分区标题与其它三区统一 accent 主色 + bold）============
    c.text((cx0, y + SEC_TITLE_H / 2), "本次签到奖励", size=FS_SEC, color=accent,
           role="bold", anchor="lm")
    _rec(boxes, "reward.title", _tb((cx0, y + SEC_TITLE_H / 2), "本次签到奖励",
                                    FS_SEC, "bold", "lm"))
    ry0 = y + SEC_TITLE_H + 8
    rw_pool = content_w - (n_reward - 1) * REWARD_PILL_GAP
    rw = rw_pool / n_reward
    for i, (lab, txt, rare) in enumerate(reward_items):
        bx0 = cx0 + i * (rw + REWARD_PILL_GAP)
        by0 = ry0
        pcol = warn if rare else ok
        c.rounded_rect((bx0, by0, bx0 + rw, by0 + REWARD_PILL_H), radius=REWARD_PILL_H // 2,
                       fill=_blend(pcol, card, 0.90), outline=_blend(pcol, card, 0.5), width=1)
        # 奖励胶囊内边距统一：左 12 token，图标→文字 gap 8 token（禁用 10/6 混用）
        R_PIL = 12
        _gold.draw(c, "wand", bx0 + R_PIL + 8, by0 + REWARD_PILL_H / 2, 16)
        label = "%s  %s" % (lab, txt) if txt else lab
        c.text((bx0 + R_PIL + 16 + 8, by0 + REWARD_PILL_H / 2), label, size=FS_REWARD,
               color=text, role="bold", anchor="lm")
        _rec(boxes, "reward.pill.%d" % i, (bx0, by0, bx0 + rw, by0 + REWARD_PILL_H))
    y += reward_h + GAP

    # ============ 本月签到记录（项目强项：完整 7 列月历）============
    if has_cal:
        c.text((cx0, y + SEC_TITLE_H / 2), "本月签到记录", size=FS_SEC, color=accent,
               role="bold", anchor="lm")
        _rec(boxes, "cal.title", _tb((cx0, y + SEC_TITLE_H / 2), "本月签到记录",
                                     FS_SEC, "bold", "lm"))
        cal_top = y + SEC_TITLE_H + 8
        cell_w = content_w / 7.0
        for wd in range(7):
            ccx = cx0 + cell_w * (wd + 0.5)
            c.text((ccx, cal_top + WD_H / 2), "日一二三四五六"[wd], size=FS_WD,
                   color=weak, role="regular", anchor="mm")
        off, dim = _month_matrix(int(cal_year), int(cal_month))
        for d in range(1, dim + 1):
            idx = off + (d - 1)
            col, row = idx % 7, idx // 7
            ccx = cx0 + cell_w * (col + 0.5)
            cy_top = cal_top + WD_H + row * DAY_H
            daykey = "%04d-%02d-%02d" % (int(cal_year), int(cal_month), d)
            checked = daykey in days
            is_today = in_month and d == today_day
            cell = (ccx - cell_w / 2 + 4, cy_top + 2,
                    ccx + cell_w / 2 - 4, cy_top + DAY_H - 2)  # 水平内缩 4 token
            cy = (cell[1] + cell[3]) / 2
            if is_today and checked:
                c.rounded_rect(cell, radius=r_ctrl, fill=_blend(ok, card, 0.80),
                               outline=ok, width=2)
                c.text((ccx, cy), "%02d" % d, size=FS_DAY, color=ok, role="bold", anchor="mm")
            elif is_today:
                c.rounded_rect(cell, radius=r_ctrl, outline=accent, width=2)
                c.text((ccx, cy), "%02d" % d, size=FS_DAY, color=accent, role="bold", anchor="mm")
            elif checked:
                c.rounded_rect(cell, radius=r_ctrl, fill=_blend(ok, card, 0.88))
                c.text((ccx, cy), "%02d" % d, size=FS_DAY, color=ok, role="bold", anchor="mm")
            elif in_month and d < today_day:
                c.rounded_rect(cell, radius=r_ctrl, outline=_blend(weak, card, 0.5), width=1)
                c.text((ccx, cy), "%02d" % d, size=FS_DAY, color=weak, role="regular", anchor="mm")
            else:
                c.text((ccx, cy), "%02d" % d, size=FS_DAY, color=weak, role="regular", anchor="mm")
        _rec(boxes, "cal.grid", (cx0, cal_top, cx1, cal_top + WD_H + rows * DAY_H))
        y += cal_h + GAP

    # ============ 说明（真实规则，不编造 Rabbit 文案）============
    makeup_txt = "*发「/签到 补签」可消耗喵币补回最近一次断签；连续签到还有阶梯加成。"
    tip_txt = "小提示：累计满 3 / 7 / 15 / 30 天可领里程碑奖励，别断签～"
    c.text((cx0, y + NOTE_LINE_H / 2), makeup_txt, size=FS_NOTE, color=sub,
           role="regular", anchor="lm")
    _rec(boxes, "note.makeup", _tb((cx0, y + NOTE_LINE_H / 2), makeup_txt,
                                   FS_NOTE, "regular", "lm"))
    c.text((cx0, y + NOTE_LINE_H + NOTE_GAP + NOTE_LINE_H / 2), tip_txt,
           size=FS_NOTE, color=weak, role="regular", anchor="lm")
    _rec(boxes, "note.tip", _tb((cx0, y + NOTE_LINE_H + NOTE_GAP + NOTE_LINE_H / 2),
                                tip_txt, FS_NOTE, "regular", "lm"))
    y += note_h + GAP

    # ============ 页脚（PC 绿胶囊 + 机器人名 + 长按提示）============
    fy = y + FOOTER_H / 2
    c.line((cx0, y + 2, cx1, y + 2), fill=div, width=1)
    c.rounded_rect((cx0, fy - PC_H / 2, cx0 + PC_W, fy + PC_H / 2),
                   radius=6, outline=ok, width=1)
    c.text((cx0 + PC_W / 2, fy), "PC", size=12, color=ok, role="bold", anchor="mm")
    foot_name = "曼珠"      # 本项目自有品牌，不冒充 Warframe Rabbit
    nx = cx0 + PC_W + 8     # 页脚元素间 gap 统一 8 token
    c.text((nx, fy), foot_name, size=FS_FOOT, color=ok, role="bold", anchor="lm")
    nx += _w(foot_name, FS_FOOT, "bold") + 8
    c.text((nx, fy), "*善用右键/长按提取文字", size=FS_FOOT, color=weak,
           role="regular", anchor="lm")
    _rec(boxes, "footer", (cx0, y + 2, cx1, y + FOOTER_H))
    c.add_region("text_weak", [nx, fy - FS_FOOT, width - PAD, fy + FS_FOOT],
                 label="弱文字", group="文字", tip="点击修改弱文字色")

    return c
