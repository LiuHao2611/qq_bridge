# -*- coding: utf-8 -*-
"""infer_listen —— 监听主项目生图任务（2026-09-13）。

职责：从「推理监控」页面（http://127.0.0.1:8099/tasks）所用的统一任务 API
`GET {TASK_MONITOR_BASE}/tasks/unified?type=image` 拉取主项目后台的生图任务，
让 QQ 对话里直接知道「项目后台在跑什么任务、生成什么图片」。

两个出口：
  * 查询（`snapshot()`）——QQ 命令「主项目任务 / 后台 / 在跑啥」调用，
    返回正在跑 / 排队中 / 最近完成的生图任务摘要 + 结果图；
  * 主动监听（`_loop()`）——开启后每 TASK_LISTEN_INTERVAL 轮询，主项目一旦
    *开始生图*（running/processing）→ 私聊主人「主项目开始生图：<提示词>」；
    *生图完成*（succeeded）→ 私聊「生图完成：<提示词>」并补发结果图。

红线（与 monitor 同）：
  * 只经 HTTP 调主项目，绝不 import server/；
  * 动态读 Config（热生效：改 TASK_LISTEN_* 不用重启 bridge）；
  * 任何异常只记日志，绝不因监听失败影响主流程（CLOSE-02）；
  * 幂等：同一 task_id 同事件只提醒一次（_seen 带 TTL 自动清理，OPS-02）。
"""
from __future__ import annotations

import asyncio
import time

import httpx

log = None


def _log():
    global log
    if log is None:
        try:
            from ..config import get_logger
            log = get_logger()
        except Exception:  # pragma: no cover
            import logging
            log = logging.getLogger("qq_bridge.infer_listen")
    return log


def _cfg(name: str, default):
    """运行时读 Config（热生效：改配置不用重启 bridge）。"""
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:
        return default


#: 生图任务状态 → 中文（兼容 canceled / cancelled 两种拼写）
_STATUS_ZH = {
    "queued": "排队中", "retrying": "重试中",
    "running": "生成中", "processing": "生成中",
    "succeeded": "已完成", "failed": "失败",
    "canceled": "已取消", "cancelled": "已取消",
}


def _status_zh(s: str) -> str:
    return _STATUS_ZH.get(str(s or "").lower(), str(s or "未知"))


