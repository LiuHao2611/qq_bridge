# -*- coding: utf-8 -*-
"""从 Rabbit「本周执刑官猎杀」参考图提取静态素材（一次性离线生成，产物入库）。

「全量对齐、禁止遗漏」的最强保证：除**动态信息**外，背景兔子线稿、右下角
Q 版吉祥物、半月徽标、标题、分割线、卡片框与左侧紫条、BOSS 头像、蓝色源力石
菱形、绿色任务胶囊、页脚线、PC 徽章、页脚文字——全部保留参考图的**原始像素**，
运行时只把三块动态区域擦净后重绘。

产物（src/plugins/qq_bridge/pic/assets/）：
  archon_base.png                 800x562 擦净动态区后的底图
  archon_ic_<mission>.png        三种任务类型线稿图标（透明底）
  archon_ic_planet.png           节点胶囊里的地球小圆图（透明底）

用法：python tools/build_archon_assets.py <参考图路径>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "assets"

PAGE = (253, 252, 253)   # 实测页面底色 #FDFCFD
CARD = (249, 243, 246)   # 实测卡片填充 #F9F3F6

#: 动态区域：(x0,y0,x1,y1,填充色) —— 运行时由模板重绘，底图上先擦净
PATCHES = [
    (136, 128, 560, 278, CARD),    # 卡片内四行文字 + 奖励下划线（避开描边/头像/菱形）
    (145, 298, 592, 432, PAGE),    # 三行任务（避开左侧绿色任务胶囊 x34-139）
    (158, 442, 404, 486, PAGE),    # 进度条（避开「- 剩余时间：」x35-131）
]

#: 图标裁片（原图坐标）：任务类型线稿 + 节点胶囊里的地球小图
ICON_CROPS = {
    "archon_ic_mobile_defense": (148, 300, 180, 340),
    "archon_ic_interception": (148, 345, 180, 385),
    "archon_ic_assassination": (148, 390, 180, 430),
    "archon_ic_planet": (409, 305, 435, 332),  # 只裁地球本体，避开胶囊蓝色描边
}


def cutout(im: Image.Image, box: tuple, bg=(253, 252, 253), tol: int = 34) -> Image.Image:
    """把接近背景色的像素抠成透明（保留蓝色线稿 / 深色球体）。"""
    tile = im.crop(box).convert("RGBA")
    px = tile.load()
    for y in range(tile.height):
        for x in range(tile.width):
            r, g, b, _ = px[x, y]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) <= tol:
                px[x, y] = (r, g, b, 0)
    return tile


def main(src: str) -> int:
    im = Image.open(src).convert("RGB")
    if im.size != (800, 562):
        print("参考图尺寸不是 800x562：", im.size)
        return 1
    base = im.copy()
    for x0, y0, x1, y1, color in PATCHES:
        base.paste(color, (x0, y0, x1, y1))
    OUT.mkdir(parents=True, exist_ok=True)
    bp = OUT / "archon_base.png"
    base.save(bp, optimize=True)
    print("base ->", bp, os.path.getsize(bp), "B")
    for name, box in ICON_CROPS.items():
        tile = cutout(im, box)
        p = OUT / (name + ".png")
        tile.save(p, optimize=True)
        print(f"icon {name:28s} {tile.size} -> {os.path.getsize(p)} B")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
