# -*- coding: utf-8 -*-
"""pic/widgets.py —— 可复用控件层（卡片 / 标题 / 进度条 / 菜单行 / 分割线 / 页脚）。

设计依据（2026 UI Card Best Practices，动手前已检索）：
  * **8pt 间距网格**：所有内外边距取 8 的倍数（8/16/24/32），节奏一致；
  * **圆角分级**：控件 8、卡片 22、进度条 pill（h//2），不混用；
  * **层级扫描顺序**：图标/视觉线索 → 标题（大字重） → 支撑信息（低对比次级色）；
  * **关键数据用颜色强调**（百分比、剩余额度），次要描述降对比。

约定：所有绘制函数返回 **下一个可用的 y 坐标**（流式排版），便于模板顺序调用。
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from . import assets, icons
from .canvas import Canvas, linear_gradient, rounded_mask
from .theme import Theme, rgb

# ---- 8pt 间距网格 ----
PAD_S = 8
PAD_M = 16
PAD_L = 24
PAD_XL = 32

# ---- 圆角分级 ----
R_CTRL = 8     # 小控件（标签、图标底）
R_CARD = 22    # 卡片
R_PANEL = 16   # 内嵌面板

# ---- 字号层级 ----
FS_TITLE = 28
FS_SUBTITLE = 20
FS_BODY = 20
FS_DESC = 16
FS_FOOT = 16

# ---- 行高 ----
ROW_H = 64      # 菜单行高（8 的倍数）
BAR_H = 18      # 进度条高
ICON_SIZE = 40  # 行内图标底（容器）
ICON_GLYPH = 26  # 图标本体：占容器 65%，符合主流 icon-in-container 规范（太小会看不清）


def row_h(t: Theme) -> int:
    """菜单行高（按 font_scale 缩放，最小 48 保证可点/可看清）。"""
    return max(48, int(round(ROW_H * float(getattr(t, "font_scale", 1.0) or 1.0))))


def _gradient_image(size: tuple[int, int], c1: str, c2: str, kind: str, angle: int) -> Image.Image:
    """生成渐变图（线性/径向），尺寸 size。

    方向约定（与编辑器「渐变角度」滑块一致，顺时针）：
      0°=左 c1 → 右 c2；90°=上 c1 → 下 c2；180°=左 c2 → 右 c1；270°=上 c2 → 下 c1。

    坑 1：`canvas.linear_gradient(horizontal=True)` 实测是**左=c2、右=c1**（它内部
      `rotate(-90)` 把原图 bottom=白 转到了左侧）。所以要得到「左 c1 右 c2」必须**交换
      参数**传 (c2, c1)，直接传 (c1, c2) 会得到完全反向的渐变（2026-09-14 实测确认）。
    坑 2：`Image.radial_gradient('L')` **中心是黑(0)、边角是白(255)**（与直觉相反）。
      `ImageOps.colorize(grad, black, white)`，所以想让中心=c2 必须传 (c2, c1)。
    """
    from PIL import Image, ImageOps
    w, h = size
    if w <= 0 or h <= 0:
        return Image.new("RGBA", (max(1, w), max(1, h)), rgb(c1) + (255,))
    if kind == "radial":
        grad = Image.radial_gradient("L").resize((w, h), Image.LANCZOS)
        # 中心=黑→c2（高亮），边缘=白→c1（底色）
        return ImageOps.colorize(grad, c2, c1).convert("RGBA")
    # 线性
    angle = int(angle or 0) % 360
    if angle == 0:    # 左 c1 → 右 c2
        return linear_gradient((w, h), c2, c1, horizontal=True).convert("RGBA")
    if angle == 180:  # 左 c2 → 右 c1
        return linear_gradient((w, h), c1, c2, horizontal=True).convert("RGBA")
    if angle == 90:   # 上 c1 → 下 c2
        return linear_gradient((w, h), c1, c2, horizontal=False).convert("RGBA")
    if angle == 270:  # 上 c2 → 下 c1
        return linear_gradient((w, h), c2, c1, horizontal=False).convert("RGBA")
    # 斜向/任意角度：256 小渐变放大到能覆盖对角线的方块 → 顺时针旋转 → 取中心 w×h。
    # 直接生成 (w+h)*1.5 的大渐变再旋转会慢一个量级（预览拖动角度会卡），
    # 渐变本身平滑，先小后放大（BILINEAR）视觉无损但快得多。
    import math
    diag = int(math.ceil(math.hypot(w, h))) + 2
    # 基准：左 c1 → 右 c2（同 angle=0），再顺时针转 angle 度
    base = linear_gradient((256, 256), c2, c1, horizontal=True).resize((diag, diag), Image.BILINEAR)
    rot = base.rotate(-angle, resample=Image.BICUBIC, expand=False)
    x0 = max(0, (rot.width - w) // 2)
    y0 = max(0, (rot.height - h) // 2)
    return rot.crop((x0, y0, x0 + w, y0 + h)).convert("RGBA")


def _lv(t: Theme, name: str, default):
    """安全读取主题数值/字符串字段。

    坑：**绝不能写 `getattr(t, name, d) or d`** —— 0 是合法业务值，`0 or d` 会被静默
    替换成默认值（Python 里 0 是 falsy）。2026-09-14 实测踩到两个：
      * 「不透明度」滑到 0 → 变成 100，图片照样全显示（降透明度功能形同虚设）；
      * 「渐变角度」设 0° → 变成 135°，横向渐变变成斜向。
    """
    v = getattr(t, name, None)
    if v is None:
        return default
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


def _apply_alpha(im: Image.Image, a: int) -> Image.Image:
    """给 RGBA 图整体乘不透明度（0~255）。"""
    if a >= 255:
        return im
    ch = im.split()[3].point(lambda p: p * a // 255)
    im = im.copy()
    im.putalpha(ch)
    return im


def _pos_offset(pos: str, bw: int, bh: int, ow: int, oh: int) -> tuple[int, int]:
    """根据位置关键字计算图片在容器中的左上角偏移。"""
    if "left" in pos:
        x = 0
    elif "right" in pos:
        x = bw - ow
    else:
        x = (bw - ow) // 2
    if "top" in pos:
        y = 0
    elif "bottom" in pos:
        y = bh - oh
    else:
        y = (bh - oh) // 2
    return x, y


def _process_fill_image(im: Image.Image, box: tuple, t: Theme, prefix: str) -> Image.Image | None:
    """按 fit/position/crop/opacity 处理一张填充图片，返回可 paste 的 RGBA 图（或 None）。"""
    x0, y0, x1, y1 = map(int, box)
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    # 裁切（百分比）
    cw = max(1, min(100, int(_lv(t, f"{prefix}_crop_w", 100))))
    ch = max(1, min(100, int(_lv(t, f"{prefix}_crop_h", 100))))
    cx = max(0, min(100 - cw, int(_lv(t, f"{prefix}_crop_x", 0))))
    cy = max(0, min(100 - ch, int(_lv(t, f"{prefix}_crop_y", 0))))
    iw, ih = im.size
    crop = (
        iw * cx // 100, ih * cy // 100,
        iw * (cx + cw) // 100, ih * (cy + ch) // 100,
    )
    try:
        im = im.crop(crop)
    except Exception:
        pass
    iw, ih = im.size
    if iw <= 0 or ih <= 0:
        return None
    fit = str(_lv(t, f"{prefix}_fit", "cover")).strip().lower()
    pos = str(_lv(t, f"{prefix}_pos", "center")).strip().lower()
    if fit == "fill":
        out = im.resize((bw, bh), Image.LANCZOS).convert("RGBA")
    elif fit == "contain":
        scale = min(bw / max(1, iw), bh / max(1, ih))
        out = im.resize((int(iw * scale), int(ih * scale)), Image.LANCZOS).convert("RGBA")
    else:  # cover / tile 都按 cover 缩放
        scale = max(bw / max(1, iw), bh / max(1, ih))
        out = im.resize((int(iw * scale), int(ih * scale)), Image.LANCZOS).convert("RGBA")
    ow, oh = out.size
    dx, dy = _pos_offset(pos, bw, bh, ow, oh)
    canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    canvas.paste(out, (dx, dy), out)
    # 不透明度
    alpha = max(0, min(100, int(_lv(t, f"{prefix}_opacity", 100))))
    if alpha < 100:
        canvas = _apply_alpha(canvas, alpha * 255 // 100)
    return canvas


def _blit_rounded(c: Canvas, layer: Image.Image, box: tuple, r: int) -> None:
    """把一层 RGBA 图按圆角遮罩**合成**到画布（真 alpha 混合，不是替换）。

    坑：不能用 `c.img.paste(layer, (x0,y0), mask)` —— paste 是**直接替换**像素，
    只按 mask 决定"换不换"，不做 alpha 混合。结果 opacity=0 的图片层虽然 alpha=0，
    RGB 值仍被写进画布，`convert('RGB')` 后图片照样显示（不透明度滑块形同虚设）。
    正确做法：先用 `Image.composite` 把圆角遮罩烧进 alpha 通道，再 `alpha_composite`。
    """
    x0, y0, x1, y1 = map(int, box)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    if layer.size != (w, h):
        layer = layer.resize((w, h), Image.LANCZOS)
    mask = rounded_mask((w, h), r)
    blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shaped = Image.composite(layer, blank, mask)
    c.img.alpha_composite(shaped, (x0, y0))


def _card_fill(c: Canvas, t: Theme, box: tuple, r: int) -> None:
    """绘制卡片底板填充（纯色/渐变/图片），不含边框/阴影。"""
    x0, y0, x1, y1 = map(int, box)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    fill_type = (getattr(t, "card_fill_type", "solid") or "solid").strip().lower()
    if fill_type == "gradient":
        kind = (getattr(t, "card_gradient_kind", "linear") or "linear").strip().lower()
        angle = int(_lv(t, "card_gradient_angle", 135))
        grad = _gradient_image((w, h), t.card, t.accent2, kind, angle)
        _blit_rounded(c, grad, box, r)
    elif fill_type == "image":
        im = assets.load(getattr(t, "card_image", "") or "")
        painted = False
        if im is not None:
            fill = _process_fill_image(im, box, t, "card_image")
            if fill is not None:
                _blit_rounded(c, fill, box, r)
                painted = True
        if not painted:
            # 没有图片资源 → 退化为纯色（保证卡片始终有底）
            _blit_rounded(c, Image.new("RGBA", (w, h), rgb(t.card) + (255,)), box, r)
        # 叠加底色（降低图片干扰/统一色调，保证文字可读）
        if painted and getattr(t, "card_image_overlay", True):
            overlay = _apply_alpha(Image.new("RGBA", (w, h), rgb(t.card) + (255,)), 110)
            _blit_rounded(c, overlay, box, r)
    else:
        # 卡片底色透明度（2026-09-17）：<100% 时半透明，露出背景图案/背景图
        ca = int(getattr(t, "card_alpha", 100) or 100)
        c.rounded_rect(box, radius=r, fill=t.card, fill_alpha=max(0, min(100, ca)) * 255 // 100)


def card(c: Canvas, t: Theme, box, radius: int | None = None, border: bool = True) -> None:
    """卡片底板（圆角 + 可选描边 + 可选阴影 + 纯色/渐变/图片填充）。

    `radius=None` 时取主题的 `t.radius`——用户在编辑器里拖圆角滑块立即反映到图上；
    描边宽度取 `t.border`（0=无描边）；阴影强度 `t.shadow`（0=扁平，最大 3）。
    """
    r = int(getattr(t, "radius", R_CARD)) if radius is None else int(radius)
    s = int(getattr(t, "shadow", 0) or 0)
    if s > 0:
        # 阴影：右下偏移的半透明圆角层（强度 1/2/3 → 偏移 2/4/6 px）
        off = s * 2
        c.rounded_rect(
            (box[0] + off, box[1] + off, box[2] + off, box[3] + off),
            radius=r, fill=t.card_shadow, fill_alpha=22 + s * 14,
        )
        # 阴影热区（半透明，范围与阴影层一致）
        c.add_region(
            "card_shadow",
            [box[0] + off, box[1] + off, box[2] + off, box[3] + off],
            label="卡片阴影", group="背景", tip="点击修改卡片阴影色",
        )
    bw = int(getattr(t, "border", 2) or 0)
    outline = t.card_border if (border and bw > 0) else None
    # 先画填充（支持渐变/图片）
    _card_fill(c, t, box, r)
    # 再画边框
    if outline:
        edge = Image.new("RGBA", (int(box[2] - box[0]), int(box[3] - box[1])), (0, 0, 0, 0))
        ImageDraw.Draw(edge).rounded_rectangle(
            [0, 0, int(box[2] - box[0]) - 1, int(box[3] - box[1]) - 1],
            radius=max(0, r), outline=rgb(outline) + (255,), width=max(1, bw),
        )
        c.img.alpha_composite(edge, (int(box[0]), int(box[1])))
    # 卡片底色热区：内缩 12px，边缘留给 card_border
    inner_pad = 12
    c.add_region(
        "card",
        [box[0] + inner_pad, box[1] + inner_pad, box[2] - inner_pad, box[3] - inner_pad],
        label="卡片底色", group="背景", tip="点击修改卡片底色",
    )
    if outline:
        # 边框热区：整个卡片外框（边缘窄环）
        c.add_region(
            "card_border",
            [max(0, box[0]), max(0, box[1]), min(c.width, box[2]), min(c.height, box[3])],
            label="卡片边框", group="背景", tip="点击修改卡片边框色",
        )
    if outline and getattr(t, "double_border", False):
        # 双线描边：外圈粗线 + 内缩细线（证书/卡片质感，对标 Rabbit 出图样本）
        inset = max(1, bw) + 3
        c.rounded_rect(
            (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset),
            radius=max(0, r - inset // 2), outline=outline, width=1,
        )


def header(
    c: Canvas, t: Theme, x: int, y: int, title: str,
    subtitle: str | None = None, right: str | None = None,
) -> int:
    """标题区：主标题（粗体）+ 可选副标题；`right` 为右侧强调值（如 42%）。

    返回下一个可用 y。
    """
    c.text((x, y), title, size=t.fs(FS_TITLE), color=t.text, role="bold", anchor="lm")
    # 标题文字热区
    tw = Canvas.text_width(title, t.fs(FS_TITLE), "bold")
    c.add_region("text", [x, y - t.fs(FS_TITLE) // 2, x + tw, y + t.fs(FS_TITLE) // 2],
                 label="标题文字", group="文字", tip="点击修改标题/正文颜色")
    if right:
        rw = Canvas.text_width(right, t.fs(FS_TITLE), "bold")
        c.text((c.width - x, y), right, size=t.fs(FS_TITLE), color=t.accent, role="bold", anchor="rm")
        c.add_region("accent", [c.width - x - rw, y - t.fs(FS_TITLE) // 2, c.width - x, y + t.fs(FS_TITLE) // 2],
                     label="右上角数值", group="主色", tip="点击修改主色")
    y += 32
    if subtitle:
        sw = Canvas.text_width(subtitle, t.fs(FS_SUBTITLE), "regular")
        c.text((x, y), subtitle, size=t.fs(FS_SUBTITLE), color=t.text_sub, anchor="lm")
        c.add_region("text_sub", [x, y - t.fs(FS_SUBTITLE) // 2, x + sw, y + t.fs(FS_SUBTITLE) // 2],
                     label="副标题文字", group="文字", tip="点击修改次级文字颜色")
        y += 24
    return y + PAD_S


def progress_bar(
    c: Canvas, t: Theme, x: int, y: int, w: int, percent: int,
    height: int = BAR_H,
) -> int:
    """进度条（pill 形 + 渐变填充）。percent 自动夹到 0~100。

    返回下一个可用 y。
    """
    p = max(0, min(100, int(percent)))
    height = max(10, int(round(height * float(getattr(t, "font_scale", 1.0) or 1.0))))
    c.rounded_rect((x, y, x + w, y + height), radius=height // 2, fill=t.track)
    # 进度条轨道热区
    c.add_region("track", [x, y, x + w, y + height], label="进度条轨道", group="进度条",
                 tip="点击修改进度条轨道色")
    fill_w = int(w * p / 100) if p > 0 else 0
    if fill_w > 2:
        # 渐变方向跟随主题（h 横向 / v 纵向 / diag 斜向→暂按横向，环形/斜向由 ring 承担）
        c.gradient_rect(
            (x, y, x + fill_w, y + height),
            t.fill_start, t.fill_end, radius=height // 2,
            horizontal=t.horizontal(),
        )
        c.add_region("fill_start", [x, y, x + fill_w, y + height], label="进度条填充", group="进度条",
                     tip="点击修改进度条填充渐变")
    return y + height


def menu_row(
    c: Canvas, t: Theme, x: int, y: int, w: int,
    icon: str, title: str, desc: str = "",
    icon_color: str | None = None, badge: str | None = None,
) -> int:
    """菜单行：图标徽章 + 标题 + 描述（+ 右侧标签）。

    返回下一行的 y（= y + ROW_H）。
    """
    rh = row_h(t)
    cy = y + rh // 2
    # 图标底（圆角方块，主色浅底 + 主色图标；圆角随主题）
    c.rounded_rect(
        (x, cy - ICON_SIZE // 2, x + ICON_SIZE, cy + ICON_SIZE // 2),
        radius=t.r_ctrl, fill=t.accent_soft,
    )
    c.add_region("accent_soft",
                 [x, cy - ICON_SIZE // 2, x + ICON_SIZE, cy + ICON_SIZE // 2],
                 label="图标徽章底", group="主色", tip="点击修改主色浅底")
    icons.draw(c, icon, x + ICON_SIZE // 2, cy, ICON_GLYPH, icon_color or t.accent)
    c.add_region("accent",
                 [x + ICON_SIZE // 2 - ICON_GLYPH // 2, cy - ICON_GLYPH // 2,
                  x + ICON_SIZE // 2 + ICON_GLYPH // 2, cy + ICON_GLYPH // 2],
                 shape="circle", label="图标", group="主色", tip="点击修改主色")

    tx = x + ICON_SIZE + PAD_M
    max_w = w - ICON_SIZE - PAD_M - (PAD_L if badge else 0)
    title_y = cy - 10 if desc else cy
    tw = Canvas.text_width(Canvas.ellipsis(title, t.fs(FS_BODY), "bold", max_w), t.fs(FS_BODY), "bold")
    c.text((tx, title_y), Canvas.ellipsis(title, t.fs(FS_BODY), "bold", max_w),
           size=t.fs(FS_BODY), color=t.text, role="bold", anchor="lm")
    c.add_region("text", [tx, title_y - t.fs(FS_BODY) // 2, tx + tw, title_y + t.fs(FS_BODY) // 2],
                 label="菜单标题", group="文字", tip="点击修改正文颜色")
    if desc:
        dw = Canvas.text_width(Canvas.ellipsis(desc, t.fs(FS_DESC), "regular", max_w), t.fs(FS_DESC), "regular")
        c.text((tx, cy + 14), Canvas.ellipsis(desc, t.fs(FS_DESC), "regular", max_w),
               size=t.fs(FS_DESC), color=t.text_sub, anchor="lm")
        c.add_region("text_sub", [tx, cy + 14 - t.fs(FS_DESC) // 2, tx + dw, cy + 14 + t.fs(FS_DESC) // 2],
                     label="菜单描述", group="文字", tip="点击修改次级文字颜色")
    if badge:
        bw = Canvas.text_width(badge, t.fs(FS_DESC), "regular")
        c.text((x + w, cy), badge, size=t.fs(FS_DESC), color=t.text_weak, anchor="rm")
        c.add_region("text_weak",
                     [x + w - bw, cy - t.fs(FS_DESC) // 2, x + w, cy + t.fs(FS_DESC) // 2],
                     label="菜单标签", group="文字", tip="点击修改弱文字颜色")
    return y + rh


def kv_row(c: Canvas, t: Theme, x: int, y: int, w: int, label: str, value: str,
           value_color: str | None = None) -> int:
    """键值行（左标签、右取值）。返回下一个可用 y。"""
    lw = Canvas.text_width(label, t.fs(FS_BODY), "regular")
    c.text((x, y), label, size=t.fs(FS_BODY), color=t.text_sub, anchor="lm")
    c.add_region("text_sub", [x, y - t.fs(FS_BODY) // 2, x + lw, y + t.fs(FS_BODY) // 2],
                 label="键名", group="文字", tip="点击修改次级文字颜色")
    vw = Canvas.text_width(value, t.fs(FS_BODY), "bold")
    c.text((x + w, y), value, size=t.fs(FS_BODY), color=value_color or t.text, role="bold", anchor="rm")
    c.add_region("text" if not value_color else "accent",
                 [x + w - vw, y - t.fs(FS_BODY) // 2, x + w, y + t.fs(FS_BODY) // 2],
                 label="取值", group="文字" if not value_color else "主色",
                 tip="点击修改" + ("文字色" if not value_color else "强调色"))
    return y + PAD_L + PAD_S


def section_label(c: Canvas, t: Theme, x: int, y: int, text: str) -> int:
    """分组标题：左侧短色条 + 主色小标题（把长菜单切成几块，便于扫读）。

    返回下一个可用 y。仅在有多分组时使用（单组菜单不画，避免多余噪音）。
    """
    bar_h = 18
    c.rounded_rect((x, y - bar_h // 2, x + 4, y + bar_h // 2), radius=2, fill=t.accent)
    c.add_region("accent", [x, y - bar_h // 2, x + 4, y + bar_h // 2],
                 label="分组色条", group="主色", tip="点击修改主色")
    tw = Canvas.text_width(text, t.fs(FS_DESC), "bold")
    c.text((x + 12, y), text, size=t.fs(FS_DESC), color=t.accent, role="bold", anchor="lm")
    c.add_region("accent", [x + 12, y - t.fs(FS_DESC) // 2, x + 12 + tw, y + t.fs(FS_DESC) // 2],
                 label="分组标题", group="主色", tip="点击修改主色")
    return y + 28


def divider(c: Canvas, t: Theme, x0: int, x1: int, y: int) -> int:
    """分割线。返回下一个可用 y。"""
    c.line((x0, y, x1, y), fill=t.divider, width=1)
    c.add_region("divider", [x0, y - 3, x1, y + 3], label="分割线", group="背景",
                 tip="点击修改分割线颜色")
    return y + PAD_M


def footer(c: Canvas, t: Theme, x: int, y: int, text: str) -> int:
    """页脚（弱对比小字）。返回下一个可用 y。"""
    c.text((x, y), text, size=t.fs(FS_FOOT), color=t.text_weak, anchor="lm")
    tw = Canvas.text_width(text, t.fs(FS_FOOT), "regular")
    c.add_region("text_weak", [x, y - t.fs(FS_FOOT) // 2, x + tw, y + t.fs(FS_FOOT) // 2],
                 label="页脚文字", group="文字", tip="点击修改弱文字颜色")
    return y + PAD_L


def badge(c: Canvas, t: Theme, x: int, y: int, text: str,
          color: str | None = None, bg: str | None = None) -> int:
    """行内标签（pill）。返回标签宽度。"""
    pad_x = 10
    fs = t.fs(FS_DESC)
    tw = Canvas.text_width(text, fs, "bold")
    w = tw + pad_x * 2
    h = max(20, int(round(24 * float(getattr(t, "font_scale", 1.0) or 1.0))))
    c.rounded_rect((x, y - h // 2, x + w, y + h // 2), radius=h // 2,
                   fill=bg or t.accent_soft)
    c.add_region("accent_soft",
                 [x, y - h // 2, x + w, y + h // 2],
                 label="标签底色", group="主色", tip="点击修改主色浅底")
    c.text((x + w // 2, y), text, size=fs, color=color or t.accent, role="bold", anchor="mm")
    c.add_region("accent",
                 [x + w // 2 - tw // 2, y - fs // 2, x + w // 2 + tw // 2, y + fs // 2],
                 label="标签文字", group="主色", tip="点击修改主色")
    return w


def panel(c: Canvas, t: Theme, box, radius: int | None = None) -> None:
    """内嵌面板（比卡片更深/更浅一档，用于强调区块）。"""
    c.rounded_rect(box, radius=(t.r_ctrl if radius is None else radius), fill=t.track)
    # 面板区域：内缩 8px 避免与外层卡片/边框混淆
    c.add_region("track",
                 [box[0] + 8, box[1] + 8, box[2] - 8, box[3] - 8],
                 label="内嵌面板", group="进度条", tip="点击修改轨道/面板色")


def stat_block(
    c: Canvas, t: Theme, x: int, y: int, w: int,
    value: str, label: str, color: str | None = None,
) -> int:
    """数值块：大数字 + 下方标签（关键数据用主色强调）。返回下一个可用 y。"""
    vw = Canvas.text_width(value, t.fs(32), "bold")
    c.text((x, y), value, size=t.fs(32), color=color or t.accent, role="bold", anchor="lm")
    c.add_region(
        "accent" if color else "text",
        [x, y - t.fs(32) // 2, x + vw, y + t.fs(32) // 2],
        label="数值", group="主色" if color else "文字",
        tip="点击修改" + ("强调色" if color else "文字色"),
    )
    lw = Canvas.text_width(label, t.fs(FS_DESC), "regular")
    c.text((x, y + 34), label, size=t.fs(FS_DESC), color=t.text_sub, anchor="lm")
    c.add_region("text_sub", [x, y + 34 - t.fs(FS_DESC) // 2, x + lw, y + 34 + t.fs(FS_DESC) // 2],
                 label="数值标签", group="文字", tip="点击修改次级文字颜色")
    return y + 34 + PAD_M


def ring_progress(
    c: Canvas, t: Theme, cx: int, cy: int, radius: int, thickness: int,
    percent: int, color_start: str | None = None, color_end: str | None = None,
) -> None:
    """环形进度（**单个核心占比**用环形，多个进度用条形 —— 2026 主流惯例）。

    实现：3× 超采样画整圈底环 + 分段弧（每段取渐变插值色，近似环形渐变），
    再 LANCZOS 缩回，避免 arc 的锯齿。percent 自动夹到 0~100。
    """
    p = max(0, min(100, int(percent)))
    size = radius * 2 + thickness + 2
    scale = 3
    big = size * scale
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    box = [0, 0, big - 1, big - 1]
    d.ellipse(box, outline=rgb(t.track) + (255,), width=thickness * scale)

    if p > 0:
        c1 = rgb(color_start or t.fill_start)
        c2 = rgb(color_end or t.fill_end)
        seg = max(1, p // 5)  # 每 5% 一段，段内取插值色 → 近似渐变
        for i in range(seg):
            a0 = -90 + (i * 5) * 3.6
            a1 = -90 + min(p, (i + 1) * 5) * 3.6
            if a1 <= a0:
                continue
            k = i / max(1, seg - 1)
            col = tuple(int(c1[j] + (c2[j] - c1[j]) * k) for j in range(3)) + (255,)
            d.arc(box, start=a0, end=a1, fill=col, width=thickness * scale)

    layer = layer.resize((size, size), Image.LANCZOS)
    c.img.alpha_composite(layer, (int(cx - size / 2), int(cy - size / 2)))
    # 环形进度热区：整个环的外接正方形；点击改填充渐变
    c.add_region(
        "fill_start",
        [cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2],
        shape="circle", label="环形进度", group="进度条",
        tip="点击修改进度条填充渐变起/末色",
    )
