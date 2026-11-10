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
# 签到卡专用分层字体（绘制/度量同源）：_w/_tb 必须走它，不能走 Canvas.text_* 的雅黑度量，
# 否则「测量按雅黑、绘制按风格字」会导致包围盒与实际绘制错位（card-design 红线 6）。
from . import checkin_font as _cf

# ---- 画布 / 留白 / 统一间距（度量与绘制同源）----
# 2026-11-10 15:0x 视觉精修：所有「间距类」常量归一到 4 的倍数 token
# （2/4/8/12/16/24/32），由 spacing_token_problems() 门禁固化，杜绝 10/14/18/22 混用。
PAD = 28                 # 主卡外边距（7×4）
GAP = 16                 # 分区之间的统一垂直间距（was 14 → token 16）
CONTENT_PAD = 16         # 主卡内左右额外留白

# ---- 顶部标题 ----
HDR_H = 96
FS_TITLE = 40            # 主标题（was 38）
FS_SUB = 16              # 副标题（was 15）
FS_RANK = 16             # 名次徽章（was 17，弱化不抢副标题）

# ---- 结果横幅 ----
BANNER_H = 92            # was 88，给放大后的主标题/说明更多呼吸
BANNER_BAR = 5           # 绿色左边框宽度（装饰条，非间距 token）
FS_BANNER_T = 24         # 横幅主标题（was 21，与核心数值同档）
FS_BANNER_M = 17         # 横幅说明正文（was 16）

# ---- 分区标题 ----
FS_SEC = 20              # 分区标题（was 19，与主标题/数值拉开层级）
SEC_TITLE_H = 28

# ---- 状态胶囊 2×2 ----
STATUS_CARD_PAD = 16     # was 18 → token
PILL_H = 56              # 14×4（was 52，给 24px bold 数值更从容的垂直呼吸）
PILL_GAP_X = 12
PILL_GAP_Y = 12          # was 10 → token
FS_PILL_LAB = 15         # 标签（was 14）
FS_PILL_VAL = 24         # 核心数值（was 20，视觉焦点）
PILL_ICON = 18           # 图标尺寸（非间距 token）

# ---- 里程碑进度条 ----
MILE_BAR_H = 16
MILE_LEGEND_H = 24
MILE_LEGEND_GAP = 20     # 图例 token 水平间距（5×4 token；was 硬编码 12，肉眼抓到粘连）
FS_LEGEND = 14           # 刻度弱文字（was 13，浅色底可读性）

# ---- 奖励胶囊 ----
REWARD_PILL_H = 40       # was 38 → token
REWARD_PILL_GAP = 12     # was 10 → token
FS_REWARD = 16           # was 15

# ---- 月历 ----
WD_H = 28                # was 26 → token
DAY_H = 36               # was 34 → token
FS_WD = 14               # 星期弱文字（was 13）
FS_DAY = 16              # 日期（was 15）

# ---- 说明 / 页脚 ----
NOTE_LINE_H = 24         # was 22 → token
NOTE_GAP = 8             # was 5 → token（两行说明间距）
FS_NOTE = 14             # 说明弱文字（was 13，浅色底可读性）
FOOTER_H = 44
FS_FOOT = 14             # 页脚弱文字（was 13）
PC_W, PC_H = 36, 24      # 控件尺寸规整（was 34,22）

_MILESTONES = ((3, 30), (7, 80), (15, 200), (30, 500))
_MILE_MAX = 30

#: 间距类常量（须为 4 的倍数 token；装饰条 BANNER_BAR / 图标 PILL_ICON /
#: 控件 PC_W,PC_H 属尺寸而非间距，不在其列）。字号层级由 type_scale_problems 守。
SPACING_KEYS = (
    "PAD", "GAP", "CONTENT_PAD", "HDR_H", "BANNER_H", "SEC_TITLE_H",
    "STATUS_CARD_PAD", "PILL_H", "PILL_GAP_X", "PILL_GAP_Y",
    "MILE_BAR_H", "MILE_LEGEND_H", "MILE_LEGEND_GAP", "REWARD_PILL_H", "REWARD_PILL_GAP",
    "WD_H", "DAY_H", "NOTE_LINE_H", "NOTE_GAP", "FOOTER_H",
)


