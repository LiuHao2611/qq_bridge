# -*- coding: utf-8 -*-
"""card-design + prove-it 门禁 —— 紫卡融合卡 ``riven_fusion``。

跑法：
    python tools/check_riven_fusion_layout.py --check --out tmp/riven_fusion.png
    python tools/check_riven_fusion_layout.py --selftest

三道闸：
  1. **版式**：``build(boxes=...)`` 登记真实包围盒，查溢出 / 重叠 / 同行间距。
  2. **口径（诚实线，最重要）**：
       * 卡面必须标「挂牌要价·非成交价」；
       * 卡面**不得出现「按融合等级的市价 / 融合溢价 %」等编造数据**——
         WFM 紫卡拍卖**不含 fuse 字段**，本卡只给「真实中位价 + 费用公式」，
         任何融合等级市价/溢价都属虚构，禁用；
       * 禁用「暂无 / 预估 / 模拟 / 成交价」等误导文案。
  3. **配色**：费用/数值中性，无涨跌配色要求（不适用涨红跌绿）。

样本覆盖：通用指南(无中位价) / 有中位价 / 高价武器 / 超长武器名 / 最长费用行(第8次累计) / 空费用表。
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.pic import resolve_theme  # noqa: E402
from qq_bridge.pic.templates import riven_fusion as _rf  # noqa: E402

MIN_GAP = 2
SRC_OK = ("WFM 紫卡挂牌要价·非成交价（中位价）｜Warframe Wiki《Fusion》费用公式："
          "紫卡按 Rare 稀有度 EBC=30 Endo / CrBC=1,449 Credits｜"
          "WFM 拍卖不暴露 fuse 字段，本卡不编造按融合等级的实时市价")
FORBIDDEN = ("暂无", "预估价", "模拟数据", "成交均价", "历史成交价", "融合溢价", "融合市价")


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """溢出 / 重叠 / 同行最小间距。"""
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


def _cost():
    from qq_bridge.game_tools.riven_fusion import fusion_cost_table
    return fusion_cost_table()


SAMPLES = [
    ("通用指南",
     {"weapon_zh": "通用指南", "weapon_en": "Riven Fusion", "slug": "",
      "median": None, "n": 0, "lo": None, "hi": None, "cost_rows": _cost(),
      "source": SRC_OK}),
    ("绝路-中位价",
     {"weapon_zh": "绝路", "weapon_en": "Rubico", "slug": "rubico",
      "median": 20.0, "n": 7, "lo": 10.0, "hi": 45.0, "cost_rows": _cost(),
      "source": SRC_OK}),
    ("格拉克斯-高价",
     {"weapon_zh": "格拉克斯", "weapon_en": "Glaxion", "slug": "glaxion",
      "median": 2800.0, "n": 93, "lo": 90.0, "hi": 10000.0, "cost_rows": _cost(),
      "source": SRC_OK}),
    ("超长武器名",
     {"weapon_zh": "非常非常长的武器中文名称用来验证截断与溢出保护机制", "weapon_en": "SuperLongWeaponNameXyz",
      "slug": "x", "median": 150.0, "n": 30, "lo": 80.0, "hi": 300.0,
      "cost_rows": _cost(), "source": SRC_OK}),
    ("最长费用行",
     {"weapon_zh": "测试", "weapon_en": "Test", "slug": "t",
      "median": 99999.0, "n": 5, "lo": 1.0, "hi": 99999.0, "cost_rows": _cost(),
      "source": SRC_OK}),
    ("空费用表",
     {"weapon_zh": "空表", "weapon_en": "Empty", "slug": "e",
      "median": 50.0, "n": 4, "lo": 20.0, "hi": 90.0, "cost_rows": [],
      "source": SRC_OK}),
]


def _render(data):
    boxes: list = []
    c = _rf.build(resolve_theme(None, "星际战甲·紫卡融合"),
                  data=data, boxes=boxes)
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
    if check_boxes(bounds, [("a", 0, 0, 40, 40), ("b", 60, 0, 100,40)]):
        errs.append("正常样本被误报")
    # 口径守卫：把「融合溢价」塞进 source，门禁必须报错（证明守卫生效）
    bad_src = "WFM 无 fuse｜融合溢价 +85%｜非成交价"
    if not any("[口径]" in p for p in check_boxes(bounds, [])):
        pass
    problems = _check_source(bad_src)
    if not any("融合溢价" in p or "禁用文案" in p for p in problems):
        errs.append("口径守卫未拦下『融合溢价』编造数据")
    # source 缺诚实声明也必须报错
    if not any("不编造" in p or "WFM 无 fuse" in p for p in
               _check_source("WFM 挂牌要价·非成交价（中位价）")):
        errs.append("口径守卫未要求『WFM 无 fuse / 不编造』诚实声明")
    # 费用公式纯函数自检（与 wiki 核对）
    rows = _cost()
    if not (rows[0]["inc_endo"] == 30 and rows[7]["cum_credit"] == 369495):
        errs.append("费用公式与 wiki 不符")
    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


def _check_source(src: str) -> list[str]:
    problems: list[str] = []
    for bad in FORBIDDEN:
        if bad in src:
            problems.append("[口径] 命中禁用文案：%s" % bad)
    if "非成交价" not in src:
        problems.append("[口径] 缺「非成交价」标注")
    if "WFM 无 fuse" not in src and "不编造" not in src:
        problems.append("[口径] 缺『WFM 无 fuse / 不编造』诚实声明")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="紫卡融合卡门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--out", default="", help="另存渲染结果为 PNG")
    ap.add_argument("--selftest", action="store_true", help="验证门禁能抓错")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    failed = 0
    for i, (name, data) in enumerate(SAMPLES):
        c, boxes = _render(data)
        w, h = c.img.size
        if args.out:
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            c.img.convert("RGB").save(args.out)
        problems = check_boxes((16, 16, w - 16, h - 16), boxes, MIN_GAP)
        src = str(data.get("source") or "")
        problems += _check_source(src)
        # 卡面渲染文本里也不得出现编造的融合等级市价/溢价
        texts = "".join(str(b[0]) for b in boxes)
        if any(bad in texts for bad in ("融合溢价", "融合市价")):
            problems.append("[口径] 卡面出现编造的融合等级市价/溢价")
        status = "PASS" if not problems else "FAIL(%d)" % len(problems)
        print("[%s] %-12s %dx%d  元素 %d" % (status, name, w, h, len(boxes)))
        for p in problems:
            print("   " + p)
        if problems:
            failed += 1
    if args.out:
        print("渲染结果 ->", args.out)
    if failed:
        print("\n[自检未通过] %d 组触线返工。" % failed)
        return 1
    print("自检 PASS（%d 组：溢出 0 / 重叠 0 / 间距 0 / 口径 0）" % len(SAMPLES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
