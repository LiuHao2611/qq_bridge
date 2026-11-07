# -*- coding: utf-8 -*-
"""pic/templates/alerts.py —— 警报任务卡片（照 Rabbit 参考卡版式，逐元素对齐）。

浅奶油底 + 顶部/底部粉色描边条；居中标题「警报任务」(深蓝) + 左侧**警报徽标**
（黑色圆角倒三角 + 白色感叹号，按参考卡量出 31×25、上两角大圆角、底尖小圆角）；
浅橙分割线；每条警报两行胶囊：
  行1：派系色胶囊标签(G系防御) + 星球真实奖杯图标 + 节点(蓝) + 右侧橙色「剩余 …」胶囊(含时钟图标)
  行2：奖励真实物品图标(左) + 奖励文字(紫) + 紧跟的现金真实图标 + 现金数目(同紫，参考卡实测)

图标均为真实游戏资产（来自 pic.item_icons 预取）：
  星球 → <Planet>TrophyBronze；奖励 → 物品 textureLocation 末段；
  现金 → ``Credits.png``（HUD 货币图标：三张扇形叠卡，青边深卡面）。
  取不到时回落几何占位（星球圆点 / 奖励菱形 / 现金圆点），卡片永远能出图。

用户明确要求（2026-11-05）：**底部说明文字 / PC 徽章 / Q 版吉祥物一律删除，以后不要再自绘这些。**

自检：build() 接受 boxes=[] 时收集所有元素真实包围盒，供 check_boxes 门禁（溢出/重叠/间距）。
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..canvas import Canvas
from ..theme import Theme
from ..icon_paste import paste_icon as _paste_icon
from ..item_icons import planet_crop as _planet_crop

C_PAGE = "#F7F2EC"        # 奶油底
C_CARD = "#FFFDFA"        # 内容卡底
C_BORDER = "#EADFCF"      # 卡片描边
C_TITLE = "#1F4E79"       # 标题深蓝
C_TRI = "#151515"         # 徽标黑
C_DIV = "#E8C9A0"         # 浅橙分割线
C_NODE = "#1F6F8B"        # 节点蓝
C_PLANET = "#8E9FB5"      # 星球灰蓝（几何回落用）
C_REMAIN = "#D35400"      # 倒计时橙
C_REWARD = "#6C3483"      # 奖励紫
C_CREDIT = "#6C3483"      # 现金（国服译名；参考卡实测与奖励文字同色，不是金色）
C_FOOT = "#8A94A6"        # 空态文字灰
C_STRIP = "#F3C9D6"       # 顶部/底部粉条

PAD = 28
FS_TITLE = 34
FS_TAG = 20
FS_NODE = 22
FS_REMAIN = 18
FS_REWARD = 19
FS_CREDIT = 19           # 现金数目：参考卡与奖励文字同字号
ROW1_H = 40
ROW2_H = 30
ALERT_GAP = 14
STRIP = 10               # 顶部/底部粉条高度
ICON_SZ = 22             # 奖励图标尺寸
PLANET_SZ = 22           # 星球奖杯图标尺寸
#: 现金图标槽：参考卡逐像素实测 21×26（三张叠卡天然比正方物品图标高一点）
CASH_W, CASH_H = 21, 26
#: 星球奖杯裁切表统一放在 ``pic/item_icons.py``（球体裁到 0.62、非球体单独量），
#: 警报卡与裂隙卡共用同一份，避免两处常量各改各的。

#: 标题徽标尺寸与「徽标→标题」间距（照参考卡实测：31×25、间距 19px、整块居中）
BADGE_W, BADGE_H = 31, 25
BADGE_GAP = 19

_MIN_GAP = 2             # 自检最小水平间距 token


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _rec(boxes, label: str, box) -> None:
    """收集元素包围盒（boxes 为 None 时零开销）。"""
    if boxes is not None:
        boxes.append((label, *box))


def _tb(pos, text: str, size: int, role: str, anchor: str) -> tuple:
    """由 text_size + anchor 推算文本真实包围盒（与布局测量同源自 _measure）。"""
    w, h = Canvas.text_size(text, size, role)
    x, y = pos
    if anchor == "mm":
        return (x - w // 2, y - h // 2, x + w // 2, y + h // 2)
    if anchor == "lm":
        return (x, y - h // 2, x + w, y + h // 2)
    if anchor in ("la", "ls"):
        return (x, y, x + w, y + h)
    if anchor in ("ra", "rs"):
        return (x - w, y, x, y + h)
    if anchor == "ms":
        return (x - w // 2, y, x + w // 2, y + h)
    return (x, y - h // 2, x + w, y + h // 2)


def _chaikin(pts: list, iters: int = 2) -> list:
    """Chaikin 切角：把折线所有拐角轻微磨圆（迭代越多越圆）。

    参考徽标的肩部/底尖只有 1–2px 圆角，故路径先按 ~1px 密采样，再跑 2 次，
    磨圆量约 0.5px，不会把形状磨成胖圆。
    """
    for _ in range(iters):
        out: list[tuple] = []
        n = len(pts)
        for i in range(n):
            p, q = pts[i], pts[(i + 1) % n]
            out.append((0.75 * p[0] + 0.25 * q[0], 0.75 * p[1] + 0.25 * q[1]))
            out.append((0.25 * p[0] + 0.75 * q[0], 0.25 * p[1] + 0.75 * q[1]))
        pts = out
    return pts


def _badge_path() -> list:
    """参考卡实测徽标轮廓（31×25 坐标系）。

    逐行量得：顶部平边 x12–19、两肩为 r≈16.9 的圆弧（最宽点在 y=5、x=0/31）、
    斜边收到底尖 (15.5, 25)。这是"圆角倒三角"的真实轮廓，不是普通三角形。
    """
    RW, RH = 31.0, 25.0
    pts: list[tuple] = [(12.0, 0.0)]
    # 左肩圆弧：圆心 (12, 16.9)，从顶点 (12,0) 到最宽点 (0,5)
    cx, cy, r = 12.0, 16.9, 16.9
    a0, a1 = -math.pi / 2, math.atan2(5.0 - cy, 0.0 - cx)
    n = max(2, int(abs(a1 - a0) * r))
    for i in range(1, n + 1):
        a = a0 + (a1 - a0) * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    # 左斜边 → 底尖（~1px 采样）
    sx, sy = pts[-1]
    for t in range(1, 26):
        pts.append((sx + (15.5 - sx) * t / 25.0, sy + (25.0 - sy) * t / 25.0))
    # 右斜边 → 最宽点 (31,5)
    for t in range(1, 26):
        pts.append((15.5 + (31.0 - 15.5) * t / 25.0, 25.0 + (5.0 - 25.0) * t / 25.0))
    # 右肩圆弧：圆心 (19, 16.9)，从 (31,5) 回到 (19,0)
    cx2, cy2, r2 = 19.0, 16.9, 16.9
    b0, b1 = math.atan2(5.0 - cy2, 31.0 - cx2), -math.pi / 2
    n2 = max(2, int(abs(b1 - b0) * r2))
    for i in range(1, n2 + 1):
        a = b0 + (b1 - b0) * i / n2
        pts.append((cx2 + r2 * math.cos(a), cy2 + r2 * math.sin(a)))
    return pts


def _alert_badge(c: Canvas, cx: float, cy: float,
                 w: int = BADGE_W, h: int = BADGE_H) -> None:
    """警报徽标：黑色"圆肩倒三角" + 白色感叹号（对齐参考卡，绝非空三角）。

    轮廓来自参考卡逐行实测（31×25）；感叹号宽 ~2px，点 y 0.16–0.22、
    竖条 y 0.40–0.65，水平居中。S=6 超采样后缩小，得到抗锯齿边缘。
    """
    S = 6
    W, H = w * S, h * S
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    poly = _chaikin(_badge_path(), 2)
    # 归一化到满 W×H：保证实测包围盒正好 31×25（不靠目测凑）
    pxs = [p[0] for p in poly]
    pys = [p[1] for p in poly]
    mnx, mxx, mny, mxy = min(pxs), max(pxs), min(pys), max(pys)
    poly = [((x - mnx) / (mxx - mnx) * W, (y - mny) / (mxy - mny) * H) for x, y in poly]
    d.polygon(poly, fill=(0, 0, 0, 255))
    ew = max(2, int(round(w * 0.065))) * S
    ex = W / 2.0 - ew / 2.0
    d.rounded_rectangle([ex, 0.160 * H, ex + ew, 0.225 * H], radius=ew / 2,
                        fill=(255, 255, 255, 255))
    d.rounded_rectangle([ex, 0.400 * H, ex + ew, 0.650 * H], radius=ew / 2,
                        fill=(255, 255, 255, 255))
    layer = layer.resize((w, h), Image.LANCZOS)
    c.paste_alpha(layer, (int(round(cx - w / 2.0)), int(round(cy - h / 2.0))))


def build(t: Theme, alerts=None, title="警报任务", width: int | None = None,
          boxes=None, **kw) -> Canvas:
    alerts = alerts or []
    width = int(width or getattr(t, "width", 800) or 800)
    x0 = PAD
    content_w = width - PAD * 2

    # ---------- 高度测量（与绘制同一套字号/行高）----------
    y = STRIP + PAD
    y += FS_TITLE + 14 + 12                       # 标题 + 间距 + 分割线区
    if alerts:
        y += len(alerts) * (ROW1_H + ROW2_H) + max(0, len(alerts) - 1) * ALERT_GAP
    else:
        y += 30
    y += 22                                       # 底部留白（无页脚）
    h = y + STRIP

    c = Canvas(width, h, C_PAGE, theme=t)
    c.rect((0, 0, width, STRIP), fill=C_STRIP)
    c.rect((0, h - STRIP, width, h), fill=C_STRIP)
    card_top, card_bot = STRIP, h - STRIP
    c.rounded_rect((14, card_top, width - 14, card_bot), radius=24,
                   fill=C_CARD, outline=C_BORDER, width=2)

    y = STRIP + PAD
    # ---------- 标题（居中）+ 左侧警报徽标（黑圆角倒三角 + 白感叹号）----------
    fs_t = FS_TITLE
    tw = _w(title, fs_t, "regular")
    total = BADGE_W + BADGE_GAP + tw
    bx = (width - total) // 2
    byc = y + fs_t // 2 + 1                        # 参考卡徽标比文字中线低 ~1px
    _alert_badge(c, bx + BADGE_W / 2.0, byc, BADGE_W, BADGE_H)
    _rec(boxes, "title.badge", (bx, byc - BADGE_H / 2.0, bx + BADGE_W, byc + BADGE_H / 2.0))
    c.text((bx + BADGE_W + BADGE_GAP, y + fs_t // 2), title, size=fs_t, color=C_TITLE,
           role="regular", anchor="lm")
    _rec(boxes, "title.text",
         _tb((bx + BADGE_W + BADGE_GAP, y + fs_t // 2), title, fs_t, "regular", "lm"))
    y += fs_t + 14
    # ---------- 分割线 ----------
    c.line((x0, y, x0 + content_w, y), fill=C_DIV, width=2)
    y += 12

    # ---------- 警报列表 ----------
    if not alerts:
        c.text((x0, y + 16), "当前没有进行中的警报任务。", size=FS_REWARD,
               color=C_FOOT, role="regular", anchor="lm")
        _rec(boxes, "empty", _tb((x0, y + 16), "当前没有进行中的警报任务。",
                                 FS_REWARD, "regular", "lm"))
        y += 30
    else:
        for i, a in enumerate(alerts):
            col = a.get("color", "#555555")
            # 行1
            cy1 = y + ROW1_H // 2
            # 派系胶囊
            tag = a.get("tag", "?")
            tag_w = _w(tag, FS_TAG, "regular") + 22
            tag_h = FS_TAG + 14
            bx0, by = x0, cy1 - tag_h // 2
            c.rounded_rect((bx0, by, bx0 + tag_w, by + tag_h), radius=tag_h // 2,
                           fill="#FFFFFF", outline=col, width=1)
            c.text((bx0 + tag_w // 2, by + tag_h // 2), tag, size=FS_TAG,
                   color=col, anchor="mm")
            _rec(boxes, "a%d.tag" % i, (bx0, by, bx0 + tag_w, by + tag_h))
            # 星球真实奖杯图标（取不到回落灰蓝圆点）
            px = bx0 + tag_w + 16
            if not _paste_icon(c, a.get("_planet_icon"), px, cy1, PLANET_SZ,
                               crop=_planet_crop(a.get("planet_en"))):
                pr = 9
                c.ellipse((px - pr, cy1 - pr, px + pr, cy1 + pr), fill=C_PLANET)
                _rec(boxes, "a%d.planet" % i, (px - pr, cy1 - pr, px + pr, cy1 + pr))
            else:
                _rec(boxes, "a%d.planet" % i, (px - PLANET_SZ // 2, cy1 - PLANET_SZ // 2,
                                               px + PLANET_SZ // 2, cy1 + PLANET_SZ // 2))
            # 节点
            nx = px + PLANET_SZ // 2 + 12
            c.text((nx, cy1), a.get("node", "?"), size=FS_NODE, color=C_NODE,
                   role="regular", anchor="lm")
            _rec(boxes, "a%d.node" % i, _tb((nx, cy1), a.get("node", "?"), FS_NODE, "regular", "lm"))
            # 倒计时胶囊（右对齐）
            rem = a.get("remain", "")
            rem_w = _w(rem, FS_REMAIN, "regular") + 40
            rem_h = FS_REMAIN + 14
            rx = x0 + content_w - rem_w
            ry = cy1 - rem_h // 2
            c.rounded_rect((rx, ry, rx + rem_w, ry + rem_h), radius=rem_h // 2,
                           fill="#FFFFFF", outline=C_REMAIN, width=1)
            clk_x, clk_y, cr = rx + 14, cy1, 6
            c.ellipse((clk_x - cr, clk_y - cr, clk_x + cr, clk_y + cr),
                      fill="#FFFFFF", outline=C_REMAIN, width=1)
            c.line((clk_x, clk_y, clk_x, clk_y - 3.5), fill=C_REMAIN, width=1)
            c.line((clk_x, clk_y, clk_x + 3, clk_y + 1), fill=C_REMAIN, width=1)
            c.text((rx + 26, cy1), rem, size=FS_REMAIN, color=C_REMAIN,
                   role="regular", anchor="lm")
            _rec(boxes, "a%d.remain" % i, (rx, ry, rx + rem_w, ry + rem_h))
            y += ROW1_H

            # 行2：奖励真实图标 + 奖励文字 + 现金真实图标 + 现金数目
            cy2 = y + ROW2_H // 2
            # 奖励图标槽（左，固定位置，有无图标都占同一槽）
            ix = x0 + 4
            if not _paste_icon(c, a.get("_reward_icon"), ix + ICON_SZ // 2, cy2, ICON_SZ):
                c.draw.polygon([(ix + ICON_SZ // 2, cy2 - 6), (ix + ICON_SZ // 2 + 6, cy2),
                                (ix + ICON_SZ // 2, cy2 + 6), (ix + ICON_SZ // 2 - 6, cy2)],
                               fill=C_REWARD)
            _rec(boxes, "a%d.reward_icon" % i, (ix, cy2 - ICON_SZ // 2,
                                                ix + ICON_SZ, cy2 + ICON_SZ // 2))
            reward = a.get("reward", "")
            rtxt_x = ix + ICON_SZ + 10
            c.text((rtxt_x, cy2), reward, size=FS_REWARD,
                   color=C_REWARD, role="regular", anchor="lm")
            _rec(boxes, "a%d.reward" % i, _tb((rtxt_x, cy2), reward,
                                             FS_REWARD, "regular", "lm"))
            # 现金：参考卡里**紧跟奖励文字**（不是右对齐），同字号同紫色
            credits = a.get("credits", "")
            if credits:
                cash_w = _w(credits, FS_CREDIT, "regular")
                gap_r, gap_c = 12, 6                       # 奖励→图标 / 图标→数字
                bx = rtxt_x + _w(reward, FS_REWARD, "regular") + gap_r
                block = CASH_W + gap_c + cash_w
                if bx + block > x0 + content_w:            # 整行放不下才贴右，门禁会盯重叠/溢出
                    bx = x0 + content_w - block
                if not _paste_icon(c, a.get("_credits_icon"), bx + CASH_W / 2.0, cy2, CASH_H):
                    c.ellipse((bx + 3, cy2 - 7, bx + 17, cy2 + 7), fill="#3A3A3A")
                _rec(boxes, "a%d.cash_icon" % i, (bx, cy2 - CASH_H // 2,
                                                  bx + CASH_W, cy2 + CASH_H // 2))
                c.text((bx + CASH_W + gap_c, cy2), credits, size=FS_CREDIT, color=C_CREDIT,
                       role="regular", anchor="lm")
                _rec(boxes, "a%d.cash" % i, _tb((bx + CASH_W + gap_c, cy2), credits,
                                                FS_CREDIT, "regular", "lm"))
            y += ROW2_H
            if i < len(alerts) - 1:      # 与高度测量一致：仅「条目之间」加间距，末条不加
                y += ALERT_GAP

    # ---------- 底部留白（用户要求：不画说明文字 / PC 徽章 / 吉祥物）----------
    y += 22
    return c
