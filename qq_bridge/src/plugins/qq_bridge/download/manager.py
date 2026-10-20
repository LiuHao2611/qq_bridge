# -*- coding: utf-8 -*-
"""download.manager —— 下载任务编排（M7 下载媒体中心，2026-10-20）。

职责（DownloadBot / StreamVault 范式本地化）：
  1. 链接分类与分发：http/ftp → aria2；磁力 → aria2 + BT 智能选文件；
     本地 .torrent → aria2；视频平台链接 → yt-dlp；
  2. BT 智能选时序编排（DownloadBot Aria2.go 同款）：
     磁力以 paused 添加（aria2 的 BT 元数据获取不受暂停影响）→ 轮询
     tellStatus 等元数据（files 非空）→ pause → btselect 挑文件 →
     changeOption select-file → unpause；
  3. 完成检测：后台循环轮询 tellStopped，发现新 complete 的 gid →
     on_complete 回调（调用方负责 QQ 回传，DL_NOTIFY 控制）；
  4. bridge 侧任务登记（data/downloads.json）：关联 scope/发起人/文件，
     供「列表 / 发我 N / 取消 N」与重启后追踪；aria2 是下载真相源，
     本登记是业务视图。

纪律：
  - CLOSE-02：RPC 未开/下载失败 → 显式错误（含开启指引），不返回假成功；
  - CLOSE-01：bridge 侧登记状态机 pending→running→complete/failed/canceled；
  - OPS-02：登记文件原子写（tmp+replace），completed 记录滚动清理；
  - 本模块不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Config, get_logger, jlog
from .aria2 import Aria2Client, Aria2Error, aria2_client as _default_aria2
from .btselect import SELECT_LARGEST, apply_strategy, describe_selection
from .ytdlp import YtDlpDownloader, YtDlpError, ytdlp_downloader as _default_ytdlp

log = get_logger()

#: 视频平台域名特征（命中走 yt-dlp；其余 http 走 aria2）
_VIDEO_HOSTS = ("bilibili.com", "b23.tv", "bilivideo.com", "douyin.com",
                "v.douyin.com", "kuaishou.com", "xiaohongshu.com",
                "youtube.com", "youtu.be", "twitter.com", "x.com",
                "tiktok.com", "instagram.com")

#: 链接类型
KIND_HTTP = "http"
KIND_MAGNET = "magnet"
KIND_TORRENT = "torrent"
KIND_VIDEO = "video"

MAGNET_RE = re.compile(r"^magnet:\?.*", re.I)
TORRENT_RE = re.compile(r"\.torrent$", re.I)


def classify(url: str) -> str:
    """链接分类（显式规则，不猜）。返回 KIND_* 之一；空串 = 无法识别。"""
    u = (url or "").strip()
    if not u:
        return ""
    if MAGNET_RE.match(u):
        return KIND_MAGNET
    if TORRENT_RE.search(u.split("?", 1)[0]):
        return KIND_TORRENT
    low = u.lower()
    if low.startswith(("http://", "https://", "ftp://")):
        if any(h in low for h in _VIDEO_HOSTS):
            return KIND_VIDEO
        return KIND_HTTP
    return ""


class DownloadError(Exception):
    """显式化的下载编排失败。"""


class DownloadManager:
    """下载任务编排器（单例见 download_manager）。"""

    #: bridge 侧登记状态（CLOSE-01 收敛终态）
    ST_PENDING = "pending"      # 已提交，等 aria2/ytdlp 接手
    ST_RUNNING = "running"
    ST_COMPLETE = "complete"
    ST_FAILED = "failed"
    ST_CANCELED = "canceled"

    def __init__(self, store_path: Path | None = None,
                 aria2: Aria2Client | None = None,
                 ytdlp: YtDlpDownloader | None = None,
                 on_complete: Callable[[dict], Awaitable[None]] | None = None) -> None:
        from ..queue import DATA_DIR
        self.path = Path(store_path) if store_path else (DATA_DIR / "downloads.json")
        self.aria2 = aria2 or _default_aria2
        self.ytdlp = ytdlp or _default_ytdlp
        self.on_complete = on_complete
        self._items: list[dict] = []
        self._loop_task: "asyncio.Task | None" = None
        self._stopped = True
        #: 已通知过完成的 aria2 gid 集合（防轮询重复通知）
        self._notified: set[str] = set()
        #: 等元数据的 BT 任务：gid → 登记 id
        self._bt_waiting: dict[str, str] = {}
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8")) or []
            else:
                data = []
            self._items = [self._norm(d) for d in data if isinstance(d, dict)]
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("downloads_load_failed", path=str(self.path),
                             error=type(e).__name__))
            self._items = []

    def _save(self) -> None:
        """原子写。失败显式抛错（CLOSE-02：不假装存上了）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            raise DownloadError("下载登记落盘失败（%s）" % type(e).__name__)

    @staticmethod
    def _norm(d: dict) -> dict:
        d.setdefault("id", uuid.uuid4().hex[:8])
        d.setdefault("kind", KIND_HTTP)
        d.setdefault("gid", "")
        d.setdefault("scope", "")
        d.setdefault("user_id", 0)
        d.setdefault("group_id", 0)
        d.setdefault("channel", "napcat")
        d.setdefault("name", "")
        d.setdefault("url", "")
        d.setdefault("filepath", "")
        d.setdefault("size", 0)
        d.setdefault("status", DownloadManager.ST_PENDING)
        d.setdefault("created_at", time.time())
        d.setdefault("finished_at", 0.0)
        d.setdefault("error", "")
        return d

    # ---------------- 查询 ----------------
    def list(self, scope: str, active_only: bool = True) -> list[dict]:
        """本会话的下载（默认只列未完成；按创建时间倒序）。"""
        out = [d for d in self._items
               if d.get("scope") == scope
               and (not active_only or d.get("status") in
                    (self.ST_PENDING, self.ST_RUNNING))]
        return sorted(out, key=lambda d: float(d.get("created_at") or 0), reverse=True)

    def get(self, did: str) -> dict | None:
        did = (did or "").strip()
        return next((d for d in self._items if d.get("id") == did), None)

    def get_by_gid(self, gid: str) -> dict | None:
        return next((d for d in self._items if d.get("gid") == gid), None)

    def resolve_ref(self, scope: str, ref: str) -> dict | None:
        """「发我/取消 第 N 个」的序号或编号 → 登记项（序号按创建倒序）。"""
        ref = (ref or "").strip()
        if not ref:
            return None
        item = self.get(ref)
        if item and item.get("scope") == scope:
            return item
        if ref.isdigit():
            items = self.list(scope, active_only=False)
            idx = int(ref)
            if 1 <= idx <= len(items):
                return items[idx - 1]
        return None

    # ---------------- 提交下载 ----------------
    async def submit(self, url: str, scope: str, user_id: int = 0,
                     group_id: int = 0, channel: str = "napcat",
                     bt_strategy: str | None = None) -> dict:
        """识别链接类型并提交。返回登记项（失败显式抛错）。"""
        text = (url or "").strip()
        kind = classify(text)
        if not kind:
            raise DownloadError(
                "这链接本喵不认识喵——支持 http/https/ftp 直链、magnet: 磁力、"
                "本地 .torrent 路径、以及 B站/抖音/YouTube 等视频页链接")
        item = self._norm({
            "id": uuid.uuid4().hex[:8], "kind": kind, "url": text[:2000],
            "scope": scope, "user_id": int(user_id or 0),
            "group_id": int(group_id or 0), "channel": str(channel or "napcat"),
            "status": self.ST_PENDING, "created_at": time.time(),
        })
        try:
            if kind == KIND_MAGNET:
                await self._submit_bt(item, text, bt_strategy)
            elif kind == KIND_TORRENT:
                await self._submit_torrent(item, text)
            elif kind == KIND_VIDEO:
                # ytdlp 同步链路：直接 await，完成后即登记 complete
                await self._run_ytdlp(item, text)
            else:
                await self._submit_http(item, text)
        except Exception:
            # 提交阶段失败：登记 failed 并落盘，错误显式上抛
            if item.get("status") not in (self.ST_COMPLETE,):
                item["status"] = self.ST_FAILED
                item["finished_at"] = time.time()
                item["error"] = "提交失败"
                self._items.append(item)
                self._save()
            raise
        if not any(d.get("id") == item["id"] for d in self._items):
            self._items.append(item)
        self._save()
        log.info(jlog("download_submitted", did=item["id"], kind=kind,
                      scope=scope, gid=item.get("gid", "")))
        return item

    async def _submit_http(self, item: dict, url: str) -> None:
        gid = await self.aria2.add_uri([url])
        item["gid"] = gid
        item["status"] = self.ST_RUNNING

    async def _submit_torrent(self, item: dict, path: str) -> None:
        p = Path(path)
        if not p.exists():
            raise DownloadError("种子文件不存在：%s" % path)
        # 种子同样走 BT 智能选（先 paused 添加等元数据）
        gid = await self.aria2.add_torrent(p, {"pause": "true"})
        item["gid"] = gid
        item["status"] = self.ST_RUNNING
        self._bt_waiting[gid] = item["id"]

    async def _submit_bt(self, item: dict, magnet: str,
                         strategy: str | None = None) -> None:
        """磁力：paused 添加 → 登记等元数据（元数据获取不受 pause 影响）。"""
        strategy = strategy or str(getattr(Config, "DL_BT_SELECT", "") or SELECT_LARGEST)
        gid = await self.aria2.add_uri([magnet], {"pause": "true"})
        item["gid"] = gid
        item["status"] = self.ST_RUNNING
        item["bt_strategy"] = strategy
        self._bt_waiting[gid] = item["id"]

    async def _run_ytdlp(self, item: dict, url: str) -> None:
        """yt-dlp 同步下载链路（await 完成即登记 complete）。"""
        item["status"] = self.ST_RUNNING
        result = await self.ytdlp.download(url)
        item["name"] = result.get("title", "")
        item["filepath"] = result.get("filepath", "")
        item["status"] = self.ST_COMPLETE
        item["finished_at"] = time.time()
        try:
            item["size"] = Path(item["filepath"]).stat().st_size
        except Exception:  # noqa: BLE001 — 文件大小拿不到不阻断登记
            item["size"] = 0
        # ytdlp 完成也走同一通知出口（StreamVault 的 Webhook 等价物）
        await self._notify(item)

    # ---------------- BT 智能选（轮询执行） ----------------
    async def _process_bt_waiting(self) -> None:
        """扫描等元数据的 BT 任务：元数据到位 → 选文件 → unpause。"""
        if not self._bt_waiting:
            return
        raw = getattr(Config, "DL_BT_META_TIMEOUT", 180.0)
        timeout = 180.0 if raw is None else float(raw)
        for gid, did in list(self._bt_waiting.items()):
            item = self.get(did)
            if item is None or item.get("status") != self.ST_RUNNING:
                self._bt_waiting.pop(gid, None)
                continue
            # 元数据等待超时 → 显式失败（禁无限等）
            if time.time() - float(item.get("created_at") or 0) > timeout:
                self._bt_waiting.pop(gid, None)
                item["status"] = self.ST_FAILED
                item["finished_at"] = time.time()
                item["error"] = "BT 元数据等待超时（%ds，网络里没种子节点？）" % int(timeout)
                self._save()
                log.warning(jlog("bt_meta_timeout", did=did, gid=gid))
                continue
            try:
                files = await self.aria2.get_files(gid)
            except Aria2Error as e:
                # 元数据还没好时 getFiles 可能报错——不视为任务失败，下轮再看
                log.info(jlog("bt_meta_waiting", did=did, gid=gid, detail=str(e)[:120]))
                continue
            real_files = [f for f in files if str(f.get("path") or "").strip()]
            if not real_files:
                continue  # 元数据未到位
            # 元数据到位：执行智能选
            try:
                await self._select_bt_files(item, gid, real_files)
            except Aria2Error as e:
                item["status"] = self.ST_FAILED
                item["finished_at"] = time.time()
                item["error"] = "BT 选文件失败：%s" % str(e)[:160]
                log.warning(jlog("bt_select_failed", did=did, gid=gid,
                                 detail=str(e)[:160]))
            self._bt_waiting.pop(gid, None)

    async def _select_bt_files(self, item: dict, gid: str, files: list[dict]) -> None:
        strategy = item.get("bt_strategy") or SELECT_LARGEST
        select_opt, indices = apply_strategy(files, strategy)
        if not select_opt:
            # 元数据没给文件大小 → 无法智能选，显式全选并说明（不静默卡死）
            log.warning(jlog("bt_select_empty", did=item["id"], gid=gid))
            await self.aria2.unpause(gid)
            item["error"] = "元数据未提供文件大小，已全选（下次可换种子）"
            return
        await self.aria2.change_option(gid, {"select-file": select_opt})
        await self.aria2.unpause(gid)
        log.info(jlog("bt_selected", did=item["id"], gid=gid,
                      strategy=strategy, picked=len(indices),
                      detail=describe_selection(files, indices)[:200]))

    # ---------------- 完成检测（轮询） ----------------
    async def _poll_aria2_completions(self) -> None:
        """tellStopped 里新出现的 complete → 通知 + 登记终态。"""
        try:
            stopped = await self.aria2.tell_stopped(0, 50)
        except Aria2Error:
            raise  # 显式上抛，由 loop 记日志（不静默返回）
        for st in stopped:
            gid = str(st.get("gid") or "")
            status = str(st.get("status") or "")
            if not gid or gid in self._notified:
                continue
            item = self.get_by_gid(gid)
            if item is None:
                self._notified.add(gid)  # 非 bridge 登记的任务（手动加的）不通知
                continue
            if status == "complete":
                self._notified.add(gid)
                await self._finish_aria2_item(item, st)
            elif status == "error":
                self._notified.add(gid)
                item["status"] = self.ST_FAILED
                item["finished_at"] = time.time()
                item["error"] = "aria2 下载出错：%s" % str(
                    st.get("errorMessage") or "未知错误")[:200]
                self._save()
                log.warning(jlog("download_failed", did=item["id"], gid=gid,
                                 err=item["error"]))

    async def _finish_aria2_item(self, item: dict, st: dict) -> None:
        """aria2 任务完成：补全文件信息 + 通知。"""
        try:
            files = await self.aria2.get_files(item["gid"])
        except Aria2Error as e:
            files = []
            log.warning(jlog("download_complete_getfiles_failed",
                             did=item["id"], detail=str(e)[:160]))
        picked = [f for f in files if str(f.get("selected") or "").lower() == "true"
                  and str(f.get("path") or "").strip()]
        if picked:
            # BT 智能选后通常只有一个文件；多文件取合计
            item["name"] = str(picked[0].get("path") or "").replace("\\", "/").split("/")[-1]
            if len(picked) > 1:
                item["name"] += " 等 %d 个文件" % len(picked)
            item["filepath"] = str(picked[0].get("path") or "")
            try:
                item["size"] = sum(float(f.get("length") or 0) for f in picked)
            except (TypeError, ValueError):
                item["size"] = 0
        else:
            item["name"] = str(st.get("files") and
                               (st["files"][0].get("path") or "").split("/")[-1]
                               or "aria2 任务")
        item["status"] = self.ST_COMPLETE
        item["finished_at"] = time.time()
        self._save()
        log.info(jlog("download_complete", did=item["id"], gid=item["gid"],
                      name=item["name"], size=int(item["size"])))
        await self._notify(item)

    async def _notify(self, item: dict) -> None:
        """完成通知（回调注入；DL_NOTIFY 关则不通知——限流非功能降级）。"""
        if self.on_complete is None:
            return
        if not bool(getattr(Config, "DL_NOTIFY", True)):
            return
        try:
            await self.on_complete(item)
        except Exception as e:  # noqa: BLE001 — 通知失败不阻断状态落地
            log.warning(jlog("download_notify_failed", did=item["id"],
                             error=type(e).__name__, detail=str(e)[:160]))

    # ---------------- 取消 / 删除 ----------------
    async def cancel(self, did: str, scope: str, remove_files: bool = False) -> dict:
        """取消任务（默认只移除任务不删文件；remove_files 高危，调用方确认流）。"""
        item = self.get(did)
        if not item or item.get("scope") != scope:
            raise DownloadError("下载 %s 不存在（只能操作本会话的）" % did)
        if item.get("status") in (self.ST_COMPLETE, self.ST_CANCELED, self.ST_FAILED):
            raise DownloadError("下载 %s 已经是 %s 状态" % (did, item.get("status")))
        gid = item.get("gid") or ""
        if item.get("kind") == KIND_VIDEO or not gid:
            # ytdlp 同步任务跑在 to_thread 里，无法中途取消——已完成/失败态
            # 由 _run_ytdlp 落地；这里只可能是刚提交尚未开始的窗口
            item["status"] = self.ST_CANCELED
            item["finished_at"] = time.time()
            self._save()
            return item
        try:
            await self.aria2.remove(gid)
        except Aria2Error as e:
            raise DownloadError("取消失败：%s" % str(e)[:200])
        item["status"] = self.ST_CANCELED
        item["finished_at"] = time.time()
        self._notified.add(gid)
        self._bt_waiting.pop(gid, None)
        self._save()
        log.info(jlog("download_canceled", did=did, gid=gid,
                      remove_files=remove_files))
        return item

    async def pause(self, did: str, scope: str) -> dict:
        item = self.get(did)
        if not item or item.get("scope") != scope:
            raise DownloadError("下载 %s 不存在（只能操作本会话的）" % did)
        gid = item.get("gid") or ""
        if not gid:
            raise DownloadError("yt-dlp 任务不支持暂停（跑起来就一口气下完）")
        await self.aria2.pause(gid)
        log.info(jlog("download_paused", did=did, gid=gid))
        return item

    async def unpause(self, did: str, scope: str) -> dict:
        item = self.get(did)
        if not item or item.get("scope") != scope:
            raise DownloadError("下载 %s 不存在（只能操作本会话的）" % did)
        gid = item.get("gid") or ""
        if not gid:
            raise DownloadError("yt-dlp 任务不支持暂停/继续")
        await self.aria2.unpause(gid)
        log.info(jlog("download_resumed", did=did, gid=gid))
        return item

    # ---------------- 后台循环 ----------------
    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._stopped = False
        # 重启恢复：aria2 里 running 的已登记任务重新纳入完成检测
        for d in self._items:
            if d.get("gid") and d.get("status") == self.ST_RUNNING:
                self._bt_waiting[d["gid"]] = d["id"]
        self._loop_task = asyncio.ensure_future(self._loop())
        log.info(jlog("download_loop_started"))

    async def stop(self) -> None:
        self._stopped = True
        t = self._loop_task
        self._loop_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        log.info(jlog("download_loop_stopped"))

    async def _loop(self) -> None:
        raw = getattr(Config, "DL_POLL_INTERVAL", 5.0)
        interval = max(1.0, 5.0 if raw is None else float(raw))
        while not self._stopped:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 单 tick 失败不杀循环
                log.warning(jlog("download_tick_failed", error=type(e).__name__,
                                 detail=str(e)[:160]))
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        await self._process_bt_waiting()
        await self._poll_aria2_completions()
        self._gc()

    def _gc(self) -> None:
        """登记滚动清理（OPS-02/03）：终态记录保留最近 100 条。"""
        done = [d for d in self._items
                if d.get("status") in (self.ST_COMPLETE, self.ST_FAILED, self.ST_CANCELED)]
        if len(done) > 100:
            done.sort(key=lambda d: float(d.get("finished_at") or 0))
            drop = {d.get("id") for d in done[:len(done) - 100]}
            if drop:
                self._items = [d for d in self._items if d.get("id") not in drop]
                try:
                    self._save()
                except DownloadError as e:
                    log.warning(jlog("downloads_gc_save_failed", detail=str(e)[:160]))


#: 模块级单例（插件入口注入 on_complete；单测各自 new 隔离）
download_manager = DownloadManager()
