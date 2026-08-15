# -*- coding: utf-8 -*-
"""P4 M3 健壮性收尾 —— 单元测试（手册 §2 M3 验收函数）。

断言聚焦：
- handle_retry 递增 retry_count 并持久化 status='retrying'（修复原 bug：写回 'queued'）；
- handle_retry 硬上限真实生效：retry_count==MAX_AUTO_RETRY 后再失败 → 返回 'failed'（不再重试）；
- notify_failure 双通道：video:{local_task_id} + task:{task_id} 均收到 failed 事件；
- release_vram_if_idle：有运行任务 → 'busy'（不调用 ComfyUI）；空闲 → 调 free memory 返回 'released'。
"""
import os
import sys
import tempfile

# 必须在导入 db / worker 之前固定临时库（db.py 在 import 时解析 DB_PATH）。
_TMP = os.path.join(tempfile.mkdtemp(prefix="p4m3_"), "test.db")
os.environ["MANJU_DB_PATH"] = _TMP

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from db import init_db, get_conn, fetch_one, row_to_dict  # noqa: E402
init_db()

import worker  # noqa: E402
from worker import handle_retry, notify_failure, release_vram_if_idle, MAX_AUTO_RETRY  # noqa: E402


def _insert_task(conn, task_id, local_task_id, status="running", retry_count=0,
                 params="{}", segment_id="seg_x", pre_auth_id="pa_x"):
    conn.execute("PRAGMA foreign_keys=OFF")  # 单元测试隔离：不强制追溯 segments/projects 外键
    conn.execute(
        """INSERT INTO generation_tasks
           (task_id, segment_id, local_task_id, model_id, pre_auth_id, status, priority,
            retry_count, params)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (task_id, segment_id, local_task_id, "seedance-2.0-mini", pre_auth_id,
         status, 0, retry_count, params))
    conn.commit()


def test_handle_retry_persists_retrying():
    """失败首次重试：retry_count 1 + status='retrying'（修复原 'queued' bug）。"""
    conn = get_conn()
    try:
        _insert_task(conn, "t_retry_1", "lt_retry_1", status="running", retry_count=0)
        task = row_to_dict(fetch_one(conn, "SELECT * FROM generation_tasks WHERE task_id=?", ("t_retry_1",)))
        res = handle_retry(conn, task, RuntimeError("mock fail"))
        row = row_to_dict(fetch_one(conn, "SELECT * FROM generation_tasks WHERE task_id=?", ("t_retry_1",)))
        assert res == "retrying"
        assert row["status"] == "retrying"          # 关键修复点：不再是 'queued'
        assert row["retry_count"] == 1
        assert row["next_attempt_at"] is not None
    finally:
        conn.close()


def test_handle_retry_hard_cap_failed():
    """硬上限真实生效：retry_count==MAX_AUTO_RETRY 再失败 → 'failed'，不再重试。"""
    # 捕获 ws 发布 + 屏蔽 push_message 副作用
    published = []
    orig_pub = worker.ws_publish
    worker.ws_publish = lambda ch, ev, data=None: published.append((ch, ev, data))
    worker.push_message = lambda *a, **k: None
    conn = get_conn()
    try:
        _insert_task(conn, "t_cap_1", "lt_cap_1", status="running", retry_count=MAX_AUTO_RETRY)
        task = row_to_dict(fetch_one(conn, "SELECT * FROM generation_tasks WHERE task_id=?", ("t_cap_1",)))
        res = handle_retry(conn, task, RuntimeError("final fail"))
        row = row_to_dict(fetch_one(conn, "SELECT * FROM generation_tasks WHERE task_id=?", ("t_cap_1",)))
        assert res == "failed"
        assert row["status"] == "failed"
        assert row["error_code"] == "50001"
        # 双通道：video:{local_task_id} 收到 failed（M3 §4.2 修复点）
        video_ch = "video:lt_cap_1"
        assert any(ch == video_ch and ev == "failed" for ch, ev, _ in published), published
        assert any(ch == "task:t_cap_1" and ev == "failed" for ch, ev, _ in published)
    finally:
        worker.ws_publish = orig_pub
        conn.close()


def test_notify_failure_double_channel():
    """notify_failure 双通道：video:{genId} + task:{id} 均推 failed（含原因）。"""
    published = []
    orig = worker.ws_publish
    worker.ws_publish = lambda ch, ev, data=None: published.append((ch, ev, data))
    try:
        task = {"task_id": "t_notify_1", "local_task_id": "lt_notify_1",
                "error_message": "显存不足 OOM"}
        notify_failure(task)
        assert any(ch == "video:lt_notify_1" and ev == "failed" for ch, ev, _ in published)
        assert any(ch == "task:t_notify_1" and ev == "failed" for ch, ev, _ in published)
        task_evt = [d for ch, ev, d in published if ch == "task:t_notify_1"][0]
        assert task_evt["reason"] == "显存不足 OOM"
    finally:
        worker.ws_publish = orig


def test_release_vram_busy_when_running():
    """有运行任务 → 'busy'，不调用 ComfyUI free memory。"""
    freed = {"called": False}

    class _FakeSvc:
        def ping(self):
            return True

        def free_memory(self):
            freed["called"] = True
            return True

    import layer0.comfyui_service as _cs
    _orig = _cs.get_service
    _cs.get_service = lambda *a, **k: _FakeSvc()
    conn = get_conn()
    try:
        _insert_task(conn, "t_busy_1", "lt_busy_1", status="running")
        result = release_vram_if_idle()
        assert result == "busy"
        assert freed["called"] is False   # 有运行任务，不应释放
    finally:
        _cs.get_service = _orig
        conn.close()


def test_release_vram_released_when_idle():
    """空闲 → 调 ComfyUI free memory，返回 'released'。"""
    freed = {"called": False}

    class _FakeSvc:
        def ping(self):
            return True

        def free_memory(self):
            freed["called"] = True
            return True

    import layer0.comfyui_service as _cs
    _orig = _cs.get_service
    _cs.get_service = lambda *a, **k: _FakeSvc()
    conn = get_conn()
    try:
        # 清掉运行态，确保空闲
        conn.execute("UPDATE generation_tasks SET status='succeeded' WHERE status='running'")
        conn.execute("UPDATE image_tasks SET status='succeeded' WHERE status='running'")
        conn.execute("UPDATE lora_train_tasks SET status='succeeded' WHERE status='running'")
        conn.commit()
        result = release_vram_if_idle()
        assert result == "released"
        assert freed["called"] is True    # 空闲时优雅释放
    finally:
        _cs.get_service = _orig
        conn.close()


if __name__ == "__main__":
    test_handle_retry_persists_retrying()
    test_handle_retry_hard_cap_failed()
    test_notify_failure_double_channel()
    test_release_vram_busy_when_running()
    test_release_vram_released_when_idle()
    print("P4 M3 全部断言通过 ✅")
