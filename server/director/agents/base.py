# -*- coding: utf-8 -*-
"""子 Agent 基类：统一状态机 + 结构化日志 + Rollback 兜底（AGENTS.md §08）。

所有四个子 Agent 继承 BaseAgent，仅实现 run() 核心逻辑；init/get_status/rollback
提供统一默认实现，避免每个 Agent 重复样板，同时保证 LOG/OPS 纪律一致。
"""
from ..agent_interface import AgentInterface, AgentContext, AgentResult, AgentStatus
from ..logutil import blog


class BaseAgent(AgentInterface):
    agent_id = "AGENT-UNKNOWN"
    agent_name = "Unknown Agent"

    def __init__(self):
        self._ctx = None
        self._status = AgentStatus(agent_id=self.agent_id, state="idle")

    def init(self, ctx: AgentContext) -> None:
        """初始化运行上下文（手册 §3 Init）。"""
        self._ctx = ctx
        self._set_state("idle", 0.0)

    def _set_state(self, state, progress=None, **detail):
        """更新 GetStatus 快照（LOG-03 状态迁移点由调用方打点）。"""
        self._status.state = state
        if progress is not None:
            self._status.progress = progress
        if detail:
            self._status.detail.update(detail)

    def _log(self, event, level="info", **kw):
        trace = self._ctx.trace_id if self._ctx else "-"
        return blog(trace, self.agent_id, event, level, **kw)

    def get_status(self, ctx: AgentContext) -> AgentStatus:
        """返回当前运行态快照（手册 §3 GetStatus）。"""
        return self._status

    def rollback(self, ctx: AgentContext) -> None:
        """回滚副作用（OPS-01：释放临时资源/复位状态）。默认无副作用，子类按需覆盖。"""
        self._log("agent_rollback_noop", level="info")
