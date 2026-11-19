# -*- coding: utf-8 -*-
"""今日行情卡（``hw_market`` 的 daily 视图）渲染后自检。

从 ``check_hw_market_layout.py`` 按功能拆出（守 20KB 门禁）：主门禁只管月度两视图，
本模块用**合成 payload**（不依赖本地快照文件，clone 下来就能跑）验证今日卡两种形态：
  * 首日基线：面板 + 「今日在售」价格表 + 基线注记；
  * 有对比：面板 + 涨幅榜 / 跌幅榜。

检查项：盒越界/重叠/同房间距/必备盒，以及口径（必须写 ZOL、每日、仅供参考；
禁止「实时」这类把参考价说成实时成交价的词）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src" / "plugins"))

from qq_bridge.pic import render_sync, resolve_theme  # noqa: E402

MIN_GAP = 8
MUST_SAY = ("ZOL", "每日", "仅供参考")
FORBID = ("实时", "秒杀", "成交价")

_BUCKET = {"key": "gpu", "name": "显卡", "tracked": 12, "compared": 12,
           "up": 4, "down": 4, "flat": 4}


def baseline_payload() -> dict:
    return {
        "view": "daily", "date": "2026-11-19", "fetched_at": "10:10", "prev_date": "",
        "buckets": [dict(_BUCKET, compared=0, up=0, down=0, flat=0)],
        "gainers": [], "losers": [], "gpu_n": 12, "cpu_n": 0, "missing": 0,
        "samples": [
            {"label": "RTX 5090 D", "price": 16499, "samples": 10},
            {"label": "RTX 5080", "price": 8299, "samples": 23},
            {"label": "RTX 5070 Ti", "price": 6599, "samples": 22},
            {"label": "RX 9070 XT", "price": 4999, "samples": 6},
            {"label": "RTX 5070", "price": 4599, "samples": 15},
            {"label": "RTX 5060", "price": 2499, "samples": 17},
        ],
        "note": "CPU / 内存存储：ZOL 公开列表无稳定日价，发「硬件行情 月报」看月度档案",
        "at": "10:10",
    }


def compare_payload() -> dict:
    def mv(label, prev, now):
        return {"key": "gpu:" + label, "label": label, "prev": prev, "latest": now,
                "pct": (now - prev) / prev * 100, "unit": "元"}

    return {
        "view": "daily", "date": "2026-11-19", "fetched_at": "10:10",
        "prev_date": "2026-11-18", "buckets": [dict(_BUCKET)],
        "gainers": [mv("RTX 5060", 2449, 2499), mv("RTX 5070", 4507, 4599),
                    mv("RX 9070 XT", 4899, 4999)],
        "losers": [mv("RTX 5080", 8547, 8299), mv("RX 9070", 4427, 4299),
                   mv("RTX 5060 Ti 8G", 3294, 3199)],
        "gpu_n": 12, "cpu_n": 0, "missing": 0, "samples": [],
        "note": "CPU / 内存存储：ZOL 公开列表无稳定日价，发「硬件行情 月报」看月度档案",
        "at": "10:10",
    }


def _boxes(payload, theme_name="light"):
    box: list = []
    img = render_sync("hw_market", theme_name, payload=payload, boxes=box)
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(img)) as im:
        return box, im.size[1]


def _contains(a, b) -> bool:
    return a[1] <= b[1] and a[2] <= b[2] and a[3] >= b[3] and a[4] >= b[4]


def _overlap(a, b) -> bool:
    return min(a[3], b[3]) > max(a[1], b[1]) and min(a[4], b[4]) > max(a[2], b[2])


def box_problems(boxes, width, height, pad=24) -> list[str]:
    out, seen = [], set()
    for k, x0, y0, x1, y1 in boxes:
        if k in seen:
            out.append("重复 label：%s" % k)
        seen.add(k)
        if x1 <= x0 or y1 <= y0:
            out.append("空盒：%s" % k)
        if x0 < pad or y0 < pad or x1 > width - pad or y1 > height - pad:
            out.append("越界：%s %r" % (k, (x0, y0, x1, y1)))
    for i in range(len(boxes)):
        a = boxes[i]
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            if _overlap(a, b) and not _contains(a, b) and not _contains(b, a):
                out.append("重叠：%s × %s" % (a[0], b[0]))
    return out


def gap_problems(boxes, pairs) -> list[str]:
    d = {b[0]: b for b in boxes}
    out = []
    for left, right in pairs:
        if left not in d or right not in d:
            out.append("缺盒：%s/%s" % (left, right))
            continue
        gap = d[right][1] - d[left][3]
        if gap < MIN_GAP:
            out.append("间距不足：%s→%s 仅 %dpx" % (left, right, gap))
    return out


def _texts(payload, theme_name="light"):
    from qq_bridge.pic.canvas import Canvas

    got: list[str] = []
    orig = Canvas.text

    def spy(self, pos, text, *a, **kw):
        got.append(str(text))
        return orig(self, pos, text, *a, **kw)

    Canvas.text = spy
    try:
        render_sync("hw_market", theme_name, payload=payload)
    finally:
        Canvas.text = orig
    return got


def wording_problems(texts: list[str]) -> list[str]:
    joined = "\n".join(texts)
    out = ["口径缺词：今日卡必须出现「%s」" % w for w in MUST_SAY if w not in joined]
    out += ["口径违规：今日卡出现「%s」" % w for w in FORBID if w in joined]
    return out


def check() -> list[str]:
    problems = []
    cases = (("baseline", baseline_payload(), {"hdr.title", "stat.0", "note", "footer",
                                               "sec.list.title", "list.0.label",
                                               "list.0.price"}),
             ("compare", compare_payload(), {"hdr.title", "stat.0", "gain.0.label",
                                             "sec.gain.title", "sec.lose.title",
                                             "lose.2.price", "note", "footer"}))
    for name, payload, required in cases:
        for theme_name in ("light", "neon"):
            boxes, height = _boxes(payload, theme_name)
            tag = "%s/%s" % (name, theme_name)
            problems += ["%s %s" % (tag, p) for p in box_problems(boxes, 800, height)]
            have = {b[0] for b in boxes}
            problems += ["%s 缺必备盒：%s" % (tag, k) for k in sorted(required - have)]
            rows = sum(1 for b in boxes if b[0].endswith(".label") and
                       (b[0].startswith("gain.") or b[0].startswith("lose.")
                        or b[0].startswith("list.")))
            expect = (len(payload.get("samples") or []) if name == "baseline"
                      else len(payload["gainers"]) + len(payload["losers"]))
            if rows != expect:
                problems.append("%s 行数不符：盒 %d ≠ 数据 %d" % (tag, rows, expect))
            problems += ["%s %s" % (tag, p) for p in wording_problems(_texts(payload, theme_name))]
    return problems


def main() -> int:
    problems = check()
    if problems:
        for p in problems:
            print("PROBLEM", p)
        print("FAIL：%d 项" % len(problems))
        return 1
    print("PASS：今日卡 baseline/compare × light/neon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
