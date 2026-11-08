# -*- coding: utf-8 -*-
"""bounty_atoms.py —— 赏金卡「度量优先」排版原语。

按 @skill:card-design 的「让它没地方犯」原则，本模块是赏金卡**唯一**的文本
混排入口：所有文本先 measure 再绘制，多色 token 走 draw_tokens 显式 gap，
列起点走 Columns 固定网格，渲染后走 check_boxes 自检。

红线对应：
  * 文字重叠 / 间距失控 → draw_tokens 逐 token 测量 + 显式 gap；
  * 对齐漂移           → Columns 固定列起点 + 行尾右锚定；
  * 溢出 / 裁切        → ellipsis 按测量宽度截断 + check_boxes 越界检测；
  * 空格凑间距         → 本模块不提供任何「加空格对齐」的写法；
  * 估算宽度           → 所有 x 推进都由 tw() 的真实测量值累加。

本项目已有 Canvas.emoji_safe / Canvas.text_width（与绘制同字形净化），
因此不重复实现字体链，只在其上补 draw_tokens / Columns / check_boxes。
"""
from __future__ import annotations

from typing import Iterable, Sequence

from .. import icons
from ..canvas import Canvas, emoji_safe
from ..fonts import load as load_font

Token = tuple[str, str, str]  # (text, color, role)
Box = tuple[int, int, int, int]


# --------------------------------------------------------------- 度量 ----
def tw(text: str, size: int, role: str = "regular") -> int:
    """真实像素宽度（与 Canvas.text 同字形净化）。"""
    return Canvas.text_width(emoji_safe(str(text)), size, role)


def th(text: str, size: int, role: str = "regular") -> int:
    return Canvas.text_size(emoji_safe(str(text)), size, role)[1]


def ellipsis(text: str, size: int, role: str, max_w: int) -> str:
    """超宽单行截断；max_w<=0 返回空串（绝不画出安全区）。"""
    if max_w <= 0:
        return ""
    return Canvas.ellipsis(emoji_safe(str(text)), size, role, max_w)


def text_box(c: Canvas, x: int, y: int, text: str, size: int,
             color: str, role: str = "regular",
             anchor: str = "lm") -> Box:
    """画一段文本并返回**与绘制完全一致**的 bbox（Pillow textbbox）。"""
    s = emoji_safe(str(text))
    f = load_font(role, size)
    c.text((x, y), s, size=size, color=color, role=role, anchor=anchor)
    b = c.draw.textbbox((x, y), s, font=f, anchor=anchor)
    return (int(b[0]), int(b[1]), int(b[2]), int(b[3]))


def draw_tokens(c: Canvas, x: int, y: int, tokens: Sequence[Token],
                size: int, gap: int = 8,
                anchor: str = "lm") -> tuple[int, list[Box]]:
    """多色/多字重混排：逐 token 测量 → 绘制 → x += 实测宽 + 显式 gap。

    返回 (总宽, 每 token 的 bbox)。禁止用空格凑间距：token 之间只走 gap 参数。
    """
    boxes: list[Box] = []
    cx = int(x)
    for text, color, role in tokens:
        if not text:
            boxes.append((cx, int(y), cx, int(y)))
            continue
        b = text_box(c, cx, int(y), text, size, color, role, anchor=anchor)
        boxes.append(b)
        # 用**绘制返回的真实 bbox 宽度**推进，而不是另一次 tw()：
        # getbbox 与 textbbox 在个别字体上会差 1px，gap=0 的连续句子若按
        # 两次测量推进会粘连/重叠。实测 bbox 是唯一权威值。
        cx += (b[2] - b[0]) + gap
    total = max(0, cx - int(x) - gap) if tokens else 0
    return total, boxes


def draw_tokens_centered(c: Canvas, cx: int, y: int, tokens: Sequence[Token],
                         size: int, gap: int = 8) -> tuple[int, list[Box]]:
    """居中版：总宽先按测量值求和，再反推起点。"""
    widths = [tw(t, size, r) for (t, _, r) in tokens]
    total = sum(widths) + gap * max(0, len(tokens) - 1)
    return draw_tokens(c, int(cx - total / 2), int(y), tokens, size, gap)


# ------------------------------------------------------------ 列网格 ----
class Columns:
    """固定列网格：列起点一经定义全卡不变，行尾元素右锚定到 content_right。"""

    def __init__(self, left: int, right: int, gap: int = 8) -> None:
        self.left = int(left)
        self.right = int(right)
        self.gap = int(gap)
        self._cols: dict[str, int] = {}

    def col(self, key: str, x: int) -> "Columns":
        self._cols[key] = int(x)
        return self

    def x(self, key: str) -> int:
        return self._cols[key]

    def tail_x(self, text: str, size: int, role: str = "regular") -> int:
        """行尾元素右锚定：与前面内容长度无关。"""
        return self.right - tw(text, size, role)

    @property
    def width(self) -> int:
        return self.right - self.left


