# -*- coding: utf-8 -*-
"""DIY 硬件行情（hw_market）契约测试。

守的是用户看得见的行为：
  * 卡面上的「几涨几跌」必须等于**直接从上游 CSV 重算**的结果（不是抄 dataset 的输出）；
  * 单型号卡的价格/历史高低/环比与 CSV 对得上；
  * 内存存储是「元/GB」，整条价必须按规格折算（不能把每 GB 单价当整条价）；
  * 两种视图都能出 800 宽 PNG，且门禁 ``check_hw_market_layout.py`` 无问题；
  * 卡面口径必须是「月度档案 / 非日内」，出现「今日 / 实时行情」即失败；
  * 上游副本与搬运清单 sha256 一致（手改 vendored CSV 会被抓）。

故意不测：内部函数名、调用次数、私有常量 —— 换实现就失败的测试没有价值。
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.hardware import build_payload, dataset as ds  # noqa: E402
from qq_bridge.hardware import market as mk  # noqa: E402
from qq_bridge.pic import render_sync  # noqa: E402
from qq_bridge.pic.templates import hw_market as tpl  # noqa: E402


def _load_gate():
    path = ROOT / "tools" / "check_hw_market_layout.py"
    spec = importlib.util.spec_from_file_location("check_hw_market_layout", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


def _csv_series(name: str, keyf):
    series: dict[str, dict[str, float]] = {}
    with open(ds.DATA / name, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            series.setdefault(keyf(r), {})[r["date"].strip()] = float(r["price_cny"])
    return series


def _counts(series) -> tuple[int, int, int]:
    months = sorted({m for v in series.values() for m in v}, key=ds.month_key)
    latest, prev = months[-1], months[-2]
    up = down = flat = 0
    for v in series.values():
        if latest not in v or prev not in v:
            continue
        p = (v[latest] - v[prev]) / v[prev] * 100
        if p > ds.FLAT_EPS:
            up += 1
        elif p < -ds.FLAT_EPS:
            down += 1
        else:
            flat += 1
    return up, down, flat


# --------------------------------------------------------------------- 数据
def test_overview_counts_match_raw_csv():
    """卡面的涨跌家数 = 直接读 CSV 重算（门禁之外的第二条独立路径）。"""
    payload = mk.overview()
    got = {b["key"]: (b["up"], b["down"], b["flat"]) for b in payload["buckets"]}
    want = {
        "gpu": _counts(_csv_series("gpu-prices.csv", lambda r: r["model"])),
        "cpu": _counts(_csv_series("cpu-prices.csv", lambda r: r["model"])),
        "mem": _counts(_csv_series("memory-prices.csv",
                                   lambda r: (r["category"], r["spec"]))),
    }
    assert got == want
    # 2026-10 这期的真实值（上游档案固定，改数据才会变；变了就该有人看一眼）
    assert got["gpu"] == (17, 0, 0)
    assert got["cpu"] == (10, 6, 5)
    assert got["mem"] == (3, 7, 1)


def test_top_mover_is_real_row_from_csv():
    """涨幅榜首 7650GRE 必须真的是 2000→2400（+20%），不能是四舍五入出来的假象。"""
    series = _csv_series("gpu-prices.csv", lambda r: r["model"])
    assert series["7650GRE"]["2026-09"] == 2000.0
    assert series["7650GRE"]["2026-10"] == 2400.0
    payload = mk.overview()
    top = payload["gainers"][0]
    assert top["label"] == "7650GRE"
    assert top["pct"] == pytest.approx(20.0)
    assert (top["prev"], top["latest"]) == (2000.0, 2400.0)


def test_model_detail_history_and_delta():
    """5090：18500（2025-08 低点）→ 47500（2026-10 新高），环比 +18.75%。"""
    det = mk.detail(mk.search("5090")[0])
    assert det["latest"] == 47500.0 and det["latest_month"] == "2026-10"
    assert (det["low"], det["low_month"]) == (18500.0, "2025-08")
    assert (det["high"], det["high_month"]) == (47500.0, "2026-10")
    assert det["pct"] == pytest.approx(18.75, abs=0.01)
    assert det["vs_low_pct"] == pytest.approx((47500 - 18500) / 18500 * 100, abs=0.01)


def test_memory_is_per_gb_and_kit_is_computed():
    """内存单价是 元/GB：32GB 整条 = 每 GB 价 × 32，绝不能直接报成 76.84 元一条。"""
    det = mk.detail(mk.search("ddr5 16gx2")[0])
    assert det["unit"] == "元/GB"
    assert det["latest"] == pytest.approx(76.84, abs=0.001)
    assert det["kit_gb"] == 32
    assert det["kit_price"] == pytest.approx(76.84 * 32, abs=0.01)


def test_empty_bucket_raises_instead_of_fake_zero():
    """数据缺失必须显式报错；不能返回「0 涨 0 跌」冒充「本期没动」。"""
    empty = ds.Dataset(instruments=[], tiers={}, meta={"data_asof": "2026-10-02"})
    with pytest.raises(ds.DatasetError):
        mk.bucket_summary(empty, "gpu")


def test_tier_notes_are_not_dropped():
    """上游备注（如 14600K「缩缸/停产」）必须原样带到卡面数据里，不许吞掉。"""
    det = mk.detail(mk.search("14600k")[0])
    assert det["tier"]["tier"] == "中高端"
    assert "缩缸" in det["tier"]["note"]


# --------------------------------------------------------------------- 检索
@pytest.mark.parametrize("query,label", [
    ("5090", "5090"),
    ("RTX 5090", "5090"),
    ("7800x3d", "R77800X3D"),
    ("i5 14600K", "i514600K"),
    ("ddr5 32g", "DDR5 · 16G×2"),
    ("固态 2T", "固态 PCIE4.0 · 2TB"),
])
def test_search_common_ways(query, label):
    hits = mk.search(query)
    assert hits and hits[0].label == label


def test_empty_arg_returns_daily_card(monkeypatch):
    """不带型号 = 今日卡（最常发的口令）；不再默认月度总览。"""
    from qq_bridge.hardware import daily

    fake = {"view": "daily", "date": "2026-11-19", "buckets": [], "samples": []}
    monkeypatch.setattr(daily, "overview_payload", lambda *a, **k: fake)
    payload, note = build_payload("")
    assert note == "" and payload is fake


def test_month_report_keeps_monthly_overview():
    """「硬件行情 月报」仍然是 frocloud 月度总览（三分类都在）。"""
    payload, note = build_payload("月报")
    assert note == ""
    assert payload["view"] == "overview" and len(payload["buckets"]) == 3


def test_command_pattern_and_arg_extraction():
    """口令接线：@机器人 / # 前缀 / 粘连写法都要能取到型号参数。"""
    import re

    from qq_bridge.hardware import PAT, _arg_of

    assert re.search(PAT, "[CQ:at,qq=10001] 硬件行情 5090", re.I)
    assert re.search(PAT, "#装机行情", re.I)
    assert re.search(PAT, "DIY行情", re.I)  # matcher 注册时带 flags=re.I

    class _E:
        def __init__(self, text):
            self._t = text

        def get_plaintext(self):
            return self._t

    assert _arg_of(_E("#硬件行情 7800x3d")) == "7800x3d"
    assert _arg_of(_E("硬件行情")) == ""


