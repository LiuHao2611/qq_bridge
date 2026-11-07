# -*- coding: utf-8 -*-
"""pic/templates/fissure.py —— 虚空裂隙专属卡片（速览/普通/钢铁 三卡共用）。

照 Rabbit「速览/钢铁/普通虚空裂隙」：浅色白卡，居中标题+分割线，
「矿工的日常：」说明，纪元分组（·前纪(T2)…），每行：
[蓝色气泡: 派系+普通/钢铁+任务] **真实星球奖杯图标** 节点(紫色)  倒计时(橙色右对齐)，
末尾 *输入提示行（灰色小字）。

星球图标（2026-11-05 用户要求「裂隙卡也缺少对应星球图标」）：
  * 与警报卡同源：``pic.item_icons`` 从 DE PublicExport 清单解析 ``<Planet>TrophyBronze``，
    业务层在 render 前 ``ensure_fissure_icons`` 预取落盘，``build`` 只做同步缓存命中；
  * 裁切表共用 ``item_icons.planet_crop()``（球体裁到 0.62、非球体单独量），两卡不会各改各的；
  * 取不到才回落灰蓝圆点，卡片永远能出图。

2026-09-24 编辑器可编辑化（无创增量）：
  * 卡片专属配色：``t.bg_overrides["fissure"]`` 里的语义色键（title_color /
    node_color / bubble_bg …）覆盖硬编码默认值——**不配时渲染与旧版逐字节一致**；
  * 注册交互热区（add_region）：编辑器预览图上点元素直接改对应专属色。
2026-09-24 全量自由编辑（无创增量）：
  * 元素微调：``bg_overrides["fissure"].elem_pos / elem_scl``（decor.elem_tf）——
    每个元素可独立 拖动改位（像素偏移，不参与排版流）与 缩放（字号/行高随缩放
    **参与排版流**，卡片高度自适应）；未配置时渲染与旧版逐字节一致；
  * 每个热区带 ``ek`` 元素键，编辑器据此写回 elem_pos / elem_scl。
自检：``build(boxes=[])`` 收集每个元素的真实包围盒，供 ``tools/check_fissure_layout.py``
（溢出/重叠/间距）在交付前强制校验。
"""
from __future__ import annotations

from .. import decor
from ..canvas import Canvas
from ..icon_paste import paste_icon
from ..item_icons import planet_crop
from ..theme import Theme

C_PAGE = "#E9EDF3"
C_CARD = "#FFFFFF"
C_BORDER = "#D3DAE4"
C_TEXT = "#2B3A4A"
C_DIM = "#8A94A6"
C_TITLE = "#1F4E79"
C_TIER = "#2B3A4A"
C_NODE = "#6C3483"     # 节点 紫
C_REMAIN = "#D35400"   # 倒计时 橙
C_BUB_BG = "#D6E6F5"
C_BUB_LINE = "#5B9BD5"
C_BUB_TEXT = "#1F4E79"
C_LINE = "#D9DEE6"

PAD = 30
FS_TITLE = 36
FS_INTRO = 22
FS_TIER = 24
FS_BUB = 22
FS_NODE = 24
FS_FOOT = 20
ROW_H = 46
BUB_PAD_X = 14
BUB_PAD_Y = 8
GAP_AFTER_BUB = 14       # 气泡 → 星球图标
GAP_AFTER_ICON = 12      # 星球图标 → 节点文字
PLANET_SZ = 26           # 星球奖杯图标（本卡节点字号 24，图标比警报卡略大才配得上）
C_PLANET = "#8E9FB5"     # 星球几何回落色（取不到奖杯时的小圆点）

#: 专属配色键 → (默认常量, 前端浮窗标签)。渲染与编辑器共用这一份映射。
_COLOR_MAP: dict[str, tuple[str, str]] = {
    "page_color":   (C_PAGE,    "页面底色"),
    "card_color":   (C_CARD,    "卡片底色"),
    "border_color": (C_BORDER,  "卡片描边"),
    "title_color":  (C_TITLE,   "标题"),
    "text_color":   (C_TEXT,    "说明文字"),
    "dim_color":    (C_DIM,     "提示小字"),
    "tier_color":   (C_TIER,    "分组标题"),
    "node_color":   (C_NODE,    "节点"),
    "remain_color": (C_REMAIN,  "倒计时"),
    "bubble_bg":    (C_BUB_BG,  "气泡底色"),
    "bubble_line":  (C_BUB_LINE, "气泡描边"),
    "bubble_text":  (C_BUB_TEXT, "气泡文字"),
    "line_color":   (C_LINE,    "分割线"),
}


