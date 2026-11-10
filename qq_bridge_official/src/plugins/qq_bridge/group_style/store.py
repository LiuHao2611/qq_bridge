# -*- coding: utf-8 -*-
"""group_style/store —— 群风格画像存储（2026-10-21）。

零主包依赖、可裸单测（仅依赖 config）；LLM 抽取在 learn.py（延迟 import
tools.llm）。存储：SQLite 单库 data/qq_group_style.db。

群风格 = 一个群聊的「说话方式画像」：氛围（atmosphere）+ 常用梗（memes）
+ 禁忌（taboo）。由 LLM 从群消息归纳，或主人手动增删。

纪律：CLOSE-02 全路径显式 GroupStyleError；不静默吞异常。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from ..config import Config, get_logger

log = get_logger()


class GroupStyleError(Exception):
    """群风格存储/读写失败（显式，CLOSE-02）。"""


def _resolve_db_path() -> str:
    env = str(getattr(Config, "GROUP_STYLE_DB", "") or "").strip()
    if env:
        return env
    from ..history import DB_PATH as HISTORY_DB
    return os.path.join(os.path.dirname(HISTORY_DB), "qq_group_style.db")


class GroupStyleStore:
    """per-scope 群风格画像存储。"""

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
                    "CREATE TABLE IF NOT EXISTS group_styles ("
                    "  scope TEXT PRIMARY KEY,"
                    "  atmosphere TEXT NOT NULL DEFAULT '',"
                    "  memes TEXT NOT NULL DEFAULT '[]',"
                    "  taboo TEXT NOT NULL DEFAULT '[]',"
                    "  sample_count INTEGER NOT NULL DEFAULT 0,"
                    "  updated_at REAL NOT NULL DEFAULT 0"
                    ")"
                )
        except Exception as e:  # noqa: BLE001
            raise GroupStyleError("群风格库初始化失败（%s）" % type(e).__name__)

    @staticmethod
    def _loads(s: str, default):
        try:
            v = json.loads(s) if s else default
            return v if v is not None else default
        except (ValueError, TypeError):
            return default

    def load(self, scope: str) -> dict:
        """返回 {atmosphere, memes:[], taboo:[], sample_count, updated_at}。

        不存在 → 返回空画像（不报错，调用方按空处理）。
        """
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT atmosphere, memes, taboo, sample_count, updated_at "
                    "FROM group_styles WHERE scope = ?", (scope,)
                ).fetchone()
        except Exception as e:  # noqa: BLE001
            raise GroupStyleError("群风格读取失败（%s）" % type(e).__name__)
        if row is None:
            return {"atmosphere": "", "memes": [], "taboo": [],
                    "sample_count": 0, "updated_at": 0.0}
        return {
            "atmosphere": row[0] or "",
            "memes": self._loads(row[1], []),
            "taboo": self._loads(row[2], []),
            "sample_count": int(row[3] or 0),
            "updated_at": float(row[4] or 0.0),
        }

    def save(self, scope: str, *, atmosphere: str = "", memes: list | None = None,
             taboo: list | None = None, sample_count: int = 0) -> None:
        """upsert 全量画像。memes/taboo 为字符串列表。"""
        memes = memes or []
        taboo = taboo or []
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO group_styles "
                    "(scope, atmosphere, memes, taboo, sample_count, updated_at) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(scope) DO UPDATE SET "
                    "atmosphere=excluded.atmosphere, memes=excluded.memes, "
                    "taboo=excluded.taboo, sample_count=excluded.sample_count, "
                    "updated_at=excluded.updated_at",
                    (scope, atmosphere, json.dumps(memes, ensure_ascii=False),
                     json.dumps(taboo, ensure_ascii=False), int(sample_count),
                     time.time()),
                )
        except Exception as e:  # noqa: BLE001
            raise GroupStyleError("群风格写入失败（%s）" % type(e).__name__)

    def add_item(self, scope: str, field: str, text: str) -> int:
        """向 memes/taboo 追加一项（去重）。返回追加后该字段项数。"""
        if field not in ("memes", "taboo"):
            raise GroupStyleError("未知风格字段：%s" % field)
        cur = self.load(scope)
        items = list(cur.get(field) or [])
        t = (text or "").strip()
        if not t:
            raise GroupStyleError("空内容不可追加")
        if t in items:
            return len(items)  # 已存在，幂等
        items.append(t)
        self.save(scope, atmosphere=cur["atmosphere"],
                  memes=items if field == "memes" else cur.get("memes"),
                  taboo=items if field == "taboo" else cur.get("taboo"),
                  sample_count=cur["sample_count"])
        return len(items)

    def remove_item(self, scope: str, field: str, text: str) -> int:
        """从 memes/taboo 删除一项（按精确匹配）。返回删除后该字段项数。"""
        if field not in ("memes", "taboo"):
            raise GroupStyleError("未知风格字段：%s" % field)
        cur = self.load(scope)
        items = [x for x in (cur.get(field) or []) if x != text]
        self.save(scope, atmosphere=cur["atmosphere"],
                  memes=items if field == "memes" else cur.get("memes"),
                  taboo=items if field == "taboo" else cur.get("taboo"),
                  sample_count=cur["sample_count"])
        return len(items)

    def reset(self, scope: str) -> None:
        """清空某 scope 的风格画像（手动重置）。"""
        try:
            with self._conn() as con:
                con.execute("DELETE FROM group_styles WHERE scope = ?", (scope,))
        except Exception as e:  # noqa: BLE001
            raise GroupStyleError("群风格重置失败（%s）" % type(e).__name__)


#: 模块级单例
group_style_store = GroupStyleStore()