def test_search_ambiguous_and_missing_are_reported():
    payload, note = build_payload("ddr5")
    assert payload is None and "命中 4 个标的" in note
    payload, note = build_payload("这型号不存在")
    assert payload is None and "共跟踪" in note and "档案里没有" in note


# --------------------------------------------------------------------- 出图
def test_render_both_views_and_gate_passes():
    problems = gate.check_all("")
    assert problems == [], "门禁报问题：%s" % problems[:5]
    for payload in (mk.overview(), mk.detail(mk.search("5090")[0])):
        png = render_sync("hw_market", "light", payload=payload)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(png)) as im:
            assert im.width == 800 and 400 < im.height < 2000


def test_card_says_monthly_not_realtime():
    """口径守门：实际绘制的文字里必须有「月度 / 非日内」，且不许出现「今日 / 实时行情」。"""
    for payload in (mk.overview(), mk.detail(mk.search("5090")[0])):
        texts = gate._texts(payload, gate.resolve_theme("light"))
        joined = "\n".join(texts)
        assert "月度" in joined and "非日内" in joined
        for bad in ("今日", "实时行情", "当天行情"):
            assert bad not in joined, "卡面出现误导词：%s" % bad
        assert payload["asof"] in joined


def test_template_refuses_empty_payload():
    """缺 payload 必须报错，不能画一张空白卡冒充成功。"""
    with pytest.raises(Exception):
        tpl.build(gate.resolve_theme("light"), None, 800)


