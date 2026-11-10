# -*- coding: utf-8 -*-
"""pic/canvas.py —— 绘图画布（Pillow 封装）。

思路借鉴 zhenxun_bot 的 BuildImage（圆角/渐变/居中/导出），**按思路重写**（AGPL 不抄源码）。

提供四类能力：
1. 画布与导出：RGBA 画布 → PNG bytes；
2. 形状：圆角矩形（含渐变填充、描边）、直线、圆形头像框；
3. 文本：按字重/字号绘制，支持 anchor 定位与最大宽度截断（省略号）；
4. 文本度量：宽高、按最大宽度自动换行。

约定：
* 所有坐标为**左上角原点**的像素坐标；
* 颜色统一接收 '#RRGGBB' 字符串（内部转 RGB），与 theme 对齐；
* **纯同步**：调用方负责放进 `asyncio.to_thread`，避免阻塞 nonebot 事件循环。
"""
from __future__ import annotations

import math
from functools import lru_cache
from io import BytesIO
from typing import Iterable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import fonts
from .theme import LAYOUT_FIELDS, rgb

Point = tuple[int, int]
Box = tuple[int, int, int, int]  # x0, y0, x1, y1


# ===========================================================================
# 渲染安全化（2026-10-21）：emoji 豆腐框修复
# ===========================================================================
# 背景（真机实证 /人设 列表）：卡片用中文字体（微软雅黑等）绘制，**没有 emoji
# 字形**，直接画 emoji 会出 .notdef 豆腐框（🐱→□；「🕯️」连变体选择符一起变两个□）。
# 原则：**只删字体画不出的字符，不删画得出的**——★ ※ → 这类 BMP 符号雅黑有
# 字形，必须保留；删不认识的才不会误伤既有卡片（电源告警 ⚠ 等照常显示）。
_VS_JUNK = "\ufe0e\ufe0f\u200d\u20e3"          # 变体选择符 / ZWJ / 组合键帽
_GLYPH_OK: dict[str, bool] = {}                # BMP 符号字形探测缓存（进程级）


def _has_glyph(ch: str) -> bool:
    """当前字体链能否真画出该字符（与 .notdef 探针比较，scan_families 同款思路）。"""
    ok = _GLYPH_OK.get(ch)
    if ok is not None:
        return ok
    try:
        f = fonts.load("regular", 20)
        m = f.getmask(ch)
        probe = f.getmask("\u0378")  # 未分配码位 → 必为 .notdef
        ok = bool(m.size[0] > 0 and m.size[1] > 0 and bytes(m) != bytes(probe))
    except Exception:  # noqa: BLE001 — 探测失败按「有字形」处理（宁多画不误删）
        ok = True
    _GLYPH_OK[ch] = ok
    return ok


def emoji_safe(text: str) -> str:
    """删掉当前中文字体画不出的 emoji/符号，返回可直接绘制与测量的文本。

    规则：
      1. 变体选择符 / ZWJ 永远删（主字符被删后它们只会变独立豆腐框）；
      2. 增补平面 emoji 区（U+1F000–U+1FFFF，含 🐱🦇🧚 等）中文字体一律没有 → 删；
      3. BMP 符号区（U+2190–U+2BFF：箭头/杂项/丁贝）按字形探测，有字形保留。
    绘制（Canvas.text）与测量（_measure/text_size/wrap）必须**同用本函数**，
    否则布局与实际绘制不一致。
    """
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        o = ord(ch)
        if ch in _VS_JUNK:
            continue
        if 0x1F000 <= o <= 0x1FFFF:
            continue
        if 0x2190 <= o <= 0x2BFF and not _has_glyph(ch):
            continue
        out.append(ch)
    return "".join(out)


