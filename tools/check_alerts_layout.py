# -*- coding: utf-8 -*-
"""card-design 渲染后自检门禁 —— 警报任务卡 ``alerts`` 模板。

跑在容器里（需要 qq_bridge 包 + Pillow）：

    python /app/tools/check_alerts_layout.py --check
    python /app/tools/check_alerts_layout.py --check --out /app/tmp/alerts.png
    python /app/tools/check_alerts_layout.py --check --live   # 用真实在线警报数据

检查项（对齐 card-design 六条红线）：
  * 溢出    任一元素越出内容安全区（红线 2）
  * 重叠    两包围盒在 x、y 两方向同时相交（红线 1）—— **全局两两比对**，
            不做「同层」过滤，否则页脚插画压到末条现金这类跨层碰撞会漏检。
  * 间距    同行相邻元素间隙小于 min_gap（红线 3）
  * 度量    包围盒由 ``build(boxes=...)`` 在真实绘制时登记，不另写复算公式（红线 6）

2026-11-05 新增：用户指出「福马/月华腩/星球/现金没有真实图标」，以及「顶部警报符号不对、
底部说明和小人都去掉」。补图标时本门禁抓到两处真实版式缺陷（末条后多算 ALERT_GAP 导致
页脚插画越出底边、插画压住末条现金），随后按用户要求**整块删除页脚/PC 徽章/吉祥物**，
标题改用参考卡量出的真警报徽标（黑圆肩倒三角 + 白感叹号，31×25）。
以后改警报卡必须跑本脚本，违规 ``exit 1``。

绝不降级：任何一组不过就 ``exit 1``；不吞异常、不跳过、不放宽阈值。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /app
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.pic import resolve_theme  # noqa: E402
from qq_bridge.pic import item_icons  # noqa: E402
from qq_bridge.pic.templates import alerts as _alerts  # noqa: E402

#: 同行相邻元素最小间隙（渲染像素）
MIN_GAP = 2

#: 固定样本：覆盖 福马 / 月华腩 / 欧罗金细胞 / 神经传感器 / 无物品 五种奖励，
#: 以及 阋神星 / 天王星 / 赤毒要塞 / 地球 四种星球。
SAMPLE_ALERTS = [
    {"tag": "C系防御", "color": "#2E86C1", "node": "Styx（阋神星）",
     "remain": "剩余 11天17小时", "reward": "3x 福马", "reward_en": "Forma",
     "planet_en": "Eris", "credits": "25000x 现金"},
    {"tag": "G系歼灭", "color": "#D35400", "node": "Taveuni（阋神星）",
     "remain": "剩余 1天3小时", "reward": "1x 月华腩", "reward_en": "Moonflesh",
     "planet_en": "Eris", "credits": "18000x 现金"},
    {"tag": "I系生存", "color": "#6C3483", "node": "Tethys（天王星）",
     "remain": "剩余 6小时4分", "reward": "2x 欧罗金细胞", "reward_en": "Orokin Cell",
     "planet_en": "Uranus", "credits": "12000x 现金"},
    {"tag": "C系移动防御", "color": "#2E86C1", "node": "Kuva Fortress（赤毒要塞）",
     "remain": "剩余 15分33秒", "reward": "1x 神经传感器", "reward_en": "Neural Sensor",
     "planet_en": "Kuva Fortress", "credits": "9000x 现金"},
    {"tag": "G系破坏", "color": "#D35400", "node": "Coba（地球）",
     "remain": "剩余 2天8小时", "reward": "无物品奖励", "reward_en": "",
     "planet_en": "Earth", "credits": "15000x 现金"},
]


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """溢出 / 重叠 / 同行最小间距。boxes: (label, x0, y0, x1, y1)。"""
    problems: list[str] = []
    bx0, by0, bx1, by1 = bounds

    for label, x0, y0, x1, y1 in boxes:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越出内容安全区 %s (bounds=%s)"
                            % (label, (x0, y0, x1, y1), bounds))

    for i, a in enumerate(boxes):
        la, ax0, ay0, ax1, ay1 = a
        for b in boxes[i + 1:]:
            lb, bx_0, by_0, bx_1, by_1 = b
            if ax0 < bx_1 and bx_0 < ax1 and ay0 < by_1 and by_0 < ay1:
                problems.append("[重叠] %s <-> %s: %s vs %s"
                                % (la, lb, (ax0, ay0, ax1, ay1), (bx_0, by_0, bx_1, by_1)))
            elif ay0 < by_1 and by_0 < ay1:      # 同一行带 → 查水平最小间距
                gap = bx_0 - ax1 if ax0 <= bx_0 else ax0 - bx_1
                if 0 <= gap < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx < %dpx"
                                    % (la, lb, gap, min_gap))
    return problems


async def _render(live: bool):
    if live:
        from qq_bridge.game_tools import warframe
        data = await warframe.alerts_data()
        alerts = data.get("alerts") or []
    else:
        alerts = [dict(a) for a in SAMPLE_ALERTS]
    await item_icons.ensure_alerts_icons(alerts)      # 预取真实图标（失败回落几何）
    boxes: list = []
    c = _alerts.build(resolve_theme(None, "警报任务"), alerts=alerts,
                      title="警报任务", width=800, boxes=boxes)
    return c, boxes, alerts


def _selftest() -> int:
    """证明门禁不是摆设：注入已知重叠/溢出，必须报错；正常样本必须不误报。"""
    bounds = (0, 0, 100, 100)
    bad_overlap = [("a", 0, 0, 60, 60), ("b", 30, 30, 90, 90)]
    bad_overflow = [("x", 90, 90, 130, 130)]
    good = [("a", 0, 0, 40, 40), ("b", 60, 0, 100, 40)]
    errs = []
    if not any("[重叠]" in p for p in check_boxes(bounds, bad_overlap)):
        errs.append("重叠未检出")
    if not any("[溢出]" in p for p in check_boxes(bounds, bad_overflow)):
        errs.append("溢出未检出")
    if check_boxes(bounds, good):
        errs.append("正常样本被误报: %s" % check_boxes(bounds, good))
    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="警报卡 card-design 自检门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--live", action="store_true", help="使用真实在线警报数据")
    ap.add_argument("--out", default="", help="另存渲染结果为 PNG")
    ap.add_argument("--selftest", action="store_true", help="验证门禁本身能抓重叠/溢出")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    c, boxes, alerts = asyncio.run(_render(args.live))
    w, h = c.img.size
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        c.img.convert("RGB").save(args.out)

    bounds = (14, 10, w - 14, h - 10)
    problems = check_boxes(bounds, boxes, MIN_GAP)

    icons = sum(1 for a in alerts if a.get("_reward_icon"))
    planets = sum(1 for a in alerts if a.get("_planet_icon"))
    print("警报卡 %dx%d，%d 条；真实图标：奖励 %d / 星球 %d / 现金 %s"
          % (w, h, len(alerts), icons, planets, alerts[0].get("_credits_icon") if alerts else "-"))
    if args.out:
        print("渲染结果 ->", args.out)

    if problems:
        print("自检 FAIL（%d 项）：" % len(problems))
        for p in problems:
            print("  " + p)
        if args.check:
            print("\n[card-design 自检未通过] 触线返工：先修版式，再重新渲染 + 自检。")
            return 1
        return 0

    print("自检 PASS（溢出 0 / 重叠 0 / 间距 0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
