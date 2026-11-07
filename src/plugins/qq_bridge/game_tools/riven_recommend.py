# -*- coding: utf-8 -*-
"""game_tools/riven_recommend.py —— 紫卡洗卡词条推荐（双维度：击杀效率 + 市场挂牌分层）。

双维度推荐：
  * 击杀效率：复用 riven_card 的 DPS 模型（_gun_std_bucket / _apply_riven / _dps），
    每个候选正面词条按**该词条满 roll 值**（data/warframe_items/riven_stat_base.json
    的 3 正上限、倾向 1.0）算「边际 DPS 贡献」即收益%，排序取前 4；
    推荐负面 = NEG_DPS_IMPACT <= 0.5 的零影响负词条（按伤害影响升序、市场友好度降序）。
    口径说明：早期版本按「统一 +100%」归一化，会让射速（桶内基数只有 0.30）
    在每把武器上都虚高到 76.9% 排第一；改用满 roll 后与 Rabbit 参考卡同量级
    （实测多重 34.6% vs 参考卡 33.7%）。融合元素（腐蚀/病毒/辐射/磁力/爆炸/毒气）
    是真实可洗词条，按元素桶并入统计，并在卡面标「(融合)」；本仓库模型不区
    元素类型（统一按病毒靶 ×2），故该维度是「模型估收益」，非实盘成交数据。
  * 市场挂牌分层：Warframe.Market `/v1/auctions/search` 取同武器两片挂牌
    （一口价降序=高价层、升序=底价层），统计每个词条的**出现率**；
    差值（高价−底价）为该词条的市场偏好度 → T0/T1/T2、推荐负面、普通负面、致命项。

为什么不做「价差 p」：该接口单次最多约 100 条且**忽略 page**（实测翻页无效），
拿不到全量样本，就推不出可靠的中位价差；硬算等于编数。详见「维度二」处注释。

数据纪律（禁止降级 / 禁止造假，CLOSE-02 精神）：
  * 接口失败/样本不足 → 如实标注原因，绝不编数、绝不静默降级；
  * 融合(combined) 实盘无独立属性字段 → 不统计、不估算，只在说明里讲清；
  * 灵化进化面板本仓库未收录 → 显式标「未收录」，不伪造数值。
"""
from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
import urllib.parse
from typing import Optional

from nonebot.log import logger

from . import warframe, riven_card as rc

# ===========================================================================
# DE 词条名 → WFM url_name（深度调研 Warframe.Market 实盘词表确认）
# ===========================================================================
DE_EN_TO_URL = {
    # 注意：WFM 实盘把「基础伤害 / 射速」合并成一条属性（实测 url 如下），
    # 写成 damage / fire_rate 会永远匹配不到，导致基伤、射速整项丢失。
    "Damage": "base_damage_/_melee_damage",
    "Base Damage": "base_damage_/_melee_damage",
    "Critical Chance": "critical_chance",
    "Critical Damage": "critical_damage",
    "Multishot": "multishot",
    "Status Chance": "status_chance",
    "Fire Rate": "fire_rate_/_attack_speed",
    "Attack Speed": "fire_rate_/_attack_speed",
    "Fire Rate / Attack Speed": "fire_rate_/_attack_speed",
    "Heat Damage": "heat_damage",
    "Cold Damage": "cold_damage",
    "Electricity Damage": "electric_damage",
    "Toxin Damage": "toxin_damage",
    "Viral Damage": "viral",
    "Corrosive Damage": "corrosive",
    "Radiation Damage": "radiation",
    "Magnetic Damage": "magnetic",
    "Blast Damage": "blast",
    "Gas Damage": "gas",
    "Impact Damage": "impact_damage",
    "Puncture Damage": "puncture_damage",
    "Slash Damage": "slash_damage",
    "Range": "range",
    "Punch Through": "punch_through",
    "Reload Speed": "reload_speed",
    "Magazine Capacity": "magazine_capacity",
    "Ammo Maximum": "ammo_maximum",
    "Zoom": "zoom",
    "Weapon Recoil": "recoil",
    "Projectile Speed": "projectile_speed",
    "Combo Duration": "combo_duration",
    "Initial Combo": "initial_combo",
    "Status Duration": "status_duration",
    "Finisher Damage": "finisher_damage",
    "Heavy Attack Efficiency": "heavy_attack_efficiency",
    "Heavy Attack Damage": "heavy_attack_damage",
    "Slide Attack Critical Chance": "critical_chance_on_slide_attack",
    "Slide Attack Critical Damage": "critical_damage_on_slide_attack",
    "Damage to Grineer": "damage_vs_grineer",
    "Damage to Corpus": "damage_vs_corpus",
    "Damage to Infested": "damage_vs_infested",
    "Damage vs Grineer": "damage_vs_grineer",
    "Damage vs Corpus": "damage_vs_corpus",
    "Damage vs Infested": "damage_vs_infested",
    "Weak Point Damage": "weak_point_damage",
}

