# -*- coding: utf-8 -*-
"""验收：主题「形状 / 排版」参数（2026-09-07 新增，解决「只能调颜色太单调」）。

覆盖四件事：
  1. 校验层：缺失补默认、越界夹取、枚举非法回落（老主题无 layout 时零迁移）
  2. 存储层：layout 能存能读，且老格式主题（只有 colors）依然可用
  3. 接口层：编辑器拿到 layout_spec / 预设色值 / 模板列表；四个模板预览都能出图
  4. **渲染层（最关键）**：改形状参数后出图**真的变了**（宽度/圆角/字号影响像素与尺寸），
     防止出现「字段存了但没人用」的假交付

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_theme_layout.py -v
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge.pic import theme as theme_mod  # noqa: E402
from src.plugins.qq_bridge.pic.theme import Theme  # noqa: E402
from src.plugins.qq_bridge.pic.themes_store import (  # noqa: E402
    LAYOUT_FIELDS, LAYOUT_SPEC, PRESETS, THEME_FIELDS, validate_layout,
)


def _colors(**over):
    """测试配色：以 neon 为底（**各字段颜色互不相同**）。

    为什么不能用「全部同一个色」：卡片与背景同色时，圆角/描边/阴影的像素差异
    会被完全吃掉，导致"改了参数但图一模一样"的假失败（2026-09-07 实测踩到）。
    """
    base = dict(PRESETS["neon"])
    base.update(over)
    return base


# ------------------------------------------------------------------ 校验层
def test_validate_layout_fills_defaults_when_missing():
    out = validate_layout(None)
    assert out == {k: spec["def"] for k, spec in LAYOUT_SPEC.items()}
    # 只传一个字段，其余仍要有默认（老主题零迁移）
    out2 = validate_layout({"radius": 4})
    assert out2["radius"] == 4
    assert out2["pad"] == LAYOUT_SPEC["pad"]["def"]


def test_validate_layout_clamps_out_of_range():
    out = validate_layout({"radius": 999, "pad": -5, "font_scale": 9.9, "shadow": 42})
    assert out["radius"] == LAYOUT_SPEC["radius"]["max"]
    assert out["pad"] == LAYOUT_SPEC["pad"]["min"]
    assert out["font_scale"] == LAYOUT_SPEC["font_scale"]["max"]
    assert out["shadow"] == LAYOUT_SPEC["shadow"]["max"]


def test_validate_layout_rejects_bad_enum():
    assert validate_layout({"grad_dir": "diagonal?"})["grad_dir"] == "h"
    assert validate_layout({"grad_dir": "v"})["grad_dir"] == "v"


def test_validate_layout_keeps_int_type():
    out = validate_layout({"width": 640.7})
    assert isinstance(out["width"], int) and out["width"] == 641


# ------------------------------------------------------------------ Theme 派生量
def test_theme_derived_metrics():
    t = Theme(name="x", **_colors(), radius=22, pad=24, font_scale=1.0)
    assert t.r_ctrl == 8          # 22*0.36 ≈ 8（小控件圆角分级）
    assert t.pad_in == 48
    assert t.fs(20) == 20
    assert t.horizontal() is True

    big = Theme(name="x", **_colors(), radius=40, pad=48, font_scale=1.5, grad_dir="v")
    assert big.r_ctrl == 14
    assert big.pad_in == 96
    assert big.fs(20) == 30
    assert big.horizontal() is False


def test_backward_compat_theme_without_layout():
    """老主题（dataclass 里不带形状字段）→ 用内置默认值，不炸。"""
    t = Theme(name="legacy", **_colors())
    assert t.radius == LAYOUT_SPEC["radius"]["def"]
    assert t.pad == LAYOUT_SPEC["pad"]["def"]
    assert t.width == LAYOUT_SPEC["width"]["def"]
    assert t.font_scale == 1.0


# ------------------------------------------------------------------ 存储层
@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.plugins.qq_bridge.pic import themes_store

    s = themes_store.ThemeStore(dir_=str(tmp_path))
    monkeypatch.setattr(themes_store, "theme_store", s)
    return s


def test_store_persists_layout(store):
    store.save("shaped", _colors(), layout={"radius": 4, "width": 640, "grad_dir": "v"})
    d = store.get("shaped")
    assert d["layout"]["radius"] == 4
    assert d["layout"]["width"] == 640
    assert d["layout"]["grad_dir"] == "v"
    # 未指定的字段也要补齐，避免下游 KeyError
    assert d["layout"]["shadow"] == LAYOUT_SPEC["shadow"]["def"]


def test_store_old_theme_file_without_layout_still_loads(store, tmp_path):
    """模拟存量主题文件（只有 colors）→ 读回来正常，resolve 后形状用默认。"""
    p = os.path.join(str(tmp_path), "old.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"name": "old", "description": "", "colors": _colors(bg="#0A0A0A"),
                   "created_at": 1, "updated_at": 1}, f)
    d = store.get("old")
    assert d and "layout" not in d
    t = theme_mod.resolve("old")
    assert t.bg == "#0A0A0A"
    assert t.radius == LAYOUT_SPEC["radius"]["def"]


def test_resolve_applies_layout_to_theme(store):
    store.save("wide", _colors(), layout={"width": 1000, "radius": 0, "font_scale": 1.4})
    t = theme_mod.resolve("wide")
    assert (t.width, t.radius, t.font_scale) == (1000, 0, 1.4)


# ------------------------------------------------------------------ 接口层
@pytest.fixture
def client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.plugins.qq_bridge.pic.editor import register

    app = FastAPI()
    register(app)
    return TestClient(app)


def test_editor_list_exposes_layout_and_templates(client):
    j = client.get("/api/pic/themes").json()
    assert set(j["layout_fields"]) == set(LAYOUT_FIELDS)
    assert "radius" in j["layout_spec"] and "label" in j["layout_spec"]["radius"]
    assert set(j["templates"]) >= {"text", "menu", "progress", "quota"}
    # 预设必须带具体色值，前端预设墙要画缩略色点
    assert j["preset_colors"]["neon"]["accent"] == "#00E5FF"
    # 颜色字段应带分组（17 个平铺是上一版"不好用"的主因）
    assert any(f["group"] == "主色" for f in j["fields"])


def test_editor_save_accepts_layout(client):
    r = client.put("/api/pic/themes/shape1",
                   json={"colors": _colors(), "layout": {"radius": 30, "shadow": 2}})
    assert r.status_code == 200 and r.json()["ok"]
    saved = client.get("/api/pic/themes/shape1").json()["theme"]
    assert saved["layout"]["radius"] == 30 and saved["layout"]["shadow"] == 2


@pytest.mark.parametrize("tpl", ["text", "menu", "progress", "quota"])
def test_preview_renders_every_template(client, tpl):
    r = client.post("/api/pic/themes/preview",
                    json={"colors": _colors(), "layout": {}, "template": tpl, "body": "预览文案"})
    assert r.status_code == 200, r.text[:200]
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_rejects_bad_layout_gracefully(client):
    """非法 layout 不能让预览 500——会被夹取/回落为默认值。"""
    r = client.post("/api/pic/themes/preview",
                    json={"colors": _colors(), "layout": {"radius": "abc", "grad_dir": "??"},
                          "template": "text", "body": "x"})
    assert r.status_code == 200


def test_extract_colors_from_image(client):
    from PIL import Image

    img = Image.new("RGB", (64, 64), "#3366CC")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    r = client.post("/api/pic/themes/extract-colors", json={"image": data_url, "count": 5})
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    assert j["ok"] and j["count"] >= 1
    top = j["colors"][0]["hex"]
    assert top.lstrip("#").upper() == "3366CC"  # 纯色图必须精确命中


def test_extract_colors_rejects_garbage(client):
    r = client.post("/api/pic/themes/extract-colors", json={"image": "not-an-image"})
    assert r.status_code == 400


# ------------------------------------------------------------------ 渲染层（关键）
def _render(client, tpl, layout, colors=None):
    r = client.post("/api/pic/themes/preview",
                    json={"colors": colors or _colors(), "layout": layout,
                          "template": tpl, "body": "形状参数验收"})
    assert r.status_code == 200, r.text[:200]
    return r.content


def _size(png: bytes):
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        return im.size


def test_width_layout_changes_output_size(client):
    """出图宽度必须真的跟着 width 走（不是只存了个字段）。"""
    assert _size(_render(client, "text", {"width": 640}))[0] == 640
    assert _size(_render(client, "text", {"width": 960}))[0] == 960


def test_radius_layout_changes_pixels(client):
    """圆角 0 与 30 渲染出的像素必须不同（否则等于没接）。"""
    sharp = _render(client, "text", {"radius": 0})
    round_ = _render(client, "text", {"radius": 30})
    assert sharp != round_


def test_font_scale_changes_pixels(client):
    small = _render(client, "menu", {"font_scale": 0.8})
    large = _render(client, "menu", {"font_scale": 1.5})
    assert small != large
    # 字号放大后行高变大 → 菜单图更高
    assert _size(large)[1] > _size(small)[1]


def test_shadow_and_border_change_pixels(client):
    flat = _render(client, "text", {"shadow": 0, "border": 0})
    rich = _render(client, "text", {"shadow": 3, "border": 4})
    assert flat != rich


def test_padding_layout_changes_pixels(client):
    tight = _render(client, "menu", {"pad": 8})
    loose = _render(client, "menu", {"pad": 56})
    assert tight != loose


# ------------------------------------------------------------------ 装饰层（2026-09-07 二次增强）
def test_bg_pattern_changes_pixels(client):
    """背景图案必须真改出图（none vs dots）。"""
    none = _render(client, "text", {"bg_pattern": "none"})
    dots = _render(client, "text", {"bg_pattern": "dots"})
    assert none != dots


def test_sticker_tiling_changes_pixels(client):
    """贴纸平铺背景必须真改出图（none vs stickers）。"""
    none = _render(client, "text", {"bg_pattern": "none"})
    stickers = _render(client, "text", {"bg_pattern": "stickers", "sticker": "wand"})
    assert none != stickers


@pytest.mark.parametrize("pat", ["grid", "diagonal", "stickers", "dots"])
def test_all_bg_patterns_render(client, pat):
    r = client.post("/api/pic/themes/preview",
                    json={"colors": _colors(), "layout": {"bg_pattern": pat, "sticker": "star"},
                          "template": "text", "body": "图案"})
    assert r.status_code == 200, r.text[:200]


def test_corner_sticker_changes_pixels(client):
    """右下角大贴纸必须真改出图（开 vs 关）。"""
    off = _render(client, "text", {"corner_sticker": False})
    on = _render(client, "text", {"corner_sticker": True, "sticker": "bolt"})
    assert off != on


def test_double_border_changes_pixels(client):
    """双线描边必须真改出图（开 vs 关）。"""
    off = _render(client, "text", {"double_border": False, "border": 2})
    on = _render(client, "text", {"double_border": True, "border": 2})
    assert off != on


def test_bg_pattern_bad_value_falls_back(client):
    """非法 bg_pattern 不应让预览 500（回落 none）。"""
    r = client.post("/api/pic/themes/preview",
                    json={"colors": _colors(), "layout": {"bg_pattern": "rainbow"},
                          "template": "text", "body": "x"})
    assert r.status_code == 200


def test_sticker_bad_value_falls_back(client):
    """非法贴纸图标名不应让预览 500。"""
    r = client.post("/api/pic/themes/preview",
                    json={"colors": _colors(), "layout": {"bg_pattern": "stickers", "sticker": "🐰"},
                          "template": "quota", "used": 10, "quota": 30})
    assert r.status_code == 200
