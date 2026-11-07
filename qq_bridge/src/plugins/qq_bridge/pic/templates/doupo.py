# -*- coding: utf-8 -*-
"""pic/templates/doupo.py —— 斗破苍穹·双帝之战 · 人物状态卡。

用于群聊「dp人物 / dp属性 / dp面板」的卡片化回复，替代原先的多行纯文本消息。

版式（浅色羊皮纸 + 赤金朱红玄幻观感，与 xiuxian.py 同一套 Rabbit 极简排版范式）：
  * 顶部居中标题「斗破大陆」+ sword 细描边图标 + 贯通分割线；
  * 角色名（左）+ 境界 chip（右，如「一星斗者」）；
  * 境界行（境界名 左 / 修为 x/y 右）+ 金→朱红渐变进度条；
    修为溢出（exp >= need，可突破）时：进度条转**暖金** + 右侧标注「· 可突破」；
  * 炼药师品阶 pill（药青，淡绿）；
  * 武器行：sword 图标 + 武器名 + 显示等级 + 四围加成（力量/敏捷/斗气/防御）；
  * 六维网格 3×2：力量 / 敏捷 / 斗气 / 防御 / 机缘 / 体力（数值大、标签小）；
  * 气血条 / 斗气条（红 / 蓝渐变，右侧标 n/n）；
  * 底部一行：金币 / 炎星石 / 战力 / 战绩（胜负数）。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * 全部文案用中文真字体绘制，**不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色可经 ``Theme.bg_overrides["doupo"]`` 覆盖，字段见 :func:`_pal`。

输入（build 的 params）：
  name       角色名
  realm      境界名（含星级，如「一星斗者」）
  exp / need 当前修为 / 升级所需
  realm_max  是否已至巅峰（True 时修为显示 x(已至巅峰)，进度条置满）
  can_break  修为已满、可突破
  alch_name  炼药师品阶名（如「二品炼药师」）
  alch_exp / alch_need 炼药经验 / 升级所需（need<=0 时显示 —）
  weapon_name 武器名（如「玄重尺」）
  weapon_level 武器显示等级（如「3」）
  weapon_stats 武器四围加成 dict（力量/敏捷/斗气/防御）
  stats      六维 dict（力量/敏捷/斗气/防御/机缘/体力）
  hp / hp_max 当前/最大气血
  mp / mp_max 当前/最大斗气
  gold       金币
  stone      炎星石数量
  power      战力
  wins/losses 战绩
  tip        底部提示（已拼好，可留空）
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# ---- 固定配色（斗破：羊皮纸米 / 朱红 / 暗金 / 药青）——未配置专属覆盖时的默认值 ----
C_PAGE = "#F2ECE2"      # 页面外背景（羊皮纸米白）
C_CARD = "#FFFFFF"      # 卡片底
C_BORDER = "#E6DCCB"    # 卡片描边
C_TEXT = "#2B2320"      # 主文字（墨褐）
C_DIM = "#8A7C6B"       # 次文字
C_TITLE = "#8A1C1C"     # 标题朱红
C_ACCENT = "#B8860B"    # 主强调（暗金）
C_LINE = "#E6DCCB"      # 分割线
C_TRACK = "#EBE2D2"     # 进度条轨道
C_BAR_FROM = "#E0A93B"  # 进度条渐变起（金）
C_BAR_TO = "#8A1C1C"    # 进度条渐变止（朱红）
# 修为已满（可突破）：暖金——与"还在修炼"的金红明确区分，一眼看出该突破了
C_BAR_READY_FROM = "#F5C978"
C_BAR_READY_TO = "#D98A2B"

# 炼药师 pill（药青，表示炼药天赋）
C_PILL_BG = "#E6EFE8"
C_PILL_LINE = "#C7D8CC"
C_PILL_TEXT = "#2F6B4F"

# 境界 chip（可突破时暖红）
C_CHIP_BG = "#FBEEDD"
C_CHIP_LINE = "#EBC79A"
C_CHIP_TEXT = "#9E1B32"

# 六维 / 武器网格底
C_CELL_BG = "#F7F1E9"
C_CELL_LINE = "#E6DCCB"

# 气血 / 斗气条
C_HP_FROM = "#F0A9A0"
C_HP_TO = "#C0392B"
C_MP_FROM = "#9FC6E8"
C_MP_TO = "#1F6FB2"

# ---- 排版 ----
PAD = 28
FS_TITLE = 34
FS_NAME = 28
FS_BODY = 20
FS_SMALL = 16
FS_TINY = 14
FS_STAT = 28
ICON = 22
BAR_H = 14
CELL_H = 80
GAP = 12
PILL_H = 36

ICON_STYLE = "line"


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _fit(s: str, max_w: int, size: int, role: str = "regular") -> str:
    """超宽截断加省略号（保证不溢出面板）。"""
    if max_w <= 0:
        return ""
    if _w(s, size, role) <= max_w:
        return s
    while s and _w(s + "…", size, role) > max_w:
        s = s[:-1]
    return (s + "…") if s else ""


def _pal(t: Theme) -> dict[str, str]:
    """本卡生效配色：专属覆盖 > 硬编码默认。"""
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("doupo") if isinstance(ov.get("doupo"), dict) else {}
    defaults = {
        "page_color": C_PAGE, "card_color": C_CARD, "border_color": C_BORDER,
        "text_color": C_TEXT, "dim_color": C_DIM, "title_color": C_TITLE,
        "accent_color": C_ACCENT, "line_color": C_LINE, "track_color": C_TRACK,
        "bar_from": C_BAR_FROM, "bar_to": C_BAR_TO,
        "bar_ready_from": C_BAR_READY_FROM, "bar_ready_to": C_BAR_READY_TO,
        "pill_bg": C_PILL_BG, "pill_line": C_PILL_LINE, "pill_text": C_PILL_TEXT,
        "chip_bg": C_CHIP_BG, "chip_line": C_CHIP_LINE, "chip_text": C_CHIP_TEXT,
        "cell_bg": C_CELL_BG, "cell_line": C_CELL_LINE,
        "hp_from": C_HP_FROM, "hp_to": C_HP_TO, "mp_from": C_MP_FROM, "mp_to": C_MP_TO,
    }
    out: dict[str, str] = {}
    for k, d in defaults.items():
        s = v.get(k)
        out[k] = s if (isinstance(s, str) and s) else d
    return out


def _card_alpha(t: Theme) -> int:
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("doupo") if isinstance(ov.get("doupo"), dict) else None
    try:
        return max(0, min(100, int(v.get("card_alpha", 100)))) if v else 100
    except (TypeError, ValueError):
        return 100


# ---- 区块高度（_measure 与 build 必须同步）----
H_TITLE = FS_TITLE + 26          # 标题 + 分割线
H_NAME = FS_NAME + 16            # 名字行（含 chip 行高）
H_REALM = (FS_SMALL + 12) + (BAR_H + 18)   # 境界行 + 进度条
H_ALCH = PILL_H + 12             # 炼药师 pill
H_WEAPON = (FS_BODY + 12) + (FS_SMALL + 10)  # 武器名 + 武器四围
H_GRID = CELL_H * 2 + GAP + 16   # 六维 3×2 网格
H_HP = (FS_SMALL + 10) + (BAR_H + 10)       # 气血条
H_MP = (FS_SMALL + 10) + (BAR_H + 10)       # 斗气条
H_FOOT = FS_SMALL + 14           # 底部信息行


def _measure(_data: dict | None = None) -> int:
    return (PAD * 2 + H_TITLE + H_NAME + H_REALM + H_ALCH + H_WEAPON
            + H_GRID + H_HP + H_MP + H_FOOT)


def _statcell(c: Canvas, x: int, y: int, w: int, p: dict[str, str],
              value: str, label: str, size: int = FS_STAT) -> None:
    """六维单元：圆角底 + 大数值 + 小标签（数值按可用宽度自动降字号）。"""
    c.rounded_rect((x, y, x + w, y + CELL_H), radius=12,
                   fill=p["cell_bg"], outline=p["cell_line"], width=1)
    # 数值（上方，左对齐）
    avail = x + w - 20 - (x + 20)
    txt, sz = value, size
    while avail > 0 and _w(txt, sz, "regular") > avail and sz > 14:
        sz -= 2
        if _w(txt, sz, "regular") <= avail:
            break
    if _w(txt, sz, "regular") > avail:
        txt = _fit(txt, avail, sz, "regular")
    c.text((x + 20, y + 22), txt, size=sz, color=p["text_color"], role="regular", anchor="lm")
    # 标签（下方）
    c.text((x + 20, y + CELL_H - 20), label, size=FS_TINY, color=p["dim_color"], anchor="lm")


def _bar(c: Canvas, x: int, y: int, w: int, pct: int,
         c1: str, c2: str, track: str, radius: int = BAR_H // 2) -> None:
    """进度条：轨道 + 渐变填充（渐变左=c2 右=c1，见 xiuxian.py 方向坑注释）。"""
    pct = max(0, min(100, int(pct)))
    c.rounded_rect((x, y, x + w, y + BAR_H), radius=radius, fill=track)
    fill_w = int(w * pct / 100)
    if fill_w > 4:
        # ⚠️ 方向坑：gradient_rect(horizontal=True) 实测「左=c2、右=c1」，
        # 所以想要「左浅右深」必须交换实参（与 xiuxian.py 一致）。
        c.gradient_rect((x, y, x + fill_w, y + BAR_H),
                        c2, c1, radius=BAR_H // 2, horizontal=True)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    h = _measure(data)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    title = data.get("title") or "斗破大陆"
    tw = _w(title, FS_TITLE, "light")
    ico_w = FS_TITLE + 10
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, "sword", tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE,
               p["title_color"], style=ICON_STYLE)
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18  # = H_TITLE - (FS_TITLE + 8) 余量

    # ---- 角色名 + 境界 chip ----
    name = str(data.get("name") or "无名")
    name_show = _fit(name, content_w - 176, FS_NAME, "regular")
    c.text((x0, y + FS_NAME // 2), name_show, size=FS_NAME,
           color=p["text_color"], role="regular", anchor="lm")
    realm = str(data.get("realm") or "凡人")
    if realm:
        chip = realm
        cw = _w(chip, FS_TINY, "regular") + 28
        ch = FS_TINY + 16
        cx1 = x0 + content_w
        c.rounded_rect((cx1 - cw, y + FS_NAME // 2 - ch // 2, cx1, y + FS_NAME // 2 + ch // 2),
                       radius=ch // 2, fill=p["chip_bg"], outline=p["chip_line"], width=1)
        c.text((cx1 - cw // 2, y + FS_NAME // 2), chip, size=FS_TINY,
               color=p["chip_text"], anchor="mm")
    y += H_NAME

    # ---- 境界 + 修为 + 进度条 ----
    exp = int(data.get("exp") or 0)
    need = int(data.get("need") or 0)
    can_break = bool(data.get("can_break"))
    if data.get("realm_max") or need <= 0:
        exp_txt = f"{exp} 修为（已至巅峰）"
        pct = 100
    else:
        if can_break:
            exp_txt = f"{exp} / {need} 修为 · 可突破"
        else:
            exp_txt = f"{exp} / {need} 修为"
        pct = int(exp * 100 / need) if need else 0
    c.text((x0, y + FS_SMALL // 2), "境界修为", size=FS_SMALL,
           color=p["dim_color"], anchor="lm")
    c.text((x0 + content_w, y + FS_SMALL // 2), exp_txt, size=FS_SMALL,
           color=p["chip_text"] if can_break else p["dim_color"], anchor="rm")
    y += FS_SMALL + 12
    bar_c1, bar_c2 = ((p["bar_ready_from"], p["bar_ready_to"]) if can_break
                      else (p["bar_from"], p["bar_to"]))
    _bar(c, x0, y, content_w, pct, bar_c1, bar_c2, p["track_color"])
    y += BAR_H + 18  # 余量并入 H_REALM

    # ---- 炼药师 pill ----
    alch = str(data.get("alch_name") or "凡品炼药师")
    alch_exp = int(data.get("alch_exp") or 0)
    alch_need = int(data.get("alch_need") or 0)
    alch_txt = f"炼药 · {alch}　{alch_exp}/{alch_need}" if alch_need > 0 else f"炼药 · {alch}"
    pw = min(content_w, _w(alch_txt, FS_BODY + 1, "regular") + 44)
    ph = PILL_H
    c.rounded_rect((x0, y, x0 + pw, y + ph), radius=ph // 2,
                   fill=p["pill_bg"], outline=p["pill_line"], width=1)
    icons.draw(c, "diamond", x0 + 18, y + ph // 2, ICON, p["pill_text"], style=ICON_STYLE)
    c.text((x0 + 18 + ICON + 8, y + ph // 2), alch_txt, size=FS_BODY,
           color=p["pill_text"], anchor="lm")
    y += PILL_H + 12  # = H_ALCH

    # ---- 武器 ----
    wname = str(data.get("weapon_name") or "徒手")
    wlv = str(data.get("weapon_level") or "0")
    icons.draw(c, "sword", x0, y + FS_BODY // 2, FS_BODY, p["accent_color"], style=ICON_STYLE)
    c.text((x0 + ICON + 10, y + FS_BODY // 2), f"{wname}　{wlv} 级", size=FS_BODY,
           color=p["text_color"], role="regular", anchor="lm")
    y += FS_BODY + 12
    ws = data.get("weapon_stats") or {}
    wstat = "　".join(f"{k} {int(ws.get(k, 0))}" for k in ("力量", "敏捷", "斗气", "防御"))
    c.text((x0, y + FS_SMALL // 2), wstat, size=FS_SMALL,
           color=p["dim_color"], anchor="lm")
    y += FS_SMALL + 10  # = H_WEAPON

    # ---- 六维 3×2 网格 ----
    st = data.get("stats") or {}
    cells = [
        ("力量", str(int(st.get("力量", 0)))),
        ("敏捷", str(int(st.get("敏捷", 0)))),
        ("斗气", str(int(st.get("斗气", 0)))),
        ("防御", str(int(st.get("防御", 0)))),
        ("机缘", str(int(st.get("机缘", 0)))),
        ("体力", str(int(st.get("体力", 0)))),
    ]
    cell_gap = GAP
    cell_w = (content_w - cell_gap * 2) // 3
    grid_y = y
    for i, (lab, val) in enumerate(cells):
        col = i % 3
        row = i // 3
        cx = x0 + col * (cell_w + cell_gap)
        cy = grid_y + row * (CELL_H + cell_gap)
        _statcell(c, cx, cy, cell_w, p, val, lab, size=FS_STAT)
    y += H_GRID

    # ---- 气血 / 斗气 条 ----
    hp = int(data.get("hp") or 0)
    hp_max = int(data.get("hp_max") or 0) or 1
    mp = int(data.get("mp") or 0)
    mp_max = int(data.get("mp_max") or 0) or 1

    c.text((x0, y + FS_SMALL // 2), "气血", size=FS_SMALL,
           color=p["dim_color"], anchor="lm")
    c.text((x0 + content_w, y + FS_SMALL // 2), f"{hp}/{hp_max}", size=FS_SMALL,
           color=p["dim_color"], anchor="rm")
    y += FS_SMALL + 10
    _bar(c, x0, y, content_w, int(hp * 100 / hp_max), p["hp_from"], p["hp_to"], p["track_color"])
    y += BAR_H + 10  # = H_HP

    c.text((x0, y + FS_SMALL // 2), "斗气", size=FS_SMALL,
           color=p["dim_color"], anchor="lm")
    c.text((x0 + content_w, y + FS_SMALL // 2), f"{mp}/{mp_max}", size=FS_SMALL,
           color=p["dim_color"], anchor="rm")
    y += FS_SMALL + 10
    _bar(c, x0, y, content_w, int(mp * 100 / mp_max), p["mp_from"], p["mp_to"], p["track_color"])
    y += BAR_H + 10  # = H_MP

    # ---- 底部信息行 ----
    gold = int(data.get("gold") or 0)
    stone = int(data.get("stone") or 0)
    power = int(data.get("power") or 0)
    wins = int(data.get("wins") or 0)
    losses = int(data.get("losses") or 0)
    foot = f"金币 {gold}　炎星石 {stone}　战力 {power}　战绩 {wins}胜{losses}负"
    c.text((x0, y + FS_SMALL // 2), _fit(foot, content_w, FS_SMALL),
           size=FS_SMALL, color=p["text_color"], anchor="lm")
    y += H_FOOT

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=FS_TINY,
               color=p["dim_color"], anchor="rb")

    return c
