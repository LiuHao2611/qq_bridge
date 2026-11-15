# -*- coding: utf-8 -*-
"""pic/templates/doupo_menu.py —— 斗破苍穹 · 口令菜单卡。

用于群聊「斗破菜单 / dp菜单」的回复：把全部口令按玩法分组列成一张卡，
替代 20 多行纯文本（手机上要滑很久，且难扫读）。

版式（羊皮纸米 / 朱红 / 暗金玄幻观感，与 doupo.py 人物卡同一套调色）：
  * 顶部居中标题「斗破菜单」+ sword 细描边图标 + 贯通分割线；
  * 每个玩法分组：左侧 4px 暗金竖条 + 分组名（强调色），组间一条暗金分隔线；
  * 每行两列：左=口令（墨色真粗体，主信息）／右=一句话说明（灰，次信息）；
  * 行与行之间统一 8px gap + 淡色分隔线；底部提示。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * **不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色复用 ``Theme.bg_overrides["doupo"]``（与人物卡同一卡片主题，编辑器改一次两卡同步）。

2026-11-15 card-design 度量优先重写（用户「优化菜单，加粗字看着模糊」）：
  1. ``layout()`` 是**唯一布局入口**：build 与 ``layout_boxes()`` 自检共用同一份
     节点坐标，杜绝「高度按一种算法算、渲染按另一种画」的底部裁切。
  2. 口令列宽由**实测最长口令**推导（``Canvas.text_width(..., "bold")``，与绘制同字体），
     上限 48% 内容宽；旧版写死 236px 且 ``max_w=CMD_COL-26`` 会把
     「上阵 技1 技2 技3 技4」截断成「上阵 技1 技2 技3…」。现在口令按列宽折行，
     **一个字都不丢**（``"".join(cmd_lines) == cmd`` 由门禁锁住）。
  3. 列间距是显式常量 ``COL_GAP=24``，不是靠空格或经验 x 偏移凑出来的；
     说明列右边界 = content_right，越界由 check_boxes 拦。
  4. 口令与说明两个文本块共用同一竖直中轴居中，折行不再上下错位。
  5. 加粗字形统一走项目内置真粗体（见 fonts.py::_BUNDLED_BOLD），不再叠伪粗描边。

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
from . import doupo_style as S

# ---- 排版（4 的倍数 token；改这里必须同步跑 tools/check_doupo_typography.py）----
PAD = 28
FS_TITLE = 34
FS_GROUP = S.FS_KEY - 2        # 22 分组名（真粗体）
FS_CMD = S.FS_KEY              # 24 口令——整张卡最重要的可扫读信息
FS_DESC = S.FS_SUB             # 17 说明
FS_TIP = S.FS_LABEL            # 16 底部提示

CMD_INSET = 14                 # 口令文字左缩进（与分组竖条对齐）
COL_GAP = 24                   # 口令列 ↔ 说明列的固定列间距（>= 24px 字号的 10px 阈值）
CMD_MIN_COL = 208              # 口令列下限（x0 起算）
CMD_MAX_RATIO = 0.48           # 口令列上限：内容宽 48%，说明列至少留 52%
ROW_GAP = 8                    # 行与行统一间距
ROW_PAD = 12                   # 行内上下留白合计
ROW_MIN = 48
CMD_LH = FS_CMD + 6            # 30（1.25×）——口令折行行高
DESC_LH = FS_DESC + 6          # 23（1.35×）——说明折行行高
GROUP_GAP = 24                 # 分组之间统一间距
HEAD_GAP = 12                  # 分组名 → 首行
ICON_STYLE = "line"

_DEFAULT_TIP = "与「修仙」重名的 商店/突破/任务/排行榜/炼丹 已统一让出，斗破一律 dp 前缀。"

#: 编辑器预览用的示例数据。业务调用总会传真实 groups；这里只保证
#: 主题编辑器点开该模板时不是一张空白卡（pic 包不 import 插件层，故内置示例）。
_DEMO = [
    {"name": "入门 · 建立角色",
     "rows": [["斗破菜单", "就是这条，随时回来看全部口令"],
              ["入道", "创建角色：斗之气一段，赠 10000 金币 + 玄重尺 + 回灵丹×5"]]},
    {"name": "修炼 · 涨修为升境界",
     "rows": [["修炼", "结算离线修为（每分钟自动 +6）"],
              ["突破", "修为满时晋升一星；102 级九星斗圣冲斗帝看机缘"]]},
]


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


def _groups(data: dict) -> list:
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)]
    return groups or _DEMO


def _cmd_col(groups: list, content_w: int) -> int:
    """口令列宽 = 实测最长口令 + 左缩进 + 显式列间距，夹在 [下限, 48% 内容宽]。

    度量与绘制同 role（"bold" → 项目内置真粗体），所以换字体后列宽自动跟着变，
    不需要手调魔法数字，也不会再出现「测量按常规字、绘制按粗体字」的溢出。
    """
    max_w = 0
    for g in groups:
        for r in _rows_of(g):
            max_w = max(max_w, Canvas.text_width(str(r[0]), FS_CMD, "bold"))
    want = CMD_INSET + max_w + COL_GAP
    return max(CMD_MIN_COL, min(int(content_w * CMD_MAX_RATIO), want))


def _row_parts(row, content_w: int, cmd_col: int) -> dict:
    """单行内容：口令/说明各自折行 + 内容块高 + 行高（build 与自检共用）。"""
    cmd = str(row[0]) if len(row) > 0 else ""
    desc = str(row[1]) if len(row) > 1 else ""
    cmd_lines = Canvas.wrap(cmd, FS_CMD, "bold", max(1, cmd_col - CMD_INSET - COL_GAP)) or [""]
    desc_lines = Canvas.wrap(desc, FS_DESC, "regular", max(1, content_w - cmd_col)) if desc else []
    block_h = max(len(cmd_lines) * CMD_LH, len(desc_lines) * DESC_LH)
    return {"cmd": cmd_lines, "desc": desc_lines, "block_h": block_h,
            "rh": max(ROW_MIN, block_h + ROW_PAD)}


def layout(data: dict | None = None, width: int | None = None) -> dict:
    """唯一布局入口：算出画布高度与全部绘制节点（含精确 y 坐标）。

    ``build()`` 只负责照节点画；``layout_boxes()`` 照同一批节点算真实包围盒。
    """
    data = data or {}
    width = max(560, min(int(width or 720), 900))
    groups = _groups(data)
    content_w = width - PAD * 2
    cmd_col = _cmd_col(groups, content_w)
    nodes: list[dict] = []

    y = PAD
    nodes.append({"k": "title", "cy": y + FS_TITLE // 2})
    y += FS_TITLE + 8
    nodes.append({"k": "line", "y": y, "strong": True})
    y += 1 + 18

    for gi, g in enumerate(groups):
        if gi:
            nodes.append({"k": "divider", "y": y - GROUP_GAP // 2, "strong": True})
        nodes.append({"k": "group", "name": str(g.get("name") or ""),
                      "cy": y + FS_GROUP // 2, "bar": (y + 3, y + FS_GROUP - 3)})
        y += FS_GROUP + HEAD_GAP
        rows = _rows_of(g)
        for ri, r in enumerate(rows):
            part = _row_parts(r, content_w, cmd_col)
            rh, block_h = part["rh"], part["block_h"]
            top = y + max(0, (rh - block_h) // 2)
            cmd_top = top + max(0, (block_h - len(part["cmd"]) * CMD_LH) // 2)
            desc_top = top + max(0, (block_h - len(part["desc"]) * DESC_LH) // 2)
            nodes.append({
                "k": "row", "y": y, "rh": rh,
                "cmd": [(ln, cmd_top + i * CMD_LH + CMD_LH // 2)
                        for i, ln in enumerate(part["cmd"])],
                "desc": [(ln, desc_top + i * DESC_LH + DESC_LH // 2)
                         for i, ln in enumerate(part["desc"])],
            })
            y += rh
            if ri < len(rows) - 1:
                nodes.append({"k": "divider", "y": y + ROW_GAP // 2, "strong": False})
                y += ROW_GAP
        y += GROUP_GAP

    y -= 4
    nodes.append({"k": "line", "y": y, "strong": True})
    y += 1 + 12
    tip = data.get("tip")
    if tip is None:
        tip = _DEFAULT_TIP
    nodes.append({"k": "tip", "cy": y + FS_TIP // 2, "text": str(tip or "")})
    y += FS_TIP + PAD

    return {"width": width, "h": max(360, y), "nodes": nodes,
            "content_w": content_w, "cmd_col": cmd_col,
            "title": str(data.get("title") or "斗破菜单")}


def layout_boxes(L: dict) -> list[tuple]:
    """把 layout() 的节点翻译成真实包围盒，供 check_boxes 自检（同源度量）。"""
    x0 = PAD
    cmd_col = L["cmd_col"]
    boxes: list[tuple] = []
    for i, n in enumerate(L["nodes"]):
        k = n["k"]
        if k == "title":
            tw = _w(L["title"], FS_TITLE, "light")
            ico_w = FS_TITLE + 10
            tx = x0 + (L["content_w"] - tw - ico_w) // 2
            boxes.append(("title", tx, n["cy"] - FS_TITLE // 2,
                          tx + ico_w + tw, n["cy"] + FS_TITLE // 2))
        elif k == "group":
            w = Canvas.text_width(n["name"], FS_GROUP, "bold")
            boxes.append(("group:%d:%s" % (i, n["name"]), x0 + CMD_INSET,
                          n["cy"] - FS_GROUP // 2, x0 + CMD_INSET + w,
                          n["cy"] + FS_GROUP // 2))
        elif k == "row":
            for j, (ln, cy) in enumerate(n["cmd"]):
                w = Canvas.text_width(ln, FS_CMD, "bold")
                boxes.append(("cmd.%d.%d" % (i, j), x0 + CMD_INSET,
                              cy - CMD_LH // 2, x0 + CMD_INSET + w, cy + CMD_LH // 2))
            for j, (ln, cy) in enumerate(n["desc"]):
                w = Canvas.text_width(ln, FS_DESC, "regular")
                boxes.append(("desc.%d.%d" % (i, j), x0 + cmd_col,
                              cy - DESC_LH // 2, x0 + cmd_col + w, cy + DESC_LH // 2))
        elif k == "tip" and n["text"]:
            txt = _fit(n["text"], L["content_w"], FS_TIP, "regular")
            w = Canvas.text_width(txt, FS_TIP, "regular")
            boxes.append(("tip", x0, n["cy"] - FS_TIP // 2,
                          x0 + w, n["cy"] + FS_TIP // 2))
    return boxes


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    L = layout(data, width)
    w, h = L["width"], L["h"]
    p = _pal(t)
    x0 = PAD
    content_w, cmd_col = L["content_w"], L["cmd_col"]

    c = Canvas(w, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, w - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    for n in L["nodes"]:
        k = n["k"]
        if k == "title":
            # ---- 标题（sword 图标 + 居中标题，与人物卡同一品牌标识） ----
            tw = _w(L["title"], FS_TITLE, "light")
            ico_w = FS_TITLE + 10
            tx = x0 + (content_w - tw - ico_w) // 2
            icons.draw(c, "sword", tx + FS_TITLE // 2, n["cy"], FS_TITLE,
                       p["title_color"], style=ICON_STYLE)
            c.text((tx + ico_w, n["cy"]), L["title"], size=FS_TITLE,
                   color=p["title_color"], role="light", anchor="lm")
        elif k == "line":
            c.line((x0, n["y"], x0 + content_w, n["y"]),
                   fill=p["accent_color"] if n["strong"] else p["line_color"], width=1)
        elif k == "divider":
            # 分组分隔线贯通、行分隔线从口令缩进处起：层级一眼可分，又不喧宾夺主
            left = x0 if n["strong"] else x0 + CMD_INSET
            c.line((left, n["y"], x0 + content_w, n["y"]),
                   fill=p["accent_color"] if n["strong"] else p["line_color"], width=1)
        elif k == "group":
            c.rounded_rect((x0 + 1, n["bar"][0], x0 + 5, n["bar"][1]),
                           radius=2, fill=p["accent_color"])
            S.key(c, (x0 + CMD_INSET, n["cy"]), n["name"],
                  size=FS_GROUP, color=p["accent_color"])
        elif k == "row":
            for ln, cy in n["cmd"]:
                S.key(c, (x0 + CMD_INSET, cy), ln, size=FS_CMD, color=p["text_color"])
            for ln, cy in n["desc"]:
                S.small(c, (x0 + cmd_col, cy), ln, size=FS_DESC, color=p["dim_color"])
        elif k == "tip":
            if n["text"]:
                S.small(c, (x0, n["cy"]), n["text"], size=FS_TIP,
                        color=p["dim_color"], max_w=content_w)

    if source:
        S.small(c, (w - PAD, h - PAD + 6), source, size=14,
                color=p["dim_color"], anchor="rb")

    return c
