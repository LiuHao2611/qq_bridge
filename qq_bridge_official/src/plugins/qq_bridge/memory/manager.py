# -*- coding: utf-8 -*-
"""memory.manager —— 记忆门面（M1 记忆系统，2026-10-20）。

两件事：
  1. recall(scope, user_id, query) → 注入 system 提示的记忆文本：
     核心画像（override 优先）+ 相关长期记忆（FTS+时间衰减）+ 群档案；
  2. ingest(scope, user_id, user_text, bot_reply) → LLM 抽取 → 落库
     （画像自动快照 / 长期事件 / 关系标签）。

接入点（__init__.py 接线，本模块不碰 nonebot）：
  - ai_mode.chat 前：extra_system += recall 文本；
  - 对话后：asyncio.create_task(ingest(...)) 后台跑（不阻塞回复）。

隐私口径（一期保守策略，诚实记录）：只抽「本会话发起用户自己暴露的
事实」+「公共事件」；第三人隐私不抽（EXTRACT_SYSTEM 已约束，validate 再
挡一层结构）。/记忆 命令族提供主人查看/删除/更正入口。

本模块不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import time
from typing import Any

from ..config import Config, get_logger, jlog
from .extract import ExtractError, MemoryExtractor, memory_extractor as _default_ext
from .store import MemoryError, MemoryStore, memory_store as _default_store

log = get_logger()


class MemoryManager:
    """记忆门面（单例见 memory_manager）。"""

    def __init__(self, store: MemoryStore | None = None,
                 extractor: MemoryExtractor | None = None) -> None:
        self.store = store or _default_store
        self.extractor = extractor or _default_ext

    # ---------------- recall：注入文本 ----------------
    def recall_text(self, scope: str, user_id: int, query: str = "") -> str:
        """组装注入 system 提示的记忆文本（无记忆返回空串）。"""
        try:
            parts: list[str] = []
            # 1) 核心画像（本会话用户）
            prof = self.store.get_profile(scope, user_id)
            view = prof.get("view") or {}
            if view:
                items = ["%s：%s" % (k, v) for k, v in list(view.items())[:12]]
                parts.append("你对这个用户的了解：\n" + "\n".join(items))
            if prof.get("relation"):
                parts.append("你与他的关系：%s" % prof["relation"])
            # 2) 相关长期记忆（FTS+衰减；无查询词回落最近几条）
            if query and query.strip():
                hits = self.store.search(query, scope,
                                         limit=int(getattr(Config, "MEMORY_RECALL_LIMIT", 5) or 5))
            else:
                hits = self.store.recent(scope, limit=3)
            if hits:
                lines = []
                for h in hits[:5]:
                    age_d = max(0, int((time.time() - float(h.get("created_at") or 0)) / 86400))
                    lines.append("· %s（%d 天前）" % (str(h.get("text") or "")[:120], age_d))
                parts.append("你记得的相关事情：\n" + "\n".join(lines))
            # 3) 群档案（群会话才注入）
            if scope.startswith("g:"):
                gp = self.store.get_group_profile(scope)
                if gp.get("vibe") or gp.get("topics"):
                    seg = "这个群的情况："
                    if gp.get("vibe"):
                        seg += "\n氛围：%s" % gp["vibe"]
                    if gp.get("topics"):
                        seg += "\n常聊话题：%s" % "、".join(
                            str(t)[:20] for t in gp["topics"][:6])
                    parts.append(seg)
            return "\n\n".join(parts)
        except MemoryError as e:
            # 记忆读取失败不阻断对话（CLOSE-02：记日志，本轮无记忆注入）
            log.warning(jlog("memory_recall_failed", scope=scope,
                             detail=str(e)[:160]))
            return ""

    # ---------------- ingest：抽取落库 ----------------
    async def ingest(self, scope: str, user_id: int, user_text: str,
                     bot_reply: str = "") -> dict:
        """抽一轮对话并落库。返回 {facts, events, relation, stored}。

        全失败路径显式（ExtractError/MemoryError 上抛由调用方记日志）——
        但抽取本身「没东西可抽」不是错误（返回全空 + stored=False）。
        """
        plan = await self.extractor.extract(user_text, bot_reply)
        stored = {"facts": 0, "events": 0, "relation": False}
        if plan["facts"]:
            facts = {f["key"]: f["value"] for f in plan["facts"]}
            self.store.set_facts(scope, user_id, facts)
            stored["facts"] = len(facts)
        for ev in plan["events"]:
            self.store.add_event(ev, scope, user_id=user_id, kind="event")
            stored["events"] += 1
        if plan["relation"]:
            self.store.set_relation(scope, user_id, plan["relation"])
            stored["relation"] = True
        if stored["facts"] or stored["events"] or stored["relation"]:
            log.info(jlog("memory_ingested", scope=scope, user_id=user_id,
                          **stored))
        return {"facts": plan["facts"], "events": plan["events"],
                "relation": plan["relation"], "stored": stored}

    # ---------------- /记忆 命令族服务 ----------------
    def profile_view(self, scope: str, user_id: int) -> str:
        """人物画像人话文本（/记忆 我）。"""
        prof = self.store.get_profile(scope, user_id)
        view = prof.get("view") or {}
        if not view and not prof.get("relation"):
            return "本喵对你还没有什么了解（多聊聊天就熟了）"
        lines = []
        if prof.get("relation"):
            lines.append("关系：%s" % prof["relation"])
        for k, v in list(view.items())[:15]:
            src = "（你更正的）" if k in (prof.get("overrides") or {}) else ""
            lines.append("· %s：%s%s" % (k, v, src))
        return "\n".join(lines)

    def forget(self, scope: str, user_id: int, key: str) -> str:
        """删除一条画像事实（override 优先删，否则删快照）。"""
        prof = self.store.get_profile(scope, user_id)
        if key in (prof.get("overrides") or {}):
            self.store.drop_override(scope, user_id, key)
            return "已忘记「%s」（更正记录也删了）" % key
        if key in (prof.get("facts") or {}):
            self.store.drop_fact(scope, user_id, key)
            return "已忘记「%s」" % key
        raise MemoryError("没有叫「%s」的记忆（/记忆 我 看现有字段）" % key)


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
memory_manager = MemoryManager()
