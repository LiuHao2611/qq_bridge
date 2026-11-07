# -*- coding: utf-8 -*-
"""P1 验收：控件层（卡片/标题/进度条/菜单行）与磁盘缓存（TTL + LRU）。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_pic_widgets.py -v
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge.pic import Canvas, cache as cache_mod, icons, theme, widgets  # noqa: E402
from src.plugins.qq_bridge.pic.theme import LIGHT, NEON, rgb  # noqa: E402

W = 800
PREVIEW_DIR = os.path.join(ROOT, "data", "pic_preview")


def _non_bg_count(c: Canvas, bg_rgb, box=None, tol: int = 30) -> int:
    """统计非背景色像素数（用于确认「确实画上了东西」）。

    用 getcolors 而非 getdata —— 后者在 Pillow 14 起被移除。
    """
    img = c.img.convert("RGB")
    if box:
        img = img.crop(box)
    bg = rgb(bg_rgb)
    n = 0
    for count, px in (img.getcolors(maxcolors=1_000_000) or []):
        if abs(px[0] - bg[0]) + abs(px[1] - bg[1]) + abs(px[2] - bg[2]) > tol:
            n += count
    return n


def _color_count(c: Canvas, target_rgb, box=None, tol: int = 40) -> int:
    """统计接近指定颜色的像素数（用于确认「某元素确实画上了」）。"""
    img = c.img.convert("RGB")
    if box:
        img = img.crop(box)
    tgt = rgb(target_rgb)
    n = 0
    for count, px in (img.getcolors(maxcolors=1_000_000) or []):
        if abs(px[0] - tgt[0]) + abs(px[1] - tgt[1]) + abs(px[2] - tgt[2]) <= tol:
            n += count
    return n


# ---------------------------------------------------------------- 图标
@pytest.mark.parametrize("kind", list(icons.ICONS))
def test_all_icons_render(kind):
    """每个图标都必须真的画出像素（缺字形/空绘制会导致 0）。"""
    t = theme.get(NEON)
    c = Canvas(80, 80, t.bg)
    icons.draw(c, kind, 40, 40, 48, t.accent)
    assert _non_bg_count(c, t.bg) > 100, "图标未绘制: %s" % kind


def test_unknown_icon_falls_back():
    t = theme.get(NEON)
    c = Canvas(80, 80, t.bg)
    icons.draw(c, "不存在的图标", 40, 40, 48, t.accent)
    assert _non_bg_count(c, t.bg) > 100
    assert icons.exists("circle") and not icons.exists("nope")


def test_icon_color_applied():
    t = theme.get(NEON)
    c = Canvas(80, 80, t.bg)
    icons.draw(c, "circle", 40, 40, 60, t.accent)
    px = c.img.convert("RGB").load()
    assert px[40, 40] == rgb(t.accent), "图标颜色未生效: %s" % (px[40, 40],)


# ---------------------------------------------------------------- 控件
def test_card_and_header():
    t = theme.get(NEON)
    c = Canvas(W, 200, t.bg)
    widgets.card(c, t, (24, 24, W - 24, 176))
    y = widgets.header(c, t, 48, 60, "本喵能干啥", subtitle="直接说人话就行", right="8 项")
    assert y > 60
    assert _non_bg_count(c, t.bg) > 5000


def test_progress_bar_widget_ratio():
    """进度条填充占比 ≈ percent（±3%）。"""
    t = theme.get(NEON)
    x, w, h = 48, 600, widgets.BAR_H
    for pct in (0, 33, 42, 75, 100):
        c = Canvas(700, 120, t.bg)
        y0 = 40
        widgets.progress_bar(c, t, x, y0, w, pct)
        px = c.img.convert("RGB").load()
        track = rgb(t.track)
        filled = sum(1 for xx in range(x, x + w)
                     if _far(px[xx, y0 + h // 2], track))
        assert abs(filled * 100.0 / w - pct) <= 3, "percent=%d 实测=%.1f%%" % (pct, filled * 100.0 / w)


def test_progress_bar_clamps():
    t = theme.get(NEON)
    c = Canvas(700, 100, t.bg)
    widgets.progress_bar(c, t, 40, 30, 600, 150)   # 超限应夹到 100
    widgets.progress_bar(c, t, 40, 60, 600, -20)   # 负数应夹到 0
    px = c.img.convert("RGB").load()
    track = rgb(t.track)
    full = sum(1 for x in range(40, 640) if _far(px[x, 39], track))
    assert full > 590, "超限未夹到 100%%: %d" % full
    zero = sum(1 for x in range(40, 640) if _far(px[x, 69], track))
    assert zero == 0, "负数未夹到 0: %d" % zero


def test_menu_row_layout():
    """菜单行：不越界、高度固定为 ROW_H、图标与文字都画上了。"""
    t = theme.get(LIGHT)
    c = Canvas(W, 300, t.bg)
    widgets.card(c, t, (24, 24, W - 24, 276))
    y = widgets.menu_row(c, t, 48, 48, W - 96, "wand", "生图", "发一句话，本喵给你画出来")
    assert y == 48 + widgets.ROW_H
    assert y < 276, "菜单行越出卡片"
    # 图标区（左侧 48~88）必须画出主色图标像素
    assert _color_count(c, t.accent, (48, 48, 88, 48 + widgets.ROW_H)) > 40
    # 徽章底色与卡片色必须有可辨识对比（浅色主题曾因对比过弱导致徽章看不见）
    assert _far(rgb(t.accent_soft), rgb(t.card), 30), "图标徽章底色与卡片对比过弱"


def test_menu_row_title_truncated():
    """超长标题必须被截断（省略号），不得溢出卡片。"""
    t = theme.get(NEON)
    c = Canvas(W, 200, t.bg)
    widgets.menu_row(c, t, 48, 40, W - 96, "image", "超长标题" * 30, "描述")
    # 卡片右边界外一列应无内容
    outside = _non_bg_count(c, t.bg, (W - 20, 40, W - 1, 40 + widgets.ROW_H))
    assert outside == 0, "内容溢出到卡片外: %d px" % outside


def test_kv_divider_footer_badge():
    t = theme.get(NEON)
    c = Canvas(W, 240, t.bg)
    widgets.card(c, t, (24, 24, W - 24, 216))
    y = widgets.kv_row(c, t, 48, 60, W - 96, "今日已用", "12 / 50")
    y = widgets.divider(c, t, 48, W - 48, y)
    y = widgets.footer(c, t, 48, y, "本喵下班了，明天再来吧")
    assert y < 216
    bw = widgets.badge(c, t, 48, 190, "本地 ComfyUI")
    assert bw > 0


# ---------------------------------------------------------------- 缓存
def test_cache_key_stable():
    a = cache_mod.make_key("menu", "neon", {"a": 1, "b": 2})
    b = cache_mod.make_key("menu", "neon", {"b": 2, "a": 1})   # 顺序无关
    c = cache_mod.make_key("menu", "light", {"a": 1, "b": 2})  # 主题不同
    assert a == b
    assert a != c
    assert len(a) == 32


def test_cache_hit_and_miss(tmp_path):
    cache = cache_mod.PicCache(directory=tmp_path, ttl=60, max_files=10)
    k = cache_mod.make_key("menu", "neon", {"v": 1})
    assert cache.get(k) is None
    cache.put(k, b"\x89PNG-fake-bytes")
    assert cache.get(k) == b"\x89PNG-fake-bytes"
    assert cache.stats()["count"] == 1


def test_cache_ttl_expire(tmp_path):
    cache = cache_mod.PicCache(directory=tmp_path, ttl=1, max_files=10)
    k = cache_mod.make_key("quota", "neon", {"v": 2})
    cache.put(k, b"\x89PNG-x")
    assert cache.get(k) is not None
    time.sleep(1.2)
    assert cache.get(k) is None, "TTL 未生效"
    assert cache.stats()["count"] == 0, "过期条目未惰性删除"


def test_cache_lru_evict(tmp_path):
    cache = cache_mod.PicCache(directory=tmp_path, ttl=600, max_files=3)
    keys = [cache_mod.make_key("t", "neon", {"i": i}) for i in range(5)]
    for i, k in enumerate(keys):
        cache.put(k, b"\x89PNG-%d" % i)
    stats = cache.stats()
    assert stats["count"] <= 3, "LRU 未淘汰: %s" % stats
    assert cache.get(keys[-1]) is not None, "最新条目不应被淘汰"
    assert cache.get(keys[0]) is None, "最旧条目应已被淘汰"


def test_cache_corrupt_index_recovers(tmp_path):
    """索引文件损坏时必须当作空缓存，绝不能影响出图。"""
    cache = cache_mod.PicCache(directory=tmp_path, ttl=60, max_files=5)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "index.json").write_text("{坏掉的 JSON", encoding="utf-8")
    assert cache.get("whatever") is None
    cache.put(cache_mod.make_key("t", "neon", {}), b"\x89PNG-ok")
    assert cache.stats()["count"] == 1


def test_cache_clear(tmp_path):
    cache = cache_mod.PicCache(directory=tmp_path, ttl=60, max_files=5)
    for i in range(3):
        cache.put(cache_mod.make_key("t", "neon", {"i": i}), b"\x89PNG")
    assert cache.clear() == 3
    assert cache.stats()["count"] == 0


# ---------------------------------------------------------------- 预览图（人工确认）
def test_generate_menu_preview():
    """产出两套主题的完整菜单预览图，供人工确认观感。"""
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    items = [
        ("wand", "生图", "发一句话，本喵给你画出来"),
        ("image", "改图", "先发张图，再说怎么改"),
        ("arrow_up", "高清放大", "把糊图放大变清晰"),
        ("square", "扩图", "往左/右/上/下扩一块"),
        ("triangle", "视频", "还没开通，别急"),
        ("question", "帮助", "把这份清单再发一遍"),
    ]
    paths = []
    for name in (NEON, LIGHT):
        t = theme.get(name)
        h = 24 + 96 + len(items) * widgets.ROW_H + 64
        c = Canvas(W, h + 24, t.bg)
        widgets.card(c, t, (24, 24, W - 24, h))
        y = widgets.header(c, t, 48, 64, "本喵能干啥", subtitle="直接说人话就行，别老打斜杠", right="6 项")
        y += 8
        for icon, title, desc in items:
            y = widgets.menu_row(c, t, 48, y, W - 96, icon, title, desc)
        widgets.footer(c, t, 48, h - 28, "闲聊免开尊口，要图直说就完事儿喵~")
        p = os.path.join(PREVIEW_DIR, "menu_%s.png" % name)
        c.save(p)
        paths.append(p)
    for p in paths:
        assert os.path.isfile(p) and os.path.getsize(p) > 2000, "预览图生成失败: %s" % p
    print("\n预览图：\n  " + "\n  ".join(paths))


def _far(a, b, tol: int = 30) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > tol
