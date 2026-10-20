# -*- coding: utf-8 -*-
"""M2 工具注册表验收（2026-10-20，tools/registry.py）。

覆盖：
  1. register 校验：空名/缺 description/parameters 非 object/非法 risk → ValueError
  2. openai_payload：OpenAI 兼容 tools 载荷结构（type=function + name/description/parameters）
  3. dispatch 权限分层：owner 仅私聊主人；confirm 拒绝并指路 /pc 通道
  4. dispatch 预算：每 scope 每日上限，耗尽显式告知；跨天重置
  5. REDUN-03 入参校验：缺必填/类型不符/enum 越界 → 显式错误，不进 handler
  6. CLOSE-05 超时：handler 卡死 → wait_for 显式超时文本
  7. CLOSE-02 禁静默：handler 抛异常 → "执行失败（类型）：详情" 显式回给模型
  8. 结果截断：超 AI_TOOL_RESULT_MAX_CHARS 截断并标注
  9. 未知工具/停用工具 → 显式错误
 10. calls/fails 运行统计

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_tools_registry.py -v
"""
from __future__ import annotations

import asyncio
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
from qq_bridge.tools.registry import (  # noqa: E402
    RISK_CONFIRM, RISK_OWNER, RISK_SAFE, Tool, ToolContext, ToolRegistry)


def _echo_tool(name="echo", risk=RISK_SAFE, result="echo-ok"):
    async def handler(args, ctx):
        return result
    return Tool(name=name, description="回显测试工具",
                parameters={"type": "object",
                            "properties": {"x": {"type": "string"}},
                            "required": []},
                handler=handler, risk=risk)


@pytest.fixture()
def reg():
    return ToolRegistry()


@pytest.fixture()
def ctx():
    return ToolContext(scope="g:1", user_id=42, group_id=1, is_private=False,
                       is_owner=False, trace_id="t1")


# ---------------- 1. register 校验 ----------------
def test_register_rejects_bad_tools(reg):
    async def h(a, c):
        return ""

    with pytest.raises(ValueError):
        reg.register(Tool(name="  ", description="d",
                          parameters={"type": "object"}, handler=h))
    with pytest.raises(ValueError):
        reg.register(Tool(name="x", description="  ",
                          parameters={"type": "object"}, handler=h))
    with pytest.raises(ValueError):
        reg.register(Tool(name="x", description="d",
                          parameters={"type": "string"}, handler=h))
    with pytest.raises(ValueError):
        reg.register(Tool(name="x", description="d",
                          parameters={"type": "object"}, handler=h, risk="god"))


def test_register_and_payload(reg):
    reg.register(_echo_tool("echo"))
    payload = reg.openai_payload()
    assert payload == [{
        "type": "function",
        "function": {"name": "echo", "description": "回显测试工具",
                     "parameters": {"type": "object",
                                    "properties": {"x": {"type": "string"}},
                                    "required": []}},
    }]
    assert [t.name for t in reg.enabled_tools()] == ["echo"]


def test_register_overwrite_warns(reg):
    reg.register(_echo_tool("echo"))
    reg.register(_echo_tool("echo", result="v2"))  # 覆盖注册不炸
    assert reg.get("echo") is not None


# ---------------- 2. dispatch 权限分层 ----------------
@pytest.mark.anyio
async def test_dispatch_unknown_tool(reg, ctx):
    out = await reg.dispatch("nope", {}, ctx)
    assert "不存在" in out


@pytest.mark.anyio
async def test_dispatch_owner_only(reg):
    reg.register(_echo_tool("secret", risk=RISK_OWNER))
    # 群聊非主人 → 拒绝
    out = await reg.dispatch("secret", {}, ToolContext(scope="g:1", is_private=False))
    assert "仅限主人私聊" in out
    # 私聊非主人 → 拒绝
    out = await reg.dispatch("secret", {}, ToolContext(scope="u:9", is_private=True,
                                                       is_owner=False))
    assert "仅限主人私聊" in out
    # 私聊主人 → 放行
    out = await reg.dispatch("secret", {}, ToolContext(scope="u:9", is_private=True,
                                                       is_owner=True))
    assert out == "echo-ok"


@pytest.mark.anyio
async def test_dispatch_confirm_rejected_with_signpost(reg, ctx):
    reg.register(_echo_tool("kill", risk=RISK_CONFIRM))
    out = await reg.dispatch("kill", {}, ctx)
    assert "/pc" in out  # 指路命令通道，不静默执行


@pytest.mark.anyio
async def test_dispatch_disabled_tool(reg, ctx):
    t = _echo_tool("off")
    t.enabled = False
    reg.register(t)
    out = await reg.dispatch("off", {}, ctx)
    assert "已停用" in out


