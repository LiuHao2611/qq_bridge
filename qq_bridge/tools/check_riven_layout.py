# -*- coding: utf-8 -*-
"""card-design 渲染后自检门禁 —— 紫卡分析卡 ``riven_analysis`` 模板。

跑在容器里（需要 qq_bridge 包 + Pillow）：

    python /app/tools/check_riven_layout.py --check
    python /app/tools/check_riven_layout.py --check --out /app/tmp/riven_fixed.png
    python /app/tools/check_riven_layout.py --selftest

检查项（对齐 card-design 六条红线）：

  * 溢出    任一登记文本越出内容安全区（红线 2）
  * 重叠    同一布局层内两包围盒在 x、y 两方向同时相交（红线 1）
  * 间距    同一布局层内同行相邻元素间隙小于 min_gap（红线 3）
  * 列对齐  各行 value / stat.low 左起点、stat.high / grade 右终点偏差 > 1px（红线 4）
  * 接入    包围盒数量不足、或数据出现连续空格凑间距（红线 5）
  * 度量    包围盒取自**真实渲染**，不另写一套复算公式（红线 6）
  * 推荐    ``recs`` 区块（市场参考 / 推荐洗卡 / 推荐负面 / 类似紫卡 / 数据来源 / 提示）
            每行都要出包围盒；状态必须如实（失败态写原因，成功态写样本数/时间戳/来源）
  * 武器    剪影兜底的武器名小标必须登记包围盒且留在框内（旧版直画会压到框底线）

2026-11-04 22:25 新增最后一项：用户截图里 PNG 卡整块推荐词条与市场信息缺失，
旧自检只查数值行排版，完全测不到这个缺陷。现在删掉 ``recs`` 模板会直接 raise，
失败态写「暂无」会被 ``check_recs`` 拦下。

绝不降级：任何一组不过就 ``exit 1``；不吞异常、不跳过、不放宽阈值。
"""
from __future__ import annotations

import argparse
import io
import os
import re as _re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /app
# qq_bridge/__init__.py 里有 `from src.group_switch import ...`，必须让 /app 也在 sys.path；
# 脚本位于 /app/tools 时 Python 不会把 /app 加进去。后插入的 plugins 优先级更高。
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "plugins"))

from PIL import Image  # noqa: E402
from qq_bridge.pic import render_sync, resolve_theme  # noqa: E402
from qq_bridge.game_tools import riven_card  # noqa: E402

#: 同行相邻元素最小间隙（渲染像素；设计空间 STAT_GAP=6 × s≈2.69 → 约 16px）
MIN_GAP = 4
#: 巨型「A-」评级字母的排版框含字体 ascent 空白，与上方「综合评级」小标签天然上下叠放，
#: 不参与两两重叠判定（其余 overall.* 仍参与）。这是版式事实，不是放宽阈值。
SKIP_LABEL = "overall.grade"


# ---------------------------------------------------------------------------
# 检查器
# ---------------------------------------------------------------------------
def _layer(label: str) -> str:
    return label.split(".", 1)[0]


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """溢出 / 重叠 / 同行最小间距。boxes: (label, x0, y0, x1, y1)。"""
    problems: list[str] = []
    bx0, by0, bx1, by1 = bounds

    for label, x0, y0, x1, y1 in boxes:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越出内容安全区 %s (bounds=%s)"
                            % (label, (x0, y0, x1, y1), bounds))

    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            la, ax0, ay0, ax1, ay1 = a
            lb, bx_0, by_0, bx_1, by_1 = b
            if _layer(la) != _layer(lb) or SKIP_LABEL in (la, lb):
                continue
            y_ov = min(ay1, by_1) - max(ay0, by_0)
            if y_ov <= 0:
                continue
            x_ov = min(ax1, bx_1) - max(ax0, bx_0)
            if x_ov > 0:
                problems.append("[重叠] %s <-> %s: %s vs %s (x相交 %.1fpx)"
                                % (la, lb, a[1:], b[1:], x_ov))
            else:
                gapx = max(bx_0 - ax1, ax0 - bx_1)
                if 0 <= gapx < min_gap:
                    problems.append("[间距] %s <-> %s 同层相邻仅 %.1fpx < %dpx"
                                    % (la, lb, gapx, min_gap))
    return problems


