# -*- coding: utf-8 -*-
"""pic/fonts.py —— 中文字体探测与加载（Pillow 路线地基）。

设计要点（2026-09-07）：
* **零额外依赖**：优先使用 Windows 系统自带中文字体（微软雅黑 / 黑体 / 宋体），
  不下载、不打包字体文件，避免体积与授权问题。
* **回退链**：每个字重给多个候选，一个都找不到才返回默认字体（不抛异常、不崩溃）。
  找不到中文时记 WARNING —— 因为用默认字体画中文会变豆腐块，必须让用户知道。
* **自定义优先**：若 `PIC_FONT_DIR`（或 qq_bridge/data/fonts）下放了同名字体，
  优先用自定义字体，方便用户换自己喜欢的字体。
* **缓存**：ImageFont.truetype 有 IO 与解析开销，按 (role, size) 缓存实例。

本模块**不 import nonebot、不 import 上级包**，可被 pytest 独立导入（便于单测）。
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageFont
from PIL.ImageFont import FreeTypeFont

# src/plugins/qq_bridge/pic/fonts.py → parents[4] = qq_bridge/（项目根）
# 注意：比 wf_sync.py 深一层（多了 pic/），必须 parents[4]，旧写 parents[3] 会落到 src/
_ROOT_DIR = Path(__file__).resolve().parents[4]
_CUSTOM_DIR = Path(os.environ.get("PIC_FONT_DIR") or _ROOT_DIR / "data" / "fonts")
# vendored 中文字体（game_tools/modcard/assets/msyh.ttc，已 COPY 进 Docker 镜像，
# 容器内无系统字体时的兜底；TTC 集合 index 0 常规 / 1 粗体）
_VENDOR_DIR = _ROOT_DIR / "src" / "plugins" / "qq_bridge" / "game_tools" / "modcard" / "assets"
_SYSTEM_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# 字重 → 候选字体文件名（按优先级）
# msyh=微软雅黑(常规) msyhbd=微软雅黑粗体 msyhl=微软雅黑细体 simhei=黑体 simsun=宋体
_CANDIDATES: dict[str, tuple[str, ...]] = {
    "regular": ("msyh.ttc", "simhei.ttf", "simsun.ttc", "Deng.ttf"),
    "bold": ("msyhbd.ttc", "simhei.ttf", "msyh.ttc", "Dengb.ttf"),
    "light": ("msyhl.ttc", "msyh.ttc", "simsun.ttc"),
}
# 兜底：系统里任意能用的字体（由 Pillow 自己找），一般是无中文能力的默认字体
_ROLES = ("regular", "bold", "light")

_WARNED: set[str] = set()
_CACHE: dict[tuple[str, int], FreeTypeFont] = {}


def font_path(role: str = "regular") -> str | None:
    """返回该字重第一个存在的字体文件路径；全部缺失返回 None。"""
    for name in _CANDIDATES.get(role, _CANDIDATES["regular"]):
        for d in (_CUSTOM_DIR, _VENDOR_DIR, _SYSTEM_DIR):
            p = d / name
            if p.is_file():
                return str(p)
    return None


def load(role: str = "regular", size: int = 20) -> FreeTypeFont:
    """加载字体。找不到任何中文字体时返回 Pillow 默认字体并记录一次 WARNING。

    注意：默认字体画中文会得到豆腐块（□），但**不能因此抛异常中断图片渲染**——
    宁可出一张字不好看的图，也不能让机器人不回话（CLOSE-02 精神 + 降级优先）。
    """
    key = (role, int(size))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    path = font_path(role)
    font: FreeTypeFont
    if path:
        try:
            # msyh.ttc 是 TTC 集合：index 0 常规 / 1 粗体。bold 角色落到 msyh.ttc
            # （无独立 msyhbd.ttc 时）要用 index 1 才是真粗体。
            idx = 1 if (role == "bold" and Path(path).name.lower() == "msyh.ttc") else 0
            font = ImageFont.truetype(path, int(size), index=idx)
        except Exception:  # 字体损坏/不支持：降级，不吞异常静默
            font = ImageFont.load_default(size=int(size))
            _warn_once(role, "load_failed")
    else:
        font = ImageFont.load_default(size=int(size))
        _warn_once(role, "not_found")

    _CACHE[key] = font
    return font


def has_cjk_font() -> bool:
    """系统是否具备中文渲染能力（决定要不要告警）。"""
    return any(font_path(r) for r in _ROLES)


def clear_cache() -> None:
    """清空字体缓存（测试或字体文件变更后使用）。"""
    _CACHE.clear()


def _warn_once(role: str, reason: str) -> None:
    """同一原因只告警一次，避免每条消息都刷日志。"""
    key = "%s:%s" % (role, reason)
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        from ..config import get_logger, jlog  # 相对导入失败则静默（本模块需可独立导入）

        get_logger().warning(jlog("pic_font_fallback", role=role, reason=reason))
    except Exception:
        pass


# ---- 字体族扫描与加载（2026-09-15 文字样式功能，见 docs/文字样式功能设计.md §4.1）----
_FAMILY_CACHE: list[dict] | None = None
_MASK_CACHE: dict[str, bool] = {}


def scan_families(force: bool = False) -> list[dict]:
    """扫描系统/自定义字体目录，按字体族聚合变体文件。

    返回 [{family, regular, bold, italic, cjk}]，cjk 族排前（QQ 卡片中文场景）。
    进程内缓存；坏字体跳过不抛异常（CLOSE-02：宁可少一个字体也不能崩）。
    """
    global _FAMILY_CACHE
    if _FAMILY_CACHE is not None and not force:
        return _FAMILY_CACHE
    fams: dict[str, dict] = {}
    for d in (_CUSTOM_DIR, _SYSTEM_DIR):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in sorted(names):
            if not n.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            p = d / n
            try:
                f = ImageFont.truetype(str(p), 16)
                fam, style = f.getname()
            except Exception:
                continue
            rec = fams.setdefault(fam, {"family": fam, "regular": None, "bold": None,
                                        "italic": None, "cjk": False})
            sl = (style or "").lower()
            slot = "bold" if "bold" in sl else (
                "italic" if ("italic" in sl or "oblique" in sl) else "regular")
            if rec[slot] is None:
                rec[slot] = str(p)
            if not rec["cjk"]:
                ok = _MASK_CACHE.get(fam)
                if ok is None:
                    # 坑（2026-09-15 实测两连）：①缺字形时 Pillow 渲染 notdef 豆腐块，
                    # mask 非空 → getbbox() 判定失效（Arial 被误判中文）；②getmask 返回的
                    # ImagingCore **没有 tobytes()**。可靠做法：把两个汉字的 mask 各自
                    # paste 进 L 图再比较位图——notdef 时两图完全相同，真中文字形互异。
                    def _glyph(ch):
                        m = f.getmask(ch)
                        if m.size[0] <= 0 or m.size[1] <= 0:
                            return None
                        # 坑③：ImagingCore 单参 paste 抛 ValueError（要 4 元 box），
                        # 异常被外层 except 吞掉后全体字体 cjk=False。bytes(m) 才可用。
                        return bytes(m)
                    try:
                        g1, g2 = _glyph("喵"), _glyph("永")
                        ok = g1 is not None and g2 is not None and g1 != g2
                    except Exception:
                        ok = False
                    _MASK_CACHE[fam] = ok
                rec["cjk"] = bool(ok)
    out = sorted(fams.values(), key=lambda r: (not r["cjk"], r["family"].lower()))
    _FAMILY_CACHE = out
    return out


def variant_flags(family: str) -> tuple[bool, bool]:
    """(是否有粗体变体, 是否有斜体变体)；族不存在 → (False, False)。"""
    rec = next((r for r in scan_families() if r["family"].lower() == (family or "").lower()), None)
    return (rec is not None and rec["bold"] is not None,
            rec is not None and rec["italic"] is not None)


def load_family(family: str, size: int = 20, bold: bool = False, italic: bool = False) -> FreeTypeFont:
    """按字体族名加载；族内缺变体由画布层兜底（伪粗/仿射斜体）。

    family 为空 / 族不存在 → 回退原 role 回退链（绝不抛异常）。
    """
    family = (family or "").strip()
    if not family:
        return load("bold" if bold else "regular", int(size))
    rec = next((r for r in scan_families() if r["family"].lower() == family.lower()), None)
    if rec is None:
        _warn_once("family", "missing:" + family)
        return load("bold" if bold else "regular", int(size))
    if italic and rec["italic"]:
        path = rec["italic"]
    elif bold and rec["bold"]:
        path = rec["bold"]
    else:
        path = rec["regular"] or rec["bold"] or rec["italic"]
    key = ("fam", family.lower(), bool(bold), bool(italic), int(size))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        font = ImageFont.truetype(path, int(size))
    except Exception:
        _warn_once("family", "load_failed:" + family)
        font = load("bold" if bold else "regular", int(size))
    _CACHE[key] = font
    return font
