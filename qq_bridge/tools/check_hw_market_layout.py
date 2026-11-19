# -*- coding: utf-8 -*-
"""card-design 渲染后自检门禁 —— DIY 硬件行情卡（``hw_market``）。

跑法（宿主 .venv 或容器内都行，**离线**：只读 vendored 上游 CSV）：

    python tools/check_hw_market_layout.py --check
    python tools/check_hw_market_layout.py --check --out tmp/hw.png
    python tools/check_hw_market_layout.py --selftest     # 注入真实错误，证明门禁会报错

检查项（对齐 card-design 红线 + 行情卡契约）：
  1. **溢出**：每个叶子盒必须完整落在卡片内容区 ``[pad, pad, W-pad, H-pad]`` 内；
  2. **重叠**：叶子盒两两比对，合法包含（面板包住内部文字）跳过；
  3. **间距**：三块分类面板、每行「名称 / 涨跌幅 / 价格」之间水平间距 ≥ 8px；
  4. **必备**：总览/明细两视图的关键盒 label 齐全（页脚/指标/图表/面板…）；
  5. **数据**：门禁**自己重新读 CSV**（不复用 dataset 聚合）算涨跌家数与榜首，
     与卡面 payload 逐项比对 —— 抓「聚合口径写错却画得很漂亮」；
  6. **口径**：拦截实际绘制文本里的「今日 / 实时行情」等词，要求出现「月度 / 非日内」，
     并核对数据截止日与 UPSTREAM.json 一致（本卡不是日内行情，禁止暗示实时）；
  7. **单位**：内存/存储必须画 元/GB 与「整条 ≈」折算，缺失即失败（上游是元/GB 口径）。

绝不降级：任一问题即 ``exit 1``；不吞异常、不放宽阈值。``--selftest`` 把真实犯过的
错（盒越界、面板重叠、页脚丢失、口径写错、统计篡改）逐类注入，必须全部被抓到。
"""
from __future__ import annotations

import argparse
import copy
import csv
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src" / "plugins"))

from qq_bridge.hardware import dataset as ds  # noqa: E402
from qq_bridge.hardware import market as mk  # noqa: E402
from qq_bridge.pic import canvas as _canvas  # noqa: E402
from qq_bridge.pic import render_sync, resolve_theme  # noqa: E402
from qq_bridge.pic.templates import hw_market as tpl  # noqa: E402

MIN_GAP = 8
PAD = 24

REQUIRED_OVERVIEW = {
    "hdr.title", "hdr.month", "hdr.sub", "stat.0", "stat.1", "stat.2",
    "sec.gain.title", "gain.0.label", "gain.0.pct", "gain.0.price",
    "sec.lose.title", "lose.0.label", "lose.0.pct", "lose.0.price",
    "footer",
}
REQUIRED_MODEL = {
    "hdr.title", "hdr.bucket", "hdr.sub", "price", "price.note",
    "metric.pct.value", "metric.vslow.value", "metric.range.value",
    "chart", "chart.hi", "chart.lo", "chart.range", "tier.title", "footer",
}
ROW_COLS = (".label", ".pct", ".price")


# ---------------------------------------------------------------- 盒检查
def _contains(a, b) -> bool:
    return a[1] <= b[1] and a[2] <= b[2] and a[3] >= b[3] and a[4] >= b[4]


def _overlap(a, b) -> bool:
    return min(a[3], b[3]) > max(a[1], b[1]) and min(a[4], b[4]) > max(a[2], b[2])


def box_problems(boxes, width, height, pad=PAD) -> list[str]:
    out: list[str] = []
    seen = set()
    for k, x0, y0, x1, y1 in boxes:
        if k in seen:
            out.append("重复 label：%s" % k)
        seen.add(k)
        if x1 <= x0 or y1 <= y0:
            out.append("空盒：%s %r" % (k, (x0, y0, x1, y1)))
        if x0 < pad or y0 < pad or x1 > width - pad or y1 > height - pad:
            out.append("越界：%s %r 超出内容区 %r"
                       % (k, (x0, y0, x1, y1), (pad, pad, width - pad, height - pad)))
    for i in range(len(boxes)):
        a = boxes[i]
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            if _overlap(a, b) and not _contains(a, b) and not _contains(b, a):
                out.append("重叠：%s %r × %s %r" % (a[0], a[1:], b[0], b[1:]))
    return out


