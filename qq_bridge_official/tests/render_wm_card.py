# -*- coding: utf-8 -*-
"""wm 市价卡布局自检：渲染一张参考卡同构的卡片，产出 PNG 供人眼比对。

不联网、不用真 WM 数据：用假头像/假图标 + 与 market_text 完全一致的标记串，
验证 guide.build 的 @wmhdr 头条块、卖单表列序（卖家/状态/声望/数量/白金价格）、
价格渐变与最低价标绿、间距与背景。

用法： python tests/render_wm_card.py
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins.qq_bridge.pic import render_raw  # noqa: E402
from src.plugins.qq_bridge.pic.theme import resolve  # noqa: E402

OUT = Path(__file__).resolve().parent


def _avatar(seed: int) -> bytes:
    """假头像：确定性配色 + 头肩剪影（模拟玩家图，非真人照片）。"""
    size = 96
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = ((seed * 47) % 200 + 30, (seed * 91) % 200 + 30, (seed * 57) % 200 + 30)
    d.ellipse((0, 0, size, size), fill=bg + (255,))
    fg = (240, 240, 240, 235)
    hr = size * 0.17
    d.ellipse((size / 2 - hr, size * 0.30 - hr, size / 2 + hr, size * 0.30 + hr), fill=fg)
    d.ellipse((size * 0.20, size * 0.52, size * 0.80, size * 1.10), fill=fg)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _icon() -> bytes:
    """假物品图标：深青底 + 亮点（模拟 MOD 卡面，非真实资产）。"""
    size = 160
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        f = y / size
        d.line((0, y, size, y), fill=(int(10 + 30 * f), int(40 + 70 * f), int(60 + 70 * f), 255))
    d.ellipse((size * 0.28, size * 0.22, size * 0.72, size * 0.66),
              fill=(250, 210, 120, 255))
    d.ellipse((size * 0.38, size * 0.32, size * 0.62, size * 0.56),
              fill=(255, 245, 200, 255))
    d.rectangle((0, size * 0.78, size, size * 0.92), fill=(20, 30, 40, 225))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _body() -> str:
    rows = [
        ("Bett_tt", "在线", 0, 1, 27),
        ("ggdd69", "在线", 4, 1, 28),
        ("Revolver_Z", "在线", 2, 1, 28),
        ("tron12819", "游戏中", 2, 1, 30),
        ("HonestKitty3831", "游戏中", 0, 2, 30),
        ("FaHa0401", "在线", 23, 1, 30),
        ("Navsult", "在线", 0, 1, 30),
        ("Selunnete", "游戏中", 1, 1, 35),
        ("WhiteSpectrumTTV", "游戏中", 9, 4, 39),
        ("KiberPunkRU", "游戏中", 9, 1, 40),
    ]
    lines = [
        "@wmhdr|抵近射击 Prime|Primed Point Blank|传奇 · MOD · 主武器 · 霰弹枪|不可兑换|1,000,000|1265|68|icons/primed_point_blank.png|+165% 伤害",
        "@hdr|卖家|状态|声望|数量|白金价格",
    ]
    for i, (name, st, rep, qty, price) in enumerate(rows, 1):
        lines.append(f"@row|{name}|{st}|{rep}|{qty}|{price}|u{i}|")
    lines += [
        "@mstat|48时内零级售价|62|77|75",
        "@mstat|90天内零级售价|50|110|75",
        "@mstat|48时内零级收价|40|58|52",
        "@mstat|90天内零级收价|35|60|48",
        "@shout|/w Bett_tt Hi! I want to buy: Primed Point Blank for 27 platinum. (wf.m via qq_bridge)",
        '@page|页码:1/127 使用"wm 抵近射击 Prime -2"查看下一页.',
        "@foot|WARFRAME.MARKET / PC",
    ]
    return "\n".join(lines)


def _body_ranked() -> str:
    """MOD 6 列样例（2026-11-10「展示物品等级」）：混合 零级/R1/满级(R3)，
    与 Hammer Shot 真实订单分布同构，验证等级标签混排 + 6 列布局 + 向后兼容。
    """
    rows = [
        ("Bett_tt", "零级", "在线", 0, 1, 27),
        ("ggdd69", "满级", "在线", 4, 1, 128),
        ("Revolver_Z", "R1", "在线", 2, 1, 110),
        ("tron12819", "满级", "游戏中", 2, 1, 134),
        ("HonestKitty3831", "零级", "游戏中", 0, 2, 101),
        ("FaHa0401", "满级", "在线", 23, 1, 140),
        ("Navsult", "R1", "在线", 0, 1, 115),
        ("Selunnete", "满级", "游戏中", 1, 1, 145),
        ("WhiteSpectrumTTV", "零级", "游戏中", 9, 4, 120),
        ("KiberPunkRU", "满级", "游戏中", 9, 1, 150),
    ]
    lines = [
        "@wmhdr|重锤射击|Hammer Shot|稀有 · MOD · 主武器|不可兑换|—|437|312|icons/hammer_shot.png|+240% 暴击伤害",
        "@hdr|卖家|等级|状态|声望|数量|白金价格",
    ]
    for i, (name, rk, st, rep, qty, price) in enumerate(rows, 1):
        lines.append(f"@row|{name}|{rk}|{st}|{rep}|{qty}|{price}|u{i}|")
    lines += [
        "@mstat|48时内全等级售价|100|3000|134",
        "@mstat|90天内全等级售价|100|3000|128",
        "@mstat|48时内全等级收价|100|150|110",
        "@mstat|90天内全等级收价|100|150|105",
        "@shout|/w Bett_tt Hi! I want to buy: Hammer Shot for 27 platinum. (wf.m via qq_bridge)",
        '@page|页码:1/44 使用"wm 重锤射击 -2"查看下一页.',
        "@foot|WARFRAME.MARKET / PC",
    ]
    return "\n".join(lines)


def _body_arcane() -> str:
    """赋能（Arcane）6 列样例（2026-11-10「满级展示满级，否则 0级」）：
    Arcane Energize 真实市场 R0 原料单（7-9p）+ R5 满级成品单（400-900p）混排。
    赋能等级已由 warframe._rank_prefix 二值化：R0~R4→0级、R5→满级，无 R1~R4。
    """
    rows = [
        ("AlkaTale", "0级", "在线", 2, 1, 7),
        ("annanjing", "0级", "在线", 0, 1, 7),
        ("Denia_x", "0级", "游戏中", 27, 8, 8),
        ("HURTLHX", "0级", "游戏中", 9, 24, 8),
        ("Ca1Ya", "0级", "游戏中", 26, 22, 8),
        ("Seccowo", "0级", "在线", 4, 7, 8),
        ("IminousPrime", "0级", "游戏中", 2, 2, 8),
        ("wshywx", "0级", "游戏中", 0, 2, 8),
        ("R5_Hunter", "满级", "在线", 200, 1, 420),
        ("R5_Seller", "满级", "游戏中", 145, 1, 899),
    ]
    lines = [
        "@wmhdr|赋能·充沛|Arcane Energize|传奇 · 赋能|不可兑换|—|1667|79|icons/arcane_energize.png|60% 几率为自己和 15 米内友军恢复 150 能量",
        "@hdr|卖家|等级|状态|声望|数量|白金价格",
    ]
    for i, (name, rk, st, rep, qty, price) in enumerate(rows, 1):
        lines.append(f"@row|{name}|{rk}|{st}|{rep}|{qty}|{price}|u{i}|")
    lines += [
        "@mstat|48时内全等级售价|5|140|8",
        "@mstat|90天内全等级售价|3|170|7",
        "@mstat|48时内全等级收价|1|120|3",
        "@mstat|90天内全等级收价|1|150|4",
        "@shout|/w AlkaTale Hi! I want to buy: Arcane Energize for 7 platinum. (wf.m via qq_bridge)",
        '@page|页码:1/167 使用"wm 赋能充沛 -2"查看下一页.',
        "@foot|WARFRAME.MARKET / PC",
    ]
    return "\n".join(lines)


def main() -> int:
    avatars = {f"u{i}": _avatar(i) for i in range(1, 11)}
    icon = _icon()
    outs = []
    for body, tag in ((_body(), ""), (_body_ranked(), "_ranked"),
                      (_body_arcane(), "_arcane")):
        for mode in ("light", "neon"):
            t = resolve(mode)
            png = render_raw("guide", t, body=body, title="", avatars=avatars,
                             item_icon=icon, source="数据源：Warframe.Market · 订单更新 2026-11-10")
            p = OUT / f"_wm_card_{mode}{tag}.png"
            p.write_bytes(png)
            outs.append(p)
            print(f"{mode}{tag}: {len(png)} bytes -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
