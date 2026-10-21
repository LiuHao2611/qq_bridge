# -*- coding: utf-8 -*-
"""P0 验收：Pillow 绘图地基（中文字体 / 画布 / 双主题 / 圆角 / 渐变进度条）。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_pic_canvas.py -v

说明：
  * 全部断言机器判定（非人工读图），覆盖规划文档第七节的 1~5、7 项；
  * `test_generate_preview` 额外产出两张预览图到 data/pic_preview/，供人工确认观感。
"""
from __future__ import annotations

import os
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge.pic import Canvas, fonts, theme  # noqa: E402
from src.plugins.qq_bridge.pic.theme import AUTO, LIGHT, NEON, rgb  # noqa: E402

PREVIEW_DIR = os.path.join(ROOT, "data", "pic_preview")
W = 800


# ---------------------------------------------------------------- 字体
def test_cjk_font_available():
    """系统中文字体必须可用，否则中文会渲染成豆腐块。"""
    assert fonts.has_cjk_font(), "未找到任何中文字体（msyh/simhei/simsun）"
    for role in ("regular", "bold"):
        p = fonts.font_path(role)
        assert p and os.path.isfile(p), "字体缺失: %s" % role


def test_cjk_text_has_real_glyph():
    """中文必须是等宽全角字形：4 字宽度 ≈ 单字宽度 × 4（豆腐块同样等宽，再验字形非空）。"""
    w1 = Canvas.text_width("好", 24, "regular")
    w4 = Canvas.text_width("好好好好", 24, "regular")
    assert w1 > 0
    assert abs(w4 - w1 * 4) <= 2, "中文非等宽，字体异常: %d vs %d" % (w4, w1 * 4)
    # 豆腐块是空心方框，仍有宽度；用「渲染像素数」确认真的画出了字形
    c = Canvas(120, 60, "#FFFFFF")
    c.text((10, 30), "好", size=40, color="#000000", anchor="lm")
    dark = sum(c.img.convert("L").histogram()[:128])  # histogram 替代 getdata（Pillow 14 将移除）
    assert dark > 50, "中文字形像素过少（疑似豆腐块/未渲染）: %d" % dark


# ---------------------------------------------------------------- 画布
def test_canvas_export_png():
    c = Canvas(W, 400, "#0B0E17")
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "导出不是 PNG"
    img = Image.open(__import__("io").BytesIO(data))
    assert img.size == (W, 400)


def test_rounded_card_corners():
    """圆角卡片：中心是卡片色，四角仍是背景色。"""
    t = theme.get(NEON)
    c = Canvas(400, 200, t.bg)
    c.rounded_rect((20, 20, 380, 180), radius=24, fill=t.card)
    px = c.img.convert("RGB").load()
    # 圆角外（左上角 2,2）应为背景；中心应为卡片色
    assert px[2, 2] == rgb(t.bg), "圆角未生效，角落被填充: %s" % (px[2, 2],)
    assert px[200, 100] == rgb(t.card), "卡片中心颜色不对: %s" % (px[200, 100],)


def test_border_drawn():
    t = theme.get(NEON)
    c = Canvas(200, 120, t.bg)
    c.rounded_rect((10, 10, 190, 110), radius=16, fill=t.card, outline=t.card_border, width=2)
    px = c.img.convert("RGB").load()
    border_hits = sum(
        1 for x in range(10, 190) if px[x, 11] == rgb(t.card_border)
    )
    assert border_hits > 100, "描边未绘制: %d" % border_hits


# ---------------------------------------------------------------- 主题
def test_two_themes_differ():
    a, b = theme.get(NEON), theme.get(LIGHT)
    assert rgb(a.bg) != rgb(b.bg)
    assert rgb(a.text) != rgb(b.text)


def test_theme_pixel_applied():
    """两套主题渲染出的画布背景像素必须分别是各自色板值。"""
    for name in (NEON, LIGHT):
        t = theme.get(name)
        c = Canvas(100, 60, t.bg)
        assert c.img.convert("RGB").load()[1, 1] == rgb(t.bg)


def test_auto_theme_by_hour():
    import datetime as dt

    assert theme.resolve(AUTO, now=dt.datetime(2026, 9, 7, 22, 0)).name == NEON
    assert theme.resolve(AUTO, now=dt.datetime(2026, 9, 7, 12, 0)).name == LIGHT


def test_unknown_theme_falls_back():
    assert theme.get("不存在的主题").name == NEON


