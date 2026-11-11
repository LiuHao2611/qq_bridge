# -*- coding: utf-8 -*-
"""game_tools/riven_alias_patch —— 紫卡词条中文别名补丁层（官方副本，2026-11-11）。

为什么需要这一层（证据，不是拍脑袋）：
  * DE Public Export 派生的 ``riven_stat_zh.json`` 只收「官方模板名」，卡面/OCR 常用
    **旧名、简写、社区叫法**，生成物里没有 → ``riven._find_stat`` 精确匹配落空，
    要么整条词条丢失（en=None），要么退回模糊匹配被更短的词条抢走（错认）。
  * 2026-11-11 用户图2（正确参照）实测复现（容器内真 ``_find_stat``）：
      '+90.4% 近战连击效率'      -> None            ← 整条丢
      '+90.4% 连击效率'          -> None            ← 整条丢
      '-109.3% 近战攻击时暴击率'  -> Critical Chance  ← 错认成普通暴击率
    Wiki（warframe.wiki《Combo Counter》）查证：
      Heavy Attack Efficiency **旧名即 "Combo Efficiency"**（现名「重击效率」，
      社区/旧客户端写「(近战)连击效率」）；base=73.44 按 3正1负 factor 0.9375
      × disp 1.2 ×(rank+1)/9 得区间 74.4%–90.9%，与图2卡面逐字吻合 → 是同一 en 键。
      Critical Chance for Slide Attack 卡面常写「滑行攻击时暴击率/近战攻击时暴击率/滑暴」。

补丁纪律（红线）：
  * **只补 aliases，不新增 en 键、不造中文主名、不改任何 base 数值**；所有被补的 en
    键必须已存在于生成物，且新增别名不得与既有词条名冲突（导入时自检，见 _validate）。
  * **零改** ``riven.py``、``riven_card.py``、``riven_stat_base.json``、
    ``riven_stat_zh.json``；本文件是独立层，不进 wf_sync 的同步清单（同步脚本只写
    ``data/warframe_items/*.json``，不碰 ``game_tools/*.py``），故不会被覆盖。
  * 幂等：``ensure()`` 可重复调用；两个消费方的加载函数本就幂等
    （``riven._load_riven_data`` 判 ``_RIVEN["disp"]``，``riven_card._load_riven_tables``
    判 ``_RAW["disp"]``），本层在其后叠加，业务后续再调加载函数会走 already-loaded 直接
    return，不会把补丁冲掉。

注入点：``game_tools/__init__.py`` 在 ``from . import ... riven, warframe ...`` 之后
调用 ``riven_alias_patch.ensure()``，保证任何 OCR / 出卡路径之前补丁已生效。
"""
from __future__ import annotations

import re as _re

__all__ = ["ALIAS_PATCH", "ensure", "merge_into", "rebuild_zh_flat", "validate"]

#: en 键 -> 追加的卡面别名。全部是同义/旧名/简写，绝不引入新词条或新数值。
#: 顺序无意义；重复与既有别名会在 merge_into 里去重。
ALIAS_PATCH: dict[str, tuple[str, ...]] = {
    # 现名「重击效率」，旧名/社区名「(近战)连击效率」——同一 en 键，同一 base 73.44。
    "Heavy Attack Efficiency": ("近战连击效率", "连击效率", "近战连击"),
    # 滑行攻击暴击几率：卡面写法「滑行攻击时暴击率/近战攻击时暴击率/滑暴」。
    "Critical Chance for Slide Attack": (
        "滑行攻击时暴击率", "滑行攻击暴击率", "近战攻击时暴击率", "滑暴",
    ),
}

_done = False


def _norm(name: str) -> str:
    """与 riven.py:116 同款归一：去所有空白 + 小写。"""
    return _re.sub(r"\s+", "", name or "").lower()


