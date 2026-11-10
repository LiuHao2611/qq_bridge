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


# ---------------------------------------------------------------- doupo 人物卡
DOUPO_DATA = {
    "name": "萧炎", "realm": "三星斗者", "exp": 1200, "need": 3000,
    "realm_max": False, "can_break": False,
    "alch_name": "二品炼药师", "alch_exp": 40, "alch_need": 200,
    "weapon_name": "玄重尺", "weapon_level": "3",
    "weapon_stats": {"力量": 180, "敏捷": 60, "斗气": 90, "防御": 50},
    "stats": {"力量": 320, "敏捷": 140, "斗气": 260, "防御": 180, "机缘": 75, "体力": 420},
    "hp": 420, "hp_max": 420, "mp": 260, "mp_max": 260,
    "gold": 12800, "stone": 6, "power": 980, "wins": 12, "losses": 3,
}


def test_doupo_panel_png_and_layout():
    """斗破人物卡正常渲染为 PNG，高度固定 712，关键色（朱红标题/暗金武器）落点正确。

    2026-11-07：名字行改造成「左侧 96 头像 + 右侧信息」，H_HEADER=112
    替代原 H_NAME=44，总高 644 → 712。
    """
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import doupo

    c = doupo.build(t, data=DOUPO_DATA)
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.height == 712, c.height  # 与 _measure 同步的版式高度
    # 朱红（标题 + 境界 chip + 进度条末端）必须出现
    assert _color_count(data, "#8A1C1C", tol=60) > 200
    # 暗金武器图标必须出现
    assert _color_count(data, "#B8860B", tol=60) > 30


def test_doupo_panel_avatar_is_drawn_inside_card():
    """用户 2026-11-07：头像必须**画进卡片里**，不是发一张独立动图。

    用纯色 96×96 假头像当探针：若模板没把 avatar_b64 画出来（或又被改成
    单独发图），卡片上就不会出现这 9216 个深色像素，断言立刻红。
    """
    import base64
    import io

    from src.plugins.qq_bridge.pic.templates import doupo

    buf = io.BytesIO()
    Image.new("RGBA", (96, 96), (40, 40, 60, 255)).save(buf, format="PNG")
    d = dict(DOUPO_DATA)
    d["avatar_b64"] = base64.b64encode(buf.getvalue()).decode("ascii")
    d["avatar_caption"] = "持剑少年"
    d["lv"] = 1

    with_avatar = doupo.build(theme.get(LIGHT), data=d).to_bytes()
    without = doupo.build(theme.get(LIGHT), data=DOUPO_DATA).to_bytes()
    n_with = _color_count(with_avatar, "#28283C", tol=0)
    n_without = _color_count(without, "#28283C", tol=0)
    assert n_with > 8000, f"头像没被画进卡片：深色像素仅 {n_with}"
    assert n_without < 200, f"无头像时不应有头像像素：{n_without}"
    assert with_avatar != without, "传不传头像卡片必须不一样"


def test_doupo_panel_break_uses_warm_gold():
    """可突破状态：进度条转暖金（#D98A2B），与"仍在修炼"的金红渐变明确区分。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import doupo

    d = dict(DOUPO_DATA)
    d["exp"], d["need"], d["can_break"] = 3000, 3000, True
    data = doupo.build(t, data=d).to_bytes()
    assert _color_count(data, "#D98A2B", tol=60) > 100, "可突破状态应出现暖金进度条"


def test_doupo_menu_png_and_groups():
    """斗破菜单卡正常渲染为 PNG，分组竖条（暗金）+ 折行说明落点正确。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import doupo_menu

    groups = [
        {"name": "入门 · 建立角色",
         "rows": [["斗破菜单 / dp菜单", "就是这条，随时回来看全部口令"],
                  ["dp入道", "创建角色：一星斗者，赠 10000 金币 + 玄重尺 + 回灵丹×5"]]},
        {"name": "修炼 · 涨修为升境界",
         "rows": [["dp突破", "修为满时晋升一星；28 级冲斗帝看机缘"],
                  ["dp修炼", "结算离线修为每分钟自动 +6"]]},
    ]
    c = doupo_menu.build(t, data={"title": "斗破菜单", "groups": groups})
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.height > 200, c.height
    # 暗金分组竖条必须出现
    assert _color_count(data, "#B8860B", tol=60) > 30
    # 朱红标题「斗破菜单」必须出现
    assert _color_count(data, "#8A1C1C", tol=60) > 50