def check_columns(boxes) -> list[str]:
    """固定列网格：同列元素起点/终点不得随前序内容宽度漂移。"""
    problems: list[str] = []
    checks = (("value", 1), ("stat.low", 1), ("stat.high", 3), ("grade", 3))
    for suffix, idx in checks:
        pts = [(b[0], b[idx]) for b in boxes
               if b[0].startswith("row") and b[0].endswith("." + suffix)]
        if len(pts) < 2:
            continue
        vals = [p[1] for p in pts]
        if max(vals) - min(vals) > 1:
            problems.append("[列对齐] %s 各行列位漂移 %dpx: %s"
                            % (suffix, max(vals) - min(vals), pts))
    return problems


def check_spaces(data) -> list[str]:
    """红线 5：禁止用连续空格凑间距 / 对齐。"""
    problems: list[str] = []

    def walk(v, path):
        if isinstance(v, str):
            if "  " in v:
                problems.append("[空格凑间距] %s=%r 含连续空格" % (path, v))
        elif isinstance(v, dict):
            for k, vv in v.items():
                walk(vv, path + "." + str(k))
        elif isinstance(v, (list, tuple)):
            for i, vv in enumerate(v):
                walk(vv, "%s[%d]" % (path, i))

    walk(data, "data")
    return problems


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
#: 成功态「市场 / 推荐词条」排版夹具。⚠️ 这里测的是**排版接线**，不是市场抓取；
#: 真实 WFM 抓取 / 聚合由 ``tools/check_riven_market.py`` 联网验证并渲染样张，
#: 两边互不冒充（prove-it：被测试的那部分不许被替身顶掉）。
_OK_RECS = {
    "status": "ok",
    "market": "WM 低价视图中位 200P｜区间 190–220P（同等级 rank8 样本 100 / 2026-11-04 23:05）",
    "pos": "多重、暴伤、毒伤（最贵 100 条词条频次·不计价）",
    "neg": "变焦 +100.0p/+100%（同分位 rank8 各 13 条·中位数·挂牌非成交）",
    "exact": "正负面相同 18 条｜中位 4000P（挂牌价·非成交）",
    "source": ("WM 挂牌要价·非成交价｜同等级 rank8｜同分位 k=13｜匹配：正负面相同"
               "｜样本 200｜未洗 3 · glaxion · 2026-11-04 23:05"),
}

#: 成功态 market bundle 夹具（字段与 23:0x 修订后的 fetch_market_bundle 一致），
#: 供 ``_real_data()`` 走**真实** ``build_card_data()`` 数据层链路。
_OK_MARKET = {
    "status": "ok", "slug": "glaxion", "ts": "2026-11-04 23:05",
    "source": ("WM 挂牌要价·非成交价｜同等级 rank8｜同分位 k=13｜匹配：正负面相同"
               "｜样本 200｜未洗 3"),
    "price_kind": "挂牌要价·非成交价", "rank": 8, "match_tier": "正负面相同",
    "pair_k": 13, "n": 100, "lo": 190.0, "hi": 220.0, "med": 200.0, "zero": 150.0,
    "sample_n": 200, "base": 200.0,
    "rec_pos": ["多重", "暴伤", "毒伤"],
    "rec_neg": {"slug": "zoom", "zh": "变焦", "n": 13, "pool_n": 84,
                "median": 300.0, "delta": 100.0, "pct": 100.0, "hot_n": 32},
    "exact": {"status": "ok", "n": 18, "median": 4000.0, "reason": ""},
    "desc_price_used": False,
}


