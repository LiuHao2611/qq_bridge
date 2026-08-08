# -*- coding: utf-8 -*-
"""Director → generation_tasks 入队辅助（full 版 ②：复用 worker 消费）。

严循 AGENTS.md §08 纪律：
  - CLOSE-04（资金安全，最高优先级）：入队前必须 pre-auth（冻结即真实扣减）；
    插入 generation_tasks 失败则自动退还冻结金（绝不遗留孤儿冻结）；
    idempotency_key 由 trace_id + segment_id 派生命名，杜绝 Date.now()+random（CLOSE-02）。
  - CLOSE-01：插入即 status='queued'，终态收敛（succeeded/failed/cancelled）交由 worker 负责。
  - CLOSE-02：task_id / local_task_id 用 db.uid()（uuid4），单次运行内唯一；跨运行靠 trace_id 隔离。

本模块是 Director 与现有 worker 队列之间的唯一桥梁：Director 不再直调 ai_local.generate_segment_video，
改为入队，worker._serve_one 原样消费（复用其重试 / 退款 / 超时 / settle 全链路）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db import get_conn, uid
from points_engine import estimate_credits, pre_auth, refund
from common import ApiError

# 默认视频生成模型（与 worker._serve_one / 现有 dispatch 端点对齐）
DEFAULT_MODEL_ID = "seedance-2.0-mini"


def estimate_segment_credits(duration, resolution, model_id=DEFAULT_MODEL_ID):
    """积分估算（SCHEMA_INDEX §6 / points_engine.estimate_credits）。

    估算失败（REDUN 降级）按基础费率 180×duration 保守冻结，避免阻断主流程。
    """
    try:
        return int(estimate_credits({
            "duration": int(duration or 12),
            "resolution": resolution or "720p",
            "audio": True,
            "model_id": model_id,
        })["estimated_credits"])
    except Exception:
        return 180 * int(duration or 12)


def enqueue_segment(segment_id, trace_id, *, duration=12, model_id=DEFAULT_MODEL_ID,
                    resolution="720p", seed=-1, asset_humanize=0, return_last_frame=0,
                    priority=0, params=None):
    """预授权 + 入队一个片段生成任务，返回 (task_id, local_task_id, pre_auth_id)。

    CLOSE-04：
      - 先 pre_auth（冻结即扣减），再插入 generation_tasks；
      - 若插入失败，自动退还冻结金，绝不遗留孤儿冻结；
      - 幂等：相同 (trace_id, segment_id) 的重复调用因 idempotency_key 唯一约束而安全。
    """
    amount = estimate_segment_credits(duration, resolution, model_id)
    idem = "dir-%s-%s" % (trace_id, segment_id)
    pa = pre_auth(amount, segment_id, idem)
    pre_auth_id = pa["pre_auth_id"]
    try:
        conn = get_conn()
        try:
            task_id = uid()
            local_task_id = uid()
            params_json = json.dumps(params or {}, ensure_ascii=False)
            conn.execute(
                """INSERT INTO generation_tasks
                   (task_id, segment_id, local_task_id, model_id, params,
                    asset_humanize, return_last_frame, seed, pre_auth_id,
                    cost_credits, status, priority, trace_id, progress)
                   VALUES (?,?,?,?,?, ?,?,?,?,?, 'queued', ?,?, 0)""",
                (task_id, segment_id, local_task_id, model_id, params_json,
                 int(asset_humanize), int(return_last_frame), int(seed),
                 pre_auth_id, amount, int(priority), trace_id))
            # 关联回 segments，便于追溯（generation_task_id）与断点续跑判定（status）
            conn.execute(
                "UPDATE segments SET generation_task_id=?, status='generating' WHERE segment_id=?",
                (task_id, segment_id))
            conn.commit()
            return task_id, local_task_id, pre_auth_id
        finally:
            conn.close()
    except Exception:
        # 插入失败：退还冻结金，避免孤儿冻结（CLOSE-04）
        try:
            refund(pre_auth_id, reason="入队失败回退")
        except Exception:
            pass
        raise


def fetch_task_statuses(task_ids):
    """批量读取 generation_tasks 状态，返回 {task_id: row_dict}。"""
    if not task_ids:
        return {}
    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in task_ids)
        rows = conn.execute(
            "SELECT task_id, status, progress, result_url, error_code, error_message "
            "FROM generation_tasks WHERE task_id IN (%s)" % placeholders,
            list(task_ids)).fetchall()
        return {r["task_id"]: dict(r) for r in rows}
    finally:
        conn.close()


def fetch_segment_video_url(segment_id):
    """读取片段最终 video_url（worker 成功时写入 segments.video_url）。"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT video_url, status FROM segments WHERE segment_id=?",
            (segment_id,)).fetchone()
        if row:
            d = dict(row)
            return d.get("video_url"), d.get("status")
        return None, None
    finally:
        conn.close()