# 融合(combined) 变体：实盘确有，但样本稀少；优先按实盘词表反查，兜底用下列 slug。
COMBINED = [
    ("Corrosive Damage (Combined)", "corrosive_combined", "腐蚀(融合)"),
    ("Viral Damage (Combined)", "viral_combined", "病毒(融合)"),
    ("Radiation Damage (Combined)", "radiation_combined", "辐射(融合)"),
    ("Magnetic Damage (Combined)", "magnetic_combined", "磁力(融合)"),
    ("Blast Damage (Combined)", "blast_combined", "爆炸(融合)"),
    ("Gas Damage (Combined)", "gas_combined", "毒气(融合)"),
    ("Heat Damage (Combined)", "heat_damage_combined", "火伤(融合)"),
    ("Cold Damage (Combined)", "cold_damage_combined", "冰伤(融合)"),
    ("Electricity Damage (Combined)", "electricity_damage_combined", "电伤(融合)"),
    ("Toxin Damage (Combined)", "toxin_damage_combined", "毒伤(融合)"),
    ("Weak Point Damage (Combined)", "weak_point_damage_combined", "弱点伤害(融合)"),
]
# url_name → 中文显示名（补 riven_card 未覆盖的实盘词表）
URL_ZH = {
    "status_damage": "状态伤害",
    "base_damage_/_melee_damage": "基础/近战伤害",
    "channeling_efficiency": "聚气效率",
    "channeling_damage": "聚气伤害",
    "critical_chance_on_slide_attack": "滑劈暴击率",
    "critical_damage_on_slide_attack": "滑劈暴伤",
    "heavy_attack_efficiency": "重击效率",
    "heavy_attack_damage": "重击伤害",
    "heavy_attack_wind_up_speed": "重击蓄力",
    "weak_point_critical_chance": "弱点暴击率",
    "weak_point_critical_damage": "弱点暴伤",
    "ammo_efficiency": "弹药效率",
    "finisher_damage": "处决伤害",
    "damage_vs_grineer": "对G系伤害",
    "damage_vs_corpus": "对C系伤害",
    "damage_vs_infested": "对I系伤害",
}
URL_TO_DE_EN = {v: k for k, v in DE_EN_TO_URL.items()}
for _en, _u, _zh in COMBINED:
    URL_TO_DE_EN[_u] = _en

# 跳过派系词条（正负皆独立词条，不参与「主推正面」排序，避免刷屏）
_FACTION = {"Damage to Grineer", "Damage vs Grineer",
            "Damage to Corpus", "Damage vs Corpus",
            "Damage to Infested", "Damage vs Infested"}
_FACTION_URLS = {DE_EN_TO_URL[e] for e in _FACTION if e in DE_EN_TO_URL}

# 灵化(Incarnon) 进化面板覆盖表
# 仓库自动数据源（riven_card._weapon_raw / WFM）不含「进化后面板」，此处录入已核实的
# 游戏实装数据（与 _panel_normal 同字段：cc/cm/status/fr/pellets/base/comp）。
# 仅覆盖已调研武器；未收录武器灵化列显式标「未收录」，绝不编造数值。键 = WFM slug。
INCARON_OVERRIDE = {
    "phenmor": {  # 凤殁（Phenmor）进化形态：射速大幅提升，伤害构成转为切割+辐射
        "cc": "20%", "cm": "×2", "status": "20%", "fr": "13.33/秒",
        "pellets": "1", "base": "140", "comp": "切割 57% · 辐射 43%",
    },
}

# 基础数据集已知「伤害构成错位（puncture/slash 互换）」的武器修正。
# 已逐条对照官方 Wiki 核实后才录入；未核实的仍按数据集展示（不猜）。
DAMAGE_COMP_OVERRIDE = {
    "phenmor": "切割 70% · 穿刺 30%",  # Wiki Phenmor: Puncture 42 / Slash 98
}


# ===========================================================================
# 工具
# ===========================================================================
def _confidence(n: int) -> int:
    """样本 n → 置信度%（n=20 约满，封顶 99）。"""
    return min(99, round(n / 20 * 100))


def _make_w(raw: dict) -> dict:
    """把原始武器 dict 包装成 DPS 模型可用的 w（补 cat=主武器/副武器/近战武器）。"""
    w = dict(raw)
    t = (raw.get("type") or raw.get("category") or "")
    if t in ("Rifle", "Shotgun", "Bow", "Launcher", "Archgun", "Kitgun", "Continuity"):
        cat = "主武器"
    elif t in ("Pistol", "Secondary"):
        cat = "副武器"
    elif t in ("Melee", "Zaw"):
        cat = "近战武器"
    else:
        cat = ""
    w["cat"] = cat
    return w


