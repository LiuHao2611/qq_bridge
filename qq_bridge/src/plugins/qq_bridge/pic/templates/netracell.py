# -*- coding: utf-8 -*-
"""pic/templates/netracell.py —— Warframe 科研任务（Netracells）专属卡片。

照用户参考卡（结构/内容全量还原，配色去粉色）重写：
  * 浅冷灰蓝页面底 + 淡纹纹理；白色圆角子卡（蓝灰描边）；
  * 顶部居中标题（左侧四角星装饰）+ 居中「本周科研任务」；
  * 区块标题条「·本周深层科研任务 / ·本周时光科研任务」（藏青底白字，无 一/二 编号）；
  * 任务子卡：左侧手绘图标 + 蓝加粗「类型 名称」标题（类型与名称间用空格，与官方卡一致）
    + (可选)蓝色描述段 + ★ 词条条目（琥珀色名称 + 灰蓝官方效果描述，逐条换行、描述自动折行）；
  * 区块末 2×2 额外选项卡（每张带琥珀色「额外」胶囊标 + 灰色效果描述）；
  * 底部淡蓝条「·剩余时间  红色数字」（精确到秒）+ 右下星点装饰（不再渲染页脚署名）。

说明：参考卡右下角为动漫少女插画，因原始素材不在工程内、且其发色与卡片底色同为粉色，
无法直接干净抠图，故以同主题「星点」装饰代替；用户若提供素材置于
pic/assets/netracell_girl.png 即自动贴入。配色严格无粉色。

重要：本模板只负责「排版」，**不持有任何任务名/描述**。任务内容来自
game_tools/warframe.py 的 _netracell_sections（按 worldstate 内部代码查
game_tools/netracell_zh.py 官方译名表），新的一周换任务无需改本文件。

输入（build 的 params）：
  sections: [{"highlight":"深层",
              "missions":[{"type","name","risks":[{"name","desc"}],"description":""}],
              "extras":[{"name","desc"}]}]
  title: "本周科研任务"
  remain: "2天19小时23分"

2026-09-24 编辑器可编辑化（无创增量）：保留 decor.elem_tf / decor.background /
bg_overrides["netracell"] 钩子（未配置 = 本主题默认观感，编辑器可改色/拖动/缩放）。
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from .. import decor
from ..canvas import Canvas
from ..theme import Theme

# ---- 固定配色（浅冷灰蓝 · 藏青/蓝 · 琥珀，无粉色）----
C_PAGE = "#EDF1F7"        # 页面外背景（浅冷灰蓝）
C_PAGE_TEX = "#DCE5F1"    # 页面纹理（略深，极淡）
C_CARD = "#FFFFFF"        # 子卡底色（白）
C_CARD_LINE = "#C9D6E8"   # 子卡描边（蓝灰）
C_TITLE = "#1F3A5F"       # 顶部标题（深藏青）
C_SECT_BG = "#2E5A88"     # 区块标题条（藏青蓝）
C_SECT_BG2 = "#234A73"    # 区块条描边
C_SECT_TEXT = "#FFFFFF"    # 区块条文字
C_MISSION_TITLE = "#1D4ED8"  # 任务标题（蓝）
C_DESC = "#3E5C82"        # 描述段（中蓝）
C_ENTRY_LABEL = "#B45309"  # 词条名称（琥珀，非粉）
C_ENTRY_DESC = "#5B6B7E"  # 词条效果描述（灰蓝）
C_EXTRA_CARD = "#F3F7FC"  # 额外卡底色（极淡蓝）
C_EXTRA_LINE = "#C9D6E8"  # 额外卡描边
C_EXTRA_TITLE = "#1F3A5F" # 额外卡标题（藏青）
C_EXTRA_LABEL_BG = "#FCD34D"   # 「额外」胶囊底色（琥珀）
C_EXTRA_LABEL_TX = "#7C4A03"   # 「额外」胶囊文字
C_REMAIN_BAR = "#E4ECF6"  # 剩余时间条底色
C_REMAIN_LABEL = "#2E5A88"  # 剩余时间标签
C_REMAIN = "#DC2626"      # 剩余时间数字（红，非粉）
C_FOOTER = "#5B6B7E"      # 署名（灰）
C_LINE = "#D3DEEC"        # 区块间分割线
C_STAR = "#9DB4D4"        # 星点装饰
C_STAR_HI = "#FCD34D"     # 高亮星点（琥珀）
C_ICON_BG = "#DCE7F5"     # 任务图标底（淡蓝）
C_ICON_RING = "#7FA6D4"   # 任务图标环
C_PC = "#2E9E5B"          # 页脚 PC 绿圆

# ---- 排版 ----
PAD = 22
SUBPAD = 14
ICON_R = 17
ICON_D = ICON_R * 2
FS_TITLE = 32
FS_SECT = 22
FS_MISSION = 21
FS_DESC = 17
FS_ENTRY = 17
FS_LABEL = 20
FS_EXTRA = 18
FS_EXTRA_DESC = 14
FS_REMAIN = 23
FS_FOOT = 14
GRID_GAP = 10
DECO_D = 56

_ICON_KIND = {
    "镜像防御": "shield", "防御": "shield", "圣物": "gem", "歼灭": "cross",
    "生存": "heart", "刺杀": "target",
}

_COLOR_MAP: dict[str, tuple[str, str]] = {
    "page_color":   (C_PAGE,       "页面底色"),
    "card_color":   (C_CARD,       "子卡底色"),
    "border_color": (C_CARD_LINE,  "子卡描边"),
    "title_color":  (C_TITLE,      "标题"),
    "sect_color":   (C_SECT_BG,    "区块条底色"),
    "sect_text_color": (C_SECT_TEXT, "区块条文字"),
    "type_color":   (C_MISSION_TITLE, "任务标题"),
    "desc_color":   (C_DESC,       "描述文字"),
    "entry_label_color": (C_ENTRY_LABEL, "词条名称"),
    "dim_color":    (C_ENTRY_DESC, "词条效果"),
    "bubble_bg":    (C_EXTRA_CARD, "额外卡底色"),
    "bubble_line":  (C_EXTRA_LINE, "额外卡描边"),
    "bubble_text":  (C_EXTRA_TITLE, "额外卡标题"),
    "extra_label_color": (C_EXTRA_LABEL_BG, "额外胶囊底色"),
    "remain_color": (C_REMAIN,     "剩余时间"),
    "remain_label_color": (C_REMAIN_LABEL, "剩余时间标签"),
    "line_color":   (C_LINE,       "分割线"),
    "footer_color": (C_FOOTER,     "署名"),
}


def _pal(t: Theme) -> dict[str, str]:
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("netracell") if isinstance(ov.get("netracell"), dict) else {}
    out: dict[str, str] = {}
    for key, (default, _lab) in _COLOR_MAP.items():
        s = v.get(key)
        out[key] = s if (isinstance(s, str) and s) else default
    return out


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _safe_text(v) -> str:
    """防泄漏：剥离 Warframe 内部代号残留的 /Lotus/.../ 路径与前缀（安全网，不伪造中文）。"""
    s = str(v or "").strip()
    if "/Lotus/" in s:
        s = s.rsplit("/", 1)[-1]
    for _p in ("PersonalMod_", "Mod_", "Conquest_", "Mission_"):
        if s.startswith(_p):
            s = s[len(_p):]
    return s


# ===========================================================================
# 手绘图形（全部矢量，规避 emoji 豆腐框；配色无粉色）
# ===========================================================================
def _spark(c: Canvas, cx: int, cy: int, r: int, color: str) -> None:
    """四角星（横竖椭圆交叉），用于条目/标题/页脚装饰。"""
    c.ellipse((cx - r, cy - r * 0.32, cx + r, cy + r * 0.32), fill=color)
    c.ellipse((cx - r * 0.32, cy - r, cx + r * 0.32, cy + r), fill=color)


def _mission_icon(c: Canvas, cx: int, cy: int, r: int, kind: str) -> None:
    c.ellipse((cx - r, cy - r, cx + r, cy + r), fill=C_ICON_BG)
    c.ellipse((cx - r, cy - r, cx + r, cy + r), outline=C_ICON_RING, width=2)
    if kind == "shield":
        c.draw.rounded_rectangle([cx - r * 0.6, cy - r * 0.55, cx + r * 0.6, cy + r * 0.7],
                                 radius=r * 0.3, outline="#FFFFFF", width=2)
    elif kind == "heart":
        c.ellipse((cx - r * 0.5, cy - r * 0.5, cx + r * 0.05, cy + r * 0.05), fill="#FFFFFF")
        c.ellipse((cx - r * 0.05, cy - r * 0.5, cx + r * 0.5, cy + r * 0.05), fill="#FFFFFF")
        c.draw.polygon([(cx - r * 0.5, cy - r * 0.05), (cx + r * 0.5, cy - r * 0.05),
                        (cx, cy + r * 0.6)], fill="#FFFFFF")
    elif kind == "cross":
        c.draw.line([cx - r * 0.55, cy, cx + r * 0.55, cy], fill="#FFFFFF", width=3)
        c.draw.line([cx, cy - r * 0.55, cx, cy + r * 0.55], fill="#FFFFFF", width=3)
    elif kind == "target":
        c.ellipse((cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5), outline="#FFFFFF", width=2)
        c.ellipse((cx - r * 0.2, cy - r * 0.2, cx + r * 0.2, cy + r * 0.2), fill="#FFFFFF")
    elif kind == "gem":
        c.draw.polygon([(cx, cy - r * 0.6), (cx + r * 0.55, cy),
                        (cx, cy + r * 0.6), (cx - r * 0.55, cy)], outline="#FFFFFF", width=2)
    else:
        for i in range(5):
            a = math.radians(i * 72 - 90)
            px = cx + math.cos(a) * r * 0.5
            py = cy + math.sin(a) * r * 0.5
            c.ellipse((px - r * 0.28, py - r * 0.34, px + r * 0.28, py + r * 0.34), fill="#FFFFFF")


def _draw_texture(c: Canvas, w: int, h: int, color: str) -> None:
    rnd = random.Random(20261030)
    for _ in range(40):
        x = rnd.randint(0, w)
        y = rnd.randint(0, h)
        r = rnd.randint(3, 6)
        _spark(c, x, y, r, color)


def _draw_girl(c: Canvas, x: int, y: int) -> bool:
    p = Path(__file__).resolve().parent / "assets" / "netracell_girl.png"
    if not p.is_file():
        return False
    try:
        from PIL import Image
        c.paste(Image.open(p).convert("RGBA"), (x, y))
        return True
    except Exception:  # noqa: BLE001
        return False


# ===========================================================================
# 高度测量（与绘制共用同一套字号/行高，保证缩放参与排版流）
# ===========================================================================
def _entry_lines(rk: dict, cw_in: int, fs_e: int) -> list[str]:
    """词条名称 + 效果描述折行。"""
    name = _safe_text(rk.get("name", ""))
    desc = _safe_text(rk.get("desc", ""))
    lines: list[str] = []
    if name:
        lines.append("★ " + name)
    if desc:
        for ln in Canvas.wrap(desc, fs_e, "regular", cw_in - 22):
            lines.append("  " + ln)
    return lines or ["★ " + name]


def _mission_card_h(m: dict, cw: int, fsz, pct, si: int, mi: int) -> int:
    fs_t = fsz("miss-title-%d-%d" % (si, mi), FS_MISSION)
    fs_d = fsz("miss-desc-%d-%d" % (si, mi), FS_DESC)
    fs_e = fsz("miss-entry-%d-%d" % (si, mi), FS_ENTRY)
    cw_in = cw - (SUBPAD * 2 + ICON_D + 8)
    h = fs_t + 6
    desc = (m.get("description") or "").strip()
    if desc:
        h += len(Canvas.wrap(desc, fs_d, "regular", cw_in)) * (fs_d + 5) + 4
    h += 2
    for rk in (m.get("risks") or []):
        h += len(_entry_lines(rk, cw_in, fs_e)) * (fs_e + 6)
    content = max(h, ICON_D + 10)
    return content + SUBPAD * 2


def _extra_cell_h(ex: dict, col_w: int, fsz, pct, si: int, k: int) -> int:
    fs_x = fsz("extra-%d-%d" % (si, k), FS_EXTRA)
    fs_d = fsz("extra-desc-%d-%d" % (si, k), FS_EXTRA_DESC)
    name = _safe_text(ex.get("name", ""))
    desc = _safe_text(ex.get("desc", ""))
    h = fs_x + 10                       # 标题行
    if desc:
        h += len(Canvas.wrap(desc, fs_d, "regular", col_w - 20)) * (fs_d + 5)
    return h + 12


def _extras_h(extras: list, cw: int, fsz, pct, si: int) -> int:
    fs_l = fsz("label-%d" % si, FS_LABEL)
    h = fs_l + 10
    col_w = (cw - GRID_GAP) // 2
    rows = [extras[i:i + 2] for i in range(0, len(extras), 2)] or [[]]
    for row in rows:
        rh = 0
        for j, ex in enumerate(row):
            rh = max(rh, _extra_cell_h(ex, col_w, fsz, pct, si, len(row) and j or 0))
        h += rh + GRID_GAP
    return h


def _sect_bar_h(fsz, si: int) -> int:
    return fsz("sect-%d" % si, FS_SECT) + 18


def _measure(sections, content_w, fsz, pct) -> int:
    y = PAD
    y += FS_TITLE + 16
    y += 14
    for si, sec in enumerate(sections):
        y += _sect_bar_h(fsz, si) + 12
        for mi, m in enumerate(sec["missions"]):
            y += _mission_card_h(m, content_w, fsz, pct, si, mi) + 10
        y += 6
        y += _extras_h(sec["extras"], content_w, fsz, pct, si) + 16
        y += 16
    y += FS_REMAIN + 22
    y += 14
    y += PAD
    return y


# ===========================================================================
# 构建
# ===========================================================================
def build(t: Theme, sections=None, title: str = "本周科研任务",
          remain: str = "", width: int | None = None, source: str | None = None,
          **kw) -> Canvas:
    sections = sections or []
    width = int(width or 600)
    x0 = PAD
    content_w = width - PAD * 2
    P = _pal(t)
    off, fsz, pct = decor.elem_tf(t, "netracell")

    h = _measure(sections, content_w, fsz, pct)
    c = Canvas(width, h, P["page_color"], theme=t)
    decor.background(c, t, tpl="netracell", only_override=True)
    _draw_texture(c, width, h, C_PAGE_TEX)

    y = PAD
    # ---- 顶部标题（居中 + 左侧四角星装饰）----
    tdx, tdy = off("title")
    fs_t = fsz("title", FS_TITLE)
    tw = _w(title, fs_t, "bold")
    deco_r = fs_t * 0.42
    block_left = (width - (tw + deco_r * 2 + 12)) // 2 + tdx
    star_cx = block_left + deco_r
    tx = block_left + deco_r * 2 + 12
    ty = y + tdy
    _spark(c, star_cx, ty + fs_t // 2, deco_r, C_STAR_HI)
    c.text((tx, ty + fs_t // 2), title, size=fs_t, color=P["title_color"],
           role="bold", anchor="lm")
    c.add_region("ov:title_color", [tx - 10, ty, tx + tw + 10, ty + fs_t + 6],
                 label="标题", tip="点击修改标题颜色", type="color")
    c.regions[-1]["values"] = {"ov:title_color": P["title_color"]}
    c.regions[-1]["ek"] = "title"
    y += fs_t + 16
    y += 14

    for si, sec in enumerate(sections):
        # ---- 区块标题条 ----
        sbx, sby = off("sect-%d" % si)
        fs_s = fsz("sect-%d" % si, FS_SECT)
        bar_h = _sect_bar_h(fsz, si)
        bx0, by0 = x0 + sbx, y + sby
        c.rounded_rect((bx0, by0, x0 + content_w + sbx, by0 + bar_h),
                       radius=12, fill=P["sect_color"])
        dot_y = by0 + bar_h // 2
        c.ellipse((bx0 + 16 - 4, dot_y - 4, bx0 + 16 + 4, dot_y + 4), fill="#FFFFFF")
        lbl = "本周%s科研任务" % sec.get("highlight", "")
        c.text((bx0 + 30, dot_y), lbl, size=fs_s, color=P["sect_text_color"],
               role="bold", anchor="lm")
        c.add_region("ov:sect_color", [bx0, by0, x0 + content_w + sbx, by0 + bar_h],
                     label="区块条", tip="点击修改区块条底色/文字色", type="color")
        c.regions[-1]["values"] = {"ov:sect_color": P["sect_color"],
                                   "ov:sect_text_color": P["sect_text_color"]}
        c.regions[-1]["ek"] = "sect-%d" % si
        y += bar_h + 12

        # ---- 任务子卡 ----
        for mi, m in enumerate(sec["missions"]):
            mdx, mdy = off("miss-%d-%d" % (si, mi))
            ch = _mission_card_h(m, content_w, fsz, pct, si, mi)
            card_x, card_y = x0 + mdx, y + mdy
            c.rounded_rect((card_x, card_y, x0 + content_w + mdx, card_y + ch),
                           radius=14, fill=P["card_color"], outline=P["border_color"], width=2)
            ix = card_x + SUBPAD + ICON_R
            iy = card_y + SUBPAD + ICON_R
            _mission_icon(c, ix, iy, ICON_R, _ICON_KIND.get(m.get("type", ""), "flower"))
            tx0 = card_x + SUBPAD + ICON_D + 8
            cw_in = content_w - (SUBPAD * 2 + ICON_D + 8)
            cy = card_y + SUBPAD
            fs_mt = fsz("miss-title-%d-%d" % (si, mi), FS_MISSION)
            c.text((tx0, cy + fs_mt // 2), "%s %s" % (m.get("type", ""), m.get("name", "")),
                   size=fs_mt, color=P["type_color"], role="bold", anchor="lm")
            cy += fs_mt + 6
            desc = (m.get("description") or "").strip()
            if desc:
                fs_d = fsz("miss-desc-%d-%d" % (si, mi), FS_DESC)
                for ln in Canvas.wrap(desc, fs_d, "regular", cw_in):
                    c.text((tx0, cy + fs_d // 2), ln, size=fs_d, color=P["desc_color"], anchor="lm")
                    cy += fs_d + 5
                cy += 4
            cy += 2
            fs_e = fsz("miss-entry-%d-%d" % (si, mi), FS_ENTRY)
            for rk in (m.get("risks") or []):
                for li, ln in enumerate(_entry_lines(rk, cw_in, fs_e)):
                    col = P["entry_label_color"] if li == 0 else P["dim_color"]
                    bold = "bold" if li == 0 else "regular"
                    c.text((tx0, cy + fs_e // 2), ln, size=fs_e, color=col,
                           role=bold, anchor="lm")
                    cy += fs_e + 6
            y += ch + 10
        y += 6

        # ---- 额外选项 2×2 网格 ----
        lbx, lby = off("label-%d" % si)
        fs_l = fsz("label-%d" % si, FS_LABEL)
        c.text((x0 + lbx, y + lby + fs_l // 2), "额外选项：", size=fs_l,
               color=P["title_color"], role="bold", anchor="lm")
        y += fs_l + 10
        fs_x = fsz("extra-0-0", FS_EXTRA)
        col_w = (content_w - GRID_GAP) // 2
        extras = sec["extras"] or []
        rows = [extras[i:i + 2] for i in range(0, len(extras), 2)] or [[]]
        for row in rows:
            row_h = 0
            cells = []
            for j, ex in enumerate(row):
                cells.append(_extra_cell_h(ex, col_w, fsz, pct, si, j))
            row_h = max(cells) if cells else (fs_x + 22)
            for j, ex in enumerate(row):
                gx = x0 + j * (col_w + GRID_GAP)
                gy = y
                c.rounded_rect((gx, gy, gx + col_w, gy + row_h), radius=10,
                               fill=P["bubble_bg"], outline=P["bubble_line"], width=1)
                name = _safe_text(ex.get("name", ""))
                desc = _safe_text(ex.get("desc", ""))
                c.text((gx + 10, gy + 10 + fs_x // 2), name, size=fs_x,
                       color=P["bubble_text"], role="bold", anchor="lm")
                lab = "额外"
                lw = _w(lab, 13, "bold") + 14
                lh = 18
                lx0 = gx + col_w - lw - 8
                ly0 = gy + 6
                c.rounded_rect((lx0, ly0, lx0 + lw, ly0 + lh), radius=9, fill=P["extra_label_color"])
                c.text((lx0 + lw // 2, ly0 + lh // 2), lab, size=13,
                       color=C_EXTRA_LABEL_TX, role="bold", anchor="mm")
                dy = gy + 10 + fs_x + 6
                if desc:
                    fs_d = fsz("extra-desc-%d-%d" % (si, j), FS_EXTRA_DESC)
                    for ln in Canvas.wrap(desc, fs_d, "regular", col_w - 20):
                        c.text((gx + 10, dy + fs_d // 2), ln, size=fs_d,
                               color=P["dim_color"], anchor="lm")
                        dy += fs_d + 5
                c.add_region("ov:bubble_bg", [gx, gy, gx + col_w, gy + row_h],
                             label="额外卡", tip="点击修改额外卡配色", type="color")
                c.regions[-1]["values"] = {"ov:bubble_bg": P["bubble_bg"],
                                           "ov:bubble_line": P["bubble_line"],
                                           "ov:bubble_text": P["bubble_text"]}
                c.regions[-1]["ek"] = "bub-%d-%d" % (si, j)
            y += row_h + GRID_GAP
        y += 16
        c.line((x0, y, x0 + content_w, y), fill=P["line_color"], width=2)
        y += 16

    # ---- 剩余时间条 ----
    if remain:
        rmx, rmy = off("remain")
        fs_r = fsz("remain", FS_REMAIN)
        bar_h = fs_r + 22
        bx0, by0 = x0 + rmx, y + rmy
        c.rounded_rect((bx0, by0, x0 + content_w + rmx, by0 + bar_h),
                       radius=12, fill=C_REMAIN_BAR)
        dy = by0 + bar_h // 2
        c.ellipse((bx0 + 18 - 4, dy - 4, bx0 + 18 + 4, dy + 4), fill=P["remain_label_color"])
        c.text((bx0 + 32, dy), "剩余时间", size=fs_r, color=P["remain_label_color"],
               role="bold", anchor="lm")
        c.text((x0 + content_w + rmx - 10, dy), remain, size=fs_r,
               color=P["remain_color"], role="bold", anchor="rm")
        c.add_region("ov:remain_color", [bx0, by0, x0 + content_w + rmx, by0 + bar_h],
                     label="剩余时间", tip="点击修改剩余时间配色", type="color")
        c.regions[-1]["values"] = {"ov:remain_color": P["remain_color"]}
        c.regions[-1]["ek"] = "remain"
        y += bar_h

    # ---- 右下星点装饰（动漫少女插画占位；按用户要求去除底部 Warframe Rabbit 署名）----
    if not _draw_girl(c, width - PAD - DECO_D + 6, y - 8):
        _spark(c, width - PAD - 16, y + 6, 13, C_STAR_HI)
        _spark(c, width - PAD - 36, y, 8, C_STAR)

    return c
