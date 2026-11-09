# -*- coding: utf-8 -*-
"""市价卡回归：浅色默认绑定 + 白金/现金/杜卡德三枚**真实游戏内图标**。

守的是用户看得见的行为（prove-it）：
  * 发「价格 / 市价 / wm」出的卡默认是浅色版，且这个绑定随代码交付（不依赖被
    ``.gitignore`` 忽略的 ``data/pic_card_themes.json``）；
  * 三枚货币图标必须是真实 PNG（Wiki Text Icons），不是几何兜底；缺图要能报错；
  * 有杜卡德时画杜卡德图标，「不可兑换」时不画；
  * 图标不能破坏 ``@mstat`` 四行价格统计，也不能造成溢出/重叠/间距问题。

反例注入由 ``tools/check_market_currency_layout.py --selftest`` 承担，这里跑一次
确认闸门本身没变成摆设。
"""
from __future__ import annotations

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
    assert names.count("tbl.hdr.platinum") == 1
    assert _currency_problems(boxes, True) == []
    assert check_boxes((16, 16, c.width - 16, c.height - 16), boxes) == []
    assert c.to_bytes()[:8] == PNG_MAGIC


def test_ducats_icon_omitted_when_item_is_not_exchangeable():
    """用户说「杜卡德币（如果有的话）」：不可兑换时不画杜卡德图标，其余两枚仍在。"""
    c, boxes = _render("light", "不可兑换")
    names = [b[0] for b in boxes]
    assert "wm.cur.ducats" not in names
    assert "wm.cur.credits" in names
    assert "tbl.hdr.platinum" in names
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
    """闸门自己也要被测：7 类注入反例（缺图/漏登记/重叠/删绑定…）必须全部被拦下。"""
    assert run_selftest() == 0
