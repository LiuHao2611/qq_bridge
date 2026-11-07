# -*- coding: utf-8 -*-
"""pic/decor.py —— 背景装饰层（圆点阵 / 网格 / 斜纹 / 贴纸平铺 + 角落大贴纸）。

设计对标（2026-09-07，用户提供 Warframe Rabbit 出图样本）：
  * **背景平铺贴纸**：整页低对比图案铺满（样本里的兔子线稿平铺背景）；
  * **角落大贴纸**：右下角一个大号半透明旋转贴纸压在最上层（样本右下角的兔子）；
  * 全部用 `icons.layer()` 自绘几何图标实现——**零新增依赖、零外部素材**，
    任何机器上跑都一致（不依赖字体 emoji / 网络 PNG）。

2026-09-07 二次增强（用户需求）：
  * 背景支持「图片背景」(`bg_pattern="image"`)：用上传/主项目生图的背景图平铺；
  * 背景平铺贴纸支持「贴纸图」(`sticker_image`)：用上传/生图的图，替代几何图标；
  * 角落大贴纸与背景贴纸**解耦**：各自有独立图标/图片来源，
    不再被迫共用同一个 `sticker` 几何图标。

层次约定（模板调用顺序）：
    Canvas(bg) → decor.background() → 卡片 → 内容 → decor.corner()

性能：贴纸平铺先生成一个 tile 图层再双循环 alpha_composite（几十次 paste，
800×500 下 <15ms），绝不做逐像素操作。
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from . import assets
from . import icons
from .canvas import Canvas
from .theme import Theme, rgb

#: 平铺参数（8pt 网格的倍数，观感与性能平衡）
_TILE = 64
_GAP = 40
_DOT_STEP = 36
_DOT_R = 3
_GRID_STEP = 48
_DIAG_STEP = 26
#: 图片类平铺的基准 tile 边长（自适应：保持图比例缩到该边长内）
_IMG_TILE = 220


def _effective_bg(t: Theme, tpl: str):
    """按模板名合并「卡片专属背景覆盖」，返回生效的背景参数视图。

    覆盖（t.bg_overrides[tpl]）里的键优先，缺失键回落主题级全局值；
    无覆盖时原样返回 t（零开销、行为与旧版完全一致）。
    """
    from dataclasses import fields as _dc_fields  # noqa: PLC0415 — 局部引入避免循环

    ov = getattr(t, "bg_overrides", None)
    if not isinstance(ov, dict):
        return t
    o = ov.get((tpl or "").strip().lower())
    if not isinstance(o, dict) or not o:
        return t
    import types as _types  # noqa: PLC0415

    d = {f.name: getattr(t, f.name) for f in _dc_fields(t) if f.name != "bg_overrides"}
    d.update(o)
    return _types.SimpleNamespace(**d)


def elem_tf(t: Theme, tpl: str):
    """卡片专属「元素微调」（2026-09-24 全量自由编辑）：读 bg_overrides[tpl] 里的

      * ``elem_pos``: ``{元素键: [dx, dy]}`` —— 该元素的像素偏移（不参与排版流，
        纯视觉平移，元素之间允许重叠——自由摆放本就是用户的选择）；
      * ``elem_scl``: ``{元素键: 百分比}`` —— 该元素字号/尺寸缩放（30~300%，
        **参与排版流**：行高/换行随字号重算，卡片高度自适应）。

    返回三个闭包（对固定风格卡模板共用）：
      * ``off(key)``            -> ``(dx, dy)``（未配置 = (0, 0)）
      * ``fsz(key, base)``      -> 缩放后的字号/尺寸（未配置 = base，逐字节一致）
      * ``pct(key)``            -> 缩放百分比 float（未配置 = 100.0）

    无创原则：未配置时 off=(0,0)、fsz=base、pct=100，渲染与旧版逐字节一致。
    """
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get(tpl) if isinstance(ov.get(tpl), dict) else {}
    pos = v.get("elem_pos") if isinstance(v.get("elem_pos"), dict) else {}
    scl = v.get("elem_scl") if isinstance(v.get("elem_scl"), dict) else {}

    def off(key: str, dx: int = 0, dy: int = 0):
        p = pos.get(key)
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                dx += max(-4000, min(4000, int(p[0])))
                dy += max(-4000, min(4000, int(p[1])))
            except (TypeError, ValueError):
                pass
        return dx, dy

    def pct(key: str) -> float:
        try:
            s = float(scl.get(key, 100))
        except (TypeError, ValueError):
            s = 100.0
        return max(30.0, min(300.0, s))

    def fsz(key: str, base: int) -> int:
        return max(8, int(round(base * pct(key) / 100.0)))

    return off, fsz, pct


def background(c: Canvas, t: Theme, tpl: str = "", only_override: bool = False) -> None:
    """画背景装饰（在画卡片**之前**调用）。未知图案一律静默跳过。

    :param tpl: 模板名——命中 ``t.bg_overrides[tpl]`` 时该卡使用专属背景，
                缺失键回落主题级全局背景（2026-09-23 卡片专属背景）。
    :param only_override: True 时**仅当**该模板存在专属覆盖才画背景
                （固定风格卡如裂隙用：全局背景不得改变其现有观感——无创原则）。
    """
    key = (tpl or "").strip().lower()
    ov_map = getattr(t, "bg_overrides", None)
    o = ov_map.get(key) if isinstance(ov_map, dict) else None
    has_override = isinstance(o, dict) and bool(o)
    if only_override and not has_override:
        return
    eff = _effective_bg(t, tpl) if has_override else t
    kind = str(getattr(eff, "bg_pattern", "none") or "none").strip().lower()
    # 背景图案热区：铺满整张画布（在页面底色之上，优先命中）
    if kind not in ("", "none"):
        c.add_region("bg_pattern", [0, 0, c.width, c.height], label="背景图案", group="背景",
                     type="layout", tip="点击修改背景图案")
    if kind in ("", "none"):
        return
    if kind == "dots":
        _dots(c, eff)
    elif kind == "grid":
        _grid(c, eff)
    elif kind == "diagonal":
        _diagonal(c, eff)
    elif kind == "stickers":
        _tiles(c, eff)
    elif kind == "image":
        _bg_image(c, eff)


def corner(c: Canvas, t: Theme) -> None:
    """右下角大贴纸（在**所有内容之后**调用 = 压最上层，水印感）。"""
    if not getattr(t, "corner_sticker", False):
        return
    size = max(48, int(min(c.width, c.height) * 0.30))
    a = _alpha(t) * 2 + 26  # 角落贴纸比平铺更明显一点
    # 优先用上传/生图的角落贴纸图；否则用**解耦的**角落图标（不再共用背景 sticker）
    img = assets.load(getattr(t, "corner_sticker_image", "") or "")
    if img is not None:
        icon = _fit(img, size, a, angle=-14)
    else:
        icon_name = getattr(t, "corner_sticker_icon", "") or getattr(t, "sticker", "star")
        icon = icons.layer(icon_name, size, t.accent, alpha=a, angle=-14)
    # 故意让贴纸往画布外探出一角（裁掉一点更像"贴上去的"，样本同款）
    sx = c.width - icon.width + size // 5
    sy = c.height - icon.height + size // 5
    c.img.alpha_composite(icon, (sx, sy))
    # 角落大贴纸热区（颜色+图标两个维度）
    c.add_region("accent2", [max(0, sx), max(0, sy), c.width, c.height],
                 label="角落贴纸", group="主色", tip="点击修改辅色")
    c.add_region("corner_sticker_icon", [max(0, sx), max(0, sy), c.width, c.height],
                 label="角落贴纸图标", group="装饰", type="layout",
                 tip="点击修改角落贴纸图标")


# ------------------------------------------------------------------ 内部
def _alpha(t: Theme) -> int:
    """主题里的贴纸不透明度（%）换算成 0~255。夹到 3~40% 防止手滑调成实心。"""
    pct = max(3, min(40, int(getattr(t, "sticker_alpha", 12) or 12)))
    return pct * 255 // 100


def _apply_alpha(im: Image.Image, a: int) -> Image.Image:
    """给 RGBA 图整体乘一个不透明度（a: 0~255）。"""
    im = im.convert("RGBA")
    if a >= 255:
        return im
    ch = im.split()[3].point(lambda p: p * a // 255)
    im.putalpha(ch)
    return im


def _fit(im: Image.Image, size: int, a: int, angle: int = 0) -> Image.Image:
    """把资源图缩放成 size×size（保持比例，居中裁切）+ 不透明度 + 旋转。"""
    im = _apply_alpha(im, a)
    im.thumbnail((size, size), Image.LANCZOS)
    if angle:
        im = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    return im


def _tile_image(c: Canvas, im: Image.Image, gap: int = 0) -> None:
    """把一张图平铺铺满画布。"""
    iw, ih = im.size
    if iw <= 0 or ih <= 0:
        return
    pitch_x, pitch_y = iw + gap, ih + gap
    for y in range(-ih // 2, c.height + ih, pitch_y):
        for x in range(-iw, c.width + iw, pitch_x):
            c.img.alpha_composite(im, (x, y))


def _dots(c: Canvas, t: Theme) -> None:
    step = _DOT_STEP
    a = _alpha(t)
    layer = Image.new("RGBA", (c.width, c.height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = rgb(t.accent) + (a,)
    for yi, y in enumerate(range(step // 2, c.height + step, step)):
        # 奇数行错位半格 → 蜂窝式点阵（比方阵更"图案感"）
        off = (yi % 2) * (step // 2)
        for x in range(step // 2 + off, c.width + step, step):
            d.ellipse([x - _DOT_R, y - _DOT_R, x + _DOT_R, y + _DOT_R], fill=col)
    c.img.alpha_composite(layer)


def _grid(c: Canvas, t: Theme) -> None:
    a = _alpha(t)
    layer = Image.new("RGBA", (c.width, c.height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = rgb(t.divider) + (min(255, a * 2),)
    for x in range(0, c.width + _GRID_STEP, _GRID_STEP):
        d.line([(x, 0), (x, c.height)], fill=col, width=1)
    for y in range(0, c.height + _GRID_STEP, _GRID_STEP):
        d.line([(0, y), (c.width, y)], fill=col, width=1)
    c.img.alpha_composite(layer)


def _diagonal(c: Canvas, t: Theme) -> None:
    a = _alpha(t)
    layer = Image.new("RGBA", (c.width, c.height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = rgb(t.divider) + (min(255, a * 2),)
    # 45° 斜纹：从左边缘外一路画到右边缘外
    for i in range(-c.height, c.width + c.height, _DIAG_STEP):
        d.line([(i, 0), (i + c.height, c.height)], fill=col, width=2)
    c.img.alpha_composite(layer)


def _tiles(c: Canvas, t: Theme) -> None:
    """背景贴纸平铺：优先用上传/生图的贴纸图，否则用几何图标。"""
    img = assets.load(getattr(t, "sticker_image", "") or "")
    if img is not None:
        a = _alpha(t)
        tile = _fit(img, _TILE, a, angle=-14)
        _tile_image(c, tile, gap=_GAP)
        return
    a = _alpha(t)
    icon = icons.layer(t.sticker, _TILE, t.accent, alpha=a, angle=-14)
    iw, ih = icon.size
    pitch_x, pitch_y = iw + _GAP, ih + _GAP
    row = 0
    for y in range(-ih // 2, c.height + ih, pitch_y):
        # 每行错开半个间距 → 砖缝式排布（整列对齐会像二维码）
        off = (row % 2) * (pitch_x // 2)
        for x in range(-iw + off, c.width + iw, pitch_x):
            c.img.alpha_composite(icon, (x, y))
        row += 1


def _bg_image(c: Canvas, t: Theme) -> None:
    """图片背景：按 fit/position/crop/opacity/overlay 铺满画布。"""
    from . import widgets as _w
    img = assets.load(getattr(t, "bg_image", "") or "")
    if img is None:
        return
    fit = str(_w._lv(t, "bg_image_fit", "tile")).strip().lower()
    if fit == "tile":
        a = _alpha(t)
        tile = _fit(img, _IMG_TILE, a)
        _tile_image(c, tile, gap=_GAP // 2)
        return
    fill = _w._process_fill_image(img, (0, 0, c.width, c.height), t, "bg_image")
    if fill is None:
        return
    c.img.alpha_composite(fill, (0, 0))
    if getattr(t, "bg_image_overlay", False):
        overlay = Image.new("RGBA", (c.width, c.height), rgb(t.bg) + (255,))
        overlay = _w._apply_alpha(overlay, 110)
        c.img.alpha_composite(overlay, (0, 0))
