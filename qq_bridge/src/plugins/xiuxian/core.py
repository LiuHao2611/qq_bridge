# -*- coding: utf-8 -*-
"""修仙文字游戏 · 核心逻辑（纯函数，零 nonebot 依赖，可单测）。

所有随机用可注入的 rng(返回 0..1) 参数，便于测试可复现。
所有修改都是"就地改 dict"——调用方负责持久化（store.put_player）。
"""

from __future__ import annotations

import random
import time

from . import config as cfg


# ---------------------------------------------------------------------------
# 基础：灵根 / 创角
# ---------------------------------------------------------------------------
def _weighted_choice(items, rng):
    total = sum(w for *_, w in items)
    r = rng() * total
    for name, mult, w in items:
        r -= w
        if r <= 0:
            return name, mult
    name, mult, _ = items[-1]
    return name, mult


def create_player(qq, name, group, rng=random.random):
    root_name, root_mult = _weighted_choice(cfg.ROOT_WEIGHTS, rng)
    now = time.time()
    return {
        "qq": str(qq), "name": name, "group": str(group),
        "root": root_name, "root_mult": root_mult,
        "realm": 0, "exp": 0, "ling_shi": cfg.START_LINGSHI,
        "age": 16, "life": cfg.REALMS[0]["life_gain"],
        "atk": 10, "def": 8, "crit": 0.05, "crit_dmg": 1.5,
        "last_settle": now, "secluding": False,
        "level_up_cd": 0, "bt_bonus": 0.0,   # 破镜CD到期时间戳 / 失败累积成功率加成
        "sect": None, "wins": 0, "losses": 0,
        "claim_streak": 0, "last_claim": 0,
        "last_explore": 0,        # 秘境探索冷却时间戳（防无限刷灵石）
        "bounty": None,   # 进行中的悬赏 {cat,name,rate,minutes,thank,fail_thank,end,...}
        # 炼丹 / 用丹（搬 Sam5440 炼丹丹药.json）
        "furnace": None,        # 拥有的炼丹炉名（None=无）
        "elixir_used": {},      # 各丹药累计使用次数 {name: n}（all_num 上限）
        "elixir_day": {},       # 每日使用次数 {"2026-10-26": {name: n}}（day_num 上限）
        "mixing": None,         # 炼丹进行中 {"name","end","buff"}
        "bt_big": 0.0,          # 待消耗的 level_up_big 突破率加成(%)，下次突破时吃掉
        "inv": {},
    }


# ---------------------------------------------------------------------------
# 修炼 / 闭关结算（抄 Sam5440/nonebot_plugin_xiuxian 的 closing_exp 机制）
# ---------------------------------------------------------------------------
def breakthrough_need(p) -> int:
    """突破到下一境界所需的修为 = **下一境界的 power 字段**。

    出处：xiuxian2_handle.set_closing_type / OtherSet.get_type 里
        need_exp = XiuxianDateManage().get_level_power(is_updata_level)
    而 get_level_power() 读的是 境界.json 的 `power`（不是 `exp`）。
    两者只在「结丹境初期」不同（power 30000 / exp 20000），照源用 power。
    """
    if p["realm"] >= cfg.MAX_REALM:
        return 0
    return int(cfg.REALMS[p["realm"] + 1]["need_exp"])


def seclude_cap(p) -> int:
    """闭关期间修为上限 = 下一境界需求 × 1.5（开源的 closing_exp_upper_limit）。

    存在的理由：不加这个上限，玩家挂一夜能囤出好几级的修为，一上线连着突破 ——
    正是 2026-10-26 用户报的"突破完又满了，一直突破，还修行啥"。
    """
    if p["realm"] >= cfg.MAX_REALM:
        return int(p["exp"])
    return int(breakthrough_need(p) * cfg.SECLUDE_CAP_RATIO)


def seclude_rate(p) -> float:
    """闭关每分钟修为 = 开源 closing_exp × 灵根倍率（灵根本项目原设定就影响产出）。"""
    return cfg.SECLUDE_EXP_PER_MIN * float(p.get("root_mult") or 1.0)


