# -*- coding: utf-8 -*-
"""tools.registry —— M2 工具注册表（Function Calling 总线核心，2026-10-20）。

设计来源（dev_plan §五「trust but verify」，2026-10-20 实读 GitHub README）：
  - yaowan233/nonebot-plugin-ai-groupmate v2.5.15：自定义 Agent Tools 注册 +
    langchain agent 决策调用；工具超时 30s、单轮 LLM 上限 8 次、工具结果截断
    6000 字、安全只读工具可并行 / 有副作用工具串行、每 scope 管理范围不跨群。

本地化取舍（个人单机，禁闭门造车亦禁堆依赖）：
  - 不引 langchain：OpenAI 兼容 tools 载荷 + 自有多回合循环（见 fc.py）；
  - 注册表纯内存 + 代码注册：工具是代码能力不是用户数据，无需 ORM；
  - 风险三级：safe（所有人）/ owner（仅私聊主人）/ confirm（高危二次确认）。
    **M2 第一轮只开放 safe + owner 的 curated 子集**——confirm 级（kill/电源/
    剪贴板写/删文件）继续留在 /pc 命令通道走既有确认流，FC 总线二轮再接；
    这不是功能降级，是发布范围决策（两条通道能力全集不变）。

纪律（AGENTS.md + dev_plan §〇 全局约束）：
  - CLOSE-02：未知工具 / 无权限 / 超时 / 执行异常 → 显式错误文本，绝不静默；
  - **禁止降级 / 禁止兜底**：工具失败原样回给模型与用户，不换备选实现；
  - REDUN-03：工具入参先过 JSON Schema 必需项 + 类型校验再执行；
  - LOG-01：register / dispatch 全量结构化日志，trace_id 贯穿；
  - 安全总则 #2：每 scope 每日工具调用计数，超限显式告知（限流非功能降级）；
  - 本模块不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import Config, get_logger, jlog

log = get_logger()

#: 风险分级（dev_plan §〇.2 安全总则：权限分层 群友 < 主人 < 主人+确认）
RISK_SAFE = "safe"
RISK_OWNER = "owner"
RISK_CONFIRM = "confirm"
_RISKS = (RISK_SAFE, RISK_OWNER, RISK_CONFIRM)

#: JSON Schema 简单类型 → Python 类型（REDUN-03 入参校验用；object/array 只验 isinstance）
_SCHEMA_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


@dataclass
class ToolContext:
    """一次工具调用的会话上下文（由调用方注入——注册表自己不碰 nonebot 事件）。"""

    scope: str = ""          # "g:<群号>" / "u:<QQ>"（与 ai_mode.event_scope 同构）
    user_id: int = 0
    group_id: int = 0
    is_private: bool = False
    is_owner: bool = False
    trace_id: str = ""
    #: 协议通道（双轨并行：napcat / official；提醒等异步回传要用）
    channel: str = "napcat"


@dataclass
class Tool:
    """一个可被大模型调用的工具。

    handler: ``async (args: dict, ctx: ToolContext) -> str``，返回给模型的
    文本结果；失败请 ``raise``（dispatch 会显式化，禁止在 handler 里静默
    返回"成功"假象）。
    """

    name: str
    description: str
    parameters: dict                 # JSON Schema（object）
    handler: Callable[[dict, ToolContext], Awaitable[str]]
    risk: str = RISK_SAFE            # safe | owner | confirm
    enabled: bool = True
    #: 运行统计（内存态，重启清零可接受）
    calls: int = 0
    fails: int = 0


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class ToolRegistry:
    """工具注册表 + 分发器（单例见 default_registry）。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        #: scope -> {"date": ..., "count": ...}（每日预算，跨天自动重置）
        self._budget: dict[str, dict[str, Any]] = {}

    # ---------------- 注册 / 查询 ----------------
    def register(self, tool: Tool) -> Tool:
        """注册工具。重名覆盖并打警告（热更新语义，供测试与二轮扩展）。"""
        name = (tool.name or "").strip()
        if not name:
            raise ValueError("tool.name 不能为空")
        if not (tool.description or "").strip():
            raise ValueError("工具 %s 缺少 description（模型靠它决定调不调）" % name)
        if not isinstance(tool.parameters, dict) or tool.parameters.get("type") != "object":
            raise ValueError("工具 %s 的 parameters 必须是 JSON Schema object" % name)
        if tool.risk not in _RISKS:
            raise ValueError("工具 %s 风险分级必须是 %s" % (name, "/".join(_RISKS)))
        if name in self._tools:
            log.warning(jlog("tool_register_overwrite", tool=name))
        self._tools[name] = tool
        log.info(jlog("tool_registered", tool=name, risk=tool.risk,
                      enabled=bool(tool.enabled)))
        return tool

    def unregister(self, name: str) -> bool:
        return self._tools.pop((name or "").strip(), None) is not None

    def get(self, name: str) -> Tool | None:
        return self._tools.get((name or "").strip())

    def enabled_tools(self) -> list[Tool]:
        return [t for t in self._tools.values() if t.enabled]

    def openai_payload(self) -> list[dict]:
        """OpenAI 兼容 chat/completions 的 tools 载荷（dev_plan：对齐事实标准）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.enabled_tools()
        ]

    # ---------------- 预算（安全总则 #2：每 scope 每日调用上限） ----------------
    def budget_left(self, scope: str) -> int | None:
        """剩余次数；None = 不限（配置 0）。"""
        cap = int(getattr(Config, "AI_TOOL_DAILY_BUDGET", 0) or 0)
        if cap <= 0:
            return None
        b = self._budget.get(scope)
        if not b or b.get("date") != _today():
            return cap
        return max(0, cap - int(b.get("count", 0)))

    def _count_call(self, scope: str) -> None:
        b = self._budget.setdefault(scope, {"date": _today(), "count": 0})
        if b.get("date") != _today():
            b["date"] = _today()
            b["count"] = 0
        b["count"] = int(b.get("count", 0)) + 1

    # ---------------- 入参校验（REDUN-03） ----------------
    @staticmethod
    def _validate_args(tool: Tool, args: Any) -> str:
        """返回错误文本；空串 = 通过。"""
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return "入参必须是 JSON object（收到 %s）" % type(args).__name__
        props = tool.parameters.get("properties") or {}
        required = tool.parameters.get("required") or []
        for key in required:
            if key not in args:
                return "缺少必填参数 %s" % key
        for key, val in args.items():
            spec = props.get(key)
            if not isinstance(spec, dict):
                continue  # 未声明的参数忽略（模型偶尔会编，不因此失败整次调用）
            t = spec.get("type")
            if t in _SCHEMA_TYPES and not isinstance(val, _SCHEMA_TYPES[t]):
                return "参数 %s 类型应为 %s（收到 %s）" % (
                    key, t, type(val).__name__)
            if t == "object" and not isinstance(val, dict):
                return "参数 %s 类型应为 object" % key
            if t == "array" and not isinstance(val, list):
                return "参数 %s 类型应为 array" % key
            enum = spec.get("enum")
            if enum and val not in enum:
                return "参数 %s 必须是 %s 之一" % (key, "/".join(str(e) for e in enum))
        return ""

    # ---------------- 分发 ----------------
    async def dispatch(self, name: str, args: dict, ctx: ToolContext) -> str:
        """执行一个工具调用，返回**给模型看的文本结果**。

        所有失败路径都显式化为文本（CLOSE-02 + 禁止兜底）：模型拿到诚实错误，
        转告用户；绝不假装成功、绝不静默换备选。
        """
        t0 = time.perf_counter()
        tool = self.get(name)
        if tool is None:
            log.warning(jlog("tool_dispatch_unknown", tool=name, scope=ctx.scope,
                             trace_id=ctx.trace_id))
            return "工具 %s 不存在（可用工具以本回合 tools 清单为准）" % name
        if not tool.enabled:
            return "工具 %s 当前已停用" % name
        # 权限分层（dev_plan §〇.2）：owner 仅私聊主人；confirm 第一轮不注册，
        # 若真被调用也明确拒绝并指路命令通道（不静默执行）。
        if tool.risk == RISK_OWNER and not (ctx.is_private and ctx.is_owner):
            log.warning(jlog("tool_dispatch_denied", tool=name, scope=ctx.scope,
                             user_id=ctx.user_id, trace_id=ctx.trace_id))
            # ★ 转述纪律（2026-10-21 真机实证）：拒绝原因必须可被模型**如实**
            #   转述。此前文案只说「仅限主人私聊使用」，模型自由发挥成
            #   「你又不是本喵主人」——把场景限制说成了身份否定（主人本人
            #   在群里也被否认了主人的身份）。补明确指引：与身份无关，
            #   不得否认主人身份。
            return ("工具 %s 仅限主人私聊使用（群聊一律不开放，保护主人硬件"
                    "隐私）。这是场景限制、与对方身份无关：即使说话的就是"
                    "主人本人，也要如实转告「宿主机管家只在小喵私聊里用，"
                    "主人私聊喊我就行」，**不得**说对方不是主人" % name)
        if tool.risk == RISK_CONFIRM:
            return ("工具 %s 需要二次确认，请让主人用 /pc 命令通道操作"
                    % name)
        # 每日预算（限流，非功能降级：超限是显式告知不是换路径）
        left = self.budget_left(ctx.scope)
        if left is not None and left <= 0:
            log.info(jlog("tool_dispatch_budget_exhausted", tool=name,
                          scope=ctx.scope, trace_id=ctx.trace_id))
            return "今日工具调用次数已用完（每 scope 每日上限），明天再试"
        # 入参校验（REDUN-03）
        arg_err = self._validate_args(tool, args)
        if arg_err:
            log.info(jlog("tool_dispatch_bad_args", tool=name, scope=ctx.scope,
                          detail=arg_err[:120], trace_id=ctx.trace_id))
            return "工具 %s 入参不对：%s" % (name, arg_err)
        # 执行（CLOSE-05 显式超时；串行——有副作用工具不并行，ai-groupmate 纪律）
        timeout = float(getattr(Config, "AI_TOOL_TIMEOUT", 30.0) or 30.0)
        try:
            result = await asyncio.wait_for(tool.handler(args or {}, ctx), timeout=timeout)
        except asyncio.TimeoutError:
            tool.fails += 1
            log.warning(jlog("tool_dispatch_timeout", tool=name, scope=ctx.scope,
                             timeout=timeout, trace_id=ctx.trace_id))
            return "工具 %s 执行超时（%ss）" % (name, int(timeout))
        except Exception as e:  # noqa: BLE001 — 显式化，绝不静默（禁止兜底）
            tool.fails += 1
            log.warning(jlog("tool_dispatch_failed", tool=name, scope=ctx.scope,
                             error=type(e).__name__, detail=str(e)[:160],
                             trace_id=ctx.trace_id))
            return "工具 %s 执行失败（%s）：%s" % (name, type(e).__name__, str(e)[:200])
        tool.calls += 1
        self._count_call(ctx.scope)
        # 结果截断（ai-groupmate：单次工具结果写回上下文上限 6000 字）
        cap = int(getattr(Config, "AI_TOOL_RESULT_MAX_CHARS", 6000) or 6000)
        text = str(result if result is not None else "")
        if len(text) > cap:
            text = text[:cap] + "……（结果过长已截断）"
        log.info(jlog("tool_dispatched", tool=name, scope=ctx.scope,
                      user_id=ctx.user_id, risk=tool.risk,
                      result_len=len(text),
                      elapsed_ms=int((time.perf_counter() - t0) * 1000),
                      trace_id=ctx.trace_id))
        return text


#: 全局单例（插件入口与单测各自可控：单测请 new 隔离）
default_registry = ToolRegistry()
