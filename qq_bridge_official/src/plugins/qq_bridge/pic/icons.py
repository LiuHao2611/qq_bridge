# -*- coding: utf-8 -*-
"""pic/icons.py —— 图标库（**纯几何自绘，不依赖字体字形**）。

为什么不直接用 emoji / Unicode 符号：
  * Pillow **不渲染彩色 emoji**（需 pilmoji + 额外字体，为几个图标引依赖不划算）；
  * Unicode 几何符号（⤢ ⏳ ⚡ 等）在 msyh/simhei 中**可能缺字形 → 豆腐块**，
    跨机器还不一致，风险不可控。
  * 自绘几何图形：100% 可控、跨平台一致、颜色随主题自适应、零依赖。

实现要点：
  * 4× 超采样 + LANCZOS 缩回 → 边缘平滑（直接 polygon 会有明显锯齿）；
  * 每个图标是「L 遮罩 + 纯色图层」，最后用遮罩贴到画布，颜色可随意换；
  * 坐标统一为 n×n 归一化空间，便于按 size 缩放。

样式：
  * ``draw(..., style="solid")``（默认）：实心填充，兼容历史模板观感（**逐字节不变**）；
  * ``draw(..., style="line")``：**SpaceX 式 monoline**——
      统一线宽（_lw，比例 0.09、下限 2px）→ 全族一致；
      圆角连接（Pillow ``line(joint="curve")``）+ 端点圆头（_caps）→ 纯净无肿包；
      几何精准、单色极简（SpaceX 视觉语言：无装饰、无渐变、单色、工程感）。
    仅 bounty 等模板按需启用，其它模板不传 style → 走 solid 分支，零影响。

2026-10-12 深度调研（SpaceX 真实规范）后重做 line 分支：
  * 弃用旧 ``_round_pts``（在多边形「每个顶点」补圆 → 锐角处堆出圆球，观感浑浊）；
  * 改用 ``_closed`` / ``_open``：闭合形状用 ``line(..., joint="curve")`` 让**所有内角**
    都是真正的圆角连接，仅闭合顶点补一个圆头；开放笔画两端补圆头。
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .theme import rgb

#: 可用图标名（集中管理，新增图标请在此登记）
ICONS = (
    "circle", "square", "round_square", "triangle", "diamond",
    "star", "bolt", "clock", "arrow_up", "arrow_right",
    "check", "cross", "question", "image", "wand", "pause",
    "cart", "sword", "gun", "book", "globe",
    # 2026-10-29 新增：精确语义 monoline 字形（替换泛用方框，提升一眼识别）
    "window", "disk", "clipboard", "tag", "scene", "list", "monitor",
)

_SS = 4  # 超采样倍数


def draw(canvas, kind: str, cx: int, cy: int, size: int, color: str,
         style: str = "solid") -> None:
    """在画布 (cx, cy) 中心处绘制一个 size×size 的图标。未知 kind 退化为 circle。"""
    size = max(4, int(size))
    fn = _SHAPES.get((kind or "").strip().lower(), _circle)
    _stamp(canvas, cx, cy, size, color, fn, style)


def exists(kind: str) -> bool:
    return (kind or "").strip().lower() in _SHAPES


def layer(kind: str, size: int, color: str, alpha: int = 255, angle: float = 0,
          style: str = "solid") -> Image.Image:
    """生成**带透明度 / 可旋转**的图标 RGBA 图层（平铺背景 / 角落贴纸用）。"""
    size = max(4, int(size))
    fn = _SHAPES.get((kind or "").strip().lower(), _circle)
    big = size * _SS
    m = Image.new("L", (big, big), 0)
    fn(ImageDraw.Draw(m), big, line=(style == "line"))
    m = m.resize((size, size), Image.LANCZOS)
    a = int(max(0, min(255, alpha)))
    m = m.point(lambda v: v * a // 255)
    img = Image.new("RGBA", (size, size), rgb(color) + (255,))
    if angle:
        img = img.rotate(angle, expand=True, resample=Image.BICUBIC)
        m = m.rotate(angle, expand=True, resample=Image.BICUBIC)
    img.putalpha(m)
    return img


# ------------------------------------------------------------------ 内部
def _stamp(canvas, cx, cy, size, color, fn, style: str = "solid") -> None:
    big = size * _SS
    layer = Image.new("L", (big, big), 0)
    fn(ImageDraw.Draw(layer), big, line=(style == "line"))
    mask = layer.resize((size, size), Image.LANCZOS)
    img = Image.new("RGBA", (size, size), rgb(color) + (255,))
    canvas.img.paste(img, (int(cx - size / 2), int(cy - size / 2)), mask)


def _lw(n: int) -> int:
    """monoline 线宽：统一比例 0.09、下限 2px（SpaceX 统一线宽观感）。

    n 为 4× 超采样空间尺寸，返回同样在超采样空间的线宽，
    缩回后实际线宽 ≈ 2px(@22) / 3px(@32) —— 全族一致、纤细克制。
    """
    return max(2, round(n * 0.09))


def _caps(d, pts, w) -> None:
    """圆头端点：在路径端点补半圆（SpaceX monoline 圆头观感，仅端点，不碰内角）。"""
    r = (w + 1) // 2
    for (x, y) in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=255)


def _stroke(d, pts, w, closed: bool = False) -> None:
    """SpaceX 式 monoline 描边：圆角连接(joint=curve) + 端点圆头。

    * 闭合形状：``line(pts+[pts[0]], joint="curve")`` 让**所有内角**成为真正圆角连接，
      仅闭合顶点（首点）是线段端点 → 用 _caps 补一个圆头，外角不再堆圆球；
    * 开放笔画：两端补圆头，中段交点已被 joint="curve" 圆角化。
    """
    seq = list(pts)
    if closed:
        seq = seq + [seq[0]]
    d.line(seq, fill=255, width=w, joint="curve")
    if closed:
        _caps(d, (pts[0],), w)
    else:
        _caps(d, (pts[0], pts[-1]), w)


def _circle(d, n, line: bool = False):
    m = n * 0.07
    if line:
        d.ellipse([m, m, n - m, n - m], outline=255, width=_lw(n))
    else:
        d.ellipse([m, m, n - m, n - m], fill=255)


def _square(d, n, line: bool = False):
    m = n * 0.10
    if line:
        # 微圆角（SpaceX 结构 4px 级：克制、不刺眼）
        d.rounded_rectangle([m, m, n - m, n - m], radius=max(1, int(n * 0.06)),
                            outline=255, width=_lw(n))
    else:
        d.rectangle([m, m, n - m, n - m], fill=255)


def _round_square(d, n, line: bool = False):
    m = n * 0.08
    r = n * 0.22
    if line:
        d.rounded_rectangle([m, m, n - m, n - m], radius=r, outline=255, width=_lw(n))
    else:
        d.rounded_rectangle([m, m, n - m, n - m], radius=r, fill=255)


def _triangle(d, n, line: bool = False):
    m = n * 0.12
    pts = [(m, m), (n - m, n / 2), (m, n - m)]
    if line:
        _stroke(d, pts, _lw(n), closed=True)
    else:
        d.polygon(pts, fill=255)


def _diamond(d, n, line: bool = False):
    m = n * 0.08
    pts = [(n / 2, m), (n - m, n / 2), (n / 2, n - m), (m, n / 2)]
    if line:
        _stroke(d, pts, _lw(n), closed=True)
    else:
        d.polygon(pts, fill=255)


def _star(d, n, line: bool = False, points: int = 5, inner: float = 0.40):
    cx = cy = n / 2
    r_out = n * 0.46
    r_in = r_out * inner
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(-90 + i * 180.0 / points)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    if line:
        _stroke(d, pts, _lw(n), closed=True)
    else:
        d.polygon(pts, fill=255)


def _bolt(d, n, line: bool = False):
    pts = [
        (n * 0.56, n * 0.06), (n * 0.24, n * 0.56), (n * 0.46, n * 0.56),
        (n * 0.40, n * 0.94), (n * 0.76, n * 0.42), (n * 0.52, n * 0.42),
        (n * 0.62, n * 0.06),
    ]
    if line:
        _stroke(d, pts, _lw(n), closed=True)
    else:
        d.polygon(pts, fill=255)


def _clock(d, n, line: bool = False):
    m = n * 0.08
    w = _lw(n)
    if line:
        d.ellipse([m, m, n - m, n - m], outline=255, width=w)
    else:
        d.ellipse([m, m, n - m, n - m], fill=255)
        r = n * 0.13
        d.ellipse([m + r, m + r, n - m - r, n - m - r], fill=0)
    hw = _lw(n)
    # 指针：开放笔画，端点圆头（中心交汇处自然圆化）
    _stroke(d, [(n / 2, n / 2), (n / 2, n * 0.30)], hw, closed=False)
    _stroke(d, [(n / 2, n / 2), (n * 0.66, n * 0.60)], hw, closed=False)


def _arrow_up(d, n, line: bool = False):
    m = n * 0.10
    w = n * 0.16
    pts = [(n / 2, m), (n - m, n * 0.46), (n / 2 + w, n * 0.46), (n / 2 + w, n - m),
           (n / 2 - w, n - m), (n / 2 - w, n * 0.46), (m, n * 0.46)]
    if line:
        _stroke(d, pts, _lw(n), closed=True)
    else:
        d.polygon(pts, fill=255)


def _arrow_right(d, n, line: bool = False):
    m = n * 0.10
    w = n * 0.16
    pts = [(n - m, n / 2), (n * 0.54, m), (n * 0.54, n / 2 - w), (m, n / 2 - w),
           (m, n / 2 + w), (n * 0.54, n / 2 + w), (n * 0.54, n - m)]
    if line:
        _stroke(d, pts, _lw(n), closed=True)
    else:
        d.polygon(pts, fill=255)


def _check(d, n, line: bool = False):
    w = _lw(n)
    pts = [(n * 0.18, n * 0.54), (n * 0.42, n * 0.76), (n * 0.84, n * 0.26)]
    if line:
        _stroke(d, pts, w, closed=False)
    else:
        d.line(pts, fill=255, width=w, joint="curve")


def _cross(d, n, line: bool = False):
    m = n * 0.24
    w = _lw(n)
    if line:
        _stroke(d, [(m, m), (n - m, n - m)], w, closed=False)
        _stroke(d, [(n - m, m), (m, n - m)], w, closed=False)
    else:
        d.line([(m, m), (n - m, n - m)], fill=255, width=w)
        d.line([(n - m, m), (m, n - m)], fill=255, width=w)


def _question(d, n, line: bool = False):
    """问号：'?' 是 ASCII，任何字体都有字形，画进遮罩即可（不用几何拼）。"""
    from . import fonts

    f = fonts.load("bold", int(n * 0.9))
    d.text((n / 2, n / 2), "?", font=f, fill=255, anchor="mm")


def _image(d, n, line: bool = False):
    m = n * 0.10
    if line:
        d.rounded_rectangle([m, m, n - m, n - m], radius=max(1, int(n * 0.12)),
                            outline=255, width=_lw(n))
        rr = n * 0.10
        d.ellipse([n * 0.62, n * 0.24, n * 0.62 + rr * 2, n * 0.24 + rr * 2],
                  outline=255, width=_lw(n))
    else:
        d.rounded_rectangle([m, m, n - m, n - m], radius=n * 0.16, fill=255)
        d.polygon([(m, n - m), (n * 0.42, n * 0.52), (n * 0.66, n - m)], fill=0)
        rr = n * 0.10
        d.ellipse([n * 0.62, n * 0.24, n * 0.62 + rr * 2, n * 0.24 + rr * 2], fill=0)


def _wand(d, n, line: bool = False):
    """魔法棒（生图意象）：斜杆 + 顶端星芒。"""
    w = _lw(n)
    _stroke(d, [(n * 0.22, n * 0.82), (n * 0.66, n * 0.34)], w, closed=False)
    cx, cy, r = n * 0.70, n * 0.28, n * 0.16
    for k in range(4):
        a = math.radians(45 + k * 90)
        _stroke(d,
                [(cx - r * math.cos(a), cy - r * math.sin(a)),
                 (cx + r * math.cos(a), cy + r * math.sin(a))],
                w, closed=False)


def _pause(d, n, line: bool = False):
    m = n * 0.18
    w = n * 0.18
    r = n * 0.04
    if line:
        d.rounded_rectangle([m, m, m + w, n - m], radius=r, outline=255, width=_lw(n))
        d.rounded_rectangle([n - m - w, m, n - m, n - m], radius=r, outline=255, width=_lw(n))
    else:
        d.rectangle([m, m, m + w, n - m], fill=255)
        d.rectangle([n - m - w, m, n - m, n - m], fill=255)


def _cart(d, n, line: bool = False):
    """购物车：把手 + 梯形筐 + 两轮。"""
    w = _lw(n)
    if line:
        _stroke(d, [(n * 0.12, n * 0.26), (n * 0.32, n * 0.42)], w, closed=False)
        _stroke(d, [(n * 0.30, n * 0.40), (n * 0.86, n * 0.40),
                    (n * 0.80, n * 0.68), (n * 0.36, n * 0.68)], w, closed=True)
        r = n * 0.08
        for cx in (n * 0.42, n * 0.72):
            d.ellipse([cx - r, n * 0.72, cx + r, n * 0.88], outline=255, width=w)
    else:
        d.line([(n * 0.12, n * 0.26), (n * 0.32, n * 0.42)], fill=255, width=w)
        d.polygon([(n * 0.30, n * 0.40), (n * 0.86, n * 0.40),
                   (n * 0.80, n * 0.68), (n * 0.36, n * 0.68)], fill=255)
        inset = max(1, int(n * 0.045))
        d.polygon([(n * 0.30 + inset, n * 0.40 + inset), (n * 0.86 - inset, n * 0.40 + inset),
                   (n * 0.80 - inset, n * 0.68 - inset), (n * 0.36 + inset, n * 0.68 - inset)], fill=0)
        r = n * 0.08
        for cx in (n * 0.42, n * 0.72):
            d.ellipse([cx - r, n * 0.72, cx + r, n * 0.88], fill=255)
            rr = r * 0.45
            d.ellipse([cx - rr, n * 0.76, cx + rr, n * 0.84], fill=0)


def _sword(d, n, line: bool = False):
    """剑（近战武器意象）：斜向剑身 + 护手 + 剑柄。"""
    pts = [(n * 0.40, n * 0.70), (n * 0.88, n * 0.20), (n * 0.97, n * 0.04),
           (n * 0.80, n * 0.12), (n * 0.32, n * 0.62)]
    w = _lw(n)
    if line:
        _stroke(d, pts, w, closed=True)
        _stroke(d, [(n * 0.28, n * 0.46), (n * 0.52, n * 0.70)], w, closed=False)
        _stroke(d, [(n * 0.42, n * 0.58), (n * 0.20, n * 0.80)], w, closed=False)
    else:
        d.polygon(pts, fill=255)
        d.line([(n * 0.28, n * 0.46), (n * 0.52, n * 0.70)], fill=255, width=w)
        d.line([(n * 0.42, n * 0.58), (n * 0.20, n * 0.80)], fill=255, width=w)


def _gun(d, n, line: bool = False):
    """枪械（主/副武器意象）：枪身 + 枪管 + 准星 + 握把。"""
    w = _lw(n)
    if line:
        d.rounded_rectangle([n * 0.02, n * 0.30, n * 0.56, n * 0.62],
                            radius=max(1, int(n * 0.05)), outline=255, width=w)
        d.line([(n * 0.56, n * 0.34), (n * 0.99, n * 0.58)], fill=255, width=w)
        d.rectangle([n * 0.84, n * 0.22, n * 0.93, n * 0.34], outline=255, width=w)
        _stroke(d, [(n * 0.30, n * 0.62), (n * 0.50, n * 0.62),
                    (n * 0.44, n * 0.90), (n * 0.24, n * 0.90)], w, closed=True)
    else:
        d.rounded_rectangle([n * 0.02, n * 0.30, n * 0.56, n * 0.62], radius=n * 0.06, fill=255)
        d.rectangle([n * 0.56, n * 0.34, n * 0.99, n * 0.58], fill=255)
        d.rectangle([n * 0.84, n * 0.22, n * 0.93, n * 0.34], fill=255)
        d.polygon([(n * 0.30, n * 0.62), (n * 0.50, n * 0.62),
                   (n * 0.44, n * 0.90), (n * 0.24, n * 0.90)], fill=255)


def _book(d, n, line: bool = False):
    """书（资料/倾向意象）：封面沿书脊留白 + 三条页线。"""
    m = n * 0.09
    if line:
        d.rounded_rectangle([m, m, n - m, n - m], radius=max(1, int(n * 0.10)),
                            outline=255, width=_lw(n))
    else:
        d.rounded_rectangle([m, m, n - m, n - m], radius=n * 0.12, fill=255)
        d.rectangle([m + n * 0.06, m + n * 0.02, m + n * 0.15, n - m - n * 0.02], fill=0)
        for i in range(3):
            yy = m + n * 0.20 + i * n * 0.17
            d.rectangle([m + n * 0.24, yy, n - m - n * 0.14, yy + n * 0.05], fill=0)


def _globe(d, n, line: bool = False):
    """地球/世界图标（赏金卡标题用）：经纬线球体。"""
    m = n * 0.07
    w = _lw(n)
    if line:
        d.ellipse([m, m, n - m, n - m], outline=255, width=w)
        lw = _lw(n)
        d.line([(n * 0.5, m), (n * 0.5, n - m)], fill=255, width=lw)
        d.line([(m, n * 0.5), (n - m, n * 0.5)], fill=255, width=lw)
        d.arc([m, n * 0.18, n - m, n * 0.82], start=0, end=180, fill=255, width=lw)
        d.arc([m, n * 0.18, n - m, n * 0.82], start=180, end=360, fill=255, width=lw)
    else:
        d.ellipse([m, m, n - m, n - m], fill=255)
        r = n * 0.13
        d.ellipse([m + r, m + r, n - m - r, n - m - r], fill=0)
        lw = max(2, int(n * 0.07))
        d.line([(n * 0.5, m), (n * 0.5, n - m)], fill=255, width=lw)
        d.line([(m, n * 0.5), (n - m, n * 0.5)], fill=255, width=lw)
        d.arc([m, n * 0.18, n - m, n * 0.82], start=0, end=180, fill=255, width=lw)
        d.arc([m, n * 0.18, n - m, n * 0.82], start=180, end=360, fill=255, width=lw)


def _window(d, n, line: bool = False):
    """窗口（/pc wins｜focus｜max）：圆角窗框 + 标题栏横线 + 右上关闭钮。"""
    m = n * 0.10
    w = _lw(n)
    yb = m + (n - 2 * m) * 0.28
    if line:
        d.rounded_rectangle([m, m, n - m, n - m], radius=max(1, int(n * 0.10)),
                            outline=255, width=w)
        # 标题栏横线（到关闭钮左侧为止，不穿过钮）
        r = (n - 2 * m) * 0.08
        cx = n - m - r - w * 0.5
        d.line([(m + w * 0.5, yb), (cx - r - w * 0.5, yb)], fill=255, width=w)
        d.ellipse([cx - r, yb - r, cx + r, yb + r], outline=255, width=w)
    else:
        d.rounded_rectangle([m, m, n - m, n - m], radius=n * 0.12, fill=255)
        d.rectangle([m, m, n - m, yb], fill=0)


def _disk(d, n, line: bool = False):
    """磁盘/硬盘（/pc disk）：圆柱形硬盘（顶椭圆 + 柱身 + 底弧）。"""
    m = n * 0.12
    w = _lw(n)
    eh = (n - 2 * m) * 0.34
    top = m
    mid = top + eh / 2
    if line:
        d.ellipse([m, top, n - m, top + eh], outline=255, width=w)
        d.line([(m, mid), (m, n - m)], fill=255, width=w)
        d.line([(n - m, mid), (n - m, n - m)], fill=255, width=w)
        d.arc([m, n - m - eh, n - m, n - m], start=0, end=180, fill=255, width=w)
    else:
        d.ellipse([m, top, n - m, top + eh], fill=255)
        d.rectangle([m, mid, n - m, n - m], fill=255)
        d.ellipse([m, n - m - eh, n - m, n - m], fill=0)


def _clipboard(d, n, line: bool = False):
    """剪贴板（/pc clip｜clipset）：圆角板 + 顶部夹持条。"""
    m = n * 0.12
    w = _lw(n)
    if line:
        d.rounded_rectangle([m, m + (n - 2 * m) * 0.14, n - m, n - m],
                            radius=max(1, int(n * 0.08)), outline=255, width=w)
        cbw = (n - 2 * m) * 0.42
        cx0 = n / 2 - cbw / 2
        d.rounded_rectangle([cx0, m, cx0 + cbw, m + (n - 2 * m) * 0.22],
                            radius=max(1, int(n * 0.05)), outline=255, width=w)
    else:
        d.rounded_rectangle([m, m + (n - 2 * m) * 0.14, n - m, n - m],
                            radius=n * 0.10, fill=255)
        cbw = (n - 2 * m) * 0.42
        cx0 = n / 2 - cbw / 2
        d.rounded_rectangle([cx0, m, cx0 + cbw, m + (n - 2 * m) * 0.22],
                            radius=n * 0.06, fill=255)


def _tag(d, n, line: bool = False):
    """标签/价签（/风格 学｜看｜加梗）：左尖角标签 + 圆孔。"""
    m = n * 0.10
    w = _lw(n)
    pts = [(n * 0.30, m), (n - m, m), (n - m, n - m),
           (n * 0.30, n - m), (m, n / 2)]
    if line:
        _stroke(d, pts, w, closed=True)
        r = (n - 2 * m) * 0.08
        cx = n * 0.44
        cy = n / 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=255, width=w)
    else:
        d.polygon(pts, fill=255)


def _scene(d, n, line: bool = False):
    """场记板（/ha 场景 <名>）：圆角板 + 顶部斜分线（开口）+ 两道竖齿。"""
    m = n * 0.10
    w = _lw(n)
    if line:
        d.rounded_rectangle([m, m, n - m, n - m], radius=max(1, int(n * 0.08)),
                            outline=255, width=w)
        yb = m + (n - 2 * m) * 0.32
        d.line([(m, yb), (n - m, m + (n - 2 * m) * 0.12)], fill=255, width=w)
        yy0 = m + (n - 2 * m) * 0.52
        for fx in (0.36, 0.56):
            x = m + (n - 2 * m) * fx
            d.line([(x, yy0), (x, n - m)], fill=255, width=w)
    else:
        d.rounded_rectangle([m, m, n - m, n - m], radius=n * 0.10, fill=255)
        d.rectangle([m, m, n - m, m + (n - 2 * m) * 0.30], fill=0)


def _list(d, n, line: bool = False):
    """列表（/dl 列表｜发我 N｜取消 N）：圆角框 + 三道横线。"""
    m = n * 0.10
    w = _lw(n)
    if line:
        d.rounded_rectangle([m, m, n - m, n - m], radius=max(1, int(n * 0.10)),
                            outline=255, width=w)
        for i in range(3):
            yy = m + (n - 2 * m) * (0.34 + i * 0.21)
            d.line([(m + (n - 2 * m) * 0.16, yy),
                    (n - m - (n - 2 * m) * 0.16, yy)], fill=255, width=w)
    else:
        d.rounded_rectangle([m, m, n - m, n - m], radius=n * 0.12, fill=255)
        for i in range(3):
            yy = m + (n - 2 * m) * (0.34 + i * 0.21)
            d.rectangle([m + (n - 2 * m) * 0.16, yy,
                         n - m - (n - 2 * m) * 0.16, yy + w], fill=0)


def _monitor(d, n, line: bool = False):
    """显示器/整机（/pc 一键体检）：屏幕 + 支柱 + 底座。"""
    m = n * 0.10
    w = _lw(n)
    sy = m + (n - 2 * m) * 0.66
    by = n - m - (n - 2 * m) * 0.06
    if line:
        d.rounded_rectangle([m, m, n - m, sy], radius=max(1, int(n * 0.08)),
                            outline=255, width=w)
        d.line([(n / 2, sy), (n / 2, by - w)], fill=255, width=w)
        d.line([(n / 2 - (n - 2 * m) * 0.18, by),
                (n / 2 + (n - 2 * m) * 0.18, by)], fill=255, width=w)
    else:
        d.rounded_rectangle([m, m, n - m, sy], radius=n * 0.10, fill=255)
        d.rectangle([n / 2 - w, sy, n / 2 + w, by], fill=255)
        d.rectangle([n / 2 - (n - 2 * m) * 0.18, by,
                     n / 2 + (n - 2 * m) * 0.18, by + w], fill=255)


_SHAPES = {
    "circle": _circle,
    "square": _square,
    "round_square": _round_square,
    "triangle": _triangle,
    "diamond": _diamond,
    "star": _star,
    "bolt": _bolt,
    "clock": _clock,
    "arrow_up": _arrow_up,
    "arrow_right": _arrow_right,
    "check": _check,
    "cross": _cross,
    "question": _question,
    "image": _image,
    "wand": _wand,
    "pause": _pause,
    "cart": _cart,
    "sword": _sword,
    "gun": _gun,
    "book": _book,
    "globe": _globe,
    "window": _window,
    "disk": _disk,
    "clipboard": _clipboard,
    "tag": _tag,
    "scene": _scene,
    "list": _list,
    "monitor": _monitor,
}