def test_doupo_list_png_and_layout():
    """斗破通用列表卡正常渲染为 PNG，暗金竖条 + 朱红标题 + 等级渐变条落点正确。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import doupo_list

    rows = [
        {"badge": "1", "title": "龙吟碎　1/5", "sub": "升一级伤害 +20%", "bar": {"cur": 1, "max": 5}},
        {"badge": "3", "title": "焚血散　3/5", "sub": "升一级伤害 +20%", "bar": {"cur": 3, "max": 5}},
        {"title": "青莲地心火", "right": "4/4 级", "sub": "异火说明一段"},
        {"title": "回灵丹 ×3", "sub": "回血回蓝", "accent": True},
    ]
    c = doupo_list.build(t, data={"icon": "bolt", "title": "斗技", "subtitle": "快捷键 1/3/7/9",
                                  "rows": rows, "tip": "用法：dp升级 龙吟碎"})
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.height > 200, c.height
    # 朱红标题「斗技」必须出现
    assert _color_count(data, "#8A1C1C", tol=60) > 50
    # 暗金左侧竖条（accent 行与分组）必须出现
    assert _color_count(data, "#B8860B", tol=60) > 20
    # 等级渐变条（暗金→朱红）至少渲染一段暖色
    assert _color_count(data, "#8A1C1C", tol=120) > 30


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
    "@wmhdr|Saryn Prime 一套|Saryn Prime Set|传奇 · 战甲 · 套装|不可兑换|1,000,000|2|2|\n"
    "@hdr|卖家|状态|声望|数量|白金价格\n"
    "@row|AYMY-DarkBlew|游戏中|0|1|79|u1|\n"
    "@row|TennoB|在线|0|2|80|u2|\n"
    "@mstat|48时内零级售价|78|78|78\n"
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

    # 该行为只属于 @title 头条（wm 卡改用 @wmhdr 后不再走这条路径），单独构造
    c = guide.build(t, body="@title|🛒 Warframe.market 物品市场\n@sub|示例")
    data = c.to_bytes()
    # 豆腐块色近似 #000000 或 #FFFFFF，这里只保证标题区有主色像素
    assert _color_count(data, t.accent, tol=60) > 200, "标题主色像素不足"


def _make_png_bytes(rgba=(120, 200, 90, 255), size=(12, 12)):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", size, rgba).save(buf, "PNG")
    return buf.getvalue()


def test_guide_market_mstat_four_rows_and_pills():
    """市价卡底部「价格统计」四行：必须恰好 4 行 × 3 气泡，且不重复渲染。

    回归背景：@mstat 初版把 4 行各自当成独立块，渲染/高度循环对每个块都跑一遍
    全部 rows → 4×4=16 行（重复 4 次）。这里用 boxes 精确数行/气泡，重复即挂。
    """
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import guide

    body = (
        "@wmhdr|测试|Test|部件 · 武器|0|0|1|0|||\n"
        "@hdr|卖家|状态|声望|数量|白金价格\n"
        "@row|A|在线|0|1|10|u1|\n"
        "@mstat|48时内零级售价|30|40|39\n"
        "@mstat|90天内零级售价|27|50|38\n"
        "@mstat|48时内零级收价|5|33|24\n"
        "@mstat|90天内零级收价|5|38|21\n"
        "@foot|X"
    )
    boxes: list = []
    c = guide.build(t, body=body, boxes=boxes)
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"

    labels = [b for b in boxes if b[0] == "mstat.label"]
    pills = [b for b in boxes if b[0].startswith("mstat.") and b[0] != "mstat.label"]
    assert len(labels) == 4, f"mstat 行数应为 4，实际 {len(labels)}（重复渲染？）"
    assert len(pills) == 12, f"mstat 气泡应为 12，实际 {len(pills)}"
    # 三种语义色都要出现：最低=ok 绿 / 最高=err 红 / 最新=link 主色
    assert _color_count(data, t.ok, tol=50) > 50, "最低价绿色气泡未绘出"
    assert _color_count(data, t.err, tol=50) > 50, "最高价红色气泡未绘出"
    assert _color_count(data, t.link, tol=50) > 50, "最新价主色气泡未绘出"


def test_guide_market_card_real_avatar_drawn():
    """真实头像 bytes 传入时渲染不报错且高度正常（真图路径被走到，非生成头像回落）。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import guide

    av = _make_png_bytes()
    body = (
        "@wmhdr|Saryn Prime 一套|Saryn Prime Set|传奇 · 战甲 · 套装|不可兑换|1,000,000|2|2|\n"
        "@hdr|卖家|状态|声望|数量|白金价格\n"
        "@row|AYMY-DarkBlew|游戏中|0|1|79|u1|\n"
        "@row|TennoB|在线|0|2|80|u2|\n"
    )
    c = guide.build(t, body=body, avatars={"u1": av, "u2": av})
    data = c.to_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.height > 200


