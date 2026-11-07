# -*- coding: utf-8 -*-
"""斗破苍穹 · 核心逻辑单测（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_core.py     # 自带 runner，不依赖 pytest
    pytest src/plugins/doupo/test_core.py

导入前桩掉 nonebot，使插件可在无 QQ 环境中安全加载（只校验纯逻辑）。
"""

from __future__ import annotations

import os
import sys
import types

# ---- 让 doupo 包与 src.* 都可被导入 ----
# PLUGINS_DIR 供 `from doupo import ...`；REPO_ROOT 供插件 __init__.py 里的
# `from src.game_mode import ...`。裸跑脚本时 CWD 不保证是仓库根，两者缺一即 ModuleNotFoundError。
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


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
    adapters = types.ModuleType("nonebot.adapters")
    onebot = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    sys.modules["nonebot.adapters"] = adapters
    sys.modules["nonebot.adapters.onebot"] = onebot
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub_nonebot()

from doupo import core, config as cfg  # noqa: E402


def _p(lv=1, **kw):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["exp"] = 0
    p["hp"] = core.stats(p)["体力"]
    st = core.stats(p)
    p["hp"] = st["体力"]
    p["mp"] = core.mp_max(p)
    p.update(kw)
    return p


# ---------------------------------------------------------------------------
# 1. 成长公式（源码 ah.java:165）
# ---------------------------------------------------------------------------
def test_c_stat_matches_source():
    # 实机 1 级：体力 880 / 力量 275 / 斗气 165 / 敏捷 220 / 防御 132
    assert core.c_stat(80, 1) == 880
    assert core.c_stat(25, 1) == 275
    assert core.c_stat(15, 1) == 165
    assert core.c_stat(20, 1) == 220
    assert core.c_stat(12, 1) == 132
    # 源码注释里的进阶值
    assert core.c_stat(80, 9) == 1520
    assert core.c_stat(80, 10) == 2400
    assert core.c_stat(80, 19) == 5520
    assert core.c_stat(80, 28) == 10240
    assert core.c_stat(25, 28) == 3200
    assert core.c_stat(15, 28) == 1920
    assert core.c_stat(20, 28) == 2560
    assert core.c_stat(12, 28) == 1536


def test_realm_and_star():
    assert cfg.full_realm(1) == "一星斗者"
    assert cfg.full_realm(9) == "三星大斗师"
    assert cfg.full_realm(10) == "一星斗灵"
    assert cfg.full_realm(19) == "一星斗宗"
    assert cfg.full_realm(27) == "三星斗圣"
    assert cfg.full_realm(28) == "斗圣巅峰"
    assert cfg.full_realm(29) == "斗帝"


# ---------------------------------------------------------------------------
# 2. 面板六格（实机逐格核对）
# ---------------------------------------------------------------------------
def test_panel_matches_real_screen():
    p = _p(1)
    st = core.stats(p)
    # 实机：力量575 敏捷480 斗气315 防御342 机缘1 体力880（=基础 + 玄重尺 300/260/150/210）
    assert st["力量"] == 575
    assert st["敏捷"] == 480
    assert st["斗气"] == 315
    assert st["防御"] == 342
    assert st["体力"] == 880
    assert st["机缘"] == 1


def test_crit_dodge_from_source():
    p = _p(1)
    # y.java:155  a[0][2] += (a[0][6]+30)/100   -> 10 + (480+30)//100 = 15
    assert core.crit_rate(p) == 15
    # y.java:157  a[0][1] += (a[0][6]+20)/200   -> 5 + (480+20)//200 = 7
    assert core.dodge_rate(p) == 7


# ---------------------------------------------------------------------------
# 3. 强化（源码 h.java:80-102 / l.java:304,361）
# ---------------------------------------------------------------------------
def test_enhance_preview_first_level():
    """实机强化框：L=0 显示「1级 ↗ 2级」，四项 +30/+26/+15/+21，需要 1 块炎星石。"""
    p = _p(1)
    pv = core.enhance_preview(p)
    assert pv["disp"] == 1 and pv["next_disp"] == 2
    assert pv["cost"] == 1
    assert pv["next"]["力量"] - pv["cur"]["力量"] == 30
    assert pv["next"]["敏捷"] - pv["cur"]["敏捷"] == 26
    assert pv["next"]["斗气"] - pv["cur"]["斗气"] == 15
    assert pv["next"]["防御"] - pv["cur"]["防御"] == 21