# ---- Director 内存片段 → 真实 segments 行 的桥接（worker 依赖真实 DB 行） --------
_VALID_DUR = {5, 8, 10, 12, 15}
_VALID_RES = {"480p", "720p", "1080p", "2k", "4k"}


def _coerce_duration(d):
    try:
        d = int(d or 12)
    except Exception:
        d = 12
    if d in _VALID_DUR:
        return d
    return min(_VALID_DUR, key=lambda x: abs(x - d))


def _coerce_res(r):
    return r if r in _VALID_RES else "720p"


def _segment_id_exists(segment_id):
    conn = get_conn()
    try:
        return bool(conn.execute("SELECT 1 FROM segments WHERE segment_id=?", (segment_id,)).fetchone())
    finally:
        conn.close()


def _segment_at_index(project_id, index):
    """按位置映射：导演一站式生成通常基于项目已建分镜，按序对齐到既有 segments 行。"""
    conn = get_conn()
    try:
        r = conn.execute(
            'SELECT segment_id FROM segments WHERE project_id=? ORDER BY "index" ASC LIMIT 1 OFFSET ?',
            (project_id, index)).fetchone()
        return r["segment_id"] if r else None
    finally:
        conn.close()


def resolve_segment_id(seg, project_id, index):
    """解析 Director 内存片段对应的真实 segment_id（不落库）。

    返回 (real_segment_id, exists_in_db)。
      - 内存 seg 自带且已存在于 DB 的 id → 直接复用；
      - 否则按位置对齐项目既有 segments 行（导演基于已建分镜的场景）；
      - 都无 → 派生稳定 id `seg-<project>-<index>`（留给 persist_segment 创建，幂等复用）。
    """
    cand = seg.get("segment_id")
    if cand and _segment_id_exists(cand):
        return cand, True
    pos = _segment_at_index(project_id, index)
    if pos:
        return pos, True
    return "seg-%s-%d" % (project_id, index), False


def persist_segment(seg, project_id, index, bound_assets_for_seg=None, resolution=None):
    """把 Director 内存片段落库为真实 segments 行（含 shots / segment_asset_refs）。

    仅在行不存在时 INSERT（不覆盖用户既有分镜内容）；返回真实 segment_id。
    worker._serve_one 依赖真实 segments 行（含 shots/asset_refs）生成视频。
    """
    real_id, exists = resolve_segment_id(seg, project_id, index)
    if exists:
        return real_id
    conn = get_conn()
    try:
        dur = _coerce_duration(seg.get("duration"))
        res = _coerce_res(resolution or seg.get("resolution") or "720p")
        title = seg.get("title") or ("片段%d" % (index + 1))
        scene = seg.get("scene_description") or seg.get("summary") or ""
        seed = int(seg.get("seed") if seg.get("seed") is not None else -1)
        conn.execute(
            """INSERT INTO segments
               (segment_id, project_id, "index", title, duration, scene_description, status, resolution, seed)
               VALUES (?,?,?,?,?,?, 'saved', ?, ?)""",
            (real_id, project_id, index, title, dur, scene, res, seed))
        for j, sh in enumerate(seg.get("shots") or []):
            shot_id = sh.get("shot_id") or ("%s-shot-%d" % (real_id, j))
            ar = sh.get("asset_refs")
            if isinstance(ar, (list, dict)):
                ar = json.dumps(ar, ensure_ascii=False)
            conn.execute(
                """INSERT INTO shots
                   (shot_id, segment_id, "index", duration, shot_type, camera_movement,
                    camera_angle, focal_length, description, voiceover, dialogue, asset_refs)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (shot_id, real_id, j, int(sh.get("duration") or 4),
                 sh.get("shot_type"), sh.get("camera_movement"), sh.get("camera_angle"),
                 sh.get("focal_length"), sh.get("description"), sh.get("voiceover"),
                 sh.get("dialogue"), ar))
        for b in (bound_assets_for_seg or []):
            conn.execute(
                "INSERT INTO segment_asset_refs (ref_id, segment_id, asset_id, asset_type, stage_id, used_in) "
                "VALUES (?,?,?,?,?,?)",
                (uid(), real_id, b.get("asset_id"), b.get("asset_type") or "role",
                 b.get("stage_id"), b.get("used_in") or "shot"))
        conn.commit()
        return real_id
    finally:
        conn.close()
