# -*- coding: utf-8 -*-
"""pic/templates/incarnon.py —— 灵化战甲及武器周常卡（照 Rabbit 原卡逐像素复刻）。

2026-11-05 全量对齐用户提供的 800×1793 参考卡（``2ddfc7dfad0c96bf6ddd37c8136d2df3.png``）。
**背景不改**：仍走 ``decor.background(..., tpl="incarnon", only_override=True)`` + 主题卡片壳，
参考图里的粉兔底纹/右下角插画一律不复制；其余版式元素全部按参考实测复刻。

参考卡实测坐标（800 宽）：
  * 标题实心黑圆人像 26px（x215..240, y40..65）+ 蓝色大标题（x261..585, y35..68）居中；
    分割线 y=87, x=50..749；说明行 y=120..140, x=36。
  * 区块标题左侧蓝色竖条 x=34..38, 高 41（y=174..214 / 578..618 / 1352..1392），文字 x=46。
  * 本周战甲 3 卡：x=34..270 / 281..518 / 529..765，y=234..502（高 268）。
    卡内：左上角真实战甲立绘 38px、紫色战甲名、右上角橙色描边「推荐」胶囊；
    「评价：」从卡左侧 x=52 起，多行铺满整卡宽。
  * 「· 下周战甲：」+ 3 个蓝色描边胶囊（y=517..552，高 36），首个胶囊内嵌橙色圆形「荐」。
  * 本周武器 5 卡（2 列 3 行）：左列 x=34..394、右列 x=405..765；
    y=638..858 / 869..1084 / 1095..1276（第三行只有左卡）。
    卡内：真实武器图标 + 紫色武器名（无方括号、无类型）、蓝色「版本」胶囊、
    右上角橙色「推荐」胶囊（仅推荐武器）、「评价：」行（可内嵌真实武器小图标）。
  * 「- 剩余时间：」蓝色前缀 + 橙色描边胶囊（y=1290..1325，高 35），无时钟图标。
  * 轮回列表外框 x=40..755, y=1411..1717（高 306），9 行 × 5 列，行距 34；
    本周行紫、下周行蓝、其余石板灰；「=>」指本周首列；推荐用橙色圆形「荐」章。
  * 页脚上方分割线 y=1736，绿色 PC 胶囊（x37..98, y1748..1770）+ 蓝色来源文字。

输入（build 的 params）::

    data: {
        "title": "本周灵化战甲及武器",
        "intro": "本周灵化战甲及武器如下：",
        "frames": [{"name":"Inaros","comment":"...","recommend":True,"icon":"<png>"}, ...],
        "next_frames": [{"name":"Nidus","rec":True}, ...],
        "weapons": [{"name":"布莱顿","recommend":False,"icon":"<png>",
                     "comment":"...",                       # 可含 \n 手工断行
                     "comment_parts":[{"text":"..."},{"icon":"<png>"}],
                     "variants":[{"name":"布莱顿 Prime"}, ...]}, ...],
        "remain": "3天12小时59分4秒",
        "rows": [[{"name":"布莱顿","rec":False,"flag":"cur"}, ...], ...],
        "footer": "Warframe Rabbit *善用右键/长按提取文字",
        "platform": "PC",
    }

``build(..., boxes=[])`` 会在**真实绘制时**逐元素登记包围盒，供
``tools/check_incarnon_layout.py --check`` 检查溢出 / 重叠 / 间距（card-design 红线）。
配色可由 ``t.bg_overrides["incarnon"]`` 逐键覆盖。
"""
from __future__ import annotations

import math
import re
from dataclasses import replace as _dc_replace

from PIL import Image

from .. import assets, decor
from ..canvas import Canvas, rgb
from ..theme import Theme

# ---- 固定配色（逐点采样自参考卡 RGB） ----
C_PAGE = "#FDF7FB"        # 页面外背景（保留原有背景系统，不复制参考图粉兔底纹）
C_CARD = "#FFFFFF"        # 卡片壳底
C_BORDER = "#E9EEF6"      # 卡片壳描边
C_BLUE = "#175794"        # 蓝：大标题 / 区块竖条 / 胶囊描边 / 前缀 / 下周行 / 页脚
C_PURPLE = "#7B5299"      # 紫：战甲名 / 武器名 / 本周行
C_ORANGE = "#CB7308"      # 橙：推荐胶囊 / 剩余时间 / 荐章
C_TEXT = "#212428"        # 主文字（区块标题 / 评价）
C_DIM = "#5C6171"         # 轮回列表普通行
C_LINE = "#B6B4B1"        # 标题下分割线
C_FOOT_LINE = "#9FA3A0"   # 页脚上方分割线
C_CARD_BG = "#F9FCFC"     # 战甲/武器卡底
C_CARD_LINE = "#B3CFD8"   # 卡描边 / 列表外框
C_VER_FILL = "#F1F1F6"    # 「版本」胶囊底
C_REMAIN_FILL = "#FBF2E9" # 剩余时间胶囊底
C_GREEN = "#0E8C73"       # PC 徽章
C_TITLE_ICON = "#212428"  # 标题实心人像圆

