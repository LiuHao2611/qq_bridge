# -*- coding: utf-8 -*-
"""警报卡真实图标解析器：物品 / 星球 / 现金 → 游戏内真实美术资产（非几何占位）。

复用 ``weapon_icons`` 的 DE Public Export manifest 与下载基础设施：
  * ``_de_manifest`` 提供全量 ``{uniqueName: textureLocation}``（20154 条）；
  * ``de_icon_url`` / ``_urllib_get`` / ``icon_cache_path`` / ``_CACHE_DIR`` 提供取图与落盘；
  * 图标统一缓存到 ``data/warframe_icons/``，文件名 = textureLocation 末段（如 ``Moonflesh.png``），
    这样 ``build()`` 里直接用 ``weapon_icons.icon_cache_path`` 同步查缓存即可，零外呼。

解析策略（与武器一致，双索引）：
  * **by_tail**：``norm(uniqueName 末段) -> uniqueName``（覆盖 Forma / OrokinCell / NeuralSensor …）；
  * **by_tex**：``norm(textureLocation 末段去扩展名) -> uniqueName``（覆盖 Moonflesh / 各类 Currency …）。
两者互补：单靠 uniqueName 漏掉 Events / Currency 类，单靠 textureLocation 漏掉 MiscItems 类。

**现金（Credits，国服译名「现金」）例外**：它是 HUD 货币图标（三张扇形叠卡，青边深卡面），
不在 PublicExport 清单覆盖范围内 —— 清单只导出 ``Lotus/Interface/Icons/StoreIcons/…``，
而货币 UI 图标位于 ``Lotus/Interface/Icons/…`` 根下；清单里名字相近的
``GenericCreditIcon.png`` 是「Credit Chip 实体道具」的 3/4 视角渲染（单张卡片），
与游戏里真正的现金图标不是同一张图（2026-11-05 用户指出「这星币的图标不对」后核实）。
故现金图标走 ``_CASH_URL``：官方 wiki 镜像的**同一张游戏内资产**（512×512 三叠卡），
URL 按 wikia 的 md5 路径规则固定，落盘名 ``Credits.png``。

设计要点：
  * 渲染线程（``build``）只做**同步缓存命中**检查，绝不外呼；
  * 业务层（``_send_alerts``）在 ``render`` 前 ``await ensure_alerts_icons`` 预拉取落盘；
  * 任何解析/下载失败都返回 ``None``，由 ``build`` 回落几何图标——卡片永远能出图。
"""
from __future__ import annotations

import asyncio
import io
import re

from . import weapon_icons as _wi

#: 现金（Credits，国服「现金」）HUD 货币图标：三张扇形叠卡、青边深卡面。
#: 落盘名与 wikia 资源名一致（``Credits.png``），build() 同步命中同一张缓存。
_CASH_FILE = "Credits.png"
#: 官方 wiki 镜像的同一张游戏资产；wikia 静态路径 = ``/<md5[0]>/<md5[:2]>/<文件名>``，
#: md5("Credits.png") = 2bd9459…，故为 ``/2/2b/Credits.png``。
_CASH_URL = "https://static.wikia.nocookie.net/warframe/images/2/2b/Credits.png"

_TEX_IDX: dict | None = None

#: 星球奖杯裁切（相对比例）：奖杯 = 星球本体 + 金色底座，卡片里只留本体。
#: 默认值来自球体奖杯的逐行实测——主体下缘 y≈0.55、底座从 y≈0.62 起（11 个球体一致），
#: 故裁到 0.62 刚好带一点点余量、不切球体。
PLANET_CROP = (0.18, 0.02, 0.82, 0.62)
#: 非球体奖杯（形态/悬停高度不同）**按墨迹包围盒实测**（alpha>24 的行列范围），
#: 再按 26px 实际渲染目视核对；改请重跑 `tools/check_fissure_layout.py --planets` 复看。
_PLANET_CROP = {
    "deimos":       (0.20, 0.02, 0.80, 0.41),   # 悬浮变形虫，底座离得远
    "kuvafortress": (0.18, 0.01, 0.82, 0.75),   # 空间站，主体一直延伸到近底座
    "void":         (0.11, 0.02, 0.89, 0.80),   # 暗色球体，赤道更靠下
    "zariman":      (0.12, 0.00, 0.89, 0.80),   # 同上，白球 + 暗边
}


def planet_crop(planet_en: str | None) -> tuple:
    """星球英文名 → 该奖杯图标的裁切框（去底座、只留本体）。"""
    return _PLANET_CROP.get(_norm(planet_en), PLANET_CROP)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tex_index() -> dict:
    """``norm(textureLocation 末段去扩展名) -> uniqueName``，仅建一次。"""
    global _TEX_IDX
    if _TEX_IDX is not None:
        return _TEX_IDX
    idx: dict[str, str] = {}
    for un, tl in _wi._de_manifest(allow_download=False).items():
        base = _norm(tl.split("/")[-1].split("!")[0].rsplit(".", 1)[0])
        if base:
            idx.setdefault(base, un)
    _TEX_IDX = idx
    return idx


#: 人工校正表：DE manifest 里这些物品指向「多物品共用」的通用占位纹理，
#: 视觉上不够辨识，改用专属纹理（仍为 DE 官方资产）。
#:   Forma 默认 → GenericComponent.png（18 个物品共用）；Forma.png 才是标志性的福马本体。
_OVERRIDE = {
    "forma": "/Lotus/Upgrades/Skins/Sigils/FormaSigil",   # → Forma.png
}


