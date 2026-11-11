# -*- coding: utf-8 -*-
"""卡片主题的内置默认绑定（随代码交付，绕过 ``data/`` 被 gitignore 导致的不交付问题）。

2026-11-09：用户要求市价卡做浅色版本；2026-11-10：签到卡同样改浅色 Rabbit 风。
``data/pic_card_themes.json`` 是运行时用户配置、且被 ``.gitignore`` 忽略、不入库 ——
只写配置的话 clone 后这些卡会回落到全局霓虹主题，浅色版本等于没交付。因此把默认绑定
放进源码，保证「星际战甲·市价」「checkin」开箱即浅色。

优先级（与 ``theme.resolve`` 完全一致）：**显式 theme_name > 配置绑定 > 内置默认 > 全局**。

  * 配置文件里同名条目优先（用户可随时改回深色）；
  * 没有配置文件时由这里兜底。
"""
from __future__ import annotations

#: 内置默认卡片主题绑定（card 名 → 主题名）。
_DEFAULT_CARD_BINDS: dict[str, str] = {
    "星际战甲·市价": "light",
    "checkin": "light",   # 签到卡：浅色 Rabbit 风（2026-11-10，替代旧 brutalist 深色终端风）
    # 紫卡全面分析卡（2026-11-11 去 Rabbit 化）：用户选定项目 light 主题底
    # （贴纸平铺 + 双线描边，与市价/签到/裂隙一致），替代原 Rabbit 粉纹/纯白底。
    # 必须内置绑定：Config.PIC_THEME 默认 neon，不绑定则拿到深色霓虹装饰。
    # 两个 render 调用点（game_tools/__init__.py:1347/1393）均传此 card 名。
    "星际战甲·紫卡全面分析": "light",
}


def default_card_theme(card: str | None) -> str:
    """返回 card 的内置默认主题名；无绑定返回 ''。"""
    return _DEFAULT_CARD_BINDS.get(card or "", "")


def resolve_card_theme(theme_name, card, card_theme_fn=None) -> str:
    """计算最终传给 ``theme.resolve`` 的 theme_name。

    优先级：显式 ``theme_name`` > 配置绑定（``card_theme_fn(card)``）> 内置默认 > 原值。
    无任一层命中时返回 ``""``（空串，交给 ``theme.resolve`` 走全局默认）。

    :param theme_name: 调用方传入的主题名（None = 未指定）。
    :param card: 业务卡片名（如 "星际战甲·市价"）。
    :param card_theme_fn: 读配置文件的函数（``theme.card_theme``）；None = 跳过配置层。
    """
    name = theme_name or None
    if name is not None:
        return name
    cfg = ""
    if card_theme_fn is not None:
        try:
            cfg = str(card_theme_fn(card) or "").strip().lower()
        except Exception:  # noqa: BLE001 — 配置读不出就回落内置默认
            cfg = ""
    if cfg:
        return cfg
    return default_card_theme(card)
