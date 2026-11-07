# -*- coding: utf-8 -*-
"""pic/templates/riven_analysis.py —— 紫卡分析卡（按用户提供的参考卡 1:1 复刻）。

参考卡实测（297×404 PNG，逐像素取样，2026-11-02）：

    底色 #F3F1ED   主文字 #1A191C   金色主色 #9B7414   金线 #9F8029
    品牌紫 #735787 轨道 #D1CEC6     分隔线 #9A9898     表头下细线 #E2E0DC

版式（坐标均为 297 宽设计空间，渲染时统一乘 S = width/297）：

    y6      页眉：紫色小方块 + 「紫卡分析」；右对齐「统一倍率」
    y28-45  大标题 + 标题右侧「Lv N」徽章 ＋ 右上角武器立绘
    y46-59  英文副标题（大写、字距加宽）· y70 倾向圆点 + 数值
    y77     金色通栏线
    y86-124 综合评级：巨型字母 + 综合偏差 / 右侧「N 正 M 负」+「步枪 MR N 满级」
    y136    分隔线 · y148 表头三列 · y153 表头下细线
    y166+   词条行（行距 57.67）：彩色图标 + 主数值 / 词条名（左列 x20）
            ＋ 统计三段（x104..x258：低值 / 均值…高低… / 高值）
            ＋ 进度条（与统计区同宽）＋ 评级右锚 x287
    底部    市场参考 / 推荐洗卡 / 推荐负面 / 类似紫卡 / 数据来源 / ±10% 提示 / 页脚
            （2026-11-04 22:25 新增推荐区块，23:0x 增「类似紫卡」行；
             高度按词条数 + 实测折行数累加，不写死）

card-design 硬约束（2026-11-04 修「百分比数字重叠」后）：
  * 底部推荐区块同样只走 :func:`_text` + :func:`_wrap_lines`：固定标签列/值列、
    显式 gap、逐字度量折行、内容驱动高度；缺 ``recs`` 直接 raise，
    不允许「市场/推荐词条整块消失」（2026-11-04 用户截图的缺陷）。
  * 全卡文本只走 :func:`_text`（度量优先 + 可登记包围盒），禁止直接 d.text 手写坐标；
    包围盒交给 ``tools/check_riven_layout.py`` 自检，违规 exit 1。
  * 统计三段列起点固定 x0/x1，中段字号按「实测剩余宽度」二分自适应；
    三段之间显式 gap=6 设计px，放不下直接 raise，绝不挤压。
  * 左列（主数值 / 词条名）按实测宽度自适应、评级右锚定，都不随内容变长而漂移。
  * 只有负词条的「数值」是紫色；进度条长度 =(偏差+10)/20，越大越满（负词条越满惩罚越重）。
  * 字号基准由实测墨迹反推，实际文本再按可用宽度收敛：换字体、换数值都不跑版。
"""
from __future__ import annotations

import logging
import re as _re

from PIL import Image, ImageDraw

from .. import fonts
from .. import weapon_icons
from ..canvas import Canvas
from ..theme import Theme

logger = logging.getLogger("qq_bridge")

#: 量墨迹用的 1px 探针画布（只用来算 textbbox，不参与出图）
_PROBE = Image.new("L", (8, 8))

#: 设计空间（参考卡原始尺寸）
BASE_W = 297.0
BASE_H = 404.0

C_BG = "#F3F1ED"
C_INK = "#1A191C"
C_GOLD = "#9B7414"
C_GOLD_LINE = "#9F8029"
C_TRACK = "#D1CEC6"
C_BRAND = "#735787"
C_LABEL = "#7F7E7A"
C_HEAD = "#787672"
C_FOOT = "#95938F"
C_RULE = "#9A9898"
C_RULE2 = "#E2E0DC"
C_NAME = "#1D1D1D"
C_WHITE = "#FFFFFF"
C_GREEN = "#3FA34D"          # 高于中值（高）：绿色（对齐参考卡进度条/偏差方向）
C_RED = "#C0392B"            # 低于中值（低）：红色

PAD_L = 6.0
PAD_R = 10.0          # 参考卡内容右边界在 x286（297-11）
ROW_TOP = 166.0
ROW_PITCH = 57.67
BAR_X = 99.0
BAR_W = 98.0
BAR_H = 4.0

#: 固定列网格（card-design：列起点由度量推算，禁止随内容漂移）。
#: 设计空间 297 宽：左区图标 + 主数值，右区统计三段 + 进度条，最右评级右锚定。
COL_ICON_X = 10.0
COL_VALUE_X = 20.0
COL_STAT_X0 = 104.0        # 统计区左边界：主数值按 78 宽反推到 x≈98，留 6 设计px 间隙
COL_STAT_X1 = 258.0        # 统计区右边界：评级区左侧留约 8 设计px 间隙
COL_GRADE_RIGHT = BASE_W - PAD_R
STAT_GAP = 6.0             # 统计三段之间的最小显式 gap（设计空间，随 s 缩放）


