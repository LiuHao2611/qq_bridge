# -*- coding: utf-8 -*-
"""老主题 JSON 缺新增颜色字段的兼容性回归（2026-09-23）。

背景：`link` 是后期新增的第 17 个颜色字段（Theme 默认 #8B5CF6 / NEON #A78BFA），
存量主题（warm.json / cs1.json）保存时还没有它。旧 validate_colors「缺一不可」
导致编辑器打开老主题一预览就报「颜色字段 link 格式错误：None」。

修复后契约：
1. 缺失 / null 字段 → 回落 NEON_THEME 对应默认值（不再报错）；
2. 存在但格式非法（非 #RRGGBB(AA)）→ 仍然报 ThemeStoreError（真坏输入不放过）；
3. 真实存量数据文件（data/themes/*.json）经 validate_colors 必须全部通过。
"""
import json
import os

import pytest

from src.plugins.qq_bridge.pic.themes_store import (
    THEME_FIELDS,
    THEMES_DIR,
    ThemeStoreError,
    validate_colors,
)


def _neon_value(field: str) -> str:
    from src.plugins.qq_bridge.pic.theme import NEON_THEME
    return str(getattr(NEON_THEME, field)).upper()


def test_missing_link_falls_back_to_default():
    """复现用户报错的场景：warm 主题 colors 无 link，校验应补默认而非报错。"""
    colors = {
        "bg": "#FFF7ED", "card": "#FFEDD5", "card_border": "#FED7AA",
        "card_shadow": "#00000010", "text": "#7C2D12", "text_sub": "#9A3412",
        "text_weak": "#C2410C", "accent": "#EA580C", "accent2": "#FB923C",
        "accent_soft": "#FFEDD5", "track": "#FED7AA", "fill_start": "#EA580C",
        "fill_end": "#FB923C", "ok": "#16A34A", "warn": "#D97706",
        "err": "#DC2626", "divider": "#FED7AA",
    }
    assert "link" not in colors
    out = validate_colors(colors)
    assert out["link"] == _neon_value("link"), "缺失的 link 应回落 NEON 默认值"
    assert set(out.keys()) == set(THEME_FIELDS), "补齐后应 17 字段齐全"


def test_null_field_falls_back_to_default():
    """前端传 null（截图报错显示 None）同样应补默认。"""
    colors = {f: "#123456" for f in THEME_FIELDS}
    colors["link"] = None
    out = validate_colors(colors)
    assert out["link"] == _neon_value("link")


def test_invalid_hex_still_raises():
    """存在但非法的值不能被静默吞掉——保留对真正坏输入的告警。"""
    colors = {f: "#123456" for f in THEME_FIELDS}
    colors["accent"] = "not-a-color"
    with pytest.raises(ThemeStoreError):
        validate_colors(colors)


def test_all_existing_theme_files_pass_validation():
    """data/themes/ 下所有存量主题 JSON（含未修复前的老结构）必须能通过校验。"""
    assert os.path.isdir(THEMES_DIR)
    files = [f for f in os.listdir(THEMES_DIR) if f.endswith(".json")]
    assert files, "主题目录不应为空"
    for fn in files:
        with open(os.path.join(THEMES_DIR, fn), encoding="utf-8") as fp:
            d = json.load(fp)
        colors = (d.get("colors") or {}) if isinstance(d, dict) else {}
        out = validate_colors(colors)
        assert set(out.keys()) == set(THEME_FIELDS), f"{fn} 补齐后字段不齐"
