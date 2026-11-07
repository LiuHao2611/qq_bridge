"""主题背景填充（纯色/渐变/图片 + 裁切/适配/不透明度）后端渲染测试。

覆盖：
1. 默认主题（card_fill_type=solid）渲染正常，卡片中心是纯 card 色
2. card_fill_type=gradient + linear：卡片左右/上下呈现 card→accent2 过渡（起止色不同）
3. card_fill_type=gradient + radial：中心色接近 accent2（径向由外向内 c1→c2）
4. card_fill_type=image + 无图片资源：退化成纯色，不抛异常
5. card_fill_type=image + 有图片：图片按 cover 铺满，opacity=0 时看不到图（等同纯色）
6. 图片裁切 crop_x/y/w/h 生效：裁切左上角 50% 区域与整图渲染结果不同
7. 页面背景图 bg_image_fit=cover 时按覆盖铺满（非平铺），整图区域非纯 bg 色
8. 模板标签 template_labels 包含中文（guide=攻略卡 / hw=硬件卡）

机器判定：assert + 非零退出码。
"""
from __future__ import annotations

import base64
import io
import os
import sys
import uuid

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.plugins.qq_bridge.pic import widgets as W  # noqa: E402
from src.plugins.qq_bridge.pic import assets as A  # noqa: E402
from src.plugins.qq_bridge.pic.canvas import Canvas  # noqa: E402
from dataclasses import replace  # noqa: E402

from src.plugins.qq_bridge.pic.theme import NEON_THEME, Theme, rgb  # noqa: E402
from src.plugins.qq_bridge.pic.themes_store import THEME_FIELDS, validate_layout  # noqa: E402
from src.plugins.qq_bridge.pic import TEMPLATE_LABELS  # noqa: E402

# 卡片矩形（放在画布中，留出边距）
BOX = (100, 100, 700, 500)


def _mk_theme(**kw) -> Theme:
    """构造主题：基于内置 NEON，把 card 设深蓝 / accent2 设品红，便于断言渐变差异。

    用 dataclasses.replace(NEON_THEME) 而不是手写全部颜色字段——Theme 颜色字段很多
    （text/body/sub/...），手写容易字段名对不上（曾误写 title 导致 TypeError）。
    """
    return replace(NEON_THEME, card="#16213E", accent2="#FF3CAC", **kw)


def _render(t: Theme, size=(800, 600)) -> Image.Image:
    c = Canvas(size[0], size[1])
    c.rounded_rect((0, 0, size[0], size[1]), radius=0, fill=t.bg)
    W.card(c, t, BOX)
    return c.img.convert("RGB")


