# -*- coding: utf-8 -*-
"""memory.store —— 三态记忆存储（M1 记忆系统，2026-10-20）。

契约来源（2026-10-20 实读 MaiBot A-Memorix README v2.0.0 验证）：
  - 长期记忆子系统 = 文本段落 + 关系 + Episode 事件 + 人物画像（自动快照 +
    手动 override）+ 检索（稀疏 FTS / 向量）+ 删除可恢复（recycle_bin）；
  - 写入去重 external_id 幂等；画像自动快照 + 手动 override（= 结构化画像
    更正，对应 personification 的画像更正需求）。

本地化取舍（个人单机、零重依赖、禁堆库）：
  - 存储：SQLite 单库（data/qq_memory.db），FTS5 稀疏检索（本机
    sqlite 3.50.4 实测支持）；**向量检索二期**（走在线 embedding API，
    一期不做——两个明确能力层，不是偷偷降级）；
  - 关系图谱简化为画像的关系标签字段（个人单机无图谱查询需求）；
  - 删除 = 软删（deleted_at）+ restore（A-Memorix recycle_bin 范式），
    硬清理由 _gc 滚动执行（OPS-02）。

三态划分（与 ai_mode 既有短期历史的分工）：
  - 短期 = ai_mode.AIChatter.history（内存+TTL，既有不动）；
  - 长期 = events 表（值得记的事件/对话摘要，FTS+时间衰减检索）；
  - 核心 = profiles 表（人物画像：结构化事实 + 手动覆盖）+ groups 表
    （群档案：氛围/话题/常客）。

纪律：同步 sqlite3 + busy_timeout，调用方 to_thread 包（history 模块同款
约定）；失败显式抛 MemoryError（CLOSE-02）；本模块不 import nonebot /
不 import 主包：可裸单测。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import Config, get_logger, jlog

log = get_logger()

#: 事件/事实保留上限（超出按时间滚动清理，OPS-02）
MAX_EVENTS = 5000
#: 画像事实条数上限（每用户）
MAX_FACTS_PER_USER = 50
#: FTS 检索默认返回条数
DEFAULT_RECALL_LIMIT = 5

KIND_FACT = "fact"      # 关于用户的持久事实（进画像候选）
KIND_EVENT = "event"    # 值得记的事件（长期库）
KIND_SUMMARY = "summary"  # 对话摘要（长期库）


class MemoryError(Exception):
    """显式化的记忆存储失败。"""


def _resolve_db_path() -> str:
    env = str(getattr(Config, "MEMORY_DB", "") or "").strip()
    if env:
        return env
    # 与 history 同目录（data/qq_memory.db）
    from ..history import DB_PATH as HISTORY_DB
    return os.path.join(os.path.dirname(HISTORY_DB), "qq_memory.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'event',
    text TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    deleted_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_scope ON events(scope, created_at);
CREATE INDEX IF NOT EXISTS idx_events_deleted ON events(deleted_at, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    text, content='events', content_rowid='rowid', tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE TABLE IF NOT EXISTS profiles (
    scope TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    facts TEXT NOT NULL DEFAULT '{}',
    overrides TEXT NOT NULL DEFAULT '{}',
    relation TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, user_id)
);

CREATE TABLE IF NOT EXISTS group_profiles (
    scope TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    vibe TEXT NOT NULL DEFAULT '',
    topics TEXT NOT NULL DEFAULT '[]',
    regulars TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL
);
"""


