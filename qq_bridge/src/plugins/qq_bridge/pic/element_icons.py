# -*- coding: utf-8 -*-
"""信条 / 终幕武器卡的真实元素伤害类型图标（Warframe Wiki 官方资产）。

2026-11-06 用户要求「严格按照我发的图片，做信条口令」。参考卡里元素符号是
Wiki 的彩色 64×64 伤害类型图标（银灰 C 形磁铁 / 紫闪电 / 绿骷髅 / 橙火焰），
**不是** DE PublicExport 里那套带菱形外框的 ``KillEnemiesDamageType*.png``
赛季挑战图标，也不是「元素色圆点」几何占位。

资产来源：``https://static.wikia.nocookie.net/warframe/images/<md5[0]>/<md5[:2]>/<文件名>``，
文件名取自 ``warframe.fandom.com/wiki/Module:DamageTypes/data`` 的 ``Icon`` 字段。
注意官方路径里 Heat 用 ``DmgHeatSmall64.png``（模块文本里写的 ``DmgFireSmall64.png``
实际 404），Toxin 用 ``DmgToxinSmall64.png``。

wikia 静态域名会把项目通用的 ``Chrome/126`` UA 判 403；本模块固定用实测可用的
``Chrome/120`` UA，并按 ``Accept`` 协商回来的 webp 统一转 PNG 落盘。

**绝不降级**：缺图 / 下载失败 / 未知元素一律 ``raise``，不画圆点、不画几何图形、
不静默跳过。渲染线程只做同步缓存命中，绝不外呼。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
import urllib.request

from . import weapon_icons as _wi

#: wikia 静态资源固定 UA（Chrome/126 会被 403，实测 120 可用）
_WIKI_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_WIKI_BASE = "https://static.wikia.nocookie.net/warframe/images"

#: 标准元素键 → Wiki 真实伤害类型图标文件名。
#: 覆盖 Kuva / Tenet / Coda 全部 progenitor 元素 + Tau，避免下次轮换再返工。
ELEMENT_FILES: dict[str, str] = {
    "impact":     "DmgImpactSmall64.png",
    "puncture":   "DmgPunctureSmall64.png",
    "slash":      "DmgSlashSmall64.png",
    "heat":       "DmgHeatSmall64.png",
    "cold":       "DmgColdSmall64.png",
    "electricity": "DmgElectricitySmall64.png",
    "toxin":      "DmgToxinSmall64.png",
    "blast":      "DmgBlastSmall64.png",
    "corrosive":  "DmgCorrosiveSmall64.png",
    "gas":        "DmgGasSmall64.png",
    "magnetic":   "DmgMagneticSmall64.png",
    "radiation":  "DmgRadiationSmall64.png",
    "viral":      "DmgViralSmall64.png",
    "tau":        "DmgTauSmall64.png",
}

#: 数据源 / 内部名 / 国服中文名 → 标准键。未知键直接 KeyError，不猜。
ELEMENT_ALIASES: dict[str, str] = {
    "fire": "heat", "flame": "heat", "heat": "heat", "火焰": "heat",
    "freeze": "cold", "cold": "cold", "冰冷": "cold", "冰": "cold",
    "electric": "electricity", "electricity": "electricity",
    "电击": "electricity", "电": "electricity",
    "poison": "toxin", "toxin": "toxin", "毒素": "toxin", "毒": "toxin",
    "explosion": "blast", "blast": "blast", "爆炸": "blast",
    "magnetic": "magnetic", "磁力": "magnetic", "磁": "magnetic",
    "impact": "impact", "冲击": "impact",
    "puncture": "puncture", "穿刺": "puncture",
    "slash": "slash", "切割": "slash",
    "corrosive": "corrosive", "腐蚀": "corrosive",
    "gas": "gas", "毒气": "gas",
    "radiation": "radiation", "辐射": "radiation",
    "viral": "viral", "病毒": "viral",
    "tau": "tau",
}

#: 标准键 → 国服中文名（wf.wiki 的 ``element`` 字段用的就是这些词）。
#: 全元素压力测试按它生成最长标签，避免下次轮换出现新元素才返工。
ELEMENT_ZH: dict[str, str] = {
    "impact": "冲击", "puncture": "穿刺", "slash": "切割",
    "heat": "火焰", "cold": "冰冷", "electricity": "电击",
    "toxin": "毒素", "blast": "爆炸", "corrosive": "腐蚀",
    "gas": "毒气", "magnetic": "磁力", "radiation": "辐射",
    "viral": "病毒", "tau": "真伤",
}

#: 元素名文字配色。4 项为参考卡逐像素实测（磁力/电击/毒素/火焰）；
#: 其余按同一张 Wiki 真实图标的墨色主值实测填写（不改色相，仅统一明度），
#: 不凭空造色。参考卡实测值见 ``tmp/tenet_measure3.py``。
ELEM_NAME_COLOR: dict[str, str] = {
    "magnetic":    "#657B82",   # 参考卡实测 (101,123,130)
    "electricity": "#8B62A1",   # 参考卡实测 (139,98,161)
    "toxin":       "#7C8D46",   # 参考卡实测 (124,141,70)
    "heat":        "#D1701E",   # 参考卡实测 (209,112,30)
    "impact":      "#6B7E80",   # 图标 (126,138,138)
    "puncture":    "#6A6258",   # 图标 (102,96,88)
    "slash":       "#776264",   # 图标 (116,97,99)
    "cold":        "#5773A3",   # 图标 (85,114,163)
    "blast":       "#704247",   # 图标 (108,63,66)
    "corrosive":   "#747D19",   # 图标 (112,124,17)
    "gas":         "#3B8269",   # 图标 (53,128,105)
    "radiation":   "#AD9210",   # 图标 (173,146,14)
    "viral":       "#AA6E91",   # 图标 (170,110,145)
    "tau":         "#822023",   # 图标 (130,28,31)
}

#: 加成值三档配色：参考卡 7 个样本实测（+49% 绿 / +33.1%、+38.3% 蓝 /
#: +25.6%、+25.7%、+26.4%、+27.7% 灰）。按数值分档，阈值取自样本间隔。
#: 这是**从参考卡反推的视觉规则**，不是游戏数据；分界点未拿到 Rabbit 源码确认，
#: 已在门禁与交差说明里标注为「未验证到的」。
BONUS_TIERS: tuple[tuple[float, str], ...] = (
    (45.0, "#328B51"),   # (50,139,81)
    (30.0, "#215694"),   # (33,86,148)
)
BONUS_LOW = "#51676B"    # (81,103,107)


def canonical(key_or_name: str | None) -> str:
    """元素键 / 中文名 → 标准键；未知直接 KeyError（禁止兜底）。"""
    q = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (key_or_name or "").lower())
    if not q:
        raise KeyError("元素键为空：信条/终幕卡必须给出 elementKey 或 element")
    k = ELEMENT_ALIASES.get(q) or (q if q in ELEMENT_FILES else None)
    if k is None:
        raise KeyError("未知元素 %r：请补进 element_icons.ELEMENT_ALIASES / ELEMENT_FILES" % key_or_name)
    return k


def icon_file(key_or_name: str | None) -> str:
    """元素 → Wiki 图标缓存文件名。"""
    return ELEMENT_FILES[canonical(key_or_name)]


def wikia_url(fname: str) -> str:
    """wikia 静态路径 = /<md5(fileName)[0]>/<md5(fileName)[:2]>/<fileName>。"""
    h = hashlib.md5(fname.encode("utf-8")).hexdigest()
    return "%s/%s/%s/%s" % (_WIKI_BASE, h[0], h[:2], fname)


def icon_path(key_or_name: str | None):
    """同步缓存命中 → Path；缺图直接 raise（渲染线程绝不外呼、绝不降级）。"""
    fname = icon_file(key_or_name)
    p = _wi.icon_cache_path(fname)
    if p is None:
        raise FileNotFoundError(
            "信条/终幕卡真实元素图标缺失：%s（%s）。"
            "请先 await ensure_element_icons(weapons)；禁止用圆点/几何图形兜底。"
            % (fname, wikia_url(fname)))
    return p


def _download(fname: str, timeout: float) -> bytes:
    req = urllib.request.Request(
        wikia_url(fname),
        headers={"User-Agent": _WIKI_UA, "Accept": "image/*,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if getattr(r, "status", 200) != 200:
            raise OSError("wikia 返回 HTTP %s" % r.status)
        data = r.read()
    if not data or len(data) < 600:
        raise OSError("wikia 返回体过小 (%d bytes)" % (len(data) if data else 0))
    return data


def _write_png(fname: str, data: bytes) -> None:
    """wikia 可能按 Accept 回 webp，统一转 PNG 落盘。"""
    from PIL import Image  # 局部导入，避免 pic 包加载时额外拉起 PIL

    with Image.open(io.BytesIO(data)) as im:
        rgba = im.convert("RGBA")
        if rgba.size != (64, 64):
            raise ValueError("%s 尺寸 %s != 64×64：不是 Wiki 伤害类型图标" % (fname, rgba.size))
        _wi._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        rgba.save(_wi._CACHE_DIR / fname, "PNG")


def elem_color(key_or_name: str | None) -> str:
    """元素名文字颜色。"""
    return ELEM_NAME_COLOR[canonical(key_or_name)]


def bonus_color(bonus) -> str:
    """参考卡实测的加成值档位配色；无加成值直接 raise。"""
    if bonus is None or bonus == "":
        raise ValueError("加成值为空：信条/终幕卡不允许画空加成")
    v = float(bonus)
    for threshold, color in BONUS_TIERS:
        if v >= threshold:
            return color
    return BONUS_LOW


async def ensure_element_icons(weapons: list[dict], timeout: float = 12.0) -> None:
    """出图前预取本批武器用到的全部真实元素图标，缺一张就整体报错。

    与警报 / 裂隙卡一致的职责边界：业务层 ``await`` 预取落盘，
    ``build`` 里只做同步缓存命中。
    """
    keys: list[str] = []
    for w in weapons or []:
        k = canonical(w.get("element_key") or w.get("element") or "")
        if k not in keys:
            keys.append(k)

    missing: list[str] = []
    for k in keys:
        fname = icon_file(k)
        if _wi.icon_cache_path(fname) is not None:
            continue
        try:
            data = await asyncio.to_thread(_download, fname, timeout)
            await asyncio.to_thread(_write_png, fname, data)
        except Exception as e:  # noqa: BLE001 — 上抛，不吞
            missing.append("%s (%s: %s)" % (fname, type(e).__name__, e))
    if missing:
        raise FileNotFoundError("信条/终幕卡真实元素图标下载失败：\n  " + "\n  ".join(missing))

    # 落盘后逐张复核，杜绝「下载到了但文件不像样」的假成功
    for k in keys:
        icon_path(k)
