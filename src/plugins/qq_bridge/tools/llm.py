# -*- coding: utf-8 -*-
"""tools.llm —— OpenAI 兼容 chat/completions 客户端（含 tools 载荷，M2 2026-10-20）。

与 ai_mode.AIChatter 同协议同纪律，但**独立成类**：tools 包不依赖 ai_mode
（ai_mode 反向注入 converse 回调，见 ai_mode.chat_tools），保证可裸单测。

纪律（逐条对齐 ai_mode.AIChatter._post， Verified 2026-10-20）：
  - CLOSE-05：httpx 显式超时（Config.AI_TIMEOUT，默认 60s）；
  - CLOSE-02：未配 Key / 401 / 429 / 5xx / 超时 / 结构异常 → 显式错误，
    绝不静默不回；**模型不支持 tools 报 4xx 也如实上抛**（禁止兜底：
    不自动去掉 tools 重试——那是偷偷降级）；
  - REDUN-02：仅 ConnectError / 429 / 5xx 指数退避重试（1s/2s + 抖动，
    总尝试封顶 3 次）；4xx 与超时不重试；
  - LOG-04：Key 只从 Config 读，严禁落日志；
  - trust_env=False：绕开本机 HTTP_PROXY（ai_mode 同款取舍）。
"""
from __future__ import annotations

import asyncio
import random

import httpx

from ..config import Config, get_logger, jlog

log = get_logger()

#: 用户可能把「完整端点 URL」直接粘进 AI_API_BASE（ai_mode 同款兼容）
_ENDPOINT_SUFFIXES = ("/chat/completions", "/responses", "/completions", "/messages")


def resolve_chat_url(base: str) -> str:
    """AI_API_BASE → OpenAI 兼容 chat/completions 全端点（幂等）。"""
    u = (base or "").strip().rstrip("/")
    low = u.lower()
    for suf in _ENDPOINT_SUFFIXES:
        if low.endswith(suf):
            u = u[: len(u) - len(suf)]
            break
    return u.rstrip("/") + "/chat/completions"


class LLMError(Exception):
    """显式化的 LLM 调用失败（message 即给上层的错误文本）。"""


class LLMClient:
    """chat/completions 客户端；tools 可选（M2 Function Calling 载荷）。"""

    async def chat(self, messages: list, tools: list | None = None,
                   temperature: float | None = None,
                   model: str | None = None) -> dict:
        """一轮对话。成功返回 assistant message dict：
        ``{"role": "assistant", "content": str, "tool_calls": [...]}``
        （无工具调用时 tool_calls 为空列表——**保持键存在**，调用方统一判断）。

        model：可覆盖默认模型（M4 视觉理解用 AI_VISION_MODEL；**不传就用
        Config.AI_MODEL，不做任何自动回落**——视觉/文本模型各司其职）。

        失败抛 LLMError（显式，绝不返回假成功）。
        """
        if not Config.AI_API_KEY:
            raise LLMError("no_key")
        payload = {
            "model": (model or "").strip() or Config.AI_MODEL,
            "messages": messages,
            "temperature": (temperature if temperature is not None
                            else Config.AI_TEMPERATURE),
            "max_tokens": Config.AI_MAX_TOKENS,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": "Bearer %s" % Config.AI_API_KEY,
            "Content-Type": "application/json",
        }
        url = resolve_chat_url(Config.AI_API_BASE)
        retries = max(0, min(int(Config.AI_MAX_RETRIES), 2))  # 封顶总尝试 3 次
        last_err = ""
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(Config.AI_TIMEOUT), trust_env=False
                ) as cli:
                    resp = await cli.post(url, json=payload, headers=headers)
            except httpx.ConnectError:
                last_err = "连不上大模型服务（%s）" % Config.AI_API_BASE
            except httpx.TimeoutException:
                raise LLMError("大模型响应超时（%ss）" % Config.AI_TIMEOUT)
            except Exception as e:  # noqa: BLE001 — CLOSE-02 显式失败
                raise LLMError("AI 调用失败（%s）" % type(e).__name__)
            sc = resp.status_code
            if sc < 400:
                try:
                    j = resp.json()
                except Exception as e:  # noqa: BLE001
                    raise LLMError("大模型返回不是 JSON（%s）" % type(e).__name__)
                msg = ((j or {}).get("choices") or [{}])[0].get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                # 归一化 tool_calls：保证 id/name/arguments(str) 三键齐
                norm_calls = []
                for i, tc in enumerate(tool_calls):
                    fn = (tc or {}).get("function") or {}
                    norm_calls.append({
                        "id": str((tc or {}).get("id") or ("call_%d" % i)),
                        "name": str(fn.get("name") or ""),
                        "arguments": fn.get("arguments") or "{}",
                    })
                content = str(msg.get("content") or "").strip()
                if not content and not norm_calls:
                    raise LLMError("大模型回了空内容")
                return {"role": "assistant", "content": content,
                        "tool_calls": norm_calls}
            if sc in (401, 403):
                raise LLMError("API Key 无效或无权访问（HTTP %d）" % sc)
            if sc == 429:
                last_err = "大模型限流（HTTP 429，请求太频繁或额度不足）"
            elif sc >= 500:
                last_err = "大模型服务端故障（HTTP %d）" % sc
            else:
                # 其它 4xx（含模型不支持 tools 的 400）：确定性失败，不重试、
                # 不剥离 tools 重试（禁止兜底——如实告知用户去关工具总线或换模型）。
                # ★ 2026-10-21：必须带上服务端原始错误体（截断），否则无法区分
                #   「模型不支持 FC」与「请求载荷畸形」——真机曾因消息格式 bug 报
                #   400 被误诊为不支持 FC（IMA 精确异常：错误要可定位）。
                body = ""
                try:
                    body = (resp.text or "").strip().replace("\n", " ")[:200]
                except Exception:  # noqa: BLE001
                    pass
                log.warning(jlog("llm_http_4xx", status=sc, body=body))
                hint = ("当前模型可能不支持 Function Calling，可到配置页关闭"
                        "「AI 工具总线」或换支持工具调用的模型")
                raise LLMError(
                    "大模型接口报错 HTTP %d（%s）| 服务端返回：%s"
                    % (sc, hint, body or "（无响应体）"))
            if attempt < retries:
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.5))
        raise LLMError(last_err or "大模型调用失败")