def settle(p, now_ts=None, rng=random.random):
    """闭关结算：**只有闭关中才产出修为**，每分钟 closing_exp 点。

    旧实现按真实时间无条件产出（离线、挂机都涨），导致"刚突破完又满了"；
    开源项目是「开始闭关 → 每分钟结算 → 出关结算」，不闭关就一点不涨。
    寿元仍只随主动突破增减，不因离线流逝（沿用本项目原设定）。
    """
    now_ts = time.time() if now_ts is None else now_ts
    elapsed = max(0.0, now_ts - p["last_settle"])
    p["last_settle"] = now_ts
    if not p.get("secluding"):
        return {"gain": 0, "died": False}
    gain = int(elapsed / 60.0 * seclude_rate(p))
    if gain <= 0:
        return {"gain": 0, "died": False}
    cap = seclude_cap(p)
    before = p["exp"]
    p["exp"] = min(cap, before + gain)
    return {"gain": p["exp"] - before, "died": False, "capped": p["exp"] >= cap}


def start_seclude(p, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    settle(p, now_ts)
    p["secluding"] = True


def stop_seclude(p, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    res = settle(p, now_ts)
    p["secluding"] = False
    return res


# ---------------------------------------------------------------------------
# 突破（概率 + 破镜CD + 失败软损；机制抄 xiuxian_2）
# ---------------------------------------------------------------------------
def can_breakthrough(p):
    return p["realm"] < cfg.MAX_REALM and p["exp"] >= breakthrough_need(p)


def bt_rate(p) -> float:
    """当前突破成功率（%）= 下一境界基础率 × (1 + 失败累积加成)，封顶 100。"""
    if p["realm"] >= cfg.MAX_REALM:
        return 0.0
    base = float(cfg.REALMS[p["realm"] + 1]["rate"])
    return min(cfg.BT_RATE_CAP, base * (1 + float(p.get("bt_bonus") or 0.0)))


def bt_rate_effective(p) -> float:
    """实际突破成功率（%，供状态卡显示）：在 bt_rate 基础上叠加丹药(bt_big)加成，封顶 100。"""
    if p["realm"] >= cfg.MAX_REALM:
        return 0.0
    return min(cfg.BT_RATE_CAP, bt_rate(p) + float(p.get("bt_big") or 0.0))


def bt_cd_left(p, now_ts=None) -> int:
    """破镜 CD 剩余秒数（开源 level_up_cd = 60 分钟）。"""
    now_ts = time.time() if now_ts is None else now_ts
    return max(0, int(float(p.get("level_up_cd") or 0) - now_ts))


def do_breakthrough(p, now_ts=None, rng=random.random):
    now_ts = time.time() if now_ts is None else now_ts
    if not can_breakthrough(p):
        return {"ok": False, "reason": "exp"}
    left = bt_cd_left(p, now_ts)
    if left > 0:
        return {"ok": False, "reason": "cd", "remain": left}
    rate = bt_rate(p) + float(p.get("bt_big") or 0.0)   # 叠加丹药(level_up_big)加成
    rate = min(cfg.BT_RATE_CAP, rate)
    if rng() * 100 < rate:
        need = breakthrough_need(p)
        p["exp"] = max(0, p["exp"] - need)
        p["realm"] += 1
        p["bt_bonus"] = 0.0
        p["bt_big"] = 0.0          # 消耗丹药加成（无论成败）
        p["level_up_cd"] = now_ts + cfg.BT_CD_SEC
        nxt = cfg.REALMS[p["realm"]]
        p["life"] += nxt["life_gain"]
        p["atk"] += int(10 * (p["realm"] + 1) * p["root_mult"])
        p["def"] += int(8 * (p["realm"] + 1) * p["root_mult"])
        p["crit"] = min(0.5, p["crit"] + 0.01)
        return {"ok": True, "realm": nxt["name"], "life_gain": nxt["life_gain"],
                "cd": cfg.BT_CD_SEC, "rate": rate}
    # 失败：按开源 level_punishment_floor/limit 扣 1%~5% 修为，成功率按 1.3 倍累积
    pct = random.uniform(cfg.BT_PENALTY_MIN, cfg.BT_PENALTY_MAX)
    p["exp"] = int(p["exp"] * (1 - pct))
    p["life"] -= cfg.BT_LIFE_COST_ON_FAIL
    p["bt_bonus"] = float(p.get("bt_bonus") or 0.0) + cfg.BT_RATE_BONUS
    p["bt_big"] = 0.0            # 消耗丹药加成（无论成败）
    return {"ok": False, "reason": "fail", "penalty": round(pct, 4),
            "rate": rate, "next_rate": bt_rate(p)}


# ---------------------------------------------------------------------------
# 战力 & 战斗
# ---------------------------------------------------------------------------
def power(p):
    base = cfg.REALMS[p["realm"]]["power_base"]
    return int(base + p["exp"] * cfg.POWER_PER_EXP + p["atk"] * 2 + p["def"] * 1.5)


def crit_mult(p) -> float:
    """暴击的期望伤害倍率 = 1 + 暴击率 × (暴击伤害 − 1)。

    ⚠️ 历史坑（2026-10-26 排查）：`crit` / `crit_dmg` 创建时就有值、每次突破 `crit += 1%`，
    但**全工程从未被读取**——涨到 50% 也一点用没有，是条纯粹的假成长线。
    这里把它折成"期望战力加成"接进 `_fight`：暴击高 → 胜率更高，
    同时保持胜负仍是**单次掷骰**，不破坏既有胜率曲线与可复现测试。
    """
    crit = min(1.0, max(0.0, float(p.get("crit") or 0.0)))
    dmg = max(1.0, float(p.get("crit_dmg") or 1.5))
    return 1.0 + crit * (dmg - 1.0)


def _fight(att, vic, rng):
    pa = power(att) * crit_mult(att)
    pd = power(vic) * crit_mult(vic)
    p_win = pa / (pa + pd) if (pa + pd) > 0 else 0.5
    return rng() < p_win, p_win


def pvp(att, vic, rng=random.random):
    att_wins, p_win = _fight(att, vic, rng)
    res = {"win": att_wins, "p_win": round(p_win, 3),
           "atk_pow": power(att), "def_pow": power(vic)}
    if att_wins:
        att["wins"] += 1
        vic["losses"] += 1
        loss = min(int(vic["ling_shi"] * cfg.PVP_LOSE_LINGSHI_PCT), cfg.STEAL_CAP)
        vic["ling_shi"] -= loss
        att["ling_shi"] += loss
        res["ling_shi"] = loss
    else:
        att["losses"] += 1
        vic["wins"] += 1
        loss = min(int(att["ling_shi"] * cfg.PVP_LOSE_LINGSHI_PCT), cfg.STEAL_CAP)
        att["ling_shi"] -= loss
        vic["ling_shi"] += loss
        res["ling_shi"] = loss
    return res


def rob(att, vic, rng=random.random):
    att_wins, _ = _fight(att, vic, rng)
    if att_wins:
        amt = min(int(vic["ling_shi"] * cfg.ROB_PCT), cfg.ROB_CAP)
        amt = max(0, amt)
        vic["ling_shi"] -= amt
        att["ling_shi"] += amt
        return {"win": True, "amt": amt}
    amt = min(int(att["ling_shi"] * cfg.ROB_PCT), cfg.ROB_CAP)
    att["ling_shi"] -= amt
    vic["ling_shi"] += amt
    return {"win": False, "amt": amt}


def steal(att, vic, rng=random.random):
    att_wins, _ = _fight(att, vic, rng)
    success = att_wins and rng() < 0.9
    if success:
        amt = min(int(vic["ling_shi"] * cfg.STEAL_PCT), cfg.STEAL_CAP)
        vic["ling_shi"] -= amt
        att["ling_shi"] += amt
        return {"win": True, "amt": amt}
    amt = min(int(att["ling_shi"] * 0.03), cfg.STEAL_CAP)
    att["ling_shi"] -= amt
    return {"win": False, "amt": amt}


def give(frm, to, amount):
    amount = int(amount)
    if amount <= 0:
        return {"ok": False, "reason": "amount"}
    if frm["ling_shi"] < amount:
        return {"ok": False, "reason": "poor"}
    frm["ling_shi"] -= amount
    to["ling_shi"] += amount
    return {"ok": True, "amt": amount}


# ---------------------------------------------------------------------------
# 领取机缘（可选、随机惊喜、不卡进度——去"上班感"）
# ---------------------------------------------------------------------------
def claim(p, now_ts=None, rng=random.random):
    now_ts = time.time() if now_ts is None else now_ts
    if now_ts - p["last_claim"] < cfg.CLAIM_COOLDOWN:
        return {"ok": False, "reason": "cooldown",
                "remain": int(cfg.CLAIM_COOLDOWN - (now_ts - p["last_claim"]))}
    amt = random.randint(cfg.CLAIM_MIN, cfg.CLAIM_MAX)
    if now_ts - p["last_claim"] < cfg.CLAIM_COOLDOWN * 2 and p["last_claim"] > 0:
        p["claim_streak"] += 1
    else:
        p["claim_streak"] = 1
    bonus = min(cfg.CLAIM_STREAK_CAP, (p["claim_streak"] - 1) * cfg.CLAIM_STREAK_BONUS)
    amt = int(amt * (1 + bonus))
    p["ling_shi"] += amt
    p["last_claim"] = now_ts
    return {"ok": True, "amt": amt, "streak": p["claim_streak"], "bonus": round(bonus, 2)}


# ---------------------------------------------------------------------------
# 悬赏任务（搬 Sam5440 的 data/xiuxian/悬赏令.json：接取 → 计时 → 判定 → 灵石）
#
# 源项目语义：一次只能接 1 个任务，任务有固定耗时（60/120/180 分钟），
# 到期后按任务自己的 rate 掷骰，成功拿 succeed_thank、失败拿 fail_thank（都是灵石）。
# 没有境界门槛，唯一的成本是时间 —— 这也是它的设计：时间就是代价。
# ---------------------------------------------------------------------------
def bounty_list():
    """全部可接任务（源的 16 条，无境界门槛）。"""
    return cfg.BOUNTIES


def bounty_info(p, now_ts=None):
    """当前任务状态：None=没接；否则带剩余秒数。"""
    now_ts = time.time() if now_ts is None else now_ts
    b = p.get("bounty")
    if not b:
        return None
    b = dict(b)
    b["remain"] = max(0, int(b["end"] - now_ts))
    b["ready"] = b["remain"] <= 0
    return b


def start_bounty(p, name, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    if p.get("bounty"):
        return {"ok": False, "reason": "busy", "info": bounty_info(p, now_ts)}
    row = cfg.find_bounty(name)
    if not row:
        return {"ok": False, "reason": "noent"}
    cat, bname, rate, minutes, thank, fail_thank, s_msg, f_msg = row
    p["bounty"] = {
        "cat": cat, "name": bname, "rate": rate, "minutes": minutes,
        "thank": thank, "fail_thank": fail_thank,
        "s_msg": s_msg, "f_msg": f_msg,
        "end": now_ts + minutes * 60,
    }
    return {"ok": True, "name": bname, "cat": cat, "minutes": minutes,
            "rate": rate, "thank": thank}


def finish_bounty(p, now_ts=None, rng=random.random):
    now_ts = time.time() if now_ts is None else now_ts
    b = p.get("bounty")
    if not b:
        return {"ok": False, "reason": "none"}
    left = int(b["end"] - now_ts)
    if left > 0:
        return {"ok": False, "reason": "wait", "remain": left, "name": b["name"]}
    win = rng() * 100 < b["rate"]
    amt = int(b["thank"] if win else b["fail_thank"])
    p["ling_shi"] += amt
    p["bounty"] = None
    return {"ok": True, "win": win, "amt": amt, "name": b["name"],
            "cat": b["cat"], "rate": b["rate"],
            "msg": b["s_msg"] if win else b["f_msg"], "ling_shi": p["ling_shi"]}


# ---------------------------------------------------------------------------
# 炼丹丹药（搬 Sam5440 的 data/xiuxian/丹药/炼丹丹药.json）
#
# 源机制忠实还原：
#   * 每种丹药有 境界门槛（realm）、每日上限 day_num、累计上限 all_num、炼制耗时 mix_need_time
#   * 炼制需「炼丹炉 + 配方药材」；配方 elixir_config 按药材「主药 type」匹配（不是名字）
#   * 三类效果：exp_up 加修为 / level_up_big 加下次突破率 / atk_buff 永久加攻击
# 源仓药材/丹炉无价格（靠灵田种植），价格是我们按品级透明换算的唯一适配（见 config_open）。
# ---------------------------------------------------------------------------
def _realm_gate_ok(p, pill) -> bool:
    ri = cfg.realm_index_of(pill["realm"])
    return ri >= 0 and p["realm"] >= ri


def mix_elixir_start(p, pill_name, now_ts=None):
    """开炉炼制：检查炉/药材/境界，消耗药材并启动 60 分钟计时。"""
    now_ts = time.time() if now_ts is None else now_ts
    if p.get("mixing"):
        left = max(0, int(p["mixing"]["end"] - now_ts))
        return {"ok": False, "reason": "busy", "name": p["mixing"]["name"], "remain": left}
    pill = cfg.find_elixir(pill_name)
    if not pill:
        return {"ok": False, "reason": "noent"}
    if not _realm_gate_ok(p, pill):
        return {"ok": False, "reason": "realm", "need": pill["realm"]}
    if not p.get("furnace"):
        return {"ok": False, "reason": "nofurnace"}
    # 检查并消耗配方药材（按主药 type）
    lack = []
    for t, cnt in pill["recipe"].items():
        have = p["inv"].get(cfg.HERB_INV_KEY(t), 0)
        if have < cnt:
            lack.append((t, cnt - have))
    if lack:
        return {"ok": False, "reason": "herb", "lack": lack}
    for t, cnt in pill["recipe"].items():
        k = cfg.HERB_INV_KEY(t)
        p["inv"][k] = p["inv"].get(k, 0) - cnt
        if p["inv"][k] <= 0:
            p["inv"].pop(k, None)
    buff = 0
    for f in cfg.FURNACES:
        if f["name"] == p["furnace"]:
            buff = f["buff"]
            break
    p["mixing"] = {"name": pill["name"], "end": now_ts + pill["mix_need_time"] * 60,
                   "buff": buff}
    return {"ok": True, "name": pill["name"], "minutes": pill["mix_need_time"], "buff": buff}


def mix_elixir_collect(p, now_ts=None):
    """领取炼成的丹药（丹炉 buff 让产出 +0/+1/+2 枚）。"""
    now_ts = time.time() if now_ts is None else now_ts
    m = p.get("mixing")
    if not m:
        return {"ok": False, "reason": "none"}
    left = int(m["end"] - now_ts)
    if left > 0:
        return {"ok": False, "reason": "wait", "remain": left, "name": m["name"]}
    cnt = 1 + int(m.get("buff") or 0)
    p["inv"][m["name"]] = p["inv"].get(m["name"], 0) + cnt
    name = m["name"]
    p["mixing"] = None
    return {"ok": True, "name": name, "count": cnt}


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def use_elixir(p, pill_name, now_ts=None):
    """服用一枚丹药，按类型施加效果并记使用次数（每日/累计上限 + 境界门槛）。"""
    now_ts = time.time() if now_ts is None else now_ts
    pill = cfg.find_elixir(pill_name)
    if not pill:
        return {"ok": False, "reason": "noent"}
    if p["inv"].get(pill["name"], 0) <= 0:
        return {"ok": False, "reason": "none"}
    if not _realm_gate_ok(p, pill):
        return {"ok": False, "reason": "realm", "need": pill["realm"]}
    day = _today()
    used_day = p["elixir_day"].get(day, {}).get(pill["name"], 0)
    if used_day >= pill["day_num"]:
        return {"ok": False, "reason": "daylimit", "day_num": pill["day_num"]}
    used_all = p["elixir_used"].get(pill["name"], 0)
    if used_all >= pill["all_num"]:
        return {"ok": False, "reason": "alllimit", "all_num": pill["all_num"]}
    bt = pill["buff_type"]
    if bt == "exp_up":
        p["exp"] += pill["buff"]
        effect = f"修为 +{pill['buff']}"
    elif bt == "atk_buff":
        p["atk"] += pill["buff"]
        effect = f"攻击 +{pill['buff']}"
    elif bt == "level_up_big":
        p["bt_big"] = min(100.0, float(p.get("bt_big") or 0.0) + pill["buff"])
        effect = f"下次突破成功率 +{pill['buff']}%"
    else:
        return {"ok": False, "reason": "unknown", "bt": bt}
    # 记账
    p["inv"][pill["name"]] -= 1
    if p["inv"][pill["name"]] <= 0:
        p["inv"].pop(pill["name"], None)
    p["elixir_used"][pill["name"]] = used_all + 1
    p["elixir_day"].setdefault(day, {})[pill["name"]] = used_day + 1
    return {"ok": True, "name": pill["name"], "bt": bt, "effect": effect, "buff": pill["buff"]}


def buy_shop_item(p, key):
    """从商店购买一件：扣灵石，炉入 furnace、药材入 inv。"""
    item = next((it for it in cfg.SHOP_ITEMS if it["key"] == key), None)
    if not item:
        return {"ok": False, "reason": "noent"}
    if p["ling_shi"] < item["price"]:
        return {"ok": False, "reason": "poor", "price": item["price"]}
    p["ling_shi"] -= item["price"]
    if item["cat"] == "炼丹炉":
        p["furnace"] = item["name"].replace("炼丹炉·", "")
    else:
        t = int(item["key"].split(":")[1])
        p["inv"][cfg.HERB_INV_KEY(t)] = p["inv"].get(cfg.HERB_INV_KEY(t), 0) + 1
    return {"ok": True, "name": item["name"], "price": item["price"], "cat": item["cat"]}


# ---------------------------------------------------------------------------
# 秘境探索（Roguelike-lite 随机节点）
# ---------------------------------------------------------------------------
def explore(p, rng=random.random):
    node = random.choice(cfg.SECRET_NODES)
    res = {"node": node}
    if node == "灵石矿":
        amt = random.randint(30, 200)
        p["ling_shi"] += amt
        res["ling_shi"] = amt
    elif node == "灵药":
        exp = random.randint(50, 400)
        p["exp"] += exp
        res["exp"] = exp
    elif node == "妖兽":
        win = random.random() < 0.6
        if win:
            exp = random.randint(100, 600)
            p["exp"] += exp
            res["exp"] = exp
            res["win"] = True
        else:
            loss = min(int(p["ling_shi"] * 0.05), cfg.STEAL_CAP)
            p["ling_shi"] -= loss
            res["ling_shi"] = -loss
            res["win"] = False
    elif node == "残破古迹":
        item = random.choice(["残破玉简", "古修遗骸", "神秘碎片"])
        p["inv"][item] = p["inv"].get(item, 0) + 1
        res["item"] = item
    elif node == "奇遇":
        res["qiyu"] = random.choice(cfg.QIYU_POOL)
        exp = random.randint(80, 300)
        p["exp"] += exp
        res["exp"] = exp
    else:
        res["empty"] = True
    return res


def explore_cd_left(p, now_ts=None) -> int:
    """距下次可探索的剩余秒数（0 = 可探索）。防 #秘境 无限刷灵石/修为。

    2026-10-26 修复：原 cmd_explore 直接调用 explore 且**无冷却**，玩家可无限刷灵石；
    后台「秘境开启 限时30分钟」广播只写 world["secret_open"] 从不读取，形同虚设。
    现改为「每人每次探索后冷却 EXPLORE_CD_SEC」，与广播口径一致。
    """
    now_ts = time.time() if now_ts is None else now_ts
    last = p.get("last_explore", 0) or 0
    left = int(cfg.EXPLORE_CD_SEC - (now_ts - last))
    return max(0, left)


# ---------------------------------------------------------------------------
# 限时秘境 · 自动探索流程（报名即自动推进，事件按倒计时随机触发）
#
# 设计（2026-10-26 改版，满足用户诉求）：
#   * 限时秘境开启后，玩家发一次「秘境」= 报名入内，后台自动开始探索；
#   * 无需反复发口令触发事件——事件由后台 _secret_tick 按倒计时随机触发；
#   * 期间再发「秘境」只查看当前进度（不触发事件、不抽奖）；
#   * 倒计时结束由后台自动关闭并播报总览（每人收获/事件数）。
# ---------------------------------------------------------------------------
def secret_new(now_ts=None) -> dict:
    """新建一个限时秘境世界状态。"""
    now_ts = time.time() if now_ts is None else now_ts
    return {
        "open": True,
        "started_at": now_ts,
        "ends_at": now_ts + cfg.SECRET_DURATION_SEC,
        "participants": {},
    }


def secret_is_open(secret, now_ts=None) -> bool:
    if not secret:
        return False
    now_ts = time.time() if now_ts is None else now_ts
    try:
        ends_at = float(secret.get("ends_at", 0) or 0)
    except (TypeError, ValueError):
        ends_at = 0.0
    return bool(secret.get("open")) and now_ts < ends_at


def secret_join(secret, p, now_ts=None) -> dict:
    """报名/开始自动探索。已在册则原样返回，不重复计数。"""
    now_ts = time.time() if now_ts is None else now_ts
    parts = secret.setdefault("participants", {})
    qq = str(p["qq"])
    if qq in parts:
        return parts[qq]
    part = {
        "qq": qq,
        "name": p.get("name") or qq,
        "joined_at": now_ts,
        "event_count": 0,
        "gain_ling_shi": 0,   # 累计"获得"灵石（只累计正收益，遇袭损失不计入）
        "gain_exp": 0,
        "items": {},
        "log": [],
        "next_event_at": now_ts + random.uniform(
            cfg.SECRET_EVENT_INTERVAL_MIN, cfg.SECRET_EVENT_INTERVAL_MAX
        ),
    }
    parts[qq] = part
    return part


def _secret_event_text(r) -> str:
    node = r.get("node")
    if node == "灵石矿":
        return f"💎 灵石矿：采得 {r.get('ling_shi', 0)} 灵石"
    if node == "灵药":
        return f"🌿 灵药：炼化得 {r.get('exp', 0)} 修为"
    if node == "妖兽":
        if r.get("win"):
            return f"⚔️ 斩妖夺丹：得 {r.get('exp', 0)} 修为"
        return f"⚔️ 遇妖兽不敌：损 {abs(int(r.get('ling_shi', 0) or 0))} 灵石"
    if node == "残破古迹":
        return f"🏚️ 残破古迹：拾得【{r.get('item')}】×1"
    if node == "奇遇":
        return f"✨ 奇遇：{r.get('qiyu')}（修为 +{r.get('exp', 0)}）"
    return "🕳️ 一无所获"


def secret_roll_event(p, part, now_ts=None) -> dict:
    """为某位探索者触发一次随机事件：真实改动玩家属性 + 记录到进度日志。"""
    now_ts = time.time() if now_ts is None else now_ts
    r = explore(p)                      # 复用节点逻辑，直接改 p 的灵石/修为/背包
    dls = int(r.get("ling_shi", 0) or 0)
    dexp = int(r.get("exp", 0) or 0)
    item = r.get("item")
    part["event_count"] = int(part.get("event_count", 0)) + 1
    part["gain_ling_shi"] = int(part.get("gain_ling_shi", 0)) + max(0, dls)
    part["gain_exp"] = int(part.get("gain_exp", 0)) + max(0, dexp)
    if item:
        items = part.setdefault("items", {})
        items[item] = items.get(item, 0) + 1
    text = _secret_event_text(r)
    entry = {"t": now_ts, "node": r.get("node"), "text": text,
             "ling_shi": dls, "exp": dexp}
    logs = part.setdefault("log", [])
    logs.append(entry)
    if len(logs) > 30:
        del logs[:-30]
    part["next_event_at"] = now_ts + random.uniform(
        cfg.SECRET_EVENT_INTERVAL_MIN, cfg.SECRET_EVENT_INTERVAL_MAX
    )
    return entry


def secret_progress_text(secret, p, now_ts=None):
    """已报名者的进度卡；未报名返回 None（由调用方决定如何提示）。"""
    now_ts = time.time() if now_ts is None else now_ts
    part = (secret.get("participants") or {}).get(str(p["qq"]))
    if not part:
        return None
    try:
        ends_at = float(secret.get("ends_at", now_ts) or now_ts)
    except (TypeError, ValueError):
        ends_at = now_ts
    left = max(0, int(ends_at - now_ts))
    mm, ss = left // 60, left % 60
    item_cnt = sum(part.get("items", {}).values())
    line2 = (f"已历 {part.get('event_count', 0)} 桩事 ｜ "
             f"灵石 +{part.get('gain_ling_shi', 0)} ｜ 修为 +{part.get('gain_exp', 0)}")
    if item_cnt:
        line2 += f" ｜ 遗物 ×{item_cnt}"
    lines = [f"🧭 秘境探索中 · 剩余 {mm} 分 {ss} 秒", line2]
    recent = (part.get("log") or [])[-3:]
    if recent:
        lines.append("最近见闻：")
        lines.extend("· " + e.get("text", "") for e in recent)
    lines.append("（途中机缘自动触发，结束后自动播报总览）")
    return "\n".join(lines)


def secret_summary_text(secret) -> str:
    """限时秘境结束后的总览播报（按收获降序，最多列 10 人）。"""
    parts = list((secret.get("participants") or {}).values())
    if not parts:
        return "🌟【限时秘境结束】本次无人入内，秘境重归沉寂。"
    parts.sort(key=lambda x: (x.get("gain_ling_shi", 0) + x.get("gain_exp", 0)),
               reverse=True)
    lines = ["🌟【限时秘境结束】探索总览"]
    for i, part in enumerate(parts[:10], 1):
        item_cnt = sum(part.get("items", {}).values())
        tail = f" ｜ 遗物 ×{item_cnt}" if item_cnt else ""
        lines.append(
            f"{i}. {part.get('name', part.get('qq'))}：{part.get('event_count', 0)} 桩 ｜ "
            f"灵石 +{part.get('gain_ling_shi', 0)} ｜ 修为 +{part.get('gain_exp', 0)}{tail}"
        )
    total_ls = sum(p.get("gain_ling_shi", 0) for p in parts)
    total_exp = sum(p.get("gain_exp", 0) for p in parts)
    lines.append(f"— 共 {len(parts)} 人入秘境，合计灵石 +{total_ls}、修为 +{total_exp}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 宗门
# ---------------------------------------------------------------------------
def create_sect(p, sect_name):
    if p["sect"]:
        return {"ok": False, "reason": "already"}
    sid = "s_" + str(p["qq"])
    sect = {"id": sid, "name": sect_name, "leader": str(p["qq"]),
            "members": [str(p["qq"])], "created": time.time()}
    p["sect"] = sid
    return {"ok": True, "sect": sect}


def join_sect(p, sect):
    if p["sect"]:
        return {"ok": False, "reason": "already"}
    if not sect:
        return {"ok": False, "reason": "notfound"}
    sect["members"].append(str(p["qq"]))
    p["sect"] = sect["id"]
    return {"ok": True, "sect": sect}


def sect_power(sect, players):
    return sum(power(p) for p in players)


def sect_war(sect_a, members_a, sect_b, members_b, rng=random.random):
    pa = sect_power(sect_a, members_a)
    pb = sect_power(sect_b, members_b)
    # 叠加随机扰动，制造以弱胜强可能
    pa_eff = pa * (0.6 + 0.8 * rng())
    pb_eff = pb * (0.6 + 0.8 * rng())
    a_wins = pa_eff >= pb_eff
    return {"a_wins": a_wins, "pa": pa, "pb": pb,
            "pa_eff": int(pa_eff), "pb_eff": int(pb_eff)}


# ---------------------------------------------------------------------------
# 世界事件：世界Boss / 秘境开启 / 奇遇
# ---------------------------------------------------------------------------
def roll_world_event(rng=random.random):
    return random.choice(["boss", "secret", "qiyu"])


def spawn_boss(participant_count=1, rng=random.random):
    hp = cfg.BOSS_BASE_HP * max(1, int(participant_count))
    return {"hp": hp, "max_hp": hp, "contributors": {},
            "expires": time.time() + 30 * 60}


def boss_hit(p, boss, rng=random.random):
    dmg = random.randint(cfg.BOSS_DMG_PER_HIT_MIN, cfg.BOSS_DMG_PER_HIT_MAX)
    # 战力高者出力更多；暴击同样生效（玩家能在「讨伐boss」的伤害数字上直接看到）
    dmg = int(dmg * (1 + power(p) / 5000.0) * crit_mult(p))
    boss["hp"] -= dmg
    qq = str(p["qq"])
    boss["contributors"][qq] = boss["contributors"].get(qq, 0) + dmg
    if boss["hp"] <= 0:
        ranked = sorted(boss["contributors"].items(), key=lambda x: -x[1])[:cfg.BOSS_TOP_N_REWARD]
        total = sum(v for _, v in ranked) or 1
        rewards = {qq: int(cfg.BOSS_REWARD_POOL * d / total) for qq, d in ranked}
        return {"dead": True, "dmg": dmg, "rewards": rewards}
    return {"dead": False, "dmg": dmg, "hp_left": boss["hp"]}


# ---------------------------------------------------------------------------
# 状态卡（文字，纯口令版无图）
# ---------------------------------------------------------------------------
def status_text(p):
    r = cfg.REALMS[p["realm"]]
    need = breakthrough_need(p)
    exp_show = f"{p['exp']}/{need}" if p["realm"] < cfg.MAX_REALM else f"{p['exp']}(已至巅峰)"
    inv = "、".join(f"{k}×{v}" for k, v in p["inv"].items()) or "空空如也"
    sect = p["sect"] or "无"
    if p["secluding"]:
        seal = (f"🔒闭关中…（每分钟 +{seclude_rate(p):.0f} 修为，"
                f"上限 {seclude_cap(p)}，发「出关」结算）")
    else:
        seal = (f"💡发送「闭关」开始修炼（每分钟 +{seclude_rate(p):.0f} 修为，"
                f"不闭关不涨）")
    fur = ""
    if p.get("furnace"):
        fur = f"🏺 炼丹炉：{p['furnace']}"
        if p.get("mixing"):
            left = max(0, int(p["mixing"]["end"] - time.time()))
            fur += f"（炼「{p['mixing']['name']}」还需 {left // 60} 分，发「收取丹药」）"
        fur += "\n"
    return (
        f"【{p['name']}】\n"
        f"境界：{r['name']}（{exp_show} 修为）\n"
        f"灵根：{p['root']}（修炼×{p['root_mult']}）\n"
        f"战力：{power(p)} ｜ 灵石：{p['ling_shi']}\n"
        f"寿元：剩余约 {int(p['life']-p['age'])} 年\n"
        f"战绩：{p['wins']}胜 {p['losses']}负 ｜ 宗门：{sect}\n"
        f"背包：{inv}\n"
        f"{fur}"
        f"{seal}"
    )


def realm_name(idx):
    return cfg.REALMS[idx]["name"]