# --------------------------------------------------------------------- 端到端
def _ev(text: str):
    from types import SimpleNamespace

    return SimpleNamespace(message_type="group", group_id=123, user_id=456,
                           channel="napcat", get_plaintext=lambda: text)


def test_command_end_to_end_sends_a_real_image(monkeypatch):
    """发「硬件行情 5090」必须真的走渲染并发图 —— 不是文字、不是静默。"""
    from qq_bridge import media

    import qq_bridge.hardware as hw

    sent: dict = {}

    async def fake_pic(ctx, data, at_user=False):
        sent["bytes"] = len(data)
        sent["group"] = ctx.get("group_id")

    async def fake_text(ctx, text, at_user=False):
        sent["text"] = text

    monkeypatch.setattr(media, "send_pic", fake_pic)
    monkeypatch.setattr(media, "send_text", fake_text)

    async def _no_fetch(arg):
        return None

    monkeypatch.setattr(hw, "_ensure_daily", _no_fetch)
    import asyncio

    asyncio.run(hw._handle_safe(_ev("硬件行情 5070")))
    assert sent.get("bytes", 0) > 5000 and "text" not in sent
    assert sent["group"] == 123


def test_command_end_to_end_ambiguous_asks_instead_of_guessing(monkeypatch):
    """「硬件行情 ddr5」命中多个 → 只回检索说明，不擅自出一张挑好的卡。"""
    from qq_bridge import media

    import qq_bridge.hardware as hw

    sent: dict = {}

    async def fake_pic(ctx, data, at_user=False):
        sent["pic"] = True

    async def fake_text(ctx, text, at_user=False):
        sent["text"] = text

    monkeypatch.setattr(media, "send_pic", fake_pic)
    monkeypatch.setattr(media, "send_text", fake_text)

    async def _no_fetch(arg):
        return None

    monkeypatch.setattr(hw, "_ensure_daily", _no_fetch)
    import asyncio

    asyncio.run(hw._handle_safe(_ev("硬件行情 ddr5")))
    assert "pic" not in sent
    assert "命中 4 个标的" in sent["text"]


def test_official_rule_guard_and_text_extraction():
    """官方通道 rule：能取到官方 text 段，但**非官方事件一律拒绝**（不吞 NapCat/闲聊）。"""
    import qq_bridge.hardware as hw

    class _Seg:
        def __init__(self, t, data):
            self.type, self.data = t, data

    class _Ev:
        def get_message(self):
            return [_Seg("text", {"text": "硬件行情 5090"})]

    assert hw._official_text_of(_Ev()) == "硬件行情 5090"
    assert hw.is_official_command(_Ev()) is False  # adapter 守卫：非官方事件不接
    assert hw.is_official_command(object()) is False


# --------------------------------------------------------------------- 搬运清单
def test_vendor_manifest_matches_files():
    """上游副本必须与清单 sha256 一致（手改 CSV / 多放文件都会被这条抓）。

    按文件路径加载：`tools/` 是命名空间目录，全量跑时 `sys.modules["tools"]` 可能
    已被 qq_bridge 的工具包占用（`from tools.vendor_frocloud import ...` 会 ImportError），
    这里绕开包名冲突，保证单跑/全量跑行为一致。
    """
    spec = importlib.util.spec_from_file_location(
        "vendor_frocloud_standalone", ROOT / "tools" / "vendor_frocloud.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.verify(verbose=False) == []
