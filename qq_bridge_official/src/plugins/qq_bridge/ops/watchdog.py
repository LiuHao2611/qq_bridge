# -*- coding: utf-8 -*-
"""ops.watchdog —— 开发机仓库巡检（M9 运维面，2026-10-20）。

需求（roadmap §M9）：「git status / CI 过没过」→ 群推送。定时巡检版：
个人开发机没有 CI runner，一期把「CI 过没过」实现为**可跑的检查**
（OPS_WATCH_CHECKS 配置的命令，如 `git status --porcelain` 计数、
`python -m pytest tests/ -q` 退出码——**命令必须只读/无副作用**，
与 gitinfo 白名单同款纪律）。

巡检逻辑：
  - 每 OPS_WATCH_INTERVAL（默认 1800s）跑一轮；
  - 脏改动数 / 未推送数 / 检查命令退出码 → 组装事件；
  - **状态变化才推送**（上次脏 0 个 → 现在 5 个才通知；没变化静默）——
    防刷屏比限流更重要（watchdog 是定时器，LLM 转述有成本）；
  - 事件推 NotificationHub（由调用方注入），本模块不直发。

本模块不 import nonebot / 不 import 主包：可裸单测（git/检查执行可注入）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from ..config import Config, get_logger, jlog
from .gitinfo import GitInfo, GitInfoError, git_info as _default_git
from .notify import LEVEL_ERROR, LEVEL_INFO, LEVEL_WARN, NotifyEvent

log = get_logger()


class RepoWatchdog:
    """仓库巡检循环（单例见 repo_watchdog）。"""

    def __init__(self, git: GitInfo | None = None,
                 on_event: Callable[[NotifyEvent], Awaitable[None]] | None = None,
                 interval: float | None = None) -> None:
        self.git = git or _default_git
        self.on_event = on_event
        raw = getattr(Config, "OPS_WATCH_INTERVAL", 1800.0)
        self.interval = 1800.0 if raw is None else float(raw)
        self._task: "asyncio.Task | None" = None
        self._stopped = True
        #: 上次巡检快照（变化检测）
        self._last_dirty: int | None = None
        self._last_unpushed: int | None = None
        self._last_check_fail: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(Config, "OPS_WATCH_ENABLE", True))

    async def _emit(self, kind: str, title: str, detail: str = "",
                    level: str = LEVEL_INFO) -> None:
        if self.on_event is None:
            return
        try:
            await self.on_event(NotifyEvent(kind=kind, title=title,
                                            detail=detail, level=level,
                                            scope=""))
        except Exception as e:  # noqa: BLE001 — 推事件失败不杀巡检
            log.warning(jlog("watch_emit_failed", kind=kind,
                             error=type(e).__name__, detail=str(e)[:160]))

    async def check_once(self) -> dict:
        """跑一轮巡检，返回本轮快照（并推送变化事件）。"""
        snap: dict = {"ok": True, "dirty": 0, "unpushed": 0, "check_fail": ""}
        try:
            st = await self.git.status()
            snap["dirty"] = int(st["dirty_count"])
            snap["unpushed"] = int(st["unpushed"])
        except GitInfoError as e:
            snap["ok"] = False
            snap["check_fail"] = str(e)[:200]
            log.warning(jlog("watch_git_failed", detail=str(e)[:160]))
            await self._emit("repo_query_failed",
                             "开发机仓库状态查不动了",
                             detail=str(e)[:300], level=LEVEL_ERROR)
            self._last_dirty = None
            self._last_unpushed = None
            return snap

        # 变化检测：脏改动从无到有 / 变多
        if self._last_dirty is not None and snap["dirty"] > self._last_dirty:
            await self._emit(
                "repo_dirty",
                "开发机仓库冒出 %d 个未提交改动（上次 %d）" % (
                    snap["dirty"], self._last_dirty),
                detail="分支 %s · 最近提交 %s" % (st["branch"], st["last_commit"]),
                level=LEVEL_WARN)
        elif self._last_dirty is None and snap["dirty"] > 0:
            # 首轮只记录不推送（启动时的存量不是新闻）
            log.info(jlog("watch_baseline", dirty=snap["dirty"]))
        # 未推送从无到有
        if self._last_unpushed is not None and snap["unpushed"] > self._last_unpushed:
            await self._emit(
                "repo_unpushed",
                "开发机仓库积了 %d 个未推送提交" % snap["unpushed"],
                detail="分支 %s" % st["branch"], level=LEVEL_WARN)
        self._last_dirty = snap["dirty"]
        self._last_unpushed = snap["unpushed"]

        # 配置的只读检查命令（如测试套件）——退出码非零即告警
        checks = str(getattr(Config, "OPS_WATCH_CHECKS", "") or "").strip()
        if checks:
            fail = await self._run_checks(checks)
            snap["check_fail"] = fail
            if fail and fail != self._last_check_fail:
                await self._emit("check_failed", "开发机自检没过：%s" % fail[:80],
                                 detail=fail[:400], level=LEVEL_ERROR)
            self._last_check_fail = fail
        return snap

    async def _run_checks(self, checks: str) -> str:
        """跑配置的检查命令（分号分隔；显式超时；返回失败描述，空=全过）。"""
        timeout = float(getattr(Config, "OPS_WATCH_CHECK_TIMEOUT", 120.0) or 120.0)
        fails = []
        for cmd in checks.split(";"):
            cmd = cmd.strip()
            if not cmd:
                continue
            try:
                import shlex
                import subprocess

                def _run(c=cmd):
                    p = subprocess.run(shlex.split(c), capture_output=True,
                                       text=True, timeout=timeout,
                                       encoding="utf-8", errors="replace")
                    return p.returncode, (p.stdout or "")[-500:] + (p.stderr or "")[-500:]

                rc, out = await asyncio.to_thread(_run)
                if rc != 0:
                    fails.append("%s → 退出码 %d：%s" % (cmd, rc, out.strip()[:200]))
            except Exception as e:  # noqa: BLE001
                fails.append("%s → 执行异常（%s）" % (cmd, type(e).__name__))
        return "\n".join(fails)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.ensure_future(self._loop())
        log.info(jlog("repo_watchdog_started", interval=self.interval))

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
        log.info(jlog("repo_watchdog_stopped"))

    async def _loop(self) -> None:
        while not self._stopped:
            if self.enabled:
                try:
                    await self.check_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — 单轮失败不杀循环
                    log.warning(jlog("watch_tick_failed", error=type(e).__name__,
                                     detail=str(e)[:160]))
            await asyncio.sleep(max(60.0, self.interval))


#: 模块级单例（插件入口注入 on_event；单测各自 new 隔离）
repo_watchdog = RepoWatchdog()
