# -*- coding: utf-8 -*-
"""P2 验收：模板层（menu / progress / quota）+ 统一 render 入口 + 缓存联动。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_pic_templates.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge.pic import (  # noqa: E402
    PicRenderError, list_templates, render, render_sync, theme,
)
from src.plugins.qq_bridge.pic.theme import LIGHT, NEON, rgb  # noqa: E402

PREVIEW_DIR = os.path.join(ROOT, "data", "pic_preview")


def _run(coro):
    """同步跑协程（conftest 的 run 是普通函数不是 fixture，这里本地实现一份）。"""
    return asyncio.run(coro)


def _open(data: bytes) -> Image.Image:
    return Image.open(__import__("io").BytesIO(data))


def _color_count(data: bytes, target: str, tol: int = 40) -> int:
    img = _open(data).convert("RGB")
    tgt = rgb(target)
    n = 0
    for count, px in (img.getcolors(maxcolors=1_000_000) or []):
        if abs(px[0] - tgt[0]) + abs(px[1] - tgt[1]) + abs(px[2] - tgt[2]) <= tol:
            n += count
    return n


# ---------------------------------------------------------------- 注册与参数
def test_list_templates():
    assert set(list_templates()) >= {"menu", "progress", "quota"}


def test_unknown_template_raises():
    with pytest.raises(PicRenderError):
        render_sync("不存在的模板")


# ---------------------------------------------------------------- menu
def test_menu_png_and_height_grows():
    """高度随条目数线性增长（用受控条目验证，避免把内容变更与布局耦合）。"""
    t = theme.get(NEON)
    from src.plugins.qq_bridge.pic.templates import menu

    one = menu.build(t, items=[("wand", "生图", "发一句话")])
    four = menu.build(t, items=[("wand", "生图", "发一句话")] * 4)
    assert one.to_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert four.height - one.height == 3 * 64, "菜单高度应随条目数线性增长"


def test_menu_owner_taller_than_general():
    """主人菜单 = 一般菜单 + 主人专属段 → 必须更高，且两个默认内容都不为空。"""
    t = theme.get(NEON)
    from src.plugins.qq_bridge.pic.templates import menu

    assert menu.GENERAL_ITEMS and menu.OWNER_ITEMS
    g = menu.build(t, items=menu.GENERAL_ITEMS, owner=False)
    o = menu.build(t, items=list(menu.GENERAL_ITEMS) + list(menu.OWNER_ITEMS), owner=True)
    assert o.height > g.height, "主人菜单应比一般菜单高（多一段主人专属）"


def test_menu_parse_items():
    """配置页的高级条目字符串解析：条目/分组/非法图标回退。"""
    from src.plugins.qq_bridge.pic.templates import menu

    it = menu.parse_items("wand|生图|发一句话;#任务与作品;clock|我的任务|查进度;bogus|坏图标|x")
    assert it[0] == {"icon": "wand", "title": "生图", "desc": "发一句话"}
    assert it[1] == {"section": "任务与作品"}
    assert it[2]["icon"] == "clock"
    assert it[3]["icon"] == "circle", "非法图标应回退为 circle，不能崩"
    # 空/纯空白 → None（调用方回退内置默认）
    assert menu.parse_items("") is None
    assert menu.parse_items("   ") is None


def test_menu_config_custom_text_and_items(monkeypatch):
    """配置页文案/条目自定义必须生效（帮助菜单图内容可改，不用改代码）。"""
    from src.plugins.qq_bridge.config import Config
    from src.plugins.qq_bridge.pic.templates import menu

    monkeypatch.setattr(Config, "PIC_MENU_TITLE", "自定义标题")
    monkeypatch.setattr(Config, "PIC_MENU_ITEMS", "star|我的图|看图;query|我的任务|查进度")
    items = menu.parse_items(Config.PIC_MENU_ITEMS)
    # 格式为 `icon|标题|描述`
    assert [x.get("title") for x in items if "title" in x] == ["我的图", "我的任务"]
    assert items[0]["desc"] == "看图"
    assert items[0]["icon"] == "star" and items[1]["icon"] == "circle", "非法图标回退 circle"
    t = theme.get(NEON)
    c = menu.build(t, items=items, title=Config.PIC_MENU_TITLE)
    assert c.height > 0


# ---------------------------------------------------------------- progress
def test_progress_png_and_bar_drawn():
    t = theme.get(NEON)
    from src.plugins.qq_bridge.pic.templates import progress

    c = progress.build(t, percent=42, elapsed=83, kind="t2i", prompt="赛博朋克风格的猫")
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # 进度条（accent 渐变）与提示词都画上了
    assert _color_count(data, t.accent, tol=60) > 500
    assert progress.from_elapsed(300, 1800) == 17  # 300/1800 ≈ 17%


def test_progress_state_color():
    """异常状态用 warn/err 色，不能永远是主色。"""
    t = theme.get(NEON)
    from src.plugins.qq_bridge.pic.templates import progress

    err = progress.build(t, percent=90, elapsed=1700, state="err").to_bytes()
    assert _color_count(err, t.err, tol=60) > 200


# ---------------------------------------------------------------- quota
def test_quota_ring_and_danger_color():
    t = theme.get(NEON)
    from src.plugins.qq_bridge.pic.templates import quota

    low = quota.build(t, used=10, quota=50).to_bytes()
    high = quota.build(t, used=50, quota=50).to_bytes()
    assert low[:8] == b"\x89PNG\r\n\x1a\n"
    # 用满（100%）必须出现危险色
    assert _color_count(high, t.err, tol=60) > 300, "100% 未使用危险色"
    # 低用量不应出现危险色
    assert _color_count(low, t.err, tol=60) == 0


def test_quota_unlimited():
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import quota

    data = quota.build(t, used=7, quota=0).to_bytes()  # quota<=0 = 不限
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- 主题
def test_two_themes_produce_different_bytes():
    a = render_sync("menu", NEON)
    b = render_sync("menu", LIGHT)
    assert a != b
    assert _open(a).convert("RGB").load()[1, 1] == rgb(theme.get(NEON).bg)
    assert _open(b).convert("RGB").load()[1, 1] == rgb(theme.get(LIGHT).bg)


def test_auto_theme_resolved_in_render():
    import datetime as dt

    from src.plugins.qq_bridge.pic.theme import resolve

    night = resolve("auto", now=dt.datetime(2026, 9, 7, 23, 0)).name
    day = resolve("auto", now=dt.datetime(2026, 9, 7, 12, 0)).name
    assert night == NEON and day == LIGHT


# ---------------------------------------------------------------- render 入口 + 缓存
def test_render_cache_hit():
    from src.plugins.qq_bridge.pic import cache as cache_mod

    saved_dir = cache_mod.pic_cache.dir
    try:
        cache_mod.pic_cache.clear()
        p = {"items": [("wand", "生图", "一句话")], "title": "缓存测试"}
        a = _run(render("menu", NEON, **p))
        b = _run(render("menu", NEON, **p))
        assert a == b, "同参数两次渲染结果应一致（缓存命中）"
        c = _run(render("menu", LIGHT, **p))
        assert a != c, "不同主题不得命中同一缓存"
    finally:
        cache_mod.pic_cache.dir = saved_dir


def test_render_is_async_and_fast():
    t0 = time.time()
    data = _run(render("progress", NEON, percent=42, elapsed=83))
    cost = (time.time() - t0) * 1000
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert cost < 300, "单次渲染耗时 %.0fms 超预算" % cost


# ---------------------------------------------------------------- guide / wm 市价卡
body_market = (
    "@title|🛒 Warframe.market 物品市场\n"
    "@sub|诺，这些都是出售[Saryn Prime 一套]的：\n"
    "@hdr|用户名|数量|价格|状态\n"
    "@row|AYMY-DarkBlew|1|79|游戏在线\n"
    "@row|TennoB|2|80|在线\n"
    "@stat|·48时内零级售价 最低78 最高78 最新78\n"
    "@shout|/w AYMY-DarkBlew Hi! I want to buy: Saryn Prime Set for 79 platinum. (wf.m via RabbitBot)\n"
    "@page|页码:1/86 使用\"wm Saryn Prime 一套 -2\"查看下一页.\n"
    "@foot|[PC] Warframe Rabbit *善用右键/长按提取文字"
)


def test_guide_market_card_renders():
    """Rabbit 风格市价卡须正常渲染为 PNG，且新 DSL 标记不抛异常。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import guide

    c = guide.build(t, body=body_market)
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.height > 400


