# -*- coding: utf-8 -*-
"""文字修仙 · 状态卡渲染。

把 player dict 渲染成「修仙路」图片卡片，供群聊「我要修仙 / 我的修仙」使用。
**渲染失败必须降级为纯文本**（铁律：不允许因为图片挂了就丢消息或静默失败）。

为什么用 importlib 独立加载 pic 包，而不是 `from ..qq_bridge import pic`
------------------------------------------------------------------------
`qq_bridge/__init__.py` 会注册全部 matcher、初始化主插件链路（含 LLM 相关），
而 xiuxian 的单测（test_core / test_commands / test_e2e）刻意**不加载主插件**，
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

from . import commands
from . import config as cfg
from . import core

log = logging.getLogger("qq_bridge.xiuxian.card")

#: 卡片路由用的业务卡名（命中卡片级主题绑定后，主题编辑器可按此卡单独改色）
CARD_NAME = "文字修仙"

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
        log.warning("修仙卡片不可用（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None


def available() -> bool:
    """卡片能力是否可用（供自检 / 测试用；不影响正常流程）。"""
    return _load_pic() is not None


def build_data(p: dict) -> dict:
    """player dict → xiuxian 模板参数。"""
    realm = int(p.get("realm") or 0)
    realm_max = realm >= cfg.MAX_REALM
    # 突破门槛 = 下一境界的修为数（开源表语义），不是当前境界的
    need = 0 if realm_max else core.breakthrough_need(p)
    exp = int(p.get("exp") or 0)
    inv = "、".join(f"{k}×{v}" for k, v in (p.get("inv") or {}).items()) or "空空如也"
    life = int(p.get("life") or 0)
    age = int(p.get("age") or 0)
    # 修为够 = 可突破；再叠加破镜 CD（开源 level_up_cd = 60 分钟），冷却中不算"可突破"
    cd_left = 0 if realm_max else core.bt_cd_left(p)
    can_break = core.can_breakthrough(p) and cd_left <= 0
    return {
        "name": p.get("name") or "无名",
        "realm": cfg.REALMS[realm]["name"],
        "exp": exp,
        "need": need,
        "realm_max": realm_max,
        "can_break": can_break,
        "cd_minutes": (cd_left + 59) // 60 if cd_left else 0,
        "next_realm": "" if realm_max else cfg.REALMS[realm + 1]["name"],
        "root": p.get("root") or "凡体",
        "root_mult": p.get("root_mult"),
        "power": core.power(p),
        "atk": int(p.get("atk") or 0),
        "dfn": int(p.get("def") or 0),
        "crit_pct": round(float(p.get("crit") or 0) * 100),
        # 突破成功率（%）：含失败累积 + 丹药加成；已至巅峰则不显示（0）
        "rate_pct": 0 if realm_max else round(core.bt_rate_effective(p)),
        "ling_shi": int(p.get("ling_shi") or 0),
        "life_left": max(0, life - age),
        "age": age,
        "wins": int(p.get("wins") or 0),
        "losses": int(p.get("losses") or 0),
        "sect": p.get("sect") or "无",
        "secluding": bool(p.get("secluding")),
        "inv": inv,
    }


def build_menu_data() -> dict:
    """口令菜单 → xiuxian_menu 模板参数。

    数据源是 commands.MENU_GROUPS（唯一源），与纯文本兜底同源，
    因此图片与文字永远不会「说的不是一回事」。
    """
    return {
        "title": "修仙菜单",
        "groups": [
            {"name": name, "rows": [[cmd, desc] for cmd, desc in rows]}
            for name, rows in commands.MENU_GROUPS
        ],
        "tip": f"共 {commands.menu_total()} 条口令 · 玩法细则发「修仙帮助」",
    }


def build_help_data() -> dict:
    """玩法细则 → xiuxian_help 模板参数（数据源：commands.HELP_GROUPS）。"""
    return {
        "title": "修仙玩法",
        "groups": [
            {"name": name, "rows": [[n, d] for n, d in rows]}
            for name, rows in commands.HELP_GROUPS
        ],
        "tip": f"共 {commands.help_total()} 条规则 · 发「修仙菜单」看全部口令",
    }


async def _render_card(template: str, data: dict, what: str) -> bytes | None:
    """渲染任意修仙卡片：主题链路 → 内置 light 兜底 → None（调用方回纯文本）。"""
    pic = _load_pic()
    if pic is None:
        return None
    try:
        return await pic.render(template, card=CARD_NAME, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("%s 主题链路渲染失败，尝试内置 light 兜底：%s", what, e)
    try:
        return await asyncio.to_thread(
            pic.render_sync, template, "light", CARD_NAME, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("%s 渲染失败（已降级为纯文本）：%s: %s", what, type(e).__name__, e)
        return None


async def render_help() -> bytes | None:
    """渲染玩法细则卡。"""
    return await _render_card("xiuxian_help", build_help_data(), "修仙玩法卡")


async def render_menu() -> bytes | None:
    """渲染口令菜单卡，返回 PNG bytes；失败返回 None（调用方降级纯文本）。"""
    return await _render_card("xiuxian_menu", build_menu_data(), "修仙菜单卡")


async def render_status(p: dict, tip: str | None = None) -> bytes | None:
    """渲染角色状态卡，返回 PNG bytes；失败返回 None（调用方降级纯文本）。

    :param tip: 覆盖卡片底部提示；None = 按是否闭关自动选默认文案。
    """
    pic = _load_pic()
    if pic is None or not p:
        return None
    data = build_data(p)
    if tip is not None:
        data["tip"] = tip
    # 首选：走完整主题链路（卡片绑定 > 自定义主题 > PIC_THEME > 内置）+ 磁盘缓存
    try:
        return await pic.render("xiuxian", card=CARD_NAME, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("修仙卡片主题链路渲染失败，尝试内置 light 兜底：%s", e)
    # 兜底：显式内置 light 主题，绕开主题解析（防止主题配置损坏导致整卡不可用）
    try:
        return await asyncio.to_thread(
            pic.render_sync, "xiuxian", "light", CARD_NAME, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("修仙卡片渲染失败（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None
