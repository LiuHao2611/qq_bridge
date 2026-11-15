# -*- coding: utf-8 -*-
"""宗门卡验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_faction_card.py     # 自带 runner，不依赖 pytest

覆盖：
  1. 模板注册（doupo_faction + 中文显示名）；
  2. build_faction_data 与 core/factions 同源（名称/据点/卡池/统计/可转宗门）；
  3. 出图：PNG、宽 720、高度 == _measure（底部不被裁）、非空白；
  4. **check_boxes 自检**：文本包围盒溢出 0 / 重叠 0 / 相邻间距 0；
  5. 卡片外 12px 页边无墨迹（不越界出血）。

产物：tmp/doupo_faction_cards/*.png，供人工视觉核对。
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(REPO_ROOT, "tmp", "doupo_faction_cards")
os.makedirs(OUT_DIR, exist_ok=True)
_CACHE_TMP = tempfile.mkdtemp(prefix="doupo_faction_")


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import card, core, config as cfg  # noqa: E402
from doupo import factions as fac  # noqa: E402


def _pic():
    pic = card._load_pic()
    assert pic is not None, "pic 包加载失败（宗门卡不可用）"
    pc = pic.pic_cache
    import pathlib
    pc.dir = pathlib.Path(_CACHE_TMP) / "pic_cache"
    pc._loaded = False
    pc._index = {}
    return pic


def _tpl():
    _pic()          # 确保独立 pic 包已注册到 sys.modules（否则子模块 import 失败）
    return importlib.import_module(card._PKG_NAME + ".templates.doupo_faction")


def _theme():
    _pic()
    return importlib.import_module(card._PKG_NAME + ".theme").get("light")


def _player(lv: int = 40, fid: str = "yunlanzong"):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["faction"] = fid
    p["faction_name"] = fac.FACTIONS[fid]["name"]
    p["loadout"] = []
    return p


# ---------------------------------------------------------------------------
# 1. 注册 + 数据同源
# ---------------------------------------------------------------------------
def test_template_registered():
    pic = _pic()
    assert "doupo_faction" in pic.list_templates(), "宗门卡模板未注册"
    assert pic.TEMPLATE_LABELS.get("doupo_faction") == "斗破宗门卡"
    assert "doupo_skills" in pic.list_templates(), "斗技卡必须保留"


def test_build_faction_data_matches_source():
    p = _player(40)
    d = card.build_faction_data(p)
    fd = fac.FACTIONS["yunlanzong"]
    assert d["name"] == fd["name"] == "云岚宗"
    assert d["base"] == fd["base"]
    assert d["pool"] == "新手池"
    assert d["npcs"] == fd["npcs"]
    assert d["flames"] == fd["flames"]
    # 斗技统计与本门池子一致（三阶之和 == 总门数）
    assert d["total_skills"] == len(core.skill_pool_sorted(p))
    assert sum(d["tier_counts"].values()) == d["total_skills"]
    # 可转宗门：只列已开放卡池，且每组都有门数
    assert d["switch_cost"] == core.faction_switch_cost(p) > 0
    open_cn = {"starter": "新手池", "advanced": "进阶池", "hidden": "隐世池"}
    want = [open_cn[x] for x in core.faction_pools_open(p)]
    assert [g["pool"] for g in d["groups"]] == want
    for g in d["groups"]:
        assert g["names"] and all("门" in n for n in g["names"])


def test_high_level_opens_all_three_pools():
    d = card.build_faction_data(_player(95))
    assert [g["pool"] for g in d["groups"]] == ["新手池", "进阶池", "隐世池"]


# ---------------------------------------------------------------------------
# 2. check_boxes：溢出 / 重叠 / 间距
# ---------------------------------------------------------------------------
def _overlap_2d(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _check_boxes(bounds, boxes, min_gap: int = 2) -> list[str]:
    """与 card-design `check_boxes` 同规则：溢出 / 两方向重叠 / 同行间距过小。"""
    bx0, by0, bx1, by1 = bounds
    problems: list[str] = []
    items = list(boxes)
    for label, x0, y0, x1, y1 in items:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越界: (%d,%d,%d,%d) bounds=%s"
                            % (label, x0, y0, x1, y1, bounds))
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, a = items[i][0], items[i][1:]
            lb, b = items[j][0], items[j][1:]
            if _overlap_2d(a, b):
                problems.append("[重叠] %s <-> %s: %s vs %s" % (la, lb, a, b))
            if a[1] < b[3] and b[1] < a[3]:
                gap_x = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gap_x < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx" % (la, lb, gap_x))
    return problems


def test_layout_passes_check_boxes():
    tpl, t = _tpl(), _theme()
    for lv in (40, 95):
        data = card.build_faction_data(_player(lv))
        boxes: list[tuple] = []
        c = tpl.build(t, data=data, width=720, boxes=boxes)
        assert len(boxes) > 20, "没收集到文本包围盒，自检会假通过"
        h = c.height
        problems = _check_boxes((12, 12, 720 - 12, h - 12), boxes)
        assert not problems, "宗门卡触线：\n" + "\n".join(problems[:8])


# ---------------------------------------------------------------------------
# 3. 出图：尺寸 / 高度 / 非空白 / 页边无墨迹
# ---------------------------------------------------------------------------
def test_render_real_card_and_preview():
    p = _player(40)
    png = asyncio.run(card.render_faction(p))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
    with open(os.path.join(OUT_DIR, "real_yunlanzong.png"), "wb") as f:
        f.write(png)

    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png)).convert("RGB")
    assert img.width == 720, "卡片宽度异常：%d" % img.width
    data = card.build_faction_data(p)
    assert img.height == _tpl()._measure(data, 720), (
        "卡片高度 != _measure（底部会裁/留白）：%d vs %d"
        % (img.height, _tpl()._measure(data, 720)))

    # 非空白
    small = img.resize((80, 80)).tobytes()
    colors = len({small[i:i + 3] for i in range(0, len(small), 3)})
    assert colors > 20, "疑似空白卡片"

    # 页边 12px 无墨迹（不越界出血）
    w, h = img.size
    px = img.load()

    def dark_in(x0, y0, x1, y1):
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                r, g, b = px[xx, yy]
                if r + g + b < 520:
                    return (xx, yy, (r, g, b))
        return None

    hits = [dark_in(0, 0, 12, h), dark_in(w - 12, 0, w, h),
            dark_in(0, 0, w, 12), dark_in(0, h - 12, w, h)]
    assert not any(hits), "页边出现墨迹（越界/裁切）：%s" % [x for x in hits if x]

    # 三档境界各出一张（视觉核对用）
    for lv in (40, 95):
        png2 = asyncio.run(card.render_faction(_player(lv)))
        with open(os.path.join(OUT_DIR, "real_lv%d.png" % lv), "wb") as f:
            f.write(png2)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    fail = []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print("PASS  %s" % name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("FACTION_CARD_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