def _err_recs(reason: str = "RivenMarketError: WFM 拍卖返回 0 条样本（glaxion / price_asc）") -> dict:
    return {
        "status": "error",
        "market": "获取失败（%s）" % reason,
        "pos": "不可用（市场数据获取失败）",
        "neg": "不可用（市场数据获取失败）",
        "exact": "不可用（市场数据获取失败）",
        "source": "WM v1 /auctions/search（数据不足，已拒绝估算） · 2026-11-04 23:05",
        "error": reason,
    }


def _row(value, name, low, mean, high, dir_, mag, grade="A-", bar=0.6,
         neg=False, icon="#27C4D9") -> dict:
    return {"value": value, "name": name, "sign": "-" if neg else "+",
            "range": "", "dev": "", "grade": grade, "bar": bar, "neg": neg,
            "icon": icon, "low": low, "mean": mean, "high": high,
            "dev_dir": dir_, "dev_mag": mag}


def _data(rows, **over) -> dict:
    d = {
        "brand": "紫卡分析", "corner": "统一倍率",
        "disp_dots": "", "disp_value": "1.20", "level": 8,
        "weapon_zh": "视使之触", "weapon_en": "ACRICA",
        "weapon_image": None, "weapon_type": "Rifle", "riven_name": "Acri-CariCan",
        "overall_grade": "A-", "overall_pct": "+4.916%",
        "layout": "3 正 1 负", "meta": "步枪 MR 14 满级",
        "stats": rows,
        "market": _OK_RECS["market"],
        "recs": dict(_OK_RECS),
        "hint": "偏差以统一倍率的理论中值为基准，正常范围为 ±10%（卡面取整可能略有偏差）。",
        "foot_left": "紫卡分析", "foot_right": "2026-11-04 21:30",
    }
    d.update(over)
    return d


def _real_data(market: dict | None = None) -> dict:
    """走真实链路 ``riven_card.build_card_data``（market **必填**，漏传会 raise）。"""
    parsed = {
        "weapon": "ACRICA", "weapon_zh": "视使之触", "riven_name": "Acri-CariCan",
        "type": "Rifle", "disp": 1.20, "mr": 14, "rank": 8,
        "stats": [
            {"en": "Critical Damage", "zh": "暴击伤害", "value": 108.8, "unit": "%", "sign": "+"},
            {"en": "Viral Damage", "zh": "病毒伤害", "value": 111.2, "unit": "%", "sign": "+"},
            {"en": "Multishot", "zh": "多重射击", "value": 130.2, "unit": "%", "sign": "+"},
            {"en": "Slash Damage", "zh": "切割伤害", "value": 108.7, "unit": "%", "sign": "-"},
        ],
    }
    return riven_card.build_card_data(parsed, market or _OK_MARKET,
                                      weapon_image=None, now="2026-11-04 21:30")


def _real_data_error() -> dict:
    """真实失败态链路：market_error_bundle() → build_card_data() → recs(status=error)。"""
    mkt = riven_card.market_error_bundle(
        riven_card.RivenMarketError("自检离线夹具：排版验证，不联网"))
    return _real_data(mkt)


