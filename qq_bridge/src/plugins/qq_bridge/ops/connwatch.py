# -*- coding: utf-8 -*-
"""ops.connwatch —— 连接看门狗（2026-11-03）。

## 它只做一件事：检测 + 告警

**本模块没有任何 restart / relogin / 重连分支，这是刻意的。**
依据 bot.py 文件头「最高优先级声明 §3/§4」：

    3. 严禁自动重登：出现登录失败信号立即熔断、只告警不重试。频繁重登是
       QQ 风控最高危行为，即 2026-09-16 封号事故的直接根因。
    4. 自动重启守护（watchdog）默认关闭；启用时只做检测+告警，且设每日上限。

所以哪怕 NapCat 已经死透，这里也只会喊，不会动手。

## 为什么需要它（2026-11-03 22:16 事故复盘）

NapCat 的 QQ 会话被服务端判离线 → NapCat 内部「正在重启 Worker」后卡死。
关键在于：**WebSocket 并没有断**。于是

  - nonebot 的 ``on_bot_disconnect`` **不触发**（连接簿看着一切正常）；
  - 现有 ``connector.health_loop`` 要 **600s** 才记一条 **info** 级 ALIVE 日志；
  - 那条日志还没人看。

结果：用户发 ``配卡 野马`` 石沉大海，4 分钟里没有任何提示，只能事后翻日志。

## 本模块的行为契约

  - 每 ``OPS_CONN_WATCH_INTERVAL``（默认 60s）探一次**真实账号态**；
  - 连续 ``OPS_CONN_WATCH_FAILS``（默认 2）次失败才判掉线 —— 滤单次超时抖动；
  - **状态变化才告警**：掉线报一次、恢复报一次，稳态不吭声（不刷屏）；
  - 每日 ``OPS_CONN_WATCH_MAX_ALERTS``（默认 20）条封顶（§4）；
  - 每次判定落 WARN/ERROR 日志，键 ``conn_state_down`` / ``conn_state_up``；
  - 维护内存快照供 ``GET /api/qq/health`` 读取（该端点不触发任何 API 调用）。

## 诚实交代一个死循环

账号掉线时用 QQ 推「账号掉线了」，hub.send 走的就是 QQ —— 必然发不出去。
所以 QQ 推送在这里只是**顺带**（能发出去就发，多半发不出去），
真正不会失效的兜底是：**ERROR 日志 + /api/qq/health**。
不要在别处假设「告警一定送达」。

本模块不 import nonebot、不 import 主包：探针与事件出口全部注入，可裸单测。
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from ..config import Config, get_logger, jlog
from .notify import LEVEL_ERROR, LEVEL_INFO, LEVEL_WARN, NotifyEvent

log = get_logger()

#: 账号真的能用
STATE_OK = "ok"
#: 没有 bot 连上来（WS 层就断了，on_bot_disconnect 正常触发的那种）
STATE_WS_DOWN = "ws_down"
#: WS 连着，但账号态探测失败（KickedOffLine / get_login_info 超时的典型形态）
STATE_ACCT_FAIL = "acct_fail"
#: 看门狗没启用或还没跑过第一轮
STATE_UNKNOWN = "unknown"

#: 掉线/恢复的对外文案
_DOWN_TEXT = {
    STATE_WS_DOWN: ("机器人和 QQ 断开了", "WebSocket 上没有在线的 bot 实例，"
                                        "NapCat 可能没起来或连不上本机 8080"),
    STATE_ACCT_FAIL: ("机器人连上了但 QQ 账号用不了", "WebSocket 还在，但真实账号态探测失败"
                                                     "（典型是 KickedOffLine 后 NapCat 卡死）"),
}


class ConnWatchdog:
    """连接看门狗：定时探账号态，状态变化才告警。**不重启、不重登。**"""

    #: 事件环容量（供 /health 展示最近几次翻转）
    HISTORY = 20

    def __init__(self,
                 probe: Callable[[], Awaitable[dict]] | None = None,
                 on_event: Callable[[NotifyEvent], Awaitable[bool]] | None = None,
                 interval: float | None = None,
                 fail_threshold: int | None = None,
                 max_alerts_per_day: int | None = None,
                 grace: float | None = None) -> None:
        #: 探针：返回 {"ok": bool, "reason": str, "user_id": str|None}
        #: 真实接线是 connector.check_account_login
        self.probe = probe
        self.on_event = on_event
        raw_iv = (interval if interval is not None
                  else getattr(Config, "OPS_CONN_WATCH_INTERVAL", 60.0))
        self.interval = 60.0 if raw_iv is None else max(15.0, float(raw_iv))
        raw_th = (fail_threshold if fail_threshold is not None
                  else getattr(Config, "OPS_CONN_WATCH_FAILS", 2))
        self.fail_threshold = max(1, int(raw_th or 1))
        raw_cap = (max_alerts_per_day if max_alerts_per_day is not None
                   else getattr(Config, "OPS_CONN_WATCH_MAX_ALERTS", 20))
        self.max_alerts_per_day = max(0, int(raw_cap or 0))
        #: 启动宽限期（秒）：容器/NapCat 先后起有时差，别一开机就喊「掉线」
        self.grace = 120.0 if grace is None else float(grace)

        self._task: "asyncio.Task | None" = None
        self._stopped = True
        self._started_at = 0.0

        # ---- 判定状态 ----
        self._state = STATE_UNKNOWN
        self._fails = 0
        self._since = 0.0            # 当前状态从何时开始
        self._last_ok_at = 0.0
        self._last_reason = ""
        self._last_checked_at = 0.0
        self._checks = 0

        # ---- 每日告警配额 ----
        self._day = ""
        self._alerts_today = 0

        #: 最近的状态翻转（倒序）
        self._history: list[dict] = []

    # ---------------- 配置 ----------------
    @property
    def enabled(self) -> bool:
        return bool(getattr(Config, "OPS_CONN_WATCH_ENABLE", True))

    # ---------------- 快照（给 /api/qq/health，零 API 调用） ----------------
    def snapshot(self) -> dict:
        """当前判定快照。纯内存读，**不会触发 get_login_info**，可高频轮询。"""
        now = time.time()
        ok = self._state == STATE_OK
        since = self._since or self._started_at
        return {
            "ok": ok,
            "state": self._state,
            "enabled": self.enabled,
            # 当前状态已持续多久（掉线时这个数字就是「已经哑了多久」）
            "state_for": round(now - since, 1) if since else 0.0,
            # 最后一次确认账号可用是什么时候 —— 掉线时看这个判哑了多久最准
            "last_ok_at": round(self._last_ok_at, 1),
            "last_ok_ago": (round(now - self._last_ok_at, 1)
                            if self._last_ok_at else None),
            "reason": self._last_reason,
            "fails": self._fails,
            "fail_threshold": self.fail_threshold,
            "checks": self._checks,
            "last_checked_at": round(self._last_checked_at, 1),
            "interval": self.interval,
            "alerts_today": self._alerts_today,
            "max_alerts_per_day": self.max_alerts_per_day,
            "history": self._history[:10],
        }

    # ---------------- 每日配额 ----------------
    def _quota_ok(self) -> bool:
        if self.max_alerts_per_day <= 0:
            return True  # 0 = 不限量（显式配置才可能走到这）
        today = time.strftime("%Y-%m-%d", time.localtime())
        if today != self._day:
            self._day = today
            self._alerts_today = 0
        if self._alerts_today >= self.max_alerts_per_day:
            return False
        self._alerts_today += 1
        return True

    # ---------------- 事件出口 ----------------
    async def _emit(self, kind: str, title: str, detail: str = "",
                    level: str = LEVEL_WARN) -> None:
        if self.on_event is None:
            log.info(jlog("conn_event_no_sink", kind=kind))
            return
        try:
            await self.on_event(NotifyEvent(kind=kind, title=title,
                                            detail=detail, level=level,
                                            scope=""))
        except Exception as e:  # noqa: BLE001 — 推事件失败不杀看门狗
            # 注意：账号掉线时这条**大概就是**发不出去，属预期，
            # 所以下面靠日志兜底，不是吞错。
            log.warning(jlog("conn_event_emit_failed", kind=kind,
                             error=type(e).__name__, detail=str(e)[:160]))

    def _record(self, kind: str, state: str, detail: str) -> None:
        self._history.insert(0, {"at": round(time.time(), 1), "kind": kind,
                                 "state": state, "detail": detail[:200]})
        del self._history[self.HISTORY:]

    # ---------------- 单轮判定 ----------------
    async def check_once(self) -> dict:
        """探一轮，必要时告警。返回本轮快照。"""
        now = time.time()
        self._last_checked_at = now
        self._checks += 1

        if self.probe is None:
            log.warning(jlog("conn_probe_missing"))
            return self.snapshot()

        try:
            res = await self.probe()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 探针自身炸了，按失败算而非放过
            res = {"ok": False,
                   "reason": "probe_raised: %s: %s" % (type(e).__name__, str(e)[:120])}
        if not isinstance(res, dict):
            res = {"ok": False, "reason": "bad_probe_result"}

        ok = bool(res.get("ok"))
        reason = str(res.get("reason") or "")

        if ok:
            self._fails = 0
            self._last_ok_at = now
            self._last_reason = ""
            if self._state in (STATE_WS_DOWN, STATE_ACCT_FAIL):
                await self._recover(state_from=self._state, now=now)
                # 恢复即新状态起点：不重置的话 state_for 会把掉线期也算进去
                self._since = now
            elif self._state == STATE_UNKNOWN:
                # 首轮就是好的：只建基线，不吭声（启动时本来就没有"之前"）
                log.info(jlog("conn_watch_baseline", state=STATE_OK))
                self._since = now
            self._state = STATE_OK
            return self.snapshot()

        # ---- 失败分支：累计，够阈值且发生状态变化才喊 ----
        self._last_reason = reason or "unknown"
        new_state = (STATE_WS_DOWN if reason == "ws_not_connected"
                     else STATE_ACCT_FAIL)
        self._fails += 1

        in_grace = self._started_at and (now - self._started_at) < self.grace
        if self._fails < self.fail_threshold:
            log.info(jlog("conn_watch_fail_pending", fails=self._fails,
                          need=self.fail_threshold, reason=self._last_reason))
            return self.snapshot()

        if self._state == new_state:
            # 已经是这个状态了，不重复喊（稳态静默）。
            # 但每 ~5 分钟留一条 info，证明看门狗自己没死（1 分钟一条太吵）。
            if self._fails % max(1, int(300 / self.interval)) == 0:
                log.info(jlog("conn_state_still_down", state=new_state,
                              for_s=round(now - (self._since or now), 1)))
            return self.snapshot()

        if in_grace:
            # 启动宽限期：容器与 NapCat 起步有先后，别一开机就喊「掉线」。
            # 只记 info，**不改状态也不推**——所以宽限期一过，若仍未恢复，
            # 下一轮照常判定并推送，不会漏报。
            log.info(jlog("conn_alert_deferred_grace", state=new_state,
                          grace=self.grace, fails=self._fails))
            return self.snapshot()

        title, why = _DOWN_TEXT.get(new_state, ("机器人连不上了", ""))
        detail = "%s\n判定依据：连续 %d 次探测失败（最近一次 %s）" % (
            why, self._fails, self._last_reason)
        # 日志**一定**写 —— 账号掉线时 QQ 推送注定发不出去，这是唯一不会失效的通道
        log.error(jlog("conn_state_down", state=new_state,
                       prev=self._state, fails=self._fails,
                       reason=self._last_reason))
        self._record("down", new_state, detail)
        self._state = new_state
        self._since = now
        if self._quota_ok():
            await self._emit("conn_down", title, detail=detail, level=LEVEL_ERROR)
        else:
            log.warning(jlog("conn_alert_quota_exhausted",
                             state=new_state, cap=self.max_alerts_per_day))
        return self.snapshot()

    async def _recover(self, state_from: str, now: float) -> None:
        down_for = round(now - (self._since or now), 1)
        title = "机器人恢复了"
        detail = "之前的状态：%s，已恢复 %.1f 秒" % (state_from, down_for)
        log.warning(jlog("conn_state_up", prev=state_from, down_for=down_for))
        self._record("up", STATE_OK, detail)
        # 恢复消息不占每日配额（配额只约束"出事了"这类告警）
        await self._emit("conn_up", title, detail=detail, level=LEVEL_INFO)

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._started_at = time.time()
        self._since = self._started_at
        self._task = asyncio.ensure_future(self._loop())
        log.info(jlog("conn_watch_started", interval=self.interval,
                      fail_threshold=self.fail_threshold,
                      max_alerts_per_day=self.max_alerts_per_day,
                      note="detect+alert only, no restart/relogin"))

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
        log.info(jlog("conn_watch_stopped"))

    async def _loop(self) -> None:
        while not self._stopped:
            if self.enabled:
                try:
                    await self.check_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — 单轮失败不杀循环
                    log.warning(jlog("conn_watch_tick_failed",
                                     error=type(e).__name__,
                                     detail=str(e)[:160]))
            await asyncio.sleep(self.interval)


#: 模块级单例（插件入口注入 probe / on_event；单测各自 new 隔离）
conn_watchdog = ConnWatchdog()
