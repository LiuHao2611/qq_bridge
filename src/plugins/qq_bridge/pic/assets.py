# -*- coding: utf-8 -*-
"""pic/assets.py —— 主题装饰资源（背景图 / 贴纸图）的本地存储 + 合规校验。

2026-09-07 用户增强：背景支持上传图片、贴纸支持上传图片或用主项目生图接口生成，
因此需要一个**本地资源库**（qq_bridge 红线：绝不写主项目目录）。

合规校验（"记得校验合规"）：
  * 扩展名白名单 + **magic bytes 二次校验**（防改后缀骗过）；
  * 体积上限（默认 2MB，贴纸/背景图无需更大）；
  * 尺寸上限（默认 2048×2048，过大既拖渲染也拖 QQ 传输）；
  * PIL 实际解码成功（防伪装成图片的损坏/恶意文件）；
  * 文件名白名单（UUID，杜绝路径穿越）。

存储：每资源一个文件 `data/themes/assets/<uuid>.<ext>`，主题 JSON 只存文件名引用。
"""
from __future__ import annotations

import io
import os
import re
import time
import uuid

from PIL import Image

#: 资源目录：pic/…/data/themes/assets（与 themes_store.THEMES_DIR 同根）
_HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR: str = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "..", "data", "themes", "assets")
)

#: 允许的扩展名（与 magic bytes 对应）
_ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".webp")
#: 文件名白名单（纯 uuid + 已知扩展名，绝不接受用户起的名字 → 防路径穿越）
_FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpe?g|webp)$")
#: 体积上限（字节）
MAX_BYTES: int = 2 * 1024 * 1024
#: 单边长上限（像素）
MAX_DIM: int = 2048

#: magic bytes 签名（文件头判断真实格式，与扩展名交叉验证）
_SIGS = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),          # JPEG（含 .jpeg）
    (b"RIFF....WEBP", ".webp"),        # WEBP（RIFF 容器，用正则占位）
)


class AssetError(Exception):
    """资源合规校验失败 / 读写失败。编辑页应捕获并提示。"""


def _sniff_ext(data: bytes) -> str | None:
    """按文件头嗅探真实扩展名（无匹配返回 None）。"""
    head = data[:32]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


#: 导入外部图片时允许的**原始**单边上限（放大 4 倍）：主项目成图常 1024~2048+，
#: 超限的直接拒绝太粗暴，交给 save_scaled 缩到 MAX_DIM 再入库。
IMPORT_MAX_DIM: int = MAX_DIM * 4


