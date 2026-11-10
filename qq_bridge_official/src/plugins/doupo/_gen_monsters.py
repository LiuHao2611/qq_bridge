# -*- coding: utf-8 -*-
"""反推怪物数值表：给定"参考角色"，算出刚好打 4 回合、掉一半血的怪。

思路（不拍脑袋）：
  rounds = 4，血量损失 = 50%
  monster.def  = 0.35 × 角色防御        -> 让减伤系数稳定在 ~0.72
  monster.hp   = 4 × 角色每回合输出
  monster.atk  = (角色体力 × 0.5 / 4) / 减伤系数(角色防御)
  monster.exp  = 升下一级所需 / 6        -> 每级约 6 场
运行后把打印的表粘回 config.py。
"""
from __future__ import annotations

import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

_nb = types.ModuleType("nonebot")
_nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
_m = types.SimpleNamespace(handle=lambda *a, **k: (lambda f: f), finish=lambda *a, **k: None)
_nb.on_regex = lambda *a, **k: _m
sys.modules["nonebot"] = _nb
sys.modules["nonebot.adapters"] = types.ModuleType("nonebot.adapters")
sys.modules["nonebot.adapters.onebot"] = types.ModuleType("nonebot.adapters.onebot")
_v = types.ModuleType("nonebot.adapters.onebot.v11")
_v.MessageEvent = object
sys.modules["nonebot.adapters.onebot.v11"] = _v

from doupo import core, config as cfg  # noqa: E402


ATK_SCALE = 1.80   # 怪攻系数：越大越难；1.8 目标胜率 ~80%


def weapon_for(lv: int) -> int:
    return 0 if lv < 5 else (2 if lv < 10 else (4 if lv < 16 else (6 if lv < 22 else 8)))


def ref(lv: int) -> dict:
    p = core.create_player("1", "参考", "g")
    p["lv"] = lv
    p["weapon"] = weapon_for(lv)
    return p


def out_per_round(p) -> float:
    """考虑技能（有蓝就放）+ 暴击期望的单回合输出（未乘减伤）。"""
    st = core.stats(p)
    best = max(cfg.SKILL_NAMES, key=lambda n: cfg.skill_dmg_mult(n, p["skills"][n]))
    sk = (st["力量"] * 0.5 + st["斗气"] * 1.5) * cfg.skill_dmg_mult(best, 1)
    normal = st["力量"] * 1.0
    cost = max(1, core.mp_max(p) // 5)
    casts = core.mp_max(p) // cost             # 一场大约能放几次技能
    crit_mult = 1 + core.crit_rate(p) / 100 * 0.5
    avg = (sk * min(4, casts) + normal * max(0, 4 - casts)) / 4
    return avg * crit_mult


def main():
    print("MONSTERS = [")
    for m in cfg.MONSTERS:
        lv = m["lv"]
        p = ref(lv)
        st = core.stats(p)
        mdef = max(20, int(st["防御"] * 0.35))
        dmg = out_per_round(p) * core.mitigation(mdef)
        hp = int(dmg * 4 / 100) * 100
        # 掉 50% 血 / 4 回合
        raw_atk = (st["体力"] * 0.5 / 4) / core.mitigation(st["防御"]) * ATK_SCALE
        atk = int(raw_atk / 10) * 10
        exp = cfg.exp_need(lv) // 6 if lv < 29 else 100000
        gold = int(exp * 0.25 / 10) * 10
        print(f'    {{"name": {m["name"]!r}, "lv": {lv}, "hp": {hp}, '
              f'"atk": {atk}, "def": {mdef}, "exp": {exp}, "gold": {gold}}},')
    print("]")


if __name__ == "__main__":
    main()
