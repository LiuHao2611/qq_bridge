# -*- coding: utf-8 -*-
"""装饰图片资源：合规校验 / 角落解耦 / 图片背景渲染。

验证 2026-09-07 用户增强：
  * 背景图（bg_pattern=image）/ 贴纸图（sticker_image）/ 角落贴纸图（corner_sticker_image）
    支持上传或主项目生图，且角落贴纸与背景贴纸**解耦**（各自独立图标/图片来源）。
"""
import io

from PIL import Image

from src.plugins.qq_bridge.pic import assets
from src.plugins.qq_bridge.pic.theme import Theme, resolve
from src.plugins.qq_bridge.pic.themes_store import validate_layout


def _png(w=64, h=64, color=(255, 0, 200, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _big_png() -> bytes:
    # 制造 > MAX_BYTES（2MB）的图片：用随机噪声避免 PNG 压缩到 2MB 以内
    import os as _os
    arr = _os.urandom(2048 * 2048 * 4)
    im = Image.frombytes("RGBA", (2048, 2048), arr)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_assets_compliance_accepts_valid_png():
    fn = assets.save(_png())
    assert assets._FILENAME_RE.match(fn), "资源文件名应为 uuid.ext 白名单"
    assert assets.exists(fn)
    assets.delete(fn)


def test_assets_compliance_rejects_bad_magic():
    # 伪装成 PNG 的文本
    try:
        assets.save(b"not an image at all" * 10)
        assert False, "应拒绝非图片内容"
    except assets.AssetError:
        pass


def test_assets_compliance_rejects_oversize():
    try:
        assets.save(_big_png())
        assert False, "应拒绝超大文件"
    except assets.AssetError:
        pass


def test_assets_safe_path_blocks_traversal():
    assert assets._safe_path("../secret.png") is None
    assert assets._safe_path("..\\secret.png") is None
    assert assets._safe_path("a" * 100 + ".png") is None  # 超长
    assert assets._safe_path("abcd.png") is None  # 非 uuid 白名单


def test_validate_layout_keeps_str_fields():
    lay = validate_layout({
        "bg_image": "0123456789abcdef0123456789abcdef.png",
        "sticker_image": "",
        "corner_sticker_image": "fedcba9876543210fedcba9876543210.webp",
        "corner_sticker_icon": "wand",
    })
    assert lay["bg_image"] == "0123456789abcdef0123456789abcdef.png"
    # 非法文件名（非白名单）→ 回落默认空串
    assert lay["sticker_image"] == ""
    assert lay["corner_sticker_image"] == "fedcba9876543210fedcba9876543210.webp"
    # 枚举非法 → 回落默认
    assert lay["corner_sticker_icon"] in ("wand", "star")


def test_corner_decoupled_from_background_sticker():
    """角落贴纸用 corner_sticker_image 时，与背景 sticker 图标无关（解耦）。"""
    fn = assets.save(_png(color=(0, 200, 255, 255)))
    try:
        base = dict(
            bg="#101018", card="#1a1f2e", card_border="#2a3450", card_shadow="#000000",
            text="#ffffff", text_sub="#aab", text_weak="#778", accent="#00E5FF",
            accent2="#FF4FD8", accent_soft="#0E2A38", track="#222", fill_start="#00E5FF",
            fill_end="#FF4FD8", ok="#3DDC97", warn="#FFB020", err="#FF5C7A", divider="#2a3450",
            bg_pattern="none", sticker="bolt", sticker_alpha=12,
            corner_sticker=True, corner_sticker_image=fn,
        )
        # 仅角落图标不同，其余完全相同（含 corner_sticker_image 同一张图）
        t_star = Theme(name="x", corner_sticker_icon="star", **base)
        t_bolt = Theme(name="x", corner_sticker_icon="bolt", **base)
        from src.plugins.qq_bridge.pic.templates import text as _text
        a = _text.build(t_star).to_bytes()
        b = _text.build(t_bolt).to_bytes()
        # 角落用图片 → 图标选择应被忽略 → 两张图字节一致
        assert a == b, "角落贴纸用图片时应与背景图标解耦"
    finally:
        assets.delete(fn)


def test_bg_image_renders_in_background():
    fn = assets.save(_png(color=(255, 80, 200, 255)))
    try:
        base = dict(
            bg="#101018", card="#1a1f2e", card_border="#2a3450", card_shadow="#000000",
            text="#ffffff", text_sub="#aab", text_weak="#778", accent="#00E5FF",
            accent2="#FF4FD8", accent_soft="#0E2A38", track="#222", fill_start="#00E5FF",
            fill_end="#FF4FD8", ok="#3DDC97", warn="#FFB020", err="#FF5C7A", divider="#2a3450",
            sticker="star", sticker_alpha=12, corner_sticker=False,
        )
        t_none = Theme(name="x", bg_pattern="none", **base)
        t_img = Theme(name="x", bg_pattern="image", bg_image=fn, **base)
        from src.plugins.qq_bridge.pic.templates import text as _text
        a = _text.build(t_none).to_bytes()
        b = _text.build(t_img).to_bytes()
        assert a != b, "背景图应改变渲染结果"
    finally:
        assets.delete(fn)