def validate_and_open(data: bytes, max_bytes: int | None = None,
                      max_dim: int | None = None) -> Image.Image:
    """合规校验 + 解码为 PIL Image（RGBA）。不合法抛 AssetError。

    :param max_bytes: 体积上限覆盖（默认 MAX_BYTES=2MB）。导入主项目成图时
        那些图常 1~3MB，用 IMPORT_MAX_BYTES 放宽（见 save_scaled）。
    :param max_dim: 单边像素上限覆盖（默认 MAX_DIM=2048）。导入路径传 IMPORT_MAX_DIM，
        因为那些图是要**缩放后**入库的，不能在校验阶段就因为大而被拒。
    """
    limit = int(max_bytes or MAX_BYTES)
    dim = MAX_DIM if max_dim is None else int(max_dim)
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
        raise AssetError("文件为空")
    if len(data) > limit:
        raise AssetError("文件过大（上限 %dKB）" % (limit // 1024))
    ext = _sniff_ext(bytes(data))
    if ext not in _ALLOWED_EXT:
        raise AssetError("不支持的图片格式（仅 PNG / JPG / WEBP）")
    try:
        im = Image.open(io.BytesIO(data))
        im.verify()  # 完整性校验（不解码像素，但能抓坏文件）
        # verify 后需重新 open 才能读像素
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        raise AssetError("图片解码失败：%s" % type(e).__name__)
    if im.width < 8 or im.height < 8:
        raise AssetError("图片尺寸过小（至少 8×8）")
    if dim > 0 and (im.width > dim or im.height > dim):
        raise AssetError("图片尺寸过大（单边上限 %dpx）" % dim)
    return im


def _encode(im: Image.Image, fmt: str, **kw) -> bytes:
    """PIL Image → 编码后的 bytes（统一走一条路，避免各调用点各写一份）。"""
    out = io.BytesIO()
    im.save(out, format=fmt, **kw)
    return out.getvalue()


def _store(raw: bytes, ext: str) -> str:
    """落盘（临时文件 + 原子替换，防半截写入），返回文件名。"""
    fname = uuid.uuid4().hex + ext
    os.makedirs(ASSETS_DIR, exist_ok=True)
    path = os.path.join(ASSETS_DIR, fname)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, path)
    return fname


def save(data: bytes) -> str:
    """校验 + 落盘，返回文件名（uuid，固定扩展名）。"""
    im = validate_and_open(data)
    ext = _sniff_ext(bytes(data)) or ".png"
    # 用校验过的 PIL 对象重新存盘（清掉可能夹带的元数据和多余通道，质量归一）
    fmt, kw = ("WEBP", {"quality": 92}) if ext == ".webp" else ("PNG", {})
    return _store(_encode(im, fmt, **kw), ext)


#: 导入外部图片（主项目文生图/图生图产物）的体积上限：
#: 项目成图 1~3MB 是常态，用上传的 2MB 上限去卡等于功能不可用。
IMPORT_MAX_BYTES: int = 8 * 1024 * 1024


def save_scaled(data: bytes, max_bytes: int | None = None) -> str:
    """导入**外部图片**（主项目成图）：先校验，超大先等比缩放，仍超再转 JPEG。

    与 `save()` 分工：`save()` 管用户上传（严格 2MB / 2048px，超限直接拒绝，
    防误传大文件）；本函数管「把主项目已有的成图搬进主题资源库」——那些图
    本来就常超 2MB，直接拒绝等于功能不可用，所以**先缩放到合规尺寸，
    再按需转 JPEG**，实在压不下去才报错（不静默降级，CLOSE-02）。

    :param max_bytes: **落盘**体积上限，默认 IMPORT_MAX_BYTES。注意：入参体积上限恒为
        IMPORT_MAX_BYTES（"别接收离谱大文件"），max_bytes 管的是**压完之后**能否达标，
        否则大图会在缩放前就被拒 —— 那就等于功能不可用。
    :raises AssetError: 非图片 / 尺寸过小 / 缩放转码后仍超限。
    """
    limit = int(max_bytes or IMPORT_MAX_BYTES)
    im = validate_and_open(data, max_bytes=max(limit, IMPORT_MAX_BYTES),
                           max_dim=IMPORT_MAX_DIM)
    if max(im.width, im.height) > MAX_DIM:
        im.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
    raw = _encode(im, "PNG")
    ext = ".png"
    if len(raw) > limit:
        jpg = _encode(im.convert("RGB"), "JPEG", quality=90, optimize=True)
        if len(jpg) <= limit:
            raw, ext = jpg, ".jpg"
    if len(raw) > limit:
        raise AssetError("图片缩放后仍超过 %dKB" % (limit // 1024))
    return _store(raw, ext)


def _safe_path(name: str) -> str | None:
    """把白名单文件名解析为绝对路径；非法返回 None（防穿越）。"""
    if not isinstance(name, str) or not _FILENAME_RE.match(name):
        return None
    p = os.path.normpath(os.path.join(ASSETS_DIR, name))
    if os.path.dirname(p) != ASSETS_DIR:  # 双保险
        return None
    return p


def load(name: str) -> Image.Image | None:
    """安全加载资源为 RGBA Image；不存在/非法返回 None。"""
    p = _safe_path(name)
    if not p or not os.path.isfile(p):
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:
        return None


def path_of(name: str) -> str | None:
    """返回资源绝对路径（供静态服务用）；非法/不存在返回 None。"""
    p = _safe_path(name)
    if not p or not os.path.isfile(p):
        return None
    return p


def delete(name: str) -> bool:
    """删除资源文件；返回是否真删。"""
    p = _safe_path(name)
    if not p or not os.path.isfile(p):
        return False
    try:
        os.remove(p)
        return True
    except OSError:
        return False


def exists(name: str) -> bool:
    p = _safe_path(name)
    return bool(p) and os.path.isfile(p)
