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
    → **主定位胶囊 + 阶位 + 来源**（续航/控场/爆发/位移…一眼可辨）
    → 完整技能描述(17px，原著全文，不再用 brief 一句带过)
    → **多维效果 chip 行**（吸血25% / 破防30% / 血毒5%·2回合…，自动折行）
    → **门控代价行**（限：起手慢、需落脚点…）
    → **五段独立的斗气条**（一重亮一段…五重亮五段，段内标「一/二/三/四/五」）
    → 伤害面板（基础倍率 / 当前倍率 / 期望单击三列大字粗体 + 下一级预估，
    明确「未计目标防御」口径，避免玩家把倍率当成实际伤害）；
  * 底部提示。

2026-11-15 多维度重构（用户明确要求）
------------------------------------
用户原话：「技能描述也没展示出来，技能数值也没展示，比如一个技能有位移又有
伤害，那就应该有闪避和少量伤害，而不是一个单纯的数字……不然所有斗技都只剩
一个数值对比，失去多样性」。因此单块内**按维度分行呈现**：
  role(主定位) / effects[](多维效果原子) / gate(门控) / desc(完整描述) / 伤害数值。
所有文字 obeys card-design 度量优先：`_w` 实测、`_fit` 截断、`Canvas.wrap`
折行；chip 逐字测宽后换行；`build(..., boxes=[...])` 收集包围盒供 check_boxes
自检（溢出/重叠/间距），把红线拦在交差前。

字重说明（重要）
----------------
``role="bold"`` 在 Canvas 里只影响 kind 推断，**不会自动选粗体文件**；
本模板所有重点文字显式传 ``bold=True``，走 msyhbd.ttc / msyh.ttc(index=1)
真粗体；主题族无粗体变体时由画布层补伪粗描边。默认卡片不受影响。

设计约束：纯 Pillow 自绘；不使用 emoji；配色读 ``Theme.bg_overrides["doupo"]``。

