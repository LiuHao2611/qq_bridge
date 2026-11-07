# -*- coding: utf-8 -*-
"""task_alert —— 任务订阅命中时推送的「单个任务」卡片（2026-11-05）。

card-design 设计前分析（动工前逐项填好，缺一项不渲染）：

* 用途：主信息 = 刚刷出来的那条裂隙任务是什么；次级 = 节点/星球/派系与剩余时间。
* 层级：主标题 30 bold 深蓝 > 副标题 20 > 信息值 18 > 标签 16 > 弱信息 15 灰；
  倒计时用强调橙（唯一大面积强调色，且只在小数块上）。
* 尺寸：固定宽 560；高度**内容驱动**（padding*2 + Σ行高 + gap*(n-1)），不写死。
* 主题：亮色。沿用 fissure 卡同套配色，QQ 图片阅读友好；浅底深字。
* 媒介：Pillow + 项目 Canvas（字体走 pic.fonts），纯中文，不引入 emoji/豆腐框。
* 动态范围：节点名/条件回显可变长 → 超宽 ellipsis 截断、条件按最大宽 wrap 换行后重算高度。
* 列网格：content_left=48 / content_right=512；标签列 x=48、值列 x=128 固定；
  行尾倒计时**右锚定到 content_right**，不随标题长短漂移。
* 间距 token：PAD=24（页面）、PAD_IN=24（卡内）、GAP_S=8、GAP_M=12、GAP_L=16；同层统一。
* 语义色：橙=剩余时间（强调）、紫=纪元、蓝=标题、灰=标签/脚注。

排版入口只有 ``_emit`` / ``_emit_right`` / ``_emit_tokens``：所有文本先经
``Canvas.text_size`` 度量，坐标由 y 游标 + 固定列起点推算，绘制与包围盒登记
共用同一组数值（度量优先，无裸 draw.text、无空格凑间距、无估算宽度）。
``build(boxes=...)`` 把真实绘制包围盒交给 tools/check_task_alert_layout.py 自检。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme

# ---- 画布 / 配色（与 fissure 卡同源，保证系列观感一致）----
W = 560
C_PAGE = "#E9EDF3"
C_CARD = "#FFFFFF"
C_BORDER = "#D3DAE4"
C_TITLE = "#1F4E79"
C_SUB = "#6C3483"
C_TEXT = "#2B3A4A"
C_DIM = "#8A94A6"
C_REMAIN = "#D35400"
C_LINE = "#DCE3EC"

PAD = 24            # 页面外边距
PAD_IN = 24         # 卡片内边距（内容安全区）
RADIUS = 16
CARD_TOP = PAD
CONTENT_LEFT = PAD + PAD_IN          # 48
CONTENT_RIGHT = W - PAD - PAD_IN     # 512
CONTENT_W = CONTENT_RIGHT - CONTENT_LEFT
VALUE_X = CONTENT_LEFT + 80          # 128：标签列宽 80

GAP_S = 8
GAP_M = 12
GAP_L = 16

FS_KICK = 16
FS_TITLE = 30
FS_SUB = 20
FS_REMAIN = 20
FS_REMAIN_LABEL = 15
FS_LABEL = 16
FS_VALUE = 18
FS_FOOT = 15


def _h(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_size(s, size, role)[1]


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _ellipsis(s: str, size: int, role: str, max_w: int) -> str:
    """度量优先截断：逐字删到 text+… 不超宽（不用字符数估宽）。"""
    s = str(s or "")
    if _w(s, size, role) <= max_w:
        return s
    while s and _w(s + "…", size, role) > max_w:
        s = s[:-1]
    return s + "…" if s else ""


def build(t: Theme, task: dict | None = None, sub_id: str = "",
          cond_label: str = "", width: int | None = None, boxes=None,
          **kw) -> Canvas:
    """绘制单任务提醒卡；boxes 非空时登记每个可见元素的真实包围盒供自检。"""
    task = task or {}
    width = W  # 固定宽度（对齐 series；忽略全局 PIC_MAX_WIDTH）
    title = str(task.get("title") or "裂隙任务")
    tier = str(task.get("tier") or "")
    mode = str(task.get("mode") or "")
    node = str(task.get("node") or "—")
    planet = str(task.get("planet") or "—")
    faction = str(task.get("faction") or "—")
    remain = str(task.get("remain") or "—")
    sid = str(sub_id or "").strip()

    # 条件回显按最大宽换行（换行后高度随之重算）
    cond_text = "订阅条件：" + str(cond_label or "不限")
    cond_lines = Canvas.wrap(cond_text, FS_FOOT, "regular", CONTENT_W)
    cancel_text = ("取消订阅：发送「取消订阅 %s」" % sid) if sid else ""
    cancel_lines = Canvas.wrap(cancel_text, FS_FOOT, "regular", CONTENT_W) if cancel_text else []

    # ---- 布局：先测量生成绘制指令，再一次性执行（绘制/登记同源）----
    ops: list[tuple] = []        # (label, x, y_top, text, size, color, role)
    rules: list[int] = []        # 分割线 y
    y = CARD_TOP + PAD_IN

    def emit(label, x, y_top, text, size, color, role="regular") -> int:
        ops.append((label, int(x), int(y_top), text, size, color, role))
        return _h(text, size, role)

    # header：左小标签 + 右订阅 ID（两者都右/左锚定，不用空格凑）
    kick = "任务订阅 · 命中提醒"
    h_kick = emit("kick", CONTENT_LEFT, y, kick, FS_KICK, C_DIM)
    sid_text = "ID " + sid if sid else ""
    if sid_text:
        h_sid = _h(sid_text, FS_KICK, "regular")
        emit("sid", CONTENT_RIGHT - _w(sid_text, FS_KICK, "regular"),
             y + (h_kick - h_sid) // 2, sid_text, FS_KICK, C_DIM)
    y += h_kick + GAP_M

    # 主信息行：标题左、倒计时整组右锚定；标题按剩余空间截断，杜绝重叠
    remain_tokens = [("距结束", FS_REMAIN_LABEL, C_DIM, "regular"),
                     (remain, FS_REMAIN, C_REMAIN, "bold")]
    remain_total = sum(_w(s, sz, r) for s, sz, _, r in remain_tokens) \
        + GAP_S * (len(remain_tokens) - 1)
    remain_x = CONTENT_RIGHT - remain_total
    title_max = remain_x - CONTENT_LEFT - GAP_L
    title = _ellipsis(title, FS_TITLE, "bold", max(40, title_max))
    h_title = _h(title, FS_TITLE, "bold")
    h_remain = max(_h(s, sz, r) for s, sz, _, r in remain_tokens)
    row_h = max(h_title, h_remain)
    emit("title", CONTENT_LEFT, y + (row_h - h_title) // 2,
         title, FS_TITLE, C_TITLE, "bold")
    cx = remain_x
    for i, (s, sz, color, role) in enumerate(remain_tokens):
        hh = _h(s, sz, role)
        emit("remain:%d" % i, cx, y + (row_h - hh) // 2, s, sz, color, role)
        cx += _w(s, sz, role) + GAP_S
    y += row_h + GAP_S

    # 副标题：纪元 / 模式 显式 token 混排（不用空格凑间距）
    sub_tokens = []
    if tier:
        sub_tokens.append((tier, FS_SUB, C_SUB, "bold"))
    if mode:
        if sub_tokens:
            sub_tokens.append(("·", FS_SUB, C_DIM, "regular"))
        sub_tokens.append((mode + "裂隙", FS_SUB, C_TEXT, "regular"))
    if sub_tokens:
        h_sub = max(_h(s, sz, r) for s, sz, _, r in sub_tokens)
        cx = CONTENT_LEFT
        for i, (s, sz, color, role) in enumerate(sub_tokens):
            hh = _h(s, sz, role)
            emit("sub:%d" % i, cx, y + (h_sub - hh) // 2, s, sz, color, role)
            cx += _w(s, sz, role) + GAP_S
        y += h_sub + GAP_L

    rules.append(y)
    y += GAP_L

    # 信息行：固定标签列 / 值列，同列起点全卡不变
    info = [("节点", node), ("星球", planet), ("派系", faction)]
    for i, (label, value) in enumerate(info):
        h_lab = _h(label, FS_LABEL, "regular")
        val = _ellipsis(value, FS_VALUE, "regular", CONTENT_RIGHT - VALUE_X)
        h_val = _h(val, FS_VALUE, "regular")
        rh = max(h_lab, h_val)
        emit("label:%s" % label, CONTENT_LEFT, y + (rh - h_lab) // 2,
             label, FS_LABEL, C_DIM)
        emit("value:%s" % label, VALUE_X, y + (rh - h_val) // 2,
             val, FS_VALUE, C_TEXT)
        y += rh + (GAP_M if i < len(info) - 1 else 0)
    y += GAP_L

    rules.append(y)
    y += GAP_L

    # footer：条件回显 + 取消指令（弱文字，明显弱于正文）
    foot_lines = [(ln, "foot:%d" % i) for i, ln in enumerate(cond_lines)]
    foot_lines += [(ln, "cancel:%d" % i) for i, ln in enumerate(cancel_lines)]
    for i, (ln, label) in enumerate(foot_lines):
        hf = emit(label, CONTENT_LEFT, y, ln, FS_FOOT, C_DIM)
        y += hf + (GAP_S if i < len(foot_lines) - 1 else 0)
    y += PAD_IN
    h = y + PAD

    # ---- 绘制 ----
    c = Canvas(width, h, C_PAGE, theme=t)
    c.rounded_rect((PAD, CARD_TOP, W - PAD, h - PAD), radius=RADIUS,
                   fill=C_CARD, outline=C_BORDER, width=2)
    c.add_region("ov:card_color", [PAD + 6, CARD_TOP + 6, W - PAD - 6, h - PAD - 6],
                 label="卡片底色", tip="点击修改本卡底色")
    c.regions[-1]["values"] = {"ov:card_color": C_CARD, "ov:border_color": C_BORDER}

    for ry in rules:
        c.line((CONTENT_LEFT, ry, CONTENT_RIGHT, ry), fill=C_LINE, width=1)
        if boxes is not None:
            boxes.append(("rule:%d" % ry, CONTENT_LEFT, ry, CONTENT_RIGHT, ry + 1))

    for label, x, y_top, text, size, color, role in ops:
        w, hh = Canvas.text_size(text, size, role)
        c.text((x, y_top + hh // 2), text, size=size, color=color,
               role=role, anchor="lm")
        if boxes is not None:
            boxes.append((label, x, y_top, x + w, y_top + hh))
    return c
