# -*- coding: utf-8 -*-
"""斗技三阶 · 四槽上阵内核（纯函数，无副作用，不依赖 nonebot）。

设计（用户 2026-11-14 定）：
  * 入道随机宗门 → 该宗门斗技分 **普通 / 高阶 / 特殊** 三阶，随修为解锁。
  * 上阵只允许 **四槽**，三阶可任意搭配（后期可 4 特殊）。
  * 战斗/掠夺每回合在「已装备且斗气足够」的技里**随机释放**；都放不出 → 武器普攻。
  * 每阶斗气消耗不同（占 mp_max 比例）。

本模块只做「技能目录 / 解锁 / 四槽 / 斗气单价」的纯计算；
实际扣血与随机释放由 core.fight / core.duel 调用本模块完成。
"""
from __future__ import annotations

from . import config as cfg
from . import factions as fac
from . import flame_skills as fs
from . import skill_book as book


# ---------------------------------------------------------------------------
# 目录派生
# ---------------------------------------------------------------------------
def _skill_name(raw: str) -> str:
    """从一条斗技描述里取展示名（仅在 skill_book 无该宗门时的兜底路径使用）。

    例："六合游身尺（地阶中，萧厉）[C1]" -> "六合游身尺"
    """
    s = str(raw).split("[")[0].strip()
    for sep in ("（", "("):
        s = s.split(sep)[0]
    s = s.split("/")[0].strip()
    return s


def _rank_tier(raw: str) -> str:
    """地/天阶 -> 高阶；其余（黄/玄/未标）-> 普通。"""
    if any(t in raw for t in ("天阶", "地阶")):
        return "高阶"
    return "普通"


def _to_entry(rec: dict, fid: str, origin: str) -> dict:
    """skill_book 记录 → 运行时斗技条目（唯一转换入口，本宗/通用共用）。"""
    tier = rec["tier"]
    novel = str(rec.get("novel") or "")
    source = str(rec.get("source") or "")
    return {
        "name": rec["name"],
        "tier": tier,
        "faction": fid,
        "origin": origin,                 # "本宗" / "通用" / "异火"
        "mp": round(rec["mp"] / 100.0, 4),
        "dmg": rec["dmg"] / 100.0,
        "unlock": int(rec.get("unlock") or cfg.SKILL_TIER_UNLOCK[tier]),
        "desc": rec["desc"],
        "brief": rec.get("brief", ""),
        "rank": rec["rank"],
        "kind": rec["kind"],
        "scope": rec["scope"],
        "source": source,
        "novel": novel,
        "cd": rec["cd"],
        # 「游戏自设」= 原著无据的战斗技，卡片要显式标注，不许冒充原著斗技
        "core": ("游戏自设" in source) or ("原著无载" in novel),
    }


def _from_book(fid: str) -> list[dict]:
    """从 skill_book（唯一权威表）派生某个宗门的**本宗专属**斗技。"""
    return [_to_entry(rec, fid, "本宗") for rec in book.skills_of(fid)]


def _from_faction_strings(fid: str) -> list[dict]:
    """兜底：skill_book 尚未收录该宗门时，退回 factions.py 的字符串解析。"""
    f = fac.FACTIONS.get(fid)
    if not f:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in f.get("skills", []):
        name = _skill_name(raw)
        # 兜底路径也必须拦住典故：factions.py 的 skills 是「宗门资料」，
        # 里面仍写着炼药/考核/丹会，绝不能因为某天 FACTION_SKILLS 缺键就把它们放回斗技池。
        if name in book.NON_SKILL_NAMES:
            continue
        if not name or name in seen:
            continue
        core = "[游戏核心]" in raw
        tier = "普通" if core else _rank_tier(raw)
        dmg = cfg.SKILL_BY_NAME.get(name, {}).get("dmg_mult", cfg.SKILL_TIER_DMG[tier])
        out.append({
            "name": name, "tier": tier, "faction": fid, "origin": "本宗",
            "mp": cfg.SKILL_TIER_MP_FRAC[tier],
            "dmg": dmg, "unlock": cfg.SKILL_TIER_UNLOCK[tier],
            "desc": raw, "brief": _skill_name(raw), "core": core,
        })
        seen.add(name)
    for sp in f.get("special_skills", []):
        name = sp.get("name")
        if name in book.NON_SKILL_NAMES:
            continue
        if not name or name in seen:
            continue
        out.append({
            "name": name, "tier": "特殊", "faction": fid, "origin": "本宗",
            "mp": cfg.SKILL_TIER_MP_FRAC["特殊"],
            "dmg": cfg.SKILL_TIER_DMG["特殊"],
            "unlock": int(sp.get("unlock_lv", cfg.SKILL_TIER_UNLOCK["特殊"])),
            "desc": sp.get("note", ""), "brief": name,
        })
        seen.add(name)
    return out


