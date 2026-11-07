# -*- coding: utf-8 -*-
"""真实图标贴图（警报卡 / 裂隙卡共用）：保比缩放 + 透明底居中 + 真 alpha 合成。

只做一件事：把一个已经预取到 ``data/warframe_icons/`` 的 PNG 贴到画布某个点上。
**取不到就返回 False**，由调用方决定画什么几何占位——绝不自己编一张图。
"""
from __future__ import annotations

from PIL import Image

from . import weapon_icons


def paste_icon(c, fname: str | None, cx: float, cy: float, size: int,
               crop=None) -> bool:
    """把真实图标贴到 (cx,cy) 居中。取不到（未缓存/损坏）返回 False。

    ``crop`` 给定时先按相对比例裁切（星球奖杯去底座），再保比缩放到 size×size 透明底，
    避免拉伸变形。
    """
    if not fname:
        return False
    p = weapon_icons.icon_cache_path(fname)
    if not p:
        return False
    try:
        img = Image.open(p).convert("RGBA")
        if crop:
            W, H = img.size
            img = img.crop((int(W * crop[0]), int(H * crop[1]),
                            int(W * crop[2]), int(H * crop[3])))
        img.thumbnail((size, size), Image.LANCZOS)      # 保比缩放，不拉伸
        sq = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        sq.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
        img = sq
    except Exception:  # noqa: BLE001 — 残图/解码失败 → 回落几何
        return False
    x = int(round(cx - size / 2.0))
    y = int(round(cy - size / 2.0))
    c.paste_alpha(img, (x, y))
    return True
