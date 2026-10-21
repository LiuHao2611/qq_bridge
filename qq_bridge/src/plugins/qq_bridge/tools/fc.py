# -*- coding: utf-8 -*-
"""tools.fc —— Function Calling 多回合循环（M2 总线执行体，2026-10-20）。

对齐 ai-groupmate 的 agent 循环纪律（2026-10-20 实读其 README 配置项）：
  - 单次处理最多 LLM 调用 8 次（AI_FC_MAX_ROUNDS），耗尽显式报错不硬撑；
  - 工具结果写回上下文的长度截断（AI_TOOL_RESULT_MAX_CHARS=6000）；
  - **串行分发**：有副作用工具不并行（安全 > 速度，个人单机场景）；
  - 轮次内消息只活在当次对话：工具调用的中间消息**不落用户历史**
    （历史只记用户原话与最终回复，保持上下文保真——同 ai_mode.note 取舍）。

禁止降级 / 禁止兜底：
  - LLM 失败 → 错误原样上抛（ai_mode.chat_tools 显式告知用户）；
  - 工具失败 → 错误文本回给模型，由模型转告用户，不换备选实现；
  - 回合耗尽 → 显式错误，不偷偷无工具收尾。
"""
from __future__ import annotations

import json

from ..config import Config, get_logger, jlog
from .llm import LLMClient
from .registry import ToolContext, ToolRegistry

log = get_logger()


class FCRunner:
    """把「一次 chat」升级为「带工具的多回合对话」。"""

    def __init__(self, registry: ToolRegistry, client: LLMClient | None = None):
        self.registry = registry
        self.client = client or LLMClient()

    async def converse(self, messages: list, ctx: ToolContext) -> tuple[str, str]:
        """多回合对话主循环。返回 (最终回复文本, 错误文本)。

        messages：调用方已组织好的完整消息表（system + 历史 + 当前 user）；
        本方法只在**副本**上追加 assistant/tool 消息，绝不改动调用方的列表，
        工具中间轮次也不写回用户历史（保真，见模块 docstring）。
        """
        if not self.registry.enabled_tools():
            return "", "tool_bus_empty"
        raw_rounds = getattr(Config, "AI_FC_MAX_ROUNDS", 8)
        max_rounds = max(1, 8 if raw_rounds is None else int(raw_rounds))
        msgs = list(messages)
        tools_payload = self.registry.openai_payload()
        for round_no in range(1, max_rounds + 1):
            try:
                msg = await self.client.chat(msgs, tools=tools_payload)
            except Exception as e:  # noqa: BLE001 — LLMError 及其它，显式上抛
                from .llm import LLMError
                detail = str(e) if isinstance(e, LLMError) else "%s" % type(e).__name__
                log.warning(jlog("fc_llm_failed", scope=ctx.scope, round=round_no,
                                 detail=detail[:160], trace_id=ctx.trace_id))
                return "", detail
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # 模型给出最终回复（可能 content 为空——理论上 llm.py 已挡）
                return str(msg.get("content") or ""), ""
            # assistant 消息（含 tool_calls）入列，再逐工具串行执行。
            # ★ 线格式回写（2026-10-21 修真 bug）：llm.chat 返回的是**内部归一化
            #   扁平格式** {id,name,arguments}；直接塞回 messages，第 2 轮请求
            #   的 assistant.tool_calls 就缺 OpenAI 协议要求的
            #   {id, type:"function", function:{name,arguments}} 嵌套壳
            #   → 服务端 400（真机实证：round=1 成功调 pc_overview、round=2 报
            #   HTTP 400，被误诊为「模型不支持 FC」）。此处必须重新包装。
            wire_calls = [{
                "id": tc.get("id") or "",
                "type": "function",
                "function": {"name": tc.get("name") or "",
                             "arguments": tc.get("arguments") or "{}"},
            } for tc in tool_calls]
            msgs.append({"role": "assistant",
                         "content": msg.get("content") or "",
                         "tool_calls": wire_calls})
            for tc in tool_calls:
                name = tc.get("name") or ""
                raw_args = tc.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                except Exception:  # noqa: BLE001 — 模型给了非 JSON 入参：显式回填，
                    # 不进 dispatch（dispatch 的 None→{} 宽容是给协议空参用的，
                    # 解析失败必须显式告知，不能静默当空参数执行——REDUN-03）
                    log.warning(jlog("fc_bad_tool_args", scope=ctx.scope,
                                     tool=name, detail=str(raw_args)[:120],
                                     trace_id=ctx.trace_id))
                    msgs.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "",
                        "content": "工具 %s 入参不是合法 JSON（%s），请重新按 schema 调用"
                                   % (name, str(raw_args)[:80]),
                    })
                    continue
                result = await self.registry.dispatch(name, args, ctx)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id") or "",
                    "content": result,
                })
            log.info(jlog("fc_round_done", scope=ctx.scope, round=round_no,
                          tools=[tc.get("name") for tc in tool_calls],
                          trace_id=ctx.trace_id))
        # 回合耗尽：显式错误，不无工具收尾（禁止兜底）
        return "", ("工具调用轮数已达上限（%d 轮），请换个说法或分几步说"
                    % max_rounds)


#: 全局单例（插件入口直接用；单测各自 new 隔离）
_default_runner: FCRunner | None = None


def get_runner() -> FCRunner:
    """进程级单例（registry 用 default_registry）。"""
    global _default_runner
    if _default_runner is None:
        from .registry import default_registry
        _default_runner = FCRunner(default_registry)
    return _default_runner