def _num(raw: dict, *keys):
    """取第一个真实存在的数值字段；缺失返回 None（**不拿 0 冒充**）。"""
    for k in keys:
        v = raw.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _panel_normal(raw: dict) -> dict:
    """普通面板（真实数据）：暴击率/暴击倍率/触发率/射速/弹丸/基础伤害 + 伤害构成。

    数据源缺字段时统一显式「—」：用 0 填坑等于编数（近战武器普遍缺 statusChance）。
    """
    dmg = raw.get("damage") or {}
    phys = {"impact": float(dmg.get("impact") or 0),
            "puncture": float(dmg.get("puncture") or 0),
            "slash": float(dmg.get("slash") or 0)}
    phys_sum = sum(phys.values())
    comp_parts = []
    if phys_sum > 0:
        for k, v in phys.items():
            if v > 0:
                comp_parts.append("%s %.0f%%" % ({"impact": "冲击", "puncture": "穿刺", "slash": "切割"}[k],
                                                 v / phys_sum * 100))
    else:
        tot = float(dmg.get("total") or 0) or 1e-9
        for k in ("heat", "cold", "electricity", "toxin", "viral", "corrosive", "radiation", "magnetic", "gas", "blast"):
            v = float(dmg.get(k) or 0)
            if v > 0:
                comp_parts.append("%s %.0f%%" % (k, v / tot * 100))
    cc = _num(raw, "criticalChance")
    cm = _num(raw, "criticalMultiplier")
    sc = _num(raw, "statusChance", "procChance")
    fr = _num(raw, "fireRate")
    ms = _num(raw, "multishot")
    total = _num(raw, "totalDamage")
    if total is None and isinstance(dmg, dict):
        total = _num(dmg, "total")
    return {
        "cc": ("%.0f%%" % (cc * 100)) if cc is not None else "—",
        # 暴伤倍率：整数不带小数位（参考卡写「×2」而非「×2.0」）
        "cm": ("—" if cm is None else
               ("×%.0f" % cm) if abs(cm - round(cm)) < 1e-9 else ("×%.1f" % cm)),
        "status": ("%.0f%%" % (sc * 100)) if sc is not None else "—",
        "fr": ("%.2f/秒" % fr) if fr is not None else "—",
        "pellets": ("%.0f" % ms) if ms is not None else "1",
        "base": ("%.0f" % total) if total is not None else "—",
        "comp": " · ".join(comp_parts) if comp_parts else "—",
    }


# ===========================================================================
# 维度一：击杀效率
# ===========================================================================
#: 满 roll 基础值表（3 正上限 / 倾向 1.0，DE 官方数值，与 riven_card._mid 同源）
_MAXROLL: dict = {}
#: 融合元素词条：单条即成型的复合元素，本仓库元素桶按「元素伤害」统一计入
_COMBINED_ELEM = {"Corrosive Damage", "Viral Damage", "Radiation Damage",
                  "Magnetic Damage", "Blast Damage", "Gas Damage"}
#: 全部元素词条：本仓库 DPS 模型只有一个元素桶（不区元素类型），十种元素边际完全相同，
#: 故排序时并成一类、只出一条代表；代表按下列顺序取（融合优先，对齐实盘洗卡习惯）。
_ELEM_ALL = _COMBINED_ELEM | {"Heat Damage", "Cold Damage",
                              "Electricity Damage", "Toxin Damage"}
_ELEM_PREF = ["Corrosive Damage", "Viral Damage", "Radiation Damage", "Magnetic Damage",
              "Blast Damage", "Gas Damage", "Heat Damage", "Cold Damage",
              "Electricity Damage", "Toxin Damage"]
#: 击杀效率区用全称（对齐参考卡「多重射击 / 基础伤害」而非市场区的「多重 / 基伤」）
_FULL_STAT = {
    "Damage": "基础伤害", "Multishot": "多重射击", "Critical Damage": "暴击伤害",
    "Critical Chance": "暴击几率", "Status Chance": "触发几率",
    "Fire Rate / Attack Speed": "射速", "Fire Rate": "射速", "Attack Speed": "攻速",
    "Slash Damage": "切割伤害", "Impact Damage": "冲击伤害", "Puncture Damage": "穿刺伤害",
    "Reload Speed": "装填速度", "Status Duration": "异常持续时间",
    "Corrosive Damage": "腐蚀伤害", "Viral Damage": "病毒伤害",
    "Radiation Damage": "辐射伤害", "Magnetic Damage": "磁力伤害",
    "Blast Damage": "爆炸伤害", "Gas Damage": "毒气伤害",
}