def test_guide_market_card_real_avatar_not_covered_by_identicon():
    """回归：真实头像画上后不能被生成头像（字母圈）盖住。

    2026-11-03 实际踩过这个坑：真图粘贴成功后把 _real 置 None，紧接着又进回落分支，
    字母圆直接盖在真脸上（截图看着像「根本没取到头像」）。用纯红头像统计红色像素，
    被盖住时红色像素归零，此断言必挂。
    """
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import guide

    body = ("@hdr|卖家|状态|声望|数量|白金价格\n"
            "@row|RedAvatar|游戏中|0|1|79|u1|\n")
    red_png = _make_png_bytes((255, 0, 0, 255), (32, 32))

    with_av = guide.build(t, body=body, avatars={"u1": red_png}).img.convert("RGB")
    without_av = guide.build(t, body=body, avatars={}).img.convert("RGB")

    def red_count(im):
        colors = im.getcolors(maxcolors=im.width * im.height) or []
        return sum(n for n, (r, g, b) in colors if r > 200 and g < 70 and b < 70)

    assert red_count(without_av) == 0, "对照组不该有红色像素（排除主题配色干扰）"
    assert red_count(with_av) > 50, "真实头像被生成头像盖住了：看不到红色头像像素"


def test_guide_market_card_avatar_fallback_to_identicon():
    """无真实头像（avatars 缺 uid）时回落确定性生成头像，不报错、不漏画。"""
    t = theme.get(LIGHT)
    from src.plugins.qq_bridge.pic.templates import guide

    body = (
        "@hdr|卖家|状态|声望|数量|白金价格\n"
        "@row|AYMY-DarkBlew|游戏中|0|1|79|u1|\n"
    )
    # avatars 为空 → 走生成头像分支
    c = guide.build(t, body=body, avatars={})
    assert c.to_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


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


