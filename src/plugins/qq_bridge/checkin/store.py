# -*- coding: utf-8 -*-
"""checkin/store.py —— 签到模块持久化（sqlite3，零主包依赖，可裸单测）。

表结构：
  profiles(scope, user_id, points, streak, total_days, last_day, updated_at)  主键 (scope, user_id)
  days(scope, user_id, day, points, kind)                                    主键 (scope, user_id, day)
    —— 本月日历渲染 + 补签判定；kind: daily / makeup（里程碑奖励已并入当日 gain）
"""
from __future__ import annotations

import os
import sqlite3
import time

from ..config import Config  # 仅此一处依赖主包 Config，保持模块可单测


class CheckinError(Exception):
    """签到库读写失败。"""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    scope      TEXT    NOT NULL,
    user_id    INTEGER NOT NULL,
    points     INTEGER NOT NULL DEFAULT 0,
    streak     INTEGER NOT NULL DEFAULT 0,
    total_days INTEGER NOT NULL DEFAULT 0,
    last_day   TEXT    NOT NULL DEFAULT '',
    updated_at REAL    NOT NULL,
    PRIMARY KEY (scope, user_id)
);
CREATE TABLE IF NOT EXISTS days (
    scope   TEXT    NOT NULL,
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,
    points  INTEGER NOT NULL DEFAULT 0,
    kind    TEXT    NOT NULL DEFAULT 'daily',
    PRIMARY KEY (scope, user_id, day)
);
"""


def _resolve_db_path() -> str:
    env = str(getattr(Config, "CHECKIN_DB", "") or "").strip()
    if env:
        return env
    from ..history import DB_PATH as HISTORY_DB
    return os.path.join(os.path.dirname(HISTORY_DB), "qq_checkin.db")


class CheckinStore:
    """签到存储（单例见 checkin_store）。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path if db_path is not None else _resolve_db_path()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_db(self) -> None:
        try:
            with self._conn() as con:
                con.executescript(_SCHEMA)
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到库初始化失败（%s）：%s"
                               % (type(e).__name__, str(e)[:200]))

    # ---------------- 档案 ----------------
    def get_profile(self, scope: str, user_id: int) -> dict:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT points, streak, total_days, last_day, updated_at "
                    "FROM profiles WHERE scope=? AND user_id=?",
                    (scope, int(user_id))).fetchone()
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到档案读取失败（%s）" % type(e).__name__)
        if not row:
            return {"scope": scope, "user_id": int(user_id), "points": 0,
                    "streak": 0, "total_days": 0, "last_day": "", "updated_at": 0.0}
        return {"scope": scope, "user_id": int(user_id),
                "points": int(row["points"]), "streak": int(row["streak"]),
                "total_days": int(row["total_days"]), "last_day": row["last_day"] or "",
                "updated_at": float(row["updated_at"] or 0)}

    def set_profile(self, scope: str, user_id: int, points: int, streak: int,
                    total_days: int, last_day: str) -> None:
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO profiles(scope, user_id, points, streak, total_days,"
                    " last_day, updated_at) VALUES(?,?,?,?,?,?,?)"
                    " ON CONFLICT(scope, user_id) DO UPDATE SET"
                    " points=excluded.points, streak=excluded.streak,"
                    " total_days=excluded.total_days, last_day=excluded.last_day,"
                    " updated_at=excluded.updated_at",
                    (scope, int(user_id), int(points), int(streak), int(total_days),
                     last_day, time.time()))
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到档案写入失败（%s）" % type(e).__name__)

    # ---------------- 每日明细 ----------------
    def get_day(self, scope: str, user_id: int, day: str) -> dict | None:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT points, kind FROM days"
                    " WHERE scope=? AND user_id=? AND day=?",
                    (scope, int(user_id), day)).fetchone()
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到日读取失败（%s）" % type(e).__name__)
        if not row:
            return None
        return {"day": day, "points": int(row["points"]), "kind": row["kind"] or "daily"}

    def upsert_day(self, scope: str, user_id: int, day: str, points: int, kind: str) -> None:
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO days(scope, user_id, day, points, kind)"
                    " VALUES(?,?,?,?,?)"
                    " ON CONFLICT(scope, user_id, day) DO UPDATE SET"
                    " points=excluded.points, kind=excluded.kind",
                    (scope, int(user_id), day, int(points), kind))
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到日写入失败（%s）" % type(e).__name__)

    def month_days(self, scope: str, user_id: int, year: int, month: int) -> dict:
        """本月已签到日 → {day: points}（含 makeup 日）。"""
        prefix = "%04d-%02d-" % (year, month)
        try:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT day, points FROM days"
                    " WHERE scope=? AND user_id=? AND day LIKE ?",
                    (scope, int(user_id), prefix + "%")).fetchall()
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到日历读取失败（%s）" % type(e).__name__)
        return {r["day"]: int(r["points"]) for r in rows}

    # ---------------- 排行榜 ----------------
    def leaderboard(self, scope: str, limit: int = 10) -> list[dict]:
        try:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT user_id, points, streak, total_days FROM profiles"
                    " WHERE scope=? ORDER BY points DESC, total_days DESC"
                    " LIMIT ?", (scope, int(limit))).fetchall()
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到榜读取失败（%s）" % type(e).__name__)
        return [{"user_id": int(r["user_id"]), "points": int(r["points"]),
                 "streak": int(r["streak"]), "total_days": int(r["total_days"])}
                for r in rows]

    def rank_of(self, scope: str, user_id: int) -> int:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT COUNT(*) AS c FROM profiles"
                    " WHERE scope=? AND points > COALESCE("
                    "   (SELECT points FROM profiles WHERE scope=? AND user_id=?), 0)",
                    (scope, scope, int(user_id))).fetchone()
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到排名读取失败（%s）" % type(e).__name__)
        return int(row["c"]) + 1

    def total_users(self, scope: str) -> int:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT COUNT(*) AS c FROM profiles WHERE scope=? AND total_days>0",
                    (scope,)).fetchone()
        except Exception as e:  # noqa: BLE001
            raise CheckinError("签到人数读取失败（%s）" % type(e).__name__)
        return int(row["c"])
