# -*- coding: utf-8 -*-
"""monitor —— 任务监控告警（2026-09-08）。

职责：轮询主项目任务状态，**异常时私聊告警给主人**。

为什么用轮询而不是主项目 WS：
  bridge 是独立进程，接主项目 WS 会引入长连接耦合——主项目一重启，bridge 就要
  处理重连风暴，违背「单向依赖、可独立崩溃」红线。轮询 + 退避更符合定位。

告警项（全部可在「机器人配置 → 常规·任务队列」调）：
  * 任务失败（status=failed）→ 新出现的才告警；
  * 任务超时（活跃任务耗时 > MONITOR_TASK_TIMEOUT）→ 每个 task_id 只告警一次；
  * 平台持续繁忙（队列连续 N 次 IMG_BUSY）→ 达阈值告警一次。

铁律：
  * MONITOR_ENABLE 默认 **false**（不打扰），开启后每 MONITOR_INTERVAL 秒轮询；
  * **幂等**：同一 task_id 同一事件只告警一次（_alerted 带 TTL 自动清理，OPS-02）；
  * 只经 HTTP 调主项目，不 import 主项目模块；
  * 任何异常只记日志，**绝不因告警失败影响主流程**（CLOSE-02 记而不吞）。
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
            log = logging.getLogger("qq_bridge.monitor")
    return log


def _cfg(name: str, default):
    """运行时读 Config（热生效：改配置不用重启 bridge）。"""
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:
        return default


class Monitor:
    """监控器。notifier: async callable(text: str) —— 私聊主人。"""

    def __init__(self, notifier=None, api_base: str | None = None, transport=None) -> None:
        self.notifier = notifier
        self._api_base = api_base
        self._transport = transport
        self._running = False
        self._task: asyncio.Task | None = None
        #: task_id -> 上次告警时间戳（幂等去重）
        self._alerted: dict[str, float] = {}
        #: 连续遇到 IMG_BUSY 的次数（由队列侧 busy 事件累加）
        self.busy_streak = 0
        self._busy_alerted = False

    @property
    def api_base(self) -> str:
        base = self._api_base
        if not base:
            base = _cfg("MANJU_API_BASE", "http://127.0.0.1:7788/api/v1")
        return str(base).rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(_cfg("MONITOR_ENABLE", False))

    @property
    def interval(self) -> float:
        return max(5.0, float(_cfg("MONITOR_INTERVAL", 30.0) or 30.0))

    @property
    def task_timeout(self) -> float:
        return max(60.0, float(_cfg("MONITOR_TASK_TIMEOUT", 600.0) or 600.0))

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="qq-monitor")
        _log().info("monitor_started interval=%.0fs enabled=%s" % (self.interval, self.enabled))

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
            except Exception as e:  # noqa: BLE001 — 监控永不因异常退出
                _log().warning("monitor_check_error error=%s detail=%s"
                               % (type(e).__name__, str(e)[:200]))
            await asyncio.sleep(self.interval)

    async def _check_once(self) -> None:
        """一轮检查：失败任务 + 超时任务 + 持续繁忙。"""
        self._gc_alerted()
        await self._check_failed()
        await self._check_timeout()
        await self._check_busy()

    def _gc_alerted(self) -> None:
        """清理过期告警记录（OPS-02：持久数据声明 TTL + 清理者），防内存无限增长。"""
        ttl = self.task_timeout * 4 + 3600
        now = time.time()
        for k in [k for k, ts in self._alerted.items() if now - ts > ttl]:
            self._alerted.pop(k, None)

    def _seen(self, key: str) -> bool:
        """该 key 是否已告警过（幂等）。"""
        return key in self._alerted

    def _mark(self, key: str) -> None:
        self._alerted[key] = time.time()

    async def _check_failed(self) -> None:
        """新出现的失败任务 → 告警。"""
        data, err, _ = await self._get("/t2i/tasks?status=failed&size=10")
        if err:
            return
        items = (data or {}).get("items") or []
        if not isinstance(items, list):
            return
        for it in items:
            if not isinstance(it, dict):
                continue
            tid = str(it.get("task_id") or "")
            if not tid or self._seen("f:" + tid):
                continue
            self._mark("f:" + tid)
            prompt = str(it.get("prompt") or "")[:40]
            reason = str(it.get("error") or it.get("error_message") or "未知原因")[:120]
            channel = ""
            try:
                if "qq" in str(((it.get("params") or {}) if isinstance(it.get("params"), dict) else {}).get("channel") or ""):
                    channel = "（QQ 投的）"
            except Exception:
                pass
            await self._alert("任务失败%s：%s\n%s\n任务号 %s"
                              % (channel, prompt, reason, tid[:8]))

    async def _check_timeout(self) -> None:
        """活跃任务超过 MONITOR_TASK_TIMEOUT 仍未完成 → 告警（每个 task_id 一次）。"""
        data, err, _ = await self._get("/t2i/active-task")
        if err or not isinstance(data, dict):
            return
        task = data.get("task")
        if not task or not isinstance(task, dict):
            self.busy_streak = 0
            return
        tid = str(task.get("task_id") or "")
        started = float(task.get("created_at") or task.get("started_at") or 0)
        if not tid or started <= 0:
            return
        elapsed = time.time() - started
        key = "t:" + tid
        if elapsed > self.task_timeout and not self._seen(key):
            self._mark(key)
            await self._alert(
                "任务疑似卡住：%s\n已耗时 %s（超过阈值 %s 秒）\n任务号 %s\n"
                "可以去「任务中心」取消它。"
                % (str(task.get("prompt") or "")[:40],
                   _human(elapsed), int(self.task_timeout), tid[:8]))

    async def _check_busy(self) -> None:
        """队列连续 IMG_BUSY 达阈值 → 告警一次（由队列 busy 事件累加 busy_streak）。"""
        threshold = max(1, int(_cfg("MONITOR_BUSY_THRESHOLD", 3) or 3))
        if self.busy_streak >= threshold and not self._busy_alerted:
            self._busy_alerted = True
            await self._alert(
                "平台持续繁忙：本地 ComfyUI 连续 %d 次被占用（IMG_BUSY）。\n"
                "多半是网页端正在跑图，或上一张卡住了。等它跑完队列会自动继续。" % self.busy_streak)
        elif self.busy_streak == 0:
            self._busy_alerted = False  # 恢复后允许再次告警

    def note_busy(self) -> None:
        """队列侧遇 IMG_BUSY 时调用（累加连续繁忙计数）。"""
        self.busy_streak += 1

    def note_submitted(self) -> None:
        """队列侧成功提交时调用（连续繁忙计数归零）。"""
        self.busy_streak = 0
        self._busy_alerted = False

    # ---------------- 出口 ----------------
    async def _alert(self, text: str) -> None:
        if not self.notifier:
            return
        try:
            await self.notifier(text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _log().warning("monitor_alert_failed error=%s detail=%s"
                           % (type(e).__name__, str(e)[:160]))

    # ---------------- HTTP ----------------
    def _client(self, read_timeout: float):
        kw = {"timeout": httpx.Timeout(10.0, read=read_timeout)}
        if self._transport is not None:
            kw["transport"] = self._transport
        return httpx.AsyncClient(**kw)

    async def _get(self, path: str, timeout: float = 10.0):
        try:
            async with self._client(timeout) as cli:
                resp = await cli.get(self.api_base + path)
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


def _human(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return "%d 秒" % s
    m, sec = divmod(s, 60)
    if m < 60:
        return "%d 分 %02d 秒" % (m, sec)
    h, m = divmod(m, 60)
    return "%d 小时 %02d 分" % (h, m)


#: 模块级单例
monitor_mgr = Monitor()

#: 监听主项目生图任务（2026-09-13）：从推理监控页 /tasks 获取，QQ 直接看后台在画啥
from .infer_listen import infer_listener  # noqa: E402  (放在单例后，避免循环导入)