# ---------------------------------------------------------------- netracell（科研卡）
def test_netracell_full_text_no_pink_no_leak():
    """科研卡：渲染成功 + 全量文本不遗漏 + 严格无粉色 + 防内部代号泄漏。

    覆盖此前回归缺口：原 test_pic_templates 仅测 menu/progress/quota，科研卡改动从未被单测守护。
    """
    from src.plugins.qq_bridge.pic.templates.netracell import build as _build
    from src.plugins.qq_bridge.pic.theme import Theme
    from src.plugins.qq_bridge.pic.canvas import Canvas

    sections = [
        {"highlight": "深层", "missions": [
            {"type": "镜像防御", "name": "HostileSecurity",
             "risks": [{"name": "吸血界影", "desc": "行动缓慢且不死的双衍王境界影，能吸取生命与能量。"},
                       {"name": "牵连", "desc": "被击杀敌人 4 米范围内的友军移速降低。"}],
             "description": "魔罩随时间变强，收集灵若浮印即可暂停魔罩并修复。"},
            {"type": "圣物", "name": "StickyFingers",
             "risks": [{"name": "DrainingResiduals", "desc": ""},
                       {"name": "ExplosiveCrawlers", "desc": ""}], "description": ""},
            {"type": "歼灭", "name": "密闭装甲",
             # 故意塞入 Warframe 内部代号，验证模板层 _safe_text 净化
             "risks": [{"name": "/Lotus/Language/Conquest/PersonalMod_Undersupplied", "desc": ""},
                       {"name": "迷人弧犬", "desc": "吸入原大发起攻击时拉向自己。"}], "description": ""},
        ], "extras": [
            {"name": "超宏衰减", "desc": "战甲和友军超宏防护获取量减少 75%。"},
            {"name": "DecayingFlesh", "desc": ""},
            {"name": "TimeDilation", "desc": ""},
            {"name": "Starvation", "desc": ""},
        ]},
        {"highlight": "时光", "missions": [
            {"type": "生存", "name": "TechrotConjunction",
             "risks": [{"name": "激进冒险", "desc": "移动/攻击/施速 +15%，受到伤害 +15%。"},
                       {"name": "MurmurIncursion", "desc": ""}], "description": ""},
            {"type": "防御", "name": "DisruptiveSounds",
             "risks": [{"name": "死后冲击", "desc": "被击杀敌人爆发出虚空能量。"},
                       {"name": "吸血界影", "desc": "行动缓慢且不死的双衍王境界影。"}], "description": ""},
            {"type": "刺杀", "name": "TankStrongArmor",
             "risks": [{"name": "炽蛇军竞速", "desc": "炽蛇军竞速描述。"},
                       {"name": "重装作战", "desc": "重装作战描述。"}], "description": ""},
        ], "extras": [
            {"name": "过敏", "desc": "负面异常状态持续时间增加到三倍。"},
            {"name": "VoidEnergyOverload", "desc": ""},
            {"name": "钝重兵刃", "desc": "近战连击数几率减少 50%。"},
            {"name": "技能禁用", "desc": "击杀 50 名敌人前所有技能禁用。"},
        ]},
    ]

    # 捕获所有绘制文本：精确验证「一个字都不准漏」
    drawn = []
    _orig = Canvas.text
    def _cap(self, pos, text, *a, **k):
        drawn.append(str(text))
        return _orig(self, pos, text, *a, **k)
    Canvas.text = _cap
    try:
        t = Theme(name="light", bg="#EDF1F7", card="#FFFFFF", card_border="#E3E8F0",
                  card_shadow="#000000", text="#E8EEF7", text_sub="#AEB9CC", text_weak="#7C879A",
                  accent="#1D4ED8", accent2="#3B82F6", accent_soft="#E8EEF7",
                  track="#2A3142", fill_start="#1D4ED8", fill_end="#3B82F6",
                  ok="#3ECF8E", warn="#F5A623", err="#F0455A", divider="#2A3142")
        c = _build(t, sections=sections, title="本周科研任务", remain="2天19小时23分", width=600)
        data = c.to_bytes()
    finally:
        Canvas.text = _orig

    # 1) 渲染成功
    img = _open(data).convert("RGB")
    assert img.size[0] == 600, img.size

    # 2) 全量文本不遗漏（标题/双区块/任务名/额外区/剩余/署名/词条）
    blob = "\n".join(drawn)
    for must in ["本周科研任务", "本周深层科研任务", "本周时光科研任务",
                 "镜像防御 HostileSecurity", "额外选项：", "剩余时间",
                 "吸血界影", "超宏衰减", "技能禁用", "★ "]:
        assert must in blob, "科研卡缺失文本: %s" % must

    # 3) 防内部代号泄漏：/Lotus 路径与 PersonalMod_ 前缀必须净化
    assert "/Lotus" not in blob, "检测到未净化的 Lotus 内部代号"
    assert "Undersupplied" in blob, "PersonalMod_ 前缀未被剥离"

    # 4) 严格无粉色（樱花粉/品红区间：R 高、B 明显高于 G、G 偏低；
    #    排除红字/琥珀/蓝/灰/白，以及红字压在浅蓝条上的抗锯齿过渡像素 b≈g）
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        px = list(img.getdata())
    pink = 0
    for (r, g, b) in px:
        if r > 210 and g < 200 and b > 120 and b > g + 8 and r - b > 20:
            pink += 1
    assert pink == 0, "检测到 %d 个粉色像素（疑似粉色主题回归）" % pink


