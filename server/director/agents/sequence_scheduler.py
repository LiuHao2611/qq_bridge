# -*- coding: utf-8 -*-
"""N04 Task Dispatch + N05 Shot Generate —— SequenceSchedulerAgent（手册 §3.3 / 设计逻辑「时序衔接调度 Agent」）。

职责：分镜任务时序编排、算力调度、首尾帧衔接控制。
委托现有实现：ai_local.generate_segment_video（与 worker._serve_one 同核心）。

相比首版骨架新增（对齐手册 §3.3 + 设计逻辑）：
  - **首尾帧锚定（enable_tail_frame）**：提取上一片段尾帧作下一片段首帧参考，
    经 params.first_frame 透传给生成引擎（full 版由 ComfyUI img2video 消费）
  - **断点续跑（resume）**：片段已生成成功且文件存在则跳过，服务中断/刷新后从断点继续
  - **优先级（priority_level）**：记录并透传队列选择（full 版进入高速/常规队列）
  - 消费上游 bound_assets 作为资产引用（Asset Strong Constraint），DB 兜底

TODO(full)：改向 generation_tasks 入队由 worker 消费；按 REDUN-01 轮询/WS 跟踪状态；
并行度上限由算力池（infra.compute_pool）动态决定。
"""
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..agent_interface import AgentContext, AgentResult
from ..errors import AgentError
from .base import BaseAgent

OUTPUTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "outputs")
os.makedirs(OUTPUTS, exist_ok=True)


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
        priority = ctx.params.get("priority_level", 0)

        results = []
        prev_last_frame = None
        try:
            import ai_local
            total = len(segments)
            for i, seg in enumerate(segments):
                seg_id = seg.get("segment_id") or ("seg_%d" % i)
                # 断点续跑：已有成功片段且文件存在 → 跳过（Observability/非破坏性）
                exist_url = self._existing_video_url(seg_id)
                if resume and exist_url and self._local_exists(exist_url):
                    results.append({"segment_id": seg_id, "level": "skip",
                                    "note": "断点续跑：已有成功片段", "video_url": exist_url})
                    if enable_tail:
                        prev_last_frame = self._extract_last_frame(self._local_path(exist_url))
                    continue

                shots = seg.get("shots") or []
                asset_refs = [{"asset_id": b["asset_id"]} for b in (bound.get(seg_id) or [])]
                if not asset_refs:
                    asset_refs = self._load_asset_refs(seg_id)   # DB 兜底
                out_path = os.path.join(OUTPUTS, "seg_%s.mp4" % seg_id)
                params = {
                    "resolution": ctx.params.get("resolution") or seg.get("resolution") or "720p",
                    "seed": seg.get("seed"),
                    "first_frame": prev_last_frame,           # 首尾帧锚定（full 版消费）
                    "enable_tail_frame": enable_tail,
                    "priority_level": priority,
                }
                level, note = ai_local.generate_segment_video(seg, shots, asset_refs, out_path, params)
                if level == "fail":
                    results.append({"segment_id": seg_id, "level": "fail", "note": note})
                    self._log("shot_generate_failed", level="warning", segment_id=seg_id, note=note)
                    prev_last_frame = None
                    continue
                rel_url = "/file?name=seg_%s.mp4" % seg_id
                self._persist_video_url(seg_id, rel_url)
                results.append({"segment_id": seg_id, "level": level, "note": note, "video_url": rel_url})
                # 首尾帧锚定：提取本片段尾帧作下一片段首帧参考（中间帧，TTL 7 天 §7.2）
                if enable_tail:
                    prev_last_frame = self._extract_last_frame(out_path)
                self._set_state("running", (i + 1) / total * 100.0)
        except Exception as e:
            self._set_state("failed", 0.0)
            raise AgentError("SHOT_GENERATE_FAILED", "分镜生成异常: %s" % e, cause=e)

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
        warnings = [r["note"] for r in failed]
        return AgentResult(status=status, data={"clips": results, "priority_level": priority}, warnings=warnings)

    # -- 首尾帧锚定 / 断点续跑辅助 ---------------------------------------------
    def _extract_last_frame(self, video_path):
        """提取视频尾帧（近结尾 0.1s）作为下一分镜首帧参考。失败返回 None（REDUN 降级）。"""
        try:
            import ai_local
            if not os.path.isfile(video_path):
                return None
            png = os.path.join(OUTPUTS, "tail_%s.png" % os.path.splitext(os.path.basename(video_path))[0])
            dur = self._duration(video_path)
            ss = max(0.0, (dur or 1.0) - 0.1)
            cmd = [ai_local.FFMPEG_BIN, "-y", "-ss", "%.2f" % ss, "-i", video_path,
                   "-frames:v", "1", "-q:v", "2", png]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            return png if os.path.isfile(png) else None
        except Exception:
            return None

    def _duration(self, video_path):
        try:
            import ai_local
            cmd = [ai_local.FFMPEG_BIN, "-i", video_path]
            out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stderr.decode("utf-8", "ignore")
            for tok in out.split():
                if tok.startswith("Duration:"):
                    h, m, s = tok.replace("Duration:", "").strip().rstrip(",").split(":")
                    return int(float(h)) * 3600 + int(float(m)) * 60 + float(s)
        except Exception:
            return None
        return None

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

    def _persist_video_url(self, seg_id, rel_url):
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return
        try:
            conn.execute(
                "UPDATE segments SET status='succeeded', video_url=?, thumbnail_url=? WHERE segment_id=?",
                (rel_url, rel_url, seg_id),
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass
            pass
