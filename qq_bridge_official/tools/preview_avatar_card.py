# -*- coding: utf-8 -*-
"""斗破「动图 + 人物卡」接入预览：模拟 QQ 实际发出的两条消息。

输出 D:/AI/manju_agent/data/pic_preview/_接入预览_动图加卡片.png
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

ROOT = Path("D:/AI/manju_agent/qq_bridge")
DOUPO = ROOT / "src" / "plugins" / "doupo"

from PIL import Image, ImageDraw  # noqa: E402


def _load(sub: str):
    """把 doupo 目录挂成一个「空壳包」再按子模块导入。

    为什么不直接 import doupo：插件 __init__.py 会注册 matcher 并调用
    get_driver()，离线预览脚本里 nonebot 未初始化会直接抛错。
    空壳包只提供 __path__，不执行 __init__.py，因此零副作用。
    """
    import importlib
    import types
    pkg = types.ModuleType("doupo_preview_pkg")
    pkg.__path__ = [str(DOUPO)]
    sys.modules["doupo_preview_pkg"] = pkg
    return importlib.import_module("doupo_preview_pkg." + sub)


core = _load("core")
card = _load("card")
avatar = _load("avatar")

OUT = Path("D:/AI/manju_agent/data/pic_preview/_接入预览_动图加卡片.png")

LEVELS = [(1, "一星斗者"), (10, "一星斗灵"), (19, "一星斗皇"), (28, "九星斗圣巅峰")]
PAD = 24
GAP = 20


def fake_player(qq: str, name: str, lv: int) -> dict:
    p = core.create_player(qq, name, "preview")
    p["lv"] = lv
    p["exp"] = 0
    p["douqi"] = 888888
    p["gold"] = 123456
    return p


async def main():
    rows = []
    for lv, label in LEVELS:
        p = fake_player("1000%d" % lv, "萧炎", lv)
        gif = avatar.avatar_for(p)
        png = await card.render_panel(p)
        g = Image.open(io.BytesIO(gif)).convert("RGBA")
        c = Image.open(io.BytesIO(png)).convert("RGBA")
        # 动图按**原生尺寸**展示 —— QQ 实际就是按原生尺寸渲染 GIF，
        # 之前为了排版对齐把它放大到卡片同高，会把低分辨率精灵糊成像素块，
        # 那是预览方法的失真，不是资源本身的问题。
        rows.append((lv, label, g, c, len(gif), len(png), gif))
        print(f"lv{lv:>3} {avatar.pick_avatar(lv):9s} 动图{g.size} {len(gif)/1024:5.1f}KB "
              f"+ 卡片{c.size} {len(png)/1024:5.1f}KB = {(len(gif)+len(png))/1024:5.1f}KB")

    W = PAD * 2 + max(r[2].width + GAP + r[3].width for r in rows)
    H = PAD * 2 + sum(max(r[2].height, r[3].height) + GAP for r in rows)
    cv = Image.new("RGB", (W, H), (44, 46, 56))
    dr = ImageDraw.Draw(cv)
    y = PAD
    for lv, label, g, c, nb, nc, _raw in rows:
        hh = max(g.height, c.height)
        dr.text((PAD, y), f"lv{lv} {label}", fill=(235, 235, 245))
        # 动图原生尺寸、垂直居中；卡片原生尺寸
        cv.paste(g, (PAD, y + 22 + (hh - g.height) // 2), g)
        cv.paste(c, (PAD + g.width + GAP, y + 22), c)
        y += hh + 22 + GAP
    cv.save(OUT)
    print("->", OUT, os.path.getsize(OUT), "B", cv.size)

    # 单条动图单独存一份，便于逐张视觉核对
    for lv, label, g, c, nb, nc, raw in rows:
        Path("D:/AI/manju_agent/data/pic_preview/_动图_lv%d_%s.gif" % (lv, avatar.pick_avatar(lv).replace('.gif', ''))).write_bytes(raw)


if __name__ == "__main__":
    asyncio.run(main())
