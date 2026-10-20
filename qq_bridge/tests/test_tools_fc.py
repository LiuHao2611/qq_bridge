# -*- coding: utf-8 -*-
"""M2 Function Calling 循环验收（2026-10-20，tools/fc.py）。

覆盖：
  1. 无注册工具 → "tool_bus_empty" 显式错误（路由层据此走纯对话，不是失败）
  2. 单回合：模型直接给最终回复（无 tool_calls）→ 原样返回
  3. 多回合：assistant(tool_calls) 入列 → 串行 dispatch → role:tool 回填 →
     续问 → 最终 content；中间消息**只在副本上**，不污染调用方 messages
  4. 一次多 tool_calls：全部串行执行并按序回填（tool_call_id 对齐）
  5. 回合耗尽（AI_FC_MAX_ROUNDS）→ 显式错误，不偷偷无工具收尾（禁止兜底）
  6. LLM 失败（LLMError）→ 错误文本上抛，不含假成功
  7. 模型给非 JSON 入参 → dispatch 显式错误回填（不崩循环）

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_tools_fc.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# 与 test_config_hot_reload.py 同款命名空间：插 src/plugins 让 `qq_bridge.*`
# 可导入。**不用 src.plugins.qq_bridge.* 限定名**——那会加载第二份 Config 类，
# 使 config_api._find_config_class 命中错的那份，热生效用例假失败
# （既有缺陷全量跑必红，见 AGENTS.md F12；本文件保持单份命名空间不加剧）。
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.config import Config  # noqa: E402
from qq_bridge.tools.fc import FCRunner  # noqa: E402
from qq_bridge.tools.llm import LLMError  # noqa: E402
from qq_bridge.tools.registry import (  # noqa: E402
    ToolContext, ToolRegistry)


class FakeClient:
    """按脚本逐回合返回的假 LLM（鸭子类型对齐 LLMClient.chat）。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[dict] = []  # 每回合收到的 messages 快照

    async def chat(self, messages, tools=None, temperature=None):
        self.seen.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _reg_with_echo():
    reg = ToolRegistry()

    async def echo(args, ctx):
        return "echo:%s" % args.get("x", "")

    from qq_bridge.tools.registry import Tool
    reg.register(Tool(name="echo", description="回显",
                      parameters={"type": "object",
                                  "properties": {"x": {"type": "string"}},
                                  "required": []},
                      handler=echo))
    return reg


def _ctx():
    return ToolContext(scope="g:1", user_id=42, group_id=1, trace_id="t1")


_BASE = [{"role": "system", "content": "s"},
         {"role": "user", "content": "u"}]


@pytest.mark.anyio
async def test_empty_registry_returns_tool_bus_empty():
    runner = FCRunner(ToolRegistry())
    reply, err = await runner.converse(list(_BASE), _ctx())
    assert reply == "" and err == "tool_bus_empty"


@pytest.mark.anyio
async def test_single_round_no_tools():
    client = FakeClient([{"role": "assistant", "content": "你好喵", "tool_calls": []}])
    runner = FCRunner(_reg_with_echo(), client)
    reply, err = await runner.converse(list(_BASE), _ctx())
    assert err == "" and reply == "你好喵"


@pytest.mark.anyio
async def test_multi_round_tool_loop():
    script = [
        # 第 1 回合：模型要调工具
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "name": "echo", "arguments": '{"x": "hi"}'}]},
        # 第 2 回合：模型基于工具结果给最终回复
        {"role": "assistant", "content": "听到了：hi", "tool_calls": []},
    ]
    client = FakeClient(script)
    runner = FCRunner(_reg_with_echo(), client)
    original = list(_BASE)
    reply, err = await runner.converse(original, _ctx())
    assert err == "" and reply == "听到了：hi"
    # 调用方列表不被改动（只在副本上追加）
    assert original == _BASE
    # 第 2 回合的 messages 里应有 assistant(tool_calls) + role:tool 回填
    second = client.seen[1]
    roles = [m["role"] for m in second]
    assert roles == ["system", "user", "assistant", "tool"]
    assert second[-1]["content"] == "echo:hi"
    assert second[-1]["tool_call_id"] == "call_1"


@pytest.mark.anyio
async def test_parallel_tool_calls_serial_dispatch():
    script = [
        {"role": "assistant", "content": "",
         "tool_calls": [
             {"id": "c1", "name": "echo", "arguments": '{"x": "a"}'},
             {"id": "c2", "name": "echo", "arguments": '{"x": "b"}'}]},
        {"role": "assistant", "content": "done", "tool_calls": []},
    ]
    client = FakeClient(script)
    runner = FCRunner(_reg_with_echo(), client)
    reply, err = await runner.converse(list(_BASE), _ctx())
    assert err == "" and reply == "done"
    tool_msgs = [m for m in client.seen[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert [m["content"] for m in tool_msgs] == ["echo:a", "echo:b"]


@pytest.mark.anyio
async def test_rounds_exhausted_explicit_error():
    # 模型永远要调工具 → 8 回合耗尽 → 显式错误
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"id": "c", "name": "echo", "arguments": "{}"}]}
    client = FakeClient([dict(call) for _ in range(20)])
    runner = FCRunner(_reg_with_echo(), client)
    old = Config.AI_FC_MAX_ROUNDS
    Config.AI_FC_MAX_ROUNDS = 3
    try:
        reply, err = await runner.converse(list(_BASE), _ctx())
    finally:
        Config.AI_FC_MAX_ROUNDS = old
    assert reply == ""
    assert "上限" in err and "3" in err
    assert len(client.seen) == 3  # 正好 3 回合，不多不少


@pytest.mark.anyio
async def test_llm_failure_propagates():
    from qq_bridge.tools.llm import LLMError
    client = FakeClient([LLMError("大模型限流（HTTP 429）")])
    runner = FCRunner(_reg_with_echo(), client)
    reply, err = await runner.converse(list(_BASE), _ctx())
    assert reply == "" and "429" in err


@pytest.mark.anyio
async def test_bad_json_args_explicit_in_loop():
    script = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "name": "echo", "arguments": "not-json"}]},
        {"role": "assistant", "content": "好吧", "tool_calls": []},
    ]
    client = FakeClient(script)
    runner = FCRunner(_reg_with_echo(), client)
    reply, err = await runner.converse(list(_BASE), _ctx())
    assert err == "" and reply == "好吧"
    # 非 JSON 入参 → 显式错误回填（不进 dispatch，循环不崩）
    tool_msg = [m for m in client.seen[1] if m["role"] == "tool"][0]
    assert "不是合法 JSON" in tool_msg["content"]