# ---------------------------------------------------------------- 渐变进度条
def _draw_bar(c: Canvas, t, x0: int, y0: int, w: int, h: int, percent: int):
    c.rounded_rect((x0, y0, x0 + w, y0 + h), radius=h // 2, fill=t.track)
    fill_w = max(0, int(w * percent / 100))
    if fill_w > 2:
        c.gradient_rect((x0, y0, x0 + fill_w, y0 + h), t.fill_start, t.fill_end, radius=h // 2)


def test_gradient_progress_ratio():
    """进度条填充占比必须≈给定百分比（±3%），这是进度图可信度的核心断言。"""
    t = theme.get(NEON)
    x0, y0, bw, bh = 40, 40, 600, 24
    for percent in (0, 25, 42, 100):
        c = Canvas(700, 120, t.bg)
        _draw_bar(c, t, x0, y0, bw, bh, percent)
        px = c.img.convert("RGB").load()
        track = rgb(t.track)
        filled = sum(1 for x in range(x0, x0 + bw) if _far(px[x, y0 + bh // 2], track))
        ratio = filled * 100.0 / bw
        assert abs(ratio - percent) <= 3, "percent=%d 实测占比=%.1f%%" % (percent, ratio)


def _far(a, b, tol: int = 30) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > tol


def test_gradient_is_smooth():
    """渐变条左右两端颜色必须不同（证明不是纯色填充）。"""
    t = theme.get(NEON)
    c = Canvas(400, 60, t.bg)
    c.gradient_rect((10, 10, 390, 50), t.fill_start, t.fill_end, radius=20)
    px = c.img.convert("RGB").load()
    left, right = px[20, 30], px[380, 30]
    assert _far(left, right, 60), "渐变未生效: %s vs %s" % (left, right)


# ---------------------------------------------------------------- 预览图（人工确认用）
def test_generate_preview():
    """产出两套主题的进度条预览图，供人工确认观感（文件非空即为通过）。"""
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    paths = []
    for name in (NEON, LIGHT):
        t = theme.get(name)
        c = Canvas(W, 300, t.bg)
        c.rounded_rect((24, 24, W - 24, 276), radius=22, fill=t.card, outline=t.card_border, width=2)
        c.text((48, 62), "生图任务进度", size=28, color=t.text, role="bold", anchor="lm")
        c.text((W - 48, 62), "42%", size=28, color=t.accent, role="bold", anchor="rm")
        _draw_bar(c, t, 48, 92, W - 96, 20, 42)
        c.text((48, 150), "已等待 1分23秒 · 预计还需 2分钟", size=20, color=t.text_sub, anchor="lm")
        c.text((48, 196), "提示词：赛博朋克风格的猫，霓虹灯，雨夜", size=20, color=t.text_sub, anchor="lm")
        c.text((48, 246), "本喵正在给你画，急也没用喵~", size=18, color=t.text_weak, anchor="lm")
        p = os.path.join(PREVIEW_DIR, "progress_%s.png" % name)
        c.save(p)
        paths.append(p)
    for p in paths:
        assert os.path.isfile(p) and os.path.getsize(p) > 1000, "预览图生成失败: %s" % p
    # 打印路径，方便人工打开
    print("\n预览图：\n  " + "\n  ".join(paths))


# ---- emoji 豆腐框修复（2026-10-21，/人设 列表真机实证）----
def test_emoji_safe_strips_unrenderable_keeps_bmp_symbols():
    """中文字体画不出的 emoji 必须删（.notdef 豆腐框），画得出的 BMP 符号必须留。"""
    from src.plugins.qq_bridge.pic.canvas import emoji_safe
    s = "人设：★ ☆ → ※ 🐱 🦇 🤖"
    out = emoji_safe(s)
    # 增补平面 emoji 一律删
    for ch in ("🐱", "🦇", "🤖"):
        assert ch not in out
    # 变体选择符 / ZWJ 一律删
    assert "\ufe0f" not in emoji_safe("🕯️") and "\u200d" not in emoji_safe("👨‍👩‍👧")
    # 雅黑有字形的 BMP 符号必须保留（★ 在旧卡片里一直正常显示）
    assert "★" in out and "☆" in out and "→" in out and "※" in out
    # 普通文本原样
    assert emoji_safe("本喵 喵~ 123abc") == "本喵 喵~ 123abc"
    assert emoji_safe("") == ""


def test_emoji_safe_measure_draw_consistent():
    """测量与绘制用同一净化：含 emoji 文本的测量宽度 == 删 emoji 后的测量宽度。"""
    from src.plugins.qq_bridge.pic.canvas import Canvas, emoji_safe
    s = "★ 本喵 🐱"
    assert Canvas.text_width(s, 20) == Canvas.text_width(emoji_safe(s), 20)
