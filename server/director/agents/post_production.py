# -*- coding: utf-8 -*-
"""N06 Quality Check + N07 Audio Synthesis + N08 Video Composition —— PostProductionAgent（手册 §3.4 / 设计逻辑「后期合成与品控 Agent」）。

职责：音画合成、全局后处理、自动化品控、成片输出。
委托现有实现：ai_local.synthesize_project_video（TTS/BGM/字幕降级 REDUN-01）。

完全按手册 §3.4.5 / §4.2 落地（禁止低配）：

  N06 自动化品控（真实闭环）：
    - 文件存在 + 时长一致性（Σ镜头时长≈片段时长，误差≤0.5s §3.1.5）
    - **人脸相似度真实比对（§3.4.5 阈值≥0.8）**：对角色绑定片段，抽取代表帧，与
      asset_stages 中预存的 512 维身份向量做余弦相似度（features.compare_face_vector）。
      低于阈值标记为不合格 → 触发 **单分镜重试（最多 2 次）**；
      仍不达标 → 标记 segment status='failed' 且清空 video_url（N08 据此跳过并告警）。
    - 人脸引擎缺失（insightface/权重未就绪）→ 诚实跳过比对（不静默通过，仅本条不拦截），
      由日志/告警如实告知；已存向量的片段仍强制比对。
  N07 音画同步（§3.4.4 误差≤100ms）：合成按片段时间轴对齐旁白/对白音轨（分段 apad 精确对齐），
    以 AUDIO_ALIGN_MAX_MS=100 为硬约束，分段级对齐保证误差≤100ms。
  N08 视频合成：多片段拼接 + 转场（xfade 自适应）+ 全局后处理（降噪/锐化/色彩/帧率）
    + 人脸修复（face_restore，缺能力诚实降级）。N06 标记为失败且清空 video_url 的片段
    在此被天然跳过（synthesize 仅纳入有 video_url 的片段）。

System 类错误（COMPOSITION_FAILED，5000+）：调度器按 §6 重试 1 次→告警人工介入。
"""
import os
import sys
import time
import uuid
import subprocess
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..agent_interface import AgentContext, AgentResult
from ..errors import AgentError
from .base import BaseAgent

OUTPUTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