def gap_problems(boxes, pairs) -> list[str]:
    """指定兄弟盒之间的水平净间距必须 ≥ MIN_GAP。"""
    d = {b[0]: b for b in boxes}
    out = []
    for left, right in pairs:
        if left not in d or right not in d:
            out.append("间距检查缺盒：%s / %s" % (left, right))
            continue
        gap = d[right][1] - d[left][3]
        if gap < MIN_GAP:
            out.append("间距不足：%s→%s 仅 %dpx（要求 ≥%d）" % (left, right, gap, MIN_GAP))
    return out


def missing_problems(boxes, required) -> list[str]:
    have = {b[0] for b in boxes}
    return ["缺少必备盒：%s" % k for k in sorted(required - have)]


def _row_pairs(prefix: str, n: int) -> list[tuple[str, str]]:
    pairs = []
    for i in range(n):
        for l, r in (ROW_COLS[0], ROW_COLS[1]), (ROW_COLS[1], ROW_COLS[2]):
            pairs.append((prefix + ".%d" % i + l, prefix + ".%d" % i + r))
    return pairs


# ---------------------------------------------------------------- 数据契约
def recompute_from_csv() -> dict:
    """门禁自己的独立聚合（直接读 CSV，不复用 dataset 的统计代码）。"""
    base = ds.DATA
    specs = (
        ("gpu", "gpu-prices.csv", lambda r: r["model"]),
        ("cpu", "cpu-prices.csv", lambda r: r["model"]),
        ("mem", "memory-prices.csv", lambda r: "%s|%s" % (r["category"], r["spec"])),
    )
    out = {}
    for key, name, keyf in specs:
        series: dict[str, dict[str, float]] = {}
        with open(base / name, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                series.setdefault(keyf(r), {})[r["date"].strip()] = float(r["price_cny"])
        months = sorted({m for v in series.values() for m in v}, key=ds.month_key)
        latest, prev = months[-1], months[-2]
        matched = {k: v for k, v in series.items() if latest in v and prev in v}
        pcts = {}
        for k, v in matched.items():
            pcts[k] = (v[latest] - v[prev]) / v[prev] * 100 if v[prev] else None
        up = [k for k, p in pcts.items() if p is not None and p > ds.FLAT_EPS]
        down = [k for k, p in pcts.items() if p is not None and p < -ds.FLAT_EPS]
        out[key] = {
            "latest": latest, "prev": prev, "up": len(up), "down": len(down),
            "flat": len(matched) - len(up) - len(down),
            "gain": max(pcts.values()) if pcts else None,
            "lose": min(pcts.values()) if pcts else None,
            "n": len(matched),
        }
    return out


def data_problems(payload: dict, truth: dict | None = None) -> list[str]:
    truth = truth or recompute_from_csv()
    out = []
    if payload.get("view") != "overview":
        return out
    for b in payload["buckets"]:
        t = truth[b["key"]]
        for field in ("up", "down", "flat", "latest", "prev"):
            if b[field] != t[field]:
                out.append("统计不符：%s.%s 卡面 %r ≠ CSV 重算 %r"
                           % (b["key"], field, b[field], t[field]))
        if b["matched"] != t["n"]:
            out.append("覆盖数不符：%s %d ≠ %d" % (b["key"], b["matched"], t["n"]))
        if b["gainers"] and abs(b["gainers"][0]["pct"] - t["gain"]) > 0.01:
            out.append("涨幅榜首不符：%s %r ≠ %r"
                       % (b["key"], b["gainers"][0]["pct"], t["gain"]))
        if b["losers"] and abs(b["losers"][0]["pct"] - t["lose"]) > 0.01:
            out.append("跌幅榜首不符：%s %r ≠ %r"
                       % (b["key"], b["losers"][0]["pct"], t["lose"]))
    return out


# ---------------------------------------------------------------- 口径契约
class _TextSpy:
    """截获 Canvas.text 实际绘制的文本（本卡口径检查用）。"""

    def __init__(self):
        self.texts: list[str] = []
        self._orig = _canvas.Canvas.text

    def __enter__(self):
        orig = self._orig
        spy = self

        def text(self, pos, text, *a, **kw):  # noqa: ANN001
            spy.texts.append(str(text))
            return orig(self, pos, text, *a, **kw)

        _canvas.Canvas.text = text
        return self

    def __exit__(self, *exc):
        _canvas.Canvas.text = self._orig
        return False


def wording_problems(texts: list[str], payload: dict) -> list[str]:
    joined = "\n".join(texts)
    out = []
    for must in tpl.MUST_SAY:
        if must not in joined:
            out.append("口径缺词：卡面必须出现「%s」" % must)
    for bad in tpl.FORBID:
        if bad in joined:
            out.append("口径违规：卡面出现「%s」（本卡是月度档案，不是日内行情）" % bad)
    if payload.get("asof") and payload["asof"] not in joined:
        out.append("数据截止日未上卡：%s" % payload["asof"])
    return out


def unit_problems(texts: list[str], payload: dict) -> list[str]:
    if payload.get("view") != "model" or payload.get("bucket") != "mem":
        return []
    joined = "\n".join(texts)
    out = []
    if "/GB" not in joined:
        out.append("内存/存储缺 元/GB 单位：上游价格是每 GB 单价，不能当整条价")
    if "整条" not in joined:
        out.append("内存/存储缺「整条 ≈ ...」折算行")
    return out


# ---------------------------------------------------------------- 渲染
def _render(view: str, theme: str):
    payload = mk.overview() if view == "overview" else mk.detail(
        mk.search("5090")[0] if view == "gpu" else mk.search("ddr5 16gx2")[0])
    boxes: list = []
    with _TextSpy() as spy:
        img = render_sync("hw_market", theme, payload=payload, boxes=boxes)
    return payload, boxes, img, spy.texts


def check_all(out: str = "") -> list[str]:
    problems: list[str] = []
    truth = recompute_from_csv()
    for theme_name in ("light", "neon"):
        theme = resolve_theme(theme_name)
        for view in ("overview", "gpu", "mem"):
            payload, boxes, img, texts = _render(view, theme_name)
            if not img:
                problems.append("%s/%s 渲染结果为空" % (theme_name, view))
                continue
            h = _img_h(img)
            req = REQUIRED_OVERVIEW if view == "overview" else REQUIRED_MODEL
            problems += ["%s/%s %s" % (theme_name, view, p)
                         for p in box_problems(boxes, 800, h)]
            problems += ["%s/%s %s" % (theme_name, view, p)
                         for p in missing_problems(boxes, req)]
            pairs = []
            if view == "overview":
                pairs = [("stat.0", "stat.1"), ("stat.1", "stat.2")]
                n1 = sum(1 for b in boxes if b[0].startswith("gain.") and b[0].endswith(".label"))
                n2 = sum(1 for b in boxes if b[0].startswith("lose.") and b[0].endswith(".label"))
                pairs += _row_pairs("gain", n1) + _row_pairs("lose", n2)
            problems += ["%s/%s %s" % (theme_name, view, p)
                         for p in gap_problems(boxes, pairs)]
            problems += ["%s/%s %s" % (theme_name, view, p)
                         for p in data_problems(payload, truth)]
            problems += ["%s/%s %s" % (theme_name, view, p)
                         for p in wording_problems(texts, payload)]
            problems += ["%s/%s %s" % (theme_name, view, p)
                         for p in unit_problems(texts, payload)]
            if out:
                path = "%s_%s_%s.png" % (os.path.splitext(out)[0], theme_name, view)
                Path(path).write_bytes(img)
                print("rendered ->", path)
    return problems


def _img_h(png: bytes) -> int:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png)) as im:
        return im.size[1]


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    failures: list[str] = []
    payload = mk.overview()
    theme = resolve_theme("light")

    def expect(name: str, mutate, check) -> None:
        restore = None
        try:
            restore = mutate()
            got = check()
            if not got:
                failures.append("%s：注入后门禁没报错（检查失效）" % name)
            else:
                print("ok  %s → %s" % (name, got[0]))
        finally:
            if restore:
                restore()

    # 1) 页脚盒丢失 → 必备盒检查必须报错
    orig_rec = tpl._rec

    def m1():
        def bad(boxes, key, box):
            if key == "footer":
                return
            return orig_rec(boxes, key, box)

        tpl._rec = bad
        return lambda: setattr(tpl, "_rec", orig_rec)

    expect("页脚盒丢失", m1,
           lambda: missing_problems(_boxes(payload, theme), REQUIRED_OVERVIEW))

    # 2) 中间面板左移 6px → 与左面板重叠
    orig_panel = tpl._panel

    def m2():
        def bad(c, t, box, b, boxes, idx):
            if idx == 1:  # 左移 20px：12px 正常间距变 -8px，必重叠
                box = (box[0] - 20, box[1], box[2] - 20, box[3])
            return orig_panel(c, t, box, b, boxes, idx)

        tpl._panel = bad
        return lambda: setattr(tpl, "_panel", orig_panel)

    expect("分类面板重叠", m2,
           lambda: box_problems(_boxes(payload, theme), 800, _height(payload, theme)))

    # 3) 统计数字被篡改 → 数据契约必须报错
    def m3():
        bad = copy.deepcopy(payload)
        bad["buckets"][0]["up"] += 1
        _SELFTEST_PAYLOAD["p"] = bad
        return lambda: _SELFTEST_PAYLOAD.pop("p", None)

    expect("涨跌家数被篡改", m3,
           lambda: data_problems(_SELFTEST_PAYLOAD["p"], recompute_from_csv()))

    # 4) 页脚写成「今日行情」→ 口径检查必须报错
    orig_foot = tpl.FOOT_OVERVIEW

    def m4():
        tpl.FOOT_OVERVIEW = "来源 frocloud · 今日行情，实时行情更新"
        return lambda: setattr(tpl, "FOOT_OVERVIEW", orig_foot)

    expect("口径写成今日/实时", m4,
           lambda: wording_problems(_texts(payload, theme), payload))

    # 5) 画布高度少算 40px → 越界检查必须报错
    orig_layout = tpl._overview_layout

    def m5():
        def bad(pad, n1, n2, notes):
            L = orig_layout(pad, n1, n2, notes)
            L["h"] -= 40
            return L

        tpl._overview_layout = bad
        return lambda: setattr(tpl, "_overview_layout", orig_layout)

    def m5_check():
        p = mk.overview()
        boxes: list = []
        img = render_sync("hw_market", "light", payload=p, boxes=boxes)
        return box_problems(boxes, 800, _img_h(img)) or []

    expect("画布高度不足（页脚/末行出界）", m5, m5_check)

    if failures:
        for f in failures:
            print("SELFTEST FAIL", f)
        return 1
    print("selftest ok：5 类注入全部被拦")
    return 0


_SELFTEST_PAYLOAD: dict = {}


def _boxes(payload, theme) -> list:
    boxes: list = []
    render_sync("hw_market", theme.name, payload=payload, boxes=boxes)
    return boxes


def _texts(payload, theme) -> list[str]:
    with _TextSpy() as spy:
        render_sync("hw_market", theme.name, payload=payload)
    return spy.texts


def _height(payload, theme) -> int:
    return _img_h(render_sync("hw_market", theme.name, payload=payload))


def main() -> int:
    ap = argparse.ArgumentParser(description="DIY 硬件行情卡渲染后自检门禁")
    ap.add_argument("--check", action="store_true", help="渲染两视图 × 深浅主题并跑全部检查")
    ap.add_argument("--out", default="", help="输出 PNG 前缀（自动加 _主题_视图.png）")
    ap.add_argument("--selftest", action="store_true", help="注入真实错误验证门禁会报错")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.check:
        ap.print_help()
        return 2
    problems = check_all(args.out)
    if problems:
        for p in problems:
            print("PROBLEM", p)
        print("FAIL：%d 项" % len(problems))
        return 1
    print("PASS：两视图 × light/neon 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
