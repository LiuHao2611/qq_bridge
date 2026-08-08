# -*- coding: utf-8 -*-
"""Layer 0 · Computing Resource Pool（执行手册 §2.1 Layer 0: Computing Resource Pool）。

抽象：多队列优先级调度、动态并发度、超时熔断（手册 §3.3.5：普通队列 120s、
高速队列 60s 超时自动熔断；并行度由剩余资源动态决定）。

本地实现（LocalComputePool）：复用项目既有单进程 worker 的线程池与优先级队列模型
（见 worker.py 的 ThreadPoolExecutor + generation_tasks 优先级排序），保持本地可运行。
"""
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional


NORMAL_TIMEOUT = 120   # 普通队列超时（秒，手册 §3.3.5）
FAST_TIMEOUT = 60      # 高速队列超时（秒）


class ComputePool(ABC):
    """算力池抽象：提交带优先级的任务，返回 task_id。"""

    @abstractmethod
    def submit(self, fn: Callable, *, priority: int = 0, queue: str = "normal", timeout: int = NORMAL_TIMEOUT, args=(), kwargs=None) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_status(self, task_id: str) -> Dict:
        raise NotImplementedError


class LocalComputePool(ComputePool):
    """本地实现：单进程线程池 + 内存态任务表（与 worker.py 模型一致）。

    说明：生产环境的真实分发仍由 worker.py 的 _loop 消费 generation_tasks 表完成；
    本池作为 Director Agent Core 对「算力资源」的统一抽象入口，便于后续替换为
    分布式队列而不动上层。当前 submit 直接委托给线程池执行，状态存于内存表。
    """

    def __init__(self, max_workers: int = 2):
        self._exec = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="director-compute")
        self._tasks: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def submit(self, fn, *, priority: int = 0, queue: str = "normal", timeout: int = NORMAL_TIMEOUT, args=(), kwargs=None):
        task_id = "cmp_" + uuid.uuid4().hex[:12]
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id, "priority": priority, "queue": queue,
                "timeout": timeout, "status": "queued", "submitted_at": int(time.time()),
            }
        # 注：本地直跑模式直接提交线程池；分布式模式此处改为入队并返回 task_id。
        self._exec.submit(self._wrap, task_id, fn, args or (), kwargs or {})
        return task_id

    def _wrap(self, task_id, fn, args, kwargs):
        with self._lock:
            self._tasks[task_id]["status"] = "running"
            self._tasks[task_id]["started_at"] = int(time.time())
        try:
            fn(*args, **kwargs)
            with self._lock:
                self._tasks[task_id]["status"] = "succeeded"
        except Exception:
            with self._lock:
                self._tasks[task_id]["status"] = "failed"
        finally:
            with self._lock:
                self._tasks[task_id]["finished_at"] = int(time.time())

    def get_status(self, task_id: str) -> Dict:
        with self._lock:
            return dict(self._tasks.get(task_id, {"task_id": task_id, "status": "unknown"}))


# 默认本地池实例（依赖倒置：上层只依赖 ComputePool 接口）
default_pool = LocalComputePool()
