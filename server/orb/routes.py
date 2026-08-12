# -*- coding: utf-8 -*-
"""DirectorOrb v2 接口层（悬浮球化身）。

全部只读 + 一个批量操作（CLOSE-04 资金安全）。所有接口受 module_flags.director_orb_v2 灰度开关保护。

设计约束（方案 §二/§四）：
  - 悬浮球不新增独立检测，只聚合导演 Agent 已有数据；
  - 五阶段核心视图：P1 资产就绪度 / P2 叙事结构 / P3 配方透明度 / P4 品控摘要 / P5 成片质检。

注意：真实库结构里 generation_tasks 无 run_id（经 segments.project_id 关联），
故 run-summary / batch-action 以 project_id 为主键（与前端 DirectorOrb 的 pid 一致），
而非手册假设的 director-run/{run_id}。接口挂载在 routers.py 的 api（prefix=/api/v1）。
"""
import json
import time
import hashlib

from db import get_conn, fetch_all, fetch_one, uid, SINGLE_USER_ID
from common import ok, ApiError
from module_manager import get_flags


# 每分镜重新生成积分成本（与序列调度一致即可，仅用于前端预估+后端校验）
ORB_COST_PER_SHOT = 100


def _check_flag():
    """灰度开关：director_orb_v2 未开启 → 拒绝服务（前端导航守卫也会拦）。"""
    if not get_flags().get("director_orb_v2"):
        raise ApiError("ORB_DISABLED", "director_orb_v2 未开启（灰度中）")


def _load_json(raw, default):
    if raw is None or raw == "":
        return default
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, (dict, list)) else default
    except Exception:
        return default


