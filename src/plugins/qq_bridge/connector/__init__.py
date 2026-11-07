# -*- coding: utf-8 -*-
"""connector（连接层，设计文档 §3.1）。

职责：
  - 维护 onebot 协议端（NapCat/LLOneBot）的 WS 连接状态；
  - 暴露统一的 call_action(action, params)，供 media 回传使用。

拓扑说明（与文档 §1 架构图一致）：
  本项目采用**反向 WS**——qq_bridge 是 WS *服务端*，由 NapCat 作为客户端主动连上来
  （NapCat 侧配置见 data/config/onebot.json（Docker 挂载卷）的 websocketReverse）。
  因此"重连"由协议端发起（NapCat 的 reconnectInterval），connector 负责：
    1) 记录连接/断开时间戳，暴露连接状态；
    2) 断线时告警（LOG-02），不做静默吞异常（CLOSE-02）。

验收（§3.1）：
  - 协议端重启后自动重连（由 NapCat 完成）→ connector 记录 attach 事件；
  - Token 错配时连接不会被建立 → status() 报告 offline，日志明确报错。

输入：nonebot 的 on_bot_connect / on_bot_disconnect 事件。
输出：稳定的 call_action 通道 + 连接状态。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..config import LOG_DIR, get_logger, jlog

log = get_logger()

# ----------------------------------------------------------------------------
# 连接事件独立日志（2026-10-29 新增）
# ----------------------------------------------------------------------------
# 背景：10-29 凌晨 KickedOffLine 排查时，主日志只有 attach/detach 两个时间戳，
#   缺「本次在线多久 / 离线多久 / 累计掉第几次」，只能事后手工比对时间戳还原曲线，
#   而且 ws 假死（连着但不通）根本不产生 detach，日志上完全静默。
# 做法：把连接事件单独落一份结构化日志，一行一条，字段固定，便于 grep / 统计 / 画曲线。
#   与主日志的关系是「同一事件的两个视图」——主日志给人看上下文，本文件给排查看曲线。
_EVENT_LOG = LOG_DIR / "qq_conn_events.log"

# 体积上限 + 保留份数：满 512KB 切一份，最多留 2 份历史（主文件 + .1 + .2）。
# 按心跳 10 分钟一条算，512KB ≈ 4000 行 ≈ 27 天；实际能压住一年以上的观察期。
_EVENT_MAX_BYTES = 512 * 1024
_EVENT_BACKUPS = 2


def _rotate_event_log() -> None:
    """按体积切分 qq_conn_events.log，避免长期运行把 logs/ 撑爆。

    为什么不用 logging.handlers.RotatingFileHandler：本模块每次写入都是
    「open → append → close」，不长期持有 file handle，直接 rename 更简单，
    也不会出现多个 handler 争抢同一文件、切分后写到旧句柄的问题。
    任何 IO 异常都吞掉，降级为「不切分、继续写」——丢事件比不轮转严重得多。
    """
    try:
        if _EVENT_LOG.stat().st_size < _EVENT_MAX_BYTES:
            return
        # 从最老的一份往前挪：删 .N → .N-1 移到 .N → … → 主文件移到 .1
        oldest = _EVENT_LOG.parent / f"{_EVENT_LOG.name}.{_EVENT_BACKUPS}"
        oldest.unlink(missing_ok=True)
        for i in range(_EVENT_BACKUPS - 1, 0, -1):
            src = _EVENT_LOG.parent / f"{_EVENT_LOG.name}.{i}"
            if src.exists():
                src.replace(_EVENT_LOG.parent / f"{_EVENT_LOG.name}.{i + 1}")
        _EVENT_LOG.replace(_EVENT_LOG.parent / f"{_EVENT_LOG.name}.1")
    except Exception as e:  # noqa: BLE001 — 轮转失败不影响事件记录
        log.warning(jlog("conn_event_rotate_failed", error=type(e).__name__))


def _human(sec: float) -> str:
    """秒 → 可读时长，让日志能直接扫。"""
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec / 60:.1f}m"
    if sec < 172800:
        return f"{sec / 3600:.2f}h"
    return f"{sec / 86400:.2f}d"


class Connector:
    """onebot 连接状态簿 + 动作代理。单例通过模块级 connector 暴露。"""

    def __init__(self) -> None:
        self._bots: dict[str, Any] = {}
        self._last_connected_at: float = 0.0
        self._last_disconnected_at: float = 0.0
        self._connect_count: int = 0
        # 本次会话起点 / 累计断开次数：用来算「在线时长」「掉第几次」
        self._session_start: float = 0.0
        self._detach_total: int = 0
        # 真实账号登录态探测缓存（与 WS 连接态相互独立，见 check_account_login）
        self._acct_cache: dict = {"ok": None, "reason": "", "user_id": None, "at": 0.0}
        self._acct_task: Any = None

    # ---------- 连接事件记录 ----------
    def _record_event(self, kind: str, **fields: Any) -> None:
        """向 qq_conn_events.log 追加一行。写失败绝不影响连接流程本身。"""
        _rotate_event_log()  # 先看要不要切分，再写
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            parts = [ts, f"{kind:<6}"]
            parts.extend(f"{k}={v}" for k, v in fields.items())
            with _EVENT_LOG.open("a", encoding="utf-8") as f:
                f.write("  ".join(parts) + "\n")
        except Exception as e:  # noqa: BLE001 — 记账失败不能拖垮连接
            log.warning(jlog("conn_event_write_failed", error=type(e).__name__))

    def _uptime(self, now: float) -> float:
        return (now - self._session_start) if self._session_start else 0.0

    # ---------- 生命周期 ----------
    def attach_bot(self, bot: Any) -> None:
        sid = str(getattr(bot, "self_id", "unknown"))
        now = time.time()
        gap = (now - self._last_disconnected_at) if self._last_disconnected_at else 0.0
        self._bots[sid] = bot
        self._last_connected_at = now
        self._connect_count += 1
        self._session_start = now
        log.info(jlog(
            "connector_attached",
            self_id=getattr(bot, "self_id", "?"),
            connect_count=self._connect_count,
            online=len(self._bots),
        ))
        self._record_event(
            "ATTACH",
            sid=self._connect_count,
            gap=f"{gap:.0f}s({_human(gap)})" if gap else "-",
            online_now=len(self._bots),
            detach_total=self._detach_total,
        )

    def detach_bot(self, bot: Any) -> None:
        sid = str(getattr(bot, "self_id", "unknown"))
        now = time.time()
        up = self._uptime(now)
        self._bots.pop(sid, None)
        self._last_disconnected_at = now
        self._detach_total += 1
        log.warning(jlog(
            "connector_detached",
            self_id=sid,
            online=len(self._bots),
            hint="协议端断开，等待 NapCat 侧自动重连（reconnectInterval）",
        ))
        self._record_event(
            "DETACH",
            sid=self._connect_count,
            up=f"{up:.0f}s({_human(up)})",
            online_now=len(self._bots),
            detach_total=self._detach_total,
        )

    async def health_loop(self, interval: float = 600.0) -> None:
        """每 interval 秒记一条 ALIVE 心跳。

        价值：WS 假死（连接在、但账号已失效 / 收不到消息）不会触发 detach，
        主日志上完全静默。有心跳就能靠「最后一条 ALIVE 之后断档」反推真实存活终点。
        另外顺带把 check_account_login 的结论记进去，区分「连着」和「真能用」。
        """
        while True:
            try:
                await asyncio.sleep(interval)
                acct = await self.check_account_login()
                self._record_event(
                    "ALIVE",
                    sid=self._connect_count,
                    up=f"{self._uptime(time.time()):.0f}s({_human(self._uptime(time.time()))})",
                    online_now=len(self._bots),
                    acct="ok" if acct.get("ok") else (acct.get("reason") or "fail"),
                    detach_total=self._detach_total,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 心跳挂了不影响连接
                log.warning(jlog("conn_health_loop_error", error=type(e).__name__))

    # ---------- 状态 ----------
    @property
    def online(self) -> bool:
        return bool(self._bots)

    def status(self) -> dict:
        now = time.time()
        return {
            "online": self.online,
            "bots": sorted(self._bots.keys()),
            "connect_count": self._connect_count,
            "last_connected_at": self._last_connected_at,
            "last_disconnected_at": self._last_disconnected_at,
            # 排障用：当前会话已在线多久、累计掉过几次
            "uptime": round(self._uptime(now), 1) if self.online else 0.0,
            "detach_total": self._detach_total,
        }

    def first_bot(self) -> Any:
        if not self._bots:
            return None
        return next(iter(self._bots.values()))

    # ---------- 真实账号登录态探测 ----------
    async def check_account_login(self, ttl: float = 20.0, timeout: float = 5.0) -> dict:
        """探测 QQ 账号是否真的登录可用（与 WS 连接态相互独立）。

        WS 连着不代表账号能收发：NapCat 被 KickedOffLine 后 WS 常不断，
        但底层 QQ 会话已失效，发消息会 sendMsg 超时。这里直接调 OneBot
        get_login_info，成功且 user_id 与已连 bot 一致才算真在线。
        结果缓存 ttl 秒，避免每次状态轮询都打一次 API（失败时会挂满 timeout）。
        """
        now = time.time()
        c = self._acct_cache
        if c["ok"] is not None and (now - c["at"]) < ttl:
            return {"ok": c["ok"], "reason": c["reason"], "user_id": c["user_id"], "cached": True}
        # 防惊群：已有探测在飞则复用其结果
        if self._acct_task is not None and not self._acct_task.done():
            try:
                return await self._acct_task
            except Exception:
                pass
        bot = self.first_bot()
        if bot is None:
            res = {"ok": False, "reason": "ws_not_connected", "user_id": None}
            self._acct_cache = {**res, "at": now}
            return res

        async def _probe():
            try:
                info = await asyncio.wait_for(bot.call_api("get_login_info"), timeout=timeout)
            except Exception as e:  # 超时 / ActionFailed 都视为账号不可用
                return {"ok": False, "reason": "%s: %s" % (type(e).__name__, str(e)[:120]), "user_id": None}
            if not isinstance(info, dict):
                return {"ok": False, "reason": "bad_login_info", "user_id": None}
            uid = info.get("user_id") or info.get("user_id_str")
            if not uid:
                return {"ok": False, "reason": "empty_login_info", "user_id": None}
            # 与 WS 上报的 self_id 交叉校验，防止拿到过期缓存
            expect = getattr(bot, "self_id", None)
            if expect and str(uid) != str(expect):
                return {"ok": False, "reason": "login_mismatch", "user_id": str(uid)}
            return {"ok": True, "reason": "", "user_id": str(uid)}

        self._acct_task = asyncio.ensure_future(_probe())
        try:
            res = await self._acct_task
        finally:
            self._acct_task = None
        self._acct_cache = {**res, "at": now}
        return res

    # ---------- 动作 ----------
    async def call_action(self, action: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        """调用 onebot 动作。无在线 bot 时抛 RuntimeError 而非静默失败（CLOSE-02）。"""
        bot = self.first_bot()
        if bot is None:
            raise RuntimeError("onebot 未连接：没有在线的 bot 实例")
        try:
            return await bot.call_api(action, **(params or {}))
        except Exception as e:  # 明确记录，不吞异常（CLOSE-02）
            log.error(jlog(
                "connector_call_action_failed",
                action=action,
                error=type(e).__name__,
                detail=str(e)[:200],
            ))
            raise


connector = Connector()