def catalog(fid: str) -> list[dict]:
    """某个宗门全部可学斗技 = 本宗专属斗技 + 通用基础斗技。

    2026-11-15 梳理：炼药师公会这类宗门原著里只有 1 门本宗斗技（千焰剑罡，
    古河在「战古河」施展），剥离炼药/考核/丹会后不能靠塞假斗技凑四槽；
    改用 ``skill_book.COMMON_SKILLS``（原著有章句、斗技堂/拍卖会流通的基础斗技）
    补齐，并标 ``origin="通用"``，卡片与文本都明说是通用而非本宗传承。
    """
    fid = str(fid or "")
    base = _from_book(fid) if fid in book.FACTION_SKILLS else _from_faction_strings(fid)
    seen = {e["name"] for e in base}
    common = [_to_entry(rec, "通用", "通用")
              for rec in book.common_entries() if rec["name"] not in seen]
    return base + common



def entries_for(p) -> list[dict]:
    """玩家技能目录：本宗斗技 + 已收服异火的专属主动技。

    2026-11-15：异火技从这里进池，四槽 / 斗技列表 / 斗技详情 / 战斗选招
    全部自动支持，不必改 skill_book 的 185 条大表。
    """
    base = catalog(str(p.get("faction") or ""))
    extra = fs.entries_for(p)
    if not extra:
        return base
    seen = {e["name"] for e in base}
    return base + [e for e in extra if e["name"] not in seen]


def learnable(p) -> list[dict]:
    """当前修为已解锁的斗技（unlock_lv <= 玩家等级）。"""
    lv = int(p.get("lv", 1))
    return [e for e in entries_for(p) if int(e["unlock"]) <= lv]


# ---------------------------------------------------------------------------
# 四槽
# ---------------------------------------------------------------------------
def _default_pick(pool: list[dict]) -> list[str]:
    """默认四槽：优先 2 普通 + 2 高阶（贴合「前期普通+高阶搭配」），不足再补特殊/其余。"""
    common = [e for e in pool if e["tier"] == "普通"]
    high = [e for e in pool if e["tier"] == "高阶"]
    spec = [e for e in pool if e["tier"] == "特殊"]
    pick = common[:2] + high[:2]
    if len(pick) < cfg.LOADOUT_SIZE:
        rest = [e for e in (common[2:] + high[2:] + spec) if e not in pick]
        pick += rest
    return [e["name"] for e in pick[:cfg.LOADOUT_SIZE]]


def ensure_loadout(p) -> list[str]:
    """把 p["loadout"] 规范成 ≤4 个「已解锁且属于本宗/通用」的技能名。

    2026-11-15 修复：旧版只在 **list 为空** 时自动配默认，于是存档里只要还剩
    1 个合法技（例如清理掉「丹会」后只剩「千焰剑罡」），四槽就永远只剩 1 个，
    用户看到的就是「我的斗技怎么就剩一个了」。现在改为**不足四槽自动补满**，
    且只补玩家没选过的技，不覆盖玩家显式上阵的选择。
    """
    pool = learnable(p)
    names = [e["name"] for e in pool]
    cur = list(dict.fromkeys(n for n in (p.get("loadout") or []) if n in names))
    if len(cur) < cfg.LOADOUT_SIZE and names:
        cur += [n for n in names if n not in cur][:cfg.LOADOUT_SIZE - len(cur)]
    p["loadout"] = cur[:cfg.LOADOUT_SIZE]
    return p["loadout"]


def loadout_entries(p) -> list[dict]:
    """已上阵四槽（或不足四个）的完整技能对象。"""
    by = {e["name"]: e for e in entries_for(p)}
    out = []
    for n in ensure_loadout(p):
        if n in by:
            out.append(by[n])
    return out


# ---------------------------------------------------------------------------
# 斗气单价 / 伤害倍率
# ---------------------------------------------------------------------------
def tier_cost(tier: str, mp_max_val: int) -> int:
    """某一阶斗技的斗气消耗（占 mp_max 比例的绝对值，至少 1）。"""
    frac = cfg.SKILL_TIER_MP_FRAC.get(tier, 0.20)
    return max(1, int(round(int(mp_max_val) * frac)))


def skill_mult(entry: dict, lv: int) -> float:
    """技能倍率：阶基础倍率 ×（每级 +20%）。"""
    return float(entry["dmg"]) * (1 + 0.2 * (int(lv) - 1))