# -------------------------------------------------------------- 徽章 ----
def badge(c: Canvas, x: int, y: int, text: str, *, size: int,
          fill: str, color: str, outline: str | None = None,
          role: str = "bold", pad_x: int = 10, pad_y: int = 5,
          dot: bool = False, notch: bool = False, radius: int | None = None,
          min_w: int = 0) -> tuple[Box, int, int]:
    """画圆角徽章，返回 (文本 bbox, 徽章宽, 徽章高)。

    dot   —— 绿色「赏金N」左侧的小圆环（参考图语义）；
    notch —— Lv 徽章右上角的小折角（参考图语义）。
    徽章本身不进 check_boxes（底色必然与文字 bbox 重叠），只登记文字 bbox。
    """
    label = emoji_safe(str(text))
    inner = tw(label, size, role)
    dot_w = (size + 6) if dot else 0
    w = max(int(min_w), dot_w + inner + pad_x * 2)
    h = size + pad_y * 2
    r = h // 2 if radius is None else radius
    c.rounded_rect((x, y, x + w, y + h), radius=r, fill=fill,
                   outline=outline, width=1)
    if notch:
        # 右上角折角：小三角切口，纯装饰，不占内容安全区
        nx, ny = x + w - 10, y
        c.draw.polygon([(nx, ny), (x + w, ny), (x + w, ny + 10)], fill=fill)
    tx = x + dot_w + pad_x
    if dot:
        rr = max(3, size // 5)
        cy = y + h // 2
        c.ellipse((x + pad_x - rr, cy - rr, x + pad_x + rr, cy + rr),
                  outline=color, width=2)
        c.ellipse((x + pad_x - rr // 2, cy - rr // 2,
                   x + pad_x + rr // 2, cy + rr // 2), fill=color)
    b = text_box(c, tx, y + h // 2, label, size, color, role, anchor="lm")
    return b, w, h


def icon(c: Canvas, kind: str, cx: int, cy: int, size: int, color: str) -> Box:
    """几何图标（细描边）+ 返回其外接 bbox。未知 kind 由 icons.draw 退化为 circle。"""
    icons.draw(c, kind, cx, cy, size, color, style="line")
    return (int(cx - size // 2), int(cy - size // 2),
            int(cx + size // 2), int(cy + size // 2))


def item_image(c: Canvas, path: str | None, x: int, y: int, size: int) -> bool:
    """奖励物品缩略图：有本地图则等比贴入 size×size，失败返回 False 由调用方画几何图标。"""
    if not path:
        return False
    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        bb = img.getchannel("A").getbbox()
        if bb:
            img = img.crop(bb)
        k = min(size / max(1, img.width), size / max(1, img.height), 1.0)
        if k < 1.0:
            img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))),
                             Image.LANCZOS)
        px = x + (size - img.width) // 2
        py = y + (size - img.height) // 2
        c.paste_alpha(img, (px, py))
        return True
    except Exception:  # noqa: BLE001 — 图源缺失不阻塞出卡
        return False


# -------------------------------------------------------------- 自检 ----
def _overlap_2d(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def check_boxes(bounds: Box, boxes: Iterable[tuple], min_gap: int = 2) -> list[str]:
    """渲染后自检：溢出 / 重叠 / 同行最小间距。返回违规描述列表（空 = 通过）。

    boxes: [(label, x0, y0, x1, y1), ...]；只登记**文本与独立图形**的 bbox，
    卡片底色/徽章底色这类必然包住文字的容器不登记，避免假阳性。
    """
    bx0, by0, bx1, by1 = bounds
    problems: list[str] = []
    items = list(boxes)
    for label, x0, y0, x1, y1 in items:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append(f"[溢出] {label} 越界: bbox=({x0},{y0},{x1},{y1}) bounds={bounds}")
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, a = items[i][0], tuple(items[i][1:])
            lb, b = items[j][0], tuple(items[j][1:])
            if _overlap_2d(a, b):
                problems.append(f"[重叠] {la} <-> {lb}: {a} vs {b}")
            if a[1] < b[3] and b[1] < a[3]:
                gx = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gx < min_gap:
                    problems.append(f"[间距] {la} <-> {lb} 水平间距 {gx}px < {min_gap}px")
    return problems


def gate(bounds: Box, boxes: Iterable[tuple], min_gap: int = 2,
         strict: bool = True) -> list[str]:
    """交付闸门：违规且 strict 时 raise，绝不静默放行。"""
    problems = check_boxes(bounds, boxes, min_gap)
    if problems and strict:
        raise SystemExit("\n".join(["[card-design 自检未通过] 触线返工："] + problems[:20]))
    return problems
