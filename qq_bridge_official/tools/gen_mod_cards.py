# -*- coding: utf-8 -*-
"""tools/gen_mod_cards.py —— 本地合成 Warframe mod 卡图（忠实复刻 WFCD mod-generator）。

背景与渊源（"严格按照大佬的做"）：
  DE 不发布成品 mod 卡图，客户端实时合成。社区权威实现是 WFCD 的
  ``mod-generator``（TS + @napi-rs/canvas）+ ``genesis-assets``（卡框/极性/字体）。
  本文件是其 Python/Pillow 等价移植，坐标与图层顺序 1:1 对齐
  ``vendor/mod-generator/src/{generator,drawers,styling,utils}.ts``：

    * 256×512 舞台 → 裁切中段到 256×380 成品卡（与 WFCD 的 outterCanvas 一致）
    * 图层顺序：背景 → 立绘 → 名称 → 描述 → 侧光 → 耗能/极性背板 → 底栏
                → 顶框 → 底框（含等级点与角光）
    * 稀有度 → 卡框档位（common→Bronze / uncommon→Silver / rare→Gold /
      legendary→Legendary），riven→Legendary 视觉 + "???" 耗能
    * 名称/类型/效果/稀有度中文取自 ``mod_full.json``；极性以 ``Mods.json`` 的
      ``polarity``（英小写，权威）为准；真实 DE 立绘取自 ``ExportUpgrades.json``
      的 ``icon``，经 browse.wf 拉取并本地缓存（离线兜底为稀有度底色占位）。

数据来源（均为 volume 挂载的 data/warframe_items/）：
  * Mods.json            warframe-items（英：name/uniqueName/rarity/polarity/
                         baseDrain/fusionLimit/type）
  * mod_full.json        {"map": {英小写: {zh,type(中),rarity(中),polarity(字母,不可靠),
                         drain,max[中文效果行]}}}
  * ExportUpgrades.json  warframe-public-export-plus（按 uniqueName：icon 真实 DE 立绘路径）

产物：data/warframe_mods/cards/<uniqueName末段>.png（256×380 RGBA）
立绘缓存：data/warframe_mods/art/<icon文件名>
素材：完全使用仓库内已 vendored 的 src/plugins/qq_bridge/game_tools/modcard/assets/
      （modFrames/ + polarities/ + msyh.ttc），构建期已 COPY 进镜像，零外网依赖。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\gen_mod_cards.py --all      # 全量合成
    .venv\\Scripts\\python.exe tools\\gen_mod_cards.py --only "聚精会神"
    .venv\\Scripts\\python.exe tools\\gen_mod_cards.py --only-en "Intensify"
模块调用（bot 按需出卡）：
    from tools.gen_mod_cards import render_mod_card
    png: bytes | None = render_mod_card("intensify")   # 英文小写；缺失返回 None
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import lzma
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------------------------------------------------------------------------
# 路径（同时兼容宿主 D:/AI/manju_agent/qq_bridge 与容器内 /app）
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "data" / "warframe_items"
MODDIR = ROOT / "data" / "warframe_mods"
CARDS = MODDIR / "cards"
ART = MODDIR / "art"
ASSETS = ROOT / "src" / "plugins" / "qq_bridge" / "game_tools" / "modcard" / "assets"
FRAMES = ASSETS / "modFrames"
#: 横版卡框（warframe.today `cardui`，281×117 / 292×120 四档：bronze/silver/gold/legendary）
WIDE_FRAMES = FRAMES / "wide"
POLARITIES = ASSETS / "polarities"
FONT_PATH = ASSETS / "msyh.ttc"

# browse.wf 实测可直连（200 image/png），cdn.warframestat.us 已 403。
# 真实 DE 立绘（mod 卡中央图）取自 ExportUpgrades.icon → browse.wf 缓存到本地。
BROWSE_WF = "https://browse.wf"
# ⚠️ 2026-11 重新调研：容器内 browse.wf 实测 403（被 WAF 拦出口 IP），
# 而 DE 官方 Public Export 的 contentHash CDN 容器内 200、且 mod 更新即同步——
# 这才是「取最新」的权威源。立绘优先走 DE 官方，browse.wf 仅作宿主兜底。
DE_BASE = "https://content.warframe.com/PublicExport/"
DE_MANIFEST_CACHE = MODDIR / "de_manifest.json"  # {mod uniqueName: textureLocation(.jpg!hash)}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ---------------------------------------------------------------------------
# 尺寸常量（对齐 WFCD：舞台 256×512，成品裁到 256×380）
# ---------------------------------------------------------------------------
STAGE_W, STAGE_H = 256, 512
CARD_W, CARD_H = 256, 380
TOP_FRAME_Y = int(STAGE_H * 0.14)        # 71
BOTTOM_FRAME_Y = int(STAGE_H * 0.65)     # 332
ART_X, ART_Y = 8, int(STAGE_H * 0.17)    # 8, 87
ART_W = STAGE_W - 16                      # 240
ART_MAX_H = 213                           # 立绘可用高度（87 → 300，给名称留位）
NAME_Y = 305
DESC_START = 322
DESC_MAX_BOTTOM = 372                     # 底栏在 376，描述不得越过
SIDELIGHT_Y = int(STAGE_H * 0.21)         # 107
BACKER_X, BACKER_Y = int(STAGE_W * 0.8), int(STAGE_H * 0.2)   # 204, 102
LOWERTAB_X = int(STAGE_W * 0.09)          # 23
LOWERTAB_Y = STAGE_H - 128 - 8            # 376

# WFCD styling.tierColor：Bronze 陶土色 / Silver 白 / Gold 暖金 / Omega 紫；
# Legendary 复用 Silver(白)。
TIER_COLOR = {
    "Bronze": (202, 154, 135),
    "Silver": (255, 255, 255),
    "Gold": (250, 231, 190),
    "Legendary": (255, 255, 255),
    "Galvanized": (255, 255, 255),
    "Amalgam": (255, 255, 255),
    "Omega": (172, 131, 213),
}
# rarity(英小写) → WFCD tier
RARITY_TIER = {
    "common": "Bronze", "uncommon": "Silver", "rare": "Gold", "legendary": "Legendary",
}


# ---------------------------------------------------------------------------
# 缓存（批量合成时避免重复开图/读 JSON）
# ---------------------------------------------------------------------------
_cache: dict = {}


def _load_json(p: Path):
    if p in _cache:
        return _cache[p]
    data = json.loads(p.read_text(encoding="utf-8"))
    _cache[p] = data
    return data


def _load_mods() -> list[dict]:
    return _load_json(ITEMS / "Mods.json")


def _mods_by_un() -> dict:
    """以 DE 唯一标识 uniqueName 为键（保证全局唯一，1733 个不重不漏）。

    WFCD mod-generator 内部即以此为准；之前用 name.lower() 做键会因「同名不同物」
    （如普通 Continuity 与 Beginner 版共用 display name）丢失条目，导致部分 mod
    永不生成卡图。这里改用 uniqueName，彻底消除重名歧义。
    """
    if "mods_by_un" in _cache:
        return _cache["mods_by_un"]
    m = {mm["uniqueName"]: mm for mm in _load_mods() if mm.get("uniqueName")}
    _cache["mods_by_un"] = m
    return m


def _name_to_uns() -> dict:
    """display name.lower() → [uniqueName, ...] 多值映射，用于按名解析到唯一物。"""
    if "name_to_uns" in _cache:
        return _cache["name_to_uns"]
    mm: dict[str, list[str]] = {}
    for m in _load_mods():
        nm = (m.get("name") or "").lower()
        un = m.get("uniqueName")
        if nm and un:
            mm.setdefault(nm, []).append(un)
    _cache["name_to_uns"] = mm
    return mm


def _canonical_un(name_lower: str) -> str | None:
    """同名多个变体时挑「主版本」（正式版 = 无变体后缀的条目）。

    同名 mod 常混入损坏版（Beginner/Intermediate）与开发遗留的 Expert 版
    （如 Deep Freeze：Beginner 2+3 / Expert 6+10 / 正式 6+5）。主版本 = 路径中
    不含任何变体后缀（Beginner/Intermediate/Expert/U13/U14）者。Prime 系列名字
    为 "Primed Xxx"（与普通版不重名），其唯一 Expert 条目会被排除后 fallback 回来。
    """
    uns = _name_to_uns().get((name_lower or "").lower())
    if not uns:
        return None
    variants = ("Beginner", "Intermediate", "Expert", "U13", "U14")
    for u in uns:
        if not any(v in u for v in variants):
            return u
    return uns[0]


def _seg_of(un: str) -> str:
    """用完整 uniqueName 生成唯一文件片段（避免仅取末段导致的 168 处末段碰撞）。"""
    return un.strip("/").replace("/", "__")


def _load_mod_full() -> dict:
    return _load_json(ITEMS / "mod_full.json").get("map", {})


def _load_export() -> dict:
    return _load_json(ITEMS / "ExportUpgrades.json")


def _font(size: int, bold: bool = False):
    try:
        # msyh.ttc 是集合字体，index 0 为常规、1 为粗体
        idx = 1 if bold else 0
        return ImageFont.truetype(str(FONT_PATH), size, index=idx)
    except Exception:  # noqa: BLE001 — 极端兜底（容器内字体必在）
        return ImageFont.load_default()


def _frame_img(tier: str, part: str) -> Image.Image | None:
    p = FRAMES / f"{tier}{part}.png"
    if not p.exists():
        # Galvanized/Amalgam 资产不完整（缺 TopRightBacker/LowerTab/CornerLights），
        # 缺的部分回退到 Gold（同为 rare 档）
        fb = {"Galvanized": "Gold", "Amalgam": "Gold"}.get(tier)
        if fb:
            p = FRAMES / f"{fb}{part}.png"
    if not p.exists():
        p = FRAMES / f"Bronze{part}.png"          # 最终回退到 Bronze
    if p.exists():
        try:
            return Image.open(p).convert("RGBA")
        except Exception:  # noqa: BLE001
            return None
    return None


def _polarity_glyph(polarity: str) -> Image.Image | None:
    if not polarity:
        return None
    p = POLARITIES / f"{polarity}.png"
    if p.exists():
        try:
            return Image.open(p).convert("RGBA")
        except Exception:  # noqa: BLE001
            return None
    return None


def _tier_of(mod: dict) -> str:
    """映射 mod → 卡框档位（决定边框样式）。共 8 种档位：

      * Common/普通   → Bronze 铜框
      * Uncommon/罕见 → Silver 银框
      * Rare/稀有     → Gold 金框
      * Legendary/传说(含 Primed/Umbral) → Legendary 亮银框
      * Riven/紫卡(type 含 Riven)        → Omega 紫晶框
      * Galvanized/镀层(name 含 Galvanized) → Galvanized 镀层框
      * Amalgam/并合(name 含 Amalgam)    → Amalgam 框
      * Peculiar/奇异(name 含 Peculiar)  → Peculiar 框
      另：Archon/执政官 统一用 Gold 金框（WFCD getTier 同款，即使存在 Legendary 变体）。
    """
    name = (mod.get("name") or "")
    t = (mod.get("type") or "")
    un = (mod.get("uniqueName") or "")
    if "Riven" in t or "Riven" in un:
        return "Omega"
    if "Galvanized" in name or "SPMod" in un:
        return "Galvanized"
    if "Amalgam" in name:
        return "Amalgam"
    if "Peculiar" in name:
        return "Peculiar"
    if "Archon" in name:
        return "Gold"
    return RARITY_TIER.get((mod.get("rarity") or "common").lower(), "Bronze")


def _is_raw_key(s: str) -> bool:
    """DE 未本地化的原始 key（例 /Lotus/Language/1999Coda/AntivirusSevenModDesc）。"""
    return bool(s) and (s.startswith("/") or "Lotus/Language" in s or "Lotus/Types" in s)


def _clean_effects(raw) -> list[str]:
    """过滤掉 DE 原始本地化 key / 空行，只保留可渲染的效果文本。"""
    out = []
    if not isinstance(raw, (list, tuple)):
        return out
    for s in raw:
        s = (s or "").strip()
        if s and not _is_raw_key(s):
            out.append(s)
    return out


def _tint(src: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """把 RGBA 图的"形状"填成纯色（对齐 WFCD drawPolarity 的 source-in）。"""
    mask = src.split()[3]
    out = Image.new("RGBA", src.size, color + (255,))
    return Image.composite(out, Image.new("RGBA", src.size, (0, 0, 0, 0)), mask)


def _unlzma_public_export(blob: bytes) -> bytes:
    """解压 DE PublicExport 的 ``*.txt.lzma``（LZMA1 alone 头部但不写 EOS）。
    与 wf_sync._unlzma_public_export 同款实现，此处内联以保证工具自包含。"""
    if len(blob) < 13:
        raise ValueError("官方索引长度不足 13 字节，疑似下载被截断")
    props = blob[0]
    dict_size = int.from_bytes(blob[1:5], "little")
    usize = int.from_bytes(blob[5:13], "little")
    if not dict_size or not usize:
        raise ValueError(f"官方索引头部非法：dict={dict_size} usize={usize}")
    lc = props % 9
    rest = props // 9
    lp, pb = rest % 5, rest // 5
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                "lc": lc, "lp": lp, "pb": pb}]
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    out = dec.decompress(blob[13:], max_length=usize)
    if len(out) != usize:
        raise ValueError(f"官方索引解压不完整：{len(out)}/{usize} 字节")
    return out


def _ensure_de_manifest(force: bool = False) -> dict:
    """确保本地有 DE 官方立绘 contentHash 映射 ``{mod uniqueName: textureLocation(.jpg!hash)}``。

    不存在（或 force）时从 DE 官方 Public Export 拉一次并裁剪缓存：
      index_en.txt.lzma → ExportManifest.json!<hash> → 只留 /Lotus/Upgrades/Mods/ 条目。
    这是「取最新」的权威源：官方 mod 一更新，manifest 的 contentHash 就跟着变。
    """
    if "de_manifest" in _cache:
        return _cache["de_manifest"]
    if DE_MANIFEST_CACHE.exists() and not force:
        try:
            data = json.loads(DE_MANIFEST_CACHE.read_text(encoding="utf-8"))
            _cache["de_manifest"] = data
            return data
        except Exception:  # noqa: BLE001
            pass
    data: dict[str, str] = {}
    try:
        idx_raw = urllib.request.urlopen(DE_BASE + "index_en.txt.lzma", timeout=30).read()
        idx_txt = _unlzma_public_export(idx_raw).decode("utf-8")
        entry = next((ln.strip() for ln in idx_txt.splitlines()
                      if ln.strip().startswith("ExportManifest.json!")), None)
        if not entry:
            raise RuntimeError("index 里找不到 ExportManifest.json")
        man_url = DE_BASE + "Manifest/" + entry
        man = json.loads(urllib.request.urlopen(man_url, timeout=60).read())["Manifest"]
        for e in man:
            un, tl = e.get("uniqueName"), e.get("textureLocation")
            if un and tl and un.startswith("/Lotus/Upgrades/Mods/"):
                data[un] = tl
        DE_MANIFEST_CACHE.parent.mkdir(parents=True, exist_ok=True)
        DE_MANIFEST_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — 拉取失败返回空映射，降级到 browse.wf
        print(f"[warn] DE manifest 拉取失败，降级 browse.wf：{type(e).__name__}: {e}")
    _cache["de_manifest"] = data
    return data


def _fetch_art(unique_name: str | None, icon: str | None) -> Image.Image | None:
    """真实 DE 立绘：优先 DE 官方 contentHash CDN（最权威、容器内可达、更新即同步），
    browse.wf 仅作宿主兜底。命中本地缓存则直接返回。"""
    # 1) DE 官方 contentHash CDN（.jpg，容器内 200）
    if unique_name:
        tl = _ensure_de_manifest().get(unique_name)
        if tl:
            fname = Path(tl.split("!", 1)[0]).name
            cache = ART / fname
            if cache.exists() and cache.stat().st_size > 500:
                try:
                    return Image.open(cache).convert("RGBA")
                except Exception:  # noqa: BLE001
                    pass
            url = DE_BASE + tl.lstrip("/")
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                    data = r.read()
                if len(data) > 500:
                    ART.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(data)
                    return Image.open(cache).convert("RGBA")
            except Exception:  # noqa: BLE001
                pass
    # 2) browse.wf 兜底（.png，宿主可达；容器内 403 会走到这里再失败→占位）
    if icon:
        fname = Path(icon).name
        cache = ART / fname
        if cache.exists() and cache.stat().st_size > 500:
            try:
                return Image.open(cache).convert("RGBA")
            except Exception:  # noqa: BLE001
                pass
        try:
            with urllib.request.urlopen(urllib.request.Request(BROWSE_WF + icon, headers=UA), timeout=25) as r:
                data = r.read()
            if len(data) > 500:
                ART.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
                return Image.open(cache).convert("RGBA")
        except Exception:  # noqa: BLE001 — 网络不可用/被墙 → 占位兜底
            return None
    return None


def _fit_art(art: Image.Image, box: tuple[int, int, int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """contain 适配：等比缩放塞进 box，水平居中、顶对齐。"""
    bx, by, bw, bh = box
    r = min(bw / art.width, bh / art.height)
    w, h = max(1, int(art.width * r)), max(1, int(art.height * r))
    im = art.resize((w, h), Image.LANCZOS)
    return im, (bx + (bw - w) // 2, by)


def _shade(img: Image.Image, percentage: float = 0.7) -> Image.Image:
    """暗化立绘（对齐 WFCD shadeImage/shadePixel）：每个像素 *= (1 - percentage)。"""
    factor = 1 - percentage
    return img.convert("RGB").point(lambda p: int(p * factor)).convert("RGBA")


def _wrap_cjk(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """CJK 友好换行（WFCD 的 wrapText 按空格切，对中文无效 → 自实现）。"""
    if not text:
        return []
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if draw.textlength(trial, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _draw_text_block(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, font,
                     color, max_w: int, line_h: int) -> int:
    """居中逐行绘制，返回末行之后的 y。"""
    for ln in _wrap_cjk(draw, text, font, max_w):
        tw = draw.textlength(ln, font=font)
        draw.text((x - tw / 2, y), ln, font=font, fill=color + (255,))
        y += line_h
    return y


def render_mod_card(english_lower: str, force: bool = False) -> bytes | None:
    """给定一个 mod 的英文名小写（mod_full 的 key），合成 WFCD 风格卡图，返回 PNG 字节。

    缺失或渲染失败时返回 None（调用方应降级到纯文本，绝不静默报错）。
    命中本地缓存（cards/<seg>.png）且非强制时直接返回缓存。
    """
    key = (english_lower or "").strip()
    if not key:
        return None
    by_un = _mods_by_un()
    un = key if key in by_un else _canonical_un(key)
    if not un:
        return None
    mod = by_un[un]
    if not mod.get("uniqueName"):
        return None
    seg = _seg_of(mod["uniqueName"])
    out = CARDS / f"{seg}.png"
    if out.exists() and not force:
        try:
            return out.read_bytes()
        except Exception:  # noqa: BLE001
            pass

    tier = _tier_of(mod)
    color = TIER_COLOR.get(tier, (255, 255, 255))

    # 中文资料（mod_full，按 display name 查——输入 key 可能是 name 也可能是 uniqueName）
    mod_name = (mod.get("name") or "").lower()
    mf = _load_mod_full().get(mod_name, {})
    name_zh = mf.get("zh") or mod.get("name") or mod_name
    type_zh = mf.get("type") or mod.get("type") or ""
    effects = _clean_effects(mf.get("max"))
    if not effects and tier != "Omega":  # 中文缺失时用 ExportUpgrades 的英文 description 兜底
        eu0 = _load_export().get(mod["uniqueName"], {})
        d = eu0.get("description")
        if d and not _is_raw_key(d):
            effects = [d]
    if tier == "Omega":
        effects = []   # 裂罅 mod 无确定效果，留给文字回复

    # 真实 DE 立绘（优先官方 contentHash CDN，browse.wf 兜底）
    eu = _load_export().get(mod["uniqueName"], {})
    art = _fetch_art(mod["uniqueName"], eu.get("icon"))

    # ---- 舞台 256×512 ----
    stage = Image.new("RGBA", (STAGE_W, STAGE_H), (0, 0, 0, 0))
    bg = _frame_img(tier, "Background")
    if bg is not None:
        stage.alpha_composite(bg, (0, 0))

    draw = ImageDraw.Draw(stage)
    nf = _font(22, bold=True)   # WFCD titleFont 22px
    df = _font(14)              # WFCD descriptionFont 14px
    max_w = int(STAGE_W * 0.85)  # WFCD maxWidth = background.width * 0.85

    # 描述行（首条效果，最多 2 行，避免压到底框）
    desc_lines: list[str] = []
    if effects:
        for ln in _wrap_cjk(draw, effects[0], df, max_w - 12):
            desc_lines.append(ln)
            if len(desc_lines) >= 2:
                break

    # 文字总高度（对齐 WFCD textHeight：标题实际高度 + 描述实际高度 + 15）
    def _bbox_h(font, text: str) -> int:
        b = font.getbbox(text)
        return b[3] - b[1]

    title_h = _bbox_h(nf, name_zh) if name_zh else 0
    desc_h = sum(_bbox_h(df, ln) for ln in desc_lines)
    mod_text_h = title_h + desc_h + 15

    # WFCD backgroundImage 动态布局：立绘占上部，文字紧跟其后
    position = int(STAGE_H * 0.17)      # 87
    thumb_w = STAGE_W - 2 * 8           # 240
    thumb_h = max(40, 256 - mod_text_h)  # 立绘原始 256 - 文字高度

    # 立绘：暗化到 30% 后拉伸（WFCD shadeImage 0.7 + drawImage 拉伸）
    if art is not None:
        shaded = _shade(art, 0.7).resize((thumb_w, thumb_h), Image.LANCZOS)
        stage.alpha_composite(shaded, (8, position))
    else:
        tint = {"Bronze": (150, 120, 80), "Silver": (120, 140, 160),
                "Gold": (190, 160, 70), "Legendary": (210, 190, 130),
                "Omega": (150, 110, 190)}.get(tier, (120, 120, 120))
        ph = Image.new("RGBA", (thumb_w, thumb_h), tint + (70,))
        stage.alpha_composite(ph, (8, position))
    position += thumb_h

    # 名称（22px，居中，tierColor；WFCD textBaseline=middle → anchor mm）
    nw = draw.textlength(name_zh, font=nf)
    draw.text((STAGE_W / 2, position + 16), name_zh, font=nf, fill=color + (255,), anchor="mm")

    position += 8 + 15  # horizontalPad + lineSpacing

    # 描述（14px，居中，每行 15px）
    if desc_lines:
        ey = position + 16  # + horizontalPad * 2
        for ln in desc_lines:
            tw = draw.textlength(ln, font=df)
            draw.text((STAGE_W / 2, ey), ln, font=df, fill=color + (255,), anchor="mm")
            ey += 15

    # 侧光（左右各一）
    sl = _frame_img(tier, "SideLight")
    if sl is not None:
        sl_l = sl.transpose(Image.FLIP_LEFT_RIGHT)
        stage.alpha_composite(sl, (STAGE_W - sl.width, SIDELIGHT_Y))
        stage.alpha_composite(sl_l, (0, SIDELIGHT_Y))

    # 耗能 + 极性背板（右上角，对齐 WFCD backerImage）
    backer = _frame_img(tier, "TopRightBacker")
    if backer is not None:
        stage.alpha_composite(backer, (BACKER_X, BACKER_Y))
    if tier == "Omega":
        bd = _font(14, bold=True)
        draw.text((BACKER_X + (backer.width * 0.25 if backer else 6),
                   BACKER_Y + (backer.height * 0.55 if backer else 12)),
                  "???", font=bd, fill=color + (255,))
    else:
        drain = mod.get("baseDrain")
        if isinstance(drain, int):
            bd = _font(15, bold=True)
            s = str(drain)
            dx = BACKER_X + (backer.width * (0.35 if len(s) >= 2 else 0.3) if backer else 4)
            dy_ = BACKER_Y + (backer.height * 0.45 if backer else 12)
            draw.text((dx, dy_), s, font=bd, fill=color + (255,))
        # 极性（权威取自 Mods.json；无对应 glyph 的 aura/umbra/universal 跳过）
        pol = mod.get("polarity")
        glyph = _polarity_glyph(pol) if pol else None
        if glyph is not None:
            gw = 18
            gx = BACKER_X + (backer.width * 0.6 if backer else 22)
            gx = min(gx, STAGE_W - gw - 2)
            gy = BACKER_Y - 2
            # 暗圆底保证极性符号在任意底色上都清晰
            draw.ellipse((gx - 2, gy - 2, gx + gw + 2, gy + gw + 2), fill=(0, 0, 0, 140))
            stage.alpha_composite(_tint(glyph, color).resize((gw, gw), Image.LANCZOS),
                                 (int(gx), int(gy)))

    # 底栏（中文类型名，对齐 WFCD lowerTabImage）
    lt = _frame_img(tier, "LowerTab")
    if lt is not None:
        stage.alpha_composite(lt, (LOWERTAB_X, LOWERTAB_Y))
    if type_zh:
        tf = _font(15, bold=True)
        tw = draw.textlength(type_zh, font=tf)
        draw.text((LOWERTAB_X + (lt.width - tw) / 2 if lt else (STAGE_W - tw) / 2,
                   LOWERTAB_Y + 4), type_zh, font=tf, fill=color + (255,))

    # 顶框
    top = _frame_img(tier, "FrameTop")
    if top is not None:
        stage.alpha_composite(top, (0, TOP_FRAME_Y))
    # 底框（含等级点 + 角光，对齐 WFCD bottomImage）
    bottom = _frame_img(tier, "FrameBottom")
    if bottom is not None:
        stage.alpha_composite(bottom, (0, BOTTOM_FRAME_Y))
        _draw_rank_pips(stage, tier, bottom, mod)

    # 裁切中段到 256×380（对齐 WFCD outterCanvas）
    card = stage.crop((0, (STAGE_H - CARD_H) // 2, STAGE_W, (STAGE_H + CARD_H) // 2))

    CARDS.mkdir(parents=True, exist_ok=True)
    card.save(out, format="PNG")
    return out.read_bytes()


def _wide_frame(tier: str, part: str) -> Image.Image | None:
    """取**横版**卡框（裁到不透明外框）。横版卡只吃横构图卡框。

    来源优先级：
      1. ``modFrames/wide/<Tier><Part>.png`` —— warframe.today `cardui` 横版框
         （FrameTop 281×117、FrameBottom 292×120，bronze/silver/gold/legendary 四档，
         2026-11-02 抓到、已 vendored 进 ``src`` 随镜像 COPY 进容器）；
      2. ``modFrames/<Tier><Part>.png`` 且**本身就是横图**（宽高比 ≥ 1.8）——
         Omega 292×120、Galvanized / Amalgam / Peculiar 512×256 都是天然横版框。

    竖版 256×128 / 256×64（Bronze/Silver/Gold/Legendary 旧资产）**明确拒绝**：
    按宽等比缩到 370 会变成 152px 高、底框被拉高 1.8 倍、翼尖顶穿卡片边界——
    这正是"边框被裁切"的根因。宁可不出框，也不硬拉竖框。
    """
    p = WIDE_FRAMES / f"{tier}{part}.png"
    if p.exists():
        im = Image.open(p).convert("RGBA")
    else:
        im = _frame_img(tier, part)
        if im is None or im.width / im.height < 1.8:
            return None
    b = im.split()[3].getbbox()
    return im.crop(b) if b else None


#: 横版卡金属描边宽度（沿四边收边，让「每种类型边框不一样」一眼可辨）
WIDE_EDGE_WIDTH = 3
#: 稀有度 → 整卡金属边框描边色（复刻参考卡 8 张母版的蓝/金/紫金属收边）。
#: 母版四边**不是深色裸边**而是金属色收边——这正是「深色满幅却不露黑边」的关键。
WIDE_EDGE_COLOR = {
    "Bronze": (176, 126, 104),      # 铜/陶土（Common）
    "Silver": (188, 208, 224),      # 冷银蓝（Uncommon）—— 母版「深层冷冻」冷蓝银
    "Gold": (226, 183, 82),         # 暖金（Rare）—— 母版「致命洪流」暖金
    "Legendary": (245, 214, 120),   # 传奇白金偏金（Legendary）—— 与冷银明确拉开
    "Galvanized": (150, 214, 230),  # 镀层/青
    "Amalgam": (170, 226, 190),     # 融合/青绿
    "Omega": (172, 131, 213),       # 紫（当前紫卡）
}
#: 稀有度 → 整卡色调浸染（复刻母版「深色满幅 + 按类型整卡异色」的质感）。
#: 立绘压暗后与本色做软光混合：Silver 冷蓝 / Gold 暖金棕 / Bronze 铜棕 /
#: Legendary 冷白金 / Omega 深紫。用户 2026-11-12 明确要的这个效果。
WIDE_TINT_COLOR = {
    "Bronze": (48, 40, 34),
    "Silver": (30, 44, 62),
    "Gold": (58, 48, 26),
    "Legendary": (46, 52, 66),
    "Galvanized": (26, 52, 58),
    "Amalgam": (32, 52, 44),
    "Omega": (46, 30, 62),
}


def _wide_fill(base: Image.Image, tint: tuple[int, int, int]) -> None:
    """把立绘底按稀有度色调浸染（软光近似：正片叠底提暗 + 加色 tint）。

    复刻母版「深色满幅但整卡有类型色」：立绘细节保留，整体压到冷蓝/暖金等
    稀有度色系，绝不做成浅灰白或透明感。
    """
    layer = Image.new("RGBA", base.size, tint + (255,))
    base.alpha_composite(layer)



def render_mod_card_wide(english_lower: str, force: bool = False) -> bytes | None:
    """横版配卡缩略卡（对齐游戏军械库/配卡界面的 370×185 横版样式，无效果描述）。

    实现与 ``tools/gen_tier_cards.py::render_tier_card`` 同款 —— 2026-11-02 定稿产物
    ``tmp/wftoday/wide_deepfreeze.png``（370×185）就是这条链路出的图：

        立绘 cover 铺满 → 灰度混合 0.45 → 压暗 0.45 → 蓝灰 tint 0.32
        → 中文名（无描边，1px 黑影）
        → 横版 FrameTop **按宽度等比**贴顶
        → 横版 FrameBottom **按宽度等比**贴底
        → 右上角 满级耗能 + 极性 → 底部等级点（y = H-14）

    **卡框一律按宽等比缩放 + 贴边对齐，绝不拉伸**。拉伸（旧的 ``bot_h=0.40·H``）会把
    底框压扁、翼尖推出卡外，就是反复出现的"边框被裁切"。
    """
    W, H = 370, 185
    key = (english_lower or "").strip()
    if not key:
        return None
    by_un = _mods_by_un()
    un = key if key in by_un else _canonical_un(key)
    if not un:
        return None
    mod = by_un[un]
    mf = _load_mod_full().get((mod.get("name") or "").lower(), {})
    name_zh = mf.get("zh") or mod.get("name") or key
    tier = _tier_of(mod)
    color = TIER_COLOR.get(tier, (255, 255, 255))
    edge_color = WIDE_EDGE_COLOR.get(tier, (198, 214, 226))
    tint = WIDE_TINT_COLOR.get(tier, (38, 44, 56))

    # 2026-11-12 定稿（用户「我就要之前那个效果，别再乱改了」）：复刻项目原始
    # 8 张母版 mod_0_0…mod_1_3 的质感——**深色满幅卡面 + 按稀有度整卡异色 +
    # 四边金属边框收边 + 右上梯形耗能标签 + 底部等级点 + 当前紫卡紫色星标**。
    # 与第19轮「圆角透明 + 浅灰白框」方向彻底相反：母版四角 alpha=255 不透明，
    # 边缘是**金属色收边**而非深色裸矩形，所以贴到浅底既不露「黑边」又是游戏卡质感。
    # base 恒 370×185 全幅不透明。
    base = Image.new("RGBA", (W, H), tint + (255,))

    # 1) 立绘：cover 铺满 → 灰度混合 → 压暗 → 按稀有度色调浸染（深色满幅，非浅灰）
    tl = _ensure_de_manifest().get(un)
    if tl:
        p = ART / Path(tl.split("!", 1)[0]).name
        if p.exists():
            try:
                art = Image.open(p).convert("RGB")
                sc = max(W / art.width, H / art.height)
                art = art.resize((int(art.width * sc), int(art.height * sc)), Image.LANCZOS)
                l = (art.width - W) // 2
                t = (art.height - H) // 2
                art = art.crop((l, t, l + W, t + H))
                gray = ImageOps.grayscale(art).convert("RGB")
                art = Image.blend(art, gray, 0.45)
                art = _shade(art, 0.52).convert("RGBA")
                # 立绘以半透明叠到稀有度色底上：保留纹理，整卡压到冷蓝/暖金等色系
                art.putalpha(150)
                base.alpha_composite(art)
                # 再铺一层稀有度色做整卡浸染，确保深色满幅、色调统一
                veil = Image.new("RGBA", (W, H), tint + (96,))
                base.alpha_composite(veil)
            except Exception:  # noqa: BLE001
                pass

    draw = ImageDraw.Draw(base)

    # 2) 卡名（粗体，1px 黑影）—— 母版为居中白字，深色卡面上高对比
    fs = 32
    fname = _font(fs, bold=True)
    while fs > 20 and draw.textlength(name_zh, font=fname) > W * 0.72:
        fs -= 2
        fname = _font(fs, bold=True)
    ny = int(H * 0.54)
    draw.text((W / 2 + 1, ny + 1), name_zh, font=fname, fill=(0, 0, 0), anchor="mm")
    draw.text((W / 2, ny), name_zh, font=fname, fill=(236, 232, 224), anchor="mm")

    # 3) 顶框：横版 FrameTop 按宽等比贴顶（真实 DE 金属翼尖）
    top = _wide_frame(tier, "FrameTop")
    if top is not None:
        th = max(1, int(top.height * W / top.width))
        base.alpha_composite(top.resize((W, th), Image.LANCZOS), (0, 0))

    # 4) 底框：横版 FrameBottom 按宽等比贴底（等比 ⇒ 翼尖完整收在卡内）
    bot = _wide_frame(tier, "FrameBottom")
    if bot is not None:
        bh = max(1, int(bot.height * W / bot.width))
        base.alpha_composite(bot.resize((W, bh), Image.LANCZOS), (0, H - bh))

    # 5) 四边金属描边收边（沿整卡外沿）：让边缘是**金属色**而非深色裸边，
    #    这是「深色满幅却不露黑边」的关键，也是母版四边被金属框包住的效果。
    edge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)
    ed.rectangle((0, 0, W - 1, H - 1), outline=edge_color + (235,), width=WIDE_EDGE_WIDTH)
    # 内圈再压一道暗边，模拟金属框的立体高光/厚度（母版边框非纯平涂）
    ed.rectangle((WIDE_EDGE_WIDTH, WIDE_EDGE_WIDTH,
                  W - 1 - WIDE_EDGE_WIDTH, H - 1 - WIDE_EDGE_WIDTH),
                 outline=(0, 0, 0, 90), width=1)
    base.alpha_composite(edge)

    # 6) 右上角：梯形耗能标签 + 极性（复刻母版右上斜角小标签）
    drain = mod.get("baseDrain")
    fl_raw = mod.get("fusionLimit")
    fusion = int(fl_raw) if isinstance(fl_raw, int) else 0
    pold = mod.get("polarity")
    glyph = _polarity_glyph(pold) if pold else None
    if tier == "Omega":
        dstr = "???"
    elif isinstance(drain, int):
        dstr = str(drain + fusion)
    else:
        dstr = "?"
    fnum = _font(20, bold=True)
    dw = draw.textlength(dstr, font=fnum)
    gsz = 22
    total = dw + (gsz if glyph else 0) + 6
    x0 = W - total - 12
    # 梯形深色标签底（右上斜角），让耗能数字在亮金属顶框上也清晰
    tag = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tag)
    td.polygon([(int(x0 - 8), 4), (W - 4, 4), (W - 4, 34), (int(x0 - 2), 34)],
               fill=(8, 10, 14, 205))
    base.alpha_composite(tag)
    draw = ImageDraw.Draw(base)
    draw.text((x0 + dw / 2, 18), dstr, font=fnum, fill=(244, 244, 246), anchor="mm")
    if glyph is not None:
        gimg = _tint(glyph, (235, 238, 245)).resize((gsz, gsz), Image.LANCZOS)
        base.alpha_composite(gimg, (int(x0 + dw + 6), 18 - gsz // 2))

    # 7) 底部等级点（官方 RankSlot；按稀有度着色；紫卡不显示）
    if tier != "Omega" and fusion > 0:
        n = min(10, fusion)
        slot = Image.open(str(FRAMES / "RankSlotActive.png")).convert("RGBA")
        slot = slot.resize((9, 9), Image.LANCZOS)
        slot = _tint(slot, edge_color)
        sy = H - 14
        sx = (W - (n * 12 - 3)) // 2
        for i in range(n):
            base.alpha_composite(slot, (sx + i * 12, sy))

    # 满幅不透明输出：四角 alpha=255（复刻母版）。绝不再用框外透明 mask / 圆角裁切。
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG")  # RGB 直出=全幅不透明，无透明圆角
    return buf.getvalue()


def _draw_rank_pips(stage: Image.Image, tier: str, bottom: Image.Image,
                    mod: dict) -> None:
    """等级点 + 角光（对齐 WFCD bottomImage）。"""
    draw = ImageDraw.Draw(stage)
    is_rare = tier in ("Omega", "Legendary")
    fusion = int(mod.get("fusionLimit") or 0)
    max_rank = min(10, fusion)
    if max_rank <= 0:
        return
    # 角光
    cl = _frame_img(tier, "CornerLights")
    if cl is not None:
        cl_l = cl.transpose(Image.FLIP_LEFT_RIGHT)
        ch = (bottom.height * 0.32 if is_rare else bottom.height * 0.24)
        cy = BOTTOM_FRAME_Y + ch
        draw_img = stage.alpha_composite
        draw_img(cl, (int(bottom.width * 0.76), int(cy)))
        draw_img(cl_l, (int(-bottom.width * 0.01), int(cy)))
    # 满级连线
    if fusion >= max_rank:
        rcl = FRAMES / "RankCompleteLine.png"
        if rcl.exists():
            try:
                line = Image.open(rcl).convert("RGBA")
                stage.alpha_composite(line, (0, int(BOTTOM_FRAME_Y + bottom.height * (0.84 if is_rare else 0.74))))
            except Exception:  # noqa: BLE001
                pass
    # 等级槽
    act = FRAMES / "RankSlotActive.png"
    emp = FRAMES / "RankSlotEmpty.png"
    if not (act.exists() and emp.exists()):
        return
    if max_rank <= 3:
        start = bottom.width * 0.425
    elif max_rank <= 5:
        start = bottom.width * 0.39
    else:
        start = bottom.width * 0.29
    slot_y = BOTTOM_FRAME_Y + int(bottom.height * (0.82 if is_rare else 0.72))
    active = Image.open(act).convert("RGBA").resize((10, 10), Image.LANCZOS)
    empty = Image.open(emp).convert("RGBA").resize((10, 10), Image.LANCZOS)
    for i in range(max_rank):
        slot = active if i < max_rank else empty  # 默认显示满级（全亮）
        stage.alpha_composite(slot, (int(start + i * 11), slot_y))


def prefetch_all_art(workers: int = 10) -> int:
    """并行预拉取全部 mod 真实 DE 立绘到本地缓存（优先 DE 官方 contentHash CDN）。

    已缓存的跳过；返回本次新下载张数。渲染阶段只读缓存、零外网，避免同步期
    串行外呼拖慢周更。DE 官方 CDN 容器内可达且更新即同步；browse.wf 容器内 403 只作宿主兜底。
    """
    ART.mkdir(parents=True, exist_ok=True)
    man = _ensure_de_manifest(force=True)  # 强制刷一次，保证拿到最新 mod 的 contentHash
    seen: set[str] = set()
    jobs: list[tuple[str, str]] = []  # (uniqueName, url)
    # 以 mod 列表为准：每个 mod 按其 uniqueName 查 manifest 的立绘 contentHash
    for m in _load_mods():
        un = m.get("uniqueName")
        if not un:
            continue
        tl = man.get(un)
        if not tl:
            continue
        fn = Path(tl.split("!", 1)[0]).name
        if fn in seen:
            continue
        seen.add(fn)
        dst = ART / fn
        if dst.exists() and dst.stat().st_size > 500:
            continue
        jobs.append((un, DE_BASE + tl.lstrip("/")))

    def _get_one(job: tuple[str, str]) -> int:
        un, url = job
        fn = Path(url.split("!", 1)[0].rsplit("/", 1)[-1])
        dst = ART / fn
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                data = r.read()
            if len(data) > 500:
                dst.write_bytes(data)
                return 1
        except Exception:  # noqa: BLE001 — 单张失败不影响整体
            return 0
        return 0

    got = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for n in ex.map(_get_one, jobs):
            got += n
    return got


def render_missing(limit: int = 0) -> int:
    """增量合成：只渲染本地尚不存在卡图的 mod（周更用，避免每周全量重渲染）。

    返回成功生成的张数。立绘走缓存，新 mod 才外呼 browse.wf。
    """
    CARDS.mkdir(parents=True, exist_ok=True)
    mods = _load_mods()
    ok = 0
    done = 0
    for m in mods:
        un = m.get("uniqueName")
        if not un:
            continue
        seg = _seg_of(un)
        if seg and (CARDS / f"{seg}.png").exists():
            continue
        if limit and done >= limit:
            break
        done += 1
        try:
            if render_mod_card(un):
                ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {m.get('name')}: {type(e).__name__}: {e}")
    return ok


# ---------------------------------------------------------------------------
# CLI（批量合成，供 wf_sync 周更调用；与按需出卡共用同一渲染管线）
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="全量合成所有 mod")
    ap.add_argument("--missing", action="store_true", help="只合成本地缺失的卡图（周更增量）")
    ap.add_argument("--prefetch-art", action="store_true", help="先并行预拉取真实 DE 立绘到缓存（与 --all/--missing 组合）")
    ap.add_argument("--only", default="", help="按中文名精确合成一张")
    ap.add_argument("--only-en", default="", help="按英文名小写精确合成一张")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.prefetch_art:
        n = prefetch_all_art()
        print(f"立绘预拉取完成 {n} 张 → {ART}")

    mods = _load_mods()
    targets: list[dict] = []
    if args.only:
        mf = _load_mod_full()
        en = next((k for k, v in mf.items() if v.get("zh") == args.only), None)
        targets = [m for m in mods if m["name"].lower() == (en or "")] if en else \
                  [m for m in mods if (m.get("name") or "").lower() == args.only.lower()]
    elif args.only_en:
        targets = [m for m in mods if (m.get("name") or "").lower() == args.only_en.lower()]
    elif args.limit:
        targets = mods[: args.limit]
    elif args.all:
        targets = mods
    elif args.missing:
        n = render_missing()
        print(f"增量合成完成 {n} 张 → {CARDS}")
        return 0
    else:
        print("请给 --all / --missing / --only / --only-en / --limit")
        return 1
    if not targets:
        print("没有匹配的 mod")
        return 1

    CARDS.mkdir(parents=True, exist_ok=True)
    ok = 0
    for m in targets:
        try:
            png = render_mod_card(m.get("uniqueName"), force=True)
            if png:
                ok += 1
        except Exception as e:  # noqa: BLE001 — 单张失败不影响整体
            print(f"[warn] {m.get('name')}: {type(e).__name__}: {e}")
    print(f"合成完成 {ok}/{len(targets)} 张 → {CARDS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