def _resolve_unique(name_en: str | None):
    """英文物品名 → DE uniqueName（人工校正 > by_tail > by_tex）。"""
    q = _norm(name_en)
    if not q:
        return None
    un = _OVERRIDE.get(q)
    if un:
        return un
    un = _wi._de_base_index().get(q)
    if un:
        return un
    return _tex_index().get(q)


async def _download(unique_name: str, timeout: float = 10.0) -> str | None:
    """uniqueName → 下载落盘 → 返回缓存文件名（textureLocation 末段）。失败 None。"""
    tl = _wi._de_texture(unique_name)
    if not tl:
        return None
    fname = tl.split("/")[-1].split("!")[0]
    if _wi.icon_cache_path(fname) is not None:
        return fname
    url = _wi.de_icon_url(unique_name)
    if not url:
        return None
    try:
        data, _ = await asyncio.to_thread(_wi._urllib_get, url, timeout)
    except Exception:  # noqa: BLE001 — 单源失败就放弃，回落几何图标
        return None
    if not data or len(data) < _wi._MIN_BYTES:
        return None
    if not (data[:8] == _wi._PNG_SIG or data[:3] == b"\xff\xd8\xff"):
        return None
    try:
        _wi._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_wi._CACHE_DIR / fname).write_bytes(data)
    except OSError:
        return None
    return fname


async def ensure_item_icon(name_en: str | None, timeout: float = 10.0) -> str | None:
    """英文物品名 → 预取真实图标，返回缓存文件名（如 ``Moonflesh.png``）。取不到 None。"""
    if not name_en:
        return None
    un = _resolve_unique(name_en)
    if not un:
        return None
    return await _download(un, timeout)


def _write_png(fname: str, data: bytes) -> str | None:
    """把下载到的图片统一转成 PNG 落盘（wiki 会按 Accept 协商回 webp）。失败 None。"""
    from PIL import Image  # 局部导入：本模块被 pic 包导入时不额外拉起 PIL

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.convert("RGBA").save(_wi._CACHE_DIR / fname, "PNG")
    except Exception:  # noqa: BLE001 — 下载到的是错误页/截断图
        return None
    return fname if _wi.icon_cache_path(fname) is not None else None


async def ensure_credits_icon(timeout: float = 10.0) -> str | None:
    """预取现金（Credits）货币图标，返回缓存文件名 ``Credits.png``；取不到 None。"""
    if _wi.icon_cache_path(_CASH_FILE) is not None:
        return _CASH_FILE
    try:
        data, _404 = await asyncio.to_thread(_wi._urllib_get, _CASH_URL, timeout)
    except Exception:  # noqa: BLE001 — 网络故障即放弃（卡片回落几何图标）
        return None
    if not data or len(data) < _wi._MIN_BYTES:
        return None
    _wi._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(_write_png, _CASH_FILE, data)


async def ensure_planet_icon(planet_en: str | None, timeout: float = 10.0) -> str | None:
    """英文星球名（如 ``Eris`` / ``Kuva Fortress``）→ 预取星球奖杯图标，返回缓存文件名。"""
    if not planet_en:
        return None
    # 奖杯纹理命名：<Planet>TrophyBronze.png（如 ErisTrophyBronze.png / KuvaFortressTrophyBronze.png）
    q = _norm(planet_en) + "trophybronze"
    un = _tex_index().get(q)
    if not un:
        return None
    return await _download(un, timeout)


async def ensure_alerts_icons(alerts: list[dict], timeout: float = 10.0) -> None:
    """就地给每条警报补 ``_reward_icon`` / ``_planet_icon``，并设全局 ``_credits_icon``。

    在 ``render`` 之前调用：保证 ``build`` 跑的时候图标已落盘，直接同步命中缓存。
    """
    # 确保 manifest 就位（缺失则联网拉一次，失败也无妨 → 回落几何）
    await asyncio.to_thread(_wi._de_manifest, True)
    credit_file = await ensure_credits_icon(timeout)
    for a in alerts:
        a["_credits_icon"] = credit_file
        rn = a.get("reward_en", "")
        a["_reward_icon"] = await ensure_item_icon(rn, timeout) if rn else None
        pn = a.get("planet_en", "")
        a["_planet_icon"] = await ensure_planet_icon(pn, timeout) if pn else None


async def ensure_fissure_icons(groups: list[dict], timeout: float = 10.0) -> None:
    """就地给裂隙卡每行补 ``_planet_icon``（同一星球只拉一次）。

    ``groups`` 是 ``warframe.fissures_data()`` 的结构：``[{label, rows:[{planet_en…}]}]``。
    与警报卡一致：``build`` 之前预取落盘，渲染线程只做同步缓存命中。
    """
    await asyncio.to_thread(_wi._de_manifest, True)
    done: dict[str, str | None] = {}
    for g in groups or []:
        for r in g.get("rows") or []:
            pn = r.get("planet_en") or ""
            if not pn:
                r["_planet_icon"] = None
                continue
            if pn not in done:
                done[pn] = await ensure_planet_icon(pn, timeout)
            r["_planet_icon"] = done[pn]
