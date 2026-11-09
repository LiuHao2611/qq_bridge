# -*- coding: utf-8 -*-
"""Warframe 市价卡货币图标层（2026-11-09）。

三枚**游戏内真实 PNG**（随代码交付，来源见 ``assets/wf_currency/README.md``）：
  * ``platinum`` 白金  —— Wiki ``Platinum64.png``（银环＋蓝宝石）
  * ``credits``  现金  —— Wiki ``Credits64.png``（Corpus 信用点卡）
  * ``ducats``   杜卡德 —— Wiki ``OrokinDucats.png``（金色 Orokin 纹章）

设计要点（遵循 AGENTS.md §18「禁止几何方块占位交差」）：
  * ``draw()`` 优先加载真实 PNG，等比 ``contain`` 不裁切（与物品图标同款处理）；
  * 真实 PNG 缺失时**不静默留空**：回落到内置几何兜底（单色轮廓），同时 ``log.warning``
    显式告警并登记 ``missing()``，由 ``gate(strict=True)`` 与测试拦住 —— 缺图一律不算交付；
  * 几何兜底是异常路径，正常生产环境三枚 PNG 都在，绝不触发；
  * 不 import ``icons`` / nonebot / 上级包，可独立单测。
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw

from .canvas import Canvas, rgb

log = logging.getLogger("qq_bridge.pic.currency_icons")

KINDS = ("platinum", "credits", "ducats")

_ASSETS = Path(__file__).resolve().parent / "templates" / "assets" / "wf_currency"

_FALLBACK_COLOR = {
    "platinum": "#8aa0c8",
    "credits": "#7f8aa0",
    "ducats": "#b08a3a",
}

_warned: set[str] = set()


def _load(kind: str) -> Image.Image | None:
    """读真实 PNG，裁掉透明边、统一 RGBA。缺失或损坏返回 ``None``。"""
    if kind not in KINDS:
        return None
    p = _ASSETS / f"{kind}.png"
    if not p.is_file():
        return None
    try:
        im = Image.open(p).convert("RGBA")
        box = im.getbbox()
        if box:
            im = im.crop(box)
        return im
    except Exception as e:  # noqa: BLE001 — 显式降级，不静默
        log.warning("wf_currency_icon_load_failed: kind=%s err=%s", kind, e)
        return None


_available: dict[str, Image.Image | None] = {}


def available(kind: str) -> bool:
    """真实 PNG 是否在位。

    加载失败 / 缺图**不写入缓存**：一次性加载失败（如测试临时把 ``_load`` 打补丁成
    返回 ``None``）不能永久污染进程内缓存，否则事后恢复真实 ``_load`` 仍会误报缺失。
    只有成功解码的图才进 ``_available``。
    """
    if kind not in KINDS:
        return False
    if kind in _available and _available[kind] is not None:
        return True
    im = _load(kind)
    if im is None:
        return False
    _available[kind] = im
    return True


def missing() -> list[str]:
    """返回缺失真实 PNG 的 kind 列表（空 = 全部到位）。

    每次重新探测磁盘：本函数是**交付闸门**（CI / 测试用），必须反映当前磁盘真实状态，
    不能被单次 render 期间 ``available`` 的进程内缓存（``_available``）掩盖。
    """
    _available.clear()
    return [k for k in KINDS if not available(k)]


def gate(strict: bool = True) -> list[str]:
    """交付闸门：缺图时（strict）直接 ``SystemExit``。返回缺失列表。"""
    miss = missing()
    if miss and strict:
        raise SystemExit(
            "市价卡货币图标缺失真实 PNG：%s；请运行 "
            "python tools/fetch_wf_currency_icons.py 或手动放入 "
            "pic/templates/assets/wf_currency/<kind>.png" % ", ".join(miss))
    return miss


def _fallback_draw(canvas: "Canvas", kind: str, cx: float, cy: float, size: int) -> None:
    """异常兜底几何（仅在真实 PNG 缺失时调用）：单色轮廓，深/浅底都可见。

    不是真实资产，只是让人一眼看出「这里本该有图标但图没了」。
    """
    d = ImageDraw.Draw(canvas.img)
    n = max(8, int(size))
    m = max(2, n // 12)
    col = rgb(_FALLBACK_COLOR.get(kind, "#888888"))
    cx, cy = int(cx), int(cy)
    if kind == "platinum":
        pts = [(n * 0.50, n * 0.08), (n * 0.82, n * 0.50),
               (n * 0.50, n * 0.94), (n * 0.18, n * 0.50)]
        d.polygon(pts, outline=col, width=m)
    elif kind == "credits":
        r = n * 0.42
        c0 = n / 2
        pts = [(c0 + r * math.cos(math.radians(30 + i * 60)),
                c0 + r * math.sin(math.radians(30 + i * 60))) for i in range(6)]
        d.polygon(pts, outline=col, width=m)
    else:  # ducats
        d.rounded_rectangle([m, m, n - m, n - m], radius=n * 0.20,
                            outline=col, width=m)


def draw(canvas: "Canvas", kind: str, cx: float, cy: float, size: int) -> bool:
    """画货币图标：真实 PNG 优先（等比 contain 不裁切）；缺失则几何兜底 + 告警。

    :return: ``True`` = 用了真实 PNG；``False`` = 走了兜底（异常，已显式告警）。
    """
    im = _load(kind) if available(kind) else None
    if im is not None:
        w, h = im.size
        s = min(float(size) / max(1, w), float(size) / max(1, h))
        nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
        layer = im.resize((nw, nh), Image.LANCZOS)
        canvas.paste(layer, (int(cx - nw / 2), int(cy - nh / 2)), layer)
        return True
    if kind not in _warned:
        _warned.add(kind)
        log.warning("wf_currency_icon_missing: kind=%s path=%s（几何兜底，禁止当作交付）",
                    kind, _ASSETS / (kind + ".png"))
    _fallback_draw(canvas, kind, cx, cy, size)
    return False
