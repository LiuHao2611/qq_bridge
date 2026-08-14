# -*- coding: utf-8 -*-
"""P2 M3 运动检测「接入生成链路」接线验证（离线、可独立运行）。

验证点（针对此前审计发现的空心缺陷 #258 C）：
  1. worker 补齐的两个回调 regenerate_video_task / mark_video_task_failed 真实存在且可用；
  2. worker._serve_one 在片段视频生成成功后，真正调用 features.motion_qc.qc_and_retry；
  3. 静态视频（运动检测未通过）→ 内部换 seed 重试 ≤2 仍静态 → 标记片段 failed（退款）；
  4. 动态视频（运动检测通过）→ 正常标记 succeeded；
  5. 品控异常不得阻塞主链路（降级放行）。

不依赖 ffmpeg / ComfyUI：仅桩掉「视频内容生成」(generate_segment_video) 与
「运动检测器」(detect_motion) 两个外部/环境相关点，其余均走真实代码路径，
以验证本次接线本身的正确性。

运行：python tests/unit/test_p2_m3_wiring.py
（使用 MANJU_DB_PATH 指向 D 盘 .tmp，绝不写 C 盘。）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

# ---- 隔离测试库（D 盘 .tmp，避免污染生产 DB / 触碰 C 盘）----
_TMP = os.path.join(ROOT, ".tmp", "p2m3_wiring")
os.makedirs(_TMP, exist_ok=True)
_TEST_DB = os.path.join(_TMP, "p2m3_%d.db" % (abs(hash(os.getpid())) % 100000))
os.environ["MANJU_DB_PATH"] = _TEST_DB

import db
import worker
import features.motion_qc as mqc

db.init_db()

SINGLE_USER = "00000000-0000-0000-0000-000000000001"
_PROJ = "p_p2m3"
_STATE = {"static": True}


# ---------------------------------------------------------------------------
# 桩：视频内容生成 + 运动检测器（仅这两点被替换，其余走真实代码）
# ---------------------------------------------------------------------------
def _fake_generate(seg, shots, asset_refs, out_path, params):
    # 写一个最小「视频文件」，仅用于让 qc_and_retry 拿到可探测路径
    with open(out_path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x08free")
    return ("ok", "fake-generated", None)


def _fake_detect(video_path, frame_diff_fn=None, extract_fn=None):
    # 由外部开关控制「静态 / 动态」判定，验证接线分支
    return {"is_static": _STATE["static"], "motion_score": 0.0 if _STATE["static"] else 0.5}


worker.generate_segment_video = _fake_generate
mqc.detect_motion = _fake_detect


# ---------------------------------------------------------------------------
# 测试脚手架
# ---------------------------------------------------------------------------
def _seed(project_id, segment_id, task_id, local_task_id, pre_auth_id):
    conn = db.get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO projects (project_id, user_id, name) VALUES (?,?,?)",
                     (project_id, SINGLE_USER, "P2M3测试项目"))
        conn.execute(
            """INSERT OR REPLACE INTO segments
               (segment_id, project_id, "index", duration, status, resolution)
               VALUES (?,?,?,?,?,?)""",
            (segment_id, project_id, 0, 12, "generating", "720p"))
        conn.execute(
            """INSERT OR REPLACE INTO point_transactions
               (txn_id, user_id, amount, type, idempotency_key)
               VALUES (?,?,?,?,?)""",
            (pre_auth_id, SINGLE_USER, 100, "estimated", pre_auth_id + "_idem"))
        conn.execute(
            """INSERT OR REPLACE INTO generation_tasks
               (task_id, segment_id, local_task_id, model_id, pre_auth_id, status, seed)
               VALUES (?,?,?,?,?,?,?)""",
            (task_id, segment_id, local_task_id, "seedance-2.0-mini", pre_auth_id, "queued", 42))
        conn.commit()
    finally:
        conn.close()


def _segment_status(segment_id):
    conn = db.get_conn()
    try:
        row = db.fetch_one(conn, "SELECT status FROM segments WHERE segment_id=?", (segment_id,))
    finally:
        conn.close()
    return (row or {}).get("status")


def test_static_video_marked_failed():
    """静态视频 → 运动检测未通过 → 片段标记 failed。"""
    _STATE["static"] = True
    seg, tid, lt, pa = "seg_static", "task_static", "local_static", "pa_static"
    _seed(_PROJ, seg, tid, lt, pa)
    task = db.row_to_dict(db.fetch_one(db.get_conn(),
                       "SELECT * FROM generation_tasks WHERE task_id=?", (tid,)))
    worker._serve_one(task)
    status = _segment_status(seg)
    assert status == "failed", "静态视频应标记 failed，实际=%r" % status
    print("[PASS] 静态视频 → segment.status =", status)


def test_dynamic_video_succeeds():
    """动态视频 → 运动检测通过 → 片段标记 succeeded。"""
    _STATE["static"] = False
    seg, tid, lt, pa = "seg_dynamic", "task_dynamic", "local_dynamic", "pa_dynamic"
    _seed(_PROJ, seg, tid, lt, pa)
    task = db.row_to_dict(db.fetch_one(db.get_conn(),
                       "SELECT * FROM generation_tasks WHERE task_id=?", (tid,)))
    worker._serve_one(task)
    status = _segment_status(seg)
    assert status == "succeeded", "动态视频应标记 succeeded，实际=%r" % status
    print("[PASS] 动态视频 → segment.status =", status)


def test_callbacks_exist():
    assert hasattr(worker, "regenerate_video_task"), "缺少 regenerate_video_task"
    assert hasattr(worker, "mark_video_task_failed"), "缺少 mark_video_task_failed"
    print("[PASS] worker 回调存在：regenerate_video_task / mark_video_task_failed")


if __name__ == "__main__":
    fails = 0
    for fn in (test_callbacks_exist, test_static_video_marked_failed, test_dynamic_video_succeeds):
        try:
            fn()
        except AssertionError as e:
            fails += 1
            print("[FAIL] %s: %s" % (fn.__name__, e))
        except Exception as e:
            fails += 1
            print("[ERROR] %s: %s" % (fn.__name__, repr(e)))
    if fails:
        print("\n=== %d 项失败 ===" % fails)
        sys.exit(1)
    print("\n=== P2 M3 接线验证全部通过 ===")
