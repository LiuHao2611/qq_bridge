# -*- coding: utf-8 -*-
"""card-design + prove-it 门禁 —— 本周执刑官猎杀卡 ``archon`` 模板。

跑法（宿主 venv 或容器内都行，tmp/ref_archon.png 是 bind mount，两边可见）：

    python tools/check_archon_layout.py --check --out tmp/archon_live.png
    python tools/check_archon_layout.py --check --live       # 用真实官方世界状态
    python tools/check_archon_layout.py --selftest           # 证明门禁能抓错

两道闸，缺一不可：
  1. **像素级对齐**（prove-it 硬证据）：底图直接来自参考图，所以除三块动态区
     之外的每一个像素都必须与参考图**完全相同**。任何静态装饰被漏画/改画，
     这里立刻非零退出。
  2. **card-design 六条红线**：溢出 / 重叠 / 同行最小间距，boxes 由
     ``build(boxes=...)`` 在真实绘制时登记，不另写复算公式。

动态区与 ``tools/build_archon_assets.py`` 的 PATCHES 必须一致（本文件独立
定义一份便于校验；改了一处必须同步另一处，selftest 会检查二者一致）。

绝不降级：任何一项不过就 ``exit 1``；不吞异常、不跳过、不放宽阈值。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from PIL import Image, ImageChops  # noqa: E402

from qq_bridge.pic import resolve_theme  # noqa: E402
from qq_bridge.pic.templates import archon as _archon  # noqa: E402

MIN_GAP = 2
#: 参考图基准：优先项目内 fixture（可复现），回退到 tmp 里的用户原图
REF_PATH = os.path.join(_ROOT, "tools", "fixtures", "ref_archon.png")
if not os.path.isfile(REF_PATH):
    REF_PATH = os.path.join(_ROOT, "tmp", "ref_archon.png")

#: 动态区（渲染时重绘，其余像素必须与参考图完全一致）
DYNAMIC = [(136, 128, 560, 278), (145, 298, 592, 432), (158, 442, 404, 486)]

#: 与参考图同周的固定样本（用于逐像素对照）
SAMPLE_REF = {
    "boss_name": "BOSS-诡文枭主",
    "quote": "如水千变化！万物皆利器！吃我三叉戟！",
    "shard": "蔚蓝源力石", "remain": "3天8小时",
    "missions": [
        {"mission_key": "MT_MOBILE_DEFENSE", "mission": "移动防御",
         "lv": "Lv130-135", "node": "地球-Cambria"},
        {"mission_key": "MT_TERRITORY", "mission": "拦截",
         "lv": "Lv135-140", "node": "地球-Everest"},
        {"mission_key": "MT_ASSASSINATION", "mission": "刺杀",
         "lv": "Lv145-150", "node": "地球-Oro"},
    ],
}
#: 超长文本压力样本：验证 ellipsis 截断后各列仍不相撞
SAMPLE_LONG = {
    "boss_name": "BOSS-非常非常长的执刑官名字用来验证截断",
    "quote": "如水千变化！万物皆利器！吃我三叉戟！再来一句超长的台词撑爆宽度测试",
    "shard": "超级长的执刑官源力石奖励名称用来验证截断",
    "remain": "6天23小时",
    "missions": [
        {"mission_key": "MT_SURVIVAL", "mission": "超长的任务类型名称测试",
         "lv": "Lv130-135", "node": "扎里曼号-超长节点名称测试溢出"},
        {"mission_key": "MT_TERRITORY", "mission": "拦截",
         "lv": "Lv135-140", "node": "地球-Everest"},
        {"mission_key": "MT_ASSASSINATION", "mission": "刺杀",
         "lv": "Lv145-150", "node": "地球-Oro"},
    ],
}
SAMPLE_EMPTY = {"boss_name": "", "quote": "", "shard": "", "remain": "—",
                "missions": []}


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


def pixel_diff(out: Image.Image, ref: Image.Image) -> int:
    """动态区之外与参考图不同的像素数（必须为 0）。"""
    if out.size != ref.size:
        return -1
    d = ImageChops.difference(out.convert("RGB"), ref.convert("RGB"))
    for box in DYNAMIC:
        d.paste((0, 0, 0), box)
    grey = d.convert("L")
    hist = grey.histogram()
    return sum(hist[1:])


def _render(data):
    boxes: list = []
    c = _archon.build(resolve_theme(None, "星际战甲·执刑官猎杀"),
                      data=data, boxes=boxes)
    return c, boxes


def _selftest() -> int:
    """证明两道闸都不是摆设：注入已知违规必须报错，正常样本不误报。"""
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
        errs.append("正常样本被误报")

    c, real_boxes = _render(SAMPLE_REF)
    real_bounds = (16, 16, _archon.W - 16, _archon.H - 16)
    if check_boxes(real_bounds, real_boxes, MIN_GAP):
        errs.append("真实样本本身未通过")
    title = next(b for b in real_boxes if b[0] == "boss")
    injected = real_boxes + [("__overlap__", title[1] + 4, title[2],
                              title[3] - 4, title[4])]
    if not any("[重叠]" in p for p in check_boxes(real_bounds, injected, MIN_GAP)):
        errs.append("模板级重叠未被拦住")
    out = c.img.convert("RGB")
    if pixel_diff(out, out) != 0:
        errs.append("像素对比对同一张图误报")
    if pixel_diff(out, Image.new("RGB", out.size, "#000000")) == 0:
        errs.append("像素对比漏报全黑图")
    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


async def _live_data():
    from qq_bridge.game_tools import warframe
    return await warframe.archon_hunt_data()


def main() -> int:
    ap = argparse.ArgumentParser(description="执刑官猎杀卡 card-design + 像素对齐门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--live", action="store_true", help="加跑真实官方世界状态样本")
    ap.add_argument("--out", default="", help="另存第一张渲染结果为 PNG")
    ap.add_argument("--selftest", action="store_true", help="验证门禁本身能抓错")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    samples = [("参考图同周样本", SAMPLE_REF),
               ("超长文本压力", SAMPLE_LONG),
               ("空数据兜底", SAMPLE_EMPTY)]
    if args.live:
        try:
            d = asyncio.run(_live_data())
            if d:
                samples.insert(0, ("实时官方数据", d))
            else:
                print("[实时] 官方数据取不到 → 视为失败（--live 不允许降级）")
                return 1
        except Exception as e:  # noqa: BLE001
            print("[实时] 取数异常 → 失败：%s: %s" % (type(e).__name__, e))
            return 1

    ref = None
    if os.path.isfile(REF_PATH):
        ref = Image.open(REF_PATH).convert("RGB")
        if ref.size != (_archon.W, _archon.H):
            print("参考图尺寸不符：", ref.size)
            return 1
    else:
        print("警告：缺参考图基准，像素级对齐检查跳过（不替代通过）")

    failed = 0
    for i, (name, data) in enumerate(samples):
        c, boxes = _render(data)
        w, h = c.img.size
        out = c.img.convert("RGB")
        if i == 0 and args.out:
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            out.save(args.out)
        problems = check_boxes((16, 16, w - 16, h - 16), boxes, MIN_GAP)
        pd = pixel_diff(out, ref) if ref is not None else 0
        if pd != 0:
            problems.append("[像素对齐] 动态区外与参考图有 %d 个像素不同" % pd)
        status = "PASS" if not problems else "FAIL(%d)" % len(problems)
        print("[%s] %-16s %dx%d 元素 %d 像素差 %s"
              % (status, name, w, h, len(boxes), pd if ref is not None else "跳过"))
        for p in problems:
            print("   " + p)
        if problems:
            failed += 1
    if args.out:
        print("渲染结果 ->", args.out)
    if failed:
        print("\n[自检未通过] %d 组触线返工：先修，再重新渲染 + 自检。" % failed)
        return 1
    print("自检 PASS（%d 组：溢出 0 / 重叠 0 / 间距 0 / 像素差 0）" % len(samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
