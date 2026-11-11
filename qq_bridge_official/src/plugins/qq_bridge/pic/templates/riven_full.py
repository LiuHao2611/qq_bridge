# -*- coding: utf-8 -*-
"""pic/templates/riven_full.py —— 紫卡全面分析卡（参考卡版式 + 项目自有背景/图标，去 Rabbit 化）。

参考卡实测 655×1920（2026-11-06，逐像素取样，见 outputs/ref_grid*.png）：

    主蓝 #1A5F87   品牌紫 #6B3F7A   绿 #3D8B43   橙 #B86B14   红 #A83C2E
    弱灰 #6E7078   墨 #1A191C      卡框 #D8D3E2   表格线 #3A3A3A
    评分框底 #F1F8EF/边 #A8CEA9   价格框底 #FBF9FC/边 #DED7E8

版式（设计空间 BASE_W=655、BASE_H=1920，S=width/655）：

    y18-56    页眉：黑底放大镜 + 居中「紫卡分析」蓝字；y71 通栏细线
    y110-145  ▷ 紫卡数值分析（项目自有图标 + 蓝色大标题）
    y168-484  数值圆角卡：武器名/Lv 徽章/倾向药丸 → 3 行词条（数值·图标·名称·
              低值·均值·高低·高值 + 绿色进度条）→ 市场负面参考 + 提示
    y500-535  ▷ 融融合词条分析
    y554-858  三列表格（状态 28-212 / 词条融合结果 212-430 / 词条数值 430-626）
              表头 554-585，4 行 585/653/721/790/858
    y880-915  ▷ 紫卡综合评分
    y943-1023 三评分框（28-182 / 191-328 / 339-492）
    y1033-1205 双价格框（28-322 / 332-626）
    y1212-1235 最近观测
    y1255-1295 ▷ 基于击杀效率配卡 - 需极化4次
    y1341-1403 / 1419-1481 8 张 MOD 缩略卡（4 列 × 2 行）
    y1510-1585 击杀效率 / 紫卡增益两行
    y1598-1730 ▷ [指令]查看紫卡其他数据 + 4 条双列指令 + 注
    y1748-1860 ▷ [指令]查看类似紫卡价格 + 2 条指令
    y1872-1905 页脚细线 + PC 徽章 + 项目自有品牌「曼珠」 + 提示

2026-11-11 去 Rabbit 化（用户决策，仅官方副本）：
  * 背景 = 项目 light 主题贴纸底（decor.background(c, t, tpl="riven_full")），
    替代原来的纯白 C_BG；主题必须绑定 light（见 pic/card_binds.py），否则拿到 neon 装饰。
  * 去掉 Rabbit 卡通 chibi.png（原右下 515..655 × 1740..1920）。
  * 兔耳 bunny.png / bunny_dark.png → 项目自有几何图标（pic/icons.py，solid/star/bolt）。
  * 页脚「Warframe Rabbit」→ 项目自有品牌「曼珠」（与 checkin.py:382 同源）。
其余全部按参考卡坐标还原。MOD 缩略卡、放大镜/购物车/柱状图图标为功能前景内容，
保留；仅去除 Rabbit 专属的卡通/兔耳/品牌三处。

度量纪律：所有文字经 ``T``/``Tokens`` 用 ``Canvas.text_width`` 实测宽度定位，
列起点为常量、行尾右锚定；``boxes`` 收集真实包围盒供门禁检测溢出/重叠/间距。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image

from .. import fonts
from .. import decor
from .. import icons as _icons
from ..canvas import Canvas
from ..theme import Theme

logger = logging.getLogger("qq_bridge")

BASE_W = 655.0
BASE_H = 1920.0
ASSETS = Path(__file__).resolve().parent / "riven_full_assets"

C_BG = "#FFFFFF"
C_BLUE = "#1A5F87"
C_PURPLE = "#6B3F7A"
C_GREEN = "#3D8B43"
C_GREEN_LT = "#8DBE92"
C_ORANGE = "#B86B14"
C_RED = "#A83C2E"
C_GRAY = "#6E7078"
C_GRAY_LT = "#9A9CA2"
C_DARK = "#1A191C"
C_RULE = "#C9C4CB"
C_CARD_BD = "#D8D3E2"
C_CARD_BG = "#FCFAFD"
C_TABLE = "#3A3A3A"
C_SCORE_BG = "#F1F8EF"
C_SCORE_BD = "#A8CEA9"
C_PRICE_BG = "#FBF9FC"
C_PRICE_BD = "#DED7E8"
C_PILL_P_BG = "#F5EDF8"
C_PILL_P_BD = "#C9A8D8"
C_PILL_B_BG = "#EDF4FA"
C_PILL_B_BD = "#A8C8DE"
C_WHITE = "#FFFFFF"

# 固定列网格（设计空间坐标，一经定义全卡不变）
L = 28.0                 # 内容左边界
R = 626.0                # 内容右边界
COL_T2 = 212.0           # 表格第 2 列起点
COL_T3 = 430.0           # 表格第 3 列起点
COL_STAT_LOW = 278.0     # 数值行：低值起点
COL_STAT_BAR_R = 594.0   # 数值行：进度条右端
COL_STAT_HIGH_R = 600.0  # 数值行：高值右锚
COL_TVAL_R = 608.0       # 表格数值列：进度条右端
COL_TDEV_R = 613.0       # 表格数值列：「高X%」实测右端 613


def _font(role: str, size: int):
    return fonts.load(role, max(4, int(size)))


def _fmt(v) -> str:
    try:
        return "{:,}".format(int(round(float(v))))
    except (TypeError, ValueError):
        return "-"


def _check_boxes(boxes: list, min_gap: float = 2.0, pad: float = L,
                 width: float = BASE_W, height: float = BASE_H) -> dict:
    """门禁：溢出 / 重叠 / 间距。三个列表全空才算通过。"""
    overflow: list[str] = []
    overlap: list[str] = []
    spacing: list[str] = []
    for (x0, y0, x1, y1, tag) in boxes:
        if x1 > width - pad + 2:
            overflow.append("right %s (%.0f>%.0f)" % (tag, x1, width - pad + 2))
        if x0 < pad - 2:
            overflow.append("left %s (%.0f<%.0f)" % (tag, x0, pad - 2))
        if y1 > height + 2:
            overflow.append("bottom %s (%.0f)" % (tag, y1))
        if y0 < -2:
            overflow.append("top %s (%.0f)" % (tag, y0))
    n = len(boxes)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            ox = min(a[2], b[2]) - max(a[0], b[0])
            if ox <= min_gap:
                continue
            v_gap = max(a[1], b[1]) - min(a[3], b[3])
            if v_gap < 0:
                overlap.append("%s<>%s (ox=%.1f v=%.1f)" % (a[4], b[4], ox, v_gap))
            elif v_gap < min_gap:
                spacing.append("%s<>%s (v_gap=%.1f)" % (a[4], b[4], v_gap))
    return {"overflow": overflow, "overlap": overlap, "spacing": spacing}


def _asset(name: str) -> Image.Image | None:
    p = ASSETS / name
    if not p.is_file():
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001 — 资产缺失不拖垮出图
        return None


def build(t: Theme, data: dict | None = None, width: int | None = None,
          boxes: list | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or BASE_W)
    S = width / BASE_W
    c = Canvas(width, int(BASE_H * S), C_BG, theme=t)
    # 项目自有 light 主题贴纸底（替代 Rabbit 粉色兔纹/纯白底）；tpl 命中卡片专属
    # 背景覆盖，缺失键回落主题级全局值。不登记内容 boxes，不影响版式门禁。
    decor.background(c, t, tpl="riven_full")
    d = c.draw
    if boxes is not None:
        boxes.clear()

    def P(v: float) -> int:
        return int(round(v * S))

    def T(x, y, text, size, color, role="regular", anchor="ls", register=True):
        """度量优先的文字入口：先测宽、再定位，顺带登记真实包围盒。"""
        d.text((P(x), P(y)), str(text), font=_font(role, size * S),
               fill=color, anchor=anchor)
        if boxes is not None and register and str(text):
            wpx = c.text_width(str(text), size * S, role) / S
            if anchor == "rs":
                x0, x1 = x - wpx, x
            elif anchor == "ms":
                x0, x1 = x - wpx / 2, x + wpx / 2
            else:
                x0, x1 = x, x + wpx
            boxes.append((x0, y - size * 0.85, x1, y + size * 0.20,
                          str(text)[:14]))
            return x1
        return x + (c.text_width(str(text), size * S, role) / S)

    def tw(text, size, role="regular") -> float:
        return c.text_width(str(text), size * S, role) / S

    def fit(text, target_w, role="bold", lo=9, hi=32) -> int:
        """按参考卡实测墨迹宽度反推字号（度量优先、字体无关）。

        target_w 为参考卡实测步进宽；``hi`` 是字号上限，防止「—」这类短占位符
        被反推成超大字号压到相邻行（真实踩过的坑）。
        """
        best = lo
        orig_hi = hi
        for _ in range(12):
            mid = (lo + hi) // 2
            if tw(str(text), mid, role) <= target_w:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
            if lo > hi:
                break
        return max(9, min(best, orig_hi))

    # 参考卡实测墨迹宽度目标（设计空间 px）；换字体也不跑版
    W_HEADER = 120        # 「紫卡分析」4 字（参考卡实测墨迹 288..405 = 117px）
    W_SEC = 196           # 章节大标题 6 字（参考卡实测墨迹 ~184px）
    W_SEC_SMALL = 146     # 参考卡「融合词条分析」实测更小（~24px）
    W_SEC_LONG = 436      # 参考卡长标题实测 419px，不能压缩成小字
    W_CMD_TITLE = 232     # 「[指令]查看紫卡其他数据」实测 228px
    W_WEAPON = 190        # 「阿利乌双枪 ArmaDex」
    W_STAT_VAL = 82       # 「+135.4%」
    W_STAT_NAME = 80      # 「触发几率」
    W_STAT_LOW = 52       # 「120.3%」
    W_STAT_MEAN = 94      # 「均值133.7%·」
    W_STAT_DEV = 70       # 「高1.3%」
    W_STAT_HIGH = 52      # 「147.0%」
    W_TH_STATE = 40       # 表头「状态」
    W_TH_RESULT = 118     # 表头「词条融合结果」
    W_TH_VALUE = 78       # 表头「词条数值」
    W_T_VAL = 90          # 表格数值「约142.4%」
    W_T_DEV = 56          # 表格「高6.5%」
    W_SCORE_BIG = 112     # 「10.1 (F)」
    W_PRICE_BIG = 166     # 「暂无匹配样本」
    W_PRICE_BIG_R = 68    # 「386P」
    W_PRICE_TITLE = 140   # 「市场价格预计」
    W_FOOT_TIP = 160      # 「*善用右键/长按提取文字」

    def clip(text, size, role, max_w) -> str:
        """长文本按实测宽截断加「…」（同层字号保持一致，不靠缩字号糊弄）。"""
        s = str(text)
        if tw(s, size, role) <= max_w:
            return s
        while s and tw(s + "…", size, role) > max_w:
            s = s[:-1]
        return (s + "…") if s else "…"

    def Tokens(x, y, parts, size, role="bold", gap=0.0, anchor="ls"):
        """多色混排：按实测宽度逐段推进，返回结束 x。"""
        total = sum(tw(txt, size, role) for txt, _ in parts) + gap * max(0, len(parts) - 1)
        cur = x - total if anchor == "rs" else (x - total / 2 if anchor == "ms" else x)
        for txt, col in parts:
            cur = T(cur, y, txt, size, col, role, "ls") + gap
        return cur

    def wrap(text, size, role, max_w):
        """CJK 逐字、ASCII 按词整体换行（不拆 Endo / Lv8 / ±10%）。"""
        units = re.findall(r"[A-Za-z0-9][A-Za-z0-9,.=/%+\-_:：]*|\s+|.", str(text))
        lines, cur = [], ""
        for u in units:
            cand = cur + u
            if cur and tw(cand, size, role) > max_w:
                lines.append(cur.rstrip())
                cur = "" if u.isspace() else u
            else:
                cur = cand
        if cur.strip():
            lines.append(cur.rstrip())
        return lines or [""]

    def paste_asset(name, x0, y0, x1, y1):
        im = _asset(name)
        if im is None:
            return False
        w, h = max(1, P(x1 - x0)), max(1, P(y1 - y0))
        im = im.resize((w, h), Image.LANCZOS)
        c.paste(im, (P(x0), P(y0)), im)
        return True

    def section(y_base, title, target_w=W_SEC, x=72.0, icon_w=30.0,
                icon_x=None):
        """项目自有 solid 图标 + 蓝色大标题，字号按该类实测墨迹宽反推。

        2026-11-11 去 Rabbit 化：原兔耳 bunny.png → 项目 pic/icons.py 的
        bolt（能量感，契合 Warframe）。参考卡各节标题并非同一字号：「融合词条
        分析」明显小于其余章节，长标题「基于击杀效率配卡 - 需极化4次」则是
        大字号通栏（不压缩）。图标占位与原兔耳一致，不挤占标题。
        """
        ix = (x - 40) if icon_x is None else icon_x
        _icons.draw(c, "bolt", P(ix + icon_w / 2), P(y_base - 16),
                    P(max(18, icon_w)), C_BLUE, style="solid")
        T(x, y_base, title, fit(title, target_w, hi=34), C_BLUE, "bold")

    def diamond(cx, cy, r, color):
        d.polygon([(P(cx), P(cy - r)), (P(cx + r), P(cy)),
                   (P(cx), P(cy + r)), (P(cx - r), P(cy))], fill=color)

    def bar(x0, x1, y, pct, color=C_GREEN, ring=False):
        """绿色进度条：两端圆点 + 中位刻度 + 当前值定位点。"""
        y = P(y)
        c.line((P(x0), y, P(x1), y), fill=color, width=max(1, P(1.6)))
        rr = max(1, P(3.2))
        for ex in (x0, x1):
            c.ellipse((P(ex) - rr, y - rr, P(ex) + rr, y + rr), fill=color)
        mid = (x0 + x1) / 2
        c.line((P(mid), y - P(6), P(mid), y + P(6)), fill=C_GRAY_LT,
               width=max(1, P(1)))
        cx = x0 + max(0.0, min(1.0, pct)) * (x1 - x0)
        cr = max(2, P(7))
        if ring:
            c.ellipse((P(cx) - cr - P(2), y - cr - P(2), P(cx) + cr + P(2), y + cr + P(2)),
                      fill=C_WHITE, outline=color, width=max(1, P(1.5)))
        c.ellipse((P(cx) - cr, y - cr, P(cx) + cr, y + cr), fill=color)

    # ---------------- [页眉] ----------------
    ic_s = 32.0
    title = "紫卡分析"
    fs_title = fit(title, W_HEADER)
    tw_title = tw(title, fs_title, "bold")
    total = ic_s + 8 + tw_title
    hx = (BASE_W - total) / 2
    paste_asset("search.png", hx, 20, hx + ic_s, 20 + ic_s)
    T(hx + ic_s + 8, 52, title, fs_title, C_BLUE, "bold")
    c.line((P(43), P(71), P(R - 14), P(71)), fill=C_RULE, width=max(1, P(1)))

    # ---------------- [1 紫卡数值分析] ----------------
    section(142, "紫卡数值分析")
    c.rounded_rect((P(L), P(168), P(R), P(484)), radius=P(12),
                   fill=C_CARD_BG, outline=C_CARD_BD, width=max(1, P(1.2)))
    # 武器名 + Lv 徽章 + 倾向药丸
    wz = str(data.get("weapon_zh") or "未指定武器")
    rn = str(data.get("riven_name") or "")
    head = wz + (" " + rn if rn else "")
    head_x = T(45, 205, head, fit(head, W_WEAPON), C_PURPLE, "bold")
    lv = data.get("level")
    if lv is not None:
        lvtxt = "Lv%s" % lv
        bw = tw(lvtxt, 17, "bold") + P(14) / S
        bx = head_x + 12
        c.rounded_rect((P(bx), P(182), P(bx + bw), P(214)), radius=P(4), fill=C_PURPLE)
        T(bx + 7, 205, lvtxt, 17, C_WHITE, "bold")
        pill_x = bx + bw + 10
    else:
        pill_x = head_x + 12
    # 倾向药丸：◉ 倾向 ●●●●○ (1.20)
    pw = 205.0
    pill_x = min(pill_x, R - 16 - pw)
    c.rounded_rect((P(pill_x), P(180), P(pill_x + pw), P(214)), radius=P(17),
                   fill=C_WHITE, outline=C_PILL_P_BD, width=max(1, P(1.2)))
    c.ellipse((P(pill_x + 12), P(194), P(pill_x + 20), P(202)),
              outline=C_PURPLE, width=max(1, P(1.4)))
    c.ellipse((P(pill_x + 15.2), P(197.2), P(pill_x + 16.8), P(198.8)), fill=C_PURPLE)
    px = pill_x + 26
    px = T(px, 205, "倾向", 16, C_PURPLE, "bold") + 8
    filled = int(data.get("disp_filled") or 3)
    for i in range(5):
        rr = 5.4
        cx = px + i * 15 + rr
        if i < filled:
            c.ellipse((P(cx - rr), P(198 - rr), P(cx + rr), P(198 + rr)), fill=C_PURPLE)
        else:
            c.ellipse((P(cx - rr), P(198 - rr), P(cx + rr), P(198 + rr)),
                      outline=C_PURPLE, width=max(1, P(1.2)))
    px += 5 * 15 + 6
    T(px, 205, "(%s)" % str(data.get("disp_value") or "—"), 15, C_PURPLE, "regular")

    c.line((P(45), P(218), P(R - 16), P(218)), fill=C_CARD_BD, width=max(1, P(1)))

    # 词条行：最多 4 条（Warframe 3正1负等）。3 条沿用原行距（参考卡像素级回归
    # 零影响）；4 条才用紧凑行距，把第 4 条塞进 stats 卡片（y=168..484），
    # 最后一行 bar y=383 距下方市场负面参考 y=412 仍有净空。
    stats = list(data.get("stats") or [])
    if len(stats) >= 4:
        row_base = [250.0, 292.0, 334.0, 376.0]
        bar_y = [257.0, 299.0, 341.0, 383.0]
        n_rows = 4
    else:
        row_base = [258.0, 307.0, 357.0]
        bar_y = [265.0, 314.0, 363.0]
        n_rows = 3
    for i in range(n_rows):
        st = stats[i] if i < len(stats) else None
        yb, ybar = row_base[i], bar_y[i]
        if not st:
            T(45, yb, "需紫卡截图", 17, C_GRAY, "regular")
            continue
        val_col = C_PURPLE
        vtext = st.get("value") or "—"
        T(50, yb, vtext, fit(vtext, W_STAT_VAL, hi=21), val_col, "bold")
        # 正/负 六边形小图标
        neg = bool(st.get("neg"))
        hx0, hy0, r = 152.0, yb - 12, 9.0
        pts = [(P(hx0 + r * 0.87), P(hy0 + r * 0.5)),
               (P(hx0 + r * 0.5), P(hy0 + r)),
               (P(hx0 - r * 0.5), P(hy0 + r)),
               (P(hx0 - r * 0.87), P(hy0 + r * 0.5)),
               (P(hx0 - r * 0.5), P(hy0)), (P(hx0 + r * 0.5), P(hy0))]
        d.polygon(pts, outline=C_RED if neg else "#7FA8BF", width=max(1, P(1.2)))
        T(hx0, hy0 + r - 1, st.get("icon") or "正", 9, C_RED if neg else "#5C89A8",
          "bold", "ms")
        nm = str(st.get("name") or "—")
        T(175, yb, nm, fit(nm, W_STAT_NAME, hi=21), C_BLUE, "bold")
        low = str(st.get("low") or "—")
        T(COL_STAT_LOW, yb, low, fit(low, W_STAT_LOW, "regular", hi=18),
          C_BLUE, "regular")
        # 均值 + 高低（同基线，逐段实测推进，右锚高值）
        mx = 358.0
        mean_txt = "均值" + str(st.get("mean") or "—") + "·"
        mx = T(mx, yb, mean_txt, fit(mean_txt, W_STAT_MEAN, "regular", hi=17),
               C_GRAY, "regular")
        dev = str(st.get("dev") or "—")
        dev_color = C_GREEN if not dev.startswith("低") else C_RED
        T(mx, yb, dev, fit(dev, W_STAT_DEV, hi=21), dev_color, "bold")
        high = str(st.get("high") or "—")
        T(COL_STAT_HIGH_R, yb, high, fit(high, W_STAT_HIGH, "regular", hi=18),
          C_BLUE, "regular", "rs")
        bar(COL_STAT_LOW, COL_STAT_BAR_R, ybar, float(st.get("bar") or 0.5))

    # 市场负面参考 + 提示
    T(45, 412, str(data.get("market_note") or ""), 17, C_BLUE, "regular")
    hint = "*提示：" + str(data.get("hint") or "")
    hl = wrap(hint, 16, "regular", R - 90)
    for i, ln in enumerate(hl[:2]):
        T(45, 442 + i * 24, ln, 16, C_GRAY, "regular")

    # ---------------- [2 融合词条分析] ----------------
    section(532, "融合词条分析", target_w=W_SEC_SMALL, x=64.0,
            icon_w=16.0, icon_x=32.0)
    # 表格外框 + 列线 + 行线
    c.rounded_rect((P(L), P(554), P(R), P(858)), radius=P(4),
                   fill=C_WHITE, outline=C_TABLE, width=max(1, P(1.2)))
    c.line((P(COL_T2), P(554), P(COL_T2), P(858)), fill=C_TABLE, width=max(1, P(1)))
    c.line((P(COL_T3), P(554), P(COL_T3), P(858)), fill=C_TABLE, width=max(1, P(1)))
    c.line((P(L), P(585), P(R), P(585)), fill=C_TABLE, width=max(1, P(1)))
    T(38, 580, "状态", fit("状态", W_TH_STATE), C_BLUE, "bold")
    T(221, 580, "词条融合结果", fit("词条融合结果", W_TH_RESULT), C_BLUE, "bold")
    T(441, 580, "词条数值", fit("词条数值", W_TH_VALUE), C_BLUE, "bold")

    rows = list(data.get("fusion_rows") or [])
    row_tops = [585.0, 653.0, 721.0, 790.0]
    row_bottoms = [653.0, 721.0, 790.0, 858.0]
    for i, rt in enumerate(row_tops):
        if i > 0:
            c.line((P(L), P(rt), P(R), P(rt)), fill=C_TABLE, width=max(1, P(1)))
        fr = rows[i] if i < len(rows) else None
        if not fr:
            continue
        # 状态药丸
        state = str(fr.get("state") or "—")
        is_fuse = state == "可融合"
        pbg = C_PILL_P_BG if is_fuse else C_PILL_B_BG
        pbd = C_PILL_P_BD if is_fuse else C_PILL_B_BD
        dot_col = C_PURPLE if is_fuse else "#2E6DA4"
        label_w = tw(state, 17, "bold")
        pw2 = label_w + 34
        py0 = rt + (8 if i == 0 else 16)
        c.rounded_rect((P(38), P(py0), P(38 + pw2), P(py0 + 32)), radius=P(16),
                       fill=pbg, outline=pbd, width=max(1, P(1.2)))
        dcx = 38 + 15
        c.ellipse((P(dcx - 5), P(py0 + 11), P(dcx + 5), P(py0 + 21)),
                  outline=dot_col, width=max(1, P(1.6)))
        c.ellipse((P(dcx - 2.2), P(py0 + 13.8), P(dcx + 2.2), P(py0 + 18.2)),
                  fill=dot_col)
        T(38 + 26, py0 + 23, state, 17, dot_col, "bold")
        # 第一列第二行文字 / 同行右侧文字
        # 长词条名（如「滑行攻击暴击几率」8 字）必须按可用宽缩放，否则越过第二列
        # 起点 x=221 与融合结果重叠（2026-11-11 第 4 行填满后实测暴露）。
        pair = str(fr.get("pair") or "")
        pcol = C_RED if fr.get("pair_color") == "red" else (C_GRAY if fr.get("pair_color") == "gray" else C_DARK)
        if i == 0:
            T(38, rt + 60, pair, fit(pair, 221 - 38 - 8, "bold", hi=19), pcol, "bold")
        else:
            pair_x = 38 + pw2 + 12
            T(pair_x, rt + 44, pair,
              fit(pair, max(24, 221 - pair_x - 8), "bold", hi=19), pcol, "bold")
        # 第二列：融合结果（多色混排）
        parts = [(str(p), C_ORANGE if col == "orange" else (C_GRAY if col == "gray" else C_PURPLE))
                 for p, col in (fr.get("result_parts") or [])]
        if parts:
            # 按实测总宽判断是否需缩小，避免溢出到第三列
            avail = COL_T3 - 221 - 12
            size = 20
            while size > 12 and sum(tw(p, size, "bold") for p, _ in parts) > avail:
                size -= 1
            Tokens(221, rt + 44, parts, size, "bold")
        # 第三列：数值 + 高低 + 进度条 + 三个刻度数字
        # 参考卡该列数值止于 x≈526、「高X%」始于 x≈562，中间必须留 ≥8px 净空；
        # 两者字号都由实测宽反推，且用固定右锚，长文本也不会把「高X%」挤走。
        v_txt = str(fr.get("value") or "—")
        d_txt = str(fr.get("dev") or "—")
        T(441, rt + 25, v_txt, fit(v_txt, W_T_VAL, hi=22), C_GREEN, "bold")
        T(COL_TDEV_R, rt + 25, d_txt, fit(d_txt, W_T_DEV, hi=20),
          C_GREEN, "bold", "rs")
        bar(441, COL_TVAL_R, rt + 40, float(fr.get("bar") or 0.5), ring=True)
        ticks = list(fr.get("ticks") or ["—", "—", "—"])
        T(441, rt + 64, str(ticks[0] or "—"), 14, C_GRAY, "regular")
        T((441 + COL_TVAL_R) / 2, rt + 64, str(ticks[1] or "—"), 14, C_GRAY,
          "regular", "ms")
        T(COL_TVAL_R, rt + 64, str(ticks[2] or "—"), 14, C_GRAY, "regular", "rs")

    # ---------------- [3 紫卡综合评分] ----------------
    section(912, "紫卡综合评分")
    score_x = [(28.0, 182.0), (191.0, 328.0), (339.0, 492.0)]
    scores = list(data.get("scores") or [])
    for i, (bx0, bx1) in enumerate(score_x):
        sc = scores[i] if i < len(scores) else {"label": "—", "value": "—"}
        c.rounded_rect((P(bx0), P(943), P(bx1), P(1023)), radius=P(10),
                       fill=C_SCORE_BG, outline=C_SCORE_BD, width=max(1, P(1.4)))
        lcol = C_RED if sc.get("label_color") == "red" else C_GRAY
        T(bx0 + 16, 970, str(sc.get("label") or "—"), 16, lcol, "regular")
        sval = str(sc.get("value") or "—")
        T(bx0 + 16, 1008, sval, fit(sval, W_SCORE_BIG, "regular", hi=30),
          C_DARK, "regular")

    # ---------------- [4 双价格框] ----------------
    price_x = [(28.0, 322.0), (332.0, 626.0)]
    pb = [data.get("price_left") or {}, data.get("price_right") or {}]
    icons = ["cart.png", "chart.png"]
    for i, (bx0, bx1) in enumerate(price_x):
        c.rounded_rect((P(bx0), P(1033), P(bx1), P(1205)), radius=P(12),
                       fill=C_PRICE_BG, outline=C_PRICE_BD, width=max(1, P(1.2)))
        p = pb[i]
        ctr = (bx0 + bx1) / 2
        ttl = str(p.get("title") or "")
        fs_ttl = fit(ttl, W_PRICE_TITLE)
        ttl_w = tw(ttl, fs_ttl, "bold")
        icx = ctr - (ttl_w + 34) / 2
        paste_asset(icons[i], icx, 1050, icx + 26, 1078)
        T(icx + 34, 1072, ttl, fs_ttl, C_PURPLE, "bold")
        c.line((P(bx0 + 14), P(1082), P(bx1 - 14), P(1082)),
               fill=C_RULE, width=max(1, P(1)))
        big = str(p.get("big") or "暂无")
        # 短价格串沿用 166 会被 fit() 反推成巨字压相邻行（真实踩过的坑）
        is_price = big.endswith("P") and big[:-1].isdigit()
        target = W_PRICE_BIG_R if (i or is_price) else W_PRICE_BIG
        fs_big = min(fit(big, target), fit(big, bx1 - bx0 - 40))
        T(ctr, 1128, big, fs_big, C_ORANGE, "bold", "ms")
        if i == 0:
            sub = str(p.get("sub") or "") + str(p.get("sub_val") or "")
            sw = tw(sub, 18, "regular")
            sx = ctr - sw / 2
            sx = T(sx, 1165, str(p.get("sub") or ""), 18, C_GRAY, "regular")
            T(sx, 1165, str(p.get("sub_val") or ""), 18, C_ORANGE, "bold")
        else:
            T(ctr, 1165, str(p.get("sub") or ""), 17, C_GRAY, "regular", "ms")
        T(ctr, 1192, str(p.get("foot") or ""), 16, C_GRAY, "regular", "ms")
    T(BASE_W / 2, 1228, str(data.get("observed") or ""), 16, C_GRAY, "regular", "ms")

    # ---------------- [5 击杀效率配卡] ----------------
    section(1290, str(data.get("build_title") or "基于击杀效率配卡"),
            target_w=W_SEC_LONG)
    mod_x = [(48.0, 173.0), (193.0, 317.0), (337.0, 462.0), (482.0, 605.0)]
    mod_y = [(1341.0, 1403.0), (1419.0, 1481.0)]
    mods = list(data.get("mods") or [])
    for r, (y0, y1) in enumerate(mod_y):
        for cc, (x0, x1) in enumerate(mod_x):
            idx = r * 4 + cc
            m = mods[idx] if idx < len(mods) else None
            name = str((m or {}).get("name") or "—")
            img = (m or {}).get("image")
            ok = False
            if img:
                try:
                    im = Image.open(img).convert("RGBA").resize(
                        (max(1, P(x1 - x0)), max(1, P(y1 - y0))), Image.LANCZOS)
                    c.paste(im, (P(x0), P(y0)), im)
                    ok = True
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                # 占位卡框：深色底 + 名称（不编造图片）
                c.rounded_rect((P(x0), P(y0), P(x1), P(y1)), radius=P(6),
                               fill="#1B2A3A", outline="#4A6072", width=max(1, P(1)))
                T((x0 + x1) / 2, (y0 + y1) / 2 + 5, name, 13, C_WHITE, "bold", "ms")

    # 击杀效率 / 紫卡增益（多色混排，逐段实测）
    def fit_line(parts, target_w, lo=12, hi=30) -> int:
        """整行混排按参考卡**实测总墨迹宽**反推字号（字体无关）。"""
        best = lo
        for _ in range(12):
            mid = (lo + hi) // 2
            total = sum(tw(txt, mid, "bold" if col != "dark" else "regular")
                        for txt, col in parts)
            if total <= target_w:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
            if lo > hi:
                break
        return max(lo, best)

    def token_line(y, parts, target_w):
        # 参考卡这两行是**左对齐到 x=30**（不居中）；逐段实测宽度推进，
        # 段间不加空格凑间距，避免中英混排宽度漂移。
        size = fit_line(parts, target_w)
        cur = 30.0
        for txt, col in parts:
            role = "regular" if col == "dark" else "bold"
            color = {"dark": C_DARK, "blue": C_BLUE, "purple": C_PURPLE,
                     "green": C_GREEN, "orange": C_ORANGE}.get(col, C_DARK)
            cur = T(cur, y, txt, size, color, role, "ls")

    if data.get("kill_line"):
        token_line(1532, data["kill_line"], 488.0)
    else:
        T(30, 1532, "击杀效率：需紫卡截图", fit("击杀效率：需紫卡截图", 300, "regular"),
          C_DARK, "regular")
    if data.get("gain_line"):
        token_line(1577, data["gain_line"], 404.0)
    else:
        T(30, 1577, "紫卡增益：需紫卡截图", fit("紫卡增益：需紫卡截图", 300, "regular"),
          C_DARK, "regular")

    # ---------------- [6/7 指令区] ----------------
    groups = list(data.get("cmd_groups") or [])
    cmd_bases = [(1622, 1775)]
    for gi, g in enumerate(groups[:2]):
        yb = 1622 if gi == 0 else 1775
        # 2026-11-11 去 Rabbit 化：原深色兔耳 bunny_dark.png → 项目 solid 菱形
        # （与指令区的紫色菱形 bullet 同族），占位与原兔耳一致。
        _icons.draw(c, "diamond", P(38.5), P(yb - 12), P(16), C_PURPLE,
                    style="solid")
        ct = str(g.get("title") or "")
        T(62, yb, ct, fit(ct, W_CMD_TITLE), C_DARK, "bold")
        bullet_y = [1660, 1698] if gi == 0 else [1812, 1850]
        for ri, pair in enumerate((g.get("rows") or [])[:2]):
            for ci in (0, 1):
                if ci >= len(pair) or not pair[ci]:
                    continue
                bx = 30 if ci == 0 else 335
                tx = 50 if ci == 0 else 355
                diamond(bx + 6, bullet_y[ri] - 6, 6, C_PURPLE)
                txt = str(pair[ci])
                size = fit(txt, 280.0, hi=19)
                txt = clip(txt, size, "bold", 280.0)
                T(tx, bullet_y[ri], txt, size, C_PURPLE, "bold")
        if gi == 0 and g.get("note"):
            T(30, 1725, str(g["note"]), 15, C_GRAY, "regular")

    # ---------------- [页脚] ----------------
    c.line((P(30), P(1872), P(530), P(1872)), fill=C_RULE, width=max(1, P(1)))
    c.rounded_rect((P(30), P(1881), P(62), P(1903)), radius=P(11),
                   fill=C_WHITE, outline=C_GREEN, width=max(1, P(1.4)))
    T(46, 1898, "PC", 14, C_GREEN, "bold", "ms")
    # 2026-11-11 去 Rabbit 化：页脚品牌 = 项目自有「曼珠」（与 checkin.py:382 同源），
    # 不再冒充 Warframe Rabbit。固定字号（不再按 W_FOOT_BRAND 反推，避免短文本
    # 被 fit 放大成巨字压到相邻提示）。
    brand = "曼珠"
    fs_brand = 15
    bx = T(68, 1898, brand, fs_brand, "#4A7A96", "bold")
    tip = str((data.get("footer") or {}).get("tip") or "")
    if tip:
        # 提示行紧跟品牌，gap=8；起点由实测宽得出，不硬编码 → 两者永不重叠
        T(bx + 8, 1898, tip, fit(tip, W_FOOT_TIP, "regular"), "#3E8A8A", "regular")
    # 2026-11-11 去 Rabbit 化：移除右下 Rabbit 卡通 chibi.png（原 515..655 ×
    # 1740..1920）。该区域改由 light 主题贴纸底自然填充，不再贴任何 Q 版形象；
    # show_chibi 参数保留兼容旧调用方，但再无卡通可贴（彻底去 Rabbit 化）。

    # 样例标签（仅版式复刻夹具显示，明确非实时数据）
    if data.get("sample"):
        T(R, 1898, "版式样例·非实时数据", 11, C_GRAY_LT, "regular", "rs")

    return c
