# -*- coding: utf-8 -*-
"""card-design 渲染后自检门禁 —— 任务订阅命中卡 ``task_alert`` 模板。

跑法（宿主 venv 或容器内都行）：

    python tools/check_task_alert_layout.py --check --out tmp/task_alert.png
    python tools/check_task_alert_layout.py --check --live      # 用真实在线裂隙数据
    python tools/check_task_alert_layout.py --selftest          # 证明门禁能抓重叠/溢出/间距

检查项（对齐 card-design 六条红线）：
  * 溢出    任一元素越出卡片安全区（红线 2）
  * 重叠    两包围盒在 x、y 两方向同时相交（红线 1）—— 全局两两比对，
            图标压到文字、标题压到右锚定倒计时都靠它抓。
  * 间距    同一行带内相邻元素间隙小于 MIN_GAP（红线 3）
  * 度量    包围盒由 ``build(boxes=...)`` 在真实绘制时登记，不另写复算公式（红线 6）

多样本覆盖动态范围：短/超长节点名、普通/钢铁、有/无纪元、有/无订阅 ID、
超长条件回显（wrap 换行后高度必须跟着重算）。

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
from qq_bridge.pic.templates import task_alert as _ta  # noqa: E402

MIN_GAP = 2

#: 固定样本：覆盖长短节点 / 普通钢铁 / 有无纪元 / 有无 ID / 超长条件
SAMPLES = [
    {"name": "标准钢铁后纪生存",
     "task": {"title": "O系钢铁生存", "tier": "Axi 后纪", "mode": "钢铁",
              "node": "虚空(雷争塔)", "planet": "虚空", "faction": "O系",
              "remain": "1小时18分"},
     "sid": "a1b2c3d4", "cond": "钢铁裂隙 · 后纪 · 生存 · 不限地图"},
    {"name": "超长节点+全能+普通",
     "task": {"title": "C系普通虚空覆涌", "tier": "Omnia 全能", "mode": "普通",
              "node": "扎里曼号(永视弧域·超长节点名称用来验证截断与溢出保护机制是否生效)",
              "planet": "扎里曼号", "faction": "C系", "remain": "9分13秒"},
     "sid": "ff001122", "cond": "普通裂隙 · 全能 · 虚空覆涌 · 扎里曼号"},
    {"name": "无纪元无ID+超长条件",
     "task": {"title": "G系钢铁中断", "tier": "", "mode": "钢铁",
              "node": "阋神星(Kala-azar)", "planet": "阋神星", "faction": "G系",
              "remain": "47分26秒"},
     "sid": "", "cond": "钢铁裂隙 · 后纪 · 生存 · 中断 · 歼灭 · 不限地图 · 补充很长很长的条件回显文本"},
    {"name": "极短倒计时",
     "task": {"title": "I系普通歼灭", "tier": "Lith 古纪", "mode": "普通",
              "node": "地球(Mantle)", "planet": "地球", "faction": "I系",
              "remain": "5秒"},
     "sid": "0000abcd", "cond": "普通裂隙 · 古纪 · 歼灭 · 地球"},
]


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """溢出 / 重叠 / 同行最小间距。boxes: (label, x0, y0, x1, y1)。"""
    problems: list[str] = []
    bx0, by0, bx1, by1 = bounds
    for label, x0, y0, x1, y1 in boxes:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越出卡片安全区 %s (bounds=%s)"
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


def _selftest() -> int:
    """证明门禁不是摆设：注入已知重叠/溢出/间距必须报错；正常样本不误报。"""
    bounds = (0, 0, 100, 100)
    bad_overlap = [("a", 0, 0, 60, 60), ("b", 30, 30, 90, 90)]
    bad_overflow = [("x", 90, 90, 130, 130)]
    bad_gap = [("p", 0, 0, 50, 40), ("q", 51, 0, 90, 40)]
    good = [("a", 0, 0, 40, 40), ("b", 60, 0, 100, 40)]
    errs = []
    if not any("[重叠]" in p for p in check_boxes(bounds, bad_overlap)):
        errs.append("重叠未检出")
    if not any("[溢出]" in p for p in check_boxes(bounds, bad_overflow)):
        errs.append("溢出未检出")
    if not any("[间距]" in p for p in check_boxes(bounds, bad_gap)):
        errs.append("间距未检出")
    if check_boxes(bounds, good):
        errs.append("正常样本被误报: %s" % check_boxes(bounds, good))

    # 模板级负例：拿真实渲染出的 boxes 把「标题压到倒计时」「元素越界」放回去，
    # 门禁必须报；去掉后必须通过。防止只测了 check_boxes 函数、没测真实版式链路。
    c, real_boxes = _render(SAMPLES[0])
    real_bounds = (_ta.PAD + 8, _ta.CARD_TOP + 8,
                   c.img.width - _ta.PAD - 8, c.img.height - _ta.PAD - 8)
    if check_boxes(real_bounds, real_boxes, MIN_GAP):
        errs.append("真实样本本身未通过")
    title_box = next(b for b in real_boxes if b[0] == "title")
    injected_overlap = real_boxes + [("__injected_overlap__", title_box[1] + 4,
                                      title_box[2], title_box[3] - 4, title_box[4])]
    if not any("[重叠]" in p for p in check_boxes(real_bounds, injected_overlap, MIN_GAP)):
        errs.append("模板级重叠未被拦住")
    injected_overflow = real_boxes + [("__injected_overflow__", real_bounds[2] - 4,
                                       real_bounds[1] + 4, real_bounds[2] + 40,
                                       real_bounds[1] + 20)]
    if not any("[溢出]" in p for p in check_boxes(real_bounds, injected_overflow, MIN_GAP)):
        errs.append("模板级溢出未被拦住")

    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


async def _live_samples() -> list[dict]:
    """用真实在线裂隙数据覆盖所有纪元/任务组合（验证真实字符串不会撑破版式）。"""
    from qq_bridge.game_tools import warframe
    from qq_bridge.task_subscribe import describe_task
    d = await warframe._worldstate(fast=True)
    fs = list(warframe._slice(d, "fissures") or [])
    out = []
    for f in fs[:12]:
        out.append({"name": "live:" + str(f.get("location") or ""),
                    "task": describe_task(f), "sid": "live0001",
                    "cond": "裂隙 · 不限地图"})
    return out or SAMPLES


def _render(sample: dict):
    boxes: list = []
    c = _ta.build(resolve_theme(None, "星际战甲·任务订阅"),
                  task=sample["task"], sub_id=sample["sid"],
                  cond_label=sample["cond"], boxes=boxes)
    return c, boxes


def main() -> int:
    ap = argparse.ArgumentParser(description="任务订阅卡 card-design 自检门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--live", action="store_true", help="用真实在线裂隙数据做样本")
    ap.add_argument("--out", default="", help="另存第一张渲染结果为 PNG")
    ap.add_argument("--selftest", action="store_true", help="验证门禁本身能抓重叠/溢出/间距")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    samples = asyncio.run(_live_samples()) if args.live else SAMPLES
    failed = 0
    for i, s in enumerate(samples):
        c, boxes = _render(s)
        w, h = c.img.size
        if i == 0 and args.out:
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            c.img.convert("RGB").save(args.out)
        bounds = (_ta.PAD + 8, _ta.CARD_TOP + 8, w - _ta.PAD - 8, h - _ta.PAD - 8)
        problems = check_boxes(bounds, boxes, MIN_GAP)
        status = "PASS" if not problems else "FAIL(%d)" % len(problems)
        print("[%s] %-28s %dx%d  元素 %d  溢出/重叠/间距 %s"
              % (status, s["name"][:28], w, h, len(boxes),
                 "0" if not problems else "%d 项" % len(problems)))
        for p in problems:
            print("   " + p)
        if problems:
            failed += 1
    if args.out:
        print("渲染结果 ->", args.out)
    if failed:
        print("\n[card-design 自检未通过] %d 组触线返工：先修版式，再重新渲染 + 自检。" % failed)
        return 1
    print("自检 PASS（%d 组样本：溢出 0 / 重叠 0 / 间距 0）" % len(samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
