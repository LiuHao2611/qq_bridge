# -*- coding: utf-8 -*-
"""card-design 渲染后自检门禁 —— 灵化战甲及武器周常卡 ``incarnon`` 模板。

跑法（宿主 venv 或容器内都行）：

    python tools/check_incarnon_layout.py --check
    python tools/check_incarnon_layout.py --check --week 7 --out tmp/incarnon_ref_layout.png
    python tools/check_incarnon_layout.py --check --week 7 --ref-geom  # 逐坐标核对参考图
    python tools/check_incarnon_layout.py --all-weeks                  # 9 组 x 11 周全量
    python tools/check_incarnon_layout.py --selftest                   # 证明门禁能抓错
    python tools/check_incarnon_layout.py --icons                      # 导出真实资产目视条

检查项（对齐 card-design 六条红线）：
  * 溢出    任一叶子元素越出卡片安全区（红线 2）
  * 重叠    两包围盒在 x、y 两方向同时相交（红线 1）—— **全局两两比对**；
            胶囊/徽章与其内部文字属合法嵌套（严格包含），跳过
  * 间距    同一行带内相邻元素间隙小于 min_gap（红线 3）
  * 度量    包围盒由 ``build(boxes=...)`` 在真实绘制时登记，不另写复算公式（红线 6）
  * 坐标    参考周（wk=7）逐点核对 800×1793 实测网格；其余组合核对骨架锚点
            + 尾部区块只允许整体顺延（内容更多时），不允许各自漂移
  * 全周    ``--all-weeks`` 跑 9 组 x 11 周 = 99 组合：渲染不抛错 / 真实资产齐全 /
            评价非空 / box 自检 0 / 骨架不漂移

2026-11-05：用户指出「优化一下灵化口令，有几处细节没有对齐，严格按照我发的图去做，
全量对齐，除了背景」。本门禁随模板重写新增，以后改灵化卡必须跑，违规 ``exit 1``。

2026-11-05 续：用户追问「别的灵化武器或者战甲评价你做了没，别等到下周又要改一遍」。
实测发现旧实现只锁了参考卡那一周：19/99 组合因缺真实资产直接抛错、卡片高度有
5 种、79 组合分区坐标漂移、7 组/8 周的 curated 顺序与推荐没填。修完后新增
``--all-weeks`` 把全部 99 组合钉死，以后轮换不再需要临时改代码。

绝不降级：任何一项不过就 ``exit 1``；不吞异常、不跳过、不放宽阈值。
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /app
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.game_tools import warframe  # noqa: E402
from qq_bridge.pic import resolve_theme  # noqa: E402
from qq_bridge.pic.templates import incarnon as _inc  # noqa: E402

#: 同行相邻元素最小间隙（渲染像素）
MIN_GAP = 2

#: 2026-11-05 用户参考卡（800×1793）逐像素实测的分区坐标
REF_GEOM = {
    "H": 1793,
    "div_y": 87,
    "intro_cy": 130,
    "sec1_y": 174,
    "frames_y": 234,
    "frames_h": 268,
    "next_y": 517,
    "sec2_y": 578,
    "weapons_y": 638,
    "remain_y": 1290,
    "sec3_y": 1352,
    "list_y": 1411,
    "list_h": 306,
    "foot_div_y": 1736,
    "foot_cy": 1758,
}

#: 参考卡那一周（灵化 A 组 + 回廊 W8）
REF_WEEK = 7
#: 9 组与 11 周互质，必须 99 周才覆盖全部组合
N_WEEKS = 99
#: 逐周恒定的骨架锚点（不随内容高度变化）
FIXED_GEOM_KEYS = ("div_y", "intro_cy", "sec1_y", "frames_y", "frames_h",
                   "next_y", "sec2_y", "weapons_y", "list_h")
#: 内容更多时允许整段顺延的尾部坐标，顺延量必须完全一致
TAIL_GEOM_KEYS = ("remain_y", "sec3_y", "list_y", "foot_div_y", "foot_cy", "H")


def _contains(a, b) -> bool:
    """a 是否严格包含 b（胶囊与其内部文字/徽章属于合法嵌套）。"""
    return (a[1] <= b[1] and a[2] <= b[2] and a[3] >= b[3] and a[4] >= b[4])


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
            if _contains(a, b) or _contains(b, a):
                continue                         # 胶囊/徽章包住自己的文字，合法
            if ax0 < bx_1 and bx_0 < ax1 and ay0 < by_1 and by_0 < ay1:
                problems.append("[重叠] %s <-> %s: %s vs %s"
                                % (la, lb, (ax0, ay0, ax1, ay1), (bx_0, by_0, bx_1, by_1)))
            elif ay0 < by_1 and by_0 < ay1:      # 同一行带 → 查水平最小间距
                gap = bx_0 - ax1 if ax0 <= bx_0 else ax0 - bx_1
                if 0 <= gap < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx < %dpx"
                                    % (la, lb, gap, min_gap))
    return problems


def _load(week: int | None):
    """取当前周（或 --week 指定的周）的真实 curated 数据，不造样本。"""
    if week is not None:
        warframe._week_offset = lambda: week     # 可复现：锁到参考卡那一周
    d = warframe.incarnon_card_data()
    if not d:
        raise RuntimeError("incarnon_card_data() 返回空：curated 数据不可用")
    return d


def _render(data, out: str = ""):
    boxes: list = []
    c = _inc.build(resolve_theme(None, "星际战甲·灵化"), data=data,
                   width=800, boxes=boxes)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        c.img.convert("RGB").save(out)
    return c, boxes


def _icons() -> int:
    """把本周 3 张战甲立绘 + 5 把武器图标 + 行内图标按真实尺寸排一条，供肉眼核对。"""
    from PIL import Image, ImageDraw

    data = _load(7)
    items: list[tuple[str, str, int]] = []
    for f in data["frames"]:
        items.append(("frame " + str(f["name"]), str(f.get("icon") or ""), _inc.FRAME_ICON))
    for i, w in enumerate(data["weapons"], 1):
        items.append(("weapon %d" % i, str(w.get("icon") or ""), _inc.WEAPON_ICON))
        for p in (w.get("comment_parts") or []):
            if p.get("icon"):
                items.append(("inline icon %d" % i, str(p["icon"]), _inc.INLINE_ICON))
    z, cell = 4, 180
    sheet = Image.new("RGB", (cell * 4, (len(items) + 3) // 4 * (cell + 24)), (253, 252, 252))
    d = ImageDraw.Draw(sheet)
    miss = []
    for i, (label, path, sz) in enumerate(items):
        x, y = (i % 4) * cell, (i // 4) * (cell + 24)
        try:
            img = _inc._load_image(path, sz * z)
            if img is None:
                raise FileNotFoundError(path)
            img = img.resize((sz, sz), Image.LANCZOS)
            sheet.paste(img.convert("RGB"), (x + (cell - sz) // 2, y + 8),
                        img.getchannel("A") if img.mode == "RGBA" else None)
        except Exception as e:  # noqa: BLE001
            miss.append("%s: %s" % (label, e))
        d.text((x + 4, y + cell - 12), label, fill=(33, 36, 40))
    out = os.path.join(_ROOT, "tmp", "incarnon_icons.png")
    sheet.save(out)
    print("真实资产目视条 -> %s（%d 项）" % (out, len(items)))
    if miss:
        print("缺图：", "; ".join(miss))
        return 1
    return 0


def geom_problems(L: dict, strict: bool) -> list[str]:
    """参考周严格逐点比对；其余周只许骨架锚点恒定 + 尾部整段顺延。

    旧实现把卡片高度交给内容行数累加，79/99 组合分区坐标漂移（1643~1830）。
    现在 ``FIXED_GEOM_KEYS`` 必须逐周等于参考图，``TAIL_GEOM_KEYS`` 只允许
    内容确实更多时整体顺延（C 组 +37px），且每个尾部的顺延量必须完全一致。
    """
    if strict:
        return ["[坐标] %s: 实测 %s / 参考 %s" % (k, L.get(k), v)
                for k, v in REF_GEOM.items() if L.get(k) != v]
    bad = ["[坐标] 骨架 %s: 实测 %s / 参考 %s" % (k, L.get(k), REF_GEOM[k])
           for k in FIXED_GEOM_KEYS if L.get(k) != REF_GEOM[k]]
    deltas = {L.get(k, -10 ** 9) - REF_GEOM[k] for k in TAIL_GEOM_KEYS}
    if len(deltas) != 1 or min(deltas) < 0:
        bad.append("[坐标] 尾部区块顺延量不一致/为负：%s"
                   % {k: L.get(k) - REF_GEOM[k] for k in TAIL_GEOM_KEYS})
    return bad


def _all_weeks(out: str = "") -> int:
    """9 组 x 11 周 = 99 组合全量验收；缺资产/缺数据/版式漂移一律 exit 1。"""
    problems: list[str] = []
    heights: dict[int, int] = {}
    shifted: list[int] = []
    ok = 0
    for wk in range(N_WEEKS):
        tag = "wk%d" % wk
        try:
            data = _load(wk)
        except Exception as e:  # noqa: BLE001
            problems.append("%s 数据组装失败：%s: %s" % (tag, type(e).__name__, e))
            continue
        empty = ([str(f.get("name")) for f in data["frames"] if not f.get("comment")]
                 + [str(w.get("name")) for w in data["weapons"] if not w.get("comment")])
        if empty:
            problems.append("%s 评价缺失：%s" % (tag, empty))
        miss: list[str] = []
        for f in data["frames"]:
            if not f.get("icon") or not os.path.isfile(str(f["icon"])):
                miss.append("frame:" + str(f.get("name")))
        for w in data["weapons"]:
            if not w.get("icon") or not os.path.isfile(str(w["icon"])):
                miss.append("weapon:" + str(w.get("name")))
            for p in (w.get("comment_parts") or []):
                if p.get("icon") and not os.path.isfile(str(p["icon"])):
                    miss.append("inline:" + str(p["icon"]))
        if miss:
            problems.append("%s 真实资产缺失：%s" % (tag, miss))
        try:
            L = _inc._plan(data, 800)
            c, boxes = _render(data, out if wk == REF_WEEK else "")
        except Exception as e:  # noqa: BLE001
            problems.append("%s 渲染失败：%s: %s" % (tag, type(e).__name__, e))
            continue
        H = c.img.size[1]
        heights[H] = heights.get(H, 0) + 1
        bad = check_boxes((16, 16, 784, H - 16), boxes, MIN_GAP)
        if bad:
            problems.append("%s box 自检 %d 项：%s" % (tag, len(bad), bad[:2]))
        bad = geom_problems(L, strict=(wk == REF_WEEK))
        if bad:
            problems.append("%s 坐标：%s" % (tag, bad[:3]))
        elif H > REF_GEOM["H"]:
            shifted.append(wk)
        ok += 1

    print("灵化卡全周自检：%d/%d 组合渲染成功；高度分布 %s；顺延组合 %d 个%s"
          % (ok, N_WEEKS, heights, len(shifted),
             ("（%s）" % ",".join(map(str, shifted[:12]))) if shifted else ""))
    if out:
        print("参考周渲染 ->", out)
    if problems:
        print("全周自检 FAIL（%d 项）：" % len(problems))
        for p in problems[:40]:
            print("  " + p)
        if len(problems) > 40:
            print("  ... 另有 %d 项" % (len(problems) - 40))
        return 1
    print("全周自检 PASS（缺资产 0 / 缺评价 0 / 溢出 0 / 重叠 0 / 间距 0 / 坐标漂移 0）")
    return 0


def _selftest() -> int:
    """证明门禁不是摆设：合成样本 + 模板真实注入错误，都必须被抓到。"""
    bounds = (0, 0, 100, 100)
    bad_overlap = [("a", 0, 0, 60, 60), ("b", 30, 30, 90, 90)]
    bad_overflow = [("x", 90, 90, 130, 130)]
    bad_gap = [("p", 0, 0, 50, 40), ("q", 51, 0, 90, 40)]
    good = [("a", 0, 0, 40, 40), ("b", 60, 0, 100, 40)]
    nested = [("pill", 0, 0, 90, 40), ("pill-text", 10, 10, 60, 30)]
    errs = []
    if not any("[重叠]" in p for p in check_boxes(bounds, bad_overlap)):
        errs.append("重叠未检出")
    if not any("[溢出]" in p for p in check_boxes(bounds, bad_overflow)):
        errs.append("溢出未检出")
    if not any("[间距]" in p for p in check_boxes(bounds, bad_gap)):
        errs.append("间距未检出")
    if check_boxes(bounds, good):
        errs.append("正常样本被误报: %s" % check_boxes(bounds, good))
    if check_boxes(bounds, nested):
        errs.append("合法嵌套被误报: %s" % check_boxes(bounds, nested))

    # ---- 真模板注入：把评价行距压成 8px，多行文字必然互相重叠 ----
    data = _load(7)
    old = _inc.LINE_H
    try:
        _inc.LINE_H = 8
        _, boxes = _render(data)
        probs = check_boxes((16, 16, 784, 1780), boxes, MIN_GAP)
        if not any("[重叠]" in p for p in probs):
            errs.append("模板注入 LINE_H=8 未检出重叠")
    finally:
        _inc.LINE_H = old

    # ---- 真模板注入：把最后一列推到画布外，模板度量阶段必须直接 raise ----
    # 比事后 box 溢出更强：列可用宽变负时当场报错，绝不画出越界的字。
    old_cols = _inc.LIST_COL_X
    try:
        _inc.LIST_COL_X = (46, 184, 337, 495, 800)
        try:
            _render(data)
            errs.append("模板注入 LIST_COL_X 越界未触发报错")
        except ValueError:
            pass
    finally:
        _inc.LIST_COL_X = old_cols

    # ---- 真模板注入：把区块标题起点推到右侧，check_boxes 必须报溢出 ----
    old_sec = _inc.SEC_TEXT_X
    try:
        _inc.SEC_TEXT_X = 700
        _, boxes = _render(data)
        probs = check_boxes((16, 16, 784, 1780), boxes, MIN_GAP)
        if not any("[溢出]" in p for p in probs):
            errs.append("模板注入 SEC_TEXT_X 越界未检出溢出")
    finally:
        _inc.SEC_TEXT_X = old_sec

    # 恢复正常后必须干净
    _, boxes = _render(data)
    probs = check_boxes((16, 16, 784, 1780), boxes, MIN_GAP)
    if probs:
        errs.append("恢复后仍有 %d 项: %s" % (len(probs), probs[:3]))

    # ---- 真模板注入：图标全置空必须抛错，绝不静默降级成占位方块 ----
    # 2026-11-05 真实故障：F 组「认知 & 冲击」因文件名含 & 漏匹配，
    # 导致 19/99 组合渲染直接抛 FileNotFoundError，下周发不出卡。
    old_icon = warframe._incarnon_icon
    try:
        warframe._incarnon_icon = lambda en: None
        d2 = _load(3)                       # wk=3 = F 组，无行内图标
        try:
            _render(d2)
            errs.append("缺武器图标未触发渲染报错")
        except FileNotFoundError:
            pass
    finally:
        warframe._incarnon_icon = old_icon

    # ---- 真模板注入：漏填 frame_recommend 必须 raise，不许静默当「无推荐」----
    real_cards = warframe._incarnon_cards()
    orig_cards_fn = warframe._incarnon_cards
    saved = real_cards["frame_recommend"].pop("8", None)
    try:
        warframe._incarnon_cards = lambda: real_cards
        try:
            _load(7)
            errs.append("缺 frame_recommend[8] 未报错")
        except KeyError:
            pass
    finally:
        warframe._incarnon_cards = orig_cards_fn
        if saved is not None:
            real_cards["frame_recommend"]["8"] = saved

    # ---- 骨架断言：把这次真实犯过的「坐标漂移」放回去，检查必须报错 ----
    L0 = _inc._plan(_load(7), 800)
    if geom_problems(L0, strict=True):
        errs.append("参考周正常版式被误报坐标问题")
    drift = dict(L0)
    drift["weapons_y"] += 5
    if not any("骨架" in p for p in geom_problems(drift, strict=False)):
        errs.append("骨架锚点漂移未检出")
    uneven = dict(L0)
    uneven["foot_cy"] += 7
    if not any("顺延量" in p for p in geom_problems(uneven, strict=False)):
        errs.append("尾部顺延量不一致未检出")

    print("selftest:", "PASS" if not errs else "FAIL — " + "; ".join(errs))
    return 0 if not errs else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="灵化卡 card-design 自检门禁")
    ap.add_argument("--check", action="store_true", help="跑自检，违规 exit 1")
    ap.add_argument("--week", type=int, default=None, help="锁定周偏移（参考卡那周是 7）")
    ap.add_argument("--ref-geom", action="store_true",
                    help="核对参考图坐标（--week 7 严格逐点，其余周核对骨架锚点）")
    ap.add_argument("--out", default="", help="另存渲染结果为 PNG")
    ap.add_argument("--icons", action="store_true", help="导出真实资产目视条")
    ap.add_argument("--selftest", action="store_true", help="验证门禁能抓错")
    ap.add_argument("--all-weeks", action="store_true",
                    help="9 组 x 11 周 = 99 组合全量验收（缺资产/漂移一律 exit 1）")
    args = ap.parse_args()

    if args.icons:
        return _icons()
    if args.selftest:
        return _selftest()
    if args.all_weeks:
        return _all_weeks(args.out)

    data = _load(args.week)
    L = _inc._plan(data, 800)
    c, boxes = _render(data, args.out)
    w, h = c.img.size
    bounds = (16, 16, w - 16, h - 16)
    problems = check_boxes(bounds, boxes, MIN_GAP)

    n_icon = (sum(1 for f in data["frames"] if f.get("icon"))
              + sum(1 for x in data["weapons"] if x.get("icon"))
              + sum(1 for x in data["weapons"] for p in (x.get("comment_parts") or [])
                    if p.get("icon")))
    n_need = len(data["frames"]) + len(data["weapons"]) + sum(
        1 for x in data["weapons"] for p in (x.get("comment_parts") or []) if p.get("icon"))

    print("灵化卡 %dx%d：战甲 %d（下周 %d）/ 武器 %d / 轮回 %d×%d；真实图标 %d/%d；包围盒 %d 项"
          % (w, h, len(data["frames"]), len(data["next_frames"]), len(data["weapons"]),
             len(data["rows"]), max(len(r) for r in data["rows"]), n_icon, n_need, len(boxes)))
    if args.out:
        print("渲染结果 ->", args.out)

    if args.ref_geom:
        # 只有显式锁到参考卡那周才逐点严格比对；其余周允许内容更多时整段顺延。
        problems.extend(geom_problems(L, strict=(args.week == REF_WEEK)))

    if problems:
        print("自检 FAIL（%d 项）：" % len(problems))
        for p in problems[:40]:
            print("  " + p)
        if len(problems) > 40:
            print("  ... 另有 %d 项" % (len(problems) - 40))
        if args.check:
            print("\n[card-design 自检未通过] 触线返工：先修版式，再重新渲染 + 自检。")
            return 1
        return 0

    print("自检 PASS（溢出 0 / 重叠 0 / 间距 0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