def _blend(c1: str, c2: str, ratio: float) -> str:
    """两色线性混合：ratio=0 → c1，ratio=1 → c2。用于浅绿/浅蓝胶囊底。"""
    r1, g1, b1 = rgb(c1)
    r2, g2, b2 = rgb(c2)
    f = max(0.0, min(1.0, ratio))
    return "#%02X%02X%02X" % (int(r1 + (r2 - r1) * f),
                              int(g1 + (g2 - g1) * f),
                              int(b1 + (b2 - b1) * f))


def _w(s: str, size: int, role: str = "regular") -> int:
    return _cf.width(s, size, role)


def _rec(boxes, label: str, box) -> None:
    """收集叶子元素包围盒（boxes 为 None 时零开销）。"""
    if boxes is not None:
        boxes.append((label, *box))


def _tb(pos, text: str, size: int, role: str, anchor: str) -> tuple:
    """由 text_size + anchor 推算文本真实包围盒（与绘制测量同源）。"""
    w, h = _cf.size(text, size, role)
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


# ---- 视觉精修门禁（纯度量，只读本模块常量；由 check_checkin_layout.py 调用）----
# 用户 2026-11-10 15:0x「注意每个边框内外边距/字体/文字大小/加粗/颜色」。
# 这两条把「字号层级」与「间距 token」从人工自觉变成机器可拦，防止下次改回
# 19/20/21 挤一档、或 10/14/18 混用。注入反例见门禁 --selftest。
TYPE_SCALE = {
    "title": FS_TITLE, "banner_t": FS_BANNER_T, "pill_val": FS_PILL_VAL,
    "sec": FS_SEC, "banner_m": FS_BANNER_M, "sub": FS_SUB, "rank": FS_RANK,
    "reward": FS_REWARD, "day": FS_DAY, "pill_lab": FS_PILL_LAB,
    "legend": FS_LEGEND, "wd": FS_WD, "note": FS_NOTE, "foot": FS_FOOT,
}


def type_scale_problems() -> list[str]:
    """字号层级契约：标题 > 核心数值 > 分区标题 > 正文 > 弱文字，逐级差≥3px。

    层级（px）：标题 40 / 核心数值·横幅主标题 24 / 分区标题 20 /
    正文 16~17 / 弱文字 14。核心数值须比分区标题大≥4，副标题/名次不得压过正文。
    """
    p: list[str] = []
    if FS_TITLE < 38:
        p.append("[字号] 标题 FS_TITLE=%d < 38（主标题须最大）" % FS_TITLE)
    if FS_BANNER_T != FS_PILL_VAL:
        p.append("[字号] 横幅主标题 %d 与核心数值 %d 应同档" % (FS_BANNER_T, FS_PILL_VAL))
    if FS_PILL_VAL - FS_SEC < 4:
        p.append("[字号] 核心数值 %d 与分区标题 %d 差 <4px（层级不分）"
                 % (FS_PILL_VAL, FS_SEC))
    if FS_SEC - FS_BANNER_M < 3:
        p.append("[字号] 分区标题 %d 与正文 %d 差 <3px" % (FS_SEC, FS_BANNER_M))
    if FS_RANK > FS_BANNER_M:
        p.append("[字号] 名次 %d 压过正文 %d（抢戏）" % (FS_RANK, FS_BANNER_M))
    weak = (FS_LEGEND, FS_WD, FS_NOTE, FS_FOOT)
    if weak != tuple([min(weak)] * len(weak)):
        p.append("[字号] 弱文字层级不齐：%s" % (weak,))
    if FS_BANNER_M - max(weak) < 2:
        p.append("[字号] 正文 %d 与弱文字 %d 差 <2px" % (FS_BANNER_M, max(weak)))
    return p


def spacing_token_problems() -> list[str]:
    """间距 token 契约：所有间距类常量须为 4 的倍数（2/4/8/12/16/24/32）。"""
    return ["[间距] %s=%d 非 4 的倍数 token" % (k, globals()[k])
            for k in SPACING_KEYS if globals()[k] % 4 != 0]
