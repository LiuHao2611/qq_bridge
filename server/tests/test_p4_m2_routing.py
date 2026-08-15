# -*- coding: utf-8 -*-
"""P4 M2 路由序回归测试（手册 §2 M2 / §5 红线）。

防止编译基线 routers_compiled 先注册的通用详情路由 GET /tasks/{task_id}
抢先匹配 GET /tasks/queue（task_id='queue' → 任务不存在）的静默缺陷复发。

断言：/tasks/queue 的注册序必须排在 /tasks/{task_id} 之前（精确字面匹配优先）。
"""
import sys
import os

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import routers  # noqa: E402


def _idx(path_suffix):
    for i, r in enumerate(routers.api.routes):
        p = getattr(r, "path", None) or ""
        if p.endswith(path_suffix):
            return i
    return None


def test_queue_route_precedes_detail_route():
    qi = _idx("/tasks/queue")
    di = _idx("/tasks/{task_id}")
    assert qi is not None, "GET /tasks/queue 路由缺失（M2 端点未注册）"
    assert di is not None, "GET /tasks/{task_id} 路由缺失（P0-P3 既有能力被破坏）"
    assert qi < di, (
        "路由序错误：/tasks/queue 必须排在 /tasks/{task_id} 之前，"
        "否则字面路由被通用详情路由抢匹配（返回 '任务不存在'）"
    )


def test_priority_and_comfy_cache_routes_registered():
    assert _idx("/tasks/{id}/priority") is not None, "POST /tasks/{id}/priority 未注册"
    assert _idx("/resource/clear-comfy-cache") is not None, "POST /resource/clear-comfy-cache 未注册"


if __name__ == "__main__":
    test_queue_route_precedes_detail_route()
    test_priority_and_comfy_cache_routes_registered()
    print("P4 M2 路由序回归测试通过 ✅")