def test_netracell_official_zh_from_codes():
    """科研卡：世界状态内部代码 → 官方中文（查表翻译，禁止把某一周任务名写死）。

    用官方 worldstate Conquests 真实报文结构喂纯函数 _netracell_sections，
    断言六任务类型/名称、★风险、额外选项全部翻成官方简中；未知代码回退干净英文。
    """
    from src.plugins.qq_bridge.game_tools.warframe import _netracell_sections

    cs = [
        {"Type": "CT_LAB", "Missions": [
            {"missionType": "MT_DEFENSE", "difficulties": [
                {"type": "CD_HARD", "deviation": "HostileSecurity", "risks": ["VoidAberration", "Quicksand"]}]},
            {"missionType": "MT_ARTIFACT", "difficulties": [
                {"type": "CD_HARD", "deviation": "StickyFingers",
                 "risks": ["DrainingResiduals", "ExplosiveCrawlers"]}]},
            {"missionType": "MT_EXTERMINATION", "difficulties": [
                {"type": "CD_HARD", "deviation": "FortifiedFoes",
                 "risks": ["AntiMaterialWeapons", "EMPBlackHole"]}]},
        ], "Variables": ["AntiGuard", "DecayingFlesh", "TimeDilation", "Starvation"]},
        {"Type": "CT_HEX", "Missions": [
            {"missionType": "MT_SURVIVAL", "difficulties": [
                {"type": "CD_HARD", "deviation": "TechrotConjunction",
                 "risks": ["AcceleratedEnemies", "MurmurIncursion"]}]},
            {"missionType": "MT_DEFENSE", "difficulties": [
                {"type": "CD_HARD", "deviation": "DisruptiveSounds",
                 "risks": ["Voidburst", "VoidAberration"]}]},
            {"missionType": "MT_ASSASSINATION", "difficulties": [
                {"type": "CD_HARD", "deviation": "TankStrongArmor",
                 "risks": ["FactionSwarm_Scaldra", "HeavyWarfare"]}]},
        ], "Variables": ["OverSensitive", "VoidEnergyOverload", "DullBlades", "AbilityLockout"]},
    ]
    sec = _netracell_sections(cs)

    # 任务类型：MT_ARTIFACT 必须是「中断」而非「圣物」
    assert [m["type"] for m in sec[0]["missions"]] == ["镜像防御", "中断", "歼灭"]
    assert [m["name"] for m in sec[0]["missions"]] == ["侵蚀感官", "暴食贪囤", "密闭装甲"]
    assert [m["name"] for m in sec[1]["missions"]] == ["科腐围攻", "吸血摇滚", "热熔装甲板"]

    # 任务说明来自官方偏差描述表（API 本身不带 description 字段，也必须翻出来）
    assert sec[0]["missions"][0]["description"].startswith("魔耳和魔瞳")
    assert sec[0]["missions"][1]["description"] == "所有贪囤断肢都是卓越者。"

    # ★ 风险官方名
    assert [r["name"] for r in sec[0]["missions"][1]["risks"]] == ["恶魔契约", "易爆潜能"]
    assert sec[1]["missions"][0]["risks"][1]["name"] == "墙壁之外"

    # 额外选项官方名（此前存在 衰变血肉/时间膨胀/饥饿/过敏/虚空能量过载/技能封锁 等错译）
    assert [e["name"] for e in sec[0]["extras"]] == ["超宏衰减", "永久损伤", "缩短技能", "弹药亏空"]
    assert [e["name"] for e in sec[1]["extras"]] == ["过敏受体", "技能超载", "钝重兵刃", "技能禁用"]

    # 未收录代码 → 干净英文回退，绝不编造中文
    unknown = _netracell_sections([{"Type": "CT_LAB", "Missions": [
        {"missionType": "MT_DEFENSE", "difficulties": [
            {"type": "CD_HARD", "deviation": "UnknownCode", "risks": ["MysteryRisk"]}]}],
        "Variables": ["MysteryMod"]}])
    assert unknown[0]["missions"][0]["name"] == "UnknownCode"
    assert unknown[0]["missions"][0]["risks"][0]["name"] == "MysteryRisk"
    assert unknown[0]["missions"][0]["risks"][0]["desc"] == ""


