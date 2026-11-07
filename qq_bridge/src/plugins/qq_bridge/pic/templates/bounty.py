# -*- coding: utf-8 -*-
"""pic/templates/bounty.py —— Warframe 赏金任务一览专属卡片。

照 Rabbit「赏金任务一览」卡：
  * 浅色干净底（聚合总览卡，无右下角战甲立绘——立绘仅关联"奖励轮换"单一区块，
    对整张总览增量有限且占用版面，已移除）；
  * 居中大标题（带 globe 图标）+ 贯通分割线；
  * 赏金轮换：左右两个圆角 pill，含奖励图标/缩略图；
  * 地球/金星/火卫二：MOD/债券列表 + 带图标的任务行；
  * 深矿 + 扎里曼/圣所/六人组：派系图标 + 任务行；
  * 底部剩余时间（clock 图标）+ 来源脚注。

2026-10-12 视觉整改（用户反馈）：
  * 去加粗：标题/区段改用 ``role="light"``（微软雅黑细体），正文用 ``regular``，
    SpaceX 式极简观感；
  * 图标全部 ``style="line"`` 细描边线框（SpaceX 极简线条设计风，纯 Pillow 几何自绘，
    零外部依赖，不爬取任何外链——项目铁律禁止浏览器/渲染引擎依赖）；
  * 内容行（世界任务 / 派系任务 / 深矿）用淡紫圆角气泡包裹（模仿 netracell 气泡观感）。

2026-10-12 后期（用户判定）：移除右下角战甲立绘
  * 立绘仅关联"奖励轮换"单一区块，对聚合总览卡增量有限且需预留右侧栏挤窄内容，判定为装饰性冗余；
  * 删除 ``_portrait_size``、``PORTRAIT_MAX``/``PORTRAIT_GUTTER`` 及 ``build`` 中立绘绘制块与 gutter 逻辑，
    内容走满宽（``cw = content_w``），页脚恢复右下角；数据源 ``warframe.py`` 的 ``portrait`` 字段未改动。

输入（build 的 params）：
  data: {
      "title": "赏金任务一览",
      "remain": "1小时24分23秒",
      "rotation": {"rot":"C","rot_rewards":[...],"vault":"B","vault_rewards":[...]},
      "worlds": [{"name":"地球赏金","label":"MOD奖励轮换","items":[...],"jobs":[...]}],
      "deep": {"name":"深矿赏金","tag":"[钢铁之路]","title":"花园除草","detail":"...","icon":"bolt","color":"#F44336"},
      "factions": [{"name":"扎里曼赏金","icon":"star","color":"#FFC107","jobs":[...]}],
      "portrait": "<PNG path>" | None
  }
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .. import assets, decor, icons
from ..canvas import Canvas
from ..theme import Theme

# ---- 固定配色（照 Rabbit 浅色卡）——未配置专属覆盖时的默认值 ----
C_PAGE = "#F0F2F5"   # 页面外背景
C_CARD = "#FFFFFF"   # 卡片底
C_BORDER = "#E1E4E8" # 卡片描边
C_TEXT = "#2B3A4A"   # 主文字
C_DIM = "#5D6D7E"    # 次文字
C_TITLE = "#1F4E79"  # 标题深蓝
C_ACCENT = "#8E44AD" # 主强调色（粉紫）
C_LINE = "#D9DEE6"   # 分割线

# 轮换 pill 配色
C_PILL_BG = "#F3E5F5"   # 淡紫底
C_PILL_LINE = "#CE93D8" # 紫描边
C_PILL_TEXT = "#4A148C" # 紫字

# 标签（MOD奖励轮换 / 债券轮换 / 隔离库轮换）
C_LABEL_BG = "#FFF3E0"   # 淡橙底
C_LABEL_LINE = "#FFCC80" # 橙描边
C_LABEL_TEXT = "#E65100" # 橙字

# 内容气泡（SpaceX 极简：中性近乎透明底 + 1px 细描边，去除 pastel 软填充）
C_BUBBLE_BG = "#F4F6F9"   # 气泡中性近白底（无 pastel）
C_BUBBLE_LINE = "#D7DCE3" # 气泡中性细描边

# ---- 排版 ----
PAD = 28
FS_TITLE = 34
FS_SECT = 22
FS_BODY = 20
FS_SMALL = 16
FS_TINY = 14
LINE_H = 34
PILL_H = 64
PILL_PAD_X = 16
ROW_ICON = 22

# ---- 图标 / 气泡风格（2026-10-12 视觉整改 → 10-12 深度调研后收紧）----
ICON_STYLE = "line"   # 全部图标细描边线框（SpaceX 式 monoline）
BUB_RADIUS = 6        # 气泡圆角（贴近 SpaceX 结构 4px 级，克制不软）
BUB_PAD_X = 14        # 气泡内左右留白
BUB_PAD_Y = 6         # 气泡内上下留白
BUB_GAP = 8           # 气泡垂直间距
# 气泡高度（内容 + 上下留白）
BUB_ROW_H = LINE_H + 2 * BUB_PAD_Y        # 单行任务行气泡
BUB_FAC_H = LINE_H * 2 + 2 + 2 * BUB_PAD_Y  # 派系两行气泡
BUB_DEEP_H = (LINE_H + 2) + (FS_BODY + 6) + 2 * BUB_PAD_Y  # 深矿块气泡


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _fit(s: str, max_w: int, size: int, role: str = "regular") -> str:
    """超宽则截断加省略号（保证文本不溢出气泡）。"""
    if _w(s, size, role) <= max_w:
        return s
    while s and _w(s + "…", size, role) > max_w:
        s = s[:-1]
    return s + "…"


def _pal(t: Theme) -> dict[str, str]:
    """本卡生效配色：专属覆盖 > 硬编码默认。"""
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("bounty") if isinstance(ov.get("bounty"), dict) else {}
    defaults = {
        "page_color": C_PAGE, "card_color": C_CARD, "border_color": C_BORDER,
        "text_color": C_TEXT, "dim_color": C_DIM, "title_color": C_TITLE,
        "accent_color": C_ACCENT, "line_color": C_LINE,
        "pill_bg": C_PILL_BG, "pill_line": C_PILL_LINE, "pill_text": C_PILL_TEXT,
        "label_bg": C_LABEL_BG, "label_line": C_LABEL_LINE, "label_text": C_LABEL_TEXT,
        "bubble_bg": C_BUBBLE_BG, "bubble_line": C_BUBBLE_LINE,
    }
    out: dict[str, str] = {}
    for k, d in defaults.items():
        s = v.get(k)
        out[k] = s if (isinstance(s, str) and s) else d
    return out


def _card_alpha(t: Theme) -> int:
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("bounty") if isinstance(ov.get("bounty"), dict) else None
    try:
        return max(0, min(100, int(v.get("card_alpha", 100)))) if v else 100
    except (TypeError, ValueError):
        return 100


def _load_image(path: str | None, size: int) -> Image.Image | None:
    """加载本地图片并缩放到 size×size 以内（保持比例），失败返回 None。

    路径是真实绝对路径（来自 weapon_icons.icon_cache_path / portraits 目录），
    直接 Image.open 打开；assets.load 仅作为后备（其白名单只接受 uuid 文件名，
    对真实物品图路径必然失败，故不依赖它）。
    """
    if not path:
        return None
    img = None
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001
        try:
            img = assets.load(path)
        except Exception:  # noqa: BLE001
            img = None
    if img is None or getattr(img, "width", 0) <= 0 or getattr(img, "height", 0) <= 0:
        return None
    # 裁掉透明边距
    bb = img.getchannel("A").getbbox()
    if bb:
        img = img.crop(bb)
    # 等比缩放，最大边 = size
    scale = min(size / img.width, size / img.height, 1.0)
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img


def _draw_icon_or_image(c: Canvas, x: int, y: int, size: int,
                        icon_name: str, icon_color: str,
                        image_path: str | None = None,
                        style: str = ICON_STYLE) -> None:
    """优先画本地图片，缺失则画几何图标（细描边风）。"""
    img = _load_image(image_path, size)
    if img is not None:
        c.img.paste(img, (x, y - img.height // 2 + size // 2), img)
    elif icons.exists(icon_name):
        icons.draw(c, icon_name, x + size // 2, y + size // 2, size, icon_color, style=style)


def _bubble(c: Canvas, x0: int, y0: int, w: int, h: int, p: dict[str, str]) -> None:
    """淡紫圆角气泡包裹：极淡底 + 同色系细描边。"""
    c.rounded_rect((x0, y0, x0 + w, y0 + h), radius=BUB_RADIUS,
                   fill=p["bubble_bg"], outline=p["bubble_line"], width=1)


def _pill(c: Canvas, x0: int, y0: int, w: int, h: int,
          icon_name: str, icon_color: str, title: str, subtitle: str,
          image_path: str | None, p: dict[str, str]) -> None:
    """圆角 pill：左侧图标/图 + 右上标题 + 右下小字。"""
    c.rounded_rect((x0, y0, x0 + w, y0 + h), radius=16,
                   fill=p["pill_bg"], outline=p["pill_line"], width=1)
    ico_size = 32
    _draw_icon_or_image(c, x0 + PILL_PAD_X, y0 + h // 2 - ico_size // 2,
                        ico_size, icon_name, icon_color, image_path)
    tx = x0 + PILL_PAD_X + ico_size + 10
    c.text((tx, y0 + h // 2 - 2), title, size=FS_BODY, color=p["pill_text"],
           role="regular", anchor="lm")
    if subtitle:
        c.text((tx, y0 + h // 2 + 18), subtitle, size=FS_TINY, color=p["dim_color"], anchor="lm")


def _chip(c: Canvas, x0: int, y0: int, text: str, color: str, p: dict[str, str]) -> int:
    """画一个标签 chip，返回占用宽度。"""
    pad_x = 10
    pad_y = 5
    fs = FS_SMALL
    tw = _w(text, fs, "regular")
    w = tw + pad_x * 2
    h = fs + pad_y * 2
    # 淡色底 + 同色系描边
    c.rounded_rect((x0, y0, x0 + w, y0 + h), radius=h // 2,
                   fill=color, outline=color, width=1, fill_alpha=20)
    c.text((x0 + w // 2, y0 + h // 2), text, size=fs, color=color, role="regular", anchor="mm")
    return w


def _row(c: Canvas, x0: int, y0: int, content_w: int,
         job: dict, p: dict[str, str]) -> int:
    """画一行任务（淡紫气泡包裹）：左侧图标 + 等级 + 类型 + 任务名。返回占用高度。"""
    _bubble(c, x0, y0, content_w, BUB_ROW_H, p)
    bx = x0 + BUB_PAD_X
    icon = job.get("icon", "circle")
    color = job.get("color", p["accent_color"])
    # 正文图标：SpaceX 式单色 monoline（统一 p["text_color"]，不再各派系彩虹配色）
    icons.draw(c, icon, bx + ROW_ICON // 2, y0 + BUB_ROW_H // 2, ROW_ICON, p["text_color"], style=ICON_STYLE)
    tx = bx + ROW_ICON + 10
    lvl = job.get("lvl", "")
    typ = job.get("type", "")
    nm = job.get("name", "")
    narmer = " 合一众" if job.get("narmer") else ""
    max_text_w = content_w - (BUB_PAD_X + ROW_ICON + 10) - BUB_PAD_X
    # 等级用强调色
    if lvl:
        seg = f"[等级 {lvl}] "
        c.text((tx, y0 + BUB_ROW_H // 2), seg, size=FS_BODY,
               color=p["accent_color"], role="regular", anchor="lm")
        tx += _w(seg, FS_BODY, "regular")
    # 类型
    if typ:
        seg = typ + " "
        c.text((tx, y0 + BUB_ROW_H // 2), seg, size=FS_BODY, color=color, role="regular", anchor="lm")
        tx += _w(seg, FS_BODY, "regular")
    # 任务名（过长截断）
    rest = _fit(nm + narmer, max(0, max_text_w - (tx - bx)), FS_BODY, "regular")
    c.text((tx, y0 + BUB_ROW_H // 2), rest, size=FS_BODY, color=p["text_color"], anchor="lm")
    return BUB_ROW_H


def _faction_row(c: Canvas, x0: int, y0: int, content_w: int,
                 job: dict, p: dict[str, str]) -> int:
    """派系赏金行（淡紫气泡包裹）：节点名 + 下一行 任务：类型 目标。返回占用高度。"""
    _bubble(c, x0, y0, content_w, BUB_FAC_H, p)
    bx = x0 + BUB_PAD_X
    icon = job.get("icon", "circle")
    color = job.get("color", p["accent_color"])
    # 正文图标：SpaceX 式单色 monoline（统一 p["text_color"]）
    icons.draw(c, icon, bx + ROW_ICON // 2, y0 + BUB_PAD_Y + LINE_H // 2,
               ROW_ICON, p["text_color"], style=ICON_STYLE)
    tx = bx + ROW_ICON + 10
    lvl = job.get("lvl")
    nm = job.get("name", "")
    typ = job.get("type", "")
    tgt = job.get("target", "")
    max_text_w = content_w - (BUB_PAD_X + ROW_ICON + 10) - BUB_PAD_X
    line1 = f"[等级 {lvl}] {nm}" if lvl else nm
    line1 = _fit(line1, max(0, max_text_w), FS_BODY, "regular")
    c.text((tx, y0 + BUB_PAD_Y + LINE_H // 2), line1, size=FS_BODY, color=p["text_color"], anchor="lm")
    y1 = y0 + BUB_PAD_Y + LINE_H + 2
    line2 = _fit(f"任务：{typ} {tgt}", max(0, max_text_w), FS_SMALL, "regular")
    c.text((tx + 8, y1 + FS_SMALL // 2), line2, size=FS_SMALL,
           color=p["dim_color"], anchor="lm")
    return BUB_FAC_H


def _measure(data: dict, content_w: int) -> int:
    """总高度测量（与 build 同套间距，保证气泡参与排版流）。"""
    y = PAD
    y += FS_TITLE + 26  # 标题 + 分割线（build: +8 标题 +18 分割）

    # 轮换
    y += FS_SECT + 10
    y += PILL_H + 10  # pills
    rot = data.get("rotation") or {}
    if (rot.get("vault_rewards") or rot.get("rot_rewards")):
        y += FS_BODY + 6  # vault/extra line
    y += LINE_H + 8  # remain
    y += 12

    # 世界
    for w in data.get("worlds") or []:
        y += FS_SECT + 10
        if w.get("label"):
            y += FS_SMALL + 12  # label chip
            y += FS_BODY + 6    # items line
        for _ in w.get("jobs") or []:
            y += BUB_ROW_H + BUB_GAP
        y += 12

    # 深矿
    deep = data.get("deep") or {}
    if deep:
        y += FS_SECT + 8
        y += BUB_DEEP_H + BUB_GAP
        y += 12

    # 派系
    for f in data.get("factions") or []:
        y += FS_SECT + 8
        for _ in f.get("jobs") or []:
            y += BUB_FAC_H + BUB_GAP
        y += 8

    # 底部
    y += LINE_H + 10
    y += PAD + 10
    return y


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 800) or 800)
    content_w = width - PAD * 2
    p = _pal(t)

    # 内容走满宽（已移除右下角战甲立绘，不再预留右侧栏）
    cw = content_w

    h = _measure(data, content_w)
    c = Canvas(width, h, p["page_color"], theme=t)
    decor.background(c, t, tpl="bounty", only_override=True)
    card_x0, card_y0 = 12, 12
    ca = _card_alpha(t)
    c.rounded_rect((card_x0, card_y0, width - card_x0, h - card_y0),
                   radius=24, fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=ca * 255 // 100)

    x0 = PAD
    y = PAD

    # ---- 标题（细体，去加粗）----
    title = data.get("title") or "赏金任务一览"
    tw = _w(title, FS_TITLE, "light")
    tx = x0 + (content_w - tw) // 2
    if icons.exists("globe"):
        ico_w = FS_TITLE + 8
        tx -= ico_w // 2
        icons.draw(c, "globe", tx + FS_TITLE // 2, y + FS_TITLE // 2, FS_TITLE, p["title_color"], style=ICON_STYLE)
        tx += ico_w
    c.text((tx, y + FS_TITLE // 2), title, size=FS_TITLE, color=p["title_color"],
           role="light", anchor="lm")
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 赏金轮换 ----
    rot = data.get("rotation") or {}
    c.text((x0, y), "赏金轮换", size=FS_SECT, color=p["title_color"], role="light", anchor="lm")
    y += FS_SECT + 10

    rot_rewards = rot.get("rot_rewards") or []
    vault_rewards = rot.get("vault_rewards") or []
    pill_w = (cw - 12) // 2
    pill_y = y
    # 左 pill：首个轮换奖励
    if rot_rewards:
        r0 = rot_rewards[0]
        _pill(c, x0, pill_y, pill_w, PILL_H,
              r0.get("fallback", "star"), p["accent_color"],
              f"合一众轮换 {rot.get('rot', '?')}", r0.get("name", ""),
              r0.get("image"), p)
    # 右 pill：优先保险库；无保险库则第二个轮换奖励
    right_pill_used = False
    if vault_rewards:
        v0 = vault_rewards[0]
        _pill(c, x0 + pill_w + 12, pill_y, pill_w, PILL_H,
              v0.get("fallback", "sword"), p["accent_color"],
              f"保险库 {rot.get('vault', '?')}", v0.get("name", ""),
              v0.get("image"), p)
        right_pill_used = True
    elif len(rot_rewards) > 1:
        r1 = rot_rewards[1]
        _pill(c, x0 + pill_w + 12, pill_y, pill_w, PILL_H,
              r1.get("fallback", "star"), p["accent_color"],
              f"合一众轮换 {rot.get('rot', '?')}", r1.get("name", ""),
              r1.get("image"), p)
        right_pill_used = True
    y += PILL_H + 10

    # 额外的轮换/保险库奖励（单行小字）
    extras: list[str] = []
    if len(rot_rewards) > 1:
        if vault_rewards and right_pill_used:
            extras += [r.get("name", "") for r in rot_rewards[1:]]
        elif right_pill_used:
            extras += [r.get("name", "") for r in rot_rewards[2:]]
        else:
            extras += [r.get("name", "") for r in rot_rewards[1:]]
    if vault_rewards and right_pill_used:
        extras += [v.get("name", "") for v in vault_rewards[1:]]
    elif vault_rewards and not right_pill_used:
        extras += [v.get("name", "") for v in vault_rewards]
    extras = [e for e in extras if e]
    if extras:
        c.text((x0, y), " · ".join(extras), size=FS_BODY, color=p["dim_color"], anchor="lm")
        y += FS_BODY + 6

    # 剩余时间
    remain = data.get("remain", "")
    if remain:
        icons.draw(c, "clock", x0 + 10, y + LINE_H // 2, FS_BODY, p["dim_color"], style=ICON_STYLE)
        c.text((x0 + FS_BODY + 16, y + LINE_H // 2), f"剩余时间：{remain}",
               size=FS_BODY, color=p["dim_color"], anchor="lm")
        y += LINE_H + 8
    y += 12

    # ---- 世界赏金 ----
    for w in data.get("worlds") or []:
        c.text((x0, y), w.get("name", ""), size=FS_SECT, color=p["title_color"], role="light", anchor="lm")
        y += FS_SECT + 10
        if w.get("label"):
            # 标签 chip
            _chip(c, x0, y, w["label"], p["label_text"], p)
            y += FS_SMALL + 12
            # items
            items = w.get("items") or []
            if items:
                c.text((x0, y), " · ".join(items), size=FS_SMALL, color=p["dim_color"], anchor="lm")
                y += FS_BODY + 6
        for j in w.get("jobs") or []:
            y += _row(c, x0, y, cw, j, p) + BUB_GAP
        y += 12

    # ---- 深矿（气泡包裹）----
    deep = data.get("deep") or {}
    if deep:
        c.text((x0, y), deep.get("name", "深矿赏金"), size=FS_SECT,
               color=p["title_color"], role="light", anchor="lm")
        y += FS_SECT + 8
        _bubble(c, x0, y, cw, BUB_DEEP_H, p)
        bx = x0 + BUB_PAD_X
        icons.draw(c, deep.get("icon", "bolt"), bx + ROW_ICON // 2,
                   y + BUB_PAD_Y + LINE_H // 2, ROW_ICON,
                   p["text_color"], style=ICON_STYLE)
        tx = bx + ROW_ICON + 10
        tag = deep.get("tag", "")
        max_text_w = content_w - (BUB_PAD_X + ROW_ICON + 10) - BUB_PAD_X
        if tag:
            seg = tag + " "
            c.text((tx, y + BUB_PAD_Y + LINE_H // 2), seg, size=FS_BODY,
                   color=p["accent_color"], role="regular", anchor="lm")
            tx += _w(seg, FS_BODY, "regular")
        title_seg = _fit(deep.get("title", ""), max(0, max_text_w - (tx - bx)), FS_BODY, "regular")
        c.text((tx, y + BUB_PAD_Y + LINE_H // 2), title_seg, size=FS_BODY,
               color=p["text_color"], anchor="lm")
        detail = _fit(deep.get("detail", ""), max(0, max_text_w), FS_SMALL, "regular")
        c.text((tx + 8, y + BUB_PAD_Y + LINE_H + 2 + FS_SMALL // 2), detail, size=FS_SMALL,
               color=p["dim_color"], anchor="lm")
        y += BUB_DEEP_H + BUB_GAP
        y += 12

    # ---- 派系赏金（气泡包裹）----
    for f in data.get("factions") or []:
        c.text((x0, y), f.get("name", ""), size=FS_SECT,
               color=p["title_color"], role="light", anchor="lm")
        y += FS_SECT + 8
        for j in f.get("jobs") or []:
            y += _faction_row(c, x0, y, cw, j, p) + BUB_GAP
        y += 8

    # ---- 底部来源（右下角）----
    if source:
        c.text((width - PAD, h - PAD - 4), source, size=FS_TINY,
               color=p["dim_color"], anchor="rb")

    return c
