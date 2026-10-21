# -*- coding: utf-8 -*-
"""ai_proactive —— AI 模式主动发言引擎（2026-10-19）。

需求：AI 模式下机器人**主动**发消息——不是用户发一句才回一句，而是群里安静下来
时自己起话头/跟进话题，像个活群友。

设计参考（2026 GitHub 权威项目调研，集百家之长，禁闭门造车）：
  - HakuchumuHYX/nyaturingtest-enhanced：安静 2h+ 才在合适时段开话题（工作日仅
    午/晚、凌晨不打扰、**一天最多两次**）；**最近消息里自己说的话超两成就别再
    主动插嘴**；没人接话不自说自话；说完 3 分钟内的人优先接住；
  - CMHopeSunshine/nonebot-plugin-learning-chat：**自适应沉默阈值** =
    平均发言间隔 × 阈值系数；今日消息 <30 不主动；**最新一条是 bot 发的就不主动**；
  - yhny1001/proactive_talk_plugin：独立触发链路 + 活跃度检测 + **LLM 最终判断**
    （生成内容可回「跳过」）+ 随机事件式开场选题 + 跟进加权；
  - luojisama/nonebot-plugin-personification：APScheduler 驱动的主动消息
    （早晚问候/话题跟进/节日祝福/群空闲话题）；
  - Funny1Potato/nonebot-plugin-aigf-master：**社交能量系统**——动态能量影响
    发言意愿，形成「话多→累了→安静→恢复」周期。

本地兼容取舍：
  - 不引 APScheduler：用 asyncio 后台 tick 循环（项目已有 queue_consume_loop 先例）；
  - 活跃度指标全部从 history 库算（list_conversations/list_messages 已有
    direction 区分入出站），不额外加钩子——单一事实来源，重启不丢；
  - 「LLM 最终判断」与「内容生成」融合为一次调用：让模型在没什么可说时回
    「跳过」，省一次调用（proactive_talk_plugin 的 llm_judge 轻量化）。

本模块**不 import nonebot**，可单测；发送/生成经调用方注入回调。
"""
from __future__ import annotations

import random
import time
from collections import deque
from datetime import datetime

from .config import Config, get_logger, jlog

log = get_logger()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class ScopeState:
    """per-scope 主动发言状态（内存态；重启重置可接受——闸门很快会重新建立）。"""

    __slots__ = ("energy", "last_proactive_at", "proactive_today", "proactive_date",
                 "greeted_date", "greeted_slots", "off")

    def __init__(self) -> None:
        self.energy: float = 100.0
        self.last_proactive_at: float = 0.0
        self.proactive_today: int = 0
        self.proactive_date: str = ""
        self.greeted_date: str = ""
        #: 今日已完成的问候时段集合（{"morning","noon","evening"}）
        self.greeted_slots: set[str] = set()
        #: per-scope 手动覆盖：None=跟随全局配置，True/False=强制
        self.off: bool | None = None

    def roll_date(self) -> None:
        """跨天重置计数。"""
        d = _today()
        if self.proactive_date != d:
            self.proactive_date = d
            self.proactive_today = 0
        if self.greeted_date != d:
            self.greeted_date = d
            self.greeted_slots = set()

    def spend(self, cost: float) -> None:
        self.energy = max(0.0, self.energy - float(cost))

    def regen(self, per_min: float, dt_sec: float) -> None:
        if per_min > 0 and dt_sec > 0:
            self.energy = min(100.0, self.energy + per_min * dt_sec / 60.0)


def parse_hours(raw: str) -> tuple[int, int]:
    """解析活跃时段 "8-23" → (8, 23)。坏值回落 (8, 23)。"""
    try:
        a, _, b = str(raw or "").partition("-")
        h0 = max(0, min(23, int(a.strip())))
        h1 = max(0, min(23, int(b.strip())))
        return h0, h1
    except (TypeError, ValueError):
        return 8, 23


def hour_in_window(hour: int, h0: int, h1: int) -> bool:
    """时段判断（支持跨零点，如 22-2）。"""
    if h0 <= h1:
        return h0 <= hour <= h1
    return hour >= h0 or hour <= h1


