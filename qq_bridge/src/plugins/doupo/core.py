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


# ---------------------------------------------------------------------------
# 等级成长  [源码 ah.java:165]
# ---------------------------------------------------------------------------
def c_stat(base: int, lv: int) -> int:
    """原版成长公式：base*10 + 阶²*base*10 + base*lv，阶=(lv-1)/9。

    实机校验（形象 0 / 玄重尺 +0 / 等级 1）：
      体力 880  力量 275  斗气 165  敏捷 220  防御 132
    与人物面板显示逐格吻合。
    """
    k = (max(1, int(lv)) - 1) // 9
    return int(base) * 10 + k * k * int(base) * 10 + int(base) * int(lv)


# ---------------------------------------------------------------------------
# 角色创建
# ---------------------------------------------------------------------------
def create_player(qq, name, group) -> dict:
    now = time.time()
    st = {
        "qq": str(qq), "name": name, "group": str(group),
        "lv": 1, "exp": 0,
        "gold": cfg.START_GOLD, "luck": cfg.START_LUCK,
        "weapon": 0,
        "hp": 0, "mp": 0,
        "skills": {n: 1 for n in cfg.SKILL_NAMES},
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
    """升一级。28 级 -> 29 级（斗帝）走机缘判定。"""
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
    """按玩家等级挑选一个"够得着但不算白给"的怪（等级差 ≤2）。"""
    lv = int(p.get("lv", 1))
    cand = [m for m in cfg.MONSTERS if abs(m["lv"] - lv) <= 2] or cfg.MONSTERS[:1]
    return rng_choice(cand, rng)


def rng_choice(seq, rng=random.random):
    if not seq:
        return None
    return seq[min(len(seq) - 1, int(rng() * len(seq)))]


def damage_schedule(p, rounds: int = 15) -> list:
    """逐回合的期望输出（已算暴击期望，未乘减伤）。

    为什么不用"平均值 × 回合数"：玩家的蓝只够放 5 次斗技（cost = mp_max//5），
    第 6 回合起只能普攻，单回合输出掉到 42%。若按 4 回合均值外推，
    高等级的怪血量会被高估一倍，实测表现为"22 级要 11 回合才杀得死"，
    而目标是 4~5 回合 —— 这正是之前 22/26 级胜率 20% 的根因。
    """
    st = stats(p)
    best = _best_skill(p)
    sk_lv = int((p.get("skills") or {}).get(best, 1))
    skill_raw = (st["力量"] * 0.5 + st["斗气"] * 1.5) * cfg.skill_dmg_mult(best, sk_lv)
    normal_raw = st["力量"] * 1.0

    pool = mp_max(p)                     # 每场战斗蓝是满的（_refill / 升级回满）
    cost = max(1, mp_max(p) // 5)
    out = []
    for _ in range(max(1, rounds)):
        if pool >= cost:
            out.append(skill_raw)
            pool -= cost
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
    delta = max(-0.6, min(0.8, (int(mon["lv"]) - lv) * cfg.LV_DELTA_STEP))
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


def _player_hit(p, target_def, use_skill, rng) -> dict:
    st = stats(p)
    crit = rng() * 100 < crit_rate(p)
    if use_skill:
        name = _best_skill(p)
        lv = int((p.get("skills") or {}).get(name, 1))
        raw = (st["力量"] * 0.5 + st["斗气"] * 1.5) * cfg.skill_dmg_mult(name, lv)
        kind = name
    else:
        raw = st["力量"] * 1.0
        kind = "普攻"
    dmg = max(1, int(raw * mitigation(target_def) * (1.5 if crit else 1.0)))
    return {"dmg": dmg, "crit": crit, "kind": kind, "skill": use_skill}


def _best_skill(p) -> str:
    """选当前等级最高（同等级取倍率最高）的技能。"""
    sk = p.get("skills") or {}
    best, best_score = cfg.SKILL_NAMES[0], -1
    for n in cfg.SKILL_NAMES:
        lv = int(sk.get(n, 1))
        score = lv * 10 + cfg.SKILL_BY_NAME[n]["dmg_mult"]
        if score > best_score:
            best, best_score = n, score
    return best


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
    cost = max(1, mp_max(p) // 5)

    rounds, dmg_log, taken = 0, [], 0
    dodge = dodge_rate(p)
    lifesteal = 0.02 * int((p.get("flames") or {}).get("三千焱炎火", 0))
    var = max(0.0, float(cfg.MON_DMG_VAR))

    while rounds < 15 and m_hp > 0 and hp > 0:
        rounds += 1
        use_skill = mp >= cost
        hit = _player_hit(p, m_def, use_skill, rng)
        if use_skill:
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
                "msg": "焚血散不是口服之物，请在「dp升级 技能名」时使用"}
    return {"ok": False, "reason": "unknown"}


# ---------------------------------------------------------------------------
# 斗技升级
# ---------------------------------------------------------------------------
def skill_up(p, name) -> dict:
    if name not in cfg.SKILL_BY_NAME:
        return {"ok": False, "reason": "noskill"}
    cur = int((p.get("skills") or {}).get(name, 1))
    mx = cfg.SKILL_BY_NAME[name]["max"]
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
# 战力榜
# ---------------------------------------------------------------------------
def rank(players) -> list:
    ps = sorted(players, key=power, reverse=True)
    return ps