def test_enhance_cost_is_l_plus_1():
    p = _p(1)
    for L in range(0, 9):
        p["weapon"] = L
        assert core.enhance_cost(p) == L + 1


def test_enhance_curve_matches_formula():
    for L in range(0, 9):
        base = 300
        expect = base
        for k in range(L + 1):
            expect += base * (k * 10) // 100
        assert cfg.weapon_stat(base, L) == expect


def test_enhance_max_hardcoded():
    """L=9（界面 10 级）走硬编码 2000/2000/1000/1200。"""
    p = _p(20)
    p["weapon"] = 9
    st = core.stats(p)
    wp = cfg.WEAPON["max_stats"]
    assert wp == {"力量": 2000, "敏捷": 2000, "斗气": 1000, "防御": 1200}
    # 装备项 = 基础 + 硬编码
    assert st["力量"] == core.c_stat(25, 20) + 2000
    assert cfg.weapon_name(9) == "异火恒古尺"
    assert cfg.weapon_name(8) == "玄重尺"


def test_enhance_blocked_at_max():
    p = _p(1)
    p["weapon"] = 9
    assert core.enhance(p)["reason"] == "max"


def test_enhance_needs_stones():
    p = _p(1)
    p["bag"] = {}
    res = core.enhance(p)
    assert res["ok"] is False and res["reason"] == "stone"
    p["bag"] = {"炎星石": 5}
    res = core.enhance(p)
    assert res["ok"] is True and p["weapon"] == 1 and core.count_item(p, "炎星石") == 4


# ---------------------------------------------------------------------------
# 4. 炼药（失败得药渣 / 品阶影响成功率 / 冷却）
# ---------------------------------------------------------------------------
def test_craft_success_and_dross():
    p = _p(1)
    p["bag"] = {"血灵草": 10, "紫叶兰": 10}
    p["alch"] = 8            # 九品 -> 95%
    res = core.craft(p, "回灵丹", rng=lambda: 0.0)
    assert res["ok"] is True
    assert core.count_item(p, "回灵丹") == 1

    p2 = _p(1)
    p2["bag"] = {"血灵草": 10, "紫叶兰": 10}
    p2["alch"] = 0
    res2 = core.craft(p2, "回灵丹", rng=lambda: 0.999)   # 必失败
    assert res2["ok"] is False and res2["reason"] == "fail"
    assert core.count_item(p2, "药渣") == cfg.CRAFT_FAIL_DROSS


def test_craft_missing_material_and_cd():
    p = _p(1)
    p["bag"] = {}
    assert core.craft(p, "回灵丹")["reason"] == "mat"

    q = _p(1)
    q["bag"] = {"血灵草": 99, "紫叶兰": 99}
    q["alch"] = 8
    core.craft(q, "回灵丹", rng=lambda: 0.0, now_ts=1000.0)
    r = core.craft(q, "回灵丹", rng=lambda: 0.0, now_ts=1001.0)
    assert r["reason"] == "cd" and r["left"] == cfg.CRAFT_CD_SEC - 1


def test_alch_level_up():
    p = _p(1)
    p["alch"] = 0
    p["alch_exp"] = 0
    p["bag"] = {"血灵草": 99, "紫叶兰": 99}
    t = 0
    for _ in range(20):
        t += cfg.CRAFT_CD_SEC + 1
        core.craft(p, "回灵丹", rng=lambda: 0.0, now_ts=float(t))
    assert p["alch"] > 0


# ---------------------------------------------------------------------------
# 5. 斗技 / 异火
# ---------------------------------------------------------------------------
def test_skill_up_needs_fenxuesan():
    p = _p(1)
    p["bag"] = {}
    r = core.skill_up(p, "龙吟碎")
    assert r["ok"] is False and r["reason"] == "mat" and r["item"] == "焚血散"

    p["bag"] = {"焚血散": 10}
    r = core.skill_up(p, "龙吟碎")
    assert r["ok"] is True and p["skills"]["龙吟碎"] == 2
    # 消耗 = 当前等级
    assert core.count_item(p, "焚血散") == 9


