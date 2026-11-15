# -*- coding: utf-8 -*-
"""pic/templates/doupo_roster.py —— 斗破·本宗斗技名录卡（两列版 / 2026-11-15）。

为什么又开一张模板
------------------
用户第一次看到当时的 23 门名录卡说「全部斗技的展示做的太简陋了」；我把多维数据补进
通用列表卡 ``doupo_list`` 后，用户第二次仍说「优化这个卡片，怎么没优化」——
因为**加信息不等于做设计**。于是另开专用模板：

  * 「上阵 / 可学」原本和技能名一样 24px 粗体，状态抢走第一眼注意力；
  * 23 行无分组无分隔，从上到下糊成一张表格；
  * 效果与代价挤成一行 30+ 字（``·`` / ``｜`` 硬连），分不清主次；
  * 普通/高阶/特殊只靠一个字的小方块区分。

用上专用模板后用户第三次要求：「技能描述加上，别偷懒，做成两列」——
名录卡不能只放 ``brief`` 一句，要展示**完整技能描述**；但单列加完整描述会撑到
6000px 以上，所以改**两列网格**。

版式（两列网格）
----------------
  * 顶部：bolt 图标 + 「本宗斗技」(38px) + 贯通分割线 + 一行统计；
  * 按阶位分三区（普通 / 高阶 / 特殊），分区标题带图标 + 右侧贯通细线；
  * 分区内**两列成对**排布，同一行两块等高（``max(h_left, h_right)``），
    左右列起点固定 → 不会因一侧描述长而错位；
  * 每门一个圆角卡片块（宽 = 列宽），左缘 4px 阶位色条；
  * 块内六层：
        血煞掌                    〔异常〕[● 上阵]  ← 名字 22 粗 / 定位胶囊 / 实底状态徽章
        〔本宗〕玄阶高级 · 攻击 · 斗气 20%           ← 副行 13
        天冥宗易尘所使，斗气凝血成掌，掌色暗红近黑   ← **完整描述 15px 折行**（首句带来源状语，禁写原著章节号）
        随掌力渗入，伤口不流血反而发黑。造成 140%
        伤害，并附加血毒（每回合 5%，持续 2 回合）。
        [伤害 ×1.40] [血毒 5%·2回合] [减疗 30%]     ← 效果 chip 序列
        限：气血虚时威力骤降，对无血之物无效        ← 代价行，更弱
  * 底部：分割线 + 用法提示。

为什么宽度仍是 720、字号只有 13–15
----------------------------------
QQ 聊天窗显示宽度固定，卡片越宽被压得越狠（1080 宽缩到 720 显示 ≈ ×0.67，
16px 视觉只剩 10.7px）。**同样的信息量，窄卡大字比宽卡小字更可读**，
所以坚持 720 两列，用字号换列数，而不是加宽卡片。

设计约束（card-design 红线）
--------------------------
  * **单一布局入口** ``_plan()``：卡片高、分区、每行成对、每块等高、折行
    全部由它产出，``build`` 与 ``_measure`` 不各自算（否则底部裁切/留白）；
  * **度量优先**：``_w`` 实测、``_fit`` 截断、``Canvas.wrap`` 折行；
    chip 逐个测宽后按显式 ``CHIP_GAP`` 打包，**禁止空格凑间距**；
  * **列对齐**：两列左起固定，名字恒从列内左缘起（状态 pill 右锚定，
    绝不放在名字左侧——否则「上阵」与「未解锁」宽度不同会让名字起点漂移）；
  * **不抢层级，但一眼可见**（2026-11-15 用户「右上角提示要更明显更显眼」）：
    状态徽章 14px 浅字 + 高对比实底 + 状态圆点（24px 高），定位胶囊仍是 13px 淡色；
    两者都不与 22px 粗体技能名同字号，但状态靠实底对比度把扫视优先级拉到定位之上；
    旧版三态都用 ``cell_bg`` 淡底 + 12px，等于把「上阵」藏在米色背景里；
  * ``build(..., boxes=[...])`` 收集全部文本包围盒，供 ``check_boxes`` 自检；
  * 纯 Pillow 自绘；不使用 emoji；配色读 ``Theme.bg_overrides["doupo"]``。

输入（build 的 data，JSON 友好）
--------------------------------
  icon/title/subtitle/tip
  groups: [{
    "tier": "普通", "count": 12, "icon": "sword",
    "rows": [{
      "name", "role", "state", "state_kind",   # state_kind: on/learn/lock
      "meta", "desc",
      "chips": [{"label","value"}],
      "gate": "…"
    }]
  }]
  rows: [...]     # 兼容扁平输入；无 groups 时按单组渲染
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

# ---- 排版（4 的倍数间距 token；两列 720 宽，字号主动收小换列数）----
PAD = 28
COL_GAP = 12
FS_TITLE = 38
FS_SUB = 17
FS_SECTION = 17
FS_NAME = 22              # 技能名（>=22 才走真粗体）
FS_ROLE = 13              # 定位胶囊
FS_STATE = 14             # 状态徽章（12→14：右上角提示要更明显）
FS_META = 13              # 副行
FS_DESC = 15              # 完整技能描述
FS_CHIP = 13              # 效果 chip
FS_GATE = 13              # 代价行
FS_TIP = 15

ICON = 20
ICON_STYLE = "line"

SECTION_H = FS_SECTION + 12
SECTION_GAP = 12
BLOCK_GAP = 8
BLOCK_GAP_COL = 12
BLOCK_PAD_X = 12
BLOCK_PAD_Y = 11
BLOCK_RADIUS = 10
BAR_W = 4

H_NAME = FS_NAME + 4      # 26
GAP_NAME_META = 4
H_META = FS_META + 8      # 21（折成两行时 13px 字号的段间 gap = 8，不挤）
GAP_META_DESC = 4
DESC_LH = FS_DESC + 5     # 20
GAP_DESC_CHIP = 6
H_CHIP = 22
CHIP_GAP = 6
CHIP_PADX = 8
GAP_CHIP_GATE = 4
GATE_LH = FS_GATE + 5     # 18
ROLE_H = 20
ROLE_PADX = 8
STATE_H = 24              # 18→24：实底徽章够高才压得住浅色羊皮纸底
STATE_PADX = 10
STATE_DOT_R = 4           # 状态圆点半径（非 emoji，Pillow 实心圆）
STATE_DOT_GAP = 6         # 圆点与文字的显式 gap，禁止空格凑间距
H_TIP = 12 + FS_TIP + 8

#: 阶位 → 左缘色条 / 分区图标（普通沉稳、高阶暗金、特殊朱红）
TIER_STYLE = {
    "普通": ("chip_line", "sword"),
    "高阶": ("accent_color", "bolt"),
    "特殊": ("title_color", "star"),
}
#: 主定位 → (底色, 描边, 文字)
ROLE_COLORS = {
    "续航": ("pill_bg", "pill_line", "pill_text"),
    "异常": ("pill_bg", "pill_line", "pill_text"),
    "防御": ("pill_bg", "pill_line", "pill_text"),
    "位移": ("pill_bg", "pill_line", "pill_text"),
    "控场": ("chip_bg", "chip_line", "chip_text"),
    "爆发": ("chip_bg", "chip_line", "chip_text"),
    "输出": ("chip_bg", "chip_line", "chip_text"),
    "功能": ("cell_bg", "cell_line", "dim_color"),
}
#: 状态徽章三态 → (实底, 描边, 文字/圆点)。
#: 2026-11-15 用户「右上角提示更明显更显眼」：旧版 learn/lock 共用 ``cell_bg``
#: 淡底 + 12px 弱文字，在米色块上几乎看不见。改成三个不同色相的深色实底 +
#: 浅字 + 左端状态圆点；**不随主题淡色漂移**（主题可被 bg_overrides 改，
#: 但状态徽章必须始终一眼可见）。上阵朱红 / 可学墨绿 / 未解锁灰褐。
STATE_COLORS = {
    "on": ("#8A1C1C", "#5E1010", "#FFF4E4"),
    "learn": ("#2F6B4F", "#1D4634", "#F1FFF6"),
    "lock": ("#6B5D4F", "#463B31", "#F7EFE6"),
}


def _luminance(hex_color: str) -> float:
    """sRGB 亮度（0 黑 / 255 白），用于判定状态徽章底色够不够深。"""
    s = str(hex_color).lstrip("#")
    try:
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 255.0
    return 0.299 * r + 0.587 * g + 0.114 * b


def state_badge_problems(style: dict | None = None, fs: int | None = None,
                         h: int | None = None) -> list[str]:
    """状态徽章醒目度契约（纯函数，参数可注入反例）。

    由 ``tools/check_skill_multidim.py`` 与 ``test_roster_card.py`` 共同消费：
    只要有人把徽章改回「12px + 淡底 + 三态同色」，交付闸门必须报错。
    """
    style = STATE_COLORS if style is None else style
    fs = FS_STATE if fs is None else fs
    h = STATE_H if h is None else h
    out: list[str] = []
    if int(fs) < 14:
        out.append("[状态徽章] 字号 %d < 14（退回看不清的 12px 小胶囊）" % int(fs))
    if int(h) < 22:
        out.append("[状态徽章] 高度 %d < 22（徽章太矮压不住浅底）" % int(h))
    keys = ("on", "learn", "lock")
    fills: list[str] = []
    for k in keys:
        if k not in style:
            out.append("[状态徽章] 缺少 %s 态配色" % k)
            continue
        v = style[k]
        if not (isinstance(v, (tuple, list)) and len(v) == 3):
            out.append("[状态徽章] %s 配色必须是 (实底, 描边, 文字)" % k)
            continue
        fill = str(v[0]).lower()
        fills.append(fill)
        if _luminance(fill) > 170:
            out.append("[状态徽章] %s 底色 %s 太浅（淡底会和羊皮纸糊在一起）"
                       % (k, fill))
    if len(set(fills)) < len(keys):
        out.append("[状态徽章] 三态实底必须互不相同（旧版可学/未解锁同色）")
    return out

#: 编辑器预览用示例（pic 包不 import 插件层）
_DEMO = {
    "icon": "bolt", "title": "本宗斗技",
    "subtitle": "天冥宗 · 共 3 门 · 上阵 1/4",
    "groups": [
        {"tier": "普通", "count": 2, "icon": "sword", "rows": [
            {"name": "血煞掌", "role": "异常", "state": "上阵", "state_kind": "on",
             "meta": "〔本宗〕玄阶高级 · 攻击 · 斗气 20%",
             "desc": "斗气凝血成掌，掌色暗红近黑；一掌拍出，血毒随掌力渗入，伤口不流血反而发黑。造成 140% 伤害，并附加血毒（每回合 5%，持续 2 回合，且治疗 -30%）。",
             "chips": [{"label": "伤害", "value": "×1.40"},
                       {"label": "血毒", "value": "5%·2回合"},
                       {"label": "减疗", "value": "30%·2回合"}],
             "gate": "气血虚时威力骤降，对无血之物无效"},
            {"name": "碎石掌", "role": "功能", "state": "可学", "state_kind": "learn",
             "meta": "〔通用〕黄阶低级 · 攻击 · 斗气 20%",
             "desc": "斗气只往掌心一聚，不做花哨架势，抬手就拍。掌力沉而短，专砸护体斗气最薄的一点。造成 95% 伤害，20% 概率击破护体，令目标防御归零 1 回合。",
             "chips": [{"label": "伤害", "value": "×0.95"},
                       {"label": "破防", "value": "100%·1回合"}],
             "gate": "威力天花板低"},
        ]},
        {"tier": "高阶", "count": 1, "icon": "bolt", "rows": [
            {"name": "九幽冥手", "role": "爆发", "state": "未解锁", "state_kind": "lock",
             "meta": "〔本宗〕天阶低级 · 攻击 · 斗气 34% · lv50 解锁",
             "desc": "斗气凝成一只漆黑大手，手指出处空间直接塌陷；大手不需飞行，而是穿过空间出现在目标面前，一把抓下。造成 200% 伤害，并无视目标 30% 防御。",
             "chips": [{"label": "伤害", "value": "×2.00"},
                       {"label": "破防", "value": "30%·1回合"}],
             "gate": "隐身/龟息目标抓空，自受空间割伤"},
        ]},
    ],
    "tip": "用法：斗技 全部 ｜ 斗技详情 <技名> 看原著出处",
}


def _groups_of(data: dict) -> list[dict]:
    """统一取分组：优先 groups，否则把扁平 rows 包成单组（兼容旧输入）。"""
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)]
    if groups:
        return [g for g in groups if (g.get("rows") or [])]
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    return [{"tier": "", "count": len(rows), "icon": "sword", "rows": rows}] if rows else []


# ---------------------------------------------------------------------------
# 盒模型：度量函数与绘制共用
# ---------------------------------------------------------------------------
def _chip_w(chip: dict) -> int:
    label = str(chip.get("label") or "")
    value = str(chip.get("value") or "")
    w = _w(label, FS_CHIP, "regular")
    if value:
        w += 5 + _w(value, FS_CHIP, "regular")
    return int(w) + CHIP_PADX * 2


def _chip_rows(chips, max_w: int) -> list[list[dict]]:
    rows: list[list[dict]] = []
    cur: list[dict] = []
    cur_w = 0
    for ch in chips or []:
        cw = _chip_w(ch)
        used = cur_w + (CHIP_GAP if cur else 0) + cw
        if cur and used > max_w:
            rows.append(cur)
            cur, cur_w = [ch], cw
        else:
            cur.append(ch)
            cur_w = used
    if cur:
        rows.append(cur)
    return rows


def _gate_lines(gate: str, max_w: int) -> list[str]:
    if not gate:
        return []
    # 「限：」用中文冒号收束，绝不写成「限 内容」用空格凑间距（card-design 红线 5）
    return Canvas.wrap("限：" + gate, FS_GATE, "regular", max(60, max_w))


def _desc_lines(row: dict, max_w: int) -> list[str]:
    """完整技能描述折行（两列窄，按列内宽逐字折 + 中文避头，不估算）。"""
    desc = str(row.get("desc") or "")
    if not desc:
        return []
    return S.wrap_cjk(desc, FS_DESC, "regular", max(60, max_w))


def _meta_lines(row: dict, inner_w: int) -> list[str]:
    """副行按「 · 」语义段折行，不逐字拆。

    逐字 ``Canvas.wrap`` 会把「lv20 解锁」拆成「lv2」+「0 解锁」（肉眼核对
    抓到）；按段打包则整段移到下一行，解锁等级完整可读。段宽与拼好后的
    行宽都先实测，单段自身超宽才退回逐字折（极少见）。
    """
    meta = str(row.get("meta") or "")
    if not meta:
        return [""]
    parts = meta.split(" · ")
    lines: list[str] = []
    cur = ""
    for part in parts:
        cand = (cur + " · " + part) if cur else part
        if cur and _w(cand, FS_META, "regular") > inner_w:
            lines.append(cur)
            cur = part
        else:
            cur = cand
    if cur:
        lines.append(cur)
    out: list[str] = []
    for ln in lines:
        if _w(ln, FS_META, "regular") <= inner_w:
            out.append(ln)
        else:
            out.extend(Canvas.wrap(ln, FS_META, "regular", max(60, inner_w)))
    return out or [""]


def _row_texts(row: dict, inner_w: int) -> dict:
    return {
        # 2026-11-15 肉眼核对：低等级玩家副行带「lvN 解锁」，旧版 _fit 会把它
        # 截成「lv…」，解锁等级凭空丢失。改为按语义段折行（信息不丢、不拆字）。
        "meta_lines": _meta_lines(row, inner_w),
        "desc_lines": _desc_lines(row, inner_w),
        "chip_rows": _chip_rows(row.get("chips"), inner_w),
        "gate_lines": _gate_lines(str(row.get("gate") or ""), inner_w),
    }


def _row_h(row: dict, inner_w: int) -> int:
    """单块高度 —— 内容驱动：副行 / 描述 / chip / 代价行数都参与累加。"""
    t = _row_texts(row, inner_w)
    h = (BLOCK_PAD_Y * 2 + H_NAME + GAP_NAME_META
         + H_META * max(1, len(t["meta_lines"])))
    n_desc = len(t["desc_lines"])
    if n_desc:
        h += GAP_META_DESC + n_desc * DESC_LH
    n_chip = len(t["chip_rows"])
    if n_chip:
        h += GAP_DESC_CHIP + n_chip * H_CHIP + (n_chip - 1) * CHIP_GAP
    n_gate = len(t["gate_lines"])
    if n_gate:
        h += GAP_CHIP_GATE + n_gate * GATE_LH
    return h


def _state_w(row: dict) -> int:
    """徽章宽 = 左内边距 + 圆点 + gap + 实测文字宽 + 右内边距。"""
    txt = str(row.get("state") or "")
    if not txt:
        return 0
    return (_w(txt, FS_STATE, "regular") + STATE_PADX * 2
            + STATE_DOT_R * 2 + STATE_DOT_GAP)


def _role_w(row: dict) -> int:
    role = str(row.get("role") or "")
    if not role:
        return 0
    return _w(role, FS_ROLE, "regular") + ROLE_PADX * 2


# ---------------------------------------------------------------------------
# 单一布局入口：高度 / 分区 / 两列成对 / 每块 y 全部从这里出
# ---------------------------------------------------------------------------
def _cols(width: int):
    """(content_w, col_w, inner_w, block_x0, block_x1) —— 两列固定起点。"""
    content_w = width - PAD * 2
    col_w = (content_w - BLOCK_GAP_COL) // 2
    inner_w = col_w - BLOCK_PAD_X * 2 - BAR_W - 4
    return content_w, col_w, inner_w, PAD, PAD + col_w + BLOCK_GAP_COL


def _plan(data: dict, width: int) -> dict:
    width = max(560, min(int(width), 900))
    content_w, col_w, inner_w, bx0, bx1 = _cols(width)

    y = PAD + FS_TITLE + 8 + 1 + 18          # 标题 + 分割线
    if data.get("subtitle"):
        y += FS_SUB + 10

    entries: list[dict] = []
    for gi, g in enumerate(_groups_of(data)):
        y += SECTION_GAP if gi else 0
        tier = str(g.get("tier") or "")
        entries.append({"kind": "section", "y": y, "h": SECTION_H,
                        "group": g, "tier": tier})
        y += SECTION_H
        rows = [r for r in (g.get("rows") or []) if isinstance(r, dict)]
        for i in range(0, len(rows), 2):
            pair = rows[i:i + 2]
            hs = [_row_h(r, inner_w) for r in pair]
            rh = max(hs) if hs else 0
            cells = [{"row": r, "x": (bx0 if k == 0 else bx1),
                      "w": col_w, "h": hs[k], "tier": tier}
                     for k, r in enumerate(pair)]
            entries.append({"kind": "pair", "y": y, "h": rh, "cells": cells})
            y += rh + BLOCK_GAP
        y -= BLOCK_GAP

    y += H_TIP
    return {"width": width, "content_w": content_w, "col_w": col_w,
            "inner_w": inner_w, "entries": entries, "h": max(420, y + PAD)}


def _measure(data: dict, width: int) -> int:
    return _plan(data, width)["h"]


# ---------------------------------------------------------------------------
def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    p = _pal(t)
    L = _plan(data, width)
    content_w, inner_w, h = L["content_w"], L["inner_w"], L["h"]
    boxes = kw.pop("boxes", None)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    def rec(label: str, txt: str, x: int, y: int, size: int, role: str,
            anchor: str) -> None:
        if boxes is None:
            return
        w = _w(txt, size, role)
        hh = int(size)
        if anchor == "lm":
            bb = (x, y - hh // 2, x + w, y + hh // 2)
        elif anchor == "rm":
            bb = (x - w, y - hh // 2, x, y + hh // 2)
        elif anchor == "mm":
            bb = (x - w // 2, y - hh // 2, x + w // 2, y + hh // 2)
        else:
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

    def ktext(label, x, y, txt, size, color, anchor="lm", max_w=None):
        bold = int(size) >= S.MIN_BOLD_FS
        role = "bold" if bold else "regular"
        s = _fit(str(txt), max_w, size, role) if max_w is not None else str(txt)
        c.text((x, y), s, size=int(size), color=color, role=role,
               bold=bold, anchor=anchor)
        rec(label, s, x, y, size, role, anchor)
        return s

    def stext(label, x, y, txt, size, color, anchor="lm", max_w=None):
        s = _fit(str(txt), max_w, size, "regular") if max_w is not None else str(txt)
        c.text((x, y), s, size=int(size), color=color, role="regular",
               bold=False, anchor=anchor)
        rec(label, s, x, y, size, "regular", anchor)
        return s

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    icon = str(data.get("icon") or "bolt")
    if not icons.exists(icon):
        icon = "sword"
    title = str(data.get("title") or "本宗斗技")
    tw = _w(title, FS_TITLE, "light")
    ico_w = FS_TITLE + 10
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, icon, tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE,
               p["title_color"], style=ICON_STYLE)
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    rec("title", title, tx + ico_w, y + FS_TITLE // 2, FS_TITLE, "light", "lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    sub = data.get("subtitle")
    if sub:
        stext("subtitle", x0, y + FS_SUB // 2, sub, FS_SUB, p["dim_color"],
              max_w=content_w)
        y += FS_SUB + 10

    # ---- 分区 + 两列斗技块 ----
    for ent in L["entries"]:
        if ent["kind"] == "section":
            g = ent["group"]
            tier = ent["tier"]
            sicon = str(g.get("icon") or TIER_STYLE.get(tier, ("", "sword"))[1])
            if not icons.exists(sicon):
                sicon = "sword"
            sy = ent["y"]
            icons.draw(c, sicon, x0 + ICON // 2, sy + FS_SECTION // 2, ICON,
                       p["accent_color"], style=ICON_STYLE)
            label = ("%s斗技" % tier) if tier else "斗技"
            cnt = int(g.get("count") if g.get("count") is not None
                      else len(g.get("rows") or []))
            sec_txt = "%s · %d 门" % (label, cnt)
            stext("section_%s" % (tier or "all"), x0 + ICON + 10,
                  sy + FS_SECTION // 2, sec_txt, FS_SECTION, p["text_color"])
            lx = x0 + ICON + 10 + _w(sec_txt, FS_SECTION, "regular") + 12
            if lx < x0 + content_w:
                c.line((lx, sy + FS_SECTION // 2, x0 + content_w,
                        sy + FS_SECTION // 2), fill=p["line_color"], width=1)
            continue

        for cell in ent["cells"]:
            row, bx, bw, rh = cell["row"], cell["x"], cell["w"], ent["h"]
            tier = cell["tier"]
            name = str(row.get("name") or "斗技")
            by = ent["y"]

            # 块背景 + 左缘阶位色条
            c.rounded_rect((bx, by, bx + bw, by + rh), radius=BLOCK_RADIUS,
                           fill=p["cell_bg"], outline=p["cell_line"], width=1)
            bar_key = TIER_STYLE.get(tier, ("chip_line", "sword"))[0]
            c.rounded_rect((bx + 1, by + 6, bx + 1 + BAR_W, by + rh - 6), radius=2,
                           fill=p[bar_key])

            ix = bx + BLOCK_PAD_X + BAR_W + 4
            iy = by + BLOCK_PAD_Y
            right = bx + bw - BLOCK_PAD_X

            # ---- 名字行：技能名（左起固定）+ 定位胶囊 + 状态 pill（均右锚定）----
            # 状态 pill 必须在右侧：若放左侧，「上阵」与「未解锁」宽度不同会让
            # 同列技能名起点逐行漂移（card-design 红线 4 对齐漂移）。
            name_y = iy + H_NAME // 2
            st_w = _state_w(row)
            role_w = _role_w(row)
            tail = st_w + (CHIP_GAP + role_w if role_w else 0)
            name_max = (right - tail - 6) - ix
            ktext("%s.name" % name, ix, name_y, name, FS_NAME, p["text_color"],
                  max_w=max(50, name_max))
            if st_w:
                state = str(row.get("state") or "")
                kind = str(row.get("state_kind") or "learn")
                fill, ring, ink = STATE_COLORS.get(kind, STATE_COLORS["learn"])
                sx = right - st_w
                c.rounded_rect((sx, name_y - STATE_H // 2, right,
                                name_y + STATE_H // 2), radius=STATE_H // 2,
                               fill=fill, outline=ring, width=1)
                # 状态圆点：左内边距起，直径 8，与文字之间留显式 6px gap
                dx = sx + STATE_PADX
                c.ellipse((dx, name_y - STATE_DOT_R,
                           dx + STATE_DOT_R * 2, name_y + STATE_DOT_R), fill=ink)
                # 文字右锚定到徽章右内缘：圆点右缘与文字左缘恰好差 STATE_DOT_GAP
                c.text((right - STATE_PADX, name_y), state, size=FS_STATE,
                       color=ink, role="regular", bold=False, anchor="rm")
                rec("%s.state" % name, state, right - STATE_PADX, name_y,
                    FS_STATE, "regular", "rm")
            if role_w:
                rx = right - st_w - (CHIP_GAP if st_w else 0) - role_w
                role = str(row.get("role") or "")
                bg_k, ln_k, tx_k = ROLE_COLORS.get(
                    role, ("cell_bg", "cell_line", "dim_color"))
                c.rounded_rect((rx, name_y - ROLE_H // 2, rx + role_w,
                                name_y + ROLE_H // 2), radius=ROLE_H // 2,
                               fill=p[bg_k], outline=p[ln_k], width=1)
                c.text((rx + role_w // 2, name_y), role, size=FS_ROLE,
                       color=p[tx_k], role="regular", bold=False, anchor="mm")
                rec("%s.role" % name, role, rx + role_w // 2, name_y,
                    FS_ROLE, "regular", "mm")

            t_ = _row_texts(row, inner_w)

            # ---- 副行（来源 + 阶位 · 类型 · 斗气 · 解锁等级），按实测宽度折行 ----
            cy = iy + H_NAME + GAP_NAME_META
            meta_lines = t_["meta_lines"] or [""]
            for mi, ml in enumerate(meta_lines):
                stext("%s.meta%d" % (name, mi), ix,
                      cy + mi * H_META + H_META // 2, ml, FS_META, p["dim_color"])
            cy += H_META * len(meta_lines)

            # ---- 完整技能描述（用户明确要求：不许只放 brief 一句）----
            if t_["desc_lines"]:
                cy += GAP_META_DESC
                for di, ln in enumerate(t_["desc_lines"]):
                    ly = cy + di * DESC_LH + DESC_LH // 2
                    c.text((ix, ly), ln, size=FS_DESC, color=p["text_color"],
                           role="regular", bold=False, anchor="lm")
                    rec("%s.desc%d" % (name, di), ln, ix, ly, FS_DESC, "regular", "lm")
                cy += len(t_["desc_lines"]) * DESC_LH

            # ---- 效果 chip（每个维度一枚胶囊，超宽自动折行）----
            if t_["chip_rows"]:
                cy += GAP_DESC_CHIP
                for ri, chips in enumerate(t_["chip_rows"]):
                    cx = ix
                    for ch in chips:
                        label = str(ch.get("label") or "")
                        value = str(ch.get("value") or "")
                        cw = _chip_w(ch)
                        c.rounded_rect((cx, cy, cx + cw, cy + H_CHIP),
                                       radius=H_CHIP // 2, fill=p["chip_bg"],
                                       outline=p["chip_line"], width=1)
                        lw = _w(label, FS_CHIP, "regular")
                        vw = _w(value, FS_CHIP, "regular") if value else 0
                        seg = lw + (5 + vw if value else 0)
                        sx = cx + (cw - seg) // 2
                        c.text((sx, cy + H_CHIP // 2), label, size=FS_CHIP,
                               color=p["chip_text"], role="regular", bold=False,
                               anchor="lm")
                        rec("%s.c%d.l" % (name, ri), label, sx, cy + H_CHIP // 2,
                            FS_CHIP, "regular", "lm")
                        if value:
                            vx = sx + lw + 5
                            c.text((vx, cy + H_CHIP // 2), value, size=FS_CHIP,
                                   color=p["title_color"], role="regular",
                                   bold=False, anchor="lm")
                            rec("%s.c%d.v" % (name, ri), value, vx,
                                cy + H_CHIP // 2, FS_CHIP, "regular", "lm")
                        cx += cw + CHIP_GAP
                    cy += H_CHIP + CHIP_GAP
                cy -= CHIP_GAP

            # ---- 代价行（更弱，单独成行，不与效果连读）----
            if t_["gate_lines"]:
                cy += GAP_CHIP_GATE
                for gi2, gl in enumerate(t_["gate_lines"]):
                    gy = cy + gi2 * GATE_LH + GATE_LH // 2
                    stext("%s.g%d" % (name, gi2), ix, gy, gl, FS_GATE,
                          p["dim_color"], max_w=inner_w)

    # ---- 底部提示 ----
    ty = L["h"] - PAD - FS_TIP - 8
    c.line((x0, ty, x0 + content_w, ty), fill=p["line_color"], width=1)
    tip = data.get("tip")
    if tip:
        stext("tip", x0, ty + 12 + FS_TIP // 2, str(tip), FS_TIP, p["dim_color"],
              max_w=content_w)

    if source:
        stext("source", width - PAD, h - PAD + 6, str(source), 14,
              p["dim_color"], anchor="rb")
    return c
