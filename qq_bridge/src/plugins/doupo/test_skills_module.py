# -*- coding: utf-8 -*-
"""斗技三阶 · 四槽上阵 · 战斗/掠夺 骨架验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_skills_module.py
"""
from __future__ import annotations

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

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    sys.modules["nonebot.adapters"] = types.ModuleType("nonebot.adapters")
    sys.modules["nonebot.adapters.onebot"] = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.Message = object
    v11.MessageSegment = types.SimpleNamespace(text=lambda *a, **k: None,
                                               image=lambda *a, **k: None)
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub_nonebot()
from doupo import core, config as cfg, factions as fac, skills as sk  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 入道分宗 + 四槽
# ---------------------------------------------------------------------------
def test_create_player_assigns_faction_and_loadout():
    p = core.create_player("9001", "甲", "g1")
    assert p.get("faction") in fac.FACTIONS, "入道未分配宗门"
    lo = p.get("loadout")
    assert isinstance(lo, list) and 1 <= len(lo) <= cfg.LOADOUT_SIZE, "四槽数量非法"
    unlocked = {e["name"] for e in core.skill_pool(p) if e["unlocked"]}
    assert all(n in unlocked for n in lo), "四槽含未解锁/非本宗技能"


def test_faction_is_stable_per_player():
    a = core.create_player("777", "乙", "g1")
    b = core.create_player("777", "乙", "g1")
    assert a["faction"] == b["faction"], "同一玩家两次入道应分到同一宗门（不可重掷）"
    c = core.create_player("778", "丙", "g1")
    assert c["faction"] in fac.FACTIONS


def test_every_faction_has_learnable_at_its_unlock_level():
    """每个宗门在「它自己进池的等级」必须至少有一门可学斗技。

    不能统一用 1 级判定：风雷阁 / 太虚古龙这类高段宗门的斗技都是地阶、天阶，
    它们是 40 级、90 级才进池的，1 级无可学是设计如此，不是缺陷。
    """
    for fid, f in fac.FACTIONS.items():
        cat = sk.catalog(fid)
        assert cat, "%s 无斗技目录" % fid
        base = fac.POOL_UNLOCK.get(f.get("pool"), 1)
        assert any(int(e["unlock"]) <= base for e in cat), (
            "%s 在进池等级 %d 时无任何可学斗技" % (fid, base))


# ---------------------------------------------------------------------------
# 2. 按阶斗气
# ---------------------------------------------------------------------------
def test_tier_mp_cost_is_increasing():
    mx = 10000
    c0, c1, c2 = (sk.tier_cost(t, mx) for t in ("普通", "高阶", "特殊"))
    assert c0 < c1 < c2, "斗气消耗应 普通<高阶<特殊"
    assert c0 == round(mx * cfg.SKILL_TIER_MP_FRAC["普通"])


def test_pick_skill_falls_back_when_no_mp():
    p = core.create_player("9002", "丁", "g1")
    e, c = core._pick_skill(p, 0, lambda: 0.0)
    assert e is None and c == 0, "斗气不足必须转武器普攻"
    e2, c2 = core._pick_skill(p, core.mp_max(p), lambda: 0.0)
    assert e2 is not None and c2 > 0, "斗气充足应能放斗技"


def test_fight_log_uses_skills_and_reports_basic_attack():
    p = core.create_player("9003", "戊", "g1")
    p["lv"] = 3
    p["hp"] = core.stats(p)["体力"]
    mon = core.monster_scaled(p, core.pick_monster(p))
    res = core.fight(p, mon, rng=lambda: 0.5)
    kinds = {it.get("kind") for it in res["log"] if not it.get("taken") and not it.get("miss")}
    assert "普攻" in kinds, "斗气耗尽后应出现武器普攻"
    assert any(k != "普攻" for k in kinds), "应至少释放过斗技"


# ---------------------------------------------------------------------------
# 3. 上阵四槽
# ---------------------------------------------------------------------------
def test_set_loadout_caps_at_four():
    p = core.create_player("9201", "辛", "g1")
    p["lv"] = 1
    pool = [e["name"] for e in core.skill_pool(p) if e["unlocked"]]
    core.set_loadout(p, pool * 2)
    assert len(p["loadout"]) <= cfg.LOADOUT_SIZE
    core.set_loadout(p, ["绝不存在的斗技"])
    assert "绝不存在的斗技" not in p["loadout"], "非法技名不得进四槽"


