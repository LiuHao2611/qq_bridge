# -*- coding: utf-8 -*-
"""市价卡货币图标绘制（2026-11-09，从 guide.py 抽出，按功能拆分避免 guide.py 继续膨胀）。

负责「统计条」「价格单元」「表头」三处：
  * 统计条：杜卡德 / 交易税（以现金 Credits 计）/ 在售卖家三列，前两列前置真实货币图标；
  * 价格单元：**每行**价格气泡后紧跟白金图标，作为整体居中于价格列；
  * 表头：最左头像列补「头像」标签（不再留白），五列统一左对齐到固定列起点。

2026-11-09 用户反馈修正：
  * 白金图标从「列头」移到「每一行价格后面」（列头只留纯文字「白金价格」）；
  * 头像列表头不再空白。
所有坐标走度量（``Canvas.text_width``）+ 显式 gap，禁止空格凑间距 / 估算宽度。
图标走 ``currency_icons.draw``（真实 PNG 优先，缺图几何兜底 + 告警）。
box 登记名与门禁 / 测试保持一致：``wm.cur.ducats`` / ``wm.cur.credits`` /
``tbl.hdr.avatar`` / ``tbl.row.badge`` / ``tbl.row.platinum``。
"""
from __future__ import annotations

from types import SimpleNamespace

from .. import currency_icons
from ..canvas import Canvas

#: 统计条里哪些列带货币图标（gi 索引 → 货币 kind）。卖家列（gi=2）无货币。
_STAT_CUR = {0: "ducats", 1: "credits"}

#: 统计条数值字号（唯一来源：高度计算与 draw_stats_strip 共用，防底部裁切）。
STAT_VAL_FS = 38


def wm_types(t) -> SimpleNamespace:
    """wm 市价卡字号/行高的**单一来源**（2026-11-10「放大加粗字体」）。

    高度计算与渲染共用同一组常量，杜绝「高度按小字算、渲染按大字画」的裁切。
    层级：主标题 > 数值 > 玩家名/价格 > 正文 > 弱文字，对齐 Warframe.Market 订单页。
    放本模块（<20KB，可增长）而非 guide.py（存量 >20KB 棘轮，只能净缩减）。
    """
    tbl_fs = t.fs(28)
    return SimpleNamespace(
        bar_fs=t.fs(28),            # 顶部「交易市场 / 买家 WTS」
        title_fs=t.fs(44), title_lh=t.fs(52),   # 商品中文大标题（第一视觉锚点）
        en_fs=t.fs(21), en_lh=t.fs(26),         # 英文名
        sub_fs=t.fs(17), sub_lh=t.fs(23),       # 分类 / 效果弱文字
        sect_fs=t.fs(30),           # 「玩家卖单」区块标题
        tbl_fs=tbl_fs,              # 表格正文（玩家名/状态/声望/数量）
        price_fs=t.fs(32),          # 价格数字（比正文更醒目）
        row_h=tbl_fs + 34,          # 行高：正文外留 34px 呼吸区
        hdr_fs=t.fs(19),            # 表头
    )


def wmhdr_text_height(wm, cols) -> int:
    """右栏文字栈（标题/英文/分类/效果）实测行高累加，供高度计算与渲染共用。"""
    h = wm.title_lh
    if cols[1]:
        h += wm.en_lh
    if cols[2]:
        h += wm.sub_lh
    if len(cols) > 8 and cols[8]:
        h += wm.sub_lh
    return h


