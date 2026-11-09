# -*- coding: utf-8 -*-
"""市价卡交付闸门：浅色默认绑定 + 真实货币图标 + 表格/商品图布局。

用法（qq_bridge 根目录，用带 httpx 的解释器）：
    .venv/Scripts/python.exe tools/check_market_currency_layout.py --check
    .venv/Scripts/python.exe tools/check_market_currency_layout.py --selftest

``--check`` 渲染五张样例卡并写截图：
    tests/_shot_market_currency_light.png       （浅色默认 + 有杜卡德 + 方图）
    tests/_shot_market_currency_neon.png       （显式深色 + 有杜卡德 + 方图）
    tests/_shot_market_currency_noducat.png    （浅色 + 杜卡德不可兑换，不画杜卡德图标）
    tests/_shot_market_currency_tall_icon.png  （竖长商品图，验图标槽自适应）
    tests/_shot_market_currency_wide_icon.png  （宽幅商品图，验图标槽自适应）

每张都按 card-design 硬规则跑 ``check_boxes``（溢出 / 重叠 / 间距 0），并核对：
  * 统计条真实货币图标（现金 / 杜卡德）；
  * 头像列头「头像」标签存在、白金图标**不在列头**；
  * 每行价格气泡后各 1 枚真实白金图标，且紧跟气泡（gap 合规）；
  * 商品图槽 AR 与源图一致（方 / 竖长 / 宽幅三档，2026-11-09「注意自适应比例大小」）；
  * ``@mstat`` 四行统计块。
任一不符 ``exit 1``。

``--selftest`` 把真实犯过的错放回去，证明闸门会报错：重叠/溢出/间距、
漏登记图标、白金退回列头、头像列头留白、某行漏白金图标、图标未紧跟气泡、
商品图槽比例失真、不可兑换仍画杜卡德、缺真实 PNG、删掉浅色内置绑定、mstat 少一行。
"""
from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import render_wm_card as R  # noqa: E402  复用真实 wm 协议样例（不联网）
from PIL import Image, ImageDraw  # noqa: E402
from src.plugins.qq_bridge.pic import (  # noqa: E402
    _resolve_card_theme_name,
    card_binds,
    currency_icons,
    theme,
)
from src.plugins.qq_bridge.pic.templates import guide  # noqa: E402

MIN_GAP = 2
CARD = "星际战甲·市价"
SHOTS = ROOT / "tests"

# (主题, 杜卡德字段值, 截图文件名, 是否应出现杜卡德图标, 商品图尺寸或 None, 源图 AR)
SAMPLES = (
    ("light", "100", "_shot_market_currency_light.png", True, None, 1.0),
    ("neon", "100", "_shot_market_currency_neon.png", True, None, 1.0),
    ("light", "不可兑换", "_shot_market_currency_noducat.png", False, None, 1.0),
    ("light", "100", "_shot_market_currency_tall_icon.png", True, (120, 240), 0.5),
    ("light", "100", "_shot_market_currency_wide_icon.png", True, (240, 120), 2.0),
)


def _synth_icon(w: int, h: int) -> bytes:
    """确定性合成商品图（离线，只验渲染层按 AR 自适应，不依赖网络）。"""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((1, 1, w - 2, h - 2), fill=(120, 140, 200, 255))
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """溢出 / 重叠 / 同行最小间距。boxes: (label, x0, y0, x1, y1)。"""
    problems: list[str] = []
    bx0, by0, bx1, by1 = bounds
    for label, x0, y0, x1, y1 in boxes:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越出安全区 %s (bounds=%s)"
                            % (label, (x0, y0, x1, y1), bounds))
    for i, a in enumerate(boxes):
        la, ax0, ay0, ax1, ay1 = a
        for b in boxes[i + 1:]:
            lb, bx_0, by_0, bx_1, by_1 = b
            if ax0 < bx_1 and bx_0 < ax1 and ay0 < by_1 and by_0 < ay1:
                problems.append("[重叠] %s <-> %s: %s vs %s"
                                % (la, lb, (ax0, ay0, ax1, ay1),
                                   (bx_0, by_0, bx_1, by_1)))
            elif ay0 < by_1 and by_0 < ay1:
                gap = bx_0 - ax1 if ax0 <= bx_0 else ax0 - bx_1
                if 0 <= gap < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx < %dpx"
                                    % (la, lb, gap, min_gap))
    return problems