# ---------------------------------------------------------------------------
# 4. 掠夺 duel
# ---------------------------------------------------------------------------
def test_duel_returns_skill_based_result():
    p = core.create_player("9101", "己", "g1")
    v = core.create_player("9102", "庚", "g1")
    r = core.duel(p, v, rng=lambda: 0.5)
    assert {"win", "rounds", "log", "hp_p", "hp_v"} <= set(r)
    assert r["rounds"] >= 1 and len(r["log"]) >= 1
    assert any(it.get("kind") != "普攻" for it in r["log"]), "掠夺也应释放四槽斗技"


# ---------------------------------------------------------------------------
# 5. 转宗：进阶池 / 隐世池 17 派的唯一入口
# ---------------------------------------------------------------------------
def test_switch_blocked_below_pool_unlock():
    """lv1 只能在新手池里转，不能跳级进进阶池（否则四槽全空、全程普攻）。"""
    p = core.create_player("9301", "壬", "g1")
    p["gold"] = 10 ** 7
    res = core.change_faction(p, "迦南学院")
    assert not res["ok"] and res["reason"] == "notfound", res
    names = core.faction_candidates(p)
    assert names and all(c["pool"] == "starter" for c in names), "lv1 不该出现进阶池"


def test_switch_works_at_pool_level():
    """lv45 能转迦南学院：扣金币、重配四槽，且四槽都属于新派。"""
    p = core.create_player("9302", "癸", "g1")
    p["lv"] = 45
    p["gold"] = 500000
    before = int(p["gold"])
    res = core.change_faction(p, "迦南学院")
    assert res["ok"], res
    assert res["faction"] == "jianan"
    assert int(p["gold"]) == before - res["cost"], "金币没按代价扣"
    names = {e["name"] for e in sk.catalog("jianan")}
    assert p["loadout"], "转宗后四槽不能为空"
    assert all(n in names for n in p["loadout"]), "四槽混进了外派技能"


def test_switch_does_not_grant_starter_items():
    """转宗只换师门，不重发入道物品 —— 否则转一次刷一次。"""
    p = core.create_player("9303", "甲", "g1")
    p["lv"] = 45
    p["gold"] = 500000
    bag_before = dict(core.bag(p))
    core.change_faction(p, "焚炎谷")
    assert core.bag(p) == bag_before, "转宗动了背包（等于刷物品）"


def test_switch_needs_gold_and_rejects_same():
    p = core.create_player("9304", "乙", "g1")
    p["lv"] = 45
    p["gold"] = 0
    res = core.change_faction(p, "花宗")
    assert not res["ok"] and res["reason"] == "gold", res
    p["gold"] = 500000
    cur = p["faction_name"]
    res2 = core.change_faction(p, cur)
    assert not res2["ok"] and res2["reason"] == "same", res2


def test_switch_keeps_skill_levels():
    """技能等级按技名保留：转走再转回来，等级还在。"""
    p = core.create_player("9305", "丙", "g1")
    p["lv"] = 45
    p["gold"] = 500000
    first = sk.catalog(p["faction"])[0]["name"]
    p.setdefault("skills", {})[first] = 4
    old_fid = p["faction"]
    core.change_faction(p, "丹塔")
    core.change_faction(p, old_fid)
    assert int(p["skills"][first]) == 4, "转宗把技能等级弄丢了"


def test_legacy_save_gets_stable_faction():
    """老档（入道时还没有分宗功能，faction=None）每次读都要补到同一个宗门。

    2026-11-15 实测宿主存档 lv47 的玩家 faction 就是 None，`_ensure_faction`
    当时用无种子 random，问一次变一次；必须与 create_player 用同一套 qq 种子。
    """
    base = {"qq": "1743124119", "name": "老档", "group": "g", "lv": 47,
            "gold": 50000, "skills": {}, "bag": {}, "flames": {}}
    got = []
    for _ in range(3):
        p = dict(base)
        core._ensure_faction(p)
        sk.ensure_loadout(p)
        got.append((p.get("faction"), tuple(p.get("loadout") or ())))
    assert len(set(got)) == 1, "老档分宗不稳定：%s" % (got,)
    assert got[0][0] in fac.FACTIONS, "老档没补上宗门"
    assert got[0][1], "老档补宗后四槽不能为空"


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
            ok += 1
            print("PASS  %s" % name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("SKILLMOD_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
