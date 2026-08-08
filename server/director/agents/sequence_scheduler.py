# -*- coding: utf-8 -*-
"""N04 Task Dispatch + N05 Shot Generate —— SequenceSchedulerAgent（手册 §3.3 / 设计逻辑「时序衔接调度 Agent」）。

职责：分镜任务时序编排、算力调度、首尾帧衔接控制。

full 版 ②（本次落地）：不再直调 ai_local.generate_segment_video，改为向 generation_tasks 入队，
由 worker 后台消费（复用其 重试 / 退款 / 超时 / settle 全链路）。调度器入队后轮询任务状态
（REDUN-01：WS 透出 + 轮询兜底）跟踪 worker 进度，直至全部收敛终态再汇总返回。

相对骨架新增/调整（对齐手册 §3.3 + 设计逻辑）：
  - **CLOSE-04（资金安全，最高优先级）**：每个片段入队前先 pre-auth（冻结即扣减）；
    pre-auth 失败（如积分不足）→ 该片段降级为 fail，不阻断其他片段；插入失败自动退款（见 enqueue）。
  - **CLOSE-02 幂等**：idempotency_key = `dir-<trace_id>-<seg_id>`，杜绝 Date.now()+random。
  - **CLOSE-01 闭环**：终态（succeeded/failed/cancelled）由 worker 收敛；调度器设看门狗上限，
    超时未收敛则告警（worker 自有 30min 看门狗回收卡死任务）。
  - **断点续跑（resume）**：片段已 succeeded 且文件存在则跳过（Observability/非破坏性）。
  - **首尾帧锚定（enable_tail_frame）**：参数透传入队（first_frame 当前引擎忽略，留作 full 版兼容）；
    跨片段尾帧链式衔接需 worker 侧串联，属 full 版引擎补全（见设计文档 §6 项 ⑦/⑧）。
  - **并行度**：由 worker 消费节奏决定（worker 单任务串行 + 重试用）；调度器只负责入队与汇聚。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..agent_interface import AgentContext, AgentResult
from ..errors import AgentError
from .base import BaseAgent
from common import ApiError

# 必须与 worker.py / ai_local.py 的真实落盘目录一致：仓库根目录下的 outputs
# （worker 用 os.path.join(dirname(dirname(__file__)),"outputs")，__file__ 在 server/ 故为仓库根/outputs；
#  本文件在 server/director/agents/，需向上 4 级才到仓库根，否则 resume 检测会漏掉已生成文件）。
_OUTPUT_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
OUTPUTS = os.path.join(_OUTPUT_BASE, "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

# 跟踪上限（略大于 worker 30min 看门狗，确保卡死任务先被回收再收敛）OPS-04
SEQ_WAIT_CAP_SECS = 30 * 60 + 120
SEQ_POLL_SECS = 3.0
DEFAULT_MODEL_ID = "seedance-2.0-mini"


class SequenceSchedulerAgent(BaseAgent):
    agent_id = "AGENT-SEQUENCE-SCHEDULER"
    agent_name = "SequenceSchedulerAgent"

    def run(self, ctx: AgentContext) -> AgentResult:
        self._set_state("running", 0.0)
        self._log("agent_run", level="info")

        segments = ctx.upstream.get("segments") or []
        if not segments:
            self._set_state("failed", 0.0)
            raise AgentError("DISPATCH_FAILED", "上游缺少 segments，无法分发生成任务")

        bound = ctx.upstream.get("bound_assets") or {}
        enable_tail = ctx.params.get("enable_tail_frame", True)   # 手册 §3.3.5 默认开启
        resume = ctx.params.get("resume", True)                    # 断点续跑默认开启
        priority = int(ctx.params.get("priority_level", 0) or 0)
        trace_id = ctx.trace_id or "DR-unknown"
        project_id = ctx.project_id
        resolution0 = ctx.params.get("resolution") or "720p"

        try:
            from director.enqueue import (enqueue_segment, fetch_task_statuses,
                                          fetch_segment_video_url, resolve_segment_id,
                                          persist_segment)
            from realtime import publish as ws_publish
        except Exception:
            enqueue_segment = None
            fetch_task_statuses = None
            fetch_segment_video_url = None
            resolve_segment_id = None
            persist_segment = None
            ws_publish = None

        by_seg = {}          # real_seg_id -> result dict（保持片段顺序汇总）
        task_ids = []        # (real_seg_id, task_id)
        real_ids = []        # 与 segments 顺序对应的真实 segment_id
        total = len(segments)

        for i, seg in enumerate(segments):
            original_seg_id = seg.get("segment_id") or ("seg_%d" % i)
            # 解析真实 segment_id（按位置对齐既有 DB 行 / 净新增派生稳定 id）
            if resolve_segment_id:
                real_seg_id, exists = resolve_segment_id(seg, project_id, i)
            else:
                real_seg_id, exists = original_seg_id, False
            real_ids.append(real_seg_id)
            # 断点续跑：已有成功片段且文件存在 → 跳过（Observability/非破坏性）
            exist_url = self._existing_video_url(real_seg_id)
            if resume and exist_url and self._local_exists(exist_url):
                by_seg[real_seg_id] = {"segment_id": real_seg_id, "level": "skip",
                                       "note": "断点续跑：已有成功片段", "video_url": exist_url}
                if ws_publish:
                    ws_publish("director:%s" % project_id, "segment",
                               {"segment_id": real_seg_id, "status": "skip", "progress": 100,
                                "video_url": exist_url, "task_id": None})
                self._set_state("running", (i + 1) / total * 100.0)
                continue

            if enqueue_segment is None:
                self._set_state("failed", 0.0)
                raise AgentError("DISPATCH_FAILED", "入队模块不可用（director.enqueue 导入失败）")

            # 落库为真实 segments 行（worker 依赖），仅当行不存在时创建（不覆盖用户既有分镜）
            if (not exists) and persist_segment:
                real_seg_id = persist_segment(seg, project_id, i,
                                              bound.get(original_seg_id), resolution0)
            # 入队参数
            duration = int(seg.get("duration") or 12)
            resolution = resolution0 or seg.get("resolution") or "720p"
            model_cfg = ctx.params.get("model_config") or {}
            model_id = model_cfg.get("video") or DEFAULT_MODEL_ID
            # 项 ④ REDUN 降级链：降级标志透传 → 切换降级模型（worker 走同一消费链路）
            if getattr(ctx, "use_fallback_model", False):
                try:
                    from director.enqueue import FALLBACK_VIDEO_MODELS
                except Exception:
                    FALLBACK_VIDEO_MODELS = {}
                fb = FALLBACK_VIDEO_MODELS.get(model_id, model_id)
                if fb != model_id:
                    self._log("degrade_model", level="warning",
                              segment_id=real_seg_id, from_model=model_id, to_model=fb)
                model_id = fb
            # 项 ④：降级队列 → 入队任务降为低优先级（避免阻塞正常队列）
            degraded_priority = getattr(ctx, "degraded_priority", None)
            if degraded_priority is not None:
                priority = int(degraded_priority)
                self._log("degrade_queue", level="warning",
                          segment_id=real_seg_id, priority=priority)
            seed = int(seg.get("seed") if seg.get("seed") is not None else -1)
            params = {
                "resolution": resolution,
                "seed": seed,
                "first_frame": None,           # full 版引擎消费；当前引擎忽略（REDUN 降级）
                "enable_tail_frame": enable_tail,
                "priority_level": priority,
                "audio": True,
            }
            try:
                task_id, _local_task_id, _pre_auth_id = enqueue_segment(
                    real_seg_id, trace_id, duration=duration, model_id=model_id,
                    resolution=resolution, seed=seed, priority=priority, params=params)
            except ApiError as e:
                # CLOSE-04：预授权失败（如积分不足）→ 该片段降级为失败，不阻断其他片段
                self._log("preauth_failed", level="warning", segment_id=real_seg_id, code=e.code)
                by_seg[real_seg_id] = {"segment_id": real_seg_id, "level": "fail",
                                      "note": "预授权失败：%s" % e.message, "video_url": None}
                self._set_state("running", (i + 1) / total * 100.0)
                continue
            task_ids.append((real_seg_id, task_id))
            self._log("enqueued", level="info", segment_id=real_seg_id, task_id=task_id)
            if ws_publish:
                ws_publish("director:%s" % project_id, "segment",
                           {"segment_id": real_seg_id, "task_id": task_id, "status": "queued",
                            "progress": 0, "video_url": None})

        # 跟踪 worker 消费（REDUN-01：轮询 generation_tasks，经 WS 透出）
        if task_ids and fetch_task_statuses:
            self._track_tasks(task_ids, project_id, trace_id, ws_publish)

        # 汇总入队片段的最终结果（断点续跑的 skip 已在上方写入 by_seg）
        if fetch_segment_video_url:
            for seg_id, _task_id in task_ids:
                vurl, _st = fetch_segment_video_url(seg_id)
                # 取最新状态，确保 worker 已写回
                st_rows = fetch_task_statuses([_task_id]) if fetch_task_statuses else {}
                row = st_rows.get(_task_id) or {}
                status = row.get("status")
                if status == "succeeded" and vurl:
                    by_seg[seg_id] = {"segment_id": seg_id, "level": "full",
                                      "note": "worker 生成完成", "video_url": vurl}
                elif status == "failed":
                    by_seg[seg_id] = {"segment_id": seg_id, "level": "fail",
                                      "note": row.get("error_message") or "生成失败",
                                      "video_url": None}
                elif status == "cancelled":
                    by_seg[seg_id] = {"segment_id": seg_id, "level": "fail",
                                      "note": "任务已取消", "video_url": None}
                else:
                    # 兜底：仍非终态（如 worker 未运行）→ 标记 fail，释放链路
                    by_seg[seg_id] = {"segment_id": seg_id, "level": "fail",
                                      "note": "跟踪超时未收敛（可能 worker 未运行）",
                                      "video_url": vurl}

        # 按片段顺序汇总（用真实 segment_id 对齐）
        results = [by_seg.get(rid, {"segment_id": rid, "level": "fail",
                                    "note": "未跟踪", "video_url": None})
                   for rid in real_ids]

        failed = [r for r in results if r.get("level") == "fail"]
        if not failed:
            status = "success"
        elif len(failed) < len(results):
            status = "partial_fail"
        else:
            status = "failed"
        self._set_state("done", 100.0, generated=len(results) - len(failed), failed=len(failed),
                        skipped=sum(1 for r in results if r.get("level") == "skip"))
        self._log("agent_done", level="info", status=status,
                  generated=len(results) - len(failed), failed=len(failed))
        warnings = [r["note"] for r in failed if r.get("note")]
        return AgentResult(status=status, data={"clips": results, "priority_level": priority}, warnings=warnings)

    # -- 跟踪 worker 消费（REDUN-01） -----------------------------------------
    def _track_tasks(self, task_ids, project_id, trace_id, ws_publish):
        """轮询 generation_tasks，跟踪每个任务终态；经 WS director:<pid> 透出 per-segment 进度。"""
        deadline = time.time() + SEQ_WAIT_CAP_SECS
        done = set()
        total = len(task_ids)
        from director.enqueue import fetch_task_statuses
        while True:
            if time.time() > deadline:
                break
            pending = [t for _, t in task_ids if t not in done]
            if not pending:
                break
            rows = fetch_task_statuses(pending)
            for seg_id, task_id in task_ids:
                if task_id in done:
                    continue
                row = rows.get(task_id)
                if not row:
                    continue
                st = row.get("status")
                prog = row.get("progress") or 0
                if ws_publish:
                    ws_publish("director:%s" % project_id, "segment",
                               {"segment_id": seg_id, "task_id": task_id, "status": st,
                                "progress": prog, "video_url": row.get("result_url")})
                if st in ("succeeded", "failed", "cancelled"):
                    done.add(task_id)
            if len(done) >= total:
                break
            time.sleep(SEQ_POLL_SECS)
        if len(done) < total:
            self._log("track_timeout", level="warning",
                      remaining=total - len(done),
                      note="部分任务未在看门狗上限内收敛（worker 卡死回收后应为 failed）")

    # -- 断点续跑辅助 ---------------------------------------------------------
    def _existing_video_url(self, seg_id):
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return None
        try:
            row = db.fetch_one(conn, "SELECT video_url, status FROM segments WHERE segment_id=?", (seg_id,))
            if row and row.get("status") == "succeeded" and row.get("video_url"):
                return row["video_url"]
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return None

    @staticmethod
    def _local_path(video_url):
        from urllib.parse import urlparse, parse_qs
        name = (parse_qs(urlparse(video_url or "").query).get("name") or [None])[0]
        return os.path.join(OUTPUTS, os.path.basename(name)) if name else None

    def _local_exists(self, video_url):
        p = self._local_path(video_url)
        return bool(p) and os.path.isfile(p)

    def _load_asset_refs(self, seg_id):
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return []
        try:
            rows = db.fetch_all(conn, "SELECT * FROM segment_asset_refs WHERE segment_id=?", (seg_id,))
            return [dict(r) for r in rows]
        finally:
            try:
                conn.close()
            except Exception:
                pass