def register_orb_routes(api):
    """把 orb 全部路由注册到给定的 APIRouter（routers.py 的 api，prefix=/api/v1）。"""

    # ============ 接口1：资产就绪度（P1 选角导演 + 美术指导） ============
    @api.get("/orb/projects/{project_id}/asset-readiness")
    def get_asset_readiness(project_id: str):
        _check_flag()
        conn = get_conn()
        try:
            def _count(asset_type):
                rows = fetch_all(
                    conn,
                    "SELECT asset_id, name, quality_flags, role_type FROM assets "
                    "WHERE (project_id=? OR is_global=1) AND type=?",
                    (project_id, asset_type),
                )
                items, ready = [], 0
                for r in rows:
                    qf = _load_json(r["quality_flags"], {})
                    flags = qf if isinstance(qf, dict) else {}
                    has_err = any((v or {}).get("severity") == "error" for v in flags.values())
                    item_ready = not has_err
                    if item_ready:
                        ready += 1
                    items.append({
                        "id": r["asset_id"], "name": r["name"], "ready": item_ready,
                        "issues": list(flags.keys()),
                        "is_primary": (r.get("role_type") == "protagonist"),
                    })
                return {"total": len(rows), "ready": ready, "items": items}

            characters = _count("role")
            scenes = _count("scene")
            props = _count("prop")

            # P1↔P2 缺口：本项目中没有任何角色绑定的场次（剧本需要角色却未绑定）
            gap_rows = fetch_all(
                conn,
                "SELECT segment_id, \"index\" FROM segments WHERE project_id=? "
                "AND segment_id NOT IN (SELECT DISTINCT segment_id FROM segment_asset_refs "
                "WHERE asset_type='role')",
                (project_id,),
            )
            missing_role_segments = [
                {"segment_id": g["segment_id"], "index": g["index"]} for g in gap_rows
            ]

            total_needed = characters["total"] + scenes["total"] + props["total"]
            total_ready = characters["ready"] + scenes["ready"] + props["ready"]
            score = int(total_ready / total_needed * 100) if total_needed > 0 else 0
            primary_not_ready = any(
                not c["ready"] for c in characters["items"] if c["is_primary"]
            )
            if primary_not_ready:
                score = min(score, 50)

            return ok({
                "readiness_score": score,
                "characters": characters,
                "scenes": scenes,
                "props": props,
                "missing_role_segments": missing_role_segments,
            })
        finally:
            conn.close()

    # ============ 接口2：叙事结构分析（P2 编剧） ============
    @api.get("/orb/projects/{project_id}/script-analysis")
    def get_script_analysis(project_id: str):
        _check_flag()
        conn = get_conn()
        try:
            rows = fetch_all(
                conn,
                'SELECT segment_id, "index", duration, scene_description, status '
                'FROM segments WHERE project_id=? ORDER BY "index"',
                (project_id,),
            )

            # 每场「场景身份」：优先用绑定的场景资产名，否则用场景描述前缀
            scene_of = {}
            for s in rows:
                sar = fetch_all(
                    conn,
                    "SELECT a.name FROM segment_asset_refs sar LEFT JOIN assets a "
                    "ON a.asset_id=sar.asset_id WHERE sar.segment_id=? "
                    "AND (sar.asset_type='scene' OR a.type='scene')",
                    (s["segment_id"],),
                )
                scene_of[s["segment_id"]] = (sar[0]["name"] if sar else (s["scene_description"] or "")[:30])

            # 连续同场景
            consecutive = []
            i = 0
            while i < len(rows):
                j = i
                sid = scene_of[rows[i]["segment_id"]]
                while j + 1 < len(rows) and scene_of[rows[j + 1]["segment_id"]] == sid and sid:
                    j += 1
                if j - i >= 2:
                    consecutive.append({
                        "start": rows[i]["index"], "end": rows[j]["index"], "scene": sid,
                    })
                i = j + 1

            # 空描述
            empty = [s["index"] for s in rows if not (s["scene_description"] or "").strip()]

            # 角色出场（按绑定的角色资产名）
            char_appearances = {}
            for s in rows:
                sar = fetch_all(
                    conn,
                    "SELECT a.name FROM segment_asset_refs sar LEFT JOIN assets a "
                    "ON a.asset_id=sar.asset_id WHERE sar.segment_id=? "
                    "AND (sar.asset_type='role' OR a.type='role')",
                    (s["segment_id"],),
                )
                for r in sar:
                    if r["name"]:
                        char_appearances.setdefault(r["name"], []).append(s["index"])

            return ok({
                "segment_count": len(rows),
                "total_duration": sum(s["duration"] or 0 for s in rows),
                "avg_duration": round(sum(s["duration"] or 0 for s in rows) / len(rows), 1) if rows else 0,
                "consecutive_same_scene": consecutive,
                "character_appearances": char_appearances,
                "empty_description_segments": empty,
            })
        finally:
            conn.close()

    # ============ 接口3：分镜 Intent 预览（P3 摄影指导 · 配方透明度） ============
    @api.get("/orb/shots/{shot_id}/intent-preview")
    def get_intent_preview(shot_id: str):
        _check_flag()
        from director.agents.continuity_checker import preview_shot_intent, check_continuity
        conn = get_conn()
        try:
            result = preview_shot_intent(shot_id)
            if not result.get("available"):
                return ok(result)
            shot = fetch_one(conn, "SELECT segment_id, asset_refs FROM shots WHERE shot_id=?", (shot_id,))
            if not shot:
                result["continuity_issues"] = []
                return ok(result)
            seg_id = shot["segment_id"]
            shot_rows = fetch_all(
                conn,
                'SELECT shot_id, "index", shot_type, camera_movement, asset_refs '
                'FROM shots WHERE segment_id=? ORDER BY "index"',
                (seg_id,),
            )
            cont_shots = []
            for sr in shot_rows:
                refs = _load_json(sr["asset_refs"], [])
                role_refs = [
                    r.get("asset_id") for r in refs
                    if isinstance(r, dict) and ((r.get("asset_type") or r.get("type")) == "role")
                ]
                cont_shots.append({
                    "index": sr["index"], "shot_type": sr["shot_type"],
                    "camera_movement": sr["camera_movement"], "role_refs": role_refs,
                })
            result["continuity_issues"] = check_continuity(cont_shots)
            return ok(result)
        finally:
            conn.close()

    # ============ 接口4：Director Run 摘要（P4 制片 · 品控摘要） ============
    @api.get("/orb/projects/{project_id}/run-summary")
    def get_run_summary(project_id: str):
        _check_flag()
        conn = get_conn()
        try:
            rows = fetch_all(
                conn,
                "SELECT t.status, t.face_similarity, t.first_frame_match, "
                "t.degrade_reason, t.cost_credits, t.segment_id, s.\"index\" AS seg_index "
                "FROM generation_tasks t JOIN segments s ON t.segment_id=s.segment_id "
                "WHERE s.project_id=?",
                (project_id,),
            )
            total = len(rows)
            completed = sum(1 for t in rows if t["status"] == "succeeded")
            failed = sum(1 for t in rows if t["status"] == "failed")
            running = sum(1 for t in rows if t["status"] in ("running", "retrying", "queued"))

            sims = [t["face_similarity"] for t in rows if t["face_similarity"] is not None]
            frames = [t["first_frame_match"] for t in rows if t["first_frame_match"] is not None]
            failure_reasons = {}
            failed_segments = []
            for t in rows:
                if t["status"] == "failed":
                    if t["degrade_reason"]:
                        failure_reasons[t["degrade_reason"]] = failure_reasons.get(t["degrade_reason"], 0) + 1
                    if t["segment_id"]:
                        failed_segments.append({"segment_id": t["segment_id"], "index": t["seg_index"]})
            points_used = sum(int(t["cost_credits"] or 0) for t in rows if t["status"] == "succeeded")

            return ok({
                "total": total, "completed": completed, "failed": failed, "running": running,
                "failed_segments": failed_segments,
                "estimated_remaining_seconds": 0,
                "face_similarity_avg": round(sum(sims) / len(sims), 2) if sims else 0,
                "face_similarity_pass_rate": round(sum(1 for s in sims if s >= 0.8) / len(sims) * 100, 1) if sims else 0,
                "first_frame_match_avg": round(sum(frames) / len(frames), 2) if frames else 0,
                "failure_reasons": failure_reasons,
                "points_used": points_used,
                "current_dag_node": "",
                "current_shot_id": "",
            })
        finally:
            conn.close()

    # ============ 接口5：批量操作（CLOSE-04 资金安全，最高优先级） ============
    @api.post("/orb/projects/{project_id}/batch-action")
    def batch_action(project_id: str, body: dict):
        _check_flag()
        action = body.get("action")  # regenerate / skip / degrade_retry
        segment_ids = body.get("segment_ids") or body.get("shot_ids") or []
        params = body.get("params") or {}
        if action not in ("regenerate", "skip", "degrade_retry"):
            raise ApiError("INVALID_ACTION", "action 必须是 regenerate/skip/degrade_retry")
        if not segment_ids or not isinstance(segment_ids, list):
            raise ApiError("INVALID_PARAMS", "segment_ids 必填且为数组")

        conn = get_conn()
        try:
            ph = ",".join("?" * len(segment_ids))
            exist = fetch_all(
                conn,
                "SELECT segment_id FROM segments WHERE project_id=? AND segment_id IN (%s)" % ph,
                (project_id, *segment_ids),
            )
            seg_set = [r["segment_id"] for r in exist]
            if len(seg_set) != len(set(segment_ids)):
                raise ApiError("INVALID_PARAMS", "部分 segment_ids 不属于本项目")

            seg_ph = ",".join("?" * len(seg_set))
            queued = 0

            if action == "regenerate":
                # CLOSE-04：重新生成必须重新 pre_auth 冻结积分（幂等键防重复扣）
                from points_engine import pre_auth, get_points_balance
                idem_key = "orb_batch:%s:%s:%s" % (
                    project_id, action,
                    hashlib.md5(",".join(sorted(seg_set)).encode()).hexdigest(),
                )
                total_cost = ORB_COST_PER_SHOT * len(seg_set)
                if get_points_balance() < total_cost:
                    raise ApiError("INSUFFICIENT_POINTS", "积分不足：需 %d" % total_cost)
                pre_auth(total_cost, "orb_batch:%s" % project_id, idem_key)
                matched = fetch_all(
                    conn,
                    "SELECT task_id FROM generation_tasks WHERE segment_id IN (%s) "
                    "AND status IN ('failed','cancelled')" % seg_ph,
                    tuple(seg_set),
                )
                queued = len(matched)
                if matched:
                    task_ph = ",".join("?" * len(matched))
                    conn.execute(
                        "UPDATE generation_tasks SET status='queued', retry_count=0 "
                        "WHERE task_id IN (%s)" % task_ph,
                        [m["task_id"] for m in matched],
                    )
            elif action == "skip":
                matched = fetch_all(
                    conn,
                    "SELECT task_id FROM generation_tasks WHERE segment_id IN (%s) "
                    "AND status IN ('queued','running','failed','retrying')" % seg_ph,
                    tuple(seg_set),
                )
                queued = len(matched)
                if matched:
                    task_ph = ",".join("?" * len(matched))
                    conn.execute(
                        "UPDATE generation_tasks SET status='cancelled' WHERE task_id IN (%s)" % task_ph,
                        [m["task_id"] for m in matched],
                    )
            elif action == "degrade_retry":
                res = params.get("resolution") or "480p"
                matched = fetch_all(
                    conn,
                    "SELECT task_id, params FROM generation_tasks WHERE segment_id IN (%s) "
                    "AND status IN ('failed','cancelled')" % seg_ph,
                    tuple(seg_set),
                )
                queued = len(matched)
                for m in matched:
                    p = _load_json(m["params"], {}) if m["params"] else {}
                    if not isinstance(p, dict):
                        p = {}
                    p["resolution"] = res
                    p["degraded"] = True
                    conn.execute(
                        "UPDATE generation_tasks SET status='queued', retry_count=0, params=? "
                        "WHERE task_id=?",
                        (json.dumps(p, ensure_ascii=False), m["task_id"]),
                    )

            conn.commit()
            # CLOSE-03：写审计日志（operation_logs 真实字段）
            conn.execute(
                "INSERT INTO operation_logs (log_id, user_id, action, object_type, object_id, "
                "diff, status, cost_credits, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (uid(), SINGLE_USER_ID, "orb_batch_%s" % action, "project", project_id,
                 json.dumps({"segment_ids": seg_set, "params": params}, ensure_ascii=False),
                 "success", (ORB_COST_PER_SHOT * len(seg_set)) if action == "regenerate" else 0,
                 int(time.time())),
            )
            conn.commit()
            return ok({"queued": queued, "refunded": 0})
        finally:
            conn.close()

    # ============ 接口6：成片质检报告（P5 剪辑师） ============
    @api.get("/orb/projects/{project_id}/synthesize-report")
    def get_synthesize_report(project_id: str):
        _check_flag()
        conn = get_conn()
        try:
            rows = fetch_all(
                conn,
                "SELECT t.face_similarity, t.first_frame_match, t.degrade_reason, t.status "
                "FROM generation_tasks t JOIN segments s ON t.segment_id=s.segment_id "
                "WHERE s.project_id=?",
                (project_id,),
            )
            if not rows:
                return ok(None, message="no synthesize report yet")
            sims = [t["face_similarity"] for t in rows if t["face_similarity"] is not None]
            frames = [t["first_frame_match"] for t in rows if t["first_frame_match"] is not None]
            failure_reasons = {}
            for t in rows:
                if t["status"] == "failed" and t["degrade_reason"]:
                    failure_reasons[t["degrade_reason"]] = failure_reasons.get(t["degrade_reason"], 0) + 1
            return ok({
                "generated": len(rows),
                "face_similarity_avg": round(sum(sims) / len(sims), 2) if sims else 0,
                "face_similarity_pass_rate": round(sum(1 for s in sims if s >= 0.8) / len(sims) * 100, 1) if sims else 0,
                "first_frame_match_avg": round(sum(frames) / len(frames), 2) if frames else 0,
                "failure_reasons": failure_reasons,
                "degrade_summary": "; ".join("%s×%d" % (k, v) for k, v in failure_reasons.items()) or "无降级",
            })
        finally:
            conn.close()