def _abs_url(url: str) -> str:
    """平台返回的相对 /file?... 拼成绝对地址（NapCat 才能下载）。"""
    s = (url or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://", "base64://", "file:///")):
        return s
    base = _cfg("MANJU_FILE_BASE", "http://127.0.0.1:7788").rstrip("/")
    if s.startswith("/"):
        return base + s
    return s


class InferListener:
    """监听主项目生图任务。notifier: async callable(text, images=None) —— 私聊主人。"""

    #: 视为「正在生成」的状态
    RUNNING = ("running", "processing")
    #: 视为「已完成」的状态
    DONE = ("succeeded",)

    def __init__(self, notifier=None, transport=None) -> None:
        self.notifier = notifier
        self._transport = transport
        self._running = False
        self._task: asyncio.Task | None = None
        #: task_id -> 上次已知状态（用于检测状态跃迁，实现幂等监听）
        self._states: dict[str, str] = {}
        #: 已告警过的关键（task_id + 事件），防重复刷屏
        self._alerted: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ---------------- 配置 ----------------
    @property
    def base(self) -> str:
        b = _cfg("TASK_MONITOR_BASE", "http://127.0.0.1:8099/api/v1")
        return str(b).rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(_cfg("TASK_LISTEN_ENABLE", False))

    @property
    def interval(self) -> float:
        return max(5.0, float(_cfg("TASK_LISTEN_INTERVAL", 15.0) or 15.0))

    @property
    def limit(self) -> int:
        return max(1, min(50, int(_cfg("TASK_LISTEN_LIMIT", 10) or 10)))

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # 启动即拉一次，把既有任务灌进 _states，避免开监听瞬间把历史任务全当成「新开始」
        try:
            items = await self._fetch("image", size=50)
            async with self._lock:
                for it in items:
                    tid = str(it.get("task_id") or "")
                    if tid:
                        self._states[tid] = str(it.get("status") or "")
        except Exception as e:  # noqa: BLE001
            _log().warning("infer_listen_seed_failed error=%s detail=%s"
                           % (type(e).__name__, str(e)[:160]))
        self._task = asyncio.create_task(self._loop(), name="qq-infer-listen")
        _log().info("infer_listen_started base=%s enabled=%s interval=%.0fs"
                    % (self.base, self.enabled, self.interval))

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    # ---------------- 主循环 ----------------
    async def _loop(self) -> None:
        while self._running:
            try:
                if self.enabled:
                    await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 监听永不因异常退出
                _log().warning("infer_listen_check_error error=%s detail=%s"
                               % (type(e).__name__, str(e)[:200]))
            await asyncio.sleep(self.interval)

    async def _check_once(self) -> None:
        self._gc_alerted()
        items = await self._fetch("image", size=50)
        now: dict[str, str] = {}
        for it in items:
            tid = str(it.get("task_id") or "")
            if not tid:
                continue
            now[tid] = str(it.get("status") or "").lower()

        async with self._lock:
            prev = self._states
            self._states = now
            # 新出现的 / 状态跃迁的 task —— 与 prev 对比
            for tid, st in now.items():
                pst = prev.get(tid)
                if st in self.RUNNING and pst not in self.RUNNING:
                    await self._alert_start(tid, it_from(items, tid))
                elif st in self.DONE and pst not in self.DONE:
                    await self._alert_done(tid, it_from(items, tid))

    def _gc_alerted(self) -> None:
        ttl = 3600 * 6
        now = time.time()
        for k in [k for k, ts in self._alerted.items() if now - ts > ttl]:
            self._alerted.pop(k, None)

    # ---------------- 监听告警 ----------------
    async def _alert_start(self, tid: str, it: dict) -> None:
        key = "start:" + tid
        if key in self._alerted:
            return
        self._alerted[key] = time.time()
        prompt = str(it.get("prompt_preview") or it.get("name") or "")[:80]
        model = str(it.get("model_id") or "")
        extra = ""
        if model:
            extra = "\n模型：%s" % model
        await self._notify("主项目开始生图：%s%s\n任务号 %s" % (prompt, extra, tid[:8]))

    async def _alert_done(self, tid: str, it: dict) -> None:
        key = "done:" + tid
        if key in self._alerted:
            return
        self._alerted[key] = time.time()
        prompt = str(it.get("prompt_preview") or it.get("name") or "")[:80]
        img = _abs_url(it.get("result_url") or it.get("thumbnail_url") or "")
        text = "主项目生图完成：%s\n任务号 %s" % (prompt, tid[:8])
        await self._notify(text, images=[img] if img else None)

    async def _notify(self, text: str, images=None) -> None:
        if not self.notifier:
            return
        try:
            await self.notifier(text, images)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _log().warning("infer_listen_notify_failed error=%s detail=%s"
                           % (type(e).__name__, str(e)[:160]))

    # ---------------- 查询（QQ 命令用）----------------
    async def snapshot(self, limit: int | None = None) -> dict:
        """返回主项目生图任务快照（用于「主项目任务」查询）。

        { running:[...], queued:[...], recent:[...], has_more:bool, total:int }
        每个元素为归一化 dict：task_id/name/status_zh/prompt/progress/model/
        params(尺寸/比例/步数/种子)/result_url(绝对)/error。
        """
        lim = int(limit or self.limit)
        items = await self._fetch("image", size=max(lim, 20))
        running, queued, recent = [], [], []
        for it in items:
            it = self._norm(it)
            st = it["status_zh"]
            if st in ("生成中",):
                running.append(it)
            elif st in ("排队中", "重试中"):
                queued.append(it)
            elif st == "已完成":
                recent.append(it)
        # 最近完成最多展示 lim 条；running/queued 全给（一般不多）
        recent = recent[:lim]
        return {
            "running": running,
            "queued": queued,
            "recent": recent,
            "has_more": False,
            "total": len(items),
        }

    def format_snapshot(self, snap: dict) -> tuple[str, list]:
        """把 snapshot 渲染成 (文本, 待发结果图列表)。"""
        running = snap.get("running") or []
        queued = snap.get("queued") or []
        recent = snap.get("recent") or []
        lines = []
        if running:
            lines.append("【正在生图 %d 张】" % len(running))
            for it in running[:8]:
                lines.append("· %s —— %s（%s%%）" % (
                    it["prompt"][:36] or it["name"][:36],
                    it["status_zh"], it["progress"]))
                if it.get("model"):
                    lines.append("  模型 %s · %s" % (it["model"], it["p_text"]))
        else:
            lines.append("【正在生图】暂无，后台这会儿闲着")
        if queued:
            lines.append("\n【排队中 %d 张】" % len(queued))
            for it in queued[:8]:
                lines.append("· %s" % (it["prompt"][:36] or it["name"][:36]))
        if recent:
            lines.append("\n【最近完成 %d 张】" % len(recent))
            for it in recent[: self.limit]:
                lines.append("· %s（%s）" % (it["prompt"][:36] or it["name"][:36],
                                            it["task_id"][:8]))
        else:
            lines.append("\n【最近完成】无")

        # 结果图：最近完成的（最多 4 张），让用户直接看到后台生成了啥
        imgs = []
        for it in recent[:4]:
            u = it.get("result_url")
            if u:
                imgs.append(u)
        title = "主项目生图任务（在跑 %d · 排队 %d · 最近完成 %d）" % (
            len(running), len(queued), len(recent))
        return title + "\n" + "\n".join(lines), imgs

    # ---------------- 内部 ----------------
    def _norm(self, it: dict) -> dict:
        """归一化单条任务为 QQ 展示友好的字段。"""
        p = it.get("params") or {}
        parts = []
        if p.get("ratio"):
            parts.append("比例 %s" % p["ratio"])
        if p.get("resolution"):
            parts.append("分辨率 %s" % p["resolution"])
        if p.get("steps"):
            parts.append("步数 %s" % p["steps"])
        if p.get("seed") is not None and str(p["seed"]) not in ("-1", "None", ""):
            parts.append("种子 %s" % p["seed"])
        return {
            "task_id": str(it.get("task_id") or "")[:8],
            "name": str(it.get("name") or ""),
            "status_zh": _status_zh(it.get("status")),
            "prompt": str(it.get("prompt_preview") or "")[:80],
            "progress": int(float(it.get("progress") or 0) * (100 if float(it.get("progress") or 0) <= 1 else 1)),
            "model": str(it.get("model_id") or ""),
            "p_text": " · ".join(parts),
            "result_url": _abs_url(it.get("result_url") or it.get("thumbnail_url") or ""),
            "error": str(it.get("error_message") or "")[:120],
        }

    async def _fetch(self, task_type: str = "image", size: int = 20) -> list:
        """拉统一任务列表（仅生图）。返回归一化前的原始 items。"""
        data, err, code = await self._get(
            "/tasks/unified?type=%s&size=%d" % (task_type, size))
        if err:
            return []
        items = (data or {}).get("items") or []
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict)]

    def _client(self, read_timeout: float):
        kw = {"timeout": httpx.Timeout(10.0, read=read_timeout)}
        if self._transport is not None:
            kw["transport"] = self._transport
        return httpx.AsyncClient(**kw)

    async def _get(self, path: str, timeout: float = 10.0):
        try:
            async with self._client(timeout) as cli:
                resp = await cli.get(self.base + path)
            try:
                j = resp.json()
            except Exception:
                return {}, "", resp.status_code
            if resp.status_code >= 400:
                return {}, "HTTP %s" % resp.status_code, resp.status_code
            d = j.get("data") if isinstance(j, dict) else None
            return (d if isinstance(d, dict) else j) or {}, "", resp.status_code
        except Exception as e:  # noqa: BLE001
            return {}, "%s: %s" % (type(e).__name__, str(e)[:120]), 0


def it_from(items: list, tid: str) -> dict:
    for it in items:
        if str(it.get("task_id") or "") == tid:
            return it
    return {}


#: 模块级单例
infer_listener = InferListener()