def test_skill_damage_mult():
    assert cfg.skill_dmg_mult("龙吟碎", 1) == cfg.SKILL_BY_NAME["龙吟碎"]["dmg_mult"]
    assert abs(cfg.skill_dmg_mult("龙吟碎", 2) / cfg.skill_dmg_mult("龙吟碎", 1) - 1.2) < 1e-9


def test_feed_flame():
    need = int(cfg.FLAME_BY_NAME["青莲地心火"]["need"])      # 第 1 级所需 = need × 1
    p = _p(1)
    p["bag"] = {"药渣": 10}
    r = core.feed_flame(p, "青莲地心火")
    assert r["ok"] and r["give"] == 10 and r["lv"] == 0
    assert r["left"] == need - 10, f"left={r['left']} need={need}"

    q = _p(1)
    q["bag"] = {"药渣": need}
    r = core.feed_flame(q, "青莲地心火")
    assert r["done"] and q["flames"]["青莲地心火"] == 1

    # 满级后必须封顶
    s = _p(1)
    s["flames"]["青莲地心火"] = cfg.FLAME_MAX_LV
    s["bag"] = {"药渣": 99999}
    r = core.feed_flame(s, "青莲地心火")
    assert not r["ok"] and r["reason"] == "max"


def test_flame_effects():
    p = _p(1)
    base_mp = core.mp_max(p)
    p["flames"]["青莲地心火"] = 2
    assert core.mp_max(p) == int(core.stats(p)["斗气"] * 2 * 1.5)

    q = _p(1)
    q["flames"]["陨落心炎"] = 2
    assert core.flame_exp_mult(q) == 2.0

    r = _p(1)
    d0, c0 = core.dodge_rate(r), core.crit_rate(r)
    r["flames"]["净莲妖火"] = 1
    assert core.dodge_rate(r) == d0 + 10 and core.crit_rate(r) == c0 + 10


# ---------------------------------------------------------------------------
# 6. 升级 / 斗帝
# ---------------------------------------------------------------------------
def test_level_up_flow():
    p = _p(1)
    p["exp"] = cfg.exp_need(1) - 1
    assert core.can_level(p) is False
    p["exp"] = cfg.exp_need(1)
    assert core.can_level(p) is True
    r = core.level_up(p)
    assert r["ok"] and p["lv"] == 2
    assert p["exp"] == 0


def test_tiandi_needs_luck():
    p = _p(28)
    p["exp"] = cfg.exp_need(28)
    p["luck"] = 0
    r = core.level_up(p, rng=lambda: 0.99)     # 超不过成功率 -> 失败
    assert r["ok"] is False and r["reason"] == "tiandi_fail"

    q = _p(28)
    q["exp"] = cfg.exp_need(28)
    q["luck"] = 0
    q["diyan"] = True                          # 帝炎 -> 100%
    r = core.level_up(q, rng=lambda: 0.999)
    assert r["ok"] and q["lv"] == 29


def test_tiandi_rate():
    assert cfg.tiandi_rate(0, False) == 0.30
    assert cfg.tiandi_rate(9999, False) == 0.90
    assert cfg.tiandi_rate(0, True) == 1.0


# ---------------------------------------------------------------------------
# 7. 战斗
# ---------------------------------------------------------------------------
def test_fight_win_and_rewards():
    p = _p(1)
    p["hp"] = core.stats(p)["体力"]
    mon = core.monster_scaled(p, cfg.MONSTERS[0])
    res = core.fight(p, mon, rng=lambda: 0.5)
    assert "win" in res and res["rounds"] >= 1
    if res["win"]:
        assert p["exp"] > 0 and p["gold"] > cfg.START_GOLD


def test_fight_always_terminates():
    """任意随机序列都必须在 15 回合内结束（防死循环）。"""
    import random
    rnd = random.Random(7)
    for lv in (1, 5, 10, 20, 28):
        for mon in cfg.MONSTERS:
            p = _p(lv)
            p["hp"] = core.stats(p)["体力"]
            res = core.fight(p, core.monster_scaled(p, mon), rng=rnd.random)
            assert res["rounds"] <= 15


