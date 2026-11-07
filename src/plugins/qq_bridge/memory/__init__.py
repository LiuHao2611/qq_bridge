# -*- coding: utf-8 -*-
"""memory —— M1 记忆系统（2026-10-20）。

三态（与 ai_mode 既有短期历史的分工）：
  短期 = ai_mode.AIChatter.history（内存+TTL，既有不动）；
  长期 = store.events（值得记的事件/对话摘要，FTS5 + 时间衰减检索）；
  核心 = store.profiles（人物画像：自动快照 facts + 手动覆盖 overrides）
         + store.group_profiles（群档案：氛围/话题/常客）。

组成：
  store.py    三态存储（SQLite data/qq_memory.db；FTS5；软删+恢复；
               滚动清理 OPS-02/03）
  extract.py  LLM 抽取（对话 → facts/events/relation，REDUN-03 JSON 校验）
  manager.py  门面：recall（注入 system 提示的文本）/ ingest（抽取落库）/
               /记忆 命令族服务（画像查看/删除/更正）

契约参考（2026-10-20 实读 MaiBot A-Memorix README v2.0.0）：文本段落 +
关系 + Episode 事件 + 人物画像（自动快照+手动 override）+ 稀疏检索 +
删除可恢复。本地化：零重依赖（sqlite3 内置 FTS5 实测可用），向量检索
二期（在线 embedding）——分层明确，非偷偷降级。

依赖方向：本包只依赖 ..config 与 ..tools.llm（延迟导入），不 import
nonebot / 不 import 主包：可裸单测。
"""
from .store import (KIND_EVENT, KIND_FACT, KIND_SUMMARY, MemoryError,
                    MemoryStore, memory_store)
from .extract import ExtractError, MemoryExtractor, memory_extractor
from .manager import MemoryManager, memory_manager

__all__ = [
    "MemoryStore", "MemoryError", "memory_store",
    "KIND_FACT", "KIND_EVENT", "KIND_SUMMARY",
    "MemoryExtractor", "ExtractError", "memory_extractor",
    "MemoryManager", "memory_manager",
]
