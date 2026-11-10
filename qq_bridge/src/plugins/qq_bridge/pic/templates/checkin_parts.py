# -*- coding: utf-8 -*-
"""pic/templates/checkin_parts.py —— 签到卡「度量/混色/包围盒」纯辅助（按功能拆分）。

从 checkin.py 拆出，让主模板 <20KB（gate.sh 硬上限 20480B，checkin.py HEAD 仅 14KB、
非存量超大文件、无棘轮豁免资格）。本模块**无业务判断**、不 import 上级包，纯函数 +
版式常量，供 checkin.build 的「度量与绘制同源」使用。

  * 版式常量：布局 y 游标用统一 GAP + 这些常量逐分区推进；
  * _month_matrix / _tb：月历几何与文本包围盒推算（门禁 check_boxes 同源）；
  * _blend：两主题色线性混合出浅胶囊底/描边（不引额外依赖）；
  * _w / _rec：文本宽度缓存包装 / 包围盒登记（boxes=None 时零开销）。
"""
from __future__ import annotations

from datetime import datetime

from ..canvas import Canvas, rgb

# ---- 画布 / 留白 / 统一间距（度量与绘制同源）----
PAD = 28                 # 主卡外边距
GAP = 14                 # 分区之间的统一垂直间距
CONTENT_PAD = 16         # 主卡内左右额外留白

# ---- 顶部标题 ----
HDR_H = 96
FS_TITLE = 38
FS_SUB = 15
FS_RANK = 17

# ---- 结果横幅 ----
BANNER_H = 88
BANNER_BAR = 5           # 绿色左边框宽度
FS_BANNER_T = 21
FS_BANNER_M = 16

# ---- 分区标题 ----
FS_SEC = 19
SEC_TITLE_H = 28

# ---- 状态胶囊 2×2 ----
STATUS_CARD_PAD = 18
PILL_H = 52
PILL_GAP_X = 12
PILL_GAP_Y = 10
FS_PILL_LAB = 14
FS_PILL_VAL = 20
PILL_ICON = 18

# ---- 里程碑进度条 ----
MILE_BAR_H = 16
MILE_LEGEND_H = 24
FS_LEGEND = 13

# ---- 奖励胶囊 ----
REWARD_PILL_H = 38
REWARD_PILL_GAP = 10
FS_REWARD = 15

# ---- 月历 ----
WD_H = 26
DAY_H = 34
FS_WD = 13
FS_DAY = 15

# ---- 说明 / 页脚 ----
NOTE_LINE_H = 22
NOTE_GAP = 5
FS_NOTE = 13
FOOTER_H = 44
FS_FOOT = 13
PC_W, PC_H = 34, 22

_MILESTONES = ((3, 30), (7, 80), (15, 200), (30, 500))
_MILE_MAX = 30


def _blend(c1: str, c2: str, ratio: float) -> str:
    """两色线性混合：ratio=0 → c1，ratio=1 → c2。用于浅绿/浅蓝胶囊底。"""
    r1, g1, b1 = rgb(c1)
    r2, g2, b2 = rgb(c2)
    f = max(0.0, min(1.0, ratio))
    return "#%02X%02X%02X" % (int(r1 + (r2 - r1) * f),
                              int(g1 + (g2 - g1) * f),
                              int(b1 + (b2 - b1) * f))


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _rec(boxes, label: str, box) -> None:
    """收集叶子元素包围盒（boxes 为 None 时零开销）。"""
    if boxes is not None:
        boxes.append((label, *box))


def _tb(pos, text: str, size: int, role: str, anchor: str) -> tuple:
    """由 text_size + anchor 推算文本真实包围盒（与绘制测量同源）。"""
    w, h = Canvas.text_size(text, size, role)
    x, y = pos
    if anchor == "mm":
        return (x - w // 2, y - h // 2, x + w // 2, y + h // 2)
    if anchor == "lm":
        return (x, y - h // 2, x + w, y + h // 2)
    if anchor in ("la", "ls"):
        return (x, y, x + w, y + h)
    if anchor in ("ra", "rs"):
        return (x - w, y, x, y + h)
    if anchor == "ms":
        return (x - w // 2, y, x + w // 2, y + h)
    return (x, y - h // 2, x + w, y + h // 2)


def _month_matrix(year: int, month: int):
    """返回 (首日列偏移[周日=0], 当月天数)。"""
    first_wd = datetime(year, month, 1).weekday()      # Mon=0
    offset = (first_wd + 1) % 7                         # 转周日为首列
    if month != 12:
        nxt = datetime(year, month + 1, 1)
    else:
        nxt = datetime(year + 1, 1, 1)
    dim = (nxt - datetime(year, month, 1)).days
    return offset, dim
