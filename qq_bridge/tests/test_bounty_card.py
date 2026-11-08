# -*- coding: utf-8 -*-
"""赏金卡重做的验收测试（@skill:card-design + @skill:prove-it）。

两类数据都跑：
  1) 参考图同款样例（字段齐全，含 tier / mission / objective）——验证「全量对齐、
     禁止遗漏」的 8 张卡 + 所有内容层级；
  2) 生产数据契约（warframe.py::_bounty_card_data 当前真实字段，无 tier / objective）
     ——验证缺省不编造、仍能出图且自检通过。

同时把 20KB 代码门禁纳入断言：本次 4 个模板文件都必须 ≤20480B。

直接跑： python tests/test_bounty_card.py   （产出截图到 tests/_shot_bounty_*.png）
pytest：  pytest tests/test_bounty_card.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins.qq_bridge.pic.theme import LIGHT_THEME  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_atoms as A  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_icons as BI  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_layout as L  # noqa: E402
from src.plugins.qq_bridge.pic.templates.bounty_layout import build as build_bounty  # noqa: E402
from src.plugins.qq_bridge.pic.templates.bounty_model import CARD_ORDER, build_model  # noqa: E402

OUT = Path(__file__).resolve().parent
TPL = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates"
SOURCE = "数据源：官方 worldstate / wf.wiki"

#: 参考图里出现的全部 16 个物品（用户点名的 MOD / 债券 / 部件 / 蓝图 / 枪托）
REF_ITEM_NAMES = [
    "Caliban 机体 蓝图", "赤炎流星 蓝图", "孢丝感染枪 枪托",
    "收割螺旋", "预言 探求", "角斗士 威猛", "私法 军备",
    "预言 神密", "角斗士 决心", "私法 活力", "猎鹰俯击",
    "2x 培训债券", "2x 庇护债券", "2x 医疗债券", "2x 预支债券", "2x 家族债券",
]


# ------------------------------------------------------------ 样例数据 ----
def _ref_data() -> dict:
    """参考图同款内容（逐字对照 2026-11-08 用户截图），字段齐全。"""
    return {
        "title": "赏金任务一览",
        "remain": "1小时48分23秒",
        "rotation": {
            "rot": "B",
            "rot_rewards": [
                {"name": "Caliban 机体 蓝图", "image": None},
                {"name": "赤炎流星 蓝图", "image": None},
            ],
            "vault": "C",
            "vault_rewards": [{"name": "孢丝感染枪 枪托", "image": None}],
        },
        "worlds": [
            {"name": "地球赏金", "label": "MOD奖励轮换",
             "items": ["收割螺旋", "预言 探求", "角斗士 威猛", "私法 军备",
                       "预言 神密", "角斗士 决心", "私法 活力", "猎鹰俯击"],
             "jobs": [
                 {"lvl": "100-100", "type": "捕获", "name": "捕获新任 Grineer 指挥官",
                  "narmer": False, "icon": "circle", "tier": "赏金6"},
                 {"lvl": "50-70", "type": "防御", "name": "带他们回家",
                  "narmer": True, "icon": "circle", "tier": "赏金7"},
             ]},
            {"name": "金星赏金", "label": "债券轮换",
             "items": ["2x 培训债券", "2x 庇护债券", "2x 医疗债券",
                       "2x 预支债券", "2x 家族债券"],
             "jobs": [
                 {"lvl": "100-100", "type": "资源", "name": "拆散陷阱",
                  "narmer": False, "icon": "diamond", "tier": "赏金6"},
                 {"lvl": "50-70", "type": "歼灭", "name": "粉碎邪教",
                  "narmer": True, "icon": "diamond", "tier": "赏金7"},
             ]},
            {"name": "火卫二赏金", "label": "隔离库轮换 C", "items": [],
             "jobs": [
                 {"lvl": "40-60", "type": "净化", "name": "异物取回",
                  "narmer": False, "icon": "triangle", "tier": "赏金5"},
                 {"lvl": "100-100", "type": "防御", "name": "重夺领地",
                  "narmer": False, "icon": "triangle", "tier": "赏金6"},
             ]},
        ],
        "deep": {"name": "深矿赏金", "tag": "[钢铁之路]", "title": "解放小动物",
                 "detail": "等级 130-140 · 5阶段 · 最终：救援", "icon": "bolt"},
        "factions": [
            {"name": "圣所赏金", "jobs": [
                {"lvl": "90-95", "name": "孽杀", "type": "歼灭",
                 "target": "剔除重型炮兵", "mission": "剔除重型炮兵",
                 "objective": "摧毁 32 个重型炮兵", "icon": "round_square", "tier": "赏金4"},
                {"lvl": "110-115", "name": "卫城区", "type": "中断",
                 "target": "忠诚的殁世机甲", "mission": "忠诚的殁世机甲",
                 "objective": "召唤一台殁世机甲", "icon": "round_square", "tier": "赏金5"},
            ]},
            {"name": "扎里曼赏金", "jobs": [
                {"lvl": "60-65", "name": "哈拉科防线", "type": "歼灭",
                 "target": "窃取新力量", "mission": "窃取新力量",
                 "objective": "找到并激活 1 个虚空裂隙", "icon": "star", "tier": "赏金1"},
                {"lvl": "90-95", "name": "翠径", "type": "移动防御",
                 "target": "唤醒与驱逐", "mission": "唤醒与驱逐",
                 "objective": "唤醒一个休眠的天使", "icon": "star", "tier": "赏金4"},
                {"lvl": "110-115", "name": "涂沃主厅", "type": "虚空覆涌",
                 "target": "减少虚空污染", "mission": "减少虚空污染",
                 "objective": "使用 9 个无垢装置", "icon": "star", "tier": "赏金5"},
            ]},
            {"name": "六人组赏金", "jobs": [
                {"lvl": "110-115", "name": "至日广场", "type": "防御",
                 "target": "背包破坏者·昆西的赏金", "mission": "背包破坏者·昆西的赏金",
                 "objective": "摧毁 12 个炽蛇军单位", "icon": "square", "tier": "赏金5"},
                {"lvl": "110-115", "name": "歼灭：科腐者", "type": "歼灭",
                 "target": "摧毁掩护·杀毒赏金", "mission": "摧毁掩护·杀毒赏金",
                 "objective": "摧毁 75 个固定物品", "icon": "square", "tier": "赏金7"},
            ]},
        ],
    }


def _prod_data() -> dict:
    """warframe.py 当前真实字段：无 tier / mission / objective。

    只剥离这三个字段，卡片与任务内容保持各自的真实数据，不替换成同一份表
    （早期版本误把三派系 jobs 全换成扎里曼数据，截图中圣所/六人组显示错乱）。
    """
    d = _ref_data()
    for w in d["worlds"]:
        for j in w["jobs"]:
            j.pop("tier", None)
    for f in d["factions"]:
        for j in f["jobs"]:
            for k in ("tier", "mission", "objective"):
                j.pop(k, None)
    d["deep"]["title"] = "花园除草"
    d["deep"]["detail"] = "等级 130-140 · 5阶段 · 最终：歼灭"
    return d


# -------------------------------------------------------------- 断言 ----
def test_model_covers_all_eight_cards() -> None:
    m = build_model(None)
    assert len(m["cards"]) == 8, "参考图 8 张卡必须一张不少"
    assert [c["title"].split(" ")[0] for c in m["cards"]] == [t for t, _ in CARD_ORDER]
    assert all(c.get("empty") == "暂无数据" for c in m["cards"]), \
        "数据为空也必须保留卡片与占位，禁止静默漏卡"
    assert m["footer"]["platform"] == "PC"
    assert m["footer"]["hint"] and m["footer"]["tip"]


def test_vault_rewards_go_to_deimos() -> None:
    """参考图：隔离库轮换 C + 孢丝感染枪 枪托 属于火卫二卡，不是顶部 pill。"""
    m = build_model(_ref_data())
    deimos = next(c for c in m["cards"] if c["title"] == "火卫二赏金")
    assert deimos["section"] == "隔离库轮换 C"
    assert [i["text"] for i in deimos["items"]] == ["孢丝感染枪 枪托"]
    rot = m["cards"][0]
    assert rot["title"] == "合一众赏金轮换 B"
    assert [i["text"] for i in rot["items"]] == ["Caliban 机体 蓝图", "赤炎流星 蓝图"]


def test_deep_hero_parsed() -> None:
    m = build_model(_ref_data())
    hero = m["cards"][1]["hero"]
    assert hero["section"] == "[钢铁之路]解放小动物"
    assert hero["lvl"] == "130-140"
    assert hero["stages"] == "5阶段"
    assert hero["final"] == "救援"


def test_tier_direct_read_wins_over_fallback() -> None:
    m = build_model(_ref_data())
    earth = next(c for c in m["cards"] if c["title"] == "地球赏金")
    assert [j["tier"] for j in earth["jobs"]] == ["赏金6", "赏金7"]
    p = build_model(_prod_data())
    pe = next(c for c in p["cards"] if c["title"] == "地球赏金")
    assert [j["tier"] for j in pe["jobs"]] == ["赏金6", "赏金7"], \
        "缺省时按参考图口径展示，绿色赏金徽章不得缺失"
    assert all(not j.get("objective") for j in
               next(c for c in p["cards"] if c["title"] == "扎里曼赏金")["jobs"]), \
        "目标行数据缺失时不得编造文案"


def test_remain_shortened() -> None:
    f = build_model(_ref_data())["footer"]
    assert f["remain"] == "1小时48分"
    assert 0.0 <= f["progress"] <= 1.0


def test_check_boxes_catches_real_violations() -> None:
    """card-design：把错放回去，检查必须报错。"""
    overlap = [("a", 10, 10, 100, 30), ("b", 90, 10, 180, 30)]
    assert any("[重叠]" in p for p in A.check_boxes((0, 0, 800, 600), overlap))
    overflow = [("a", 790, 10, 850, 30)]
    assert any("[溢出]" in p for p in A.check_boxes((0, 0, 800, 600), overflow))
    tight = [("a", 10, 10, 100, 30), ("b", 100, 10, 180, 30)]
    assert any("[间距]" in p for p in A.check_boxes((0, 0, 800, 600), tight, min_gap=2))


def test_all_template_files_under_20kb() -> None:
    for name in ("bounty.py", "bounty_model.py", "bounty_atoms.py",
                 "bounty_layout.py", "bounty_icons.py"):
        size = (TPL / name).stat().st_size
        assert size <= 20480, f"{name} 超出 20KB 硬门禁: {size}B"


# ------------------------------------------------- 真实物品图标 ----------
def test_icon_index_normalizes_and_covers_reference_items() -> None:
    """「2x 培训债券」与 wf.wiki 的「培训债务债券」必须归一到同一 key。"""
    assert BI.normalize("2x 培训债券") == "培训债券"
    assert BI.normalize("培训债务债券") == "培训债券"
    idx = BI.load_index()
    assert len(idx) > 50, "图标索引异常为空，检查 wf.wiki 解析/磁盘索引"
    for n in REF_ITEM_NAMES:
        assert BI.normalize(n) in idx, f"索引缺 {n}"


def test_every_reference_item_has_real_official_icon() -> None:
    """每个物品都必须拿到 DE 官方图标文件，不是几何方块。"""
    BI.prewarm(REF_ITEM_NAMES)
    model = build_model(_ref_data())
    items = [i for c in model["cards"] for i in c["items"]]
    assert len(items) == len(REF_ITEM_NAMES), (len(items), len(REF_ITEM_NAMES))
    for it in items:
        p = it.get("image")
        assert p and Path(p).is_file(), f"{it['text']} 没有真实图标（退回几何方块）"
        with Image.open(p) as im:
            assert im.format == "PNG"
            assert im.width >= 128 and im.height >= 128, \
                f"{it['text']} 图标分辨率过低: {im.size}"


def test_missing_icon_gate_catches_the_old_shortcut() -> None:
    """把这次真实犯过的错放回去（图标解析全失效），检查必须报错。"""
    original = BI.ensure
    BI.ensure = lambda *a, **k: None  # 模拟「又偷懒不去做图标」
    try:
        c = build_bounty(LIGHT_THEME, data=_ref_data(), width=800, source=SOURCE)
    finally:
        BI.ensure = original
    assert c.missing_icons, "图标全丢时渲染层必须显式登记，不能静默画方块"
    try:
        L.gate_icons(c, strict=True)
    except SystemExit as e:
        assert "没有真实官方图标" in str(e)
    else:
        raise AssertionError("把错放回去，gate_icons 必须拦下")


def _render(data: dict, out_name: str) -> tuple[list[str], int, int]:
    # 先预取，保证渲染时图标已落盘（与项目 item_icons.ensure_alerts_icons 同款时序）
    BI.prewarm(REF_ITEM_NAMES)
    c = build_bounty(LIGHT_THEME, data=data, width=800, source=SOURCE)
    problems = list(getattr(c, "layout_problems", []))
    # 严格闸门：排版违规 / 图标缺失任一不过都直接 SystemExit(1)，拦在交差前
    A.gate((16, 0, c.width - 16, c.height), getattr(c, "layout_boxes", []),
           min_gap=2, strict=True)
    L.gate_icons(c, strict=True)
    path = OUT / out_name
    c.save(str(path))
    print(f"[shot] {path}  {c.width}x{c.height}  "
          f"problems={len(problems)} missing_icons={len(c.missing_icons)}")
    return problems, c.width, c.height


def test_render_reference_strict() -> None:
    problems, w, h = _render(_ref_data(), "_shot_bounty_reference.png")
    assert not problems, f"参考图同款数据自检未通过: {problems[:5]}"
    assert w == 800 and h > 1200


def test_render_production_shape_strict() -> None:
    problems, _, _ = _render(_prod_data(), "_shot_bounty_production.png")
    assert not problems, f"生产数据契约自检未通过: {problems[:5]}"


def test_registered_template_smoke() -> None:
    """真实注册链冒烟：pic/__init__.py 的 "bounty": _bounty.build 仍能出 PNG。"""
    from src.plugins.qq_bridge.pic import render_sync
    png = render_sync("bounty", card="星际战甲·赏金",
                      data=_ref_data(), source=SOURCE, width=800)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 10000, "导出 PNG 异常过小，可能画成空白卡"


def test_theme_override_does_not_leak() -> None:
    """带覆盖的渲染不得污染后续渲染（模块级常量是进程共享的）。"""
    from src.plugins.qq_bridge.pic.templates import bounty_layout as L
    from dataclasses import replace
    tinted = replace(LIGHT_THEME, bg_overrides={"bounty": {"page_color": "#123456"}})
    L.apply_theme(tinted)
    assert L.C_PAGE == "#123456"
    L.apply_theme(LIGHT_THEME)
    assert L.C_PAGE == L._DEFAULT_COLORS["C_PAGE"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"ALL PASS ({len(fns)})")
