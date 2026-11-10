# -*- coding: utf-8 -*-
"""card-design + prove-it 门禁 —— 紫卡倾向卡（DE 44.1.3 后统一 1.2）。

跑法：
    python tools/check_disp_layout.py --check --out tmp/disp_card.png
    python tools/check_disp_layout.py --selftest

两道闸：
  1. **版式**：``guide.build(boxes=...)`` 登记 disp 面板真实包围盒，
     ``check_boxes`` 查溢出 / 重叠 / 同行最小间距，违规 exit 1。
  2. **口径**：所有对外文本不得再出现旧倾向系统的星级点阵、五档点评、
     「最新倾向」角标、以及会误导的「热门武器不建议投入」。任何一处复活
     立刻 exit 1 —— 这是 2026-10-22 DE 移除倾向差异后最容易回潮的错误。

样本覆盖：真实口令输出 / 无历史对照 / 超长武器名 / 纯文本降级 / 紫卡分析。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.game_tools import _strip_markers, warframe  # noqa: E402
from qq_bridge.pic import resolve_theme  # noqa: E402
from qq_bridge.pic.templates import guide as _guide  # noqa: E402

MIN_GAP = 2

#: 旧口径黑名单一经出现即失败（含星级点阵、分档点评、过期角标）
FORBIDDEN = (
    "●●●●○", "●○○○○", "倾向极低：热门武器", "倾向极高：冷门武器",
    "最新倾向", "毕业裂罅卡最值钱", "(0.85)", "(0.70)", "(1.35)",
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


def _render(body: str):
    boxes: list = []
    c = _guide.build(resolve_theme(None, "星际战甲·紫卡倾向"),
                     body=body, boxes=boxes)
    return c, boxes


def _stale(body: str) -> list[str]:
    """旧口径守卫：返回命中的过期片段。"""
    return [w for w in FORBIDDEN if w in body]


def _selftest() -> int:
    """证明两道闸都不是摆设。"""
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
    # 把旧口径放回去，守卫必须报错；去掉后必须通过
    if not _stale("@foot|· 倾向极低：热门武器，紫卡几乎无提升，不建议投入。"):
        errs.append("旧口径未被拦住")
    good = asyncio.run(warframe.riven_disposition_text("绝路"))
    if _stale(good):
        errs.append("真实输出命中旧口径：%s" % _stale(good))
    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="紫卡倾向卡 card-design + 口径门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--out", default="", help="另存第一张渲染结果为 PNG")
    ap.add_argument("--selftest", action="store_true", help="验证门禁本身能抓错")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    real = asyncio.run(warframe.riven_disposition_text("绝路"))
    real_no_icon = "\n".join(
        ln.rsplit("|", 1)[0] if ln.startswith("@disp|") and ln.count("|") >= 4 else ln
        for ln in real.splitlines())
    samples = [
        ("真实口令·绝路", real),
        ("无官方图标回落", real_no_icon),
        ("无历史对照", "@ttl|紫卡倾向（已统一）|book\n"
                    "@sub|2026-10-22 起，全武器统一 1.20|24\n"
                    "@disp|新武器|主武器|1.20|\n"
                    "@foot|· 现行规则下所有武器紫卡同强度。"),
        ("超长武器名", "@ttl|紫卡倾向（已统一）|book\n"
                   "@disp|非常非常长的武器中文名称用来验证面板宽度与截断|近战|1.20|"
                   "改动前 1.55 → 词条约 -23%|\n"
                   "@foot|· 现行规则下所有武器紫卡同强度。"),
    ]
    failed = 0
    for i, (name, body) in enumerate(samples):
        c, boxes = _render(body)
        w, h = c.img.size
        out = c.img.convert("RGB")
        if i == 0 and args.out:
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            out.save(args.out)
        problems = check_boxes((16, 16, w - 16, h - 16), boxes, MIN_GAP)
        stale = _stale(body) + _stale(_strip_markers(body))
        if stale:
            problems.append("[口径] 命中过期倾向文案：%s" % ", ".join(sorted(set(stale))))
        # 现行倍率必须出现在纯文本降级里
        plain = _strip_markers(body)
        if "1.20" not in plain or "全武器统一" not in plain:
            problems.append("[口径] 纯文本降级缺「1.20 / 全武器统一」")
        status = "PASS" if not problems else "FAIL(%d)" % len(problems)
        print("[%s] %-18s %dx%d  元素 %d" % (status, name, w, h, len(boxes)))
        for p in problems:
            print("   " + p)
        if problems:
            failed += 1

    # 其它对外输出也不能再出现旧口径
    for label, text in (
        ("紫卡分析", asyncio.run(warframe.riven_analysis_text("绝路"))),
        ("模拟开卡", asyncio.run(warframe.sim_riven_text())),
        ("帮助文本", warframe.help_text()),
    ):
        stale = _stale(text)
        if stale:
            print("[FAIL] %s 命中旧口径：%s" % (label, stale))
            failed += 1
        else:
            print("[PASS] %-18s 未命中旧口径" % label)

    if args.out:
        print("渲染结果 ->", args.out)
    if failed:
        print("\n[自检未通过] %d 组触线返工。" % failed)
        return 1
    print("自检 PASS（%d 组：溢出 0 / 重叠 0 / 间距 0 / 旧口径 0）" % len(samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
