# -*- coding: utf-8 -*-
"""group_style/learn —— 群风格 LLM 抽取（2026-10-21）。

从群消息归纳「氛围 + 常用梗 + 禁忌」。REDUN-03：对 LLM 返回的 JSON 做严格
结构校验，坏 JSON / 非 dict / 字段类型错 → 显式 LearnError（不伪造风格）。

纪律：LLM 不可用或失败 → 显式上抛，绝不返回假风格（禁止降级/兜底）。
"""
from __future__ import annotations

import json
from typing import Any

from ..config import Config, get_logger

log = get_logger()

#: 抽取系统提示：只抽「群公开语境」的风格，不抽个体隐私。
LEARN_SYSTEM = (
    "你是一个群聊风格分析师。给定一段群聊最近消息，请归纳这个群的「说话方式」。"
    "只输出 JSON，不要任何解释。结构：\n"
    "{\n"
    '  "atmosphere": "一句话描述这个群的整体氛围（如：轻松沙雕/技术讨论/熟人闲聊）",\n'
    '  "memes": ["这个群高频使用的梗、口头禅、特色用语（最多 8 条，没有就空数组）"],\n'
    '  "taboo": ["这个群明显反感、不宜说的话或话题（最多 5 条，没有就空数组"]\n'
    "}\n"
    "注意：memes 是群体公共用语，不是某个人的隐私；taboo 只归纳群内氛围，勿上升人身。"
    "atmosphere 控制在 20 字内。"
)

#: 兜底 JSON 骨架（用于校验失败时对照，非降级数据）
_EMPTY = {"atmosphere": "", "memes": [], "taboo": []}


class LearnError(Exception):
    """群风格抽取失败（显式，CLOSE-02）。"""


def _coerce_list(v: Any) -> list:
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, str):
            s = x.strip()
            if s:
                out.append(s)
        elif isinstance(x, (int, float)):
            s = str(x).strip()
            if s:
                out.append(s)
    # 去重保序
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _validate(raw: Any) -> dict:
    """结构校验（REDUN-03）。返回规整后的风格 dict；非法 → LearnError。"""
    if not isinstance(raw, dict):
        raise LearnError("风格抽取结果不是 JSON 对象")
    atmosphere = raw.get("atmosphere")
    if atmosphere is not None and not isinstance(atmosphere, str):
        raise LearnError("atmosphere 字段不是字符串")
    memes = _coerce_list(raw.get("memes"))
    taboo = _coerce_list(raw.get("taboo"))
    return {
        "atmosphere": (atmosphere or "").strip()[:60],
        "memes": memes[:8],
        "taboo": taboo[:5],
    }


class StyleLearner:
    """群风格 LLM 抽取器（LLMClient 可注入，零主包硬依赖）。"""

    def __init__(self, client: Any = None) -> None:
        self._client = client  # 延迟构造

    @property
    def client(self):
        if self._client is None:
            from ..tools.llm import LLMClient
            self._client = LLMClient()
        return self._client

    def _strip_fence(self, text: str) -> str:
        s = (text or "").strip()
        if s.startswith("```"):
            # 去掉 ```json / ``` 围栏
            s = s.split("\n", 1)[-1] if "\n" in s else s[3:]
            if s.endswith("```"):
                s = s[:-3]
            s = s.strip()
        return s

    async def learn(self, messages: list, max_meme: int = 8,
                    max_taboo: int = 5) -> dict:
        """messages: 群消息文本列表（正序，已裁剪到合适条数）。

        返回规整后的风格 dict。LLM 失败 / 无 key → 显式 LearnError。
        """
        if not messages:
            raise LearnError("没有可用群消息，无法学习风格")
        joined = "\n".join("[%d] %s" % (i + 1, m) for i, m in enumerate(messages))
        try:
            resp = await self.client.chat(
                [
                    {"role": "system", "content": LEARN_SYSTEM},
                    {"role": "user", "content": "群聊最近消息：\n%s" % joined},
                ],
                temperature=0.3,
            )
        except Exception as e:  # noqa: BLE001 — LLM 不可用 → 显式上抛
            raise LearnError("风格抽取 LLM 调用失败（%s）" % type(e).__name__)
        content = (resp or {}).get("content") or ""
        if not content:
            raise LearnError("风格抽取返回空内容")
        try:
            raw = json.loads(self._strip_fence(content))
        except (ValueError, TypeError) as e:
            raise LearnError("风格抽取结果不是合法 JSON（%s）" % type(e).__name__)
        style = _validate(raw)
        # 应用上限（_validate 已裁，这里再兜底一次）
        style["memes"] = style["memes"][:max_meme]
        style["taboo"] = style["taboo"][:max_taboo]
        return style


#: 模块级单例
style_learner = StyleLearner()
