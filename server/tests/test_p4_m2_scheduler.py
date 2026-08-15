# -*- coding: utf-8 -*-
"""P4 M2 批量生产 / 流水线调度 —— 单元测试（手册 §2 M2 验收函数）。

断言聚焦：
- schedule_tasks 按优先级降序 + 同优先级创建时间升序（稳定）；
- 依赖标注复用 P2 M2 首尾帧链：params.first_frame 非空 → waiting，否则 ready；
- GPU_MAX_CONCURRENT 保持 1（禁并发 OOM 红线）；
- 空队列安全返回 []。
"""
import sys
import os

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from features.task_scheduler import (  # noqa: E402
    schedule_tasks,
    has_dependency,
    GPU_MAX_CONCURRENT,
)


def test_gpu_max_concurrent_is_one():
    """红线（§5）：GPU 并发保持 1，禁止开并发导致 OOM。"""
    assert GPU_MAX_CONCURRENT == 1


def test_schedule_priority_desc():
    """优先级降序：5 > 3 > 1。"""
    tasks = [
        {"task_id": "a", "priority": 1, "created_at": 1, "params": "{}"},
        {"task_id": "b", "priority": 5, "created_at": 2, "params": "{}"},
        {"task_id": "c", "priority": 3, "created_at": 3, "params": "{}"},
    ]
    out = schedule_tasks(tasks)
    assert [t["task_id"] for t in out] == ["b", "c", "a"]


def test_schedule_stable_same_priority_by_created_at():
    """同优先级按创建时间升序（稳定二级排序）。"""
    tasks = [
        {"task_id": "late", "priority": 9, "created_at": 50, "params": "{}"},
        {"task_id": "early", "priority": 9, "created_at": 10, "params": "{}"},
    ]
    out = schedule_tasks(tasks)
    assert [t["task_id"] for t in out] == ["early", "late"]


def test_schedule_dependency_waiting_vs_ready():
    """依赖标注（复用 P2 M2 首尾帧链）：有 first_frame → waiting；无 → ready。"""
    tasks = [
        {"task_id": "no_dep", "priority": 1, "created_at": 1, "params": "{}"},
        {"task_id": "with_dep", "priority": 1, "created_at": 2,
         "params": '{"first_frame": "/outputs/task_x.mp4"}'},
    ]
    out = schedule_tasks(tasks)
    by_id = {t["task_id"]: t for t in out}
    assert by_id["no_dep"]["schedule_status"] == "ready"
    assert by_id["with_dep"]["schedule_status"] == "waiting"


def test_has_dependency_variants():
    """first_frame 真值判定：有效路径 / 空 / 0 / 缺失 分别 True / False。"""
    assert has_dependency({"params": '{"first_frame": "/p/last.png"}'}) is True
    assert has_dependency({"params": '{"first_frame": ""}'}) is False
    assert has_dependency({"params": '{"first_frame": 0}'}) is False
    assert has_dependency({"params": '{"first_frame": false}'}) is False
    assert has_dependency({"params": "{}"}) is False
    # dict 形式锚点也视为有依赖
    assert has_dependency({"params": '{"first_frame": {"type": "image"}}'}) is True


def test_schedule_empty():
    """空队列安全返回 []。"""
    assert schedule_tasks([]) == []


if __name__ == "__main__":
    test_gpu_max_concurrent_is_one()
    test_schedule_priority_desc()
    test_schedule_stable_same_priority_by_created_at()
    test_schedule_dependency_waiting_vs_ready()
    test_has_dependency_variants()
    test_schedule_empty()
    print("P4 M2 全部断言通过 ✅")