def build_cases() -> list[tuple[str, dict]]:
    std4 = [
        _row("+108.8%", "暴击伤害", "121.1%", "135.0%", "148.5%", "低", "19.4%", "D", 0.32),
        _row("+111.2%", "病毒伤害", "91.1%", "101.2%", "111.4%", "高", "9.8%", "S", 0.99),
        _row("+130.2%", "多重射击", "91.1%", "101.2%", "111.4%", "高", "28.6%", "S", 0.99),
        _row("-108.7%", "切割伤害", "97.2%", "108.0%", "118.8%", "高", "0.7%", "B", 0.54,
             neg=True, icon="#E74C3C"),
    ]
    return [
        ("real_chain", _real_data()),
        ("std_4rows", _data(std4)),
        ("3rows_2pos1neg", _data(std4[:2] + [std4[3]], layout="2 正 1 负")),
        ("1row", _data([std4[0]], layout="1 正 0 负")),
        ("extreme_long", _data([
            _row("+1234.5%", "超长词条名称测试", "9999.9%", "9999.9%", "9999.9%", "低", "999.9%"),
            _row("-1234.5%", "超长词条名称测试", "9999.9%", "9999.9%", "9999.9%", "高", "999.9%",
                 neg=True),
        ], layout="1 正 1 负")),
        ("multiplier_unit", _data([
            _row("x1.23", "暴击倍率", "x1.02", "x1.13", "x1.24", "高", "8.8%"),
            _row("+2.3", "攻击范围", "1.8", "2.1", "2.4", "低", "9.5%"),
        ])),
        ("long_market", _data(std4, recs=dict(_OK_RECS, market=(
            "WM 低价视图中位 123456P｜区间 98765–999999P（同等级 rank8 样本 128 / 2026-11-04 23:05）"
            "｜最高 999999P｜挂牌时间跨度 30 天｜数据来自 warframe.market 实时聚合")))),
        ("market_ok_long", _data(std4, recs=dict(
            _OK_RECS,
            pos="多重、暴伤、毒伤、腐蚀、冰伤、火伤、射速、基伤（最贵 100 条词条频次·不计价）",
            neg="变焦 +1234567.8p/+99999%（同分位 rank8 各 9999 条·中位数·挂牌非成交）"))),
        ("market_exact_few", _data(std4, recs=dict(
            _OK_RECS,
            exact="不可估价：同组合同等级样本仅 2 条 < 3，不给估价（未用其他层级冒充）",
            source=("WM 挂牌要价·非成交价｜同等级 rank8｜同分位 k=13｜匹配：同负词条"
                    "｜样本 200｜未洗 3 · glaxion · 2026-11-04 23:05")))),
        ("market_exact_unknown", _data(std4, recs=dict(
            _OK_RECS,
            exact="不可估价：词条未收录 WFM 映射：FooDamage/BarDamage（未用其他层级冒充）"))),
        ("market_error", _data(std4, recs=_err_recs())),
        ("market_error_long", _data(std4, recs=_err_recs(
            "RivenMarketError: WFM 拍卖请求失败（glaxion / price_desc）："
            "ClientConnectorError: Cannot connect to host api.warframe.market:443 "
            "ssl:default [连接超时；请检查 Platform 头与 curl UA，或稍后重试]"))),
        ("real_chain_error", _real_data_error()),
        ("empty_stats_fields", _data([
            _row("+108.8%", "暴击伤害", "", "", "", "", ""),
            _row("-108.7%", "切割伤害", "", "", "", "", "", neg=True),
        ], level=None, weapon_en="", layout="1 正 1 负")),
    ]


# ---------------------------------------------------------------------------
# 渲染 + 门禁
# ---------------------------------------------------------------------------
def render_case(data: dict, png_out: str | None = None):
    boxes: list[tuple] = []
    t = resolve_theme(None, None)
    png = render_sync("riven_analysis", theme_name=None, card="紫卡分析",
                      data=data, boxes=boxes)
    im = Image.open(io.BytesIO(png))
    if png_out:
        with open(png_out, "wb") as f:
            f.write(png)
    return boxes, (0, 0, im.width, im.height)


