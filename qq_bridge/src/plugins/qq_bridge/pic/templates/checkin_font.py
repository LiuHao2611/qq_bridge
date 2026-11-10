# -*- coding: utf-8 -*-
"""签到卡专用分层字体层（2026-11-10「字体太简单，该粗则粗、不同字用不同字体」）。

问题背景（真实源码坑，见 canvas.py）：``Canvas.text()`` 绘制走
``fonts.load_family(theme.font_family,...)``，但 ``Canvas.text_size/_measure``
度量走 ``fonts.load(role,size)``（**不读 font_family**）——两者字体决策分裂。
全局改 fonts.py/canvas.py 会波及所有卡片模板，故签到卡自带一套「绘制+度量同源」
的字体层，只作用于签到卡，不碰全局映射。

分层（三款真实风格字体，子集与来源见 assets/checkin_fonts/README.md）：
  * ``display`` 马善政毛笔板书 —— 主标题「签到」（size>=36）
  * ``section`` 龙藏硬笔行楷 —— 四个分区标题（size==SECTION_SIZE=20）
  * ``heavy``  站酷快乐体圆润粗 —— 所有 role="bold" 数值/强调（伪粗 stroke 加字重）
  * ``body``   站酷快乐体圆润 —— 其余正文/标签/月历/页脚

**同源保证**：一段文字先经 ``resolve(text,size,role)`` 定唯一 ImageFont
（整串缺任一字形 -> 整串回退 vendored 雅黑，绝不逐字混排导致字宽错位），
绘制 ``wrap(c).text`` 与度量 ``measure/width`` 用**同一个** font 对象。
``checkin_parts._w/_tb`` 与 ``checkin.py`` 的数值盒高度都改调本模块，杜绝
「测量按雅黑、绘制按风格字」的错位（card-design 红线 6）。

不 import nonebot/上级业务包，可独立单测。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont
from PIL.ImageFont import FreeTypeFont

from .. import fonts as _sysfonts
from ..canvas import Canvas, rgb, emoji_safe

log = logging.getLogger("qq_bridge.pic.checkin_font")

_ASSETS = Path(__file__).resolve().parent / "assets" / "checkin_fonts"
FONT_FILES = {               # 逻辑角色 -> 子集字体文件（heavy/body 同文件，伪粗在绘制层区分）
    "display": "display.ttf",
    "section": "section.ttf",
    "heavy": "heavy.ttf",
    "body": "body.ttf",
}
DISPLAY_MIN = 36             # size>=36 -> 毛笔主标题
SECTION_SIZE = 20            # size==20 -> 行楷分区标题（== checkin_parts.FS_SEC）
ROLE_LABEL = {
    "display": "毛笔板书主标题", "section": "硬笔行楷分区标题",
    "heavy": "圆润粗体数值", "body": "圆润正文",
}
_FALLBACK_PATH = (Path(__file__).resolve().parents[1] / "game_tools" / "modcard"
                  / "assets" / "msyh.ttc")   # vendored 雅黑，已随 COPY src 进镜像
_missing: set[str] = set()
_fallback_logged: set[str] = set()


def route(size: int, role: str) -> str:
    """按字号/字重分流到四款逻辑字体之一（gate/测试读它校验分层）。"""
    size = int(size)
    if size >= DISPLAY_MIN:
        return "display"
    if size == SECTION_SIZE:
        return "section"
    return "heavy" if role == "bold" else "body"


@lru_cache(maxsize=512)
def _fallback_font(size: int) -> FreeTypeFont:
    if _FALLBACK_PATH.is_file():
        try:
            return ImageFont.truetype(str(_FALLBACK_PATH), int(size), index=0)
        except Exception:  # noqa: BLE001
            pass
    return _sysfonts.load("regular", int(size))


def _has_all_glyphs(font: FreeTypeFont, text: str) -> bool:
    probe = bytes(font.getmask("\u0378"))     # 未分配码位 = .notdef
    return all(bytes(font.getmask(ch)) != probe for ch in set(text) if not ch.isspace())


@lru_cache(maxsize=2048)
def resolve(text: str, size: int, role: str) -> tuple[FreeTypeFont, str, bool]:
    """整串唯一字体决策：返回 (font, 逻辑角色, 是否回退雅黑)。

    子集字体缺任一字形 -> 整串回退雅黑（典型：动态昵称含子集外字）。
    绝不逐字混排，否则 getlength 累加与整串 shaping 不一致、度量会错位。
    """
    size = int(size)
    logical = route(size, role)
    name = FONT_FILES[logical]
    p = _ASSETS / name
    font = None
    if p.is_file():
        try:
            cand = ImageFont.truetype(str(p), size)
            if _has_all_glyphs(cand, text):
                return cand, logical, False
            font = cand                       # 有字体但缺字 -> 下面回退
        except Exception as e:  # noqa: BLE001
            if name not in _missing:
                _missing.add(name)
                log.warning("checkin_font_load_failed: %s (%s)", p, e)
    else:
        if name not in _missing:
            _missing.add(name)
            log.warning("checkin_font_missing: %s（请运行 tools/build_checkin_assets.py）", p)
    key = "%s/%s" % (logical, text[:16])
    if key not in _fallback_logged:
        _fallback_logged.add(key)
        log.warning("checkin_font_fallback: [%s] %r 子集缺字，整串回退雅黑", logical, text[:24])
    return _fallback_font(size), logical, True


def _stroke(size: int, role: str, logical: str, fallback: bool) -> int:
    """伪粗：圆润体无独立 bold 字形时用同色描边加字重；毛笔/行楷靠自身笔锋不加。
    回退雅黑时雅黑 msyh.ttc 的 bold 由 index 决定，这里统一不描边，避免过重。"""
    if fallback or role != "bold":
        return 0
    if logical in ("heavy", "body"):
        return max(1, int(size) // 14)
    return 0


def _safe(text: str) -> str:
    return emoji_safe(str(text))


# ---------------------------------------------------------------- 同源度量
@lru_cache(maxsize=8192)
def measure(text: str, size: int, role: str = "regular") -> tuple[int, int]:
    """文本 (宽, 高)：与绘制整串同一 font 的 getlength + getbbox，含伪粗补偿。"""
    s = _safe(text)
    if not s:
        return 0, int(size)
    font, logical, fb = resolve(s, int(size), role)
    sw = _stroke(int(size), role, logical, fb)
    w = int(round(font.getlength(s))) + 2 * sw
    box = font.getbbox(s)
    h = int(box[3] - box[1]) + 2 * sw
    return w, max(1, h)


@lru_cache(maxsize=8192)
def width(text: str, size: int, role: str = "regular") -> int:
    return measure(text, size, role)[0]


def size(text: str, font_size: int, role: str = "regular") -> tuple[int, int]:
    """与 Canvas.text_size 同签名，便于 checkin_parts/模板直接替换调用。"""
    return measure(text, font_size, role)


def baseline(size: int, role: str = "regular") -> int:
    """该字体的 ascender（anchor='la' 时 y 到字顶的距离），供 mm/lm 盒定位校正。"""
    return int(_font_metrics(size, role)[0])


@lru_cache(maxsize=256)
def _font_metrics(size: int, role: str) -> tuple[int, int]:
    f, _, _ = resolve("永", int(size), role)   # 用固定字取度量，与具体文本无关
    a, d = f.getmetrics()
    return a, d


# ---------------------------------------------------------------- 绘制代理
class _StyledCanvas:
    """Canvas 实例代理：只覆写 text/text_size/text_width，其余 __getattr__ 透传。

    ``wrap(c)`` 后模板内 ``c.text(...)`` 自动走分层字体；
    ``c.rounded_rect/line/paste_alpha/img/save/add_region`` 等原样透传，
    ``icons.draw(c,..)/gold_icons.draw(c,..)`` 直接用 c.img/c.paste 不受影响。
    """

    __slots__ = ("_c",)

    def __init__(self, canvas: Canvas) -> None:
        object.__setattr__(self, "_c", canvas)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_c"), name)

    def text(self, pos, text: str, size: int = 20, color: str = "#EAF0FF",
             role: str = "regular", anchor: str = "la", kind: str | None = None):
        """整串同一 font 绘制（保留 Pillow 原生 anchor 语义）+ 伪粗描边。"""
        c = object.__getattribute__(self, "_c")
        s = _safe(text)
        font, logical, fb = resolve(s, int(size), role)
        sw = _stroke(int(size), role, logical, fb)
        c.draw.text((int(pos[0]), int(pos[1])), s, font=font, fill=rgb(color) + (255,),
                    anchor=anchor, stroke_width=sw,
                    stroke_fill=rgb(color) + (255,) if sw else None)
        return c

    def text_size(self, text: str, size: int = 20, role: str = "regular"):
        return size(text, size, role)

    def text_width(self, text: str, size: int = 20, role: str = "regular") -> int:
        return width(text, size, role)


def wrap(canvas: Canvas) -> _StyledCanvas:
    """把签到卡 Canvas 包成分层字体代理（幂等：已代理则原样返回）。"""
    if isinstance(canvas, _StyledCanvas):
        return canvas
    return _StyledCanvas(canvas)


# ---------------------------------------------------------------- 交付闸门
def gate(strict: bool = True) -> list[str]:
    """校验四款子集字体存在且可加载；缺字体 strict 直接 SystemExit。"""
    miss = [n for n in FONT_FILES.values() if not (_ASSETS / n).is_file()]
    if miss:
        err = "签到卡风格字体缺失：%s；请运行 python tools/build_checkin_assets.py" % ", ".join(miss)
        if strict:
            raise SystemExit(err)
        log.warning(err)
    return miss
