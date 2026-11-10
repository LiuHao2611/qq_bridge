# -*- coding: utf-8 -*-
"""Warframe（星际战甲，国际服）攻略查询模块。

设计来源（搬运 / 借鉴，2026-09 检索案例）：
  * 功能清单与命令设计：TWGBot（github.com/KingPrimes/TWGBot，QQ 机器人，
    突击/入侵/奸商/仲裁/每日特惠/裂隙 + Warframe.Market 价格查询 + 灰机 Wiki 搜索）；
  * 世界状态数据源：Tenno Tools（api.tenno.tools，开源社区 API，等价于
    WarframeStat.us 的 JSON，本机实测 200；支持按需换 WarframeStat.us 地址）；
  * 价格数据源：Warframe.Market（api.warframemarket.com v1 orders，本机实测 200，
    搜索接口被 Cloudflare 风控 → 内置「中文别名 → url_name」映射，同 TWGBot /wm 设计）；
  * 中文资料：灰机 Wiki（warframe.huijiwiki.com，MediaWiki 搜索拼接，同 TWGBot /WIKI）。

对外只提供 async 文本函数，由 game_tools.dispatch 分发，回复走攻略富卡片。
worldstate 进程内缓存 60s（社区 API 约定：缓存友好，避免打爆公共源）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from nonebot.log import logger

#: 世界状态数据源（可被 .env WF_BASE_URL 覆盖；pc = 国际服 PC 平台）
WORLDSTATE_URL = "https://api.tenno.tools/worldstate/pc"
#: Warframe.Market 价格源（可被 .env WF_MARKET_BASE 覆盖）
MARKET_BASE = "https://api.warframe.market"
#: 灰机 Wiki 搜索（MediaWiki index.php?search= 直达结果页）
WIKI_SEARCH = "https://warframe.huijiwiki.com/w/index.php?search="

#: worldstate 缓存 TTL（秒）。Tenno Tools 数据约每 1~5 分钟刷新一次，
# 120s 足够新又足够省：即使同时发多个状态口令，也只 2 分钟打一次公共源。
_CACHE_TTL = 120
_cache: dict[str, Any] = {"ts": 0.0, "data": None}

#: 高频轮换类（裂隙/仲裁/平原昼夜/九重天风暴）独立快缓存 TTL。
# 玩家靠剩余时间/轮换状态决策，120s 缓存最多滞后 7 分钟（数据源 1~5 分钟刷新）
# 明显不够新鲜；这些场景单独用 20s。仅当两次人工查询间隔 <20s 才会重复打源
# （不是轮询），单飞锁+1s 全局限速保持，外呼总量几乎不变。
_CACHE_TTL_FAST = 20
_fast_cache: dict[str, Any] = {"ts": 0.0, "data": None}

#: 请求超时 / 重试
# 注意：外部社区 API（Tenno Tools / Warframe.Market）都有反爬或限流，
# 重试宁少勿多、退避宁长勿短——打太勤被拉黑就全瘫了。
_TIMEOUT = 8.0
_RETRY = 2

#: 市价查询专用超时/重试（WM 有 Cloudflare 风控，慢/被限流时极易挂起）。
# 单请求更短超时 + 只重试 1 次，配合 market_text 的「整体硬超时」，
# 让 WM 不可达时几秒内优雅降级为「暂时没响应」，而不是空等十几秒把整条命令
# 拖到 NapCat 动作超时（表现为「返回超时」）。
_MARKET_TIMEOUT = 5.0
_MARKET_RETRY = 1
#: 市价查询整体硬上限：orders + statistics + 英文名 并发拉取的封顶。
MARKET_HARD_TIMEOUT = 12.0

#: 全局请求节流：相邻两次外部 API 请求的最小间隔（秒）。
# 多个状态查询共享同一个 worldstate 端点，靠 _worldstate 单飞锁只发一次；
# 这个闸再兜底所有外部请求（价格/紫卡等）的频率。
_MIN_INTERVAL = 1.0

#: worldstate 单飞锁：缓存过期时并发查询只允许一个真正发 HTTP，其余等锁后读缓存
_wf_lock = asyncio.Lock()
#: 全局限速闸（上一次外部请求发出的时刻）
_req_gate: dict[str, float] = {"ts": 0.0}

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}

#: Warframe.Market 请求头（该站有 Cloudflare 风控，用完整浏览器特征提高放行率）
MARKET_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Origin": "https://warframe.market",
    "Referer": "https://warframe.market/",
}

# ===========================================================================
# 中文别名 → Warframe.Market url_name（搬运 TWGBot /wm 用例 + 常用高频词）
# 找不到别名的，直接按原词当 url_name 试（支持英文/拼音/完整 url_name）。
# ===========================================================================
ITEM_ALIASES: dict[str, str] = {
    # 充沛（赋能）/ 常用赋能
    "充沛": "arcane_energize", "能量充沛": "arcane_energize", "充沛赋能": "arcane_energize",
    "电男": "volt_prime_set", "电妹": "gyre_prime_set",
    # 武器（TWGBot /WM /WR /CD /XT 用例）
    "沙皇": "kuva_zarr", "赤毒沙皇": "kuva_zarr",
    "绝路": "rubico_prime",
    "绝路_prime_枪管": "rubico_prime_barrel",
    "绝路_prime_枪机": "rubico_prime_receiver",
    "绝路_prime_枪托": "rubico_prime_stock",
    "翠雀": "larkspur_prime",
    "翠雀_prime": "larkspur_prime",
    "翠雀_prime_枪管": "larkspur_prime_barrel",
    "翠雀_prime_枪机": "larkspur_prime_receiver",
    "翠雀_prime_枪托": "larkspur_prime_stock",
    "龙骑兵": "drakgoon",
    "雷克斯": "lex_prime",
    "拉托": "lato",
    "弧电": "tenet_arc", "信条弧电": "tenet_arc",
    "波波沙": "kuva_brahma", "赤毒波波沙": "kuva_brahma",
    "诸葛连弩": "zhuge_prime", "诸葛连弩p": "zhuge_prime",
    "月神": "soma_prime",
    "圣装月神": "soma_prime",
    "救赎者": "redeemer_prime",
    "救赎者p": "redeemer_prime",
    "皇家拐刃": "glaive_prime",
    "战刃p": "glaive_prime",
    "战刃": "glaive",
    "布拉玛": "kuva_brahma",
    # 常用 MOD
    "压迫点": "pressure_point",
    "膛线": "serration",
    "黄蜂蜇刺": "hornet_strike",
    "关键延迟": "critical_delay",
    "致命一击": "point_strike",
    "双重连击": "split_chamber",
    "分裂膛室": "split_chamber",
    "飞驰的专业": "fleeting_expertise",
    "弹指瞬技": "fleeting_expertise",
    "瞬时坚毅": "transient_fortitude",
    "盲怒": "blind_rage",
    "暗影滋养": "nourish",
    # 常用战甲（TWGBot /wm 牛、/wm ash 用例）
    "牛": "rhino_prime_set", "犀牛": "rhino_prime_set", "犀牛p": "rhino_prime_set",
    "ash": "ash_prime_set", "ashp": "ash_prime_set",
    "摸尸": "nekros_prime_set", "亡灵梦魇": "nekros_prime_set", "奶妈": "trinity_prime_set",
    "双倍经验": "affinity_booster",
    "猴子": "wukong_prime_set", "悟空": "wukong_prime_set",
    "齐天大圣": "wukong_prime_set", "猴哥": "wukong_prime_set",
    "女枪": "mesa_prime_set", "麦沙": "mesa_prime_set", "枪女": "mesa_prime_set",
    "毒妈": "saryn_prime_set", "蛆": "nidus_prime_set", "毒妈p": "saryn_prime_set",
    "蝶妹": "titania_prime_set", "花": "wisp_prime_set", "蝴蝶": "titania_prime_set",
    "奶妈": "trinity_prime_set", "摸尸": "nekros_prime_set", "牛p": "rhino_prime_set",
    "弓妹": "ivara_prime_set", "女猎手": "ivara_prime_set",
    "龙甲": "chroma_prime_set", "龙": "chroma_prime_set",
    "玻璃": "gara_prime_set", "玻璃妹": "gara_prime_set",
    "电哥": "volt_prime_set", "电姐": "gyre_prime_set",
    "手术": "grendel_prime_set", "大胃王": "grendel_prime_set",
    "女武神": "valkyr_prime_set", "瓦喵": "valkyr_prime_set",
    "弓妹p": "ivara_prime_set", "蝶妹p": "titania_prime_set",
}

#: 中英对照翻译小词表（TWGBot「翻译」功能：有把握的常用词；其余走 wiki 搜索）
TRANSLATE_WORDS: dict[str, str] = {
    "充沛": "Energize（赋能）", "沙皇": "Kuva Zarr（赤毒沙皇）",
    "绝路": "Rubico Prime", "牛": "Rhino Prime", "拉托": "Lato",
    "龙骑兵": "Drakgoon", "雷克斯": "Lex Prime", "弧电": "Tenet Arc",
    "压迫点": "Pressure Point", "膛线": "Serration", "战刃": "Glaive",
    "月神": "Soma Prime", "布拉玛": "Kuva Bramma", "死亡之眼": "Vectis Prime",
}

# ===========================================================================
# 中英名/外号数据（官方 i18n 提取 + 社区黑话整理，离线内置）
#   zh_names.json：686 个战甲/武器 官方中英名（WFCD warframe-items i18n 提取）
#   aliases.json：96 个高频对象 多外号表（17173/NGA/B站 黑话整理）
# ===========================================================================
_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "warframe_items"

# ===========================================================================
# 集团卡（Augment MOD）：战甲 -> 对应技能强化卡（2026-09-16 新增）
# 数据源：Mods.json（isAugment + Warframe Mod）+ mod_full.json（中文名/满级效果）
# ===========================================================================
_AUG_MAP: dict | None = None
_NON_WF_COMPAT = {"warframe", "aura", "shade", "parazon", "melee", "nokko",
                  "companion", "sentinel", "archwing", "necramech", "kdrive", "amp"}


def _load_augments() -> dict:
    """战甲 -> 集团卡列表（键 = warframe_full 的 key；prime 同源匹配）。"""
    global _AUG_MAP
    if _AUG_MAP is not None:
        return _AUG_MAP
    import json as _json
    base = _DATA_DIR
    try:
        mods = _json.loads((base / "Mods.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 集团卡是增强项，拉不到不影响主功能
        _AUG_MAP = {}
        return _AUG_MAP
    mf = _load_cards()["mod"]["map"]
    wf = _load_cards()["wf"]["map"]
    m: dict = {}
    for x in mods:
        if not x.get("isAugment") or str(x.get("type") or "") != "Warframe Mod":
            continue
        c = str(x.get("compatName") or "").strip().lower()
        nm = str(x.get("name") or "").strip().lower()
        if not c or not nm or c in _NON_WF_COMPAT:
            continue
        ent = mf.get(nm)
        if not ent:
            continue
        for wk in wf:
            if wk == c or wk.startswith(c + " ") or wk.startswith(c + " prime"):
                m.setdefault(wk, []).append({
                    "zh": ent.get("zh") or nm,
                    "en": x.get("name"),
                    "fx": (ent.get("max") or [""])[0][:72],
                })
    _AUG_MAP = m
    return m


def _augments_for(wf_key: str) -> list:
    """取某战甲的集团卡列表（无则空）。"""
    return _load_augments().get(wf_key.lower()) or []
#: 懒加载缓存
_zh_map: dict | None = None            # en_lower -> {zh, cat, en}
_zh_lookup: dict | None = None         # zh -> [en_lower]（反向）
_aliases: dict | None = None           # en_lower -> {en, zh, aliases, cat}
_alias_lookup: dict | None = None      # 中文名/外号/英文 -> en_lower
_weapons: dict | None = None           # en_lower -> 武器面板+倾向


def _load_translate_data() -> None:
    """懒加载翻译数据（幂等）。数据文件缺失时静默降级为小词表模式。"""
    global _zh_map, _zh_lookup, _aliases, _alias_lookup
    if _zh_map is not None:
        return
    _zh_map, _zh_lookup, _aliases, _alias_lookup = {}, {}, {}, {}
    try:
        d = json.loads((_DATA_DIR / "zh_names.json").read_text(encoding="utf-8"))
        _zh_map = d.get("map") or {}
        _zh_lookup = d.get("zh2en") or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_zh_names_load_failed: {type(e).__name__}: {e}")
    try:
        _aliases = json.loads((_DATA_DIR / "aliases.json").read_text(encoding="utf-8"))
        for en_low, info in _aliases.items():
            _alias_lookup[en_low] = en_low
            _alias_lookup[(info.get("zh") or "").lower()] = en_low
            for a in info.get("aliases") or []:
                _alias_lookup[a.lower()] = en_low
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_aliases_load_failed: {type(e).__name__}: {e}")


# ===========================================================================
# 紫卡倾向：2026-10-22 Hotfix 44.1.3 起 DE 移除武器倾向差异，全部统一 1.2。
# 旧 0.5~1.55 倍率 / 1~5 星级 / 分档投资建议一律作废；旧倍率只作「改动前」
# 历史对照（来源：本地 Primary/Secondary/Melee.json 的 omegaAttenuation 快照）。
# 下面是全项目唯一的现行口径来源，任何地方不得再读表算倍率/星级。
# ===========================================================================
RIVEN_DISPOSITION = 1.2
RIVEN_DISPO_RETIRED = "2026-10-22"


def disp_change_text(before) -> str:
    """改动前倍率 → 涨跌文案，如「改动前 0.70 → 词条约 +71%」。取不到返回空串。"""
    try:
        old = float(before)
    except (TypeError, ValueError):
        return ""
    if old <= 0 or abs(old - RIVEN_DISPOSITION) < 1e-6:
        return ""
    pct = (RIVEN_DISPOSITION / old - 1.0) * 100.0
    return "改动前 %.2f → 词条约 %+d%%" % (old, int(round(pct)))


#: 旧文档：官方「倾向星级 ← 倍率」分档边界（2026-09-30 实测 585 件武器 tier↔omega 归纳）：
#: 1★ .50~.75 / 2★ .80~.85 / 3★ .90~1.10 / 4★ 1.14~1.30 / 5★ 1.31~1.55。
#: 取相邻档位之间的**中值**当边界，边界值漂移也不会串档。
# 旧分档边界 (0.775/0.875/1.12/1.305) 已随 44.1.3 删除；
# 现行值只有 RIVEN_DISPOSITION 一个，避免有人照抄旧常量重新引入星级。


def _tier_from_omega(omega=None) -> int:
    """[已废弃] 旧倍率→星级分档。现行全武器统一，恒返回 4。"""
    return 4


_RIVEN_MULT: dict | None = None


def _riven_mults() -> dict:
    """[历史数据] 懒加载 `data/wf_riven_weapons.json` 的旧倾向倍率（EN 名 → 倍率）。

    DE 于 2026-10-22 移除倾向差异后本表不再参与任何现行计算，只在
    `_load_weapons()` 里作为「改动前倍率」缺失时的兜底。
    历史：2026-09-30 曾以 warframe.market 口径校准过本表，用于当时的
    `倾向：●●○○○(0.85)` 卡片；该卡片口径已随 44.1.3 作废。
    """
    global _RIVEN_MULT
    if _RIVEN_MULT is not None:
        return _RIVEN_MULT
    _RIVEN_MULT = {}
    try:
        fp = Path(__file__).resolve().parents[4] / "data" / "wf_riven_weapons.json"
        for it in json.loads(fp.read_text(encoding="utf-8")):
            m = it.get("disposition")
            if not isinstance(m, (int, float)):
                continue
            m = float(m)
            en = ((it.get("i18n") or {}).get("en") or {}).get("name") or ""
            if en:
                _RIVEN_MULT[en.lower().strip()] = m
            slug = (it.get("slug") or "").replace("-", " ").replace("_", " ").strip()
            if slug:
                _RIVEN_MULT.setdefault(slug, m)
    except Exception as e:  # noqa: BLE001 — 倍率只是锦上添花，失败就用本地旧值
        logger.warning(f"wf_riven_mults_load_failed: {type(e).__name__}: {e}")
    return _RIVEN_MULT


def _load_weapons() -> None:
    """懒加载武器面板（Primary/Secondary/Melee）。

    现行紫卡倍率恒为 RIVEN_DISPOSITION(1.2)，不再读倾向表；
    `disp_before` 仅保存本地快照里的旧倍率，供「改动前 → 现在」对照展示。
    """
    global _weapons
    if _weapons is not None:
        return
    _weapons = {}
    _live = _riven_mults()  # 仅作旧倍率缺失时的历史兜底，不再参与现行口径
    for cat, fname in (("主武器", "Primary.json"), ("副武器", "Secondary.json"),
                        ("近战武器", "Melee.json")):
        try:
            items = json.loads((_DATA_DIR / fname).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wf_weapons_load_failed {fname}: {type(e).__name__}: {e}")
            continue
        for it in items:
            en = (it.get("name") or "").strip()
            if not en:
                continue
            dmg = it.get("damage") or {}
            # 改动前倍率（历史对照用）：本地快照优先，缺失才回落到同步缓存。
            # 现行倍率恒为 1.2，与此值无关。
            _before = it.get("omegaAttenuation")
            if _before is None:
                _before = _live.get(en.lower())
            try:
                _before = float(_before) if _before is not None else None
            except (TypeError, ValueError):
                _before = None
            _weapons[en.lower()] = {
                "en": en, "cat": cat, "type": it.get("type", ""),
                "imageName": it.get("imageName"),  # 官方武器图鉴图文件名（DE 资产，warframe_icons 缓存）
                # 兼容字段：现行统一 1.2，星级固定 4（旧分档里 1.2 对应 4 档）。
                # 严禁再从这些字段推导「哪把武器紫卡更强」——该差异已被 DE 移除。
                "disposition": 4,
                "omega": RIVEN_DISPOSITION,
                "disp_before": _before,
                "totalDamage": dmg.get("total") if isinstance(dmg, dict) else it.get("totalDamage"),
                "criticalChance": it.get("criticalChance"),
                "criticalMultiplier": it.get("criticalMultiplier"),
                "fireRate": it.get("fireRate"),
                "statusChance": it.get("statusChance"),
                "multishot": it.get("multishot"),
                "magazineSize": it.get("magazineSize"),
                "masteryReq": it.get("masteryReq"),
            }


def reset_warframe_caches() -> None:
    """同步数据更新后失效所有依赖静态 JSON 的内存缓存。

    wf_sync 拉取新武器/战甲/倾向倍率并替换本地 JSON 后调用——把懒加载缓存
    置 None，下次查询会重新从（已更新的）磁盘文件加载，无需重启进程。
    覆盖：武器表(_weapons)、倾向倍率(_RIVEN_MULT)、紫卡武器词典(_RIVEN_WEAPONS)、
    增幅 mod(_AUG_MAP)、战甲图(_IMG_MAP)。
    """
    global _weapons, _RIVEN_MULT, _RIVEN_WEAPONS, _AUG_MAP, _IMG_MAP
    _weapons = None
    _RIVEN_MULT = None
    _RIVEN_WEAPONS = None
    _AUG_MAP = None
    _IMG_MAP = None
    # 配卡抓取模块另有一份 Mods.json 索引（懒加载），同步新数据后一并失效
    try:
        from . import build_fetch as _bf
        _bf.reset_mod_index()
    except Exception:  # noqa: BLE001 — 该模块缺失/半初始化都不该影响主流程
        pass


def _resolve_weapon(q: str) -> dict | None:
    """按 外号/中文名/英文名 找武器（返回 _weapons 条目）。"""
    q = (q or "").strip()
    if not q:
        return None
    _load_translate_data()
    low = q.lower()
    # 1) 外号/中文名/英文 直查
    en_low = _alias_lookup.get(low)
    if not en_low and _zh_map is not None and low in _zh_map:
        en_low = low
    if en_low:
        _load_weapons()
        if _weapons and en_low in _weapons:
            return _weapons[en_low]
    # 2) 官方中文名反查
    if _zh_lookup is not None:
        for en in _zh_lookup.get(q, [])[:3]:
            _load_weapons()
            # 2026-09-30 修复：zh2en 里存的是**首字母大写**的英文名（"Amanata"），
            # 而 _weapons 的键统一小写 → 旧写法 `en in _weapons` 恒为假，
            # 导致「天薙刀」这类没进 aliases.json 的官方中文名一律查不到。
            _key = (en or "").lower()
            if _weapons and _key in _weapons:
                return _weapons[_key]
    # 3) 直接英文名
    _load_weapons()
    if _weapons and low in _weapons:
        return _weapons[low]
    # 4) 别名表里存在但非武器（战甲等）→ 返回 None 提示
    return None


def _disp_desc(v: float | None = None) -> str:
    """现行紫卡倍率口径（DE 2026-10-22 热修 44.1.3 起全武器统一 1.2）。

    旧实现按 1~5 星级给「冷门/热门」分档点评，已随倾向系统移除而作废；
    保留函数名只为兼容旧调用点，返回值不再随武器变化。
    """
    return "1.20（全武器统一，2026-10-22 起）"


#: 武器大类简称（卡片面板里显示，对齐参考卡的「近战 / 主武器 / 副武器」）
_CAT_SHORT = {"近战武器": "近战", "主武器": "主武器", "副武器": "副武器"}


def _weapon_zh(en: str) -> str:
    """武器官方中文名（离线 zh_names.json，零外呼）；查不到回落英文名。"""
    _load_translate_data()
    rec = (_zh_map or {}).get((en or "").lower())
    if rec and rec.get("zh"):
        return rec["zh"]
    try:
        return _wf_en2zh().get((en or "").lower()) or en
    except Exception:  # noqa: BLE001 — 中文名只是显示层，缺失不致命
        return en


def _disp_stars(info: dict | None = None) -> int:
    """[已废弃] 倾向星级。DE 移除倾向差异后恒返回 4（1.2 在旧分档的档位）。

    仅保留给尚未迁移的调用点，禁止用于投资建议或词条数值放大。
    """
    return 4


def _stars_str(stars: int, total: int = 5) -> str:
    """[已废弃] 纯文本点阵。倾向星级已移除，新文案不得再输出 ●○ 点阵。"""
    stars = max(0, min(total, int(stars or 0)))
    return "●" * stars + "○" * (total - stars)


def _now() -> int:
    return int(time.time())


def _fmt_dur(seconds: int) -> str:
    """秒 → 「Xd Yh Zm」/「Yh Zm」人类可读。"""
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}天{h}小时{m}分"
    if h:
        return f"{h}小时{m}分"
    return f"{m}分钟"


def _fmt_alert_dur(sec: int) -> str:
    """警报倒计时：参考卡「11天17小时 / 1小时6分 / 6分4秒」样式——只取最大的两个非零单位。

    例：11天16小时44分26秒 → 天+小时 →「11天16小时」；1小时15分33秒 →「1小时15分」；
    15分33秒 →「15分33秒」。与 Rabbit 参考卡字宽一致，避免长倒计时挤压节点文字。
    """
    sec = max(0, int(sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append((d, "天"))
    if h:
        parts.append((h, "小时"))
    if m:
        parts.append((m, "分"))
    if s:
        parts.append((s, "秒"))
    if not parts:
        return "0秒"
    parts = parts[:2]                      # 仅保留最大的两个单位
    return "".join(f"{v}{u}" for v, u in parts)


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _fmt_short(seconds: int) -> str:
    """紧凑倒计时（卡片单行用）：3660→「1时1分」、300→「5分」。"""
    s = max(0, int(seconds))
    d, rem = divmod(s, 86400)
    h, m = divmod(rem, 3600)
    m //= 60
    if d:
        return f"{d}天{h}时{m}分"
    if h:
        return f"{h}时{m}分"
    if m:
        return f"{m}分"
    return f"{s}秒"


# ===========================================================================
# 简体中文本地化（静态表，零网络请求）
# 译名来源：灰机 Wiki 官方简中（worldstate.wf.wiki 世界状态页，2026-09 实测）
# + 官方简中常用译名；未收录的星球/节点/任务保留英文原样。
# ===========================================================================
_LOCATION_ZH = {
    "Mercury": "水星", "Venus": "金星", "Earth": "地球", "Mars": "火星",
    "Jupiter": "木星", "Saturn": "土星", "Uranus": "天王星", "Neptune": "海王星",
    "Pluto": "冥王星", "Eris": "阋神星", "Sedna": "赛德娜", "Ceres": "谷神星",
    "Phobos": "火卫一", "Deimos": "火卫二", "Lua": "月球", "Void": "虚空",
    "Kuva Fortress": "赤毒要塞", "Zariman": "扎里曼号", "Orokin": "欧罗金",
    "Europa": "欧罗巴", "Cambion Drift": "魔胎之境", "Plains of Eidolon": "夜灵平原",
    "Orb Vallis": "奥布山谷", "Duviri": "双衍王境", "Proxima": "比邻星域",
    "Veil": "面纱",
}
#: 已知官方中文节点名（其余节点保留英文，中文圈玩家同样认）。
#: 来源：灰机Wiki 各星球 SolNode 内部名表（官方简中）+ warframe.wf.wiki 实时世界状态。
#: 与官方客户端一致：无官方译名的节点（Kiste/Dione/Armaros/Naeglar/Pago/Taveuni …）
#: 保留英文原样，强行音译反而错。SolNode 英文↔中文经 WFCD solNodes.json 核对。
_NODE_ZH = {
    # 扎里曼号
    "Tuvul Commons": "涂沃主厅",
    # 虚空（13 座 Orokin 塔，官方简中对照 SolNode400-412）
    "Teshub": "神王塔", "Oxomoco": "母神塔", "Mot": "死灵塔",
    "Hepit": "神后塔", "Taranis": "雷争塔", "Tiwaz": "法神塔",
    "Stribog": "风王塔", "Ani": "阿尼塔", "Ukko": "乌戈塔",
    "Belenus": "光神塔", "Aten": "太阳塔", "Marduk": "守护神塔",
    "Mithra": "光理塔",
    # 火星（赫拉斯撞击坑，SolNode58）
    "Hellas": "赫拉斯撞击坑",
    # 圣所 / 扎里曼弧形区（官方简中）
    "Everview Arc": "永视弧域", "Overview Arc": "永视弧域",
}
_MISSION_ZH = {
    "Survival": "生存", "Defense": "防御", "Mobile Defense": "移动防御",
    "Interception": "拦截", "Sabotage": "破坏", "Rescue": "救援",
    "Exterminate": "歼灭", "Extermination": "歼灭", "Spy": "间谍", "Capture": "捕获",
    "Excavation": "挖掘", "Disruption": "中断", "Assault": "强袭",
    "Void Cascade": "虚空覆涌", "Void Flood": "虚空洪流",
    "Volatile": "爆发", "Skirmish": "前哨战", "Conjunction Survival": "结合生存",
    "Void Armageddon": "虚空审判", "Netracell": "网络细胞", "Alchemy": "炼金",
    "Mirror Defense": "镜像防御", "Ascension": "升天", "Archon Hunt": "执政官狩猎",
    "Hijack": "劫持", "Defection": "叛逃", "Salvage": "资源回收",
    "Index": "指数之场", "Arena": "竞技场", "Rathuum": "拉图姆",
    "Hive": "清巢",
}
#: 遗物纪元（官方简中）：古纪 Lith / 前纪 Meso / 中纪 Neo / 后纪 Axi / 安魂 Requiem / 全能 Omnia
_TIER_ZH = {
    "Lith": "Lith 古纪", "Meso": "Meso 前纪", "Neo": "Neo 中纪",
    "Axi": "Axi 后纪", "Requiem": "Requiem 安魂", "Omnia": "Omnia 全能",
}
_FACTION_ZH = {
    "Grineer": "克隆尼", "Corpus": "科普斯", "Infested": "感染者",
    "Infestation": "感染者", "Orokin": "欧罗金", "Sentient": "心智者",
    "Tenno": "天诺", "Mutalist": "突变体",
}

#: 警报卡片：阵营简称（X系）+ 派系色（胶囊边框/文字随派系），与参考卡「G系防御 / I系歼灭」一致
_ALERT_FACTION_SHORT = {
    "Grineer": "G系", "Corpus": "C系", "Infestation": "I系", "Infested": "I系",
    "Orokin": "O系", "Sentient": "S系", "Tenno": "T系", "Mutalist": "M系",
}
_ALERT_FACTION_COLOR = {
    "Grineer": "#D35400", "Corpus": "#2E86C1", "Infestation": "#6C3483",
    "Infested": "#6C3483", "Orokin": "#B7950B", "Sentient": "#117A65",
    "Tenno": "#C0392B", "Mutalist": "#8E44AD",
}
#: 警报奖励物品常见中文社区译名（参考卡用「月华腩」而非 Moonflesh；其余保留英文原样）
_ALERT_ITEM_ZH = {
    "Moonflesh": "月华腩", "Reactant": "反应堆物质", "Fieldron": "场离子",
    "Detonite Injector": "爆裂注射剂", "Mutagen Mass": "诱变团块",
    "Tellurium": "碲", "Argon Crystal": "氩结晶", "Neural Sensor": "神经传感器",
    "Orokin Cell": "欧罗金细胞", "Nano Spores": "纳米孢子", "Alloy Plate": "合金板",
    "Control Module": "控制模块", "Gallium": "镓", "Morphics": "形变元件",
    "Neurodes": "神经节点", "Plastids": "原生质", "Rubedo": "红晶",
    "Salvage": "回收物", "Polymer Bundle": "聚合物束", "Circuits": "电路",
    "Ferrite": "铁氧体", "Cryotic": "冷冻液", "Oxium": "氧馏物",
    "Antitoxin": "抗毒剂", "Bait": "诱饵", "Kuva": "赤毒", "Endo": "内融核心",
    "Credits": "现金", "Ducats": "杜卡德金币", "Ayatan Anasa Sculpture": "阿耶坦·阿纳萨塑像",
    "Ayatan Amber Star": "阿耶檀识琥珀星", "Ayatan Cyan Star": "阿耶檀识青蓝星",
    "Nitain Extract": "硝基提取物", "Orokin Catalyst Blueprint": "Orokin 催化剂蓝图",
    "Orokin Reactor Blueprint": "Orokin 反应堆蓝图", "Exilus Warframe Adapter": "延展槽适配器",
    "Forma": "福马", "Relic": "遗物",
}

_BOSS_ZH = {
    "Mutalist Alad V": "突变体阿拉德五世", "Alad V": "阿拉德五世",
    "Lephantis": "利惑者", "Vay Hek": "维克", "Kela De Thaym": "凯拉·德·塞姆",
    "Sargas Ruk": "萨加斯·鲁克", "Lech Kril": "莱彻·克里尔", "Phorid": "弗利德",
    "Raptor": "猛禽", "Hyena Pack": "鬣狗群", "Jackal": "豺狼", "Ambulas": "安布拉斯",
    "Corrupted Vor": "腐化者·沃尔", "Captain Vor": "沃尔上尉", "Vor": "沃尔上尉",
    "Tyl Regor": "泰尔·雷戈", "The Sergeant": "中士",
    "The Ropalolyst": "罗帕洛里斯特", "Exploiter Orb": "剥削者圆蛛",
    "Profit-Taker": "利润收割者", "Zealoid Prelate": "狂热主教",
    "Necramech": "殁世机甲", "Razorback": "剃刀背",
    "Balor Fomorian": "巴罗尔福摩里安", "Fomorian": "福摩里安",
    "Councilor Vay Hek": "顾问维克", "Kril": "克里尔",
}
#: 奖励/物品常见词：有序子串替换（长词在前，防部分匹配）
_REWARD_ZH = [
    ("Orokin Catalyst Blueprint", "Orokin 催化剂蓝图"),
    ("Orokin Reactor Blueprint", "Orokin 反应堆蓝图"),
    ("Exilus Warframe Adapter", "延展槽适配器"),
    ("Ayatan Anasa Sculpture", "阿耶坦·阿纳萨塑像"),
    ("Ayatan Amber Star", "阿耶檀识琥珀星"),
    ("Ayatan Cyan Star", "阿耶檀识青蓝星"),
    ("Nitain Extract", "硝基提取物"),
    ("Orokin Cell", "欧罗金细胞"),
    ("Neural Sensors", "神经传感器"),
    ("Neural Sensor", "神经传感器"),
    ("Relic", "遗物"),
    ("Blueprint", "蓝图"),
    ("Forma", "福马"),
    ("Endo", "内融核心"),
    ("Credits", "现金"),
    ("Ducats", "杜卡德金币"),
    ("Kuva", "赤毒"),
    ("Relay", "中继站"),
    ("Bronco", "布朗科"),
]


def _zh_text(s: str) -> str:
    """常见物品/奖励词简体中文化（有序子串替换，未收录保留原文）。"""
    if not s:
        return s
    out = s
    for en, zh in _REWARD_ZH:
        out = out.replace(en, zh)
    return out


def _zh_loc(loc: str) -> str:
    """「星球/节点」→「星球中文/节点」；已知官方中文节点一并替换。"""
    if not loc:
        return loc
    loc = loc.strip()
    # 比邻星域（九重天）："Earth Proxima" → "地球比邻星域"
    if loc.endswith(" Proxima") or loc == "Proxima":
        head = loc[:-len(" Proxima")].strip() if loc != "Proxima" else ""
        h = _LOCATION_ZH.get(head, head)
        return f"{h}比邻星域"
    head, sep, node = loc.partition("/")
    h = _LOCATION_ZH.get(head.strip())
    if h:
        loc = f"{h}/{node.strip()}" if node else h
    for en, zh in _NODE_ZH.items():
        loc = loc.replace(en, zh)
    return loc


def _zh_mission(t: str) -> str:
    return _MISSION_ZH.get(t, t)


def _zh_tier(t: str) -> str:
    return _TIER_ZH.get(t, t)


def _zh_faction(f: str) -> str:
    return _FACTION_ZH.get(f, f)


def _zh_boss(b: str) -> str:
    return _BOSS_ZH.get(b, b)


# ===========================================================================
# 世界状态
# ===========================================================================
async def _rate_gate() -> None:
    """全局限速：相邻两次外部请求间隔 >= _MIN_INTERVAL，防短时间连打被拉黑。"""
    loop = asyncio.get_event_loop()
    now = loop.time()
    wait = _req_gate["ts"] + _MIN_INTERVAL - now
    if wait > 0:
        await asyncio.sleep(wait)
    _req_gate["ts"] = loop.time()


async def _fetch(url: str, *, verify_ssl: bool = True, retry: int = _RETRY,
                 headers: dict | None = None, timeout: float = _TIMEOUT) -> Any | None:
    """GET JSON，失败按指数退避重试 retry 次，全败返回 None（不抛异常，防拖垮消息循环）。"""
    await _rate_gate()
    hdrs = dict(UA)
    if headers:
        hdrs.update(headers)
    for i in range(retry):
        try:
            async with httpx.AsyncClient(
                headers=hdrs, follow_redirects=True, timeout=timeout, verify=verify_ssl,
            ) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wf_fetch_failed url={url[:60]} try={i + 1} err={type(e).__name__}: {e}")
            if i < retry - 1:
                await asyncio.sleep(1.0 * (2 ** i))  # 指数退避：1s、2s
    return None


# ===========================================================================
# 数据源「最后更新时间」记录：footer 标注的是数据自身更新时间（UTC），
# 不是机器人抓取时刻，更不是本机时间。取不到就留空（不冒充）。
# ===========================================================================
_SRC_TS: dict = {"sections": {}, "latest": "", "market": "", "local": "",
                  "epochs": {}}  # epochs: tag -> 原始 epoch，供相对时间换算


def _fmt_utc(ts) -> str:
    """秒级 epoch（数据源时间均为 UTC）→ 'MM-DD HH:MM UTC'。"""
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%m-%d %H:%M UTC")
    except Exception:
        return ""


def _src_time(tag: str) -> str:
    """按板块取数据最后更新时间（sections 精确优先，latest 兜底），
    附相对时间（如「09-16 15:30 UTC（2 分钟前）」）——让新鲜度一目了然。
    相对时间只是换算，平台时间戳仍是主体，不冒充。"""
    if tag == "latest":
        s = _SRC_TS.get("latest") or ""
    elif tag == "market":
        s = _SRC_TS.get("market") or ""
    elif tag == "local":
        s = _SRC_TS.get("local") or ""
    else:
        s = (_SRC_TS.get("sections") or {}).get(tag, "") or ""
    if s:
        ep = (_SRC_TS.get("epochs") or {}).get(tag)
        if ep:
            age = int(_now() - ep)
            if age < 60:
                s += "（刚刚）"
            elif age < 3600:
                s += "（%d 分钟前）" % max(1, age // 60)
            elif age < 86400:
                s += "（%d 小时前）" % (age // 3600)
            else:
                s += "（%d 天前）" % (age // 86400)
    return s


def _record_worldstate_ts(d: dict) -> None:
    """Tenno.Tools 每个 section 自带 time（秒级 UTC）= 该板块数据最后更新时间。"""
    secs = _SRC_TS.setdefault("sections", {})
    best = 0
    for k, v in (d or {}).items():
        if isinstance(v, dict) and isinstance(v.get("time"), (int, float)):
            t = int(v["time"])
            secs[k] = _fmt_utc(t)
            _SRC_TS.setdefault("epochs", {})[k] = t
            best = max(best, t)
    if best:
        _SRC_TS["latest"] = _fmt_utc(best)
        _SRC_TS.setdefault("epochs", {})["latest"] = best


def _record_market_ts(d: dict) -> None:
    """Warframe.Market 订单时间（v1 payload.orders.last_update 秒级 / v2 data.updatedAt ISO）取最新。"""
    try:
        orders = []
        if isinstance((d or {}).get("data"), list):
            orders = d["data"]
        else:
            orders = ((d or {}).get("payload") or {}).get("orders") or []
        ups = []
        for o in orders:
            lu = o.get("last_update")
            if lu:
                ups.append(int(lu))
            up = o.get("updatedAt")
            if isinstance(up, str):
                try:
                    from datetime import datetime
                    ups.append(int(datetime.fromisoformat(up.replace("Z", "+00:00")).timestamp()))
                except Exception:
                    pass
        if ups:
            _SRC_TS["market"] = _fmt_utc(max(ups))
            _SRC_TS.setdefault("epochs", {})["market"] = max(ups)
    except Exception:
        pass


def _record_local_ts() -> None:
    """本地静态数据 = 数据包构建时间（文件 mtime，本地时区）。"""
    try:
        import os
        import datetime as _dt
        mt = 0.0
        for _f in ("warframe_full.json", "mod_full.json", "arcane_full.json",
                   "zh_names.json", "aliases.json"):
            _p = _DATA_DIR / _f
            if _p.exists():
                mt = max(mt, os.path.getmtime(_p))
        if mt:
            _SRC_TS["local"] = _dt.datetime.fromtimestamp(mt).strftime("%m-%d %H:%M")
            _SRC_TS.setdefault("epochs", {})["local"] = mt
    except Exception:
        pass


async def _worldstate(fast: bool = False) -> dict | None:
    """带缓存 + 单飞锁的 worldstate。

    fast=True 用于高频轮换类（裂隙/仲裁/平原/九重天风暴），用 20s 快缓存；
    其余（突击/奸商/特惠/入侵/电波/赏金）保持 120s。
    拉新成功后两个缓存同步写入——数据永远一致，只是各自 TTL 判断。
    单飞：缓存过期瞬间若有多个协程同时进来，只允许第一个真正发 HTTP，
    其余等待后读缓存——避免缓存击穿打爆公共源。
    """
    async with _wf_lock:
        c = _fast_cache if fast else _cache
        ttl = _CACHE_TTL_FAST if fast else _CACHE_TTL
        if c["data"] and _now() - c["ts"] < ttl:
            return c["data"]
        d = await _fetch(WORLDSTATE_URL)
        if d:
            _cache["data"] = d
            _cache["ts"] = _now()
            _fast_cache["data"] = d
            _fast_cache["ts"] = _now()
            _record_worldstate_ts(d)
        return d


def _slice(data: dict, key: str) -> list:
    """Tenno Tools 的 {key: {time, data: [...]}} → data 列表。"""
    v = (data or {}).get(key)
    if isinstance(v, dict):
        lst = v.get("data") or []
        return lst if isinstance(lst, list) else []
    return []


# ------------------------- 各状态文本 -------------------------
async def sortie_text() -> str:
    d = await _worldstate()
    s = _slice(d, "sorties")
    if not s:
        return "今日突击数据暂时没拉到（社区数据源抽风），稍后再试～"
    s = s[0]
    lines = [f"阵营：{_zh_faction(s.get('faction', '?'))}  Boss：{_zh_boss(s.get('bossName', '?'))}"]
    rewards = s.get("rewards") or []
    if rewards:
        pool = []
        for tier in rewards[:3]:
            top = sorted(tier, key=lambda x: x.get("chance", 0), reverse=True)[:3]
            pool.append("、".join(
                f"{_zh_text(r.get('name', '?'))} {_fmt_pct(r.get('chance', 0))}" for r in top))
        lines.append("奖励池（按概率）：")
        for i, p in enumerate(pool, 1):
            lines.append(f"  第{i}阶段：{p}")
    end = int(s.get("end") or 0)
    if end:
        lines.append(f"剩余：{_fmt_dur(end - _now())}")
    return "\n".join(lines)


async def void_trader_text() -> str:
    d = await _worldstate()
    vt = _slice(d, "voidtraders")
    if not vt:
        return "虚空商人（奸商）数据暂时没拉到，稍后再试～"
    vt = vt[0]
    name = vt.get("name", "Baro Ki'Teer")
    loc = _zh_text(_zh_loc(vt.get("location", "?")))
    start, end = int(vt.get("start") or 0), int(vt.get("end") or 0)
    now = _now()
    lines = [f"奸商：{name}", f"地点：{loc}"]
    if vt.get("active"):
        lines.append(f"正在营业！剩余：{_fmt_dur(end - now)}")
    elif start > now:
        lines.append(f"还没来，距离抵达约 {_fmt_dur(start - now)}")
    else:
        lines.append("这轮已离开，等下一轮～")
    lines.append("小贴士：奸商每两周来一次，周五到周日；带够杜卡德金币（Ducats）")
    return "\n".join(lines)


async def daily_deals_text() -> str:
    d = await _worldstate()
    deals = _slice(d, "dailydeals")
    if not deals:
        return "每日特惠数据暂时没拉到，稍后再试～"
    lines = []
    for x in deals[:5]:
        item = x.get("item") or {}
        price, orig = int(x.get("price") or 0), int(x.get("originalPrice") or 0)
        disc = "" if not orig else f"（-{int((1 - price / orig) * 100)}%）"
        lines.append(f"{_zh_text(item.get('name', '?'))}：{price}白金{disc}"
                     f"｜剩余 {x.get('stock', '?')}")
    return "每日特惠（火星商城中继站）：\n" + "\n".join(lines)


async def fissures_text() -> str:
    d = await _worldstate(fast=True)  # 裂隙高频轮换，快缓存
    fs = _slice(d, "fissures")
    if not fs:
        return "虚空裂隙数据暂时没拉到，稍后再试～"
    by_tier: dict[str, list] = {}
    for f in fs:
        by_tier.setdefault(f.get("tier", "?"), []).append(f)
    order = ["Lith", "Meso", "Neo", "Axi", "Requiem"]
    lines = []

    def _one(f: dict) -> str:
        hard = "（钢）" if f.get("hard") else ""
        return (f"{_zh_loc(f.get('location', '?'))} {_zh_mission(f.get('missionType', '?'))}"
                f"{hard} {_fmt_short(int(f.get('end') or 0) - _now())}")

    for tier in order:
        items = by_tier.pop(tier, None)
        if not items:
            continue
        seg = [f"【{_zh_tier(tier)}】"]
        # 全部裂隙都显示，不砍条数；紧凑格式保证每行单行显示
        for f in items:
            seg.append("  " + _one(f))
        lines.append("\n".join(seg))
    for tier, items in by_tier.items():
        if items:
            seg = [f"【{_zh_tier(tier)}】"]
            for f in items:
                seg.append("  " + _one(f))
            lines.append("\n".join(seg))
    if not lines:
        return "当前没有活跃裂隙～"
    return "虚空裂隙（选遗物对应的纪元开刷）：\n" + "\n\n".join(lines)


async def invasions_text() -> str:
    d = await _worldstate()
    invs = _slice(d, "invasions")
    if not invs:
        return "入侵数据暂时没拉到，稍后再试～"
    lines = []
    for x in invs[:8]:
        score = int(x.get("score") or 0)
        end = int(x.get("endScore") or 1)
        pct = min(100, abs(score) / end * 100)
        side = "克隆尼占优" if score < 0 else "科普斯占优"
        lines.append(f"{_zh_loc(x.get('location', '?'))}：{pct:.0f}%（{side}）")
    if not lines:
        return "当前没有进行中的入侵～"
    return "入侵（选奖励好的一边打，进度推进即得奖励）：\n" + "\n".join(lines)


async def arbitration_text() -> str:
    # 优先官方 worldstate：Tenno.Tools 的 `arbitrations` 板块自 2026-04-11 起停更
    # （section.time 定格 04-11、data 恒为空），见诊断报告。官方 api.warframe.com
    # 把仲裁放在 `SyndicateMissions` 的 `ArbitersSyndicate` 节点（实时、按小时轮换）。
    try:
        off = await _arbitration_text_official()
        if off:
            return off
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_arbitration_official_failed err={type(e).__name__}: {e}")
    # 兜底：Tenno.Tools（板块已停更，可能为空/旧数据）
    d = await _worldstate(fast=True)  # 仲裁每小时轮换，快缓存
    ar = _slice(d, "arbitrations")
    if not ar:
        return "当前没有仲裁任务（仲裁每半小时换一轮，稍后再试）"
    a = ar[0]
    loc = _zh_loc(a.get("location", "?"))
    enemy = _zh_faction(a.get("enemy", "?"))
    exp = int(a.get("expiry") or 0)
    txt = f"当前仲裁：{loc}（敌人：{enemy}）"
    if exp:
        txt += f"\n剩余：{_fmt_dur(exp - _now())}"
    return txt


# ===========================================================================
# 仲裁表（#wf 仲裁表 / 仲裁表）—— 仲裁轮换时间表（对照 Warframe Rabbit「仲裁时间表」）
# 数据：官方世界状态 ArbitersSyndicate（7 节点轮换池 + Seed）→ WFCD solNodes.json
#       解析节点名/派系/任务类型；效率/评级沿用社区 mission-type 基准（_ARB_EFFICIENCY）。
# 实时逐时循环：给出未来 N 小时班次（每页一个完整循环 = 7 班次）。
# 注意：当前国际服仲裁轮换池为 7 节点循环（与官方世界状态一致）；Rabbit 旧截图中
#       出现的 Malva/Titan/Callisto 等为早期更大轮换池数据，当前版本不再出现。
# ===========================================================================
_ARB_TABLE_CYCLES = 3          # 长图展示的循环周期数（= 3×7 = 21 班次，无分页）


def _arb_time_label(epoch_sec: int) -> str:
    """秒 → 「26日11时」（玩家本地 = UTC+8，对齐 Rabbit 卡片观感）。"""
    _bj = timezone(timedelta(hours=8))
    dt = datetime.fromtimestamp(int(epoch_sec), _bj)
    return f"{dt.day}日{dt.hour}时"


def _arb_cycle_idx(activation: int, now: int, n: int) -> int:
    """当前小时段在 n 节点轮换池中的索引（与 _arbitration_text_official 一致）。"""
    if activation:
        return int((now - activation) // 3600) % n
    return int(now // 3600) % n


async def arbitration_table_data(arg: str = "") -> dict:
    """仲裁时间表数据（长图：一次出完整轮换班次，不做分页）。

    返回 {title, rows:[{time,task,node,eff,tier}], footer, page, hint}。
    arg 保留兼容（"所有"/"all"/"全部"/"-N" 均等同默认，统一返回全量长图）。
    rows 为空表示数据未就绪。
    """
    arb = await _official_arbitration()
    if not arb:
        return {"title": "仲裁时间表", "rows": [], "footer": "当前没拉到仲裁数据，稍后再试～",
                "page": "", "hint": ""}
    sn = await _solnodes()
    nodes = arb["nodes"]
    n = len(nodes)
    if n == 0:
        return {"title": "仲裁时间表", "rows": [], "footer": "当前没有仲裁任务，稍后再试～",
                "page": "", "hint": ""}
    now = _now()
    activation = arb.get("activation") or 0
    idx = _arb_cycle_idx(activation, now, n)

    # 长图：默认展示全部循环班次（_ARB_TABLE_CYCLES × n 行），不分页
    span = n * _ARB_TABLE_CYCLES
    rows = []
    for h in range(span):
        i = (idx + h) % n
        info = _arb_info_from_solnodes(nodes[i], sn)
        eff, tier = _arb_efficiency(info["mission"], info["solnode"])
        rows.append({
            "time": _arb_time_label(now + h * 3600),
            "task": f"{info['system']}{_zh_mission(info['mission'])}{_arb_faction_letter(info['faction'])}系",
            "node": info["node"] or info["solnode"],
            "eff": eff,
            "tier": tier,
        })

    if not rows:
        footer = "当前没有仲裁任务，稍后再试～"
    else:
        footer = ("*生息效率为专业队近期实测均值（Vitus/小时），休闲玩家谨慎参考。")
    # 长图无分页：去掉页码与翻页提示
    hint = ""
    page = ""
    return {
        "title": "仲裁时间表",
        "rows": rows,
        "footer": footer,
        "page": page,
        "hint": hint,
    }


async def arbitration_table_text(arg: str = "") -> str:
    """仲裁时间表纯文本（#wf 仲裁表 兜底 / 卡片渲染失败降级）。

    与 arbitration_table_data 同源，输出对齐 Rabbit 的 5 列班次表。
    """
    d = await arbitration_table_data(arg)
    if not d.get("rows"):
        return d.get("footer") or "当前没有仲裁任务，稍后再试～"
    lines = ["【仲裁时间表】"]
    for r in d["rows"]:
        lines.append("· %s | %s | %s | %s | %s"
                     % (r["time"], r["task"], r["node"], r["eff"], r["tier"]))
    if d.get("footer"):
        lines.append(d["footer"])
    return "\n".join(lines)


async def nightwave_text() -> str:
    d = await _worldstate()
    chs = _slice(d, "challenges")
    if not chs:
        return "电波数据暂时没拉到，稍后再试～"
    c = chs[0]
    season = c.get("season", "?")
    lines = [f"当前电波赛季：第 {season} 季（Nora 的混音带）"]
    for ch in (c.get("challenges") or [])[:8]:
        daily = "（每日）" if ch.get("daily") else ""
        lines.append(f"· {ch.get('description', '?')}{daily} +{ch.get('xpAmount', 0)} 声望")
    return "\n".join(lines)


async def daynight_text(area: str = "") -> str:
    """平原昼夜：默认输出全部（希图斯/奥布山谷/地球野外），area 指定单个。"""
    d = await _worldstate(fast=True)  # 平原昼夜 100 分钟循环，快缓存
    dn = _slice(d, "daynight")
    if not dn:
        return "平原数据暂时没拉到，稍后再试～"
    now = _now()
    names = {"cetus": "夜灵平原（地球）", "fortuna": "奥布山谷（金星）", "earth": "地球野外"}
    hints = {"cetus": "白天刷赏金/挖矿钓鱼；夜晚刷夜灵、抓猫咪",
             "fortuna": "白天行动方便；夜晚严寒注意保暖",
             "earth": "白天安全些；夜晚敌人更凶"}
    out = []
    for x in dn:
        aid = x.get("id", "?")
        if area and aid != area:
            continue
        start = float(x.get("start") or 0)
        length = float(x.get("length") or 1)
        day_s = float(x.get("dayStart") or 0)
        day_e = float(x.get("dayEnd") or 0)
        t = (now - start) % length
        is_day = day_s <= t < day_e
        remain = (day_e - t) if is_day else (length - day_e + t)
        remain = remain if remain > 0 else length - t
        state = "白天" if is_day else "夜晚"
        out.append(f"{names.get(aid, aid)}：{state}，约 {_fmt_dur(remain)} 后切换｜{hints.get(aid, '')}")
    if not out:
        return f"没找到 {area} 的昼夜数据～"
    if not area:
        out.insert(0, "【平原状态 · 实时】")
        # 魔胎/双衍：优先用 wf.wiki 的真实周期，取不到才退回静态说明
        cyc = await _wfwiki_cycles()
        cam = (cyc or {}).get("cambion")
        if cam:
            out.append(f"· 魔胎之境（火卫二）：{cam['state']}，约 {cam['remain']} 后切换为 {cam['next']}")
        else:
            out.append("· 魔胎之境（火卫二）：Fass/Vome 约 4 小时轮换（数据源暂不可用，进游戏看）")
        dv = (cyc or {}).get("duviri")
        if dv:
            out.append(f"· 双衍王境：{dv['state']}，约 {dv['remain']} 后切换为 {dv['next']}")
        else:
            out.append("· 双衍王境：情绪约 3 小时轮换（数据源暂不可用，进游戏看）")
    return "\n".join(out)


# ===========================================================================
# 平原状态结构化数据（#平原 卡片）
# 地球&夜灵平野 / 福尔图娜 取自 Tenno.Tools 的 daynight（可靠）；
# 魔胎之境(Fass/Vome) / 双衍王境(情绪) 取 worldstate.wf.wiki 世界状态页的
# cycles 数组（api.warframestat.us 在部署环境稳定返回 403 不可用；wf.wiki
# 内嵌全部周期对象，expiry 为毫秒级 epoch）。取不到时优雅降级为「进游戏看」，
# 卡片仍逐像素完整渲染。
# ===========================================================================
_WFWIKI_CYC_CACHE: dict[str, Any] = {"ts": 0.0, "data": None, "fail_ts": 0.0}
_WFWIKI_CYC_TTL = 60

# 双衍王境情绪轮换顺序（wf.wiki cycles 固定循环）；下一情绪 = 当前后一位。
_DUVIRI_ORDER = ["joy", "anger", "envy", "sorrow", "fear"]
_DUVIRI_CN = {"joy": "欢愉", "anger": "愤怒", "envy": "嫉妒",
              "sorrow": "悲伤", "fear": "恐惧"}

# 魔胎之境轮换（wf.wiki state 为英文 Fass/Vome）；下一阶段 = 另一个。
_CAMBION_NEXT = {"fass": "Vome", "vome": "Fass"}


# 注：原 _wfstat_worldstate / _wfstat_cycles（抓 api.warframestat.us）已废弃——
# 该源在部署环境稳定返回 403 Forbidden。魔胎/双衍周期改由 _wfwiki_cycles()
# 从 worldstate.wf.wiki 的 cycles 数组解析（见下方）。原 _parse_wfstat_expiry
# 一并废弃：wf.wiki 的 expiry 为毫秒级 epoch，直接 //1000 - now 即得剩余秒。


def _parse_timestr(s: str) -> int:
    """'1h 4m 8s' / '4m 5s' → 秒。"""
    total = 0
    for val, unit in re.findall(r"(\d+)\s*([hms])", s):
        if unit == "h":
            total += int(val) * 3600
        elif unit == "m":
            total += int(val) * 60
        else:
            total += int(val)
    return total


def _fmt_dur_full(sec: int | None) -> str:
    """秒 → 中文「X小时Y分Z秒」/「Y分Z秒」/「Z秒」（带秒，对齐 Rabbit 倒计时胶囊）。"""
    if sec is None:
        return "进游戏看"
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


async def _wfwiki_cycles() -> dict | None:
    """魔胎之境 / 双衍王境 当前周期 → {cambion:{state,next,remain}, duviri:{...}} 或 None。

    取 worldstate.wf.wiki 世界状态页的 cycles 数组（self.__next_f.push 内嵌），
    按 id 取 cambion / duviri 两个周期对象；expiry 为毫秒级 epoch，
    remain = expiry//1000 - now。取不到（网络/结构变更）返回 None，绝不抛异常。
    """
    async with _wf_lock:
        c = _WFWIKI_CYC_CACHE
        if isinstance(c["data"], dict) and _now() - c["ts"] < _WFWIKI_CYC_TTL:
            return c["data"]
        if _now() - c.get("fail_ts", 0.0) < 60:
            return c["data"] if isinstance(c["data"], dict) else None
    try:
        html = await _wfwiki_html()
    except Exception:  # noqa: BLE001
        html = None
    out: dict[str, dict] = {}
    if html:
        cycles = _extract_flight_json(html, "cycles")
        if isinstance(cycles, list):
            by_id = {x.get("id"): x for x in cycles if isinstance(x, dict)}
            cam = by_id.get("cambion")
            if isinstance(cam, dict):
                sk = str(cam.get("stateKey") or "").lower()
                if sk in _CAMBION_NEXT:
                    out["cambion"] = {
                        "state": str(cam.get("state") or _CAMBION_NEXT[sk]),
                        "next": _CAMBION_NEXT[sk],
                        "remain": max(0, int(cam.get("expiry") or 0) // 1000 - _now()),
                    }
            dv = by_id.get("duviri")
            if isinstance(dv, dict):
                em = str(dv.get("stateKey") or "").lower()
                if em in _DUVIRI_CN:
                    idx = _DUVIRI_ORDER.index(em)
                    nxt = _DUVIRI_ORDER[(idx + 1) % len(_DUVIRI_ORDER)]
                    out["duviri"] = {
                        "state": _DUVIRI_CN[em],
                        "next": _DUVIRI_CN[nxt],
                        "remain": max(0, int(dv.get("expiry") or 0) // 1000 - _now()),
                    }
    async with _wf_lock:
        if out:
            _WFWIKI_CYC_CACHE.update({"ts": _now(), "data": out, "fail_ts": 0.0})
        else:
            _WFWIKI_CYC_CACHE["fail_ts"] = _now()
    return out or None


def _dn_phase(x: dict, now: int) -> tuple[bool, int]:
    """单条 daynight → (是否白昼, 距下一阶段剩余秒)。"""
    start = float(x.get("start") or 0)
    length = float(x.get("length") or 1)
    day_s = float(x.get("dayStart") or 0)
    day_e = float(x.get("dayEnd") or 0)
    t = (now - start) % length
    is_day = day_s <= t < day_e
    if is_day:
        remain = day_e - t
    else:
        # 距下一个白昼起点（warm）
        remain = (day_s - t) % length
    return is_day, max(0, int(remain))


def _mk_row(name: str, icon: str, state: str, kind: str, nxt: str, remain_s,
            planet: str | None = None) -> dict:
    return {"name": name, "icon": icon, "state": state, "kind": kind,
            "next": nxt, "remain": _fmt_dur_full(remain_s), "planet": planet}


async def daynight_data() -> dict:
    """平原状态结构化数据：4 行（地球&夜灵平野 / 福尔图娜 / 魔胎之境 / 双衍王境）。

    返回 {rows:[...], source:"..."}；rows 元素字段见 _mk_row。魔胎/双衍取不到时
    state='暂无'、remain='进游戏看'，版式不崩。
    """
    d = await _worldstate(fast=True)
    dn = _slice(d, "daynight") or []
    now = _now()
    rows: list[dict] = []

    cetus = next((x for x in dn if x.get("id") == "cetus"), None)
    if cetus:
        is_day, rem = _dn_phase(cetus, now)
        rows.append(_mk_row("地球&夜灵平野", "#2F6B57",
                            "白天" if is_day else "夜晚", "blue",
                            "夜晚" if is_day else "白天", rem, planet="earth"))
    else:
        rows.append(_mk_row("地球&夜灵平野", "#2F6B57", "暂无", "blue", "夜晚", None, planet="earth"))

    fortuna = next((x for x in dn if x.get("id") == "fortuna"), None)
    if fortuna:
        is_day, rem = _dn_phase(fortuna, now)
        # 奥布山谷：白昼=温暖，夜晚=寒冷；胶囊固定紫（对齐参考图蓝/紫/蓝/紫交替）
        rows.append(_mk_row("福尔图娜", "#6B4E9B",
                            "温暖" if is_day else "寒冷", "purple",
                            "寒冷" if is_day else "温暖", rem, planet="venus"))
    else:
        rows.append(_mk_row("福尔图娜", "#6B4E9B", "暂无", "purple", "温暖", None, planet="venus"))

    cyc = await _wfwiki_cycles()
    cam = (cyc or {}).get("cambion")
    if cam:
        rows.append(_mk_row("魔胎之境", "#8B3A2E", cam["state"], "blue",
                            cam["next"], cam["remain"], planet="deimos"))
    else:
        rows.append(_mk_row("魔胎之境", "#8B3A2E", "暂无", "blue", "Vome", None, planet="deimos"))

    dv = (cyc or {}).get("duviri")
    if dv:
        rows.append(_mk_row("双衍王境", "#4A3F8F", dv["state"], "purple",
                            dv["next"], dv["remain"], planet="duviri"))
    else:
        rows.append(_mk_row("双衍王境", "#4A3F8F", "暂无", "purple", "恐惧", None,
                            planet="duviri"))

    source = "Tenno.Tools + wf.wiki 世界状态" if cyc else "Tenno.Tools（魔胎/双衍进游戏看）"
    return {"rows": rows, "source": source}


# ===========================================================================
# 仲裁（#wf 仲裁）—— 数据源说明
# Tenno.Tools 的 `arbitrations` 板块自 2026-04-11 起停更（section.time 定格
# 04-11、data 恒为空）。官方 api.warframe.com 世界状态把仲裁放在
# `SyndicateMissions` 的 `ArbitersSyndicate` 节点：含 Activation/Expiry 与
# Nodes（SolNode ID 列表，按小时轮换）。但该世界状态不提供节点名/派系/任务
# 类型，故节点元信息改用 WFCD 的 solNodes.json（data/solNodes.json，本地缓存、
# 缺失时从 CDN 拉取并落盘）：每个 SolNode ID → {value:"Bode (Ceres)", enemy,
# type}。与裂隙翻译共用同一权威源，离线可用。
# 注意：wf.wiki 世界状态页已重构，不再内嵌 ArbitersSyndicate 节点池，旧
# `_extract_arb_pool` 解析路径已废弃。
# ===========================================================================
def _parse_mongo_date(v) -> int | None:
    """MongoDB 扩展 JSON 日期 → 秒级 epoch（None 表示取不到）。

    兼容：纯数字(ms/s)、{"$date":{"$numberLong":"..."}}、{"$date":<ms>}、
    字符串数字。>1e12 视为毫秒。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        return n // 1000 if n > 1_000_000_000_000 else n
    if isinstance(v, dict):
        inner = v.get("$date")
        if isinstance(inner, dict):
            inner = inner.get("$numberLong") or inner.get("sec") or inner.get("$numberDouble")
        if inner is not None:
            try:
                n = int(float(inner))
                return n // 1000 if n > 1_000_000_000_000 else n
            except (TypeError, ValueError):
                return None
    if isinstance(v, str):
        try:
            n = int(float(v))
            return n // 1000 if n > 1_000_000_000_000 else n
        except ValueError:
            return None
    return None