def test_netracell_remain_has_seconds():
    """剩余时间精确到秒（与官方卡一致：X天Y小时Z分W秒）。"""
    from src.plugins.qq_bridge.game_tools.warframe import _fmt_dhms

    assert _fmt_dhms(2 * 86400 + 19 * 3600 + 25 * 60 + 46) == "2天19小时25分46秒"
    assert _fmt_dhms(3 * 3600 + 5 * 60 + 7) == "3小时5分7秒"
    assert _fmt_dhms(42) == "0分42秒"


def test_netracell_week_key_from_activation():
    """游戏周键取自官方 Conquests.Activation（周重置时间戳）。"""
    from src.plugins.qq_bridge.game_tools.netracell_zh import week_key_of

    cs = [{"Type": "CT_LAB", "Activation": {"$date": {"$numberLong": "1792972800000"}}}]
    assert week_key_of(cs) == "1792972800000"
    assert week_key_of([]).startswith("20")  # 缺失时退化为 ISO 年-周


def test_netracell_weekly_snapshot(monkeypatch):
    """每周首次取卡刷新一次官方快照；同周复用、不重复下载。"""
    from src.plugins.qq_bridge.game_tools import netracell_zh as z

    calls: list = []
    monkeypatch.setattr(z, "_raw", {"_meta": {"week_key": "W1"}, "conquest": {}, "missions": {}})
    monkeypatch.setattr(z, "_data", {})

    def _fake_refresh(week_key=None, timeout=0):
        calls.append(week_key)
        z._raw = {"_meta": {"week_key": week_key}, "conquest": {}, "missions": {}}
        return 1

    monkeypatch.setattr(z, "refresh_from_official", _fake_refresh)

    assert z.snapshot_week() == "W1"
    assert z.ensure_weekly("W1") is False   # 同周：复用快照，不刷新
    assert calls == []
    assert z.ensure_weekly("W2") is True    # 跨周：刷新一次
    assert calls == ["W2"]
    assert z.ensure_weekly("W2") is False   # 刷新后同周：不再刷新
    assert calls == ["W2"]


