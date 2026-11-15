# -*- coding: utf-8 -*-
"""斗技多维度验收：数据完整 + 效果真进战斗（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_multidim.py

守的是用户 2026-11-15 明确要求的两件事：
  1. 天冥宗每门斗技都要有多维设计（role/effects/gate），低伤技能不能只有数字；
  2. 效果不是死文本 —— 破防真增伤、吸血真回血、控制真跳过、DoT 真扣血。

故意弄坏任一项（删掉 effects / 让 _hit 不消费效果）本测试必然失败。
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
from doupo import core, skills as sk, skill_effects as fx, config as cfg  # noqa: E402


def _tianming_player():
    p = core.create_player("77777", "天冥弟子", "g1")
    p["faction"] = "tianmingzong"
    p["loadout"] = []
    sk.ensure_loadout(p)
    return p


# ---------------------------------------------------------------------------
# 1. 数据完整性：23 门都有多维设计，低伤技能必带战术维度
# ---------------------------------------------------------------------------
def test_all_tianming_skills_have_multidim_design():
    ents = sk.catalog("tianmingzong")
    assert len(ents) == 23, f"天冥宗应 23 门，实际 {len(ents)}"
    for e in ents:
        name = e["name"]
        assert e.get("role") in fx.ROLES, f"{name} 主定位非法: {e.get('role')!r}"
        assert e.get("effects"), f"{name} 无效果原子（退化成纯数值）"
        assert e.get("gate"), f"{name} 无门控代价"
        assert not fx.validate_effects(e["effects"]), f"{name} 效果字段非法"


def test_low_damage_skills_carry_tactical_dimension():
    """反无脑红线：伤害倍率 <1.2 的技能必须带战术维度（不能只比数字大）。"""
    low = [e for e in sk.catalog("tianmingzong") if float(e["dmg"]) < 1.2]
    assert low, "应存在低伤技能用于验证"
    for e in low:
        kinds = {x["t"] for x in e["effects"]}
        # 纯输出向的加伤不算战术维度，必须有控制/异常/防御/位移/破防/减益之一
        tactical = kinds & {
            "dot", "pierce", "debuff_atk", "debuff_evade", "debuff_heal",
            "stun", "silence", "root", "knockback", "reflect", "reduce", "evade",
            "lifesteal", "chain", "cond",
        }
        assert tactical, f"低伤技能 {e['name']} 只有输出，无战术维度: {kinds}"


# ---------------------------------------------------------------------------
# 2. 效果真进战斗（trust but verify：直接调 _hit 对比）
# ---------------------------------------------------------------------------
def test_pierce_really_increases_damage():
    """破防 50% 应让同一击打在 def=800 的怪身上伤害显著更高。"""
    p = _tianming_player()
    base = {"name": "测试掌", "tier": "普通", "dmg": 1.0,
            "mp": 0.2, "cd": 2, "unlock": 1, "effects": []}
    plain = dict(base)
    pierce = dict(base, effects=[{"t": "pierce", "v": 0.5, "d": 1, "p": 1.0}])
    rng = lambda: 0.01  # 必暴击 + 破防必中，两组同序列，唯一变量是破防
    h_plain = core._hit(p, 800, plain, rng, fx.new_state(), fx.new_state(), {})
    h_pierce = core._hit(p, 800, pierce, rng, fx.new_state(), fx.new_state(), {})
    assert h_pierce["dmg"] > h_plain["dmg"], (
        f"破防未增伤: {h_plain['dmg']} -> {h_pierce['dmg']}")
    assert any("破防" in n for n in h_pierce["notes"]), "破防未播报维度文案"


def test_lifesteal_returned_for_fight_to_heal():
    """吸血效果必须回传 heal_ratio 并播报，fight 才可能真回血。"""
    p = _tianming_player()
    entry = {"name": "嗜血化骨手", "tier": "高阶", "dmg": 1.6,
             "effects": [{"t": "lifesteal", "v": 0.25}]}
    h = core._hit(p, 200, entry, lambda: 0.5, fx.new_state(), fx.new_state(), {})
    assert abs(h["heal_ratio"] - 0.25) < 1e-9, f"吸血比例未回传: {h['heal_ratio']}"
    assert any("吸血25%" in n for n in h["notes"]), "吸血未播报"


def test_dot_ticks_and_control_skips_turn():
    """DoT 连续两回合扣血后到期；眩晕让目标跳过一次行动。"""
    target = fx.new_state()
    fx.on_hit([{"t": "dot", "v": 0.05, "d": 2, "kind": "流血"},
               {"t": "stun", "p": 1.0, "d": 1}], fx.new_state(), target, 1000, lambda: 0.01)
    a = fx.on_turn_start(target, lambda: 0.9)
    b = fx.on_turn_start(target, lambda: 0.9)
    c = fx.on_turn_start(target, lambda: 0.9)
    assert a["dot_dmg"] == 50 and b["dot_dmg"] == 50 and c["dot_dmg"] == 0
    assert a["skip"] is True and b["skip"] is False, "眩晕应只跳过一次行动"


def test_fight_log_carries_multidim_notes():
    """真实战斗链路里，天冥宗技能命中后 notes 随战报带回（逐维可播报）。"""
    p = _tianming_player()
    p["hp"] = core.stats(p)["体力"]
    mon = core.monster_scaled(p, cfg.MONSTERS[0])
    # rng=0.01 会掷中概率型效果（碎石掌 20% 破防 / 铁山拳 10% 眩晕 /
    # 血煞掌 DoT 默认必中），确保效果链路被真实走到，而非被概率跳过
    res = core.fight(p, mon, rng=lambda: 0.01)
    all_notes = [n for it in res["log"] for n in (it.get("notes") or [])]
    # 四槽随机释放，至少应命中过一次带效果的天冥宗技（本宗技全带 effects）
    skill_hits = [it for it in res["log"] if it.get("skill")]
    assert skill_hits, "战斗未释放任何斗技"
    assert any(it.get("notes") for it in skill_hits) or all_notes, (
        "战斗中出现斗技命中却完全没有多维文案，效果未进战斗链路")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn(); ok += 1
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print(f"FAIL  {name} -> {type(e).__name__}: {e}")
    print("=" * 70)
    print(f"MULTIDIM_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
