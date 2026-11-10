# -*- coding: utf-8 -*-
"""M5 预算单元测试（仅测 budget 包；可裸跑，不依赖 nonebot）。"""
import sys
sys.path.insert(0, "src/plugins")

from qq_bridge.budget import BudgetError, BudgetManager, BudgetStore


def _tmp_manager(tmp_path, **overrides):
    mgr = BudgetManager(store=BudgetStore(db_path=str(tmp_path / "b.db")))
    return mgr


def test_store_record_and_get(tmp_path):
    st = BudgetStore(db_path=str(tmp_path / "b.db"))
    d = "2026-10-21"
    st.record("g:1", day=d, calls=3, est_tokens=2400)
    st.record("g:1", day=d, calls=2)  # 再记 2 次
    snap = st.get_scope_day("g:1", d)
    assert snap["scope_calls"] == 5
    assert snap["scope_tokens"] == 2400  # 第二次没传 token，不累加
    assert snap["global_calls"] == 5


def test_store_global_aggregates(tmp_path):
    st = BudgetStore(db_path=str(tmp_path / "b.db"))
    d = "2026-10-21"
    st.record("g:1", day=d, calls=2)
    st.record("g:2", day=d, calls=3)
    snap = st.get_scope_day("g:1", d)
    assert snap["global_calls"] == 5


def test_store_reset_scope(tmp_path):
    st = BudgetStore(db_path=str(tmp_path / "b.db"))
    st.record("g:1", day="2026-10-21", calls=5)
    st.reset_scope("g:1")
    assert st.get_scope_day("g:1", "2026-10-21")["scope_calls"] == 0


class _Cfg:
    BUDGET_ENABLE = True
    BUDGET_SHADOW = False
    BUDGET_GLOBAL_DAILY_CALLS = 10
    BUDGET_PER_SCOPE_DAILY_CALLS = 5
    BUDGET_WARN_RATIO = 0.8
    BUDGET_EST_TOKEN_PER_CALL = 800


def _mgr_with_cfg(tmp_path, cfg):
    import qq_bridge.budget.manager as bm
    bm.Config = cfg
    mgr = BudgetManager(store=BudgetStore(db_path=str(tmp_path / "b.db")))
    return mgr


def test_check_disabled_when_not_enabled(tmp_path):
    cfg = _Cfg(); cfg.BUDGET_ENABLE = False
    mgr = _mgr_with_cfg(tmp_path, cfg)
    r = mgr.check("g:1", "proactive")
    assert r["ok"] is True and r["reason"] == "disabled"


def test_check_shadow_does_not_gate(tmp_path):
    cfg = _Cfg(); cfg.BUDGET_SHADOW = True
    mgr = _mgr_with_cfg(tmp_path, cfg)
    for _ in range(100):  # 远超上限
        mgr.record("g:1", calls=1)
    r = mgr.check("g:1", "proactive")
    assert r["ok"] is True and r["reason"] == "shadow"


def test_check_proactive_gated_by_scope_cap(tmp_path):
    cfg = _Cfg()  # scope cap=5, global=10
    mgr = _mgr_with_cfg(tmp_path, cfg)
    for _ in range(5):
        mgr.record("g:1", calls=1)
    r = mgr.check("g:1", "proactive")
    assert r["ok"] is False and r["reason"] == "scope_exhausted"


def test_check_proactive_gated_by_global_cap(tmp_path):
    cfg = _Cfg()  # scope cap=5（大），global=10
    cfg.BUDGET_PER_SCOPE_DAILY_CALLS = 100
    mgr = _mgr_with_cfg(tmp_path, cfg)
    for _ in range(10):
        mgr.record("g:1", calls=1)
    r = mgr.check("g:1", "proactive")
    assert r["ok"] is False and r["reason"] == "global_exhausted"


def test_check_reactive_always_ok_but_exhausted_flag(tmp_path):
    cfg = _Cfg()
    mgr = _mgr_with_cfg(tmp_path, cfg)
    for _ in range(10):  # 全局耗尽
        mgr.record("g:1", calls=1)
    r = mgr.check("g:1", "reactive")
    assert r["ok"] is True          # reactive 永远放行
    assert r["reason"] == "exhausted"  # 但透传 exhausted 让 caller 附诚实提示


def test_check_warn_level(tmp_path):
    cfg = _Cfg()  # scope cap=5 → 4 次达 0.8 warn
    mgr = _mgr_with_cfg(tmp_path, cfg)
    for _ in range(4):
        mgr.record("g:1", calls=1)
    snap = mgr.snapshot("g:1")
    assert snap["scope_level"] == "warn"
    r = mgr.check("g:1", "proactive")  # warn 仍可放行
    assert r["ok"] is True and r["reason"] == "warn"


def test_status_text_disabled(tmp_path):
    cfg = _Cfg(); cfg.BUDGET_ENABLE = False
    mgr = _mgr_with_cfg(tmp_path, cfg)
    assert "未启用" in mgr.status_text("g:1")