# ---------------------------------------------------------------- riven_analysis
def _riven_data(weapon_image=None):
    """构造一张 4 词条紫卡分析卡数据（与参考卡版式一致）。"""
    return {
        "brand": "紫卡分析", "corner": "统一倍率",
        "weapon_zh": "葛拉特勒", "weapon_en": "GRATTLER", "riven_name": "X",
        "weapon_image": weapon_image, "weapon_type": "Secondary",
        "overall_grade": "B+", "overall_pct": "+1.432%",
        "layout": "3 正 1 负", "meta": "重武器 MR 14 满级",
        "stats": [
            {"value": "+31.0%", "name": "暴击几率", "sign": "+", "range": "121.5% − 148.5%",
             "dev": "+3.542%", "grade": "A-", "bar": 0.673, "neg": False},
            {"value": "+165.6%", "name": "基础伤害", "sign": "+", "range": "167.1% − 204.2%",
             "dev": "+1.432%", "grade": "B+", "bar": 0.55, "neg": False},
            {"value": "+149.4%", "name": "暴击伤害", "sign": "+", "range": "134.4% − 164.2%",
             "dev": "+2.310%", "grade": "A-", "bar": 0.61, "neg": False},
            {"value": "−59.4%", "name": "触发持续时间", "sign": "-", "range": "−59.4% − −48.6%",
             "dev": "+3.910%", "grade": "B-", "bar": 0.69, "neg": True},
        ],
        "foot_left": "紫卡分析", "foot_right": "2026-11-03 15:08",
    }


def _region_non_bg(data: bytes, x0: int, y0: int, x1: int, y1: int,
                   bg: str, tol: int = 40) -> int:
    """右上角框区域内，与背景色差异超 tol 的像素数（>0 即有内容）。"""
    img = _open(data).convert("RGB")
    bgr = rgb(bg)
    cnt = 0
    for yy in range(max(0, y0), min(img.height, y1)):
        for xx in range(max(0, x0), min(img.width, x1)):
            px = img.getpixel((xx, yy))
            if abs(px[0] - bgr[0]) + abs(px[1] - bgr[1]) + abs(px[2] - bgr[2]) > tol:
                cnt += 1
    return cnt


def _region_color_count(data: bytes, x0: int, y0: int, x1: int, y1: int,
                        target: str, tol: int = 45) -> int:
    """右上角框区域内，接近 target 色的像素数（验证剪影实体而非仅描边）。"""
    img = _open(data).convert("RGB")
    tgt = rgb(target)
    cnt = 0
    for yy in range(max(0, y0), min(img.height, y1)):
        for xx in range(max(0, x0), min(img.width, x1)):
            px = img.getpixel((xx, yy))
            if abs(px[0] - tgt[0]) + abs(px[1] - tgt[1]) + abs(px[2] - tgt[2]) <= tol:
                cnt += 1
    return cnt


def _riven_topright_box(width: int):
    """右上角武器框在设计空间 (x200..290, y13..73) 映射到像素坐标。"""
    s = width / 297.0
    return int(200 * s), int(13 * s), int(290 * s), int(73 * s)


def test_riven_analysis_topright_fallback_when_no_image():
    """回归（紫卡分析右上角无武器图 bug）：weapon_image 缺失时也必须绘制武器剪影兜底图，永不空白。"""
    from src.plugins.qq_bridge.pic.templates import riven_analysis

    c = riven_analysis.build(theme.get(NEON), _riven_data(weapon_image=None), width=800)
    data = c.to_bytes()
    x0, y0, x1, y1 = _riven_topright_box(800)
    # 兜底画的是品牌紫武器剪影＋描边，背景 #F3F1ED —— 区域内必有非背景内容
    assert _region_non_bg(data, x0, y0, x1, y1, "#F3F1ED") > 100, \
        "右上角缺失武器图时必须有剪影兜底内容，永不空白"
    # 关键：剪影实体在内框中部（非仅边缘描边）。把框向内收缩 8px，统计品牌紫像素，
    # 必须超过描边本身体积——证明真画了一张武器剪影，而非只有空框（防 build_silhouette 静默失败）。
    ix0, iy0, ix1, iy1 = x0 + 8, y0 + 8, x1 - 8, y1 - 8
    purple = _region_color_count(data, ix0, iy0, ix1, iy1, "#735787", tol=45)
    assert purple > 500, \
        "右上角内框品牌紫像素仅 %d：剪影实体疑似缺失（可能只画了描边）" % purple