def test_monster_scaled_tracks_player():
    """缩放到"同阶"：玩家每级变强，怪也跟着变强，但"几回合打完"恒定。

    这条是防断崖的回归测试 —— 固定数值怪物 + 玩家每 9 级平方跃升 = 必死局。
    """
    for lv in range(1, 29):
        p = _p(lv)
        st = core.stats(p)
        pool = [m for m in cfg.MONSTERS if abs(m["lv"] - lv) <= 2] or cfg.MONSTERS[:1]
        for sp in pool:
            m = core.monster_scaled(p, sp)
            # 玩家斩杀回合：血量 ÷ 逐回合累计伤害，必须落在设计值附近
            cum, r = 0.0, 0
            for d in core.damage_schedule(p, 15):
                r += 1
                cum += d * core.mitigation(m["def"])
                if cum >= m["hp"]:
                    break
            assert 3 <= r <= 7, f"lv{lv} {sp['name']} 需 {r} 回合斩杀"
            # 怪斩杀回合
            per = m["atk"] * core.mitigation(st["防御"])
            mr = st["体力"] / per
            assert 3.0 <= mr <= 8.0, f"lv{lv} {sp['name']} 怪需 {mr:.1f} 回合斩杀玩家"


def test_out_of_pool_monster_still_killable():
    """越级怪（生产里抽不到，但防呆）也必须在 15 回合内打得死，不能变成死局。"""
    for lv in (1, 10, 20, 28):
        p = _p(lv)
        for sp in cfg.MONSTERS:
            m = core.monster_scaled(p, sp)
            cum = 0.0
            hits = 0
            for d in core.damage_schedule(p, 15):
                hits += 1
                cum += d * core.mitigation(m["def"])
                if cum >= m["hp"]:
                    break
            assert hits <= 12, f"lv{lv} 打 {sp['name']} 需 {hits} 回合"


def test_fight_mp_not_carried_over():
    """斗气每战回满、战后不结转 —— 否则连打第二场只能普攻（输出 42%）。"""
    p = _p(10)
    p["hp"] = core.stats(p)["体力"]
    before = core.mp_max(p)
    core.fight(p, core.monster_scaled(p, cfg.MONSTERS[3]), rng=lambda: 0.5)
    assert p["mp"] == before
    # 第二场的第一击必须仍是斗技（伤害显著高于普攻）
    p["hp"] = core.stats(p)["体力"]
    res = core.fight(p, core.monster_scaled(p, cfg.MONSTERS[3]), rng=lambda: 0.5)
    assert res["log"][0].get("skill") is True


def test_mitigation_bounds():
    assert abs(core.mitigation(0) - 1.0) < 1e-9
    assert core.mitigation(800) == 0.5
    assert 0 < core.mitigation(99999) < 0.05


# ---------------------------------------------------------------------------
# 8. 商店 / 背包
# ---------------------------------------------------------------------------
def test_shop_buy():
    p = _p(1)
    p["gold"] = 1000
    r = core.shop_buy(p, "血灵草", 2)
    assert r["ok"] and p["gold"] == 1000 - 240
    assert core.count_item(p, "血灵草") == 2

    r2 = core.shop_buy(p, "地心芝", 99)
    assert r2["ok"] is False and r2["reason"] == "poor"


def test_bag_slots_hides_zero():
    p = _p(1)
    p["bag"] = {"炎星石": 0, "药渣": 3}
    names = [n for n, _ in core.bag_slots(p)]
    assert "炎星石" not in names and "药渣" in names


# ---------------------------------------------------------------------------
# 9. 任务
# ---------------------------------------------------------------------------
def test_task_flow():
    p = _p(1)
    r = core.task_accept(p, rng=lambda: 0.0)
    assert r["ok"] and p["task"]
    t = p["task"]
    core.task_progress(p, t["kind"], t["target"])
    res = core.task_submit(p)
    assert res["ok"] and p["task"] is None


def test_main_task_flow():
    p = _p(1)
    m = core.current_main(p)
    core.main_progress(p, m["kind"], m["target"])
    res = core.main_submit(p)
    assert res["ok"] and p["main_idx"] == 1


# ---------------------------------------------------------------------------
# 10. 战力
# ---------------------------------------------------------------------------
def test_power_monotonic():
    a = _p(1)
    b = _p(10)
    c = _p(28)
    assert core.power(a) < core.power(b) < core.power(c)


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
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print(f"FAIL  {name}  ->  {type(e).__name__}: {e}")
    print("=" * 70)
    print(f"CORE_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
