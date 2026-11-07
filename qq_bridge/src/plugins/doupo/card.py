# -*- coding: utf-8 -*-
"""斗破苍穹·双帝之战 · 人物卡渲染。

把 player dict 渲染成「斗破大陆」图片卡片，供群聊「dp人物 / dp属性 / dp面板」使用。
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
        "tip": f"共 {commands.menu_total()} 条口令 · 与「修仙」重名的已统一让出，斗破一律 dp 前缀",
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
# 通用列表卡（斗技 / 异火 / 宝库 / 背包 / 排行）
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
    """斗技列表 → doupo_list 模板参数。"""
    sk = p.get("skills") or {}
    rows = []
    for s in cfg.SKILLS:
        lv = int(sk.get(s["name"], 1))
        rows.append({
            "badge": str(s["key"]),
            "title": f"{s['name']}　{lv}/{s['max']}",
            "sub": "升一级伤害 +20%",
            "bar": {"cur": lv, "max": s["max"]},
        })
    item = cfg.SKILL_UP_ITEM
    return {
        "icon": "bolt", "title": "斗技",
        "subtitle": "快捷键 1/3/7/9 · 升一级伤害 +20%",
        "rows": rows,
        "tip": f"用法：dp升级 龙吟碎 · 现有 {item} ×{core.count_item(p, item)}",
    }


def build_flames_data(p: dict) -> dict:
    """异火列表 → doupo_list 模板参数。"""
    fl = p.get("flames") or {}
    dross = core.count_item(p, "药渣")
    rows = []
    for f in cfg.FLAMES:
        name = f["name"]
        lv = int(fl.get(name, 0))
        if name == cfg.FLAME_DIYAN:
            state = "已收服" if p.get("diyan") else "未得（成就斗帝自燃）"
            sub = f["desc"]
        elif lv >= cfg.FLAME_MAX_LV:
            state = f"{lv} 级（圆满）"
            sub = f["desc"]
        else:
            need = int(f["need"]) * (lv + 1)
            state = f"{lv}/{cfg.FLAME_MAX_LV} 级"
            sub = f"{f['desc']}　下级需 {commands._fmt(need)} 药渣"
        rows.append({"title": name, "right": state, "sub": sub})
    return {
        "icon": "star", "title": "异火",
        "subtitle": f"药渣 ×{dross}",
        "rows": rows,
        "tip": "用法：dp养火 青莲地心火（一次喂光现有药渣）",
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
        "tip": "用法：dp买 血灵草 2",
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
        "tip": "用法：dp服丹 回灵丹",
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
        "tip": "发 dp抢 @某人 掠夺 10% 金币",
    }


async def render_skills(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_skills_data(p), "斗破斗技")


async def render_flames(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_flames_data(p), "斗破异火")


async def render_shop(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_shop_data(p), "斗破宝库")


async def render_bag(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_bag_data(p), "斗破背包")


async def render_rank(top: list) -> bytes | None:
    return await _render_list("doupo_list", build_rank_data(top), "斗破排行")
