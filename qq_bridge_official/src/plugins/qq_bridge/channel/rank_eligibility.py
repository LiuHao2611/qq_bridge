# -*- coding: utf-8 -*-
"""市价卡「rank 按钮资格」判定（仅官方副本，2026-11-11 第五次修改，AST/数据零手工）。

## 为什么第五次修改（用户「战甲武器可没有等级，只有赋能和 mod 才有等级」）

第四次修改给所有「市价口令 + 物品名」一律挂 ``满级 / 0级`` 两个带参按钮。用户
真实群反馈：``wm 绝路``（绝路=武器 Rubico Prime）也冒出 ``满级 / 0级``，点了
必然进 ``market_text`` 的 ``if not is_mod: return "「绝路」不是 MOD，无法按等级
过滤"`` —— 按钮是错的。用户明确：「战甲武器可没有等级，只有赋能和 mod 才有
等级，你搞清楚了」，要求举一反三覆盖全部物品类别，而不是只拉黑「绝路」一词。

## 唯一事实源：复用 business 真函数，绝不复刻解析（零漂移）

按钮资格的本质问题只有一个：``market_text(物品)`` 会不会因为「不是 MOD」而拒绝
``满级/R0`` 等级过滤。而 ``market_text`` 在线是用 ``is_mod = any("rank" in o
for o in orders)``（请求 WM API 后按订单动态判定）＋ ``is_arcane``（wikidata tags
或 url 前缀）—— 按钮层发消息前拿不到 orders，但可以用**同一套离线数据**判定：

  * 解析：直接调用 ``warframe._resolve_url_name(item) -> slug``（business 真函数，
    逐字节同源，绝不在 channel 层复刻）。这一步是「绝路→武器 / 狂怒→MOD furor /
    充沛→赋能 arcane_energize / 加速→MOD quickening」零歧义的关键——我实测过任何
    自写简化解析都会把「狂怒」「充沛」判错。``ITEM_ALIASES``（绝路→rubico_prime、
    沙皇→kuva_zarr、充沛→arcane_energize）优先级、aliases.json 黑话、wf_items_v2
    精确中文/英文优先、形近字、后缀变体、部件组合，全部由真函数一次性提供。
  * 分类：``warframe._wf_dict()["by_slug"][slug]["tags"]``（WM 全量 3840 件词典，
    与 ``market_text`` 的 ``_wf_dict`` 同一份），按 tags 判定物品类别：
      - ``mod in tags and riven_mod not in tags`` → MOD（有等级）；
      - ``arcane_enhancement in tags`` → 赋能（有等级）；
      - 其余（``weapon`` / ``warframe`` / ``component`` / ``blueprint`` / 紫卡
        ``riven_mod`` / 赋能头盔 ``arcane_helmet`` / 空 tag） → **无等级**。

这样按钮资格与 ``market_text`` 的等级过滤在物品类别上严格同源：WM 只有 MOD /
赋能的订单带 ``rank`` 字段，与 tags 的 ``mod`` / ``arcane_enhancement`` 一一对应。

## 依赖边界（为何这样设计 import）

``qq_keyboard`` 是 channel 层纯标准库模块，宿主离线单测时按文件直载、无包上下文，
且 warframe 依赖 nonebot/httpx（重量级）。故本模块：
  * 顶层零 import warframe / nonebot / httpx，只用标准库；
  * ``resolve/tags`` 两个后端函数**延迟**解析：优先用 :func:`set_backend` 注入
    （容器真运行环境由 ``qq_keyboard`` 在首次调用时注入 business 真函数；宿主单测
    注入真函数的直载版本）；未注入时惰性走 ``from ..game_tools.warframe import``
    （正常插件包上下文）。
  * 任意异常（词典缺失 / slug 不在词典 / warframe 未加载）→ 一律返回 ``False``
    （**保守不挂 rank 按钮**）：宁可少给按钮，绝不误给武器挂等级按钮。

纯函数、无全局可变缓存（注入的 backend 只读），宿主/容器可离线单测。
"""
from __future__ import annotations

from typing import Callable, Iterable

#: 后端函数类型：``resolve(item: str) -> slug: str``；``tags(slug: str) -> Iterable[str]``。
ResolveFn = Callable[[str], str]
TagsFn = Callable[[str], Iterable[str]]

_backend: tuple[ResolveFn, TagsFn] | None = None


def set_backend(resolve: ResolveFn, tags: TagsFn) -> None:
    """注入 business 真函数（容器由 qq_keyboard 装配；宿主单测注入直载版）。

    ``resolve`` 必须是 ``warframe._resolve_url_name`` 本体（逐字节同源），
    ``tags`` 必须读 ``warframe._wf_dict()["by_slug"]``。测试禁用本钩子造假数据——
    注入验证只用于「把真结果反过来错判」，不是替换事实源。
    """
    global _backend
    _backend = (resolve, tags)


def reset_backend() -> None:
    """清掉注入，回到惰性相对 import（测试隔离用）。"""
    global _backend
    _backend = None


def _default_backend() -> tuple[ResolveFn, TagsFn]:
    """插件正常包上下文：惰性导入 business 真函数（顶层零重量级依赖）。"""
    from ..game_tools.warframe import (  # noqa: PLC0415
        _resolve_url_name as resolve,
        _wf_dict as wf_dict,
    )

    def tags(slug: str) -> Iterable[str]:
        return ((wf_dict().get("by_slug") or {}).get(slug) or {}).get("tags") or []

    return resolve, tags


def _is_rankable_tags(slug: str, tags: Iterable[str]) -> bool:
    """按 wf_items_v2 tags 判「有等级」——MOD / 赋能为真，其余全假。

    与 ``market_text`` 在线 ``is_mod``/``is_arcane`` 在物品类别上严格同源：
      * MOD：``mod`` 且非 ``riven_mod``（紫卡 7 件带 mod+riven_mod，无等级，排除）；
      * 赋能：``arcane_enhancement``（赋能头盔是 ``arcane_helmet`` 而非本 tag，
        再叠 slug 含 ``_helmet`` 双保险，与 business ``is_arcane`` 排除式一致）。
      * 武器 / 战甲 / 部件 / 蓝图 / 空 tag：tags 不含上述 → 无等级。
    """
    t = set(tags or ())
    is_mod = ("mod" in t) and ("riven_mod" not in t)
    is_arcane = ("arcane_enhancement" in t) and "_helmet" not in (slug or "")
    return bool(is_mod or is_arcane)


def can_have_rank(item: str) -> bool:
    """物品是否可挂 ``满级 / R0`` 等级按钮（只有 MOD / 赋能为真）。

    参数 ``item`` 是市价口令后的**纯物品名**（已由 qq_keyboard 剥离前缀等级词），
    如 ``绝路`` / ``持久力 Prime`` / ``赋能·充沛``。返回 ``False`` 的三种情形：
      1. 空 / 非 MOD/赋能（武器、战甲、部件、蓝图、紫卡、赋能头盔）；
      2. slug 不在 wf_items_v2 词典（tags 空）；
      3. backend 异常（词典缺失 / warframe 未加载）—— 保守不挂，绝不误挂。
    """
    name = (item or "").strip()
    if not name:
        return False
    global _backend
    try:
        if _backend is None:
            _backend = _default_backend()  # 惰性装配 business 真函数，仅一次
        resolve, tags = _backend
        slug = resolve(name)
        return _is_rankable_tags(slug, tags(slug))
    except Exception:  # noqa: BLE001 — 保守降级：判不出就不挂等级按钮
        return False