# ---------------------------------------------------------------------------
# 字号反推（按墨迹高度 / 宽度）
# ---------------------------------------------------------------------------
def _font(role: str, size: int):
    return fonts.load(role, max(4, int(size)))


def _ink_h(text: str, role: str, size: int) -> int:
    """真实**墨迹**高度（不是排版框高度）。详见 _ink_box。"""
    return _ink_box(text, role, size)[1]


def _ink_box(text: str, role: str, size: int) -> tuple[int, int]:
    """真正**紧贴像素**的墨迹宽高：画进临时层再量非零像素框。

    为什么不用 font.getbbox / ImageDraw.textbbox：两者都按字形**步进宽**（advance）
    计算，含左右 side bearing，比实际墨迹宽 2~4px/字。按它反推字号会整体偏小
    （实测：目标 78px 的「+192.2%」只画出 69px）。
    """
    f = _font(role, size)
    w = max(8, size * (len(text) + 2))
    h = max(8, size * 3)
    layer = Image.new("L", (w, h), 0)
    ImageDraw.Draw(layer).text((size // 2, size // 2), text, font=f, fill=255, anchor="la")
    bb = layer.getbbox()
    return ((bb[2] - bb[0], bb[3] - bb[1]) if bb else (0, 0))


def _ink_w(text: str, role: str, size: int) -> float:
    return float(_ink_box(text, role, size)[0])


def _width(text: str, role: str, size: int) -> float:
    """**步进**宽度（含左右字距）——逐字排布/右对齐定位要用它，不能用墨迹宽。"""
    f = _font(role, size)
    try:
        return float(f.getlength(text))
    except Exception:  # noqa: BLE001 — 极老 Pillow 无 getlength
        bb = f.getbbox(text)
        return float(bb[2] - bb[0])


def _size_for_h(sample: str, role: str, target: float, lo: int = 4, hi: int = 96) -> int:
    """二分出「墨迹高度 ≈ target」的字号。全部字号都走这里，换字体也不跑版。"""
    best = lo
    for _ in range(12):
        mid = (lo + hi) // 2
        if _ink_h(sample, role, mid) <= target:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
        if lo > hi:
            break
    return max(4, best)


def _size_for_w(sample: str, role: str, target: float, lo: int = 4, hi: int = 96) -> int:
    """二分出「墨迹宽度 ≈ target」的字号。

    用于**列宽敏感**的文字（词条数值 / 偏差）：这两列左右都被夹住，字体自带的
    字宽差异会直接造成「偏差贴到区间上」（实测微软雅黑比参考卡字体宽 ~12%）。
    """
    best = lo
    for _ in range(12):
        mid = (lo + hi) // 2
        if _ink_w(sample, role, mid) <= target:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
        if lo > hi:
            break
    return max(4, best)


def _fit_width_size(text: str, role: str, target: float, hi: int = 96) -> int:
    """二分出「**步进宽** <= target」的最大字号。

    定位用的是 :func:`_width`（步进宽），反推字号也必须用同一度量。旧实现按
    固定样本 "+192.2% / 均值000.0% : 低00.0%" 反推一个字号就套到所有实际文本上，
    真实数值一变长，中段就压到低值/高值上——这正是用户反馈的百分比数字重叠根因。
    """
    best, lo, hh = 4, 4, max(4, int(hi))
    for _ in range(14):
        mid = (lo + hh) // 2
        if _width(text, role, mid) <= target:
            best, lo = mid, mid + 1
        else:
            hh = mid - 1
        if lo > hh:
            break
    return max(4, best)


def _text(d, boxes, label, xy, text, size, color, anchor="ls", role="bold"):
    """**唯一文本入口**：度量优先绘制 + 可选包围盒登记。

    ``boxes`` 为 None 时零开销（生产运行时走这条路）；自检脚本传入 list，
    收集**真实渲染**的包围盒交给 ``check_boxes`` 门禁，而不是另写一套复算公式
    （自己考自己的检查等于没加）。
    """
    if not text:
        return None
    f = _font(role, size)
    d.text(xy, text, font=f, fill=color, anchor=anchor)
    if boxes is not None:
        boxes.append((label, *d.textbbox(xy, text, font=f, anchor=anchor)))
    return None


def _spaced_text(c: Canvas, xy: tuple[float, float], text: str, size: float,
                 color: str, role: str, spacing: float) -> float:
    """字距加宽的文本（参考卡「BURSTON PRIME」是大写+宽字距，Canvas 无此参数）。
    返回结束 x。"""
    x, y = xy
    for ch in text:
        c.draw.text((x, y), ch, font=_font(role, size), fill=color, anchor="ls")
        x += _width(ch, role, size) + spacing
    return x


def _icon_dot(c: Canvas, cx: float, cy: float, r: float, color: str) -> None:
    """词条左侧彩色圆形图标（按词条类别取色，对齐参考卡的彩色词条图标）。"""
    c.ellipse((int(cx - r), int(cy - r), int(cx + r), int(cy + r)), fill=color)


def _lv_badge(c: Canvas, x: float, y: float, level, fs_title: int, s: float, P) -> None:
    """标题右侧「Lv N」紫色圆角徽章（参考卡同款）。level=None 时不画。"""
    if level is None:
        return
    label = f"Lv {level}"
    fs = max(6, int(7 * s))
    f = _font("bold", fs)
    w = _width(label, "bold", fs)
    pad = int(3 * s)
    bh = max(10, int(13 * s))
    c.rounded_rect((int(x), int(y), int(x + w + 2 * pad), int(y + bh)),
                   radius=int(bh / 2), fill=C_BRAND)
    c.draw.text((int(x + pad), int(y + bh / 2 + fs * 0.18)), label,
                font=f, fill=C_WHITE, anchor="ls")


def _disp_line(c: Canvas, x: float, y: float, dots: str, value: str,
               fs: int, s: float, P) -> None:
    """「倍率 1.20 · 全武器统一」：DE 44.1.3 后不再画星级点阵。

    旧实现画星级点阵，点数随武器倍率变化；倾向差异移除后
    该可视化会误导。保留 dots 参数只为兼容既有 data 契约，不再绘制。
    """
    d = c.draw
    _lbl = "倍率"
    d.text((x, y), _lbl, font=_font("bold", fs), fill=C_LABEL, anchor="ls")
    x0 = x + _width(_lbl, "bold", fs) + int(5 * s)
    d.text((x0, y), f"{value} · 全武器统一", font=_font("bold", fs),
           fill=C_GOLD, anchor="ls")



def _stat_summary(d, boxes, tag: str, x0: float, y: float, x1: float, st: dict,
                  fs_range: int, fs_dev_base: int, gap: float) -> None:
    """统计行：低值（左锚 x0）· 均值…高/低…（固定列区间居中）· 高值（右锚 x1）。

    card-design 度量优先 / 固定列网格 / 显式 gap：
      * 三段列起点全部由 :func:`_width` 实测推算，禁止按字符数估算；
      * 中段字号按「低值/高值实测占位后剩下的可用宽度」二分自适应；
      * 三段之间至少留 ``gap``；放不下直接 raise，绝不静默挤压或降级。
    """
    low = st.get("low") or ""
    mean = st.get("mean") or ""
    high = st.get("high") or ""
    dir_ = st.get("dev_dir") or ""
    mag = st.get("dev_mag") or ""
    # 全角冒号自带字宽，不用空格凑间距（card-design 红线 5）
    mid = "均值" + mean + "：" + dir_ + mag
    col = C_GREEN if dir_ == "高" else C_RED

    w_low = _width(low, "bold", fs_range)
    w_high = _width(high, "bold", fs_range)
    left = x0 + w_low + gap
    right = x1 - w_high - gap
    avail = right - left
    if avail <= 0:
        raise RuntimeError(
            "[card-design 红线1] 统计行三段放不下：x0=%.1f x1=%.1f 低值宽=%.1f "
            "高值宽=%.1f gap=%.1f；应缩小字号或加宽统计区，不得挤压"
            % (x0, x1, w_low, w_high, gap))
    fs_mid = min(fs_dev_base, _fit_width_size(mid, "bold", avail, hi=fs_dev_base))
    w_mid = _width(mid, "bold", fs_mid)
    if w_mid > avail + 1:
        raise RuntimeError(
            "[card-design 红线1] 中段实测宽 %.1f 超过可用宽 %.1f" % (w_mid, avail))

    _text(d, boxes, tag + ".low", (x0, y), low, fs_range, C_LABEL, "ls")
    _text(d, boxes, tag + ".high", (x1, y), high, fs_range, C_LABEL, "rs")
    _text(d, boxes, tag + ".mid", ((left + right) / 2.0, y), mid, fs_mid, col, "ms")


def _bar2(c: Canvas, x: float, y: float, w: float, h: float, pct: float,
          is_high: bool, P) -> None:
    """圆角进度条：轨道 + 绿/红填充（高=绿/低=红，对齐参考卡）+ 白色定位点标当前 roll。

    定位点 x 夹在轨道圆角内（避免圆点被圆角裁切）；bar≈0 时（roll 低于下限）在左端画圆点。
    """
    r = h / 2
    c.rounded_rect((int(x), int(y), int(x + w), int(y + h)), radius=int(r), fill=C_TRACK)
    fill_w = max(0.0, min(1.0, pct)) * w
    col = C_GREEN if is_high else C_RED
    px = int(round(x + fill_w))
    px = max(int(x + r), min(int(x + w - r), px))   # 夹在轨道内
    if fill_w <= 0.5:
        c.ellipse((px - int(r), int(y), px + int(r), int(y + h)), fill=col)
        return
    if fill_w < h:
        cx = x + fill_w / 2
        c.ellipse((int(cx - r), int(y), int(cx + r), int(y + h)), fill=col)
        return
    c.rounded_rect((int(x), int(y), int(round(x + fill_w)), int(y + h)),
                   radius=int(r), fill=col)
    dot = max(2.0, h * 0.9)
    c.ellipse((px - int(dot), int(y - dot / 2), px + int(dot), int(y + h + dot / 2)),
              fill=C_WHITE, outline=col, width=max(1, int(h * 0.5)))


def _wrap_lines(text: str, role: str, size: int, max_w: float,
                max_lines: int = 12) -> list[str]:
    """按 :func:`_width` **实测步进宽**逐字折行（card-design：不按字符数估算宽度）。

    放不下就折行并让卡片变高（内容驱动高度）；超过 ``max_lines`` 直接 raise，
    绝不截断文本、也不缩成蚂蚁字冒充「一行放得下」。
    """
    lines: list[str] = []
    cur = ""
    for ch in text:
        if cur and _width(cur + ch, role, size) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        raise RuntimeError(
            "[card-design 红线2] 推荐区块折行 %d 行 > %d（max_w=%.1f, size=%d）：%r；"
            "应调整列宽/字号，不得裁切" % (len(lines), max_lines, max_w, size, text[:60]))
    return lines


def _recs_blocks(data: dict, fs: int, label_x: int, right: int,
                 gap_px: int) -> tuple[list[dict], int]:
    """底部「市场 / 推荐词条」区块的**固定列网格**布局（度量优先，显式 gap）。

    返回 ``(blocks, val_x)``；每个 block 含 key/label/lines/color/ys，``ys`` 是每行
    baseline（像素空间），由 :func:`build` 按实际折行数累加。
    """
    recs = data.get("recs")
    if not isinstance(recs, dict) or recs.get("status") not in ("ok", "error"):
        raise RuntimeError(
            "[card-design 接入] riven_analysis 需要 build_card_data() 产出的 recs "
            "（市场参考 / 推荐洗卡 / 推荐负面 / 数据来源）。缺失说明数据层没把真实 "
            "WFM 市场数据接进图片卡——2026-11-04 用户截图的缺陷，禁止静默省略。")
    ok = recs["status"] == "ok"
    rows = [
        ("recs.market", "*市场参考：", recs.get("market") or "", C_INK if ok else C_RED),
        ("recs.pos", "*推荐洗卡：", recs.get("pos") or "", C_INK if ok else C_RED),
        ("recs.neg", "*推荐负面：", recs.get("neg") or "", C_BRAND if ok else C_RED),
        # 2026-11-04 23:0x 新增：「正负面相同」精确匹配结果，无样本时如实写原因
        ("recs.exact", "*类似紫卡：", recs.get("exact") or "", C_GOLD if ok else C_RED),
        ("recs.source", "*数据来源：", recs.get("source") or "", C_FOOT),
        ("recs.hint", "*提示：", data.get("hint") or "", C_FOOT),
    ]
    label_w = max(_width(lbl, "bold", fs) for _, lbl, _, _ in rows)
    val_x = label_x + label_w + gap_px
    max_val_w = right - val_x
    if max_val_w <= 0:
        raise RuntimeError(
            "[card-design 红线1] 推荐区块值列宽 %.1f <= 0（标签列占满）" % max_val_w)
    blocks = []
    for key, lbl, val, color in rows:
        lines = _wrap_lines(val, "bold", fs, max_val_w)
        for ln in lines:
            w = _width(ln, "bold", fs)
            if w > max_val_w + 1:
                raise RuntimeError(
                    "[card-design 红线2] 推荐区块行宽 %.1f > %.1f：%r"
                    % (w, max_val_w, ln))
        blocks.append({"key": key, "label": lbl, "lines": lines,
                       "color": color, "ys": []})
    return blocks, val_x


# ---------------------------------------------------------------------------
# 主渲染
# ---------------------------------------------------------------------------
def build(t: Theme, data: dict | None = None, width: int | None = None, **kw) -> Canvas:
    """渲染紫卡分析卡。

    data 结构见 ``riven_card.build_card_data()``（已按参考卡补齐）：
        {
          "brand": "紫卡分析", "corner": "统一倍率",
          "weapon_zh": "伯斯顿", "weapon_en": "BURSTON PRIME",
          "riven_name": "Acri-igniata", "weapon_image": "…png" | None,
          "disp": 1.20, "disp_dots": "", "disp_value": "1.20",
          "level": 8,                                  # Lv 徽章（None 不画）
          "overall_grade": "A-", "overall_pct": "+3.910%",
          "layout": "3 正 1 负", "meta": "步枪 MR 0 满级",
          "stats": [{"value": "+192.2%", "name": "基础伤害", "sign": "+",
                     "range": "167.1% − 204.2%", "dev": "+3.542%", "grade": "A-",
                     "bar": 0.673, "neg": False, "icon": "#27C4D9",
                     "low": "167.1%", "mean": "185.6%", "high": "204.2%",
                     "dev_dir": "高", "dev_mag": "3.5%"}, ...],
          "market": "WM 挂牌中位 200P｜区间 190–220P（同等级 rank8 样本 100 / 时间）",
          "recs": {"status": "ok" | "error", "market": "…", "pos": "…", "neg": "…",
                   "exact": "正负面相同 N 条｜中位 …P（挂牌价·非成交）",
                   "source": "WM 挂牌要价·非成交价 · 匹配：… · slug · 时间"},
          "hint": "偏差以…±10%…",
          "foot_left": "紫卡分析", "foot_right": "2026-11-02 09:24",
        }
    """
    data = data or {}
    # 自检专用：传 list 收集真实渲染包围盒；生产运行时为 None，零开销、不改变出图。
    boxes = kw.get("boxes")
    width = int(width or getattr(t, "width", 800) or 800)
    s = width / BASE_W
    _rows = data.get("stats") or []
    n = max(1, min(4, len(_rows)))

    def P(v: float) -> int:
        return int(round(v * s))

    # 固定列网格（全部按 s 缩放一次，词条行/表头/进度条共用同一组起点）
    x0 = P(COL_STAT_X0)
    x1 = P(COL_STAT_X1)
    gap = P(STAT_GAP)
    grade_right = P(COL_GRADE_RIGHT)

    # ---- 字号（按参考卡墨迹尺寸反推到当前字体） ----
    fs_title = _size_for_w("伯斯顿", "bold", 61 * s)
    fs_value = _size_for_w("+192.2%", "bold", 78 * s)
    fs_grade = _size_for_h("A", "bold", 15 * s)
    fs_big = _size_for_h("A", "bold", 27 * s)
    fs_pct = _size_for_h("+3.910%", "bold", 11 * s)
    fs_layout = _size_for_h("3正1负", "bold", 11 * s)
    fs_name = _size_for_h("基础伤害", "bold", 10 * s)
    fs_range = _size_for_h("167.1%", "bold", 8 * s)
    # 统计中段字号的**上限**；实际字号由 _fit_width_size 按每行剩余宽度收敛（见 _stat_summary）
    fs_dev = _size_for_w("均值000.0%：低00.0%", "bold", 120 * s)
    fs_lbl = _size_for_h("综合评级", "bold", 6 * s)
    fs_head = _size_for_h("词条数值", "bold", 6 * s)
    fs_meta = _size_for_h("步枪满级", "bold", 7 * s)
    fs_foot = _size_for_h("紫卡分析", "bold", 5 * s)
    fs_sub = _size_for_h("BURSTON", "bold", 8 * s)
    fs_corner = _size_for_h("统一倍率", "bold", 5 * s)
    fs_disp = _size_for_h("倾向", "bold", 6 * s)
    fs_mkt = _size_for_h("市场参考", "bold", 6 * s)

    # ---- 动态高度：标题/评级区固定，词条行按需，底部按**实测折行数**累加 ----
    # 2026-11-04 22:25：新增市场/推荐词条区块后，底部不再是固定 2 行；
    # 失败态错误原因可能很长，必须先算折行再定高度（card-design 内容驱动高度）。
    last_top = ROW_TOP + ROW_PITCH * (n - 1)
    content_bottom = last_top + 44.0
    y_recs = P(content_bottom + 10.0)
    recs_blocks, recs_val_x = _recs_blocks(
        data, fs_mkt, P(PAD_L), grade_right, P(STAT_GAP))
    line_px = max(10.0, fs_mkt * 1.55)      # 行距 > 文本 bbox 高，保证不重叠
    block_gap_px = max(2.0, P(4.0))         # 块间显式 gap
    cursor = float(y_recs)
    for b in recs_blocks:
        for _ in b["lines"]:
            b["ys"].append(cursor + line_px * 0.45)   # baseline 落在行内
            cursor += line_px
        cursor += block_gap_px
    y_footrule = cursor + P(6.0)
    y_foot = y_footrule + P(10.0)
    h = int(round(y_foot + P(10.0)))

    c = Canvas(width, h, C_BG, theme=t)
    c.rect((0, 0, width, h), C_BG)
    d = c.draw

    # ---- 页眉 ----
    d.rectangle((P(6), P(7), P(6) + max(2, P(4)), P(7) + max(2, P(3))), fill=C_BRAND)
    _text(d, boxes, "header.brand", (P(13), P(11.5)), data.get("brand", "紫卡分析"),
          fs_foot, C_BRAND, "ls")
    _text(d, boxes, "header.corner", (grade_right, P(10.5)), data.get("corner", "统一倍率"),
          fs_corner, C_LABEL, "rs")

    # ---- 标题（中英同字号） + Lv 徽章 ----
    title = data.get("weapon_zh") or ""
    if data.get("weapon_suffix"):
        title += f" {data['weapon_suffix']}"
    tw = _width(title, "bold", fs_title)
    _text(d, boxes, "title.zh", (P(5), P(45)), title, fs_title, C_INK, "ls")
    _lv_badge(c, P(5) + tw + P(10), P(40), data.get("level"), fs_title, s, P)

    # ---- 武器立绘（右上角，等比缩放） ----
    if not _paste_weapon(c, data.get("weapon_image"), P(200), P(13), P(90), P(60)):
        logger.warning(
            "riven_analysis: weapon_image 缺失或绘制失败，改绘武器剪影兜底 "
            "weapon_zh=%r weapon_en=%r type=%r path=%r",
            data.get("weapon_zh"), data.get("weapon_en"),
            data.get("weapon_type"), data.get("weapon_image"))
        _weapon_fallback(c, P(200), P(13), P(90), P(60),
                         data.get("weapon_zh"), data.get("weapon_en"), P, s,
                         data.get("weapon_type"), boxes)

    # ---- 英文副标题（大写 + 宽字距） ----
    _spaced_text(c, (P(6), P(58.5)), (data.get("weapon_en") or "").upper(),
                 fs_sub, C_INK, "bold", 0.95 * s)

    # ---- 倍率行（现行统一 1.20，不再画星级点阵） ----
    _disp_line(c, P(6), P(70), data.get("disp_dots", ""), data.get("disp_value", ""),
               fs_disp, s, P)

    # ---- 金色通栏线 ----
    c.rect((P(PAD_L), P(77), P(BASE_W - PAD_R), P(78)), C_GOLD_LINE)

    # ---- 综合评级 ----
    _text(d, boxes, "overall.label", (P(6), P(91)), "综合评级", fs_lbl, C_LABEL, "ls")
    _grade_glyph(c, P(7), P(125), data.get("overall_grade", "-"), fs_big, C_GOLD, s,
                 boxes=boxes, label="overall.grade")
    _text(d, boxes, "overall.pct", (P(58), P(125)), data.get("overall_pct", ""),
          fs_pct, C_GOLD, "ls")
    _text(d, boxes, "overall.layout", (grade_right, P(110)), data.get("layout", ""),
          fs_layout, C_GOLD, "rs")
    _meta_line(c, grade_right, P(124), data.get("meta", ""), fs_meta, s)

    # ---- 表头 ----
    c.rect((P(PAD_L), P(136), P(BASE_W - PAD_R), P(137)), C_RULE)
    cols = data.get("columns") or ["词条数值", "低值·均值·高/低", "评级"]
    _text(d, boxes, "head.value", (P(6), P(148)), cols[0], fs_head, C_HEAD, "ls")
    _text(d, boxes, "head.stat", (x0, P(148)), cols[1], fs_head, C_HEAD, "ls")
    _text(d, boxes, "head.grade", (grade_right, P(148)), cols[2], fs_head, C_HEAD, "rs")
    c.rect((P(PAD_L), P(153), P(BASE_W - PAD_R), P(153)), C_RULE2)

    # ---- 词条行（固定列网格：x0/x1/grade_right 全行共用，不随内容宽度漂移） ----
    for i, st in enumerate(_rows):
        top = ROW_TOP + ROW_PITCH * i
        tag = "row%d" % (i + 1)
        neg = bool(st.get("neg"))
        vcolor = C_BRAND if neg else C_INK
        # 词条左侧彩色圆形图标
        _icon_dot(c, P(COL_ICON_X), P(top + 20), 4.5 * s, st.get("icon") or C_LABEL)
        # 数值 + 词条名（同一左列起点；按实测可用宽度收敛字号，绝不越界压到统计区）
        val = st.get("value", "") or ""
        nm = st.get("name", "") or ""
        left_w = x0 - P(COL_VALUE_X) - gap
        fs_val = min(fs_value, _fit_width_size(val, "bold", left_w, hi=fs_value)) if val else fs_value
        fs_nm = min(fs_name, _fit_width_size(nm, "bold", left_w, hi=fs_name)) if nm else fs_name
        _text(d, boxes, tag + ".value", (P(COL_VALUE_X), P(top + 15)),
              val, fs_val, vcolor, "ls")
        _text(d, boxes, tag + ".name", (P(COL_VALUE_X), P(top + 32)),
              nm, fs_nm, C_NAME, "ls")
        # 统计三段：低值左锚 x0 / 中段自适应居中 / 高值右锚 x1，显式 gap
        _stat_summary(d, boxes, tag + ".stat", x0, P(top + 13), x1, st,
                      fs_range, fs_dev, gap)
        # 进度条（与统计区同一列宽，绿/红填充 + 定位点）
        _bar2(c, x0, P(top + 28), x1 - x0, max(2, P(BAR_H)),
              float(st.get("bar") or 0.0), st.get("dev_dir") == "高", P)
        # 评级（右锚定到内容右边界，不随前序内容变长而位移）
        _grade_glyph(c, grade_right, P(top + 22), st.get("grade", "-"),
                     fs_grade, C_GOLD, s, anchor="rs",
                     boxes=boxes, label=tag + ".grade")

    # ---- 市场参考 / 推荐洗卡 / 推荐负面 / 数据来源 / 提示 ----
    # 2026-11-04 22:25：整块只走 _text，包围盒统一登记到 "recs" 层，
    # 让 check_riven_layout.py 能抓到任意两行之间的重叠与间距失控。
    rule_y = int(y_recs - P(5))
    c.rect((P(PAD_L), rule_y, grade_right, rule_y + max(1, P(0.8))), C_GOLD_LINE)
    for b in recs_blocks:
        _text(d, boxes, b["key"] + ".label", (P(PAD_L), b["ys"][0]),
              b["label"], fs_mkt, C_LABEL, "ls")
        for i, ln in enumerate(b["lines"]):
            _text(d, boxes, "%s.line%d" % (b["key"], i + 1),
                  (recs_val_x, b["ys"][i]), ln, fs_mkt, b["color"], "ls")

    # ---- 页脚 ----
    c.rect((P(PAD_L), int(y_footrule), grade_right,
            int(y_footrule) + max(1, P(1))), C_RULE)
    _text(d, boxes, "foot.left", (P(6), int(y_foot)), data.get("foot_left", "紫卡分析"),
          fs_foot, C_BRAND, "ls")
    _text(d, boxes, "foot.right", (grade_right, int(y_foot)), data.get("foot_right", ""),
          fs_foot, C_FOOT, "rs")
    return c


# ---------------------------------------------------------------------------
# 局部绘制
# ---------------------------------------------------------------------------
def _grade_glyph(c: Canvas, x: int, y: int, grade: str, size: int, color: str,
                 s: float, anchor: str = "ls", boxes=None, label: str = "grade") -> None:
    """评级字母：字母 + **自绘短横**。

    参考卡的「A−」不是靠字体里的连字符（太细太短），而是一条粗横杠
    （实测 47px 总宽、横杠约 16×4px、位于字母中部偏下）。故拆成「字母 + 矩形」画，
    保证与参考卡形状一致；无减号时只画字母。
    """
    g = (grade or "").replace("−", "-").replace("–", "-").strip()
    base = g.rstrip("-")
    dash = len(g) != len(base)
    f = _font("bold", size)
    letter_w = _width(base, "bold", size)
    dash_w = size * 0.42
    gap = size * 0.04
    total = letter_w + (gap + dash_w if dash else 0)
    x0 = x - total if anchor.endswith("s") and anchor.startswith("r") else x
    _text(c.draw, boxes, label, (x0, y), base, size, color, "ls")
    if dash:
        # 横杠垂直位置：字母高度的 42%~58%（对照参考卡 y=111..114 / 字母 98..124）
        bb = f.getbbox(base)
        cap = bb[3] - bb[1]
        ty = y - cap * 0.42
        th = max(2.0, cap * 0.17)
        c.rect((int(x0 + letter_w + gap), int(ty - th / 2),
                int(x0 + letter_w + gap + dash_w), int(ty + th / 2)), color)


def _bar(c: Canvas, x: int, y: int, w: int, h: int, pct: float, P) -> None:
    """圆角进度条：轨道 #D1CEC6 + 金色填充。端点全圆角（参考卡两端都是半圆）。"""
    r = h / 2
    c.rounded_rect((x, y, x + w, y + h), radius=int(r), fill=C_TRACK)
    fill_w = max(0.0, min(1.0, pct)) * w
    if fill_w <= 0.5:
        return
    if fill_w < h:  # 太短时画成圆点，避免出现方角
        cx = x + fill_w / 2
        c.ellipse((int(cx - r), y, int(cx + r), y + h), fill=C_GOLD)
        return
    c.rounded_rect((x, y, x + int(round(fill_w)), y + h), radius=int(r), fill=C_GOLD)


def _meta_line(c: Canvas, right: int, baseline: int, text: str, size: int, s: float) -> None:
    """「步枪 MR 0 满级」：整行右对齐；其中「MR N」用深墨加粗，其余用灰。

    参考卡实测：整行右边界 x286、基线 y124，「MR 0」明显比两侧中文更黑。
    做法：先整行灰色画一遍，再按前缀宽度把「MR N」用深色重画一遍（同字号，
    前缀宽度可加，误差 < 0.5px），比逐词右排更稳。
    """
    if not text:
        return
    f = _font("bold", size)
    total = _width(text, "bold", size)
    x0 = right - total
    c.draw.text((x0, baseline), text, font=f, fill=C_FOOT, anchor="ls")
    m = _re.search(r"MR\s*\d+", text)
    if m:
        dx = _width(text[:m.start()], "bold", size)
        c.draw.text((x0 + dx, baseline), m.group(0), font=f, fill=C_INK, anchor="ls")


def _paste_weapon(c: Canvas, path: str | None, x: int, y: int, w: int, h: int) -> bool:
    """把武器立绘等比贴进右上角方框（右对齐、垂直居中）。

    返回是否成功贴图：缺路径/坏图/绘制异常都返回 False，由调用方改绘占位框，
    保证右上角永不空白。
    """
    if not path:
        return False
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001 — 缺图/坏图不影响出卡
        return False
    try:
        ratio = min(w / img.width, h / img.height)
        nw, nh = max(1, int(img.width * ratio)), max(1, int(img.height * ratio))
        img = img.resize((nw, nh), Image.LANCZOS)
        # **必须带 mask**：DE/WFCD 的武器 PNG 都是透明底（Acceltra 实测 80% 像素 alpha=0），
        # 不带 mask 的 paste 会把透明区的 RGB 当成实色盖上去，右上角出现一块死色方块。
        c.paste(img, (x + w - nw, y + (h - nh) // 2), mask=img)
        return True
    except Exception:  # noqa: BLE001
        return False


def _fit_size(text: str, role: str, max_w: float, max_h: float) -> int:
    """二分出「墨迹宽 <= max_w 且字号 <= max_h」的最大字号（占位框内文字自适应）。"""
    best = 4
    lo, hi = 4, 96
    for _ in range(14):
        mid = (lo + hi) // 2
        if mid > max_h or _ink_w(text, role, mid) > max_w:
            hi = mid - 1
        else:
            best, lo = mid, mid + 1
        if lo > hi:
            break
    return max(4, best)


def _weapon_fallback(c: Canvas, x: int, y: int, w: int, h: int,
                     zh: str | None, en: str | None, P, s: float,
                     category: str | None = None, boxes=None) -> None:
    """取不到官方立绘时，在右上角绘制一张**武器剪影图**＋ 武器名小标。

    用户明确要求「武器图片」而非文字框，故兜底画一张品牌紫武器剪影
    （枪械/近战按 category 区分），下方留一行武器名便于辨识。剪影是透明底 RGBA，
    用 ``paste(mask=)`` 合成不会留下白块；整框用品牌紫描边收口，视觉上就是一枚
    武器图标，右上角**永不空白**。

    ``category`` 取 riven_card 透传的 ``weapon_type``（Rifle/Pistol/Shotgun/Melee/Zaw…），
    None 时退化为枪械剪影，保证任何武器都有图可画。
    """
    # 品牌紫描边收口（极细、锐利，与参考卡边框一致）
    c.rounded_rect((x, y, x + w, y + h), radius=0,
                   outline=C_BRAND, width=max(1, P(1.2)))
    if boxes is not None:
        # 单独一层（weaponframe），避免与 weapon.label 在同层被判「重叠」；
        # 门禁用它核对武器名小标必须留在框内。
        boxes.append(("weaponframe.rect", x, y, x + w, y + h))
    # 剪影占上方 ~74%，底部留行放武器名
    strip = int(h * 0.26)
    sil_h = h - strip
    if sil_h > 4:
        try:
            sil = weapon_icons.build_silhouette(category, w, sil_h, C_BRAND)
            # 透明底：必须以剪影自身作 mask 合成，否则透明区会糊成黑块
            c.paste(sil, (x, y), mask=sil)
        except Exception:  # noqa: BLE001 — 剪影兜底也失败就留空框，绝不崩
            logger.warning("riven_analysis: 武器剪影绘制失败 category=%r", category)
    # 武器名小标（底行居中）——2026-11-04 23:3x 修复
    # 旧版拿「字号 size」当排版框高度，anchor="la" 的真实行高比 size 大约 20%，
    # 实测 CJK 标签压到剪影框底线外 2~3px（card-design 红线 2：溢出/裁切）。
    # 现在按 textbbox **实测**行高在底行内居中，并显式断言整框落在剪影框内；
    # 同时走唯一文本入口 _text 登记包围盒，让自检能看见这行字。
    label = (zh or "").strip() or ((en or "").upper().strip())
    if label and strip > 6:
        size = _fit_size(label, "bold", w - 2 * P(5), strip * 0.62)
        tw = _width(label, "bold", size)
        tx = x + (w - tw) / 2.0
        f = _font("bold", size)
        probe = c.draw.textbbox((0, 0), label, font=f, anchor="lm")
        ty = y + sil_h + (strip - (probe[3] - probe[1])) / 2.0 - probe[1]
        bb = c.draw.textbbox((tx, ty), label, font=f, anchor="lm")
        if (bb[0] < x or bb[1] < y or bb[2] > x + w or bb[3] > y + h):
            raise RuntimeError(
                "[card-design 红线2] 武器名小标 %r 排版框 %s 越出剪影框 %s"
                % (label, bb, (x, y, x + w, y + h)))
        _text(c.draw, boxes, "weapon.label", (tx, ty), label, size, C_INK, "lm")
