# -*- coding: utf-8 -*-
"""导演调度 Agent 统一接口（执行手册 §3 · Sub-Agent Module Specification）。

所有子 Agent 必须实现 AgentInterface，包含四个标准方法：
  Init()      —— 初始化 Agent 运行上下文（加载模型句柄、读取项目全局参数等）。
  Run()       —— 执行本 Agent 的核心职责，返回 AgentResult。
  Rollback()  —— 回滚本 Agent 造成的副作用（清理临时资源、复位任务状态）。
  GetStatus() —— 返回本 Agent 的当前运行状态（供调度器/前端查询）。

这与手册「所有子 Agent 必须实现 AgentInterface 标准接口（Init/Run/Rollback/GetStatus）」
完全对齐；同时遵守 AGENTS.md 的 CLOSE/REDUN/LOG/OPS 纪律（终态收敛、幂等、
结构化日志、临时资源释放、数据 TTL）。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AgentContext:
    """贯穿 DAG 全链路的请求上下文（LOG-01 trace_id 贯穿）。"""
    trace_id: str
    project_id: str
    user_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    upstream: Dict[str, Any] = field(default_factory=dict)   # 上游 Agent 的输出

    def child(self, **overrides) -> "AgentContext":
        """派生子上下文，默认继承 trace_id/project_id/user_id。"""
        base = dict(trace_id=self.trace_id, project_id=self.project_id,
                    user_id=self.user_id, params=dict(self.params), upstream=dict(self.upstream))
        base.update(overrides)
        return AgentContext(**base)


@dataclass
class AgentResult:
    """Agent 标准化输出。"""
    status: str                                   # success | partial_fail | failed
    data: Dict[str, Any] = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None

    def ok(self) -> bool:
        return self.status == "success"


@dataclass
class AgentStatus:
    """GetStatus 返回的运行态快照。"""
    agent_id: str
    state: str = "idle"                           # idle | running | done | failed
    progress: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)


class AgentInterface(ABC):
    """子 Agent 必须实现的统一接口（手册 §3）。"""

    agent_id: str = "AGENT-UNKNOWN"
    agent_name: str = "Unknown Agent"

    @abstractmethod
    def init(self, ctx: AgentContext) -> None:
        """初始化运行上下文。"""
        raise NotImplementedError

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentResult:
        """执行核心职责。"""
        raise NotImplementedError

    @abstractmethod
    def rollback(self, ctx: AgentContext) -> None:
        """回滚副作用。"""
        raise NotImplementedError

    @abstractmethod
    def get_status(self, ctx: AgentContext) -> AgentStatus:
        """返回当前运行状态。"""
        raise NotImplementedError