# ---- 网格（全部来自参考图像素测量） ----
X0 = 34                    # 内容左边界（区块竖条 / 卡左沿）
X1 = 765                   # 内容右边界
CW = X1 - X0               # 731
FRAME_GAP = 11
WEAPON_GAP = 11
SEC_BAR_W = 5
SEC_BAR_H = 41
SEC_TEXT_X = 56

CARD_PAD = 18              # 卡内左右边距
FRAME_ICON = 38            # 战甲立绘槽
WEAPON_ICON = 34           # 武器图标槽
FRAME_MIN_H = 268          # 战甲卡参考高度；内容更多时按测量结果增长
FRAME_NAME_OFF = 31        # 战甲名行中心相对卡顶
FRAME_COMMENT_TOP = 56     # 评价首行相对卡顶
LINE_H = 33                # 行距
VER_TOP = 59               # 第一个「版本」胶囊相对卡顶
VER_H = 27                 # 「版本」胶囊高
VER_PITCH = 37.5           # 「版本」胶囊行距
VER_LABEL_W = 42           # 左侧「版本」圆标签宽
VER_GAP_COMMENT = 17       # 末个版本胶囊到评价首行
W_BOTTOM = 21              # 武器卡底部留白

LIST_X0, LIST_X1 = 40, 755
LIST_ROW_H = 34
LIST_COL_X = (46, 184, 337, 495, 630)       # 5 列起始 x（参考实测）
LIST_BADGE_CX = (149, 302, 459, 595, 730)   # 每列「荐」章中心 x（参考实测）
BADGE_SZ = 22
BADGE_FS = 17
LIST_BADGE_GAP = 4         # 名字右端到「荐」章左端的最小间隙（度量优先）
INLINE_ICON = 22           # 评价行内嵌真实武器小图标

#: 参考卡实测的「剩余时间」行起点。每周内容多寡不同，但骨架必须锁在这里：
#: 内容更矮时把差值吸收成武器区后的留白；只有内容确实更高才整段顺延，
#: 绝不压缩内容/改字号去凑高度（用户最高声明：禁止降级）。
REF_REMAIN_Y = 1290

NEXT_PILL_H = 36
REMAIN_PILL_H = 35
DIV_TOP_X0, DIV_TOP_X1 = 50, 749

FS_TITLE = 36
FS_INTRO = 22
FS_SECT = 26
FS_FRAME_NAME = 24
FS_WEAPON_NAME = 24
FS_COMMENT = 22
FS_VER_LABEL = 16
FS_VER = 18
FS_NEXT = 20
FS_NEXT_PREFIX = 20
FS_REMAIN = 22
FS_REMAIN_PREFIX = 20
FS_LIST = 20
FS_FOOT = 16
FS_REC_FRAME = 18
FS_REC_WEAPON = 18
TITLE_ICON_SZ = 26

#: 专属配色键 → (默认常量, 前端浮窗标签)
_COLOR_MAP: dict[str, tuple[str, str]] = {
    "page_color":       (C_PAGE,        "页面底色"),
    "card_color":       (C_CARD,        "卡片底色"),
    "border_color":     (C_BORDER,      "卡片描边"),
    "title_color":      (C_BLUE,        "大标题"),
    "text_color":       (C_TEXT,        "正文/评价"),
    "dim_color":        (C_DIM,         "轮回列表普通行"),
    "sect_color":       (C_TEXT,        "区块标题"),
    "frame_color":      (C_PURPLE,      "战甲名"),
    "weapon_color":     (C_PURPLE,      "武器名"),
    "rec_color":        (C_ORANGE,      "推荐标记"),
    "disp_name_color":  (C_BLUE,        "版本胶囊文字"),
    "disp_val_color":   (C_BLUE,        "版本胶囊描边"),
    "remain_color":     (C_ORANGE,      "剩余时间"),
    "line_color":       (C_LINE,        "分割线"),
    "bubble_bg":        (C_CARD_BG,     "卡片气泡底"),
    "bubble_line":      (C_CARD_LINE,   "卡片气泡边"),
    "list_text":        (C_DIM,         "轮回列表"),
    "list_cur":         (C_PURPLE,      "本周行"),
    "list_next":        (C_BLUE,        "下周行"),
    "list_rec":         (C_ORANGE,      "推荐荐章"),
    "pc_border":        (C_GREEN,       "PC徽章描边"),
    "pc_text":          (C_GREEN,       "PC徽章字"),
    "foot_color":       (C_BLUE,        "页脚来源"),
    "title_icon_color": (C_TITLE_ICON,  "标题人像图标"),
    "blue_color":       (C_BLUE,        "蓝：竖条/胶囊/前缀"),
    "ver_fill":         (C_VER_FILL,    "版本胶囊底"),
    "remain_fill":      (C_REMAIN_FILL, "剩余时间胶囊底"),
    "foot_line":        (C_FOOT_LINE,   "页脚分割线"),
}


def _pal(t: Theme) -> dict[str, str]:
    """本卡生效配色：专属覆盖 > 硬编码默认。"""
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("incarnon") if isinstance(ov.get("incarnon"), dict) else {}
    out: dict[str, str] = {}
    for key, (default, _lab) in _COLOR_MAP.items():
        s = v.get(key)
        out[key] = s if (isinstance(s, str) and s) else default
    return out


