# -*- coding: utf-8 -*-
"""pic/templates/guide.py —— 攻略长图卡片（游戏工具箱专用）。

布局：
  * 可选头像（140×140 圆角，avatar=PNG bytes；没有则退化为纯文本布局）；
  * body 约定：以【…】开头的行渲染为**主色分区标题**，其余为正文（自动换行）；
  * 【技能】/【集团卡】区块内的「·」行渲染为**气泡块**（技能=主色系、
    集团卡=金色系，2026-09-16 用户要求：每个技能单独气泡、集团卡与技能配色区分）；
  * 高度按行数自适应，正文上限 46 行（再长截断，避免出超大图）；
  * 右下角可选来源小字。
"""
from __future__ import annotations

import colorsys
import hashlib
import re
from io import BytesIO

from PIL import Image, ImageDraw

from ..canvas import Canvas, rgb, rounded_mask
from ..theme import Theme
from .. import decor, icons, widgets as w

AVATAR = 140
# 2026-09-16：技能/集团卡改为完整描述（不再 42/44 字截断），行上限放宽防超高图
MAX_ROWS = 130
FOOT_H = 30

# 气泡内边距
BUB_PAD_X = 16
BUB_PAD_Y = 9


def _eff_card(t) -> str:
    """卡片半透明时返回「卡片显示色」= card 按 card_alpha 与页面底色混合（气泡底色基准）。"""
    base = t.card if hasattr(t, "card") else t.bg
    ca = int(getattr(t, "card_alpha", 100) or 100)
    if ca >= 100:
        return base
    return _tint(base, max(0, min(100, ca)) / 100.0, t.bg)


def _tint(color: str, alpha: float, base: str) -> str:
    """color 以 alpha 比例混入 base，返回不透明色。

    画布导出时 RGBA→RGB 会丢弃 alpha 通道（canvas.save 里 convert("RGB")），
    半透明层会变成实心色。因此气泡底色**预先与卡片底色混合**成不透明淡色，
    深浅主题都稳定（2026-09-16 用户反馈气泡太实心/难看后修复）。
    """
    c, b = rgb(color), rgb(base)
    r = int(c[0] * alpha + b[0] * (1 - alpha))
    g = int(c[1] * alpha + b[1] * (1 - alpha))
    bl = int(c[2] * alpha + b[2] * (1 - alpha))
    return "#%02x%02x%02x" % (r, g, bl)


# ---- 市价表格：玩家头像 + 价格渐变气泡 辅助（2026-11-03）----
def _hsv_color(h: float, s: float = 0.5, v: float = 0.62) -> str:
    """HSL→RGB hex，s/v 取中值保证深色/浅色主题都可读。"""
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360.0, s, v)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _avatar_color(uid: str) -> str:
    """玩家头像底色：id 稳定哈希 → 色相，保证同一玩家永远同色（确定性，跨进程一致）。"""
    if not uid:
        return "#6b7280"
    h = int(hashlib.md5(uid.encode("utf-8")).hexdigest(), 16) % 360
    return _hsv_color(h, 0.52, 0.60)


def _price_grad(t: float) -> str:
    """价格梯度：低=青绿 → 中=琥珀 → 高=玫红（t∈[0,1]，1=最高价）。"""
    t = max(0.0, min(1.0, t))
    stops = [(0.0, (45, 212, 191)), (0.5, (251, 191, 36)), (1.0, (248, 113, 113))]
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = int(c0[0] + (c1[0] - c0[0]) * f)
            g = int(c0[1] + (c1[1] - c0[1]) * f)
            b = int(c0[2] + (c1[2] - c0[2]) * f)
            return "#%02x%02x%02x" % (r, g, b)
    return "#%02x%02x%02x" % stops[-1][1]


def _text_on(hexcolor: str) -> str:
    """按亮度选黑/白文字，保证气泡配色上可读。"""
    r, g, b = rgb(hexcolor)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return "#0b0e14" if lum > 150 else "#ffffff"


def _cover_square(img: Image.Image, size: int, vertical: bool = False) -> Image.Image:
    """裁正方形再缩放（不变形）。vertical=True 裁顶部——战甲全身立绘头部在上方，
    裁顶部得到「半身像」；默认居中裁（普通头像）。"""
    w, h = img.size
    side = min(w, h)
    x0 = (w - side) // 2
    if vertical:
        y0 = 0  # 顶部：保留头肩
    else:
        y0 = (h - side) // 2
    img = img.crop((x0, y0, x0 + side, y0 + side))
    return img.resize((size, size), Image.LANCZOS)


