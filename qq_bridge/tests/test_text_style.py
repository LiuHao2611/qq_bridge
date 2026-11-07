# -*- coding: utf-8 -*-
"""文字样式功能后端测试（docs/文字样式功能设计.md §7.1）。

覆盖：布局校验（缺省/非法色/注入）、字体族扫描与回退、
渲染像素差异（粗/描边/下划线/删除线/斜体）、默认样式逐像素回归。
运行：cd D:\\AI\\manju_agent\\qq_bridge && python -m pytest tests/test_text_style.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dataclasses import replace  # noqa: E402

from src.plugins.qq_bridge.pic import fonts  # noqa: E402
from src.plugins.qq_bridge.pic import widgets as W  # noqa: E402
from src.plugins.qq_bridge.pic.canvas import Canvas  # noqa: E402
from src.plugins.qq_bridge.pic.theme import NEON_THEME  # noqa: E402
from src.plugins.qq_bridge.pic.themes_store import validate_layout  # noqa: E402


# ---------------------------------------------------------------- 校验
def test_layout_defaults_off():
    out = validate_layout({})
    assert out["font_family"] == ""
    for r in ("title", "body", "sub", "badge"):
        for k in ("bold", "italic", "underline", "strike", "stroke"):
            assert out[f"{r}_{k}"] is False, f"{r}_{k} 默认应关"
        assert out[f"{r}_stroke_color"] == "#000000"


def test_layout_color_field():
    out = validate_layout({"title_stroke_color": "#FF8800"})
    assert out["title_stroke_color"] == "#FF8800"
    # 非法色值回落默认（str 类型按资源文件名校验会清空——color 类型必须用 _HEX）
    out2 = validate_layout({"title_stroke_color": "javascript:alert(1)"})
    assert out2["title_stroke_color"] == "#000000"


def test_layout_str_free_font_family():
    out = validate_layout({"font_family": "Microsoft YaHei"})
    assert out["font_family"] == "Microsoft YaHei"
    # 控制字符剥除 + 限长（防注入）
    out2 = validate_layout({"font_family": "a\x00b\nc"})
    assert "\x00" not in out2["font_family"] and "\n" not in out2["font_family"]


# ---------------------------------------------------------------- 字体族
def test_scan_families_structure_and_cache():
    fams1 = fonts.scan_families()
    assert isinstance(fams1, list)
    for r in fams1:
        assert set(r) >= {"family", "regular", "bold", "italic", "cjk"}
        assert r["regular"] or r["bold"] or r["italic"]
    fams2 = fonts.scan_families()
    assert fams1 is fams2, "二次调用应走缓存（同一对象）"


def test_load_family_fallbacks():
    # 空 family → 原回退链
    f0 = fonts.load_family("", 20)
    assert f0 is not None
    # 不存在的族 → 回退不抛异常
    f1 = fonts.load_family("不存在的字体族XYZ", 20)
    assert f1 is not None
    # 真实族（扫描结果第一个）加载成功
    fams = fonts.scan_families()
    if fams:
        f2 = fonts.load_family(fams[0]["family"], 24, bold=True, italic=True)
        assert f2 is not None


def test_variant_flags_consistent():
    fams = fonts.scan_families()
    if fams:
        hb, hi = fonts.variant_flags(fams[0]["family"])
        assert hb == (fams[0]["bold"] is not None)
        assert hi == (fams[0]["italic"] is not None)


# ---------------------------------------------------------------- 渲染差异
def _render(**kw):
    t = replace(NEON_THEME, **kw)
    c = Canvas(600, 300, t.bg, theme=t)
    W.card(c, t, (40, 40, 560, 260))
    c.text((70, 150), "本地硬件占用测试", size=36, color=t.text, role="bold", anchor="lm")
    return c.img.convert("RGB")


def test_default_style_matches_unbound_canvas():
    """默认（样式全关）渲染与不绑主题的旧路径**逐像素一致**（回归保护）。"""
    t = NEON_THEME
    c1 = Canvas(600, 300, t.bg, theme=t)
    W.card(c1, t, (40, 40, 560, 260))
    c1.text((70, 150), "本地硬件占用测试", size=36, color=t.text, role="bold", anchor="lm")
    c2 = Canvas(600, 300, t.bg)  # 旧路径：无 theme 绑定
    W.card(c2, t, (40, 40, 560, 260))
    c2.text((70, 150), "本地硬件占用测试", size=36, color=t.text, role="bold", anchor="lm")
    assert c1.img.tobytes() == c2.img.tobytes(), "默认样式下渲染必须与旧版逐像素一致"


def test_bold_changes_pixels():
    assert _render().tobytes() != _render(title_bold=True).tobytes()


def test_stroke_changes_pixels():
    assert _render().tobytes() != _render(title_stroke=True).tobytes()


def test_underline_changes_pixels():
    assert _render().tobytes() != _render(title_underline=True).tobytes()


def test_strike_changes_pixels():
    assert _render().tobytes() != _render(title_strike=True).tobytes()


def test_italic_changes_pixels():
    # 有无 Italic 变体都应产生差异（无变体走仿剪切慢路径）
    assert _render().tobytes() != _render(title_italic=True).tobytes()


def test_font_family_changes_pixels():
    fams = [f for f in fonts.scan_families() if f["cjk"]]
    if len(fams) >= 2:
        a = _render(font_family=fams[0]["family"])
        b = _render(font_family=fams[-1]["family"])
        assert a.tobytes() != b.tobytes(), "不同字体族渲染应不同"


def test_bad_font_family_falls_back():
    img = _render(font_family="绝不存在的字体XYZ")
    # 回退链渲染不崩溃，且非全空白
    colors = img.getcolors(maxcolors=1 << 20)
    assert colors and len(colors) > 1
