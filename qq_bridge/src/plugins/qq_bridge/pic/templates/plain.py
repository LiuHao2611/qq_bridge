# -*- coding: utf-8 -*-
"""pic/templates/plain.py —— 平原状态和时间卡（照 Warframe Rabbit 参考卡逐像素复刻）。

版式依据（2026-11-04 用 PIL 对参考图 600x513 做像素采样/列投影实测）：
  * 画布 600x513，整体底色 #FDFCFD（近白；参考图无灰边框，无可见卡片描边）；
  * 标题「平原状态和时间」36px 粗体 #1E5D95，左侧 28px 近黑时钟描线图标，整组水平居中
    （实测时钟 x151..178、标题 x197..447，组中心 299≈画布中心）；
  * 顶部分割线 y=87，x50..550，1px #BEB7B8；
  * 四组平原，行距 83.5：行1 圆心 x49/r14 + 地名 24px 粗体 #20262B（x76）
    + 状态胶囊（**固定 x=254**，h29/r9，填充 #F2F0F4，描边/文字 蓝#1E5D95 或 紫#604093）；
    行2 紫色斜体「- 距离X还有」20px #604093（x35）+ 橙色倒计时胶囊
    （填充 #FAF1ED / 描边 #EAC9B7 / 文字 #C96D2A，h28，紧贴斜体后 4px）；
  * 底部分割线 y=456，x50..550；PC 绿描边胶囊（#328A51，h22）+ 绿色机器人名 16px
    + 灰色「*善用右键/长按提取文字」16px #8A8F98。

数据缺口说明：地球&夜灵平野、福尔图娜 取 Tenno.Tools 世界状态（daynight）；
魔胎之境(Fass/Vome)、双衍王境(情绪) 取 worldstate.wf.wiki 的 cycles 数组
（api.warframestat.us 部署环境 403）。取不到时优雅降级为「进游戏看」，版式不崩。
"""
from __future__ import annotations

import os
from dataclasses import replace

from PIL import Image, ImageDraw

from ..canvas import Canvas
from ..theme import Theme

#: 星球圆形图标资源目录（爬取的真实星球图，96px 透明底，圆形遮罩后贴 28px）
_PLANET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "planets")
#: 世界名 → 星球资源键（缺省映射；数据行也可显式给 planet 字段）
_PLANET_BY_NAME = {
    "地球&夜灵平野": "earth",
    "福尔图娜": "venus",
    "魔胎之境": "deimos",
    # 双衍王境没有对应真实星球，用官方场景图（白塔）圆形化占位
    "双衍王境": "duviri",
}
#: (类型, key, size) → PIL 图 / 遮罩
_PLANET_CACHE: dict = {}
#: 需要套圆形遮罩的星球键（球形天体 / 场景图）；
#: 火卫二是真实的不规则土豆形，保留原轮廓，不强制圆化。
_PLANET_ROUND = {"earth", "venus", "duviri"}

# ---- 画布 / 版式（全部来自参考图实测）----
W = 600
H_BASE = 513          # 4 行时的总高
BG = "#FDFCFD"        # 参考图整图底色（近白）
C_TITLE = "#1E5D95"   # 标题深蓝（实测 #1E5D95）
C_CLOCK = "#1E1E22"   # 时钟描线（实测近黑）
C_NAME = "#20262B"    # 地名近黑
C_DIV = "#BEB7B8"     # 分割线灰（实测 #BEB7B8）
C_DESC = "#604093"    # 斜体说明紫（与紫胶囊同色）
C_HINT = "#8A8F98"    # 底部灰色提示

# 状态胶囊：蓝 / 紫（填充统一 #F2F0F4）
C_PILL_FILL = "#F2F0F4"
C_BLUE = "#1E5D95"
C_PURPLE = "#604093"

# 倒计时胶囊（橙）
C_REM_FILL = "#FAF1ED"
C_REM_LINE = "#EAC9B7"
C_REM_TEXT = "#C96D2A"

# 底部 PC 胶囊（绿）
C_GREEN = "#328A51"

# ---- 字号（按 Canvas 实测字宽校准到参考图像素）----
FS_TITLE = 36   # 「平原状态和时间」→ 252px（参考 251）
FS_NAME = 24    # 「地球&夜灵平野」→ 165px（参考 161）
FS_PILL = 17    # 「寒冷」→ 34px，胶囊宽 69
FS_DESC = 20    # 「- 距离夜晚还有」→ 135px（参考 ~130）
FS_REM = 18     # 「1小时4分8秒」→ 105px，胶囊宽 133
FS_PC = 13      # 「PC」→ 17px，胶囊宽 32
FS_FOOT = 16    # 机器人名 / 灰色提示

