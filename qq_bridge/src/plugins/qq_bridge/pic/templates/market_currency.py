# -*- coding: utf-8 -*-
"""市价卡货币图标绘制（2026-11-09，从 guide.py 抽出，按功能拆分避免 guide.py 继续膨胀）。

负责「统计条」与「价格表头」里的游戏内货币图标：
  * 统计条：杜卡德 / 交易税（以现金 Credits 计）/ 在售卖家三列，前两列前置真实货币图标；
  * 价格表头：「白金价格」前置白金图标，作为「图标 + 文字」整体居中单元。

图标走 ``currency_icons.draw``（真实 PNG 优先，缺图几何兜底 + 告警）。
所有坐标沿用 guide.py 的布局变量，box 登记名与门禁 / 测试保持一致
（``wm.cur.ducats`` / ``wm.cur.credits`` / ``tbl.hdr.platinum``）。
"""
from __future__ import annotations

from .. import currency_icons
from ..canvas import Canvas

#: 统计条里哪些列带货币图标（gi 索引 → 货币 kind）。卖家列（gi=2）无货币。
_STAT_CUR = {0: "ducats", 1: "credits"}


def draw_stats_strip(c: "Canvas", t, pad_in: int, y: int, content_w: int, boxes, base,
                     tint, ducats, tax, sellers, online) -> int:
    """画统计条三列（杜卡德 / 交易税 / 在售卖家）并前置货币图标，返回更新后的 y。

    ``base`` / ``tint`` 来自 guide.py（列表气泡底色 / 列竖线着色）。
    """
    _lab_fs, _val_fs = t.fs(15), t.fs(30)
    _groups = [
        ("杜卡德", "DUCATS", ducats, ""),
        ("交易税", "TRADE TAX", tax, ""),
        ("在售卖家", "SELLERS", sellers, f"在线 {online}" if online else ""),
    ]
    _gw = content_w / 3
    _ico_sz = t.fs(28)
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


def draw_price_header(c: "Canvas", t, hx: int, ry: int, row_h: int, hfs: int,
                      cell: str, boxes) -> None:
    """画价格表头单元：前置白金图标，作为「图标 + 文字」整体居中单元。"""
    _tw = Canvas.text_width(cell, hfs, "bold")
    _psz = hfs + 4
    _pgap = 6
    _total = _psz + _pgap + _tw
    _start = hx - _total // 2
    currency_icons.draw(c, "platinum", _start + _psz // 2, ry + row_h // 2, _psz)
    if boxes is not None:
        boxes.append(("tbl.hdr.platinum", int(_start), int(ry + row_h // 2 - _psz // 2),
                      int(_start + _psz), int(ry + row_h // 2 + _psz // 2)))
    c.text((_start + _psz + _pgap, ry + row_h // 2), cell, size=hfs,
           color=t.text_sub, role="bold", anchor="lm")
