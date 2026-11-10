# -*- coding: utf-8 -*-
"""card-design 渲染后自检门禁 —— 虚空裂隙卡 ``fissure`` 模板（速览 / 普通 / 钢铁 三卡）。

跑法（宿主 venv 或容器内都行）：

    python tools/check_fissure_layout.py --check
    python tools/check_fissure_layout.py --check --mode steel --out tmp/fissure_steel.png
    python tools/check_fissure_layout.py --check --live            # 用真实在线裂隙数据
    python tools/check_fissure_layout.py --planets                 # 15 个星球图标 26px 目视条
    python tools/check_fissure_layout.py --selftest                # 证明门禁能抓重叠/溢出

检查项（对齐 card-design 六条红线）：
  * 溢出    任一元素越出卡片安全区（红线 2）
  * 重叠    两包围盒在 x、y 两方向同时相交（红线 1）—— **全局两两比对**，
            不做「同层」过滤：图标压到节点文字、节点压到倒计时都靠它抓。
  * 间距    同一行带内相邻元素间隙小于 min_gap（红线 3）
  * 度量    包围盒由 ``build(boxes=...)`` 在真实绘制时登记，不另写复算公式（红线 6）

2026-11-05：用户指出「裂隙卡也缺少对应星球图标，优化一下，包括钢铁裂隙、普通裂隙」。
三卡（速览/普通/钢铁）共用本模板，星球图标从 ``pic.item_icons`` 预取真实奖杯
（``<Planet>TrophyBronze``，裁切表见 ``item_icons.planet_crop``），本门禁同步新增。
以后改裂隙卡必须跑本脚本，违规 ``exit 1``。

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
from qq_bridge.pic.templates import fissure as _fissure  # noqa: E402

#: 同行相邻元素最小间隙（渲染像素）
MIN_GAP = 2

#: 固定样本：15 个当前会出现的星球全覆盖（含非球体奖杯 Deimos / Kuva Fortress / Void / Zariman），
#: 气泡名字故意取长短不一，验证图标右边界不会因为气泡变长而漂移。
SAMPLE_GROUPS = [
    {"label": "古纪(T1)", "rows": [
        {"bubble": "G系钢铁间谍", "node": "火星(Arval)", "remain": "32分59秒", "planet_en": "Mars"},
        {"bubble": "G系钢铁虚空洪流", "node": "扎里曼号(永视弧域)", "remain": "20分34秒", "planet_en": "Zariman"},
    ]},
    {"label": "前纪(T2)", "rows": [
        {"bubble": "C系钢铁防御", "node": "火卫一(Gulliver)", "remain": "1小时19分", "planet_en": "Phobos"},
        {"bubble": "G系钢铁破坏", "node": "谷神星(Ker)", "remain": "1小时8分", "planet_en": "Ceres"},
        {"bubble": "C系普通捕获", "node": "欧罗巴(Ose)", "remain": "9分13秒", "planet_en": "Europa"},
    ]},
    {"label": "中纪(T3)", "rows": [
        {"bubble": "C系钢铁挖掘", "node": "海王星(Despina)", "remain": "34分36秒", "planet_en": "Neptune"},
        {"bubble": "G系钢铁拦截", "node": "天王星(Umbriel)", "remain": "47分26秒", "planet_en": "Uranus"},
        {"bubble": "M系普通生存", "node": "火卫二(Perso)", "remain": "45分29秒", "planet_en": "Deimos"},
    ]},
    {"label": "后纪(T4)", "rows": [
        {"bubble": "O系钢铁防御", "node": "虚空(雷争塔)", "remain": "1小时18分", "planet_en": "Void"},
        {"bubble": "I系钢铁防御", "node": "阋神星(Kala-azar)", "remain": "1小时34分", "planet_en": "Eris"},
        {"bubble": "O系普通生存", "node": "月球(Yuvarium)", "remain": "22分3秒", "planet_en": "Lua"},
    ]},
    {"label": "安魂(T5)", "rows": [
        {"bubble": "G系钢铁防御", "node": "赤毒要塞(Nabuk)", "remain": "5分0秒", "planet_en": "Kuva Fortress"},
    ]},
    {"label": "全能(T6)", "rows": [
        {"bubble": "M系钢铁炼金", "node": "火卫二(Cambire)", "remain": "23分10秒", "planet_en": "Deimos"},
        {"bubble": "G系普通捕获", "node": "地球(Mantle)", "remain": "15分53秒", "planet_en": "Earth"},
        {"bubble": "C系普通破坏", "node": "木星(Thebe)", "remain": "2分29秒", "planet_en": "Jupiter"},
        {"bubble": "G系普通强袭", "node": "赛德娜(Charybdis)", "remain": "1小时16分", "planet_en": "Sedna"},
        {"bubble": "C系普通生存", "node": "冥王星(Palus)", "remain": "19分6秒", "planet_en": "Pluto"},
    ]},
]
SAMPLE_INTRO = "矿工的日常："
SAMPLE_FOOTERS = ['*输入"裂隙"可以筛选普通及钢铁裂隙。',
                  '*输入"钢铁裂隙"可以查看全部钢铁裂隙。']


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
                                % (la, lb, (ax0, ay0, ax1, ay1), (bx_0, by_0, bx_1, by_1)))
            elif ay0 < by_1 and by_0 < ay1:      # 同一行带 → 查水平最小间距
                gap = bx_0 - ax1 if ax0 <= bx_0 else ax0 - bx_1
                if 0 <= gap < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx < %dpx"
                                    % (la, lb, gap, min_gap))
    return problems


async def _load(live: bool, mode: str):
    if live:
        from qq_bridge.game_tools import warframe
        data = await warframe.fissures_data(mode)
        groups = data.get("groups") or []
        intro, footers, title = data.get("intro", ""), data.get("footers", []), data.get("title", "虚空裂隙")
    else:
        groups = [dict(g, rows=[dict(r) for r in g["rows"]]) for g in SAMPLE_GROUPS]
        intro, footers = SAMPLE_INTRO, list(SAMPLE_FOOTERS)
        title = {"quick": "速览虚空裂隙", "normal": "普通虚空裂隙",
                 "steel": "钢铁虚空裂隙"}.get(mode, "虚空裂隙")
    await item_icons.ensure_fissure_icons(groups)   # 预取真实星球图标（取不到回落圆点）
    return groups, intro, footers, title


def _render(groups, intro, footers, title):
    boxes: list = []
    c = _fissure.build(resolve_theme(None, "星际战甲·虚空裂隙"), groups=groups, title=title,
                       intro=intro, footers=footers, width=800, boxes=boxes)
    return c, boxes


def _selftest() -> int:
    """证明门禁不是摆设：注入已知重叠/溢出必须报错；正常样本必须不误报。"""
    bounds = (0, 0, 100, 100)
    bad_overlap = [("a", 0, 0, 60, 60), ("b", 30, 30, 90, 90)]
    bad_overflow = [("x", 90, 90, 130, 130)]
    bad_gap = [("p", 0, 0, 50, 40), ("q", 51, 0, 90, 40)]      # 间距 1px < 2px
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
    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


def _planets() -> int:
    """把 15 个星球图标按卡片真实尺寸（26px、去底座裁切）排一条，供肉眼核对。"""
    from PIL import Image, ImageDraw
    names = ["Ceres", "Deimos", "Earth", "Eris", "Europa", "Jupiter", "Kuva Fortress", "Lua",
             "Mars", "Neptune", "Phobos", "Pluto", "Sedna", "Void", "Zariman"]
    SZ, Z = _fissure.PLANET_SZ, 6
    cell = SZ * Z
    sheet = Image.new("RGB", (5 * cell, 3 * (cell + 24)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    miss = []
    for i, p in enumerate(names):
        f = f"{p.replace(' ', '')}TrophyBronze.png"
        path = item_icons._wi.icon_cache_path(f)
        if not path:
            miss.append(p)
            continue
        img = Image.open(path).convert("RGBA")
        W, H = img.size
        cr = item_icons.planet_crop(p)
        img = img.crop((int(W * cr[0]), int(H * cr[1]), int(W * cr[2]), int(H * cr[3])))
        img.thumbnail((SZ, SZ), Image.LANCZOS)
        sq = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
        sq.alpha_composite(img, ((SZ - img.width) // 2, (SZ - img.height) // 2))
        sq = sq.resize((cell, cell), Image.NEAREST)
        x, y = (i % 5) * cell, (i // 5) * (cell + 24)
        sheet.paste(sq.convert("RGB"), (x, y), sq)
        d.text((x + 2, y + cell + 3), p, fill=(0, 0, 0))
    out = os.path.join(_ROOT, "tmp", "fissure_planets.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out)
    print("星球图标（%dpx）-> %s" % (SZ, out))
    if miss:
        print("缺图标：", ", ".join(miss))
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="裂隙卡 card-design 自检门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--live", action="store_true", help="使用真实在线裂隙数据")
    ap.add_argument("--mode", default="steel", choices=["quick", "normal", "steel"],
                    help="--live 时的卡片模式（默认 steel）")
    ap.add_argument("--out", default="", help="另存渲染结果为 PNG")
    ap.add_argument("--planets", action="store_true", help="导出 15 个星球图标目视条")
    ap.add_argument("--selftest", action="store_true", help="验证门禁本身能抓重叠/溢出/间距")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.planets:
        return _planets()

    groups, intro, footers, title = asyncio.run(_load(args.live, args.mode))
    c, boxes = _render(groups, intro, footers, title)
    w, h = c.img.size
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        c.img.convert("RGB").save(args.out)

    bounds = (16, 16, w - 16, h - 16)
    problems = check_boxes(bounds, boxes, MIN_GAP)

    rows = sum(len(g["rows"]) for g in groups)
    icons = sum(1 for g in groups for r in g["rows"] if r.get("_planet_icon"))
    print("裂隙卡 %s %dx%d：%d 组 / %d 行；星球图标 %d/%d"
          % (title, w, h, len(groups), rows, icons, rows))
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
