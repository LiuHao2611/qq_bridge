# -*- coding: utf-8 -*-
"""pic/templates/doupo_roster.py —— 斗破·本宗斗技名录卡（2026-11-15 视觉重做）。

为什么又开一张模板
------------------
用户第一次看到 23 门名录卡时说「全部斗技的展示做的太简陋了」；我先把多维数据
补进了 ``doupo_list`` 的行里，用户第二次仍说「优化这个卡片，怎么没优化」——
因为**加信息不等于做设计**。旧版（复用通用列表卡）的实际观感是：

  * 「上阵 / 可学」用了和技能名一样的 24px 粗体暗金 → 状态抢走第一眼注意力；
  * 23 行没有任何分组与分隔，从上到下糊成一张表格，找不到行边界；
  * 效果与代价挤成一行 30+ 字（``血毒5%·2回合 · 减疗30%·2回合 ｜ 限：…``），
    用 ``·`` / ``｜`` 硬连，读起来累且分不清主次；
  * 进度条贯穿整行 12px 高，是视觉上最重的一块，却读不出等级数字；
  * 普通/高阶/特殊只靠一个字的小方块区分，没有真正的阶位层级。

因此另开专用模板，``doupo_list`` 保持原样给异火/宝库/背包/排行复用（无创原则）。

版式
----
  * 顶部：bolt 图标 + 「本宗斗技」(38px) + 贯通分割线 + 一行统计（宗门 / 门数 / 上阵数）；
  * 按阶位分三个分区：``普通斗技 · 12 门`` / ``高阶斗技 · 10 门`` / ``特殊斗技 · 1 门``，
    分区标题带图标 + 右侧贯通细线，读者能快速定位；
  * 每门一个**独立圆角块**（``cell_bg`` + ``cell_line``），左缘 4px 阶位色条：
      ┌────────────────────────────────────────────┐
      │ ▍ 血煞掌   〔异常〕                  [上阵] │  ← 名字 24 粗 / 定位胶囊 / 状态 pill
      │   玄阶高级 · 攻击 · 斗气 20%                │  ← 弱信息 15
      │   [伤害 ×1.40] [血毒 5%·2回合] [减疗 30%]  │  ← 效果 chip 胶囊，自动折行
      │   限 气血虚时威力骤降，对无血之物无效        │  ← 代价行，更弱
      └────────────────────────────────────────────┘
  * 底部：分割线 + 用法提示。

设计约束（card-design 红线）
--------------------------
  * **单一布局入口** ``_plan()``：高度、每个块的 y、每行 y 全部由它产出，
    ``build`` 与 ``_measure`` 不各自算一遍（否则底部会裁/留白）；
  * **度量优先**：所有宽度 ``_w`` 实测、超宽 ``_fit`` 截断、段落 ``Canvas.wrap``；
    chip 逐个测宽后按显式 ``CHIP_GAP`` 打包换行，**禁止空格凑间距**；
  * **对齐**：状态 pill 右锚定到块右边，定位胶囊紧随技能名，阶位色条恒贴块左缘；
  * **不抢层级**：状态 pill 14px 常规字，绝不与 24px 粗体技能名同字号；
  * ``build(..., boxes=[...])`` 收集全部文本包围盒，供 ``check_boxes`` 自检；
  * 纯 Pillow 自绘；不使用 emoji；配色读 ``Theme.bg_overrides["doupo"]``。

输入（build 的 data，JSON 友好）
--------------------------------
  icon/title/subtitle/tip
  groups: [{
    "tier": "普通", "count": 12, "icon": "sword",
    "rows": [{
      "name", "badge", "role", "state", "state_kind",   # state_kind: on/learn/lock
      "meta",        # 「玄阶高级 · 攻击 · 斗气 20%」
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

# ---- 排版（4 的倍数间距 token）----
PAD = 28
FS_TITLE = 38
FS_SUB = 18
FS_SECTION = 17           # 分区标题
FS_NAME = 24              # 技能名（重点，真粗体）
FS_META = 15              # 副行（阶位 · 类型 · 斗气）
FS_CHIP = 15              # 效果 chip
FS_GATE = 14              # 代价行（更弱）
FS_STATE = 14             # 状态 pill
FS_TIP = 16

ICON = 22
ICON_STYLE = "line"

SECTION_H = FS_SECTION + 12
SECTION_GAP = 12
BLOCK_GAP = 8             # 块与块之间
BLOCK_PAD_X = 14
BLOCK_PAD_Y = 10
BLOCK_RADIUS = 12
BAR_W = 4                 # 左侧阶位色条宽

H_NAME = FS_NAME + 4      # 28
GAP_NAME_META = 6
H_META = FS_META + 5      # 20
GAP_META_CHIP = 6
H_CHIP = 26
CHIP_GAP = 8              # chip 之间（水平/垂直同值）
CHIP_PADX = 9
GAP_CHIP_GATE = 6
GATE_LH = FS_GATE + 5     # 19
ROLE_H = 24
ROLE_PADX = 10
STATE_H = 22
STATE_PADX = 10
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
#: 状态 pill → (底色, 描边, 文字)。上阵=暗金强调，可学=中性，未解锁=最弱。
STATE_STYLE = {
    "on": ("chip_bg", "accent_color", "accent_color"),
    "learn": ("cell_bg", "cell_line", "dim_color"),
    "lock": ("cell_bg", "cell_line", "dim_color"),
}

#: 编辑器预览用示例（pic 包不 import 插件层）
_DEMO = {
    "icon": "bolt", "title": "本宗斗技",
    "subtitle": "天冥宗 · 共 3 门 · 上阵 1/4",
    "groups": [
        {"tier": "普通", "count": 2, "icon": "sword", "rows": [
            {"name": "血煞掌", "badge": "普", "role": "异常", "state": "上阵",
             "state_kind": "on", "meta": "玄阶高级 · 攻击 · 斗气 20%",
             "chips": [{"label": "伤害", "value": "×1.40"},
                       {"label": "血毒", "value": "5%·2回合"},
                       {"label": "减疗", "value": "30%·2回合"}],
             "gate": "气血虚时威力骤降，对无血之物无效"},
            {"name": "碎石掌", "badge": "普", "role": "功能", "state": "可学",
             "state_kind": "learn", "meta": "黄阶低级 · 攻击 · 斗气 20%",
             "chips": [{"label": "伤害", "value": "×0.95"},
                       {"label": "破防", "value": "100%·1回合"}],
             "gate": "威力天花板低"},
        ]},
        {"tier": "高阶", "count": 1, "icon": "bolt", "rows": [
            {"name": "九幽冥手", "badge": "高", "role": "爆发", "state": "未解锁 lv50",
             "state_kind": "lock", "meta": "天阶低级 · 攻击 · 斗气 34%",
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
# 盒模型：每个元素一个度量函数，绘制与自检共用
# ---------------------------------------------------------------------------
def _chip_w(chip: dict) -> int:
    label = str(chip.get("label") or "")
    value = str(chip.get("value") or "")
    w = _w(label, FS_CHIP, "regular")
    if value:
        w += 6 + _w(value, FS_CHIP, "regular")
    return int(w) + CHIP_PADX * 2


def _chip_rows(chips, max_w: int) -> list[list[dict]]:
    """按可用宽度逐行打包（显式 CHIP_GAP；不估算、不用空格）。"""
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
    return Canvas.wrap("限：" + gate, FS_GATE, "regular", max(80, max_w))


def _row_texts(row: dict, inner_w: int) -> dict:
    """一门的全部文本布局（chip 行 / 门控行按宽度折行）。"""
    return {
        "chip_rows": _chip_rows(row.get("chips"), inner_w),
        "gate_lines": _gate_lines(str(row.get("gate") or ""), inner_w),
    }


def _row_h(row: dict, inner_w: int) -> int:
    """单块高度 —— 内容驱动，长门控/多效果不会裁切。"""
    t = _row_texts(row, inner_w)
    h = BLOCK_PAD_Y * 2 + H_NAME + GAP_NAME_META + H_META
    n_chip = len(t["chip_rows"])
    if n_chip:
        h += GAP_META_CHIP + n_chip * H_CHIP + (n_chip - 1) * CHIP_GAP
    n_gate = len(t["gate_lines"])
    if n_gate:
        h += GAP_CHIP_GATE + n_gate * GATE_LH
    return h


def _state_w(row: dict) -> int:
    txt = str(row.get("state") or "")
    if not txt:
        return 0
    return _w(txt, FS_STATE, "regular") + STATE_PADX * 2


def _role_w(row: dict) -> int:
    role = str(row.get("role") or "")
    if not role:
        return 0
    return _w(role, FS_CHIP, "regular") + ROLE_PADX * 2


# ---------------------------------------------------------------------------
# 单一布局入口：高度 / 分区 / 每块 y 全部从这里出
# ---------------------------------------------------------------------------
def _plan(data: dict, width: int) -> dict:
    width = max(560, min(int(width), 900))
    content_w = width - PAD * 2
    inner_w = content_w - BLOCK_PAD_X * 2 - BAR_W - 6

    y = PAD + FS_TITLE + 8 + 1 + 18          # 标题 + 分割线
    if data.get("subtitle"):
        y += FS_SUB + 10

    entries: list[dict] = []
    for gi, g in enumerate(_groups_of(data)):
        y += SECTION_GAP if gi else 0
        entries.append({"kind": "section", "y": y, "h": SECTION_H, "group": g})
        y += SECTION_H
        for row in [r for r in (g.get("rows") or []) if isinstance(r, dict)]:
            rh = _row_h(row, inner_w)
            entries.append({"kind": "row", "y": y, "h": rh, "row": row,
                            "tier": str(g.get("tier") or "")})
            y += rh + BLOCK_GAP
        y -= BLOCK_GAP                         # 组末不留 gap

    y += H_TIP
    return {"width": width, "content_w": content_w, "inner_w": inner_w,
            "entries": entries, "h": max(420, y + PAD)}


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

    # ---- 分区 + 斗技块 ----
    for ent in L["entries"]:
        if ent["kind"] == "section":
            g = ent["group"]
            tier = str(g.get("tier") or "")
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

        row, rh, by = ent["row"], ent["h"], ent["y"]
        tier = ent["tier"]
        name = str(row.get("name") or "斗技")
        # 块背景 + 左缘阶位色条
        c.rounded_rect((x0, by, x0 + content_w, by + rh), radius=BLOCK_RADIUS,
                       fill=p["cell_bg"], outline=p["cell_line"], width=1)
        bar_key = TIER_STYLE.get(tier, ("chip_line", "sword"))[0]
        c.rounded_rect((x0 + 1, by + 8, x0 + 1 + BAR_W, by + rh - 8), radius=2,
                       fill=p[bar_key])

        ix = x0 + BLOCK_PAD_X + BAR_W + 6
        iy = by + BLOCK_PAD_Y

        # ---- 标题行：技能名 + 定位胶囊 + 状态 pill（右锚定）----
        name_y = iy + H_NAME // 2
        st_w = _state_w(row)
        st_x = x0 + content_w - BLOCK_PAD_X
        role_w = _role_w(row)
        # 名字可用宽 = 总宽 - 定位胶囊 - 状态 pill - 两道 gap
        name_max = (st_x - st_w - 8) - ix - (role_w + 8 if role_w else 0)
        ktext("%s.name" % name, ix, name_y, name, FS_NAME, p["text_color"],
              max_w=max(60, name_max))
        if role_w:
            rx = st_x - st_w - 8 - role_w
            role = str(row.get("role") or "")
            bg_k, ln_k, tx_k = ROLE_COLORS.get(role, ("cell_bg", "cell_line", "dim_color"))
            c.rounded_rect((rx, name_y - ROLE_H // 2, rx + role_w, name_y + ROLE_H // 2),
                           radius=ROLE_H // 2, fill=p[bg_k], outline=p[ln_k], width=1)
            c.text((rx + role_w // 2, name_y), role, size=FS_CHIP, color=p[tx_k],
                   role="regular", bold=False, anchor="mm")
            rec("%s.role" % name, role, rx + role_w // 2, name_y, FS_CHIP, "regular", "mm")
        if st_w:
            state = str(row.get("state") or "")
            kind = str(row.get("state_kind") or "learn")
            bg_k, ln_k, tx_k = STATE_STYLE.get(kind, STATE_STYLE["learn"])
            c.rounded_rect((st_x - st_w, name_y - STATE_H // 2, st_x,
                            name_y + STATE_H // 2), radius=STATE_H // 2,
                           fill=p[bg_k], outline=p[ln_k], width=1)
            c.text((st_x - st_w // 2, name_y), state, size=FS_STATE, color=p[tx_k],
                   role="regular", bold=False, anchor="mm")
            rec("%s.state" % name, state, st_x - st_w // 2, name_y, FS_STATE,
                "regular", "mm")

        # ---- 副行（阶位 · 类型 · 斗气）----
        cy = iy + H_NAME + GAP_NAME_META
        stext("%s.meta" % name, ix, cy + H_META // 2, str(row.get("meta") or ""),
              FS_META, p["dim_color"], max_w=inner_w)
        cy += H_META

        # ---- 效果 chip（每个维度一个胶囊）----
        t_ = _row_texts(row, inner_w)
        if t_["chip_rows"]:
            cy += GAP_META_CHIP
            for ri, chips in enumerate(t_["chip_rows"]):
                cx = ix
                for ch in chips:
                    label = str(ch.get("label") or "")
                    value = str(ch.get("value") or "")
                    cw = _chip_w(ch)
                    c.rounded_rect((cx, cy, cx + cw, cy + H_CHIP), radius=H_CHIP // 2,
                                   fill=p["chip_bg"], outline=p["chip_line"], width=1)
                    lw = _w(label, FS_CHIP, "regular")
                    vw = _w(value, FS_CHIP, "regular") if value else 0
                    seg = lw + (6 + vw if value else 0)
                    sx = cx + (cw - seg) // 2
                    c.text((sx, cy + H_CHIP // 2), label, size=FS_CHIP,
                           color=p["chip_text"], role="regular", bold=False, anchor="lm")
                    rec("%s.c%d.l" % (name, ri), label, sx, cy + H_CHIP // 2,
                        FS_CHIP, "regular", "lm")
                    if value:
                        vx = sx + lw + 6
                        c.text((vx, cy + H_CHIP // 2), value, size=FS_CHIP,
                               color=p["title_color"], role="regular", bold=False,
                               anchor="lm")
                        rec("%s.c%d.v" % (name, ri), value, vx, cy + H_CHIP // 2,
                            FS_CHIP, "regular", "lm")
                    cx += cw + CHIP_GAP
                cy += H_CHIP + CHIP_GAP
            cy -= CHIP_GAP

        # ---- 代价行（更弱，单独成行，不与效果连读）----
        if t_["gate_lines"]:
            cy += GAP_CHIP_GATE
            for gi2, gl in enumerate(t_["gate_lines"]):
                gy = cy + gi2 * GATE_LH + GATE_LH // 2
                stext("%s.g%d" % (name, gi2), ix, gy, gl, FS_GATE, p["dim_color"],
                      max_w=inner_w)

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
