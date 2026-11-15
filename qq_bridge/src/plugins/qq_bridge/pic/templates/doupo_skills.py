# -*- coding: utf-8 -*-
"""pic/templates/doupo_skills.py —— 斗破·斗技专用卡（2026-11-14）。

为什么单独一张模板，而不是继续复用 doupo_list
------------------------------------------------
用户要求「加上技能描述和详细的技能伤害描述、进度条改成五个条、重要文字
特殊字体加粗放大、让人一看到重点不用放大图片」。通用列表卡是给
异火/宝库/背包/排行复用的紧凑行式版式（主标题 20px、说明 15px、单条连续进度条），
把斗技硬塞进去会：① 把另外四张卡一起改坏；② 字更小、信息更挤。
因此另开专用模板，doupo_list 保持原样（无创原则）。

版式（羊皮纸米 / 朱红 / 暗金，配色与人物卡、菜单卡同源）
------------------------------------------------
  * 顶部：大标题「斗技」+ 副标题 + **升级材料横幅**（焚血散 ×N 大字粗体）
    + 统一伤害口径说明；
  * 每门斗技一块卡片：快捷键 chip + 技能名(27px 粗体) + Lv.x/5(30px 粗体朱红)
    → 完整技能描述(17px) → **五段独立的斗气条**（一重亮一段…五重亮五段，
    段内标「一/二/三/四/五」）→ 伤害面板（基础倍率 / 当前倍率 / 期望单击
    三列大字粗体 + 下一级预估）；
  * 底部提示。

字重说明（重要）
----------------
``role="bold"`` 在 Canvas 里只影响 kind 推断，**不会自动选粗体文件**；
本模板所有重点文字显式传 ``bold=True``，走 msyhbd.ttc / msyh.ttc(index=1)
真粗体；主题族无粗体变体时由画布层补伪粗描边。默认卡片不受影响。

设计约束：纯 Pillow 自绘；不使用 emoji；配色读 ``Theme.bg_overrides["doupo"]``。

输入（build 的 data，JSON 友好）：
  icon/title/subtitle/item/item_count/formula/tip
  rows: [{
    "badge", "name", "lv", "max", "desc",
    "base_mult", "cur_mult", "next_mult", "dmg", "next_dmg",
    "segments", "lit", "maxed"
  }]
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与人物卡 / 菜单卡 / 通用列表卡共用同一套配色与度量工具
from .doupo import _card_alpha, _fit, _pal, _w
# 四张卡共用同一份字号/字重规范（本卡是首个按该规范落地的模板，数值不变）
from . import doupo_style as S

# ---- 排版（字比通用列表卡大一圈：用户 2026-11-14 要求「不放大图片也能看重点」）----
PAD = 28
FS_TITLE = 38
FS_SUB = S.FS_SUB + 1          # 18
FS_ITEM_LABEL = 15
FS_ITEM = S.FS_KEY + 2         # 26 升级材料「焚血散 ×3」
FS_RULE = S.FS_KEY + 2         # 26 「每升一重 伤害 +20%」：够大才不会被伪粗糊掉
FS_FORMULA = 15                # 统一伤害口径
FS_NAME = S.FS_HERO - 3        # 27 技能名
FS_LV = S.FS_HERO              # 30 Lv.3/5
FS_BADGE = 19                  # 快捷键
FS_DESC = S.FS_SUB             # 17 技能描述
FS_SEG_LABEL = 15
FS_SEG = 16                    # 段内「一…五」
FS_LABEL = 15                  # 伤害面板标签
FS_VALUE = S.FS_KEY + 2        # 26 伤害面板大字
FS_NEXT = S.FS_SUB             # 17 下级预估（小字不加粗，只靠字号与强调色）
FS_TIP = S.FS_LABEL            # 16

ICON = 22
ICON_STYLE = "line"

BADGE_W = 46
BADGE_H = 40
BADGE_GAP = 12

DESC_LH = FS_DESC + 5          # 22
TITLE_H = BADGE_H              # 40
SEG_LABEL_H = 19
SEG_H = 26
SEG_GAP = 10
SEG_RADIUS = 8
PANEL_H = 92
PANEL_COL_GAP = 8
PANEL_PAD = 14
BLOCK_PAD = 14
BLOCK_RADIUS = 16
ROW_GAP = 12
BANNER_H = 70          # 标签与大字之间留足呼吸空间（原 62 会贴在一起）

#: 五段条的段名（斗技五重，中文数字比 1/2/3 更贴原著语感）
SEG_NAMES = ("一", "二", "三", "四", "五")
SEG_LIT_TEXT = "#FFF6E8"

#: 编辑器预览用示例数据（pic 包不 import 插件层，故内置示例）
_DEMO = {
    "icon": "bolt",
    "title": "斗技",
    "subtitle": "四门斗技 · 五重斗气 · 快捷键 1/3/7/9",
    "item": "焚血散",
    "item_count": 3,
    "formula": "伤害口径：(力量×0.5 + 斗气×1.5) × 当前倍率 × 暴击期望（未计目标防御）",
    "rows": [
        {"badge": "1", "name": "龙吟碎", "lv": 3, "max": 5,
         "desc": "凝火化龙，攻击周围目标，技能每提升一级，技能伤害增加 20%，将目标击晕几率增加 20%",
         "base_mult": 1.60, "cur_mult": 2.24, "next_mult": 2.56,
         "dmg": 1234, "next_dmg": 1410, "segments": 5, "lit": 3, "maxed": False},
        {"badge": "3", "name": "天火三玄变", "lv": 1, "max": 5,
         "desc": "焚炎附体，连续三段火劲贯体而出，技能每提升一级伤害增加 20%",
         "base_mult": 1.40, "cur_mult": 1.40, "next_mult": 1.68,
         "dmg": 771, "next_dmg": 925, "segments": 5, "lit": 1, "maxed": False},
    ],
    "tip": "用法：升级 龙吟碎 · 现有 焚血散 ×3",
}


def _rows_of(data) -> list:
    return [r for r in (data.get("rows") or []) if isinstance(r, dict)]


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _desc_lines(item: dict, inner_w: int) -> list[str]:
    """技能描述按换行分段，逐段自动折行。

    合并「斗技详情」后，desc 由「主描述 / 类型·冷却 / 出处 / 原著 / 解锁提示」
    多行组成（以 \\n 分隔）。这里每段独立折行，空段保留为视觉留白；
    返回的总行数会驱动 _row_h / _seg_y 自动撑高卡片。
    """
    desc = str(item.get("desc") or "")
    if not desc:
        return []
    avail = max(80, inner_w - BADGE_W - BADGE_GAP)
    out: list[str] = []
    for part in desc.split("\n"):
        if part.strip() == "":
            out.append("")  # 空段 → 视觉留白
            continue
        out.extend(Canvas.wrap(part, FS_DESC, "regular", avail))
    return out


def _row_h(item: dict, inner_w: int) -> int:
    """单块高度。build 的 y 步进必须与这里完全一致，否则底部留白会歪。"""
    n = max(1, len(_desc_lines(item, inner_w)))
    return (BLOCK_PAD + TITLE_H + 6 + n * DESC_LH + 8
            + SEG_LABEL_H + 4 + SEG_H + 8 + PANEL_H + BLOCK_PAD)


def _content_y(data: dict) -> int:
    """第一个技能块的顶边 y（标题 / 副标题 / 材料横幅 / 伤害口径之后）。"""
    y = PAD + FS_TITLE + 8 + 1 + 18           # 标题 + 分割线
    if data.get("subtitle"):
        y += FS_SUB + 10
    y += BANNER_H + 10                        # 升级材料横幅
    if data.get("formula"):
        y += FS_FORMULA + 12
    return y


def _rows_layout(data: dict, inner_w: int):
    """产出 (item, block_y, row_h)。

    渲染与像素级验收**共用这一个布局源**：验收脚本据此取五段条的像素坐标，
    不需要自己重算一遍（重算容易与渲染漂移，造成"验证恒真"的假通过）。
    """
    y = _content_y(data)
    for item in _rows_of(data):
        rh = _row_h(item, inner_w)
        yield item, y, rh
        y += rh + ROW_GAP


def _seg_y(item: dict, block_y: int, inner_w: int) -> int:
    """该块五段条的顶边 y（与 build 里的绘制步进同步）。"""
    iy = block_y + BLOCK_PAD
    n = max(1, len(_desc_lines(item, inner_w)))
    dy = iy + TITLE_H + 6 + n * DESC_LH + 8
    return dy + SEG_LABEL_H + 4


def _measure(data: dict, inner_w: int) -> int:
    y = _content_y(data)
    for r in _rows_of(data):
        y += _row_h(r, inner_w) + ROW_GAP
    y += -ROW_GAP + 1 + 12 + FS_TIP + PAD
    return max(320, y)


def segment_boxes(x: int, y: int, w: int, item: dict) -> list[tuple[int, int, int, int]]:
    """五段条每段的外接矩形（与 ``_draw_segments`` 同源）。"""
    n = max(1, min(5, _int(item.get("segments"), 5)))
    seg_w = max(12, (w - (n - 1) * SEG_GAP) // n)
    return [(x + i * (seg_w + SEG_GAP), y,
             x + i * (seg_w + SEG_GAP) + seg_w, y + SEG_H) for i in range(n)]


def segment_positions(data: dict, width: int = 720) -> list[list[tuple[int, int, int, int]]]:
    """每个技能块的五段条像素坐标，供机器验收逐段采样点亮状态。"""
    width = max(560, min(int(width), 900))
    content_w = width - PAD * 2
    block_w = content_w - 12
    inner_x = PAD + 6 + 18
    inner_w = block_w - 36
    out = []
    for item, by, _rh in _rows_layout(data, inner_w):
        out.append(segment_boxes(inner_x, _seg_y(item, by, inner_w), inner_w, item))
    return out


def _draw_segments(c: Canvas, x: int, y: int, w: int, item: dict, p: dict) -> None:
    """五条**独立**斗气段：一重亮一段、二重亮两段……五重全亮。

    与通用列表卡的单条渐变进度条（``doupo.py::_bar``）完全不同的语义：
    这里是离散段位，段与段之间有真实空隙，段数恒为 5。
    """
    boxes = segment_boxes(x, y, w, item)
    n = len(boxes)
    lit = max(0, min(n, _int(item.get("lit"), _int(item.get("lv"), 1))))
    for i, box in enumerate(boxes):
        sx = box[0]
        seg_w = box[2] - box[0]
        if i < lit:
            # 左金右朱红（gradient_rect 的方向坑见 doupo.py::_bar 注释）
            c.gradient_rect(box, p["bar_to"], p["bar_from"],
                            radius=SEG_RADIUS, horizontal=True,
                            outline=p["chip_line"])
            color = SEG_LIT_TEXT
        else:
            c.rounded_rect(box, radius=SEG_RADIUS, fill=p["track_color"],
                           outline=p["border_color"], width=1)
            color = p["dim_color"]
        label = SEG_NAMES[i] if i < len(SEG_NAMES) else str(i + 1)
        c.text((sx + seg_w // 2, y + SEG_H // 2), label,
               size=FS_SEG, color=color, role="bold", bold=(i < lit), anchor="mm")


def _draw_damage_panel(c: Canvas, x: int, y: int, w: int, item: dict, p: dict) -> None:
    """伤害三要素：基础倍率 / 当前倍率 / 期望单击，全部大字粗体。"""
    c.rounded_rect((x, y, x + w, y + PANEL_H), radius=10,
                   fill=p["card_color"], outline=p["chip_line"], width=1)
    base = _num(item.get("base_mult"), 1.0)
    cur = _num(item.get("cur_mult"), base)
    dmg = _int(item.get("dmg"))
    cols = [
        ("基础倍率", "×%.2f" % base, p["dim_color"], p["text_color"], "regular"),
        ("当前倍率", "×%.2f" % cur, p["dim_color"], p["title_color"], "bold"),
        ("期望单击", "{:,}".format(dmg), p["dim_color"], p["title_color"], "bold"),
    ]
    col_w = (w - PANEL_PAD * 2 - PANEL_COL_GAP * 2) // 3
    for i, (label, value, lc, vc, role) in enumerate(cols):
        cx = x + PANEL_PAD + i * (col_w + PANEL_COL_GAP)
        c.text((cx, y + 9), label, size=FS_LABEL, color=lc, anchor="la")
        c.text((cx, y + 9 + FS_LABEL + 7), _fit(value, col_w, FS_VALUE, role),
               size=FS_VALUE, color=vc, role=role, bold=(role == "bold"), anchor="la")
    ny = y + PANEL_H - FS_NEXT - 9
    need = _int(item.get("item_need"))
    item_name = str(item.get("item") or "焚血散")
    if item.get("maxed"):
        note = "已至五重圆满 · 倍率封顶，伤害不再成长"
    else:
        note = "下级 ×%.2f · 期望 %s 点 · 每升一重伤害 +20%%" % (
            _num(item.get("next_mult"), cur), "{:,}".format(_int(item.get("next_dmg"))))
    right_note = "已圆满" if item.get("maxed") else "需 %s ×%d" % (item_name, need)
    right_w = _w(right_note, FS_NEXT, "regular") + 10
    c.text((x + PANEL_PAD, ny), _fit(note, w - PANEL_PAD * 2 - right_w, FS_NEXT),
           size=FS_NEXT, color=p["dim_color"], anchor="la")
    # 小字显式 bold=False：17px 中文加 1px 伪粗会糊成一团（2026-11-14 视觉验收）
    c.text((x + w - PANEL_PAD, ny), right_note, size=FS_NEXT,
           color=p["accent_color"], role="regular", bold=False, anchor="ra")


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    block_x = PAD + 6
    block_w = content_w - 12
    inner_x = block_x + 18
    inner_w = block_w - 36
    h = _measure(data, inner_w)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    icon = str(data.get("icon") or "bolt")
    if not icons.exists(icon):
        icon = "sword"
    title = str(data.get("title") or "斗技")
    tw = _w(title, FS_TITLE, "light")
    ico_w = FS_TITLE + 10
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, icon, tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE,
               p["title_color"], style=ICON_STYLE)
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 副标题 ----
    sub = data.get("subtitle")
    if sub:
        c.text((x0, y + FS_SUB // 2), _fit(str(sub), content_w, FS_SUB),
               size=FS_SUB, color=p["dim_color"], anchor="lm")
        y += FS_SUB + 10

    # ---- 升级材料横幅（重点：物品名与数量，特殊字体 + 加粗放大）----
    bx0, bx1 = x0, x0 + content_w
    c.rounded_rect((bx0, y, bx1, y + BANNER_H), radius=12,
                   fill=p["chip_bg"], outline=p["chip_line"], width=1)
    item_name = str(data.get("item") or "焚血散")
    item_n = _int(data.get("item_count"))
    c.text((bx0 + 16, y + 10), "升级材料", size=FS_ITEM_LABEL,
           color=p["dim_color"], anchor="la")
    c.text((bx0 + 16, y + 10 + FS_ITEM_LABEL + 8),
           _fit("%s ×%d" % (item_name, item_n), content_w // 2, FS_ITEM, "bold"),
           size=FS_ITEM, color=p["title_color"], role="bold", bold=True, anchor="la")
    # 这句横画多（重/伤害），26px 再叠 1px 伪粗会把笔画粘成一团；
    # 保持 26px 大字 + 暗金强调色即可醒目，不牺牲可读性。
    c.text((bx1 - 16, y + BANNER_H // 2),
           _fit("每升一重  伤害 +20%", content_w // 2 - 20, FS_RULE, "regular"),
           size=FS_RULE, color=p["accent_color"], role="regular", bold=False, anchor="rm")
    y += BANNER_H + 10

    # ---- 统一伤害口径（每个技能不必重复一遍公式）----
    formula = data.get("formula")
    if formula:
        c.text((x0, y + FS_FORMULA // 2), _fit(str(formula), content_w, FS_FORMULA),
               size=FS_FORMULA, color=p["dim_color"], anchor="lm")
        y += FS_FORMULA + 12

    # ---- 四门斗技 ----
    layout = list(_rows_layout(data, inner_w))
    C_WHITE = "#FFFFFF"
    for item, block_y, rh in layout:
        y = block_y
        equipped = bool(item.get("equipped"))
        locked = bool(item.get("locked"))
        # 上阵块：朱红描边加粗，一眼可辨「哪些在阵上」；其余用默认细描边
        block_outline = p["title_color"] if equipped else p["cell_line"]
        block_lw = 3 if equipped else 1
        c.rounded_rect((block_x, y, block_x + block_w, y + rh), radius=BLOCK_RADIUS,
                       fill=p["cell_bg"], outline=block_outline, width=block_lw)
        # 左侧竖条：上阵朱红、其余暗金，块边界更跳
        bar_color = p["title_color"] if equipped else p["accent_color"]
        c.rounded_rect((block_x + 1, y + 14, block_x + 5, y + rh - 14), radius=2,
                       fill=bar_color)

        iy = y + BLOCK_PAD
        badge = str(item.get("badge") or "")
        # 编号 chip：上阵填充朱红+白字，未解锁用 dim，其余 chip 色
        if equipped:
            badge_fill, badge_text = p["title_color"], C_WHITE
        elif locked:
            badge_fill, badge_text = p["chip_bg"], p["dim_color"]
        else:
            badge_fill, badge_text = p["chip_bg"], p["chip_text"]
        c.rounded_rect((inner_x, iy, inner_x + BADGE_W, iy + BADGE_H), radius=9,
                       fill=badge_fill, outline=p["chip_line"], width=1)
        c.text((inner_x + BADGE_W // 2, iy + BADGE_H // 2),
               _fit(badge, BADGE_W - 8, FS_BADGE, "bold"), size=FS_BADGE,
               color=badge_text, role="bold", bold=True, anchor="mm")

        # 右端状态标签（上阵 / 未解锁）：先量宽度，再决定名字可用空间
        lv = _int(item.get("lv"), 1)
        mx = max(1, _int(item.get("max"), 5))
        lv_text = "Lv.%d / %d" % (lv, mx)
        lv_w = _w(lv_text, FS_LV, "bold")
        tag_text = ""
        tag_color = p["dim_color"]
        tag_star = False
        if equipped:
            tag_text, tag_color, tag_star = "上阵", p["title_color"], True
        elif locked:
            ul = item.get("unlock")
            tag_text = "未解锁 lv%s" % ul if str(ul).isdigit() else "未解锁"
        tag_w = 0
        tag_icon = 0
        if tag_text:
            tag_icon = (FS_SUB + 6) if tag_star else 0
            tag_w = tag_icon + _w(tag_text, FS_SUB, "bold") + 18

        # 技能名（大 + 粗）：上阵朱红、未解锁 dim、其余墨褐
        name_x = inner_x + BADGE_W + BADGE_GAP
        name_color = (p["title_color"] if equipped
                      else (p["dim_color"] if locked else p["text_color"]))
        name_max = (block_x + block_w - 18) - lv_w - 12 - (tag_w + 10) - name_x
        name = str(item.get("name") or "斗技")
        name_disp = _fit(name, max(40, name_max), FS_NAME, "bold")
        c.text((name_x, iy + BADGE_H // 2), name_disp,
               size=FS_NAME, color=name_color, role="bold", bold=True, anchor="lm")
        c.text((block_x + block_w - 18, iy + BADGE_H // 2), lv_text,
               size=FS_LV, color=p["title_color"], role="bold", bold=True, anchor="rm")

        # 右端标签：星 + 文字（上阵） / 纯文字（未解锁）
        if tag_text:
            tx = block_x + block_w - 18 - lv_w - 10 - tag_w
            if tag_star:
                icons.draw(c, "star", tx + tag_icon // 2, iy + BADGE_H // 2,
                           tag_icon, tag_color, style="solid")
            c.text((tx + tag_icon, iy + BADGE_H // 2), tag_text,
                   size=FS_SUB, color=tag_color, role="bold", bold=True, anchor="lm")

        # 完整技能描述（与 cfg.SKILLS[*]["desc"] 同源）
        dy = iy + TITLE_H + 6
        lines = _desc_lines(item, inner_w)
        desc_color = p["dim_color"] if locked else p["text_color"]
        for i, ln in enumerate(lines):
            c.text((name_x, dy + i * DESC_LH + DESC_LH // 2), ln,
                   size=FS_DESC, color=desc_color, anchor="lm")

        # 五段独立斗气条（坐标与像素级验收共用 _seg_y / segment_positions）
        seg_top = _seg_y(item, block_y, inner_w)
        lit = max(0, min(5, _int(item.get("lit"), lv)))
        c.text((inner_x, seg_top - 4 - SEG_LABEL_H // 2),
               "斗技五重 · 已亮 %d / 5 段" % lit, size=FS_SEG_LABEL,
               color=p["dim_color"], anchor="lm")
        _draw_segments(c, inner_x, seg_top, inner_w, item, p)

        _draw_damage_panel(c, inner_x, seg_top + SEG_H + 8, inner_w, item, p)

    # ---- 底部提示 ----
    y = (layout[-1][1] + layout[-1][2]) if layout else (_content_y(data) - ROW_GAP)
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip:
        c.text((x0, y + FS_TIP // 2), _fit(str(tip), content_w, FS_TIP),
               size=FS_TIP, color=p["dim_color"], anchor="lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
