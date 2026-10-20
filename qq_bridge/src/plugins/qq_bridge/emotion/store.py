# -*- coding: utf-8 -*-
"""emotion.store —— 情感/好感度/日程存储（M3，2026-10-20）。

表（SQLite data/qq_emotion.db）：
  moods        per scope 的二维情感状态（valence/arousal/text + 时间戳）
  affections   per scope per user 的好感度（0~100）+ 手动覆盖的关系标签
  schedules    日程事件（用户提到的安排：面试/考试/约会…）→ 到期关心

与 M2 提醒的分工（避免重复实现）：
  - 提醒（reminders）= **用户主动设的**、到点回传原话；
  - 日程（schedules）= **机器人自己记下的**（对话里听到的安排）、
    到点主动关心（「你今天不是面试吗？」）。语义不同，各管一段。

纪律：同步 sqlite3 + busy_timeout（history 模块同款约定，调用方
to_thread 包）；失败显式 EmotionError（CLOSE-02）；软删 + 恢复 + gc
（OPS-02）；不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid

from ..config import Config, get_logger, jlog
from .mood import (A_NEUTRAL, FAV_DEFAULT, FAV_MAX, FAV_MIN, MoodState,
                   V_NEUTRAL, EmotionError, mood_text)

log = get_logger()

#: 日程保留上限（超出滚动清理，OPS-02）
MAX_SCHEDULES = 500


def _resolve_db_path() -> str:
    env = str(getattr(Config, "EMOTION_DB", "") or "").strip()
    if env:
        return env
    from ..history import DB_PATH as HISTORY_DB
    return os.path.join(os.path.dirname(HISTORY_DB), "qq_emotion.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS moods (
    scope TEXT PRIMARY KEY,
    valence REAL NOT NULL DEFAULT 0.0,
    arousal REAL NOT NULL DEFAULT 0.5,
    text TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS affections (
    scope TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    favor INTEGER NOT NULL DEFAULT 30,
    relation TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, user_id)
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    due_at REAL NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    deleted_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sched_due ON schedules(deleted_at, done, due_at);
"""


