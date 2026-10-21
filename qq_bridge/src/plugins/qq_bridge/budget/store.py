# -*- coding: utf-8 -*-
"""budget/store —— AI 调用预算存储（2026-10-21）。

零主包依赖、可裸单测。存储：SQLite 单库 data/qq_budget.db。

按自然日记录每个 scope 的 AI 调用次数与估算 token；全局用量 = 当日所有
scope 之和（查询时聚合，不另存冗余行）。

纪律：CLOSE-02 全路径显式 BudgetError。
"""
from __future__ import annotations

import os
import sqlite3
import time

from ..config import Config, get_logger

log = get_logger()


class BudgetError(Exception):
    """预算存储/读写失败（显式，CLOSE-02）。"""


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _resolve_db_path() -> str:
    env = str(getattr(Config, "BUDGET_DB", "") or "").strip()
    if env:
        return env
    from ..history import DB_PATH as HISTORY_DB
    return os.path.join(os.path.dirname(HISTORY_DB), "qq_budget.db")


class BudgetStore:
    """per-scope 每日用量存储。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path if db_path is not None else _resolve_db_path()
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _init(self) -> None:
        try:
            with self._conn() as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS usage ("
                    "  scope TEXT NOT NULL,"
                    "  day TEXT NOT NULL,"
                    "  calls INTEGER NOT NULL DEFAULT 0,"
                    "  est_tokens INTEGER NOT NULL DEFAULT 0,"
                    "  PRIMARY KEY (scope, day)"
                    ")"
                )
        except Exception as e:  # noqa: BLE001
            raise BudgetError("预算库初始化失败（%s）" % type(e).__name__)

    def record(self, scope: str, day: str | None = None, calls: int = 1,
               est_tokens: int = 0) -> None:
        if calls <= 0 and est_tokens <= 0:
            return
        day = day or _today()
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO usage (scope, day, calls, est_tokens) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(scope, day) DO UPDATE SET "
                    "calls = calls + excluded.calls, "
                    "est_tokens = est_tokens + excluded.est_tokens",
                    (scope, day, int(calls), int(est_tokens)),
                )
        except Exception as e:  # noqa: BLE001
            raise BudgetError("预算记录失败（%s）" % type(e).__name__)

    def get_scope_day(self, scope: str, day: str | None = None) -> dict:
        day = day or _today()
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT calls, est_tokens FROM usage "
                    "WHERE scope = ? AND day = ?", (scope, day)
                ).fetchone()
                g = con.execute(
                    "SELECT COALESCE(SUM(calls),0), COALESCE(SUM(est_tokens),0) "
                    "FROM usage WHERE day = ?", (day,)
                ).fetchone()
        except Exception as e:  # noqa: BLE001
            raise BudgetError("预算读取失败（%s）" % type(e).__name__)
        return {
            "scope_calls": int(row[0]) if row else 0,
            "scope_tokens": int(row[1]) if row else 0,
            "global_calls": int(g[0]),
            "global_tokens": int(g[1]),
        }

    def reset_scope(self, scope: str) -> None:
        try:
            with self._conn() as con:
                con.execute("DELETE FROM usage WHERE scope = ?", (scope,))
        except Exception as e:  # noqa: BLE001
            raise BudgetError("预算重置失败（%s）" % type(e).__name__)


#: 模块级单例
budget_store = BudgetStore()