def _pal(t: Theme) -> dict[str, str]:
    """解析本卡生效配色：专属覆盖 > 硬编码默认（未配置 = 旧版观感，逐字节一致）。"""
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("fissure") if isinstance(ov.get("fissure"), dict) else {}
    out: dict[str, str] = {}
    for key, (default, _lab) in _COLOR_MAP.items():
        s = v.get(key)
        out[key] = s if (isinstance(s, str) and s) else default
    return out


def _card_alpha(t: Theme) -> int:
    """卡片底色透明度（0-100）：bg_overrides["fissure"].card_alpha，缺省 100。

    100（默认）→ fill_alpha=255 → 渲染与旧版逐字节一致（无创）；
    <100 → canvas.rounded_rect 走 alpha_composite 半透明层，露出底下背景图案/背景图。
    """
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("fissure") if isinstance(ov.get("fissure"), dict) else None
    try:
        return max(0, min(100, int(v.get("card_alpha", 100)))) if v else 100
    except (TypeError, ValueError):
        return 100


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _rec(boxes, label: str, box) -> None:
    """收集元素包围盒（boxes 为 None 时零开销）。"""
    if boxes is not None:
        boxes.append((label, *box))


def _tb(pos, text: str, size: int, role: str, anchor: str) -> tuple:
    """由 text_size + anchor 推算文本真实包围盒（与绘制同源自 ``Canvas.text_size``）。"""
    w, h = Canvas.text_size(text, size, role)
    x, y = pos
    if anchor == "mm":
        return (x - w // 2, y - h // 2, x + w // 2, y + h // 2)
    if anchor == "lm":
        return (x, y - h // 2, x + w, y + h // 2)
    if anchor == "rm":
        return (x - w, y - h // 2, x, y + h // 2)
    if anchor == "ms":
        return (x - w // 2, y, x + w // 2, y + h)
    return (x, y, x + w, y + h)


def build(t: Theme, groups=None, title="虚空裂隙", intro="", footers=None,
          width: int | None = None, boxes=None, **kw) -> Canvas:
    groups = groups or []
    footers = footers or []
    width = int(width or getattr(t, "width", 800) or 800)
    x0 = PAD
    content_w = width - PAD * 2
    P = _pal(t)   # 本卡生效配色（专属覆盖 > 默认）
    off, fsz, pct = decor.elem_tf(t, "fissure")   # 元素微调（未配置 = 旧版逐字节一致）

    def _row_h(gi: int, ri: int) -> int:
        """行高：随该行三个元素的缩放比例放大/缩小（缺省 = ROW_H，逐字节一致）。"""
        s = max(pct("bub-%d-%d" % (gi, ri)), pct("node-%d-%d" % (gi, ri)),
                pct("rem-%d-%d" % (gi, ri)))
        return max(24, int(round(ROW_H * s / 100.0)))

    # ---- 高度测量（与绘制同一套缩放字号/行高，保证缩放参与排版流）----
    fs_title = fsz("title", FS_TITLE)
    fs_intro = fsz("intro", FS_INTRO)
    fs_foot = fsz("foot", FS_FOOT)
    y = PAD
    y += fs_title + 18 + 14           # 标题 + 分割线区
    for ln in intro.split("\n"):
        y += fs_intro + 6
    y += 8
    for gi, g in enumerate(groups):
        y += fsz("tier-%d" % gi, FS_TIER) + 12
        for ri in range(len(g["rows"])):
            y += _row_h(gi, ri)
        y += 14
    y += 6
    for _f in footers:
        y += fs_foot + 6
    y += PAD + 6
    h = y

    c = Canvas(width, h, P["page_color"], theme=t)
    # 卡片专属背景（2026-09-23）：仅当主题 bg_overrides 里显式配了 fissure 才画，
    # 全局背景不得改变裂隙卡的现有观感（无创原则——固定风格卡按 Rabbit 样本硬编码配色）。
    decor.background(c, t, tpl="fissure", only_override=True)
    ca = _card_alpha(t)   # 卡片底色透明度（专属覆盖 > 100）
    c.rounded_rect((14, 14, width - 14, h - 14), radius=26,
                   fill=P["card_color"], outline=P["border_color"], width=2,
                   fill_alpha=ca * 255 // 100)
    # 卡片壳热区：点空白处改 卡片底色/描边/透明度（values 供浮窗显示当前生效值）
    c.add_region("ov:card_color", [20, 20, width - 20, h - 20],
                 label="卡片底色/描边", tip="点击修改本卡的卡片底色、描边与透明度（仅本卡生效）")
    c.regions[-1]["values"] = {"ov:card_color": P["card_color"],
                               "ov:border_color": P["border_color"],
                               "ov:card_alpha": ca}

    y = PAD
    # ---- 标题居中（可拖动/缩放：ek=title）----
    tdx, tdy = off("title")
    fs_t = fs_title
    tw = _w(title, fs_t, "regular")
    tx = (width - tw) // 2 + tdx
    ty = y + tdy
    c.text((tx, ty + fs_t // 2), title,
           size=fs_t, color=P["title_color"], role="regular", anchor="lm")
    _rec(boxes, "title", (tx, ty, tx + tw, ty + fs_t))
    c.add_region("ov:title_color", [tx - 10, ty, tx + tw + 10, ty + fs_t + 6],
                 label="标题", tip="拖动移动 · 拖右下角缩放 · 点击改标题颜色")
    c.regions[-1]["values"] = {"ov:title_color": P["title_color"]}
    c.regions[-1]["ek"] = "title"
    y += fs_t + 14
    # ---- 分割线（可上下拖：ek=line）----
    _ldx, ldy = off("line")
    c.line((x0, y + ldy, x0 + content_w, y + ldy), fill=P["line_color"], width=2)
    c.add_region("ov:line_color", [x0, y + ldy - 2, x0 + content_w, y + ldy + 2],
                 label="分割线", tip="拖动上下移动 · 点击修改分割线颜色")
    c.regions[-1]["values"] = {"ov:line_color": P["line_color"]}
    c.regions[-1]["ek"] = "line"
    y += 14

    # ---- 开头说明（整块可拖动/缩放：ek=intro）----
    idx, idy = off("intro")
    fs_i = fs_intro
    intro_y0 = y + idy
    for ln in intro.split("\n"):
        if ln:
            for i, part in enumerate(Canvas.wrap(ln, fs_i, "regular", content_w)):
                c.text((x0 + idx, y + idy + fs_i // 2), part, size=fs_i,
                       color=P["text_color"] if i == 0 else P["dim_color"], anchor="lm")
                _rec(boxes, "intro.%d" % i,
                     _tb((x0 + idx, y + idy + fs_i // 2), part, fs_i, "regular", "lm"))
                y += fs_i
        y += 6
    if intro.strip():
        c.add_region("ov:text_color", [x0 + idx, intro_y0 - 2, x0 + content_w + idx, y + idy + 2],
                     label="说明文字", tip="拖动移动 · 点击修改说明文字颜色")
        c.regions[-1]["values"] = {"ov:text_color": P["text_color"]}
        c.regions[-1]["ek"] = "intro"
    y += 4

    # ---- 分组 ----
    for gi, g in enumerate(groups):
        gdx, gdy = off("tier-%d" % gi)
        fs_g = fsz("tier-%d" % gi, FS_TIER)
        c.text((x0 + gdx, y + gdy + fs_g // 2), "·" + g["label"], size=fs_g,
               color=P["tier_color"], role="regular", anchor="lm")
        _rec(boxes, "tier.%d" % gi,
             _tb((x0 + gdx, y + gdy + fs_g // 2), "·" + g["label"], fs_g, "regular", "lm"))
        c.add_region("ov:tier_color",
                     [x0 + gdx, y + gdy - 2,
                      x0 + gdx + _w("·" + g["label"], fs_g) + 8, y + gdy + fs_g + 4],
                     label="分组标题", tip="拖动移动 · 点击修改分组标题颜色")
        c.regions[-1]["values"] = {"ov:tier_color": P["tier_color"]}
        c.regions[-1]["ek"] = "tier-%d" % gi
        y += fs_g + 10
        for ri, r in enumerate(g["rows"]):
            rh = _row_h(gi, ri)
            cy = y + rh // 2
            # 气泡（可拖动/缩放：ek=bub-gi-ri）
            bdx, bdy = off("bub-%d-%d" % (gi, ri))
            fs_b = fsz("bub-%d-%d" % (gi, ri), FS_BUB)
            bub_w = _w(r["bubble"], fs_b) + BUB_PAD_X * 2
            bub_h = fs_b + BUB_PAD_Y * 2
            bx, by = x0 + bdx, cy - bub_h // 2 + bdy
            c.rounded_rect((bx, by, bx + bub_w, by + bub_h), radius=9,
                           fill=P["bubble_bg"], outline=P["bubble_line"], width=1)
            c.text((bx + bub_w // 2, by + bub_h // 2), r["bubble"], size=fs_b,
                   color=P["bubble_text"], anchor="mm")
            _rec(boxes, "bub.%d.%d" % (gi, ri), (bx, by, bx + bub_w, by + bub_h))
            c.add_region("ov:bubble_bg", [bx, by, bx + bub_w, by + bub_h],
                         label="气泡", tip="拖动移动 · 拖右下角缩放 · 点击改气泡配色")
            c.regions[-1]["values"] = {"ov:bubble_bg": P["bubble_bg"],
                                       "ov:bubble_line": P["bubble_line"],
                                       "ov:bubble_text": P["bubble_text"]}
            c.regions[-1]["ek"] = "bub-%d-%d" % (gi, ri)
            # 星球奖杯图标（真实资产，随节点缩放一起放大；取不到回落灰蓝圆点）
            psz = max(14, int(round(PLANET_SZ * pct("node-%d-%d" % (gi, ri)) / 100.0)))
            px = bx + bub_w + GAP_AFTER_BUB + psz // 2
            if not paste_icon(c, r.get("_planet_icon"), px, cy, psz,
                              crop=planet_crop(r.get("planet_en"))):
                pr = max(6, psz // 2 - 2)
                c.ellipse((px - pr, cy - pr, px + pr, cy + pr), fill=C_PLANET)
            _rec(boxes, "planet.%d.%d" % (gi, ri),
                 (px - psz // 2, cy - psz // 2, px + psz // 2, cy + psz // 2))
            # 节点（紫，可拖动/缩放：ek=node-gi-ri）
            ndx, ndy = off("node-%d-%d" % (gi, ri))
            fs_n = fsz("node-%d-%d" % (gi, ri), FS_NODE)
            nx = px + psz // 2 + GAP_AFTER_ICON + ndx
            c.text((nx, cy + ndy), r["node"], size=fs_n, color=P["node_color"], anchor="lm")
            _rec(boxes, "node.%d.%d" % (gi, ri),
                 _tb((nx, cy + ndy), r["node"], fs_n, "regular", "lm"))
            c.add_region("ov:node_color",
                         [nx - 2, cy + ndy - fs_n // 2 - 4,
                          nx + _w(r["node"], fs_n) + 4, cy + ndy + fs_n // 2 + 4],
                         label="节点", tip="拖动移动 · 点击修改节点文字颜色")
            c.regions[-1]["values"] = {"ov:node_color": P["node_color"]}
            c.regions[-1]["ek"] = "node-%d-%d" % (gi, ri)
            # 倒计时（橙，右对齐，可拖动/缩放：ek=rem-gi-ri）
            rdx, rdy = off("rem-%d-%d" % (gi, ri))
            fs_r = fsz("rem-%d-%d" % (gi, ri), FS_NODE)
            c.text((x0 + content_w + rdx, cy + rdy), r["remain"], size=fs_r,
                   color=P["remain_color"], anchor="rm")
            rw = _w(r["remain"], fs_r)
            _rec(boxes, "remain.%d.%d" % (gi, ri),
                 _tb((x0 + content_w + rdx, cy + rdy), r["remain"], fs_r, "regular", "rm"))
            c.add_region("ov:remain_color",
                         [x0 + content_w + rdx - rw - 4, cy + rdy - fs_r // 2 - 4,
                          x0 + content_w + rdx + 2, cy + rdy + fs_r // 2 + 4],
                         label="倒计时", tip="拖动移动 · 点击修改倒计时数字颜色")
            c.regions[-1]["values"] = {"ov:remain_color": P["remain_color"]}
            c.regions[-1]["ek"] = "rem-%d-%d" % (gi, ri)
            y += rh
        y += 12

    # ---- 末尾提示（整块可拖动/缩放：ek=foot）----
    fdx, fdy = off("foot")
    fs_f = fs_foot
    foot_y0 = y + fdy
    for fi, f in enumerate(footers):
        c.text((x0 + fdx, y + fdy + fs_f // 2), f, size=fs_f, color=P["dim_color"], anchor="lm")
        _rec(boxes, "foot.%d" % fi,
             _tb((x0 + fdx, y + fdy + fs_f // 2), f, fs_f, "regular", "lm"))
        y += fs_f + 6
    if footers:
        c.add_region("ov:dim_color", [x0 + fdx, foot_y0 - 2, x0 + content_w + fdx, y + fdy + 2],
                     label="提示小字", tip="拖动移动 · 点击修改提示文字颜色")
        c.regions[-1]["values"] = {"ov:dim_color": P["dim_color"]}
        c.regions[-1]["ek"] = "foot"

    return c
