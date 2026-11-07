# -*- coding: utf-8 -*-
"""pic/templates/doupo_menu.py —— 斗破苍穹 · 口令菜单卡。

用于群聊「斗破菜单 / dp菜单」的回复：把全部口令按玩法分组列成一张卡，
替代 20 多行纯文本（手机上要滑很久，且难扫读）。

版式（羊皮纸米 / 朱红 / 暗金玄幻观感，与 doupo.py 人物卡同一套调色）：
  * 顶部居中标题「斗破菜单」+ sword 细描边图标 + 贯通分割线；
  * 每个玩法分组：左侧 4px 暗金竖条 + 分组名（强调色）；
  * 每行两列：左=口令（墨色，主信息）／右=一句话说明（灰，次信息，过长自动折行）；
  * 底部提示：与「修仙」重名的口令已统一让出，斗破一律 dp 前缀。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * **不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色复用 ``Theme.bg_overrides["doupo"]``（与人物卡同一卡片主题，编辑器改一次两卡同步）。

输入（build 的 params，均为 JSON 友好类型，便于编辑器预览）：
  title     标题，默认「斗破菜单」
  groups    [{"name": 分组名, "rows": [[口令, 说明], ...]}, ...]
  tip       底部提示（可留空）
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与人物卡共用配色/度量工具（同包内复用，避免两份 _pal 各自漂移）
from .doupo import _card_alpha, _fit, _pal, _w

# ---- 排版 ----
PAD = 28
FS_TITLE = 34
FS_GROUP = 18
FS_CMD = 19
FS_DESC = 16
FS_TIP = 14
CMD_COL = 200          # 说明列起点（相对内容区左边）；最长口令「斗破菜单 / dp菜单」约 160px
ROW_PAD = 12
ROW_MIN = 42
DESC_LH = FS_DESC + 4  # 说明折行行高
GROUP_GAP = 18
ICON_STYLE = "line"

#: 编辑器预览用的示例数据。业务调用总会传真实 groups；这里只保证
#: 主题编辑器点开该模板时不是一张空白卡（pic 包不 import 插件层，故内置示例）。
_DEMO = [
    {"name": "入门 · 建立角色",
     "rows": [["斗破菜单 / dp菜单", "就是这条，随时回来看全部口令"],
              ["dp入道", "创建角色：一星斗者，赠 10000 金币 + 玄重尺 + 回灵丹×5"]]},
    {"name": "修炼 · 涨修为升境界",
     "rows": [["dp修炼", "结算离线修为（每分钟自动 +6）"],
              ["dp突破", "修为满时晋升一星；28 级冲斗帝看机缘"]]},
]


def _rows_of(g: dict) -> list:
    rows = g.get("rows") or []
    return [r for r in rows if r]


def _row_h(desc: str, content_w: int) -> int:
    """单行高度：说明 1 行取 ROW_MIN；折行则按行数撑高（与 build 严格一致）。"""
    avail = content_w - CMD_COL
    lines = Canvas.wrap(desc or "", FS_DESC, "regular", avail)
    n = max(1, len(lines))
    return max(ROW_MIN, n * DESC_LH + ROW_PAD)


def _measure(groups: list, content_w: int) -> int:
    """总高度（与 build 里的 y 步进严格一致，否则卡底留白会歪）。"""
    y = PAD + FS_TITLE + 8 + 1 + 18
    for g in groups:
        y += FS_GROUP + 12
        for r in _rows_of(g):
            y += _row_h(r[1] if len(r) > 1 else "", content_w)
        y += GROUP_GAP
    y += -4 + 1 + 12 + FS_TIP + PAD
    return max(360, y)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)] or _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    h = _measure(groups, content_w)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题（sword 图标 + 居中标题，与人物卡同一品牌标识） ----
    title = str(data.get("title") or "斗破菜单")
    tw = _w(title, FS_TITLE, "light")
    ico_w = FS_TITLE + 10
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, "sword", tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE,
               p["title_color"], style=ICON_STYLE)
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
            rh = _row_h(desc, content_w)
            cy = y + rh // 2
            c.text((x0 + 14, cy), _fit(cmd, CMD_COL - 26, FS_CMD), size=FS_CMD,
                   color=p["text_color"], role="regular", anchor="lm")
            if desc:
                avail = content_w - CMD_COL
                lines = Canvas.wrap(desc, FS_DESC, "regular", avail)
                block_h = len(lines) * DESC_LH
                dy = y + max(0, (rh - block_h) // 2) + FS_DESC // 2
                for i, ln in enumerate(lines):
                    c.text((x0 + CMD_COL, dy + i * DESC_LH), ln, size=FS_DESC,
                           color=p["dim_color"], anchor="lm")
            y += rh
        y += GROUP_GAP

    # ---- 底部提示 ----
    y -= 4
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip is None:
        tip = "与「修仙」重名的 商店/突破/任务/排行榜/炼丹 已统一让出，斗破一律 dp 前缀。"
    if tip:
        c.text((x0, y + FS_TIP // 2), _fit(str(tip), content_w, FS_TIP),
               size=FS_TIP, color=p["dim_color"], anchor="lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")

    return c