def _render(theme_name: str, ducat: str, item_icon: bytes | None = None):
    """按真实 wm 协议渲染一张卡，返回 (Canvas, boxes)。"""
    body = R._body().replace("|不可兑换|1,000,000|", "|%s|1,000,000|" % ducat)
    t = theme.get(theme_name)
    boxes: list = []
    c = guide.build(t, body=body, boxes=boxes,
                    avatars={f"u{i}": R._avatar(i) for i in range(1, 11)},
                    item_icon=item_icon if item_icon is not None else R._icon(),
                    source="数据源：Warframe.Market · 市价卡布局验收")
    return c, boxes


def icon_slot_ar(boxes: list) -> float | None:
    """商品图槽的实测长宽比；未登记返回 None。"""
    slot = next((b for b in boxes if b[0] == "wm.hdr.item_icon"), None)
    if not slot:
        return None
    return (slot[3] - slot[1]) / max(1, slot[4] - slot[2])


def _icon_slot_problems(boxes: list, src_ar: float) -> list[str]:
    """商品图槽必须按源图真实 AR 自适应（容差 0.08），不能压成固定方槽。"""
    ar = icon_slot_ar(boxes)
    if ar is None:
        return ["[商品图] 未登记 wm.hdr.item_icon 槽包围盒"]
    if abs(ar - src_ar) > 0.08:
        return ["[商品图] 槽 AR=%.3f 与源图 AR=%.3f 不符（未按真实比例自适应）" % (ar, src_ar)]
    return []


def _currency_problems(boxes: list, want_ducats: bool) -> list[str]:
    """核对货币图标真实包围盒 + 表头/每行价格结构 + @mstat 四行不回归。"""
    p: list[str] = []
    names = [b[0] for b in boxes]
    for n in ["wm.cur.credits"] + (["wm.cur.ducats"] if want_ducats else []):
        if names.count(n) != 1:
            p.append("[图标] %s 应恰好登记 1 个包围盒，实际 %d" % (n, names.count(n)))
    if not want_ducats and "wm.cur.ducats" in names:
        p.append("[图标] 杜卡德「不可兑换」时不应画杜卡德图标")
    # 头像列头必须有标签（2026-11-09「列头第一列头像处被空着了」）
    if names.count("tbl.hdr.avatar") != 1:
        p.append("[表头] 头像列头「头像」标签缺失/重复（第一列不得留白）")
    # 白金图标：每行价格后各一枚，绝不能退回列头
    if "tbl.hdr.platinum" in names:
        p.append("[白金] 白金图标退回列头（应在每一行价格后面）")
    n_badge = names.count("tbl.row.badge")
    n_plat = names.count("tbl.row.platinum")
    if n_badge < 1:
        p.append("[白金] 未登记任何价格气泡")
    if n_plat != n_badge:
        p.append("[白金] 每行价格后应各 1 枚图标：气泡 %d / 图标 %d" % (n_badge, n_plat))
    badges = sorted((b for b in boxes if b[0] == "tbl.row.badge"), key=lambda b: b[2])
    plats = sorted((b for b in boxes if b[0] == "tbl.row.platinum"), key=lambda b: b[2])
    for a, b in zip(badges, plats):
        gap = b[1] - a[3]
        if not (0 <= gap <= 16):
            p.append("[白金] 图标未紧跟价格气泡（gap=%d）" % gap)
    labels = [b for b in boxes if b[0] == "mstat.label"]
    pills = [b for b in boxes if b[0].startswith("mstat.") and b[0] != "mstat.label"]
    if len(labels) != 4 or len(pills) != 12:
        p.append("[mstat] 应为 4 行 / 12 气泡，实际 %d / %d" % (len(labels), len(pills)))
    return p