def _maxroll_tbl(cls: str) -> dict:
    """按紫卡类别取满 roll 表。缺表/读失败返回 {}（调用方如实跳过，不估算）。"""
    if not _MAXROLL:
        try:
            _MAXROLL.update(json.loads(
                (rc._DATA_DIR / "riven_stat_base.json").read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001 — 读不到就如实空表，不编数
            logger.warning(f"riven_recommend_maxroll_failed: {type(e).__name__}: {e}")
            _MAXROLL["__error__"] = str(e)[:120]
    return _MAXROLL.get(cls or "") or {}


#: 面板 type → 紫卡类别（仅在 `riven_disp.json` 查不到该武器时的次选。
#: 面板分类（Throwing / Dual Pistols / Companion Weapon…）与 DE 紫卡类别是两套词表，
#: 直接拿面板 type 查词条表会查空——实测 Throwing/Dual Pistols 曾整块空白。）
RIVEN_CLASS = {
    "Rifle": "Rifle", "Shotgun": "Shotgun", "Pistol": "Pistol",
    "Throwing": "Pistol", "Dual Pistols": "Pistol",
    "Melee": "Melee", "Zaw": "Zaw", "Zaw Component": "Zaw",
    "Kitgun": "Kitgun", "Archgun": "Archgun",
}
_FAMILY_SUFFIX = re.compile(r"\s+(Prime|Wraith|Vandal|Dex|Kuva|Tenet|Prisma|Mara)$", re.I)


def _riven_class(name_en: str, type_key: str) -> str:
    """武器 → 紫卡类别（决定词条满 roll 表）。

    首选 `riven_disp.json`：DE 官方紫卡表，623 把武器带 `type`，**守护武器也在内**
    （Artax→Rifle、Deconstructor→Melee、Sweeper→Shotgun），是唯一权威口径。
    查不到才退回面板 type 映射；都没有 → 空串（该武器确实没有紫卡，卡面如实写）。
    """
    disp, _, _ = rc._load_riven_tables()
    for key in (name_en, _FAMILY_SUFFIX.sub("", (name_en or "").strip())):
        rec = disp.get(key or "")
        if isinstance(rec, dict) and rec.get("type"):
            return str(rec["type"])
    return RIVEN_CLASS.get((type_key or "").strip(), "")


def _full_stat(en: str) -> str:
    """击杀效率区显示名：全称 + 融合元素后缀。"""
    zh = _FULL_STAT.get(en) or rc._short_stat(en)
    return zh + "(融合)" if en in _COMBINED_ELEM else zh


def _apply_one(bucket: dict, en: str, val: float) -> None:
    """把一个词条按满 roll 值折进配卡桶。

    融合元素走元素桶（riven_card._apply_riven 只认四基础元素，融合变体在仓库
    统计表里是独立键，需在此并入，否则整类词条边际恒为 0 被静默丢弃）。
    """
    if en in _COMBINED_ELEM:
        bucket["elem"] += float(val) / 100.0
    else:
        rc._apply_riven(bucket, [{"en": en, "value": float(val), "unit": "%", "sign": "+"}])


def _kill_eff_recs(w: dict, type_key: str, name_en: str = "") -> dict:
    """正面词条（满 roll 边际 DPS 收益% 前 4）+ 推荐负面（零影响负 前 3）。

    方法论：对每个候选正面词条，取该词条在 `riven_stat_base.json` 里的满 roll 值
    （3 正上限、倾向 1.0），折进 `rc._gun_std_bucket` 后重算 `rc._dps`，
    收益 = 边际/基线 − 1。完全复用 riven_card 的 DPS 模型，不另立公式。
    """
    cls = _riven_class(name_en, type_key)
    if not cls:
        # 确实没有紫卡的武器：如实说明，不硬套别类词条表
        return {"pos": [], "neg": [],
                "na": "该武器没有紫卡（%s 不在紫卡类别表内），无洗卡词条"
                      % (name_en or type_key or "未知武器")}
    tbl = _maxroll_tbl(cls)
    if not tbl:
        return {"pos": [], "neg": [],
                "na": "缺 %s 类词条满 roll 表（data/warframe_items/riven_stat_base.json），无法估值" % cls}
    _, base_tbl, _ = rc._load_riven_tables()
    cand = [e for e in (base_tbl.get(cls) or {}) if e not in _FACTION]
    # 元素类预排：稳定排序保证「同值时融合优先」，不让并列随机成「电伤/火伤」
    _pref = {en: i for i, en in enumerate(_ELEM_PREF)}
    cand.sort(key=lambda e: _pref.get(e, 99))

    base_dps = rc._dps(w, rc._gun_std_bucket(w)) if w else 0.0

    def _marginal(en: str, val: float) -> float:
        if not w or base_dps <= 0:
            return 0.0
        b = rc._gun_std_bucket(w)
        _apply_one(b, en, val)
        return rc._dps(w, b)

    rows = []
    for en in cand:
        val = tbl.get(en)
        if not val or val < 1.0:
            continue  # 表里没有该词条的满 roll 值 → 不参与排序（不拿假值顶替）
        g = (_marginal(en, val) / base_dps - 1.0) * 100.0 if base_dps > 0 else 0.0
        rows.append((en, g, val))
    rows.sort(key=lambda x: x[1], reverse=True)
    seen = set()
    pos = []
    for en, g, val in rows:
        if g <= 0:
            continue
        key = "elem" if en in _ELEM_ALL else en   # 元素类只出一条代表
        if key in seen:
            continue
        seen.add(key)
        pos.append({"zh": _full_stat(en), "gain": round(g, 1), "roll": round(val, 1)})
        if len(pos) >= 4:
            break

    safe_neg = [e for e in cand
                if rc.NEG_DPS_IMPACT.get(e, 10.0) <= 0.5]
    safe_neg.sort(key=lambda e: (rc.NEG_DPS_IMPACT.get(e, 0.0),
                                 -rc.NEG_MARKET.get(e, 0)))
    neg = [rc._neg_zh(e) for e in safe_neg[:3]]
    return {"pos": pos, "neg": neg}


# ===========================================================================
# 维度二：市场挂牌分层
#
# 直接复用 riven_card 的市场抓取与口径（同源、同缓存、同节流、同错误类型）：
#   * `rc._fetch_auction_page(slug, sort_by)` 带 buyout_policy=direct + API 型 UA
#     + 600s 页面缓存 + 0.35s 节流；失败一律 `RivenMarketError` 上抛。
#   * `price_desc`（最贵 100 条，实测含 66666p 挂单）**只用于词条频次**，
#     `rc._hot_counts` 是它唯一出口，价格绝不进入任何计算（AGENTS.md 明文规则）。
#   * 价格一律取 `price_asc`（低价视图）的**中位数** `rc._robust_center`。
#
# 本卡的指标 = **出现率**，不是价差：
#   WFM 公开检索只暴露「最便宜 100 / 最贵 100」两个截断视图（page 被忽略，
#   实测 asc 第 1、2 页 id 完全重合），没有代表性中间样本 → 无法推可靠价差；
#   硬算等于编数。故改为：
#       高价层出现率 = 该词条在 price_desc 100 条里出现的比例
#       底价层出现率 = 该词条在 price_asc 100 条里出现的比例
#   两者差值 = 高价挂牌对该词条的偏好度（可复现、可核对）。
#   实测 Phenmor：87% 的高价卡是 3 正 1 负，而底价卡多为 2~3 属性，
#   故「-暴率」在高价层 82%、底价层 4% —— 这正是洗卡要留的负面。
# ===========================================================================


def _slice_n(auctions: list) -> int:
    """该片里带词条属性的挂牌条数（出现率的分母）。"""
    return sum(1 for a in auctions if rc._auction_attrs(a))


def _attrs_median(auctions: list) -> float:
    """该片挂牌价中位数（rc._robust_center 唯一入口，不用均值）。"""
    prices = rc._prices_of(auctions)
    return rc._robust_center(prices) if prices else 0.0


def _zh_of(u: str) -> str:
    """url_name → 中文名（本模块补充表 + 词典反查；查不到原样返回，不编译名）。"""
    if u in URL_ZH:
        return URL_ZH[u]
    en = URL_TO_DE_EN.get(u)
    if en:
        return rc._short_stat(en)
    return u


def _short_or_zh(u: str) -> str:
    """url_name → 中文短名：先走 riven_card 的权威词表，再退回本模块补充表。"""
    s = rc._short(u)
    if s and s != u:
        return s
    return _zh_of(u)


def _market_recs(type_key: str, asc: list, desc: list) -> dict:
    """高价层/底价层出现率 → 正面 T0/T1/T2 + 推荐负面 + 普通负面 + 致命项。"""
    n_hi, n_lo = _slice_n(desc), _slice_n(asc)
    base = {"n_hi": n_hi, "n_lo": n_lo,
            "med_hi": round(_attrs_median(desc), 1), "med_lo": round(_attrs_median(asc), 1)}
    if n_hi < 10 or n_lo < 10:
        return {"ok": False, "err": "该武器挂牌过少（高价 %d 条 / 底价 %d 条），分层统计不可用"
                                    % (n_hi, n_lo), **base}
    id_hi = {a.get("id") for a in desc}
    id_lo = {a.get("id") for a in asc}
    if len(id_hi & id_lo) >= min(n_hi, n_lo) * 0.8:
        return {"ok": False, "err": "该武器挂牌集中在同一价位（两片样本重合），分层统计不可用", **base}

    c_hi_pos = rc._hot_counts(desc, True)
    c_hi_neg = rc._hot_counts(desc, False)
    c_lo_pos = rc._hot_counts(asc, True)
    c_lo_neg = rc._hot_counts(asc, False)

    _, base_tbl, _ = rc._load_riven_tables()
    all_en = list(base_tbl.get(type_key) or [])
    # universe: (url, zh, is_comb, mpos_ok, neg_ok)
    #   mpos_ok = 该武器类型合法且非派系词条（派系正/负是独立卡，混进「正面」会刷屏）
    #   neg_ok  = 词典内已知词条（含派系），可进「推荐/普通负面/致命项」
    universe: list = []
    for en in all_en:
        u = DE_EN_TO_URL.get(en)
        if u:
            universe.append((u, _short_or_zh(u), False, en not in _FACTION, True))
    for _en, u, zh in COMBINED:
        universe.append((u, zh, True, True, True))
    seen = {u for u, _, _, _, _ in universe}
    for u in list(c_hi_pos) + list(c_hi_neg) + list(c_lo_pos) + list(c_lo_neg):
        if u in seen or u in URL_TO_DE_EN:
            continue
        seen.add(u)
        universe.append((u, _zh_of(u), False, False, False))

    # 阈值按实盘校准（Phenmor 实测：高价层 87% 是 3 正 1 负、底价层多为 2~3 属性）：
    #   正面看「高价层出现率 − 底价层出现率」，≥5 个百分点才列，否则视为无区分度；
    #   负面在垃圾卡池里种类被摊薄（每种约 2~5%），故按出现次数取。
    MIN_HI, MIN_PAIR, MIN_LIFT, MIN_DROP = 5, 3, 5.0, 5.0
    mpos, rec_neg, ord_neg, deadly, comb_missing = [], [], [], [], []
    for u, zh, is_comb, mpos_ok, neg_ok in universe:
        h = c_hi_pos.get(u, 0)
        l = c_lo_pos.get(u, 0)
        hn = c_hi_neg.get(u, 0)
        ln = c_lo_neg.get(u, 0)
        if h == 0 and l == 0 and hn == 0 and ln == 0:
            if is_comb:
                comb_missing.append(zh)
            continue
        pos_hi = h / n_hi * 100.0
        pos_lo = l / n_lo * 100.0
        neg_hi = hn / n_hi * 100.0
        neg_lo = ln / n_lo * 100.0
        pos_lift = pos_hi - pos_lo
        neg_lift = neg_hi - neg_lo

        if not neg_ok:      # url-only 未知词条（近战词条挂在步枪卡等脏数据）：不展示
            continue

        # ── 正面：高价层明显更常见 ──
        if mpos_ok and h >= MIN_HI and pos_lift >= MIN_LIFT:
            tier = "T0" if pos_lift >= 45.0 else "T1" if pos_lift >= 20.0 else "T2"
            mpos.append({"zh": zh, "hi": round(pos_hi), "lo": round(pos_lo),
                         "n_hi": h, "n_lo": l, "conf": _confidence(h + l), "tier": tier})
        # ── 推荐负面：高价层更爱用的负词条（市场口径，伤害影响见「击杀效率」区块） ──
        if hn >= 2 and neg_lift >= 2.0 and u not in _FACTION_URLS:
            rec_neg.append({"zh": zh, "hi": round(neg_hi), "lo": round(neg_lo),
                            "n_hi": hn, "n_lo": ln, "conf": _confidence(hn + ln),
                            "lift": neg_lift})
        elif hn + ln >= MIN_PAIR and hn + ln > h + l:
            ord_neg.append({"zh": zh, "hi": round(neg_hi), "lo": round(neg_lo),
                            "n_hi": hn, "n_lo": ln, "conf": _confidence(hn + ln)})
        # ── 致命项：底价层常见、高价层罕见（正负皆计，含「看着是正面其实掉价」） ──
        if l >= MIN_PAIR and -pos_lift >= MIN_DROP:
            deadly.append({"zh": zh, "sign": "+", "hi": round(pos_hi), "lo": round(pos_lo),
                           "n_hi": h, "n_lo": l, "conf": _confidence(h + l), "drop": -pos_lift})
        if ln >= MIN_PAIR and -neg_lift >= MIN_DROP:
            deadly.append({"zh": zh, "sign": "-", "hi": round(neg_hi), "lo": round(neg_lo),
                           "n_hi": hn, "n_lo": ln, "conf": _confidence(hn + ln), "drop": -neg_lift})

    mpos.sort(key=lambda x: (-x["hi"] + x["lo"], -x["hi"]))
    rec_neg.sort(key=lambda x: -x["lift"])
    ord_neg.sort(key=lambda x: (-(x["lo"] - x["hi"]), -x["lo"]))
    deadly.sort(key=lambda x: -x["drop"])
    return {
        "ok": True, **base,
        "mpos": mpos[:10], "rec_neg": rec_neg[:3],
        "ord_neg": ord_neg[:9], "deadly": deadly[:15],
        "comb_missing": comb_missing,
    }


async def _market_recs_cached(slug_key: str, type_key: str) -> dict:
    """抓两片挂牌 → 分层统计。抓取复用 riven_card（自带 600s 页面缓存 + 0.35s 节流）。

    失败一律如实返回错误原因（`RivenMarketError` 原文），不静默降级、不写「暂无」。
    """
    if not slug_key:
        return {"ok": False, "err": "无法解析该武器的 Warframe.Market 标识，市场维度不可用"}
    slug = slug_key.replace("-", "_")   # WFM 物品 url 用下划线（标识规范化，不是备选方案）
    try:
        asc = await rc._fetch_auction_page(slug, "price_asc")
        desc = await rc._fetch_auction_page(slug, "price_desc")   # 仅词条频次
    except rc.RivenMarketError as e:
        return {"ok": False, "err": str(e)[:140]}
    except Exception as e:  # noqa: BLE001 — 未预期异常也要如实上卡面
        return {"ok": False, "err": "市场数据获取失败：%s: %s" % (type(e).__name__, str(e)[:100])}
    return _market_recs(type_key, asc, desc)


def _fmt_num(v) -> str:
    """价格显示：整数不带小数点（70 而非 70.0），小于 10 才保留一位。"""
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(f - round(f)) < 1e-9:
        return "%.0f" % f
    return ("%.1f" % f) if abs(f) < 10 else ("%.0f" % f)


def _weapon_label(raw: dict, fallback: str) -> str:
    """卡片显示名：优先官方中文名（对齐参考卡「凤殁」），缺中文名回落英文名。"""
    en = (raw.get("name") or "").strip()
    if not en:
        return fallback
    try:
        zh = (warframe._weapon_zh(en) or "").strip()
    except Exception:  # noqa: BLE001 — 中文名只是显示层，缺失不致命
        zh = ""
    return zh or en


#: 变体后缀：紫卡拍卖按**武器家族**挂单，Prime/Wraith 等变体不单列
#: （实测 weapon_url_name=burston 200 / burston_prime 400 / rubico_prime 400）
_VARIANT_SUFFIX = (" prime", " vandal", " wraith", " dex")


def _family_slug(name_en: str) -> str:
    """武器名 → WFM 紫卡武器 slug（家族级，下划线形式）。

    紫卡拍卖接口只认家族 slug：`rubico_prime` 会 400，必须查 `rubico`。
    词典（wf_riven_weapons.json）里带连字符，接口要下划线，这里一并规范化。
    """
    q = (name_en or "").strip()
    if not q:
        return ""
    low = q.lower()
    for suf in _VARIANT_SUFFIX:
        if low.endswith(suf):
            low = low[: -len(suf)].strip()
            break
    try:
        d = warframe._riven_weapons()
    except Exception:  # noqa: BLE001 — 词典缺失时按名字推导，不编造
        d = {"by_en": {}}
    rec = (d.get("by_en") or {}).get(low) or (d.get("by_en") or {}).get(q.lower())
    slug = (rec or {}).get("slug") or low.replace(" ", "_")
    return (slug or "").replace("-", "_").strip()


# ===========================================================================
# 主入口
# ===========================================================================
async def recommend_data(weapon_query: str) -> dict:
    """紫卡洗卡词条推荐主入口。返回可直接喂给 pic 模板的 dict。"""
    raw = rc._weapon_raw(weapon_query)
    if not raw:
        try:
            res = warframe._resolve_weapon(weapon_query)
            if res:
                raw = rc._weapon_raw(res.get("name") or res.get("en") or "")
        except Exception:  # noqa: BLE001
            raw = None
    if not raw:
        return {"status": "error", "msg": "未收录该武器：%s（请检查名称，如「凤殁」「绝路 rubico」）" % weapon_query}

    # 用英文名解析 WFM slug：riven 词典的 by_zh 实测为空（830 条全在 by_en），
    # 直接传中文名会解析失败退回原文 → 市场查询不筛选、拿到全站数据。必须用 raw 的英文名。
    # 紫卡拍卖按武器家族挂单（rubico_prime 会 400），故取家族级 slug。
    name_en = raw.get("name") or weapon_query
    slug = _family_slug(name_en) or warframe._resolve_riven_weapon(name_en)
    slug_key = (slug or "").strip().lower()
    type_key = raw.get("type") or raw.get("category") or ""
    w = _make_w(raw)
    panel = _panel_normal(raw)
    if slug_key in DAMAGE_COMP_OVERRIDE:
        panel["comp"] = DAMAGE_COMP_OVERRIDE[slug_key]
    is_inc = any("Incarnon" in (t or "") for t in (raw.get("tags") or []))
    inc = INCARON_OVERRIDE.get(slug_key) if is_inc else None

    # 武器定位描述（按真实面板暴击/触发阈值推导，非编造；对齐参考卡「可能是…武器」）
    _cc = round(float(raw.get("criticalChance") or 0), 3)
    _sc = round(float(raw.get("statusChance") or raw.get("procChance") or 0), 3)
    if _cc >= 0.20 and _sc >= 0.20:
        weapon_desc = "可能是暴击触发双修武器"
    elif _cc >= 0.20:
        weapon_desc = "可能是暴击武器"
    elif _sc >= 0.20:
        weapon_desc = "可能是触发武器"
    else:
        weapon_desc = "基础面板武器"

    ke = _kill_eff_recs(w, type_key, name_en)
    market = await _market_recs_cached(slug_key, type_key)
    market_err = None if market.get("ok") else (market.get("err") or "市场分层数据暂不可用")

    notes = [
        "击杀效率＝该词条按满 roll（3 正上限、倾向 1.0）折进标准配卡桶后的边际 DPS 收益%，"
        "非实盘成交数据；元素类统一按病毒靶计算，不区分元素类型",
        "市场维度＝挂牌分层出现率：高价视图（price_desc 最贵 100 条）只统计词条频次，"
        "其价格不参与任何计算；低价视图（price_asc 最便宜 100 条）只取中位数作参照",
        "出现率差（高价−底价）越大＝高价挂牌越偏爱该词条；「致命项」相反（底价常见、高价罕见，洗到基本不加价）",
        "融合元素（腐蚀／病毒／辐射／磁力／爆炸／毒气）在击杀效率维度已计入并标「(融合)」，"
        "但本模型十种元素同值，故只出一条代表（融合优先）；Warframe.Market 拍卖属性里"
        "没有对应独立字段，市场维度统计不到",
        "样本为 WFM 挂牌要价（非成交价），未按紫卡等级过滤；同卡最多一个融合词条，"
        "推荐随武器、配装与模拟场景变化",
    ]
    if market.get("ok"):
        notes.append("市场分层样本：高价视图 %d 条（中位 %sp）· 低价视图 %d 条（中位 %sp）" % (
            market.get("n_hi", 0), _fmt_num(market.get("med_hi", 0)),
            market.get("n_lo", 0), _fmt_num(market.get("med_lo", 0))))
    return {
        "status": "ok",
        "weapon": _weapon_label(raw, weapon_query),
        "weapon_en": raw.get("name") or weapon_query,
        "weapon_desc": weapon_desc,
        "slug": slug,
        "type_label": type_key,
        "is_incarnon": is_inc,
        "panel": {
            "normal": panel,
            "incarnon": inc,
            "incarnon_note": ("灵化进化后面板（已录入游戏实装数据）" if inc
                              else "本仓库未收录灵化进化后面板，故灵化列标「未收录」"),
        },
        "kill_eff": ke,
        "market": market,
        "market_err": market_err,
        "notes": notes,
        "source": "Warframe.Market 挂牌分层 + 本仓库 DPS 模型",
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }


def recommend_text(data: dict) -> str:
    """纯文本降级（卡片渲染失败时用），口径与卡片一致，绝不静默。"""
    if data.get("status") != "ok":
        return data.get("msg") or "未收录该武器"
    lines = ["紫卡洗卡词条推荐 · " + str(data.get("weapon"))
             + ("（灵化）" if data.get("is_incarnon") else "")]
    p = (data.get("panel") or {}).get("normal") or {}
    lines.append("基础面板：暴击%s / 暴伤%s / 触发%s / 射速%s / 弹丸%s / 基伤%s（%s）" % (
        p.get("cc"), p.get("cm"), p.get("status"), p.get("fr"), p.get("pellets"),
        p.get("base"), p.get("comp")))
    inc = (data.get("panel") or {}).get("incarnon")
    if inc:
        lines.append("灵化面板：暴击%s / 暴伤%s / 触发%s / 射速%s / 弹丸%s / 基伤%s（%s）" % (
            inc.get("cc"), inc.get("cm"), inc.get("status"), inc.get("fr"),
            inc.get("pellets"), inc.get("base"), inc.get("comp")))
    ke = data.get("kill_eff") or {}
    if ke.get("pos"):
        lines.append("【击杀效率·正面】" + " / ".join(
            "%s +%.1f%%" % (x["zh"], x["gain"]) for x in ke["pos"]))
    elif ke.get("na"):
        lines.append("【击杀效率】" + str(ke["na"]))
    if ke.get("neg"):
        lines.append("【击杀效率·推荐负面】" + " / ".join(ke["neg"]))
    mk = data.get("market") or {}
    if mk.get("ok"):
        lines.append("市场分层（高价层 %d 条 / 中位 %sp · 底价层 %d 条 / 中位 %sp）；"
                     "数值=该词条在该层挂牌中的出现率" % (
                         mk.get("n_hi", 0), _fmt_num(mk.get("med_hi", 0)),
                         mk.get("n_lo", 0), _fmt_num(mk.get("med_lo", 0))))
        if mk.get("mpos"):
            lines.append("【正面·高价/底价】" + " / ".join(
                "[%s]%s %d%% / %d%%" % (x["tier"], x["zh"], x["hi"], x["lo"])
                for x in mk["mpos"][:8]))
        if mk.get("rec_neg"):
            lines.append("【推荐负面·高价/底价】" + " / ".join(
                "-%s %d%%/%d%%" % (x["zh"], x["hi"], x["lo"]) for x in mk["rec_neg"]))
        if mk.get("deadly"):
            lines.append("【致命项·高价/底价】" + " / ".join(
                "%s%s %d%%/%d%%" % (x["sign"], x["zh"], x["hi"], x["lo"])
                for x in mk["deadly"][:8]))
    else:
        lines.append("市场分层：" + (data.get("market_err") or "暂无行情"))
    lines.append("（击杀效率=满 roll 边际 DPS 收益（模型估算）；市场维度=挂牌分层出现率，"
                 "高价层只统计词条频次、价格不入算；融合元素在 WFM 拍卖属性里无独立字段，"
                 "故只出现在击杀效率维度）")
    return "\n".join(lines)


if __name__ == "__main__":
    import json

    async def _t():
        for q in ("凤殁", "rubico"):
            d = await recommend_data(q)
            print("====", q, "====")
            print(json.dumps(d, ensure_ascii=False, indent=1)[:900])
            m = d.get("market") or {}
            if m.get("ok"):
                print("  slices: hi=%d(med %s) lo=%d(med %s)" % (
                    m["n_hi"], _fmt_num(m["med_hi"]), m["n_lo"], _fmt_num(m["med_lo"])))
                print("  mpos:", [(x["zh"], x["tier"], x["hi"], x["lo"]) for x in m["mpos"][:8]])
                print("  rec_neg:", [(x["zh"], x["hi"], x["lo"]) for x in m["rec_neg"]])
                print("  deadly:", [(x["sign"], x["zh"], x["hi"], x["lo"]) for x in m["deadly"][:6]])
    asyncio.run(_t())