def check_recs(boxes, data) -> list[str]:
    """推荐区块接入 + **市场口径**门禁（22:25 新增，23:0x 扩口径）。

    22:25 的缺陷是「整块市场/推荐词条消失」；23:0x 的缺陷是「拿最贵 100 条
    的算术均值当溢价，+3949%」。两种都要拦：前者查包围盒，后者查口径标注。
    """
    problems: list[str] = []
    recs = data.get("recs")
    if not isinstance(recs, dict) or recs.get("status") not in ("ok", "error"):
        problems.append("[接入] data.recs 缺失或 status 非法：%r" % (recs,))
        return problems
    labels = {b[0] for b in boxes}
    for key in ("market", "pos", "neg", "exact", "source", "hint"):
        if "recs.%s.label" % key not in labels:
            problems.append("[接入] 缺少 recs.%s.label：推荐区块整块没画出来" % key)
        if not any(b[0].startswith("recs.%s.line" % key) for b in boxes):
            problems.append("[接入] 缺少 recs.%s.line*：该行内容没画出来" % key)
    # 固定列网格：标签列左起点、值列首行左起点跨行不漂移
    for suffix in ("label", "line1"):
        pts = [b[1] for b in boxes
               if b[0].startswith("recs.") and b[0].endswith("." + suffix)]
        if len(pts) > 1 and max(pts) - min(pts) > 1:
            problems.append("[列对齐] recs.%s 列位漂移 %dpx"
                            % (suffix, max(pts) - min(pts)))
    # 状态如实：失败态必须写原因（禁止「暂无」冒充成功）；成功态必须标清口径
    if recs["status"] == "error":
        if "获取失败" not in recs.get("market", ""):
            problems.append("[状态] 失败态没写明获取失败原因（疑似又用「暂无」冒充成功）")
        if "暂无" in recs.get("market", ""):
            problems.append("[状态] 失败态写了「暂无」，属于静默降级")
        for key in ("pos", "neg", "exact"):
            if "不可用" not in recs.get(key, ""):
                problems.append("[状态] 失败态 recs.%s 没写明不可用" % key)
    else:
        need_kw = (("market", "样本"), ("market", "同等级"),
                   ("market", "低价视图"),
                   ("pos", "不计价"), ("neg", "中位数"), ("neg", "同分位"),
                   ("source", "非成交"), ("source", "匹配"),
                   ("source", "同分位"))
        for key, kw in need_kw:
            if kw not in recs.get(key, ""):
                problems.append("[口径] 成功态 recs.%s 缺少 %r；"
                                "禁止把挂牌价写成成交价、或拿最贵视图计价"
                                % (key, kw))
        if not recs.get("exact"):
            problems.append("[口径] 成功态 recs.exact 为空，精确匹配结果没上卡")
        if not _re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", recs.get("source", "")):
            problems.append("[状态] 成功态 recs.source 缺少市场数据时间戳")
    return problems


def check_weapon(boxes) -> list[str]:
    """武器剪影兜底标签必须在框内（2026-11-04 23:3x 新发现）。

    旧版这行字用 ``c.draw.text`` 直画，没登记包围盒；实测中文标签压到框底线外
    2~3px，而自检完全看不见（``check_boxes`` 只检查登记过的元素）。
    """
    problems: list[str] = []
    fr = [b for b in boxes if b[0] == "weaponframe.rect"]
    lb = [b for b in boxes if b[0] == "weapon.label"]
    if len(fr) != 1:
        problems.append("[接入] 武器剪影框包围盒缺失/重复，门禁看不见底行位置")
    if len(lb) != 1:
        problems.append("[接入] 武器名小标未走 _text 登记包围盒；直画会压线也没人拦")
    if fr and lb:
        _, fx0, fy0, fx1, fy1 = fr[0]
        _, lx0, ly0, lx1, ly1 = lb[0]
        if lx0 < fx0 + 1 or ly0 < fy0 + 1 or lx1 > fx1 - 1 or ly1 > fy1 - 1:
            problems.append("[溢出] 武器名小标 %s 越出剪影框 %s（四边至少留 1px）"
                            % ((lx0, ly0, lx1, ly1), (fx0, fy0, fx1, fy1)))
    return problems


def check_all(bounds, boxes, data) -> list[str]:
    problems = check_boxes(bounds, boxes)
    problems += check_columns(boxes)
    problems += check_spaces(data)
    problems += check_recs(boxes, data)
    problems += check_weapon(boxes)
    # 接入门禁：模板没接 _text 就一个框都不会产出（旧版模板会在这里被拦住）
    if len(boxes) < 8:
        problems.append("[接入] 模板仅产出 %d 个包围盒（<8），自检形同虚设；"
                        "请确认所有文本走 _text" % len(boxes))
    return problems


