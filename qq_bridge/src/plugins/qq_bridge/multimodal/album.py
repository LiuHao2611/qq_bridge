# -*- coding: utf-8 -*-
"""media.album —— 图片/媒体档案与历史召回（M4，2026-10-20）。

需求（dev_plan M4）：历史图召回 —— 「看看我之前那张图」「我发过什么图」。

与既有 gallery.py 的分工（不重复）：
  - `gallery.py` = **t2i 生成结果**的相册（编号回看/翻页，生图链路专用）；
  - `album.py` = **会话里收发过的图**的档案（who/when/what，带视觉描述，
    供「我之前发的那张图是啥」这类回忆型召回）。

存储：data/media_album.json（原子写 + 滚动清理 OPS-02）。
每条：{id, scope, user_id, kind(image/video/voice), path或url, caption
（视觉描述，可空）, ts}。

召回：按 scope 最近 N 条；可按用户过滤；「第几个」按时间倒序编号
（与 gallery 的「第 N 张」口径一致：1 = 最新）。

纪律：失败显式 AlbumError；不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from ..config import Config, get_logger, jlog

log = get_logger()

#: 档案保留上限（超出滚动清理，OPS-02）
MAX_ALBUM = 1000


class AlbumError(Exception):
    """显式化的媒体档案失败。"""


class MediaAlbum:
    """会话媒体档案（单例见 media_album）。"""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from ..queue import DATA_DIR
            path = DATA_DIR / "media_album.json"
        self.path = Path(path)
        self._items: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8")) or []
            else:
                data = []
            self._items = [self._norm(d) for d in data if isinstance(d, dict)]
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("album_load_failed", path=str(self.path),
                             error=type(e).__name__))
            self._items = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            raise AlbumError("媒体档案落盘失败（%s）" % type(e).__name__)

    @staticmethod
    def _norm(d: dict) -> dict:
        d.setdefault("id", uuid.uuid4().hex[:8])
        d.setdefault("scope", "")
        d.setdefault("user_id", 0)
        d.setdefault("kind", "image")
        d.setdefault("ref", "")       # 本地路径或 URL
        d.setdefault("caption", "")   # 视觉描述（看图后回填）
        d.setdefault("ts", time.time())
        return d

    # ---------------- 写入 ----------------
    def add(self, scope: str, ref: str, kind: str = "image",
            user_id: int = 0, caption: str = "", ts: float | None = None) -> dict:
        ref = (ref or "").strip()
        if not ref:
            raise AlbumError("媒体引用不能为空")
        if kind not in ("image", "video", "voice", "file"):
            raise AlbumError("媒体类型只能是 image/video/voice/file")
        item = self._norm({"id": uuid.uuid4().hex[:8], "scope": scope,
                           "user_id": int(user_id or 0), "kind": kind,
                           "ref": ref[:500], "caption": caption[:500],
                           "ts": time.time() if ts is None else float(ts)})
        self._items.append(item)
        self._save()
        log.info(jlog("album_added", aid=item["id"], scope=scope, kind=kind))
        return item

    def set_caption(self, aid: str, caption: str) -> bool:
        """回填视觉描述（看图后更新，供后续召回）。"""
        it = self.get(aid)
        if not it:
            return False
        it["caption"] = (caption or "")[:500]
        self._save()
        return True

    # ---------------- 查询 ----------------
    def get(self, aid: str) -> dict | None:
        aid = (aid or "").strip()
        return next((d for d in self._items if d.get("id") == aid), None)

    def recent(self, scope: str, limit: int = 10, user_id: int | None = None,
               kind: str | None = None) -> list[dict]:
        """最近媒体（时间倒序：第 1 个 = 最新）。"""
        items = [d for d in self._items if d.get("scope") == scope]
        if user_id is not None:
            items = [d for d in items if int(d.get("user_id") or 0) == int(user_id)]
        if kind:
            items = [d for d in items if d.get("kind") == kind]
        items.sort(key=lambda d: float(d.get("ts") or 0), reverse=True)
        return items[:max(1, int(limit))]

    def pick(self, scope: str, n: int, user_id: int | None = None) -> dict | None:
        """按「第 N 个」（1 = 最新）取一条。越界返回 None（显式，不猜）。"""
        items = self.recent(scope, limit=MAX_ALBUM, user_id=user_id)
        if 1 <= int(n) <= len(items):
            return items[int(n) - 1]
        return None

    def format_list(self, scope: str, limit: int = 10,
                    user_id: int | None = None) -> str:
        """人话列表（/看图 列表）。"""
        items = self.recent(scope, limit=limit, user_id=user_id, kind="image")
        if not items:
            return "这个会话还没存过图片喵"
        lines = ["最近的图片（1 = 最新）："]
        for i, it in enumerate(items, 1):
            cap = ("「%s」" % it["caption"][:30]) if it.get("caption") else "（没描述）"
            lines.append("%d. %s %s" % (
                i, time.strftime("%m-%d %H:%M", time.localtime(float(it.get("ts") or 0))),
                cap))
        return "\n".join(lines)

    # ---------------- 清理 ----------------
    def gc(self) -> int:
        """滚动清理（保留最近 MAX_ALBUM 条）。幂等。"""
        if len(self._items) <= MAX_ALBUM:
            return 0
        self._items.sort(key=lambda d: float(d.get("ts") or 0))
        drop = len(self._items) - MAX_ALBUM
        self._items = self._items[drop:]
        self._save()
        log.info(jlog("album_gc", dropped=drop))
        return drop


#: 模块级单例（单测各自 new 隔离）
media_album = MediaAlbum()
