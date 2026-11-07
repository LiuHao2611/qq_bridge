# -*- coding: utf-8 -*-
"""pic/templates/xiuxian.py —— 文字修仙 · 角色状态卡。

用于群聊「我要修仙 / 我的修仙」的卡片化回复，替代原先的多行纯文本消息。

版式（浅色卡，与 bounty.py 同一套 Rabbit 极简观感）：
  * 顶部居中标题「修仙路」+ wand 细描边图标 + 贯通分割线；
  * 角色名（左）+ 闭关状态 chip（右）；
  * 境界行（境界名 左 / 修为 x/y 右）+ 青绿渐变进度条；
    修为溢出（exp > need，可突破）时：进度条转**暖金** + 右侧标注「· 可突破」+ 底部提示改喊突破；
  * 灵根淡紫 pill（灵根名 + 修炼倍率）；
  * 四宫格数值：战力 / 灵石 / 寿元 / 战绩（数值大、标签小）；
  * 宗门 / 背包信息行；
  * 底部操作提示。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * 全部文案用中文真字体绘制，**不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色可经 ``Theme.bg_overrides["xiuxian"]`` 覆盖，字段见 :func:`_pal`。

输入（build 的 params）：
  name       角色名
  title      标题，默认「修仙路」
  realm      境界名
  exp / need 当前修为 / 升级所需
  realm_max  是否已至巅峰（True 时修为显示 x(已至巅峰)，进度条置满）
  root       灵根名（如「单灵根」）
  root_mult  灵根倍率（如 1.3）
  power      战力
  ling_shi   灵石
  life_left  剩余寿元（年）
  age        当前年龄
  wins/losses 战绩
  sect       宗门名（空 = 无）
  secluding  是否闭关中
  inv        背包文本（已拼好的字符串）
  tip        底部提示（已拼好，可留空）
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# ---- 固定配色（修仙：竹青 / 月白 / 墨）——未配置专属覆盖时的默认值 ----
C_PAGE = "#EEF2EF"    # 页面外背景（淡青灰）
C_CARD = "#FFFFFF"    # 卡片底
C_BORDER = "#E1E7E3"  # 卡片描边
C_TEXT = "#22322B"    # 主文字（墨绿黑）
C_DIM = "#5F6F67"     # 次文字
C_TITLE = "#1F5F4B"   # 标题深竹绿
C_ACCENT = "#2E8B6E"  # 主强调（青绿）
C_LINE = "#DCE5E0"    # 分割线
C_TRACK = "#DFE8E2"   # 进度条轨道
C_BAR_FROM = "#7FD1B3"  # 进度条渐变起
C_BAR_TO = "#2E8B6E"    # 进度条渐变止
# 修为已满（可突破）：暖金——与"还在修炼"的青绿明确区分，一眼看出该突破了
C_BAR_READY_FROM = "#F5C978"
C_BAR_READY_TO = "#D98A2B"

# 灵根 pill（淡紫，表示天赋稀有）
C_PILL_BG = "#F4E9F7"
C_PILL_LINE = "#D4B7DD"
C_PILL_TEXT = "#5B2C6F"

# 状态 chip（闭关中：暖橙）
C_CHIP_BG = "#FFF1E2"
C_CHIP_LINE = "#F2C79A"
C_CHIP_TEXT = "#B35C00"

# 四宫格面板底
C_CELL_BG = "#F6F9F7"
C_CELL_LINE = "#E3EAE6"

# ---- 排版 ----
PAD = 26
FS_TITLE = 32
FS_NAME = 26
FS_BODY = 20
FS_SMALL = 16
FS_TINY = 14
FS_STAT = 30
ICON = 20
BAR_H = 12
CELL_H = 78
GAP = 10

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
    v = ov.get("xiuxian") if isinstance(ov.get("xiuxian"), dict) else {}
    defaults = {
        "page_color": C_PAGE, "card_color": C_CARD, "border_color": C_BORDER,
        "text_color": C_TEXT, "dim_color": C_DIM, "title_color": C_TITLE,
        "accent_color": C_ACCENT, "line_color": C_LINE, "track_color": C_TRACK,
        "bar_from": C_BAR_FROM, "bar_to": C_BAR_TO,
        "bar_ready_from": C_BAR_READY_FROM, "bar_ready_to": C_BAR_READY_TO,
        "pill_bg": C_PILL_BG, "pill_line": C_PILL_LINE, "pill_text": C_PILL_TEXT,
        "chip_bg": C_CHIP_BG, "chip_line": C_CHIP_LINE, "chip_text": C_CHIP_TEXT,
        "cell_bg": C_CELL_BG, "cell_line": C_CELL_LINE,
    }
    out: dict[str, str] = {}
    for k, d in defaults.items():
        s = v.get(k)
        out[k] = s if (isinstance(s, str) and s) else d
    return out


def _card_alpha(t: Theme) -> int:
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("xiuxian") if isinstance(ov.get("xiuxian"), dict) else None
    try:
        return max(0, min(100, int(v.get("card_alpha", 100)))) if v else 100
    except (TypeError, ValueError):
        return 100


def _autosize(s: str, max_w: int, sizes: tuple[int, ...]) -> tuple[str, int]:
    """按可用宽度自动降字号；最小字号仍放不下才截断。

    存在的理由：灵石/战力可能到 8 位数字（实测 88888888 会顶出格子边界把
    右侧"寿元"格压花），战绩"999 胜 1 负"同理。固定字号无法覆盖，必须自适应。
    灵石是消耗品，精确值比"1.2万"这类缩写更重要 → 优先降字号而非截断，
    因此档位一直降到 14px（再小则不可读）。
    """
    s = str(s)
    cand = [int(x) for x in sizes if int(x) >= 14]
    if not cand:
        cand = [14]
    for sz in cand:
        if _w(s, sz, "regular") <= max_w:
            return s, sz
    sz = cand[-1]
    return _fit(s, max_w, sz, "regular"), sz


def _measure(data: dict) -> int:
    """总高度（与 build 同套间距）。"""
    y = PAD
    y += FS_TITLE + 26           # 标题 + 分割线
    y += FS_NAME + 14            # 名字行（含 chip 行高）
    y += FS_SMALL + 12           # 境界行
    y += BAR_H + 18              # 进度条
    y += 34 + 12                 # 灵根 pill
    y += CELL_H + 16             # 四宫格
    # 攻 / 防 / 暴击 属性行（与 build 同步：三字段全缺则不占高度）
    if any(data.get(k) is not None for k in ("atk", "dfn", "crit_pct")):
        y += FS_SMALL + 10
    y += (FS_SMALL + 10) * 2     # 宗门 / 背包
    y += 8 + 1 + 12              # 分割线
    y += FS_SMALL + PAD          # 底部提示
    return y


def _cell(c: Canvas, x: int, y: int, w: int, p: dict[str, str],
          value: str, label: str, icon: str, size: int = FS_STAT) -> None:
    """四宫格单元：图标 + 大数值 + 小标签（数值按可用宽度自动降字号）。"""
    c.rounded_rect((x, y, x + w, y + CELL_H), radius=12,
                   fill=p["cell_bg"], outline=p["cell_line"], width=1)
    cy = y + 26
    tx = x + 22
    if icons.exists(icon):
        icons.draw(c, icon, x + 22, cy, ICON, p["accent_color"], style=ICON_STYLE)
        tx = x + 22 + ICON + 8
    # 可用宽度 = 格子右边界 - 右内边距 - 文字起点
    avail = x + w - 14 - tx
    txt, sz = _autosize(value, avail, (size, size - 4, size - 8, size - 12, size - 14))
    c.text((tx, cy), txt, size=sz, color=p["text_color"], role="regular", anchor="lm")
    c.text((x + 20, y + CELL_H - 20), label, size=FS_TINY, color=p["dim_color"], anchor="lm")


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    h = _measure(data)

    c = Canvas(width, h, p["page_color"], theme=t)
    # 卡片底（12px 外边距 + 24 圆角，与其它卡片一致）
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    title = data.get("title") or "修仙路"
    tw = _w(title, FS_TITLE, "light")
    ico_w = FS_TITLE + 10
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, "wand", tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE,
               p["title_color"], style=ICON_STYLE)
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 角色名 + 状态 chip ----
    name = str(data.get("name") or "无名")
    name_show = _fit(name, content_w - 176, FS_NAME, "regular")
    c.text((x0, y + FS_NAME // 2), name_show, size=FS_NAME,
           color=p["text_color"], role="regular", anchor="lm")
    if data.get("secluding"):
        chip = "闭关中 · 修为×2"
        cw = _w(chip, FS_TINY, "regular") + 20
        ch = FS_TINY + 14
        cx1 = x0 + content_w
        c.rounded_rect((cx1 - cw, y + FS_NAME // 2 - ch // 2, cx1, y + FS_NAME // 2 + ch // 2),
                       radius=ch // 2, fill=p["chip_bg"], outline=p["chip_line"], width=1)
        c.text((cx1 - cw // 2, y + FS_NAME // 2), chip, size=FS_TINY,
               color=p["chip_text"], anchor="mm")
    y += FS_NAME + 14

    # ---- 境界 + 修为 + 进度条 ----
    realm = str(data.get("realm") or "凡人")
    exp = int(data.get("exp") or 0)
    need = int(data.get("need") or 0)
    can_break = bool(data.get("can_break"))
    cd_minutes = int(data.get("cd_minutes") or 0)
    full = need > 0 and exp >= need
    rate_pct = int(data.get("rate_pct") or 0)
    if data.get("realm_max") or need <= 0:
        exp_txt = f"{exp} 修为（已至巅峰）"
        pct = 100
    else:
        # 修为够但破镜在冷却（开源 level_up_cd=60 分钟）时说清"还要调息多久"，
        # 否则满格条 + 无论怎么点都没反应，会被当成 bug。
        if can_break:
            suffix = f" · 可突破（{rate_pct}%）" if rate_pct else " · 可突破"
        elif full and cd_minutes:
            suffix = f" · 调息 {cd_minutes} 分钟"
            if rate_pct:
                suffix += f"｜成功率 {rate_pct}%"
        else:
            # 平时也给出"突破率"，让玩家知道离破境有多大把握（这才是修仙者最想看的数）
            suffix = f" · 突破率 {rate_pct}%" if rate_pct else ""
        exp_txt = f"{exp} / {need} 修为" + suffix
        pct = int(exp * 100 / need) if need else 0
    c.text((x0, y + FS_SMALL // 2), f"境界 · {realm}", size=FS_SMALL,
           color=p["dim_color"], anchor="lm")
    c.text((x0 + content_w, y + FS_SMALL // 2), exp_txt, size=FS_SMALL,
           color=p["chip_text"] if (can_break or full) else p["dim_color"], anchor="rm")
    y += FS_SMALL + 12

    bar_c1, bar_c2 = ((p["bar_ready_from"], p["bar_ready_to"]) if (can_break or full)
                      else (p["bar_from"], p["bar_to"]))
    c.rounded_rect((x0, y, x0 + content_w, y + BAR_H), radius=BAR_H // 2, fill=p["track_color"])
    fill_w = int(content_w * max(0, min(100, pct)) / 100)
    if fill_w > 4:
        # ⚠️ 方向坑：canvas.linear_gradient(horizontal=True) 实测是「左=c2、右=c1」
        # （见 widgets.py 坑 1，2026-09-14 实测），所以想要「左浅右深」必须**交换实参**。
        # 直接传 (bar_from, bar_to) 会得到右端变浅的条 —— 满格时看着像没填满，
        # 2026-10-26 用户截图报障的就是这个。
        c.gradient_rect((x0, y, x0 + fill_w, y + BAR_H),
                        bar_c2, bar_c1, radius=BAR_H // 2, horizontal=True)
    y += BAR_H + 18

    # ---- 灵根 pill ----
    root = str(data.get("root") or "凡体")
    mult = data.get("root_mult")
    pill_txt = f"灵根 · {root}" + (f"　修炼 ×{mult}" if mult not in (None, "") else "")
    pw = min(content_w, _w(pill_txt, FS_BODY + 1, "regular") + 44)
    ph = 34
    c.rounded_rect((x0, y, x0 + pw, y + ph), radius=ph // 2,
                   fill=p["pill_bg"], outline=p["pill_line"], width=1)
    icons.draw(c, "star", x0 + 18, y + ph // 2, ICON, p["pill_text"], style=ICON_STYLE)
    c.text((x0 + 18 + ICON + 8, y + ph // 2), pill_txt, size=FS_BODY,
           color=p["pill_text"], anchor="lm")
    y += ph + 12

    # ---- 四宫格 ----
    cell_gap = 10
    cell_w = (content_w - cell_gap * 3) // 4
    life_left = data.get("life_left")
    life_txt = f"{int(life_left)} 年" if life_left is not None else "—"
    cells = [
        ("sword", str(data.get("power", 0)), "战力", FS_STAT),
        ("diamond", str(data.get("ling_shi", 0)), "灵石", FS_STAT),
        ("clock", life_txt, "寿元", FS_STAT),
        # 战绩文案天然更长（"27胜 9负"），起始字号降一档，再由 _autosize 兜底
        ("check", f"{data.get('wins', 0)}胜 {data.get('losses', 0)}负", "战绩", FS_BODY),
    ]
    for i, (ico, val, lab, sz) in enumerate(cells):
        _cell(c, x0 + i * (cell_w + cell_gap), y, cell_w, p, val, lab, ico, size=sz)
    y += CELL_H + 16

    # ---- 攻 / 防 / 暴击（把"战力"这一个合成值拆开，看得见强弱来源）----
    atk = data.get("atk")
    dfn = data.get("dfn")
    crit = data.get("crit_pct")
    if atk is not None or dfn is not None or crit is not None:
        attr_txt = (f"攻击 {int(atk or 0)} · 防御 {int(dfn or 0)} · 暴击 {int(crit or 0)}%")
        c.text((x0, y + FS_SMALL // 2), _fit(attr_txt, content_w, FS_SMALL),
               size=FS_SMALL, color=p["dim_color"], anchor="lm")
        y += FS_SMALL + 10

    # ---- 宗门 / 背包 ----
    sect = str(data.get("sect") or "无")
    for label, val in (("宗门", sect), ("背包", str(data.get("inv") or "空"))):
        cy2 = y + FS_SMALL // 2
        # 统一用 6px 实心小圆点作 bullet。
        # 不用 icons 的 book：line 风格在 16px 下画出来是个空方框，辨识度极低（实测图确认），
        # 且两行分别用不同图标会让缩进不一致。
        c.rounded_rect((x0 + 2, cy2 - 3, x0 + 8, cy2 + 3), radius=3, fill=p["dim_color"])
        c.text((x0 + 18, cy2), label, size=FS_SMALL, color=p["dim_color"], anchor="lm")
        vx = x0 + 18 + _w(label, FS_SMALL) + 10
        val_show = _fit(val, x0 + content_w - vx, FS_SMALL, "regular")
        c.text((vx, cy2), val_show, size=FS_SMALL, color=p["text_color"], anchor="lm")
        y += FS_SMALL + 10

    # ---- 分割线 + 底部提示 ----
    y += 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip is None:
        if can_break:
            nxt = data.get("next_realm") or "下一境界"
            tip = f"修为已满 · 发送「突破」冲击【{nxt}】"
        elif full and cd_minutes:
            tip = f"修为已满 · 破镜需调息，还需 {cd_minutes} 分钟"
        elif data.get("secluding"):
            tip = "闭关中 · 发送「出关」结算修为"
        else:
            tip = "发送「闭关」打坐修炼（不闭关不涨）·「突破」冲境界"
    if tip:
        icons.draw(c, "bolt", x0 + 8, y + FS_SMALL // 2, 16, p["accent_color"], style=ICON_STYLE)
        c.text((x0 + 26, y + FS_SMALL // 2), _fit(tip, content_w - 26, FS_SMALL),
               size=FS_SMALL, color=p["dim_color"], anchor="lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=FS_TINY,
               color=p["dim_color"], anchor="rb")

    return c
