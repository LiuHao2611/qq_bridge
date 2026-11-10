# -*- coding: utf-8 -*-
"""pic/templates/riven.py —— 紫卡 MOD 卡牌模板（模拟开卡专用）。

2026-09-17 v3：直接用用户提供的游戏内紫卡截图（riven_base.png）做底图，
只在固定位置随机替换文字（武器名/英文名/词条/段位/洗数/词条数）。

底图坐标（640×860 已标定）：
  * 右上角：洗数（大字白）
  * 中部：武器名（金 42px 居中）、英文名（灰 20px 居中）
  * 词条两行：正词条亮白 / 负词条玫红（左对齐 x≈210）
  * 左下：段位；右下：词条数
  * 底部 y≈830：数据源小字

body 结构化行（由 game_tools.warframe 的 sim_riven 生成）：
  WEAPON|中文名|英文名|武器类型
  DISP|1.35
  POS|+292.1% 触发几率
  NEG|-36.3% 对 Corpus 伤害
  META|段位 6 洗数 8
  NOTE|模拟开卡纯属娱乐，出货请勿当真
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

from ..canvas import Canvas
from ..theme import Theme

W = 640
H = 860
_BASE = Path(__file__).resolve().parent / "riven_base.png"
_WHITE = "#FFFFFF"
_GOLD = "#FFD977"
_SUB = "#C8C0E8"
_POS = "#FFF6DC"
_NEG = "#F08CB8"
_WEAK = "#B9B4C9"


def _load_base(width: int = W, height: int = H) -> Image.Image:
    img = Image.open(str(_BASE)).convert("RGB")
    # 目标比例 640:860≈0.744；原图 304:391≈0.777 → 先按目标比例裁再缩放，避免形变
    w, h = img.size
    target = width / height
    cur = w / h
    if cur > target:  # 太宽 → 裁左右
        nw = int(h * target)
        x0 = (w - nw) // 2
        img = img.crop((x0, 0, x0 + nw, h))
    else:  # 太高 → 裁上下
        nh = int(w / target)
        y0 = (h - nh) // 2
        img = img.crop((0, y0, w, y0 + nh))
    img = img.resize((width, height), Image.LANCZOS)
    # 轻度锐化补偿放大糊感
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
    return img


def build(t: Theme, body: str = "", title: str | None = None,
          source: str | None = None, width: int = W) -> Canvas:
    weapon_zh = weapon_en = wtype = ""
    pos, neg, meta, note = [], [], "", ""
    for ln in (body or "").splitlines():
        ln = ln.strip()
        if ln.startswith("WEAPON|"):
            parts = ln.split("|", 3)
            weapon_zh = parts[1] if len(parts) > 1 else ""
            weapon_en = parts[2] if len(parts) > 2 else ""
            wtype = parts[3] if len(parts) > 3 else ""
        elif ln.startswith("POS|"):
            pos.append(ln[4:])
        elif ln.startswith("NEG|"):
            neg.append(ln[4:])
        elif ln.startswith("META|"):
            meta = ln[5:]
        elif ln.startswith("NOTE|"):
            note = ln[5:]

    rolls = 0
    mastery = ""
    if meta:
        try:
            m = meta.replace("段位", "").replace("洗数", ",")
            parts = [x.strip() for x in m.split(",") if x.strip()]
            if len(parts) >= 1:
                mastery = parts[0]
            if len(parts) >= 2:
                rolls = int(parts[1])
        except Exception:  # noqa: BLE001
            pass

    c = Canvas(width, height := H, bg="#000000")
    c.paste(_load_base(width, height), (0, 0))

    # ---- 盖住底图原有文字（半透明暗板，透出底图纹理，坐标按 640×860 标定）----
    for _x0, _y0, _x1, _y1 in (
        (430, 20, 640, 150),    # 右上洗数（含底图顶部旧数字）
        (116, 408, 580, 578),   # 武器名 + 英文名
        (172, 522, 578, 668),   # 词条两行
        (96, 688, 650, 832),    # 左下段位 + 右下词条数（含旧循环图标）
    ):
        c.rounded_rect((_x0, _y0, _x1, _y1), 12, fill="#1C1130", fill_alpha=235)

    # ---- 右上角：洗数 ----
    c.text((width - 42, 92), "%d" % rolls, size=34, color=_WHITE, role="bold", anchor="ra")

    # ---- 武器名（金 居中）----
    if weapon_zh:
        c.text((width // 2, 470), weapon_zh, size=44, color=_GOLD, role="bold", anchor="ma")

    # ---- 英文名/类型（灰 居中）----
    _sub = "  ".join(x for x in (weapon_en, wtype) if x)
    if _sub:
        c.text((width // 2, 538), _sub, size=20, color=_SUB, role="regular", anchor="ma")

    # ---- 词条两行（左对齐 x≈210）----
    rows = pos + neg
    _rx = 210
    for k, txt in enumerate(rows[:2]):
        _is_neg = k >= len(pos)
        c.text((_rx, 568 + k * 44), txt, size=27, color=(_NEG if _is_neg else _POS),
               role="bold", anchor="la")

    # ---- 左下：段位 ----
    if mastery:
        c.text((136, 722), "段位 %s" % mastery, size=22, color=_WHITE, role="bold", anchor="la")

    # ---- 右下：词条数 ----
    c.text((width - 132, 722), "%d" % min(len(rows), 2), size=28, color=_GOLD, role="bold", anchor="ra")

    # ---- 底部：数据源小字（右下角，半透明感）----
    if source:
        c.text((width - 36, height - 26), source, size=13, color=_WEAK, role="regular", anchor="ra")
    return c
