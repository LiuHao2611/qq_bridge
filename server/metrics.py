# -*- coding: utf-8 -*-
"""Prometheus 监控指标（手册 §7.2 监控体系）。

暴露：生成任务计数 / 耗时直方图 / 导演队列深度 / 活跃运行数。
所有指标为进程内自增，不依赖外部服务；目标机部署时由 Prometheus 抓取 :7788/metrics。

若运行环境未安装 prometheus_client（部署遗漏），本模块自动降级为 no-op，
保证后端不因监控依赖缺失而崩溃（诚实降级，启动期打印一次告警）。
"""
import time
import functools

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST,
    )
    _HAVE_PROM = True
except Exception:  # pragma: no cover - 仅部署遗漏时触发
    _HAVE_PROM = False
    import logging
    logging.getLogger("metrics").warning(
        "prometheus_client 未安装，监控指标降级为 no-op；请 pip install prometheus_client")

    class _Noop:
        def __init__(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

        def inc(self, *a, **k):
            pass

        def observe(self, *a, **k):
            pass

        def set(self, *a, **k):
            pass

        def dec(self, *a, **k):
            pass

    Counter = Histogram = Gauge = _Noop
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"

    def generate_latest():
        return b"# prometheus_client not installed; metrics disabled\n"


# 生成任务总数（按类型与结果）。task_type: comic_image / video / face_restore / tts
GENERATION_TOTAL = Counter(
    "manju_generation_total",
    "生成任务总数（按类型与结果）",
    ["task_type", "status"],
)

# 生成任务耗时（秒），按类型分桶
GENERATION_DURATION = Histogram(
    "manju_generation_duration_seconds",
    "生成任务耗时（秒）",
    ["task_type"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1200),
)

# 当前待处理/执行中的导演任务数
QUEUE_DEPTH = Gauge(
    "manju_queue_depth",
    "当前待处理/执行中的导演任务数",
)

# 当前活跃导演运行数
ACTIVE_RUNS = Gauge(
    "manju_active_runs",
    "当前活跃导演运行数",
)


def observe_generation(task_type, status, duration=None):
    """记录一次生成的计数与耗时。status: success / failed。"""
    GENERATION_TOTAL.labels(task_type=task_type, status=status).inc()
    if duration is not None:
        GENERATION_DURATION.labels(task_type=task_type).observe(duration)


def set_queue_depth(n):
    QUEUE_DEPTH.set(max(0, int(n)))


def set_active_runs(n):
    ACTIVE_RUNS.set(max(0, int(n)))


def track_generation(task_type):
    """函数装饰器：自动埋点生成计数与时间。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            t0 = time.time()
            try:
                r = fn(*a, **k)
                observe_generation(task_type, "success", time.time() - t0)
                return r
            except Exception:
                observe_generation(task_type, "failed", time.time() - t0)
                raise
        return wrapper
    return deco


def metrics_response():
    """返回 Prometheus exposition 格式文本与 content-type。"""
    return generate_latest(), CONTENT_TYPE_LATEST
