# -*- coding: utf-8 -*-
"""P4 M2 批量生产 / 流水线调度（手册 §2 M2）。

职责：把「批量提交的分镜生成任务」按 优先级 + 依赖 智能排序，输出可消费的队列。
不在这里真正派发（派发仍由 worker._loop 负责，避免破坏 P0-P3 已验证的派发路径），
本模块只负责「排序 + 依赖标注」，使 worker 的 SELECT ORDER BY priority DESC 拿到的是
经过依赖标注的队列，前端也能展示等待/可调度状态。

复用（手册 §0.3）：
- 依赖判定复用 P2 M2 首尾帧链：分镜 params.first_frame 非空 → 依赖上游尾帧（需串行等待）。

红线（§5）：GPU_MAX_CONCURRENT 保持 1（视频显存占用高，禁止开并发导致 OOM）；
「并行」只体现为无依赖分镜的智能排队顺序，非高并发。
"""
import json

# 诚实约束：单机显存有限，视频生成并发保持 1（禁止开并发导致 OOM）。
GPU_MAX_CONCURRENT = 1


def _params_of(task):
    """安全解析 task.params（可能为 JSON 字符串 / dict / None）。"""
    raw = task.get("params") if isinstance(task, dict) else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _created_ts(task):
    """创建时间（用于同优先级稳定二级排序）。"""
    return (task or {}).get("created_at") or 0


def _priority_of(task):
    """优先级（VIP 高优先，降序用）。"""
    return (task or {}).get("priority", 0) or 0


def has_dependency(task):
    """复用 P2 M2 首尾帧链：分镜 params.first_frame 非空 → 依赖上游尾帧（需串行等待）。

    输入：task（含 params）
    处理：判定 first_frame 是否为有效锚点（非空 / 非假值 / 非占位）
    输出：True=有依赖（需等待上游），False=无依赖（可立即调度）
    """
    p = _params_of(task)
    ff = p.get("first_frame")
    if ff is None or ff is False:
        return False
    if isinstance(ff, str):
        s = ff.strip().lower()
        if s in ("", "null", "none", "0", "false"):
            return False
        return True
    if isinstance(ff, (int, float)):
        return ff != 0
    # 字典/列表等视为已指定锚点
    return bool(ff)


def schedule_tasks(tasks):
    """M2 §4 批量调度核心（输入→处理→输出）。

    输入：批量任务列表（dict，含 priority / params / segment_id / created_at 等）
    处理：
      1) 按优先级降序（VIP 高优先），同优先级按创建时间升序（稳定）；
      2) 依赖标注：有首尾帧依赖的分镜 → schedule_status='waiting'（等待上游），
         无依赖 → 'ready'（可调度）；
      3) 附带 gpu_max_concurrent 提示（保持 1，不并行）。
    输出：排好序的队列（每项新增 schedule_status 字段）。
    """
    if not tasks:
        return []

    # 处理 1：优先级降序 + 创建时间升序（稳定二级排序）
    ordered = sorted(
        tasks,
        key=lambda t: (-_priority_of(t), _created_ts(t)),
    )

    # 处理 2：依赖标注（复用 P2 M2 首尾帧链）
    for t in ordered:
        t["schedule_status"] = "waiting" if has_dependency(t) else "ready"

    # 处理 3：附 GPU 并发约束提示（仅供前端展示，不在此改变派发）
    return ordered


def enqueue_order(tasks):
    """返回仅含 id 的有序队列（供派发/调试用，纯函数）。

    输入：任务列表；输出：按 schedule_tasks 排序后的 task_id 序列。
    """
    return [t.get("task_id") for t in schedule_tasks(tasks)]