# ---- 位置（参考图实测坐标）----
DIV_X0, DIV_X1 = 50, 550
TITLE_CY = 51
DIV1_Y = 87
ROW1_CY = 135
ROW_PITCH = 83.5
DESC_DY = 34          # 行2 相对行1 的垂直偏移
DIV_GAP = 71          # 末行行1 到 底部分割线
FOOT_GAP = 25         # 底部分割线到 footer 中线
BOTTOM_PAD = 32       # footer 中线到画布底

DESC_X = 35
ICON_CX = 49
ICON_R = 14
NAME_X = 76
PILL_X = 254
PILL_RADIUS = 9
PILL_PAD_X = 17
PILL_PAD_Y = 6
REM_RADIUS = 8
REM_PAD_X = 14
REM_PAD_Y = 5
REM_GAP = 4

PC_X = 37
PC_RADIUS = 6
PC_PAD_X = 7
PC_PAD_Y = 4
FOOT_NAME_X = 78
FOOT_HINT_GAP = 9


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _circle_mask(size: int) -> Image.Image:
    """抗锯齿圆形遮罩（4x 超采样后缩回）。"""
    ck = ("mask", size)
    m = _PLANET_CACHE.get(ck)
    if m is None:
        s = size * 4
        m = Image.new("L", (s, s), 0)
        ImageDraw.Draw(m).ellipse((0, 0, s - 1, s - 1), fill=255)
        m = m.resize((size, size), Image.LANCZOS)
        _PLANET_CACHE[ck] = m
    return m


def _planet_img(key: str | None, size: int) -> Image.Image | None:
    """载入 pic/planets/<key>.png 并缩放到 size（缓存）；缺失返回 None 走色块回退。"""
    if not key:
        return None
    ck = ("img", key, size)
    im = _PLANET_CACHE.get(ck)
    if im is not None:
        return im
    path = os.path.join(_PLANET_DIR, f"{key}.png")
    if not os.path.isfile(path):
        return None
    try:
        im = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        return None
    _PLANET_CACHE[ck] = im
    return im


