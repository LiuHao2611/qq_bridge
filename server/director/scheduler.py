# -*- coding: utf-8 -*-
"""GlobalScheduler：导演调度全局编排器（对应 DAG N01 Task Receive / N09 Final Output）。

职责（执行手册 §4 · Core Execution Flow）：
  - 接收前端触发（N01），初始化 AgentContext（trace_id 贯穿 LOG-01）
  - 按 DAG 拓扑顺序（N01→N09）调用对应子 Agent 的 Run()
  - 依赖校验：前置节点必须 DONE 才允许后继节点进入（§4.1）
  - 重试与降级（§6）：Agent 抛 AgentError 时按 RETRY_POLICIES + retry_after 退避；
    耗尽触发降级（model→降级模型 / queue→降级队列），再失败则收敛为 FAILED
  - 状态收敛（CLOSE-01）：PENDING→RUNNING→DONE/FAILED/SKIPPED，全链路终态单一
  - 分支规则（§4.2）：用户终止→全链路回滚；质量失败片段在 N08 跳过并告警
  - 看门狗（OPS-04）：整体耗时 > 30min → 终止 + 告警

本实现为「先搭骨架再填充」阶段：单机串行编排，复用现有 ai_local 生成/合成核心，
不改动现有 worker.py/routers.py。full 版将 N04/N05 改为入队 worker + 并行调度。
"""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .dag import DAG_NODES, NODE_BY_ID, topological_order, NodeStatus
from .agent_interface import AgentContext, AgentResult
from .errors import AgentError, retry_after
from .logutil import blog
from .agents import (
    ScriptParserAgent,
    VisualControlAgent,
    SequenceSchedulerAgent,
    PostProductionAgent,
)

WATCHDOG_SECS = 30 * 60  # OPS-04 看门狗 > 30min


class DirectorRunResult:
    """一次全链路编排的结果快照。"""

    def __init__(self, trace_id):
        self.trace_id = trace_id
        self.node_status = {}    # node_id -> NodeStatus
        self.node_data = {}      # node_id -> AgentResult.data
        self.errors = []
        self.final_output = None
        self.ok = True

    def to_dict(self):
        return {
            "trace_id": self.trace_id,
            "ok": self.ok,
            "final_output": self.final_output,
            "node_status": {k: v.value for k, v in self.node_status.items()},
            "errors": self.errors,
        }


