# -*- coding: utf-8 -*-
"""生成 8 种 mod 卡框档位（含紫卡）+ Archon 的代表横版卡，用于验证 tier 判定与卡框映射。

用法：.venv\\Scripts\\python.exe tools\\gen_tier_cards.py
输出：data/warframe_mods/tier_cards/<tier>.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import gen_mod_cards as g
OUT = ROOT / "data" / "warframe_mods" / "tier_cards"

W, H = 370, 185


def load_frame(tier: str, part: str) -> Image.Image | None:
    """取卡框：优先 warframe.today 横版卡框，兜底 genesis-assets。"""
    # warframe.today 横版卡框（本地 tmp/wftoday 缓存）
    p = ROOT / "tmp" / "wftoday" / f"{tier.lower()}-{part}.png"
    if not p.exists():
        p = ROOT / "tmp" / "wftoday" / f"{tier}-{part}.png"
    # genesis-assets（Galvanized/Amalgam/Peculiar/Omega 等横版卡框）
    if not p.exists():
        p = g.FRAMES / f"{tier}{part}.png"
    if not p.exists():
        return None
    im = Image.open(p).convert("RGBA")
    b = im.split()[3].getbbox()
    return im.crop(b) if b else None


def render_tier_card(name_zh: str, english: str, out: str) -> None:
    un = g._canonical_un(english)
    mod = g._mods_by_un()[un] if un else None
    tier = g._tier_of(mod) if mod else "Bronze"
    polarity = (mod or {}).get("polarity")
    drain = (mod or {}).get("baseDrain")
    fusion = int((mod or {}).get("fusionLimit") or 0)

    # 立绘：优先 DE manifest，兜底 ExportUpgrades icon
    art = None
    if mod:
        eu = g._load_export().get(un, {})
        art = g._fetch_art(un, eu.get("icon"))
        if art is not None:
            art = art.convert("RGB")
    if art is None:
        art = Image.new("RGB", (256, 256), (40, 44, 52))

    # 横版铺满 + 蓝灰压暗
    sc = max(W / art.width, H / art.height)
    art = art.resize((int(art.width * sc), int(art.height * sc)), Image.LANCZOS)
    l = (art.width - W) // 2
    t = (art.height - H) // 2
    art = art.crop((l, t, l + W, t + H))
    gray = ImageOps.grayscale(art).convert("RGB")
    art = Image.blend(art, gray, 0.45)
    art = g._shade(art, 0.45).convert("RGB")
    tint = Image.new("RGB", (W, H), (28, 38, 52))
    art = Image.blend(art, tint, 0.32)

    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    base.paste(art, (0, 0))
    draw = ImageDraw.Draw(base)

    # 卡名（粗体、偏大）
    fs = 32
    fname = g._font(fs, bold=True)
    while fs > 20 and draw.textlength(name_zh, font=fname) > W * 0.72:
        fs -= 2
        fname = g._font(fs, bold=True)
    ny = int(H * 0.55)
    draw.text((W / 2 + 1, ny + 1), name_zh, font=fname, fill=(0, 0, 0), anchor="mm")
    draw.text((W / 2, ny), name_zh, font=fname, fill=(232, 228, 220), anchor="mm")

    # 卡框（按 tier）
    top = load_frame(tier, "FrameTop") if tier in ("Galvanized", "Amalgam", "Peculiar", "Omega") \
        else load_frame(tier.lower(), "frame-top")
    if top is None:
        top = load_frame(tier, "FrameTop")
    if top:
        th = int(top.height * W / top.width)
        base.alpha_composite(top.resize((W, th), Image.LANCZOS), (0, 0))
    bot = load_frame(tier, "FrameBottom") if tier in ("Galvanized", "Amalgam", "Peculiar", "Omega") \
        else load_frame(tier.lower(), "frame-bottom")
    if bot is None:
        bot = load_frame(tier, "FrameBottom")
    if bot:
        bh = int(bot.height * W / bot.width)
        base.alpha_composite(bot.resize((W, bh), Image.LANCZOS), (0, H - bh))

    # 右上角：数字（紫卡显示 ???）+ 极性
    pol = g._polarity_glyph(polarity) if polarity else None
    fnum = g._font(20, bold=True)
    if tier == "Omega":
        dstr = "???"
    elif isinstance(drain, int):
        dstr = str(drain + fusion)
    else:
        dstr = "?"
    dw = draw.textlength(dstr, font=fnum)
    gsz = 22
    pol_w = gsz if pol else 0
    total = dw + pol_w + 6
    x0 = W - total - 10
    # 紫卡数字加黑色描边，确保在紫框上清晰
    if tier == "Omega":
        draw.text((x0 + dw / 2 + 1, 19), dstr, font=fnum, fill=(0, 0, 0), anchor="mm")
    draw.text((x0 + dw / 2, 18), dstr, font=fnum, fill=(240, 240, 240), anchor="mm")
    if pol:
        pol = g._tint(pol, (235, 238, 245)).resize((gsz, gsz), Image.LANCZOS)
        base.alpha_composite(pol, (int(x0 + dw + 6), 18 - gsz // 2))

    # 等级点（紫卡不显示等级点）
    if tier != "Omega" and fusion > 0:
        n = min(10, fusion)
        slot = Image.open(str(g.FRAMES / "RankSlotActive.png")).convert("RGBA")
        slot = slot.resize((9, 9), Image.LANCZOS)
        sy = H - 14
        total = n * 12 - 3
        sx = (W - total) // 2
        for i in range(n):
            base.alpha_composite(slot, (sx + i * 12, sy))

    out_img = Image.new("RGB", (W, H), (16, 18, 22))
    out_img.paste(base.convert("RGB"), (0, 0), base.split()[3])
    out_img.save(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # 8 种档位 + Archon 的代表 mod（英文名, 中文名, 档位）
    reps = [
        ("vitality", "生命力", "Bronze"),
        ("deep freeze", "深层冷冻", "Silver"),
        ("lethal torrent", "致命洪流", "Gold"),
        ("primed heated charge", "火焰装填 Prime", "Legendary"),
        ("galvanized shot", "镀层 准确射手", "Galvanized"),
        ("amalgam serration", "并合 膛线", "Amalgam"),
        ("peculiar bloom", "奇异绽放", "Peculiar"),
        ("archgun riven mod", "曲翼枪械裂罅 Mod", "Omega"),
        ("archon continuity", "执政官 川流不息", "Archon"),
    ]
    for en, zh, tier in reps:
        out = OUT / f"{tier}.png"
        render_tier_card(zh, en, str(out))
        print(f"  生成 {tier:12s} -> {out.name}  ({zh})")


if __name__ == "__main__":
    main()