def test_riven_analysis_topright_image_pasted(tmp_path):
    """回归：有真实武器图时右上角应贴图而非占位框（验证粘贴路径未被占位逻辑顶替）。"""
    from src.plugins.qq_bridge.pic.templates import riven_analysis
    from PIL import Image as _PILImage

    # 造一张不透明亮色图，明显区别于背景 (#F3F1ED) 与白底占位框 (#FFFFFF)
    img = _PILImage.new("RGBA", (120, 80), (220, 40, 40, 255))
    p = tmp_path / "wpn.png"
    img.save(p)
    c = riven_analysis.build(theme.get(NEON), _riven_data(weapon_image=str(p)), width=800)
    data = c.to_bytes()
    x0, y0, x1, y1 = _riven_topright_box(800)
    # 有真图时不应出现白色占位框底色，且区域内应有贴图内容
    assert _color_count(data, "#FFFFFF", tol=30) == 0, "有真图时不应出现白底占位框"
    assert _region_non_bg(data, x0, y0, x1, y1, "#F3F1ED") > 200, \
        "有真图时右上角应有贴图内容"


def test_weapon_icons_cache_name_fallback():
    """锁定修复：image_name 为 None 时按英文名 CamelCase 推导缓存名（如 Gratlter）。"""
    from src.plugins.qq_bridge.pic import weapon_icons as wi

    assert wi._cache_name(None, "Gratlter") == "Gratlter.png"
    # 「&」两种写法都试（URL 遍历全部变体），缓存名取首候选即可，自洽即可
    assert wi._cache_name(None, "Ak & Brunt") == "AkBrunt.png"
    # 有 imageName 时优先用官方名，行为不变
    assert wi._cache_name("akarius-2009d8750f.png", "Akarius") == "akarius-2009d8750f.png"
    assert wi._cache_name(None, "") is None
    # 据此，本地库没收的武器也能拿到缓存路径（若已落盘）；无图时返回 None 而非崩溃
    assert wi.icon_cache_path(None, "Gratlter") is None


def test_weapon_icons_de_official_source_preferred():
    """回归：DE 官方 Public Export 是全量图源，必须排在候选地址第一位。

    覆盖 2026-11-04 的核心修复：此前只接 WFCD 仓库镜像（约 2/3 覆盖），
    Acceltra / Akarius 这类「无后缀本体」取不到图，右上角只能画剪影。
    DE 官方 CDN 实测覆盖全部 830 把紫卡武器，``source_urls`` 首位必须是它。
    """
    from src.plugins.qq_bridge.pic import weapon_icons as wi

    if not (wi._DE_MANIFEST_CACHE.exists()
            and (wi._CACHE_DIR.parent / "warframe_items" / "riven-public-export.json").exists()):
        pytest.skip("DE manifest / 紫卡武器全表未预拉，跳过全量图源断言（跑 --prefetch 即可）")

    # 本地四件套收录的武器：能反查到 DE uniqueName
    assert (wi.weapon_unique_name("acceltra-f87797699a.png", "Acceltra")
            == "/Lotus/Weapons/Tenno/LongGuns/SapientPrimary/SapientPrimaryWeapon")
    urls = wi.source_urls("acceltra-f87797699a.png", "Acceltra",
                          wi.weapon_unique_name(None, "Acceltra"))
    assert urls and urls[0].startswith(wi._DE_BASE), "DE 官方全量图源必须排第一"
    # manifest 已落盘时（本机预拉过），URL 必须指向真实 store 图标
    if wi._DE_MANIFEST_CACHE.exists():
        assert "SapientPrimaryWeapon.png!" in urls[0]

    # 四件套之外的紫卡武器（空战枪 葛拉特勒）也能通过紫卡全表解析出 uniqueName
    un_grattler = wi.weapon_unique_name(None, "Grattler")
    assert un_grattler.endswith("/ArchGRNAAGun"), un_grattler
    assert wi.source_urls(None, "Grattler", un_grattler)[0].startswith(wi._DE_BASE)