class GlobalScheduler:
    def __init__(self):
        self.agents = {
            "script_parser": ScriptParserAgent(),
            "visual_control": VisualControlAgent(),
            "sequence_scheduler": SequenceSchedulerAgent(),
            "post_production": PostProductionAgent(),
        }
        self._cancel = False

    # -- 外部控制 -------------------------------------------------------------
    def cancel(self):
        """用户主动终止（§4.2 分支规则 3）：触发全链路回滚。"""
        self._cancel = True
        blog("-", "scheduler", "run_cancelled", level="warning")

    # -- 主入口 ---------------------------------------------------------------
    def run(self, project_id, user_id=None, params=None, start_node="N01", trace_id=None):
        trace_id = trace_id or ("DR-%s" % uuid.uuid4().hex[:16])
        ctx = AgentContext(trace_id=trace_id, project_id=project_id,
                           user_id=user_id, params=params or {})
        result = DirectorRunResult(trace_id)
        blog(trace_id, "scheduler", "run_start", level="info",
             project_id=project_id, start_node=start_node, node_count=len(DAG_NODES))
        started = time.time()
        upstream = {}  # 扁平累积各 Agent 语义输出（segments/enhanced/clips/output_path）

        for node_id in topological_order():
            if node_id < start_node:   # 跳过早于起点的节点（N01<N02<... 单字符比较）
                continue

            if self._cancel:
                blog(trace_id, "scheduler", "run_rollback", level="warning", reason="user_cancel")
                self._rollback_all(ctx, upstream)
                result.ok = False
                result.errors.append({"node": node_id, "reason": "user_cancel"})
                break

            node = NODE_BY_ID[node_id]

            # 依赖校验（§4.1）：前置节点必须 DONE
            pending_preds = [p for p in node.predecessors
                             if result.node_status.get(p) != NodeStatus.DONE]
            if pending_preds:
                result.node_status[node_id] = NodeStatus.SKIPPED
                blog(trace_id, "scheduler", "node_skipped", level="warning",
                     node=node_id, reason="predecessors_not_done", pending=pending_preds)
                continue

            result.node_status[node_id] = NodeStatus.RUNNING
            blog(trace_id, "scheduler", "node_enter", level="info", node=node_id, agent=node.agent)

            # 看门狗（OPS-04）
            if time.time() - started > WATCHDOG_SECS:
                result.ok = False
                result.errors.append({"node": node_id, "reason": "watchdog_timeout"})
                blog(trace_id, "scheduler", "watchdog_timeout", level="error", node=node_id)
                break

            try:
                res = self._run_node(node, ctx, upstream, trace_id)
            except AgentError as e:
                # 重试耗尽后仍失败 → 终态收敛（CLOSE-01）
                result.node_status[node_id] = NodeStatus.FAILED
                result.ok = False
                result.errors.append({"node": node_id, "key": e.key, "code": e.code,
                                      "message": e.message})
                blog(trace_id, "scheduler", "node_failed", level="error",
                     node=node_id, key=e.key, code=e.code)
                break

            # 处理 AgentResult（可能 partial_fail / failed）
            result.node_data[node_id] = res.data
            if res.status == "failed":
                result.node_status[node_id] = NodeStatus.FAILED
                result.ok = False
                result.errors.append({"node": node_id,
                                      "message": (res.error or {}).get("message") or "agent returned failed"})
                blog(trace_id, "scheduler", "node_failed", level="error",
                     node=node_id, reason="agent_failed")
                break
            if res.status == "partial_fail":
                # BRANCH_RULES：部分分镜失败仍继续（失败片段在 N08 跳过并告警）
                result.node_status[node_id] = NodeStatus.DONE
                blog(trace_id, "scheduler", "node_partial", level="warning", node=node_id)
            else:
                result.node_status[node_id] = NodeStatus.DONE
            upstream.update(res.data)
            if node_id == "N09":
                result.final_output = res.data.get("final_output")
            blog(trace_id, "scheduler", "node_done", level="info", node=node_id)

        blog(trace_id, "scheduler", "run_finish", level="info",
             ok=result.ok, final_output=result.final_output)
        return result

    # -- 节点执行 + 重试/降级 -------------------------------------------------
    def _run_node(self, node, ctx, upstream, trace_id):
        """执行单个 DAG 节点，按 §6 处理 AgentError 重试；耗尽触发降级并抛出。"""
        attempt = 0
        while True:
            try:
                res = self._exec_node(node, ctx, upstream)
                if not isinstance(res, AgentResult):
                    res = AgentResult(status="success", data=res or {})
                return res
            except AgentError as e:
                p = e.policy
                if not p.retryable or attempt >= p.max_retries:
                    # 耗尽：触发降级动作（model→降级模型 / queue→降级队列），再失败则收敛
                    self._degrade(node, e)
                    raise
                delay = retry_after(e, attempt)
                blog(trace_id, "scheduler", "retry_wait", level="warning",
                     node=node.node_id, attempt=attempt + 1, max=p.max_retries,
                     delay=round(delay, 2), degrade=p.degrade_on_exhaust)
                if delay > 0:
                    time.sleep(delay)
                attempt += 1

    def _exec_node(self, node, ctx, upstream):
        """分发到具体 Agent（或调度器自身负责的 N01/N09）。返回 AgentResult。"""
        if node.agent == "scheduler":
            if node.node_id == "N01":
                return AgentResult(status="success", data=self._n01_receive(ctx))
            if node.node_id == "N09":
                return AgentResult(status="success", data=self._n09_final(upstream))
            return AgentResult(status="success", data={})
        agent = self.agents[node.agent]
        agent.init(ctx)                       # 手册 §3 Init
        child_ctx = ctx.child(upstream=dict(upstream))
        return agent.run(child_ctx)           # 手册 §3 Run

    def _n01_receive(self, ctx):
        """N01 Task Receive：参数校验（CLOSE / Parameter 类不重试）。"""
        if not ctx.project_id and not ctx.params.get("story"):
            raise AgentError("INVALID_PARAMETER", "Task Receive 需要 project_id 或 story")
        return {"received": True, "project_id": ctx.project_id}

    def _n09_final(self, upstream):
        """N09 Final Output：成片装配（路径已在 PostProduction 写入 upstream.output_path）。"""
        final = upstream.get("output_path") or upstream.get("final_output")
        clips = upstream.get("clips") or []
        return {"final_output": final, "clip_count": len(clips)}

    def _degrade(self, node, err):
        """降级触发点（§6 重试耗尽）：记录降级动作，full 版切换降级模型/队列。"""
        blog("-", "scheduler", "degrade_triggered", level="warning",
             node=node.node_id, category=err.category_enum.name,
             action=err.policy.degrade_on_exhaust, key=err.key)

    def _rollback_all(self, ctx, upstream):
        """用户终止时全链路回滚（§4.2 分支规则 3 / OPS-01）：调用各 Agent Rollback。"""
        for agent in self.agents.values():
            try:
                agent.rollback(ctx)
            except Exception:
                pass


def build_director():
    """构造默认 Director 调度器（工厂）。"""
    return GlobalScheduler()
