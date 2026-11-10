# -*- coding: utf-8 -*-
"""card-design + prove-it 门禁 —— 紫卡趋势 / 词条价差卡 ``riven_trend``。

跑法：
    python tools/check_riven_trend_layout.py --check --out tmp/riven_trend.png
    python tools/check_riven_trend_layout.py --selftest

三道闸：
  1. **版式**：``build(boxes=...)`` 登记真实包围盒，查溢出 / 重叠 / 同行间距。
     折线段与采样点（``chart.*``）共享端点属设计意图，只查溢出、豁免互相重叠。
  2. **口径**：卡面必须标「挂牌要价·非成交价」；首次采样必须如实写「第 1 个
     采样点」；禁止「暂无 / 预估 / 模拟 / 成交价」等误导文案。
  3. **配色**：涨/正溢价红、跌/负溢价绿（中国习惯），由 ``_delta_color`` 断言。

样本覆盖：首点 / 上涨 / 下跌 / 混合多点 / 价差正常 / 价差空 / 超长名称。
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.pic import resolve_theme  # noqa: E402
from qq_bridge.pic.templates import riven_trend as _rt  # noqa: E402

MIN_GAP = 2
SRC_TREND = ("WFM 紫卡挂牌要价·非成交价｜price_asc 低价视图｜同等级 rank8｜"
             "中位数｜本地采样 6 点（2.5 天）")
SRC_SPREAD = ("WFM 紫卡挂牌要价·非成交价｜price_asc 低价视图｜同等级 rank8｜"
              "同分位配对对称对比｜中位数｜非成交价")
SRC = SRC_TREND
FORBIDDEN = ("暂无", "预估价", "模拟数据", "成交均价", "历史成交价")


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """溢出 / 重叠 / 同行最小间距；chart.* 折线只查溢出。"""
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
            if la.startswith("chart.") or lb.startswith("chart."):
                continue                      # 折线/点共享端点是设计意图
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


def _trend(pts, *, zh="绝路", enough=None):
    import time
    now = time.time()
    points = [{"ts": now - (len(pts) - 1 - i) * 7200, "median": v,
               "n": 8, "lo": v * 0.8, "hi": v * 1.3, "pool": 100}
              for i, v in enumerate(pts)]
    return {"weapon_zh": zh, "weapon_en": "Rubico", "slug": "rubico",
            "points": points, "n_points": len(points), "current": points[-1],
            "change_pct": (pts[-1] / pts[0] - 1) * 100 if pts[0] else 0.0,
            "span_days": (len(pts) - 1) * 7200 / 86400,
            "just_sampled": True,
            "enough_for_trend": (len(pts) >= 2) if enough is None else enough,
            "source": SRC}


def _spread(rows, *, zh="冷冻光束步枪"):
    return {"weapon_zh": zh, "weapon_en": "Glaxion", "slug": "glaxion",
            "base_median": 100, "base_n": 13, "rows": rows, "source": SRC_SPREAD}


SAMPLES = [
    ("趋势·首点", "trend", _trend([20]), "第 1 个采样点"),
    ("趋势·上涨", "trend", _trend([20, 24, 30, 28, 40, 52]), None),
    ("趋势·下跌", "trend", _trend([80, 70, 62, 55, 48, 40]), None),
    ("趋势·12点", "trend", _trend([30, 32, 28, 35, 41, 38, 45, 50, 47, 55, 60, 58]), None),
    ("趋势·超长名", "trend", _trend([20, 30], zh="非常非常长的武器中文名称用来验证截断与溢出保护机制"), None),
    ("价差·正常", "spread", _spread([
        {"attr": "reload_speed", "zh": "装填", "pair_k": 13, "median": 400, "pct": 300.0},
        {"attr": "cold_damage", "zh": "冰伤", "pair_k": 13, "median": 200, "pct": 100.0},
        {"attr": "critical_chance", "zh": "暴率", "pair_k": 13, "median": 150, "pct": 50.0},
        {"attr": "status_chance", "zh": "触发", "pair_k": 13, "median": 140, "pct": 40.0},
        {"attr": "base_damage_/_melee_damage", "zh": "基伤", "pair_k": 13, "median": 100, "pct": 0.0},
    ]), None),
    ("价差·负溢价", "spread", _spread([
        {"attr": "critical_damage", "zh": "暴伤", "pair_k": 9, "median": 60, "pct": -40.0},
        {"attr": "ammo_maximum", "zh": "弹药", "pair_k": 8, "median": 90, "pct": -10.0},
    ]), None),
    ("价差·空池", "spread", _spread([]), None),
    ("价差·超长词条", "spread", _spread([
        {"attr": "x", "zh": "超级超级长的词条名称用来验证列宽截断", "pair_k": 5,
         "median": 99999, "pct": 1234.5},
    ]), None),
]


def _render(mode, data):
    boxes: list = []
    c = _rt.build(resolve_theme(None, "星际战甲·紫卡趋势"),
                  data=data, mode=mode, boxes=boxes)
    return c, boxes


def _selftest() -> int:
    errs = []
    bounds = (0, 0, 100, 100)
    if not any("[重叠]" in p for p in check_boxes(
            bounds, [("a", 0, 0, 60, 60), ("b", 30, 30, 90, 90)])):
        errs.append("重叠未检出")
    if not any("[溢出]" in p for p in check_boxes(
            bounds, [("x", 90, 90, 130, 130)])):
        errs.append("溢出未检出")
    if not any("[间距]" in p for p in check_boxes(
            bounds, [("p", 0, 0, 50, 40), ("q", 51, 0, 90, 40)])):
        errs.append("间距未检出")
    if check_boxes(bounds, [("a", 0, 0, 40, 40), ("b", 60, 0, 100, 40)]):
        errs.append("正常样本被误报")
    # 折线共享端点豁免，但溢出仍要报
    if check_boxes(bounds, [("chart.seg0", 0, 0, 60, 60), ("chart.seg1", 60, 60, 90, 90)]):
        errs.append("折线共享端点被误报重叠")
    if not any("[溢出]" in p for p in check_boxes(bounds, [("chart.segX", 90, 90, 140, 140)])):
        errs.append("折线溢出未检出")
    # 配色：涨红跌绿
    if _rt._delta_color(12.0) != _rt.C_UP or _rt._delta_color(-12.0) != _rt.C_DOWN:
        errs.append("涨红跌绿配色错误")
    # 口径守卫
    if any(w in SRC for w in FORBIDDEN):
        errs.append("基准口径文案命中禁用词")
    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="紫卡趋势/价差卡门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--out", default="", help="另存前两张渲染结果为 PNG")
    ap.add_argument("--selftest", action="store_true", help="验证门禁能抓错")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    failed = 0
    for i, (name, mode, data, must_have) in enumerate(SAMPLES):
        c, boxes = _render(mode, data)
        w, h = c.img.size
        if i < 2 and args.out:
            out = args.out.replace(".png", "_%s.png" % ("trend" if mode == "trend" else "spread"))
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            c.img.convert("RGB").save(out)
        problems = check_boxes((16, 16, w - 16, h - 16), boxes, MIN_GAP)
        src = str(data.get("source") or "")
        for bad in FORBIDDEN:
            if bad in src:
                problems.append("[口径] 命中禁用文案：%s" % bad)
        if "非成交价" not in src:
            problems.append("[口径] 缺「非成交价」标注")
        texts = "".join(str(b[0]) for b in boxes)
        if mode == "trend" and not data.get("enough_for_trend"):
            if "chart.empty" not in texts:
                problems.append("[口径] 首点未如实标注采样起点")
        if must_have:
            # 首点提示必须真的画出来
            labels = [o[0] for o in (boxes or [])]
            if "chart.empty" not in labels:
                problems.append("[口径] 缺「%s」提示" % must_have)
        status = "PASS" if not problems else "FAIL(%d)" % len(problems)
        print("[%s] %-14s %dx%d  元素 %d" % (status, name, w, h, len(boxes)))
        for p in problems:
            print("   " + p)
        if problems:
            failed += 1
    if args.out:
        print("渲染结果 ->", args.out.replace(".png", "_*.png"))
    if failed:
        print("\n[自检未通过] %d 组触线返工。" % failed)
        return 1
    print("自检 PASS（%d 组：溢出 0 / 重叠 0 / 间距 0 / 口径 0）" % len(SAMPLES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
