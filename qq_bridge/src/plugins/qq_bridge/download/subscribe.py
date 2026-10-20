# -*- coding: utf-8 -*-
"""download.subscribe —— 订阅监控（M7 下载媒体中心，2026-10-20）。

范式参考 StreamVault（2026-10-20 实读 README）：B站收藏夹/UP、抖音作品
列表的**更新监控 + 自动下载 + 完成通知**。

实现取舍（个人单机，禁堆依赖）：
  - **不为每个平台写 API 客户端**——统一用 yt-dlp 的 flat playlist 提取
    （`extract_flat=True`：一次请求只拿 id/title，不逐条解析，轻）；
  - 新条目判定 = 当前列表 id 不在上次记录的 id 集合；
  - 发现新条目 → 提交 yt-dlp 下载（复用 DownloadManager.submit）→
    on_new 回调通知（调用方发 QQ）；
  - 检查间隔默认 1 小时（DL_SUB_INTERVAL），到点才查（yt-dlp 请求有
    成本，且部分平台频烦请求会风控）。

纪律：CLOSE-02（检查失败记日志下轮重试，不杀循环）；OPS-02（登记文件
原子写）；本模块不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Config, get_logger, jlog

log = get_logger()

#: 订阅检查时 playlist 最多回看几条（防巨量收藏夹拖垮请求）
_PLAYLIST_END = 20
#: last_ids 记忆深度（防重复通知的滚动窗口）
_ID_MEMORY = 100


class SubscriptionError(Exception):
    """显式化的订阅业务错误。"""


class SubscriptionStore:
    """订阅登记：data/subscriptions.json 原子持久化。"""

    def __init__(self, path: Path | None = None) -> None:
        from ..queue import DATA_DIR
        self.path = Path(path) if path else (DATA_DIR / "subscriptions.json")
        self._subs: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8")) or []
            else:
                data = []
            self._subs = [self._norm(s) for s in data if isinstance(s, dict)]
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("subscriptions_load_failed", path=str(self.path),
                             error=type(e).__name__))
            self._subs = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._subs, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            raise SubscriptionError("订阅登记落盘失败（%s）" % type(e).__name__)

    @staticmethod
    def _norm(s: dict) -> dict:
        s.setdefault("id", uuid.uuid4().hex[:8])
        s.setdefault("scope", "")
        s.setdefault("user_id", 0)
        s.setdefault("group_id", 0)
        s.setdefault("channel", "napcat")
        s.setdefault("url", "")
        s.setdefault("title", "")
        s.setdefault("platform", "")
        s.setdefault("last_ids", [])
        s.setdefault("enabled", True)
        s.setdefault("created_at", time.time())
        s.setdefault("last_check_at", 0.0)
        s.setdefault("last_new_count", 0)
        return s

    def list(self, scope: str) -> list[dict]:
        return sorted((s for s in self._subs if s.get("scope") == scope),
                      key=lambda s: float(s.get("created_at") or 0))

    def get(self, sid: str) -> dict | None:
        sid = (sid or "").strip()
        return next((s for s in self._subs if s.get("id") == sid), None)

    def add(self, url: str, scope: str, title: str = "", platform: str = "",
            user_id: int = 0, group_id: int = 0, channel: str = "napcat") -> dict:
        url = (url or "").strip()
        if not url:
            raise SubscriptionError("订阅链接不能为空")
        if any(s.get("url") == url and s.get("scope") == scope for s in self._subs):
            raise SubscriptionError("这个会话已经订阅过同一链接了")
        cap_raw = getattr(Config, "DL_SUB_MAX_PER_SCOPE", 5)
        cap = 5 if cap_raw is None else int(cap_raw)
        if len(self.list(scope)) >= cap:
            raise SubscriptionError("每个会话最多订阅 %d 个，先取消几个吧" % cap)
        sub = self._norm({
            "id": uuid.uuid4().hex[:8], "url": url[:2000], "scope": scope,
            "title": (title or "").strip()[:80], "platform": platform,
            "user_id": int(user_id or 0), "group_id": int(group_id or 0),
            "channel": str(channel or "napcat"), "created_at": time.time(),
        })
        self._subs.append(sub)
        self._save()
        log.info(jlog("subscription_added", sid=sub["id"], scope=scope,
                      platform=platform))
        return sub

    def remove(self, sid: str, scope: str) -> dict:
        sub = self.get(sid)
        if not sub or sub.get("scope") != scope:
            raise SubscriptionError("订阅 %s 不存在（只能删本会话的）" % sid)
        self._subs = [s for s in self._subs if s.get("id") != sid]
        self._save()
        log.info(jlog("subscription_removed", sid=sid, scope=scope))
        return sub

    def due(self, now: float | None = None) -> list[dict]:
        """到点该检查的订阅（启用 + 超过间隔）。"""
        now = time.time() if now is None else float(now)
        raw = getattr(Config, "DL_SUB_INTERVAL", 3600.0)
        interval = max(60.0, 3600.0 if raw is None else float(raw))
        out = []
        for s in self._subs:
            if not s.get("enabled"):
                continue
            if now - float(s.get("last_check_at") or 0) >= interval:
                out.append(s)
        return out

    def mark_checked(self, sid: str, new_count: int, new_ids: list[str]) -> None:
        sub = self.get(sid)
        if not sub:
            return
        sub["last_check_at"] = time.time()
        sub["last_new_count"] = int(new_count)
        merged = list(new_ids) + list(sub.get("last_ids") or [])
        sub["last_ids"] = merged[:_ID_MEMORY]
        try:
            self._save()
        except SubscriptionError as e:
            log.warning(jlog("subscription_mark_save_failed", sid=sid,
                             detail=str(e)[:160]))


#: 模块级单例（单测各自 new 隔离）
subscription_store = SubscriptionStore()


class SubscriptionMonitor:
    """订阅监控循环：到期检查 yt-dlp flat playlist → 新条目自动下载。"""

    def __init__(self, store: SubscriptionStore | None = None,
                 submit=None, on_new: Callable[[dict, list], Awaitable[None]] | None = None,
                 ydl_cls: Any = None) -> None:
        self.store = store or subscription_store
        #: submit: async (url, scope, user_id, group_id, channel) -> item
        self.submit = submit
        self.on_new = on_new
        self._ydl_cls = ydl_cls
        self._task: "asyncio.Task | None" = None
        self._stopped = True

    def _resolve_cls(self):
        if self._ydl_cls is not None:
            return self._ydl_cls
        try:
            from yt_dlp import YoutubeDL
        except Exception as e:  # noqa: BLE001
            raise SubscriptionError("yt-dlp 没装（%s）：pip install yt-dlp"
                                    % type(e).__name__)
        return YoutubeDL

    async def check_one(self, sub: dict) -> list[dict]:
        """检查一个订阅，返回新条目列表 [{id,title,url}]（无更新返回空）。"""
        ydl_cls = self._resolve_cls()
        url = sub.get("url", "")
        known = set(sub.get("last_ids") or [])

        def _extract() -> list[dict]:
            opts = {"quiet": True, "no_warnings": True, "noprogress": True,
                    "extract_flat": True, "noplaylist": False,
                    "playlistend": _PLAYLIST_END, "socket_timeout": 60}
            with ydl_cls(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            entries: list[dict] = []
            if isinstance(info, dict):
                if info.get("_type") == "playlist":
                    entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
                else:
                    entries = [info]  # 单条链接（如个人空间只有一个视频时）
            return entries

        try:
            entries = await asyncio.to_thread(_extract)
        except Exception as e:  # noqa: BLE001 — 检查失败显式记录，下轮重试
            log.warning(jlog("subscription_check_failed", sid=sub.get("id", ""),
                             error=type(e).__name__, detail=str(e)[:160]))
            return []

        new_items: list[dict] = []
        new_ids: list[str] = []
        for e in entries:
            eid = str(e.get("id") or "")
            title = str(e.get("title") or "")
            eurl = str(e.get("url") or e.get("webpage_url") or "")
            if eid and eid in known:
                continue
            if eurl:
                new_items.append({"id": eid, "title": title, "url": eurl})
                if eid:
                    new_ids.append(eid)
        self.store.mark_checked(sub["id"], len(new_items), new_ids)
        if new_items:
            log.info(jlog("subscription_new_items", sid=sub["id"],
                          count=len(new_items),
                          titles=";".join(i["title"] for i in new_items)[:200]))
        return new_items

    async def _tick(self) -> None:
        for sub in self.store.due():
            new_items = await self.check_one(sub)
            if not new_items:
                continue
            # 自动下载（限 DL_SUB_AUTO_DOWNLOAD，关=只通知）
            if bool(getattr(Config, "DL_SUB_AUTO_DOWNLOAD", True)) and self.submit:
                for item in new_items:
                    try:
                        await self.submit(item["url"], sub["scope"],
                                          user_id=sub.get("user_id", 0),
                                          group_id=sub.get("group_id", 0),
                                          channel=sub.get("channel", "napcat"))
                    except Exception as e:  # noqa: BLE001 — 单条失败不阻其它
                        log.warning(jlog("subscription_submit_failed",
                                         sid=sub["id"], title=item["title"][:60],
                                         error=type(e).__name__))
            if self.on_new is not None:
                try:
                    await self.on_new(sub, new_items)
                except Exception as e:  # noqa: BLE001
                    log.warning(jlog("subscription_notify_failed", sid=sub["id"],
                                     error=type(e).__name__))

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.ensure_future(self._loop())
        log.info(jlog("subscription_monitor_started"))

    async def stop(self) -> None:
        self._stopped = True
        t = self._task
        self._task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        log.info(jlog("subscription_monitor_stopped"))

    async def _loop(self) -> None:
        # 检查粒度：分钟级足够（实际检查频率由 store.due 的间隔控制）
        while not self._stopped:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 单 tick 失败不杀循环
                log.warning(jlog("subscription_tick_failed",
                                 error=type(e).__name__, detail=str(e)[:160]))
            await asyncio.sleep(60.0)


#: 模块级单例（插件入口注入 submit/on_new；单测各自 new 隔离）
subscription_monitor = SubscriptionMonitor()
