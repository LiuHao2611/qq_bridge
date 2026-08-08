# -*- coding: utf-8 -*-
"""统一响应信封与错误码（AGENT_GUIDE §5 / SCHEMA_INDEX §7）。

所有接口返回：{ code, data, message, timestamp }
code = 0 表示成功；非 0 为错误码（见 ERROR_CODES）。
"""
import time
import uuid
import os
import logging


# 统一日志目录：D:\AI\logs（所有应用日志都落在这里，绝不放 C 盘）
# 允许通过 MANJU_LOG_DIR 覆盖（开发环境无管理员权限 / 默认日志文件被占用时可用）
_AI_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # D:\AI
LOG_DIR = os.environ.get("MANJU_LOG_DIR") or os.path.join(_AI_ROOT, "logs")


def setup_logging(name="app"):
    """把应用日志同时写到 D:/AI/logs/<name>.log 与 stdout（stdout 由启动脚本再重定向到 D 盘）。

    返回该模块的 logger，供 print 替代使用。幂等：重复调用不会叠加 handler。
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, name + ".log")
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    already = any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", "").replace("\\", "/").endswith(name + ".log")
        for h in root.handlers
    )
    if not already:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(sh)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
    # 让 uvicorn 的访问/错误日志也落到同一文件体系
    for nm in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(nm)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        if not lg.handlers:
            lg.addHandler(fh)
            lg.addHandler(sh)
    return logging.getLogger(name)


def envelope(code=0, data=None, message="ok", request_id=None):
    return {
        "code": code,
        "data": data,
        "message": message,
        "request_id": request_id or uuid.uuid4().hex,
        "timestamp": int(time.time() * 1000),
    }


def paginate(items, total, page, size):
    """KB 列表信封：{items, pagination:{page,page_size,total,total_pages,has_next,has_prev}}"""
    size = size or 1
    page = page or 1
    total_pages = (total + size - 1) // size if size else 0
    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }


# category{4-digit}: 1xxxx 参数 / 2xxxx 业务 / 3xxxx 鉴权 / 4xxxx 系统 / 5xxxx 第三方
ERROR_CODES = {
    "INVALID_PARAMS":       (10001, 400, "参数格式错误"),
    "MISSING_FIELD":        (10002, 400, "缺少必填字段"),
    "NOT_FOUND":            (40404, 404, "资源不存在"),
    "CONFLICT":             (40909, 409, "资源冲突"),
    "INSUFFICIENT_POINTS":  (20001, 402, "积分不足"),
    "DUPLICATE_REFERENCE":  (20002, 409, "资产引用已存在"),
    "MAX_REFS_EXCEEDED":    (20003, 422, "资产引用超过上限(9)"),
    "DURATION_SUM_MISMATCH":(20004, 422, "分镜时长之和与片段时长不一致"),
    "AUTH_REQUIRED":        (30001, 401, "需要本地鉴权"),
    "INTERNAL_ERROR":       (40001, 500, "系统内部错误"),
    "AI_PROVIDER_TIMEOUT":  (50001, 504, "AI 提供商超时"),
    "AI_CONTENT_BLOCKED":   (50002, 422, "内容被审核拒绝"),
    "AI_QUOTA_EXHAUSTED":   (50003, 429, "AI 上游配额耗尽"),
    "RUNNINGHUB_INVALID_PARAMS": (50004, 400, "工作流参数被拒绝"),
}


class ApiError(Exception):
    def __init__(self, name, detail=None, http_status=None):
        meta = ERROR_CODES.get(name, (40001, 500, "系统内部错误"))
        self.code = meta[0]
        self.http_status = http_status or meta[1]
        self.message = detail or meta[2]
        super().__init__(self.message)


def ok(data=None, message="ok"):
    return envelope(0, data, message)


def ok_envelope(code=0, data=None, message="ok"):
    return envelope(code, data, message)
