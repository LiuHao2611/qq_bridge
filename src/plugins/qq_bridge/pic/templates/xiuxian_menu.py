# -*- coding: utf-8 -*-
"""pic/templates/xiuxian_menu.py —— 文字修仙 · 口令菜单卡。

用于群聊「修仙菜单」的回复：把所有口令按玩法分组列成一张两列清单卡，
替代 20 多行纯文本（手机上要滑很久，且难扫读）。

版式（与 xiuxian.py 状态卡同一套 Rabbit 极简观感，配色共享同一份 bg_overrides）：
  * 顶部居中标题「修仙菜单」+ book 细描边图标 + 贯通分割线；
  * 每个玩法分组：左侧 4px 青绿竖条 + 分组名；
  * 每行两列：左=口令原文（墨色，主信息）／右=一句话说明（灰色，次信息）；
  * 底部提示：口令直接发群里，@ 要真的 @。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * **不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色复用 ``bg_overrides["xiuxian"]``（与状态卡同一卡片主题，编辑器改一次两卡同步）。

输入（build 的 params，均为 JSON 友好类型，便于编辑器预览）：
  title     标题，默认「修仙菜单」
  groups    [{"name": 分组名, "rows": [[口令, 说明], ...]}, ...]
  tip       底部提示（可留空）
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与状态卡共用配色/度量工具（同包内复用，避免两份 _pal 各自漂移）
from .xiuxian import _autosize, _card_alpha, _fit, _pal, _w

# ---- 排版 ----
PAD = 26
FS_TITLE = 30
FS_GROUP = 16
FS_CMD = 18
FS_DESC = 16
CMD_COL = 200     # 说明列起点（相对内容区左边）；最长口令「送灵石 100 @某人」152px，留 34px 呼吸
ROW_H = 36
GROUP_GAP = 20
ICON_STYLE = "line"

#: 编辑器预览用的示例数据。业务调用总会传真实 groups；这里只保证
#: 主题编辑器点开该模板时不是一张空白卡（pic 包不 import 插件层，故内置示例）。
_DEMO = [
    {"name": "入门 · 建立角色",
     "rows": [["我要修仙", "创建角色，随机灵根，赠 50 灵石"], ["我的修仙", "查看状态卡"]]},
    {"name": "修炼 · 涨修为升境界",
     "rows": [["闭关", "修为产出 ×2，离线也累积"], ["突破", "冲击下一境界"]]},
]


def _list_glyph(c: Canvas, cx: int, cy: int, color: str) -> None:
    """手绘「清单」字形（三条不等长圆头横线）。

    不用 icons 的 book：line 风格下 book 在标题尺寸会退化成一个空心方框
    （30px 实测确认），辨识度为零；手绘三行线更像菜单、也更稳。
    """
    for i, w in enumerate((16, 16, 10)):
        yy = cy - 6 + i * 6
        c.rounded_rect((cx - 8, yy - 1, cx - 8 + w, yy + 1), radius=1, fill=color)


def _rows_of(g: dict) -> list:
    rows = g.get("rows") or []
    return [r for r in rows if r]


def _measure(groups: list) -> int:
    """总高度（与 build 里的 y 步进严格一致，否则卡底留白会歪）。"""
    y = PAD + FS_TITLE + 8 + 1 + 18
    for g in groups:
        y += FS_GROUP + 12 + len(_rows_of(g)) * ROW_H + GROUP_GAP
    y += -4 + 1 + 12 + FS_DESC + PAD
    return max(260, y)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)] or _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    h = _measure(groups)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题（手绘清单字形 + 居中标题） ----
    title = str(data.get("title") or "修仙菜单")
    tw = _w(title, FS_TITLE, "light")
    ico_w = 16 + 12
    tx = x0 + (content_w - tw - ico_w) // 2
    _list_glyph(c, tx + 8, y + FS_TITLE // 2, p["title_color"])
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 分组清单 ----
    for g in groups:
        # 分组头：4px 竖条 + 分组名（比正文小半号、用强调色，形成节奏但不抢戏）
        c.rounded_rect((x0 + 1, y + 3, x0 + 5, y + FS_GROUP - 3), radius=2, fill=p["accent_color"])
        c.text((x0 + 14, y + FS_GROUP // 2), str(g.get("name") or ""), size=FS_GROUP,
               color=p["accent_color"], role="regular", anchor="lm")
        y += FS_GROUP + 12

        for row in _rows_of(g):
            cmd = str(row[0]) if len(row) > 0 else ""
            desc = str(row[1]) if len(row) > 1 else ""
            cy = y + ROW_H // 2
            c.text((x0 + 14, cy), _fit(cmd, CMD_COL - 22, FS_CMD), size=FS_CMD,
                   color=p["text_color"], role="regular", anchor="lm")
            if desc:
                avail = content_w - CMD_COL
                txt, sz = _autosize(desc, avail, (FS_DESC, FS_DESC - 1, FS_DESC - 2, FS_DESC - 3))
                c.text((x0 + CMD_COL, cy), txt, size=sz, color=p["dim_color"], anchor="lm")
            y += ROW_H
        y += GROUP_GAP

    # ---- 底部提示 ----
    y -= 4
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip is None:
        tip = "口令直接发在群里即可 · 写成 @某人 的地方要真的 @ 对方"
    if tip:
        icons.draw(c, "bolt", x0 + 8, y + FS_DESC // 2, 14, p["accent_color"], style=ICON_STYLE)
        c.text((x0 + 26, y + FS_DESC // 2), _fit(str(tip), content_w - 26, FS_DESC),
               size=FS_DESC, color=p["dim_color"], anchor="lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")

    return c