def _card_alpha(t: Theme) -> int:
    """卡片底色透明度：bg_overrides["incarnon"].card_alpha，缺省 100。"""
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("incarnon") if isinstance(ov.get("incarnon"), dict) else None
    try:
        return max(0, min(100, int(v.get("card_alpha", 100)))) if v else 100
    except (TypeError, ValueError):
        return 100


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _h(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_size(s, size, role)[1]


def _load_image(path: str | None, size: int) -> Image.Image | None:
    """加载真实 DE 资产并等比缩放（按 alpha 包围盒裁掉透明边）。"""
    if not path:
        return None
    img = None
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:
        try:
            img = assets.load(path)
        except Exception:
            img = None
    if img is None or getattr(img, "width", 0) <= 0 or getattr(img, "height", 0) <= 0:
        return None
    bb = img.getchannel("A").getbbox()
    if bb:
        img = img.crop(bb)
    scale = min(size / img.width, size / img.height, 1.0)
    if scale < 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                         Image.LANCZOS)
    return img


def _paste_img(c: Canvas, path: str | None, x: int, y: int, slot: int) -> tuple[int, int, int, int]:
    """在 slot×slot 槽里居中贴真实图标；返回实际贴图包围盒。缺图直接抛错（禁止兜底）。"""
    img = _load_image(path, slot)
    if img is None:
        raise FileNotFoundError("灵化卡真实图标缺失或无法解码：%r" % (path,))
    px = x + (slot - img.width) // 2
    py = y + (slot - img.height) // 2
    c.paste_alpha(img, (px, py))
    return (px, py, px + img.width, py + img.height)


# ---------------------------------------------------------------------------
# 文本 token 流：中英混排换行 + 行内真实图标（度量优先，绝不按字数估宽）
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> list[tuple]:
    """中文逐字可断，英文/数字整词不断。"""
    toks: list[tuple] = []
    for tk in re.findall(r"[A-Za-z0-9][A-Za-z0-9&.\-']*|\s+|[^A-Za-z0-9\s]", str(text or "")):
        if str(tk).startswith("\n"):
            continue
        toks.append(("t", tk))
    return toks


def _comment_tokens(w: dict) -> list[tuple]:
    """把 comment / comment_parts 统一成 token 流；``\\n`` 变强制换行。"""
    parts = w.get("comment_parts")
    toks: list[tuple] = []
    if isinstance(parts, list) and parts:
        for p in parts:
            if not isinstance(p, dict):
                continue
            if p.get("icon"):
                toks.append(("i", str(p["icon"])))
            elif p.get("text") is not None:
                toks.extend(_tokenize(str(p["text"])))
    else:
        raw = str(w.get("comment") or "")
        for i, seg in enumerate(raw.split("\n")):
            if i:
                toks.append(("br", ""))
            toks.extend(_tokenize("评价：" + seg if i == 0 else seg))
    return toks


def _tok_width(tk: tuple, size: int) -> int:
    return INLINE_ICON + 4 if tk[0] == "i" else _w(tk[1], size)


def _wrap_tokens(toks: list[tuple], size: int, max_w: int) -> list[list[tuple]]:
    """贪心分行；手工换行 ``('br','')`` 强制断行，行首空白丢弃。"""
    lines: list[list[tuple]] = []
    cur: list[tuple] = []
    cw = 0
    for tk in toks:
        if tk[0] == "br":
            lines.append(cur)
            cur, cw = [], 0
            continue
        tw = _tok_width(tk, size)
        if cur and cw + tw > max_w:
            lines.append(cur)
            cur, cw = [], 0
            if tk[0] == "t" and tk[1].isspace():
                continue
        cur.append(tk)
        cw += tw
    lines.append(cur)
    return [ln for ln in lines if ln] or [[("t", "")]]


def _line_width(ln: list[tuple], size: int) -> int:
    return sum(_tok_width(tk, size) for tk in ln)


# ---------------------------------------------------------------------------
# 单次版式规划：测量与绘制共用，杜绝两套公式漂移
# ---------------------------------------------------------------------------
def _frame_widths() -> list[int]:
    avail = CW - FRAME_GAP * 2
    a = avail // 3
    return [a, avail - a * 2, a]          # 236 / 237 / 236 —— 与参考 x=34/281/529 一致


def _weapon_width() -> int:
    return (CW - WEAPON_GAP) // 2          # 360


