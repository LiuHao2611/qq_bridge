# -*- coding: utf-8 -*-
"""配卡图片卡渲染（横版 MOD 卡版式）。

版式对齐 Warframe Rabbit「配卡推荐」：深青渐变底 + 微软雅黑，
MOD 配置区为 **横版缩略卡**（2:1，军械库/配卡界面样式），
由 ``tools/gen_mod_cards.py::render_mod_card_wide`` 用官方卡框资产 + DE 官方立绘
+ 中文名 + 能耗/极性/等级点 **实时合成**（绝不自画卡框）。

数据来源：``data/warframe_items/builds.json``（社区可扩展本地库，来源标注 overframe）。
武器图/赋能图取自 WFCD 平台 ``data/img``，缓存于 ``weapons/`` ``arcanes/``。
overframe 本机 403、无公开 API，故不做实时抓取；未收录武器由命令层如实引导，绝不编造。

运行预览（手动出图核对观感，不参与运行时）：
    python -m qq_bridge.src.plugins.qq_bridge.game_tools.build_card
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---- 横版 MOD 卡合成器（真实卡框资产，复用 #wf mod 同一套生成器）----
try:
    from tools.gen_mod_cards import render_mod_card_wide  # type: ignore
except Exception:  # noqa: BLE001 — 项目根不在 sys.path 时补一次
    import sys
    _ROOT = Path(__file__).resolve().parents[4]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    try:
        from tools.gen_mod_cards import render_mod_card_wide  # type: ignore
    except Exception:  # noqa: BLE001
        render_mod_card_wide = None  # type: ignore

# ---- 字体（vendored 微软雅黑，bake 进镜像，Docker 内也可用）----
_FONT_BUNDLED = Path(__file__).resolve().parent / "modcard" / "assets" / "msyh.ttc"


def F(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(_FONT_BUNDLED), size, index=1 if bold else 0)
    except Exception:  # noqa: BLE001
        fallback = "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"
        return ImageFont.truetype(fallback, size)


# ---- 配色（深青渐变，与灵化周常卡同一套）----
W_CARD = 900
MARGIN = 36
BG_TOP = (33, 96, 103)
BG_BOT = (11, 24, 26)
WHITE = (236, 246, 246)
CYAN = (74, 196, 206)
DIM = (158, 178, 178)
PANEL = (22, 46, 49)
PANEL_HI = (30, 64, 68)
DIV = (52, 78, 80)
ACCENT = (200, 235, 235)
MOD_BORDER = (70, 120, 124)


# ===========================================================================
# 基础工具
# ===========================================================================
def _vgrad(w: int, h: int, c1, c2) -> Image.Image:
    base = Image.new("RGB", (w, h))
    px = base.load()
    for y in range(h):
        t = y / max(1, h)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return base


def _wrap(draw, text: str, font, maxw: int) -> list[str]:
    """中文按字符宽度换行（遇 \\n 强制断行）。"""
    out: list[str] = []
    for para in (text or "").split("\n"):
        if not para:
            out.append("")
            continue
        line = ""
        for ch in para:
            trial = line + ch
            if draw.textlength(trial, font=font) <= maxw or not line:
                line = trial
            else:
                out.append(line)
                line = ch
        if line:
            out.append(line)
    return out


def _round(draw, box, radius, fill, outline=None, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=outline, width=width or 1)


def _open(path: str):
    try:
        im = Image.open(path)
        return im.convert("RGBA") if im.mode != "RGBA" else im
    except Exception:  # noqa: BLE001
        return None


def _place_contain(img: Image.Image, path: str, box, target: int,
                   border: bool = True, outline: tuple = ACCENT) -> bool:
    """真实图（武器/赋能）等比 contain 进 box 并居中贴；失败返回 False。"""
    ic = _open(path)
    if ic is None:
        return False
    iw, ih = ic.size
    if not iw or not ih:
        return False
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    scale = min(target / ih, bw / iw)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    ic = ic.resize((nw, nh), Image.LANCZOS)
    x = x0 + (bw - nw) // 2
    y = y0 + (bh - nh) // 2
    if border:
        dd = ImageDraw.Draw(img)
        _round(dd, (x - 2, y - 2, x + nw + 2, y + nh + 2), 10, PANEL_HI)
        _round(dd, (x - 2, y - 2, x + nw + 2, y + nh + 2), 10, outline, width=2)
    img.paste(ic, (x, y), ic)
    return True


# ---- 横版 MOD 卡缓存（同一次渲染内避免重复合成）----
_MOD_CARD_CACHE: dict[str, Image.Image | None] = {}


def _mod_card(en: str, w: int, h: int):
    """按英文名合成横版 MOD 卡（370×180）并缩放到 (w,h)；失败返回 None。"""
    key = (en or "").strip().lower()
    if not key:
        return None
    if key not in _MOD_CARD_CACHE:
        im = None
        if render_mod_card_wide is not None:
            try:
                raw = render_mod_card_wide(key)
                if raw:
                    im = Image.open(io.BytesIO(raw)).convert("RGBA")
            except Exception:  # noqa: BLE001 — 单卡失败不拖垮整张
                im = None
        _MOD_CARD_CACHE[key] = im
    im = _MOD_CARD_CACHE.get(key)
    if im is None:
        return None
    return im.resize((w, h), Image.LANCZOS)


#: 有 Exilus 武器槽的类别。官方 `Exilus Weapon Adapter` 描述原文：
#: "Fuses with a Primary, Secondary or Melee weapon to unlock the Exilus Mod Slot"
#: （Archgun/Archmelee 没有，而本项目只读 Primary/Secondary/Melee 三张表）。
#: 含义：这三类武器都是 **8 主槽 + 1 Exilus = 9 格**，槽位是武器固有的，
#: 不能因为数据源只列了 8 张卡就把第 9 格藏起来。
EXILUS_CATS = ("主武器", "副武器", "近战武器")


def _empty_slot(d, box, title: str, note: str) -> None:
    """画一个「空卡槽」：虚框 + 槽位名 + 原因。

    为什么需要它：主/副/近战武器实际是 **8 个主槽 + 1 个 Exilus 槽 = 9 格**。
    warframe.today 只列 8 张主槽卡，Exilus 那张通常写在正文里、多数干脆没写。
    少了这一格，卡片看起来就是「少了一张卡」。这里把第 9 格画出来，但**不编卡名**：
    知道的填真卡（见 build_fetch 的正文提取），不知道的就明确标「该来源未给出」。
    """
    x1, y1, x2, y2 = box
    _round(d, box, 9, PANEL, outline=DIV, width=1)
    # 虚线感：沿边框点 4 角短线，和实心 MOD 卡区分开
    seg = 10
    for (ax, ay, bx, by) in ((x1 + 6, y1 + 6, x1 + 6 + seg, y1 + 6),
                             (x2 - 6 - seg, y2 - 6, x2 - 6, y2 - 6)):
        d.line([(ax, ay), (bx, by)], fill=DIV, width=2)
    f1 = F(17, True)
    w1 = d.textlength(title, font=f1)
    d.text((x1 + ((x2 - x1) - w1) / 2, y1 + (y2 - y1) / 2 - 20), title, font=f1, fill=DIM)
    if note:
        f2 = F(13)
        w2 = d.textlength(note, font=f2)
        d.text((x1 + ((x2 - x1) - w2) / 2, y1 + (y2 - y1) / 2 + 4), note, font=f2, fill=DIM)


def _section_title(draw, x, y, text):
    """小节标题：青色竖条 + 标题字。"""
    draw.rectangle([(x, y + 4), (x + 5, y + 30)], fill=CYAN)
    draw.text((x + 16, y + 2), text, font=F(24, True), fill=WHITE)


# ===========================================================================
# 主渲染
# ===========================================================================
def render_build(data: dict) -> bytes:
    """渲染一条配卡为 PNG bytes。

    data 约定（由 warframe.build_card_data 产出）：
        {"zh","en","icon","set":{index,tag,tier,forma,version,status,author,link,
         mods:[{name,en,rank,max,polarity,note}], arcane:{name,rank,image},
         summary, howto}, "page","total"}
    """
    zh = data.get("zh") or data.get("en") or "?"
    en = data.get("en") or ""
    s = data.get("set") or {}
    mods = list(s.get("mods") or [])
    # 槽位是武器固有的：主/副/近战都是 8 主槽 + 1 Exilus = 9 格。
    # 数据源（warframe.today）只给 8 张主槽卡，手写条目也可能只写 8 张 ——
    # 这里按武器类别补齐第 9 格；卡名不知道就画明确标注的空槽，绝不编。
    if (data.get("cat") or "") in EXILUS_CATS and len(mods) == 8:
        mods.append({"name": "Exilus 槽", "en": "", "rank": 0, "max": 0,
                     "polarity": "", "note": "该来源未给出",
                     "slot": "exilus", "placeholder": True})
    arcane = s.get("arcane") or {}
    summary = s.get("summary") or ""
    howto = s.get("howto") or ""

    items_dir = _find_items_dir()

    def _res(rel):
        return str(items_dir / rel) if rel else None

    icon_path = _res(data.get("icon"))

    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    body_font = F(19)

    # ---- 尺寸常量 ----
    inner_w = W_CARD - 2 * MARGIN
    gap = 12
    cols = 4
    col_w = (inner_w - (cols - 1) * gap) // cols          # 198
    mod_h = max(1, round(col_w * 185 / 370))              # 99（2:1，与 render_mod_card_wide 一致，防底框被压/切）
    rows = max(1, math.ceil(len(mods) / cols)) if mods else 0
    grid_h = rows * mod_h + max(0, rows - 1) * gap

    sum_lines = _wrap(tmp, summary, body_font, inner_w - 24) if summary else []
    how_lines = _wrap(tmp, howto, body_font, inner_w - 24) if howto else []

    def sec_h(lines):
        return (34 + len(lines) * 28 + 12) if lines else 0

    top_pad = 24
    header_h = 60
    title_h = 96
    sub_h = 32
    gap_v = 16
    arcane_h = 64
    page_h = 28
    footer_h = 52
    # 赋能区：只有真的知道赋能时才占高度（自动抓取的条目没有赋能，
    # 别留一个写着「—」的空框，看着像数据缺了）
    _has_arcane = bool((arcane or {}).get("name"))
    H = (top_pad + header_h + title_h + sub_h
         + (sec_h(sum_lines) + gap_v if sum_lines else 0)
         + (34 + grid_h + gap_v if rows else 0)
         + (34 + arcane_h + gap_v if _has_arcane else 0)
         + (sec_h(how_lines) + gap_v if how_lines else 0)
         + page_h + footer_h + 20)

    img = _vgrad(W_CARD, H, BG_TOP, BG_BOT)
    d = ImageDraw.Draw(img)
    x = MARGIN
    y = top_pad

    # ---- 头部：# 配卡推荐 ----
    d.rectangle([(x, y), (x + 6, y + header_h - 16)], fill=CYAN)
    d.text((x + 18, y + 4), "# 配卡推荐", font=F(32, True), fill=WHITE)
    y += header_h
    d.line([(x, y), (W_CARD - x, y)], fill=DIV, width=1)
    y += 14

    # ---- 武器标题（真实武器图 + 中文名 + 英文名 + 副信息）----
    icon_box = (x, y, x + 88, y + 88)
    if icon_path and _place_contain(img, icon_path, icon_box, 82, border=True, outline=ACCENT):
        pass
    else:
        _round(d, icon_box, 14, PANEL_HI, outline=CYAN, width=2)
    name_x = x + 108
    d.text((name_x, y - 2), zh, font=F(42, True), fill=WHITE)
    if en:
        d.text((name_x, y + 52), "(%s)" % en, font=F(20), fill=DIM)
    y += title_h
    # 副信息行：缺字段就跳过，不要拼出 "? Forma · 版本  · " 这种噪声
    _parts = ["第%s套" % s.get("index", 1), s.get("tag") or "", s.get("tier") or ""]
    if str(s.get("forma") or "").strip() not in ("", "?"):
        _parts.append("%s Forma" % s["forma"])
    if s.get("version"):
        _parts.append("版本 %s" % s["version"])
    if s.get("status"):
        _parts.append(str(s["status"]))
    sub = " · ".join(p for p in _parts if p)
    d.text((x, y), sub, font=F(20), fill=CYAN)
    y += sub_h + 4

    # ---- 玩法摘要 ----
    if sum_lines:
        _section_title(d, x, y, "玩法摘要")
        y += 34
        for ln in sum_lines:
            d.text((x + 4, y), ln, font=body_font, fill=WHITE)
            y += 28
        y += 12 + gap_v

    # ---- MOD 配置（4 列横版缩略卡）----
    if rows:
        _section_title(d, x, y, "MOD 配置")
        y += 34
        for i, m in enumerate(mods):
            r, c = divmod(i, cols)
            sx = x + c * (col_w + gap)
            sy = y + r * (mod_h + gap)
            if m.get("placeholder"):
                _empty_slot(d, (sx, sy, sx + col_w, sy + mod_h),
                            m.get("name") or "空槽", m.get("note") or "")
                continue
            card = _mod_card(m.get("en") or m.get("name") or "", col_w, mod_h)
            if card is not None:
                # 直接贴图，不再套圆角描边：官方卡框自身带翼尖/亮边，外加 1px 圆角
                # 描边会压住底框左右角，看起来像"边框被裁切"。
                img.paste(card, (sx, sy), card)
            else:
                # 诚实降级：无卡时画面板 + 中文名（不画假卡框）
                _round(d, (sx, sy, sx + col_w, sy + mod_h), 9, PANEL, outline=DIV, width=1)
                nf = F(16, True)
                nl = _wrap(d, m.get("name", ""), nf, col_w - 16)
                ty = sy + mod_h // 2 - len(nl[:2]) * 10
                for ln in nl[:2]:
                    tw = d.textlength(ln, font=nf)
                    d.text((sx + (col_w - tw) / 2, ty), ln, font=nf, fill=WHITE)
                    ty += 20
        y += grid_h + gap_v

    # ---- 赋能（真实赋能图 + 名 + 等级）----
    if _has_arcane:
        _section_title(d, x, y, "赋能")
        y += 34
        _round(d, (x, y, x + inner_w, y + arcane_h), 12, PANEL_HI, outline=CYAN, width=1)
        ax = x + 18
        if arcane.get("image"):
            _place_contain(img, _res(arcane["image"]), (ax, y + 10, ax + 44, y + 54),
                           42, border=True, outline=ACCENT)
            ax += 56
        d.text((ax, y + 18), arcane.get("name", "—"), font=F(23, True), fill=WHITE)
        if arcane.get("rank"):
            d.text((ax + d.textlength(arcane.get("name", ""), font=F(23, True)) + 14,
                    y + 22), arcane.get("rank", ""), font=F(18), fill=CYAN)
        y += arcane_h + gap_v

    # ---- 怎么用 ----
    if how_lines:
        _section_title(d, x, y, "怎么用")
        y += 34
        for ln in how_lines:
            d.text((x + 4, y), ln, font=body_font, fill=DIM)
            y += 28
        y += 12 + gap_v

    # ---- 翻页提示 ----
    total = int(data.get("total") or 1)
    page = int(data.get("page") or 1)
    if total > 1:
        pg = "页码 %d/%d · 用『配卡 %s %s %s -%d』看下一页" % (
            page, total, zh, s.get("tag", ""), s.get("tier", ""), (page % total) + 1)
    else:
        pg = "该武器仅 1 套本地配卡；更多流派可到 overframe.gg 搜 %s" % en
    d.text((x, y), pg, font=F(16), fill=CYAN)
    y += page_h

    # ---- 来源脚注（按条目真实来源标注，禁止把 warframe.today 的数据写成 overframe）----
    d.line([(x, y), (W_CARD - x, y)], fill=DIV, width=1)
    y += 8
    _src = s.get("source") or {}
    if _src:
        _l1 = "来源：%s · %s" % (_src.get("site") or "社区", _src.get("url") or "")
    else:
        _l1 = "来源：overframe · %s · overframe.gg%s" % (
            s.get("author") or "社区", s.get("link") or "")
    d.text((x, y), _l1, font=F(14), fill=DIM)
    d.text((x, y + 22), "[PC] Warframe Rabbit 风格复刻 · 横版卡由官方卡框资产合成",
           font=F(13), fill=DIM)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


# ===========================================================================
# 数据目录定位
# ===========================================================================
def _find_items_dir() -> Path:
    """定位 data/warframe_items（向上遍历，兼容不同 checkout 布局）。"""
    cur = Path(__file__).resolve().parent
    for _ in range(8):
        cand = cur / "data" / "warframe_items"
        if cand.exists():
            return cand
        cur = cur.parent
    return Path(__file__).resolve().parents[4] / "data" / "warframe_items"


# ===========================================================================
# 预览（手动核对观感，不参与运行时）
# ===========================================================================
def _preview():
    items = _find_items_dir()
    lib = json.loads((items / "builds.json").read_text(encoding="utf-8"))
    w = next(iter(lib["weapons"].values()))
    s = w["sets"][0]
    data = {"zh": w["zh"], "en": w["en"], "icon": w.get("icon"),
            "set": s, "page": 1, "total": len(w["sets"])}
    out = items / "preview_laetum_build.png"
    out.write_bytes(render_build(data))
    print("preview ->", out)


if __name__ == "__main__":
    _preview()