@lru_cache(maxsize=8192)
def _measure(text: str, size: int, role: str) -> tuple[int, int]:
    """文本宽高（缓存：按 (文本, 字号, 字重) 复用 getbbox 结果）。

    卡片里大量重复 token（"倾向："/"["/"]"/武器名等）与逐字换行都受益。
    字体本身已由 fonts.load 按 (role,size) 缓存，这里再缓存测量结果避免重复 shaping。
    """
    font = fonts.load(role, size)
    box = font.getbbox(emoji_safe(text))
    return int(box[2] - box[0]), int(box[3] - box[1])


def _region_type(field: str) -> str:
    return "layout" if field in LAYOUT_FIELDS else "color"


def _region_group(field: str) -> str:
    if field in ("bg", "card", "card_border", "card_shadow", "divider"):
        return "背景"
    if field in ("text", "text_sub", "text_weak"):
        return "文字"
    if field in ("accent", "accent2", "accent_soft"):
        return "主色"
    if field in ("track", "fill_start", "fill_end"):
        return "进度条"
    if field in ("ok", "warn", "err"):
        return "语义色"
    return "其他"


def rounded_mask(size: tuple[int, int], radius: int, scale: int = 4) -> Image.Image:
    """生成圆角遮罩（L 模式，白色=保留）。

    用 scale 倍超采样再缩回，得到平滑边缘（直接画小图会有明显锯齿）。
    """
    w, h = size
    if w <= 0 or h <= 0:
        return Image.new("L", (max(1, w), max(1, h)), 0)
    radius = int(max(0, min(radius, min(w, h) // 2)))
    big = (max(1, w * scale), max(1, h * scale))
    mask = Image.new("L", big, 0)
    d = ImageDraw.Draw(mask)
    if radius <= 0:
        d.rectangle([0, 0, big[0] - 1, big[1] - 1], fill=255)
    else:
        d.rounded_rectangle([0, 0, big[0] - 1, big[1] - 1], radius=radius * scale, fill=255)
    return mask.resize((max(1, w), max(1, h)), Image.LANCZOS)


def linear_gradient(size: tuple[int, int], c1: str, c2: str, horizontal: bool = True) -> Image.Image:
    """生成线性渐变图（RGB）。

    实现：Pillow 内置 `Image.linear_gradient('L')` 是 256×256 的**竖直**渐变（上黑下白）：
      * 竖直渐变 → 直接 resize 到目标尺寸；
      * 横向渐变 → 先 `rotate(-90)`（顺时针 90°，原顶部=黑转到左侧）再 resize。
    然后逐通道对灰度做线性插值得到 RGB。避免引入 numpy，也避免逐像素 putpixel 慢路径。

    坑：不能先 resize 成 (1, N) 再拉成 (N, 1) —— 目标高度为 1 时整列会被压成**同一个灰度值**，
    渐变退化成纯色（2026-09-07 实测踩到，左中右像素完全相同）。
    """
    w, h = (max(1, int(size[0])), max(1, int(size[1])))
    r1, g1, b1 = rgb(c1)
    r2, g2, b2 = rgb(c2)
    grad = Image.linear_gradient("L")
    if horizontal:
        grad = grad.rotate(-90, expand=True)
    grad = grad.resize((w, h), Image.BILINEAR)
    chans = []
    for a, b in ((r1, r2), (g1, g2), (b1, b2)):
        chans.append(grad.point(lambda v, a=a, b=b: int(a + (b - a) * v / 255)))
    return Image.merge("RGB", tuple(chans))


class Canvas:
    """绘图画布。"""

    def __init__(self, width: int, height: int, bg: str = "#0B0E17", theme=None) -> None:
        self.width = int(width)
        self.height = int(height)
        self.bg = bg
        # 绑定主题后 text() 自动按 kind 解析文字样式（加粗/斜体/划线/描边/字体族）；
        # 不绑定（theme=None）则全部默认关闭，渲染行为与旧版逐像素一致。
        self.theme = theme
        self.img = Image.new("RGBA", (self.width, self.height), rgb(bg) + (255,))
        self.draw = ImageDraw.Draw(self.img)
        # 可交互区域：供主题编辑器在预览图上直接点选编辑
        self.regions: list[dict] = []
        # 页面底色热区：整个画布
        self.add_region("bg", [0, 0, self.width, self.height], label="页面底色", group="背景",
                        tip="点击修改页面底色")
        self._region_labels: dict[str, str] = {
            "bg": "页面底色", "card": "卡片底色", "card_border": "卡片边框",
            "card_shadow": "卡片阴影", "text": "正文", "text_sub": "次级文字",
            "text_weak": "弱文字", "accent": "主色", "accent2": "辅色",
            "accent_soft": "主色浅底", "track": "进度条轨道", "fill_start": "填充渐变起",
            "fill_end": "填充渐变末", "ok": "成功色", "warn": "警告色",
            "err": "危险色", "divider": "分割线", "bubble": "技能气泡", "aug_color": "集团卡气泡", "tbl": "列表",
            "title": "标题", "source": "来源小字",
        }

    # ---------------- 形状 ----------------
    def rounded_rect(
        self,
        box: Box,
        radius: int = 16,
        fill: str | None = None,
        outline: str | None = None,
        width: int = 1,
        fill_alpha: int = 255,
    ) -> "Canvas":
        """圆角矩形。`fill_alpha` < 255 时画半透明层（**卡片阴影**用，2026-09-07 新增）。"""
        x0, y0, x1, y1 = box
        w, h = int(x1 - x0), int(y1 - y0)
        if w <= 0 or h <= 0:
            return self
        if fill:
            # 半透明层必须 alpha_composite（像素级混合）才能露出底下背景图案/背景图：
            # paste+遮罩 是整像素替换，导出 convert(RGB) 丢 alpha 后变实心（2026-09-17 修复）
            layer = Image.new("RGBA", (w, h), rgb(fill) + (int(fill_alpha),))
            if radius > 0:
                from PIL import ImageChops
                mask = rounded_mask((w, h), radius)
                layer.putalpha(ImageChops.multiply(layer.split()[3], mask))
            self.img.alpha_composite(layer, (int(x0), int(y0)))
        if outline:
            # 描边单独一层：ImageDraw 的 outline 在圆角处宽度不均，用遮罩裁切更干净
            edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(edge).rounded_rectangle(
                [0, 0, w - 1, h - 1], radius=max(0, radius), outline=rgb(outline) + (255,), width=width
            )
            self.img.alpha_composite(edge, (int(x0), int(y0)))
        return self

    def gradient_rect(
        self,
        box: Box,
        c1: str,
        c2: str,
        radius: int = 16,
        horizontal: bool = True,
        outline: str | None = None,
    ) -> "Canvas":
        """渐变填充的圆角矩形（进度条/强调条用）。"""
        x0, y0, x1, y1 = box
        w, h = int(x1 - x0), int(y1 - y0)
        if w <= 0 or h <= 0:
            return self
        grad = linear_gradient((w, h), c1, c2, horizontal=horizontal).convert("RGBA")
        self.img.paste(grad, (int(x0), int(y0)), rounded_mask((w, h), radius))
        if outline:
            edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(edge).rounded_rectangle(
                [0, 0, w - 1, h - 1], radius=max(0, radius), outline=rgb(outline) + (255,), width=1
            )
            self.img.alpha_composite(edge, (int(x0), int(y0)))
        return self

    def rect(self, box: Box, fill: str) -> "Canvas":
        self.draw.rectangle(list(box), fill=rgb(fill) + (255,))
        return self

    def line(self, box: Sequence[int], fill: str, width: int = 1) -> "Canvas":
        self.draw.line(list(box), fill=rgb(fill) + (255,), width=width)
        return self

    def ellipse(self, box: Box, fill: str | None = None, outline: str | None = None, width: int = 1) -> "Canvas":
        self.draw.ellipse(
            list(box),
            fill=rgb(fill) + (255,) if fill else None,
            outline=rgb(outline) + (255,) if outline else None,
            width=width,
        )
        return self

    def paste(self, img: Image.Image, pos: Point, mask: Image.Image | None = None) -> "Canvas":
        self.img.paste(img, (int(pos[0]), int(pos[1])), mask)
        return self

    def paste_alpha(
        self, img: Image.Image, pos: Point, mask: Image.Image | None = None
    ) -> "Canvas":
        """真 alpha 混合贴图（保留图片自身半透明；mask 只削 alpha，不改 RGB）。

        与 paste() 的差别：paste 走 ``Image.paste``，是按 mask 做**线性替换**，
        源图透明区的 RGB（通常是被清零的黑）会被一起混进画布，
        圆形/圆角边缘因此出现一圈暗描边。这里把 mask 乘进 alpha 通道后
        再 ``alpha_composite``，让 RGB 保持直通（straight alpha），边缘不变暗。
        """
        src = img if img.mode == "RGBA" else img.convert("RGBA")
        if mask is not None:
            m = mask if mask.mode == "L" else mask.convert("L")
            if m.size != src.size:
                m = m.resize(src.size, Image.LANCZOS)
            src = src.copy()
            src.putalpha(ImageChops.multiply(src.getchannel("A"), m))
        x, y = int(pos[0]), int(pos[1])
        cw, ch = self.img.size
        if x < 0 or y < 0 or x + src.width > cw or y + src.height > ch:
            # 越界：先贴到整画布透明层（paste 会自动裁掉越界部分）再整体合成
            layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            layer.paste(src, (x, y))
            self.img = Image.alpha_composite(self.img, layer)
        else:
            self.img.alpha_composite(src, (x, y))
        return self

    # ---------------- 文本 ----------------
    def _text_style(self, kind: str) -> dict:
        """按文本角色（title/body/sub/badge）读取主题的文字样式开关。"""
        t = self.theme
        out = {"bold": False, "italic": False, "underline": False, "strike": False,
               "stroke": False, "stroke_color": "#000000"}
        if t is None:
            return out
        for k in ("bold", "italic", "underline", "strike", "stroke"):
            out[k] = bool(getattr(t, f"{kind}_{k}", False))
        out["stroke_color"] = str(getattr(t, f"{kind}_stroke_color", "#000000") or "#000000")
        return out

    def text(
        self,
        pos: Point,
        text: str,
        size: int = 20,
        color: str = "#EAF0FF",
        role: str = "regular",
        anchor: str = "la",
        kind: str | None = None,
    ) -> "Canvas":
        """绘制文本。anchor 语义同 Pillow（'la' 左上、'lm' 左中、'mm' 正中、'ra' 右上）。

        文字样式（2026-09-15，docs/文字样式功能设计.md §4.2）：
        kind=文本角色（title/body/sub/badge），缺省按字重 role 推断
        （bold→title、light→sub、regular→body）。管线：字体族 → 伪粗 → 描边 →
        绘制 → 划线；斜体无变体字体时走仿剪切慢路径（画到临时层再贴回）。
        """
        s_txt = emoji_safe(str(text))
        size = int(size)
        kind = kind or {"bold": "title", "light": "sub", "regular": "body"}.get(role, "body")
        st = self._text_style(kind)
        t = self.theme
        family = str(getattr(t, "font_family", "") or "") if t is not None else ""
        has_bold, has_italic = (False, False)
        if family:
            try:
                has_bold, has_italic = fonts.variant_flags(family)
            except Exception:
                has_bold = has_italic = False
        font = fonts.load_family(family, size, bold=st["bold"], italic=st["italic"])
        fill = rgb(color) + (255,)

        # 描边优先于伪粗（伪粗=同色细描边）
        sw = 0
        sfill = None
        if st["stroke"]:
            sw = max(1, size // 12)
            sfill = rgb(st["stroke_color"]) + (255,)
        elif st["bold"] and family and not has_bold:
            sw = max(1, size // 16)
            sfill = fill

        fake_italic = st["italic"] and (not family or not has_italic)

        if fake_italic:
            # 仿剪切：文本画进临时层 → AFFINE (1, k, -k·H) 使顶部右移 → 贴回。
            # 划线也画进临时层，随字一起斜（斜体下划线本就该斜）。
            k = 0.2
            pad = max(4, size // 2)
            probe = self.draw.textbbox((0, 0), s_txt, font=font, anchor="la", stroke_width=sw)
            w0 = probe[2] - probe[0] + 2 * pad
            h0 = probe[3] - probe[1] + 2 * pad
            layer = Image.new("RGBA", (max(1, w0), max(1, h0)), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            ld.text((pad - probe[0], pad - probe[1]), s_txt, font=font, fill=fill,
                    anchor="la", stroke_width=sw, stroke_fill=sfill)
            if st["underline"] or st["strike"]:
                lw = max(1, size // 14)
                wpx = ld.textlength(s_txt, font=font)
                lx0, lx1 = pad - probe[0], pad - probe[0] + wpx
                if st["strike"]:
                    yy = (probe[1] + probe[3]) // 2 + pad - probe[1]
                    ld.line([lx0, yy, lx1, yy], fill=fill, width=lw)
                if st["underline"]:
                    yy = probe[3] + max(1, size // 10) + pad - probe[1]
                    ld.line([lx0, yy, lx1, yy], fill=fill, width=lw)
            layer = layer.transform(
                layer.size, Image.AFFINE, (1, k, -k * layer.height, 0, 1, 0),
                resample=Image.BILINEAR,
            )
            tgt = self.draw.textbbox((int(pos[0]), int(pos[1])), s_txt, font=font, anchor=anchor)
            self.img.alpha_composite(layer, (int(tgt[0]) - pad, int(tgt[1]) - pad))
            return self

        self.draw.text((int(pos[0]), int(pos[1])), s_txt, font=font, fill=fill,
                       anchor=anchor, stroke_width=sw, stroke_fill=sfill)
        if st["underline"] or st["strike"]:
            lw = max(1, size // 14)
            try:
                wpx = self.draw.textlength(s_txt, font=font)
            except Exception:
                wpx = 0
            bb = self.draw.textbbox((int(pos[0]), int(pos[1])), s_txt, font=font,
                                    anchor=anchor, stroke_width=sw)
            x0 = bb[0]
            if st["strike"]:
                yy = (bb[1] + bb[3]) // 2
                self.draw.line([x0, yy, x0 + wpx, yy], fill=fill, width=lw)
            if st["underline"]:
                yy = bb[3] + max(1, size // 10)
                self.draw.line([x0, yy, x0 + wpx, yy], fill=fill, width=lw)
        return self

    @staticmethod
    def text_size(text: str, size: int = 20, role: str = "regular") -> tuple[int, int]:
        """文本宽高（含少量行距余量）。"""
        return _measure(str(text), int(size), role)

    @staticmethod
    def text_width(text: str, size: int = 20, role: str = "regular") -> int:
        return _measure(str(text), int(size), role)[0]

    @staticmethod
    def wrap(text: str, size: int, role: str, max_width: int) -> list[str]:
        """按最大宽度换行。中文逐字断行，英文/数字按空白断词。

        性能：旧实现对每个前缀 ``cur+ch`` 重新调 ``text_width``（整体 O(N²) 的 getbbox）；
        新实现对每个字符只取一次宽度（``text_width`` 按 (字符,字号,字重) 命中缓存），
        累加判断换行 → O(N)。CJK 字形无字距，逐字累加宽度与整串测量一致；
        拉丁文混排误差在卡片内边距容差内。断行逻辑与旧版逐字符一致。
        """
        if max_width <= 0:
            return [emoji_safe(str(text))]
        lines: list[str] = []
        for para in emoji_safe(str(text)).split("\n"):
            if not para:
                lines.append("")
                continue
            cur = ""
            cur_w = 0
            for ch in para:
                w = Canvas.text_width(ch, size, role)
                if cur_w and cur_w + w > max_width:
                    lines.append(cur)
                    cur = ch
                    cur_w = w
                else:
                    cur += ch
                    cur_w += w
            if cur:
                lines.append(cur)
        return lines

    @staticmethod
    def ellipsis(text: str, size: int, role: str, max_width: int) -> str:
        """超宽单行文本截断为 'xxx…'。"""
        text = emoji_safe(str(text))
        if Canvas.text_width(text, size, role) <= max_width:
            return text
        s = text
        while s and Canvas.text_width(s + "…", size, role) > max_width:
            s = s[:-1]
        return s + "…"

    # ---------------- 交互区域（主题编辑器预览图层） ----------------
    def add_region(
        self,
        field: str,
        bounds: Box,
        *,
        label: str | None = None,
        group: str | None = None,
        shape: str = "rect",
        tip: str | None = None,
        type: str | None = None,
    ) -> "Canvas":
        """记录一个可点击区域，供主题编辑器在预览图上直接编辑对应字段。

        :param field: theme 字段名（如 'card' / 'radius'），在 LAYOUT_FIELDS 内自动标为 layout。
        :param bounds: [x0, y0, x1, y1] 像素边界（左上角原点）。
        :param label: 中文标签；缺省时用内置简短标签或字段名本身。
        :param group: 分组（背景/文字/主色/形状…）；缺省时按字段语义推断。
        :param shape: rect / circle（circle 时 bounds 为外接正方形）。
        :param tip: hover 提示文案；缺省由 label + 字段名生成。
        :param type: 显式指定区域类型（'color'/'layout'…）；缺省时按字段名推断。
            调用方（如背景图案热区）可强制标为 'layout' 以便页面分组。
        """
        x0, y0, x1, y1 = (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
        if x1 <= x0 or y1 <= y0:
            return self
        rtype = type or _region_type(field)
        lab = label or self._region_labels.get(field) or field
        grp = group or ("形状" if rtype == "layout" else _region_group(field))
        self.regions.append({
            "field": field,
            "label": lab,
            "group": grp,
            "type": rtype,
            "shape": shape,
            "bounds": [x0, y0, x1, y1],
            "tip": tip or f"点击修改 {lab}（{field}）",
        })
        return self

    # ---------------- 导出 ----------------
    def to_bytes(self, fmt: str = "PNG") -> bytes:
        buf = BytesIO()
        # RGBA→RGB：半透明像素（卡片透明度/阴影）合成到页面底色，避免丢 alpha 变实心
        base = Image.new("RGBA", self.img.size, rgb(self.bg) + (255,))
        Image.alpha_composite(base, self.img).convert("RGB").save(buf, format=fmt, optimize=True)
        return buf.getvalue()

    def save(self, path: str) -> str:
        self.img.convert("RGB").save(path, format="PNG", optimize=True)
        return path


def fit_content_height(rows: Iterable[int], padding: int, gap: int) -> int:
    """按各行高度累加计算画布高度（供模板自适应高度使用）。"""
    rs = list(rows)
    if not rs:
        return padding * 2
    return int(padding * 2 + sum(rs) + gap * (len(rs) - 1))


def pct(value: float, total: float) -> int:
    """安全百分比（0~100 整数），total<=0 时为 0。"""
    if total <= 0:
        return 0
    return int(max(0, min(100, round(value * 100.0 / total))))


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def human_duration(seconds: float) -> str:
    """秒 → '1分23秒' / '45秒'（中文习惯，QQ 里比 '83s' 直观）。"""
    s = int(max(0, seconds))
    if s < 60:
        return "%d秒" % s
    m, sec = divmod(s, 60)
    if m < 60:
        return "%d分%02d秒" % (m, sec)
    h, m = divmod(m, 60)
    return "%d小时%02d分" % (h, m)


_SQRT2 = math.sqrt(2)


def scale(value: float, factor: float = 1.0) -> int:
    """按比例缩放并取整（避免布局出现 0 尺寸）。"""
    return max(1, int(round(value * factor)))
