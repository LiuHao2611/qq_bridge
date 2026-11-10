# -*- coding: utf-8 -*-
"""市价卡回归：浅色默认绑定 + 真实货币图标 + 商品图/价格表版式。

守的是用户看得见的行为（prove-it）：
  * 发「价格 / 市价 / wm」出的卡默认是浅色版，且这个绑定随代码交付（不依赖被
    ``.gitignore`` 忽略的 ``data/pic_card_themes.json``）；
  * 三枚货币图标必须是真实 PNG（Wiki Text Icons），不是几何兜底；缺图要能报错；
  * 有杜卡德时画杜卡德图标，「不可兑换」时不画；
  * 2026-11-09 用户第二条反馈：白金图标在**每一行价格后面**，不在列头；
    头像列头不再留白；总图显示**商品自身图**（不是全游戏通用 BLUEPRINT 工程图），
    且图标槽按源图真实长宽比自适应；
  * 图标不能破坏 ``@mstat`` 四行价格统计，也不能造成溢出/重叠/间距问题。

反例注入由 ``tools/check_market_currency_layout.py --selftest`` 承担，这里跑一次
确认闸门本身没变成摆设。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 全量 pytest 时 ``tools`` 会被解析成 ``src.plugins.qq_bridge.tools``（带相对导入），
# 所以按顶层模块名从 tools/ 目录直接加载门禁脚本，不依赖包路径。
_TOOLS = ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from src.plugins.qq_bridge.pic import card_binds, currency_icons, theme  # noqa: E402
import check_market_currency_layout as gate  # noqa: E402

CARD = gate.CARD
_currency_problems = gate._currency_problems
_render = gate._render
check_boxes = gate.check_boxes
run_selftest = gate.run_selftest

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_market_card_defaults_to_light_without_config_file(monkeypatch):
    """内置默认绑定必须由代码兜住（否则 clone 后不是浅色版），且配置优先于内置默认。"""
    # 无配置时，市价卡 → light
    assert card_binds.default_card_theme(CARD) == "light"
    # 配置绑定优先于内置默认
    assert card_binds.resolve_card_theme(None, CARD, lambda c: "neon" if c == CARD else "") == "neon"
    # 无配置 → 内置默认兜底
    assert card_binds.resolve_card_theme(None, CARD, lambda c: "") == "light"
    # 显式 theme_name 优先于一切
    assert card_binds.resolve_card_theme("dark", CARD, lambda c: "neon") == "dark"
    # 拿掉内置默认 → 立刻失效（证明上面那条不是靠别处碰巧生效）
    monkeypatch.setattr(card_binds, "_DEFAULT_CARD_BINDS", {})
    assert card_binds.default_card_theme(CARD) == ""
    assert card_binds.resolve_card_theme(None, CARD, lambda c: "") == ""


def test_light_binding_does_not_leak_to_other_cards():
    """只给市价卡做浅色版，不能把其它卡也一起改成浅色。"""
    assert card_binds.default_card_theme(CARD) == "light"
    assert card_binds.default_card_theme("星际战甲·赏金") == ""
    assert card_binds.default_card_theme("") == ""


def test_three_real_currency_pngs_are_shipped():
    assert currency_icons.missing() == [], "三枚真实游戏内图标必须随代码交付"
    assert currency_icons.gate(strict=True) == []
    for kind in currency_icons.KINDS:
        assert currency_icons.available(kind)


def test_missing_currency_png_gate_fails_loudly(monkeypatch):
    """把资源拿走 → gate 必须报错，不能静默用几何图标糊过去。"""
    monkeypatch.setattr(currency_icons, "_load", lambda kind: None)
    with pytest.raises(SystemExit):
        currency_icons.gate(strict=True)
    assert set(currency_icons.missing()) == set(currency_icons.KINDS)


def test_real_png_path_is_used_not_geometry_fallback(monkeypatch):
    """正常渲染时一次都不许落到几何兜底（``_fallback_draw``）。"""
    fallback_calls: list = []
    monkeypatch.setattr(currency_icons, "_fallback_draw",
                        lambda *a, **kw: fallback_calls.append(a))
    _render("light", "100")
    assert fallback_calls == [], "真实 PNG 缺失/加载失败，渲染悄悄用了几何占位图"


def test_market_card_draws_all_three_icons_without_layout_violations():
    c, boxes = _render("light", "100")
    names = [b[0] for b in boxes]
    assert names.count("wm.cur.ducats") == 1
    assert names.count("wm.cur.credits") == 1
    # 白金不在列头，而在每一行价格气泡后面（2026-11-09 用户反馈）
    assert "tbl.hdr.platinum" not in names
    assert names.count("tbl.hdr.avatar") == 1, "头像列头第一列不得留白"
    assert names.count("tbl.row.badge") >= 1
    assert names.count("tbl.row.platinum") == names.count("tbl.row.badge")
    assert gate.icon_slot_ar(boxes) is not None, "商品图槽必须登记包围盒供版式核对"
    assert _currency_problems(boxes, True) == []
    assert check_boxes((16, 16, c.width - 16, c.height - 16), boxes) == []
    assert c.to_bytes()[:8] == PNG_MAGIC


def test_ducats_icon_omitted_when_item_is_not_exchangeable():
    """用户说「杜卡德币（如果有的话）」：不可兑换时不画杜卡德图标，其余两枚仍在。"""
    c, boxes = _render("light", "不可兑换")
    names = [b[0] for b in boxes]
    assert "wm.cur.ducats" not in names
    assert "wm.cur.credits" in names
    assert "tbl.hdr.platinum" not in names
    assert names.count("tbl.row.platinum") == names.count("tbl.row.badge")
    assert check_boxes((16, 16, c.width - 16, c.height - 16), boxes) == []
    assert _currency_problems(boxes, False) == []


def test_neon_theme_still_draws_real_icons():
    """显式换深色主题时图标照样在（浅色版是默认绑定，不是把主题写死）。"""
    c, boxes = _render("neon", "100")
    assert _currency_problems(boxes, True) == []
    assert check_boxes((16, 16, c.width - 16, c.height - 16), boxes) == []
    assert c.to_bytes()[:8] == PNG_MAGIC


def test_mstat_four_rows_not_regressed_by_currency_icons():
    _, boxes = _render("light", "100")
    labels = [b for b in boxes if b[0] == "mstat.label"]
    pills = [b for b in boxes if b[0].startswith("mstat.") and b[0] != "mstat.label"]
    assert len(labels) == 4 and len(pills) == 12


def test_layout_gate_selftest_catches_injected_regressions():
    """闸门自己也要被测：10 类注入反例（缺图/漏登记/重叠/删绑定…）必须全部被拦下。"""
    assert run_selftest() == 0


# ---------------- 2026-11-09 用户第二条反馈：总图 / 每行白金 / 头像列头 ----------------
def test_platinum_icon_follows_every_price_cell():
    """白金必须紧贴每一行价格气泡（不是列头），且垂直居中、间距合规。"""
    _, boxes = _render("light", "100")
    names = [b[0] for b in boxes]
    assert "tbl.hdr.platinum" not in names, "白金图标退回列头"
    assert names.count("tbl.hdr.avatar") == 1, "头像列头被留白"
    badges = sorted((b for b in boxes if b[0] == "tbl.row.badge"), key=lambda b: b[2])
    plats = sorted((b for b in boxes if b[0] == "tbl.row.platinum"), key=lambda b: b[2])
    assert len(badges) == 10 and len(plats) == 10
    for badge, plat in zip(badges, plats):
        gap = plat[1] - badge[3]
        assert 0 <= gap <= 16, "图标没紧跟价格气泡：gap=%d" % gap
        assert abs((badge[2] + badge[4]) / 2 - (plat[2] + plat[4]) / 2) <= 2, "图标未与价格垂直居中"


def test_item_icon_slot_follows_source_aspect_ratio():
    """商品图槽按源图真实 AR 连续自适应（方 / 竖长 / 宽幅），且排版零问题。"""
    for dims, src_ar in (((160, 160), 1.0), ((120, 240), 0.5), ((240, 120), 2.0)):
        c, boxes = _render("light", "100", gate._synth_icon(*dims))
        assert gate._icon_slot_problems(boxes, src_ar) == [], dims
        assert check_boxes((16, 16, c.width - 16, c.height - 16), boxes) == [], dims


def test_overall_blueprint_uses_item_own_icon(monkeypatch):
    """总图（blueprint 且无 component）必须取商品自身 icon，不能套通用 subIcon。

    真实踩过的错：WM set 响应里总图的 `i18n.en.subIcon` 是
    `sub_icons/blueprint_128x128.png`（全游戏共用），旧代码拿它覆盖 `icon`
    再映射成 Fandom `Blueprint.png` → 每张总图卡都显示同一张 BLUEPRINT 工程图。
    """
    from src.plugins.qq_bridge.game_tools import warframe

    own = "items/images/en/atlas_prime_blueprint.91fb26397de19c27bfece550a8315a14.png"

    async def fake_market_get(url: str, params: str = "") -> dict | None:
        if "/v2/orders/item/" in url:
            return {"data": [{"type": "sell", "platinum": 10, "quantity": 1,
                              "user": {"status": "ingame", "ingameName": "S", "id": "u1"}}]}
        if "/v2/item/" in url and "/set" in url:
            return {"data": {"items": [{
                "slug": "atlas_prime_blueprint",
                "tags": ["warframe", "prime", "blueprint"],   # 关键：没有 component
                "ducats": 45, "tradingTax": 2000,
                "i18n": {"en": {"name": "Atlas Prime Blueprint", "icon": own,
                                "subIcon": "sub_icons/blueprint_128x128.png"}},
            }]}}
        if "/v1/items/" in url and "/statistics" in url:
            return {"payload": {"statistics_closed": {"48hours": [], "90days": []},
                                "statistics_live": {"48hours": [], "90days": []}}}
        return None

    monkeypatch.setattr(warframe, "_market_get", fake_market_get)
    warframe._MARKET_CACHE.update({"key": "", "ts": 0.0, "data": None})
    warframe._MARKET_NAME_CACHE.update({"key": "", "ts": 0.0, "name": ""})
    warframe._MARKET_META_CACHE.clear()

    r = asyncio.run(warframe.market_text("atlas_prime_blueprint", page=1))
    hdr = next(ln for ln in r.split("\n") if ln.startswith("@wmhdr|"))
    assert own in hdr, "总图没用商品自身 icon：%s" % hdr
    assert "sub_icons/blueprint_128x128.png" not in r, "总图仍在用全游戏通用蓝图符号"
    assert "部件" not in hdr, "总图分类不应被误标为部件"


def test_generic_blueprint_art_mapping_is_disabled():
    """Fandom `Blueprint.png` 是与商品无关的通用工程图，映射表必须删掉。"""
    from src.plugins.qq_bridge.game_tools import warframe

    assert warframe._part_art_name("sub_icons/blueprint_128x128.png") is None
    assert "Blueprint.png" not in warframe._PART_ART_MAP.values()
    # 真实部位图映射不能跟着一起被误删
    assert warframe._part_art_name(
        "sub_icons/warframe/prime_chassis_128x128.png") == "PrimeChassis.png"


def test_overall_blueprint_routing_never_uses_part_art(monkeypatch):
    """总图分类（蓝图 · 战甲）必须落 WM 商品自身 icon，既不走 Fandom 部位图也不走官方套装图。"""
    from src.plugins.qq_bridge.game_tools import _collect_market_item_icon
    from src.plugins.qq_bridge.game_tools import warframe

    calls: list[str] = []

    async def fake_official(en):
        calls.append("official")
        return b"OFFICIAL"

    async def fake_wm(rel):
        calls.append("wm")
        return b"WM"

    async def fake_part(rel):
        calls.append("part")
        return b"PART"

    monkeypatch.setattr(warframe, "fetch_official_art", fake_official)
    monkeypatch.setattr(warframe, "fetch_item_icon", fake_wm)
    monkeypatch.setattr(warframe, "fetch_part_art", fake_part)

    body = ("@wmhdr|Atlas Prime 总图|Atlas Prime Blueprint|蓝图 · 战甲|45|2000|1|1|"
            "items/images/en/atlas_prime_blueprint.png|\n"
            "@hdr|卖家|状态|声望|数量|白金价格\n")
    assert asyncio.run(_collect_market_item_icon(body)) == b"WM"
    assert calls == ["wm"], "总图被错误路由到官方图/通用部位图：%s" % calls

    # 部件仍应走部位图（区别对待，不是一刀切禁止 subIcon）
    calls.clear()
    body_part = body.replace("蓝图 · 战甲", "部件 · 战甲 · 蓝图")
    assert asyncio.run(_collect_market_item_icon(body_part)) == b"PART"
    assert calls == ["part"]


def test_item_icon_proxy_keeps_original_aspect_ratio(monkeypatch):
    """取图代理只限宽、不限高：竖长 512×965 立绘不能被 `fit=cover` 裁成方图。"""
    from src.plugins.qq_bridge.game_tools import warframe

    urls: list[str] = []
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    class Resp:
        status_code = 200
        content = png
        headers = {"content-type": "image/png"}

    async def fake_get(url, headers, cookies, timeout):
        urls.append(url)
        return Resp()

    monkeypatch.setattr(warframe, "_http_get", fake_get)
    monkeypatch.setattr(warframe, "_load_cf_cookies", lambda: [])   # 无 cookie → 直接走代理
    monkeypatch.setattr(warframe, "_icon_cache_get", lambda *a, **k: warframe._MISS)
    monkeypatch.setattr(warframe, "_icon_cache_put", lambda *a, **k: None)

    assert asyncio.run(warframe.fetch_item_icon(
        "items/images/en/atlas_prime_blueprint.png")) == png
    assert len(urls) == 1
    assert "&w=%d&output=png" % warframe._ICON_MAX_W in urls[0]
    assert "&h=" not in urls[0] and "fit=cover" not in urls[0], "代理仍会把竖图裁成方图"
