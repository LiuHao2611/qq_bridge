# -*- coding: utf-8 -*-
"""N06 Quality Check + N07 Audio Synthesis + N08 Video Composition —— PostProductionAgent（手册 §3.4 / 设计逻辑「后期合成与品控 Agent」）。

职责：音画合成、全局后处理、自动化品控、成片输出。
委托现有实现：ai_local.synthesize_project_video（TTS/BGM/字幕降级 REDUN-01）。

相比首版骨架新增（对齐手册 §3.4 + 设计逻辑）：
  - **结构化品控（N06）**：文件存在 + 时长一致性（Σ镜头时长≈片段时长，误差≤0.5s §3.1.5）
    + 人脸相似度占位（full 版接人脸比对，阈值≥0.8 §3.4.5）；产出 quality_report / failed_shot_list
  - 输出 final_video_url / quality_report / failed_shot_list（对齐 §3.4.3）
  - 转场按镜头类型自适应、全局后处理（人脸修复/降噪/锐化/帧率/调色）在 full 版 ComfyUI 阶段补全

System 类错误（COMPOSITION_FAILED，5000+）：调度器按 §6 重试 1 次→告警人工介入。
"""
import os
import sys
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..agent_interface import AgentContext, AgentResult
from ..errors import AgentError
from .base import BaseAgent

OUTPUTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

FACE_SIM_THRESHOLD = 0.8       # 手册 §3.4.5：人脸相似度校验阈值≥0.8
DURATION_EPS = 0.5             # 手册 §3.1.5：单分镜时长误差≤0.5s


class PostProductionAgent(BaseAgent):
    agent_id = "AGENT-POST-PRODUCTION"
    agent_name = "PostProductionAgent"

    def run(self, ctx: AgentContext) -> AgentResult:
        self._set_state("running", 0.0)
        self._log("agent_run", level="info")

        project_id = ctx.project_id
        if not project_id:
            self._set_state("failed", 0.0)
            raise AgentError("COMPOSITION_FAILED", "缺少 project_id，无法合成全集")

        # N06 质量检查（结构化）
        qc = self._quality_check(project_id)
        failed_shots = qc["failed_shot_list"]
        if failed_shots:
            # BRANCH_RULES：失败片段在 N08 跳过并告警，不阻断主流程
            self._log("quality_check_partial", level="warning",
                      failed_shots=failed_shots, total=qc["total"])

        out_path = os.path.join(OUTPUTS, "project_%s_final.mp4" % project_id)
        options = {
            "tts": bool(ctx.params.get("tts", False)),
            "bgm": bool(ctx.params.get("bgm", False)),
            "subtitle": bool(ctx.params.get("subtitle", False)),
            "bgm_path": ctx.params.get("bgm_path") or "",
        }
        try:
            import ai_local
            level, note = ai_local.synthesize_project_video(project_id, out_path, options)
        except Exception as e:
            self._set_state("failed", 0.0)
            raise AgentError("COMPOSITION_FAILED", "合成全集异常: %s" % e, cause=e)

        if level == "fail":
            self._set_state("failed", 100.0)
            self._log("agent_failed", level="error", note=note)
            return AgentResult(status="failed",
                               data={"note": note, "quality_report": qc, "failed_shot_list": failed_shots},
                               error={"key": "COMPOSITION_FAILED", "message": note})

        self._set_state("done", 100.0, output=out_path)
        self._log("agent_done", level="info", output=out_path, level_detail=level, note=note)
        return AgentResult(
            status="success",
            data={"output_path": out_path, "level": level, "note": note,
                  "quality_report": qc, "failed_shot_list": failed_shots},
        )

    def _quality_check(self, project_id):
        """N06 自动化品控：文件存在 + 时长一致性 + 人脸相似度（占位）。"""
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return {"total": 0, "missing": [], "reports": [], "failed_shot_list": []}
        try:
            segs = db.fetch_all(
                conn,
                "SELECT segment_id, title, video_url, status, duration FROM segments WHERE project_id=?",
                (project_id,),
            )
            reports = []
            failed = []
            missing = []
            for s in segs:
                seg_id = s["segment_id"]
                vurl = s.get("video_url")
                rep = {"segment_id": seg_id, "title": s.get("title"), "file_ok": False,
                       "duration_ok": True, "duration_error": 0.0, "face_sim": None,
                       "pass": False}
                if not vurl or not self._local_exists(vurl):
                    missing.append(seg_id)
                    rep["file_ok"] = False
                else:
                    rep["file_ok"] = True
                    # 时长一致性：Σ镜头时长 vs 片段时长
                    shots = db.fetch_all(conn,
                        'SELECT duration FROM shots WHERE segment_id=?', (seg_id,))
                    shot_sum = sum(float(sh.get("duration") or 0) for sh in shots)
                    seg_dur = float(s.get("duration") or 0)
                    d_err = abs(shot_sum - seg_dur)
                    rep["duration_error"] = round(d_err, 2)
                    rep["duration_ok"] = d_err <= DURATION_EPS
                    # 人脸相似度：full 版接比对算法；骨架占位 None（不拦截）
                    rep["pass"] = rep["file_ok"] and rep["duration_ok"]
                reports.append(rep)
                if not rep["pass"]:
                    failed.append(seg_id)
            return {"total": len(segs), "missing": missing, "reports": reports,
                    "failed_shot_list": failed}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _local_exists(video_url):
        name = (parse_qs(urlparse(video_url or "").query).get("name") or [None])[0]
        return bool(name) and os.path.isfile(os.path.join(OUTPUTS, os.path.basename(name)))