class Activity:
    """一个会话的活跃度快照（从 history 算出）。"""

    __slots__ = ("scene", "peer_id", "scope", "last_ts", "last_is_bot",
                 "idle_sec", "avg_gap", "bot_ratio", "recent_count",
                 "recent_texts", "greet_slot")

    def __init__(self, scene: str, peer_id: int, scope: str, last_ts: float,
                 last_is_bot: bool, idle_sec: float, avg_gap: float,
                 bot_ratio: float, recent_count: int, recent_texts: list,
                 greet_slot: str | None = None):
        self.scene = scene
        self.peer_id = peer_id
        self.scope = scope
        self.last_ts = last_ts
        self.last_is_bot = last_is_bot
        self.idle_sec = idle_sec
        self.avg_gap = avg_gap
        self.bot_ratio = bot_ratio
        self.recent_count = recent_count
        self.recent_texts = recent_texts
        self.greet_slot = greet_slot


def scope_of(scene: str, peer_id: int) -> str:
    """与 dispatcher.QQEvent.scope_key / ai_mode.event_scope 同构。"""
    return ("g:%d" % peer_id) if scene == "group" else ("u:%d" % peer_id)


def compute_activity(scene: str, peer_id: int, messages: list,
                     now: float | None = None) -> Activity:
    """从 history.list_messages 的正序消息列表算活跃度。

    messages: [{ts, direction("in"/"out"), text, nickname, ...}, ...]（正序）
    """
    now = time.time() if now is None else float(now)
    scope = scope_of(scene, peer_id)
    if not messages:
        return Activity(scene, peer_id, scope, 0.0, False, 1e18, 0.0, 0.0, 0, [])
    ts = [float(m.get("ts") or 0) for m in messages if m.get("ts")]
    last_ts = ts[-1] if ts else 0.0
    idle = (now - last_ts) if last_ts else 1e18
    # 平均发言间隔：用相邻时间差的中位数（抗长安静干扰）
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    gaps.sort()
    avg_gap = gaps[len(gaps) // 2] if gaps else 0.0
    # bot 占比（近 50 条里 direction=out 的比例，nyaturingtest 的两成闸门）
    window = messages[-50:]
    n_bot = sum(1 for m in window if str(m.get("direction") or "in") == "out")
    bot_ratio = n_bot / len(window) if window else 0.0
    texts = []
    for m in window[-10:]:
        who = "本喵" if str(m.get("direction") or "in") == "out" else (
            m.get("card") or m.get("nickname") or "群友")
        t = str(m.get("text") or "").strip()
        if t:
            texts.append("%s：%s" % (who, t[:40]))
    return Activity(scene, peer_id, scope, last_ts,
                    str(messages[-1].get("direction") or "in") == "out",
                    idle, avg_gap, bot_ratio, len(messages), texts)


class ProactiveEngine:
    """主动发言引擎：闸门判定 + 状态记账。发送/生成由调用方注入。"""

    #: 问候时段定义：(slot 名, 起始小时, 结束小时)
    GREET_SLOTS = (("morning", 7, 9), ("noon", 11, 12), ("evening", 21, 23))
    #: 能量恢复（点/分钟）——「话多→累了→安静→恢复」周期
    ENERGY_REGEN_PER_MIN = 2.0

    def __init__(self) -> None:
        self._states: dict[str, ScopeState] = {}
        self._last_tick_at = time.time()

    # ---------------- 状态 ----------------
    def state(self, scope: str) -> ScopeState:
        st = self._states.get(scope)
        if st is None:
            st = ScopeState()
            self._states[scope] = st
        st.roll_date()
        return st

    def set_off(self, scope: str, off: bool | None) -> None:
        self.state(scope).off = off

    def is_off(self, scope: str) -> bool:
        st = self.state(scope)
        if st.off is not None:
            return st.off
        return not Config.AI_PROACTIVE_ENABLE

    # ---------------- 自适应沉默阈值 ----------------
    def idle_threshold(self, act: Activity) -> float:
        """学习-chat 同款：沉默阈值 = 平均发言间隔 × 系数，clamp 到 [min, max]。

        平均间隔取不到（新群/样本少）→ 用中位默认 300s。
        """
        base = act.avg_gap if act.avg_gap > 0 else 300.0
        thr = base * max(1.0, float(Config.AI_PROACTIVE_IDLE_FACTOR))
        return max(float(Config.AI_PROACTIVE_IDLE_MIN),
                   min(float(Config.AI_PROACTIVE_IDLE_MAX), thr))

    # ---------------- 闸门 ----------------
    def should_consider(self, act: Activity,
                        now: float | None = None) -> tuple[bool, str]:
        """判断该会话此刻是否可以考虑主动发言。返回 (ok, reason)。

        reason 用于日志/调试；ok=False 时 reason 说明被哪道闸门挡下。
        """
        now = time.time() if now is None else float(now)
        st = self.state(act.scope)
        if self.is_off(act.scope):
            return False, "off"
        h0, h1 = parse_hours(Config.AI_PROACTIVE_HOURS)
        if not hour_in_window(datetime.now().hour, h0, h1):
            return False, "hours"          # 凌晨不打扰（nyaturingtest）
        if st.proactive_today >= max(0, int(Config.AI_PROACTIVE_DAILY_MAX)):
            return False, "daily_cap"      # 一天最多两次（nyaturingtest）
        if now - st.last_proactive_at < max(0, int(Config.AI_PROACTIVE_MIN_INTERVAL)):
            return False, "min_interval"
        if act.last_is_bot:
            return False, "bot_last"       # 最新一条是 bot 发的→没人接话，别自说自话
        if act.recent_count < 10:
            return False, "too_quiet"      # 今日消息太少（learning-chat 的 30 条轻量版）
        thr = self.idle_threshold(act)
        if act.idle_sec < thr:
            return False, "not_idle"       # 还没安静够
        if act.bot_ratio > 0.2:
            return False, "bot_ratio"      # 自己说话超两成→收住（nyaturingtest）
        if now - act.last_ts > 6 * 3600:
            return False, "dead"           # 半天没动静的群不硬撩
        return True, "ok"

    def roll_energy(self) -> None:
        """tick 时按时间恢复能量。"""
        now = time.time()
        dt = now - self._last_tick_at
        self._last_tick_at = now
        if dt <= 0:
            return
        for st in self._states.values():
            st.roll_date()
            st.regen(self.ENERGY_REGEN_PER_MIN, dt)

    def willing(self, scope: str) -> bool:
        """能量×概率 roll（aigf 社交能量：累了的猫懒得主动搭话）。"""
        st = self.state(scope)
        chance = max(0.0, min(1.0, float(Config.AI_PROACTIVE_CHANCE))) * (st.energy / 100.0)
        return random.random() < chance

    def mark_proactive(self, scope: str, energy_cost: float) -> None:
        st = self.state(scope)
        st.last_proactive_at = time.time()
        st.proactive_today += 1
        st.spend(energy_cost)

    def mark_greeted(self, scope: str, slot: str) -> None:
        st = self.state(scope)
        st.roll_date()
        st.greeted_slots.add(slot)

    def greet_due(self, act: Activity) -> str | None:
        """当前是否有到点的问候没发（早/午/晚，每日每段一次）。"""
        if not Config.AI_PROACTIVE_GREET or self.is_off(act.scope):
            return None
        st = self.state(act.scope)
        if act.recent_count < 3:
            return None  # 死群不问候
        if act.last_is_bot:
            return None  # 没人接话别连续自言自语
        hour = datetime.now().hour
        for slot, h0, h1 in self.GREET_SLOTS:
            if h0 <= hour <= h1 and slot not in st.greeted_slots:
                # 问候也讲基本法：至少安静过 10 分钟，别人家正聊着插句早安
                if act.idle_sec >= 600:
                    return slot
        return None


# 模块级单例
proactive_engine = ProactiveEngine()
