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
  - 看门狗（OPS-04）：整体耗时 > 30min → 终止 + 告警 + 退冻结金（CLOSE-04 资金兜底，见项 ⑥）

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
        n03 = self.node_data.get("N03") or {}
        n06 = self.node_data.get("N06") or {}
        return {
            "trace_id": self.trace_id,
            "ok": self.ok,
            "final_output": self.final_output,
            "node_status": {k: v.value for k, v in self.node_status.items()},
            "errors": self.errors,
            # Layer 3 透出：N03 风格参考图 / N06 品控报告（手册 §3.2.2 输出 / §3.4.5）
            "global_style_ref": n03.get("global_style_ref") or "",
            "quality_report": n06.get("quality_report") or {},
            "quality_alerts": n06.get("quality_alerts") or [],
            "failed_shot_list": n06.get("failed_shot_list") or [],
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
        self._refunded_traces = set()  # CLOSE-04：本次 run 退冻结金去重（项 ⑥）

    # -- 外部控制 -------------------------------------------------------------
    def cancel(self):
        """用户主动终止（§4.2 分支规则 3）：触发全链路回滚。"""
        self._cancel = True
        blog("-", "scheduler", "run_cancelled", level="warning")

    # -- 主入口 ---------------------------------------------------------------
    def run(self, project_id, user_id=None, params=None, start_node="N01",
            trace_id=None, on_progress=None):
        trace_id = trace_id or ("DR-%s" % uuid.uuid4().hex[:16])
        ctx = AgentContext(trace_id=trace_id, project_id=project_id,
                           user_id=user_id, params=params or {})
        result = DirectorRunResult(trace_id)
        self._refunded_traces = set()  # CLOSE-04：本次 run 退冻结金去重（项 ⑥）
        blog(trace_id, "scheduler", "run_start", level="info",
             project_id=project_id, start_node=start_node, node_count=len(DAG_NODES))
        started = time.time()
        upstream = {}  # 扁平累积各 Agent 语义输出（segments/enhanced/clips/output_path）
        order = topological_order()
        total = len(order)

        for idx, node_id in enumerate(order):
            if node_id < start_node:   # 跳过早于起点的节点（N01<N02<... 单字符比较）
                continue

            if self._cancel:
                blog(trace_id, "scheduler", "run_rollback", level="warning", reason="user_cancel")
                self._rollback_all(ctx, upstream)
                # CLOSE-04：用户终止 → 退本 trace 未派发的冻结金 + 告警（项 ⑥）
                self._refund_pending_preauths(ctx, reason="用户主动终止退冻结金")
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
                self._emit_progress(on_progress, idx, total, node_id, "skipped")
                continue

            result.node_status[node_id] = NodeStatus.RUNNING
            blog(trace_id, "scheduler", "node_enter", level="info", node=node_id, agent=node.agent)
            self._emit_progress(on_progress, idx, total, node_id, "running")

            # 看门狗（OPS-04）
            if time.time() - started > WATCHDOG_SECS:
                result.ok = False
                result.errors.append({"node": node_id, "reason": "watchdog_timeout"})
                blog(trace_id, "scheduler", "watchdog_timeout", level="error", node=node_id)
                # CLOSE-04：看门狗超时 → 退本 trace 未派发(queued)任务的冻结金 + 告警（项 ⑥）
                self._refund_pending_preauths(ctx, reason="看门狗超时(>30min)未派发任务退冻结金")
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
                # CLOSE-04：节点失败 → 退本 trace 未派发的冻结金 + 告警（项 ⑥）
                self._refund_pending_preauths(ctx, reason="节点失败退冻结金(%s)" % node_id)
                self._emit_progress(on_progress, idx, total, node_id, "failed",
                                    info={"key": e.key, "code": e.code})
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
                # CLOSE-04：节点失败 → 退本 trace 未派发的冻结金 + 告警（项 ⑥）
                self._refund_pending_preauths(ctx, reason="节点失败退冻结金(%s)" % node_id)
                self._emit_progress(on_progress, idx, total, node_id, "failed",
                                    info={"reason": "agent_failed"})
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
            self._emit_progress(on_progress, idx, total, node_id, "done")

        blog(trace_id, "scheduler", "run_finish", level="info",
             ok=result.ok, final_output=result.final_output)
        return result

    # -- 进度透出（§6 进度透出：复用 ws_publish 经 WebSocket / 轮询推前端） --------
    def _emit_progress(self, cb, idx, total, node_id, status, info=None):
        if not callable(cb):
            return
        try:
            cb(node_id=node_id, status=status,
               progress=round((idx + 1) / max(1, total), 3),
               info=info or {})
        except Exception:
            pass  # 进度回调失败不得影响主链路

    # -- 节点执行 + 重试/降级 -------------------------------------------------
    def _run_node(self, node, ctx, upstream, trace_id):
        """执行单个 DAG 节点，按 §6 处理 AgentError 重试；耗尽触发降级并再试一次（项 ④）。

        降级实际切换（REDUN 降级链）：MODEL 类耗尽 → 置 use_fallback_model（入队切降级模型）；
        SCHEDULE 类耗尽 → 置 degraded_priority（入队降为低优先级=降级队列）。降级后给节点
        一次重试机会（degraded 标志防止无限降级），成功则全链路继续，再失败则收敛抛出。
        """
        attempt = 0
        degraded = False
        while True:
            try:
                res = self._exec_node(node, ctx, upstream)
                if not isinstance(res, AgentResult):
                    res = AgentResult(status="success", data=res or {})
                return res
            except AgentError as e:
                p = e.policy
                if not p.retryable or attempt >= p.max_retries:
                    # 耗尽：未降级过则触发降级动作（model→降级模型 / queue→降级队列），再试一次
                    if p.degrade_on_exhaust != "none" and not degraded:
                        self._degrade(node, e, ctx)
                        degraded = True
                        blog(trace_id, "scheduler", "degrade_retry", level="warning",
                             node=node.node_id, action=p.degrade_on_exhaust)
                        # 降级后仅给一次重试机会（attempt 置满，下次失败即收敛，避免无限重试）
                        attempt = p.max_retries
                        continue
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
        # M3 决策预检（手册 §3.2.3）：进入该阶段即攒批推送 A 类决策点。
        # 幂等：同项目+阶段+标题已 pending 复用、已 resolved 跳过（不重复问）。
        # 映射 agent→阶段：script_parser→P2 / visual_control→P3 / sequence_scheduler→P4
        # （post_production=P5 按契约无强制决策；scheduler 自身 N01/N09 跳过）。
        _phase = {"script_parser": "P2", "visual_control": "P3",
                  "sequence_scheduler": "P4"}.get(node.agent)
        if _phase and ctx.project_id:
            try:
                from director.decision_engine import preflight_check
                preflight_check(ctx.project_id, _phase)
            except Exception as _e:
                blog(trace_id, "scheduler", "preflight_failed", level="warning",
                     phase=_phase, error=str(_e)[:160])
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

    def _degrade(self, node, err, ctx):
        """降级触发点（§6 重试耗尽，项 ④ REDUN 降级链）：实际切换降级模型/队列。

        - model：置 ctx.use_fallback_model=True（子 Agent 入队时切 FALLBACK_VIDEO_MODELS 降级模型）
        - queue：置 ctx.degraded_priority=DEGRADED_PRIORITY（入队降为低优先级，等同降级队列）
        切换后由 _run_node 给节点一次重试机会；同时 blog WARN 记录降级类别与动作。
        """
        action = err.policy.degrade_on_exhaust
        if action == "model":
            try:
                from director.enqueue import FALLBACK_VIDEO_MODELS
            except Exception:
                FALLBACK_VIDEO_MODELS = {}
            ctx.use_fallback_model = True
            blog("-", "scheduler", "degrade_triggered", level="warning",
                 node=node.node_id, category=err.category_enum.name, action="model",
                 use_fallback_model=True, fallback_models=list(FALLBACK_VIDEO_MODELS.keys()), key=err.key)
        elif action == "queue":
            try:
                from director.enqueue import DEGRADED_PRIORITY
            except Exception:
                DEGRADED_PRIORITY = -10
            ctx.degraded_priority = DEGRADED_PRIORITY
            blog("-", "scheduler", "degrade_triggered", level="warning",
                 node=node.node_id, category=err.category_enum.name, action="queue",
                 degraded_priority=DEGRADED_PRIORITY, key=err.key)
        else:
            blog("-", "scheduler", "degrade_triggered", level="warning",
                 node=node.node_id, category=err.category_enum.name, action=action, key=err.key)

    def _rollback_all(self, ctx, upstream):
        """用户终止时全链路回滚（§4.2 分支规则 3 / OPS-01）：调用各 Agent Rollback。"""
        for agent in self.agents.values():
            try:
                agent.rollback(ctx)
            except Exception:
                pass

    # -- CLOSE-04 看门狗/失败 资金兜底（项 ⑥） ------------------------------------
    def _refund_pending_preauths(self, ctx, reason):
        """退本 trace 下「未派发(queued)」任务的冻结金 + 透出告警（CLOSE-04 资金安全）。

        仅退 worker 从未派发的任务（status='queued'）：其 pre_auth 仍为 estimated 冻结态、
        且 worker 不会 settle（worker 仅选 'queued'/'retrying'）。已 succeeded(已 settle)/
        failed(已 refund) 的任务不在此列 —— 杜绝「用户白嫖成片」或重复退款（fund loss）。
        幂等：同一次 run 内以 trace_id 去重；points_engine.refund 本身亦幂等。
        """
        if not ctx or not getattr(ctx, "trace_id", None):
            return {"refunded_count": 0, "refunded_total": 0}
        if ctx.trace_id in getattr(self, "_refunded_traces", set()):
            return {"refunded_count": 0, "refunded_total": 0}
        self._refunded_traces.add(ctx.trace_id)

        try:
            from db import get_conn
            from points_engine import refund
            from realtime import publish as ws_publish
        except Exception:
            get_conn = None
            refund = None
            ws_publish = None

        # 1) 查本 trace 下未派发的待退任务
        rows = []
        if get_conn:
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT task_id, pre_auth_id FROM generation_tasks "
                    "WHERE trace_id=? AND status='queued' AND pre_auth_id IS NOT NULL",
                    (ctx.trace_id,)).fetchall()
            finally:
                conn.close()

        # 2) 逐笔退还冻结金（CLOSE-04）
        refunded_count = 0
        refunded_total = 0
        cancelled_ids = []
        for r in rows:
            pa = r["pre_auth_id"]
            try:
                res = refund(pa, reason=reason)
                refunded_total += int((res or {}).get("refunded", 0) or 0)
                refunded_count += 1
                cancelled_ids.append(r["task_id"])
            except Exception:
                pass

        # 3) 终态收敛（CLOSE-01）：取消未派发任务，阻止 worker 后续 pickup
        if cancelled_ids and get_conn:
            conn = get_conn()
            try:
                ph = ",".join("?" for _ in cancelled_ids)
                conn.execute(
                    "UPDATE generation_tasks SET status='cancelled' "
                    "WHERE task_id IN (%s) AND status='queued'" % ph,
                    cancelled_ids)
                conn.commit()
            finally:
                conn.close()

        # 4) 告警透出（REDUN-01 / WS director:<pid>）
        if refunded_count:
            blog(ctx.trace_id, "scheduler", "preauth_refunded", level="warning",
                 reason=reason, refunded_count=refunded_count, refunded_total=refunded_total)
            if ws_publish and getattr(ctx, "project_id", None):
                try:
                    ws_publish("director:%s" % ctx.project_id, "alert", {
                        "level": "warning",
                        "reason": reason,
                        "refunded_count": refunded_count,
                        "refunded_total": refunded_total,
                        "trace_id": ctx.trace_id,
                    })
                except Exception:
                    pass
        return {"refunded_count": refunded_count, "refunded_total": refunded_total}


def build_director():
    """构造默认 Director 调度器（工厂）。"""
    return GlobalScheduler()