def _px(img: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    return img.getpixel((x, y))


def _close(a: tuple[int, int, int], b: tuple[int, int, int], tol: int = 12) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ---------------------------------------------------------------- 1. 纯色
def test_solid_card_fill():
    t = _mk_theme(card_fill_type="solid")
    img = _render(t)
    center = _px(img, 400, 300)
    assert _close(center, rgb(t.card)), f"纯色卡片中心应为 card 色 {rgb(t.card)}，实际 {center}"


# ---------------------------------------------------------------- 2. 线性渐变
def test_linear_gradient_card_fill():
    t = _mk_theme(card_fill_type="gradient", card_gradient_kind="linear", card_gradient_angle=0)
    img = _render(t)
    left = _px(img, 120, 300)
    right = _px(img, 680, 300)
    # 角度 0 = 横向：左端接近 card，右端接近 accent2
    assert _close(left, rgb(t.card), tol=25), f"横向渐变左端应接近 card {rgb(t.card)}，实际 {left}"
    assert _close(right, rgb(t.accent2), tol=25), f"横向渐变右端应接近 accent2 {rgb(t.accent2)}，实际 {right}"
    # 中间色应介于两者之间（与两端都不同）
    mid = _px(img, 400, 300)
    assert not _close(mid, left, tol=10) and not _close(mid, right, tol=10), \
        f"渐变中间色 {mid} 应与两端 {left}/{right} 都不同（否则退化成纯色）"


# ---------------------------------------------------------------- 3. 径向渐变
def test_radial_gradient_card_fill():
    t = _mk_theme(card_fill_type="gradient", card_gradient_kind="radial")
    img = _render(t)
    center = _px(img, 400, 300)
    corner = _px(img, 120, 120)
    # 径向：中心=accent2，边缘=card
    assert _close(center, rgb(t.accent2), tol=30), f"径向中心应接近 accent2，实际 {center}"
    assert not _close(center, corner, tol=20), "径向中心与边角应不同"


# ---------------------------------------------------------------- 4. 图片填充无资源
def test_image_fill_without_asset_falls_back_to_solid():
    t = _mk_theme(card_fill_type="image", card_image="")
    img = _render(t)
    center = _px(img, 400, 300)
    # 无图片 → 退化纯色（card 色，overlay 叠加后仍以 card 为主）
    assert _close(center, rgb(t.card), tol=40), f"无图片资源时应退化纯色，实际 {center}"


# ---------------------------------------------------------------- 5/6. 图片填充（真实资源）
@pytest.fixture()
def sample_image():
    """创建一张可辨识的测试图：左半红、右半蓝，用于验证 cover/裁切。"""
    im = Image.new("RGB", (400, 400))
    for x in range(400):
        for y in range(400):
            im.putpixel((x, y), (255, 0, 0) if x < 200 else (0, 0, 255))
    return im


def _save_asset(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return A.save(buf.getvalue())


def test_image_fill_cover_and_opacity(sample_image):
    fname = _save_asset(sample_image)
    try:
        # cover 铺满：卡片区域应出现图片颜色（红/蓝），而不是纯 card 色
        t = _mk_theme(card_fill_type="image", card_image=fname,
                      card_image_fit="cover", card_image_overlay=False)
        img = _render(t)
        left = _px(img, 150, 300)
        right = _px(img, 650, 300)
        assert left != right, f"cover 铺满后左右应有明显色差（红/蓝），实际 {left} / {right}"
        assert not _close(left, rgb(t.card), tol=20), "图片填充后中心不应还是纯 card 色"

        # opacity=0 → 图片完全透明 → 只剩底色（card）
        t0 = _mk_theme(card_fill_type="image", card_image=fname,
                       card_image_fit="cover", card_image_opacity=0, card_image_overlay=False)
        img0 = _render(t0)
        c0 = _px(img0, 400, 300)
        assert _close(c0, rgb(t0.bg), tol=8) or _close(c0, (0, 0, 0), tol=8), \
            f"不透明度 0 时卡片区应露出底色，实际 {c0}"
    finally:
        A.delete(fname)


def test_image_fill_crop_changes_result(sample_image):
    fname = _save_asset(sample_image)
    try:
        # 整图 cover
        t_full = _mk_theme(card_fill_type="image", card_image=fname,
                           card_image_fit="cover", card_image_overlay=False)
        img_full = _render(t_full)
        # 只裁右半（crop_x=50, w=50）→ 应只剩蓝色
        t_crop = _mk_theme(card_fill_type="image", card_image=fname,
                           card_image_fit="cover", card_image_overlay=False,
                           card_image_crop_x=50, card_image_crop_y=0,
                           card_image_crop_w=50, card_image_crop_h=100)
        img_crop = _render(t_crop)
        left_full = _px(img_full, 150, 300)
        left_crop = _px(img_crop, 150, 300)
        assert left_full != left_crop, \
            f"裁切后渲染结果应变化：整图 {left_full} vs 裁切 {left_crop}"
        # 裁右半 → 全是蓝色调（B 通道显著高于 R）
        assert left_crop[2] > left_crop[0], f"裁右半应偏蓝，实际 RGB={left_crop}"
    finally:
        A.delete(fname)


# ---------------------------------------------------------------- 7. 页面背景图 cover
def test_bg_image_fit_cover(sample_image):
    fname = _save_asset(sample_image)
    try:
        t = _mk_theme(bg_pattern="image", bg_image=fname,
                      bg_image_fit="cover", bg_image_overlay=False, sticker_alpha=100)
        from src.plugins.qq_bridge.pic import decor
        c = Canvas(800, 600)
        decor.background(c, t)
        img = c.img.convert("RGB")
        left = _px(img, 50, 50)
        right = _px(img, 750, 50)
        assert left != right, f"cover 背景图左右应有色差，实际 {left} / {right}"
        assert not _close(left, rgb(t.bg), tol=20), "cover 背景图不应退化纯色"
    finally:
        A.delete(fname)


# ---------------------------------------------------------------- 8. 模板中文标签
def test_template_labels_chinese():
    assert TEMPLATE_LABELS.get("guide") == "攻略卡", f"guide 应为「攻略卡」，实际 {TEMPLATE_LABELS.get('guide')}"
    assert TEMPLATE_LABELS.get("hw") == "硬件卡", f"hw 应为「硬件卡」，实际 {TEMPLATE_LABELS.get('hw')}"
    # 不能有残留英文 key 作为显示名
    for k, v in TEMPLATE_LABELS.items():
        assert v and v != k, f"模板 {k} 缺少中文显示名"
    assert any(ord(ch) > 127 for ch in TEMPLATE_LABELS["guide"]), "guide 标签必须是中文"


# ---------------------------------------------------------------- 9. 接口级：中文标签 + 预览可用
@pytest.fixture
def store(tmp_path, monkeypatch):
    """临时目录替换 theme_store，避免污染真实数据。"""
    from src.plugins.qq_bridge.pic import themes_store
    s = themes_store.ThemeStore(dir_=str(tmp_path))
    monkeypatch.setattr(themes_store, "theme_store", s)
    return s


@pytest.fixture
def client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.plugins.qq_bridge.pic.editor import register
    app = FastAPI()
    register(app)
    return TestClient(app)


def test_themes_api_returns_chinese_template_labels(client):
    j = client.get("/api/pic/themes").json()
    labels = j.get("template_labels") or {}
    assert labels.get("guide") == "攻略卡", f"接口应返回中文标签，实际 {labels.get('guide')}"
    assert labels.get("hw") == "硬件卡", f"接口应返回中文标签，实际 {labels.get('hw')}"
    # 每个模板 key 都要有中文名（不能漏，否则前端又显示英文）
    for k in j.get("templates") or []:
        assert labels.get(k), f"模板 {k} 缺中文显示名"
        assert any(ord(ch) > 127 for ch in labels[k]), f"模板 {k} 的显示名不是中文：{labels[k]}"


def test_themes_api_exposes_new_layout_fields(client):
    spec = client.get("/api/pic/themes").json()["layout_spec"]
    for k in ("card_fill_type", "card_gradient_kind", "card_gradient_angle",
              "card_image", "card_image_fit", "card_image_opacity", "card_image_crop_x"):
        assert k in spec, f"编辑器拿不到字段 {k}，前端就不会渲染对应控件"
    assert [o["value"] for o in spec["card_fill_type"]["options"]] == ["solid", "gradient", "image"]
    assert [o["value"] for o in spec["card_image_fit"]["options"]] == ["cover", "contain", "fill", "tile"]


def test_preview_api_renders_gradient_card(client):
    """预览接口端到端：渐变填充主题能出图（不 500），且图里真的有渐变。

    注意该接口返回的是 **JSON（png 字段是 base64）** 而不是裸图片流
    （前端拿到后塞进 <img src="data:image/png;base64,...">）。
    """
    colors = {f: "#102030" for f in THEME_FIELDS}
    colors.update(card="#16213E", accent2="#FF3CAC", bg="#0B0E17")
    r = client.post("/api/pic/themes/preview/interactive", json={
        "colors": colors,
        "layout": {"card_fill_type": "gradient", "card_gradient_kind": "linear",
                   "card_gradient_angle": 0},
        "template": "text", "body": "测试正文", "title": "标题",
    })
    assert r.status_code == 200, f"预览接口失败：{r.status_code} {r.text[:200]}"
    j = r.json()
    assert j.get("ok"), f"预览返回错误：{j.get('error')}"
    raw = base64.b64decode(j["png"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "png 字段不是合法 PNG"

    # 端到端：card 深蓝 → accent2 品红 横向渐变，卡片左右应有明显色差
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size
    left = im.getpixel((int(w * 0.15), h // 2))
    right = im.getpixel((int(w * 0.85), h // 2))
    diff = max(abs(a - b) for a, b in zip(left, right))
    assert diff > 40, f"渐变未生效：左右像素几乎一样 {left} / {right}（差 {diff}）"


def test_theme_save_load_keeps_fill_fields(client, store):
    """新字段要能随主题落盘并原样读回（否则用户保存后再打开设置全丢）。"""
    colors = {f: "#102030" for f in THEME_FIELDS}
    layout = {
        "card_fill_type": "image", "card_image_fit": "contain",
        "card_image_opacity": 0, "card_gradient_angle": 0,
        "card_image_crop_x": 25, "card_image_crop_w": 50,
    }
    assert client.put("/api/pic/themes/filltest",
                      json={"colors": colors, "layout": layout}).json()["ok"]
    got = client.get("/api/pic/themes/filltest").json()["theme"]["layout"]
    assert got["card_fill_type"] == "image"
    assert got["card_image_fit"] == "contain"
    # 0 是合法值，不能被默认值吃掉（曾因 `or default` 把 0 变成 100/135）
    assert got["card_image_opacity"] == 0, f"不透明度 0 被吞：{got['card_image_opacity']}"
    assert got["card_gradient_angle"] == 0, f"角度 0 被吞：{got['card_gradient_angle']}"
    assert got["card_image_crop_x"] == 25 and got["card_image_crop_w"] == 50


# ---------------------------------------------------------------- 10. 布局校验
def test_validate_layout_includes_new_fields():
    out = validate_layout({})
    assert out["card_fill_type"] == "solid", "默认应为纯色（兼容老主题零迁移）"
    assert out["card_image_fit"] == "cover"
    assert out["bg_image_fit"] == "tile", "页面背景图默认平铺（保持旧行为）"
    assert out["card_image_opacity"] == 100
    # 越界夹取
    out2 = validate_layout({"card_gradient_angle": 9999, "card_image_opacity": -5})
    assert out2["card_gradient_angle"] == 360, f"角度应夹到 360，实际 {out2['card_gradient_angle']}"
    assert out2["card_image_opacity"] == 0, "不透明度应夹到 0"
    # 非法枚举回落默认
    out3 = validate_layout({"card_fill_type": "not_a_type"})
    assert out3["card_fill_type"] == "solid", "非法填充类型应回落 solid"