def test_guide_market_card_price_badge_and_status():
    """市价卡渲染后，价格列出现 name 色像素，状态列出现 ok 绿色像素。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import guide

    c = guide.build(t, body=body_market)
    data = c.to_bytes()
    # 紫色用户名/价格边框
    assert _color_count(data, t.link, tol=60) > 100, "未检测到用户名/价格边框紫色"
    # 绿色状态
    assert _color_count(data, t.ok, tol=60) > 100, "未检测到在线状态绿色"


def test_guide_market_card_cart_icon_replaces_emoji():
    """标题里的 🛒 emoji 不应被渲染成豆腐块（实际由几何购物车图标替代）。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import guide

    c = guide.build(t, body=body_market)
    data = c.to_bytes()
    # 豆腐块色近似 #000000 或 #FFFFFF，这里只保证标题区有主色像素
    assert _color_count(data, t.accent, tol=60) > 200, "标题主色像素不足"


def test_render_size_budget():
    """QQ 场景体积预算：硬上限 2MB（协议），软基线 256KB。

    2026-10-21 调整：菜单卡随 M6/M8 等命令族扩条目，实测 211KB 已越过旧的
    200KB 软基线——这是**内容变多的正常结果**，不是渲染退化，故基线调至 256KB
    〔用户待决：若后续再涨，正确做法是调色板量化（256 色）压体积，而不是继续
    放宽阈值；本条只挡「异常暴涨」，不挡正常增长〕。
    """
    data = _run(render("menu", NEON))
    assert len(data) < 2 * 1024 * 1024
    assert len(data) < 256 * 1024, "菜单图体积 %.1fKB 偏大" % (len(data) / 1024)


# ---------------------------------------------------------------- 预览图（人工确认）
def test_generate_all_previews():
    """产出 3 模板 × 2 主题 = 6 张预览图，供人工确认观感。"""
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    from src.plugins.qq_bridge.pic.templates import menu, progress, quota

    paths = []
    for name in (NEON, LIGHT):
        t = theme.get(name)
        specs = [
            ("menu", menu.build(t, items=menu.DEFAULT_ITEMS)),
            ("progress", progress.build(
                t, percent=42, elapsed=83, eta=120, kind="t2i",
                prompt="赛博朋克风格的猫，霓虹灯，雨夜，电影感", trace_id="a1b2c3d4e5f6")),
            ("quota", quota.build(t, used=12, quota=50, user_label="QQ 3858…")),
        ]
        for tag, c in specs:
            p = os.path.join(PREVIEW_DIR, "%s_%s.png" % (tag, name))
            c.save(p)
            paths.append(p)
    for p in paths:
        assert os.path.isfile(p) and os.path.getsize(p) > 2000
    print("\n预览图（%d 张）：\n  %s" % (len(paths), "\n  ".join(paths)))