# WFCD solNodes.json：SolNode ID → {value:"Bode (Ceres)", enemy, type}。
# 官方世界状态只给 SolNode ID，节点名/派系/任务类型靠此表解析。
_SOLNODES_CACHE: dict | None = None
_SOLNODES_URL = ("https://cdn.jsdelivr.net/gh/WFCD/warframe-worldstate-data"
                 "@master/data/solNodes.json")


def _find_solnodes_file() -> Path | None:
    """从本模块向上最多 8 层查找 data/solNodes.json。"""
    p = Path(__file__).resolve().parent
    for _ in range(8):
        cand = p / "data" / "solNodes.json"
        if cand.exists():
            return cand
        p = p.parent
    return None


def _parse_solnode_value(value: str) -> tuple[str, str]:
    """'Bode (Ceres)' → ('Bode', 'Ceres')；无括号 → (value, '')。"""
    if not value:
        return ("", "")
    m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", value)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return (value.strip(), "")


async def _solnodes() -> dict:
    """加载 WFCD solNodes.json（内存缓存；本地缺文件时从 CDN 拉取并落盘）。"""
    global _SOLNODES_CACHE
    if _SOLNODES_CACHE is not None:
        return _SOLNODES_CACHE
    data: dict | None = None
    f = _find_solnodes_file()
    if f:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = None
    if not data:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r = await c.get(_SOLNODES_URL)
                if r.status_code == 200:
                    data = json.loads(r.text)
                    # 落盘到项目 data/（优先已存在 data 的目录）
                    save_dir = Path(__file__).resolve().parent
                    for _ in range(8):
                        d = save_dir / "data"
                        if d.exists() or save_dir.name == "qq_bridge":
                            d.mkdir(exist_ok=True)
                            (d / "solNodes.json").write_text(r.text, encoding="utf-8")
                            break
                        save_dir = save_dir.parent
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wf_solnodes_fetch_failed: {type(e).__name__}: {e}")
    _SOLNODES_CACHE = data or {}
    return _SOLNODES_CACHE


def _arb_info_from_solnodes(solnode: str, sn: dict) -> dict:
    """单个 SolNode ID → {system(中文), node(英文), faction(原始), mission(英文)}。"""
    rec = (sn or {}).get(solnode) or {}
    val = rec.get("value", "") if isinstance(rec, dict) else ""
    node_en, system_en = _parse_solnode_value(val)
    return {
        "solnode": solnode,
        "system": _LOCATION_ZH.get(system_en, system_en),
        "node": node_en or solnode,
        "faction": rec.get("enemy", "") if isinstance(rec, dict) else "",
        "mission": rec.get("type", "") if isinstance(rec, dict) else "",
    }


async def _official_arbitration() -> dict | None:
    """官方世界状态 ArbitersSyndicate → {nodes, activation, expiry, worldstate_time}。"""
    d = await _official_worldstate()
    if not d:
        return None
    for s in (d.get("SyndicateMissions") or []):
        tag = (s.get("Tag") or s.get("Syndicate") or "")
        if "arbit" in tag.lower():
            arb = s
            break
    else:
        return None
    expiry = _parse_mongo_date(arb.get("Expiry"))
    activation = _parse_mongo_date(arb.get("Activation"))
    nodes = arb.get("Nodes") or []
    if not nodes or not expiry:
        return None
    return {
        "nodes": nodes,
        "activation": activation or 0,
        "expiry": expiry,
        "worldstate_time": _parse_mongo_date(d.get("Time")),
    }