# ---------------- 3. 预算 ----------------
@pytest.mark.anyio
async def test_dispatch_daily_budget(reg, ctx):
    reg.register(_echo_tool("echo"))
    old = Config.AI_TOOL_DAILY_BUDGET
    Config.AI_TOOL_DAILY_BUDGET = 2
    try:
        assert reg.budget_left("g:1") == 2
        assert await reg.dispatch("echo", {}, ctx) == "echo-ok"
        assert reg.budget_left("g:1") == 1
        assert await reg.dispatch("echo", {}, ctx) == "echo-ok"
        assert reg.budget_left("g:1") == 0
        out = await reg.dispatch("echo", {}, ctx)
        assert "上限" in out  # 超限显式告知，不进 handler
    finally:
        Config.AI_TOOL_DAILY_BUDGET = old


@pytest.mark.anyio
async def test_budget_cross_day_reset(reg):
    old = Config.AI_TOOL_DAILY_BUDGET
    Config.AI_TOOL_DAILY_BUDGET = 1
    try:
        reg._count_call("g:1")
        assert reg.budget_left("g:1") == 0
        # 手动把记账改到昨天 → 应视为新的一天，额度回满
        reg._budget["g:1"]["date"] = "1999-01-01"
        assert reg.budget_left("g:1") == 1
    finally:
        Config.AI_TOOL_DAILY_BUDGET = old


# ---------------- 4. REDUN-03 入参校验 ----------------
@pytest.mark.anyio
async def test_dispatch_bad_args(reg, ctx):
    entered = []

    async def handler(args, c):
        entered.append(args)
        return "should-not-run"

    reg.register(Tool(name="t", description="d",
                      parameters={"type": "object",
                                  "properties": {
                                      "a": {"type": "string"},
                                      "b": {"type": "integer"},
                                      "c": {"type": "string", "enum": ["x", "y"]}},
                                  "required": ["a"]},
                      handler=handler))
    # 缺必填
    out = await reg.dispatch("t", {}, ctx)
    assert "缺少必填参数 a" in out
    # 类型不符
    out = await reg.dispatch("t", {"a": "ok", "b": "not-int"}, ctx)
    assert "类型应为 integer" in out
    # enum 越界
    out = await reg.dispatch("t", {"a": "ok", "c": "z"}, ctx)
    assert "必须是 x/y 之一" in out
    # 非 dict 入参
    out = await reg.dispatch("t", "not-a-dict", ctx)
    assert "JSON object" in out
    assert entered == []  # 校验不过绝不进 handler


@pytest.mark.anyio
async def test_dispatch_valid_args_pass(reg, ctx):
    reg.register(_echo_tool("echo"))
    out = await reg.dispatch("echo", {"x": "hi"}, ctx)
    assert out == "echo-ok"


# ---------------- 5. CLOSE-05 超时 / CLOSE-02 异常显式化 ----------------
@pytest.mark.anyio
async def test_dispatch_timeout(reg, ctx):
    async def slow(a, c):
        await asyncio.sleep(5)

    reg.register(Tool(name="slow", description="慢工具",
                      parameters={"type": "object"}, handler=slow))
    old = Config.AI_TOOL_TIMEOUT
    Config.AI_TOOL_TIMEOUT = 0.2
    try:
        out = await reg.dispatch("slow", {}, ctx)
        assert "执行超时" in out and "0" in out
    finally:
        Config.AI_TOOL_TIMEOUT = old
    assert reg.get("slow").fails == 1


@pytest.mark.anyio
async def test_dispatch_handler_error_explicit(reg, ctx):
    async def boom(a, c):
        raise RuntimeError("磁盘满了")

    reg.register(Tool(name="boom", description="炸",
                      parameters={"type": "object"}, handler=boom))
    out = await reg.dispatch("boom", {}, ctx)
    assert "执行失败" in out and "RuntimeError" in out and "磁盘满了" in out
    assert reg.get("boom").fails == 1  # 显式记账，绝不静默


@pytest.mark.anyio
async def test_dispatch_result_truncation(reg, ctx):
    long_text = "x" * 50
    reg.register(_echo_tool("echo", result=long_text))
    old = Config.AI_TOOL_RESULT_MAX_CHARS
    Config.AI_TOOL_RESULT_MAX_CHARS = 10
    try:
        out = await reg.dispatch("echo", {}, ctx)
        assert out == "x" * 10 + "……（结果过长已截断）"
    finally:
        Config.AI_TOOL_RESULT_MAX_CHARS = old


@pytest.mark.anyio
async def test_dispatch_counts_calls(reg, ctx):
    reg.register(_echo_tool("echo"))
    await reg.dispatch("echo", {}, ctx)
    await reg.dispatch("echo", {}, ctx)
    assert reg.get("echo").calls == 2
    assert reg.get("echo").fails == 0