def selftest(cases) -> None:
    """prove-it：把这次真实犯过的错放回去，检查必须报错；改回来必须通过。"""
    # 1) 检查器本身能抓到重叠 / 越界
    bad = [("row1.stat.low", 100, 100, 200, 120),
           ("row1.stat.mid", 150, 100, 250, 120)]
    assert any("[重叠]" in p for p in check_boxes((0, 0, 800, 1000), bad)), \
        "检查器失效：两个明确相交的包围盒没有被拦住"
    assert any("[溢出]" in p for p in
               check_boxes((0, 0, 200, 200), [("row1.value", 190, 10, 260, 30)])), \
        "检查器失效：越界文本没有被拦住"
    good = [("row1.stat.low", 100, 100, 200, 120),
            ("row1.stat.high", 216, 100, 300, 120)]
    assert not check_boxes((0, 0, 800, 1000), good), \
        "检查器误报：正常间隙被判违规"

    # 2) 真实反例：拿真实链路渲染出的 boxes，把中段按旧版固定字号比例放大，
    #    复现「均值X : 高/低X」压到低值/高值上的重叠，自检必须报错。
    boxes, bounds = render_case(cases[0][1])
    injected = []
    for label, x0, y0, x1, y1 in boxes:
        if label.endswith(".stat.mid"):
            w, cx = x1 - x0, (x0 + x1) / 2.0
            nw = w * 1.9          # 旧版 120 设计px vs 可用约 92 设计px
            x0, x1 = cx - nw / 2.0, cx + nw / 2.0
        injected.append((label, x0, y0, x1, y1))
    probs = check_all(bounds, injected, cases[0][1])
    if not any("[重叠]" in p or "[间距]" in p for p in probs):
        raise AssertionError(
            "反例失败：把 2026-11-04 真实犯过的百分比重叠放回去，自检却没有报错")
    assert not check_all(bounds, boxes, cases[0][1]), \
        "反例失败：修好后的真实渲染仍被判违规"

    # 3) 2026-11-04 真实缺陷反例：把推荐区块整个删掉，模板必须直接 raise，
    #    不允许「静默少画几行」——这正是用户截图里发生的事。
    no_recs = dict(cases[0][1])
    no_recs.pop("recs", None)
    try:
        render_case(no_recs)
    except Exception as e:  # noqa: BLE001 — 渲染层会包装成 PicRenderError
        assert "recs" in str(e), \
            "反例失败：删掉 recs 后报错信息没指出该改什么：%s" % e
    else:
        raise AssertionError(
            "反例失败：删掉 recs 后模板仍照常出图，整块推荐词条会再次消失")

    # 4) 失败态写「暂无」冒充成功（旧版行为），check_recs 必须拦住。
    case_map = dict(cases)
    err_case = case_map["market_error"]
    eboxes, ebounds = render_case(err_case)
    assert not check_all(ebounds, eboxes, err_case), \
        "反例失败：正常失败态渲染被判违规"
    tampered = dict(err_case)
    tampered["recs"] = dict(err_case["recs"], market="WM 拍卖样本不足，暂无")
    assert any("[状态]" in p for p in check_recs(eboxes, tampered)), \
        "反例失败：失败态写「暂无」冒充成功，门禁没拦住"

    # 5) 推荐行与标签列重叠（把排版红线错误放回去），check_boxes 必须报错。
    injected2 = []
    for label, bx0, by0, bx1, by1 in eboxes:
        if label == "recs.pos.line1":
            bx0 -= int((bx1 - bx0) * 0.5) + 40
        injected2.append((label, bx0, by0, bx1, by1))
    assert any("[重叠]" in p for p in check_boxes(ebounds, injected2)), \
        "反例失败：推荐行与标签重叠，门禁没拦住"

    # 6) 2026-11-04 23:0x 口径反例：把「非成交 / 不计价 / 中位数 / 同等级」
    #    标注删掉，等于又拿最贵挂单当成交价，check_recs 必须拦住。
    ok_case = case_map["std_4rows"]
    oboxes, obounds = render_case(ok_case)
    assert not check_all(obounds, oboxes, ok_case), \
        "反例失败：正常成功态渲染被判违规"
    bad_variants = (
        dict(ok_case["recs"], source="WM 成交均价 · glaxion · 2026-11-04 23:05"),
        dict(ok_case["recs"], neg="变焦 +7898p/+3949%（高价挂牌 79 条算术均值）"),
        dict(ok_case["recs"], pos="多重、暴伤、毒伤（高价挂牌频次计价）"),
        dict(ok_case["recs"], market="WM 低价基线 200P（样本 200 / 2026-11-04 23:05）"),
        dict(ok_case["recs"], exact=""),
        # 23:25 真实犯过的错：退回「全量负词条池」的不对称对比（+700% 伪溢价），
        # 卡面丢了「同分位」标注，门禁必须拦住。
        dict(ok_case["recs"],
             neg="变焦 +700.0p/+700%（同负词条普通挂牌 84 条·中位数）"),
        dict(ok_case["recs"],
             source=("WM 挂牌要价·非成交价｜同等级 rank8｜匹配：正负面相同｜样本 200｜未洗 3"
                     " · glaxion · 2026-11-04 23:05")),
    )
    for i, bad_recs in enumerate(bad_variants):
        t = dict(ok_case)
        t["recs"] = bad_recs
        assert any("[口径]" in p for p in check_recs(oboxes, t)), \
            "反例失败：口径变体 %d 没被拦住（旧版 +3949% 会复发）" % i

    # 7) 漏画「类似紫卡」行（把包围盒删掉），接入门禁必须报错。
    no_exact = [b for b in oboxes if not b[0].startswith("recs.exact")]
    assert any("[接入]" in p and "exact" in p
               for p in check_recs(no_exact, ok_case)), \
        "反例失败：漏画「类似紫卡」行没被拦住"

    # 8) 2026-11-04 23:3x 真实缺陷：武器名小标压到剪影框底线外。
    #    把标签下移 20px 放回去，门禁必须报「溢出」；删掉登记必须报「接入」。
    assert not check_weapon(oboxes), \
        "反例失败：修复后的武器名小标仍被判越界"
    pushed = [(lab, x0, y0 + 20 if lab == "weapon.label" else y0,
               x1, y1 + 20 if lab == "weapon.label" else y1)
              for lab, x0, y0, x1, y1 in oboxes]
    assert any("[溢出]" in p for p in check_weapon(pushed)), \
        "反例失败：把武器名小标压到框外，门禁没拦住"
    unregistered = [b for b in oboxes if b[0] != "weapon.label"]
    assert any("[接入]" in p for p in check_weapon(unregistered)), \
        "反例失败：武器名小标没登记包围盒，门禁没拦住"
    print("SELFTEST OK：重叠/越界/删 recs/失败态写暂无/推荐行重叠/口径标注缺失/"
          "漏画类似紫卡行/丢同分位标注/武器名压框均被拦住；改回来通过")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="跑全量用例门禁")
    ap.add_argument("--selftest", action="store_true", help="只跑反例验证")
    ap.add_argument("--out", default="", help="把第一张真实链路卡存成 PNG")
    args = ap.parse_args()
    if not (args.check or args.selftest):
        args.check = True

    cases = build_cases()
    selftest(cases)          # 每次都跑：从来没报过错的检查等于没加
    if not args.check:
        return 0

    failed, total = 0, 0
    for name, data in cases:
        boxes, bounds = render_case(
            data, png_out=args.out if args.out and total == 0 else None)
        problems = check_all(bounds, boxes, data)
        total += 1
        if problems:
            failed += 1
            print("FAIL %-22s boxes=%d bounds=%s" % (name, len(boxes), bounds))
            for p in problems:
                print("     " + p)
        else:
            print("PASS %-22s boxes=%d bounds=%s" % (name, len(boxes), bounds))
    print("SUMMARY: %d cases, %d passed, %d failed" % (total, total - failed, failed))
    if args.out:
        print("PNG: " + args.out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