def run_check() -> int:
    problems: list[str] = []
    miss = currency_icons.missing()
    if miss:
        problems.append("[资产] 缺真实 PNG：%s" % ", ".join(miss))
    if card_binds.default_card_theme(CARD) != "light":
        problems.append("[主题] %s 代码内置默认绑定未指向 light（实得 %r）"
                        % (CARD, card_binds.default_card_theme(CARD)))
    resolved = _resolve_card_theme_name(None, CARD)
    if resolved != "light":
        problems.append("[主题] 整链解析未落到 light（实得 %r，若为运行时配置覆盖请忽略）" % resolved)
    if theme.resolve(resolved, CARD).name != theme.LIGHT:
        problems.append("[主题] resolve(%r, %s) 未落到 light" % (resolved, CARD))

    for theme_name, ducat, shot, want, dims, src_ar in SAMPLES:
        icon = _synth_icon(*dims) if dims else None
        c, boxes = _render(theme_name, ducat, icon)
        (SHOTS / shot).write_bytes(c.to_bytes())
        layout = check_boxes((16, 16, c.width - 16, c.height - 16), boxes, MIN_GAP)
        cur = _currency_problems(boxes, want) + _icon_slot_problems(boxes, src_ar)
        print("[%s] %s → %dx%d, boxes=%d, 槽 AR=%s, 排版问题=%d, 图标问题=%d"
              % ("OK" if not layout and not cur else "FAIL", shot, c.width, c.height,
                 len(boxes), ("%.3f" % icon_slot_ar(boxes)) if icon_slot_ar(boxes) else "-",
                 len(layout), len(cur)))
        for x in layout + cur:
            print("   ", x)
        problems += ["%s: %s" % (shot, x) for x in layout + cur]

    print("\n市价卡布局闸门：%s" % ("PASS（%d/%d 样例，溢出 0 / 重叠 0 / 间距 0）"
                                % (len(SAMPLES), len(SAMPLES))
                                if not problems else "FAIL %d 项" % len(problems)))
    return 1 if problems else 0


