# -*- coding: utf-8 -*-
"""佩兰数列信条武器轮换卡（``#wf 信条`` / ``tenet``）。

2026-11-06 用户指令：「信条卡片的排版不对，要个终幕的一样，重做，认真点」。
本文件改写为与 ``coda.py``（终幕卡 800×998 参考图两列网格版式）**视觉完全一致**
的两列网格版式：标题书签 + 分割线、intro 蓝 bracket、元信息两行、两列浅蓝武器卡、
真实武器图标 + 真实 Wiki 元素图标 + 元素名 + 带 ``+`` 的加成值、淡黄橙字胶囊倒计时、
页脚 PC 圆胶囊。

与 ``coda.py`` 的关系（遵守「信条与终幕是两个口令、两套独立模板」长期纪律）：
  * 本文件是**独立模块**，独立 ``build`` / ``_plan``、独立 ``kind="tenet"``；
    两个模板互传错 kind 直接 ``raise``；
  * **网格骨架（COL_X/CELL_W/CELL_H/CELL_PITCH/CELL_Y0/W）+ 调色板 + 字号
    与 ``coda.py`` 逐常量一致**，由 ``tools/check_tenet_layout.py`` 的
    ``_skeleton_problems`` 锁死，保证信条卡视觉与终幕卡对齐、不会漂移到不一样；
  * 数据仍各自驱动：信条 5 把（佩兰数列）、终幕 7 把（埃莉诺）；标题/说明/卡片
    主题覆盖（``bg_overrides["tenet"]``）各自生效，两口令内容不混淆。

参考卡逐像素实测（``tmp/tenet_measure2.py`` / ``tmp/tenet_measure3.py``，与 coda 同源
参考图 e4837e4b71cfc4ebf2c08549e34c028c.png）：
  * 页面底色 ``#FDFBFC``；顶部装饰背景走 ``decor.background(..., only_override=True)``，
    不自绘兔子底纹 / 右下角插画。
  * 标题深蓝 ``#215694`` 36px 粗体 + 左侧黑色书签轮廓；标题下分割线 ``y=87``。
  * intro 左侧蓝色圆头竖条 + 说明文字；元信息两行（来源/快照/下次刷新）。
  * 两列武器卡：列 ``x=34 / 405``，卡底浅蓝 ``#F2F8F9``，行首 ``y=244``，行距 163，
    高 153；紫名 28px + 名称下细线；左下真实武器图标 64px。
  * 右下：真实 Wiki 伤害类型图标 + 元素名（元素色）+ 带 ``+`` 的加成值（档位色），
    整组右对齐到 ``x0+343``。淡黄橙字胶囊倒计时；页脚分割线 + 绿色 PC 圆胶囊。

设计纪律（card-design）：
  * 单一 ``_plan(data, width)`` 同时供测量与绘制，``width != 800`` 直接 ``raise``；
  * ``build(..., boxes=[])`` 在**真实绘制时**逐叶子登记包围盒；
  * 所有宽度用 ``Canvas.text_width`` 实测，禁止估算 / 空格凑间距；
  * 无几何兜底：武器图标 / 元素图标缺失直接 ``FileNotFoundError``。
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from .. import decor
from .. import element_icons as _ei
from ..canvas import Canvas
from ..theme import Theme, rgb

# ---------------------------------------------------------------- 参考卡实测配色（与 coda.py 逐常量一致）
C_PAGE = "#FDFBFC"       # (253,251,252) 页面 / 卡片底
C_TITLE = "#215694"      # (33,86,148)  标题 / intro 竖条 / 卡左竖条 / 页脚文字
C_DARK = "#22232E"       # (34,35,46)   书签 / intro / 元信息
C_PURPLE = "#833E79"     # (131,62,121) 武器名
C_CELL = "#F2F8F9"       # (242,248,249) 武器卡底
C_UNDER = "#D0DCEA"      # (208,220,234) 名称下细线
C_DIV_TOP = "#B7B9B7"    # (183,185,183) 标题下分割线
C_DIV_FOOT = "#9FA5AA"   # (159,165,170) 页脚分割线
C_CAP_BG = "#FBF5EB"     # (251,245,235) 倒计时胶囊底
C_CAP_LINE = "#E8CBB4"   # (232,203,180) 胶囊描边
C_CAP_TEXT = "#D1701E"   # (209,112,30)  胶囊文字
C_PC = "#328B51"         # (50,139,81)   PC 徽章

# ---------------------------------------------------------------- 字号（与 coda.py 一致）
FS_TITLE = 36
FS_INTRO = 26
FS_META = 23
FS_NAME = 28
FS_ELEM = 28
FS_BONUS = 30
FS_REMAIN = 19
FS_CAP = 21
FS_FOOT = 17
FS_PC = 14

# ---------------------------------------------------------------- 参考卡实测网格（与 coda.py 逐常量一致）
W = 800
TOP_DIV_Y = 87
TOP_DIV_X0, TOP_DIV_X1 = 50, 750
TITLE_CENTER = 403
TITLE_CY = 51
BOOKMARK_W, BOOKMARK_H = 18, 24
BOOKMARK_Y0 = 41
TITLE_GAP = 24

BRACKET_X0, BRACKET_X1 = 34, 39
BRACKET_Y0, BRACKET_Y1 = 124, 171
INTRO_X, INTRO_CY = 54, 146
META_X, META1_CY, META2_CY = 36, 189, 222

COL_X = (34, 405)
CELL_W = 360
CELL_H = 153
CELL_PITCH = 163
CELL_Y0 = 244
CELL_RADIUS = 10
BAR_W = 8
CARD_INSET = 4

NAME_X_OFF = 21
NAME_CY_OFF = 32
UNDER_Y_OFF = 60
UNDER_X0_OFF = 21
UNDER_X1_OFF = 344

WEAPON_ICON_X_OFF = 26
WEAPON_ICON_Y_OFF = 72
WEAPON_ICON_SZ = 64
LOWER_CY_OFF = 104

ELEM_ICON_SZ = 28
GAP_ICON_NAME = 10
GAP_NAME_BONUS = 8
GROUP_RIGHT_OFF = 343
MIN_GROUP_GAP = 20            # 元素组与武器图标之间的最小水平间隙

REMAIN_X = 36
REMAIN_TEXT = "- 距离下次刷新："
CAP_X = 211
CAP_W = 184              # 参考卡实测宽度（对应「1天9小时2分43秒」）
CAP_H = 34
CAP_PAD_L = 9
CAP_PAD_R = 5
FOOT_DIV_X0, FOOT_DIV_X1 = 37, 779
FOOT_DIV_GAP = 19
PC_X, PC_W, PC_H = 37, 33, 21
PC_TOP_OFF = 13
FOOT_TEXT_X = 79
FOOT_TEXT = "Warframe Rabbit *善用右键/长按提取文字"
BOTTOM_PAD = 24

# ---------------------------------------------------------------- 主题覆盖（与 coda.py 同键，读 bg_overrides["tenet"]）
_COLOR_MAP: dict[str, tuple[str, str]] = {
    "page_color":   (C_PAGE,      "页面底色"),
    "title_color":  (C_TITLE,     "标题/竖条"),
    "text_color":   (C_DARK,      "说明与元信息"),
    "name_color":   (C_PURPLE,    "武器名"),
    "cell_bg":      (C_CELL,      "武器卡底"),
    "cell_bar":     (C_TITLE,     "武器卡左竖条"),
    "under_color":  (C_UNDER,     "名称下细线"),
    "cap_bg":       (C_CAP_BG,    "倒计时胶囊底"),
    "cap_line":     (C_CAP_LINE,  "倒计时胶囊边"),
    "cap_text":     (C_CAP_TEXT,  "倒计时文字"),
    "pc_color":     (C_PC,        "PC 徽章"),
    "foot_color":   (C_TITLE,     "页脚文字"),
}


def _pal(t: Theme, kind: str) -> dict[str, str]:
    """卡片专属配色覆盖 > 硬编码默认。``kind`` 决定读 bg_overrides 的哪张卡。"""
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get(kind) if isinstance(ov.get(kind), dict) else {}
    out: dict[str, str] = {}
    for key, (default, _lab) in _COLOR_MAP.items():
        s = v.get(key)
        out[key] = s if (isinstance(s, str) and s) else default
    return out


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _h(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_size(s, size, role)[1]


def _rec(boxes, label: str, box) -> None:
    if boxes is not None:
        boxes.append((label, int(box[0]), int(box[1]), int(box[2]), int(box[3])))


def _txt(c: Canvas, boxes, label: str, x: int, cy: int, text: str, size: int,
         color: str, role: str = "regular") -> int:
    """左中锚点绘文本 + 登记真实包围盒；返回实测宽度。"""
    tw, th = Canvas.text_size(text, size, role)
    c.text((x, cy), text, size=size, color=color, role=role, anchor="lm")
    _rec(boxes, label, (x, cy - th // 2, x + tw, cy - th // 2 + th))
    return tw


def _load(path: str, size: int, label: str) -> Image.Image:
    """真实资产加载 + 等比缩放。缺图 / 解码失败直接 raise，绝不画占位。"""
    try:
        img = Image.open(path).convert("RGBA")
    except Exception as e:
        raise FileNotFoundError("%s 无法解码：%s (%s)" % (label, path, e)) from e
    if img.width <= 0 or img.height <= 0:
        raise FileNotFoundError("%s 尺寸非法：%s" % (label, path))
    bb = img.getchannel("A").getbbox()
    if bb:
        img = img.crop(bb)
    img.thumbnail((size, size), Image.LANCZOS)
    return img


# ---------------------------------------------------------------- 单一版式规划（与 coda.py 同构，kind="tenet"）
def _plan(data: dict, width: int) -> dict:
    """测量与绘制共用的唯一坐标来源；所有文字宽度实测。"""
    if width != W:
        raise ValueError(
            "信条卡按参考图 800px 宽度逐像素对齐（坐标由 "
            "tools/check_tenet_layout.py --ref-geom 锁定），不接受 width=%d；"
            "请把 PIC_MAX_WIDTH 设回 800，或改版式后同步更新门禁坐标。" % width)

    kind = str(data.get("kind") or "tenet")
    if kind != "tenet":
        raise ValueError(
            "信条模板只接受 kind='tenet'，收到 %r；终幕口令请走 coda.py，"
            "两个口令不得共用版式。" % kind)

    weapons = list(data.get("weapons") or [])
    if not weapons:
        raise ValueError("信条卡 weapons 为空：不渲染空卡（禁止假数据）")
    remain = str(data.get("remain") or "")
    if not remain:
        raise ValueError("信条卡缺少 remain 倒计时：不渲染空胶囊")

    p: dict = {"width": width, "kind": kind}
    title = str(data.get("title") or "")
    tw = _w(title, FS_TITLE, "bold")
    group_w = BOOKMARK_W + TITLE_GAP + tw
    p["book_x"] = int(round(TITLE_CENTER - group_w / 2.0))
    p["title_x"] = p["book_x"] + BOOKMARK_W + TITLE_GAP
    p["title_w"] = tw
    p["title"] = title

    p["intro"] = str(data.get("intro") or "")
    src = str(data.get("source") or "wf.wiki")
    snap = str(data.get("snapshot") or "")
    p["meta1"] = "更新来源：%s" % src
    if snap:
        p["meta1"] += " 快照时间：%s" % snap
    p["meta1"] += " 下次刷新："
    p["meta2"] = str(data.get("next_refresh") or "")
    p["meta1_w"] = _w(p["meta1"], FS_META)
    p["meta2_w"] = _w(p["meta2"], FS_META)

    # ---- 每张卡的元素组（右对齐到 x0+343），全部按实测宽度排 ----
    cells = []
    for i, w in enumerate(weapons):
        col, row = i % 2, i // 2
        x0 = COL_X[col]
        y0 = CELL_Y0 + row * CELL_PITCH
        key = _ei.canonical(w.get("element_key") or w.get("element") or "")
        bonus = w.get("bonus")
        bonus_str = str(w.get("bonus_str") or "")
        if not bonus_str:
            raise ValueError("第 %d 把武器缺 bonus_str" % (i + 1))
        bcolor = _ei.bonus_color(bonus)
        ec = _ei.elem_color(key)
        name = str(w.get("name") or "")
        name_w = _w(name, FS_NAME, "bold")
        elem = str(w.get("element") or "")
        elem_w = _w(elem, FS_ELEM)
        bonus_w = _w(bonus_str, FS_BONUS, "bold")
        group_w = ELEM_ICON_SZ + GAP_ICON_NAME + elem_w + GAP_NAME_BONUS + bonus_w
        group_right = x0 + GROUP_RIGHT_OFF
        group_left = group_right - group_w
        if group_left < x0 + WEAPON_ICON_X_OFF + WEAPON_ICON_SZ + MIN_GROUP_GAP:
            raise ValueError(
                "第 %d 把武器元素组压到武器图标：group_left=%d < %d；"
                "请按参考卡调整字号/间距，不要硬塞。" %
                (i + 1, group_left, x0 + WEAPON_ICON_X_OFF + WEAPON_ICON_SZ + MIN_GROUP_GAP))
        cells.append({
            "idx": i, "x0": x0, "y0": y0, "w": w, "key": key,
            "name": name, "name_w": name_w, "elem": elem, "elem_w": elem_w,
            "bonus": bonus_str, "bonus_w": bonus_w, "bcolor": bcolor, "ec": ec,
            "group_left": group_left, "group_right": group_right,
            "lower_cy": y0 + LOWER_CY_OFF,
        })
    p["cells"] = cells
    rows = (len(weapons) + 1) // 2
    grid_bottom = CELL_Y0 + (rows - 1) * CELL_PITCH + CELL_H

    # ---- 尾部：倒计时胶囊 → 页脚分割线 → PC 徽章 ----
    cap_y = grid_bottom + 1
    p["cap_y"] = cap_y
    p["cap_cy"] = cap_y + CAP_H // 2
    p["cap_text_w"] = _w(remain, FS_CAP, "bold")
    # 胶囊宽度按实测文字宽度算：倒计时长度每次都不同，固定宽度会裁字（红线 2）。
    # 参考卡那一帧恰好是 184px；更长的倒计时只让胶囊向右长，不缩字号、不裁切。
    p["cap_w"] = max(CAP_W, CAP_PAD_L + p["cap_text_w"] + CAP_PAD_R)
    if CAP_X + p["cap_w"] > W - 35:
        raise ValueError("倒计时胶囊右侧溢出：%d > %d" %
                         (CAP_X + p["cap_w"], W - 35))
    p["foot_div_y"] = cap_y + CAP_H + FOOT_DIV_GAP
    p["pc_y"] = p["foot_div_y"] + PC_TOP_OFF
    p["foot_cy"] = p["pc_y"] + PC_H // 2
    p["H"] = p["pc_y"] + PC_H + BOTTOM_PAD
    p["remain"] = remain
    p["rows"] = rows
    p["grid_bottom"] = grid_bottom
    return p


# ---------------------------------------------------------------- 绘制原语（与 coda.py 一致）
def _bookmark(c: Canvas, boxes, x: int, p: dict) -> None:
    """参考卡实测的书签轮廓：圆肩矩形 + 底部 V 形缺口，黑色描边。"""
    x1 = x + BOOKMARK_W
    y0, y1 = BOOKMARK_Y0, BOOKMARK_Y0 + BOOKMARK_H
    mid = x + BOOKMARK_W / 2.0
    r = 3
    pts = [(x + r, y0), (x1 - r, y0), (x1, y0 + r),
           (x1, y1), (mid, y1 - 8), (x, y1), (x, y0 + r), (x + r, y0)]
    d = ImageDraw.Draw(c.img)
    d.line(pts, fill=rgb(C_DARK) + (255,), width=2, joint="curve")
    _rec(boxes, "bookmark", (x, y0, x1, y1))


def _cell(c: Canvas, boxes, cell: dict, p: dict) -> None:
    """一张武器卡：蓝竖条 → 浅蓝卡底 → 紫色名 → 细线 → 真实图标 / 元素组。"""
    x0, y0, i = cell["x0"], cell["y0"], cell["idx"]
    lab = "cell%d-" % (i + 1)

    # 左侧蓝色圆头竖条（在卡片底下，露出一小条并包住左上圆角）
    c.rounded_rect((x0, y0, x0 + BAR_W, y0 + CELL_H), radius=4, fill=p["cell_bar"])
    _rec(boxes, lab + "bar", (x0, y0, x0 + BAR_W, y0 + CELL_H))

    # 卡底（无外描边：参考卡靠底色与页面底色的明度差区分）
    c.rounded_rect((x0 + CARD_INSET, y0, x0 + CELL_W, y0 + CELL_H),
                   radius=CELL_RADIUS, fill=p["cell_bg"])

    # 武器名
    _txt(c, boxes, lab + "name", x0 + NAME_X_OFF, y0 + NAME_CY_OFF,
         cell["name"], FS_NAME, p["name_color"], "bold")

    # 名称下细横线
    uy = y0 + UNDER_Y_OFF
    ux0, ux1 = x0 + UNDER_X0_OFF, x0 + UNDER_X1_OFF
    c.line((ux0, uy, ux1, uy), fill=p["under_color"], width=1)
    _rec(boxes, lab + "under", (ux0, uy, ux1, uy + 1))

    # 左下真实武器图标
    icon = _load(str(cell["w"].get("icon") or ""), WEAPON_ICON_SZ,
                 "信条武器 %s 的真实武器图标" % cell["name"])
    ix = x0 + WEAPON_ICON_X_OFF + (WEAPON_ICON_SZ - icon.width) // 2
    iy = cell["lower_cy"] - icon.height // 2
    c.paste_alpha(icon, (ix, iy))
    _rec(boxes, lab + "weapon", (ix, iy, ix + icon.width, iy + icon.height))

    # 右下真实元素伤害类型图标
    epath = _ei.icon_path(cell["key"])
    eimg = _load(str(epath), ELEM_ICON_SZ, "元素 %s 的真实伤害类型图标" % cell["key"])
    gx = cell["group_left"]
    ex = gx + (ELEM_ICON_SZ - eimg.width) // 2
    ey = cell["lower_cy"] - eimg.height // 2
    c.paste_alpha(eimg, (ex, ey))
    _rec(boxes, lab + "elem-icon", (ex, ey, ex + eimg.width, ey + eimg.height))

    # 元素名（元素色）+ 带 + 的加成值（参考卡实测的三档色）
    nx = gx + ELEM_ICON_SZ + GAP_ICON_NAME
    _txt(c, boxes, lab + "elem-name", nx, cell["lower_cy"],
         cell["elem"], FS_ELEM, cell["ec"])
    bx = nx + cell["elem_w"] + GAP_NAME_BONUS
    _txt(c, boxes, lab + "bonus", bx, cell["lower_cy"],
         cell["bonus"], FS_BONUS, cell["bcolor"], "bold")


def build(t: Theme, data: dict | None = None, source: str | None = None,
          width: int | None = None, boxes: list | None = None, **kw) -> Canvas:
    """渲染佩兰数列信条武器卡；``boxes`` 给定时逐叶子登记包围盒供门禁校验。"""
    data = data or {}
    width = int(width or getattr(t, "width", W) or W)
    plan = _plan(data, width)
    p = _pal(t, plan["kind"])

    c = Canvas(width, plan["H"], p["page_color"], theme=t)
    # 背景只走主题覆盖机制（用户长期规则：不自绘兔子底纹 / 右下角插画）
    decor.background(c, t, tpl=plan["kind"], only_override=True)

    # ---- 标题 + 书签 + 分割线 ----
    _bookmark(c, boxes, plan["book_x"], p)
    _txt(c, boxes, "title-text", plan["title_x"], TITLE_CY,
         plan["title"], FS_TITLE, p["title_color"], "bold")
    c.line((TOP_DIV_X0, TOP_DIV_Y, TOP_DIV_X1, TOP_DIV_Y),
           fill=C_DIV_TOP, width=1)
    _rec(boxes, "top-div", (TOP_DIV_X0, TOP_DIV_Y, TOP_DIV_X1, TOP_DIV_Y + 1))

    # ---- intro 蓝色 bracket + 说明 ----
    if plan["intro"]:
        c.rounded_rect((BRACKET_X0, BRACKET_Y0, BRACKET_X1, BRACKET_Y1),
                       radius=2, fill=p["title_color"])
        _rec(boxes, "intro-bar", (BRACKET_X0, BRACKET_Y0, BRACKET_X1, BRACKET_Y1))
        _txt(c, boxes, "intro-text", INTRO_X, INTRO_CY,
             plan["intro"], FS_INTRO, p["text_color"])

    # ---- 元信息两行 ----
    _txt(c, boxes, "meta1", META_X, META1_CY,
         plan["meta1"], FS_META, p["text_color"])
    if plan["meta2"]:
        _txt(c, boxes, "meta2", META_X, META2_CY,
             plan["meta2"], FS_META, p["text_color"])

    # ---- 两列武器卡 ----
    for cell in plan["cells"]:
        _cell(c, boxes, cell, p)

    # ---- 倒计时标签 + 淡黄胶囊 ----
    _txt(c, boxes, "remain-label", REMAIN_X, plan["cap_cy"],
         REMAIN_TEXT, FS_REMAIN, p["foot_color"])
    c.rounded_rect((CAP_X, plan["cap_y"], CAP_X + plan["cap_w"], plan["cap_y"] + CAP_H),
                   radius=CAP_H // 2, fill=p["cap_bg"], outline=p["cap_line"], width=1)
    _rec(boxes, "cap", (CAP_X, plan["cap_y"], CAP_X + plan["cap_w"], plan["cap_y"] + CAP_H))
    _txt(c, boxes, "cap-text", CAP_X + CAP_PAD_L, plan["cap_cy"],
         plan["remain"], FS_CAP, p["cap_text"], "bold")

    # ---- 页脚分割线 + PC 圆胶囊 + Rabbit 签名 ----
    fy = plan["foot_div_y"]
    c.line((FOOT_DIV_X0, fy, FOOT_DIV_X1, fy), fill=C_DIV_FOOT, width=1)
    _rec(boxes, "foot-div", (FOOT_DIV_X0, fy, FOOT_DIV_X1, fy + 1))

    pc_y = plan["pc_y"]
    c.rounded_rect((PC_X, pc_y, PC_X + PC_W, pc_y + PC_H),
                   radius=PC_H // 2, outline=p["pc_color"], width=1)
    _rec(boxes, "pc-pill", (PC_X, pc_y, PC_X + PC_W, pc_y + PC_H))
    pc_tw = _w("PC", FS_PC)
    _txt(c, boxes, "pc-text", PC_X + (PC_W - pc_tw) // 2, plan["foot_cy"],
         "PC", FS_PC, p["pc_color"])
    _txt(c, boxes, "foot-text", FOOT_TEXT_X, plan["foot_cy"],
         FOOT_TEXT, FS_FOOT, p["foot_color"])
    return c