def _plan(data: dict, width: int) -> dict:
    """算出所有区块的坐标、每张卡的行数与高度；绘制只读这份结果。"""
    scale_x = width / 800.0
    if abs(scale_x - 1.0) > 1e-6:
        raise ValueError(
            "灵化卡按参考图 800px 宽度逐像素对齐（分区坐标由 "
            "tools/check_incarnon_layout.py --ref-geom 锁定），不接受 width=%d；"
            "请把 PIC_MAX_WIDTH 设回 800，或调整参考卡布局后同步更新门禁坐标。" % width)

    p: dict = {"width": width}
    frames = list(data.get("frames") or [])
    weapons = list(data.get("weapons") or [])
    rows = list(data.get("rows") or [])
    next_frames = list(data.get("next_frames") or [])

    # 战甲卡：评价行（含手工换行）+ 统一高度
    fw = _frame_widths()
    frame_cards = []
    max_lines = 1
    for i, f in enumerate(frames[:3]):
        max_w = fw[i] - CARD_PAD * 2
        lines = _wrap_tokens(_comment_tokens(f), FS_COMMENT, max_w)
        for ln in lines:
            if _line_width(ln, FS_COMMENT) > max_w:
                raise ValueError("战甲卡评价行溢出：%s / %dpx > %dpx"
                                 % (f.get("name"), _line_width(ln, FS_COMMENT), max_w))
        max_lines = max(max_lines, len(lines))
        frame_cards.append({"f": f, "lines": lines})
    frame_h = max(FRAME_MIN_H, FRAME_COMMENT_TOP + (max_lines - 1) * LINE_H + _h("评", FS_COMMENT) + 26)

    # 武器卡：版本胶囊 + 评价行；同排两卡取较高者
    ww = _weapon_width()
    w_cards = []
    for w in weapons:
        vers = list(w.get("variants") or [])
        lines = _wrap_tokens(_comment_tokens(w), FS_COMMENT, ww - CARD_PAD * 2)
        for ln in lines:
            if _line_width(ln, FS_COMMENT) > ww - CARD_PAD * 2:
                raise ValueError("武器卡评价行溢出：%s" % w.get("name"))
        v_pills = []
        for j, v in enumerate(vers):
            name = str(v.get("name") or "")
            tw = _w(name, FS_VER)
            v_pills.append({"name": name, "w": VER_LABEL_W + 4 + tw + 12,
                            "text_w": tw,
                            "y": int(round(VER_TOP + j * VER_PITCH))})
        h = (VER_TOP + (len(vers) - 1) * VER_PITCH + VER_H + VER_GAP_COMMENT
             + (len(lines) - 1) * LINE_H + _h("评", FS_COMMENT) + W_BOTTOM)
        w_cards.append({"w": w, "vers": v_pills, "lines": lines, "h": int(round(h))})
    w_rows = []
    for r in range((len(w_cards) + 1) // 2):
        pair = w_cards[r * 2:r * 2 + 2]
        w_rows.append({"cards": pair, "h": max(x["h"] for x in pair)})

    # 下周战甲胶囊（宽度按真实文字度量）
    n_pills = []
    for nf in next_frames[:3]:
        name = str(nf.get("name") or "")
        rec = bool(nf.get("rec"))
        tw = _w(name, FS_NEXT)
        pw = 20 + tw + (4 + BADGE_SZ if rec else 0)
        n_pills.append({"name": name, "rec": rec, "tw": tw, "w": int(round(pw))})

    remain = str(data.get("remain") or "")
    remain_pill_w = int(round(18 + _w(remain, FS_REMAIN)))

    # ---- 纵向编排（参考图逐段实测的间隔） ----
    y = 28
    title_cy = y + 24
    y += 48
    p["title_cy"] = title_cy
    y += 11
    p["div_y"] = y                                  # 87
    y += 1 + 30
    p["intro_cy"] = y + 12                          # 130
    y += 24 + 32
    p["sec1_y"] = y                                  # 174
    y += SEC_BAR_H + 19
    p["frames_y"] = y                               # 234
    p["frames_h"] = frame_h
    p["frame_cards"] = frame_cards
    p["frame_w"] = fw
    y += frame_h + 15
    p["next_y"] = y                                  # 517
    p["next_pills"] = n_pills
    y += NEXT_PILL_H + 25
    p["sec2_y"] = y                                  # 578
    y += SEC_BAR_H + 19
    p["weapons_y"] = y                              # 638
    p["weapon_rows"] = w_rows
    for row in w_rows:
        y += row["h"] + WEAPON_GAP
    y -= WEAPON_GAP
    y += 12
    # 骨架锁死：内容比参考周矮时，把差值吸收成武器区之后的留白，
    # 使剩余时间/轮回表/页脚起点逐周恒定；只有内容确实更高（C 组）
    # 才让下面整段顺延。绝不压缩内容或改字号去凑固定高度（禁止降级）。
    p["weapon_spacer"] = max(0, REF_REMAIN_Y - y)
    y = max(REF_REMAIN_Y, y)
    p["remain_y"] = y                               # 1290（内容更多时顺延）
    p["remain_pill_w"] = remain_pill_w
    y += REMAIN_PILL_H + 27
    p["sec3_y"] = y                                  # 1352
    y += SEC_BAR_H + 18
    p["list_y"] = y                                  # 1411
    p["list_h"] = len(rows) * LIST_ROW_H
    p["rows"] = rows
    y += p["list_h"] + 19
    p["foot_div_y"] = y                             # 1736
    y += 22
    p["foot_cy"] = y                                # 1758
    y += 18 + 17
    p["H"] = y
    return p


# ---------------------------------------------------------------------------
# 绘制原语（每个叶子元素登记包围盒）
# ---------------------------------------------------------------------------
def _rec(boxes, label: str, box) -> None:
    if boxes is not None:
        boxes.append((label, int(box[0]), int(box[1]), int(box[2]), int(box[3])))


def _txt(c: Canvas, boxes, label: str, x: int, cy: int, text: str, size: int,
         color: str, role: str = "regular", register: bool = True) -> int:
    """左中锚点绘文本并登记包围盒，返回实测宽度。

    ``register=False`` 用于同一行里分色绘制的多段文字：由调用方登记一个合并包围盒，
    避免把「同一句话的相邻字」误判成 0px 间距违规。
    """
    text = str(text)
    tw, th = Canvas.text_size(text, size, role)
    c.text((x, cy), text, size=size, color=color, role=role, anchor="lm")
    if register:
        _rec(boxes, label, (x, cy - th // 2, x + tw, cy - th // 2 + th))
    return tw


def _pill(c: Canvas, boxes, label: str, x: int, y: int, w: int, h: int,
          color: str, fill: str | None = None, lw: int = 1) -> None:
    c.rounded_rect((x, y, x + w, y + h), radius=h // 2, fill=fill,
                   outline=color, width=lw)
    _rec(boxes, label, (x, y, x + w, y + h))


def _version_pill(c: Canvas, boxes, label: str, x: int, y: int, w: int,
                  color: str, fill: str) -> None:
    """参考卡「版本」胶囊：左端是一个椭圆标签，右端是圆角封口，中间用椭圆右弧分隔。

    不能先画整条胶囊再盖标签——那样胶囊的上下描边会横穿标签（已实测踩坑）。
    这里把外形做成**一条闭合路径**：椭圆左半 + 上下直边 + 右端圆弧。
    """
    h = VER_H
    r = h / 2.0
    cr = VER_LABEL_W / 2.0
    cx = x + cr
    cy = y + r
    pts: list[tuple[float, float]] = [(cx, float(y)), (x + w - r, float(y))]
    for k in range(1, 13):
        ang = -math.pi / 2 + math.pi * k / 12
        pts.append((x + w - r + r * math.cos(ang), cy + r * math.sin(ang)))
    pts.append((cx, float(y + h)))
    for k in range(1, 24):
        ang = math.pi / 2 + math.pi * k / 23
        pts.append((cx + cr * math.cos(ang), cy + r * math.sin(ang)))
    c.draw.polygon(pts, fill=rgb(fill) + (255,))
    c.draw.line(pts + [pts[0]], fill=rgb(color) + (255,), width=1, joint="curve")
    # 椭圆右弧 = 标签与变体名之间的分隔线（参考实测）
    c.draw.arc((x, y, x + VER_LABEL_W, y + h), -90, 90, fill=rgb(color) + (255,), width=1)
    _rec(boxes, label, (x, y, x + w, y + h))


def _seal(c: Canvas, cx: int, cy: int, r: int, color: str, lobes: int = 14) -> None:
    """参考卡「荐」章实测是带浅波瓣的印章环，不是光滑圆：外圈齿状 + 内圈浅底。"""
    def poly(rad: float) -> list[tuple[float, float]]:
        pts = []
        for k in range(lobes * 2):
            ang = 2 * math.pi * k / (lobes * 2)
            rr = rad - (1.15 if k % 2 else 0.0)
            pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
        return pts
    c.draw.polygon(poly(r), fill=rgb(color) + (255,))
    c.draw.polygon(poly(r - 2.2), fill=(255, 249, 240, 255))


def _badge(c: Canvas, boxes, label: str, cx: int, cy: int, color: str,
           d: int = BADGE_SZ, fs: int = BADGE_FS) -> None:
    """橙色「荐」印章（参考实测直径 22px，波瓣橙环 + 浅底 + 橙字）。"""
    r = d // 2
    y0 = cy - r
    _seal(c, cx, cy, r, color)
    c.text((cx, cy), "荐", size=fs, color=color, role="regular", anchor="mm")
    _rec(boxes, label, (cx - r, y0, cx + r, y0 + d))


def _title_icon(c: Canvas, boxes, cx: int, cy: int, col: str) -> None:
    """参考原卡：实心黑圆 + 白色人像剪影（头 + 肩）。"""
    r = TITLE_ICON_SZ // 2
    c.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
    c.ellipse((cx - 3, cy - 8, cx + 3, cy - 2), fill=C_CARD)
    c.ellipse((cx - 7, cy + 1, cx + 7, cy + 11), fill=C_CARD)
    _rec(boxes, "title-icon", (cx - r, cy - r, cx + r, cy + r))


def _section(c: Canvas, boxes, label: str, x: int, y: int, text: str,
             p: dict[str, str], extra: list[tuple[str, str]] | None = None) -> int:
    """蓝色竖条 + 区块标题（extra 为标题后的彩色括号标注）。"""
    c.rounded_rect((x, y, x + SEC_BAR_W, y + SEC_BAR_H), radius=2, fill=p["blue_color"])
    _rec(boxes, label + "-bar", (x, y, x + SEC_BAR_W, y + SEC_BAR_H))
    cy = y + SEC_BAR_H // 2
    th = _h(text, FS_SECT)
    cx = _txt(c, boxes, label + "-text", SEC_TEXT_X, cy, text, FS_SECT,
              p["sect_color"], register=False)
    cx += SEC_TEXT_X
    for suffix, color in (extra or []):
        cx += _txt(c, boxes, label + suffix, cx, cy, suffix, FS_SECT, color,
                   register=False)
    # 整行（含分色括号标注）只登记一个包围盒
    _rec(boxes, label + "-text", (SEC_TEXT_X, cy - th // 2, cx, cy - th // 2 + th))
    return cx


def _draw_comment_line(c: Canvas, boxes, label: str, x: int, cy: int,
                       ln: list[tuple], color: str) -> None:
    """画一行（文本 token + 行内真实武器图标），整行登记一个包围盒。"""
    cx = x
    th = _h("评", FS_COMMENT)
    y0 = cy - th // 2
    for tk in ln:
        if tk[0] == "i":
            box = _paste_img(c, tk[1], cx, cy - INLINE_ICON // 2, INLINE_ICON)
            cx += INLINE_ICON + 4
        else:
            s = tk[1]
            w = _w(s, FS_COMMENT)
            c.text((cx, cy), s, size=FS_COMMENT, color=color, role="regular", anchor="lm")
            cx += w
    _rec(boxes, label, (x, y0, cx, y0 + th))


def _frame_card(c: Canvas, boxes, x0: int, y0: int, cw: int, h: int,
                card: dict, idx: int, p: dict[str, str]) -> None:
    f = card["f"]
    c.rounded_rect((x0, y0, x0 + cw, y0 + h), radius=10,
                   fill=p["bubble_bg"], outline=p["bubble_line"], width=1)
    ix, iy = x0 + CARD_PAD, y0 + 12
    box = _paste_img(c, f.get("icon"), ix, iy, FRAME_ICON)
    _rec(boxes, "frame%d-icon" % idx, box)
    nx = x0 + CARD_PAD + FRAME_ICON + 10
    _txt(c, boxes, "frame%d-name" % idx, nx, y0 + FRAME_NAME_OFF,
         str(f.get("name") or ""), FS_FRAME_NAME, p["frame_color"])
    if f.get("recommend"):
        tw = _w("推荐", FS_REC_FRAME)
        pw, ph = tw + 18, 36
        px, py = x0 + cw - CARD_PAD - pw, y0 + 17
        _pill(c, boxes, "frame%d-rec" % idx, px, py, pw, ph, p["rec_color"])
        c.text((px + pw // 2, py + ph // 2), "推荐", size=FS_REC_FRAME,
               color=p["rec_color"], role="regular", anchor="mm")
    for j, ln in enumerate(card["lines"]):
        cy = y0 + FRAME_COMMENT_TOP + j * LINE_H + _h("评", FS_COMMENT) // 2
        _draw_comment_line(c, boxes, "frame%d-comment%d" % (idx, j),
                           x0 + CARD_PAD, cy, ln, p["text_color"])


def _weapon_card(c: Canvas, boxes, x0: int, y0: int, cw: int, h: int,
                 card: dict, idx: int, p: dict[str, str]) -> None:
    w = card["w"]
    c.rounded_rect((x0, y0, x0 + cw, y0 + h), radius=10,
                   fill=p["bubble_bg"], outline=p["bubble_line"], width=1)
    ix, iy = x0 + CARD_PAD, y0 + 8
    box = _paste_img(c, w.get("icon"), ix, iy, WEAPON_ICON)
    _rec(boxes, "weapon%d-icon" % idx, box)
    nx = x0 + CARD_PAD + WEAPON_ICON + 14
    _txt(c, boxes, "weapon%d-name" % idx, nx, y0 + 25,
         str(w.get("name") or ""), FS_WEAPON_NAME, p["weapon_color"])

    if w.get("recommend"):
        tw = _w("推荐", FS_REC_WEAPON)
        pw, ph = tw + 28, 35
        px, py = x0 + cw - 18 - pw, y0 + 19
        _pill(c, boxes, "weapon%d-rec" % idx, px, py, pw, ph, p["rec_color"])
        c.text((px + pw // 2, py + ph // 2), "推荐", size=FS_REC_WEAPON,
               color=p["rec_color"], role="regular", anchor="mm")

    vx = x0 + CARD_PAD
    for j, v in enumerate(card["vers"]):
        vy = y0 + v["y"]
        _version_pill(c, boxes, "weapon%d-ver%d" % (idx, j), vx, vy, v["w"],
                      p["disp_val_color"], p["ver_fill"])
        c.text((vx + VER_LABEL_W // 2, vy + VER_H // 2), "版本",
               size=FS_VER_LABEL, color=p["disp_val_color"], role="regular", anchor="mm")
        tx = vx + VER_LABEL_W + 4
        c.text((tx, vy + VER_H // 2), v["name"], size=FS_VER,
               color=p["disp_name_color"], role="regular", anchor="lm")

    for j, ln in enumerate(card["lines"]):
        nver = max(1, len(card["vers"]))
        cy = (y0 + VER_TOP + (nver - 1) * VER_PITCH + VER_H + VER_GAP_COMMENT
              + j * LINE_H + _h("评", FS_COMMENT) // 2)
        _draw_comment_line(c, boxes, "weapon%d-comment%d" % (idx, j),
                           x0 + CARD_PAD, cy, ln, p["text_color"])


def _list_row(c: Canvas, boxes, y: int, row: list, ri: int, p: dict[str, str]) -> None:
    CELL_GAP = 10         # 相邻列文字间隙（实测 2px 时长名字会与下一列视觉粘连）
    LIST_MIN_FS = 16      # 长名字降字号下限，再放不下就报错，不裁字
    cy = y + LIST_ROW_H // 2
    is_cur = any(str(cell.get("flag") or "") == "cur" for cell in row[:5])
    for i, cell in enumerate(row[:5]):
        name = str(cell.get("name") or "")
        flag = str(cell.get("flag") or "")
        color = (p["list_cur"] if flag == "cur" else
                 p["list_next"] if flag == "next" else p["list_text"])
        cx = LIST_COL_X[i]
        next_x = (LIST_COL_X[i + 1] if i + 1 < len(LIST_COL_X) else LIST_X1)
        label = "list%d-%d-" % (ri, i)

        # 度量优先：列可用宽扣除「荐」章与本周行「=>」指针后，按实测宽度
        # 逐像素降字号（F 组「认知 & 冲击」实测 139px > 列宽 138px）。
        # 绝不估算字数、绝不压到相邻列、绝不裁字。
        ptr_w = _w("=>", FS_LIST) if (is_cur and i == 0) else 0
        avail = next_x - cx - CELL_GAP - ptr_w
        if cell.get("rec"):
            avail -= BADGE_SZ + LIST_BADGE_GAP
        size = FS_LIST
        while size > LIST_MIN_FS and _w(name, size) > avail:
            size -= 1
        if _w(name, size) > avail:
            raise ValueError(
                "轮回表第%d行第%d列「%s」%dpx 仍放不进 %dpx 列宽："
                "请调整 LIST_COL_X 或数据源，不要压缩间距/裁字。"
                % (ri, i, name, _w(name, size), avail))
        th = max(_h(name, size), _h("=>", FS_LIST) if ptr_w else 0)
        if ptr_w:
            cx += _txt(c, boxes, label + "ptr", cx, cy, "=>", FS_LIST,
                       p["list_cur"], register=False)
            cx += _txt(c, boxes, label + "name", cx, cy, name, size, color,
                       register=False)
            # 「=>」与武器名是同一串文字，合并成一个包围盒
            _rec(boxes, label + "name",
                 (LIST_COL_X[i], cy - th // 2, cx, cy - th // 2 + th))
        else:
            cx += _txt(c, boxes, label + "name", cx, cy, name, size, color)
        if cell.get("rec"):
            r = BADGE_SZ // 2
            # 荐章默认停在参考卡实测的固定列位；名字更长时右移到名字之后。
            limit = next_x - r - 2
            want = max(LIST_BADGE_CX[i], cx + LIST_BADGE_GAP + r)
            badge_cx = min(limit, want)
            if badge_cx - r < cx + LIST_BADGE_GAP:
                raise ValueError(
                    "轮回表第%d行第%d列「%s」放不下荐章（名字右端 %d，列上限 %d）："
                    "请缩短该武器名或调整 LIST_COL_X，不要压缩间距。"
                    % (ri, i, name, cx, limit))
            _badge(c, boxes, label + "badge", badge_cx, cy - 3, p["list_rec"])


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build(t: Theme, data: dict | None = None, width: int | None = None,
          boxes: list | None = None, **kw) -> Canvas:
    """渲染灵化周常卡；boxes 给定时逐元素登记包围盒供门禁校验。"""
    data = data or {}
    width = int(width or getattr(t, "width", 800) or 800)
    if t is not None:
        t = _dc_replace(t, title_bold=False, body_bold=False,
                        sub_bold=False, badge_bold=False)
    p = _pal(t)
    L = _plan(data, width)
    H = L["H"]
    c = Canvas(width, H, p["page_color"], theme=t)
    decor.background(c, t, tpl="incarnon", only_override=True)
    ca = _card_alpha(t)
    c.rounded_rect((14, 14, width - 14, H - 14), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=ca * 255 // 100)
    c.add_region("ov:card_color", [20, 20, width - 20, H - 20],
                 label="卡片底色/描边", tip="点击修改本卡的卡片底色、描边与透明度（仅本卡生效）")
    c.regions[-1]["values"] = {"ov:card_color": p["card_color"],
                               "ov:border_color": p["border_color"],
                               "ov:card_alpha": ca}

    # ---- 标题 + 分割线 ----
    title = str(data.get("title") or "本周灵化战甲及武器")
    tw = _w(title, FS_TITLE)
    block = TITLE_ICON_SZ + 20 + tw
    bx = (width - block) // 2
    cy = L["title_cy"]
    _title_icon(c, boxes, bx + TITLE_ICON_SZ // 2, cy, p["title_icon_color"])
    tx = bx + TITLE_ICON_SZ + 20
    _txt(c, boxes, "title-text", tx, cy, title, FS_TITLE, p["title_color"])
    c.add_region("ov:title_color", [tx - 10, cy - FS_TITLE // 2, tx + tw + 10, cy + FS_TITLE // 2],
                 label="大标题", tip="点击修改标题颜色")
    c.regions[-1]["values"] = {"ov:title_color": p["title_color"]}
    c.line((DIV_TOP_X0, L["div_y"], DIV_TOP_X1, L["div_y"]), fill=p["line_color"], width=1)

    intro = str(data.get("intro") or "")
    if intro:
        _txt(c, boxes, "intro", 36, L["intro_cy"], intro, FS_INTRO, p["text_color"])

    # ---- 本周战甲 ----
    _section(c, boxes, "sec1", X0, L["sec1_y"], "·本周战甲", p)
    fy, fh = L["frames_y"], L["frames_h"]
    xs = [X0, X0 + L["frame_w"][0] + FRAME_GAP,
          X0 + L["frame_w"][0] + FRAME_GAP + L["frame_w"][1] + FRAME_GAP]
    for i, card in enumerate(L["frame_cards"]):
        _frame_card(c, boxes, xs[i], fy, L["frame_w"][i], fh, card, i + 1, p)

    # ---- 下周战甲（蓝色描边胶囊 + 橙色荐章） ----
    nx = _txt(c, boxes, "next-prefix", 36, L["next_y"] + NEXT_PILL_H // 2,
              "· 下周战甲：", FS_NEXT_PREFIX, p["sect_color"]) + 36
    nx += 26
    for i, pill in enumerate(L["next_pills"]):
        ny = L["next_y"]
        _pill(c, boxes, "next-pill%d" % i, nx, ny, pill["w"], NEXT_PILL_H, p["blue_color"])
        cx = nx + 10
        cx += _txt(c, boxes, "next-name%d" % i, cx, ny + NEXT_PILL_H // 2,
                   pill["name"], FS_NEXT, p["blue_color"])
        if pill["rec"]:
            _badge(c, boxes, "next-badge%d" % i, cx + 4 + BADGE_SZ // 2,
                   ny + NEXT_PILL_H // 2 - 5, p["list_rec"])
        nx += pill["w"] + 15

    # ---- 本周武器 ----
    _section(c, boxes, "sec2", X0, L["sec2_y"], "·本周武器", p)
    wy = L["weapons_y"]
    for ri, row in enumerate(L["weapon_rows"]):
        for ci, card in enumerate(row["cards"]):
            _weapon_card(c, boxes, X0 + ci * (_weapon_width() + WEAPON_GAP),
                         wy, _weapon_width(), row["h"], card, ri * 2 + ci + 1, p)
        wy += row["h"] + WEAPON_GAP

    # ---- 剩余时间 ----
    ry = L["remain_y"] + REMAIN_PILL_H // 2
    rx = _txt(c, boxes, "remain-prefix", 36, ry, "- 剩余时间：", FS_REMAIN_PREFIX,
              p["blue_color"]) + 36
    rx += 17
    _pill(c, boxes, "remain-pill", rx, L["remain_y"], L["remain_pill_w"],
          REMAIN_PILL_H, p["remain_color"], fill=p["remain_fill"])
    c.text((rx + 9, ry), str(data.get("remain") or ""), size=FS_REMAIN,
           color=p["remain_color"], role="regular", anchor="lm")
    c.add_region("ov:remain_color", [rx, L["remain_y"], rx + L["remain_pill_w"],
                                     L["remain_y"] + REMAIN_PILL_H],
                 label="剩余时间", tip="点击修改剩余时间颜色")
    c.regions[-1]["values"] = {"ov:remain_color": p["remain_color"]}

    # ---- 轮回完整列表 ----
    _section(c, boxes, "sec3", X0, L["sec3_y"],
             str(data.get("list_title") or "·灵化武器轮回完整列表"), p,
             extra=[("（本周）", p["list_cur"]), ("（下周）", p["list_next"]), ("：", p["sect_color"])])
    ly = L["list_y"]
    c.rounded_rect((LIST_X0, ly, LIST_X1, ly + L["list_h"]), radius=10,
                   outline=p["bubble_line"], width=1)
    for ri, row in enumerate(L["rows"]):
        _list_row(c, boxes, ly + ri * LIST_ROW_H, row, ri + 1, p)

    # ---- 页脚：分割线 + PC 胶囊 + 来源 ----
    c.line((35, L["foot_div_y"], 765, L["foot_div_y"]), fill=p["foot_line"], width=1)
    pc_w, pc_h = 32, 22
    pc_y = L["foot_cy"] - pc_h // 2
    _pill(c, boxes, "pc-pill", 37, pc_y, pc_w, pc_h, p["pc_border"])
    c.text((37 + pc_w // 2, L["foot_cy"]), str(data.get("platform") or "PC"),
           size=14, color=p["pc_text"], role="regular", anchor="mm")
    foot = str(data.get("footer") or "")
    if foot:
        _txt(c, boxes, "footer", 100, L["foot_cy"], foot, FS_FOOT, p["foot_color"])

    return c
