# -*- coding: utf-8 -*-
"""市价卡交付闸门：浅色默认绑定 + 白金/现金/杜卡德三枚**真实游戏内图标**。

用法（qq_bridge 根目录）：
    python tools/check_market_currency_layout.py --check
    python tools/check_market_currency_layout.py --selftest

``--check`` 渲染三张样例卡并写截图：
    tests/_shot_market_currency_light.png      （浅色默认绑定 + 有杜卡德）
    tests/_shot_market_currency_neon.png      （显式深色主题 + 有杜卡德）
    tests/_shot_market_currency_noducat.png   （浅色 + 杜卡德不可兑换，不画杜卡德图标）

每张都按 card-design 硬规则跑 ``check_boxes``（溢出 / 重叠 / 间距 0），并核对
三枚货币图标的**真实包围盒**与 ``@mstat`` 四行统计块。任一不符 ``exit 1``。

``--selftest`` 把真实犯过的错放回去，证明闸门会报错：重叠/溢出/间距、
漏登记图标、不可兑换仍画杜卡德、缺真实 PNG、删掉浅色内置默认绑定、mstat 少一行。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import render_wm_card as R  # noqa: E402  复用真实 wm 协议样例（不联网）
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

# (主题, 杜卡德字段值, 截图文件名, 是否应出现杜卡德图标)
SAMPLES = (
    ("light", "100", "_shot_market_currency_light.png", True),
    ("neon", "100", "_shot_market_currency_neon.png", True),
    ("light", "不可兑换", "_shot_market_currency_noducat.png", False),
)


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


def _render(theme_name: str, ducat: str):
    """按真实 wm 协议渲染一张卡，返回 (Canvas, boxes)。"""
    body = R._body().replace("|不可兑换|1,000,000|", "|%s|1,000,000|" % ducat)
    t = theme.get(theme_name)
    boxes: list = []
    c = guide.build(t, body=body, boxes=boxes,
                    avatars={f"u{i}": R._avatar(i) for i in range(1, 11)},
                    item_icon=R._icon(),
                    source="数据源：Warframe.Market · 市价卡货币图标验收")
    return c, boxes


def _currency_problems(boxes: list, want_ducats: bool) -> list[str]:
    """核对三枚货币图标的真实包围盒 + @mstat 四行不回归。"""
    p: list[str] = []
    names = [b[0] for b in boxes]
    need = ["wm.cur.credits", "tbl.hdr.platinum"] + (["wm.cur.ducats"] if want_ducats else [])
    for n in need:
        if names.count(n) != 1:
            p.append("[图标] %s 应恰好登记 1 个包围盒，实际 %d" % (n, names.count(n)))
    if not want_ducats and "wm.cur.ducats" in names:
        p.append("[图标] 杜卡德「不可兑换」时不应画杜卡德图标")
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

    for theme_name, ducat, shot, want in SAMPLES:
        c, boxes = _render(theme_name, ducat)
        shot_path = SHOTS / shot
        shot_path.write_bytes(c.to_bytes())
        layout = check_boxes((16, 16, c.width - 16, c.height - 16), boxes, MIN_GAP)
        cur = _currency_problems(boxes, want)
        icons = [b[0] for b in boxes if b[0].startswith(("wm.cur.", "tbl.hdr.platinum"))]
        print("[%s] %s → %dx%d, boxes=%d, 图标=%s, 排版问题=%d, 图标问题=%d, 截图=%s"
              % ("OK" if not layout and not cur else "FAIL", shot, c.width, c.height,
                 len(boxes), ",".join(icons) or "无", len(layout), len(cur), shot_path.name))
        for x in layout + cur:
            print("   ", x)
        problems += ["%s: %s" % (shot, x) for x in layout + cur]

    print("\n市价卡货币图标闸门：%s" % ("PASS（3/3 样例，溢出 0 / 重叠 0 / 间距 0）"
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
    if _currency_problems(boxes, True):
        errs.append("正常有杜卡德样本被误报")
    # ① 漏登记白金图标
    no_plat = [b for b in boxes if b[0] != "tbl.hdr.platinum"]
    if not _currency_problems(no_plat, True):
        errs.append("漏画/漏登记白金图标未被拦住")
    # ② 杜卡德不可兑换却仍画图标
    bogus = boxes + [("wm.cur.ducats", 48, 222, 76, 250)]
    if not _currency_problems(bogus, False):
        errs.append("不可兑换仍画杜卡德图标未被拦住")
    # ③ mstat 少一行
    row_idx = next(i for i, b in enumerate(boxes) if b[0] == "mstat.label")
    no_row = [b for i, b in enumerate(boxes) if i != row_idx]
    if not _currency_problems(no_row, True):
        errs.append("mstat 少一行未被拦住")
    # ④ 缺真实 PNG：gate 必须报错，还原后必须恢复
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
    # ⑤ 删掉代码级浅色内置绑定（且无用户配置文件）必须不再返回 light
    orig_binds = card_binds._DEFAULT_CARD_BINDS
    orig_path = theme._CARD_THEMES_PATH
    card_binds._DEFAULT_CARD_BINDS = {}
    # 同时模拟「无用户配置文件」：把配置路径指到一个不存在的文件，让 theme.card_theme 回落 ''
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
    ap.add_argument("--check", action="store_true", help="渲染三张截图并跑自检（默认）")
    ap.add_argument("--selftest", action="store_true", help="注入反例验证闸门会报错")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    return run_check()


if __name__ == "__main__":
    raise SystemExit(main())
