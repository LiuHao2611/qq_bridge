# -*- coding: utf-8 -*-
"""硬件占用环形色块图（hw 模板）验收。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe -m pytest tests/test_pic_hw.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.plugins.qq_bridge.pic import list_templates, render_sync, theme  # noqa: E402
from src.plugins.qq_bridge.pic.templates import hw as _hw  # noqa: E402
from src.plugins.qq_bridge.pic.theme import NEON  # noqa: E402

_PNG = b"\x89PNG\r\n\x1a\n"


def _run(coro):
    return asyncio.run(coro)


def _open(data: bytes) -> Image.Image:
    return Image.open(__import__("io").BytesIO(data))


def _color_count(data: bytes, pct: int, tol: int = 45) -> int:
    """统计整图中与 load_color(pct) 同色的像素数（与渲染同口径）。"""
    img = _open(data).convert("RGB")
    hexs = _hw.load_color(pct)
    tgt = (int(hexs[1:3], 16), int(hexs[3:5], 16), int(hexs[5:7], 16))
    n = 0
    for count, px in (img.getcolors(maxcolors=2_000_000) or []):
        if abs(px[0] - tgt[0]) + abs(px[1] - tgt[1]) + abs(px[2] - tgt[2]) <= tol:
            n += count
    return n


SNAP_HIGH = {
    "ts": 1694668800,
    "cpu": {"percent": 95.0, "count_physical": 4, "count_logical": 8},
    "mem": {"used": 1024 ** 3 * 11.8, "total": 1024 ** 3 * 15.9, "percent": 95.0},
    "disk": {"path": "D:", "used": 1024 ** 3 * 397.7, "total": 1024 ** 3 * 476.9, "percent": 95.0},
    "gpus": [{"index": 0, "name": "NVIDIA GeForce GTX 1660 Ti",
              "utilization": 95.0, "mem_used": 1024 ** 3 * 5, "mem_total": 1024 ** 3 * 6,
              "temperature": 80.0, "power_draw": 110.0, "power_limit": 120.0}],
}
SNAP_LOW = {
    "ts": 1694668800,
    "cpu": {"percent": 8.0, "count_physical": 4, "count_logical": 8},
    "mem": {"used": 1024 ** 3 * 2.0, "total": 1024 ** 3 * 15.9, "percent": 12.0},
    "disk": {"path": "D:", "used": 1024 ** 3 * 50.0, "total": 1024 ** 3 * 476.9, "percent": 10.0},
    "gpus": [{"index": 0, "name": "NVIDIA GeForce GTX 1660 Ti",
              "utilization": 3.0, "mem_used": 1024 ** 2 * 500, "mem_total": 1024 ** 3 * 6,
              "temperature": 45.0, "power_draw": 12.0, "power_limit": 120.0}],
}


def test_hw_listed():
    assert "hw" in set(list_templates())


def test_load_color_gradient():
    """低占用偏绿（G>R），高占用偏红（R>G）——一眼可辨。"""
    g = _hw.load_color(5)
    r = _hw.load_color(95)
    rg = [int(g[i:i + 2], 16) for i in (1, 3, 5)]
    rr = [int(r[i:i + 2], 16) for i in (1, 3, 5)]
    assert rg[1] > rg[0], "低占用应为绿色（G>R）"
    assert rr[0] > rr[1], "高占用应为红色（R>G）"


def test_collect_extracts_percents():
    items = _hw._collect(SNAP_HIGH)
    labels = [it[0] for it in items]
    assert "CPU" in labels and "内存" in labels and "磁盘 D:" in labels
    assert any(it[0].startswith("GPU") for it in items)
    # 各指标 percent 与快照一致
    by = {it[0]: it[2] for it in items}
    assert by["CPU"] == 95 and by["内存"] == 95


def test_render_hw_png():
    t = theme.get(NEON)
    data = render_sync("hw", NEON, snap=SNAP_HIGH)
    assert data[:8] == _PNG
    img = _open(data)
    assert img.height > 200  # 含标题 + 4 行指标 + 页脚


def test_high_occupancy_shows_more_red():
    """高占用整图红色像素应明显多于低占用整图（环形色块直观区分）。"""
    high = render_sync("hw", NEON, snap=SNAP_HIGH)
    low = render_sync("hw", NEON, snap=SNAP_LOW)
    red_high = _color_count(high, 100)
    red_low = _color_count(low, 100)
    assert red_high > red_low * 3, "高占用图应有显著更多的红色环形像素（%d vs %d）" % (red_high, red_low)