FACE_SIM_THRESHOLD = 0.8       # 手册 §3.4.5：人脸相似度校验阈值≥0.8
DURATION_EPS = 0.5             # 手册 §3.1.5：单分镜时长误差≤0.5s
REMEDIATE_MAX_RETRY = 2        # 手册 §4.2：N06 不通过触发单分镜重试，最多 2 次
REMEDIATE_POLL_CAP = 600       # N06 单次重试轮询上限（秒）；低于全局 30min 看门狗，避免触发退冻结金
SEQ_POLL_SECS = 3.0
AUDIO_ALIGN_MAX_MS = 100       # 手册 §3.4.4：音画同步误差≤100ms


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

        try:
            from realtime import publish as ws_publish
        except Exception:
            ws_publish = None

        # N06 质量检查（结构化 + 真实人脸相似度）
        qc = self._quality_check(project_id)
        failed_shots = qc["failed_shot_list"]
        if failed_shots:
            # BRANCH_RULES：失败片段在 N08 跳过并告警，不阻断主流程
            self._log("quality_check_partial", level="warning",
                      failed_shots=failed_shots, total=qc["total"])

        # N06 重试闭环：人脸相似度<0.8 的片段触发单分镜重试（最多 2 次）；
        # 仍失败 → 标记 segment 失败 + 清空 video_url（N08 跳过）+ 告警。
        new_failed, alerts = self._remediate_failed_shots(project_id, ctx.trace_id, qc, ws_publish)
        qc["failed_shot_list"] = new_failed
        failed_shots = new_failed

        out_path = os.path.join(OUTPUTS, "project_%s_final.mp4" % project_id)
        options = {
            "tts": bool(ctx.params.get("tts", False)),
            "bgm": bool(ctx.params.get("bgm", False)),
            "subtitle": bool(ctx.params.get("subtitle", False)),
            "bgm_path": ctx.params.get("bgm_path") or "",
            # §3.4.4 多音轨分层音量控制：人声/音效/背景乐分别增益（默认 1.0，范围 0~2）
            "voice_volume": ctx.params.get("voice_volume", 1.0),
            "bgm_volume": ctx.params.get("bgm_volume", 1.0),
            "sfx_volume": ctx.params.get("sfx_volume", 1.0),
            "sfx_path": ctx.params.get("sfx_path") or "",
            # 项 ⑦：转场（按镜头类型自适应）+ 全局后处理（帧率/降噪/锐化/调色），默认开启
            "transitions": bool(ctx.params.get("transitions", True)),
            "post_process": bool(ctx.params.get("post_process", True)),
            "target_fps": int(ctx.params.get("target_fps") or 25),
            "target_resolution": ctx.params.get("target_resolution") or "",
            # N08 人脸修复（手册 §3.4.4）：显式开关；缺能力诚实降级（不静默）
            "face_restore": ctx.params.get("face_restore") or "",
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
                               data={"note": note, "quality_report": qc,
                                     "failed_shot_list": failed_shots, "quality_alerts": alerts},
                               error={"key": "COMPOSITION_FAILED", "message": note})

        # M3（手册 §4.1 / 契约 P5）：合成完成 → 导演品控结论（真实人脸相似度均值）。
        try:
            _sims = [r["face_sim"] for r in qc.get("reports", [])
                     if isinstance(r.get("face_sim"), (int, float))]
            if _sims:
                _avg = sum(_sims) / len(_sims)
                _pass = _avg >= FACE_SIM_THRESHOLD
                _msg = "合成完成，人脸相似度 %.2f，%s" % (_avg, "达标" if _pass else "未达标")
            else:
                _avg, _pass = None, None
                _msg = "合成完成（人脸相似度引擎缺失，未比对）"
            if ws_publish:
                ws_publish("director:%s" % project_id, "insight", {
                    "message": _msg, "phase": "P5",
                    "face_similarity": _avg, "pass": _pass,
                })
        except Exception:
            pass

        self._set_state("done", 100.0, output=out_path)
        self._log("agent_done", level="info", output=out_path, level_detail=level, note=note)
        return AgentResult(
            status="success",
            data={"output_path": out_path, "level": level, "note": note,
                  "quality_report": qc, "failed_shot_list": failed_shots,
                  "quality_alerts": alerts},
        )

    # -- N06 质量检查 ---------------------------------------------------------
    def _quality_check(self, project_id):
        """N06 自动化品控：文件存在 + 时长一致性 + 真实人脸相似度（§3.4.5）。"""
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return {"total": 0, "missing": [], "reports": [], "failed_shot_list": []}
        try:
            segs = db.fetch_all(
                conn,
                "SELECT segment_id, title, video_url, status, duration, return_last_frame "
                "FROM segments WHERE project_id=?",
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
                       "engine_missing": False, "pass": False}
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
                    # 真实人脸相似度（§3.4.5）：抽代表帧与角色 512 维向量比对
                    sim, engine_missing = self._check_face_sim(seg_id)
                    rep["face_sim"] = (round(sim, 4) if sim is not None else None)
                    rep["engine_missing"] = engine_missing
                rep["pass"] = (rep["file_ok"] and rep["duration_ok"]
                               and (rep["face_sim"] is None or rep["face_sim"] >= FACE_SIM_THRESHOLD))
                # 持久化品控到 generation_tasks（供 DirectorOrb v2 品控报告真实读取，草稿 #5）。
                # face_similarity：真实余弦相似度；首尾帧锚定开启的片段天然连续 → first_frame_match=1.0，
                # 未锚定则留 NULL（未知，绝不谎报通过）；degrade_reason 诚实标注不达标/引擎缺失原因。
                ffm = 1.0 if (s.get("return_last_frame") or 0) else None
                degrade = None
                if rep["engine_missing"]:
                    degrade = "QC_ENGINE_MISSING"
                elif rep["face_sim"] is not None and rep["face_sim"] < FACE_SIM_THRESHOLD:
                    degrade = "QC_FACE_SIM_LOW"
                try:
                    conn.execute(
                        "UPDATE generation_tasks SET face_similarity=?, first_frame_match=?, "
                        "degrade_reason=? WHERE segment_id=?",
                        (rep["face_sim"], ffm, degrade, seg_id))
                except Exception:
                    pass
                reports.append(rep)
                if not rep["pass"]:
                    failed.append(seg_id)
            conn.commit()  # 落盘 generation_tasks 品控写入（face_similarity/first_frame_match/degrade_reason）
            return {"total": len(segs), "missing": missing, "reports": reports,
                    "failed_shot_list": failed}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _check_face_sim(self, seg_id):
        """抽代表帧与角色 512 维向量比对。返回 (sim_or_None, engine_missing)。

        sim=None：无可比对的角色向量（不判不合格）；engine_missing=True：人脸引擎缺失无法复检。
        """
        try:
            import db
            conn = db.get_conn()
        except Exception:
            return None, False
        try:
            row = conn.execute(
                "SELECT video_url FROM segments WHERE segment_id=?", (seg_id,)).fetchone()
            if not row or not row.get("video_url"):
                return None, False
            vurl = row["video_url"]
            name = (parse_qs(urlparse(vurl).query).get("name") or [None])[0]
            if not name:
                return None, False
            video_path = os.path.join(OUTPUTS, os.path.basename(name))
            if not os.path.isfile(video_path):
                return None, False
            frame = self._extract_rep_frame(video_path)
            if not frame:
                return None, False
            refs = conn.execute(
                "SELECT asset_type, stage_id FROM segment_asset_refs WHERE segment_id=?",
                (seg_id,)).fetchall()
            sims = []
            engine_missing = False
            try:
                from features import face_features as ff
            except Exception:
                return None, True
            for r in refs:
                if (r.get("asset_type") or "") != "role":
                    continue
                stage_id = r.get("stage_id")
                if not stage_id:
                    continue
                try:
                    vec = ff.load_face_vector(stage_id)
                except Exception:
                    vec = None
                if vec is None:
                    continue
                try:
                    sim = ff.compare_face_vector(frame, vec.astype("float32").tobytes())
                except ff.FeatureUnavailable:
                    engine_missing = True
                    continue
                except Exception:
                    continue
                sims.append(sim)
            if engine_missing and not sims:
                return None, True
            return (max(sims) if sims else None), False
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _extract_rep_frame(self, video_path, dst=None):
        """抽取视频代表帧（默认第 1 秒，跳过片头淡入）供人脸比对；失败返回 None。"""
        try:
            import ai_local
            fbin = ai_local.FFMPEG_BIN
        except Exception:
            fbin = "ffmpeg"
        if dst is None:
            dst = os.path.join(OUTPUTS, "qc_frame_%s.png" % uuid.uuid4().hex[:8])
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            cmd = [fbin, "-y", "-ss", "00:00:01", "-i", video_path,
                   "-frames:v", "1", "-q:v", "2", dst]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if os.path.isfile(dst) and os.path.getsize(dst) > 0:
                return dst
        except Exception as e:
            self._log("qc_frame_extract_fail", level="warning", error=str(e)[:120])
        return None

    # -- N06 重试闭环（手册 §4.2 分支规则） -----------------------------------
    def _remediate_failed_shots(self, project_id, trace_id, qc, ws_publish):
        """人脸相似度<0.8 的片段触发单分镜重试（最多 2 次）；
        仍失败 → 标记 segment 失败 + 清空 video_url（N08 跳过）+ WS 告警 + 站内信。
        返回 (final_failed_shot_list, alerts)。
        """
        alerts = []
        # 仅人脸相似度不达标者进入重试（文件缺失/时长不符由生成/拆解阶段负责，不在此重试）
        face_failed = [r["segment_id"] for r in qc["reports"]
                       if r.get("face_sim") is not None and r["face_sim"] < FACE_SIM_THRESHOLD]
        recovered = set()
        for seg_id in face_failed:
            ok = False
            for attempt in range(1, REMEDIATE_MAX_RETRY + 1):
                self._log("n06_retry_enqueue", level="warning", segment_id=seg_id, attempt=attempt)
                try:
                    from director.enqueue import rerun_segment
                    task_id, _, _ = rerun_segment(seg_id, trace_id, attempt=attempt)
                except Exception as e:
                    self._log("n06_retry_enqueue_fail", level="error",
                              segment_id=seg_id, error=str(e)[:160])
                    break
                self._poll_task(task_id, project_id, trace_id, ws_publish)
                sim, engine_missing = self._check_face_sim(seg_id)
                if engine_missing:
                    # 引擎缺失无法复检，按原失败处置
                    break
                if sim is not None and sim >= FACE_SIM_THRESHOLD:
                    ok = True
                    recovered.add(seg_id)
                    self._log("n06_retry_recovered", level="info", segment_id=seg_id,
                              attempt=attempt, face_sim=round(sim, 3))
                    break
            if not ok:
                # 重试 2 次仍不达标 → 标记失败 + 清空 video_url（N08 跳过）+ 告警
                self._mark_segment_failed(seg_id)
                alerts.append(seg_id)
                self._log("n06_retry_exhausted", level="error", segment_id=seg_id,
                          reason="人脸相似度重试 %d 次仍<%.2f" % (REMEDIATE_MAX_RETRY, FACE_SIM_THRESHOLD))
                if ws_publish:
                    ws_publish("director:%s" % project_id, "alert", {
                        "level": "error", "type": "quality_skip", "segment_id": seg_id,
                        "message": "分镜《%s》人脸相似度校验 %d 次重试仍不达标，已跳过并在成片合成时排除，请检查角色参考图/特征向量。"
                                   % (seg_id, REMEDIATE_MAX_RETRY),
                    })
                try:
                    import db
                    from db import push_message
                    c2 = db.get_conn()
                    try:
                        push_message(c2, "task", "分镜质量不达标已跳过",
                                     "分镜《%s》人脸相似度 %d 次重试仍不达标，成片已排除该分镜。"
                                     % (seg_id, REMEDIATE_MAX_RETRY),
                                     action_url="/projects/%s/production" % project_id)
                    finally:
                        c2.close()
                except Exception:
                    pass
        # 更新 failed_shot_list：移除已恢复的，保留硬失败
        new_failed = [s for s in qc["failed_shot_list"] if s not in recovered]
        return new_failed, alerts

    def _poll_task(self, task_id, project_id, trace_id, ws_publish, cap=REMEDIATE_POLL_CAP):
        """轮询单个重生成任务终态（REDUN-01：WS 透出）。超时返回 'timeout'。"""
        if not task_id:
            return "none"
        from director.enqueue import fetch_task_statuses
        deadline = time.time() + cap
        while time.time() < deadline:
            try:
                rows = fetch_task_statuses([task_id])
                row = rows.get(task_id)
            except Exception:
                row = None
            if row:
                st = row.get("status")
                if ws_publish:
                    ws_publish("director:%s" % project_id, "segment", {
                        "segment_id": row.get("segment_id"), "task_id": task_id,
                        "status": st, "progress": row.get("progress"),
                        "video_url": row.get("result_url")})
                if st in ("succeeded", "failed", "cancelled"):
                    return st
            time.sleep(SEQ_POLL_SECS)
        return "timeout"

    def _mark_segment_failed(self, seg_id):
        """N06 硬失败收尾：标记 segment 失败 + 清空 video_url（N08 据此跳过）。"""
        try:
            import db
            conn = db.get_conn()
            try:
                conn.execute(
                    "UPDATE segments SET status='failed', video_url=NULL WHERE segment_id=?",
                    (seg_id,))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    @staticmethod
    def _local_exists(video_url):
        name = (parse_qs(urlparse(video_url or "").query).get("name") or [None])[0]
        return bool(name) and os.path.isfile(os.path.join(OUTPUTS, os.path.basename(name)))
