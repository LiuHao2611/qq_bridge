# -*- coding: utf-8 -*-
"""数值平衡模拟（不是单测，是调参工具）。

对每个等级：造一个"正常养成"的角色（武器按等级合理强化），
对同阶怪打 200 场，统计胜率 / 平均回合 / 残血比例。
判定标准（机器验收）：
  * 胜率下限 70% —— 不能出现"打不过"的死局
  * 胜率上限 99.5% —— 不能白给；1 级是新手第一战，豁免上限
  * 平均回合 2~10（不拖沓）
  * 全程 "升到斗帝需要多少场战斗" 落在 120~400 场

调参记录（别再从头试错）：
  * 固定数值怪物 + 玩家每 9 级一次 阶² 平方跃升 = 必然断崖（实测 16~18 级胜率 0%）
    -> 改成 core.monster_scaled 按玩家当前属性反推。
  * 血量曾按"单回合平均伤害 × 回合数"外推，忽略了"蓝只够放 5 次斗技"，
    第 6 回合起输出掉到 42% -> 22/26 级要 11 回合才杀得死。改成 damage_schedule 逐回合累计。
  * 物种倍率（血 ×1.05 攻 ×1.00）叠等级差（±14%）会把难度比值推出刀尖 -> 全部越界翻转。
    改成物种难度直接写成 hard = 怪斩杀回合 ÷ 玩家斩杀回合，可调且单调。
  * 胜负是整数回合比较，没有伤害浮动时胜率只会在 0/50/100 之间跳；
    MON_DMG_VAR=0.25 + MON_ROUND_RATIO=1.10 是扫出来的组合（28 级全部落在 73%~99.5%）。

运行：python src/plugins/doupo/_balance.py
"""
from __future__ import annotations

import os
import random
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

# 桩掉 nonebot：本工具只算数值，不需要真驱动
_nb = types.ModuleType("nonebot")
_nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)


def _on_regex(*a, **k):
    m = types.SimpleNamespace()
    m.handle = lambda *a, **k: (lambda f: f)
    m.finish = lambda *a, **k: None
    return m


_nb.on_regex = _on_regex
sys.modules["nonebot"] = _nb
_ad = types.ModuleType("nonebot.adapters")
_ob = types.ModuleType("nonebot.adapters.onebot")
_v11 = types.ModuleType("nonebot.adapters.onebot.v11")
_v11.MessageEvent = object
_v11.Message = object
_v11.MessageSegment = types.SimpleNamespace(text=lambda *a, **k: None)
sys.modules["nonebot.adapters"] = _ad
sys.modules["nonebot.adapters.onebot"] = _ob
sys.modules["nonebot.adapters.onebot.v11"] = _v11

from doupo import core, config as cfg  # noqa: E402

N = 200


def make(lv: int, weapon: int | None = None) -> dict:
    p = core.create_player("1", "模拟", "g")
    p["lv"] = lv
    if weapon is None:
        # 合理养成：前期 0~2 级强化，中期 4，后期 6~8
        weapon = 0 if lv < 5 else (2 if lv < 10 else (4 if lv < 16 else (6 if lv < 22 else 8)))
    p["weapon"] = weapon
    st = core.stats(p)
    p["hp"] = st["体力"]
    p["mp"] = core.mp_max(p)
    p["flames"] = {n: (1 if lv >= 20 else 0) for n in cfg.FLAME_NAMES}
    return p


def species_pool(lv: int) -> list:
    """与 core.pick_monster 同口径（±2 级）。"""
    return [m for m in cfg.MONSTERS if abs(m["lv"] - lv) <= 2] or cfg.MONSTERS[-2:]


def run_level(p: dict, rnd) -> tuple:
    """跑一个等级的 N 场战斗，每场都随机抽物种（与生产完全一致）。"""
    pool = species_pool(int(p["lv"]))
    wins, rounds, hps = 0, [], []
    for _ in range(N):
        q = dict(p)
        q["bag"] = {}
        q["bonus"] = {}
        sp = pool[int(rnd.random() * len(pool))]
        mon = core.monster_scaled(q, sp)
        res = core.fight(q, mon, rng=rnd.random)
        if res["win"]:
            wins += 1
            rounds.append(res["rounds"])
        hps.append(q["hp"] / core.stats(q)["体力"])
    return wins / N, (sum(rounds) / len(rounds) if rounds else 0), (sum(hps) / len(hps))


def main() -> int:
    rnd = random.Random(20261026)
    print(f"{'Lv':>3} {'境界':<10} {'武器':>3} {'战力':>7} "
          f"{'怪物池':<18} {'胜率':>6} {'回合':>5} {'残血%':>6}")
    print("-" * 82)
    bad = []
    for lv in range(1, 30):
        p = make(lv)
        wr, avg_r, avg_hp = run_level(p, rnd)
        pool = "/".join(m["name"] for m in species_pool(lv))
        flag = ""
        upper = 1.01 if lv == 1 else 0.995      # 1 级新手第一战豁免上限
        if lv < 29 and not (0.70 <= wr <= upper):
            flag = "  <== 胜率越界"
            bad.append((lv, "winrate", wr))
        if avg_r and not (2 <= avg_r <= 10):
            flag += "  <== 回合越界"
            bad.append((lv, "rounds", avg_r))
        print(f"{lv:>3} {cfg.full_realm(lv):<10} {p['weapon']:>3} {core.power(p):>7} "
              f"{pool:<18} {wr:>6.1%} {avg_r:>5.1f} {avg_hp:>6.1%}{flag}")

    # ---- 全程场次估算（按各等级实际的异火加成算）----
    total_fights = 0
    exp_rnd = random.Random(20261027)
    for lv in range(1, 29):
        need = cfg.exp_need(lv)
        p = make(lv)
        pool = species_pool(lv)
        gain = 0
        for _ in range(50):
            sp = pool[int(exp_rnd.random() * len(pool))]
            gain += int(core.monster_scaled(p, sp)["exp"] * core.flame_exp_mult(p))
        gain = max(1, int(gain / 50))
        total_fights += max(1, -(-need // gain))
    print("-" * 78)
    print(f"升到斗帝总计约需 {total_fights} 场战斗（目标 120~400）")
    if not (120 <= total_fights <= 400):
        bad.append((0, "total_fights", total_fights))
    print(f"结论：{' 全部达标' if not bad else ' 有 ' + str(len(bad)) + ' 项越界 -> ' + str(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