class MemoryStore:
    """三态记忆存储（单例见 memory_store）。

    表：
      events       长期记忆（fact/event/summary，FTS + 时间衰减 + 软删）
      profiles     核心·人物画像（facts 自动快照 + overrides 手动覆盖）
      group_profiles 核心·群档案（氛围/话题/常客）
    """

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
        except Exception as e:  # noqa: BLE001 — 建表失败显式（FTS5 缺失等）
            raise MemoryError("记忆库初始化失败（%s）：%s"
                              % (type(e).__name__, str(e)[:200]))

    # ---------------- 长期记忆（events） ----------------
    def add_event(self, text: str, scope: str, user_id: int = 0,
                  kind: str = KIND_EVENT, weight: float = 1.0,
                  source: str = "", now: float | None = None) -> dict:
        """写一条长期记忆。返回事件 dict（失败显式抛错）。"""
        text = (text or "").strip()
        if not text:
            raise MemoryError("记忆内容不能为空")
        if kind not in (KIND_FACT, KIND_EVENT, KIND_SUMMARY):
            raise MemoryError("记忆类型只能是 %s" % "/".join(
                (KIND_FACT, KIND_EVENT, KIND_SUMMARY)))
        now = time.time() if now is None else float(now)
        ev = {"id": uuid.uuid4().hex[:12], "scope": scope,
              "user_id": int(user_id or 0), "kind": kind,
              "text": text[:500], "weight": float(weight),
              "source": source[:120], "created_at": now, "deleted_at": 0.0}
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO events(id, scope, user_id, kind, text, weight,"
                    " source, created_at, deleted_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (ev["id"], ev["scope"], ev["user_id"], ev["kind"],
                     ev["text"], ev["weight"], ev["source"],
                     ev["created_at"], ev["deleted_at"]))
        except Exception as e:  # noqa: BLE001
            raise MemoryError("记忆写入失败（%s）" % type(e).__name__)
        log.info(jlog("memory_event_added", eid=ev["id"], scope=scope,
                      kind=kind, weight=ev["weight"]))
        return ev

    def search(self, query: str, scope: str, limit: int = DEFAULT_RECALL_LIMIT,
               now: float | None = None) -> list[dict]:
        """检索 + 时间衰减打分（近期记忆权重更高，越老越淡）。

        CJK 检索方案（2026-10-20 实测 FTS5 unicode61 **不索引 CJK**——
        默认分词器把汉字当分隔符丢弃，故换 trigram；但 trigram 有最小
        3 字符约束，中文两字词极常用，故按查询长度走两条明确路径）：
          - len(query) >= 3 → FTS5 trigram MATCH（BM25 相关性 + 衰减）；
          - len(query) < 3  → LIKE 子串匹配（相关分恒定 1.0 + 衰减）。
        这是**规则路由不是兜底**：两条路径都产出「命中集 + 衰减加权」，
        按长度选索引策略；查询词为空/无命中 → 空列表（不报错）。

        打分 = 相关性 × 时间衰减 × weight；衰减半衰期 MEMORY_DECAY_DAYS
        （默认 30 天）。
        """
        q = (query or "").strip()
        if not q:
            return []
        now = time.time() if now is None else float(now)
        half_life = max(1.0, float(getattr(Config, "MEMORY_DECAY_DAYS", 30.0) or 30.0)) * 86400.0
        try:
            with self._conn() as con:
                if len(q) >= 3:
                    # trigram 路径：FTS5 标准转义——双写引号包成 phrase
                    # （phrase 内按字面匹配，OR/AND/引号等不再有运算符语义）
                    q_safe = '"%s"' % q.replace('"', '""')
                    rows = con.execute(
                        "SELECT e.id, e.scope, e.user_id, e.kind, e.text, e.weight,"
                        " e.created_at, bm25(events_fts) AS rank"
                        " FROM events_fts f JOIN events e ON e.rowid = f.rowid"
                        " WHERE events_fts MATCH ? AND e.scope = ? AND e.deleted_at = 0"
                        " ORDER BY rank LIMIT ?",
                        (q_safe, scope, max(1, int(limit)) * 3),
                    ).fetchall()
                    base_rank = lambda r: float(r["rank"] or 0)  # noqa: E731
                else:
                    # 短查询：LIKE 子串（转义 % _ 防通配符注入）
                    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%") \
                        .replace("_", "\\_") + "%"
                    rows = con.execute(
                        "SELECT id, scope, user_id, kind, text, weight, created_at,"
                        " 1.0 AS rank FROM events"
                        " WHERE scope=? AND deleted_at=0 AND text LIKE ? ESCAPE '\\'"
                        " ORDER BY created_at DESC LIMIT ?",
                        (scope, like, max(1, int(limit)) * 3),
                    ).fetchall()
                    base_rank = lambda r: float(r["rank"] or 1.0)  # noqa: E731
        except Exception as e:  # noqa: BLE001 — FTS 语法错等显式失败
            raise MemoryError("记忆检索失败（%s）：%s" % (type(e).__name__, str(e)[:160]))
        scored = []
        for r in rows:
            age = max(0.0, now - float(r["created_at"] or 0))
            decay = 0.5 ** (age / half_life)          # 半衰期衰减
            score = base_rank(r) * decay * float(r["weight"] or 1.0)
            scored.append((score, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [dict(item, score=sc) for sc, item in scored[:max(1, int(limit))]]

    @staticmethod
    def _fts_query(q: str) -> str:
        """（保留）查询词转义助手——trigram 路径下查询词按字面匹配即可。"""
        return q.replace('"', " ").strip() or '""'

    def recent(self, scope: str, limit: int = 5, kind: str | None = None) -> list[dict]:
        """最近的长期记忆（无查询词时的兜底注入）。"""
        try:
            with self._conn() as con:
                if kind:
                    rows = con.execute(
                        "SELECT id, scope, user_id, kind, text, weight, created_at"
                        " FROM events WHERE scope=? AND deleted_at=0 AND kind=?"
                        " ORDER BY created_at DESC LIMIT ?",
                        (scope, kind, max(1, int(limit)))).fetchall()
                else:
                    rows = con.execute(
                        "SELECT id, scope, user_id, kind, text, weight, created_at"
                        " FROM events WHERE scope=? AND deleted_at=0"
                        " ORDER BY created_at DESC LIMIT ?",
                        (scope, max(1, int(limit)))).fetchall()
        except Exception as e:  # noqa: BLE001
            raise MemoryError("记忆读取失败（%s）" % type(e).__name__)
        return [dict(r) for r in rows]

    def soft_delete(self, eid: str, scope: str) -> bool:
        """软删一条记忆（A-Memorix recycle_bin 范式：可恢复）。"""
        try:
            with self._conn() as con:
                cur = con.execute(
                    "UPDATE events SET deleted_at=? WHERE id=? AND scope=? AND deleted_at=0",
                    (time.time(), eid, scope))
        except Exception as e:  # noqa: BLE001
            raise MemoryError("记忆删除失败（%s）" % type(e).__name__)
        ok = cur.rowcount > 0
        log.info(jlog("memory_event_deleted" if ok else "memory_event_delete_miss",
                      eid=eid, scope=scope))
        return ok

    def restore(self, eid: str, scope: str) -> bool:
        """恢复软删的记忆。"""
        try:
            with self._conn() as con:
                cur = con.execute(
                    "UPDATE events SET deleted_at=0 WHERE id=? AND scope=? AND deleted_at>0",
                    (eid, scope))
        except Exception as e:  # noqa: BLE001
            raise MemoryError("记忆恢复失败（%s）" % type(e).__name__)
        return cur.rowcount > 0

    # ---------------- 核心·人物画像 ----------------
    def get_profile(self, scope: str, user_id: int) -> dict:
        """人物画像（facts 自动快照 + overrides 手动覆盖，override 优先）。"""
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT facts, overrides, relation, updated_at FROM profiles"
                    " WHERE scope=? AND user_id=?", (scope, int(user_id))).fetchone()
        except Exception as e:  # noqa: BLE001
            raise MemoryError("画像读取失败（%s）" % type(e).__name__)
        if not row:
            return {"scope": scope, "user_id": int(user_id), "facts": {},
                    "overrides": {}, "relation": "", "updated_at": 0.0}
        facts = self._loads(row["facts"], {})
        overrides = self._loads(row["overrides"], {})
        # 视图：facts 值展开为裸值（facts 里存的是 {value, at} 包装），
        # override 同字段覆盖（结构化画像更正：用户说的话优先）
        view = {k: (v.get("value") if isinstance(v, dict) else v)
                for k, v in facts.items()}
        view.update({k: v.get("value") for k, v in overrides.items()})
        return {"scope": scope, "user_id": int(user_id), "facts": facts,
                "overrides": overrides, "relation": row["relation"] or "",
                "view": view, "updated_at": float(row["updated_at"] or 0)}

    @staticmethod
    def _loads(raw: str, default: Any) -> Any:
        try:
            return json.loads(raw) if raw else default
        except Exception:  # noqa: BLE001 — 坏 JSON 不当致命（返回默认+留痕）
            log.warning(jlog("memory_json_corrupt", detail=str(raw)[:80]))
            return default

    def set_facts(self, scope: str, user_id: int, facts: dict) -> dict:
        """LLM 抽取的自动快照（合并写入；同键覆盖；超上限拒绝显式报错）。"""
        if not isinstance(facts, dict) or not facts:
            raise MemoryError("画像事实不能为空")
        cur = self.get_profile(scope, user_id)
        merged = dict(cur["facts"])
        for k, v in facts.items():
            k = str(k)[:40]
            merged[k] = {"value": str(v)[:200], "at": time.time()}
        if len(merged) > MAX_FACTS_PER_USER:
            raise MemoryError("画像事实超上限（%d 条）——先删几条旧的"
                              % MAX_FACTS_PER_USER)
        return self._save_profile(scope, user_id, merged, cur["overrides"],
                                  cur["relation"])

    def set_override(self, scope: str, user_id: int, key: str, value: str,
                     by: int = 0) -> dict:
        """手动覆盖（结构化画像更正：override 优先于自动快照）。"""
        key = str(key or "").strip()
        if not key:
            raise MemoryError("更正项名不能为空")
        cur = self.get_profile(scope, user_id)
        overrides = dict(cur["overrides"])
        overrides[key[:40]] = {"value": str(value)[:200], "by": int(by or 0),
                               "at": time.time()}
        return self._save_profile(scope, user_id, cur["facts"], overrides,
                                  cur["relation"])

    def drop_override(self, scope: str, user_id: int, key: str) -> dict:
        cur = self.get_profile(scope, user_id)
        overrides = {k: v for k, v in cur["overrides"].items() if k != key}
        return self._save_profile(scope, user_id, cur["facts"], overrides,
                                  cur["relation"])

    def drop_fact(self, scope: str, user_id: int, key: str) -> dict:
        cur = self.get_profile(scope, user_id)
        facts = {k: v for k, v in cur["facts"].items() if k != key}
        return self._save_profile(scope, user_id, facts, cur["overrides"],
                                  cur["relation"])

    def set_relation(self, scope: str, user_id: int, relation: str) -> dict:
        cur = self.get_profile(scope, user_id)
        return self._save_profile(scope, user_id, cur["facts"],
                                  cur["overrides"], str(relation)[:40])

    def _save_profile(self, scope: str, user_id: int, facts: dict,
                      overrides: dict, relation: str) -> dict:
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO profiles(scope, user_id, facts, overrides,"
                    " relation, updated_at) VALUES(?,?,?,?,?,?)"
                    " ON CONFLICT(scope, user_id) DO UPDATE SET"
                    " facts=excluded.facts, overrides=excluded.overrides,"
                    " relation=excluded.relation, updated_at=excluded.updated_at",
                    (scope, int(user_id), json.dumps(facts, ensure_ascii=False),
                     json.dumps(overrides, ensure_ascii=False), relation,
                     time.time()))
        except Exception as e:  # noqa: BLE001
            raise MemoryError("画像写入失败（%s）" % type(e).__name__)
        log.info(jlog("memory_profile_saved", scope=scope, user_id=user_id,
                      facts=len(facts), overrides=len(overrides)))
        return self.get_profile(scope, user_id)

    # ---------------- 核心·群档案 ----------------
    def get_group_profile(self, scope: str) -> dict:
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT title, vibe, topics, regulars, updated_at FROM"
                    " group_profiles WHERE scope=?", (scope,)).fetchone()
        except Exception as e:  # noqa: BLE001
            raise MemoryError("群档案读取失败（%s）" % type(e).__name__)
        if not row:
            return {"scope": scope, "title": "", "vibe": "", "topics": [],
                    "regulars": [], "updated_at": 0.0}
        return {"scope": scope, "title": row["title"] or "",
                "vibe": row["vibe"] or "",
                "topics": self._loads(row["topics"], []),
                "regulars": self._loads(row["regulars"], []),
                "updated_at": float(row["updated_at"] or 0)}

    def update_group_profile(self, scope: str, title: str | None = None,
                             vibe: str | None = None, topics: list | None = None,
                             regulars: list | None = None) -> dict:
        cur = self.get_group_profile(scope)
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO group_profiles(scope, title, vibe, topics,"
                    " regulars, updated_at) VALUES(?,?,?,?,?,?)"
                    " ON CONFLICT(scope) DO UPDATE SET"
                    " title=excluded.title, vibe=excluded.vibe,"
                    " topics=excluded.topics, regulars=excluded.regulars,"
                    " updated_at=excluded.updated_at",
                    (scope, cur["title"] if title is None else str(title)[:80],
                     cur["vibe"] if vibe is None else str(vibe)[:200],
                     json.dumps(cur["topics"] if topics is None
                                else list(topics)[:20], ensure_ascii=False),
                     json.dumps(cur["regulars"] if regulars is None
                                else list(regulars)[:30], ensure_ascii=False),
                     time.time()))
        except Exception as e:  # noqa: BLE001
            raise MemoryError("群档案写入失败（%s）" % type(e).__name__)
        log.info(jlog("memory_group_saved", scope=scope))
        return self.get_group_profile(scope)

    # ---------------- 清理（OPS-02/03：幂等、分批） ----------------
    def gc(self, now: float | None = None) -> int:
        """滚动清理：软删超 MEMORY_KEEP_DAYS 的记忆 + events 总数超限删最旧。

        返回清理条数。幂等（重复跑无副作用）。
        """
        now = time.time() if now is None else float(now)
        keep_days = max(1.0, float(getattr(Config, "MEMORY_KEEP_DAYS", 180.0) or 180.0))
        cutoff = now - keep_days * 86400.0
        try:
            with self._conn() as con:
                cur = con.execute(
                    "UPDATE events SET deleted_at=? WHERE deleted_at=0 AND created_at<?",
                    (now, cutoff))
                aged = cur.rowcount
                # 总数超限：删最旧的未软删记录（分批 ≤500，OPS-03）
                total = con.execute(
                    "SELECT COUNT(*) FROM events WHERE deleted_at=0").fetchone()[0]
                over = max(0, int(total) - MAX_EVENTS)
                if over:
                    con.execute(
                        "UPDATE events SET deleted_at=? WHERE id IN ("
                        " SELECT id FROM events WHERE deleted_at=0"
                        " ORDER BY created_at ASC LIMIT ?)", (now, min(over, 500)))
        except Exception as e:  # noqa: BLE001
            raise MemoryError("记忆清理失败（%s）" % type(e).__name__)
        if aged or over:
            log.info(jlog("memory_gc", aged=aged, over_cap=over))
        return int(aged + over)


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
memory_store = MemoryStore()
