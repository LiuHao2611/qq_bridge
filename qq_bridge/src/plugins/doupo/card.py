# -*- coding: utf-8 -*-
"""斗破苍穹·双帝之战 · 人物卡渲染。

把 player dict 渲染成「斗破大陆」图片卡片，供群聊「人物 / 属性 / 面板」口令使用。
**渲染失败必须降级为纯文本**（铁律：不允许因为图片挂了就丢消息或静默失败）。

为什么用 importlib 独立加载 pic 包，而不是 `from ..qq_bridge import pic`
------------------------------------------------------------------------
`qq_bridge/__init__.py` 会注册全部 matcher、初始化主插件链路（含 LLM 相关），
而 doupo 的单测（test_core / test_commands / test_e2e）刻意**不加载主插件**，
以保证测试隔离、不触发模型调用。直接 import 上级包会把那套副作用统统带进来。

所以这里用 spec_from_file_location 把 `qq_bridge/pic` 目录直接挂成一个独立包，
只取它的绘制能力（Canvas / fonts / icons / Theme / render），零副作用。
pic 包自身约定「不 import nonebot、不 import 上级包」，因此可安全独立加载。
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path

from . import config as cfg
from . import core
from . import commands
from . import avatar
from . import factions as fac

log = logging.getLogger("qq_bridge.doupo.card")

#: 卡片路由用的业务卡名（命中卡片级主题绑定后，主题编辑器可按此卡单独改色）
CARD_NAME = "斗破人物"

#: 菜单卡的独立卡名（与人物卡共享 bg_overrides["doupo"] 配色，但可在编辑器里单独改）
CARD_MENU_NAME = "斗破菜单"

#: 卡片宽度（QQ 聊天窗内 720 显示效果最稳，过宽会被压缩导致字小）
CARD_WIDTH = 720

_PKG_NAME = "_qq_bridge_pic_standalone"
_PIC = None
_PIC_FAILED = False


def _load_pic():
    """把 qq_bridge/pic 作为独立包加载并缓存；失败返回 None（永久降级纯文本）。"""
    global _PIC, _PIC_FAILED
    if _PIC is not None:
        return _PIC
    if _PIC_FAILED:
        return None
    if _PKG_NAME in sys.modules:
        _PIC = sys.modules[_PKG_NAME]
        return _PIC
    try:
        pic_dir = Path(__file__).resolve().parents[1] / "qq_bridge" / "pic"
        init = pic_dir / "__init__.py"
        if not init.is_file():
            raise FileNotFoundError("pic 包不存在: %s" % init)
        spec = importlib.util.spec_from_file_location(
            _PKG_NAME, str(init), submodule_search_locations=[str(pic_dir)]
        )
        if spec is None or spec.loader is None:
            raise ImportError("无法为 pic 包创建 spec")
        mod = importlib.util.module_from_spec(spec)
        # 先注册再 exec：pic 内部有相对 import，需要包已在 sys.modules 中
        sys.modules[_PKG_NAME] = mod
        spec.loader.exec_module(mod)
        _PIC = mod
        return mod
    except Exception as e:  # noqa: BLE001
        _PIC_FAILED = True
        sys.modules.pop(_PKG_NAME, None)
        log.warning("斗破卡片不可用（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None


def available() -> bool:
    """卡片能力是否可用（供自检 / 测试用；不影响正常流程）。"""
    return _load_pic() is not None


def build_data(p: dict) -> dict:
    """player dict → doupo 模板参数。

    与 commands.cmd_panel 平行但信息等价：它生成纯文本兜底，这里生成图片数据，
    两者读同一个 player，因此「图与文永远不会说的不是一回事」。
    """
    core.settle(p)  # 结算离线修为（幂等），保证图片里的修为是最新的
    st = core.stats(p)
    lv = int(p.get("lv", 1))
    need = cfg.exp_need(lv)
    exp = int(p.get("exp", 0))
    realm_max = lv >= cfg.MAX_LV

    alch = cfg.ALCHEMIST[int(p.get("alch", 0))]
    alch_name = "%s炼药师" % alch[0]   # alch[0] 为品阶名（如「二品」）
    alch_need = int(alch[1])
    alch_exp = int(p.get("alch_exp", 0))

    w = int(p.get("weapon", 0))
    wname = cfg.weapon_name(w)
    wlv = cfg.weapon_display_level(w)
    wp: dict[str, int] = {}
    for k, base in cfg.WEAPON["stats"].items():
        wp[k] = cfg.weapon_stat(base, w) if w < 9 else cfg.WEAPON["max_stats"][k]

    return {
        "name": p.get("name") or "无名",
        "lv": lv,
        # 静态头像：从对应档位 GIF 首帧裁出，base64 传给模板画进卡片左侧。
        # 用户 2026-11-07 明确要求「不用放动态人物小人了，截图一个头像放进卡片里」。
        # 资源缺失时给空串，模板会画占位头像，卡片照常出图。
        "avatar_b64": avatar.headshot_for(p) or "",
        "avatar_caption": avatar.headshot_caption(lv),
        "realm": cfg.full_realm(lv),
        "exp": exp, "need": need, "realm_max": realm_max,
        "can_break": bool(core.can_level(p)),
        "alch_name": alch_name, "alch_exp": alch_exp, "alch_need": alch_need,
        "weapon_name": wname, "weapon_level": wlv, "weapon_stats": wp,
        "stats": st,
        "hp": int(p.get("hp", 0)), "hp_max": int(st["体力"]),
        "mp": int(p.get("mp", 0)), "mp_max": core.mp_max(p),
        "gold": int(p.get("gold", 0)),
        "stone": core.count_item(p, "炎星石"),
        "power": core.power(p),
        "wins": int(p.get("wins", 0)), "losses": int(p.get("losses", 0)),
    }


async def render_panel(p: dict, tip: str | None = None) -> bytes | None:
    """渲染人物卡，返回 PNG bytes；失败返回 None（调用方降级纯文本）。

    :param tip: 覆盖卡片底部提示；None = 不额外附加。
    """
    pic = _load_pic()
    if pic is None or not p:
        return None
    data = build_data(p)
    if tip is not None:
        data["tip"] = tip
    # 首选：走完整主题链路（卡片绑定 > 自定义主题 > PIC_THEME > 内置）+ 磁盘缓存
    try:
        return await pic.render("doupo", card=CARD_NAME, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("斗破卡片主题链路渲染失败，尝试内置 light 兜底：%s", e)
    # 兜底：显式内置 light 主题，绕开主题解析（防止主题配置损坏导致整卡不可用）
    try:
        return await asyncio.to_thread(
            pic.render_sync, "doupo", "light", CARD_NAME, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("斗破卡片渲染失败（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None


def build_menu_data() -> dict:
    """口令菜单 → doupo_menu 模板参数。

    数据源是 commands.MENU_GROUPS（唯一源），与纯文本兜底同源，
    因此图片与文字永远不会「说的不是一回事」。
    """
    return {
        "title": "斗破菜单",
        "groups": [
            {"name": name, "rows": [[cmd, desc] for cmd, desc in rows]}
            for name, rows in commands.MENU_GROUPS
        ],
        "tip": f"共 {commands.menu_total()} 条口令 · 纯中文，菜单是唯一切换口",
    }


async def render_menu() -> bytes | None:
    """渲染口令菜单卡，返回 PNG bytes；失败返回 None（调用方降级纯文本）。"""
    pic = _load_pic()
    if pic is None:
        return None
    data = build_menu_data()
    # 首选：走完整主题链路（卡片绑定 > 自定义主题 > PIC_THEME > 内置）+ 磁盘缓存
    try:
        return await pic.render("doupo_menu", card=CARD_MENU_NAME, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("斗破菜单卡主题链路渲染失败，尝试内置 light 兜底：%s", e)
    # 兜底：显式内置 light 主题，绕开主题解析（防止主题配置损坏导致整卡不可用）
    try:
        return await asyncio.to_thread(
            pic.render_sync, "doupo_menu", "light", CARD_MENU_NAME, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("斗破菜单卡渲染失败（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# 通用列表卡（异火 / 宝库 / 背包 / 排行）
# 斗技自 2026-11-14 起走 doupo_skills 专用模板（描述/伤害/五段条/重点大字），
# 不再与上面四张共用 doupo_list。
# ---------------------------------------------------------------------------
async def _render_list(template: str, data: dict, card_name: str) -> bytes | None:
    """渲染任意斗破列表卡：主题链路 → 内置 light 兜底 → None（调用方回纯文本）。"""
    pic = _load_pic()
    if pic is None:
        return None
    try:
        return await pic.render(template, card=card_name, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("%s 主题链路渲染失败，尝试内置 light 兜底：%s", card_name, e)
    try:
        return await asyncio.to_thread(
            pic.render_sync, template, "light", card_name, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("%s 渲染失败（已降级为纯文本）：%s: %s", card_name, type(e).__name__, e)
        return None


def build_skills_data(p: dict) -> dict:
    """斗技 → doupo_skills 专用模板参数（完整技能描述 + 详细伤害口径）。

    伤害数字与 core.damage_schedule() / core._player_hit() 同源：
        raw = (力量×0.5 + 斗气×1.5) × cfg.skill_dmg_mult(name, lv)
        期望单击 = raw × (1 + 暴击率/100 × 0.5)   # 暴击 1.5 倍的期望
    这里展示的是**期望单击（未计目标防御）**，与战斗内实际单次命中的差别只有
    mitigation(目标防御) 与暴击随机，故不会图文说两回事。
    """
    sk = p.get("skills") or {}
    st = core.stats(p)
    raw_base = st["力量"] * 0.5 + st["斗气"] * 1.5
    crit_mult = 1.0 + core.crit_rate(p) / 100.0 * 0.5
    item = cfg.SKILL_UP_ITEM
    have = core.count_item(p, item)
    frac = cfg.SKILL_TIER_MP_FRAC

    pool = core.skill_pool(p)
    own = sum(1 for e in pool if e.get("origin") == "本宗")
    common = sum(1 for e in pool if e.get("origin") == "通用")
    rows = []
    for e in core.loadout_entries(p):
        name = e["name"]
        tier = e["tier"]
        mx = 5
        lv = max(1, min(mx, int(sk.get(name, 1))))
        maxed = lv >= mx
        base_mult = float(e["dmg"])
        cur_mult = base_mult * (1 + 0.2 * (lv - 1))
        next_mult = cur_mult if maxed else base_mult * (1 + 0.2 * lv)
        rows.append({
            "badge": tier[0],
            "name": name,
            "lv": lv, "max": mx, "maxed": maxed,
            # 卡片只有两行位：放「来源 + 斗气 + 短释」；完整说明走 斗技详情
            # 2026-11-15 梳理后必须标清「本宗 / 通用 / 异火 / 游戏自设」，
            # 免得再把炼药大会之类的东西混进斗技卡。
            "desc": (f"[{tier}] {_origin_mark(e)} 斗气 {int(frac[tier] * 100)}%·mp_max ｜ "
                     f"{e.get('brief') or e.get('desc', '')}"),
            "base_mult": round(base_mult, 2),
            "cur_mult": round(cur_mult, 2),
            "next_mult": round(next_mult, 2),
            "dmg": int(round(raw_base * cur_mult * crit_mult)),
            "next_dmg": int(round(raw_base * next_mult * crit_mult)),
            "segments": mx, "lit": lv,
            "item": item,
            "item_need": 0 if maxed else cfg.skill_up_cost(lv),
        })

    fac_name = p.get("faction_name") or "无宗门"
    return {
        "icon": "bolt", "title": "斗技",
        "subtitle": (f"{fac_name} · 本宗 {own} 门 / 通用 {common} 门 · "
                     f"四槽上阵（{len(rows)}/{cfg.LOADOUT_SIZE}）"),
        "item": item, "item_count": have,
        "formula": "伤害口径：(力量×0.5 + 斗气×1.5) × 阶倍率 ×(1+0.2/级) × 暴击期望（未计目标防御）",
        "rows": rows,
        "tip": (f"通用=斗气大陆流通的基础斗技，非本宗传承 ｜ 上阵 <4技名> ｜ "
                f"现有 {item} ×{have}"),
    }


def _origin_mark(e: dict) -> str:
    """斗技来源标记：一眼分清本宗传承 / 通用基础 / 异火技 / 游戏自设。"""
    origin = e.get("origin") or "本宗"
    mark = {"本宗": "〔本宗〕", "通用": "〔通用〕", "异火": "〔异火〕"}.get(origin, f"〔{origin}〕")
    if e.get("core"):
        mark += "〔自设〕"
    return mark


def build_roster_data(p: dict) -> dict:
    """本宗斗技名录 → doupo_list 通用列表卡参数。

    与 render_skills 的区别：那张是「四槽上阵专用卡」，只画已上阵项；
    这张画**本宗全部斗技**（含未解锁、已收服异火的主动技），用于「斗技 全部」。
    基本固定的名录内容必须出卡片，不能拿纯文本糊弄。
    """
    sk = p.get("skills") or {}
    pool = core.skill_pool(p)
    tier_order = {t: i for i, t in enumerate(cfg.SKILL_TIERS)}
    pool_sorted = sorted(pool, key=lambda e: tier_order.get(e["tier"], 9))
    rows = []
    for e in pool_sorted:
        name = e["name"]
        unlocked = bool(e.get("unlocked"))
        equipped = bool(e.get("equipped"))
        state = "上阵" if equipped else (
            "可学" if unlocked else f"未解锁 lv{e.get('unlock', '?')}")
        lv = max(1, min(5, int(sk.get(name, 1))))
        frac = int(cfg.SKILL_TIER_MP_FRAC.get(e["tier"], 0.2) * 100)
        sub = f"{_origin_mark(e)}{e.get('rank', '')} · {e.get('kind', '')} · 斗气 {frac}%"
        if e.get("flame"):
            sub = f"异火技〔{e['flame']}〕 · " + sub
        brief = str(e.get("brief") or e.get("desc") or "")
        if brief:
            sub += " ｜ " + brief
        rows.append({
            "badge": e["tier"][0],
            "title": name,
            "right": state,
            "sub": sub,
            "bar": {"cur": lv, "max": 5},
        })
    fac_name = p.get("faction_name") or "无宗门"
    on = sum(1 for e in pool if e.get("equipped"))
    own = sum(1 for e in pool if e.get("origin") == "本宗")
    common = sum(1 for e in pool if e.get("origin") == "通用")
    return {
        "icon": "bolt", "title": "本宗斗技",
        "subtitle": (f"{fac_name} · 共 {len(rows)} 门（本宗 {own} / 通用 {common}） · "
                     f"上阵 {on}/{cfg.LOADOUT_SIZE}"),
        "rows": rows,
        "tip": "〔通用〕为斗技堂流通基础斗技，非本宗传承 ｜ 斗技详情 <技名> 看原著出处",
    }


async def render_roster(p: dict) -> bytes | None:
    """「斗技 全部」出图：本宗全部斗技名录卡。"""
    return await _render_list("doupo_list", build_roster_data(p), "斗破本宗斗技")


def build_faction_data(p: dict) -> dict:
    """宗门 → doupo_faction 模板参数（卡片化展示，设计美化）。

    与 commands.cmd_faction 纯文本兜底同源：读同一个 player / FACTIONS，
    因此「图片里的宗门」和「文字里的宗门」永远不会说两回事。
    """
    core.settle(p)
    name = p.get("faction_name") or "无宗门"
    fid = p.get("faction") or ""
    fac_def = fac.FACTIONS.get(fid, {})
    POOL_CN = {"starter": "新手池", "advanced": "进阶池", "hidden": "隐世池"}
    pool = str(fac_def.get("pool", ""))
    base = str(fac_def.get("base", ""))
    npcs = list(fac_def.get("npcs", []) or [])
    flames = list(fac_def.get("flames", []) or [])
    lv = int(p.get("lv", 1))

    # 本门斗技统计（普通/高阶/特殊 门数）
    pool_sorted = core.skill_pool_sorted(p)
    tier_counts = {t: sum(1 for e in pool_sorted if e["tier"] == t)
                   for t in cfg.SKILL_TIERS}

    # 可转宗门（按卡池分组，只列当前境界已开放的）
    cands = core.faction_candidates(p)
    groups = []
    for pool_key in ("starter", "advanced", "hidden"):
        rows = [c for c in cands if c["pool"] == pool_key]
        if not rows:
            continue
        names = ["%s %d门" % (c["name"], c["skills"]) for c in rows]
        groups.append({"pool": POOL_CN.get(pool_key, pool_key), "names": names})

    cost = core.faction_switch_cost(p)
    open_pools = [POOL_CN[x] for x in core.faction_pools_open(p)]
    return {
        "name": name,
        "pool": POOL_CN.get(pool, pool),
        "base": base,
        "npcs": npcs,
        "flames": flames,
        "tier_counts": tier_counts,
        "total_skills": len(pool_sorted),
        "open_pools": open_pools,
        "lv": lv,
        "groups": groups,
        "switch_cost": cost,
        "tip": "用法：转宗 <宗门名>（只换师门并重配四槽，已升技能等级按技名保留）",
    }


async def render_faction(p: dict) -> bytes | None:
    """「宗门」出图：宗门概览 / 斗技统计 / 代表人物 / 传承异火 / 可转宗门。"""
    return await _render_list("doupo_faction", build_faction_data(p), "斗破宗门")

def build_flames_data(p: dict) -> dict:
    """异火列表 → doupo_list 模板参数。"""
    fl = p.get("flames") or {}
    dross = core.count_item(p, "药渣")
    rows = []
    for f in cfg.FLAMES:
        name = f["name"]
        lv = int(fl.get(name, 0))
        note = ""
        if name == cfg.FLAME_DIYAN:
            state = "已收服" if p.get("diyan") else "未得（成就斗帝自燃）"
            sub = f["desc"]
        elif lv >= cfg.FLAME_MAX_LV:
            state = f"{lv} 级（圆满）"
            sub = f["desc"]
        else:
            need = int(f["need"]) * (lv + 1)
            state = f"{lv}/{cfg.FLAME_MAX_LV} 级"
            sub = f["desc"]
            # 2026-11-15 card-design：「下级需 N 药渣」独立成 note 行，用显式
            # NOTE_GAP 与描述拉开；旧版用全角空格拼进 sub，长描述折行后会连读。
            note = f"下级需 {commands._fmt(need)} 药渣"
        rows.append({"title": name, "right": state, "sub": sub, "note": note})
    return {
        "icon": "star", "title": "异火",
        "subtitle": f"药渣 ×{dross}",
        "rows": rows,
        "tip": "用法：养火 青莲地心火（一次喂光现有药渣）",
    }


def build_shop_data(p: dict) -> dict:
    """宝库列表 → doupo_list 模板参数。"""
    rows = []
    for it in core.shop_list():
        rows.append({"title": it["name"], "right": f"{it['price']} 金", "sub": it["desc"]})
    return {
        "icon": "cart", "title": "随身商店",
        "subtitle": f"金币 {int(p.get('gold', 0))}",
        "rows": rows,
        "tip": "用法：买 血灵草 2",
    }


def build_bag_data(p: dict) -> dict:
    """背包列表 → doupo_list 模板参数。"""
    items = core.bag_slots(p)
    known = {r["name"]: r["desc"] for r in cfg.RECIPES}
    known.update({h["name"]: h["desc"] for h in cfg.HERBS})
    rows = []
    for name, n in items[:24]:
        rows.append({"title": f"{name} ×{n}", "sub": known.get(name, "")})
    return {
        "icon": "diamond", "title": "包裹",
        "subtitle": f"{len(items)}/24 格 · 金币 {int(p.get('gold', 0))}",
        "rows": rows,
        "tip": "用法：服丹 回灵丹",
    }


def build_rank_data(top: list) -> dict:
    """战力榜（已排序前 N）→ doupo_list 模板参数。"""
    rows = []
    for i, q in enumerate(top, 1):
        rows.append({
            "badge": f"#{i}",
            "title": q.get("name") or "无名",
            "right": f"{cfg.full_realm(int(q.get('lv', 1)))} · 战力 {core.power(q)}",
            "accent": i == 1,
        })
    return {
        "icon": "arrow_up", "title": "本群战力榜",
        "subtitle": f"前 {len(rows)} 名",
        "rows": rows,
        "tip": "发 抢 @某人 掠夺 10% 金币",
    }


async def render_skills(p: dict) -> bytes | None:
    # 专用模板：技能描述 + 详细伤害 + 五段斗气条 + 重点大字粗体。
    # 不再复用 doupo_list，避免把异火/宝库/背包/排行一起改坏。
    return await _render_list("doupo_skills", build_skills_data(p), "斗破斗技")


async def render_flames(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_flames_data(p), "斗破异火")


async def render_shop(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_shop_data(p), "斗破宝库")


async def render_bag(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_bag_data(p), "斗破背包")


async def render_rank(top: list) -> bytes | None:
    return await _render_list("doupo_list", build_rank_data(top), "斗破排行")
