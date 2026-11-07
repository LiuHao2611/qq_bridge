# -*- coding: utf-8 -*-
"""archon —— 本周执刑官猎杀卡（2026-11-05，逐像素对齐 Rabbit 参考图）。

card-design 设计前分析（动工前逐项填好）：

* 用途：主信息 = 本周 BOSS；次级 = 三阶段任务（类型/等级/节点）+ 剩余时间。
* 层级：底图原像素已含标题 36 bold / 徽标 / 分割线 / 页脚；动态层字号
  BOSS 24 bold > 台词 23 > 任务名 24 bold > 节点 22 > Lv 18 > 标签 21。
* 尺寸：**固定 800×562**，与参考图逐像素同尺寸（不是内容驱动高度）。
* 主题：浅色。页面 #FDFCFD、卡片 #F9F3F6，配色全部取自参考图实测。
* 媒介：Pillow + 项目 Canvas；字体/字重走 pic.fonts；无 emoji。
* 动态范围：BOSS 名/台词/奖励/任务名/节点/倒计时长度可变 → 全部先
  ``Canvas.text_width`` 度量，超宽 ellipsis 截断；Lv 胶囊起点由任务名实际
  宽度 + 固定 gap 推算，节点胶囊左缘恒定 x=404（行尾不随前文漂移）。
  任务名上限 96px / 节点上限 150px 由「不与下一列相撞」反推，不是经验值。
* 列网格：卡片文字列 x=138；任务行 图标 x=148 / 任务名 x=182 /
  Lv 胶囊 (182 + w(任务名) + 8) / 节点胶囊固定 x=404。
* 间距 token：任务行 pitch 45；任务名→Lv gap 8；Lv→节点 gap 12；
  奖励标签→奖励 gap 8；胶囊内 padding 7 / 6。
* 语义色：紫 #885088（主宰者/BOSS）、深蓝 #106088（台词/任务名/节点/进度条）、
  蓝紫 #5070D0（奖励源力石，带下划线）、橙 #C65723（Lv 底 + 倒计时数字）、
  绿 #308848（任务胶囊，底图已保留）。

「全量对齐、禁止遗漏」的实现：``pic/assets/archon_base.png`` 是从参考图
擦净三块动态区后生成的**原始像素底图**——兔子线稿背景、右下角 Q 版吉祥物、
半月徽标、标题、分割线、卡片框与左侧紫条、BOSS 头像、蓝色源力石菱形、
绿色任务胶囊、页脚线、PC 徽章、页脚文字全部原样保留。运行时只重绘动态信息。

排版只留 ``emit`` 一个文本入口：先度量收集，再按「图形 → 图标 → 文字」
固定顺序一次性绘制；``build(boxes=...)`` 用同一组坐标登记真实包围盒
（度量与绘制同源，无裸 draw.text、无空格凑间距）。
"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from ..canvas import Canvas
from ..theme import Theme

# ---- 画布（与参考图逐像素一致）----
W, H = 800, 562
PAGE = "#FDFCFD"
CARD = "#F9F3F6"

C_TITLE = "#106088"       # 深蓝：标题/台词/任务名/节点/进度条描边
C_PURPLE = "#885088"      # 紫：当前战局主宰者 / BOSS
C_SHARD = "#5070D0"       # 蓝紫：奖励源力石（带下划线）
C_ORANGE = "#C65723"      # 橙：等级胶囊底 / 倒计时数字
C_ORANGE_TEXT = "#FFFFFF"
C_PROG_DARK = "#2A658D"   # 进度条已过部分
C_PROG_LIGHT = "#E9F1F8"  # 进度条未过部分
C_LABEL = "#202028"       # 奖励：标签近黑

# ---- 参考图实测坐标 ----
CARD_TEXT_X = 138
CY_HOST, CY_BOSS, CY_QUOTE, CY_REWARD = 141, 172, 222, 256
UNDERLINE_Y = 268                # 参考图实测下划线所在行

ROW_Y0, ROW_PITCH, ROW_H = 303, 45, 34
IC_X, MISSION_X, NODE_X = 148, 182, 404
GAP_MISSION_LV, GAP_LV_NODE = 8, 12
LV_PAD_X, NODE_PAD_X = 7, 4
NODE_PLANET_SZ, NODE_GAP_PLANET = 26, 4
#: 由「Lv 胶囊右缘 + gap ≤ NODE_X」反推的任务名/节点最大宽
MISSION_MAX_W = 96
NODE_MAX_W = 150

PROG_X0, PROG_X1 = 164, 399
PROG_Y0, PROG_Y1 = 446, 481
PROG_RADIUS = 17

FS_HOST, FS_BOSS, FS_QUOTE = 23, 26, 23
FS_REWARD_LABEL, FS_SHARD, FS_MISSION = 23, 26, 24
FS_LV, FS_NODE, FS_PROG = 18, 22, 25

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


@lru_cache(maxsize=8)
def _asset(name: str) -> Image.Image | None:
    p = os.path.join(_ASSETS, name)
    if not os.path.isfile(p):
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _ellipsis(s: str, size: int, role: str, max_w: int) -> str:
    s = str(s or "")
    if _w(s, size, role) <= max_w:
        return s
    while s and _w(s + "…", size, role) > max_w:
        s = s[:-1]
    return s + "…" if s else ""


#: missionType → 图标裁片名（取自参考图）
_ICONS = {
    "MT_MOBILE_DEFENSE": "archon_ic_mobile_defense.png",
    "MT_TERRITORY": "archon_ic_interception.png",
    "MT_INTERCEPTION": "archon_ic_interception.png",
    "MT_ASSASSINATION": "archon_ic_assassination.png",
}


def build(t: Theme, data: dict | None = None, width: int | None = None,
          boxes=None, **kw) -> Canvas:
    """绘制执刑官猎杀卡；boxes 非空时登记所有动态内容元素真实包围盒供自检。

    底图静态像素（标题/徽标/背景/吉祥物/页脚等）不登记为内容元素——它们
    是容器，文字落在其中属于设计，不参与「内容两两重叠」判定。
    """
    data = data or {}
    missions = list(data.get("missions") or [])[:3]
    boss = str(data.get("boss_name") or "BOSS-执刑官")
    quote = str(data.get("quote") or "本周执刑官已降临，准备迎战！")
    shard = str(data.get("shard") or "执刑官源力石")
    remain = str(data.get("remain") or "—")

    texts: list[tuple] = []   # (label, x, y_top, text, size, color, role, underline)
    rects: list[tuple] = []   # (box, fill, outline, width, radius)
    icons: list[tuple] = []   # (label, img, x, y, w, h)

    def emit(label, x, y_cy, text, size, color, role="regular", underline=False) -> tuple[int, int]:
        """度量优先：只登记，返回 (宽, 高)；绘制统一在最后按序执行。"""
        w, h = Canvas.text_size(text, size, role)
        texts.append((label, int(x), int(y_cy - h // 2), text, size, color, role, underline))
        return w, h

    def rect(box, fill, outline=None, lw=1, radius=0):
        rects.append((box, fill, outline, lw, radius))

    def icon(label, img: Image.Image | None, x: int, y: int, w: int, h: int) -> None:
        if img is None:
            return
        icons.append((label, img.resize((w, h), Image.LANCZOS), x, y, w, h))

    # ---------------- 卡片内四行 ----------------
    emit("host", CARD_TEXT_X, CY_HOST, "当前战局主宰者：", FS_HOST, C_PURPLE, "bold")
    emit("boss", CARD_TEXT_X, CY_BOSS,
         _ellipsis(boss, FS_BOSS, "bold", 420), FS_BOSS, C_PURPLE, "bold")
    emit("quote", CARD_TEXT_X, CY_QUOTE,
         _ellipsis(quote, FS_QUOTE, "regular", 420), FS_QUOTE, C_TITLE)
    rw, _ = emit("reward_label", CARD_TEXT_X, CY_REWARD, "奖励：",
                 FS_REWARD_LABEL, C_LABEL)
    emit("shard", CARD_TEXT_X + rw + 8, CY_REWARD,
         _ellipsis(shard, FS_SHARD, "bold", 300), FS_SHARD, C_SHARD,
         "bold", underline=True)

    # ---------------- 三行任务 ----------------
    for i, m in enumerate(missions):
        y0 = ROW_Y0 + ROW_PITCH * i
        cy = y0 + ROW_H // 2
        mk = str(m.get("mission_key") or "")
        mission = _ellipsis(str(m.get("mission") or "—"), FS_MISSION, "bold", MISSION_MAX_W)
        lv = str(m.get("lv") or "")
        node = _ellipsis(str(m.get("node") or "—"), FS_NODE, "regular", NODE_MAX_W)

        im = _asset(_ICONS.get(mk, "archon_ic_mobile_defense.png"))
        if im is not None:
            ih = min(40, int(im.height * 32 / im.width))
            icon(f"row{i}.icon", im, IC_X, cy - ih // 2, 32, ih)

        mw, _ = emit(f"row{i}.mission", MISSION_X, cy, mission, FS_MISSION,
                     C_TITLE, "bold")
        lv_x = MISSION_X + mw + GAP_MISSION_LV
        lv_w = _w(lv, FS_LV, "bold") + LV_PAD_X * 2   # 与实际绘制同一字重
        rect((lv_x, y0, lv_x + lv_w, y0 + ROW_H), C_ORANGE, radius=4)
        # 右上角小折角（参考图细节，禁止遗漏）
        rect((lv_x + lv_w - 22, y0, lv_x + lv_w - 2, y0 + 4), C_ORANGE)
        emit(f"row{i}.lv.text", lv_x + LV_PAD_X, cy, lv, FS_LV, C_ORANGE_TEXT, "bold")

        # 节点胶囊：左缘恒定 NODE_X，不随任务名长度漂移
        nw = _w(node, FS_NODE, "regular")
        node_w = NODE_PAD_X + NODE_PLANET_SZ + NODE_GAP_PLANET + nw + NODE_PAD_X
        rect((NODE_X, y0, NODE_X + node_w, y0 + ROW_H), "#FFFFFF",
             outline=C_TITLE, lw=1, radius=ROW_H // 2)
        planet = _asset("archon_ic_planet.png")
        px = NODE_X + NODE_PAD_X
        py = cy - NODE_PLANET_SZ // 2
        icon(f"row{i}.planet", planet, px, py, NODE_PLANET_SZ, NODE_PLANET_SZ)
        emit(f"row{i}.node.text",
             NODE_X + NODE_PAD_X + NODE_PLANET_SZ + NODE_GAP_PLANET,
             cy, node, FS_NODE, C_TITLE)

    # ---------------- 剩余时间进度条 ----------------
    rect((PROG_X0, PROG_Y0, PROG_X1, PROG_Y1), C_PROG_LIGHT,
         outline=C_TITLE, lw=1, radius=PROG_RADIUS)
    try:
        days = int(str(remain).split("天")[0])
    except (ValueError, IndexError):
        days = 3
    ratio = min(0.78, max(0.18, days / 7.0))
    dark_x1 = PROG_X0 + int((PROG_X1 - PROG_X0) * ratio)
    pw = _w(remain, FS_PROG, "bold")
    emit("progress.text", (PROG_X0 + PROG_X1) // 2 - pw // 2,
         (PROG_Y0 + PROG_Y1) // 2, remain, FS_PROG, C_ORANGE, "bold")

    # ---------------- 一次性绘制：图形 → 图标 → 文字 ----------------
    c = Canvas(W, H, PAGE, theme=t)
    base = _asset("archon_base.png")
    if base is not None:
        c.paste_alpha(base, (0, 0))

    for box, fill, outline, lw, radius in rects:
        c.rounded_rect(box, radius=radius, fill=fill, outline=outline, width=lw)

    # 进度条已过部分：整图透明层 + 圆角遮罩，只裁出左侧深色段
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (PROG_X0, PROG_Y0, PROG_X1, PROG_Y1), radius=PROG_RADIUS, fill=255)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((PROG_X0, PROG_Y0, dark_x1, PROG_Y1),
                                    fill=(42, 101, 141, 255))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
    c.paste_alpha(layer, (0, 0))

    for label, img, x, y, w, h in icons:
        c.paste_alpha(img, (x, y))
        if boxes is not None:
            boxes.append((label, x, y, x + w, y + h))

    for label, x, y_top, text, size, color, role, underline in texts:
        w, h = Canvas.text_size(text, size, role)
        c.text((x, y_top + h // 2), text, size=size, color=color,
               role=role, anchor="lm")
        if underline:
            c.line((x, UNDERLINE_Y, x + w, UNDERLINE_Y), fill=color, width=2)
        if boxes is not None:
            boxes.append((label, x, y_top, x + w, y_top + h))
    return c
