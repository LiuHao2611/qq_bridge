# -*- coding: utf-8 -*-
"""斗破苍穹 · 核心逻辑（纯函数，零 nonebot 依赖，可直接单测）。

约定：
  * 所有函数只就地改 player dict，持久化由调用方（commands 层）负责
  * 所有随机都走可注入的 rng(返回 0..1)，方便测试复现
  * 不 import nonebot / 不发消息 / 不读文件 —— 保证 test_core 能裸跑
"""

from __future__ import annotations

import random
import time

from . import config as cfg
from . import factions as fac
from . import skills as sk_mod


# ---------------------------------------------------------------------------
# 等级成长  [源码 ah.java:165]
# ---------------------------------------------------------------------------
def c_stat(base: int, lv: int) -> int:
    """原版成长公式：base*10 + 阶²*base*10 + base*lv。

    2026-11-14：阶改成按**大境界下标**取（cfg.realm_index），
    不再写死 (lv-1)//9 —— 原著补全 斗之气/斗尊巅峰/半圣 后各境界小段数不同
    （半圣 3 段、斗帝 1 级），用除法会把斗圣前五星算成半圣阶。
    实机校验（形象 0 / 玄重尺 +0 / 等级 1）：
      体力 880  力量 275  斗气 165  敏捷 220  防御 132
    与人物面板显示逐格吻合。
    """
    k = cfg.realm_index(lv)
    return int(base) * 10 + k * k * int(base) * 10 + int(base) * int(lv)


# ---------------------------------------------------------------------------
# 角色创建
# ---------------------------------------------------------------------------
def create_player(qq, name, group) -> dict:
    now = time.time()
    st = {
        "qq": str(qq), "name": name, "group": str(group),
        "lv": 1, "exp": 0,
        "lv_ver": 3,          # 原著13阶存档标记（store 用它判断要不要迁移）
        "gold": cfg.START_GOLD, "luck": cfg.START_LUCK,
        "weapon": 0,
        "hp": 0, "mp": 0,
        "skills": {},
        "flames": {n: 0 for n in cfg.FLAME_NAMES},
        "bag": {"回灵丹": cfg.START_HP_POTION, "炎星石": cfg.START_STONES},
        "alch": cfg.ALCH_START, "alch_exp": 0,
        "bonus": {},
        "last_settle": now, "last_fight": 0, "last_craft": 0,
        "wins": 0, "losses": 0,
        "main_idx": 0, "task": None,
        "diyan": False,
    }
    st["hp"] = stats(st)["体力"]
    st["mp"] = mp_max(st)

    # 入道随机宗门 + 自动配四槽（斗技三阶四槽骨架）
    # 按 qq 定种子：每个玩家分到一个"随机但稳定"的宗门（不可反复重掷）
    try:
        fac.assign_faction(st, rng=random.Random("faction:" + str(qq)).random)
    except Exception:
        st.setdefault("faction", "")
        st.setdefault("faction_name", "")
    try:
        sk_mod.ensure_loadout(st)
    except Exception:
        st["loadout"] = []
    return st


# ---------------------------------------------------------------------------
# 属性面板
# ---------------------------------------------------------------------------
def stats(p) -> dict:
    """当前六维（力量/敏捷/斗气/防御/体力）+ 机缘。

    [源码] 基础值走 c_stat；玄重尺 4 词条加算；L=9 走硬编码 2000/2000/1000/1200。
    """
    lv = int(p.get("lv", 1))
    out = {k: c_stat(v, lv) for k, v in cfg.HERO_BASE.items()}

    w = int(p.get("weapon", 0))
    if w >= cfg.WEAPON["max_internal"]:
        for k, v in cfg.WEAPON["max_stats"].items():
            out[k] = out.get(k, 0) + v
    else:
        for k, v in cfg.WEAPON["stats"].items():
            out[k] = out.get(k, 0) + cfg.weapon_stat(v, w)

    for k, v in (p.get("bonus") or {}).items():
        out[k] = out.get(k, 0) + int(v)

    if p.get("diyan"):
        for k in out:
            out[k] = int(out[k] * 1.2)

    out["机缘"] = int(p.get("luck", 0))
    return out


def mp_max(p) -> int:
    """斗气槽上限。青莲地心火每级 +25%（实机说明原文）。"""
    st = stats(p)
    flame = int((p.get("flames") or {}).get("青莲地心火", 0))
    return int(st["斗气"] * 2 * (1 + 0.25 * flame))


def dodge_rate(p) -> float:
    """闪避率（%）。[源码] y.java:157  a[0][1] += (a[0][6]+20)/200

    净莲妖火：实机说明「每提升一级增加 10% 暴击和闪避几率」-> 闪避也按 10%/级。
    """
    st = stats(p)
    base = cfg.HERO_DODGE + (st["敏捷"] + 20) // 200
    base += 10 * int((p.get("flames") or {}).get("净莲妖火", 0))
    base += int((p.get("bonus") or {}).get("闪避", 0))
    return float(min(80, base))


def crit_rate(p) -> float:
    """暴击率（%）。[源码] y.java:155  a[0][2] += (a[0][6]+30)/100"""
    st = stats(p)
    base = cfg.HERO_CRIT + (st["敏捷"] + 30) // 100
    base += 10 * int((p.get("flames") or {}).get("净莲妖火", 0))
    base += int((p.get("bonus") or {}).get("暴击", 0))
    return float(min(80, base))


