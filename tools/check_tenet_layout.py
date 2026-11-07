# -*- coding: utf-8 -*-
"""card-design 渲染后自检门禁 —— 信条 / 终幕武器轮换卡（``tenet`` / ``coda``）。

跑法（宿主 venv 或容器内都行）：

    python tools/check_tenet_layout.py --check
    python tools/check_tenet_layout.py --check --ref-geom --out tmp/tenet_gate.png
    python tools/check_tenet_layout.py --check --fixture        # 离线：用真实抓取快照
    python tools/check_tenet_layout.py --selftest --fixture     # 离线：证明门禁能抓错
    python tools/check_tenet_layout.py --selftest               # 在线：容器里跑（不挂 tests/）
    python tools/check_tenet_layout.py --icons                  # 导出真实元素图标目视条

数据源**由调用方显式指定**：带 ``--fixture`` 只读 ``tests/fixtures/`` 里的真实快照，
不带则只读实时 worldstate。二者互不自动回退（禁止降级）；容器镜像不含 ``tests/``，
所以容器内一律不带 ``--fixture``，容器内带 ``--fixture`` 会明确报错而不是偷偷改数据源。

检查项（对齐 card-design 六条红线）：
  * 溢出 / 重叠 / 同行最小间距 —— ``build(boxes=...)`` 真实绘制时登记的叶子包围盒，
    全局两两比对；胶囊与其内部文字属合法嵌套（严格包含）跳过。
  * 坐标  —— ``--ref-geom`` 逐点核对用户参考卡实测的**共享骨架**（头部 + 首行两列）；
    只锁纯几何元素（分割线 / 竖条 / 卡位 / 下划线），不锁字体墨迹 bbox；
    tail（foot-div / pc-pill / 高度）按武器数推导，信条（5 把）/ 终幕（7 把）都适用。
  * 骨架  —— ``_skeleton_problems`` 锁两口令网格常量（W/COL_X/CELL_W/CELL_H/
    CELL_PITCH/CELL_Y0）逐常量相等，保证信条卡视觉与终幕卡对齐（用户 2026-11-06 要求）。
  * 资产  —— 真实武器图标 + 14 种 Wiki 真实伤害类型图标齐全且可解码；
    元素键归一；加成值带 ``+``；两口令**独立模板**（不得让 coda.build 成为 tenet.build
    薄别名）但网格骨架必须一致；档位配色锁参考卡 7 个样本。
  * 两个口令各自独立模板全覆盖 —— ``#wf 信条``（5 把）与 ``#wf 终幕``（7 把）都是同一套
    两列网格版式（2026-11-06 用户要求信条卡排版与终幕一致），都渲染并各自跑 box 自检；
    门禁拦住「两模板又共用 build」与「信条网格又漂离终幕」两类真实犯过的错。

2026-11-06 用户指令：「信条卡片的排版不对，要个终幕的一样，重做，认真点」。本门禁随
``tenet.py`` 重写新增，以后改信条/终幕卡必须跑，违规 ``exit 1``；``--selftest`` 把真实
犯过的错放回去验证检查确实会报错。

绝不降级：任何一项不过就 ``exit 1``；不吞异常、不跳过、不放宽阈值。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from qq_bridge.game_tools import warframe  # noqa: E402
from qq_bridge.pic import element_icons as _ei  # noqa: E402
from qq_bridge.pic import resolve_theme  # noqa: E402
from qq_bridge.pic.templates import coda as _coda  # noqa: E402
from qq_bridge.pic.templates import tenet as _tenet  # noqa: E402

MIN_GAP = 2

#: 用户参考卡 e4837e4b71cfc4ebf2c08549e34c028c.png 逐像素实测结构锚点。
#: 只锁**两口令共享、不依赖武器数与字体的骨架元素**（头部 + 首行两列），
#: 坐标来自 tmp/tenet_measure2.py / tenet_measure3.py；无论信条（5 把）还是终幕
#: （7 把），首行都在 CELL_Y0=244，所以这些锚点两种卡逐点一致。
#: tail（foot-div / pc-pill / 高度）由武器数推导，见 ``_geom_problems``。
REF_GEOM: dict[str, tuple] = {
    "top-div":   (50, 87, 750, 88),
    "intro-bar": (34, 124, 39, 171),
    "cell1-bar": (34, 244, 42, 397),
    "cell2-bar": (405, 244, 413, 397),
    "cell1-under": (55, 304, 378, 305),
    "cell2-under": (426, 304, 749, 305),
}
#: 结构锚点容差：参考卡每行用了分数行距（实测行首 244/407/570/734），
#: 我们的整数网格是匀速 163；首行锚点逐点核对，±1px 属参考卡取整差异，
#: 但仍逐点核对，绝不放宽到「看着差不多」。
GEOM_TOL = 1
#: 参考卡 7 个加成值实测出的三档配色（锁住 element_icons.bonus_color）
REF_BONUS_COLORS = (
    (33.1, "#215694"), (49.0, "#328B51"), (38.3, "#215694"),
    (25.7, "#51676B"), (25.6, "#51676B"), (27.7, "#51676B"),
    (26.4, "#51676B"),
)
#: 参考卡 4 种元素名实测色
REF_ELEM_COLORS = {
    "magnetic": "#657B82", "electricity": "#8B62A1",
    "toxin": "#7C8D46", "heat": "#D1701E",
}

_FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "tenet_coda_flight.json")


def _contains(a, b) -> bool:
    """a 是否严格包含 b（胶囊与其内部文字属合法嵌套）。"""
    return a[1] <= b[1] and a[2] <= b[2] and a[3] >= b[3] and a[4] >= b[4]


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
            lb, b0, b1, b2, b3 = b
            if _contains(a, b) or _contains(b, a):
                continue
            if ax0 < b2 and b0 < ax1 and ay0 < b3 and b1 < ay1:
                problems.append("[重叠] %s <-> %s: %s vs %s"
                                % (la, lb, (ax0, ay0, ax1, ay1), (b0, b1, b2, b3)))
            elif ay0 < b3 and b1 < ay1:
                gap = b0 - ax1 if ax0 <= b0 else ax0 - b2
                if 0 <= gap < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx < %dpx"
                                    % (la, lb, gap, min_gap))
    return problems


async def _load(kind: str, use_fixture: bool) -> dict:
    if use_fixture:
        with open(_FIXTURE, encoding="utf-8") as f:
            snap = json.load(f)
        warframe._WFWIKI_FLIGHT_CACHE = {"ts": 0.0, "data": None}
        warframe._WFWIKI_FLIGHT_CACHE.update({"ts": 1e18, "data": snap})
    d = await warframe.tenet_card_data(kind)
    if not d:
        raise RuntimeError("tenet_card_data(%r) 返回空：worldstate 不可用" % kind)
    return d


def _render(data: dict, out: str = ""):
    boxes: list = []
    tpl = _coda if data.get("kind") == "coda" else _tenet
    card = ("星际战甲·终幕信条武器" if data.get("kind") == "coda"
            else "星际战甲·佩兰数列信条武器")
    c = tpl.build(resolve_theme(None, card), data=data, width=800, boxes=boxes)
    if out:
        c.save(out)
    return c, boxes


def _skeleton_problems() -> list[str]:
    """两口令网格骨架必须一致：保证信条卡视觉与终幕卡对齐（用户 2026-11-06 要求）。

    锁的是几何网格常量，不是字体墨迹；信条/终幕仍是独立模块（独立 build/_plan、独立
    kind），但网格骨架（宽度/列/单元格/行距/首行）逐常量相等，避免漂移到「看起来不一样」。
    """
    problems: list[str] = []
    pairs = [
        ("W", _tenet.W, _coda.W),
        ("COL_X", _tenet.COL_X, _coda.COL_X),
        ("CELL_W", _tenet.CELL_W, _coda.CELL_W),
        ("CELL_H", _tenet.CELL_H, _coda.CELL_H),
        ("CELL_PITCH", _tenet.CELL_PITCH, _coda.CELL_PITCH),
        ("CELL_Y0", _tenet.CELL_Y0, _coda.CELL_Y0),
    ]
    for name, a, b in pairs:
        if a != b:
            problems.append("[骨架] 信条与终幕网格常量 %s 不一致：tenet=%r coda=%r；"
                            "两口令视觉必须对齐（用户要求信条卡排版与终幕一致）" % (name, a, b))
    return problems


def _asset_problems(data: dict) -> list[str]:
    problems: list[str] = []
    if _coda.build is _tenet.build or _coda._plan is _tenet._plan:
        problems.append("[资产] 信条与终幕又共用版式了：两个口令必须各自独立模板"
                        "（独立 build/_plan，coda.py 不得成为 tenet.py 的薄别名）；"
                        "但两套模板的网格骨架必须一致，保证信条卡视觉与终幕卡对齐")
    if not data.get("weapons"):
        problems.append("[资产] weapons 为空")
    if not data.get("remain"):
        problems.append("[资产] 倒计时为空")
    for i, w in enumerate(data.get("weapons") or []):
        tag = "第%d把 %s" % (i + 1, w.get("name"))
        bs = str(w.get("bonus_str") or "")
        if not bs.startswith("+"):
            problems.append("[资产] %s 加成值缺 + 号：%r" % (tag, bs))
        try:
            key = _ei.canonical(w.get("element_key") or w.get("element") or "")
            _ei.icon_path(key)
        except (KeyError, FileNotFoundError) as e:
            problems.append("[资产] %s 真实元素图标不可用：%s" % (tag, e))
        ip = str(w.get("icon") or "")
        if not ip or not os.path.isfile(ip):
            problems.append("[资产] %s 真实武器图标不可用：%r" % (tag, ip))
        else:
            try:
                from PIL import Image
                with Image.open(ip) as im:
                    im.verify()
            except Exception as e:  # noqa: BLE001
                problems.append("[资产] %s 武器图标无法解码：%s" % (tag, e))
    return problems


def _geom_problems(boxes: list, H: int, mod) -> list[str]:
    """结构锚点校验（武器数无关）。

    * 共享骨架（head + 首行两列）按 ``REF_GEOM`` 逐点核对，两口令一致；
    * tail（foot-div / pc-pill / 整体高度）由网格推导：最后一格底边 → +1 → 胶囊
      → 页脚分割线 → PC 徽章。无论信条（5 把）还是终幕（7 把）都按同一公式，保证
      尾部结构不被随手挪动。
    """
    problems: list[str] = []
    got = {lab: (x0, y0, x1, y1) for lab, x0, y0, x1, y1 in boxes}
    for lab, want in REF_GEOM.items():
        if lab not in got:
            problems.append("[坐标] 缺少参考锚点 %s" % lab)
            continue
        delta = [abs(int(a) - int(b)) for a, b in zip(got[lab], want)]
        if any(d > GEOM_TOL for d in delta):
            problems.append("[坐标] %s = %s，参考卡实测 %s（差值 %s > %dpx）"
                            % (lab, got[lab], want, delta, GEOM_TOL))

    cell_bars = [b for b in boxes if b[0].startswith("cell") and b[0].endswith("-bar")]
    if cell_bars:
        max_bottom = max(b[4] for b in cell_bars)
        exp_foot = max_bottom + 1 + mod.CAP_H + mod.FOOT_DIV_GAP
        exp_pc = exp_foot + mod.PC_TOP_OFF
        exp_H = exp_pc + mod.PC_H + mod.BOTTOM_PAD
        if "foot-div" not in got:
            problems.append("[坐标] 缺少 foot-div")
        else:
            fy = got["foot-div"][1]
            if abs(fy - exp_foot) > GEOM_TOL:
                problems.append("[坐标] foot-div y=%d 期望 %d（尾部未按网格推导）"
                                % (fy, exp_foot))
        if "pc-pill" not in got:
            problems.append("[坐标] 缺少 pc-pill")
        else:
            py = got["pc-pill"][1]
            if abs(py - exp_pc) > GEOM_TOL:
                problems.append("[坐标] pc-pill y=%d 期望 %d（PC 徽章未紧跟页脚分割线）"
                                % (py, exp_pc))
        if abs(H - exp_H) > GEOM_TOL:
            problems.append("[坐标] 卡片高度 %d 期望 %d（尾部结构推导不一致）" % (H, exp_H))
    else:
        problems.append("[坐标] 没有渲染出任何武器卡（cell*-bar）")
    return problems


def all_elements_problems(use_fixture: bool = False) -> list[str]:
    """14 种元素 × 最长加成 ``+59.9%`` 的版式压力测试。

    只用真实武器图标 / 真实元素图标，把元素键与加成值换成**边界值**做容量检查；
    这是压力用例，不是对外展示的数据，绝不写进生产数据组装路径。
    """
    data = asyncio.run(_load("coda", use_fixture))
    base = data["weapons"]
    keys = list(_ei.ELEMENT_FILES)
    weapons = []
    for i, key in enumerate(keys):
        w = dict(base[i % len(base)])
        w["element_key"] = key
        w["element"] = _ei.ELEMENT_ZH[key]
        w["bonus"] = 59.9
        w["bonus_str"] = "+59.9%"
        weapons.append(w)
    stress = dict(data)
    stress["weapons"] = weapons
    stress["remain"] = "9天23小时59分59秒"   # 最长倒计时
    try:
        _c, boxes = _render(stress)
    except Exception as e:  # noqa: BLE001
        return ["[全元素] 压力渲染抛错：%s: %s" % (type(e).__name__, e)]
    problems = ["[全元素] %s" % p
                for p in check_boxes((0, 0, _c.width, _c.height), boxes)]
    if len(boxes) < 14 * 7:
        problems.append("[全元素] 叶子包围盒只有 %d 个，14 把武器应 >= 98" % len(boxes))
    return problems


def check_kind(kind: str, use_fixture: bool, ref_geom: bool, out: str = "") -> list[str]:
    data = asyncio.run(_load(kind, use_fixture))
    problems = ["[%s] %s" % (kind, p) for p in _asset_problems(data)]
    mod = _coda if kind == "coda" else _tenet
    c, boxes = _render(data, out)
    problems += ["[%s] %s" % (kind, p)
                 for p in check_boxes((0, 0, c.width, c.height), boxes)]
    if ref_geom:
        problems += ["[%s] %s" % (kind, p) for p in _geom_problems(boxes, c.height, mod)]
    return problems, c, boxes, data


def _check(args) -> int:
    all_problems: list[str] = []
    for kind in ("tenet", "coda"):
        out = ""
        if args.out:
            out = args.out.replace(".png", "_%s.png" % kind)
        problems, c, boxes, data = check_kind(kind, args.fixture, args.ref_geom, out)
        print("%s: %d 把武器 / %dx%d / %d 个叶子包围盒 / %d 项问题"
              % (kind, len(data["weapons"]), c.width, c.height, len(boxes), len(problems)))
        for p in problems:
            print("   ", p)
        all_problems += problems
    # 两口令网格骨架必须一致（信条卡视觉与终幕卡对齐）
    all_problems += _skeleton_problems()
    # 参考卡 7 个样本的档位配色 + 4 种元素名色必须锁死
    for v, want in REF_BONUS_COLORS:
        got = _ei.bonus_color(v)
        if got != want:
            all_problems.append("[配色] 加成 %.1f%% 期望 %s，实际 %s" % (v, want, got))
    for k, want in REF_ELEM_COLORS.items():
        got = _ei.elem_color(k)
        if got != want:
            all_problems.append("[配色] 元素 %s 期望 %s，实际 %s" % (k, want, got))
    if len(_ei.ELEMENT_FILES) < 14:
        all_problems.append("[资产] 元素图标表只覆盖 %d 种，必须 >= 14" % len(_ei.ELEMENT_FILES))
    print("元素图标资产：%d/14 种" % sum(
        1 for k in _ei.ELEMENT_FILES if _safe_icon(k)))
    stress = all_elements_problems(args.fixture)
    print("全元素压力检查（14 种 × +59.9%% × 最长倒计时）：%d 项问题" % len(stress))
    for p in stress:
        print("   ", p)
    all_problems += stress
    if all_problems:
        print("\nFAIL：共 %d 项" % len(all_problems))
        return 1
    print("\nPASS：溢出 0 / 重叠 0 / 间距 0 / 坐标 0 / 资产 0")
    return 0


def _safe_icon(k: str) -> bool:
    try:
        _ei.icon_path(k)
        return True
    except Exception:
        return False


def _selftest(use_fixture: bool = True) -> int:
    """把真实犯过的错误放回去，门禁必须报错；改回来必须通过。

    ``use_fixture=False`` 供容器里跑（容器不挂 tests/，用实时 worldstate）。
    数据源由调用方显式指定，绝不自动切换。
    """
    failures: list[str] = []

    def must_fail(name, fn, expect: str):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            if expect.lower() in (type(e).__name__ + " " + str(e)).lower():
                print("  OK  %-28s -> %s: %s" % (name, type(e).__name__, str(e)[:90]))
            else:
                failures.append("%s 抛了别的错：%s: %s" % (name, type(e).__name__, e))
            return
        failures.append("%s 竟然没报错（检查等于没加）" % name)

    print("--selftest：注入真实错误，验证门禁会拦住")
    print("数据源：%s" % ("真实快照 --fixture（tests/fixtures/）" if use_fixture
                       else "实时 worldstate（不读 tests/，容器可用）"))
    # 1) 缺真实元素图标 → 必须 raise，不许画圆点/几何占位
    orig_files = dict(_ei.ELEMENT_FILES)

    def _missing_elem():
        _ei.ELEMENT_FILES["magnetic"] = "__absent_damage_icon__.png"
        try:
            data = asyncio.run(_load("coda", use_fixture))
            _render(data)
        finally:
            _ei.ELEMENT_FILES.clear()
            _ei.ELEMENT_FILES.update(orig_files)

    # 判据必须指名「真实元素图标」，不能只认 FileNotFoundError：
    # 否则若有人把 _load 的数据源写死成夹具，容器里读不到夹具抛的
    # FileNotFoundError 会被误当成「缺图标已被拦住」，门禁等于白加。
    must_fail("缺真实元素图标", _missing_elem, "真实元素图标")

    # 2) 加成值丢掉 + 号 → 资产检查必须报错
    def _no_plus():
        data = asyncio.run(_load("coda", use_fixture))
        data["weapons"][0]["bonus_str"] = "33.1%"
        ps = _asset_problems(data)
        if not any("缺 + 号" in p for p in ps):
            raise AssertionError("资产检查没抓到丢 + 号")
        raise RuntimeError("asset-check-caught 缺 + 号")

    must_fail("加成值丢 + 号", _no_plus, "asset-check-caught")

    # 3) 行距被改小 → 必然重叠（真实的对齐漂移）
    orig_pitch = _coda.CELL_PITCH

    def _row_overlap():
        _coda.CELL_PITCH = 100
        try:
            data = asyncio.run(_load("coda", use_fixture))
            _c, boxes = _render(data)
            ps = check_boxes((0, 0, 800, _c.height), boxes)
            if not any("[重叠]" in p for p in ps):
                raise AssertionError("check_boxes 没抓到行重叠")
            raise RuntimeError("box-check-caught 行重叠 %d 项" % len(ps))
        finally:
            _coda.CELL_PITCH = orig_pitch

    must_fail("行距漂移导致重叠", _row_overlap, "box-check-caught")

    # 4) 列间距被改小 → 必然重叠
    orig_cols = _coda.COL_X

    def _col_overlap():
        _coda.COL_X = (34, 240)
        try:
            data = asyncio.run(_load("coda", use_fixture))
            _c, boxes = _render(data)
            ps = check_boxes((0, 0, 800, _c.height), boxes)
            if not any("[重叠]" in p for p in ps):
                raise AssertionError("check_boxes 没抓到列重叠")
            raise RuntimeError("box-check-caught 列重叠 %d 项" % len(ps))
        finally:
            _coda.COL_X = orig_cols

    must_fail("列距漂移导致重叠", _col_overlap, "box-check-caught")

    # 5) 宽度不是 800 → 直接拒绝，不许悄悄缩放
    def _bad_width():
        data = asyncio.run(_load("coda", use_fixture))
        c, _ = _coda.build(resolve_theme(None, "星际战甲·终幕信条武器"),
                           data=data, width=900)
        raise AssertionError("width=900 竟然渲染成功 %s" % ((c.width, c.height),))

    must_fail("宽度不是 800", _bad_width, "valueerror")

    # 6) 参考坐标漂移 → --ref-geom 必须报错
    orig_y0 = _coda.CELL_Y0

    def _geom_drift():
        _coda.CELL_Y0 = 250
        try:
            data = asyncio.run(_load("coda", use_fixture))
            _c, boxes = _render(data)
            ps = _geom_problems(boxes, _c.height, _coda)
            if not any("[坐标]" in p for p in ps):
                raise AssertionError("ref-geom 没抓到卡位漂移：%s" % ps)
            raise RuntimeError("geom-check-caught 卡位漂移 %d 项" % len(ps))
        finally:
            _coda.CELL_Y0 = orig_y0

    must_fail("卡位整体漂移", _geom_drift, "geom-check-caught")

    # 6b) 信条/终幕网格骨架漂移 → --check 的 _skeleton_problems 必须报错
    #     （2026-11-06 用户要求信条卡排版与终幕一致，所以两套模板网格常量
    #      必须逐常量相等；这里防止信条又漂回单列或其它网格）
    orig_t_pitch = _tenet.CELL_PITCH

    def _skeleton_drift():
        _tenet.CELL_PITCH = 120   # 与 coda 的 163 不一致
        try:
            ps = _skeleton_problems()
            if not any("网格常量" in p for p in ps):
                raise AssertionError("骨架检查没抓到网格漂移：%s" % ps)
            raise RuntimeError("skeleton-check-caught 网格漂移 %d 项" % len(ps))
        finally:
            _tenet.CELL_PITCH = orig_t_pitch

    must_fail("信条网格与终幕不一致", _skeleton_drift, "skeleton-check-caught")

    # 7) 两个口令又合并成一套模板 → 资产检查必须报错
    #    （2026-11-06 真实犯过的错：把 coda.py 改成 tenet.py 的薄别名，
    #     用户纠正「信条和终幕是两个口令」后已拆开，这里防止再合并回去）
    orig_build = _coda.build

    def _merged_templates():
        _coda.build = _tenet.build
        try:
            data = asyncio.run(_load("coda", use_fixture))
            ps = _asset_problems(data)
            if not any("共用版式" in p for p in ps):
                raise AssertionError("资产检查没抓到两口令又合并模板：%s" % ps)
            raise RuntimeError("asset-check-caught 两口令共用版式")
        finally:
            _coda.build = orig_build

    must_fail("两口令共用版式", _merged_templates, "asset-check-caught")

    # 8) 全部改回来 → 正式 --check 必须通过
    print("\n恢复后复跑正式检查：")
    ns = argparse.Namespace(fixture=use_fixture, ref_geom=True, out="")
    rc = _check(ns)
    if rc != 0:
        failures.append("恢复后 --check --ref-geom 仍然 FAIL（自检把状态改坏了）")

    if failures:
        print("\nSELFTEST FAIL：")
        for f in failures:
            print("   ", f)
        return 1
    print("\nSELFTEST PASS：8 类真实错误全部被拦住，恢复后 0 项问题")
    return 0


def _icons(out: str) -> int:
    from PIL import Image, ImageDraw

    keys = list(_ei.ELEMENT_FILES)
    sheet = Image.new("RGB", (7 * 120, 2 * 130), (253, 251, 252))
    d = ImageDraw.Draw(sheet)
    for i, k in enumerate(keys):
        im = Image.open(_ei.icon_path(k)).convert("RGBA")
        im = im.resize((64, 64), Image.LANCZOS)
        x, y = (i % 7) * 120 + 28, (i // 7) * 130 + 10
        sheet.paste(im, (x, y), im)
        d.text((x, y + 70), k, fill=(34, 35, 46))
    sheet.save(out)
    print("元素图标目视条 ->", out, "%d 种" % len(keys))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="信条/终幕武器卡渲染后自检门禁")
    ap.add_argument("--check", action="store_true", help="渲染两种 kind 并跑全部检查")
    ap.add_argument("--ref-geom", action="store_true", help="逐点核对参考卡实测结构锚点")
    ap.add_argument("--fixture", action="store_true",
                    help="显式用真实抓取快照（离线可复现）；容器镜像不含 tests/，不要带")
    ap.add_argument("--out", default="", help="输出渲染 PNG（自动加 _tenet/_coda 后缀）")
    ap.add_argument("--selftest", action="store_true",
                    help="注入真实错误验证门禁会报错；数据源跟随 --fixture")
    ap.add_argument("--icons", default="", help="导出真实元素图标目视条到指定路径")
    args = ap.parse_args()
    if args.selftest:
        return _selftest(args.fixture)
    if args.icons:
        return _icons(args.icons)
    if args.check:
        return _check(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
