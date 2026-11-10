# -*- coding: utf-8 -*-
"""签到卡「黄金铸造」图标层（2026-11-10）。

七枚**真实黄金浮雕 PNG**（随代码交付，来源/许可见 ``assets/checkin_gold/README.md``）：
Font Awesome Free 6.5.0 Solid SVG -> 宿主离线镀金（金色渐变+顶高光+深铜描边+
投影）-> 128x128 RGBA。运行时只读 PNG，等比 contain 不裁切、不拉伸（按真实 AR）。

遵循 AGENTS.md §18/§67「禁止几何方块占位交差」：
  * ``draw()`` 返回真实贴上时的包围盒 ``(x0,y0,x1,y1)``，供模板登记供门禁量化；
  * 真实 PNG 缺失时**不静默留空、不把几何兜底当交付**：``log.warning`` 显式告警+
    ``missing()`` 登记，``gate(strict=True)`` 直接 ``SystemExit``，由门禁/测试拦住；
  * 几何兜底仅异常路径，让人一眼看出「这里本该有金图标但图没了」。
  * 不 import icons / nonebot / 上级包，可独立单测。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

from ..canvas import Canvas, rgb

log = logging.getLogger("qq_bridge.pic.checkin_gold_icons")

#: 模板里的 icon 名 -> 七枚规范资产名之一。diamond（旧喵币几何名）-> coins。
KIND_TO_ASSET = {
    "clock": "clock",
    "bolt": "bolt",
    "check": "check",
    "diamond": "coins",             # 当前喵币：旧 diamond 几何 -> 黄金金币
    "coins": "coins",
    "arrow_up": "arrow-up",         # 距里程碑
    "arrow-up": "arrow-up",
    "star": "star",                 # 群内名次
    "wand": "wand-magic-sparkles",  # 奖励胶囊
    "wand-magic-sparkles": "wand-magic-sparkles",
}
#: 交付闸门要求到位的七枚真实资产（规范名）
KINDS = ("clock", "bolt", "check", "coins", "arrow-up", "star", "wand-magic-sparkles")
_ASSETS = Path(__file__).resolve().parent / "assets" / "checkin_gold"

_FALLBACK_COLOR = {
    "clock": "#B07A18", "bolt": "#C8891C", "check": "#9A6A12",
    "coins": "#C08A20", "arrow-up": "#A87A18", "star": "#D09A24",
    "wand-magic-sparkles": "#B88020",
}
_warned: set[str] = set()
_available: dict[str, Image.Image | None] = {}


def asset_name(kind: str) -> str | None:
    return KIND_TO_ASSET.get((kind or "").strip().lower())


def _read(name: str) -> Image.Image | None:
    """按规范名读真实 PNG，裁透明边、统一 RGBA。缺失/损坏返回 None。"""
    p = _ASSETS / (name + ".png")
    if not p.is_file():
        return None
    try:
        im = Image.open(p).convert("RGBA")
        box = im.getbbox()
        if box:
            im = im.crop(box)
        return im
    except Exception as e:  # noqa: BLE001 — 显式告警，不静默吞
        log.warning("checkin_gold_icon_load_failed: asset=%s err=%s", name, e)
        return None


def available(kind: str) -> bool:
    """真实 PNG 是否在位。加载失败不写缓存，避免测试打桩污染进程内状态。"""
    name = asset_name(kind)
    if not name:
        return False
    if _available.get(name) is not None:
        return True
    im = _read(name)
    if im is None:
        return False
    _available[name] = im
    return True


def missing() -> list[str]:
    """每次重探磁盘：返回缺失/损坏的规范资产名列表（空=全到位）。交付闸门用。"""
    _available.clear()
    return [k for k in KINDS if _read(k) is None]


def gate(strict: bool = True) -> list[str]:
    """交付闸门：缺图时 strict 直接 SystemExit。返回缺失列表。"""
    miss = missing()
    if miss and strict:
        raise SystemExit(
            "签到卡黄金图标缺失真实 PNG：%s；请运行 "
            "python tools/build_checkin_assets.py 生成到 "
            "pic/templates/assets/checkin_gold/" % ", ".join(miss))
    return miss


def _fallback_draw(canvas: Canvas, kind: str, cx: float, cy: float, size: int) -> None:
    """异常兜底几何（仅真实 PNG 缺失时）：单色轮廓，不是交付物。"""
    d = ImageDraw.Draw(canvas.img)
    n = max(8, int(size))
    m = max(2, n // 12)
    col = rgb(_FALLBACK_COLOR.get(asset_name(kind) or "", "#888888"))
    cx, cy = int(cx), int(cy)
    name = asset_name(kind)
    if name == "clock":
        d.ellipse([cx - n / 2, cy - n / 2, cx + n / 2, cy + n / 2], outline=col, width=m)
        d.line([(cx, cy - n / 4), (cx, cy), (cx + n / 5, cy + n / 6)], fill=col, width=m)
    elif name == "check":
        d.line([(cx - n / 3, cy), (cx - n / 10, cy + n / 4), (cx + n / 3, cy - n / 3)],
               fill=col, width=m)
    elif name == "bolt":
        d.polygon([(cx + n / 6, cy - n / 2), (cx - n / 4, cy + n / 12),
                   (cx, cy + n / 12), (cx - n / 6, cy + n / 2),
                   (cx + n / 4, cy - n / 12), (cx, cy - n / 12)], outline=col)
    else:  # coins / star / arrow-up / wand 统一菱形提示「图丢了」
        d.polygon([(cx, cy - n / 2), (cx + n / 2, cy), (cx, cy + n / 2), (cx - n / 2, cy)],
                  outline=col, width=m)


def draw(canvas: Canvas, kind: str, cx: float, cy: float, size: int) -> tuple[int, int, int, int]:
    """画黄金图标（真实 PNG 优先、等比 contain 不裁切），返回真实包围盒。

    :return: 真实贴上的包围盒 ``(x0,y0,x1,y1)``，供模板登记 boxes。
             异常兜底同样返回盒（但 gate/测试会拦下这种状态）。
    """
    name = asset_name(kind)
    im = _read(name) if available(kind) else None
    if im is not None:
        w, h = im.size
        s = min(float(size) / max(1, w), float(size) / max(1, h))
        nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
        layer = im.resize((nw, nh), Image.LANCZOS)
        x0, y0 = int(round(cx - nw / 2)), int(round(cy - nh / 2))
        # 不传 mask：layer 自带 straight alpha，paste_alpha 走 alpha_composite 直接保真。
        # 曾误传 layer 自身当 mask —— paste_alpha 对 RGBA mask 会 convert("L") 取**亮度**，
        # 使 alpha 被亮度二次相乘：深色写实金币 (110,86,41) 亮度≈0.34 -> alpha 削到 34%，
        # 浅橙胶囊底透出 66% -> 冲淡成发白青斑球面（2026-11-10 实卡肉眼+avgRGB 证据）。
        # 6 枚 FA 镀金图标因本身亮(L 高)削得少而一直没暴露，写实币才现形。禁止加回 mask。
        canvas.paste_alpha(layer, (x0, y0))
        return (x0, y0, x0 + nw, y0 + nh)
    key = name or (kind or "?")
    if key not in _warned:
        _warned.add(key)
        log.warning("checkin_gold_icon_missing: kind=%s path=%s"
                    "（几何兜底，禁止当作交付；gate/测试会拦）", kind,
                    _ASSETS / (str(name or kind) + ".png"))
    _fallback_draw(canvas, kind, cx, cy, size)
    return (int(cx - size / 2), int(cy - size / 2),
            int(cx + size / 2), int(cy + size / 2))
