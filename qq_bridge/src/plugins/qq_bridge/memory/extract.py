# -*- coding: utf-8 -*-
"""memory.extract —— LLM 记忆抽取（M1 记忆系统，2026-10-20）。

需求（dev_plan M1）：明确记忆三态 + 画像 + 群档案。参考 MaiBot A-Memorix
的 ingest_summary/ingest_text 写入与 personification 的画像抽取。

设计：
  - 每轮 AI 对话后异步抽取（不阻塞回复——asyncio.create_task 后台跑）；
  - 抽取协议（REDUN-03：LLM 输出 JSON，按 schema 校验，不过显式错误）：
      {"facts": [{"key": "字段", "value": "值"}],      → 画像候选（自动快照）
       "events": ["值得记的事件"],                     → 长期库
       "relation": "关系标签（可空）"}
  - **只抽关于对话用户的事实**（不抽第三人隐私——群聊里别人说的话也可能是
    敏感信息，一期只记「本会话用户自己暴露的」+「公共事件」）；
  - LLM 失败/输出坏 JSON → 显式记日志跳过（不静默塞垃圾；下轮对话还能再抽）。

纪律：CLOSE-05 显式超时；本模块不 import nonebot / 不 import 主包：
可裸单测（LLMClient 可注入）。
"""
from __future__ import annotations

import json
from typing import Any

from ..config import Config, get_logger, jlog

log = get_logger()

EXTRACT_SYSTEM = (
    "你是记忆抽取器。读一段群聊/私聊对话（最后一条是机器人要回的话已经给出），"
    "只输出一个 JSON 对象，不要多余文字、不要 markdown 代码块。字段：\n"
    "- facts：关于【对话发起用户】的事实条目数组，每项 {\"key\":\"简短字段名\","
    "\"value\":\"值\"}（如 喜好/习惯/职业/关系）。没有就空数组；\n"
    "- events：值得长期记住的事件数组（字符串，如「他说下周要考试」）。"
    "只记发起用户自己说的或当众说出的公共事件，不要记别人的隐私。没有就空数组；\n"
    "- relation：你判断机器人与这个用户的关系标签（如 熟人/网友/大佬/新人），"
    "不确定就空串。\n"
    "拿不准就不抽——空数组比错记忆好。"
)


class ExtractError(Exception):
    """显式化的抽取失败。"""


class MemoryExtractor:
    """对话 → 记忆条目（LLM 抽取 + JSON 校验）。"""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _resolve_client(self):
        if self._client is not None:
            return self._client
        from ..tools.llm import LLMClient
        self._client = LLMClient()
        return self._client

    @staticmethod
    def validate(plan: Any) -> dict:
        """REDUN-03：校验 LLM 输出的抽取计划。不过则显式错误。"""
        if not isinstance(plan, dict):
            raise ExtractError("抽取结果不是 JSON object")
        facts_raw = plan.get("facts") or []
        events_raw = plan.get("events") or []
        if not isinstance(facts_raw, list) or not isinstance(events_raw, list):
            raise ExtractError("facts/events 必须是数组")
        facts = []
        for f in facts_raw[:10]:
            if isinstance(f, dict) and str(f.get("key") or "").strip() \
                    and str(f.get("value") or "").strip():
                facts.append({"key": str(f["key"])[:40],
                              "value": str(f["value"])[:200]})
        events = [str(e)[:200] for e in events_raw[:5]
                  if str(e or "").strip()]
        relation = str(plan.get("relation") or "").strip()[:40]
        return {"facts": facts, "events": events, "relation": relation}

    async def extract(self, user_text: str, bot_reply: str = "") -> dict:
        """抽一轮对话。返回 {facts, events, relation}（可全空——没东西可记）。"""
        utext = (user_text or "").strip()
        if not utext:
            raise ExtractError("对话内容为空")
        client = self._resolve_client()
        convo = "用户：%s" % utext[:500]
        if bot_reply:
            convo += "\n机器人：%s" % str(bot_reply)[:300]
        try:
            msg = await client.chat([
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": convo},
            ], temperature=0.2)
        except Exception as e:  # noqa: BLE001 — LLMError 等显式上抛
            from ..tools.llm import LLMError
            detail = str(e) if isinstance(e, LLMError) else "%s" % type(e).__name__
            raise ExtractError("记忆抽取调用大模型失败：%s" % detail)
        content = str(msg.get("content") or "").strip()
        if content.startswith("```"):  # 宽容剥壳（只剥围栏，不兜底内容）
            content = content.strip("`").strip()
            if content.lower().startswith("json"):
                content = content[4:].strip()
        try:
            plan = json.loads(content)
        except Exception:
            log.warning(jlog("memory_extract_bad_json", detail=content[:160]))
            raise ExtractError("抽取结果不是 JSON（模型没按协议输出）")
        return self.validate(plan)


#: 模块级单例（单测各自 new 隔离）
memory_extractor = MemoryExtractor()