def build(t: Theme, rows=None, title="平原状态和时间", footer_name="曼珠",
          width: int | None = None, **kw) -> Canvas:
    rows = rows or []
    # 参考图为 600 宽；忽略全局 PIC_MAX_WIDTH（800），保证「宽高完全一致」。
    width = W
    n = len(rows)

    # 本卡强制：标题/地名粗体 + 说明行斜体（无斜体字体时走仿剪切）。
    # Theme 是 frozen dataclass，用 replace 生成本卡专用副本，不影响其它卡/全局主题。
    t = replace(t, title_bold=True, sub_italic=True)

    # ---- 高度：与参考图同构（4 行 → 513）----
    div2_y = ROW1_CY + int(ROW_PITCH * (n - 1)) + DIV_GAP if n else DIV1_Y + DIV_GAP
    foot_cy = div2_y + FOOT_GAP
    h = foot_cy + BOTTOM_PAD

    c = Canvas(width, h, BG, theme=t)
    # 编辑器热区：整图为卡片底色（参考图无可见描边，故不画边框）
    c.add_region("ov:card_color", [0, 0, width, h],
                 label="卡片底色", tip="点击修改本卡底色")
    c.regions[-1]["values"] = {"ov:card_color": BG, "ov:border_color": BG}

    # ---- 标题（居中：时钟 + 文字整组）----
    tw = _w(title, FS_TITLE, "bold")
    icon_gap = 19
    total_w = ICON_R * 2 + icon_gap + tw
    start_x = (width - total_w) // 2
    clock_cx = start_x + ICON_R
    _clock(c, clock_cx, TITLE_CY, ICON_R, C_CLOCK)
    c.add_region("ov:title_color",
                 [start_x, TITLE_CY - FS_TITLE // 2, start_x + total_w, TITLE_CY + FS_TITLE // 2],
                 label="标题", tip="点击修改标题颜色")
    c.regions[-1]["values"] = {"ov:title_color": C_TITLE}
    c.text((start_x + ICON_R * 2 + icon_gap, TITLE_CY), title,
           size=FS_TITLE, color=C_TITLE, role="bold", anchor="lm")

    # ---- 顶部分割线 ----
    c.line((DIV_X0, DIV1_Y, DIV_X1, DIV1_Y), fill=C_DIV, width=1)

    # ---- 四组平原 ----
    for i, r in enumerate(rows):
        line1_y = ROW1_CY + int(round(ROW_PITCH * i))
        line2_y = line1_y + DESC_DY

        # 图标：真实星球图（球形图走圆形遮罩）；缺资源时回退为实心色圆
        accent = r.get("icon", "#888888")
        ix, iy = ICON_CX, line1_y
        pkey = r.get("planet") or _PLANET_BY_NAME.get(r.get("name", ""))
        pimg = _planet_img(pkey, ICON_R * 2)
        if pimg is not None:
            # 不规则天体（火卫二等）保留真实轮廓，只给球形/场景图加圆形遮罩
            mk = _circle_mask(ICON_R * 2) if pkey in _PLANET_ROUND else None
            c.paste_alpha(pimg, (ix - ICON_R, iy - ICON_R), mk)
        else:
            c.ellipse((ix - ICON_R, iy - ICON_R, ix + ICON_R, iy + ICON_R),
                      fill=accent, outline="#2A2E33", width=2)

        # 地名（黑色粗体）
        c.text((NAME_X, line1_y), r["name"], size=FS_NAME, color=C_NAME,
               role="bold", anchor="lm")

        # 状态胶囊：固定 x=PILL_X（参考图四行左缘对齐 254，与地名长短无关）
        is_purple = r.get("kind") == "purple"
        st_line = C_PURPLE if is_purple else C_BLUE
        st_w = _w(r["state"], FS_PILL) + PILL_PAD_X * 2
        st_h = FS_PILL + PILL_PAD_Y * 2
        st_x = PILL_X
        st_y = line1_y - st_h // 2
        c.rounded_rect((st_x, st_y, st_x + st_w, st_y + st_h), radius=PILL_RADIUS,
                       fill=C_PILL_FILL, outline=st_line, width=1)
        c.text((st_x + st_w // 2, line1_y), r["state"], size=FS_PILL,
               color=st_line, anchor="mm")

        # 行2：紫色斜体「- 距离X还有」+ 橙色倒计时胶囊
        itext = "- 距离%s还有" % r.get("next", "")
        c.text((DESC_X, line2_y), itext, size=FS_DESC, color=C_DESC,
               role="regular", anchor="lm", kind="sub")
        rem = r.get("remain", "进游戏看")
        rem_w = _w(rem, FS_REM) + REM_PAD_X * 2
        rem_h = FS_REM + REM_PAD_Y * 2
        rem_x = DESC_X + _w(itext, FS_DESC) + REM_GAP
        rem_y = line2_y - rem_h // 2
        c.rounded_rect((rem_x, rem_y, rem_x + rem_w, rem_y + rem_h), radius=REM_RADIUS,
                       fill=C_REM_FILL, outline=C_REM_LINE, width=1)
        c.text((rem_x + rem_w // 2, line2_y), rem, size=FS_REM,
               color=C_REM_TEXT, anchor="mm")

    # ---- 底部分割线 + footer ----
    c.line((DIV_X0, div2_y, DIV_X1, div2_y), fill=C_DIV, width=1)
    fy = foot_cy

    # PC 胶囊（绿描边）
    pc_w = _w("PC", FS_PC) + PC_PAD_X * 2
    pc_h = FS_PC + PC_PAD_Y * 2
    c.rounded_rect((PC_X, fy - pc_h // 2, PC_X + pc_w, fy + pc_h // 2), radius=PC_RADIUS,
                   fill=None, outline=C_GREEN, width=1)
    c.text((PC_X + pc_w // 2, fy), "PC", size=FS_PC, color=C_GREEN, anchor="mm")

    # 机器人名（绿）
    c.text((FOOT_NAME_X, fy), footer_name, size=FS_FOOT, color=C_GREEN, anchor="lm")
    # 灰色提示
    nx3 = FOOT_NAME_X + _w(footer_name, FS_FOOT) + FOOT_HINT_GAP
    c.text((nx3, fy), "*善用右键/长按提取文字", size=FS_FOOT, color=C_HINT, anchor="lm")

    return c


def _clock(c: Canvas, cx: int, cy: int, r: int, color: str) -> None:
    """时钟描线图标：圆 + 两根指针（参考图直径 28、近黑）。"""
    c.ellipse((cx - r, cy - r, cx + r, cy + r), fill=None, outline=color, width=2)
    c.line((cx, cy, cx, cy - int(r * 0.6)), fill=color, width=2)
    c.line((cx, cy, cx + int(r * 0.48), cy), fill=color, width=2)