def validate(zh: dict) -> None:
    """导入自检（防补丁造错）：每个 en 键必须已存在于生成物，且别名不得与
    其它 en 键的既有名/别名冲突（否则会把一条词条认成另一条）。违反即抛，
    让守门/启动期暴露，绝不静默生效。"""
    if not isinstance(zh, dict):
        raise ValueError("riven_alias_patch: zh 表未加载")
    # 先收集既有「规范名 -> en」占用表（含主名与已有别名，不含本补丁新增）
    owner: dict[str, str] = {}
    for en, info in zh.items():
        names = [str((info or {}).get("zh", ""))]
        names += [str(a) for a in ((info or {}).get("aliases") or [])]
        for n in names:
            key = _norm(n)
            if key:
                # 生成物自身若已有重复名，以先出现的为准，不在补丁层报错
                owner.setdefault(key, en)
    for en, aliases in ALIAS_PATCH.items():
        if en not in zh:
            raise ValueError("riven_alias_patch: 补丁 en 键不存在于生成物: %r" % en)
        for a in aliases:
            key = _norm(a)
            held = owner.get(key)
            if held and held != en:
                raise ValueError(
                    "riven_alias_patch: 别名 %r 已属于 %r，不能挂到 %r" % (a, held, en))


def merge_into(zh: dict) -> bool:
    """把 ALIAS_PATCH 的别名合并进一个 zh 表（就地）；返回是否发生了新增。"""
    changed = False
    for en, aliases in ALIAS_PATCH.items():
        info = zh.setdefault(en, {"zh": en, "aliases": []})
        if not isinstance(info, dict):
            info = {"zh": en, "aliases": []}
            zh[en] = info
        cur = info.setdefault("aliases", [])
        if not isinstance(cur, list):
            cur = []
            info["aliases"] = cur
        for a in aliases:
            if a not in cur:
                cur.append(a)
                changed = True
    return changed


def rebuild_zh_flat(zh: dict) -> list[tuple[str, str]]:
    """按 riven.py:110-118 的**完全同款**规则重建中文别名扁平表。

    原规则：names = [zh 主名] + aliases；去空白、小写；按长度降序（最具体先匹配）。
    必须与 riven.py 保持一致，否则会改变匹配优先级引入回归。
    """
    zh_flat: list[tuple[str, str]] = []
    for k, info in (zh or {}).items():
        names = [(info or {}).get("zh", "")] + ((info or {}).get("aliases") or [])
        for n in names:
            if n:
                zh_flat.append((_norm(n), k))
    zh_flat.sort(key=lambda x: len(x[0]), reverse=True)
    return zh_flat


def ensure() -> bool:
    """幂等安装补丁。返回本次是否实际安装（首次 True，后续 False）。"""
    global _done
    if _done:
        return False
    # 延迟导入，避免与 game_tools/__init__ 形成 import 环；调用点在本包 riven 已导入之后。
    from . import riven as _riven
    from . import riven_card as _riven_card

    # 主动触发两个消费方的幂等懒加载；补丁叠加后，业务再次调用加载函数会 already-return。
    _riven._load_riven_data()
    _riven_card._load_riven_tables()

    r_zh = _riven._RIVEN.get("zh")
    c_zh = _riven_card._RAW.get("zh")

    # 自检用消费方各自的「原始」表（此刻尚未被补丁污染），确认别名不越权、en 键真实存在。
    if isinstance(r_zh, dict):
        validate(r_zh)

    # 叠加到两个消费方各自的 zh 表（它们是两次独立 json.loads，对象不同，都要改）。
    if isinstance(r_zh, dict):
        merge_into(r_zh)
    if isinstance(c_zh, dict):
        merge_into(c_zh)

    # 只有 riven.py 维护 zh_flat，按原规则重建；riven_card 直接读 zh 表，无扁平表。
    if isinstance(r_zh, dict):
        _riven._RIVEN["zh_flat"] = rebuild_zh_flat(r_zh)

    _done = True
    return True
