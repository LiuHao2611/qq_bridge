# -*- coding: utf-8 -*-
"""checkin/manager.py —— 签到业务编排（每日签到 / 补签 / 排行榜 / 日历）。

游戏化设计依据（Duolingo streak + 京东日历签到 + 米游社）：
  * 每日基础喵币 + 连签阶梯加成（短周期，避免长关卡断签即放弃）；
  * 累计天数里程碑奖励（3/7/15/30 天，多段短周期——领奖后下一关进度立即可见，
    消除「领完奖即流失」的断点，研究指 17% 流失发生在领奖当下）；
  * 连续 + 累计双轨：累计更宽容，抗「破罐破摔效应」（What-the-Hell Effect）；
  * 补签：消耗喵币修复最近一次断签，给弹性、也制造货币消耗出口；
  * 排行榜：社交竞争，刺激回访。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from ..config import Config
from .store import CheckinError, CheckinStore, _resolve_db_path

#: 累计天数 → 里程碑奖励（喵币）。多段短周期，领奖后下一关进度立即可见。
MILESTONES = ((3, 30), (7, 80), (15, 200), (30, 500))


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return None


def _today() -> str:
    tz = _tz()
    d = datetime.now(tz) if tz else datetime.now()
    return d.strftime("%Y-%m-%d")


def _shift_day(day: str, n: int) -> str:
    """把 'YYYY-MM-DD' 偏移 n 天（用绝对日期算，跨月跨年安全）。"""
    y, m, dd = (int(x) for x in day.split("-"))
    d1 = datetime(y, m, dd) + _one_day() * n
    return d1.strftime("%Y-%m-%d")


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


class CheckinManager:
    def __init__(self, store: CheckinStore | None = None) -> None:
        self.store = store or CheckinStore()

    async def do_checkin(self, scope: str, user_id: int,
                         today: str | None = None) -> dict:
        """执行一次签到。返回结果字典供卡片/文本渲染。"""
        today = today or _today()
        prof = await asyncio.to_thread(self.store.get_profile, scope, user_id)
        if prof["last_day"] == today:
            return {"status": "already", "today": today, **prof}
        # 连签判定：昨天签过 → +1；否则归 1（中间断过）
        streak = prof["streak"]
        if prof["last_day"] == _shift_day(today, -1):
            streak += 1
        else:
            streak = 1
        total = prof["total_days"] + 1
        # 每日基础 + 连签阶梯加成（每 5 连签多 +1）
        daily = int(getattr(Config, "CHECKIN_DAILY_POINTS", 5))
        streak_bonus = (streak // 5) * int(getattr(Config, "CHECKIN_STREAK_BONUS", 1))
        gain = daily + streak_bonus
        milestone_bonus = 0
        hit = None
        for mday, mbonus in MILESTONES:
            if total == mday:
                milestone_bonus = mbonus
                hit = mday
                gain += mbonus
                break
        points = prof["points"] + gain
        await asyncio.to_thread(self.store.set_profile, scope, user_id,
                               points, streak, total, today)
        await asyncio.to_thread(self.store.upsert_day, scope, user_id,
                               today, gain, "daily")
        rank = await asyncio.to_thread(self.store.rank_of, scope, user_id)
        return {
            "status": "ok", "today": today, "points": points, "streak": streak,
            "total_days": total, "gain": gain, "daily": daily,
            "streak_bonus": streak_bonus, "milestone_bonus": milestone_bonus,
            "milestone_hit": hit, "rank": rank,
        }

    async def make_up(self, scope: str, user_id: int,
                      today: str | None = None) -> dict:
        """补签：消耗喵币修复最近一次断签（last_day 的次日）。"""
        today = today or _today()
        prof = await asyncio.to_thread(self.store.get_profile, scope, user_id)
        if prof["last_day"] == "":
            return {"status": "none", "msg": "你还没开始签到，先 /签到 一下吧～"}
        if prof["last_day"] == today:
            return {"status": "today", "msg": "今天已经签过啦，不用补。"}
        if prof["last_day"] == _shift_day(today, -1):
            return {"status": "intact", "msg": "连续签到没断，不需要补签喵～"}
        gap_day = _shift_day(prof["last_day"], 1)
        cost = int(getattr(Config, "CHECKIN_MAKEUP_COST", 20))
        if prof["points"] < cost:
            return {"status": "poor", "msg": "喵币不够补签（需 %d），先多签几天攒一攒～"
                    % cost, "need": cost, "have": prof["points"]}
        new_streak = prof["streak"] + 1
        new_total = prof["total_days"] + 1
        new_points = prof["points"] - cost
        await asyncio.to_thread(self.store.set_profile, scope, user_id,
                               new_points, new_streak, new_total, gap_day)
        await asyncio.to_thread(self.store.upsert_day, scope, user_id,
                               gap_day, 0, "makeup")
        rank = await asyncio.to_thread(self.store.rank_of, scope, user_id)
        return {"status": "ok", "gap_day": gap_day, "cost": cost,
                "points": new_points, "streak": new_streak,
                "total_days": new_total, "rank": rank}

    async def get_profile(self, scope: str, user_id: int,
                         today: str | None = None) -> dict:
        today = today or _today()
        prof = await asyncio.to_thread(self.store.get_profile, scope, user_id)
        prof["today"] = today
        prof["already"] = (prof["last_day"] == today)
        nxt = None
        for mday, mbonus in MILESTONES:
            if prof["total_days"] < mday:
                nxt = (mday, mbonus, mday - prof["total_days"])
                break
        prof["next_milestone"] = nxt
        return prof

    async def leaderboard(self, scope: str, limit: int = 10) -> list[dict]:
        return await asyncio.to_thread(self.store.leaderboard, scope, limit)

    async def rank_of(self, scope: str, user_id: int) -> int:
        return await asyncio.to_thread(self.store.rank_of, scope, user_id)

    async def month_calendar(self, scope: str, user_id: int, year: int, month: int,
                             today: str | None = None) -> dict:
        today = today or _today()
        days = await asyncio.to_thread(self.store.month_days, scope, user_id, year, month)
        return {"year": year, "month": month, "days": days, "today": today}