输入（build 的 data，JSON 友好）：
  icon/title/subtitle/item/item_count/formula/tip
  rows: [{
    "badge", "name", "lv", "max", "desc", "rank", "origin_mark",
    "role", "chips": [{"label","value"}], "gate",
    "base_mult", "cur_mult", "next_mult", "dmg", "next_dmg",
    "segments", "lit", "maxed", "item", "item_need"
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

# ---- 多维度区（2026-11-15）----
# 间距 token 全部 4 的倍数，与 card-design 规范一致；同层统一 gap。
FS_ROLE = 16                   # 主定位胶囊文字（>=16 清晰下限）
FS_CHIP = 15                   # 效果 chip 文字
FS_GATE = 15                   # 门控代价文字
ROLE_H = 26                    # 主定位胶囊高
CHIP_H = 24                    # 效果 chip 高
GATE_LH = FS_GATE + 5          # 20
META_GAP = 8                   # 标题行 → 定位行
TAG_GAP = 8                    # 定位/效果/门控各行之间
CHIP_GAP = 8                   # chip 之间的显式水平/垂直 gap（禁止空格凑距）
CHIP_PADX = 9                  # chip 内左右留白
CHIP_PADY = 4
ROLE_PADX = 12
ROLE_COLORS = {                # 主定位 → (底色, 描边, 文字) 语义色，暗金/朱红系
    "续航": ("pill_bg", "pill_line", "pill_text"),
    "异常": ("pill_bg", "pill_line", "pill_text"),
    "控场": ("chip_bg", "chip_line", "chip_text"),
    "爆发": ("chip_bg", "chip_line", "chip_text"),
    "防御": ("pill_bg", "pill_line", "pill_text"),
    "位移": ("pill_bg", "pill_line", "pill_text"),
    "功能": ("cell_bg", "cell_line", "dim_color"),
    "输出": ("chip_bg", "chip_line", "chip_text"),
}

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
        {"badge": "1", "name": "嗜血化骨手", "lv": 3, "max": 5,
         "rank": "地阶低级", "origin_mark": "〔本宗〕", "role": "续航",
         "desc": "掌爪涌出血红斗气，凝成一只巨大的血手；血手张开时生出一股诡异吸力，要把对手体内的血硬扯出来。造成 160% 伤害，并回复自身相当于伤害 25% 的体力（吸血）。",
         "chips": [{"label": "吸血", "value": "25%"}],
         "gate": "敌有异火则反噬自伤",
         "base_mult": 1.60, "cur_mult": 2.24, "next_mult": 2.56,
         "dmg": 1234, "next_dmg": 1410, "segments": 5, "lit": 3, "maxed": False,
         "item": "焚血散", "item_need": 3},
        {"badge": "3", "name": "血煞掌", "lv": 1, "max": 5,
         "rank": "玄阶高级", "origin_mark": "〔本宗〕", "role": "异常",
         "desc": "斗气凝血成掌，掌色暗红近黑；一掌拍出，血毒随掌力渗入，伤口不流血反而发黑。造成 140% 伤害，并附加血毒（每回合 5%，持续 2 回合，且治疗 -30%）。",
         "chips": [{"label": "血毒", "value": "5%·2回合"},
                   {"label": "减疗", "value": "30%·2回合"}],
         "gate": "气血虚时威力骤降，对无血之物无效",
         "base_mult": 1.40, "cur_mult": 1.40, "next_mult": 1.68,
         "dmg": 771, "next_dmg": 925, "segments": 5, "lit": 1, "maxed": False,
         "item": "焚血散", "item_need": 1},
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
    desc = str(item.get("desc") or "")
    if not desc:
        return []
    # 避头：中文长描述逐字折行会让「。」掉到行首（实测「。造成 95% 伤害」）
    return S.wrap_cjk(desc, FS_DESC, "regular", max(80, inner_w - BADGE_W - BADGE_GAP))


def _chip_w(chip: dict) -> int:
    """单个效果 chip 的文字宽（度量优先，label 与 value 分开测后相加）。"""
    label = str(chip.get("label") or "")
    value = str(chip.get("value") or "")
    w = _w(label, FS_CHIP, "regular")
    if value:
        w += 4 + _w(value, FS_CHIP, "bold")   # 数值 bold，与 label 间 4px
    return int(w) + CHIP_PADX * 2


def _chip_rows(item: dict, inner_w: int) -> list[list[dict]]:
    """效果 chip 按可用宽度逐行打包（显式 CHIP_GAP，禁止空格凑距）。"""
    chips = [c for c in (item.get("chips") or []) if isinstance(c, dict)]
    rows: list[list[dict]] = []
    cur: list[dict] = []
    cur_w = 0
    for ch in chips:
        cw = _chip_w(ch)
        used = cur_w + (CHIP_GAP if cur else 0) + cw
        if cur and used > inner_w:
            rows.append(cur)
            cur, cur_w = [ch], cw
        else:
            cur.append(ch)
            cur_w = used
    if cur:
        rows.append(cur)
    return rows


def _gate_lines(item: dict, inner_w: int) -> list[str]:
    gate = str(item.get("gate") or "")
    if not gate:
        return []
    return Canvas.wrap("限：" + gate, FS_GATE, "regular", max(80, inner_w - 4))


def _row_h(item: dict, inner_w: int) -> int:
    """单块高度。build 的 y 步进必须与这里完全一致，否则底部留白会歪。

    内容驱动：描述行数 + chip 行数 + 门控行数都参与累加，长描述/多效果不会裁切。
    """
    n = max(1, len(_desc_lines(item, inner_w)))
    n_chip = len(_chip_rows(item, inner_w))
    n_gate = len(_gate_lines(item, inner_w))
    h = BLOCK_PAD + TITLE_H + META_GAP + ROLE_H
    h += 6 + n * DESC_LH + TAG_GAP
    if n_chip:
        h += n_chip * CHIP_H + (n_chip - 1) * CHIP_GAP + TAG_GAP
    if n_gate:
        h += n_gate * GATE_LH + TAG_GAP
    h += SEG_LABEL_H + 4 + SEG_H + 8 + PANEL_H + BLOCK_PAD
    return h


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
    dy = iy + TITLE_H + META_GAP + ROLE_H + 6 + n * DESC_LH + TAG_GAP
    n_chip = len(_chip_rows(item, inner_w))
    n_gate = len(_gate_lines(item, inner_w))
    if n_chip:
        dy += n_chip * CHIP_H + (n_chip - 1) * CHIP_GAP + TAG_GAP
    if n_gate:
        dy += n_gate * GATE_LH + TAG_GAP
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

    #: 可选：收集文本包围盒（card-design check_boxes 自检；默认 None 零开销）
    boxes = kw.pop("boxes", None)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    def rec(label: str, txt: str, x: int, y: int, size: int, role: str,
            anchor: str) -> None:
        """记录文本包围盒（绘制与度量同 role，锚点换算与 Canvas 一致）。"""
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
        else:                                  # la
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

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
    for item, block_y, rh in layout:
        y = block_y
        c.rounded_rect((block_x, y, block_x + block_w, y + rh), radius=BLOCK_RADIUS,
                       fill=p["cell_bg"], outline=p["cell_line"], width=1)
        # 左侧暗金竖条：块边界一眼可辨
        c.rounded_rect((block_x + 1, y + 14, block_x + 5, y + rh - 14), radius=2,
                       fill=p["accent_color"])

        iy = y + BLOCK_PAD
        name = str(item.get("name") or "斗技")
        # 快捷键 chip
        badge = str(item.get("badge") or "")
        c.rounded_rect((inner_x, iy, inner_x + BADGE_W, iy + BADGE_H), radius=9,
                       fill=p["chip_bg"], outline=p["chip_line"], width=1)
        badge_txt = _fit(badge, BADGE_W - 8, FS_BADGE, "bold")
        c.text((inner_x + BADGE_W // 2, iy + BADGE_H // 2), badge_txt, size=FS_BADGE,
               color=p["chip_text"], role="bold", bold=True, anchor="mm")
        rec("%s.badge" % name, badge_txt, inner_x + BADGE_W // 2, iy + BADGE_H // 2,
            FS_BADGE, "bold", "mm")

        # 技能名（大 + 粗）与等级（更大 + 粗 + 朱红）
        name_x = inner_x + BADGE_W + BADGE_GAP
        lv = _int(item.get("lv"), 1)
        mx = max(1, _int(item.get("max"), 5))
        lv_text = "Lv.%d / %d" % (lv, mx)
        lv_w = _w(lv_text, FS_LV, "bold")
        name_txt = _fit(name, block_x + block_w - 18 - lv_w - 18 - name_x, FS_NAME, "bold")
        c.text((name_x, iy + BADGE_H // 2), name_txt,
               size=FS_NAME, color=p["text_color"], role="bold", bold=True, anchor="lm")
        rec("%s.name" % name, name_txt, name_x, iy + BADGE_H // 2, FS_NAME, "bold", "lm")
        c.text((block_x + block_w - 18, iy + BADGE_H // 2), lv_text,
               size=FS_LV, color=p["title_color"], role="bold", bold=True, anchor="rm")
        rec("%s.lv" % name, lv_text, block_x + block_w - 18, iy + BADGE_H // 2,
            FS_LV, "bold", "rm")

        # ---- 定位 / 来源行（主定位胶囊 + 阶位 + 来源）----
        meta_y = iy + TITLE_H + META_GAP
        role = str(item.get("role") or "")
        mx0 = inner_x
        if role:
            bg_k, ln_k, tx_k = ROLE_COLORS.get(role, ("chip_bg", "chip_line", "chip_text"))
            rw = ROLE_PADX * 2 + _w(role, FS_ROLE, "regular")
            c.rounded_rect((mx0, meta_y, mx0 + rw, meta_y + ROLE_H), radius=ROLE_H // 2,
                           fill=p[bg_k], outline=p[ln_k], width=1)
            c.text((mx0 + rw // 2, meta_y + ROLE_H // 2), role, size=FS_ROLE,
                   color=p[tx_k], role="regular", bold=False, anchor="mm")
            rec("%s.role" % name, role, mx0 + rw // 2, meta_y + ROLE_H // 2,
                FS_ROLE, "regular", "mm")
            mx0 += rw + CHIP_GAP
        meta_txt = " ".join(x for x in (str(item.get("rank") or ""),
                                        str(item.get("origin_mark") or "")) if x)
        if meta_txt:
            c.text((mx0, meta_y + ROLE_H // 2), _fit(meta_txt, inner_w - (mx0 - inner_x), FS_LABEL),
                   size=FS_LABEL, color=p["dim_color"], role="regular", bold=False, anchor="lm")
            rec("%s.meta" % name, meta_txt, mx0, meta_y + ROLE_H // 2,
                FS_LABEL, "regular", "lm")

        # ---- 完整技能描述（与 skill_book 权威 desc 同源；折行不丢字）----
        dy = meta_y + ROLE_H + 6
        lines = _desc_lines(item, inner_w)
        for i, ln in enumerate(lines):
            ly = dy + i * DESC_LH + DESC_LH // 2
            c.text((inner_x, ly), ln, size=FS_DESC, color=p["text_color"],
                   role="regular", bold=False, anchor="lm")
            rec("%s.desc%d" % (name, i), ln, inner_x, ly, FS_DESC, "regular", "lm")

        # ---- 多维效果 chip 行（每个维度一个 chip，显式 gap，禁止空格凑距）----
        chip_top = dy + len(lines) * DESC_LH + TAG_GAP
        chip_rws = _chip_rows(item, inner_w)
        for ri, row in enumerate(chip_rws):
            cx = inner_x
            cy = chip_top + ri * (CHIP_H + CHIP_GAP)
            for ch in row:
                label = str(ch.get("label") or "")
                value = str(ch.get("value") or "")
                cw = _chip_w(ch)
                c.rounded_rect((cx, cy, cx + cw, cy + CHIP_H), radius=CHIP_H // 2,
                               fill=p["chip_bg"], outline=p["chip_line"], width=1)
                # label 与 bold 数值分段度量，中心对齐到 chip 内
                lw = _w(label, FS_CHIP, "regular")
                vw = _w(value, FS_CHIP, "bold") if value else 0
                seg_w = lw + (4 + vw if value else 0)
                sx = cx + (cw - seg_w) // 2
                c.text((sx, cy + CHIP_H // 2), label, size=FS_CHIP,
                       color=p["chip_text"], role="regular", bold=False, anchor="lm")
                rec("%s.chip%d.label" % (name, ri), label, sx, cy + CHIP_H // 2,
                    FS_CHIP, "regular", "lm")
                if value:
                    vx = sx + lw + 4
                    c.text((vx, cy + CHIP_H // 2), value, size=FS_CHIP,
                           color=p["title_color"], role="bold", bold=True, anchor="lm")
                    rec("%s.chip%d.value" % (name, ri), value, vx, cy + CHIP_H // 2,
                        FS_CHIP, "bold", "lm")
                cx += cw + CHIP_GAP

        # ---- 门控 / 代价行 ----
        gate_top = chip_top + len(chip_rws) * CHIP_H + (len(chip_rws) - 1) * CHIP_GAP + TAG_GAP
        for gi, gl in enumerate(_gate_lines(item, inner_w)):
            gy = gate_top + gi * GATE_LH + GATE_LH // 2
            c.text((inner_x, gy), gl, size=FS_GATE, color=p["accent_color"],
                   role="regular", bold=False, anchor="lm")
            rec("%s.gate%d" % (name, gi), gl, inner_x, gy, FS_GATE, "regular", "lm")

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
        tip_txt = _fit(str(tip), content_w, FS_TIP)
        c.text((x0, y + FS_TIP // 2), tip_txt, size=FS_TIP,
               color=p["dim_color"], anchor="lm")
        rec("tip", tip_txt, x0, y + FS_TIP // 2, FS_TIP, "regular", "lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