def draw_stats_strip(c: "Canvas", t, pad_in: int, y: int, content_w: int, boxes, base,
                     tint, ducats, tax, sellers, online) -> int:
    """画统计条三列（杜卡德 / 交易税 / 在售卖家）并前置货币图标，返回更新后的 y。

    ``base`` / ``tint`` 来自 guide.py（列表气泡底色 / 列竖线着色）。
    """
    _lab_fs, _val_fs = t.fs(15), t.fs(STAT_VAL_FS)   # 数值放大加粗，标签仍弱文字
    _groups = [
        ("杜卡德", "DUCATS", ducats, ""),
        ("交易税", "TRADE TAX", tax, ""),
        ("在售卖家", "SELLERS", sellers, f"在线 {online}" if online else ""),
    ]
    _gw = content_w / 3
    _ico_sz = t.fs(34)
    _ico_gap = 8
    for gi, (lab_zh, lab_en, val, sub) in enumerate(_groups):
        _gx = pad_in + gi * _gw
        _tx = _gx
        # 货币图标（若有）：跨标签+数值两行竖直居中，置于数值区左侧
        if gi in _STAT_CUR and not (gi == 0 and str(val) == "不可兑换"):
            _kn = _STAT_CUR[gi]
            _iy = y + (_lab_fs + 6) / 2
            currency_icons.draw(c, _kn, _tx + _ico_sz / 2, int(_iy), _ico_sz)
            if boxes is not None:
                boxes.append((f"wm.cur.{_kn}", int(_tx), int(_iy - _ico_sz / 2),
                              int(_tx + _ico_sz), int(_iy + _ico_sz / 2)))
            _tx += _ico_sz + _ico_gap
        c.text((_tx, y), lab_zh, size=_lab_fs, color=t.text_sub, anchor="lm")
        _lw = Canvas.text_width(lab_zh, _lab_fs, "regular")
        c.text((_tx + _lw + 6, y), lab_en, size=t.fs(12), color=t.text_sub, anchor="lm")
        _vy = y + _lab_fs + 6
        c.text((_tx, _vy), str(val), size=_val_fs, color=t.text, role="bold", anchor="lm")
        if sub:
            _vw = Canvas.text_width(str(val), _val_fs, "bold")
            c.text((_tx + _vw + 10, _vy + _val_fs // 3), sub,
                   size=t.fs(15), color=t.text_sub, anchor="lm")
        if gi < 2:
            c.line((_gx + _gw - 12, y - 4, _gx + _gw - 12, y + _val_fs + _lab_fs + 6),
                   fill=tint(t.accent, 0.18, base), width=1)
    y += _lab_fs + 6 + _val_fs + 12
    return y


def draw_table_header(c: "Canvas", t, col_x, box_x0: int, av_w: int,
                      ry: int, row_h: int, hfs: int, cells, boxes) -> None:
    """画表头：头像列居中补「头像」标签，其余五列统一左对齐到固定列起点。

    白金图标不进列头（用户反馈），列头价格单元只是纯文字 ``白金价格``。
    """
    cy = ry + row_h // 2
    lab = "头像"
    lw = Canvas.text_width(lab, hfs, "bold")
    lx = box_x0 + (av_w - lw) / 2
    c.text((lx, cy), lab, size=hfs, color=t.text_sub, role="bold", anchor="lm")
    if boxes is not None:
        boxes.append(("tbl.hdr.avatar", int(lx), int(cy - hfs / 2),
                      int(lx + lw), int(cy + hfs / 2)))
    for ci, cell in enumerate(list(cells)[:5]):
        c.text((col_x[ci] + 14, cy), cell, size=hfs,
               color=t.text_sub, role="bold", anchor="lm")


def draw_price_cell(c: "Canvas", t, cx: int, ry: int, row_h: int, tbl_fs: int,
                    cell: str, pcol: str, outline: str, text_color: str, boxes,
                    price_fs: int | None = None) -> None:
    """画价格单元：渐变/绿价格气泡 + **后置白金真实图标**，整体居中于价格列。

    组宽 = 气泡宽 + gap(8) + 图标边长，起点 = 列中线 − 组宽/2（先度量再定位）。
    ``price_fs`` 让价格数字比表格正文更醒目（参考图纯大数字）；缺省沿用 tbl_fs。
    """
    pfs = price_fs or tbl_fs
    tw = Canvas.text_width(cell, pfs, "bold")
    badge_h = row_h - 14
    badge_w = max(tw + 28, badge_h + 10)
    ico = max(16, int(badge_h * 0.64))
    gap = 9
    total = badge_w + gap + ico
    bx0 = int(round(cx - total / 2))
    by0 = ry + (row_h - badge_h) // 2
    cy = ry + row_h // 2
    c.rounded_rect((bx0, by0, bx0 + badge_w, by0 + badge_h),
                   radius=badge_h // 2, fill=pcol, outline=outline, width=2)
    c.text((bx0 + badge_w // 2, cy), cell, size=pfs,
           color=text_color, role="bold", anchor="mm")
    ix = bx0 + badge_w + gap
    currency_icons.draw(c, "platinum", ix + ico / 2, cy, ico)
    if boxes is not None:
        boxes.append(("tbl.row.badge", bx0, by0, bx0 + badge_w, by0 + badge_h))
        boxes.append(("tbl.row.platinum", ix, int(cy - ico / 2),
                      ix + ico, int(cy + ico / 2)))