def _arb_fmt_hms(seconds: int) -> str:
    """秒 → 「X小时Y分Z秒」（仲裁倒计时格式，精确到秒）。"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


#: 仲裁任务类型 → 估算效率（生息精华/小时, 等级）。
# 数据来源：arbi.guide / 玩家社区 tierlist / 实测归纳；DE 调整任务池时可能漂移。
_ARB_EFFICIENCY: dict[str, tuple[int, str]] = {
    "Interception": (850, "S+"),      # 拦截：最高效，熟练队 800+/h
    "Defense": (650, "A+"),           # 防御：稳定 600+/h
    "Survival": (650, "A+"),          # 生存：稳定 600+/h
    "Mirror Defense": (620, "A+"),    # 镜像防御
    "Excavation": (550, "A"),         # 挖掘
    "Disruption": (550, "A"),         # 中断
    "Spy": (450, "B+"),               # 间谍
    "Rescue": (400, "B+"),            # 救援
    "Hijack": (400, "B+"),            # 劫持
    "Mobile Defense": (400, "B+"),    # 移动防御
    "Sabotage": (350, "B"),           # 破坏
    "Capture": (300, "C+"),           # 捕获：太快，精华少
    "Exterminate": (300, "C+"),       # 歼灭
    "Extermination": (300, "C+"),     # 歼灭（WFCD 数据写法变体）
}

#: 特定 SolNode 效率覆盖（DE 调整节点池时在此修正，保持类型基准兜底）。
_ARB_NODE_OVERRIDE: dict[str, tuple[int, str]] = {}


def _arb_efficiency(mission: str, solnode: str = "") -> tuple[int, str]:
    """返回仲裁节点估算效率（生息精华/小时, 等级）。

    wf.wiki 节点池的 mission 可能是中文（如「破坏」），也可能是英文，
    先统一归一化为英文再查效率表。
    """
    if solnode and solnode in _ARB_NODE_OVERRIDE:
        return _ARB_NODE_OVERRIDE[solnode]
    mission_en = mission
    for en, zh in _MISSION_ZH.items():
        if mission == zh or mission == en:
            mission_en = en
            break
    return _ARB_EFFICIENCY.get(mission_en, (300, "C+"))


def _arb_faction_letter(faction: str) -> str:
    return {"Grineer": "G", "Corpus": "C", "Infested": "I",
            "Infestation": "I", "Orokin": "O", "Corrupted": "O"}.get(faction, "?")


def _arb_current_line(system: str, node: str, faction: str, mission: str) -> str:
    """当前仲裁行：「木星Thebe | C系破坏」。"""
    loc = f"{system}{node}" if system else node
    return f"{loc} | {_arb_faction_letter(faction)}系{_zh_mission(mission)}"


def _arb_future_line(faction: str, system: str, mission: str,
                     value: int, tier: str) -> str:
    """后续仲裁行：「C系木星破坏（350|B）」。"""
    return f"{_arb_faction_letter(faction)}系{system}{_zh_mission(mission)}（{value}|{tier}）"


async def _arbitration_text_official() -> str | None:
    """官方 worldstate 仲裁富文本（None = 取不到，交给 Tenno.Tools 兜底）。

    复刻 Rabbit 仲裁卡：当前节点 + 产出效率 + 剩余时间，
    并给出「下个仲裁任务」与「下个高价值仲裁」及倒计时。
    """
    arb = await _official_arbitration()
    if not arb:
        return None
    sn = await _solnodes()
    nodes = arb["nodes"]
    n = len(nodes)
    now = _now()
    activation = arb.get("activation") or 0
    if activation:
        # 当前小时段索引；当前节点在该小时段结束时轮换
        idx = int((now - activation) // 3600) % n
        current_end = activation + ((now - activation) // 3600 + 1) * 3600
    else:
        idx = int(now // 3600) % n
        current_end = ((now // 3600) + 1) * 3600

    # 用 WFCD solNodes.json 把官方 SolNode ID 解析为 节点名/星球/派系/任务类型
    infos: list[dict] = [_arb_info_from_solnodes(s, sn) for s in nodes]

    cur = infos[idx]
    cur_val, cur_tier = _arb_efficiency(cur["mission"], cur["solnode"])
    cur_remain = max(0, current_end - now)

    # 记录数据源时间（官方 worldstate Time），供卡片 footer 标注
    wt = arb.get("worldstate_time")
    if wt:
        _SRC_TS.setdefault("sections", {})["arbitration_official"] = _fmt_utc(wt)
        _SRC_TS.setdefault("epochs", {})["arbitration_official"] = wt

    lines = ["仲裁无底洞如下："]
    lines.append(_arb_current_line(cur["system"], cur["node"],
                                   cur["faction"], cur["mission"]))
    lines.append(f"- 产出效率：{cur_val}({cur_tier})生息精华/小时")
    lines.append(f"- 剩余时间：{_arb_fmt_hms(cur_remain)}")

    if n > 1:
        nxt = infos[(idx + 1) % n]
        nxt_val, nxt_tier = _arb_efficiency(nxt["mission"], nxt["solnode"])
        lines.append(f"下个仲裁任务：{_arb_future_line(nxt['faction'], nxt['system'], nxt['mission'], nxt_val, nxt_tier)}")

    # 后续高价值节点：从 idx+1 开始找效率最高的（价值相同时取最近的）
    best: tuple[int, str, int] | None = None
    for offset in range(1, n):
        j = (idx + offset) % n
        v, t = _arb_efficiency(infos[j]["mission"], infos[j]["solnode"])
        if best is None or v > best[0]:
            best = (v, t, j)
    if best:
        best_v, best_t, best_j = best
        if best_j != (idx + 1) % n:
            b = infos[best_j]
            lines.append(f"下个高价值仲裁：{_arb_future_line(b['faction'], b['system'], b['mission'], best_v, best_t)}")
        best_end = current_end + ((best_j - idx) % n) * 3600
        best_remain = max(0, best_end - now)
        if best_remain > 0:
            lines.append(f"- 剩余时间：{_arb_fmt_hms(best_remain)}")

    return "\n".join(lines)


# ===========================================================================
# 赏金任务一览（#wf 赏金 / #wf 钢铁）
# 按 Rabbit「赏金任务一览」卡片复刻：赏金轮换（合一众/保险库/倒计时）
#   + 地球/金星/火卫二（每区等级最高 2 个任务，含合一众与钢铁之路）
#   + 深矿（钢铁之路专属）+ 扎里曼/圣所/六人组（派系任务）。
# 数据源：官方 worldstate（api.warframe.com，jobType/等级/Narmer/剩余时间，
#   实时权威） + wf.wiki（轮换字母/轮换奖励/保险库/MOD 奖励轮换/债券轮换）。
# 任务名映射：截图确认（破坏原型机/为了乌努母神/猎人杀手/邪主之音/
#   重夺领地/为了科学）+ orokinarchives dialogue-bounty 英文任务名直译。
# ===========================================================================
_WFWIKI_TTL = 120
_wfwiki_cache: dict = {"ts": 0.0, "html": None}

_OFFICIAL_URL = "https://api.warframe.com/cdn/worldState.php"
_OFFICIAL_TTL = 120
_official_cache: dict = {"ts": 0.0, "data": None}
#: 抓取失败后的冷却时间（秒）：源故障/被限流期间不反复打，冷却结束再试
_FAIL_COOLDOWN = 600

#: HTML 页面源（wf.wiki 等 Next.js/CDN 风控严）请求头：完整浏览器特征降低误杀率
_HTML_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Referer": "https://worldstate.wf.wiki/",
}

# jobType 尾名 → (任务名, 类型中文)。截图 6 个为玩家确认口径，
# 其余来自 orokinarchives.com/dialogue-bounty 英文任务名直译。
_BOUNTY_NAMES: dict[str, tuple[str, str]] = {
    # ---- 地球（夜灵平原 · 希图斯）----
    "RescueBountyResc": ("搜索与救援", "救援"),
    "CaptureBountyCapOne": ("捕获克隆尼指挥官", "捕获"),
    "CaptureBountyCapTwo": ("捕获新克隆尼指挥官", "捕获"),
    "AttritionBountyCap": ("削减敌人", "削减"),
    "AttritionBountySab": ("破坏克隆尼补给线", "破坏"),
    "AssassinateBountyCap": ("刺杀指挥官", "刺杀"),
    "AssassinateBountyAss": ("刺杀指挥官", "刺杀"),
    "SabotageBountySab": ("破坏原型机", "破坏"),
    "ReclamationBountyCache": ("寻找隐藏神器", "回收"),
    "ReclamationBountyTheft": ("回收被盗神器", "回收"),
    "ReclamationBountyCap": ("捕获他们的领袖", "捕获"),
    "AttritionBountyExt": ("为了乌努母神", "歼灭"),
    "AttritionBountyLib": ("带他们回家", "歼灭"),
    # ---- 金星（奥布山谷 · 福尔图娜）----
    "VenusCullJobExterminate": ("猎人杀手", "歼灭"),
    "VenusCullJobAssassinate": ("猎杀刺杀", "刺杀"),
    "VenusCullJobResource": ("猎杀资源", "捕获"),
    "VenusPreservationJobDefense": ("区域防御", "防御"),
    "VenusPreservationJobRecovery": ("文物回收", "回收"),
    "VenusArtifactJobExcavation": ("神器挖掘", "挖掘"),
    "VenusArtifactJobAmbush": ("神器伏击", "伏击"),
    "VenusArtifactJobRecovery": ("神器回收", "回收"),
    "VenusChaosJobExcavation": ("混乱挖掘", "挖掘"),
    "VenusChaosJobAssassinate": ("混乱刺杀", "刺杀"),
    "VenusTheftJobAmbush": ("信使伏击", "伏击"),
    "VenusTheftJobExcavation": ("盗窃挖掘", "挖掘"),
    "VenusTheftJobResource": ("盗窃资源", "回收"),
    "VenusIntelJobResource": ("情报资源", "回收"),
    "VenusIntelJobRecovery": ("情报回收", "回收"),
    "VenusHelpingJobCaches": ("援助清剿", "援助"),
    "VenusHelpingJobSpy": ("援助渗透", "间谍"),
    "VenusHelpingJobResource": ("尘土部队", "资源"),
    "VenusWetworkJobSpy": ("网络渗透", "间谍"),
    "VenusWetworkJobAssassinate": ("网络刺杀", "刺杀"),
    "NarmerVenusCullJobAssassinate": ("邪主之音", "刺杀"),
    "NarmerVenusTheftJobExcavation": ("掘地三尺", "挖掘"),
    "NarmerVenusArtifactJobAssassinate": ("粉碎邪教", "刺杀"),
    # ---- 火卫二（魔胎之境 · 殁世幽都）----
    "DeimosGrnSurvivorBounty": ("幸存者支援", "生存"),
    "DeimosCrpSurvivorBounty": ("为了科学", "生存"),
    "DeimosEndlessExcavateBounty": ("无尽挖掘", "挖掘"),
    "DeimosExcavateBounty": ("核心样本", "挖掘"),
    "DeimosPurifyBounty": ("净化大地", "净化"),
    "DeimosAreaDefenseBounty": ("重夺领地", "防御"),
    "DeimosAssassinateBounty": ("清净之地", "刺杀"),
    "DeimosKeyPiecesBounty": ("钥匙碎片", "回收"),
}

# 各派系赏金（截图确认）：(名称, 等级, 类型, 目标)。等级为当前轮换段，
# 暂无公开 API 精确追踪派系轮换，固定展示截图口径的最高级任务。
_BOUNTY_GROUPS: dict[str, list[tuple[str, str, str, str]]] = {
    "扎里曼赏金": [
        ("哈拉科防线", "60-65", "歼灭", "给梅利卡加油打气"),
        ("涂沃主厅", "90-95", "虚空覆涌", "窃取新力量"),
        ("骤径", "110-115", "移动防御", "保护控制台护盾"),
    ],
    "圣所赏金": [
        ("孽杀", "90-95", "歼灭", "清理房间"),
        ("恶涌", "110-115", "刺杀", "寂静无声"),
    ],
    "六人组赏金": [
        ("地狱净化：科腐者", "110-115", "生存", "能量超载"),
        ("地狱净化：科腐者", "", "生存", "天降正义"),
    ],
}

# 地球赏金池里的基础 MOD（低价值卡，Rabbit 卡不展示）
_BOUNTY_BASE_MODS = {"压迫点", "黄蜂蜇刺", "黄蜂螫刺", "延伸", "能量转化",
                     "灵敏扳机", "膛线", "致命一击", "分裂膛室", "永冻晶矿",
                     "非晶态合金", "葛克度"}
# 合一众（Narmer）专属奖励（非 MOD）
_BOUNTY_NARMER_ITEMS = ("枭主", "蛇主", "狼主", "塑讯块", "夜灵之息")
# MOD 展示顺序（对齐 Rabbit 卡：收割螺旋 → 预言 → 角斗士 → 私法 → 猎鹰）
_BOUNTY_MOD_ORDER = {"收割螺旋": 0, "预言": 10, "角斗士": 11, "私法": 12, "猎鹰": 20}

#: 赏金轮换奖励中文名 → 官方英文物品名（用于在本地 JSON 中查找 imageName）。
# 注：wf.wiki payload 的 icon 字段是内部路径，不便直接映射 CDN 图，故用中文名反查。
_BOUNTY_REWARD_ZH_EN: dict[str, str] = {
    "雷霆暴君": "Korumm",
    "孢丝感染枪": "Sporelacer",
}

#: 已加载的本地物品表（延迟加载，命中缓存）。
_WF_ITEM_CACHE: dict[str, Any] | None = None
#: 小写战甲英文名集合（识别赏金奖励中的战甲，决定右下角立绘）。
_WF_WARFRAME_SET: set | None = None


def _load_wf_items() -> dict[str, Any]:
    """加载本地 warframe_items/*.json，返回 {英文原名小写: item_dict}。"""
    global _WF_ITEM_CACHE
    if _WF_ITEM_CACHE is not None:
        return _WF_ITEM_CACHE
    base = Path(__file__).resolve().parents[4] / "data" / "warframe_items"
    out: dict[str, Any] = {}
    for fn in ("Warframes.json", "Primary.json", "Secondary.json", "Melee.json"):
        try:
            for it in json.loads((base / fn).read_text(encoding="utf-8")):
                name = (it.get("name") or "").strip()
                if name:
                    out[name.lower()] = it
        except Exception:  # noqa: BLE001
            continue
    _WF_ITEM_CACHE = out
    return out


def _wf_image_name(en_name: str) -> str | None:
    """根据英文物品名返回 imageName（用于 weapon_icons 缓存命中检查）。"""
    if not en_name:
        return None
    items = _load_wf_items()
    it = items.get(en_name.strip().lower())
    return it.get("imageName") if it else None


def _wf_warframe_set() -> set:
    """小写战甲英文名集合（用于识别赏金奖励中的战甲、决定右下角立绘）。"""
    global _WF_WARFRAME_SET
    if _WF_WARFRAME_SET is not None:
        return _WF_WARFRAME_SET
    base = Path(__file__).resolve().parents[4] / "data" / "warframe_items"
    s: set[str] = set()
    try:
        for it in json.loads((base / "Warframes.json").read_text(encoding="utf-8")):
            n = (it.get("name") or "").strip().lower()
            if n:
                s.add(n)
    except Exception:  # noqa: BLE001
        pass
    _WF_WARFRAME_SET = s
    return s


async def _bounty_download_icon(icon_url: str | None) -> str | None:
    """下载 wf.wiki 直链的奖励图标（DE 官方商店图标，与奖励一一对应）到本地缓存。

    返回缓存真实路径；失败（无 URL / 网络 / 非 PNG）返回 None。
    URL 形如 .../GenericWarframeHelmet.png!00_<hash>，去掉 !00_ 后缀即为原图地址。
    """
    if not icon_url or not icon_url.startswith("http"):
        return None
    base = icon_url.split("!00_")[0]
    name = base.rsplit("/", 1)[-1]
    if not name or "." not in name:
        import hashlib
        name = "bounty_" + hashlib.md5(icon_url.encode("utf-8")).hexdigest()[:12] + ".png"
    cache = Path(__file__).resolve().parents[4] / "data" / "warframe_icons" / name
    try:
        if cache.is_file() and cache.stat().st_size >= 600:
            return str(cache)
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as c:
            r = await c.get(icon_url, headers={"User-Agent": _HTML_UA.get("User-Agent", "Mozilla/5.0")})
            data = r.content
            if getattr(r, "status_code", 0) == 200 and len(data) >= 600 and data[:8] == b"\x89PNG\r\n\x1a\n":
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
                return str(cache)
    except Exception:  # noqa: BLE001 — 图源抖动不影响出卡
        return None
    return None


async def _bounty_resolve_image(image_name: str | None, en: str | None,
                               icon_url: str | None, is_wf: bool = False) -> str | None:
    """把奖励解析为本地图标真实路径，优先级：

      * 战甲奖励：优先 WFCD 仓库特定战甲立绘（caliban.png 头胸像）→ wf.wiki 通用头盔兜底；
      * 武器/其它：优先 wf.wiki 直链（DE 官方特定武器图，如 ArchonTrident=雷霆暴君）→ 本地库兜底。

    全程失败返回 None，由模板回落几何图标——永不阻塞出卡。
    """
    # 战甲：优先 WFCD 仓库特定战甲立绘（用干净英文名作缓存名，仓库即 <Name>.png）
    if is_wf and en:
        try:
            from ..pic.weapon_icons import ensure_icon, icon_cache_path
            await ensure_icon(en + ".png", en)
            p = icon_cache_path(en + ".png")
            if p:
                return str(p)
        except Exception:  # noqa: BLE001
            pass
    # wf.wiki 直链（武器特定图 / 战甲通用头盔）
    if icon_url:
        p = await _bounty_download_icon(icon_url)
        if p:
            return p
    # 兜底：本地库 hashed imageName
    if image_name:
        try:
            from ..pic.weapon_icons import ensure_icon, icon_cache_path
            await ensure_icon(image_name, en)
            p = icon_cache_path(image_name)
            if p:
                return str(p)
        except Exception:  # noqa: BLE001
            pass
    return None


def _bounty_reward_info(zh_name: str) -> tuple[str | None, str | None, bool]:
    """赏金奖励中文/英文名 → (官方 imageName, 英文物品名, 是否为战甲)。

    用于 bounty 卡片的奖励缩略图与右下角立绘：
      * 显式中文映射（雷霆暴君→Korumm、孢丝感染枪→Sporelacer）；
      * 通用：在本地物品库做大小写无关子串匹配（兼容中/英/混合 payload），
        例如 "Caliban Neuroptics" / "Caliban 头部神经元" 都能命中 Caliban 战甲。
    取不到返回 (None, None, False)，由模板回落几何图标。
    """
    if not zh_name:
        return None, None, False
    s = zh_name.strip()
    # 1) 显式中文映射（武器）
    for zh_key, en in _BOUNTY_REWARD_ZH_EN.items():
        if zh_key in s:
            return _wf_image_name(en), en.lower(), False
    # 2) 通用：本地物品库子串匹配（兼容 EN/ZH 混合）
    items = _load_wf_items()
    low = s.lower()
    best_name: str | None = None
    best_len = 0
    for name in items:
        if name and (name in low or low.startswith(name)):
            if len(name) > best_len:
                best_len = len(name)
                best_name = name
    if best_name:
        it = items[best_name]
        return it.get("imageName"), best_name, best_name in _wf_warframe_set()
    return None, None, False


def _fmt_hms(seconds: int) -> str:
    """秒 → 「X小时Y分Z秒」（Rabbit 赏金卡倒计时格式，精确到秒）。"""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


async def _official_worldstate() -> dict | None:
    """官方 worldstate（api.warframe.com，120s 缓存 + 单飞锁 + 限速闸）。

    官方源带 jobType（任务定义，可映射中文名）、enemy 等级、Narmer 路径与
    Expiry（轮换倒计时权威），比 wf.wiki 的卡面数据新鲜一个轮换周期。
    """
    async with _wf_lock:
        if _official_cache["data"] and _now() - _official_cache["ts"] < _OFFICIAL_TTL:
            return _official_cache["data"]
        # 失败冷却：源故障/被限流时冷却期内不重试，避免连打被拉黑
        if _now() - (_official_cache.get("fail_ts") or 0.0) < _FAIL_COOLDOWN:
            return _official_cache.get("data")
        d = await _fetch(_OFFICIAL_URL)
        if d:
            _official_cache.update({"ts": _now(), "data": d, "fail_ts": 0.0})
        else:
            _official_cache["fail_ts"] = _now()
        return d


def _wfwiki_payload(html: str) -> dict:
    """解析 wf.wiki 的 RSC payload → {bountyBoard, groups, bounties}。

    注意：payload 内嵌中文为直接 UTF-8 字符，只去掉 JSON 转义引号即可，
    不能做 unicode_escape（会把正常中文按字节误解码成乱码）。
    """
    try:
        blocks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S)
        full = "".join(blocks).replace('\\"', '"')
        out: dict = {}
        dec = json.JSONDecoder()
        for key in ("bountyBoard", "groups", "bounties"):
            i = full.find(f'"{key}":')
            if i < 0:
                continue
            try:
                v, _ = dec.raw_decode(full[i + len(f'"{key}"'):].lstrip(" ").lstrip(":"))
                out[key] = v
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception:  # noqa: BLE001
        return {}


def _bounty_mods(cetus_jobs: list) -> list[str]:
    """从地球赏金 rewards 提取 MOD（按 Rabbit 顺序：等级组升序 + 组内固定序）。"""
    items, seen = [], set()
    for j in cetus_jobs or []:
        lvl = j.get("minLevel") or 0
        for r in j.get("rewards") or []:
            nm = r.get("name", "")
            ic = r.get("icon", "") or ""
            if "Cards/Images" not in ic:
                continue
            if "套" in nm or "蓝图" in nm or nm in _BOUNTY_BASE_MODS or nm in seen:
                continue
            if any(k in nm for k in _BOUNTY_NARMER_ITEMS):
                continue
            seen.add(nm)
            order = next((v for k, v in _BOUNTY_MOD_ORDER.items() if k in nm), 30)
            items.append((lvl, order, nm))
    items.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in items]


def _bounty_bonds(solaris_jobs: list) -> list[str]:
    """从金星赏金 rewards 提取债务债券（保序去重，count>1 加「Nx 」前缀）。"""
    bonds, seen = [], set()
    for j in solaris_jobs or []:
        for r in j.get("rewards") or []:
            nm = r.get("name", "")
            if "债券" not in nm or nm in seen:
                continue
            seen.add(nm)
            c = r.get("count") or 1
            bonds.append(f"{c}x {nm}" if c and c > 1 else nm)
    return bonds


def _bounty_job_line(job: dict) -> str:
    """单个任务行 → 「[等级 X-Y]类型 任务名[ 合一众]」。

    任务名已含类型词（如「破坏原型机」含「破坏」）时不重复前缀；
    合一众任务统一补「类型 任务名 合一众」。
    """
    jt = (job.get("jobType") or "").rsplit("/", 1)[-1]
    mn, mx = job.get("minLevel"), job.get("maxLevel")
    nm, typ = _BOUNTY_NAMES.get(jt, (None, None))
    if nm is None:
        nm = job.get("objective") or jt
        typ = job.get("objective") or ""
    lvl = f"[等级 {mn}-{mx}]"
    pre = f"{typ} " if typ and typ not in nm else ""
    if job.get("narmer"):
        return f"{lvl}{pre}{nm} 合一众"
    return f"{lvl}{pre}{nm}"


def _bounty_top_jobs(jobs: list, n: int = 2) -> list[dict]:
    """每区等级最高的 n 个任务（排除隔离库 vault），与 Rabbit 卡一致。"""
    cand = [j for j in (jobs or []) if not j.get("isVault")]
    cand = [j for j in cand if (j.get("minLevel") or 0) > 0]
    cand.sort(key=lambda j: (j.get("minLevel") or 0, j.get("maxLevel") or 0), reverse=True)
    return cand[:n]


def _rot_reward_name(r: dict) -> str:
    """合一众轮换奖励名（部件/武器补「蓝图」后缀，对齐 Rabbit 卡）。"""
    nm = r.get("name", "")
    ic = r.get("icon", "") or ""
    if "蓝图" in nm:
        return nm
    if "CraftingComponents" in ic or "/Weapons/" in ic:
        return nm + " 蓝图"
    return nm


async def _bounty_card_data(official: dict | None, pl: dict) -> dict:
    """把官方 worldstate + wf.wiki payload 整理成赏金卡结构化数据。

    返回 dict 供 pic/templates/bounty.py 渲染；字段与 Rabbit 赏金卡版式对齐。
    """
    # ---- 轮换与倒计时 ----
    bounties = pl.get("bounties") or {}
    rot = bounties.get("rot") or "?"
    rot_rewards = []
    for r in bounties.get("rotRewards") or []:
        nm = _rot_reward_name(r)
        img, en, is_wf = _bounty_reward_info(nm)
        rot_rewards.append({
            "name": nm,
            "image": await _bounty_resolve_image(img, en, r.get("icon"), is_wf),
            "en": en,
            "is_warframe": is_wf,
            "fallback": "star",
        })
    vault = bounties.get("vaultRot") or "?"
    vault_rewards = []
    for r in bounties.get("vaultRewards") or []:
        nm = r.get("name", "")
        img, en, is_wf = _bounty_reward_info(nm)
        vault_rewards.append({
            "name": nm,
            "image": await _bounty_resolve_image(img, en, r.get("icon"), is_wf),
            "en": en,
            "is_warframe": is_wf,
            "fallback": "gun" if "枪" in nm else "sword",
        })
    remain = 0
    sm = ((official or {}).get("SyndicateMissions")) or []
    for sy in sm:
        if sy.get("Tag") == "CetusSyndicate":
            e = (sy.get("Expiry") or {}).get("$date") or {}
            exp = e.get("$numberLong") or (sy.get("Expiry") or {}).get("sec") or 0
            exp = int(exp)
            remain = exp / 1000 - _now() if exp > 10**12 else exp - _now()
            break
    remain_str = _fmt_hms(remain) if remain > 0 else ""

    # ---- 地球/金星/火卫二 ----
    job_tag = {"地球赏金": "CetusSyndicate", "金星赏金": "SolarisSyndicate",
               "火卫二赏金": "EntratiSyndicate"}
    syn_by_tag = {}
    if official:
        syn_by_tag = {s.get("Tag"): s for s in (official.get("SyndicateMissions") or [])}
    bb_by_tag = {}
    for b in pl.get("bountyBoard") or []:
        bb_by_tag[b.get("tag")] = b

    cetus_bb = bb_by_tag.get("CetusSyndicate") or {}
    solaris_bb = bb_by_tag.get("SolarisSyndicate") or {}
    mods = _bounty_mods(cetus_bb.get("jobs") or [])
    bonds = _bounty_bonds(solaris_bb.get("jobs") or [])

    world_meta = {
        "地球赏金": ("circle", "#4CAF50"),
        "金星赏金": ("diamond", "#FF9800"),
        "火卫二赏金": ("triangle", "#9C27B0"),
    }
    worlds: list[dict] = []
    for zh, tag in job_tag.items():
        sy = syn_by_tag.get(tag) or {}
        jobs = list(sy.get("Jobs") or [])
        for j in jobs:
            j["minLevel"] = j.get("minEnemyLevel") or j.get("minLevel")
            j["maxLevel"] = j.get("maxEnemyLevel") or j.get("maxLevel")
            j["narmer"] = "/Narmer/" in (j.get("jobType") or "")
        top = _bounty_top_jobs(jobs)
        icon, color = world_meta[zh]
        entry: dict = {"name": zh, "icon": icon, "color": color, "jobs": []}
        if zh == "地球赏金" and mods:
            entry["label"] = "MOD奖励轮换"
            entry["items"] = mods
        elif zh == "金星赏金" and bonds:
            entry["label"] = "债券轮换"
            entry["items"] = bonds
        for j in top:
            jt = (j.get("jobType") or "").rsplit("/", 1)[-1]
            nm, typ = _BOUNTY_NAMES.get(jt, (None, None))
            if nm is None:
                nm = j.get("objective") or jt
                typ = j.get("objective") or ""
            entry["jobs"].append({
                "lvl": f"{j.get('minLevel')}-{j.get('maxLevel')}",
                "type": typ,
                "name": nm,
                "narmer": bool(j.get("narmer")),
                "icon": icon,
                "color": color,
            })
        worlds.append(entry)

    # ---- 深矿 ----
    deep = {
        "name": "深矿赏金",
        "tag": "[钢铁之路]",
        "title": "花园除草",
        "detail": "等级 130-140 · 5阶段 · 最终：歼灭",
        "icon": "bolt",
        "color": "#F44336",
    }

    # ---- 派系赏金 ----
    faction_meta = {
        "扎里曼赏金": ("star", "#FFC107"),
        "圣所赏金": ("round_square", "#00BCD4"),
        "六人组赏金": ("square", "#607D8B"),
    }
    factions: list[dict] = []
    for fac, jobs in _BOUNTY_GROUPS.items():
        icon, color = faction_meta[fac]
        entry = {"name": fac, "icon": icon, "color": color, "jobs": []}
        for node, lvl, typ, tgt in jobs:
            entry["jobs"].append({
                "lvl": lvl or None,
                "name": node,
                "type": typ,
                "target": tgt,
                "icon": icon,
                "color": color,
            })
        factions.append(entry)

    # ---- 右下角装饰立绘：只用 portraits/<en>.png 全幅立绘；没有则留空 ----
    # 强行把战甲头盔图标放大到 220px 会遮挡正文/页脚（用户反馈跑偏+太大），
    # 因此右下角只展示真正的全身立绘；轮换 pill 里已有战甲/武器缩略图。
    portrait: str | None = None
    pdir = Path(__file__).resolve().parents[4] / "data" / "warframe_items" / "portraits"
    for r in rot_rewards:
        if r.get("is_warframe") and r.get("en"):
            cand = pdir / (r["en"] + ".png")
            if cand.is_file():
                portrait = str(cand)
                break
    # 不随机取 portraits 下第一张其它图，避免“错战甲”；无对应全幅立绘就保持干净

    return {
        "title": "赏金任务一览",
        "remain": remain_str,
        "rotation": {
            "rot": rot,
            "rot_rewards": rot_rewards,
            "vault": vault,
            "vault_rewards": vault_rewards,
        },
        "worlds": worlds,
        "deep": deep,
        "factions": factions,
        "portrait": portrait,
    }


def _bounty_card_text(official: dict | None, pl: dict) -> str:
    """按截图「赏金任务一览」卡输出完整文本。official 缺省时任务区降级。"""
    lines = ["@ttl|赏金任务一览|globe", ""]

    # ---- 赏金轮换（合一众轮换 + 保险库 + 剩余时间）----
    bounties = pl.get("bounties") or {}
    rot = bounties.get("rot") or "?"
    rot_rewards = [_rot_reward_name(r) for r in bounties.get("rotRewards") or []]
    vault = bounties.get("vaultRot") or "?"
    vault_rewards = [r.get("name", "") for r in bounties.get("vaultRewards") or []]
    lines.append("【赏金轮换】")
    if rot_rewards:
        lines.append(f"合一众轮换 {rot}：" + "｜".join(rot_rewards))
    if vault_rewards:
        lines.append(f"保险库 {vault}：" + "｜".join(vault_rewards))
    # 剩余时间：官方 Cetus 轮换 expiry（权威倒计时）
    remain = 0
    sm = ((official or {}).get("SyndicateMissions")) or []
    for sy in sm:
        if sy.get("Tag") == "CetusSyndicate":
            e = (sy.get("Expiry") or {}).get("$date") or {}
            exp = e.get("$numberLong") or (sy.get("Expiry") or {}).get("sec") or 0
            exp = int(exp)
            remain = exp / 1000 - _now() if exp > 10**12 else exp - _now()
            break
    if remain > 0:
        lines.append(f"-剩余时间：{_fmt_hms(remain)}")
    lines.append("")

    # ---- 地球/金星/火卫二（官方当前轮换任务 + MOD/债券轮换）----
    job_tag = {"地球赏金": "CetusSyndicate", "金星赏金": "SolarisSyndicate",
               "火卫二赏金": "EntratiSyndicate"}
    syn_by_tag = {}
    if official:
        syn_by_tag = {s.get("Tag"): s for s in (official.get("SyndicateMissions") or [])}
    bb_by_tag = {}
    for b in pl.get("bountyBoard") or []:
        bb_by_tag[b.get("tag")] = b

    cetus_bb = bb_by_tag.get("CetusSyndicate") or {}
    solaris_bb = bb_by_tag.get("SolarisSyndicate") or {}
    mods = _bounty_mods(cetus_bb.get("jobs") or [])
    bonds = _bounty_bonds(solaris_bb.get("jobs") or [])

    for zh, tag in job_tag.items():
        sy = syn_by_tag.get(tag) or {}
        jobs = list(sy.get("Jobs") or [])
        for j in jobs:
            j["minLevel"] = j.get("minEnemyLevel") or j.get("minLevel")
            j["maxLevel"] = j.get("maxEnemyLevel") or j.get("maxLevel")
            j["narmer"] = "/Narmer/" in (j.get("jobType") or "")
        top = _bounty_top_jobs(jobs)
        if zh == "地球赏金" and mods:
            lines.append("【地球赏金】MOD奖励轮换")
            lines.append(" · ".join(mods))
        elif zh == "金星赏金" and bonds:
            lines.append("【金星赏金】债券轮换")
            lines.append(" · ".join(bonds))
        else:
            lines.append(f"【{zh}】")
        if top:
            for j in top:
                lines.append(_bounty_job_line(j))
        else:
            lines.append("· 当前任务数据暂缺")
        lines.append("")

    # ---- 深矿（钢铁之路专属，2.5h 轮换，暂无公开 API，固定截图口径）----
    lines.append("【深矿赏金】")
    lines.append("[钢铁之路]花园除草")
    lines.append("等级 130-140·5阶段·最终：歼灭")
    lines.append("")

    # ---- 扎里曼/圣所/六人组（派系任务，截图口径）----
    for fac, jobs in _BOUNTY_GROUPS.items():
        lines.append(f"【{fac}】")
        for node, lvl, typ, tgt in jobs:
            if lvl:
                lines.append(f"[等级 {lvl}]{node}")
            else:
                lines.append(f"{node}")
            lines.append(f"任务：{typ} {tgt}")
        lines.append("")

    return "\n".join(lines).rstrip()


async def _wfwiki_html() -> str | None:
    """抓 worldstate.wf.wiki（120s 缓存 + 全局限速闸 + 失败冷却）。"""
    now = _now()
    if _wfwiki_cache["html"] and now - _wfwiki_cache["ts"] < _WFWIKI_TTL:
        return _wfwiki_cache["html"]
    # 失败冷却：源故障/被限流时冷却期内不重试
    if now - (_wfwiki_cache.get("fail_ts") or 0.0) < _FAIL_COOLDOWN:
        return _wfwiki_cache.get("html")
    await _rate_gate()
    try:
        async with httpx.AsyncClient(headers=_HTML_UA, follow_redirects=True, timeout=_TIMEOUT) as c:
            r = await c.get("https://worldstate.wf.wiki/")
            r.raise_for_status()
            _wfwiki_cache.update({"ts": now, "html": r.text, "fail_ts": 0.0})
            return r.text
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_wfwiki_fetch_failed: {type(e).__name__}: {e}")
        _wfwiki_cache["fail_ts"] = _now()
        return None


async def bounties_card_data() -> dict | None:
    """赏金任务一览（#wf 赏金）：结构化数据，供 bounty 图片模板渲染。"""
    official = await _official_worldstate()
    html = await _wfwiki_html()
    pl = _wfwiki_payload(html) if html else {}
    if official or pl:
        return await _bounty_card_data(official, pl)
    return None


async def bounties_text() -> str:
    """赏金任务一览（#wf 赏金）：Rabbit 卡格式，轮换 + 三大开放世界 +
    深矿 + 扎里曼/圣所/六人组 + 剩余时间。"""
    official = await _official_worldstate()
    html = await _wfwiki_html()
    pl = _wfwiki_payload(html) if html else {}
    if official or pl:
        txt = _bounty_card_text(official, pl)
        if txt:
            return txt
    # 降级：tenno.tools worldstate bounties（简版）
    d = await _worldstate()
    bs = _slice(d, "bounties")
    if not bs:
        return "赏金数据暂时没拉到，稍后再试～"
    syn_zh = {"Ostron": "希图斯（地球）", "Solaris United": "索拉里斯联盟（金星）",
              "Entrati": "英择谛（火卫二）", "The Holdfasts": "坚守者（扎里曼号）",
              "Cavia": "科维兽（火卫二）", "Vox Solaris": "索拉里斯之声（金星）",
              "Plague Star": "热美亚裂缝（活动）"}
    lines = ["赏金任务一览", "", "【赏金轮换 · 每日刷新】"]
    for b in bs[:6]:
        syn = syn_zh.get(b.get("syndicate", ""), b.get("syndicate", "?"))
        jobs = b.get("jobs") or []
        stages = len(jobs)
        remain = _fmt_dur(int(b.get("end") or 0) - _now())
        lines.append(f"· {syn}：{stages} 阶段｜剩 {remain}")
    return "\n".join(lines)


async def steel_bounties_text() -> str:
    """钢铁之路赏金（#wf 钢铁）：普通卡 + 钢铁规则说明。
    钢铁模式下地球/金星/火卫二/圣所/六人组赏金为 100-150 级，
    深矿为钢铁之路专属（130-140），扎里曼无钢铁赏金。"""
    base = await bounties_text()
    if not base or base.startswith("赏金数据"):
        return "钢铁之路赏金数据暂时没拉到，稍后再试～"
    head = ("【钢铁之路赏金】\n"
            "· 钢铁模式下：地球/金星/火卫二/圣所/六人组赏金为 100-150 级\n"
            "· 深矿（金星 企业重组）为钢铁之路专属：130-140 级\n"
            "· 扎里曼无钢铁赏金\n")
    return head + "\n" + base


_INCARNON_FILE = Path(__file__).resolve().parents[4] / "data" / "warframe_items" / "incarnon_rotation.json"
_INCARNON_ANCHOR = (2026, 9, 14)  # 周一 00:00 UTC = 灵化 C 组 + 回廊 Week1


def _incarnon_rotation() -> dict:
    """读取本地灵化/回廊轮换表（失败给空）。"""
    try:
        return json.loads(_INCARNON_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _week_offset() -> int:
    """距锚点周一 00:00 UTC 的完整周数。"""
    import datetime as _dt
    anchor = _dt.datetime(*_INCARNON_ANCHOR, tzinfo=_dt.timezone.utc)
    now = _dt.datetime.now(_dt.timezone.utc)
    return max(0, (now - anchor).days // 7)


def incarnon_text() -> str:
    """灵化武器轮换表（本周 + 完整 9 周循环）+ 普通回廊战甲轮换。"""
    rot = _incarnon_rotation()
    inc = rot.get("incarnon") or {}
    cir = rot.get("circuit") or {}
    if not inc:
        return "灵化轮换表还没加载，稍后再试～"
    wk = _week_offset()
    letters = ["C", "D", "E", "F", "G", "H", "I", "A", "B"]  # 锚点周=C，按偏移轮转
    cur = letters[wk % 9]
    out = ["【灵化武器轮换】本周 " + cur + " 组"]
    # 偏移显示：C 本周、D 下周、E +2 … B +8
    label = {0: "本周", 1: "下周", 2: "2 周后", 3: "3 周后", 4: "4 周后",
             5: "5 周后", 6: "6 周后", 7: "7 周后", 8: "8 周后"}
    for off, l in enumerate(letters):
        weapons = inc.get(l)
        if not weapons:
            continue
        line = "· " + label[off] + "（" + l + "）：" + "｜".join(weapons)
        if off == 0:
            line += "（可在钢铁无尽回廊 Tier5/Tier10 二选一）"
        out.append(line)
    out.append("")
    out.append("【普通回廊战甲轮换】本周 Week" + str((wk % 11) + 1))
    wk_label = {0: "本周", 1: "下周", 2: "2 周后", 3: "3 周后", 4: "4 周后",
                5: "5 周后", 6: "6 周后", 7: "7 周后", 8: "8 周后", 9: "9 周后", 10: "10 周后"}
    for off in range(11):
        w = str(((wk + off) % 11) + 1)
        frames = cir.get(w)
        if not frames:
            continue
        out.append("· " + wk_label[off] + "（Week" + w + "）：" + "｜".join(frames))
    return "\n".join(out)


# ===========================================================================
# 灵化战甲及武器周常卡（2026-10-22 重做：照 Warframe Rabbit 原卡逐区域复刻）
#
# 数据源：data/warframe_items/incarnon_cards.json（本仓库 curated）
#   * 轮换顺序/中文名：原卡「灵化武器轮回完整列表」OCR 逐字校对；
#   * 版本胶囊名 / 评价（含 [[icon:en]] 行内真实图标与 \n 原始断行）/ 推荐标记：
#     2026-11-05 用户提供的 800×1793 参考卡逐字复刻；
#   * 「下周战甲」胶囊 = circuit 下一周，荐章由 frame_recommend 按周指定。
# 倒计时口径：每周一 00:00 UTC（= 北京时间周一 08:00）重置，与轮换锚点一致。
# ===========================================================================
_INCARNON_CARDS_FILE = _DATA_DIR / "incarnon_cards.json"
_INCARNON_ICON_DIR = _DATA_DIR.parent / "warframe_icons"
_INCARNON_FRAME_DIR = _DATA_DIR.parent / "warframe_portraits"


def _incarnon_cards() -> dict:
    """读取灵化卡片数据表（失败记日志返回空，CLOSE-02 禁止静默）。"""
    try:
        return json.loads(_INCARNON_CARDS_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_incarnon_cards_load_failed: {type(e).__name__}: {e}")
        return {}


def _incarnon_icon(en: str) -> str | None:
    """本地官方武器图标（data/warframe_icons/<kebab>-<hash>.png）。

    只认「kebab + 8 位以上十六进制哈希」= 本体图（如 `dera-64d79d44e1.png`），
    避免把 `dera-vandal-fa64fda12e.png` 误当成本体。
    """
    raw = str(en or "").lower()
    kebab = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    # DE 导出的少数本体图标文件名保留 &（F 组「认知 & 冲击」= ack-&-brunt-*.png），
    # 标准 kebab 会把它吃掉导致整组漏命中；补一个保留 & 的候选。
    kept = re.sub(r"[^a-z0-9&]+", "-", raw).strip("-")
    kebabs = [k for k in dict.fromkeys((kebab, kept)) if k]
    if not kebabs:
        return None
    pats = [re.compile(r"^%s-[0-9a-f]{8,}\.png$" % re.escape(k)) for k in kebabs]
    try:
        for p in sorted(_INCARNON_ICON_DIR.iterdir()):
            if any(pat.match(p.name) for pat in pats):
                return str(p)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_incarnon_icon_scan_failed: {type(e).__name__}: {e}")
    return None


#: 评价里的行内武器图标标记：``[[icon:burston]]`` → data/warframe_icons/burston-*.png
_INC_COMMENT_ICON_RE = re.compile(r"\[\[icon:([A-Za-z0-9 _&'-]+)\]\]")


def _incarnon_comment_parts(comment: str) -> list[dict] | None:
    """把 ``[[icon:en]]`` 解析成真实 DE 武器图标路径列表。

    无标记返回 None（模板走纯文本）；有标记但本地缺图**直接抛错**，
    绝不降级成占位方块或把标记原样画出来（用户最高声明：禁止兜底）。
    """
    raw = str(comment or "")
    if not _INC_COMMENT_ICON_RE.search(raw):
        return None
    full = "评价：" + raw
    parts: list[dict] = []
    pos = 0
    for m in _INC_COMMENT_ICON_RE.finditer(full):
        if m.start() > pos:
            parts.append({"text": full[pos:m.start()]})
        path = _incarnon_icon(m.group(1))
        if not path:
            raise FileNotFoundError("灵化卡行内图标缺失：%s（应放 data/warframe_icons/）"
                                    % m.group(1))
        parts.append({"icon": path})
        pos = m.end()
    if pos < len(full):
        parts.append({"text": full[pos:]})
    return parts


#: 战甲立绘缓存目录（data/warframe_portraits/<En>.png，官方 DE 资产，
#: 源：WFCD/warframe-items 镜像）。缺图时模板直接 raise，绝不画几何占位兜底；
#: 发送流程出图前调用 ensure_frame_icon() 预取，失败如实报错。
#: WFCD 的 imageName 偶尔用 Lotus 内部代号而非英文名（2026-11-05 实测 W11 两张）：
#: Baruuk -> Pacifist.png / Hildryn -> IronFrame.png，Prime 版才叫 <En>Prime.png。
_FRAME_IMAGE_ALIAS = {
    "Baruuk": "Pacifist",
    "Hildryn": "IronFrame",
}
_FRAME_ICON_SRC = (
    "https://ghfast.top/https://raw.githubusercontent.com/WFCD/warframe-items/master/data/img/%s.png",
    "https://gh-proxy.com/https://raw.githubusercontent.com/WFCD/warframe-items/master/data/img/%s.png",
)


def _frame_icon(en: str) -> str | None:
    """本地战甲立绘路径（同步命中检查，不外呼）。"""
    try:
        p = _INCARNON_FRAME_DIR / ("%s.png" % str(en or "").strip())
        return str(p) if p.exists() and p.stat().st_size > 600 else None
    except Exception:  # noqa: BLE001
        return None


async def ensure_frame_icon(en: str, timeout: float = 8.0) -> str | None:
    """本地缺失时拉取战甲立绘落盘（幂等；失败返回 None，调用方必须显式报错，不兜底）。"""
    hit = _frame_icon(en)
    if hit:
        return hit
    name = str(en or "").strip()
    if not name:
        return None
    src_name = _FRAME_IMAGE_ALIAS.get(name, name)

    def _get() -> bytes | None:
        import urllib.request

        for tpl in _FRAME_ICON_SRC:
            try:
                req = urllib.request.Request(tpl % src_name, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    b = r.read()
                if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) > 600:
                    return b
            except Exception:  # noqa: BLE001 — 换下一个镜像
                continue
        return None

    try:
        data = await asyncio.wait_for(asyncio.to_thread(_get), timeout=timeout + 2)
    except Exception as e:  # noqa: BLE001 — CLOSE-05 超时保护，绝不拖垮消息
        logger.warning(f"wf_frame_icon_timeout: {name}: {type(e).__name__}")
        return None
    if not data:
        logger.info(f"wf_frame_icon_absent: {name}")
        return None
    try:
        _INCARNON_FRAME_DIR.mkdir(parents=True, exist_ok=True)
        (_INCARNON_FRAME_DIR / ("%s.png" % name)).write_bytes(data)
        return str(_INCARNON_FRAME_DIR / ("%s.png" % name))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_frame_icon_save_failed: {name}: {type(e).__name__}: {e}")
        return None


def _incarnon_remain() -> str:
    """距下次灵化/回廊轮换（周一 00:00 UTC）的剩余：3天21小时1分35秒。"""
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    monday = (now - _dt.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    nxt = monday + _dt.timedelta(days=7)
    secs = max(0, int((nxt - now).total_seconds()))
    d, r = divmod(secs, 86400)
    hh, r = divmod(r, 3600)
    mm, ss = divmod(r, 60)
    return "%d天%d小时%d分%d秒" % (d, hh, mm, ss)


def incarnon_card_data() -> dict:
    """组装灵化周常卡数据（pic 模板 incarnon 的输入）。

    返回 {} 表示数据不可用，调用方应降级为纯文本。
    """
    cards = _incarnon_cards()
    rot = cards.get("rotation") or {}
    weapons = cards.get("weapons") or {}
    frames = cards.get("frames") or {}
    if not rot or not weapons:
        return {}

    letters = ["C", "D", "E", "F", "G", "H", "I", "A", "B"]  # 锚点周=C，按偏移轮转
    wk = _week_offset()
    cur_g = letters[wk % 9]
    next_g = letters[(wk + 1) % 9]

    # ---- 本周武器卡（5 张）----
    # 卡片摆放顺序 = 原卡实物顺序（card_order），与轮回列表的列顺序不同（2026-10-22 校正）
    # 9 组必须逐组显式列出；缺键直接报错，绝不静默回退到轮换表顺序。
    card_order = cards.get("card_order") or {}
    if cur_g not in card_order:
        raise KeyError("灵化卡缺 card_order[%s]：9 组摆放顺序必须全部显式列出" % cur_g)
    order = card_order[cur_g] or []
    cur_names = list(rot.get(cur_g) or [])
    if order:
        cur_names.sort(key=lambda z: order.index(z) if z in order else 99)
    w_cards: list[dict] = []
    for zh in cur_names:
        w = weapons.get(zh) or {}
        raw_comment = str(w.get("comment", "") or "")
        w_cards.append({
            "name": zh,
            "type": w.get("type", ""),
            "recommend": bool(w.get("recommend")),
            # 有行内图标标记时由 comment_parts 供模板渲染；纯文本兜掉标记本身。
            "comment": _INC_COMMENT_ICON_RE.sub("", raw_comment),
            "comment_parts": _incarnon_comment_parts(raw_comment),
            "icon": _incarnon_icon(w.get("en") or zh),
            # 参考卡「版本」胶囊只列变体名，不再显示旧版紫卡倾向倍率。
            "variants": [{"name": v.get("name", "")}
                         for v in (w.get("variants") or [])],
        })

    # ---- 本周战甲卡（普通回廊轮换，3 张）----
    # 卡片摆放顺序 = 原卡实物顺序（frame_order），与轮换表内顺序不同（2026-10-22 校正）
    cir = _incarnon_rotation().get("circuit") or {}
    _fwk = str((wk % 11) + 1)
    _nwk = str(((wk + 1) % 11) + 1)
    frame_order = cards.get("frame_order") or {}
    rec_map = cards.get("frame_recommend") or {}
    # 11 周的顺序/推荐必须逐周显式列出：漏填直接报错，绝不静默回退到
    # 轮换表顺序或「本周无推荐」（后者是主观判断，只能由 curated 数据给出）。
    for _k in (_fwk, _nwk):
        if _k not in frame_order:
            raise KeyError("灵化卡缺 frame_order[%s]：11 周战甲顺序必须全部显式列出" % _k)
        if _k not in rec_map:
            raise KeyError("灵化卡缺 frame_recommend[%s]：无推荐也要显式写 []" % _k)
    _forder = frame_order[_fwk] or []
    _items = list(cir.get(_fwk) or [])
    if _forder:
        _items.sort(key=lambda it: _forder.index(str(it).split(" ")[0])
                    if str(it).split(" ")[0] in _forder else 99)
    cur_rec = set(rec_map[_fwk] or [])
    f_cards: list[dict] = []
    for item in _items:
        en = str(item).split(" ")[0]
        meta = frames.get(en) or {}
        f_cards.append({
            "name": en,
            "zh": meta.get("zh", ""),
            "comment": meta.get("comment", ""),
            "recommend": en in cur_rec,
            "icon": _frame_icon(en),   # 本地真实 DE 立绘；缺图由模板直接报错
        })

    # ---- 下周战甲胶囊（参考卡「· 下周战甲：」行，荐章按周 curated） ----
    _norder = frame_order[_nwk] or []
    _nitems = list(cir.get(_nwk) or [])
    if _norder:
        _nitems.sort(key=lambda it: _norder.index(str(it).split(" ")[0])
                     if str(it).split(" ")[0] in _norder else 99)
    _nrec = set(rec_map[_nwk] or [])
    next_frames = [{"name": str(it).split(" ")[0],
                    "rec": str(it).split(" ")[0] in _nrec}
                   for it in _nitems[:3]]

    # ---- 轮回完整列表：A~I 固定 9 行 × 5 列，本周/下周只做高亮标记 ----
    rows: list[list[dict]] = []
    for g in "ABCDEFGHI":
        row = []
        for zh in (rot.get(g) or []):
            w = weapons.get(zh) or {}
            row.append({
                "name": zh,
                "rec": bool(w.get("recommend")),
                "flag": "cur" if g == cur_g else ("next" if g == next_g else ""),
            })
        if row:
            rows.append(row)

    return {
        "title": "本周灵化战甲及武器",
        "intro": "本周灵化战甲及武器如下：",
        "frames": f_cards,
        "next_frames": next_frames,
        "weapons": w_cards,
        "remain": _incarnon_remain(),
        "list_title": "·灵化武器轮回完整列表",
        "rows": rows,
        "footer": "Warframe Rabbit *善用右键/长按提取文字",
        "platform": "PC",
        "week_group": cur_g,
        "next_group": next_g,
    }


# ===========================================================================
# 科研任务（Netracells / Conquests）本周一览
# 按 Rabbit「本周科研任务」卡片复刻：深层（CT_LAB）+ 时光（CT_HEX），
# 各 3 任务（钢铁难度 CD_HARD 词缀）+ 4 个额外选项 + 周常剩余时间。
# 数据源：官方 worldstate Conquests（Activation/Expiry/Missions/Variables）。
# ===========================================================================
def _clean_wf_code(v) -> str:
    """把 Warframe 内部代号规整成可读键：剥离 ``/Lotus/.../`` 路径与 ``PersonalMod_`` 等前缀。

    修复：Variables 偶发返回完整 Lotus 路径（如
    ``/Lotus/Language/Conquest/PersonalMod_Undersupplied``），直接当文案会泄漏内部路径；
    规整为 ``Undersupplied`` 后再查映射表得到 ``供给不足``。任务类型/偏离/词缀的普通代号
    （``MT_EXTERMINATION`` / ``FortifiedFoes`` / ``Quicksand`` 等）不含 ``/Lotus/``、也无
    上述前缀，原样返回，不影响既有映射。
    """
    if not v:
        return v
    s = str(v).strip()
    if "/Lotus/" in s:
        s = s.rsplit("/", 1)[-1]
    for _p in ("PersonalMod_", "Mod_", "Conquest_", "Mission_"):
        if s.startswith(_p):
            s = s[len(_p):]
    return s


#: 科研全部中文名/描述集中在 netracell_zh.py（单一事实来源，按 worldstate 内部代码索引）。
#: 此处只做别名导入——**不在本文件维护任何任务名/描述字面量**，避免与官方译名漂移，
#: 也保证新的一周只靠代码查表即可翻译，无需再对着截图抄。
from .netracell_zh import (
    mission_type as _netra_type,
    mission_name as _netra_name,
    mission_desc as _netra_desc,
    risk_name as _netra_risk_name,
    risk_desc as _netra_risk_desc,
    modifier_name as _netra_mod_name,
    modifier_desc as _netra_mod_desc,
    week_key_of as _netra_week_key,
    ensure_weekly as _netra_ensure_weekly,
)

_NETRA_TYPE_LABEL = {"CT_LAB": "本周深层科研任务", "CT_HEX": "本周时光科研任务"}


def _fmt_dhms(seconds: int) -> str:
    """秒 → 「X天Y小时Z分W秒」（科研周常倒计时格式，精确到秒）。"""
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}天{h}小时{m}分{s}秒"
    if h:
        return f"{h}小时{m}分{s}秒"
    return f"{m}分{s}秒"


#: Conquest Type → 区块标题红色高亮词
_NETRA_HIGHLIGHT = {"CT_LAB": "深层", "CT_HEX": "时光"}


def _mission_desc(m) -> str:
    """取任务描述（官方 API 可能带 narrative description）；取不到返回空串。

    不伪造：API 没有就返回空，由模板决定展示（无描述则整段省略），绝不编一段假文案。
    兼容 API 直接给字符串、或给 ``{"value"/"$text"/"en": "..."}`` 字典两种写法。
    """
    if not isinstance(m, dict):
        return ""
    d = m.get("description") or m.get("Description") or ""
    if isinstance(d, dict):
        d = d.get("value") or d.get("$text") or d.get("en") or ""
    return (str(d) if d else "").strip()


def _netracell_sections(cs: list) -> list[dict]:
    """Conquests 原始列表 → 卡片区块（**纯函数，不联网**，便于单测）。

    任务类型/名称/风险/额外选项一律查 netracell_zh 官方译名表；查不到回退
    干净英文代号，绝不编造中文。因此新的一周只靠代码即可翻译，无需截图。
    """
    sections: list[dict] = []
    for c in cs:
        ct = c.get("Type") or ""
        missions: list[dict] = []
        for m in c.get("Missions") or []:
            mt = m.get("missionType") or ""
            d2 = next((x for x in (m.get("difficulties") or []) if x.get("type") == "CD_HARD"), None)
            if d2 is None:
                d2 = (m.get("difficulties") or [{}])[0]
            typ = _netra_type(ct, mt)
            dev_code = _clean_wf_code(d2.get("deviation"))
            dev = _netra_name(ct, dev_code) or "?"
            raw_risks = d2.get("risks") or []
            risks = [{"name": _netra_risk_name(rc), "desc": _netra_risk_desc(rc)}
                     for rc in (_clean_wf_code(r) for r in raw_risks)]
            # 任务说明 = 偏差的官方中文机制描述（API 不提供描述，统一查官方本地化表）；查不到再回退 API 字段。
            desc = _netra_desc(ct, dev_code) or _mission_desc(m)
            missions.append({"type": typ, "name": dev, "risks": risks, "description": desc})
        raw_vars = c.get("Variables") or []
        extras = [{"name": _netra_mod_name(vc), "desc": _netra_mod_desc(vc)}
                  for vc in (_clean_wf_code(v) for v in raw_vars)]
        sections.append({"highlight": _NETRA_HIGHLIGHT.get(ct, ""),
                         "missions": missions, "extras": extras})
    return sections


async def _netracell_build():
    """读 Conquests → (sections, remain_str)；数据拉空返回 (None, "")。"""
    d = await _official_worldstate()
    cs = ((d or {}).get("Conquests")) or []
    if not cs:
        return None, ""
    # 每个游戏周「第一次取卡」时刷新一次官方简中快照；同周内只读本地快照、不再下载。
    try:
        if await asyncio.to_thread(_netra_ensure_weekly, _netra_week_key(cs)):
            logger.info("netracell 官方简中快照已按新周刷新（week=%s）", _netra_week_key(cs))
    except Exception as e:  # noqa: BLE001 —— 刷新失败不阻断卡片，ensure_weekly 已记 WARNING
        logger.warning("netracell 周快照刷新异常：%s", e)
    remain = 0
    for c in cs:
        e = (c.get("Expiry") or {}).get("$date") or {}
        exp = int(e.get("$numberLong") or 0)
        if exp:
            remain = exp / 1000 - _now() if exp > 10**12 else exp - _now()
            break
    return _netracell_sections(cs), (_fmt_dhms(remain) if remain > 0 else "")


async def netracell_data() -> dict:
    """科研专属卡片结构化数据（给 pic 模板 netracell 用）。"""
    sections, remain = await _netracell_build()
    if sections is None:
        return {}
    return {"title": "本周科研任务", "sections": sections, "remain": remain}


async def netracell_text() -> str:
    """科研任务纯文本版（卡片渲染失败时降级兜底用）。"""
    sections, remain = await _netracell_build()
    if sections is None:
        return "科研数据暂时没拉到，稍后再试～"
    lines: list[str] = []
    for sec in sections:
        hl = sec["highlight"]
        lines.append(f"本周{hl}科研任务如下：")
        for m in sec["missions"]:
            rk = " ".join(r["name"] for r in m["risks"])
            lines.append(f"[{m['type']}] {m['name']} ({rk})")
        lines.append("额外选项：")
        lines.append(" / ".join(e["name"] for e in sec["extras"]))
        lines.append("")
    if remain:
        lines.append("- 剩余时间：" + remain)
    return "\n".join(lines).rstrip()


# ===========================================================================
# 虚空裂隙专属卡片数据（速览 / 普通 / 钢铁 三卡，照 Rabbit 排版）
# ===========================================================================
_FISS_TIER_ORDER = ["Lith", "Meso", "Neo", "Axi", "Requiem", "Omnia"]
_FISS_TIER_LABEL = {
    "Lith": "古纪(T1)", "Meso": "前纪(T2)", "Neo": "中纪(T3)",
    "Axi": "后纪(T4)", "Requiem": "安魂(T5)", "Omnia": "全能(T6)",
}
_FISS_FACTION_LETTER = {
    "Grineer": "G", "Corpus": "C", "Infestation": "I", "Infested": "I",
    "Corrupted": "O", "Orokin": "O",
}
#: 速览「好打」任务：普通收这 6 类，钢铁只收 歼灭/虚空洪流
_FISS_GOOD_NORMAL = {"Rescue", "Exterminate", "Disruption", "Survival", "Void Cascade", "Void Flood"}
_FISS_GOOD_STEEL = {"Exterminate", "Void Flood"}
_FISS_INTRO = {
    "quick": '矿工的日常：\n*速览裂隙只显示好打的裂隙，你可以使用"裂隙 普通中断,钢铁生存"来筛选裂隙。',
    "normal": "矿工的日常：",
    "steel": "矿工的日常：",
}
_FISS_FOOTER = {
    "quick": ['*输入"普通裂隙"可以查看全部普通裂隙。',
              '*输入"钢铁裂隙"可以查看全部钢铁裂隙。',
              '*输入"九重天"可以查看九重天裂隙。'],
    "normal": ['*输入"裂隙"可以筛选普通及钢铁裂隙。',
               '*输入"钢铁裂隙"可以查看全部钢铁裂隙。'],
    "steel": ['*输入"裂隙"可以筛选普通及钢铁裂隙。',
              '*输入"普通裂隙"可以查看全部普通裂隙。'],
}
_FISS_TITLE = {"quick": "速览虚空裂隙", "normal": "普通虚空裂隙", "steel": "钢铁虚空裂隙"}


def _fmt_fissure(seconds: int) -> str:
    """Rabbit 裂隙倒计时：>=1小时「X小时Y分」；<1小时「X分Y秒」；<1分「Y秒」。"""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{sec}秒"
    return f"{sec}秒"


def _fiss_node(location: str) -> str:
    """「Void/Oxomoco」→「虚空(母神塔)」；其他节点保留英文括号。"""
    loc = (location or "").strip()
    head, sep, node = loc.partition("/")
    planet = _LOCATION_ZH.get(head.strip(), head.strip())
    node_zh = _NODE_ZH.get(node.strip(), node.strip())
    return f"{planet}({node_zh})" if node_zh else planet


def _fiss_planet_en(location: str) -> str:
    """「Void/Oxomoco」→「Void」：星球奖杯图标解析用的英文 token。"""
    return (location or "").strip().partition("/")[0].strip()


async def alerts_data() -> dict:
    """警报卡片结构化数据。来源：Tenno Tools worldstate alerts。

    映射（已对照真实 JSON 字段，禁止脑补）：
      faction/missionType/location/minLevel/maxLevel/end/rewards{credits,items[]}
      → tag(阵营+任务) / node(节点（星球）) / remain(剩余…) / reward(数量x 物品) / credits。
    """
    d = await _worldstate()
    al = _slice(d, "alerts")
    out: list[dict] = []
    now = _now()
    for a in al:
        fac = a.get("faction") or ""
        mt = a.get("missionType") or ""
        short = _ALERT_FACTION_SHORT.get(fac, (fac or "?")[:1] + "系")
        color = _ALERT_FACTION_COLOR.get(fac, "#555555")
        tag = f"{short}{_zh_mission(mt)}"
        head, _, node = (a.get("location") or "").partition("/")
        planet_en = head.strip()                      # 英文星球 token（如 "Eris"/"Kuva Fortress"），图标解析用
        planet = _LOCATION_ZH.get(planet_en, planet_en)
        node_zh = _NODE_ZH.get(node.strip(), node.strip())
        node_disp = f"{node_zh}（{planet}）" if node_zh else (planet or "?")
        end = int(a.get("end") or 0)
        remain = "剩余 " + _fmt_alert_dur(end - now) if end else "已结束"
        rw = a.get("rewards") or {}
        items = rw.get("items") or []
        if items:
            it = items[0]
            iname = _ALERT_ITEM_ZH.get(it.get("name", ""), it.get("name", ""))
            reward = f"{it.get('count', 1)}x {iname}"
            reward_en = it.get("name", "")             # 英文物品名（图标解析用，无 count 前缀）
        else:
            reward = "无物品奖励"
            reward_en = ""
        credits = rw.get("credits") or 0
        credit_str = f"{credits}x 现金" if credits else ""
        out.append({
            "tag": tag, "color": color, "node": node_disp,
            "remain": remain, "reward": reward, "credits": credit_str,
            "reward_en": reward_en, "planet_en": planet_en,   # 真实图标解析所需的英文键
        })
    return {"alerts": out, "title": "警报任务", "count": len(out), "empty": len(out) == 0}


async def alert_text() -> str:
    """警报纯文本兜底（出图失败时用）。"""
    data = await alerts_data()
    if data["empty"]:
        return "当前没有进行中的警报任务（社区数据源抽风或真没有）。"
    lines = ["【警报任务】"]
    for a in data["alerts"]:
        lines.append(f"{a['tag']} · {a['node']} · {a['remain']}")
        extra = f" ｜ {a['credits']}" if a["credits"] else ""
        lines.append(f"  奖励：{a['reward']}{extra}")
    return "\n".join(lines)


async def fissures_data(mode: str = "quick") -> dict:
    """裂隙卡片结构化数据。mode: quick / normal / steel。"""
    d = await _worldstate(fast=True)
    fs = _slice(d, "fissures")
    rows: list[dict] = []
    for f in fs:
        hard = bool(f.get("hard"))
        mt = f.get("missionType") or ""
        faction = f.get("faction") or ""
        letter = _FISS_FACTION_LETTER.get(faction, faction[:1])
        # 筛选
        if mode == "steel" and not hard:
            continue
        if mode == "normal" and hard:
            continue
        if mode == "quick":
            if hard:
                if mt not in _FISS_GOOD_STEEL:
                    continue
            else:
                if mt not in _FISS_GOOD_NORMAL:
                    continue
        lv = "钢铁" if hard else "普通"
        bubble = f"{letter}系{lv}{_zh_mission(mt)}"
        remain = _fmt_fissure(int(f.get("end") or 0) - _now())
        loc = f.get("location", "")
        rows.append({"tier": f.get("tier") or "", "bubble": bubble,
                     "node": _fiss_node(loc), "remain": remain,
                     "planet_en": _fiss_planet_en(loc)})   # 星球图标解析用
    # 按纪元分组
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["tier"], []).append(r)
    groups = []
    for tier in _FISS_TIER_ORDER:
        items = by.pop(tier, None)
        if items:
            groups.append({"label": _FISS_TIER_LABEL.get(tier, tier), "rows": items})
    for tier, items in by.items():
        groups.append({"label": _FISS_TIER_LABEL.get(tier, tier), "rows": items})
    return {"title": _FISS_TITLE.get(mode, "虚空裂隙"),
            "intro": _FISS_INTRO.get(mode, ""),
            "groups": groups, "footers": _FISS_FOOTER.get(mode, [])}


async def voidstorms_text() -> str:
    """九重天虚空风暴（Railjack 裂隙，含倒计时）。"""
    d = await _worldstate(fast=True)  # 风暴轮换，快缓存
    vs = _slice(d, "voidstorms")
    if not vs:
        return "当前没有九重天虚空风暴～"
    lines = ["【九重天 · 虚空风暴（含倒计时）】"]
    for v in vs[:10]:
        hard = "（钢）" if v.get("hard") else ""
        remain = _fmt_dur(int(v.get("end") or 0) - _now())
        lines.append(f"· {_zh_loc(v.get('location', '?'))} {_zh_mission(v.get('missionType', '?'))}{hard} 剩{remain}")
    return "\n".join(lines)


def weekly_text() -> str:
    """周本/周期任务一览（静态周期知识；无公开 API 的不装倒计时）。"""
    return (
        "【星际战甲 · 周期任务】国际服\n"
        "· 执政官狩猎（Archon Hunt）：每周一轮换（周四刷新），奖励 什锦结晶，换执政官MOD\n"
        "· 深层档案馆（Deep Archimedea）：每周一刷新，月之华 换 圣装/灵化源力石\n"
        "· 电波周挑战：每周刷新（见 #wf 电波）；每日挑战每日刷新\n"
        "· 仲裁：每小时轮换一次（见 #wf 仲裁）；逐时班次表见 #wf 仲裁表\n"
        "· 虚空商人（奸商）：每两周一次，周五~周日（见 #wf 奸商）\n"
        "· 每日刷新：突击 / 每日特惠 / 赏金 / 裂隙（见对应口令）\n"
        "· 钢铁之路：常驻，通关全图后解锁（见 #wf 钢铁）\n"
        "注：执政官/深层档案馆暂无公开实时接口，未显示倒计时。"
    )


# ===========================================================================
# 本周执刑官猎杀（Archon Hunt）—— 官方 worldState.php 的 LiteSorties
# ---------------------------------------------------------------------------
# 2026-11-05 实测：Tenno Tools 没有 archon section，wf.wiki 首页也不再内嵌
# archonHunt；但官方 api.warframe.com/cdn/worldState.php 的 **LiteSorties** 就是
# 执政官狩猎（Reward = ArchonSortieRewards，Boss = SORTIE_BOSS_BOREAL 等），
# 三阶段 Missions + Expiry 齐全。节点名走 WFCD solNodes.json 解析。
# ===========================================================================
#: Boss → 中文名 / 卡面台词 / 源力石奖励。BOREAL 逐字对齐 Rabbit 参考图。
_ARCHON_BOSS = {
    "SORTIE_BOSS_BOREAL": {
        "name": "诡文枭主", "quote": "如水千变化！万物皆利器！吃我三叉戟！",
        "shard": "蔚蓝源力石",
    },
    "SORTIE_BOSS_AMAR": {
        "name": "欺谋狼主", "quote": "阴影之中，猎手已至！", "shard": "琥珀源力石",
    },
    "SORTIE_BOSS_NIRA": {
        "name": "混沌蛇主", "quote": "毒潮翻涌，无处可逃！", "shard": "绯红源力石",
    },
}
_ARCHON_BOSS_DEFAULT = {"name": "执刑官", "quote": "本周执刑官已降临，准备迎战！",
                        "shard": "执刑官源力石"}
#: 官方 missionType → 卡面中文（MT_TERRITORY 即「拦截」）
_ARCHON_MISSION_ZH = {
    "MT_MOBILE_DEFENSE": "移动防御", "MT_TERRITORY": "拦截",
    "MT_INTERCEPTION": "拦截", "MT_ASSASSINATION": "刺杀",
    "MT_SURVIVAL": "生存", "MT_DEFENSE": "防御", "MT_EXCAVATION": "挖掘",
    "MT_SPY": "间谍", "MT_RESCUE": "救援", "MT_EXTERMINATION": "歼灭",
    "MT_SABOTAGE": "破坏", "MT_DISRUPTION": "中断", "MT_HIJACK": "劫持",
    "MT_CAPTURE": "捕获", "MT_ARTIFACT": "挖掘",
}
#: 执政官三阶段等级区间固定（官方 LiteSorties 不带等级字段）
_ARCHON_LV = ["Lv130-135", "Lv135-140", "Lv145-150"]
_ARCHON_LABELS = ["任务一", "任务二", "任务三"]


def _fmt_archon_remain(sec: int) -> str:
    """参考卡口径：>=1天「X天Y小时」；<1天「X小时Y分」；<1小时「Y分」。"""
    s = max(0, int(sec or 0))
    d, rem = divmod(s, 86400)
    h, rem2 = divmod(rem, 3600)
    m = rem2 // 60
    if d:
        return f"{d}天{h}小时"
    if h:
        return f"{h}小时{m}分"
    return f"{m}分"


async def archon_hunt_data() -> dict | None:
    """本周执刑官猎杀结构化数据（供 archon 专属卡渲染）；取不到返回 None。

    返回 {boss, boss_zh, quote, shard, missions:[{label,mission,lv,node,planet_en,
    mission_key}], remain, title, source}。
    """
    d = await _official_worldstate()
    if not isinstance(d, dict):
        return None
    lite = (d.get("LiteSorties") or [None])[0]
    if not isinstance(lite, dict) or not lite.get("Missions"):
        return None
    boss_key = str(lite.get("Boss") or "")
    meta = _ARCHON_BOSS.get(boss_key, _ARCHON_BOSS_DEFAULT)
    sn = await _solnodes()
    missions: list[dict] = []
    for i, m in enumerate(list(lite.get("Missions") or [])[:3]):
        mk = str(m.get("missionType") or "")
        node_id = str(m.get("node") or "")
        node_en, planet_en = _parse_solnode_value(
            str((sn.get(node_id) or {}).get("value") or ""))
        planet_zh = _LOCATION_ZH.get(planet_en, planet_en)
        node_disp = f"{planet_zh}-{node_en}" if planet_zh else (node_en or node_id)
        missions.append({
            "label": _ARCHON_LABELS[i],
            "mission": _ARCHON_MISSION_ZH.get(mk, mk.replace("MT_", "")),
            "mission_key": mk,
            "lv": _ARCHON_LV[i],
            "node": node_disp,
            "planet_en": planet_en,
        })
    expiry = _parse_mongo_date(lite.get("Expiry"))
    remain = _fmt_archon_remain((expiry or 0) - _now())
    return {
        "boss": boss_key, "boss_zh": meta["name"],
        "boss_name": "BOSS-" + meta["name"],
        "quote": meta["quote"], "shard": meta["shard"],
        "missions": missions, "remain": remain,
        "title": "本周执刑官猎杀",
        "source": "DE 官方世界状态（LiteSorties）",
    }


async def archon_hunt_text() -> str:
    """执政官狩猎纯文本兜底（出图失败时用）。"""
    data = await archon_hunt_data()
    if not data:
        return "本周执刑官猎杀：官方世界状态暂时取不到，稍后再试～"
    lines = [f"【{data['title']}】", f"当前战局主宰者：{data['boss_name']}",
             data["quote"], f"奖励：{data['shard']}"]
    for m in data["missions"]:
        lines.append(f"{m['label']} {m['mission']} {m['lv']}  {m['node']}")
    lines.append(f"剩余时间：{data['remain']}")
    return "\n".join(lines)


# ===========================================================================
# 武器轮换（灰机 Wiki 中文世界状态页：信条/终幕武器轮换 + 倒计时）
# ===========================================================================
_WIKI_TTL = 120
_wiki_cache: dict = {"ts": 0.0, "html": None}


async def _fetch_html(url: str, *, retry: int = 1) -> str | None:
    """抓原始 HTML（走全局限速闸 + 有限重试），失败返回 None。"""
    await _rate_gate()
    for i in range(retry):
        try:
            async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=_TIMEOUT) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.text
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wf_fetch_html_failed url={url[:60]} try={i + 1} err={type(e).__name__}: {e}")
            if i < retry - 1:
                await asyncio.sleep(1.0)
    return None


async def _wfwiki() -> str | None:
    """灰机 Wiki 世界状态页（中文，含信条/终幕武器轮换），缓存 120s。"""
    async with _wf_lock:
        if _wiki_cache["html"] and _now() - _wiki_cache["ts"] < _WIKI_TTL:
            return _wiki_cache["html"]
        h = await _fetch_html("https://worldstate.wf.wiki/", retry=1)
        if h:
            _wiki_cache["html"] = h
            _wiki_cache["ts"] = _now()
        return h


def _wiki_block(html: str, anchor: str) -> str | None:
    """提取 wf.wiki 某卡片块：刷新倒计时 + 批次 + 条目清单（HTML→文本）。"""
    i = html.find(anchor)
    if i < 0:
        return None
    j = html.find("</section>", i)
    seg = html[i:j if j > 0 else i + 4000]
    import re as _re
    m = _re.search(r"刷新\s*<!--\s*-->?\s*([^<]+)</span>", seg)
    refresh = m.group(1).strip() if m else ""
    m2 = _re.search(r"tabular-nums\">\s*(\d+)\s*</span>", seg)
    batch = m2.group(1) if m2 else ""
    items = _re.findall(r"<li[^>]*>(.*?)</li>", seg, _re.S)
    names = []
    for it in items[:8]:
        txt = _re.sub(r"<[^>]+>", " ", it)
        txt = _re.sub(r"\s+", " ", txt).strip()
        if txt and txt not in names:
            names.append(txt)
    head = anchor + (f"｜批次 {batch}" if batch else "")
    if refresh:
        head += f"｜刷新 {refresh}"
    if names:
        # 每个武器独立一行，避免一行塞一堆读起来累（卡片渲染也不超宽）
        return head + "\n" + "\n".join("  · " + n for n in names)
    return head if (refresh or batch) else None


async def rotation_text() -> str:
    """武器轮换：信条/终幕（灰机 Wiki 实时）+ 赤毒/灵化（静态说明）。"""
    html = await _wfwiki()
    lines = ["【星际战甲 · 武器轮换】国际服"]
    if html:
        for anchor in ("信条武器轮换", "终幕武器轮换"):
            blk = _wiki_block(html, anchor)
            if blk:
                lines.append(blk)
        if len(lines) == 1:
            lines.append("轮换数据暂时没解析出来（页面结构变了），稍后再试～")
    else:
        lines.append("轮换数据暂时没拉到（灰机 Wiki 抽风），稍后再试～")
    lines.append("· 赤毒武器轮换：每周一次（游戏内赤毒玄骸），无公开 API 倒计时")
    lines.append("· 灵化武器（卡瓦莱罗 Cavia）：每周轮换，无公开 API 倒计时，进游戏看")
    return "\n".join(lines)


# ===========================================================================
# 信条/终幕武器轮换（worldstate.wf.wiki Next.js flight 数据）
# ===========================================================================

def _extract_flight_json(html: str, key: str) -> Any | None:
    """从 worldstate.wf.wiki 的 Next.js flight 字符串中提取某个 JSON 值。

    flight 数据形如：self.__next_f.push([1,"...\\\"tenetCoda\\\":{...}...\"])
    本函数先定位 key，再找到包裹它的 JS 字符串字面量，解转义后按 JSON 解析。
    键值支持 object / array / string / number / bool / null。
    """
    if not html:
        return None
    idx = html.find(f'\\"{key}\\":')
    if idx < 0:
        return None
    # 向前找 JS 字符串字面量的开引号（不被反斜杠转义的 "）
    start = -1
    pos = idx
    while pos > 0:
        pos -= 1
        if html[pos] == '"':
            bs = 0
            k = pos - 1
            while k >= 0 and html[k] == '\\':
                bs += 1
                k -= 1
            if bs % 2 == 0:
                start = pos
                break
    if start < 0:
        return None
    # 向后找闭引号
    end = idx
    while end < len(html):
        if html[end] == '"':
            bs = 0
            k = end - 1
            while k >= 0 and html[k] == '\\':
                bs += 1
                k -= 1
            if bs % 2 == 0:
                break
        end += 1
    if end <= start:
        return None
    s = html[start + 1:end]
    # 解 JS 字符串转义
    s = s.replace('\\"', '"').replace('\\\\', '\\').replace('\\/', '/')
    m = re.search(rf'"{key}":', s)
    if not m:
        return None
    sub = s[m.end():]
    i = 0
    while i < len(sub) and sub[i] in ' \t\n\r':
        i += 1
    if i >= len(sub):
        return None
    ch = sub[i]
    if ch == '"':  # string
        j = i + 1
        esc = False
        while j < len(sub):
            if esc:
                esc = False
            elif sub[j] == '\\':
                esc = True
            elif sub[j] == '"':
                break
            j += 1
        try:
            return json.loads(sub[i:j + 1])
        except Exception:
            return None
    if ch in '{[':  # object / array
        close = '}' if ch == '{' else ']'
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(sub)):
            c = sub[j]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
                continue
            if c in '{[':
                depth += 1
            elif c in '}]':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(sub[i:j + 1])
                    except Exception:
                        return None
        return None
    # number / bool / null
    j = i
    while j < len(sub) and sub[j] not in ',}] \t\n\r':
        j += 1
    try:
        return json.loads(sub[i:j])
    except Exception:
        return None


_WFWIKI_FLIGHT_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_WFWIKI_FLIGHT_TTL = 120


async def _wfwiki_flight_data() -> dict | None:
    """抓取 worldstate.wf.wiki 的 flight 数据，提取 timestamp + tenetCoda。

    缓存 120s；失败 / 页面结构变更返回 None。
    """
    now = _now()
    if _WFWIKI_FLIGHT_CACHE["data"] and now - _WFWIKI_FLIGHT_CACHE["ts"] < _WFWIKI_FLIGHT_TTL:
        return _WFWIKI_FLIGHT_CACHE["data"]
    html = await _fetch_html("https://worldstate.wf.wiki/", retry=1)
    if not html:
        return None
    ts = _extract_flight_json(html, "timestamp")
    tenet_coda = _extract_flight_json(html, "tenetCoda")
    if tenet_coda is None:
        return None
    data = {
        "timestamp": ts,
        "tenet": tenet_coda.get("tenet") or [],
        "tenet_expiry": tenet_coda.get("expiry"),
        "coda": tenet_coda.get("coda") or [],
        "coda_expiry": tenet_coda.get("codaExpiry"),
        "coda_batch": tenet_coda.get("codaBatch") or "",
    }
    _WFWIKI_FLIGHT_CACHE.update({"ts": now, "data": data})
    return data


def _ms_to_local_str(ms: int | None) -> str:
    """毫秒时间戳 → 本地时间字符串 YYYY-MM-DD HH:MM:SS。"""
    if not ms:
        return ""
    try:
        dt = datetime.fromtimestamp(ms / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _countdown_text(target_ms: int, now_ms: int | None = None) -> str:
    """倒计时文本：1天15小时32分37秒。"""
    if not target_ms:
        return ""
    now_ms = now_ms or int(time.time() * 1000)
    sec = max(0, (target_ms - now_ms) // 1000)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts: list[str] = []
    if d:
        parts.append(f"{d}天")
    if h:
        parts.append(f"{h}小时")
    if m:
        parts.append(f"{m}分")
    if s or not parts:
        parts.append(f"{s}秒")
    return "".join(parts)


def _tenet_weapon_name(name: str) -> str:
    """把「信条·铁晶磁轨炮」中间点换空格、去掉「终幕/信条」前缀，对齐 Rabbit 卡片。"""
    name = name.replace("·", " ").strip()
    for _p in ("终幕 ", "信条 "):
        if name.startswith(_p):
            name = name[len(_p):].strip()
    return name


async def tenet_card_data(kind: str = "tenet") -> dict | None:
    """信条/终幕 武器轮换卡数据（两个口令各自独立版式，数据按 kind 分开）。

    kind: "tenet" → 佩兰数列信条武器（两列网格 tenet.py，与终幕视觉对齐）；
          "coda"  → 终幕武器轮换（800×998 参考图两列 coda.py）。
    返回 dict 供对应模板渲染；拉不到 worldstate 返回 None。
    版式不共用：两个模板互相传错 kind 会直接 raise。

    2026-11-06 按用户参考卡改正：
      * 加成值带 ``+``（如 ``+33.1%``），与参考图一致；
      * 真实武器图标 / 真实元素图标缺一即 ``raise``，绝不留空或让模板画占位；
      * 元素键用 ``pic.element_icons.canonical`` 归一，未知元素直接 KeyError，
        覆盖 14 种 progenitor 元素，下次轮换不返工。
    """
    from ..pic import element_icons as _ei

    d = await _wfwiki_flight_data()
    if not d:
        return None
    items = d.get(kind) or []
    if not items:
        return None
    expiry = d.get("tenet_expiry") if kind == "tenet" else d.get("coda_expiry")
    title = "佩兰数列信条武器" if kind == "tenet" else "终幕信条武器"
    intro = "佩兰数列售卖的信条武器如下：" if kind == "tenet" else "埃莉诺售卖的终幕武器如下："
    batch = d.get("coda_batch") if kind == "coda" else ""
    # 并发下载图标（DE 官方商店图标，与 _bounty_download_icon 同缓存池）
    icon_urls = [it.get("icon") for it in items]
    icon_paths = await asyncio.gather(*[_bounty_download_icon(u) for u in icon_urls])
    weapons: list[dict] = []
    missing_icons: list[str] = []
    for it, ip in zip(items, icon_paths):
        bonus = it.get("bonus")
        if bonus is None:
            raise ValueError("信条/终幕武器 %s 缺加成值：禁止画空加成" % it.get("name"))
        val = f"{int(bonus)}" if bonus == int(bonus) else f"{bonus}"
        # 参考图带 + 号
        bonus_str = "+" + val + "%"
        name = _tenet_weapon_name(it.get("name", ""))
        if not ip:
            missing_icons.append(name)
        # 未知元素直接炸，别等渲染时才发现
        key = _ei.canonical(it.get("elementKey") or it.get("element") or "")
        weapons.append({
            "name": name,
            "element": it.get("element") or "",
            "element_key": key,
            "bonus": bonus,
            "bonus_str": bonus_str,
            "icon": ip or "",
        })
    if missing_icons:
        raise FileNotFoundError(
            "信条/终幕卡真实武器图标缺失：%s；禁止用几何 sword 兜底。" % "、".join(missing_icons))

    # 真实元素图标预取落盘（覆盖本批全部元素）；缺图直接 raise，不画圆点。
    await _ei.ensure_element_icons(weapons)
    return {
        "title": title,
        "intro": intro,
        "batch": batch,
        "source": "wf.wiki",
        "snapshot": _ms_to_local_str(d.get("timestamp")),
        "next_refresh": _ms_to_local_str(expiry),
        "remain": _countdown_text(expiry),
        "weapons": weapons,
        "kind": kind,
    }


async def tenet_text(kind: str = "tenet") -> str:
    """信条/终幕 武器轮换文本降级（无图标）。"""
    data = await tenet_card_data(kind)
    if not data:
        return "信条/终幕武器轮换数据暂时没拉到（灰机 Wiki 抽风），稍后再试～"
    lines = [data["title"], "", data["intro"]]
    lines.append(f"更新来源：{data['source']}")
    if data["snapshot"]:
        lines.append(f"快照时间：{data['snapshot']}")
    if data["next_refresh"]:
        lines.append(f"下次刷新：{data['next_refresh']}")
    for w in data["weapons"]:
        lines.append(f"· {w['name']}  {w['element']}  {w['bonus_str']}")
    if data["remain"]:
        lines.append(f"- 距离下次刷新：{data['remain']}")
    lines.append("[PC] qq_bridge Warframe 助手 善用右键/长按提取文字")
    return "\n".join(lines)


# ===========================================================================
# Warframe.Market 价格查询（中文别名 → url_name → orders）
# ===========================================================================
_RIVEN_WEAPONS: dict | None = None


def _riven_weapons() -> dict:
    """懒加载 WM 紫卡武器词典（data/wf_riven_weapons.json，418 件含中文名）。"""
    global _RIVEN_WEAPONS
    if _RIVEN_WEAPONS is not None:
        return _RIVEN_WEAPONS
    _RIVEN_WEAPONS = {"by_slug": {}, "by_zh": {}, "by_en": {}}
    try:
        fp = Path(__file__).resolve().parents[4] / "data" / "wf_riven_weapons.json"
        if fp.exists():
            items = json.loads(fp.read_text(encoding="utf-8"))
            for it in items:
                slug = it.get("slug") or ""
                zh = (it.get("i18n") or {}).get("zh-hans", {}).get("name") or ""
                en = (it.get("i18n") or {}).get("en", {}).get("name") or ""
                rec = {"slug": slug, "zh": zh, "en": en, "group": it.get("group") or ""}
                if slug:
                    _RIVEN_WEAPONS["by_slug"][slug] = rec
                if zh:
                    _RIVEN_WEAPONS["by_zh"].setdefault(zh.lower().strip(), rec)
                if en:
                    _RIVEN_WEAPONS["by_en"].setdefault(en.lower().strip(), rec)
    except Exception:
        pass
    return _RIVEN_WEAPONS


def _resolve_riven_weapon(q: str) -> str:
    """紫卡武器名 → WM riven 武器 slug（绝路→rubico；先精确再包含）。"""
    d = _riven_weapons()
    q0 = (q or "").strip().lower()
    if not q0:
        return ""
    if q0 in d["by_zh"]:
        return d["by_zh"][q0]["slug"]
    if q0 in d["by_en"]:
        return d["by_en"][q0]["slug"]
    hit = None
    for k, rec in d["by_zh"].items():
        if q0 in k or k in q0:
            if hit is None or len(rec["slug"]) < len(hit["slug"]):
                hit = rec
    if hit:
        return hit["slug"]
    for k, rec in d["by_en"].items():
        if q0 in k:
            if hit is None or len(rec["slug"]) < len(hit["slug"]):
                hit = rec
    if hit:
        return hit["slug"]
    return q0.replace(" ", "_")


_WF_DICT: dict | None = None


def _wf_dict() -> dict:
    """懒加载 WM 全量物品词典（data/wf_items_v2.json，3840 件含中文名）。"""
    global _WF_DICT
    if _WF_DICT is not None:
        return _WF_DICT
    _WF_DICT = {"by_slug": {}, "by_zh": {}, "by_en": {}}
    try:
        fp = Path(__file__).resolve().parents[4] / "data" / "wf_items_v2.json"
        if fp.exists():
            items = json.loads(fp.read_text(encoding="utf-8"))
            for it in items:
                slug = it.get("slug") or ""
                i18n = it.get("i18n") or {}
                zh = (i18n.get("zh-hans") or {}).get("name") or ""
                en = (i18n.get("en") or {}).get("name") or ""
                rec = {"slug": slug, "zh": zh, "en": en, "tags": it.get("tags") or []}
                if slug:
                    _WF_DICT["by_slug"][slug] = rec
                if zh:
                    _WF_DICT["by_zh"].setdefault(zh.lower().strip(), rec)
                if en:
                    _WF_DICT["by_en"].setdefault(en.lower().strip(), rec)
    except Exception:
        pass
    return _WF_DICT


# 部件黑话 → WM slug 后缀（战甲/武器部件组合解析用）
# 部件黑话 → WM slug 候选后缀（2026-09-17 按词典实测分布：
#   战甲=neuroptics/chassis/systems+blueprint；主武器=barrel/receiver/stock；
#   副武器=barrel/receiver；近战=blade/handle；弓=grip/string/上下弓臂；
#   守护=carapace/systems/cerebrum；曲翼=harness/wings；其余=link/gauntlet/disc 等）
_PART_CANDS = {
    "头": ["neuroptics_blueprint", "neuroptics", "cerebrum"],
    "头部": ["neuroptics_blueprint", "neuroptics", "cerebrum"],
    "神经光网": ["neuroptics_blueprint", "neuroptics"],
    "机体": ["chassis_blueprint", "chassis"],
    "骨架": ["chassis_blueprint", "chassis"],
    "底盘": ["chassis_blueprint", "chassis"],
    "系统": ["systems_blueprint", "systems"],
    "蓝图": ["blueprint"],
    "总图": ["blueprint"],
    "枪管": ["barrel", "barrel_blueprint"],
    "枪机": ["receiver", "reciever"],
    "枪托": ["stock"],
    "刀刃": ["blade"], "刀片": ["blade"], "剑刃": ["blade"],
    "握柄": ["handle", "hilt"], "柄": ["handle", "hilt"],
    "外壳": ["carapace"], "甲壳": ["carapace"],
    "弓身": ["grip"], "弓弦": ["string"],
    "弓臂": ["upper_limb", "lower_limb"],
    "拳套": ["gauntlet"], "锤头": ["head"], "圆盘": ["disc"],
    "爪刃": ["blades"], "链条": ["chain"], "连接器": ["link"],
    "外甲": ["harness_blueprint", "harness"], "机翼": ["wings_blueprint", "wings"],
    "镖袋": ["pouch"], "星镖": ["stars"], "靴子": ["boot"],
    "护手": ["guard"], "饰物": ["ornament"], "弓臂上": ["upper_limb"], "弓臂下": ["lower_limb"],
}


# 常见输入 typo / 形近字归一化（整类词统一，不逐物品维护）。
# 「执刑官」(Archon) 的高频错写：执政官（形近）、执行官（多音字「行」xíng 同音），
# 归一化后由全量词典精确命中，覆盖全部执刑官 MOD/赋能，无需为每件单独加别名。
# DE 增删物品也靠词典刷新自动同步。
_TYPO_MAP: dict[str, str] = {
    "执政官": "执刑官",
    "执行官": "执刑官",
}


def _resolve_url_name(q: str) -> str:
    q0 = (q or "").strip()
    # 形近字归一化（在别名/词典匹配前做，整类词受益）
    for _bad, _good in _TYPO_MAP.items():
        if _bad in q0:
            q0 = q0.replace(_bad, _good)
    # ① 先用「完整输入」精确命中词典（中文/英文全名）。
    #    否则下面的「Prime/p 后缀」逻辑会先把「抵近射击 Prime」拆成「抵近射击」
    #    → 命中普通 point_blank（3p）而不是 primed_point_blank（27p）。
    #    2026-11-03 用真实数据实测踩过这个坑。
    _d0 = _wf_dict()
    if _d0.get("by_slug"):
        _k0 = q0.lower().strip()
        _nk0 = _k0.replace("·", "").replace(" ", "")
        _hit0 = _d0["by_zh"].get(_k0) or _d0["by_en"].get(_k0)
        if not _hit0:
            for _k, _rec in _d0["by_zh"].items():
                if _nk0 == _k.replace("·", "").replace(" ", ""):
                    _hit0 = _rec
                    break
        if _hit0:
            return _hit0["slug"]
    q = q0.lower().replace(" ", "_")
    # 后缀变体：一套 / p / prime（如「毒妈一套」「电男p」「轮椅prime」→ 解析本体再升级）
    _sufm = re.search(r"(.+?)(一套|prime|p)$", q0, flags=re.I)
    if _sufm and len(_sufm.group(1).strip()) >= 1:
        _b0 = _sufm.group(1).strip()
        _bv = _resolve_url_name(_b0)
        if _bv and _bv != _b0:
            _d = _wf_dict().get("by_slug", {})
            if _bv.endswith("_prime_set") or _bv.endswith("_prime") or _bv.endswith("_set"):
                return _bv
            if f"{_bv}_prime_set" in _d:
                return f"{_bv}_prime_set"
            if f"{_bv}_prime" in _d:
                return f"{_bv}_prime"
            return _bv
    if q in ITEM_ALIASES:
        return ITEM_ALIASES[q]
    if q0.lower() in {k.lower(): v for k, v in ITEM_ALIASES.items()}:
        return ITEM_ALIASES[q0.lower()]
    # 社区黑话 aliases.json（96 个高频对象的多外号表，翻译与查询共用）
    _load_translate_data()
    if _alias_lookup:
        _en = _alias_lookup.get(q0.lower().strip())
        if _en:
            _slug = _en.replace(" ", "_")
            _d = _wf_dict()
            if _slug in _d.get("by_slug", {}):
                return _slug
            if (_slug + "_set") in _d.get("by_slug", {}):
                return _slug + "_set"
            # 战甲普通本体不可交易：aliases 命中普通名时自动升级 Prime 套装
            if (_slug + "_prime_set") in _d.get("by_slug", {}):
                return _slug + "_prime_set"
            if (_slug + "_prime") in _d.get("by_slug", {}):
                return _slug + "_prime"
            return _slug
    # 全量词典：精确中文/英文名 → 包含匹配（取最短 slug，避免误命中）
    d = _wf_dict()
    if d["by_slug"]:
        key = q0.lower().strip()

        def _nk(s: str) -> str:
            # 去除「·」与空格再比，兼容「赋能·充沛」↔「充沛」「赋能充沛」
            return s.replace("·", "").replace(" ", "").lower()

        if key in d["by_zh"]:
            return d["by_zh"][key]["slug"]
        if key in d["by_en"]:
            return d["by_en"][key]["slug"]
        # 去「·」/空格后的精确匹配（赋能类中文名带间隔号）
        _kn = _nk(key)
        _exact = None
        for k, rec in d["by_zh"].items():
            if _kn == _nk(k):
                _exact = rec
                break
        if _exact:
            return _exact["slug"]
        hit = None
        for k, rec in d["by_zh"].items():
            if key in k or k in key:
                if hit is None or len(rec["slug"]) < len(hit["slug"]):
                    hit = rec
        if hit:
            return hit["slug"]
        for k, rec in d["by_en"].items():
            if key in k:
                if hit is None or len(rec["slug"]) < len(hit["slug"]):
                    hit = rec
        if hit:
            return hit["slug"]
    # 组合解析：战甲/武器/守护别名 + 部件黑话（如「电男 机体」「绝路 枪管」「毒妈头」「救赎者 刀刃」）
    _pm = re.search(r"(.+?)(机体|神经光网|系统|骨架|底盘|蓝图|总图|枪管|枪机|枪托|刀刃|刀片|剑刃|"
                    r"握柄|柄|外壳|甲壳|弓身|弓弦|弓臂|拳套|锤头|圆盘|爪刃|链条|连接器|外甲|机翼|"
                    r"镖袋|星镖|靴子|护手|饰物|头)", q0)
    if _pm:
        _base = _resolve_url_name(_pm.group(1).strip())
        if _base and _base != _pm.group(1).strip():
            _bp = _base.replace("_set", "")
            _sufs = _PART_CANDS.get(_pm.group(2), [])
            _d = _wf_dict().get("by_slug", {})
            for _suf in _sufs:
                for _cand in (f"{_bp}_{_suf}", f"{_base}_{_suf}",
                              f"{_bp}_prime_{_suf}", f"{_base}_prime_{_suf}"):
                    if _cand in _d:
                        return _cand
    return q  # 兜底：原词直接当 url_name 试


def _item_cat_text(url_name: str) -> str:
    """按 WM 全量词典 tags 推导物品中文分类（用于 wm 结果标题行）。"""
    rec = _wf_dict().get("by_slug", {}).get(url_name)
    if not rec:
        return ""
    tags = rec.get("tags") or []
    names = []
    if "warframe" in tags:
        names.append("战甲")
    if "weapon" in tags:
        names.append("武器")
    if "component" in tags:
        names.append("部件")
    if "blueprint" in tags and "component" not in tags:
        names.append("蓝图")
    if "set" in tags:
        names.append("套装")
    if "mod" in tags:
        names.append("MOD")
    if "augment" in tags:
        names.append("集团卡")
    if "stance" in tags:
        names.append("架势")
    if "aura" in tags:
        names.append("光环")
    if "relic" in tags:
        names.append("遗物")
    if "arcane_enhancement" in tags:
        names.append("赋能")
    if "scene" in tags:
        names.append("装饰/场景")
    if "skin" in tags:
        names.append("外观")
    if "sentinel" in tags:
        names.append("守护")
    if "kubrow" in tags or "kavat" in tags or "hound" in tags or "moa" in tags:
        names.append("同伴")
    if "archwing" in tags:
        names.append("曲翼")
    if "fish" in tags:
        names.append("鱼")
    if "gem" in tags:
        names.append("宝石")
    if "riven_mod" in tags or "veiled_riven" in tags:
        names.append("紫卡")
    if "emote" in tags:
        names.append("表情")
    if "ayatan_sculpture" in tags or "ayatan_star" in tags:
        names.append("阿耶檀识")
    if "requiem" in tags:
        names.append("安魂MOD")
    if not names:
        names = tags[:2]
    return " · ".join(names)


def categories_text() -> str:
    """Warframe.Market 物品分类说明（wm 可查任意分类物品）。"""
    return (
        "Warframe.Market 物品分类（wm <物品> 可查全部）：\n"
        "· 战甲套装（warframe+set）：如 Saryn Prime 一套\n"
        "· 战甲部件（warframe+component）：战甲蓝图/系统/机体/头部\n"
        "· 武器套装（weapon+set）：如 绝路 Prime 一套\n"
        "· 武器部件（weapon+component）：枪管/枪机/枪托/蓝图\n"
        "· MOD：普通 / 集团卡（augment）/ 架势（stance）/ 光环（aura）\n"
        "· 赋能（arcane_enhancement）：如 充沛\n"
        "· 遗物（relic）：Axi / Lith / Meso / Neo\n"
        "· 守护/同伴：守护（sentinel）/ 库狛（kubrow）/ 库娃（kavat）\n"
        "· 装饰：场景 / 外观 / 表情 / 阿耶檀识\n"
        "· 紫卡（riven_mod / veiled_riven）\n"
        "· 其他：曲翼 / 亡骸机甲 / 九重天 / 鱼 / 宝石 / 钥匙 等\n"
        "示例：wm Saryn Prime 一套｜wm 电男｜wm 充沛｜wm 绝路 Prime 枪管"
    )


def _market_fail_msg(url_name: str) -> str:
    """WM 请求失败降级提示：url_name 是合法英文才拼官网链接，中文名不拼坏链接。"""
    _ok = url_name and all(ord(c) < 128 for c in url_name)
    _url = f"\n可去官网看：https://warframe.market/items/{url_name}" if _ok else ""
    return (f"Warframe.Market 暂时没响应（风控或网络波动），稍后再试～{_url}"
            f"\n（不确定物品英文名？发 #wf 分类 看分类）")


async def price_text(item: str) -> str:
    """普通物品 / 赤毒 / 信条 武器售价：最低卖价 + 最高买价 + 挂单数。"""
    if not item:
        return "格式：#wf 价格 <物品>（例：#wf 价格 充沛 / #wf 价格 kuva zarr）"
    url_name = _resolve_url_name(item)
    try:
        d = await asyncio.wait_for(
            _market_get(f"{MARKET_BASE}/v2/orders/item/{url_name}"),
            timeout=MARKET_HARD_TIMEOUT,
        )
    except Exception:  # noqa: BLE001 — 超时或请求失败，统一优雅降级
        logger.warning("wf_price_query_timeout url_name=%s", url_name)
        return _market_fail_msg(url_name)
    if not d:
        return _market_fail_msg(url_name)
    _record_market_ts(d)
    if d.get("data") is None:
        return f"没找到「{item}」（英文名 {url_name} 可能不对，试试 #wf 分类 看分类）"
    orders = d.get("data") or []
    if not orders:
        return f"「{item}」在市场上暂无挂单（英文名 {url_name}）"
    sells = [o for o in orders if o.get("type") == "sell"]
    buys = [o for o in orders if o.get("type") == "buy"]
    sells.sort(key=lambda o: (o.get("rank") or 0, o.get("platinum") or 1e9))
    buys.sort(key=lambda o: -(o.get("platinum") or 0))
    lines = [f"【{item}】Warframe.Market 实时价"]
    if sells:
        top = sells[0]
        mr = top.get("rank")
        mr_s = f" 满级" if mr and mr > 0 else (" 无等级" if mr == 0 else "")
        lines.append(f"最低卖价：{top.get('platinum')}p{mr_s}"
                     f"（{top.get('quantity', 1)}个，在售 {len(sells)} 单）")
        more = sells[1:4]
        for o in more:
            lines.append(f"  · {o.get('platinum')}p（x{o.get('quantity', 1)}）")
    if buys:
        lines.append(f"最高买价：{buys[0].get('platinum')}p（买家 {len(buys)} 单）")
    lines.append(f"官网：https://warframe.market/items/{url_name}")
    return "\n".join(lines)


_MARKET_CACHE: dict = {"key": "", "ts": 0.0, "data": None}
_MARKET_CACHE_TTL = 60.0
_MARKET_NAME_CACHE: dict = {"key": "", "ts": 0.0, "name": ""}
_MARKET_NAME_TTL = 300.0
#: url_name → set 接口里的整条物品记录（icon/ducats/tradingTax/rarity），
#: 与英文名同源缓存，避免为头条块再发第 4 个请求（WM 公共限流 3 次/秒）。
_MARKET_META_CACHE: dict[str, dict] = {}


async def _market_en_name(url_name: str) -> str:
    """物品官方英文名（喊话用），优先 v2 item set 的 i18n.en.name，5 分钟缓存。

    2026-09-29 修复：set 接口返回整组成员，必须取 **slug 与查询项一致** 的那条，
    否则查「悟空蓝图」会拿成「Wukong Prime Set」（喊话喊错物品）。
    """
    now = _now()
    if _MARKET_NAME_CACHE["key"] == url_name and now - _MARKET_NAME_CACHE["ts"] < _MARKET_NAME_TTL:
        return _MARKET_NAME_CACHE["name"]
    name = url_name.replace("_", " ").title()
    try:
        d = await _market_get(f"{MARKET_BASE}/v2/item/{url_name}/set")
        items = ((d or {}).get("data") or {}).get("items") or []
        hit = next((it for it in items if it.get("slug") == url_name), None) or (items[0] if items else None)
        if hit:
            en = (hit.get("i18n") or {}).get("en") or {}
            name = en.get("name") or name
            _MARKET_META_CACHE[url_name] = hit   # 头条块数据（图标/杜卡德/税率）随 set 响应缓存
        _MARKET_NAME_CACHE.update({"key": url_name, "ts": now, "name": name})
    except Exception:
        pass
    return name


def _market_display_name(url_name: str, fallback: str = "") -> str:
    """WM 物品官方中文名（离线词典 i18n zh-hans，零外呼）。

    如 wukong_prime_set → 「Wukong Prime 一套」。
    2026-09-29 修复：此前标题直接用用户的原始输入词（「猴子一套」），
    与 Rabbit 卡片「出售[Wukong Prime 一套]的：」不一致。
    """
    rec = _wf_dict().get("by_slug", {}).get(url_name)
    if rec:
        return rec.get("zh") or rec.get("en") or fallback or url_name
    return fallback or url_name


def _pad2(s: str, w: int) -> str:
    """中文宽度补偿对齐（中文按 2 字符算）。"""
    vis = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(1, w - vis)


async def _market_get(url: str, params: str = "", *,
                      timeout: float = _MARKET_TIMEOUT, retry: int = _MARKET_RETRY) -> dict | None:
    """WM API 带 60s 缓存请求（风控期降低外呼频率）。

    对齐 TWGBot /wm 的请求规范：orders 带 include=item（响应含 items_in_set
    可拿官方英文名），头部带 platform/language，避免被视为裸爬虫。
    timeout/retry 默认用市价专用短值（见模块常量），不让慢请求拖垮整条命令。
    """
    full = url + ("?" + params if params else "")
    now = _now()
    if _MARKET_CACHE["key"] == full and now - _MARKET_CACHE["ts"] < _MARKET_CACHE_TTL:
        return _MARKET_CACHE["data"]
    hdrs = dict(MARKET_UA)
    hdrs.setdefault("Platform", "pc")
    hdrs.setdefault("Language", "zh-hans")
    d = await _fetch(full, verify_ssl=False, headers=hdrs, timeout=timeout, retry=retry)
    if d:
        _MARKET_CACHE.update({"key": full, "ts": now, "data": d})
    return d


# MOD 等级标记可在物品名「结尾」或「开头」出现（玩家写法不一），
# 例如「延伸 满级」「满级 延伸」「延伸满级」都应识别为「延伸 + 满级」。
_MOD_RANK_PATTERNS_END = [
    # 满级：支持「延伸 满级」「延伸满级」「延伸 max rank」
    (re.compile(r"(.*?)\s*(满级|满\s*级|max\s*rank|max)\s*$", re.I), "max"),
    # 零级：支持「延伸 R0」「延伸零级」「延伸0级」「延伸未升级」
    (re.compile(r"(.*?)\s*(未升级|未升|零级|0级|rank\s*0|r0)\s*$", re.I), 0),
    # R{N} / rank N：支持「延伸 R5」「延伸 R 5」「延伸rank5」
    (re.compile(r"(.*?)\s*rank\s*(\d+)\s*$", re.I), "num"),
    (re.compile(r"(.*?)\s*r(\d+)\s*$", re.I), "num"),
    # {N}级：支持「延伸5级」「延伸 5级」
    (re.compile(r"(.*?)\s*(\d+)级\s*$", re.I), "num"),
    # 仅数字：必须带空格，避免误吞「延伸5」这类名字
    (re.compile(r"(.*?)\s+(\d+)\s*$", re.I), "num"),
]
# 开头（等级词后带空格）：支持「满级 延伸」「R5 延伸」「5级 延伸」
_MOD_RANK_PATTERNS_START = [
    (re.compile(r"^\s*(满级|满\s*级|max\s*rank|max)\s+(.+)$", re.I), "max"),
    (re.compile(r"^\s*(未升级|未升|零级|0级|rank\s*0|r0)\s+(.+)$", re.I), 0),
    (re.compile(r"^\s*(?:rank\s*)?r(\d+)\s+(.+)$", re.I), "num"),
    (re.compile(r"^\s*(\d+)级\s+(.+)$", re.I), "num"),
]
# 开头但等级词「紧贴」物品名无空格：覆盖日志真实输入「满级执行官 延伸」。
# 剩余部分强制以汉字开头，避免英文误吞（如 maxwell 不会被当成 max + well）。
_CJK_HEAD = r"([\u4e00-\u9fff].*)$"
_MOD_RANK_PATTERNS_START_NOSPACE = [
    (re.compile(r"^\s*(满级|满\s*级|max\s*rank|max)" + _CJK_HEAD, re.I), "max"),
    (re.compile(r"^\s*(未升级|未升|零级|0级|rank\s*0|r0)" + _CJK_HEAD, re.I), 0),
    (re.compile(r"^\s*(?:rank\s*)?r(\d+)" + _CJK_HEAD, re.I), "num"),
]


def _parse_mod_rank(item: str) -> tuple[str, int | None, str | None]:
    """从查询文本解析 MOD 等级：满级 / R0 / R5 / 5级 / 10。
    等级标记可在物品名「结尾」或「开头」，且无论开头还是结尾都兼容「有空格 / 无空格紧接」
    两种写法（2026-10-08 修复：之前只认结尾+空格，导致日志真实输入
    「满级执行官 延伸」被当成普通 Stretch 零级）。
    返回 (clean_item, target_rank, rank_label)。
    target_rank 为 None 表示未指定；rank_label 为 '满级' 时，target_rank
    需后续结合 mod_max_rank 再解析。
    """
    s = (item or "").strip()
    for pat, kind in _MOD_RANK_PATTERNS_END:
        m = pat.match(s)
        if m:
            clean = m.group(1).strip()
            if kind == "max":
                return clean, None, "满级"
            if kind == 0:
                return clean, 0, "R0"
            rank = int(m.group(m.lastindex))  # num 模式的等级在最后一个 group
            return clean, rank, f"R{rank}"
    for pat, kind in _MOD_RANK_PATTERNS_START:
        m = pat.match(s)
        if m:
            clean = m.group(m.lastindex).strip()  # 开头模式：物品名在最后一个 group
            if kind == "max":
                return clean, None, "满级"
            if kind == 0:
                return clean, 0, "R0"
            rank = int(m.group(1))
            return clean, rank, f"R{rank}"
    for pat, kind in _MOD_RANK_PATTERNS_START_NOSPACE:
        m = pat.match(s)
        if m:
            clean = m.group(m.lastindex).strip()  # 剩余部分（强制汉字开头）
            if kind == "max":
                return clean, None, "满级"
            if kind == 0:
                return clean, 0, "R0"
            rank = int(m.group(1))
            return clean, rank, f"R{rank}"
    return s, None, None


def _extract_max_mod_rank(stats: dict | None) -> int | None:
    """从 WM statistics 响应中扫描 mod_rank，返回最大等级（用于解析「满级」）。"""
    if not stats:
        return None
    pl = stats.get("payload") or {}
    ranks = set()
    for key in ("statistics_live", "statistics_closed"):
        x = pl.get(key)
        if isinstance(x, dict):
            arr = (x.get("48hours") or []) + (x.get("90days") or [])
        else:
            arr = x or []
        for entry in arr:
            r = entry.get("mod_rank")
            if r is not None:
                ranks.add(r)
    return max(ranks) if ranks else None


async def market_text(item: str, page: int = 1) -> str:
    """Warframe.Market 物品市场（Rabbit 风格：出售列表 + 价格统计 + 喊话 + 页码）。
    支持 MOD 等级过滤：wm 持久力p 满级 / wm 持久力p R0 / wm 持久力p R5。
    """
    if not item:
        return "格式：wm <物品>（例：wm 翠雀 Prime 枪管）｜翻页：wm 物品 -2"
    clean_item, target_rank, rank_label = _parse_mod_rank(item)
    url_name = _resolve_url_name(clean_item)

    # 订单 + 统计 + 英文名 并发拉取（统计用于 MOD 满级解析 + 价格走势；英文名喊话用）。
    # 整体加「硬超时」MARKET_HARD_TIMEOUT：WM 风控/慢时若任一请求挂起，wait_for 取消子任务
    # 并降级为「暂时没响应」，绝不让整条命令卡到 NapCat 动作超时（表现为「返回超时」）。
    try:
        d_orders, stats, en_name = await asyncio.wait_for(
            asyncio.gather(
                _market_get(f"{MARKET_BASE}/v2/orders/item/{url_name}"),
                _market_get(f"{MARKET_BASE}/v1/items/{url_name}/statistics"),
                _market_en_name(url_name),
            ),
            timeout=MARKET_HARD_TIMEOUT,
        )
        # 物品详情（图标/杜卡德/交易税/稀有度）复用 set 接口同一次响应（_market_en_name 已缓存），
        # 不再额外发请求——WM 公共限流 3 次/秒，多打一次会触发 429。
        item_detail = _MARKET_META_CACHE.get(url_name)
    except Exception:  # noqa: BLE001 — 超时或任一请求抛错，统一优雅降级
        logger.warning("wf_market_query_timeout url_name=%s", url_name)
        return _market_fail_msg(url_name)
    if not d_orders:
        return _market_fail_msg(url_name)
    _record_market_ts(d_orders)
    data = d_orders.get("data")
    if data is None:
        return f"没找到「{clean_item}」（英文名 {url_name} 可能不对，试试 wm 充沛 / wm 绝路 Prime 枪管）"
    orders = data or []
    if not orders:
        return f"「{clean_item}」在市场上暂无挂单（英文名 {url_name}）"

    is_mod = any("rank" in o for o in orders)
    # 从统计接口推导 MOD 满级数；统计缺失时从当前订单兜底
    mod_max_rank = _extract_max_mod_rank(stats)
    if is_mod and mod_max_rank is None:
        mod_max_rank = max((o.get("rank") for o in orders if "rank" in o), default=None)

    # 处理「满级」语义
    if rank_label == "满级":
        if not is_mod:
            return f"「{clean_item}」不是 MOD，无法按等级过滤"
        if mod_max_rank is None:
            return f"「{clean_item}」暂无等级信息，无法确定满级"
        target_rank = mod_max_rank

    # MOD 未指定等级=全部等级混排（2026-11-10 定案），每行标等级；显式等级仍过滤且照标。
    if target_rank is not None and not is_mod:
        return f"「{clean_item}」不是 MOD，无法按等级过滤"
    if target_rank is not None:
        sells = [o for o in orders if o.get("type") == "sell" and o.get("rank") == target_rank]
    else:
        sells = [o for o in orders if o.get("type") == "sell"]

    if not sells:
        display = f"{clean_item} {rank_label}" if rank_label else clean_item
        return f"「{display}」在市场上暂无挂单（英文名 {url_name}）"

    # 官方中文名（离线词典，零外呼）：wukong_prime_set → 「Wukong Prime 一套」
    _zh_name = _market_display_name(url_name, clean_item)
    # 标题只在用户显式指定等级时才带后缀；默认零级不额外加「R0」
    display_item = f"{_zh_name} {rank_label}" if rank_label else _zh_name

    # 是否为在线/游戏内玩家（Rabbit 卡片「游戏在线/在线」列判定）
    def _is_online(o):
        return ((o.get("user") or {}).get("status") or "").lower().replace("_", "") in (
            "ingame", "online", "in_game")

    # 排序（Rabbit 风格·买家视角）：价格低→高（最便宜优先）→ 同价最新上架优先。
    # v2 orders 的时间字段是 updatedAt（v1 才是 last_update），旧实现读 last_update
    # 恒为空 → 同价排序失效（2026-09-29 修复，兼容两种字段名）。
    def _lu_ts(o):
        lu = o.get("updatedAt") or o.get("createdAt") or o.get("last_update") or ""
        try:
            return datetime.fromisoformat(lu.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    def _price_key(o):
        return ((o.get("platinum") or 0), -(_lu_ts(o)))

    # 列表顺序（对齐 Rabbit 卡片 1/125 页）：**在线卖家整组在前**，组内按价格升序，
    # 其后接离线卖家（同价离线不挤掉在线），状态列照实标「游戏在线/在线/离线」。
    online = sorted([o for o in sells if _is_online(o)], key=_price_key)
    offline = sorted([o for o in sells if not _is_online(o)], key=_price_key)
    ordered = online + offline

    # 分页（2026-09-29 修复）：页码必须按**全部出售挂单**计算，之前只对展示用的
    # 10 条算页数 → total 恒为 1、永远不出现「页码:1/125」那行。
    per = 10
    total = max(1, (len(ordered) + per - 1) // per)
    page = max(1, min(page, total))
    rows = ordered[(page - 1) * per: page * per]

    _cat = _item_cat_text(url_name)

    # ---- 物品详情（图标/杜卡德/交易税/稀有度）解析 ----
    # 来源：/v1/items/{url_name} 的 payload.items[0]（与订单同风控策略，失败不影响出卡）
    _icon_rel, _ducats, _tax, _rarity, _eff_disp = "", None, None, "", ""
    if item_detail:
        _id0 = item_detail
        # v2 物品的图标在 i18n.en.icon/thumb（ItemI18NJson），根字段仅 v1 才有 → 两边都试
        _i18n0 = _id0.get("i18n") or {}
        _en0 = _i18n0.get("en") or {}
        _zh0 = _i18n0.get("zh-hans") or _i18n0.get("zh") or {}
        _tags0 = _id0.get("tags") or []
        _icon_rel = (_id0.get("icon") or _en0.get("icon")
                     or _en0.get("thumb") or _id0.get("thumb") or "")
        # 总图用商品自身 icon（Atlas Prime 完整立绘）；subIcon 是全游戏共用的
        # blueprint_128x128.png → Fandom Blueprint.png，套上去每张总图卡都长一样
        # （2026-11-09「总图取的不对」）。部件蓝图仍用 subIcon 区分部位。
        if "component" in _tags0:
            _sub_icon = (_en0.get("subIcon") or _id0.get("subIcon")
                         or _id0.get("sub_icon") or "")
            if _sub_icon:
                _icon_rel = _sub_icon
        if _id0.get("ducats") is not None:
            _ducats = int(_id0.get("ducats"))
        _tax_v = _id0.get("tradingTax")          # v2 camelCase
        if _tax_v is None:
            _tax_v = _id0.get("trading_tax")     # v1 snake_case（兼容）
        if _tax_v is not None:
            _tax = int(_tax_v)
        _rarity = (_id0.get("rarity") or "").strip()
        # 效果行只给 MOD/赋能类（面板文本，如「+165% 伤害」）；
        # 武器/部件/战甲的 description 是通用说明句（「一个 Prime 武器的制作部件。」），
        # 塞进头条会让每张部件卡都多一行废话，故过滤掉。
        _eff_disp = ""
        if any(t in _tags0 for t in ("mod", "augment", "stance", "aura",
                                     "riven_mod", "veiled_riven", "arcane_enhancement")):
            _eff_disp = (_zh0.get("description") or _en0.get("description") or "")
            # 换行/竖线会破坏标记串，先归一
            _eff_disp = _eff_disp.replace("|", "/").replace("\n", " ").strip()
    _RARITY_ZH = {"legendary": "传奇", "rare": "稀有", "uncommon": "罕见", "common": "常见"}
    _rarity_zh = _RARITY_ZH.get(_rarity.lower(), "")
    # 分类展示串：稀有度 · 类型（参考卡「传奇 · MOD · 主武器 · 霰弹枪」结构）。
    # 优先用 v2 实时 tags（含 primary/shotgun 等细分类），缺失再回落本地词典分类。
    _TAG_ZH = {
        "warframe": "战甲", "weapon": "武器", "primary": "主武器", "secondary": "副武器",
        "melee": "近战", "shotgun": "霰弹枪", "rifle": "步枪", "pistol": "手枪",
        "bow": "弓", "sniper": "狙击枪", "mod": "MOD", "augment": "集团卡",
        "stance": "架势", "aura": "光环", "relic": "遗物", "arcane_enhancement": "赋能",
        "scene": "装饰/场景", "skin": "外观", "sentinel": "守护", "kubrow": "库狛",
        "kavat": "库娃", "hound": "猎犬", "moa": "恐鸟", "archwing": "曲翼",
        "fish": "鱼", "gem": "宝石", "riven_mod": "紫卡", "veiled_riven": "紫卡",
        "emote": "表情", "ayatan_sculpture": "阿耶檀识", "ayatan_star": "阿耶檀识",
        "set": "套装", "component": "部件", "blueprint": "蓝图", "requiem": "安魂MOD",
    }
    _tag_parts: list[str] = []
    for _t in ((item_detail or {}).get("tags") or []):
        _zh_t = _TAG_ZH.get(_t)
        if _zh_t and _zh_t not in _tag_parts:
            _tag_parts.append(_zh_t)
    # 标题已含「一套」时：分类不再重复「套装」，英文名也去掉 Set 后缀。
    # 用户反馈「战甲名字后面怎么多了个 set」——「Saryn Prime 一套 / Saryn Prime Set / 套装 · 战甲」
    # 三处重复，改为「Saryn Prime 一套 / Saryn Prime / 战甲」。
    _is_set_name = "一套" in display_item
    if _is_set_name and "套装" in _tag_parts:
        _tag_parts.remove("套装")
    if _is_set_name and _cat:
        # 本地词典回落串（如「战甲 · 套装」）同样去掉重复的「套装」
        _cat = " · ".join([p for p in _cat.split(" · ") if p and p != "套装"])
    _en_disp = en_name
    if _is_set_name:
        _en_disp = re.sub(r"\s+Set$", "", en_name, flags=re.I).strip() or en_name
    _cat_disp = " · ".join(_tag_parts) if _tag_parts else _cat
    _cat_disp = " · ".join([p for p in (_rarity_zh, _cat_disp) if p])
    _ducats_disp = "不可兑换" if _ducats is None else str(_ducats)
    _tax_disp = "—" if _tax is None else f"{_tax:,}"
    _sellers_n = len(ordered)
    _online_n = len(online)

    # 卡片头条块 @wmhdr|：中文名|英文名|分类|杜卡德|交易税|在售卖家|在线卖家|图标路径|效果行
    # （图标路径相对 static/assets，渲染层据此单独拉取；卖家/在线数来自本次订单统计）
    lines = [f"@wmhdr|{display_item}|{_en_disp}|{_cat_disp}|{_ducats_disp}|{_tax_disp}"
             f"|{_sellers_n}|{_online_n}|{_icon_rel}|{_eff_disp}"]
    # 等级短标签：表格「等级」列 + 统计前缀共用。非 MOD 恒零级；MOD 零级/满级/Rx/全等级。
    def _rank_prefix(rank):
        if not is_mod:
            return "零级"
        if rank is None:
            return "全等级"
        if rank == 0:
            return "零级"
        if mod_max_rank is not None and rank == mod_max_rank:
            return "满级"
        return f"R{rank}"

    # 表头：MOD 卖家|等级|状态|声望|数量|白金价格|uid|头像；非 MOD 仍 5 列。
    lines.append("@hdr|卖家|等级|状态|声望|数量|白金价格" if is_mod
                 else "@hdr|卖家|状态|声望|数量|白金价格")
    _ST = {"ingame": "游戏中", "online": "在线", "offline": "离线"}
    for o in rows:
        u = o.get("user") or {}
        name = u.get("ingameName") or u.get("ingame_name") or "?"
        _raw_status = (u.get("status") or "").lower().replace("_", "")
        st = _ST.get(_raw_status, u.get("status") or "?")
        rep = u.get("reputation")
        rep = 0 if rep is None else int(rep)
        # uid/头像固定最后两列（5/6 列通用），渲染层据此拉真实头像。
        uid = u.get("id") or ""
        avatar_rel = u.get("avatar") or ""
        if is_mod:
            _rk = _rank_prefix(o.get("rank"))
            if _rk == "全等级":
                _rk = "—"  # 单条缺 rank 不冒充零级/满级
            lines.append(f"@row|{name}|{_rk}|{st}|{rep}|{o.get('quantity') or 1}|{o.get('platinum') or 0}|{uid}|{avatar_rel}")
        else:
            lines.append(f"@row|{name}|{st}|{rep}|{o.get('quantity') or 1}|{o.get('platinum') or 0}|{uid}|{avatar_rel}")

    # ---- 价格统计（Rabbit 卡片四行：48时内/90天内 × 售价/收价）----
    # 口径（对齐 WM 官网 statistics 页，2026-09-29 重写）：
    #   · 售价 = 成交价 → statistics_closed 最近一档
    #       （48hours 是逐小时档、90days 是逐日档，两条线各取各自窗口）
    #   · 收价 = 挂收价 → statistics_live 中 order_type=buy 的最近一档
    #   · 最新 = 该档 open_price（最新成交价）；live 档无 open_price → 显示「暂无」
    #   · 非 MOD 物品（战甲/套装/部件等）恒为零级 → 前缀「零级」
    # 旧实现的 3 个 bug：①把 live 的 48hours+90days 拼成一个数组取最后一条 →
    # 48时内售价 实际取到 90天档；②90天内两行恒缺失；③「最新」取 avg_price（30.5 这种均价）。
    if stats:
        pl = stats.get("payload") or {}

        def _sec_arr(section, window, typ=None, rank=None):
            """取 statistics_* 某个窗口的档位数组；typ 过滤 order_type（live 用），
            rank 过滤 mod_rank（MOD 用）。兼容 section 为 dict（现行）或 list（旧版）。"""
            if isinstance(section, dict):
                arr = section.get(window) or []
            elif isinstance(section, list):
                arr = section
            else:
                arr = []
            if typ:
                arr = [e for e in arr if e.get("order_type") == typ]
            if rank is not None:
                arr = [e for e in arr if e.get("mod_rank") == rank]
            return arr

        def _fmt_price(v):
            # 参考卡全部为整数（最低62 最高77 最新75 / 收价 最新56），这里统一取整显示。
            # 白金价格为整数档，median 偶有 .5（如 55.5→56），与参考卡口径一致。
            if v is None:
                return "暂无"
            try:
                return str(int(round(float(v))))
            except Exception:
                return str(v)

        def _stat_fields(label, arr):
            """一行统计 → 结构化四字段 (label, 最低, 最高, 最新)；数组空返回 None。

            口径（2026-09-29 与 Rabbit 参考卡逐值比对确认，沿用）：
              · 最低 = min(整个窗口所有档的 min_price)；最高 = max(所有档的 max_price)
                —— 单档不可能有这么宽（参考卡 90天售价 50-110 / 48时售价 62-77）。
              · 最新 = 最近一档的 median（closed/live 均有该字段）；live 档无 open_price → 旧实现收价两行恒「暂无」。
            渲染层据此画「左标签 + 右三气泡（最低绿/最高红/最新主色）」。
            """
            if not arr:
                return None
            lows = [e.get("min_price") for e in arr if e.get("min_price") is not None]
            highs = [e.get("max_price") for e in arr if e.get("max_price") is not None]
            if not lows and not highs:
                return None
            last = arr[-1]
            newest = last.get("median")
            if newest is None:
                newest = last.get("wa_price")
            if newest is None:
                newest = last.get("avg_price")
            if newest is None:
                newest = last.get("open_price")
            return (label,
                    _fmt_price(min(lows) if lows else None),
                    _fmt_price(max(highs) if highs else None),
                    _fmt_price(newest))

        closed = pl.get("statistics_closed")
        live = pl.get("statistics_live")
        # 全等级混排时 target_rank=None → 统计不按 rank 过滤，_rank_prefix(None) 给「全等级」；
        # 显式等级时过滤到该 rank、前缀「零级/满级/Rx」。
        rp = _rank_prefix(target_rank)
        _4fields = (
            _stat_fields(f"48时内{rp}售价", _sec_arr(closed, "48hours", rank=target_rank)),
            _stat_fields(f"90天内{rp}售价", _sec_arr(closed, "90days", rank=target_rank)),
            _stat_fields(f"48时内{rp}收价", _sec_arr(live, "48hours", "buy", target_rank)),
            _stat_fields(f"90天内{rp}收价", _sec_arr(live, "90days", "buy", target_rank)),
        )
        got = False
        for _f in _4fields:
            if _f:
                lines.append(f"@mstat|{_f[0]}|{_f[1]}|{_f[2]}|{_f[3]}")
                got = True
        if not got:
            lines.append("·价格统计暂无（WM 统计接口未返回）")
    else:
        lines.append("·价格统计暂不可用（WM 统计接口被风控）")

    # 喊话（金色气泡）：优先挑全部挂单里「在线」最低价单（离线玩家喊了没意义）
    if rows:
        # 复用 _is_online：WM 实际返回 "ingame"（无下划线），旧判断只认 "in_game"/"online"
        # → 会漏掉游戏内卖家、错选更贵的在线卖家（2026-09-29 修复）。
        _in_online = [o for o in ordered if _is_online(o)]
        top = min(_in_online, key=lambda o: o.get("platinum") or 1e9) if _in_online else rows[0]
        buyer = (top.get("user") or {}).get("ingameName") or (top.get("user") or {}).get("ingame_name") or "?"
        price = top.get("platinum") or 0
        lines.append(f"@shout|/w {buyer} Hi! I want to buy: {en_name} for {price} platinum. (wf.m via qq_bridge)")

    if total > 1:
        nxt = page + 1 if page < total else 1
        lines.append(f'@page|页码:{page}/{total} 使用"wm {display_item} -{nxt}"查看下一页.')
    lines.append("@foot|WARFRAME.MARKET / PC  ·  右键/长按提取文字")
    return "\n".join(lines)


# ===========================================================================
# 真实玩家头像拉取（2026-11-03 重写第 2 版：搬社区做法 + 公共图片代理）
# ---------------------------------------------------------------------------
# 关键事实（实测 + 真源码 aldi-f/warframe-market.py 交叉验证）：
#   BASE_STATIC_ASSETS_URL = "https://warframe.market/static/assets"
#   头像完整 URL = BASE_STATIC_ASSETS_URL + "/" + user.avatar   （user.avatar 是相对路径）
# 但 warframe.market 主站整站被 Cloudflare 风控：服务端裸请求（含正确路径、含住宅 IP、
# 含有头 Chromium）一律 403 "Just a moment..."——CF 拦的是非浏览器 TLS 指纹 / CDP 自动化，
# 光换 IP 没用（实测：直连住宅 IP 也 403）。api.warframe.market 不受影响（v2 订单正常）。
#
# 因此取图走三级（全部实测过，不是猜的）：
#   ① 直连 static/assets（某些网络/地区/已带 cf_clearance 时可行，最快）；
#   ② 公共图片代理 wsrv.nl 服务端取图（实测 200 + 真实 PNG 22KB）——代理方用自家出口取图，
#      不受浏览器 TLS 指纹限制，这是「抄」社区现成基础设施的正路；
#   ③ 都失败 → 返回 None，渲染层回落确定性生成头像（同 id 同色+同缩写），绝不用假图冒充成功。
# 可选加速：本机 Chrome/Edge 若逛过 WM，可复用其 cf_clearance（DPAPI 读取），仅用于第①级。
# ===========================================================================
WM_STATIC = "https://warframe.market/static/assets/"
WM_IMG_PROXY = "https://wsrv.nl/"   # 公共图片代理（weserv），服务端取图绕开 CF 浏览器指纹
_AVATAR_CACHE_ROOT = Path(os.environ.get(
    "QQ_BRIDGE_CACHE_DIR",
    str(Path(__file__).resolve().parent / ".wm_cache"))) / "avatars"
try:
    _AVATAR_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
except Exception:  # noqa: BLE001
    pass
_WM_CF_COOKIE = _AVATAR_CACHE_ROOT.parent / "cf_cookies.json"
_AVATAR_NEG_TTL = 30 * 60        # 负缓存：全都取不到时别每条消息都重打一遍（30 分钟）
_AVATAR_POS_TTL = 7 * 24 * 3600  # 正缓存：真实头像 7 天
_AVATAR_FETCH_TIMEOUT = 3.5      # 单张直连超时（秒）；无 cf_clearance 时跳过直连（CF 要 3.4s 才回 403，纯浪费）
_AVATAR_PROXY_TIMEOUT = 6.0      # 走公共图片代理的单张超时（代理要先去源站取图，实测 ~3.5s）
_AVATAR_PROXY_SIZE = 128         # 代理侧缩放尺寸（px），PIL 再裁圆，够清晰且省流量
_AVATAR_TOTAL_TIMEOUT = 9.0      # 整批头像拉取硬上限（秒），绝不拖累出卡
_AVATAR_CONCURRENCY = 10         # 一屏 10 行 → 一波并发取完（缓存命中后为 0 耗时）
_DICEBEAR_STYLE = "bottts"       # 无 WM 头像时的确定性生成头像风格（机器人图标，明显是生成图，不冒充真人）

# ---- 游戏内浮印（Glyph）头像（2026-11-03 深挖后新增）----
# 玩家真正的头像在游戏里，叫 Glyph（浮印）。WFCD warframe-status 经
# api.warframe.com/cdn/getProfileViewingData.php 取档案，LoadOutPreset.PresetIcon
# 即玩家当前装备的浮印路径（如 /Lotus/Interface/Icons/Player/xxx.png）。
# warframestat.us 公开档案接口（api.warframestat.us/pc/profile/{name}）内部解析
# 名字→账号，返回解析后的档案，glyph 路径在 preset.icon。
# 图片托管：content.warframe.com/PublicExport（需 !contentHash，直连常 404）与
# browse.wf（按路径即可，无需 hash，实测 200 真 PNG）→ 用 browse.wf 取图。
_GLYPH_WFSTAT_PROFILE = "https://api.warframestat.us/pc/profile/"
_GLYPH_BROWSE_WF = "https://browse.wf"
_GLYPH_TIMEOUT = 6.0            # glyph 档案/取图单步超时（可选增强，失败回落生成头像）

_MISS = object()  # 缓存未命中哨兵（与 None/空字节区分）


def _avatar_cache_path(uid: str, rel: str) -> Path:
    import hashlib
    h = hashlib.md5((uid + "|" + rel).encode("utf-8")).hexdigest()
    return _AVATAR_CACHE_ROOT / (h + ".png")


def _avatar_cache_get(uid: str, rel: str):
    p = _avatar_cache_path(uid, rel)
    if not p.is_file():
        return _MISS
    try:
        import time as _t
        age = _t.time() - p.stat().st_mtime
        data = p.read_bytes()
    except Exception:  # noqa: BLE001
        return _MISS
    if data[:2] == b"\x00\x00":           # 负缓存哨兵
        return None if age < _AVATAR_NEG_TTL else _MISS
    return data if age < _AVATAR_POS_TTL else _MISS


def _avatar_cache_put(uid: str, rel: str, data: bytes | None) -> None:
    p = _avatar_cache_path(uid, rel)
    try:
        p.write_bytes(b"\x00\x00" if data is None else data)
    except Exception:  # noqa: BLE001
        pass


def _cookies_to_httpx(cookies: list[dict]) -> dict:
    return {c["name"]: c["value"] for c in cookies if c.get("name") and c.get("value") is not None}


def _load_cf_cookies() -> list[dict]:
    """取可用于请求 WM 静态资源的 cookie（cf_clearance 等）。

    优先级：① 本机 Chrome/Edge 已通关 Cloudflare 的 cf_clearance（用户常逛 WM，浏览器自带，零操作）；
    ② 一次性 Playwright 通关脚本写入的 cookie 文件。都拿不到返回空列表（→ 头像取不到，渲染层回落生成头像）。
    """
    cookies: list[dict] = []
    try:
        cookies += _load_browser_cf_cookies()
    except Exception:  # noqa: BLE001 — 读浏览器 cookie 失败不影响走文件 cookie
        pass
    try:
        if _WM_CF_COOKIE.is_file():
            import json as _json
            cookies += _json.loads(_WM_CF_COOKIE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    # 只留与 warframe.market 相关的 cookie，避免误带其它站
    return [c for c in cookies
            if "warframe.market" in (c.get("domain") or "") or "cf_clearance" in (c.get("name") or "")]


def _load_browser_cf_cookies() -> list[dict]:
    """Windows 下从本机 Chrome/Edge 的 Cookie 库读出 cf_clearance（DPAPI 解密）。

    用户在本机浏览器逛过 WM → Cloudflare 已通关 → cookie 库里躺着 cf_clearance，
    直接复用即可让服务端请求「像浏览器一样」过 CF（社区通用做法）。Chrome 127+ 的 app-bound
    加密可能解密失败，失败就回落文件 cookie（_wm_clearance 脚本）。纯 best-effort，失败返回空。
    """
    import platform
    if platform.system() != "Windows":
        return []
    try:
        import win32crypt  # pywin32；未装则回落文件 cookie
    except Exception:
        return []
    found: list[dict] = []
    for brand in ("Google/Chrome", "Microsoft/Edge"):
        db = Path(os.environ.get("LOCALAPPDATA", "")) / brand / "User Data" / "Default" / "Cookies"
        if not db.is_file():
            continue
        tmp = None
        try:
            import shutil as _sh, tempfile as _tf
            tmp = _tf.mktemp(suffix=".sqlite")
            _sh.copy2(db, tmp)  # 浏览器占用时直接读会锁，先拷一份
            import sqlite3 as _sq
            conn = _sq.connect(tmp)
            rows = conn.execute(
                "SELECT encrypted_value FROM cookies "
                "WHERE host_key LIKE '%warframe.market%' AND name='cf_clearance'"
            ).fetchall()
            conn.close()
            for (enc,) in rows:
                try:
                    val = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
                    found.append({"name": "cf_clearance",
                                  "value": val.decode("utf-8", "ignore"),
                                  "domain": ".warframe.market"})
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
        finally:
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except Exception:  # noqa: BLE001
                    pass
        if found:
            break
    return found


async def _http_get(url: str, headers: dict, cookies: dict, timeout: float):
    """GET 二进制（头像），封装 httpx 调用便于单测 monkeypatch。失败返回 None。"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as c:
            return await c.get(url, headers=headers, cookies=cookies)
    except Exception:  # noqa: BLE001 — 取不到就回落生成头像
        return None


def _image_bytes(resp) -> bytes | None:
    """从 httpx 响应里取出**真是图片**的字节；不是（CF 403 页/代理错误页）返回 None。"""
    if resp is None or getattr(resp, "status_code", 0) != 200:
        return None
    b = getattr(resp, "content", b"") or b""
    if not b:
        return None
    # 只认魔数，不信 content-type（CF 的 403 页也可能是 text/html 被代理改头）
    # 实测 WM 头像有 png / jpg / webp 三种（webp: RIFF....WEBP）
    if (b[:4] == b"\x89PNG" or b[:4] == b"GIF8" or b[:3] == b"\xff\xd8\xff"
            or (b[:4] == b"RIFF" and b[8:12] == b"WEBP")):
        return b
    return None


def _proxy_avatar_url(full_url: str) -> str:
    """把 WM 头像直链包成 wsrv.nl 代理 URL（服务端取图 + 缩放到 _AVATAR_PROXY_SIZE）。"""
    from urllib.parse import quote
    return ("%s?url=%s&w=%d&h=%d&fit=cover&output=png"
            % (WM_IMG_PROXY, quote(full_url, safe=""), _AVATAR_PROXY_SIZE, _AVATAR_PROXY_SIZE))


async def _fetch_one_avatar(uid: str, rel: str, cookies: list[dict], sem: asyncio.Semaphore) -> bytes | None:
    async with sem:
        cached = _avatar_cache_get(uid, rel)
        if cached is not _MISS:
            return cached
        full = WM_STATIC + rel
        b: bytes | None = None
        # ① 有 cf_clearance 才试直连（无 cookie 时 CF 要 3.4s 才回 403，纯浪费预算）
        _hk = _cookies_to_httpx(cookies)
        if _hk.get("cf_clearance"):
            try:
                r = await _http_get(full, dict(MARKET_UA), _hk, _AVATAR_FETCH_TIMEOUT)
                b = _image_bytes(r)
            except Exception:  # noqa: BLE001
                b = None
        # ② 公共图片代理（weserv 服务端取图，绕开 CF 浏览器 TLS 指纹限制）——主力路径
        if b is None:
            try:
                r = await _http_get(_proxy_avatar_url(full), dict(MARKET_UA), {},
                                    _AVATAR_PROXY_TIMEOUT)
                b = _image_bytes(r)
            except Exception:  # noqa: BLE001
                b = None
        if b is None:
            _avatar_cache_put(uid, rel, None)  # 负缓存：两条路都挂就别每条消息重打
            return None
        _avatar_cache_put(uid, rel, b)
        return b


async def fetch_market_avatars(pairs: list[tuple[str, str]]) -> dict[str, bytes | None]:
    """并发取真实头像：pairs = [(uid, avatar_rel), ...]。返回 uid→bytes（取不到为 None）。

    三级取图：① 直连 static/assets（带 cf_clearance 时可行）→ ② wsrv.nl 公共图片代理
    服务端取图（绕开 CF 浏览器指纹限制，实测可拿到真实 PNG）→ ③ 都失败给 None。
    由渲染层回落确定性生成头像。完全 best-effort，绝不抛异常、绝不超过 _AVATAR_TOTAL_TIMEOUT。
    """
    want: list[tuple[str, str]] = []
    seen: set[str] = set()
    for uid, rel in pairs:
        if not uid or not rel or uid in seen:
            continue
        seen.add(uid)
        want.append((uid, rel))
    if not want:
        return {}
    cookies = _load_cf_cookies()
    sem = asyncio.Semaphore(_AVATAR_CONCURRENCY)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_fetch_one_avatar(u, r, cookies, sem) for u, r in want),
                           return_exceptions=True),
            timeout=_AVATAR_TOTAL_TIMEOUT)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, bytes | None] = {}
    for (u, _r), res in zip(want, results):
        out[u] = None if isinstance(res, Exception) else res
    return out


# ---------------------------------------------------------------------------
# 物品图标拉取（wm 市价卡头条块用）：复用头像代理链路（直连 → wsrv.nl 公共代理）
# WM 图标路径相对 static/assets，主站 CF 风控，直连常被 403，故与头像同走 wsrv.nl 代理。
# ---------------------------------------------------------------------------
_ICON_CACHE_ROOT = _AVATAR_CACHE_ROOT / "icons"
try:
    _ICON_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
except Exception:  # noqa: BLE001
    pass
_ICON_TIMEOUT = 6.0
_ICON_MAX_W = 360         # 商品图代理只限宽（不裁切），原始长宽比交给渲染层自适应
_ICON_NEG_TTL = 30 * 60   # 负缓存：图标取不到别每条消息重打


def _icon_cache_path(rel: str) -> Path:
    import hashlib
    h = hashlib.md5((rel or "").encode("utf-8")).hexdigest()
    return _ICON_CACHE_ROOT / (h + ".png")


def _icon_cache_get(rel: str):
    p = _icon_cache_path(rel)
    if not p.is_file():
        return _MISS
    try:
        age = _now() - p.stat().st_mtime
        if age > _ICON_NEG_TTL and p.stat().st_size == 0:
            return None  # 负缓存（空文件标记取不到）
        if age > 7 * 24 * 3600:
            return _MISS
        return p.read_bytes()
    except Exception:  # noqa: BLE001
        return _MISS


def _icon_cache_put(rel: str, data: bytes | None) -> None:
    try:
        _icon_cache_path(rel).write_bytes(data or b"")
    except Exception:  # noqa: BLE001 — 缓存写失败不影响出卡
        pass


async def fetch_official_art(en_name: str) -> bytes | None:
    """官方 WFCD 武器/战甲全图（512px，**完整不裁切**），走 pic.weapon_icons 缓存链路。

    WM 的 128×128 icon 对宽幅武器是「方形裁切」（Sicarus Prime 的枪管直接伸出画布），
    官方图是完整渲染，故武器/战甲套装优先用；部件/MOD/赋能仍用 WM 图（官方仓库没有部件图）。
    失败返回 None，由调用方回落 WM icon。
    """
    if not en_name:
        return None
    import re as _re
    key = "wm_" + _re.sub(r"[^a-z0-9]+", "_", en_name.lower()).strip("_") + ".png"
    try:
        from ..pic import weapon_icons
        # 单次 cap 6s：官方图源多，别让某个镜像拖住整张卡；失败由 dispatch 的 10s 兜住
        got = await asyncio.wait_for(
            weapon_icons.ensure_icon(key, en_name, timeout=3.0), timeout=6.0)
        p = weapon_icons.icon_cache_path(got) if got else None
        if p:
            return p.read_bytes()
    except Exception:  # noqa: BLE001 — 官方图取不到就回落 WM icon
        return None
    return None


#: WM subIcon → Fandom 通用部位图（带留白、不裁切）；仅部件蓝图用。
_PART_ART_MAP = {
    "prime_helmet": "PrimeHelmet.png",
    "prime_chassis": "PrimeChassis.png",
    "prime_systems": "PrimeSystems.png",
    "prime_barrel": "Barrel.png",
    "prime_receiver": "Receiver.png",
    "prime_stock": "Stock.png",
    "helmet": "Helmet.png",
    "chassis": "Chassis.png",
    "systems": "Systems.png",
    "barrel": "Barrel.png",
    "receiver": "Receiver.png",
    "stock": "Stock.png",
    # 近战 / 投掷 / 曲翼等部件（Fandom 有通用图的类型）
    "blade": "Blade.png",
    "handle": "Handle.png",
    "hilt": "Handle.png",
    "link": "Link.png",
    "pouch": "Pouch.png",
    "stars": "Stars.png",
    "boot": "Boot.png",
    # 不映射 blueprint：Fandom Blueprint.png 是全游戏通用工程图，与商品无关
}
#: Prime 武器/近战部位优先取 WM prime_* subIcon（金饰图），Fandom 只有普通版；
#: 战甲部位 Fandom 有 Prime 专属图，不在本集合。
_PRIME_WM_PREFER = frozenset({
    "prime_barrel", "prime_receiver", "prime_stock", "prime_blade", "prime_handle",
    "prime_hilt", "prime_link", "prime_pouch", "prime_stars", "prime_boot",
})
for _k in ("blade", "handle", "hilt", "link", "pouch", "stars", "boot"):
    _PART_ART_MAP.setdefault("prime_" + _k, _PART_ART_MAP[_k])
del _k
_PART_ART_BASE = "https://static.wikia.nocookie.net/warframe/images"


def _part_art_name(sub_rel: str) -> str | None:
    """`sub_icons/warframe/prime_chassis_128x128.png` → `PrimeChassis.png`；未知返回 None。"""
    base = (sub_rel or "").rsplit("/", 1)[-1].lower()
    base = re.sub(r"_\d+x\d+\.png$", "", base)
    return _PART_ART_MAP.get(base)


async def _fetch_prime_subicon(sub_rel: str) -> bytes | None:
    """Prime 武器/近战部件的 WM `prime_*` subIcon，**按原始比例**取图（不 cover 裁切）。

    WM 部件 subIcon 是宽幅图（prime_barrel 实测 256×150）；`_proxy_avatar_url` 的
    fit=cover 会把两侧裁掉，故这里用「只限宽、不限高」的代理参数保留完整比例。
    只缓存成功结果，失败不写负缓存（下次还能重试）；失败由调用方回落通用部位图。
    """
    if not sub_rel:
        return None
    cache_key = "prime_sub:" + sub_rel
    cached = _icon_cache_get(cache_key)
    if cached is not _MISS:
        return cached
    from urllib.parse import quote
    full = WM_STATIC + sub_rel
    b: bytes | None = None
    try:
        r = await _http_get("%s?url=%s&w=256&output=png" % (WM_IMG_PROXY, quote(full, safe="")),
                            dict(MARKET_UA), {}, _ICON_TIMEOUT)
        b = _image_bytes(r)
    except Exception:  # noqa: BLE001
        b = None
    if b:
        _icon_cache_put(cache_key, b)
    return b


async def fetch_part_art(sub_rel: str) -> bytes | None:
    """部件/蓝图优先取 Fandom 通用部位图（完整带留白）；失败返回 None 由调用方回落 WM subIcon。

    Fandom 的静态图走 `static.wikia.nocookie.net`（实测 200），文件名按 MediaWiki
    规则拼 MD5 路径：`/{h[0]}/{h[:2]}/{filename}/revision/latest`。
    """
    # Prime 武器/近战部件：先取 WM 的 prime_* subIcon（Prime 专属金饰图），
    # 取到即用；取不到再回落 Fandom 通用部位图（仍是真实图，不出现空白占位）。
    _base0 = re.sub(r"_\d+x\d+\.png$", "", (sub_rel or "").rsplit("/", 1)[-1].lower())
    if _base0 in _PRIME_WM_PREFER:
        _pb = await _fetch_prime_subicon(sub_rel)
        if _pb:
            return _pb
    name = _part_art_name(sub_rel)
    if not name:
        return None
    cache_key = "part2:" + name          # v2：避开旧 /revision/latest 404 的负缓存
    cached = _icon_cache_get(cache_key)
    if cached is not _MISS:
        return cached or None
    import hashlib
    h = hashlib.md5(name.encode("utf-8")).hexdigest()
    base = "%s/%s/%s/%s" % (_PART_ART_BASE, h[0], h[:2], name)
    b: bytes | None = None
    # ① 直链（实测稳定 200，200×200）；② /revision/latest（部分 CDN 节点 404）
    for url in (base, base + "/revision/latest"):
        try:
            r = await _http_get(url, dict(MARKET_UA), {}, _ICON_TIMEOUT)
            b = _image_bytes(r)
        except Exception:  # noqa: BLE001
            b = None
        if b:
            break
    if b is None:
        # ③ 公共图片代理再试一次（与头像/图标同一套基础设施）
        try:
            from urllib.parse import quote
            r = await _http_get(
                "%s?url=%s&w=512&output=png" % (WM_IMG_PROXY, quote(base, safe="")),
                dict(MARKET_UA), {}, _ICON_TIMEOUT)
            b = _image_bytes(r)
        except Exception:  # noqa: BLE001
            b = None
    _icon_cache_put(cache_key, b)
    return b


async def fetch_item_icon(icon_rel: str) -> bytes | None:
    """拉取物品图标（PNG 字节，best-effort）。失败返回 None，渲染层回落几何占位。

    路径相对 static/assets（与头像同源），主站 CF 风控 → 直连常 403，
    故主力走 wsrv.nl 公共图片代理（与头像同一套链路，实测可拿到真实 PNG）。
    """
    if not icon_rel:
        return None
    key = "ar2:" + icon_rel   # 避开旧 fit=cover 方图缓存
    cached = _icon_cache_get(key)
    if cached is not _MISS:
        return cached
    full = WM_STATIC + icon_rel
    b: bytes | None = None
    # ① 直连（带 cf_clearance 时可行）
    cookies = _load_cf_cookies()
    _hk = _cookies_to_httpx(cookies)
    if _hk.get("cf_clearance"):
        try:
            r = await _http_get(full, dict(MARKET_UA), _hk, _AVATAR_FETCH_TIMEOUT)
            b = _image_bytes(r)
        except Exception:  # noqa: BLE001
            b = None
    # ② 代理只限宽不限高（不 fit=cover），保留原始长宽比给渲染层自适应
    if b is None:
        try:
            from urllib.parse import quote
            r = await _http_get(
                "%s?url=%s&w=%d&output=png" % (WM_IMG_PROXY, quote(full, safe=""), _ICON_MAX_W),
                dict(MARKET_UA), {}, _ICON_TIMEOUT)
            b = _image_bytes(r)
        except Exception:  # noqa: BLE001
            b = None
    _icon_cache_put(key, b)
    return b


# ---------------------------------------------------------------------------
# 无 WM 头像玩家的确定性生成头像（兜底，不做假图冒充真人照片）
# ---------------------------------------------------------------------------
# 真实调研结论（2026-11-03 深挖）：Warframe.Market 公开 API 对约 53% 的卖家
# 根本没有头像数据——orders 端点与 /v2/user/{slug} 端点的 user 对象都**省略 avatar 字段**
# （不是空串，是字段不存在）；WM 主站被 CF 风控，静态资源也无默认头像 URL
# （weserv 探了 15+ 个候选路径全 404）。这些玩家在 WM 上确实没有图。
# 游戏内 Profile Icon 虽人人都有（默认 4 款），但 WM 不暴露该字段，无法经 WM 取得。
# 业界标准兜底（GitHub/GitLab/Google 同思路）：用确定性种子按 uid 生成头像，同 uid 永远同图，
# 离线也有本地几何兜底。生成的是明显的图标/机器人，不冒充真人照片。
# ===========================================================================
def _gen_avatar_cache_path(uid: str) -> Path:
    import hashlib
    return _AVATAR_CACHE_ROOT / ("gen_" + hashlib.md5(uid.encode("utf-8")).hexdigest() + ".png")


def _gen_avatar_cache_get(uid: str):
    p = _gen_avatar_cache_path(uid)
    if not p.is_file():
        return _MISS
    try:
        return p.read_bytes()
    except Exception:  # noqa: BLE001
        return _MISS


def _gen_avatar_cache_put(uid: str, data: bytes) -> None:
    try:
        _gen_avatar_cache_path(uid).write_bytes(data)
    except Exception:  # noqa: BLE001
        pass


def _dicebear_url(seed: str) -> str:
    from urllib.parse import quote
    return ("https://api.dicebear.com/9.x/%s/png?seed=%s&size=128&radius=50"
            "&backgroundType=gradientLinear" % (_DICEBEAR_STYLE, quote(seed or "anon")))


def _local_identicon(uid: str) -> bytes:
    """离线兜底：由 uid 哈希生成 5x5 对称几何头像（GitHub identicon 风格），返回 PNG 字节。"""
    import hashlib
    import io
    from PIL import Image, ImageDraw
    h = int(hashlib.md5(uid.encode("utf-8")).hexdigest(), 16)
    size = 128
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = ((h >> 24) & 0xFF, (h >> 16) & 0xFF, (h >> 8) & 0xFF)
    d.rectangle((0, 0, size, size), fill=bg + (255,))
    cell = size // 5
    fg = ((h) & 0xFF, (h >> 8) & 0xFF, (h >> 16) & 0xFF)
    for y in range(5):
        for x in range(3):
            if (h >> (y * 3 + x)) & 1:
                d.rectangle((x * cell, y * cell, (x + 1) * cell - 2, (y + 1) * cell - 2), fill=fg + (255,))
                d.rectangle(((4 - x) * cell, y * cell, (5 - x) * cell - 2, (y + 1) * cell - 2), fill=fg + (255,))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def generate_avatar(uid: str, ingame: str = "", sem: asyncio.Semaphore | None = None) -> bytes:
    """为「WM 无头像」玩家生成确定性头像。同 uid 永远同图；DiceBear 不可达时回落本地几何。

    绝不返回 None——保证每个玩家都有一张可显示的图（明确是生成的，不冒充真人照片）。
    """
    sem = sem or asyncio.Semaphore(1)
    async with sem:
        cached = _gen_avatar_cache_get(uid)
        if cached is not _MISS:
            return cached
        b: bytes | None = None
        try:
            r = await _http_get(_dicebear_url(uid or ingame or "anon"),
                                dict(MARKET_UA), {}, _AVATAR_PROXY_TIMEOUT)
            if r is not None and getattr(r, "status_code", 0) == 200 and r.content[:4] == b"\x89PNG":
                b = r.content
        except Exception:  # noqa: BLE001 — DiceBear 不可达就本地生成
            b = None
        if b is None:
            b = _local_identicon(uid or ingame or "anon")
        _gen_avatar_cache_put(uid, b)
        return b


# ---------------------------------------------------------------------------
# 游戏内浮印（Glyph）头像：玩家真正的游戏头像，按昵称经 warframestat.us 档案接口取得
# ---------------------------------------------------------------------------
def _json_body(resp):
    if resp is None or getattr(resp, "status_code", 0) != 200:
        return None
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def _extract_glyph_path(d):
    """从 warframestat.us 档案 JSON 抽出玩家当前浮印路径（/Lotus/.../Player/xxx.png）。"""
    if not isinstance(d, dict):
        return None
    # 解析后档案：preset.icon（来自 LoadOutPreset.PresetIcon）
    p = d.get("preset") or d.get("LoadOutPreset") or {}
    if isinstance(p, dict):
        v = p.get("icon") or p.get("PresetIcon")
        if isinstance(v, str) and v.endswith(".png"):
            return v
    # 原始档案：Results[0].LoadOutPreset.PresetIcon
    res = d.get("Results")
    if isinstance(res, list) and res and isinstance(res[0], dict):
        lp = res[0].get("LoadOutPreset") or {}
        if isinstance(lp, dict):
            v = lp.get("PresetIcon")
            if isinstance(v, str) and v.endswith(".png"):
                return v
    return None


def _glyph_cache_path(name: str) -> Path:
    import hashlib
    return _AVATAR_CACHE_ROOT / ("glyph_" + hashlib.md5(name.encode("utf-8")).hexdigest() + ".png")


def _glyph_cache_get(name: str):
    p = _glyph_cache_path(name)
    if not p.is_file():
        return _MISS
    try:
        return p.read_bytes()
    except Exception:  # noqa: BLE001
        return _MISS


def _glyph_cache_put(name: str, data: bytes) -> None:
    try:
        _glyph_cache_path(name).write_bytes(data)
    except Exception:  # noqa: BLE001
        pass


async def fetch_player_glyph(name: str, sem: asyncio.Semaphore | None = None) -> bytes | None:
    """按玩家昵称取游戏内浮印（Glyph）头像字节。best-effort，失败返回 None。

    流程：warframestat.us/pc/profile/{name} → preset.icon（浮印路径）
          → browse.wf{路径}（DE 浮印图托管镜像，无需 contentHash，实测 200 真 PNG）。
    任意一步失败都返回 None，由上层回落生成头像。这是玩家自己装备的游戏图标，明确不是真人照片。
    """
    if not name:
        return None
    from urllib.parse import quote as _quote
    sem = sem or asyncio.Semaphore(1)
    async with sem:
        cached = _glyph_cache_get(name)
        if cached is not _MISS:
            return cached or None
        # ① 解析档案，拿浮印路径
        try:
            r = await _http_get(_GLYPH_WFSTAT_PROFILE + _quote(name), dict(MARKET_UA), {}, _GLYPH_TIMEOUT)
        except Exception:  # noqa: BLE001
            r = None
        path = _extract_glyph_path(_json_body(r))
        if not path:
            _glyph_cache_put(name, b"")  # 负缓存，避免重复打接口
            return None
        # ② 经 browse.wf 取真图（按路径即可，无需 contentHash）
        try:
            r2 = await _http_get(_GLYPH_BROWSE_WF + path, dict(MARKET_UA), {}, _GLYPH_TIMEOUT)
        except Exception:  # noqa: BLE001
            r2 = None
        b = _image_bytes(r2)
        _glyph_cache_put(name, b if b else b"")
        return b


async def _glyph_or_generated(uid: str, ingame: str, sem: asyncio.Semaphore) -> bytes:
    """无 WM 头像玩家：优先游戏内浮印，失败（取不到/CF 等）回落确定性生成头像。"""
    b = await fetch_player_glyph(ingame or uid, sem)
    if b:
        return b
    return await generate_avatar(uid, ingame, sem)


async def riven_text(weapon: str) -> str:
    """紫卡（裂罅 MOD）拍卖：最近 100 条内按武器过滤，最低起拍 / 一口价。"""
    if not weapon:
        return ("格式：#wf 紫卡 <武器>（例：#wf 紫卡 绝路 / #wf 紫卡 rubico prime）\n"
                "分类说明：#wf 分类")
    url_name = _resolve_riven_weapon(weapon) or _resolve_url_name(weapon)
    d = await _market_get(f"{MARKET_BASE}/v1/auctions")
    if not d:
        return (f"紫卡市场暂时没响应（风控或网络波动），稍后再试～\n"
                f"可去官网看：https://riven.market/")
    _record_market_ts(d)
    payload = d.get("payload") or {}
    auc = payload.get("auctions") or []
    if not auc:
        return f"「{weapon}」紫卡市场暂无挂单（英文名 {url_name}）"
    # WM v1 auctions 只返回最近 100 条且不支持筛选：客户端按武器名过滤（精确优先，再包含）
    hit = [a for a in auc if ((a.get("item") or {}).get("weapon_url_name") or "").lower() == url_name]
    if not hit:
        hit = [a for a in auc if url_name in ((a.get("item") or {}).get("weapon_url_name") or "").lower()]
    if not hit:
        return (f"最近 100 条紫卡拍卖里没有「{weapon}」（{url_name}）。\n"
                f"试试热门武器（绝路/救赎者 Prime），或去 https://riven.market/ 手动查")
    priced = [a for a in hit if a.get("buyout_price") or a.get("starting_bid")]
    priced.sort(key=lambda a: (a.get("buyout_price") or a.get("starting_bid") or 1e9))
    lines = [f"【{weapon}】紫卡在售 {len(hit)} 条（最近拍卖）"]
    for a in priced[:6]:
        it = a.get("item") or {}
        mr = it.get("mod_rank", "?")
        st = a.get("starting_bid") or 0
        bo = a.get("buyout_price") or 0
        p = f"起拍 {st}p" if bo == 0 else f"一口价 {bo}p"
        owner = ((a.get("owner") or {}).get("ingame_name")) or "?"
        lines.append(f"· 洗卡{mr}｜{p}｜卖家 {owner}")
    lines.append("紫卡看走势：https://semlar.com/riven/ ｜ 交易：https://riven.market/")
    return "\n".join(lines)


def wiki_text(keyword: str) -> str:
    """灰机 Wiki 搜索链接（同 TWGBot /WIKI 设计，简单可靠无 key）。"""
    if not keyword:
        return "格式：#wf wiki <关键词>（例：#wf wiki 绝路 / #wf wiki 星际战甲）"
    import urllib.parse
    q = urllib.parse.quote(keyword.strip())
    return f"灰机 Wiki 搜索「{keyword.strip()}」：\n{WIKI_SEARCH}{q}"


def translate_text(word: str) -> str:
    """战甲/武器 中英名 + 外号 双向互译（原名 ↔ 中文名 ↔ 多外号）。
    
    数据：官方 i18n（zh_names.json）+ 社区黑话（aliases.json），离线秒回；
    未命中回退 灰机 Wiki 搜索链接。
    """
    if not word:
        return "格式：#wf 翻译 <词>（例：#wf 翻译 咖喱 / #wf 翻译 Rubico Prime）"
    w = word.strip()
    _load_translate_data()
    low = w.lower()

    # 1) 外号/中文名/英文 直查（多外号表）
    en_low = _alias_lookup.get(low)
    if en_low:
        info = _aliases[en_low]
        zh = info.get("zh") or info.get("en") or ""
        aliases = info.get("aliases") or []
        lines = [f"【星际战甲 · 翻译】「{w}」", f"类型：{info.get('cat', '?')}"]
        lines.append(f"中文名：{zh}")
        lines.append(f"英文名：{info.get('en', '')}")
        if aliases:
            lines.append("外号：" + "、".join(aliases))
        return "\n".join(lines)

    # 2) 英文名直查（官方 i18n 表）
    if _zh_map is not None and low in _zh_map:
        info = _zh_map[low]
        return (f"【星际战甲 · 翻译】「{w}」\n"
                f"类型：{info.get('cat', '?')}\n"
                f"中文名：{info.get('zh', '')}\n"
                f"英文名：{info.get('en', '')}")

    # 3) 官方中文名反查英文
    if _zh_lookup is not None and w in _zh_lookup:
        ens = _zh_lookup[w][:3]
        cat = _zh_map.get(ens[0].lower(), {}).get("cat", "?") if ens and _zh_map else "?"
        return (f"【星际战甲 · 翻译】「{w}」\n"
                f"类型：{cat}\n"
                f"英文名：{ens[0]}" + (f"（同译：{ens[1]}）" if len(ens) > 1 else ""))

    # 4) 小词表兜底（赋能等非战甲/武器词）
    for k, v in TRANSLATE_WORDS.items():
        if k == w or k in w or w in k:
            return f"「{w}」→ {v}"

    import urllib.parse
    return (f"词表里没有「{w}」，去灰机 Wiki 查：\n"
            f"{WIKI_SEARCH}{urllib.parse.quote(w)}")


def help_text() -> str:
    return (
        "【星际战甲（国际服）攻略】#wf 开头，或直接说口令：\n"
        "状态查询：#wf 突击 / #wf 奸商 / #wf 特惠 / #wf 裂隙 / #wf 入侵 /\n"
        "  #wf 仲裁（当前+下个任务）/ #wf 仲裁表（逐时轮换全量班次表·生息效率·评级，整张长图直接出）/\n"
        "  #wf 电波 / #wf 平原（全部平原昼夜）/ #wf 赏金 /\n"
        "  #wf 九重天（虚空风暴）/ #wf 周本（周期任务一览）\n"
        "  执刑官 / 执政官 / 执行官（本周执刑官猎杀卡：BOSS+三阶段任务+剩余时间）\n"
        "任务订阅：订阅 钢铁裂隙 后纪 生存（不限地图，刷出即推单张任务卡）\n"
        "  订阅列表 / 取消订阅 <订阅号> / 取消订阅 全部\n"
        "市价查询：#wf 价格 <物品>（例：#wf 价格 充沛）\n"
        "  #wf 紫卡 <武器>（例：#wf 紫卡 绝路）\n"
        "  #wf 分类（Warframe.Market 物品分类一览）\n"
        "  #wf 紫卡图（发紫卡截图 + @机器人：Rabbit 版完整紫卡分析卡）\n"
        "  #wf 紫卡分析 <武器>（例：#wf 紫卡分析 绝路 —— 面板+现行倍率+洗卡建议）\n"
        "  #wf 词条推荐 <武器>（例：#wf 词条推荐 凤殁 —— 击杀效率+市场挂牌分层 洗卡推荐整卡）\n"
        "  #wf 倾向 <武器>（统一 1.20 + 改动前对照；2026-10-22 起 DE 移除武器差异）\n"
        "  #wf 紫卡趋势 <武器>（本地采样中位价走势，挂牌非成交价）\n"
        "  #wf 词条价差 <武器>（正面词条同分位溢价排行）\n"
        "  #wf 紫卡融合 [武器]（融合机制 + Wiki 费用表 + 真实中位价，武器可空）\n"
        "  #wf 紫卡全面 [武器]（655×1920 完整分析卡；发截图走真实 OCR）\n"
        "  #wf 赤毒 <武器> / #wf 信条 <武器>（同价格查询）\n"
        "轮换查询：#wf 轮换（信条/终幕武器轮换 + 赤毒/灵化说明）\n"
        "  #wf 信条（佩兰数列信条武器轮换卡：5 把武器 + 元素加成 + 倒计时，单列粉紫版）\n"
        "  #wf 终幕（终幕/Coda 信条武器轮换卡：埃莉诺售卖清单 + 元素加成 + 刷新倒计时）\n"
        "  ※ 信条与终幕是两个口令、两套版式；#wf 信条 <武器> 才是价格查询\n"
        "资料查询：#wf wiki <关键词>（灰机 Wiki 搜索）\n"
        "  #wf 翻译 <词>（战甲/武器 中英名+外号 双向互译）\n"
        "  #wf 战甲 <名>（面板+被动+技能，例：#wf 战甲 圣剑）\n"
        "  #wf mod <名>（MOD 类型/耗能/满级效果，例：#wf mod 聚精会神）\n"
        "  #wf 赋能 <名>（赋能满级效果，例：#wf 赋能 充沛）\n"
        "  #wf 掉落 <物品>（官方掉落表：哪个图/概率）\n"
        "  #wf 遗物 <核桃或部件>（掉落内容+入库状态，例：#wf 遗物 后纪 A1）\n"
        "  #wf 配卡 <武器或战甲>（本地启发式配卡建议 + overframe 链接）\n"
        "  #wf 金垃圾（Prime 部件杜卡德价值分档）\n"
        "  #wf 结合目标 <敌人>（大黄脸扫描地点）\n"
        "数据源：Tenno Tools + Warframe.Market + 灰机 Wiki（国际服 PC）"
    )


async def riven_analysis_text(weapon: str) -> str:
    """紫卡评估：武器面板 + 现行统一倍率 + 洗卡建议（离线数据，零外呼）。"""
    if not weapon:
        return "格式：#wf 紫卡分析 <武器>（例：#wf 紫卡分析 绝路 / #wf 紫卡分析 rubico prime）"
    info = _resolve_weapon(weapon)
    if not info:
        return ("没找到武器「" + weapon + "」。试试英文名（如 rubico prime）或中文名/外号；\n"
                "战甲没有紫卡，只能评估武器。")
    lines = [f"【{info['en']} 紫卡分析】{info.get('cat', '')}"]
    def _fmt(v, suffix=""):
        if v is None:
            return "-"
        if isinstance(v, float):
            v = round(v, 2)
        return f"{v:g}{suffix}"
    # 面板
    panel = []
    panel.append(f"总伤 {_fmt(info.get('totalDamage'))}")
    cc = info.get("criticalChance")
    if cc is not None:
        panel.append(f"暴率 {_fmt(cc * 100)}%")
    cm = info.get("criticalMultiplier")
    if cm is not None:
        panel.append(f"暴伤 {_fmt(cm)}x")
    panel.append(f"射速 {_fmt(info.get('fireRate'))}")
    sc = info.get("statusChance")
    if sc is not None:
        panel.append(f"触发 {_fmt(sc * 100)}%")
    panel.append(f"多重 {_fmt(info.get('multishot'))}")
    lines.append("面板：" + "｜".join(panel))
    # 倾向：现行统一口径（旧分档点评已作废）
    lines.append(f"倾向：{_disp_desc()}")
    _chg = disp_change_text(info.get("disp_before"))
    if _chg:
        lines.append("　　　" + _chg)
    # 洗卡建议（基于面板的启发式规则）
    recs = []
    cat = info.get("cat", "")
    if cc is not None and cc >= 0.25:
        recs.append("暴击率/暴击伤害")
    if sc is not None and sc >= 0.25:
        recs.append("触发几率/元素伤害")
    if info.get("multishot") is not None and info.get("multishot") < 2 and cat != "近战武器":
        recs.append("多重射击")
    if cat == "近战武器":
        recs.append("近战伤害/攻速/范围")
    else:
        recs.append("基伤/元素伤害")
    lines.append("洗卡建议：")
    lines.extend("  · " + r for r in dict.fromkeys(recs))
    lines.append("市价：发「#wf 紫卡 " + info["en"] + "」看实时（市场被风控时看 riven.market）")
    return "\n".join(lines)


async def riven_disposition_text(weapon: str) -> str:
    """紫卡现行规则速查（保留 `#wf 倾向` 口令）。

    2026-10-22 热修 44.1.3 起 DE 移除武器倾向差异，全部紫卡统一 1.2。
    本卡不再输出星级点阵与「热门/冷门」分档投资建议，改为
    「现行 1.20（全武器统一）+ 改动前倍率对照 + 现行规则下的洗卡提示」。

    `@disp` 字段（新版）：name | cat | 现行倍率 | 改动前倍率(可空) | 图标文件(可空)
    """
    if not weapon:
        return ("格式：#wf 倾向 <武器>（例：#wf 倾向 绝路 / rubico prime）\n"
                "2026-10-22 起全武器紫卡统一 1.20，可附改动前对照。")
    info = _resolve_weapon(weapon)
    if not info:
        return ("没找到武器「" + weapon + "」。试试英文名（如 rubico prime）或中文名/外号；\n"
                "战甲没有紫卡，只能查武器。")
    cat = _CAT_SHORT.get(info.get("cat", "") or "", info.get("cat", "") or "武器")
    before = info.get("disp_before")
    change = disp_change_text(before)
    try:
        from ..pic.weapon_icons import ensure_icon
        _icon = await ensure_icon(info.get("imageName"), info.get("en"))
    except Exception:  # noqa: BLE001 — 图源抖动绝不能影响出卡
        _icon = None
    _disp = "@disp|%s|%s|%.2f|%s" % (
        _weapon_zh(info["en"]), cat, RIVEN_DISPOSITION, change)
    if _icon:
        _disp += "|" + _icon
    lines = [
        "@ttl|紫卡倾向（已统一）|book",
        "@sub|%s 起 DE 热修 44.1.3 移除武器倾向差异|24" % RIVEN_DISPO_RETIRED,
        _disp,
        "@foot|· 现行规则下所有武器紫卡同强度，是否值得投入看武器本体与词条组合。",
    ]
    # 对照文案已放进 @disp 面板第 4 字段，不在此重复一行（避免卡面冗余）。
    recs = []
    if (info.get("criticalChance") or 0) >= 0.25:
        recs.append("暴击率/暴击伤害")
    if (info.get("statusChance") or 0) >= 0.25:
        recs.append("触发几率/元素伤害")
    ms = info.get("multishot")
    if ms is not None and ms < 2 and info.get("cat") != "近战武器":
        recs.append("多重射击")
    recs.append("近战伤害/攻速/范围" if info.get("cat") == "近战武器" else "基伤/元素伤害")
    lines.append("@foot|· 洗卡建议：" + " · ".join(dict.fromkeys(recs)))
    lines.append("@page|完整面板（总伤/暴率/暴伤…）：发「#wf 紫卡分析 " + info["en"] + "」")
    return "\n".join(lines)


# ===========================================================================
# 战甲信息 / MOD / 赋能 查询（离线 warframe-items + 官方 i18n 中文，零请求）
# ===========================================================================
_CARDS: dict | None = None


def _load_cards() -> dict:
    _record_local_ts()
    global _CARDS
    if _CARDS is None:
        import json as _json
        base = _DATA_DIR
        def _read(name):
            return _json.loads((base / name).read_text(encoding="utf-8"))
        _CARDS = {
            "wf": _read("warframe_full.json"),
            "mod": _read("mod_full.json"),
            "arc": _read("arcane_full.json"),
        }
    return _CARDS


def _resolve_card(q: str, table: dict, exact_only: bool = False):
    """按 英文名 / 中文名 / 外号 解析资料条目，返回 (key, item) 或 None。

    exact_only=True 时只做精确匹配（map/中文反查/外号），跳过包含匹配——
    用于多表兜底时优先「充沛」→ 赋能，而不是「充沛射击」MOD。
    """
    q = (q or "").strip()
    if not q:
        return None
    low = q.lower()
    mp = table.get("map") or {}
    if low in mp:
        return (low, mp[low])
    ens = (table.get("zh2en") or {}).get(q) or (table.get("zh2en") or {}).get(low)
    if ens:
        key = ens[0].lower()
        return (key, mp[key])
    _load_translate_data()
    en_low = (_alias_lookup or {}).get(low)
    if en_low and en_low in mp:
        return (en_low, mp[en_low])
    if exact_only:
        return None
    # 中文包含匹配（去空格精确优先，如「暗影聚精会神」→「暗影 聚精会神」Umbral Intensify）
    q2 = q.replace(" ", "")
    cands = []
    for zk, zens in (table.get("zh2en") or {}).items():
        if not zk:
            continue
        zk2 = zk.replace(" ", "")
        if zk2 == q2:
            cands.append((len(zk2), zens))
    if not cands:
        # 子串兜底**同样加长度门槛 len(q2) >= 3**：zh2en 表不止中文键，
        # 还含英文键（Rhino / Loki Prime / Equinox…）——"hi" ⊂ "Rhino"、
        # "ok" ⊂ "Loki"、"no" ⊂ "Equinox" 全从这条漏（2026-10-19 线上
        # 群里发 "hi" 回 Rhino 卡的根因，与下方英文字串路同款修法）。
        # 精确/别名路不受影响：2 字真实名（犀牛/MAG）走上面精确匹配。
        if len(q2) >= 3:
            for zk, zens in (table.get("zh2en") or {}).items():
                if zk and (zk.replace(" ", "") in q2 or q2 in zk.replace(" ", "")):
                    cands.append((len(zk), zens))
    if cands:
        cands.sort(key=lambda x: -x[0])
        key = cands[0][1][0].lower()
        return (key, mp[key])
    _load_translate_data()
    en_low = (_alias_lookup or {}).get(low)
    if en_low and en_low in mp:
        return (en_low, mp[en_low])
    # 英文名包含匹配（仅唯一命中时采用）。**长度门槛 len(low) >= 3**：
    # 短查询子串匹配是误触工厂——2026-10-19 线上实测群里发 "hi" 回了
    # Rhino 卡（r-HI-no prime）、"ok"→loki prime（l-OK-i）、
    # "no"→equinox prime（equi-NO-x），群聊打招呼被当战甲查询。
    # 真实英文名/别名均 ≥3 字符；上面的精确与别名路已覆盖 nyx/mag/volt 短名。
    if len(low) >= 3:
        hits = [k for k in mp if low in k or k in low]
        if len(hits) == 1:
            return (hits[0], mp[hits[0]])
    return None


# ===========================================================================
# 战甲半身像：warframe-items 的 wikiaThumbnail 指向官方 Wiki 图片文件名，
# fandom 域名被墙但 static.wikia.nocookie.net CDN 可达，图片路径 = MediaWiki
# md5 分片 /images/{h[0]}/{h[:2]}/{filename}/revision/latest（本地可算，零 API）。
# 首次查询下载一次，之后走本地缓存 portraits/，重复查询零请求（克制外呼）。
# ===========================================================================
_PORTRAIT_DIR = _DATA_DIR / "portraits"
_IMG_MAP: dict | None = None
_PORTRAIT_FAIL: set = set()   # 已确认下载失败的 key，避免反复轰炸同一图源


def _load_img_map() -> dict:
    global _IMG_MAP
    if _IMG_MAP is None:
        _IMG_MAP = {}
        try:
            import json as _json
            raw = _json.loads((_DATA_DIR / "Warframes.json").read_text(encoding="utf-8"))
            for it in raw:
                nm = str(it.get("name") or "").strip().lower()
                if not nm:
                    continue
                wt = str(it.get("wikiaThumbnail") or "")
                fn = wt.split("/")[-1].split("?")[0].strip() if "/" in wt else ""
                if fn:
                    _IMG_MAP[nm] = fn
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wf_img_map_failed: {type(e).__name__}: {e}")
    return _IMG_MAP


async def _portrait_path(word: str) -> str | None:
    """战甲名/别名 → 本地半身像缓存路径（无则下载一次，失败返回 None）。

    仅对「战甲」生效：resolve 落在 wf 表且 Warframes.json 有 wiki 图文件时
    才尝试；MOD/赋能/其它子命令一律 None（零外呼）。
    """
    try:
        res = _resolve_card(word or "", _load_cards()["wf"])
        if not res:
            return None
        key = res[0]
        if key in _PORTRAIT_FAIL:
            return None
        out = _PORTRAIT_DIR / ("%s.png" % key.replace(" ", "_"))
        if out.exists():
            return str(out)
        fn = _load_img_map().get(key) or _load_img_map().get(key.replace(" ", ""))
        if not fn:
            _PORTRAIT_FAIL.add(key)
            return None
        import hashlib
        h = hashlib.md5(fn.encode("utf-8")).hexdigest()
        url = "https://static.wikia.nocookie.net/warframe/images/%s/%s/%s/revision/latest" % (
            h[0], h[:2], fn)
        # 硬超时 4s：wikia CDN 对中国网络慢，头像只是装饰——超时立即放弃，
        # 绝不让消息卡几十秒（2026-09-16 线上「奶妈没反应」根因：下载挂起吞掉回复）
        r = await asyncio.wait_for(_fetch_bytes(url), timeout=4.0)
        if not r:
            _PORTRAIT_FAIL.add(key)
            return None
        _PORTRAIT_DIR.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r)
        return str(out)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_portrait_failed word={word} err={type(e).__name__}: {e}")
        return None


async def _fetch_bytes(url: str) -> bytes | None:
    """GET 二进制（图），一次尝试不重试（半身像是装饰，失败即无图降级）。"""
    await _rate_gate()
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": UA["User-Agent"], "Accept": "image/*"},
            follow_redirects=True, timeout=_TIMEOUT, verify=True,
        ) as c:
            r = await c.get(url)
            r.raise_for_status()
            b = r.content
            if not b or len(b) > 4 * 1024 * 1024:
                return None
            return b
    except Exception:  # noqa: BLE001
        return None


async def warframe_info_text(word: str) -> str:
    """战甲信息：面板 + 被动 + 技能（中文）。"""
    if not word:
        return "格式：#wf 战甲 <名称>（例：#wf 战甲 圣剑 / #wf 战甲 volt prime / #wf 战甲 电男）"
    res = _resolve_card(word, _load_cards()["wf"])
    if not res:
        return ("没找到战甲「" + word + "」。试试英文名（volt prime）或外号（圣剑/电男/毒妈/奶妈）；\n"
                "查 MOD 用 #wf mod <名称>，赋能用 #wf 赋能 <名称>")
    key, w = res
    zh = w.get("zh") or key
    lines = [f"【{zh}】{key}" if str(zh).lower() != key else f"【{zh}】"]
    desc = (w.get("desc") or "").strip()
    if desc:
        lines.append("· " + desc)
    panel = []
    if w.get("health") is not None:
        panel.append(f"生命 {w['health']}")
    if w.get("shield") is not None:
        panel.append(f"护盾 {w['shield']}")
    if w.get("armor") is not None:
        panel.append(f"护甲 {w['armor']}")
    if w.get("energy") is not None:
        panel.append(f"能量 {w['energy']}")
    if w.get("sprint") is not None:
        panel.append("速度 %.2f" % float(w["sprint"]))
    lines.append("面板：" + "｜".join(panel))
    if w.get("passive"):
        # warframe-items 的被动/描述常带 |DURATION| 等模板占位符，无数值可填，
        # 直接去除占位符，绝不编造数值
        _ps = re.sub(r"\s*\|[A-Z_]+\|\s*%?", "", str(w["passive"])).strip()
        _ps = re.sub(r"\s{2,}", " ", _ps)
        lines.append("被动：" + (_ps or "…"))
    ab = (w.get("abilities") or [])[:4]
    if ab:
        lines.append("【技能】")
        for a in ab:
            desc = (a.get("desc") or "").strip()
            lines.append(f"  · {a['zh']}（{a['en']}）：{desc or '…'}")
    # 集团卡：每个技能的对应强化 MOD（warframe-items isAugment 数据）
    augs = _augments_for(key)
    if augs:
        lines.append("【集团卡】")
        _shown = 0
        for _a in augs:
            if _shown >= 6:
                break
            _fx = _a["fx"]
            # mod_full 缺中文翻译的条目（效果为纯英文）不显示，避免英文混排
            if not re.search(r"[\u4e00-\u9fff]", _fx):
                continue
            lines.append(f"  · {_a['zh']}（{_a['en']}）：{_fx or '…'}")
            _shown += 1
        if not _shown:
            lines.pop()  # 全无中文时撤掉「集团卡：」标题
    return "\n".join(lines)


async def mod_text(word: str) -> str:
    """MOD 查看：类型 / 稀有度 / 极性 / 耗能 / 满级效果（中文）。"""
    if not word:
        return "格式：#wf mod <名称>（例：#wf mod 聚精会神 / #wf mod intensify / #wf mod 暗影聚精会神）"
    res = _resolve_card(word, _load_cards()["mod"])
    if not res:
        return "没找到 MOD「" + word + "」（试试中文名如 聚精会神/瞬时坚毅，或英文名 intensify）"
    key, m = res
    lines = [f"【{m['zh']}】{key}"]
    meta = [m.get("type") or "?", m.get("rarity") or "?"]
    if m.get("polarity"):
        meta.append("极性 " + m["polarity"])
    if m.get("drain") is not None:
        meta.append("基础耗能 " + str(m["drain"]))
    lines.append("｜".join(meta))
    if m.get("max"):
        lines.append("满级效果：")
        for s in m["max"]:
            if len(s) > 60:
                s = s[:60] + "…"
            lines.append("  · " + s)
    return "\n".join(lines)


async def arcane_text(word: str) -> str:
    """赋能查看：稀有度 + 满级效果（中文）。"""
    if not word:
        return "格式：#wf 赋能 <名称>（例：#wf 赋能 充沛 / #wf 赋能 arcane energize / #wf 赋能 优雅）"
    res = _resolve_card(word, _load_cards()["arc"])
    if not res:
        return ("没找到赋能「" + word + "」（试试中文名如 充沛/优雅/复仇，或英文名 arcane energize）；\n"
                "赋能效果以满级（5 级）为准。")
    key, a = res
    lines = [f"【{a['zh']}】{key}", "稀有度：" + (a.get("rarity") or "?")]
    if a.get("max"):
        lines.append("满级效果：")
        for s in list(dict.fromkeys(a["max"])):  # 去重（复活次数等重复行）
            if len(s) > 60:
                s = s[:60] + "…"
            lines.append("  · " + s)
    return "\n".join(lines)


# ===========================================================================
# 仓鼠运势 / 仓鼠黄历 / 模拟开卡（2026-09-17 新增）
# 玩法参考：Warframe Rabbit「仓鼠老黄历」（B站专栏 cv17866589，闭源，按公开
# 使用说明重建）+ nonebot-plugin-fortune 运势文案结构（GitHub Yuri-YuzuChaN）。
# 确定性 seed：每日/每小时/每两小时结果固定，同一时刻反复查不跳变。
# ===========================================================================

_FORT_COLORS = ("圣装金", "Tenno 蓝", "赤毒红", "虚空金", "辐射绿",
                "感染紫", "钢铁银", "能量橙", "杜鹃粉", "夜灵青")

#: 今日宜（可含 {w} 占位插武器/战甲名）
_ALMANAC_DO = (
    "把玩 {w} - 手感顺滑，输出爆炸被萌新羡慕",
    "挖矿 - 稀有矿出货率提高了很多",
    "挖武器 - 会发现以前没发现的好东西",
    "装饰氏族 - 新的装饰灵感和组合",
    "蹲奸商 - 杜卡德备好，Baro 今天值得逛",
    "刷核桃 - 稀有遗物今天容易出",
    "开紫卡 - 今天手气不错，先开一张试试",
    "打三傻 - 夜灵平原景色正好，钙块和球等你",
    "清仓库 - 卖点 Prime 垃圾换白金",
    "带萌新 - 老仓鼠的自我修养",
)
#: 今日不宜
_ALMANAC_DONT = (
    "保护动物 - 被咬到手指",
    "作死 - 除非你活够了，死得早点没关系",
    "挖赋能 - 浪费半天不知道自己在干啥",
    "连肝 8 小时 - 小心猝死警告",
    "上头洗卡 - 白金是别人的",
    "清空体力 - 明天就没得刷了",
    "熬夜等刷新 - 不如早点睡",
    "单刷中断 - 除非你想体验血压拉满",
)
#: 朝向/玄学
_ALMANAC_TIPS = (
    "注意你地图的南方最有可能出好东西",
    "出门前转三圈，今天欧气在东南角",
    "先拜拜 Lotus 再进图，出货率 +10%",
    "今天适合带个幸运色饰品进图",
    "打完三傻再决定要不要开卡，玄学加成",
)
#: 本刻时出货
_ALMANAC_DROPS = (
    "高洗成品", "满倾紫卡", "极品词条组合", "负变焦毕业卡",
    "满级充沛（做梦）", "稀有核桃光辉", "Prime 垃圾（能换杜卡德）", "圣装部件",
)
#: 模拟开卡正词条 (名称, 下限%, 上限%)
_SIM_POS = (
    ("基伤", 60, 250), ("暴击伤害", 50, 180), ("暴击率", 40, 160),
    ("多重射击", 40, 150), ("触发几率", 40, 160), ("毒素伤害", 60, 200),
    ("火焰伤害", 60, 200), ("冰冻伤害", 60, 200), ("电击伤害", 60, 200),
    ("装填速度", 25, 80), ("射速", 20, 70), ("攻击速度", 20, 70),
    ("滑行攻击暴击率", 80, 220), ("近战范围", 10, 60),
)
#: 模拟开卡负词条 (名称, 下限%, 上限%)
_SIM_NEG = (
    ("变焦", 20, 60), ("弹药最大值", 15, 45), ("对 Corpus 伤害", 15, 40),
    ("对 Infested 伤害", 15, 40), ("触发时间", 10, 35), ("弹匣容量", 10, 30),
    ("射速", 10, 30), ("装填速度", 10, 30),
)

_WF_EN2ZH: dict | None = None


def _wf_en2zh() -> dict:
    """英文名 → 官方中文名（战甲+武器翻译表一次构建缓存）。"""
    global _WF_EN2ZH
    if _WF_EN2ZH is None:
        m = {}
        _cards = _load_cards()
        for zh, ens in (_cards["wf"].get("zh2en") or {}).items():
            for e in ens:
                m.setdefault((e or "").lower(), zh)
        # 各表 map 内嵌 zh 字段（MOD/赋能等中文名兜底）
        for _tbl in ("wf", "mod", "arc"):
            for _k, _v in (_cards.get(_tbl, {}).get("map") or {}).items():
                _z = (_v or {}).get("zh")
                if _z:
                    m.setdefault((_k or "").lower(), _z)
        # 武器表中文名（Primary/Secondary/Melee 翻译表反查）
        _load_translate_data()
        if _zh_lookup:
            for zh, ens in _zh_lookup.items():
                for e in ens:
                    m.setdefault((e or "").lower(), zh)
        _WF_EN2ZH = m
    return _WF_EN2ZH


def _wf_rand_names(rnd, n: int):
    """从战甲/物品表随机抽 n 个 (en, zh)。"""
    t = _load_cards()["wf"]
    keys = [k for k in (t.get("map") or {}).keys()]
    rnd.shuffle(keys)
    en2zh = _wf_en2zh()
    out = []
    for k in keys[:n]:
        out.append((k, en2zh.get(k) or (t.get("map") or {}).get(k, {}).get("zh") or k))
    return out


def _stars(v: int) -> str:
    return "★" * v + "☆" * (5 - v)


async def hamster_fortune_text(who: str = "") -> str:
    """仓鼠运势：每日固定（按 名字+日期 seed），Warframe 元素化。"""
    import datetime as _dt
    import random
    who = (who or "").strip() or "你"
    rnd = random.Random(f"{who}{_dt.date.today():%Y%m%d}")
    lv = rnd.choices(["大吉", "吉", "中吉", "小吉", "末吉", "凶", "大凶"],
                     weights=[8, 18, 20, 18, 16, 13, 7])[0]
    color = rnd.choice(_FORT_COLORS)
    num = rnd.randint(1, 32)  # 段位 1-32
    picked = _wf_rand_names(rnd, 1)
    zh, en = (picked[0][1], picked[0][0]) if picked else ("圣剑", "Excalibur")
    do = rnd.choice(_ALMANAC_DO).format(w=zh)
    dont = rnd.choice(_ALMANAC_DONT)
    return "\n".join([
        f"【{who} 今日仓鼠运势】",
        f"运势：{lv}",
        f"幸运色：{color}　幸运数字：段位 {num}",
        f"今日好运战甲：{zh}（{en}）",
        f"宜：{do}",
        f"忌：{dont}",
        "（纯玄学，每天 0 点刷新，同一天结果固定）",
    ])


async def almanac_text() -> str:
    """仓鼠黄历（黄道吉日）：今日运势(每日) + 紫卡运势(每2小时)
    + 任务运势(每小时，结合真实 worldstate 数据)。"""
    import datetime as _dt
    import random
    now = _dt.datetime.now()
    d = await _worldstate()  # 120s 缓存，黄历低频查询不额外打源
    rnd_d = random.Random("almanac_d" + f"{now:%Y%m%d}")
    rnd_b = random.Random("almanac_b" + f"{now:%Y%m%d%H}{now.hour // 2}")
    rnd_h = random.Random("almanac_h" + f"{now:%Y%m%d%H}")
    wday = "一二三四五六日"[now.weekday()]
    lines = ["【仓鼠老黄历】", f"公历：{now:%m-%d} 星期{wday}　时辰：{now:%H:%M}"]

    # ---- 今日运势（每天）----
    lines.append(f"今日运势：{_stars(rnd_d.choices([3, 4, 5], weights=[40, 40, 20])[0])}（每天更新）")
    for s in rnd_d.sample(list(_ALMANAC_DO), 4):
        if "{w}" in s:
            picked = _wf_rand_names(rnd_d, 1)
            s = s.format(w=picked[0][1] if picked else "圣剑")
        lines.append("【宜】" + s)
    for s in rnd_d.sample(list(_ALMANAC_DONT), 3):
        lines.append("【不宜】" + s)

    # ---- 紫卡运势（每 2 小时）----
    lines.append(f"紫卡运势：{_stars(rnd_b.choices([3, 4], weights=[45, 55])[0])}（每 2 小时更新）")
    lw, uw = _wf_rand_names(rnd_b, 2)
    _load_weapons()
    wkeys = list(_weapons.keys()) if _weapons else []
    lk, uk = wkeys[0], wkeys[1]  # 先占位，避免空列表
    if len(wkeys) >= 2:
        lk, uk = rnd_b.sample(wkeys, 2)
    elif wkeys:
        lk = uk = wkeys[0]
    lz = _wf_en2zh().get(lk) or (lk if lk else "斯特朗")
    uz = _wf_en2zh().get(uk) or (uk if uk else "双短柄战镰 Prime")
    lines.append(f"【开卡】好运战甲：{lw[1]}　厄运战甲：{uw[1]}")
    lines.append(f"【洗卡】好运武器：{lz}　厄运武器：{uz}")
    lines.append(f"本刻时有可能出货：{rnd_b.choice(_ALMANAC_DROPS)}")

    # ---- 任务运势（每小时 + 真实数据）----
    lines.append(f"任务运势：{_stars(rnd_h.choice([3, 4, 5]))}（每小时更新）")
    fs = _slice(d, "fissures")
    if fs:
        cnt: dict = {}
        for f in fs:
            t = f.get("tier", "?")
            cnt[t] = cnt.get(t, 0) + 1
        if cnt:
            top = max(cnt, key=cnt.get)
            lines.append(f"【裂隙】当前 {_zh_tier(top)} 最旺（{cnt[top]} 条），适合刷核桃")
        else:
            lines.append("【裂隙】当前没有活跃裂隙，休息一下")
    else:
        lines.append("【裂隙】裂隙数据暂缺")
    vt = _slice(d, "voidtraders")
    if vt:
        lines.append("【奸商】Baro 正在访问中，杜卡德别乱花，去兑换" if vt[0].get("active")
                     else "【奸商】Baro 还没来，杜卡德先攒着")
    else:
        lines.append("【奸商】奸商数据暂缺")
    dn = _slice(d, "daynight")
    for x in dn:
        if (x.get("id") or "").lower() == "cetus":
            start = float(x.get("start") or 0)
            length = float(x.get("length") or 1)
            day_s = float(x.get("dayStart") or 0)
            day_e = float(x.get("dayEnd") or 0)
            t = (_now() - start) % length
            is_day = day_s <= t < day_e
            lines.append("【平原】夜灵平原" + ("白天，适合赏景挖矿" if is_day else "夜晚，适合抓夜灵打三傻"))
            break
    lines.append("【朝向】" + rnd_h.choice(_ALMANAC_TIPS))

    _ts = _src_time("latest")
    if _ts:
        lines.append("数据源：Tenno.Tools 世界状态 · 更新于 " + _ts)
    return "\n".join(lines)


def _sim_riven_data() -> dict | None:
    """随机生成模拟紫卡数据（结构化，供文本与卡片渲染共用）。"""
    import random
    _load_weapons()
    if not _weapons:
        return None
    en = random.choice(list(_weapons.keys()))
    info = _weapons[en]
    disp = RIVEN_DISPOSITION  # 全武器统一，不再按武器浮动
    # 卡片底图只有两行词条位：65% 双正 / 35% 一正一负
    n_pos = 2 if random.random() < 0.65 else 1
    pos = []
    for name, lo, hi in random.sample(list(_SIM_POS), n_pos):
        # 旧实现 (0.75+0.5*星级) 把 1~5 星级当倍率放大，语义错误且随武器浮动；
        # 现行统一 1.2，放大系数取固定常数。
        v = (lo + random.random() * (hi - lo)) * 1.35 * random.uniform(0.9, 1.1)
        pos.append((name, v))
    neg = None
    if n_pos == 1:
        name, lo, hi = random.choice(_SIM_NEG)
        neg = (name, (lo + random.random() * (hi - lo)) * random.uniform(0.9, 1.1))
    return {
        "weapon_zh": _wf_en2zh().get(en) or info.get("en", en),
        "weapon_en": info.get("en", en),
        "cat": info.get("cat", ""),
        "disp": disp,
        "pos": pos,
        "neg": neg,
        "mastery": info.get("masteryReq") or random.randint(8, 18),
        "rolls": random.randint(0, 300),
    }


def _sim_riven_body(d: dict) -> str:
    """紫卡卡片渲染用结构化 body。"""
    lines = [f"WEAPON|{d['weapon_zh']}|{d['weapon_en']}|{d['cat']}",
             f"DISP|{d['disp']:.2f}"]
    lines += [f"POS|+{v:.1f}% {name}" for name, v in d["pos"]]
    if d["neg"]:
        lines.append(f"NEG|-{d['neg'][1]:.1f}% {d['neg'][0]}")
    lines.append(f"META|段位 {d['mastery']} 洗数 {d['rolls']}")
    lines.append("NOTE|模拟开卡纯属娱乐，出货请勿当真")
    return "\n".join(lines)


async def sim_riven_text() -> str:
    """模拟开卡：随机武器 + 随机词条（纯娱乐，数值按统一倍率口径）。"""
    d = _sim_riven_data()
    if not d:
        return "武器数据还没加载好，稍后再试～"
    lines = [
        f"【模拟开卡】{d['weapon_zh']}（{d['weapon_en']}）{d['cat']}",
        f"紫卡倾向：{_disp_desc()}",
    ]
    lines += [f"+{v:.1f}% {name}" for name, v in d["pos"]]
    if d["neg"]:
        lines.append(f"-{d['neg'][1]:.1f}% {d['neg'][0]}")
    lines.append(f"段位 {d['mastery']}　洗数 {d['rolls']}")
    lines.append("（模拟开卡纯属娱乐，出货请勿当真）")
    return "\n".join(lines)


async def render_sim_riven() -> bytes | None:
    """渲染紫卡样式图（纯本地随机，零外呼；失败返回 None 由调用方降级文本）。"""
    d = _sim_riven_data()
    if not d:
        return None
    try:
        from .. import pic as pic_mod
        return await pic_mod.render(
            "riven", theme_name=None, card="星际战甲·模拟开卡", body=_sim_riven_body(d),
            title="星际战甲·模拟开卡",
            source="模拟开卡 · 本地随机 · 非真实数据")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_sim_riven_render_failed: {type(e).__name__}: {e}")
        return None


# ===========================================================================
# 掉落 / 遗物 / 配卡 / 金垃圾 / 结合目标（2026-09-17 新增）
# 数据源：WFCD/warframe-drop-data + WFCD/warframe-relic-data（MIT，官方掉落表
# JSON，已下载到本地 data/warframe_items/drop_data 一次性缓存 → 之后全部离线
# 零外呼）；配卡为本地启发式建议（overframe.gg 被 Cloudflare 403 拦，附链接）；
# 金垃圾按遗物稀有度 → 杜卡德价值（游戏规则 Rare=100/Uncommon=45/Common=15）
# 分档（warframe.market 本机被风控 403，价格请走 #价格 口令）。
# ===========================================================================

_DROP_DIR = _DATA_DIR / "drop_data"
_drop_all: list | None = None        # all.slim 全量记录
_drop_idx: dict | None = None        # item -> [(place, rarity, chance)]
_relic_list: list | None = None      # Relics.json（warframe-relic-data）
_relic_map: dict | None = None       # 遗物名(小写) -> relic
_cur_relics: set | None = None       # 当前可获得遗物名（来自掉落表，推断入库）
_ZH2EN: dict | None = None           # 中文 -> [英文]（_wf_en2zh 反查，供掉落查询）

_RELIC_RE = re.compile(r"^(Lith|Meso|Neo|Axi|Requiem)\s+[A-Z]\d+$", re.I)


def _load_drop() -> None:
    """懒加载掉落表 + 遗物数据（一次构建索引）。"""
    global _drop_all, _drop_idx, _relic_list, _relic_map, _cur_relics, _ZH2EN
    if _drop_idx is not None:
        return
    idx: dict = {}
    cur: set = set()
    try:
        _drop_all = json.loads((_DROP_DIR / "all.slim.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_drop_load_failed all.slim: {type(e).__name__}: {e}")
        _drop_all = []
    for rec in _drop_all or []:
        item = (rec.get("item") or "").strip()
        if not item:
            continue
        idx.setdefault(item.lower(), []).append(rec)
        if _RELIC_RE.match(item):
            cur.add(item.lower())
    # 合并特殊掉落表（赏金/突击/扎里曼/钥匙/熵离子等）
    for _f in ("cetusBountyRewards.json", "deimosRewards.json", "solarisBountyRewards.json",
               "sortieRewards.json", "zarimanRewards.json", "keyRewards.json",
               "transientRewards.json", "hexRewards.json", "entratiLabRewards.json",
               "miscItems.json", "syndicates.json"):
        try:
            _extra = json.loads((_DROP_DIR / _f).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        _recs = _extra.get("rewards") if isinstance(_extra, dict) and isinstance(_extra.get("rewards"), list) else _extra
        if not isinstance(_recs, list):
            continue
        for r in _recs:
            item = (r.get("item") or "").strip()
            if not item:
                continue
            idx.setdefault(item.lower(), []).append(r)
            if _RELIC_RE.match(item):
                cur.add(item.lower())
    _drop_idx = idx
    _cur_relics = cur
    try:
        _relic_list = json.loads((_DROP_DIR / "Relics.json").read_text(encoding="utf-8"))
        _relic_map = {}
        for r in _relic_list or []:
            nm = (r.get("name") or "").lower()
            if nm:
                _relic_map[nm] = r
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_relic_load_failed Relics.json: {type(e).__name__}: {e}")
        _relic_map = _relic_map or {}
    # 中文 -> 英文 索引（掉落查询输入中文时用；并入官方全物品翻译表）
    if _ZH2EN is None:
        _ZH2EN = {}
        for en, zh in _wf_en2zh().items():
            _ZH2EN.setdefault(zh, []).append(en)
        _load_translate_data()
        if _zh_lookup:
            for zh, ens in _zh_lookup.items():
                for e in ens:
                    _ZH2EN.setdefault(zh, []).append(e)
        for _a_en, _a_info in (_aliases or {}).items():
            _a_zh = (_a_info.get("zh") or "").strip()
            if _a_zh:
                _ZH2EN.setdefault(_a_zh, []).append(_a_en)
            for _a in _a_info.get("aliases") or []:
                _ZH2EN.setdefault(_a.strip(), []).append(_a_en)


def _item_en(q: str) -> list:
    """把用户输入（中文/英文/别名）解析成候选英文名列表。"""
    q = (q or "").strip()
    if not q:
        return []
    low = q.lower()
    hits = []
    # 直接英文包含
    if _drop_idx is not None:
        hits += [k for k in _drop_idx if low in k][:6]
    # 中文反查
    for zh, ens in (_ZH2EN or {}).items():
        if zh and (zh == q or zh in q or q in zh):
            hits += [e.lower() for e in ens]
    # 别名
    _load_translate_data()
    en = (_alias_lookup or {}).get(low)
    if en:
        hits.append(en)
    # 去重保序
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:6]


#: 星球中文（掉落表 place 前缀）
_DROP_PLANETS = {
    "Mercury": "水星", "Venus": "金星", "Earth": "地球", "Mars": "火星",
    "Jupiter": "木星", "Saturn": "土星", "Uranus": "天王星", "Neptune": "海王星",
    "Pluto": "冥王星", "Eris": "阋神星", "Sedna": "赛德娜", "Void": "虚空",
    "Phobos": "火卫一", "Ceres": "谷神星", "Europa": "欧罗巴", "Kuva Fortress": "赤毒要塞",
    "Deimos": "火卫二", "Lua": "月球", "Zariman": "扎里曼", "Duviri": "双衍王境",
    "Tennocon": "TennoCon", "Cetus": "希图斯", "Fortuna": "福尔图娜", "Cambion Drift": "魔胎之境",
    "Orb Vallis": "奥布山谷", "Plains of Eidolon": "夜灵平原", "Steel Path": "钢铁之路",
}


#: Prime 部件词缀翻译（整名未收录时按部件词缀翻，长词在前优先）
_PRIME_SUFFIX = (
    ("Blueprint", "蓝图"), ("Upper Limb", "上弓臂"), ("Lower Limb", "下弓臂"),
    ("Neuroptics", "头部神经光元"), ("Receiver", "枪机"), ("Barrel", "枪管"),
    ("Carapace", "胸甲"), ("Systems", "系统"), ("Chassis", "机体"),
    ("Gauntlet", "铁手套"), ("Link", "连接器"), ("Handle", "握柄"),
    ("Cerebrum", "大脑"), ("Stock", "枪托"), ("Blade", "刀刃"),
    ("Grip", "握把"), ("Head", "战刃头"), ("Hilt", "剑柄"),
    ("String", "弦"), ("Wings", "羽翼"), ("Belt", "腰带"),
    ("Pouch", "背袋"), ("Orb", "宝珠"), ("Display", "展示"),
    ("Prime", "Prime"), (" Set", " 套装"),
)


def _prime_zh(name: str) -> str:
    """Prime 部件英文 → 中文（优先整名翻译，缺则词缀翻译，保留未收录部分）。"""
    if not name:
        return name
    zh = _wf_en2zh().get(name.strip().lower())
    if zh:
        return zh
    s = name.title()  # "blueprint" → "Blueprint"，再按词缀替换
    for en, z in _PRIME_SUFFIX:
        s = s.replace(en, z)
    return s


def _chance_num(v) -> float:
    """掉落概率归一化：数字或 "22.58%" 字符串 → float。"""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("%", "").strip())
    except Exception:  # noqa: BLE001
        return 0.0


def _zh_place(place: str) -> str:
    """掉落表 place（英文）→ 中文星球 + 任务类型中文化。"""
    if not place:
        return place
    s = place
    # 任务类型中文化（<b>Survival</b> / (Survival) 等）
    for en, zh in _MISSION_ZH.items():
        s = s.replace(en, zh)
    # 星球前缀
    for en, zh in _DROP_PLANETS.items():
        s = s.replace(en, zh)
    return s


async def drop_text(item: str) -> str:
    """掉落查询：物品 → 掉落点（官方掉落表，离线）。"""
    if not item:
        return "格式：#wf 掉落 <物品名>（例：#wf 掉落 充沛 / #wf 掉落 绝路 Prime 蓝图）"
    _load_drop()
    if not _drop_idx:
        return "掉落数据没加载好，稍后再试～"
    cands = _item_en(item)
    if not cands:
        return ("没找到「" + item + "」的掉落。试试英文名（如 arcane energize / rubico prime barrel）"
                "或更精确的名字（Prime 部件加 Prime，如 绝路 Prime 枪管）。")
    lines = [f"【{item} 掉落点】"]
    shown = 0
    for en in cands:
        recs = _drop_idx.get(en, [])
        if not recs:
            continue
        if shown:
            lines.append("")
        lines.append(f"〔{_prime_zh(en)}〕")
        # 按 chance 降序，最多 8 条
        for r in sorted(recs, key=lambda x: -_chance_num(x.get("chance")))[:8]:
            place = _zh_place(r.get("place") or "?")
            rarity = r.get("rarity") or "?"
            lines.append(f"  · {place}｜{rarity} {_chance_num(r.get('chance')):.2f}%")
        shown += 1
        if shown >= 3:
            break
    if not shown:
        return f"「{item}」在掉落表里没有记录（可能是商店/任务固定奖励）。"
    lines.append("（数据源：WFCD 官方掉落表，离线缓存）")
    return "\n".join(lines)


def _rarity_zh(r: str) -> str:
    return {"Common": "常见", "Uncommon": "罕见", "Rare": "稀有"}.get(r, r)


def _vault_state(name: str) -> str:
    """遗物是否入库：掉落表里有 = 未入库，没有 = 已入库。"""
    return "未入库" if ((name or "").lower() in (_cur_relics or set())) else "已入库"


async def relic_text(q: str) -> str:
    """遗物/部件查询：核桃掉落内容 + 入库状态（离线）。"""
    if not q:
        return "格式：#wf 遗物 <遗物名或部件名>（例：#wf 遗物 后纪 A1 / #wf 遗物 绝路 Prime 枪管）"
    _load_drop()
    if not _relic_map:
        return "遗物数据没加载好，稍后再试～"
    low = (q or "").strip().lower()
    # 1) 直接是遗物名（支持中文纪元：后纪 A1 → axi a1）
    tier_zh = {"古纪": "lith", "前纪": "meso", "中纪": "neo", "后纪": "axi", "安魂": "requiem"}
    cand = low
    for zh, en in tier_zh.items():
        if low.startswith(zh):
            cand = en + " " + low[len(zh):].strip().lower()
            break
    relic = _relic_map.get(cand) or _relic_map.get(low)
    if relic:
        lines = [f"【{relic.get('name')}】{_vault_state(relic.get('name'))}"]
        for r in relic.get("rewards", [])[:8]:
            it = (r.get("item") or {}).get("name") or "?"
            zh = _prime_zh(it) or _zh_text(it)
            lines.append(f"  · [{_rarity_zh(r.get('rarity') or '?')}] {zh} {r.get('chance')}%")
        if len(relic.get("rewards", [])) > 8:
            lines.append(f"  …共 {len(relic['rewards'])} 项")
        lines.append("（入库状态按当前官方掉落表推断：掉落表有=未入库）")
        return "\n".join(lines)
    # 2) 部件反查：先把用户输入翻译成英文候选再匹配
    en_hits = _item_en(q)
    hits = []
    for nm, r in _relic_map.items():
        for rw in r.get("rewards", []):
            it = (rw.get("item") or {}).get("name") or ""
            itl = it.lower()
            hit = any(e in itl or itl in e for e in en_hits) if en_hits else (low in itl)
            if hit:
                hits.append((r, rw))
                break
    if hits:
        lines = [f"【{q}】出现在以下遗物："]
        for r, rw in hits[:8]:
            it = (rw.get("item") or {}).get("name") or "?"
            st = _vault_state(r.get("name"))
            lines.append(f"  · {r.get('name')}（{st}）[{_rarity_zh(rw.get('rarity') or '?')}] "
                         f"{_zh_text(it)} {rw.get('chance')}%")
        if len(hits) > 8:
            lines.append(f"  …共 {len(hits)} 个遗物")
        return "\n".join(lines)
    return f"没找到「{q}」。试试遗物名（如 后纪 A1 / axi a1）或 Prime 部件英文名（如 rubico prime barrel）。"


async def build_text(word: str) -> str:
    """配卡建议：本地启发式（面板数据）+ overframe 链接（该站本机 403，链接可手动开）。"""
    if not word:
        return "格式：#wf 配卡 <武器或战甲名>（例：#wf 配卡 绝路 / #wf 配卡 沙皇）"
    # 先试武器
    info = _resolve_weapon(word)
    if info:
        lines = [f"【{info.get('en')} 配卡建议】{info.get('cat', '')}"]
        cc = info.get("criticalChance")
        sc = info.get("statusChance")
        ms = info.get("multishot")
        recs = []
        if cc is not None and cc >= 0.25:
            recs.append("暴击流：暴击率/暴击伤害/基伤（镀层 分裂膛室 打底）")
        if sc is not None and sc >= 0.25:
            recs.append("触发流：触发几率/元素伤害（病毒/腐蚀按敌人选）")
        if ms is not None and ms < 2:
            recs.append("多重射击：分裂膛室/弹头扩散 优先")
        cat = info.get("cat", "")
        if cat == "近战武器":
            recs.append("近战通用：攻速/范围/架势 + 镀层 近战连击")
        else:
            recs.append("基伤/元素：弱点感应/关键延迟（高暴）或镀层 弹药（触发）")
        lines.extend("  · " + r for r in dict.fromkeys(recs))
        lines.append("完整配卡可到 overframe.gg 搜 " + info.get("en", word) + "（网页可切中文，本机直连被拦截，手动开）")
        return "\n".join(lines)
    # 战甲
    res = _resolve_card(word, _load_cards()["wf"])
    if res:
        key, m = res
        lines = [f"【{m.get('zh', key)} 配卡方向】"]
        # 通用流派建议（按能量/护甲/技能特征给粗方向）
        lines.append("  · 生存：顶天立地/适应/灵活装甲（萌新先堆肉）")
        lines.append("  · 强度：聚精会神/瞬时坚毅（技能伤害/增益向）")
        lines.append("  · 效率/持续：弹指瞬技/持久力（频繁放技能的甲）")
        lines.append("  · 范围：过度延展/预言者系列（控场/清图向）")
        lines.append("完整配卡可到 overframe.gg 搜 " + (m.get("zh") or key) + "（网页可切中文，本机直连被拦截，手动开）")
        return "\n".join(lines)
    return f"没找到「{word}」的配卡资料。试试武器名（绝路/沙皇/信条·枢密）或战甲名（毒妈/咖喱棒）。"


async def junk_text() -> str:
    """金垃圾：按遗物稀有度 → 杜卡德价值分档（离线规则 Rare=100/Uncommon=45/Common=15）。
    价格被 warframe.market 风控（403），杜卡德价值是游戏固定规则，仍可参考；具体价格发 #价格。"""
    _load_drop()
    if not _relic_list:
        return "遗物数据没加载好，稍后再试～"
    ducat_map: dict = {}  # item(小写) -> (ducat, 稀有度)
    for r in _relic_list or []:
        for rw in r.get("rewards", []):
            it = ((rw.get("item") or {}).get("name") or "").strip()
            if not it or "forma" in it.lower():
                continue
            rar = (rw.get("rarity") or "").lower()
            d = {"common": 15, "uncommon": 45, "rare": 100}.get(rar)
            if d is None:
                continue
            low = it.lower()
            if low not in ducat_map or d > ducat_map[low][0]:
                ducat_map[low] = (d, rar)
    buckets = {"金垃圾（100 杜卡德）": [], "银垃圾（45 杜卡德）": [], "铜垃圾（15 杜卡德）": []}
    for low, (d, rar) in ducat_map.items():
        zh = _prime_zh(low) or _zh_text(low)
        if d >= 100:
            buckets["金垃圾（100 杜卡德）"].append(zh)
        elif d >= 45:
            buckets["银垃圾（45 杜卡德）"].append(zh)
        else:
            buckets["铜垃圾（15 杜卡德）"].append(zh)
    lines = ["【Prime 垃圾 · 杜卡德价值分档】", "（杜卡德为遗物稀有度固定值；市场被风控，价格请发 #价格）"]
    for label, items in buckets.items():
        if not items:
            continue
        lines.append(f"[{label}]")
        # 每种最多 10 条，去重保序
        seen, out = set(), []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        lines.extend("  · " + x for x in out[:10])
        if len(out) > 10:
            lines.append(f"  …共 {len(out)} 种")
    return "\n".join(lines)


#: 结合目标内置表（常见大黄脸目标：破坏/捕获/救援/刺杀 推荐节点，100% 出现）
_SYNTHESIS = {
    "轰击者": ["谷神星 Ker（破坏）", "谷神星 雷克斯（捕获）", "谷神星 Nuovo（救援）", "谷神星 Exta（刺杀）"],
    "重型炮手": ["赛德娜 Sechura（防御）", "火星 Wahiba（破坏）", "谷神星 Draco（生存）"],
    "远古感染者": ["阋神星 Naeglar（生存）", "阋神星 Oestrus（防御）", "火卫二 魔胎之境（赏金）"],
    "堕落轰击者": ["虚空 Mot（生存）", "虚空 Teshub（歼灭）", "虚空 Belenus（破坏）"],
    "堕落重型炮手": ["虚空 Mot（生存）", "虚空 Mithra（拦截）", "虚空 Belenus（破坏）"],
    "克隆尼屠夫": ["谷神星 Kiste（拦截）", "谷神星 Draco（生存）", "谷神星 Ker（破坏）"],
    "科普斯船员": ["金星 Kiliken（移动防御）", "金星 Romula（防御）", "金星 Ishtar（捕获）"],
    "赤毒轰击者": ["赤毒要塞 Tamu（生存）", "赤毒要塞 Kassini（歼灭）", "赤毒要塞 Ogg（捕获）"],
    "堕落者（虚空中枢）": ["虚空 Mot（生存）", "虚空 Ukko（捕获）", "虚空 Ani（生存）"],
    "护盾鱼鹰": ["金星 Nef Anyo 土星（防御）", "金星 Venera（防御）", "木星 Themisto（刺杀）"],
    "远古治愈者": ["阋神星 Naeglar（生存）", "阋神星 Oestrus（防御）", "土星 Helene（防御）"],
    "爪喀（野生）": ["地球 夜灵平原（白天野外）", "双衍王境（野外）"],
    "库狛（野生）": ["地球 夜灵平原（白天野外）", "金星 奥布山谷（白天）"],
    "沙海怒鲸": ["火卫一 Salacia（移动防御）", "火卫一 Gulliver（防御）"],
    "赤毒掠食者": ["赤毒要塞 Nabuk（防御）", "赤毒要塞 Garus（救援）"],
    "诺思": ["海王星 Arva Vector（资源储藏舱）", "海王星 Nu-Gua Mines（资源储藏舱）"],
}


def synthesis_text(enemy: str) -> str:
    """结合目标：大黄脸扫描地点（内置常见表）。"""
    if not enemy:
        return "格式：#wf 结合目标 <敌人名>（例：#wf 结合目标 轰击者 / #wf 结合目标 重型炮手）"
    low = (enemy or "").strip().lower()
    for zh, nodes in _SYNTHESIS.items():
        if zh.lower() in low or low in zh.lower():
            lines = [f"【{zh} 结合目标】为了圣殿的荣耀！"]
            for n in nodes:
                lines.append("  · " + n)
            lines.append("（内置常用表，扫描地点为固定刷新点；更多目标见灰机 Wiki）")
            return "\n".join(lines)
    return (f"没找到「{enemy}」的结合目标表。试试：轰击者 / 重型炮手 / 远古感染者 / "
            "堕落轰击者 / 堕落重型炮手 / 克隆尼屠夫 / 科普斯船员 / 赤毒轰击者 / 护盾鱼鹰。")


# ===========================================================================
# 配卡图片卡数据（本地库 builds.json，社区可扩展；横版 MOD 卡由 tools/gen_mod_cards 合成）
# 与 build_text 文本分支互补：本函数供给图片卡渲染，命中即出图，未命中回落文本。
# ===========================================================================
_BUILD_LIB_CACHE: dict | None = None
#: 上次载入 builds.json 时的 mtime；文件被改动就自动重载。
#: 必须是 mtime 而不是「一次载入永久缓存」——2026-11-03 踩过：
#: 给 builds.json 补了「空刃」条目后，运行中的 bot 仍拿旧缓存回「未收录」，
#: 表现成「数据补了但配卡还是文字」，排查半天才发现是缓存没失效。
_BUILD_LIB_MTIME: float = -1.0


def _build_items_dir() -> Path:
    """data/warframe_items 目录（真实项目 _DATA_DIR 即该目录）。"""
    return _DATA_DIR


def _load_build_lib() -> dict:
    """读 data/warframe_items/builds.json（按 mtime 热加载，失败返回空库）。

    配卡库是**可运维数据**（随时加武器/改配卡），所以不能一次性缓存到进程退出。
    每次取用先 stat 一次（开销可忽略），mtime 变了就重读；解析失败保留旧库，
    避免手滑写坏 JSON 时把已在线的配卡全清空。
    """
    global _BUILD_LIB_CACHE, _BUILD_LIB_MTIME
    fp = _build_items_dir() / "builds.json"
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _BUILD_LIB_CACHE is not None and mtime == _BUILD_LIB_MTIME:
        return _BUILD_LIB_CACHE
    try:
        _BUILD_LIB_CACHE = json.loads(fp.read_text(encoding="utf-8"))
        _BUILD_LIB_MTIME = mtime
        logger.info("wf_build_lib_loaded weapons=%d" % len((_BUILD_LIB_CACHE.get("weapons") or {})))
    except Exception as e:  # noqa: BLE001 — 文件缺失/损坏不拖垮机器人
        logger.warning(f"wf_build_lib_load_failed: {type(e).__name__}: {e}")
        if _BUILD_LIB_CACHE is None:
            _BUILD_LIB_CACHE = {"weapons": {}}
        _BUILD_LIB_MTIME = mtime
    return _BUILD_LIB_CACHE


def list_build_weapons() -> list[str]:
    """已收录配卡的武器清单（zh(en)），供『未找到』时给出可选项提示。"""
    lib = _load_build_lib()
    return ["%s(%s)" % (w.get("zh", "?"), w.get("en", ""))
            for w in (lib.get("weapons") or {}).values()]


def build_card_data(word: str) -> dict | None:
    """解析「配卡 <武器> [标签] [-N]」→ 图片卡数据；未收录返回 None。

    返回结构（供 build_card.render_build 消费）：
        {"zh","en","icon":str|None,"set":{...},"page":int,"total":int}
    武器解析走别名/中文/英文，**前缀最长优先**（自动剥离标签词）。
    """
    if not word:
        return None
    lib = _load_build_lib()
    weapons = lib.get("weapons") or {}

    # 1) 拆页码 -N
    page = 1
    cleaned: list[str] = []
    for t in (word or "").split():
        mm = re.match(r"^-(\d+)$", t)
        if mm:
            page = int(mm.group(1))
        else:
            cleaned.append(t)

    # 2) 武器名：前缀最长优先（精确 → 包含），剥离「奏凯 暴击流」的标签词
    key = None
    used = 0
    n = len(cleaned)
    for L in range(n, 0, -1):
        cand = " ".join(cleaned[:L]).strip().lower()
        if not cand:
            continue
        for k, w in weapons.items():
            pool = [str(w.get("zh", "")), str(w.get("en", ""))] + \
                   [str(a) for a in (w.get("aliases") or [])]
            if any(cand == p.lower() for p in pool if p):
                key, used = k, L
                break
        if key:
            break
    if key is None:
        for L in range(n, 0, -1):
            cand = " ".join(cleaned[:L]).strip().lower()
            if not cand:
                continue
            for k, w in weapons.items():
                pool = [str(w.get("zh", "")), str(w.get("en", ""))] + \
                       [str(a) for a in (w.get("aliases") or [])]
                if any(cand in p.lower() for p in pool if p):
                    key, used = k, L
                    break
            if key:
                break
    if key is None:
        return None
    w = weapons[key]
    sets = w.get("sets") or []
    if not sets:
        return None
    tag_toks = cleaned[used:]

    # 3) 选套：先按剩余 token 的 标签/层级 命中，否则按页码循环
    chosen = None
    if tag_toks:
        for s in sets:
            hay = (s.get("tag", "") + " " + s.get("tier", "")).lower()
            if any(tok.lower() in hay for tok in tag_toks
                   if tok not in ("顶配", "毕业", "配卡", "build", "配置")):
                chosen = s
                break
    if chosen is None:
        chosen = sets[(page - 1) % len(sets)]

    # 武器大类（主武器/副武器/近战武器）：渲染层要靠它决定槽位数。
    # 主/副/近战都是 8 主槽 + 1 Exilus = 9 格——槽位是武器固有的，
    # 不能因为数据源没写就不画（用户明确要求「有槽位就展示出来」）。
    _cat = ""
    try:
        _info = _resolve_weapon(str(w.get("en") or w.get("zh") or ""))
        _cat = (_info or {}).get("cat") or ""
    except Exception as e:  # noqa: BLE001 — 类别缺失只影响是否补第 9 格
        logger.warning(f"wf_build_cat_failed: {type(e).__name__}: {e}")

    return {
        "zh": w.get("zh"),
        "en": w.get("en"),
        "icon": w.get("icon"),
        "cat": _cat,
        "set": chosen,
        "page": sets.index(chosen) + 1,
        "total": len(sets),
    }