def power(p) -> int:
    """战力：用于排行榜/切磋。"""
    s = stats(p)
    return int(
        s["力量"] * 2.0 + s["敏捷"] * 1.5 + s["斗气"] * 1.2
        + s["防御"] * 1.5 + s["体力"] * 0.2
        + int(p.get("weapon", 0)) * 200
    )


# ---------------------------------------------------------------------------
# 被动修炼（离线也涨，不强制打卡）
# ---------------------------------------------------------------------------
def flame_exp_mult(p) -> float:
    """陨落心炎：每级获得经验 +50%。"""
    return 1.0 + 0.5 * int((p.get("flames") or {}).get("陨落心炎", 0))


def settle(p, now_ts=None) -> dict:
    now_ts = time.time() if now_ts is None else now_ts
    elapsed = max(0.0, now_ts - float(p.get("last_settle", now_ts)))
    gain = int(elapsed / 60.0 * cfg.CULTIVATE_PER_MIN * flame_exp_mult(p))
    if gain > 0:
        p["exp"] = int(p.get("exp", 0)) + gain
    p["last_settle"] = now_ts
    return {"gain": gain, "minutes": int(elapsed // 60)}


# ---------------------------------------------------------------------------
# 升级 / 突破
# ---------------------------------------------------------------------------
def can_level(p) -> bool:
    lv = int(p.get("lv", 1))
    return lv < cfg.MAX_LV and int(p.get("exp", 0)) >= cfg.exp_need(lv)


def level_up(p, rng=random.random) -> dict:
    """升一级。102 级九星斗圣 -> 103 级（斗帝）走机缘判定。"""
    lv = int(p.get("lv", 1))
    if lv >= cfg.MAX_LV:
        return {"ok": False, "reason": "max"}
    need = cfg.exp_need(lv)
    if int(p.get("exp", 0)) < need:
        return {"ok": False, "reason": "exp", "need": need - int(p.get("exp", 0))}

    if lv >= cfg.PEAK_LV:
        # 进阶斗帝：机缘决定成功率（m.java：机缘值影响进阶斗帝几率）
        rate = cfg.tiandi_rate(p.get("luck", 0), p.get("diyan", False))
        if rng() < rate:
            p["exp"] = int(p["exp"]) - need
            p["lv"] = cfg.MAX_LV
            p["diyan"] = True
            p["luck"] = 9999
            _refill(p)
            return {"ok": True, "tiandi": True, "rate": rate}
        p["exp"] = int(p["exp"] * (1 - cfg.LOSE_HP_ON_DEFEAT))
        return {"ok": False, "reason": "tiandi_fail", "rate": rate}

    p["exp"] = int(p["exp"]) - need
    p["lv"] = lv + 1
    _refill(p)
    return {"ok": True, "lv": p["lv"], "name": cfg.full_realm(p["lv"])}


def _refill(p) -> None:
    st = stats(p)
    p["hp"] = st["体力"]
    p["mp"] = mp_max(p)


# ---------------------------------------------------------------------------
# 战斗
# ---------------------------------------------------------------------------
def mitigation(defence: int) -> float:
    """减伤系数：防御 800 时减半。"""
    d = max(0, int(defence))
    return 800.0 / (800.0 + d)


def pick_monster(p, rng=random.random) -> dict:
    """按玩家等级挑选一个"够得着但不算白给"的怪。

    九星制把等级轴拉到 83，而物种表只有 7 只，用原来的「等级差 ≤2」会大面积
    选不到（中高等级玩家一直掉回最弱的地鬼）。改成两步：
      1) 先只留「同一大境界内」的物种（等级差 ≤ LV_PER_REALM），保证不会
         让 1 级新手撞上 27 级的赤炎狼（等级差修正会把战斗拖成 9 回合）；
      2) 再在窗口内取等级最接近的 3 只随机，保留一点随机性。
    窗口内一只都没有（物种表没覆盖到）时退到全表最近的一只。
    """
    lv = int(p.get("lv", 1))
    window = [m for m in cfg.MONSTERS if abs(int(m["lv"]) - lv) <= cfg.LV_PER_REALM]
    pool = window or cfg.MONSTERS
    cand = sorted(pool, key=lambda m: abs(int(m["lv"]) - lv))[:3]
    return rng_choice(cand or cfg.MONSTERS[:1], rng)


def rng_choice(seq, rng=random.random):
    if not seq:
        return None
    return seq[min(len(seq) - 1, int(rng() * len(seq)))]


def damage_schedule(p, rounds: int = 15) -> list:
    """逐回合的期望输出（四槽模型 · 已算暴击期望，未乘减伤）。

    与 `fight` 同口径：每回合在已上阵四槽里**随机释放**，期望≈四槽平均；
    斗气按阶扣除，不足以释放任一技时转武器普攻。用于反解同阶怪血量，
    使「几回合打完」保持为设计值（TTK_PLAYER）。
    """
    st = stats(p)
    normal_raw = st["力量"] * float(cfg.WEAPON_BASE_MULT)
    entries = _loadout_entries(p)
    if not entries:
        return [normal_raw] * max(1, rounds)

    mpmax = mp_max(p)
    lv_map = p.get("skills") or {}
    slots = []
    for e in entries:
        lv = int(lv_map.get(e["name"], 1))
        raw = (st["力量"] * 0.5 + st["斗气"] * 1.5) * sk_mod.skill_mult(e, lv)
        slots.append((raw, sk_mod.tier_cost(e["tier"], mpmax)))
    avg_dmg = sum(d for d, _ in slots) / len(slots)
    avg_cost = sum(c for _, c in slots) / len(slots)

    pool = mpmax
    out = []
    for _ in range(max(1, rounds)):
        if pool >= avg_cost:
            out.append(avg_dmg)
            pool -= avg_cost
        else:
            out.append(normal_raw)
    mult = 1.0 + crit_rate(p) / 100.0 * 0.5
    return [d * mult for d in out]


def cum_damage(p, rounds: int) -> float:
    """前 rounds 个回合的累计期望输出（未乘减伤）。"""
    sch = damage_schedule(p, int(rounds) + 1)
    whole = int(rounds)
    frac = rounds - whole
    total = sum(sch[:whole])
    if frac > 0 and whole < len(sch):
        total += sch[whole] * frac
    return total


def monster_scaled(p, mon) -> dict:
    """把"物种"实例化成一只同阶怪。

    为什么不写死数值：玩家每 9 级 c_stat 有一次 阶² 的平方级跃升，
    固定血量的怪在跃升前后会从"白给"直接变"必死"（实测断崖：16~18 级胜率 0%）。

    血量：反解"玩家刚好在 TTK_PLAYER 回合斩杀"对应的累计伤害。
    攻击：反解"怪刚好在 TTK_PLAYER × MON_ROUND_RATIO 回合斩杀玩家"的单击值。
    """
    st = stats(p)
    lv = int(p.get("lv", 1))
    # 等级差只微调"几回合打完"，上下限收紧到 ±20%（原 ±60%/+80%）：
    # 原著 13 阶补齐后，物种表(7 只)不可能覆盖 103 级，总有些等级只能遇到
    # 隔了十几级的怪；旧钳位会把这种战斗压成 2.9 回合秒杀或拖到 9 回合，
    # 越过 test_monster_scaled_tracks_player 的 3~8 回合设计窗口。
    delta = max(-0.2, min(0.2, (int(mon["lv"]) - lv) * cfg.LV_DELTA_STEP))
    m_def = max(20, int(st["防御"] * cfg.MON_DEF_RATIO))

    # 玩家打完需要几回合（等级差只影响时长，不影响难度）
    k_rounds = cfg.TTK_PLAYER * (1.0 + delta)
    m_hp = int(cum_damage(p, k_rounds) * mitigation(m_def))
    # 怪打死玩家需要几回合 —— 物种难度直接就是这个比值，hard 越小越可怕
    m_rounds = k_rounds * cfg.MON_ROUND_RATIO * float(mon["hard"])
    m_atk = int(st["体力"] / m_rounds / mitigation(st["防御"]))
    exp = int(cfg.exp_need(lv) / cfg.EXP_FIGHTS_PER_LV * float(mon.get("exp_mul", 1.0)))
    gold = int(exp * 0.25 * float(mon.get("gold_mul", 1.0)))
    return {
        "name": mon["name"], "lv": int(mon["lv"]),
        "hp": max(1, m_hp), "atk": max(1, m_atk), "def": m_def,
        "exp": max(1, exp), "gold": max(1, gold),
    }


def _legacy_entries() -> list[dict]:
    """无宗门旧档的兜底：沿用原 4 门核心斗技（按普通阶计）。"""
    return [{
        "name": s["name"], "tier": "普通", "faction": "",
        "mp": cfg.SKILL_TIER_MP_FRAC["普通"], "dmg": float(s["dmg_mult"]),
        "unlock": 1, "desc": s.get("desc", ""),
    } for s in cfg.SKILLS]


def _ensure_faction(p) -> bool:
    """旧存档没有 faction 时惰性补一个（入道分宗的兜底）。

    ⚠️ 必须按 qq 定种子，与 create_player 同一套种子：否则旧档每次读出来都现抽一个，
    玩家会看到「宗门」每次问都不一样（实测 lv47 老档 faction=None，走的就是这条路径）。
    """
    if p.get("faction"):
        return True
    try:
        seed = "faction:" + str(p.get("qq") or "")
        fac.assign_faction(p, rng=random.Random(seed).random)
    except Exception:
        return False
    return bool(p.get("faction"))


def _loadout_entries(p) -> list[dict]:
    """当前上阵四槽的完整技能对象；无宗门旧档退回原 4 核心。"""
    _ensure_faction(p)
    ents = sk_mod.loadout_entries(p)
    return ents if ents else _legacy_entries()


def _pick_skill(p, mp: int, rng=random.random):
    """在「已上阵且斗气足够」的技中随机挑一个；都不够 -> (None, 0) 转普攻。"""
    entries = _loadout_entries(p)
    if not entries:
        return None, 0
    mpmax = mp_max(p)
    options = []
    for e in entries:
        c = sk_mod.tier_cost(e["tier"], mpmax)
        if c <= mp:
            options.append((e, c))
    if not options:
        return None, 0
    e, c = options[min(len(options) - 1, int(rng() * len(options)))]
    return e, c


def _hit(p, target_def, entry, rng) -> dict:
    """一次出手：entry 为 None 时用武器普攻，否则按该斗技算伤害。"""
    st = stats(p)
    crit = rng() * 100 < crit_rate(p)
    if entry is not None:
        lv = int((p.get("skills") or {}).get(entry["name"], 1))
        raw = (st["力量"] * 0.5 + st["斗气"] * 1.5) * sk_mod.skill_mult(entry, lv)
        kind = entry["name"]
    else:
        raw = st["力量"] * float(cfg.WEAPON_BASE_MULT)
        kind = "普攻"
    dmg = max(1, int(raw * mitigation(target_def) * (1.5 if crit else 1.0)))
    return {"dmg": dmg, "crit": crit, "kind": kind, "skill": entry is not None}


def faction_pools_open(p) -> list[str]:
    """当前境界已开放的卡池（新手 / 进阶 / 隐世）。"""
    lv = int(p.get("lv", 1))
    return [pool for pool in ("starter", "advanced", "hidden")
            if lv >= int(fac.POOL_UNLOCK.get(pool, 1))]


def faction_switch_cost(p) -> int:
    """转宗所需金币（随境界上涨）。"""
    return cfg.faction_switch_cost(int(p.get("lv", 1)))


def faction_candidates(p) -> list[dict]:
    """当前境界可以转去的宗门（排除本门）。

    入道只进新手池，进阶池要 lv40、隐世池要 lv90 —— 不转宗的话，
    25 派里 17 派的斗技玩家永远见不到。
    """
    lv = int(p.get("lv", 1))
    cur = str(p.get("faction") or "")
    out = []
    for pool in ("starter", "advanced", "hidden"):
        if lv < int(fac.POOL_UNLOCK.get(pool, 1)):
            continue
        for fid, f in fac.FACTIONS.items():
            if f["pool"] != pool or fid == cur:
                continue
            out.append({
                "faction": fid, "name": f["name"], "pool": pool,
                "unlock_lv": int(f["unlock_lv"]), "weight": int(f["weight"]),
                "skills": len(sk_mod.catalog(fid)),
            })
    return out


def change_faction(p, key: str) -> dict:
    """转宗：改换门庭，重配四槽。

    * 只能转去**当前境界已开放**的卡池里的宗门；
    * 扣金币（`cfg.faction_switch_cost`）；
    * **不重新发该派的入道物品**（否则转一次刷一次）；
    * 已升的技能等级按技名保留。
    """
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "reason": "noarg"}
    cands = faction_candidates(p)
    if not cands:
        return {"ok": False, "reason": "nopool"}

    hit = next((c for c in cands if c["faction"] == key or c["name"] == key), None)
    if not hit:
        hit = next((c for c in cands if key in c["name"] or key in c["faction"]), None)
    if not hit:
        same = fac.FACTIONS.get(str(p.get("faction") or ""))
        if same and (key in same["name"] or key == p.get("faction")):
            return {"ok": False, "reason": "same", "name": same["name"]}
        return {"ok": False, "reason": "notfound", "options": [c["name"] for c in cands]}

    cost = faction_switch_cost(p)
    if int(p.get("gold", 0)) < cost:
        return {"ok": False, "reason": "gold", "need": cost,
                "have": int(p.get("gold", 0))}

    old_name = str(p.get("faction_name") or "")
    p["gold"] = int(p.get("gold", 0)) - cost
    p["faction"] = hit["faction"]
    p["faction_name"] = hit["name"]
    p["loadout"] = []                      # 清空后由 ensure_loadout 按新派重配
    sk_mod.ensure_loadout(p)
    return {"ok": True, "faction": hit["faction"], "name": hit["name"],
            "old": old_name, "cost": cost,
            "loadout": list(p.get("loadout") or []),
            "pool": hit["pool"]}


def _best_skill(p) -> str:
    """兼容旧接口：取上阵四槽里期望伤害最高的一门。"""
    entries = _loadout_entries(p)
    if not entries:
        return cfg.SKILL_NAMES[0]
    st = stats(p)
    lv_map = p.get("skills") or {}
    best = max(entries, key=lambda e: (st["力量"] * 0.5 + st["斗气"] * 1.5)
               * sk_mod.skill_mult(e, int(lv_map.get(e["name"], 1))))
    return best["name"]


def skill_pool(p) -> list[dict]:
    """本宗全部斗技（含未解锁），带 unlocked / equipped 标记。"""
    _ensure_faction(p)
    ents = sk_mod.entries_for(p) or _legacy_entries()
    lv = int(p.get("lv", 1))
    lo = set(sk_mod.ensure_loadout(p))
    out = []
    for e in ents:
        d = dict(e)
        d["unlocked"] = int(d["unlock"]) <= lv
        d["equipped"] = d["name"] in lo
        out.append(d)
    return out


def skill_pool_sorted(p) -> list[dict]:
    """本宗斗技（含未解锁）按三阶稳定排序：普通 → 高阶 → 特殊。

    宗门卡统计（build_faction_data）与斗技名录共用同一顺序，
    保证「卡片上的门数统计」与 skill_pool 完全同源、不会错位。
    """
    pool = skill_pool(p)
    tier_order = {t: i for i, t in enumerate(cfg.SKILL_TIERS)}
    return sorted(pool, key=lambda e: (tier_order.get(e["tier"], 9),))


def loadout_entries(p) -> list[dict]:
    """公开版：当前上阵四槽的完整技能对象。"""
    return _loadout_entries(p)


def set_loadout(p, names) -> list[str]:
    """设置四槽：只保留本宗已解锁技能，最多 4 个。"""
    allowed = {e["name"] for e in skill_pool(p) if e["unlocked"]}
    p["loadout"] = [n for n in names if n in allowed][:cfg.LOADOUT_SIZE]
    return sk_mod.ensure_loadout(p)


def fight(p, mon, rng=random.random) -> dict:
    """回合制模拟，返回战报。不直接发消息，只改 p。

    斗气是「这一战能放几次斗技」的资源，每战回满、战后不结转：
    否则连打第二场时是 0 蓝状态，单回合输出只剩 42%，几乎必输。
    """
    st = stats(p)
    hp = int(p.get("hp") or st["体力"])
    mp = mp_max(p)
    m_hp = int(mon["hp"])
    m_atk = int(mon["atk"])
    m_def = int(mon["def"])

    rounds, dmg_log, taken = 0, [], 0
    dodge = dodge_rate(p)
    lifesteal = 0.02 * int((p.get("flames") or {}).get("三千焱炎火", 0))
    var = max(0.0, float(cfg.MON_DMG_VAR))

    while rounds < 15 and m_hp > 0 and hp > 0:
        rounds += 1
        entry, cost = _pick_skill(p, mp, rng)   # 随机释放「已上阵且斗气够」的技
        hit = _hit(p, m_def, entry, rng)
        if entry is not None:
            mp -= cost
        m_hp -= hit["dmg"]
        dmg_log.append(hit)
        if lifesteal > 0:
            heal = int(hit["dmg"] * lifesteal)
            hp = min(st["体力"], hp + heal)

        if m_hp <= 0:
            break
        # 怪反击
        if rng() * 100 < dodge:
            dmg_log.append({"dmg": 0, "kind": "闪避", "miss": True})
            continue
        roll = 1.0 - var + 2.0 * var * rng()
        dealt = max(1, int(m_atk * mitigation(st["防御"]) * roll))
        hp -= dealt
        taken += dealt
        dmg_log.append({"dmg": dealt, "kind": "兽击", "taken": True})

    win = m_hp <= 0
    p["hp"] = max(0 if not win else 1, hp)
    p["mp"] = mp_max(p)

    res = {
        "win": win, "rounds": rounds, "log": dmg_log,
        "taken": taken, "mon": mon, "mon_hp_left": max(0, m_hp),
    }
    if win:
        p["wins"] = int(p.get("wins", 0)) + 1
        exp = int(mon["exp"] * flame_exp_mult(p))
        p["exp"] = int(p.get("exp", 0)) + exp
        gold = int(mon["gold"] * (cfg.DROP_GOLD_MULT[0] + rng()))
        p["gold"] = int(p.get("gold", 0)) + gold
        res.update(exp=exp, gold=gold, drops=_roll_drops(p, rng))
    else:
        p["losses"] = int(p.get("losses", 0)) + 1
        p["hp"] = int(st["体力"] * (1 - cfg.LOSE_HP_ON_DEFEAT))
        res["hp_after"] = p["hp"]
    return res


def duel(p, vic, rng=random.random) -> dict:
    """掠夺：双方各用自己四槽随机互殴（p 先手）。斗气不足转武器普攻。

    只算胜负与战报，不改双方 hp/gold（结算由 commands.cmd_rob 负责），
    与 `fight` 共用四槽 / 按阶斗气 / 普攻兜底逻辑。
    """
    sp, sv = stats(p), stats(vic)
    hp_p, hp_v = int(sp["体力"]), int(sv["体力"])
    mp_p, mp_v = mp_max(p), mp_max(vic)
    log, rounds = [], 0
    while rounds < 15 and hp_p > 0 and hp_v > 0:
        rounds += 1
        e, c = _pick_skill(p, mp_p, rng)
        hit = _hit(p, int(sv["防御"]), e, rng)
        if e is not None:
            mp_p -= c
        hp_v -= hit["dmg"]
        log.append({"who": "p", **hit})
        if hp_v <= 0:
            break
        e2, c2 = _pick_skill(vic, mp_v, rng)
        hit2 = _hit(vic, int(sp["防御"]), e2, rng)
        if e2 is not None:
            mp_v -= c2
        hp_p -= hit2["dmg"]
        log.append({"who": "v", **hit2})
    win = hp_v <= 0 and hp_p > 0
    return {"win": win, "rounds": rounds, "log": log,
            "hp_p": max(0, hp_p), "hp_v": max(0, hp_v)}


def _roll_drops(p, rng) -> list:
    """[源码 h.java:57-78] 千分率判定 + 基础值到 2 倍随机。"""
    out = []
    if int(rng() * 1000) < cfg.DROP_RATE_STONE:
        n = 1 + int(rng() * 2)
        add_item(p, "炎星石", n)
        out.append(("炎星石", n))
    if int(rng() * 1000) < cfg.DROP_RATE_HERB:
        h = rng_choice(cfg.HERBS, rng)
        n = 1 + int(rng() * 2)
        add_item(p, h["name"], n)
        out.append((h["name"], n))
    return out


# ---------------------------------------------------------------------------
# 背包
# ---------------------------------------------------------------------------
def bag(p) -> dict:
    return p.setdefault("bag", {})


def count_item(p, name) -> int:
    return int(bag(p).get(name, 0))


def add_item(p, name, n=1) -> None:
    b = bag(p)
    b[name] = int(b.get(name, 0)) + int(n)
    if b[name] <= 0:
        b.pop(name, None)


def take_item(p, name, n=1) -> bool:
    if count_item(p, name) < int(n):
        return False
    add_item(p, name, -int(n))
    return True


def bag_slots(p) -> list:
    """按 24 格展示（实机包裹 4×6）。超出部分追加。"""
    items = [(k, v) for k, v in bag(p).items() if v > 0]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return items


# ---------------------------------------------------------------------------
# 强化（玄重尺）  [源码 l.java:361-368 / h.java:80-102]
# ---------------------------------------------------------------------------
def enhance_cost(p) -> int:
    """需要炎星石数量 = 内部 L + 1（实机 L=0 时显示「需要 1 块」）。"""
    return int(p.get("weapon", 0)) + 1


def enhance(p) -> dict:
    w = int(p.get("weapon", 0))
    if w > 8:
        return {"ok": False, "reason": "max"}
    cost = enhance_cost(p)
    if not take_item(p, "炎星石", cost):
        return {"ok": False, "reason": "stone", "need": cost, "have": count_item(p, "炎星石")}
    p["weapon"] = w + 1
    _refill(p)
    return {
        "ok": True, "internal": p["weapon"],
        "display": cfg.weapon_display_level(p["weapon"]),
        "name": cfg.weapon_name(p["weapon"]),
        "evolved": p["weapon"] > 8,
    }


def enhance_preview(p) -> dict:
    """强化框预览：当前 4 词条 -> 下一级 4 词条（实机那个「1级 ↗ 2级」框）。"""
    w = int(p.get("weapon", 0))
    cur, nxt = {}, {}
    for k, base in cfg.WEAPON["stats"].items():
        cur[k] = cfg.weapon_stat(base, w) if w < 9 else cfg.WEAPON["max_stats"][k]
        n2 = w + 1
        nxt[k] = cfg.weapon_stat(base, n2) if n2 < 9 else cfg.WEAPON["max_stats"][k]
    return {"cur": cur, "next": nxt, "cost": w + 1,
            "disp": cfg.weapon_display_level(w),
            "next_disp": cfg.weapon_display_level(w + 1)}


# ---------------------------------------------------------------------------
# 炼药  [实机丹方 + 提示语「失败也得药渣」「炼药师等级影响成功率」]
# ---------------------------------------------------------------------------
def craft_cd_left(p, now_ts=None) -> int:
    now_ts = time.time() if now_ts is None else now_ts
    left = int(cfg.CRAFT_CD_SEC - (now_ts - float(p.get("last_craft", 0))))
    return max(0, left)


def craft(p, name, rng=random.random, now_ts=None) -> dict:
    rec = cfg.RECIPE_BY_NAME.get(name)
    if not rec:
        return {"ok": False, "reason": "nodrug"}
    now_ts = time.time() if now_ts is None else now_ts
    cd = craft_cd_left(p, now_ts)
    if cd > 0:
        return {"ok": False, "reason": "cd", "left": cd}

    missing = []
    for mat, n in rec["need"].items():
        if count_item(p, mat) < n:
            missing.append(f"{mat}×{n}(有{count_item(p, mat)})")
    if missing:
        return {"ok": False, "reason": "mat", "missing": missing}

    for mat, n in rec["need"].items():
        take_item(p, mat, n)
    p["last_craft"] = now_ts

    rate = cfg.craft_rate(p.get("alch", 0))
    if rng() < rate:
        add_item(p, name, 1)
        p["alch_exp"] = int(p.get("alch_exp", 0)) + cfg.CRAFT_EXP
        up = _alch_level_up(p)
        return {"ok": True, "rate": rate, "alch_up": up}
    add_item(p, "药渣", cfg.CRAFT_FAIL_DROSS)
    p["alch_exp"] = int(p.get("alch_exp", 0)) + cfg.CRAFT_EXP // 2
    up = _alch_level_up(p)
    return {"ok": False, "reason": "fail", "rate": rate,
            "dross": cfg.CRAFT_FAIL_DROSS, "alch_up": up}


def _alch_level_up(p) -> str:
    idx = int(p.get("alch", cfg.ALCH_START))
    while idx < cfg.ALCH_MAX:
        need = cfg.ALCHEMIST[idx][1]
        if int(p.get("alch_exp", 0)) < need:
            break
        p["alch_exp"] = int(p["alch_exp"]) - need
        idx += 1
        p["alch"] = idx
    return cfg.ALCHEMIST[idx][0] if idx != int(p.get("alch", 0)) else ""


def use_item(p, name) -> dict:
    """服用丹药。"""
    rec = cfg.RECIPE_BY_NAME.get(name)
    if not rec:
        return {"ok": False, "reason": "nodrug"}
    if not take_item(p, name, 1):
        return {"ok": False, "reason": "none"}

    st = stats(p)
    eff, val = rec["effect"], int(rec["value"])
    if eff == "hp":
        before = int(p.get("hp", 0))
        p["hp"] = min(st["体力"], before + val)
        return {"ok": True, "msg": f"气血 +{p['hp'] - before}（{p['hp']}/{st['体力']}）"}
    if eff == "mp":
        mx = mp_max(p)
        before = int(p.get("mp", 0))
        p["mp"] = min(mx, before + val)
        return {"ok": True, "msg": f"斗气 +{p['mp'] - before}（{p['mp']}/{mx}）"}
    if eff == "exp":
        p["exp"] = int(p.get("exp", 0)) + val
        return {"ok": True, "msg": f"修为 +{val}"}
    if eff == "luck":
        p["luck"] = min(9999, int(p.get("luck", 0)) + val)
        return {"ok": True, "msg": f"机缘 +{val}（现 {p['luck']}）"}
    if eff == "dodge":
        b = p.setdefault("bonus", {})
        b["闪避"] = int(b.get("闪避", 0)) + val
        return {"ok": True, "msg": f"永久闪避 +{val}%"}
    if eff == "crit":
        b = p.setdefault("bonus", {})
        b["暴击"] = int(b.get("暴击", 0)) + val
        return {"ok": True, "msg": f"永久暴击 +{val}%"}
    if eff == "stat":
        b = p.setdefault("bonus", {})
        for k in ("力量", "敏捷", "斗气", "防御", "体力"):
            b[k] = int(b.get(k, 0)) + val
        _refill(p)
        return {"ok": True, "msg": f"全属性 +{val}"}
    if eff == "skill":
        return {"ok": False, "reason": "notuse",
                "msg": "焚血散不是口服之物，请在「升级 技能名」时使用"}
    return {"ok": False, "reason": "unknown"}


# ---------------------------------------------------------------------------
# 斗技升级
# ---------------------------------------------------------------------------
def skill_up(p, name) -> dict:
    # 允许上阵四槽里的任意斗技（不再限原 4 核心）；旧 4 核心始终兼容
    allowed = {e["name"] for e in skill_pool(p)} | set(cfg.SKILL_NAMES)
    if name not in allowed:
        return {"ok": False, "reason": "noskill"}
    cur = int((p.get("skills") or {}).get(name, 1))
    mx = int(cfg.SKILL_BY_NAME.get(name, {}).get("max", 5))
    if cur >= mx:
        return {"ok": False, "reason": "max", "lv": cur}
    cost = cfg.skill_up_cost(cur)
    if not take_item(p, cfg.SKILL_UP_ITEM, cost):
        return {"ok": False, "reason": "mat", "need": cost,
                "item": cfg.SKILL_UP_ITEM, "have": count_item(p, cfg.SKILL_UP_ITEM)}
    p.setdefault("skills", {})[name] = cur + 1
    return {"ok": True, "lv": cur + 1, "max": mx}


# ---------------------------------------------------------------------------
# 异火喂养
# ---------------------------------------------------------------------------
def feed_flame(p, name, amount=None) -> dict:
    fl = cfg.FLAME_BY_NAME.get(name)
    if not fl:
        return {"ok": False, "reason": "noflame"}
    if name == cfg.FLAME_DIYAN:
        return {"ok": False, "reason": "diyan"}
    cur = int((p.get("flames") or {}).get(name, 0))
    if cur >= cfg.FLAME_MAX_LV:
        return {"ok": False, "reason": "max", "lv": cur}
    need = int(fl["need"]) * (cur + 1)
    have = count_item(p, "药渣")
    if have <= 0:
        return {"ok": False, "reason": "nodross"}
    give = have if amount is None else min(int(amount), have)
    give = min(give, need)
    take_item(p, "药渣", give)
    done = give >= need
    if done:
        p.setdefault("flames", {})[name] = cur + 1
        _refill(p)
    return {"ok": True, "name": name, "give": give, "need": need,
            "left": max(0, need - give), "lv": cur + (1 if done else 0), "done": done}


# ---------------------------------------------------------------------------
# 商店
# ---------------------------------------------------------------------------
def shop_list() -> list:
    out = []
    for h in cfg.HERBS:
        out.append({"name": h["name"], "price": h["price"], "desc": h["desc"]})
    for s in cfg.SHOP_EXTRA:
        out.append({"name": s["name"], "price": s["price"], "desc": s["desc"]})
    return out


def shop_buy(p, name, n=1) -> dict:
    n = max(1, int(n))
    item = next((x for x in shop_list() if x["name"] == name), None)
    if not item:
        return {"ok": False, "reason": "noitem"}
    total = int(item["price"]) * n
    if int(p.get("gold", 0)) < total:
        return {"ok": False, "reason": "poor", "need": total, "have": int(p.get("gold", 0))}
    p["gold"] = int(p["gold"]) - total
    add_item(p, name, n)
    return {"ok": True, "name": name, "n": n, "total": total}


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
def current_main(p) -> dict | None:
    idx = int(p.get("main_idx", 0))
    if idx >= len(cfg.MAIN_TASKS):
        return None
    return cfg.MAIN_TASKS[idx]


def task_accept(p, rng=random.random) -> dict:
    if p.get("task"):
        return {"ok": False, "reason": "busy", "task": p["task"]}
    t = rng_choice(cfg.SIDE_TASK_POOL, rng)
    t = dict(t)
    t["progress"] = 0
    p["task"] = t
    return {"ok": True, "task": t}


def task_progress(p, kind, n=1) -> None:
    """打怪/采药/炼药/强化时推进支线。"""
    t = p.get("task")
    if not t or t.get("kind") != kind:
        return
    t["progress"] = int(t.get("progress", 0)) + int(n)


def task_submit(p) -> dict:
    t = p.get("task")
    if not t:
        return {"ok": False, "reason": "none"}
    if int(t.get("progress", 0)) < int(t.get("target", 1)):
        return {"ok": False, "reason": "unfinished", "task": t}
    rw = t["reward"]
    p["gold"] = int(p.get("gold", 0)) + int(rw.get("gold", 0))
    p["exp"] = int(p.get("exp", 0)) + int(rw.get("exp", 0))
    p["luck"] = min(9999, int(p.get("luck", 0)) + int(rw.get("luck", 0)))
    p["task"] = None
    return {"ok": True, "task": t, "reward": rw}


def main_progress(p, kind, n=1) -> None:
    t = current_main(p)
    if not t or t.get("kind") != kind:
        return
    p["main_progress"] = int(p.get("main_progress", 0)) + int(n)


def main_submit(p) -> dict:
    t = current_main(p)
    if not t:
        return {"ok": False, "reason": "done"}
    if int(p.get("main_progress", 0)) < int(t["target"]):
        return {"ok": False, "reason": "unfinished", "task": t,
                "progress": int(p.get("main_progress", 0))}
    rw = t["reward"]
    p["gold"] = int(p.get("gold", 0)) + int(rw.get("gold", 0))
    p["exp"] = int(p.get("exp", 0)) + int(rw.get("exp", 0))
    p["luck"] = min(9999, int(p.get("luck", 0)) + int(rw.get("luck", 0)))
    p["main_idx"] = int(p.get("main_idx", 0)) + 1
    p["main_progress"] = 0
    return {"ok": True, "task": t, "reward": rw, "next": current_main(p)}


# ---------------------------------------------------------------------------
# 任务途中的机遇（原版世界地图随机事件，文案见 cfg.CHANCE_EVENTS）
# ---------------------------------------------------------------------------
#: 可注入的随机源。验收脚本用 set_chance_rng(lambda: 1.0) 关掉随机，
#: 用 set_chance_rng(lambda: 0.0) 强制必触发，避免概率让测试变 flaky。
_chance_rng = random.random


def set_chance_rng(fn) -> None:
    """注入机遇随机源（仅测试/调试用）；传 None 恢复 random.random。"""
    global _chance_rng
    _chance_rng = fn or random.random


def _pick_chance(rng) -> dict:
    total = sum(max(0, int(e.get("weight", 1))) for e in cfg.CHANCE_EVENTS)
    pick = rng() * total
    acc = 0
    for e in cfg.CHANCE_EVENTS:
        acc += max(0, int(e.get("weight", 1)))
        if pick < acc:
            return e
    return cfg.CHANCE_EVENTS[-1]


def _apply_chance(p, ev: dict, rng) -> dict:
    """把一次机遇真正结算到玩家 dict，返回可展示的结果（不改存档 schema）。"""
    text = str(ev.get("text") or "")
    tone = str(ev.get("tone") or "neutral")

    # 竞猜：从原版 5 组问答里抽一题展示，奖励按原版 1000 金币
    quiz = ev.get("quiz")
    if quiz:
        text = "《斗破苍穹》有奖竞猜！" + str(rng_choice(quiz, rng)) + " 答案正确，获得1000金币奖励。"

    # 条件：需要交出丹药。原版「丹不够，老者悻悻走开」是真实分支，不扣不奖。
    need = ev.get("need") or {}
    for name, n in need.items():
        if count_item(p, name) < int(n):
            return {"name": ev["name"], "text": str(ev.get("missing_text") or text),
                    "tone": "neutral", "gain": "一无所获", "src": ev.get("src", ""),
                    "reward": {}}
    # 条件：破财事件金币不足 → 原版「匪头嫌你穷，踹走」分支，零损失
    guard = int(ev.get("guard_gold") or 0)
    if guard > 0 and int(p.get("gold", 0)) < guard:
        return {"name": ev["name"], "text": str(ev.get("poor_text") or text),
                "tone": "neutral", "gain": "毫发无伤", "src": ev.get("src", ""),
                "reward": {}}

    for name, n in need.items():
        take_item(p, name, int(n))

    rw = ev.get("reward") or {}
    parts: list[str] = []
    gold = int(rw.get("gold") or 0)
    if gold:
        p["gold"] = max(0, int(p.get("gold", 0)) + gold)
        parts.append("金币 %+d" % gold)
    exp = int(rw.get("exp") or 0)
    if exp:
        p["exp"] = int(p.get("exp", 0)) + exp
        parts.append("修为 %+d" % exp)
    luck = int(rw.get("luck") or 0)
    if luck:
        p["luck"] = min(9999, max(0, int(p.get("luck", 0)) + luck))
        parts.append("机缘 %+d" % luck)
    for name, n in (rw.get("item") or {}).items():
        if int(n) > 0:
            add_item(p, name, int(n))
            parts.append("%s ×%d" % (name, int(n)))
    if need:
        parts.append("交出 " + "、".join("%s ×%d" % (k, int(v)) for k, v in need.items()))
    return {"name": ev["name"], "text": text, "tone": tone,
            "gain": " · ".join(parts) if parts else "无事发生",
            "src": ev.get("src", ""), "reward": rw}


def roll_chance(p, rng=None) -> dict | None:
    """掷一次机遇；命中则**直接结算**奖励到 p 并返回展示数据。

    只在「做任务途中」调用（见 advance_task），没任务时不触发。
    """
    if not cfg.CHANCE_EVENTS:
        return None
    r = rng or _chance_rng
    if r() >= float(cfg.CHANCE_RATE):
        return None
    return _apply_chance(p, _pick_chance(r), r)


def advance_task(p, kind, n: int = 1, rng=None) -> dict | None:
    """推进任务进度，并在任务途中按概率触发一次机遇。

    :param kind: 单个 kind 字符串；或 {kind: n} 一次推进多种（一次动作只掷一次机遇）。
    :return: 命中的机遇 dict；没接支线且主线已通关 / 未命中则 None。

    注意：core.task_progress() / main_progress() 保持纯进度语义不变（测试直接调它们），
    机遇只在命令层走这个统一入口，避免一次动作被掷两次。
    """
    if isinstance(kind, dict):
        for k, v in kind.items():
            task_progress(p, k, v)
            main_progress(p, k, v)
    else:
        task_progress(p, kind, n)
        main_progress(p, kind, n)
    if not (p.get("task") or current_main(p)):
        return None
    return roll_chance(p, rng)


# ---------------------------------------------------------------------------
# 战力榜
# ---------------------------------------------------------------------------
def rank(players) -> list:
    ps = sorted(players, key=power, reverse=True)
    return ps