class EmotionStore:
    """情感/好感度/日程存储（单例见 emotion_store）。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path if db_path is not None else _resolve_db_path()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_db(self) -> None:
        try:
            with self._conn() as con:
                con.executescript(_SCHEMA)
        except Exception as e:  # noqa: BLE001
            raise EmotionError("情感库初始化失败（%s）：%s"
                               % (type(e).__name__, str(e)[:200]))

    # ---------------- 情感状态 ----------------
    def get_mood(self, scope: str) -> MoodState:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT valence, arousal, text, updated_at FROM moods"
                    " WHERE scope=?", (scope,)).fetchone()
        except Exception as e:  # noqa: BLE001
            raise EmotionError("情绪读取失败（%s）" % type(e).__name__)
        if not row:
            return MoodState(V_NEUTRAL, A_NEUTRAL, mood_text(V_NEUTRAL, A_NEUTRAL),
                             time.time())
        return MoodState(float(row["valence"]), float(row["arousal"]),
                         row["text"] or "平静", float(row["updated_at"] or 0))

    def save_mood(self, scope: str, st: MoodState) -> None:
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO moods(scope, valence, arousal, text, updated_at)"
                    " VALUES(?,?,?,?,?)"
                    " ON CONFLICT(scope) DO UPDATE SET valence=excluded.valence,"
                    " arousal=excluded.arousal, text=excluded.text,"
                    " updated_at=excluded.updated_at",
                    (scope, st.valence, st.arousal, st.text, st.updated_at))
        except Exception as e:  # noqa: BLE001
            raise EmotionError("情绪写入失败（%s）" % type(e).__name__)

    # ---------------- 好感度 ----------------
    def get_favor(self, scope: str, user_id: int) -> dict:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT favor, relation, updated_at FROM affections"
                    " WHERE scope=? AND user_id=?", (scope, int(user_id))).fetchone()
        except Exception as e:  # noqa: BLE001
            raise EmotionError("好感度读取失败（%s）" % type(e).__name__)
        if not row:
            return {"scope": scope, "user_id": int(user_id),
                    "favor": FAV_DEFAULT, "relation": "", "updated_at": 0.0}
        return {"scope": scope, "user_id": int(user_id),
                "favor": int(row["favor"]), "relation": row["relation"] or "",
                "updated_at": float(row["updated_at"] or 0)}

    def save_favor(self, scope: str, user_id: int, favor: int,
                   relation: str = "") -> dict:
        favor = max(FAV_MIN, min(FAV_MAX, int(favor)))
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO affections(scope, user_id, favor, relation,"
                    " updated_at) VALUES(?,?,?,?,?)"
                    " ON CONFLICT(scope, user_id) DO UPDATE SET"
                    " favor=excluded.favor, relation=excluded.relation,"
                    " updated_at=excluded.updated_at",
                    (scope, int(user_id), favor, relation[:40], time.time()))
        except Exception as e:  # noqa: BLE001
            raise EmotionError("好感度写入失败（%s）" % type(e).__name__)
        return self.get_favor(scope, user_id)

    def set_relation(self, scope: str, user_id: int, relation: str) -> dict:
        """手动指定关系标签（覆盖好感度派生，供 /情感 更正用）。"""
        cur = self.get_favor(scope, user_id)
        return self.save_favor(scope, user_id, cur["favor"], relation)

    def top_favors(self, scope: str, limit: int = 5) -> list[dict]:
        """好感度排行（/情感 榜单）。"""
        try:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT user_id, favor, relation FROM affections"
                    " WHERE scope=? ORDER BY favor DESC LIMIT ?",
                    (scope, max(1, int(limit)))).fetchall()
        except Exception as e:  # noqa: BLE001
            raise EmotionError("好感度榜单读取失败（%s）" % type(e).__name__)
        return [dict(r) for r in rows]

    # ---------------- 日程 ----------------
    def add_schedule(self, title: str, due_at: float, scope: str,
                     user_id: int = 0, source: str = "") -> dict:
        title = (title or "").strip()
        if not title:
            raise EmotionError("日程内容不能为空")
        try:
            due = float(due_at)
        except (TypeError, ValueError):
            raise EmotionError("日程到期时间必须是时间戳")
        item = {"id": uuid.uuid4().hex[:8], "scope": scope,
                "user_id": int(user_id or 0), "title": title[:120],
                "due_at": due, "source": source[:120], "done": 0,
                "notified": 0, "created_at": time.time(), "deleted_at": 0.0}
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO schedules(id, scope, user_id, title, due_at,"
                    " source, done, notified, created_at, deleted_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], item["scope"], item["user_id"], item["title"],
                     item["due_at"], item["source"], item["done"],
                     item["notified"], item["created_at"], item["deleted_at"]))
        except Exception as e:  # noqa: BLE001
            raise EmotionError("日程写入失败（%s）" % type(e).__name__)
        log.info(jlog("schedule_added", sid=item["id"], scope=scope,
                      title=title[:40], due_at=due))
        return item

    def list_schedules(self, scope: str, include_done: bool = False) -> list[dict]:
        try:
            with self._conn() as con:
                if include_done:
                    rows = con.execute(
                        "SELECT id, scope, user_id, title, due_at, source, done,"
                        " notified FROM schedules WHERE scope=? AND deleted_at=0"
                        " ORDER BY due_at ASC", (scope,)).fetchall()
                else:
                    rows = con.execute(
                        "SELECT id, scope, user_id, title, due_at, source, done,"
                        " notified FROM schedules WHERE scope=? AND deleted_at=0"
                        " AND done=0 ORDER BY due_at ASC", (scope,)).fetchall()
        except Exception as e:  # noqa: BLE001
            raise EmotionError("日程读取失败（%s）" % type(e).__name__)
        return [dict(r) for r in rows]

    def get_schedule(self, sid: str, scope: str) -> dict | None:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT id, scope, user_id, title, due_at, source, done,"
                    " notified FROM schedules WHERE id=? AND scope=? AND deleted_at=0",
                    (sid, scope)).fetchone()
        except Exception as e:  # noqa: BLE001
            raise EmotionError("日程读取失败（%s）" % type(e).__name__)
        return dict(row) if row else None

    def mark_done(self, sid: str, scope: str) -> bool:
        try:
            with self._conn() as con:
                cur = con.execute(
                    "UPDATE schedules SET done=1 WHERE id=? AND scope=? AND deleted_at=0",
                    (sid, scope))
        except Exception as e:  # noqa: BLE001
            raise EmotionError("日程标记失败（%s）" % type(e).__name__)
        return cur.rowcount > 0

    def mark_notified(self, sid: str, scope: str) -> bool:
        """标记已关心过（防重复打扰）。

        幂等语义：只认「未标记过 → 已标记」这次真实变更；重复调用返回
        False（不重复计数，调用方可据此判断是不是第一次关心）。
        """
        try:
            with self._conn() as con:
                cur = con.execute(
                    "UPDATE schedules SET notified=1 WHERE id=? AND scope=?"
                    " AND deleted_at=0 AND notified=0", (sid, scope))
        except Exception as e:  # noqa: BLE001
            raise EmotionError("日程通知标记失败（%s）" % type(e).__name__)
        return cur.rowcount > 0

    def soft_delete_schedule(self, sid: str, scope: str) -> bool:
        try:
            with self._conn() as con:
                cur = con.execute(
                    "UPDATE schedules SET deleted_at=? WHERE id=? AND scope=?"
                    " AND deleted_at=0", (time.time(), sid, scope))
        except Exception as e:  # noqa: BLE001
            raise EmotionError("日程删除失败（%s）" % type(e).__name__)
        return cur.rowcount > 0

    def due_schedules(self, scope: str = "", now: float | None = None,
                      window: float = 3600.0, limit: int = 5) -> list[dict]:
        """到点/即将到点的日程（到期关心用；未通知过的优先）。

        window：提前多少秒算「即将」（默认 1 小时）。
        """
        now = time.time() if now is None else float(now)
        try:
            with self._conn() as con:
                if scope:
                    rows = con.execute(
                        "SELECT id, scope, user_id, title, due_at, source, done,"
                        " notified FROM schedules WHERE scope=? AND deleted_at=0"
                        " AND done=0 AND notified=0 AND due_at<=?"
                        " ORDER BY due_at ASC LIMIT ?",
                        (scope, now + window, max(1, int(limit)))).fetchall()
                else:
                    rows = con.execute(
                        "SELECT id, scope, user_id, title, due_at, source, done,"
                        " notified FROM schedules WHERE deleted_at=0 AND done=0"
                        " AND notified=0 AND due_at<=?"
                        " ORDER BY due_at ASC LIMIT ?",
                        (now + window, max(1, int(limit)))).fetchall()
        except Exception as e:  # noqa: BLE001
            raise EmotionError("到期日程查询失败（%s）" % type(e).__name__)
        return [dict(r) for r in rows]

    # ---------------- 清理（OPS-02/03） ----------------
    def gc(self, now: float | None = None) -> int:
        """滚动清理：已完成/已过期超 EMOTION_SCHEDULE_KEEP_DAYS 的日程软删。"""
        now = time.time() if now is None else float(now)
        keep = max(1.0, float(getattr(Config, "EMOTION_SCHEDULE_KEEP_DAYS", 30.0) or 30.0))
        cutoff = now - keep * 86400.0
        try:
            with self._conn() as con:
                cur = con.execute(
                    "UPDATE schedules SET deleted_at=? WHERE deleted_at=0"
                    " AND (done=1 OR due_at<?)", (now, cutoff))
                n = cur.rowcount
                total = con.execute(
                    "SELECT COUNT(*) FROM schedules WHERE deleted_at=0").fetchone()[0]
                over = max(0, int(total) - MAX_SCHEDULES)
                if over:
                    con.execute(
                        "UPDATE schedules SET deleted_at=? WHERE id IN ("
                        " SELECT id FROM schedules WHERE deleted_at=0"
                        " ORDER BY created_at ASC LIMIT ?)", (now, min(over, 500)))
        except Exception as e:  # noqa: BLE001
            raise EmotionError("日程清理失败（%s）" % type(e).__name__)
        if n or over:
            log.info(jlog("emotion_gc", cleaned=int(n), over_cap=over))
        return int(n + over)


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
emotion_store = EmotionStore()
