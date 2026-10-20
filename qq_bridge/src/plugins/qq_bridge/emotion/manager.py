# -*- coding: utf-8 -*-
"""emotion.manager —— 情感门面（M3，2026-10-20）。

两件事（与 M1 memory 同款门面模式）：
  1. prompt(scope, user_id) → 注入 system 提示的情感文本：
     「现在的情绪」+「对这个用户的亲疏」+「他最近的安排」；
  2. update_from_chat(scope, user_id, user_text, bot_reply) → 对话后更新：
     LLM 判定情感变化（可选）+ 好感度增减 + 记录日程。

动态态度原则（personification）：**语气由 LLM 决定，代码只注入事实**
（情绪/好感度/关系/日程），不用关键词表硬编码「对谁说什么话」。

失败口径（诚实）：
  - 情感计算/存储失败 → 显式记日志，本轮无情感注入（不阻对话）；
  - LLM 判定失败 → 只记日志（下轮还能再判），不假装判过；
  - 没抽出日程 = 正常态（不报错）。

本模块不 import nonebot / 不 import 主包：可裸单测（组件可注入）。
"""
from __future__ import annotations

import time

from ..config import Config, get_logger, jlog
from .mood import MoodEngine, MoodState, EmotionError, mood_engine as _default_engine
from .schedule import ScheduleError, ScheduleExtractor, schedule_extractor as _default_sched
from .store import EmotionStore, emotion_store as _default_store

log = get_logger()


class EmotionManager:
    """情感门面（单例见 emotion_manager）。"""

    def __init__(self, store: EmotionStore | None = None,
                 engine: MoodEngine | None = None,
                 extractor: ScheduleExtractor | None = None) -> None:
        self.store = store or _default_store
        self.engine = engine or _default_engine
        self.extractor = extractor or _default_sched

    # ---------------- 注入 ----------------
    def prompt(self, scope: str, user_id: int, now: float | None = None) -> str:
        """组装情感注入文本（无情感/无关数据 → 空串）。"""
        now = time.time() if now is None else float(now)
        try:
            parts: list[str] = []
            # 1) 当前情绪（先衰减到「此刻」再描述）
            st = self.store.get_mood(scope)
            self.engine.decay(st, now=now)
            parts.append(self.engine.mood_prompt(st))
            # 2) 对这个用户的亲疏
            fav = self.store.get_favor(scope, user_id)
            parts.append(self.engine.relation_prompt(
                fav["favor"], fav.get("relation") or ""))
            # 3) 他最近的安排（到期关心素材：让模型知道该不该问一句）
            scheds = self.store.list_schedules(scope)
            if scheds:
                lines = ["他接下来的安排："]
                for s in scheds[:3]:
                    lines.append("· %s（%s）" % (
                        s.get("title") or "?",
                        time.strftime("%m-%d %H:%M",
                                      time.localtime(float(s.get("due_at") or 0)))))
                parts.append("\n".join(lines))
            return "\n".join(parts)
        except EmotionError as e:
            log.warning(jlog("emotion_prompt_failed", scope=scope,
                             detail=str(e)[:160]))
            return ""

    # ---------------- 更新 ----------------
    def bump(self, scope: str, user_id: int, dv: float = 0.0, da: float = 0.0,
             dfavor: int = 0, emotion: str = "", intensity: float = 0.5,
             now: float | None = None) -> dict:
        """一次更新：情感增量/指定情感名 + 好感度增减。返回更新后快照。"""
        now = time.time() if now is None else float(now)
        st = self.store.get_mood(scope)
        self.engine.decay(st, now=now)
        if emotion:
            self.engine.apply_emotion(st, emotion, intensity, now=now)
        else:
            self.engine.apply_delta(st, dv, da, now=now)
        self.store.save_mood(scope, st)
        fav = self.store.get_favor(scope, user_id)
        new_favor = self.engine.bump_favor(fav["favor"], dfavor)
        if new_favor != fav["favor"] or not fav["updated_at"]:
            self.store.save_favor(scope, user_id, new_favor, fav.get("relation") or "")
            fav = self.store.get_favor(scope, user_id)
        log.info(jlog("emotion_bumped", scope=scope, user_id=user_id,
                      mood=st.text, valence=round(st.valence, 2),
                      favor=fav["favor"], dfavor=dfavor))
        return {"mood": st.as_dict(), "favor": fav}

    async def update_from_chat(self, scope: str, user_id: int, user_text: str,
                               bot_reply: str = "", now: float | None = None) -> dict:
        """对话后更新：抽日程（未来安排）落库；情感/好感度由调用方或
        LLM 判定另行 bump（本方法只做确定性的日程抽取，避免一次对话
        两次 LLM 调用）。

        返回 {schedules: [新增条目]}。抽取没结果 = 正常（空列表）。
        """
        now = time.time() if now is None else float(now)
        out = {"schedules": []}
        if not Config.EMO_SCHEDULE_EXTRACT:
            return out
        try:
            items = await self.extractor.extract(user_text, now=now)
        except ScheduleError as e:
            log.info(jlog("schedule_extract_skipped", scope=scope,
                          detail=str(e)[:160]))
            return out
        for it in items:
            row = self.store.add_schedule(it["title"], it["due_ts"], scope,
                                          user_id=user_id, source="chat")
            out["schedules"].append(row)
        return out

    # ---------------- /情感 命令族服务 ----------------
    def status_text(self, scope: str, user_id: int) -> str:
        """我的情感状态（/情感 我）。"""
        now = time.time()
        st = self.store.get_mood(scope)
        self.engine.decay(st, now=now)
        self.store.save_mood(scope, st)
        fav = self.store.get_favor(scope, user_id)
        from .mood import relation_stage
        stage = fav.get("relation") or relation_stage(fav["favor"])
        lines = ["本喵现在的心情：%s（愉悦 %.2f / 兴奋 %.2f）" % (
            st.text, st.valence, st.arousal),
            "对你的好感度：%d/100（%s）" % (fav["favor"], stage)]
        scheds = self.store.list_schedules(scope)
        if scheds:
            lines.append("记着你的安排：")
            from .schedule import format_due_line
            lines.extend(format_due_line(s, now=now) for s in scheds[:5])
        return "\n".join(lines)


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
emotion_manager = EmotionManager()
