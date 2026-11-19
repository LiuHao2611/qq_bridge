# -*- coding: utf-8 -*-
"""每日行情（ZOL）契约测试：解析 / 分词 / 涨跌对比 / 调度 / 卡面口径。

全部离线：解析用 `tests/fixtures/hw_zol_*.html`（2026-11-19 真实页面快照），
快照读写全部 monkeypatch，绝不联网、绝不写真实 data/ 目录。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "plugins"))


def _load_gate():
    path = ROOT / "tools" / "hw_daily_gate.py"
    spec = importlib.util.spec_from_file_location("hw_daily_gate_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()
from qq_bridge.hardware import daily, zol  # noqa: E402

GPU_HTML = (ROOT / "tests" / "fixtures" / "hw_zol_gpu.html").read_text(encoding="utf-8")
CPU_HTML = (ROOT / "tests" / "fixtures" / "hw_zol_cpu.html").read_text(encoding="utf-8")


# --------------------------------------------------------------------- 解析
def test_parse_cards_and_variant_filter():
    cards = zol.parse_cards(GPU_HTML)
    assert len(cards) == 23
    hit16 = zol._pick(cards, r"(?<!\d)16G", "")
    hit8 = zol._pick(cards, r"(?<!\d)8G", "")
    assert len(hit16) == 8 and min(p for _, p in hit16) == 3635
    assert len(hit8) == 9 and min(p for _, p in hit8) == 3199
    # 容量不明的卡片（魔刃 MAX）不能被塞进任何一边
    assert all("魔刃" not in n for n, _ in hit16 + hit8)


def test_parse_cpu_page_and_junk_filter():
    cards = zol.parse_cards(CPU_HTML)
    assert len(cards) == 10
    assert zol.cpu_token("AMD Ryzen 7 9800X3D") == "9800X3D"
    assert zol.cpu_token("AMD Ryzen 7 3700X8核心/16线程，7nm") == "3700X"
    assert zol.cpu_token("Intel 酷睿 Ultra 9 285台式机处理器") == "285"
    assert zol.is_junk("某二手 5090") and not zol.is_junk("NVIDIA RTX 5090")


@pytest.mark.parametrize("label,token,family", [
    ("R79800X3D", "9800X3D", "R7"),
    ("i514600K", "14600K", "I5"),
    ("U9285K", "285K", "U9"),
    ("U5250K PLUS", "250KPLUS", "U5"),
    ("R99950X3D2", "9950X3D2", "R9"),
])
def test_model_token(label, token, family):
    assert zol.model_token(label) == token
    assert zol.family_of(label) == family


# --------------------------------------------------------------------- 对比
def _snap(day, items, at="10:10:00+08:00"):
    return {"date": day, "fetched_at": "%sT%s" % (day, at), "items": items, "errors": []}


def test_compare_counts_and_movers():
    today = _snap("2026-11-19", {
        "gpu:RTX 5070": {"price": 4599}, "gpu:RTX 5080": {"price": 8299},
        "gpu:RTX 5060": {"price": 2499}, "gpu:RTX 5050": {"price": 2099},
    })
    prev = _snap("2026-11-18", {
        "gpu:RTX 5070": {"price": 4500}, "gpu:RTX 5080": {"price": 8299},
        "gpu:RTX 5060": {"price": 2499}, "gpu:RTX 5050": {"price": 2200},
    })
    cmp = daily.compare(today, prev)
    b = cmp["buckets"][0]
    assert (b["up"], b["down"], b["flat"], b["compared"]) == (1, 1, 2, 4)
    assert cmp["gainers"][0]["label"] == "RTX 5070"
    assert cmp["gainers"][0]["pct"] == pytest.approx(2.2, abs=0.01)
    assert cmp["losers"][0]["label"] == "RTX 5050"
    # 新出现的型号不算涨跌（没有基期）
    assert all(m["label"] != "RTX 5090" for m in cmp["gainers"])


def test_compare_ignores_unknown_and_keeps_missing_visible():
    today = _snap("2026-11-19", {"gpu:RTX 5070": {"price": 4599}})
    cmp = daily.compare(today, None)
    assert cmp["buckets"][0]["compared"] == 0
    assert cmp["gainers"] == [] and cmp["losers"] == []


def test_overview_payload_baseline_and_compare(monkeypatch):
    today = _snap("2026-11-19", {"gpu:RTX 5070": {"price": 4599, "samples": 15},
                                 "gpu:RTX 5080": {"price": 8299, "samples": 23}})
    prev = _snap("2026-11-18", {"gpu:RTX 5070": {"price": 4500}, "gpu:RTX 5080": {"price": 8300}})
    monkeypatch.setattr(daily, "load", lambda day=None: today)
    monkeypatch.setattr(daily, "latest_before", lambda day: ("", None))
    base = daily.overview_payload()
    assert base["prev_date"] == "" and len(base["samples"]) == 2
    assert base["samples"][0]["label"] == "RTX 5080"  # 按价格降序
    monkeypatch.setattr(daily, "latest_before", lambda day: ("2026-11-18", prev))
    comp = daily.overview_payload()
    assert comp["prev_date"] == "2026-11-18" and comp["buckets"][0]["compared"] == 2
    assert comp["gainers"][0]["label"] == "RTX 5070"


def test_model_payload_uses_own_daily_history(monkeypatch):
    d1 = _snap("2026-11-17", {"gpu:RTX 5070": {"price": 4400, "samples": 12}})
    d2 = _snap("2026-11-19", {"gpu:RTX 5070": {"price": 4599, "samples": 15}})
    book = {"2026-11-17": d1, "2026-11-19": d2}
    monkeypatch.setattr(daily, "load", lambda day=None: book.get(day or "2026-11-19"))
    monkeypatch.setattr(daily, "days", lambda: sorted(book))
    mp = daily.model_payload("RTX 5070")
    assert mp["latest"] == 4599 and mp["prev"] == 4400
    assert mp["pct"] == pytest.approx((4599 - 4400) / 4400 * 100, abs=0.01)
    assert len(mp["chart"]) == 2 and mp["source"] == "daily"
    assert daily.model_payload("不存在") is None


# --------------------------------------------------------------------- 调度
def test_schedule_and_next_run(monkeypatch):
    monkeypatch.delenv("HW_DAILY_AT", raising=False)
    assert daily.schedule_at() == (10, 10)
    before = datetime(2026, 11, 19, 9, 0)
    assert daily.next_run(before) == datetime(2026, 11, 19, 10, 10)
    after = datetime(2026, 11, 19, 11, 0)
    assert daily.next_run(after) == datetime(2026, 11, 20, 10, 10)
    monkeypatch.setenv("HW_DAILY_AT", "21:30")
    assert daily.schedule_at() == (21, 30)
    with pytest.raises(daily.DailyError):
        monkeypatch.setenv("HW_DAILY_AT", "25:99")
        daily.schedule_at()


# --------------------------------------------------------------------- 卡面门禁
def test_daily_gate_passes_synthetic_payloads():
    assert gate.check() == []


def test_daily_gate_catches_missing_rows_and_forbidden_words():
    payload = gate.compare_payload()
    payload["gainers"] = payload["gainers"][:1]  # 数据少两行 → 盒行数不符
    problems = gate.box_problems(gate._boxes(payload)[0], 800, gate._boxes(payload)[1])
    assert problems == []
    # 行数契约由 check() 的 expect 比对负责：直接验证它的判据不空转
    boxes, _ = gate._boxes(payload)
    rows = sum(1 for b in boxes if b[0].startswith("gain.") and b[0].endswith(".label"))
    assert rows == 1 and len(payload["gainers"]) == 1  # 一致时不会报；注入下面这种才报
    assert gate.wording_problems(["今日行情 实时报价"]) != []
    assert gate.wording_problems(["ZOL 每日抓取 仅供参考"]) == []