def run_selftest() -> int:
    errs: list[str] = []
    bounds = (0, 0, 100, 100)
    if not any("[重叠]" in p for p in check_boxes(bounds, [("a", 0, 0, 60, 60), ("b", 30, 30, 90, 90)])):
        errs.append("重叠未检出")
    if not any("[溢出]" in p for p in check_boxes(bounds, [("x", 90, 90, 130, 130)])):
        errs.append("溢出未检出")
    if not any("[间距]" in p for p in check_boxes(bounds, [("p", 0, 0, 50, 40), ("q", 51, 0, 90, 40)])):
        errs.append("间距未检出")
    if check_boxes(bounds, [("a", 0, 0, 40, 40), ("b", 60, 0, 100, 40)]):
        errs.append("正常样本被误报")

    _, boxes = _render("light", "100")
    if _currency_problems(boxes, True) or _icon_slot_problems(boxes, 1.0):
        errs.append("正常有杜卡德样本被误报")
    # ① 漏登记全部白金图标
    no_plat = [b for b in boxes if b[0] != "tbl.row.platinum"]
    if not _currency_problems(no_plat, True):
        errs.append("漏画/漏登记白金图标未被拦住")
    # ② 白金图标退回列头（本次被纠正的错）
    if not _currency_problems(boxes + [("tbl.hdr.platinum", 600, 100, 620, 120)], True):
        errs.append("白金退回列头未被拦住")
    # ③ 头像列头留白（本次被纠正的错）
    no_avatar = [b for b in boxes if b[0] != "tbl.hdr.avatar"]
    if not _currency_problems(no_avatar, True):
        errs.append("头像列头留白未被拦住")
    # ④ 某一行漏画白金图标
    i = next(k for k, b in enumerate(boxes) if b[0] == "tbl.row.platinum")
    if not _currency_problems([b for k, b in enumerate(boxes) if k != i], True):
        errs.append("某行漏白金图标未被拦住")
    # ⑤ 白金图标离气泡太远（不再「紧跟价格后面」）
    moved = [("tbl.row.platinum", b[1] + 60, b[2], b[3] + 60, b[4])
             if b[0] == "tbl.row.platinum" and k == i else b
             for k, b in enumerate(boxes)]
    if not _currency_problems(moved, True):
        errs.append("白金图标未紧跟价格气泡未被拦住")
    # ⑥ 商品图槽比例失真（本次被纠正的「注意自适应比例大小」）
    bogus_slot = [("wm.hdr.item_icon", 48, 94, 200, 150)
                  if b[0] == "wm.hdr.item_icon" else b for b in boxes]
    if not _icon_slot_problems(bogus_slot, 1.0):
        errs.append("商品图槽比例失真未被拦住")
    if not _icon_slot_problems([b for b in boxes if b[0] != "wm.hdr.item_icon"], 1.0):
        errs.append("商品图槽漏登记未被拦住")
    # ⑦ 杜卡德不可兑换却仍画图标
    bogus = boxes + [("wm.cur.ducats", 48, 222, 76, 250)]
    if not _currency_problems(bogus, False):
        errs.append("不可兑换仍画杜卡德图标未被拦住")
    # ⑧ mstat 少一行
    row_idx = next(i for i, b in enumerate(boxes) if b[0] == "mstat.label")
    no_row = [b for i, b in enumerate(boxes) if i != row_idx]
    if not _currency_problems(no_row, True):
        errs.append("mstat 少一行未被拦住")
    # ⑨ 缺真实 PNG：gate 必须报错，还原后必须恢复
    orig_load = currency_icons._load
    currency_icons._load = lambda k: None  # type: ignore[assignment]
    try:
        try:
            currency_icons.gate(strict=True)
            errs.append("缺真实 PNG 未触发 SystemExit")
        except SystemExit:
            pass
        if not currency_icons.missing():
            errs.append("缺图时 missing() 未报出")
    finally:
        currency_icons._load = orig_load  # type: ignore[assignment]
    if currency_icons.missing():
        errs.append("还原后仍报缺图")
    # ⑩ 删掉代码级浅色内置绑定（且无用户配置文件）必须不再返回 light
    orig_binds = card_binds._DEFAULT_CARD_BINDS
    orig_path = theme._CARD_THEMES_PATH
    card_binds._DEFAULT_CARD_BINDS = {}
    theme._CARD_THEMES_PATH = str(ROOT / "data" / "__no_such_theme_bind__.json")
    try:
        if card_binds.default_card_theme(CARD) == "light":
            errs.append("删掉默认绑定仍返回 light（浅色版没被代码兜住）")
        if _resolve_card_theme_name(None, CARD) == "light":
            errs.append("删掉默认绑定后整链仍返回 light（浅色版没被代码兜住）")
    finally:
        card_binds._DEFAULT_CARD_BINDS = orig_binds
        theme._CARD_THEMES_PATH = orig_path
    if card_binds.default_card_theme(CARD) != "light":
        errs.append("浅色绑定还原失败")

    print("selftest：%s" % ("PASS 全部注入反例被拦下" if not errs else "FAIL — " + "; ".join(errs)))
    return 1 if errs else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="渲染样例截图并跑自检（默认）")
    ap.add_argument("--selftest", action="store_true", help="注入反例验证闸门会报错")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    return run_check()


if __name__ == "__main__":
    raise SystemExit(main())