def _contain_box(img: Image.Image, box_w: int, box_h: int, pad: int = 6) -> Image.Image:
    """等比缩放进 box_w×box_h（**不裁切**），透明边先裁掉再居中。

    宽幅武器、竖长战甲/部件都能完整可见；与 _cover_square 的区别：
    那个会裁掉四周，这个只缩不放，并支持非正方形图标槽。
    """
    img = img.convert("RGBA")
    try:
        bb = img.getchannel("A").getbbox()
    except Exception:  # noqa: BLE001 — 无 alpha/解析异常就按原图
        bb = None
    if bb:
        img = img.crop(bb)          # 去掉透明留白，让主体尽量占满
    w, h = img.size
    if w <= 0 or h <= 0:
        return Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    inner_w = max(1, box_w - pad * 2)
    inner_h = max(1, box_h - pad * 2)
    scale = min(inner_w / w, inner_h / h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    canvas.paste(img, ((box_w - nw) // 2, (box_h - nh) // 2), img)
    return canvas


def _split_name(line: str) -> tuple[str, str]:
    """「· 名字（EN）：描述」→ (名字段含冒号, 描述)。拆不出则整行当描述。"""
    s = line.strip()
    m = re.match(r"^[·•]\s*(.+?[（(].*?[）)]\s*[:：]\s*)(.*)$", s)
    if m and m.group(1):
        return m.group(1), m.group(2)
    m = re.match(r"^[·•]\s*(.+?[:：]\s*)(.*)$", s)
    if m and m.group(1):
        return m.group(1), m.group(2)
    return "", s


def _chip_metrics(t) -> tuple[int, int, int, int]:
    """芯片尺寸（紫卡分析 MOD 芯片与贡献行共用）：
    返回 (行高, 芯片高, 水平内边距, 芯片内字号)。行高与高度预算共用同一值。"""
    chip_fs = t.fs(20)
    chip_h = chip_fs + 12
    row_h = chip_h + 8
    cpx = max(12, int(round(14 * float(getattr(t, "font_scale", 1.0) or 1.0))))
    return row_h, chip_h, cpx, chip_fs


def _chips_layout(items: list[str], t, content_w: int) -> list[list[tuple[str, int]]]:
    """芯片流式排布测量：返回行列表，每行 [(文本, 占宽)]。"""
    row_h, _ch, cpx, chip_fs = _chip_metrics(t)
    rows: list[list[tuple[str, int]]] = []
    cur: list[tuple[str, int]] = []
    x = 0
    gap = 10
    for name in items:
        w = Canvas.text_width(name, chip_fs, "bold") + cpx * 2
        if cur and x + gap + w > content_w:
            rows.append(cur)
            cur, x = [], 0
        if cur:
            x += gap
        cur.append((name, w))
        x += w
    if cur:
        rows.append(cur)
    return rows


def build(
    t: Theme,
    body: str = "",
    title: str | None = None,
    avatar: bytes | None = None,
    avatar_vertical: bool = False,
    icon: str | None = "wand",
    width: int | None = None,
    source: str | None = None,
    avatars: dict | None = None,
    item_icon: bytes | None = None,
    boxes: list | None = None,   # card-design 自检：非空时登记 disp 块真实包围盒
) -> Canvas:
    from . import market_currency  # 惰性导入：避免 pic/__init__.py 半初始化期间的循环导入
    body = str(body or "").strip()
    width = int(width or getattr(t, "width", 800) or 800)
    pad = int(getattr(t, "pad", 24) or 24)
    pad_in = pad * 2
    _av = 156 if avatar_vertical else AVATAR  # 半身像稍大更醒目
    line_h = t.fs(30)
    head_h = t.fs(34)
    font_size = t.fs(20)
    content_w = width - pad_in * 2 - (0 if not avatar else 0)  # 正文全宽（头像在顶部）
    base = _eff_card(t)  # 卡片底色（气泡/占位/分隔线混合基准），供各块复用

    # wm 头条图标：预解码 + 按长宽比决定图标槽（宽图给宽槽、长图给高槽）。
    # 高度计算与渲染共用同一组尺寸，避免长图被压小、宽图被裁。
    _icon_img = None
    _icon_ar = 1.0
    if item_icon:
        try:
            _icon_img = Image.open(BytesIO(item_icon)).convert("RGBA")
            _bb = _icon_img.getchannel("A").getbbox()
            if _bb:
                _icon_img = _icon_img.crop(_bb)
            _icon_ar = _icon_img.width / max(1, _icon_img.height)
        except Exception:  # noqa: BLE001 — 坏图按无图标处理
            _icon_img = None
            _icon_ar = 1.0
    # 图标槽按图片真实长宽比连续自适应（面积恒定、长边有上限），contain 不裁切
    _ibase = t.fs(104)
    _s = max(0.72, min(1.62, _icon_ar ** 0.5))
    _iw = int(round(_ibase * _s))
    _ih = int(round(_ibase / _s))

    # 字号/行高单一来源；纯表格卡（@hdr/@row）也用同一份，避免 wm=None 崩溃
    wm = market_currency.wm_types(t)

    # ---- 解析 body -> 块（head / text / skill气泡 / aug气泡）----
    blocks: list[dict] = []
    cur_zone = None  # None / "skill" / "aug"：后续「·」行归属
    for raw in body.split("\n"):
        raw = raw.rstrip()
        if not raw:
            continue
        if raw.startswith("【") and "】" in raw:
            if raw.startswith("【技能】"):
                cur_zone = "skill"
            elif raw.startswith("【集团卡】"):
                cur_zone = "aug"
            else:
                cur_zone = "generic"  # 2026-09-17：其他分区后的「·」行同样渲染气泡
            blocks.append({"kind": "head", "raw": raw,
                           "lines": Canvas.wrap(raw, t.fs(22), "bold", content_w)})
            continue
        if cur_zone in ("skill", "aug", "generic") and raw.strip().startswith("·"):
            blocks.append({"kind": cur_zone, "raw": raw,
                           "lines": Canvas.wrap(raw, font_size, "regular", content_w - BUB_PAD_X * 2)})
            continue
        # ---- wm 市价卡头条块（物品图标 + 标题 + 统计条）----
        if raw.startswith("@wmhdr|"):
            _cols = [c for c in raw.split("|")[1:]]
            blocks.append({"kind": "wmhdr", "cols": (_cols + [""] * 9)[:9]})
            continue
        # ---- 市场卡片结构化标记（wm 输出）----
        if raw.startswith("@hdr|") or raw.startswith("@row|"):
            _is_hdr = raw.startswith("@hdr|")
            _cols = [c for c in raw.split("|")[1:]]
            _row = {"hdr": _is_hdr, "cols": _cols}
            if blocks and blocks[-1].get("kind") == "tbl":
                blocks[-1]["rows"].append(_row)
                if _is_hdr:
                    blocks[-1]["headers"] = _cols
            else:
                blocks.append({"kind": "tbl", "rows": [_row], "headers": (_cols if _is_hdr else [])})
            continue
        if raw.startswith("@ttl|"):
            # 居中大标题（+ 可选前置图标），下方贯通横线
            _tp = raw[5:].split("|")
            _tt = (_tp[0] or "").strip()
            if not _tt:
                continue
            blocks.append({"kind": "ctitle", "raw": _tt,
                           "icon": (_tp[1].strip() if len(_tp) > 1 else ""),
                           "lines": Canvas.wrap(_tt, t.fs(30), "bold", content_w)})
            continue
        if raw.startswith("@disp|"):
            # 紫卡现行规则面板（DE 44.1.3 后全武器统一 1.2）：两行强调色圆角框
            #   行1  [武器图标] [中文名] 类型 -
            #   行2  现行 1.20 · 全武器统一   改动前 0.70 → 词条约 +71%
            # 字段：name | cat | 现行倍率 | 改动前对照文案(可空) | 图标文件(可空)
            _dp = raw[6:].split("|")
            if len(_dp) < 3:
                continue
            blocks.append({"kind": "disp", "name": _dp[0].strip(), "cat": _dp[1].strip(),
                           "cur": _dp[2].strip(),
                           "before": _dp[3].strip() if len(_dp) > 3 else "",
                           "icon": _dp[4].strip() if len(_dp) > 4 else "",
                           "lines": []})
            continue
        if raw.startswith("@title|"):
            blocks.append({"kind": "title", "raw": raw[7:],
                           "lines": Canvas.wrap(raw[7:], t.fs(24), "bold", content_w)})
            continue
        if raw.startswith("@sub|"):
            # `@sub|说明文字` 或 `@sub|说明文字|字号`（字号可省略，省略 = 正文号）
            _sp = raw[5:].split("|")
            _st = _sp[0]
            _sfs = font_size
            if len(_sp) > 1 and _sp[1].strip().isdigit():
                _sfs = t.fs(int(_sp[1].strip()))
            blocks.append({"kind": "sub", "raw": _st, "fs": _sfs,
                           "lines": Canvas.wrap(_st, _sfs, "regular", content_w)})
            continue
        if raw.startswith("@w|"):
            blocks.append({"kind": "warn", "raw": raw[3:],
                           "lines": Canvas.wrap(raw[3:], font_size, "regular", content_w - BUB_PAD_X * 2)})
            continue
        if raw.startswith("@shout|"):
            blocks.append({"kind": "shout", "raw": raw[7:],
                           "lines": Canvas.wrap(raw[7:], font_size, "regular", content_w)})
            continue
        if raw.startswith("@foot|"):
            blocks.append({"kind": "foot", "raw": raw[6:],
                           "lines": Canvas.wrap(raw[6:], t.fs(16), "regular", content_w)})
            continue
        if raw.startswith("@stat|"):
            blocks.append({"kind": "stat", "raw": raw[6:],
                           "lines": Canvas.wrap(raw[6:], font_size, "regular", content_w)})
            continue
        if raw.startswith("@mstat|"):
            # 市价卡底部「价格统计」四行（结构化）：标签|最低|最高|最新
            # 连续多行合并进同一个 mstat 块（rows 列表），整体渲染一次、高度一次
            _mp = raw[7:].split("|")
            if len(_mp) >= 4:
                _row = {"label": _mp[0].strip(), "low": _mp[1].strip(),
                        "high": _mp[2].strip(), "newest": _mp[3].strip()}
                if blocks and blocks[-1].get("kind") == "mstat":
                    blocks[-1]["rows"].append(_row)
                else:
                    blocks.append({"kind": "mstat", "rows": [_row], "lines": []})
            continue
        if raw.startswith("@page|"):
            blocks.append({"kind": "page", "raw": raw[6:],
                           "lines": Canvas.wrap(raw[6:], t.fs(16), "regular", content_w)})
            continue
        # ---- 紫卡分析卡标记（对齐 Warframe Rabbit「紫卡分析」版式，2026-10-08）----
        if raw.startswith("@rstat|"):
            # 词条行：@rstat|+104.5%|暴击伤害|(91.0%-111.4%|高2.18%)
            # 注意 rng 段内含「|」分隔符 → maxsplit=2 防截断
            _rp = raw[7:].split("|", 2)
            blocks.append({"kind": "rstat", "val": _rp[0].strip(),
                           "name": (_rp[1].strip() if len(_rp) > 1 else ""),
                           "rng": (_rp[2].strip() if len(_rp) > 2 else ""),
                           "lines": []})
            continue
        if raw.startswith("@score|"):
            # 综合评分行（评分数字带强调框）：@score|·紫卡综合评分 - |68.3（A）| - 伤害卡
            _sp = raw[7:].split("|")
            blocks.append({"kind": "score", "pre": _sp[0].strip(),
                           "chip": (_sp[1].strip() if len(_sp) > 1 else ""),
                           "post": (_sp[2].strip() if len(_sp) > 2 else ""),
                           "lines": []})
            continue
        if raw.startswith("@chips|"):
            # 芯片流式排布（当前配卡 MOD 列表）：@chips|深层冷冻|病原弹头|…
            _names = [x.strip() for x in raw[7:].split("|") if x.strip()]
            if _names:
                blocks.append({"kind": "chips", "items": _names, "lines": []})
            continue
        if raw.startswith("@chipline|"):
            # 单行：左芯片 + 右说明（每词条贡献行）；rest 内含「|」→ maxsplit=1 防截断
            _cp = raw[10:].split("|", 1)
            blocks.append({"kind": "chipline", "chip": _cp[0].strip(),
                           "rest": (_cp[1].strip() if len(_cp) > 1 else ""),
                           "lines": []})
            continue
        if raw.startswith("@cmd|"):
            # 指令行（紫粉色加粗，Rabbit footer 指令段）：@cmd|rm 暴伤 多重 …
            blocks.append({"kind": "cmd", "raw": raw[5:],
                           "lines": Canvas.wrap(raw[5:], font_size, "bold", content_w)})
            continue
        if raw.startswith("@alert|"):
            # 红/橙色警示小字（*注意：…）：@alert|*注意：伤害计算仅供参考
            blocks.append({"kind": "alert", "raw": raw[7:],
                           "lines": Canvas.wrap(raw[7:], t.fs(16), "regular", content_w)})
            continue
        blocks.append({"kind": "text", "raw": raw,
                       "lines": Canvas.wrap(raw, font_size, "regular", content_w)})

    # 总渲染行数（气泡块按内部行数 + 上下留白折算）→ MAX_ROWS 截断
    def _blk_rows(b):
        if b["kind"] == "tbl":
            return len(b["rows"])
        if b["kind"] == "disp":
            return 2
        if b["kind"] == "wmhdr":
            return 9
        return len(b["lines"]) + (1 if b["kind"] in ("skill", "aug", "generic", "warn") else 0)

    total_rows = 0
    for b in blocks:
        total_rows += _blk_rows(b)
    if total_rows > MAX_ROWS:
        trimmed: list[dict] = []
        n = 0
        for b in blocks:
            add = _blk_rows(b)
            if n + add > MAX_ROWS:
                break
            trimmed.append(b)
            n += add
        trimmed.append({"kind": "text", "raw": "…（内容过长已截断）", "lines": ["…（内容过长已截断）"]})
        blocks = trimmed

    # ---- 高度 ----
    y0 = pad + 20
    y_calc = y0
    if avatar:
        y_calc += _av + 20
    elif title:
        # 只有真的会画标题时才预留高度（wm 卡 title 被抑制，避免底部大片空白）
        y_calc += head_h + 10
    for b in blocks:
        if b["kind"] == "head":
            y_calc += head_h + 10
        elif b["kind"] in ("skill", "aug", "generic"):
            y_calc += len(b["lines"]) * line_h + BUB_PAD_Y * 2
        elif b["kind"] == "tbl":
            y_calc += len(b["rows"]) * wm.row_h + 14
        elif b["kind"] == "wmhdr":
            # 高度与渲染共用 market_currency 字号/文字栈，杜绝底部裁切
            y_calc += wm.bar_fs + 12 + 14             # 顶部栏
            y_calc += max(_ih, market_currency.wmhdr_text_height(wm, b["cols"])) + 14 + 14
            y_calc += t.fs(15) + 6 + t.fs(market_currency.STAT_VAL_FS) + 12 + 14
            y_calc += wm.sect_fs + 12                 # 卖单标题
        elif b["kind"] == "warn":
            y_calc += len(b["lines"]) * line_h + BUB_PAD_Y * 2
        elif b["kind"] == "page":
            y_calc += t.fs(20) + 8
        elif b["kind"] == "title":
            y_calc += t.fs(26) + 6
        elif b["kind"] == "ctitle":
            y_calc += t.fs(34) + 10 + 14
        elif b["kind"] == "disp":
            y_calc += 2 * line_h + BUB_PAD_Y * 2 + 10
        elif b["kind"] == "sub":
            # 副标题可带独立字号；行距取默认行高与字号+4 的较大者，防大字溢出
            y_calc += len(b["lines"]) * max(line_h, (b.get("fs") or font_size) + 4)
        elif b["kind"] == "shout":
            y_calc += len(b["lines"]) * line_h
        elif b["kind"] == "foot":
            y_calc += len(b["lines"]) * t.fs(20) + 8
        elif b["kind"] == "mstat":
            # mstat 块：上留白 + 分隔 + N 行 + 下留白，高度只算一次
            _mrows = len(b.get("rows") or [])
            y_calc += 10 + 8
            y_calc += _mrows * (t.fs(20) + 24)
            y_calc += 16   # 底部留白：shout 块自身无上留白，ascent 再吃 ~7px，8px 会视觉粘连
        elif b["kind"] == "rstat":
            y_calc += line_h + 8
        elif b["kind"] == "score":
            y_calc += t.fs(34) + 12
        elif b["kind"] == "chips":
            _cw = _chips_layout(b["items"], t, content_w)
            _row_h, *_ = _chip_metrics(t)
            y_calc += len(_cw) * _row_h + 4
        elif b["kind"] == "chipline":
            y_calc += _chip_metrics(t)[0] + 6
        elif b["kind"] == "cmd":
            y_calc += len(b["lines"]) * line_h
        elif b["kind"] == "alert":
            y_calc += len(b["lines"]) * t.fs(20) + 4
        else:
            y_calc += len(b["lines"]) * line_h
    _tail = 36 if body.startswith("@wmhdr|") else 60
    h = y_calc + pad + _tail + (FOOT_H if source else 0)
    c = Canvas(width, h, t.bg, theme=t)
    decor.background(c, t, tpl="guide")
    w.card(c, t, (pad, pad, width - pad, h - pad))

    y = y0
    if avatar:
        try:
            img = _cover_square(Image.open(BytesIO(avatar)).convert("RGBA"), _av,
                                vertical=avatar_vertical)
            c.paste(img, (pad_in, y), rounded_mask((_av, _av), 20))
        except Exception:
            pass  # 头像坏了就跳过，正文照常
        c.text((pad_in + _av + 20, y + _av // 2), title or "攻略",
               size=t.fs(24), color=t.text, role="bold", anchor="lm")
        y += _av + 20
    elif title:
        if getattr(t, "title_fixed", False):
            # 固定覆盖层：像素定位（距左上）+ 独立字号，可拖动调整；
            # 不参与正文布局流、不改变画布高度 → 拖动松手不会整卡跳变
            t_x = float(getattr(t, "title_x", 48))
            t_y = float(getattr(t, "title_y", 44))
            t_fs = getattr(t, "title_fs", 22)
            tw = Canvas.text_width(title, t.fs(t_fs), "bold")
            c.add_region("title", [t_x, t_y - 10, t_x + tw + 26, t_y + t.fs(t_fs) + 6],
                         label="标题", group="标题")
            c.rounded_rect((t_x, t_y - 10, t_x + 6, t_y + t.fs(t_fs) + 2), radius=3, fill=t.accent)
            c.text((t_x + 20, t_y + t.fs(t_fs) // 2 - 4), title, size=t.fs(t_fs),
                   color=t.text, role="bold", anchor="lm")
        else:
            c.add_region("title", [pad_in, y - 10, pad_in + content_w, y + head_h],
                         label="标题", group="标题")
            c.rounded_rect((pad_in, y - 10, pad_in + 6, y + head_h), radius=3, fill=t.accent)
            c.text((pad_in + 20, y + head_h // 2 - 4), title, size=t.fs(22), color=t.text, role="bold", anchor="lm")
            y += head_h + 10

    # ---- 逐块绘制 ----
    for b in blocks:
        kind = b["kind"]
        if kind == "head":
            col = (getattr(t, "aug_color", "") or t.warn) if b["raw"].startswith("【集团卡】") else t.accent
            # 分区标题热区（集团卡标题→aug_color，其余→accent），就地编辑可点选
            c.add_region("aug_color" if b["raw"].startswith("【集团卡】") else "accent",
                         [pad_in, y - 4, pad_in + content_w, y + head_h],
                         label="分区标题", group="气泡")
            c.text((pad_in, y), b["lines"][0], size=t.fs(22), color=col, role="bold", anchor="lm")
            y += head_h + 10
            continue
        if kind in ("skill", "aug", "generic"):
            box_x0, box_y0 = pad_in, y
            box_w = content_w
            box_h = len(b["lines"]) * line_h + BUB_PAD_Y * 2
            base = _eff_card(t)
            bub_r = getattr(t, "bubble_radius", 12)
            bub_w = getattr(t, "bubble_border_width", 1)
            if kind == "aug":
                c.add_region("aug_color",
                             [box_x0, box_y0, box_x0 + box_w, box_y0 + box_h],
                             label="集团卡气泡", group="气泡")
                box_col = getattr(t, "aug_color", "") or t.warn
                f_a = getattr(t, "aug_fill_alpha", 13)
                b_a = getattr(t, "aug_border_alpha", 55)
                c.rounded_rect((box_x0, box_y0, box_x0 + box_w, box_y0 + box_h),
                               radius=bub_r, fill=box_col, fill_alpha=f_a * 255 // 100,
                               outline=_tint(box_col, b_a / 100.0, base), width=bub_w)
            else:
                c.add_region("bubble",
                             [box_x0, box_y0, box_x0 + box_w, box_y0 + box_h],
                             label="技能气泡", group="气泡")
                box_col = t.accent
                f_a = getattr(t, "bubble_fill_alpha", 13)
                b_a = getattr(t, "bubble_border_alpha", 55)
                # 底色真 alpha 混合：透出背景图案，半透明时不显死色
                c.rounded_rect((box_x0, box_y0, box_x0 + box_w, box_y0 + box_h),
                               radius=bub_r, fill=box_col, fill_alpha=f_a * 255 // 100,
                               outline=_tint(box_col, b_a / 100.0, base), width=bub_w)
            ty = box_y0 + BUB_PAD_Y
            first = True
            for ln in b["lines"]:
                if first:
                    name, desc = _split_name(ln)
                    if name:
                        c.text((box_x0 + BUB_PAD_X, ty + line_h // 2), name,
                               size=font_size, color=box_col, role="bold", anchor="lm")
                        x_off = box_x0 + BUB_PAD_X + Canvas.text_width(name, font_size, "bold")
                        if desc:
                            c.text((x_off, ty + line_h // 2), desc,
                                   size=font_size, color=t.text, anchor="lm")
                    else:
                        c.text((box_x0 + BUB_PAD_X, ty + line_h // 2), ln,
                               size=font_size, color=t.text, anchor="lm")
                    first = False
                else:
                    c.text((box_x0 + BUB_PAD_X, ty + line_h // 2), ln,
                           size=font_size, color=t.text, anchor="lm")
                ty += line_h
            y = box_y0 + box_h + 4
            continue
        if kind == "ctitle":
            # 居中大标题 + 前置图标 + 贯通横线（对齐 Warframe Rabbit「武器倾向」卡头条）
            _cfs = t.fs(34)
            _ifs = t.fs(26)                        # 图标比文字略小，参考卡即如此
            _ict = b.get("icon") or ""
            _iw = (_ifs + 8) if (_ict and icons.exists(_ict)) else 0
            _tw = Canvas.text_width(b["raw"], _cfs, "bold")
            _lx = pad_in + max(0, int((content_w - _tw - _iw) / 2))
            _cy = y + _cfs // 2
            c.add_region("title", [pad_in, y - 6, pad_in + content_w, y + _cfs],
                         label="大标题", group="标题")
            if _iw:
                icons.draw(c, _ict, _lx + _ifs // 2, _cy, _ifs, t.accent)
            c.text((_lx + _iw, _cy), b["raw"], size=_cfs,
                   color=t.accent, role="bold", anchor="lm")
            y += _cfs + 10
            c.line((pad_in, y, pad_in + content_w, y), fill=t.divider, width=1)
            y += 14
            continue
        if kind == "disp":
            # 紫卡现行规则面板。token 全部先 Canvas.text_width 度量再绘制，
            # 显式 gap（不用空格凑），行尾对照文本不随前面内容漂移。
            _cur = b["cur"]
            _before_txt = b.get("before") or ""
            _rh = t.fs(30)
            _box_h = 2 * _rh + BUB_PAD_Y * 2
            _nfs = t.fs(26)                        # 行1 字号
            _lfs = t.fs(26)                        # 行2 字号
            _ifs = t.fs(26)                        # 武器图标尺寸
            _gap = t.fs(6)
            _gap_big = t.fs(16)
            _pad_x = t.fs(32)                      # 面板左右内边距
            _base = _eff_card(t)
            _ico = "sword" if b["cat"].startswith("近战") else "gun"
            _has_ico = icons.exists(_ico)
            _cat_txt = b["cat"] + " -"   # 不用前导空格凑间距，改由显式 _gap 控制
            _im_ico = None
            if b.get("icon"):
                try:
                    from ..weapon_icons import icon_cache_path
                    _p_ico = icon_cache_path(b["icon"])
                    if _p_ico is not None:
                        _im_ico = Image.open(_p_ico).convert("RGBA")
                        _bb = _im_ico.getchannel("A").getbbox()
                        if _bb:
                            _im_ico = _im_ico.crop(_bb)
                except Exception:  # noqa: BLE001 — 图源异常回落到几何图标
                    _im_ico = None
            _ico_w = _ico_h = 0
            if _im_ico is not None and _im_ico.width and _im_ico.height:
                # 图标高度必须 ≤ 行高，否则会向下压到行2（card-design 重叠红线）
                _ics = min(_rh / _im_ico.height, (_rh * 2.0) / _im_ico.width)
                _ico_w = max(1, int(round(_im_ico.width * _ics)))
                _ico_h = max(1, int(round(_im_ico.height * _ics)))
                _ico_slot = _ico_w + _gap
            else:
                _im_ico = None
                _ico_slot = (_ifs + _gap) if _has_ico else 0
            def _fit(text, size, role, max_w):
                """度量优先截断：逐字删到 text+… 不超宽（不用字符数估宽）。"""
                text = str(text or "")
                if max_w <= 0:
                    return ""
                if Canvas.text_width(text, size, role) <= max_w:
                    return text
                while text and Canvas.text_width(text + "…", size, role) > max_w:
                    text = text[:-1]
                return (text + "…") if text else ""

            # 可用宽度 = 内容区 - 左右内边距；行1 再扣图标槽与类别段
            _avail = content_w - _pad_x * 2
            _cat_w = Canvas.text_width(_cat_txt, _nfs, "regular")
            _name_tok = _fit("[" + b["name"] + "]", _nfs, "bold",
                             max(40, _avail - _ico_slot - _gap - _cat_w))
            _w_a = _ico_slot + Canvas.text_width(_name_tok, _nfs, "bold") \
                + _gap + _cat_w
            # 行2 三段 token 的度量（决定面板宽度，禁止按字符数估算）
            _t1 = "现行 " + _cur
            _t2 = "· 全武器统一"
            _t1_w = Canvas.text_width(_t1, _lfs, "bold")
            _t2_w = Canvas.text_width(_t2, _lfs, "regular")
            _t3_max = _avail - _t1_w - _gap - _t2_w - _gap_big
            # 放不下对照就整体省略，绝不画到画布外（card-design 溢出红线）
            _t3 = _fit(_before_txt, _lfs, "regular", _t3_max) if _t3_max >= 60 else ""
            _t3_w = Canvas.text_width(_t3, _lfs, "regular") if _t3 else 0
            _w_b = _t1_w + _gap + _t2_w + (_gap_big + _t3_w if _t3 else 0)
            _box_w = int(min(content_w, max(_w_a, _w_b) + _pad_x * 2))
            _bx0, _by0 = pad_in, y
            c.add_region("accent", [_bx0, _by0, _bx0 + _box_w, _by0 + _box_h],
                         label="紫卡现行规则面板", group="气泡")
            c.rounded_rect((_bx0, _by0, _bx0 + _box_w, _by0 + _box_h),
                           radius=getattr(t, "bubble_radius", 12),
                           fill=t.accent_soft,
                           outline=_tint(t.accent, 0.85, _base), width=1)
            _c1 = _by0 + BUB_PAD_Y + _rh // 2
            _c2 = _by0 + BUB_PAD_Y + _rh * 3 // 2

            def _tok(label, x, cy, text, size, color, role="regular"):
                """disp 块唯一文本入口：度量 + 绘制 + 真实 bbox 登记同源。"""
                w, h = Canvas.text_size(text, size, role)
                c.text((x, cy), text, size=size, color=color, role=role, anchor="lm")
                if boxes is not None:
                    boxes.append((label, x, cy - h // 2, x + w, cy + h // 2))
                return x + w

            # ---- 行1：图标 + [名字] 类型 - ----
            _tx = _bx0 + _pad_x
            if _im_ico is not None:
                _ico_img = _im_ico.resize((_ico_w, _ico_h), Image.LANCZOS)
                c.paste(_ico_img, (_tx, _c1 - _ico_h // 2), _ico_img)
                if boxes is not None:
                    boxes.append(("disp.icon", _tx, _c1 - _ico_h // 2,
                                  _tx + _ico_w, _c1 - _ico_h // 2 + _ico_h))
                _tx += _ico_slot
            elif _has_ico:
                icons.draw(c, _ico, _tx + _ifs // 2, _c1, _ifs, t.text_sub)
                if boxes is not None:
                    boxes.append(("disp.geom_icon", _tx, _c1 - _ifs // 2,
                                  _tx + _ifs, _c1 + _ifs // 2))
                _tx += _ifs + _gap
            # 名字（含括号）与类别是两个 token，中间显式 _gap；不用空格凑间距，
            # 也避免 check_boxes 把零间隙的贴合括号误判成「间距失控」。
            _tx = _tok("disp.name", _tx, _c1, _name_tok, _nfs, t.link, "bold")
            _tx += _gap
            _tok("disp.cat", _tx, _c1, _cat_txt, _nfs, t.accent)
            # ---- 行2：现行倍率 · 全武器统一  [改动前对照] ----
            _lx = _bx0 + _pad_x
            _lx = _tok("disp.cur", _lx, _c2, _t1, _lfs, t.link, "bold") + _gap
            _lx = _tok("disp.unified", _lx, _c2, _t2, _lfs, t.text)
            if _t3:
                _lx += _gap_big
                _tok("disp.before", _lx, _c2, _t3, _lfs, t.accent)
            # 面板与下方脚注留 16px（与脚注行距同层一致，避免贴脸）
            y = _by0 + _box_h + 16
            continue
        if kind == "wmhdr":
            # ---- wm 市价卡头条块（对齐 WF.Market 订单页）----
            _c0 = b["cols"]
            _zh, _en, _cat = _c0[0], _c0[1], _c0[2]
            _ducats, _tax, _sellers, _online = _c0[3], _c0[4], _c0[5], _c0[6]
            _eff = _c0[8] if len(_c0) > 8 else ""      # 效果行（MOD 面板文本，可空）
            _bar_fs = wm.bar_fs
            # ① 顶部栏：交易市场 / MARKET ORDERS（左）· 买家 WTS（右，主色）
            c.text((pad_in, y), "交易市场", size=_bar_fs, color=t.text, role="bold", anchor="lm")
            _w1 = Canvas.text_width("交易市场", _bar_fs, "bold")
            c.text((pad_in + _w1 + 10, y), "MARKET ORDERS", size=t.fs(15), color=t.text_sub, anchor="lm")
            _rt = "买家 WTS"
            _rw = Canvas.text_width(_rt, _bar_fs, "bold")
            c.text((pad_in + content_w - _rw, y), _rt, size=_bar_fs, color=t.link, role="bold", anchor="lm")
            y += _bar_fs + 12
            c.line((pad_in, y, pad_in + content_w, y), fill=t.divider, width=2)
            y += 14
            # ② 物品块：左图标 + 右文字栈（图标槽按 AR 自适应）
            _ix, _iy = pad_in, y
            if _icon_img is not None:
                try:
                    # 等比自适应不裁切 + 浅主色底衬
                    _plate = Image.new("RGBA", (_iw, _ih), (0, 0, 0, 0))
                    _pd = ImageDraw.Draw(_plate)
                    _pd.rounded_rectangle((0, 0, _iw, _ih), radius=14,
                                          fill=_tint(t.accent, 0.10, base))
                    _im = _contain_box(_icon_img, _iw, _ih, pad=max(4, int(_ibase * 0.04)))
                    _plate.alpha_composite(_im)
                    c.paste(_plate, (_ix, _iy), rounded_mask((_iw, _ih), 14))
                except Exception:  # noqa: BLE001 — 图标坏了就回落几何占位
                    _icon_img = None
            if _icon_img is None:
                # 几何占位（PIL 默认字体无中文字形，不用文字避免豆腐框）
                _ph = Image.new("RGBA", (_iw, _ih), (0, 0, 0, 0))
                _pd = ImageDraw.Draw(_ph)
                _pd.rounded_rectangle((0, 0, _iw, _ih), radius=14,
                                      fill=_tint(t.accent, 0.16, base))
                _pd.rounded_rectangle((int(_iw * 0.28), int(_ih * 0.28),
                                       int(_iw * 0.72), int(_ih * 0.72)),
                                      radius=int(min(_iw, _ih) * 0.10),
                                      outline=_tint(t.accent, 0.75, base), width=3)
                c.paste(_ph, (_ix, _iy), rounded_mask((_iw, _ih), 14))
            if boxes is not None:
                boxes.append(("wm.hdr.item_icon", _ix, _iy, _ix + _iw, _iy + _ih))
            _tx = _ix + _iw + 18
            _tw_max = (pad_in + content_w) - _tx

            def _fit_txt(s, fs, role="regular"):
                """按头条块右栏宽度截断加省略号，防长名/长描述溢出卡片。"""
                if not s or Canvas.text_width(s, fs, role) <= _tw_max:
                    return s
                _s = s
                while _s and Canvas.text_width(_s + "…", fs, role) > _tw_max:
                    _s = _s[:-1]
                return (_s + "…") if _s else "…"

            _cy = _iy
            c.text((_tx, _cy), _fit_txt(_zh, wm.title_fs, "bold"), size=wm.title_fs,
                   color=t.text, role="bold", anchor="lm"); _cy += wm.title_lh
            if _en:
                c.text((_tx, _cy), _fit_txt(_en, wm.en_fs), size=wm.en_fs,
                       color=t.text_sub, anchor="lm"); _cy += wm.en_lh
            if _cat:
                c.text((_tx, _cy), _fit_txt(_cat, wm.sub_fs), size=wm.sub_fs,
                       color=t.text_sub, anchor="lm"); _cy += wm.sub_lh
            if _eff:
                c.text((_tx, _cy), _fit_txt(_eff, wm.sub_fs), size=wm.sub_fs,
                       color=t.text_sub, anchor="lm"); _cy += wm.sub_lh
            y = _iy + max(_ih, _cy - _iy) + 14
            c.line((pad_in, y, pad_in + content_w, y), fill=t.divider, width=1)
            y += 14
            # ③ 统计条：杜卡德 / 交易税 / 在售卖家（绘制见 market_currency）
            y = market_currency.draw_stats_strip(
                c, t, pad_in, y, content_w, boxes, base, _tint,
                _ducats, _tax, _sellers, _online)
            c.line((pad_in, y, pad_in + content_w, y), fill=t.divider, width=1)
            y += 14
            # ④ 玩家卖单 区块标题（右：在线优先 / ONLINE FIRST）
            c.text((pad_in, y), "玩家卖单", size=wm.sect_fs, color=t.text, role="bold", anchor="lm")
            _sw = Canvas.text_width("玩家卖单", wm.sect_fs, "bold")
            c.text((pad_in + _sw + 10, y), "SELL ORDERS", size=t.fs(16), color=t.text_sub, anchor="lm")
            _on = "在线优先 / ONLINE FIRST"
            _ow = Canvas.text_width(_on, t.fs(15), "regular")
            c.text((pad_in + content_w - _ow, y), _on, size=t.fs(15), color=t.text_sub, anchor="lm")
            y += wm.sect_fs + 12
            continue
        if kind == "tbl":
            rows = b["rows"]
            # 头像列 + 价格渐变气泡；列序见 @hdr（卖家|等级?|状态|声望|数量|白金价格）
            tbl_fs = wm.tbl_fs
            row_h = wm.row_h
            AV = max(38, tbl_fs + 12)                      # 头像直径
            box_h = len(rows) * row_h + 14
            box_x0, box_y0 = pad_in, y
            base = _eff_card(t)
            bub_r = getattr(t, "bubble_radius", 12)
            bub_w = getattr(t, "bubble_border_width", 1)
            # 列表气泡背景
            c.add_region("tbl",
                         [box_x0, box_y0, box_x0 + content_w, box_y0 + box_h],
                         label="列表", group="表格")
            c.rounded_rect((box_x0, box_y0, box_x0 + content_w, box_y0 + box_h),
                           radius=bub_r, fill=t.accent,
                           fill_alpha=getattr(t, "tbl_bubble_alpha", 9) * 255 // 100,
                           outline=_tint(t.accent, getattr(t, "tbl_bubble_border_alpha", 38) / 100.0, base),
                           width=bub_w)
            # 头像列固定宽 + 其余按比例（MOD 6 列含等级，否则 5 列）
            av_w = AV + 14
            col_area_x0 = box_x0 + av_w
            col_area_w = (box_x0 + content_w) - col_area_x0
            _headers = b.get("headers") or []
            _has_rank = any(h.strip() == "等级" for h in _headers)
            _ncols = 6 if _has_rank else 5
            _ratios = market_currency.table_ratios(_has_rank)
            col_x = [col_area_x0]
            for _r in _ratios:
                col_x.append(col_x[-1] + col_area_w * _r)
            # 列竖线
            for ci in range(1, len(_ratios)):
                xx = col_x[ci]
                c.line((xx, box_y0 + 7, xx, box_y0 + box_h - 7),
                       fill=_tint(t.accent, getattr(t, "tbl_col_line_alpha", 26) / 100.0, base), width=1)
            # 表头 → 列角色
            _HS = {"用户名": "name", "卖家": "name", "用户": "name",
                   "数量": "qty", "等级": "rank",
                   "价格": "price", "单价": "price", "白金价格": "price", "白金": "price",
                   "状态": "status", "声望": "rep", "信誉": "rep"}
            _col_roles = [_HS.get(h.strip(), "text") for h in (_headers + [""] * _ncols)[:_ncols]]
            _price_ci = _col_roles.index("price") if "price" in _col_roles else _ncols - 1
            _name_color = getattr(t, "link", "#8B5CF6")
            # 价格梯度（最低价标绿＝最优）
            _prices = []
            for r in rows:
                if r["hdr"]:
                    continue
                _pc = (r["cols"] + [""] * 8)
                try:
                    _prices.append(int(float(_pc[_price_ci])))
                except Exception:
                    pass
            _pmin = min(_prices) if _prices else 0
            _pmax = max(_prices) if _prices else 0
            for ri, r in enumerate(rows):
                ry = box_y0 + 7 + ri * row_h
                # 末两列恒 uid/头像；前 _ncols 列可见
                _full = r["cols"] + [""] * (_ncols + 2)
                _c = _full[:_ncols]
                uid = _full[_ncols]
                if r["hdr"]:
                    # 表头底色覆盖头像列
                    h_a = getattr(t, "tbl_header_alpha", 24)
                    if h_a > 0:
                        c.rounded_rect((box_x0 + 4, ry, col_x[-1] - 4, ry + row_h),
                                       radius=8, fill=t.accent, fill_alpha=h_a * 255 // 100)
                    market_currency.draw_table_header(
                        c, t, col_x, box_x0, av_w, ry, row_h, wm.hdr_fs, _c, boxes)
                    continue
                # 头像：真实优先，取不到回落占位
                ax = box_x0 + 8
                ay = ry + (row_h - AV) // 2
                _real = (avatars or {}).get(uid) if uid else None
                _drew_real = False
                if _real:
                    # 真头像：裁方 → 圆形蒙版 → 粘贴
                    try:
                        _aimg = Image.open(BytesIO(_real)).convert("RGBA")
                        _aimg = _cover_square(_aimg, AV)
                        c.paste(_aimg, (ax, ay), rounded_mask((AV, AV), AV // 2))
                        _drew_real = True
                    except Exception:  # noqa: BLE001
                        _drew_real = False
                if not _drew_real:  # 中性占位：灰底+人形剪影，不冒充真人照片
                    _ph = Image.new("RGBA", (AV, AV), (0, 0, 0, 0))
                    _pd = ImageDraw.Draw(_ph)
                    _muted = _tint(t.accent, 0.16, base)
                    _pd.ellipse((0, 0, AV, AV), fill=_muted)
                    _fg = _tint(base, 0.55, "#9aa0a6")
                    _hr = AV * 0.17
                    _hx, _hy = AV // 2, int(AV * 0.37)
                    _pd.ellipse((_hx - _hr, _hy - _hr, _hx + _hr, _hy + _hr), fill=_fg)  # 头
                    _sr = AV * 0.30
                    _pd.ellipse((_hx - _sr, int(AV * 0.55), _hx + _sr, int(AV * 1.15)), fill=_fg)  # 肩
                    c.paste(_ph, (ax, ay), rounded_mask((AV, AV), AV // 2))
                for ci, cell in enumerate(_c):
                    role = _col_roles[ci] if ci < len(_col_roles) else "text"
                    if role == "rank":
                        _col = market_currency.rank_colors(t).get(
                            cell, (t.accent, 16))[0]
                        market_currency.draw_rank_cell(
                            c, t, (col_x[ci] + col_x[ci + 1]) // 2, ry, row_h,
                            tbl_fs, cell, _tint(_col, 0.5, base), boxes)
                    elif role == "price":
                        try:
                            pv = int(float(cell)) if cell not in ("", "-") else _pmin
                        except Exception:
                            pv = _pmin
                        if pv == _pmin:
                            pcol = t.ok  # 最低价＝最优价，标绿
                        else:
                            tt = 0.5 if _pmax <= _pmin else (pv - _pmin) / (_pmax - _pmin)
                            pcol = _price_grad(tt)
                        # 气泡 + 后置白金图标居中，价格用独立大字
                        market_currency.draw_price_cell(
                            c, t, (col_x[ci] + col_x[ci + 1]) // 2, ry, row_h,
                            tbl_fs, cell, pcol, _tint(pcol, 0.65, base),
                            _text_on(pcol), boxes, price_fs=wm.price_fs)
                    elif role == "status":
                        _is_game = "游戏" in cell
                        _is_on = ("在线" in cell) or _is_game
                        _dot_col = t.link if _is_game else (t.ok if _is_on else t.text_sub)
                        _dr = max(4, int(tbl_fs * 0.16))
                        _dx = col_x[ci] + 14 + _dr
                        c.ellipse((_dx - _dr, ry + row_h // 2 - _dr,
                                   _dx + _dr, ry + row_h // 2 + _dr), fill=_dot_col)
                        c.text((col_x[ci] + 14 + _dr * 2 + 6, ry + row_h // 2), cell,
                               size=tbl_fs, color=_dot_col, role="bold", anchor="lm")
                    elif role == "name":
                        # 名称限宽截断，绝不压到下一列
                        _maxw = (col_x[ci + 1] - col_x[ci]) - 14 - 6
                        _disp = cell
                        if Canvas.text_width(_disp, tbl_fs, "bold") > _maxw:
                            while _disp and Canvas.text_width(
                                    _disp + "…", tbl_fs, "bold") > _maxw:
                                _disp = _disp[:-1]
                            _disp = (_disp + "…") if _disp else "…"
                        c.text((col_x[ci] + 14, ry + row_h // 2), _disp, size=tbl_fs,
                               color=_name_color, role="bold", anchor="lm")
                    else:
                        c.text((col_x[ci] + 14, ry + row_h // 2), cell, size=tbl_fs,
                               color=t.text, anchor="lm")
                # 行间细线（列表分隔线）
                if ri < len(rows) - 1:
                    c.line((col_x[0] + 6, ry + row_h, col_x[-1] - 6, ry + row_h),
                           fill=_tint(t.accent, 0.24, base), width=1)
            y = box_y0 + box_h + 8
            continue
        if kind == "warn":
            box_col = getattr(t, "aug_color", "") or t.warn
            f_a = getattr(t, "aug_fill_alpha", 13)
            b_a = getattr(t, "aug_border_alpha", 55)
            bub_r = getattr(t, "bubble_radius", 12)
            bub_w = getattr(t, "bubble_border_width", 1)
            box_h = len(b["lines"]) * line_h + BUB_PAD_Y * 2
            c.rounded_rect((pad_in, y, pad_in + content_w, y + box_h),
                           radius=bub_r, fill=box_col, fill_alpha=f_a * 255 // 100,
                           outline=_tint(box_col, b_a / 100.0, base), width=bub_w)
            ty = y + BUB_PAD_Y
            for ln in b["lines"]:
                c.text((pad_in + BUB_PAD_X, ty + line_h // 2), ln, size=font_size,
                       color=box_col, role="bold", anchor="lm")
                ty += line_h
            y = y + box_h + 4
            continue
        if kind == "page":
            c.text((pad_in, y), b["lines"][0] if b["lines"] else " ", size=t.fs(16),
                   color=t.text_sub, anchor="lm")
            y += t.fs(20) + 8
            continue
        if kind == "rstat":
            # 紫卡词条行：数值(accent 加粗) + 名称(link 加粗) + 范围|偏差(弱化小字)
            _fs = t.fs(22)
            _cy = y + line_h // 2
            _tx = pad_in
            c.text((_tx, _cy), b["val"], size=_fs, color=t.accent, role="bold", anchor="lm")
            _tx += Canvas.text_width(b["val"], _fs, "bold")
            if b["name"]:
                c.text((_tx, _cy), " " + b["name"], size=_fs, color=t.link, role="bold", anchor="lm")
                _tx += Canvas.text_width(" " + b["name"], _fs, "bold")
            if b["rng"]:
                c.text((_tx, _cy), " " + b["rng"], size=t.fs(18), color=t.text_sub, anchor="lm")
            y += line_h + 8
            continue
        if kind == "score":
            # 综合评分行：前置文字 + 强调框芯片（评分档位）+ 后缀文字
            _fs = t.fs(24)
            _cy = y + (t.fs(34) + 12) // 2
            _tx = pad_in
            if b["pre"]:
                c.text((_tx, _cy), b["pre"], size=_fs, color=t.text, role="bold", anchor="lm")
                _tx += Canvas.text_width(b["pre"], _fs, "bold")
            if b["chip"]:
                _cw = Canvas.text_width(b["chip"], _fs, "bold") + 22
                _chh = _fs + 10
                c.rounded_rect((_tx, _cy - _chh // 2, _tx + _cw, _cy + _chh // 2),
                               radius=_chh // 2, fill=t.accent_soft, outline=t.accent, width=2)
                c.text((_tx + _cw // 2, _cy), b["chip"], size=_fs, color=t.accent,
                       role="bold", anchor="mm")
                _tx += _cw + 8
            if b["post"]:
                c.text((_tx, _cy), " " + b["post"], size=_fs, color=t.text, role="bold", anchor="lm")
            y += t.fs(34) + 12
            continue
        if kind == "chips":
            # 芯片流式排布（当前配卡 MOD 列表，参考卡为圆角胶囊）
            row_h, chip_h, cpx, chip_fs = _chip_metrics(t)
            _rows = _chips_layout(b["items"], t, content_w)
            _base = _eff_card(t)
            for r in _rows:
                _tx = pad_in
                for name, _cwid in r:
                    c.rounded_rect((_tx, y, _tx + _cwid, y + chip_h), radius=chip_h // 2,
                                   fill=t.accent_soft,
                                   outline=_tint(t.accent, 0.85, _base), width=1)
                    c.text((_tx + _cwid // 2, y + chip_h // 2), name, size=chip_fs,
                           color=t.link, role="bold", anchor="mm")
                    _tx += _cwid + 10
                y += row_h
            y += 4
            continue
        if kind == "chipline":
            # 单行：左芯片 + 右说明（每词条贡献行）
            row_h, chip_h, cpx, chip_fs = _chip_metrics(t)
            _base = _eff_card(t)
            _cy = y + row_h // 2
            _tx = pad_in
            _cw = Canvas.text_width(b["chip"], chip_fs, "bold") + cpx * 2
            c.rounded_rect((_tx, _cy - chip_h // 2, _tx + _cw, _cy + chip_h // 2),
                           radius=chip_h // 2, fill=t.accent_soft,
                           outline=_tint(t.accent, 0.85, _base), width=1)
            c.text((_tx + _cw // 2, _cy), b["chip"], size=chip_fs,
                   color=t.link, role="bold", anchor="mm")
            _tx += _cw + 12
            if b["rest"]:
                c.text((_tx, _cy), b["rest"], size=t.fs(20), color=t.text, anchor="lm")
            y += row_h + 6
            continue
        if kind == "cmd":
            # 指令行（紫粉色加粗，对齐参考卡 footer 指令段）
            for ln in b["lines"]:
                c.text((pad_in, y), ln, size=font_size, color=t.link, role="bold", anchor="lm")
                y += line_h
            continue
        if kind == "alert":
            # 红/橙警示小字（*注意：…）
            for ln in b["lines"]:
                c.text((pad_in, y), ln, size=t.fs(16), color=t.err, anchor="lm")
                y += t.fs(20)
            y += 4
            continue
        if kind == "title":
            _title_text = b["lines"][0] if b["lines"] else " "
            _tfs = t.fs(24)
            if _title_text.startswith("🛒 "):
                # 购物车图标替代 emoji，避免字体缺字形出豆腐块
                icons.draw(c, "cart", pad_in + _tfs // 2, y, _tfs, t.accent)
                c.text((pad_in + _tfs + 8, y), _title_text[2:], size=_tfs,
                       color=t.accent, role="bold", anchor="lm")
            else:
                c.text((pad_in, y), _title_text, size=_tfs,
                       color=t.accent, role="bold", anchor="lm")
            y += t.fs(26) + 6
            continue
        if kind == "shout":
            for ln in b["lines"]:
                c.text((pad_in, y), ln, size=font_size, color=t.text, anchor="lm")
                y += line_h
            continue
        if kind == "foot":
            for ln in b["lines"]:
                c.text((pad_in, y), ln, size=t.fs(16), color=t.text_sub, anchor="lm")
                y += t.fs(20)
            y += 4
            continue
        if kind == "mstat":
            # ---- 市价卡底部「价格统计」四行（优化版）----
            # 标签（左，加粗）+ 右侧三等分：最低(绿)/最高(红)/最新(主色)，
            # 每个值 = 小标签 + 数值胶囊（度量优先，组居中于等分列）。
            _fs_val = t.fs(20)
            _fs_cap = t.fs(14)
            _pill_h = int(t.fs(20)) + 14
            _row_h = _pill_h + 10
            _base = _eff_card(t)
            _rows_m = b.get("rows") or []
            # 上方留白 + 细分隔 + 小间距，避免贴住上方表格
            y += 10
            c.line((pad_in, y, pad_in + content_w, y), fill=t.divider, width=1)
            y += 8
            # 标签列取本块最大宽度；值区三等分，组按度量宽度居中（列内起点稳定）
            _label_w = max((Canvas.text_width(r["label"], t.fs(17), "bold") for r in _rows_m), default=0)
            _cap_w = Canvas.text_width("最高", _fs_cap, "regular")   # 三个小标签同宽（2 字）
            _val_x0 = pad_in + _label_w + t.fs(32)
            _sub_w = ((pad_in + content_w) - _val_x0) / 3.0
            _col_low, _col_high, _col_new = t.ok, t.err, t.link

            def _pill_w(num: str) -> int:
                return int(max(Canvas.text_width(num, _fs_val, "bold") + t.fs(16), _pill_h + 6))

            def _draw_pill(x: float, cy: int, num: str, col: str) -> None:
                _bw = _pill_w(num)
                _bx, _by = int(round(x)), int(cy - _pill_h // 2)
                c.rounded_rect((_bx, _by, _bx + _bw, _by + _pill_h), radius=_pill_h // 2,
                               fill=_tint(col, 0.18, _base),
                               outline=_tint(col, 0.85, _base), width=2)
                c.text((_bx + _bw // 2, cy), num, size=_fs_val, color=col, role="bold", anchor="mm")
                if boxes is not None:
                    boxes.append((f"mstat.{num}", _bx, _by, _bx + _bw, _by + _pill_h))

            for r in _rows_m:
                _cy = y + _pill_h // 2
                c.text((pad_in, _cy), r["label"], size=t.fs(17), color=t.text, role="bold", anchor="lm")
                if boxes is not None:
                    boxes.append(("mstat.label", pad_in, _cy - t.fs(17) // 2, pad_in + _label_w, _cy + t.fs(17) // 2))
                _subs = [("最低", r["low"], _col_low), ("最高", r["high"], _col_high), ("最新", r["newest"], _col_new)]
                for si, (cap, num, col) in enumerate(_subs):
                    _gw = _cap_w + 6 + _pill_w(num)                       # 组宽 = 小标签 + 间距 + 胶囊
                    _gx = _val_x0 + si * _sub_w + (_sub_w - _gw) / 2.0     # 居中于该等分列
                    c.text((_gx, _cy), cap, size=_fs_cap, color=t.text_sub, anchor="lm")
                    _draw_pill(_gx + _cap_w + 6, _cy, num, col)
                y += _row_h
            y += 16   # 与高度计算里的底部留白逐像素一致（8px 会让末行气泡贴住喊话文字）
            continue
        if kind == "sub":
            # 说明行：可带独立字号（@sub|文本|字号），行距与高度计算保持一致
            _sfs = b.get("fs") or font_size
            for ln in b["lines"]:
                c.text((pad_in, y), ln, size=_sfs, color=t.text, anchor="lm")
                y += max(line_h, _sfs + 4)
            continue
        for ln in b["lines"]:
            c.text((pad_in, y), ln, size=font_size, color=t.text, anchor="lm")
            y += line_h

    # 右下角来源小字（信息获取平台 + 时间戳）
    if source:
        if getattr(t, "source_fixed", False):
            # 固定覆盖层：像素定位（距右下）+ 独立字号，可拖动调整；
            # 不参与正文布局流、不改变画布高度 → 拖动松手不会整卡跳变
            s_x = width - float(getattr(t, "source_x", 48))
            s_y = h - float(getattr(t, "source_y", 52))
            s_fs = getattr(t, "source_fs", 12)
            sw = Canvas.text_width(source, t.fs(s_fs), "regular")
            c.add_region("source",
                         [s_x - sw, s_y - t.fs(s_fs), s_x, s_y + t.fs(s_fs)],
                         label="来源小字", group="标题")
            c.text((s_x, s_y), source, size=t.fs(s_fs), color=t.text_sub, anchor="rm")
        else:
            c.add_region("text_sub",
                         [width - pad_in - Canvas.text_width(source, t.fs(12), "regular"),
                          h - pad - 4 - t.fs(12), width - pad_in, h - pad + t.fs(12)],
                         label="来源小字", group="文字")
            c.text((width - pad_in, h - pad - 4), source, size=t.fs(12), color=t.text_sub, anchor="rm")

    # wm 市价卡对齐参考卡（干净版式）：不画角落装饰，避免遮挡右下来源小字
    if not body.startswith("@wmhdr|"):
        decor.corner(c, t)
    return c
