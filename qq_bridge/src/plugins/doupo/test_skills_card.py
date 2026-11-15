# -*- coding: utf-8 -*-
"""斗技专用卡验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_skills_card.py     # 自带 runner，不依赖 pytest
    pytest src/plugins/doupo/test_skills_card.py

覆盖 2026-11-14 用户要求的四项：
  1. 完整技能描述 + 详细伤害描述（与 cfg.SKILLS / core 战斗公式同源）；
  2. 五条独立斗气段：一级亮一段、二级亮两段……五级全亮（**像素级**逐段采样）；
  3. 重点文字真的用粗体（bold=True 与默认逐像素不同），且默认行为无创；
  4. 重点字号显著大于通用列表卡。

产物：tmp/doupo_skills_cards/*.png，供人工视觉核对（不只看测试通过）。
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

OUT_DIR = os.path.join(REPO_ROOT, "tmp", "doupo_skills_cards")
os.makedirs(OUT_DIR, exist_ok=True)
#: 每次进程都用一个全新的临时目录放卡片缓存。不能用 OUT_DIR 下的固定子目录：
#: 上轮验收留下的索引文件会让新代码命中旧 PNG，视觉/尺寸断言全部假通过。
_CACHE_TMP = tempfile.mkdtemp(prefix="doupo_skills_")


def _stub_nonebot():
    """与 test_card.py 同款桩：让 doupo 包能在无 QQ 环境里纯逻辑加载。"""
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


def _pic():
    import pathlib

    pic = card._load_pic()
    assert pic is not None, "pic 包加载失败（斗技卡不可用）"
    # ⚠️ 磁盘缓存必须改道到全新临时目录：否则改了模板后 render 会命中上一轮
    # 留下的旧 PNG（含磁盘索引），像素/尺寸断言全部假通过。
    # 也不能直接删 /app/data/pic_cache —— 那是线上 bind 挂载的运行态目录。
    pc = pic.pic_cache
    pc.dir = pathlib.Path(_CACHE_TMP) / "pic_cache"
    pc._loaded = False
    pc._index = {}
    return pic


def _tpl():
    return importlib.import_module(card._PKG_NAME + ".templates.doupo_skills")


def _theme():
    return importlib.import_module(card._PKG_NAME + ".theme").get("light")


def _demo_data():
    """两行示例，避免每张验证图都画四块（视觉产物另出真实玩家卡）。"""
    return {
        "icon": "bolt", "title": "斗技",
        "subtitle": "四门斗技 · 五重斗气 · 快捷键 1/3/7/9",
        "item": "焚血散", "item_count": 3,
        "formula": "伤害口径：(力量×0.5 + 斗气×1.5) × 当前倍率 × 暴击期望（未计目标防御）",
    "rows": [
        {"badge": "1", "name": "龙吟碎", "lv": 1, "max": 5,
         "rank": "地阶低级", "origin_mark": "〔本宗〕", "role": "爆发",
         "desc": cfg.SKILLS[0]["desc"],
         "chips": [{"label": "破防", "value": "30%"}, {"label": "眩晕", "value": "10%"}],
         "gate": "起手慢、需蓄势",
         "base_mult": 1.60, "cur_mult": 1.60,
         "next_mult": 1.92, "dmg": 1000, "next_dmg": 1200,
         "segments": 5, "lit": 1, "item": "焚血散", "item_need": 1},
        {"badge": "3", "name": "天火三玄变", "lv": 1, "max": 5,
         "rank": "玄阶高级", "origin_mark": "〔本宗〕", "role": "续航",
         "desc": cfg.SKILLS[1]["desc"],
         "chips": [{"label": "吸血", "value": "25%"}],
         "gate": "敌有异火则反噬",
         "base_mult": 1.40, "cur_mult": 1.40,
         "next_mult": 1.68, "dmg": 875, "next_dmg": 1050,
         "segments": 5, "lit": 1, "item": "焚血散", "item_need": 1},
    ],
        "tip": "用法：升级 龙吟碎 · 现有 焚血散 ×3",
    }


def _all_lv(data: dict, lv: int) -> dict:
    """把每行改到指定等级（lit 与 lv 同步），其余字段等比缩放。"""
    out = dict(data)
    rows = []
    for r in data["rows"]:
        q = dict(r)
        q["lv"] = lv
        q["lit"] = lv
        q["maxed"] = lv >= int(q["max"])
        mult = round(float(q["base_mult"]) * (1 + 0.2 * (lv - 1)), 2)
        q["cur_mult"] = mult
        q["next_mult"] = mult if q["maxed"] else round(mult + float(q["base_mult"]) * 0.2, 2)
        q["dmg"] = int(1000 * mult)
        q["next_dmg"] = int(1000 * (mult + (0 if q["maxed"] else float(q["base_mult"]) * 0.2)))
        q["item_need"] = 0 if q["maxed"] else lv
        rows.append(q)
    out["rows"] = rows
    return out


def _is_warm(px) -> bool:
    """点亮段（金→朱红渐变）与未点亮段（米色轨道）的像素判别。"""
    r, g, b = px[0], px[1], px[2]
    return (r - b) > 60 and r > 110


def _lit_flags(img, boxes) -> list[bool]:
    """逐段取靠左 1/4、上方 6px 的采样点（避开段中央的「一/二/三」文字）。"""
    return [_is_warm(img.getpixel((b[0] + (b[2] - b[0]) // 4, b[1] + 6)))
            for b in boxes]


# ---------------------------------------------------------------------------
# 1. 模板注册与数据同源
# ---------------------------------------------------------------------------
def test_template_registered():
    pic = _pic()
    assert "doupo_skills" in pic.list_templates(), "专用模板未注册"
    assert pic.TEMPLATE_LABELS.get("doupo_skills") == "斗破斗技卡"
    assert "doupo_list" in pic.list_templates(), "通用列表卡必须保留（异火/宝库/背包/排行）"


def test_build_skills_data_details():
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = 40
    core.skill_pool(p)                      # 确保四槽就绪
    lo = core.loadout_entries(p)
    levels = [3, 1, 5, 2]
    p["skills"] = {e["name"]: levels[i % 4] for i, e in enumerate(lo)}
    core.add_item(p, cfg.SKILL_UP_ITEM, 7)

    d = card.build_skills_data(p)
    assert d["title"] == "斗技"
    assert d["item"] == "焚血散" and d["item_count"] == 7
    assert len(d["rows"]) == len(lo)        # 四槽（不足则按实际）
    assert "力量" in d["formula"] and "暴击期望" in d["formula"]

    st = core.stats(p)
    raw_base = st["力量"] * 0.5 + st["斗气"] * 1.5
    crit_mult = 1.0 + core.crit_rate(p) / 100.0 * 0.5
    for r, e in zip(d["rows"], lo):
        assert r["name"] == e["name"]
        # 2026-11-15 多维重构：desc 必须是完整原著描述（不再是 brief 一句），
        # 并带主定位 / 多维效果 chip / 门控，而不是只有一个伤害数字。
        assert r["desc"] == str(e.get("desc") or e.get("brief") or ""), "完整描述须与权威表同源"
        assert len(r["desc"]) > 20, "描述过短，疑似又退回 brief"
        assert r["role"] in ("", "输出", "爆发", "续航", "控场", "异常", "防御", "位移", "功能")
        assert isinstance(r["chips"], list) and all("label" in c for c in r["chips"])
        assert isinstance(r["gate"], str)
        assert r["base_mult"] == round(float(e["dmg"]), 2)
        mult = float(e["dmg"]) * (1 + 0.2 * (r["lv"] - 1))
        assert r["cur_mult"] == round(mult, 2)
        assert r["dmg"] == int(round(raw_base * mult * crit_mult)), "伤害须与战斗公式同源"
        assert r["max"] == 5 and r["segments"] == 5 and r["lit"] == r["lv"]
    # 天冥宗默认四槽应有主定位与多维效果（防止多维字段没进卡片数据）
    assert any(r["role"] for r in d["rows"]), "四槽全无主定位，多维数据没进卡片"
    assert any(r["chips"] for r in d["rows"]), "四槽全无多维效果，玩家只能看到一个数字"
    assert any(r["maxed"] for r in d["rows"])
    for r in d["rows"]:
        assert r["item_need"] == (0 if r["maxed"] else cfg.skill_up_cost(r["lv"]))


# ---------------------------------------------------------------------------
# 2. 五段条像素级验收
# ---------------------------------------------------------------------------
def test_five_segments_light_up_by_level():
    tpl, t = _tpl(), _theme()
    for lv in range(1, 6):
        data = _all_lv(_demo_data(), lv)
        c = tpl.build(t, data=data, width=720)
        img = c.img.convert("RGB")
        boxes = tpl.segment_positions(data, 720)
        assert len(boxes) == len(data["rows"]), "每个技能块都要有五段条"
        for row_boxes in boxes:
            assert len(row_boxes) == 5, "必须是 5 条独立的段"
            flags = _lit_flags(img, row_boxes)
            assert flags == [True] * lv + [False] * (5 - lv), (
                "Lv.%d 应为前 %d 段亮，实测 %s" % (lv, lv, flags))


def test_segment_probe_rejects_forged_lighting():
    """自举：把第 4 段涂成暖色，采样必须数出 4 段亮 —— 证明检测不是恒真。"""
    from PIL import ImageDraw

    tpl, t = _tpl(), _theme()
    data = _all_lv(_demo_data(), 3)
    img = tpl.build(t, data=data, width=720).img.convert("RGB")
    boxes = tpl.segment_positions(data, 720)[0]
    assert _lit_flags(img, boxes) == [True, True, True, False, False]
    forged = img.copy()
    b = boxes[3]
    ImageDraw.Draw(forged).rectangle([b[0] + 2, b[1] + 2, b[2] - 2, b[3] - 2],
                                     fill=(200, 80, 40))
    assert _lit_flags(forged, boxes) == [True, True, True, True, False], (
        "采样没能发现第 4 段被点亮，像素验收会假通过")


# ---------------------------------------------------------------------------
# 3. 字体：真加粗 + 默认无创 + 字号放大
# ---------------------------------------------------------------------------
def _ink(bold):
    Canvas = _pic().Canvas
    c = Canvas(320, 70, "#FFFFFF")
    kw = {} if bold is None else {"bold": bold}
    c.text((10, 10), "焚血散 ×3", size=26, color="#000000", **kw)
    raw = c.img.convert("L").tobytes()
    return sum(1 for v in raw if v < 128), c.to_bytes()


def test_bold_is_really_bolder_and_default_is_unchanged():
    n_false, b_false = _ink(False)
    n_true, b_true = _ink(True)
    n_default, b_default = _ink(None)
    assert b_default == b_false, "默认 bold=None 必须与旧行为逐像素一致（无创）"
    assert n_default == n_false
    assert b_true != b_false, "bold=True 没有换粗体（重点文字仍是常规字重）"
    assert n_true > n_false, "粗体墨迹应多于常规：bold=%d regular=%d" % (n_true, n_false)


def test_small_text_is_not_overbolded():
    """格式护栏：15px 小字不能叠 1px 伪粗（实测会把「已圆满」糊成一团）。"""
    def ink_small(bold):
        Canvas = _pic().Canvas
        c = Canvas(200, 50, "#FFFFFF")
        kw = {} if bold is None else {"bold": bold}
        c.text((8, 8), "已圆满", size=15, color="#000000", **kw)
        raw = c.img.convert("L").tobytes()
        return sum(1 for v in raw if v < 128), c.to_bytes()

    n0, b0 = ink_small(False)
    n1, b1 = ink_small(True)
    assert b0 == b1 and n0 == n1, "小字被伪粗描边糊化了，右下角物品提示会不可读"


def test_card_cache_is_isolated():
    """缓存必须落在全新临时目录，不能是上一轮留下的产物（防视觉断言假通过）。"""
    pc = _pic().pic_cache
    assert str(pc.dir).startswith(_CACHE_TMP), "缓存没改道，会命中旧 PNG"
    assert "/app/data" not in str(pc.dir), "不许写线上 bind 挂载的 data/pic_cache"


def test_key_font_sizes_are_larger_than_generic_list():
    tpl = _tpl()
    lst = importlib.import_module(card._PKG_NAME + ".templates.doupo_list")
    assert tpl.FS_NAME > lst.FS_ITEM, "技能名必须比通用列表卡主标题大"
    assert tpl.FS_LV >= 30, "等级必须显著放大"
    assert tpl.FS_VALUE > lst.FS_ITEM, "伤害/倍率数字必须放大"
    assert tpl.FS_ITEM > lst.FS_ITEM, "升级物品必须放大"
    assert tpl.FS_DESC >= lst.FS_SMALL, "描述不能比通用列表卡的说明更小"


# ---------------------------------------------------------------------------
# 4. 出图与产物（供视觉核对）
# ---------------------------------------------------------------------------
def test_render_real_card_and_export_previews():
    p = core.create_player("10001", "萧炎", "20001")
    p["lv"] = 40
    p["skills"] = {"龙吟碎": 3, "天火三玄变": 1, "八极崩": 5, "五轮离火法": 2}
    core.add_item(p, cfg.SKILL_UP_ITEM, 6)
    png = asyncio.run(card.render_skills(p))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
    with open(os.path.join(OUT_DIR, "real_mixed.png"), "wb") as f:
        f.write(png)

    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png))
    # 2026-11-15 多维重构：每块多了定位/完整描述/chip/门控四段信息，
    # 四槽卡高度合理上移到 ~2000px；上限放宽但仍拒绝异常高。
    assert img.width == 720 and 600 < img.height < 2800, (
        "卡片尺寸异常：%dx%d" % (img.size))
    small = img.convert("RGB").resize((80, 80)).tobytes()
    colors = len({small[i:i + 3] for i in range(0, len(small), 3)})
    assert colors > 20, "疑似空白卡片"

    tpl, t = _tpl(), _theme()
    for lv in (1, 3, 5):
        data = _all_lv(_demo_data(), lv)
        c = tpl.build(t, data=data, width=720)
        buf = c.to_bytes()
        assert buf[:8] == b"\x89PNG\r\n\x1a\n"
        with open(os.path.join(OUT_DIR, "seg_%d.png" % lv), "wb") as f:
            f.write(buf)


def test_descriptions_wrap_without_losing_text():
    """格式护栏：2026-11-15 起展示**完整**描述（不再 brief），折行只换行不丢字。

    行数上限按最长原著描述放宽到 6 行；高度由 _row_h 内容驱动，不写死。
    """
    tpl = _tpl()
    p = core.create_player("10001", "测试", "20001")
    d = card.build_skills_data(p)
    inner_w = 720 - 28 * 2 - 12 - 36
    for r in d["rows"]:
        lines = tpl._desc_lines(r, inner_w)
        assert 1 <= len(lines) <= 6, "%s 描述折了 %d 行" % (r["name"], len(lines))
        assert "".join(lines) == r["desc"], "%s 描述被截断或丢字" % r["name"]
    # 多维区：有 chip / 门控字段的行必须能算出正行数（非天冥宗斗技允许为空）。
    for r in d["rows"]:
        if r.get("chips"):
            assert len(tpl._chip_rows(r, inner_w)) >= 1, "效果 chip 没算出行"
        if r.get("gate"):
            assert len(tpl._gate_lines(r, inner_w)) >= 1, "门控没算出行"
        assert tpl._row_h(r, inner_w) > 0


# ---------------------------------------------------------------------------
# 5. card-design 强制门禁：check_boxes 溢出 / 重叠 / 间距（2026-11-15）
# ---------------------------------------------------------------------------
def _overlap_2d(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _check_boxes(bounds, boxes, min_gap: int = 2) -> list[str]:
    """与 card-design skill `check_boxes` 同规则：溢出 / 两方向重叠 / 同行间距过小。"""
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
    """真实天冥宗四槽数据走 check_boxes：溢出 0 / 重叠 0 / 间距 0。"""
    tpl, t = _tpl(), _theme()
    p = core.create_player("10001", "天冥弟子", "20001")
    p["lv"] = 50
    p["faction"] = "tianmingzong"
    from doupo import factions as fac
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    core.skill_pool(p)
    data = card.build_skills_data(p)
    boxes: list[tuple] = []
    c = tpl.build(t, data=data, width=720, boxes=boxes)
    assert len(boxes) > 30, "收集到的包围盒太少，自检会假通过：%d" % len(boxes)
    problems = _check_boxes((12, 12, 720 - 12, c.height - 12), boxes)
    assert not problems, "斗技卡触线：\n" + "\n".join(problems[:10])


def test_check_boxes_rejects_forged_overlap():
    """自举：人为塞一对重叠 bbox，check_boxes 必须报错（证明门禁不是恒真）。"""
    problems = _check_boxes((0, 0, 100, 100), [
        ("a", 10, 10, 50, 30), ("b", 40, 20, 80, 40)])
    assert any("[重叠]" in p for p in problems), "重叠检测失效，门禁恒真"


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
    print("SKILLS_CARD_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
