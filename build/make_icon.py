# -*- coding: utf-8 -*-
"""生成 ManjuAgent 安装包用的 Windows 图标 (.ico，含多尺寸) 与托盘 PNG。

纯 PIL 绘制：圆角底色 + 场记板(Clapperboard) + 播放三角，避免依赖外部字体/图片。
用法：python build/make_icon.py  ->  产出 build/manju_icon.ico, build/manju_tray.png
"""
import os
import math
from PIL import Image, ImageDraw


def _round_rect(draw, box, radius, fill):
    x0, y0, x1, y1 = box
    draw.polygon(
        [(x0 + radius, y0), (x1 - radius, y0), (x1, y0 + radius),
         (x1, y1 - radius), (x1 - radius, y1), (x0 + radius, y1),
         (x0, y1 - radius), (x0, y0 + radius)],
        fill=fill)


def _make(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    # 底色（紫红渐变近似：上下两色块）
    _round_rect(d, (pad, pad, size - pad, size - pad), size // 6, (124, 58, 237, 255))  # 紫
    _round_rect(d, (pad, pad, size - pad, int(size * 0.62)), size // 6, (217, 70, 239, 255))  # 品红上块

    # 场记板斜纹（上块区域）
    n = max(3, size // 24)
    for i in range(-n, n * 2):
        x = pad + i * (size // n)
        d.line([(x, pad + 1), (x + size // n, int(size * 0.34))], fill=(255, 255, 255, 255), width=max(1, size // 48))

    # 播放三角（居中偏下）
    cx, cy = size / 2, size * 0.66
    r = size * 0.20
    tri = [(cx - r * 0.7, cy - r), (cx - r * 0.7, cy + r), (cx + r, cy)]
    d.polygon(tri, fill=(255, 255, 255, 255))
    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [_make(s) for s in sizes]
    ico_path = os.path.join(here, "manju_icon.ico")
    frames[0].save(ico_path, sizes=[(s, s) for s in sizes])
    png_path = os.path.join(here, "manju_tray.png")
    _make(64).save(png_path)
    print("wrote", ico_path, png_path)


if __name__ == "__main__":
    main()
