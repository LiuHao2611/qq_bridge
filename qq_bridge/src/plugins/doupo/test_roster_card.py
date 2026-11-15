# -*- coding: utf-8 -*-
"""本宗斗技名录卡验收（零依赖、exit-code 可验证）。

守用户感受得到的行为：
  * 「斗技 全部」必须出**卡片**，不能拿纯文本糊弄；
  * 卡片画的是本宗**全部**斗技（含未解锁、已收服异火的主动技），
    不是只看四槽上阵项——用户上次只上阵 1 门就以为「斗技只剩一个」；
  * 玩家只上阵 1 门时，名录卡仍应列出全部门技；
  * 渲染出的 PNG 合法且尺寸在可用范围内。

运行：
    python src/plugins/doupo/test_roster_card.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for mod in ("nonebot.adapters", "nonebot.adapters.onebot",
                "nonebot.adapters.onebot.v11"):
        sys.modules[mod] = types.ModuleType(mod)
    for attr in ("Message", "MessageEvent", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import card, config as cfg, core  # noqa: E402


def _p(lv=48, faction=None):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    if faction:
        p["faction"] = faction
        p["faction_name"] = faction
    return p


def test_roster_covers_every_faction_skill():
    p = _p()
    data = card.build_roster_data(p)
    pool = core.skill_pool(p)
    assert len(data["rows"]) == len(pool) > 1, "名录卡没画出本宗全部斗技"
    assert {r["title"] for r in data["rows"]} == {e["name"] for e in pool}
    assert f"共 {len(pool)} 门" in data["subtitle"]


def test_only_one_equipped_still_lists_all():
    """真实事故：玩家四槽只上阵 1 门，卡片不能因此只剩 1 行。

    2026-11-15 梳理后新增 ``ensure_loadout`` 自动补满四槽（旧版只在空列表时补，
    导致「斗技就剩一个」）。这里同时锁住两个契约：四槽必须补满、
    名录卡仍画全部斗技（而不是只画那 4 个上阵项）。
    """
    p = _p()
    usable = [e["name"] for e in core.skill_pool(p) if e["unlocked"]]
    assert usable, "测试前提：本宗至少有 1 门已解锁斗技"
    core.set_loadout(p, usable[:1])
    lo = core.loadout_entries(p)
    assert len(lo) == 4, "四槽未自动补满：%s" % [e["name"] for e in lo]
    data = card.build_roster_data(p)
    assert len(data["rows"]) == len(core.skill_pool(p)), "名录卡退化成只画上阵项"
    assert len(data["rows"]) > len(lo), "名录卡至少要比四槽多列未上阵斗技"


def test_locked_and_flame_skills_are_marked():
    low = _p(lv=1)
    rights = [str(r["right"]) for r in card.build_roster_data(low)["rows"]]
    assert any("未解锁" in r for r in rights), "未解锁斗技没标注"

    p = _p()
    p["flames"]["青莲地心火"] = 1
    subs = [str(r["sub"]) for r in card.build_roster_data(p)["rows"]]
    assert any("异火技" in s for s in subs), "已收服异火的主动技没进名录卡"


def test_roster_rows_carry_multidim_tags():
    """2026-11-15：名录每行必须带主定位 + 多维效果标签 + 门控，
    而不是只有「技能名 + 一句 brief」——否则玩家只能比数字大小。"""
    from doupo import factions as fac

    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    rows = card.build_roster_data(p)["rows"]
    assert len(rows) == 23, "天冥宗应为 23 门，实测 %d" % len(rows)
    assert any("〔" in r["sub"] and "〕" in r["sub"] for r in rows), "没画主定位标签"
    assert any(r["note"] for r in rows), "没画多维效果 / 门控行"
    assert all("note" in r for r in rows), "note 字段必须存在（可为空串）"
    # 每门至少有一条可见维度信息：主定位、效果 tag、或门控代价
    for r in rows:
        assert ("〔" in r["sub"]) or r["note"], "「%s」没有任何维度信息" % r["title"]
    # 复合技能必须拆成多个可见维度，而不是一个数字
    by_name = {r["title"]: r for r in rows}
    assert "血毒" in by_name["血煞掌"]["note"] and "减疗" in by_name["血煞掌"]["note"], (
        "复合技能血煞掌没拆成多维标签：%s" % by_name["血煞掌"]["note"])
    assert "限：" in by_name["嗜血化骨手"]["note"], "门控代价没展示"


def _check_boxes(bounds, boxes, min_gap: int = 2) -> list[str]:
    """与 card-design skill 同规则：溢出 / 两方向重叠 / 同行间距过小。"""
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
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                problems.append("[重叠] %s <-> %s: %s vs %s" % (la, lb, a, b))
            if a[1] < b[3] and b[1] < a[3]:
                gap_x = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gap_x < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx" % (la, lb, gap_x))
    return problems


def test_roster_passes_check_boxes():
    """card-design 门禁：真实天冥宗 23 行名录卡，溢出 0 / 重叠 0 / 间距 0。"""
    import importlib

    from doupo import factions as fac

    pic = card._load_pic()
    assert pic is not None
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_list")
    theme = importlib.import_module(card._PKG_NAME + ".theme").get("light")

    p = _p(faction="tianmingzong")
    p["faction_name"] = fac.FACTIONS["tianmingzong"]["name"]
    data = card.build_roster_data(p)
    boxes: list[tuple] = []
    c = tpl.build(theme, data=data, width=720, boxes=boxes)
    assert len(boxes) > 60, "收集到的包围盒太少，自检会假通过：%d" % len(boxes)
    problems = _check_boxes((12, 12, 720 - 12, c.height - 12), boxes)
    assert not problems, "名录卡触线：\n" + "\n".join(problems[:10])


def test_roster_renders_valid_png():
    from PIL import Image

    p = _p()
    png = asyncio.run(card.render_roster(p))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", "没出图 / 不是 PNG"
    im = Image.open(io.BytesIO(png))
    im.verify()
    w, h = im.size
    assert 400 <= w <= 1200 and 200 <= h <= 4000, f"卡片尺寸异常 {w}x{h}"
    assert h > 400, "名录卡太矮，可能没画出多行"


def test_roster_data_is_json_friendly():
    import json
    p = _p()
    json.dumps(card.build_roster_data(p), ensure_ascii=False)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn()
            print("PASS ", name)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("FAIL ", name, "->", type(e).__name__, str(e)[:150])
            fail.append(name)
    print("\nROSTER_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
