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

    # ---- 以下为原缺失而静默回落 INTERNAL_ERROR(500) 的错误码：补全以返回正确 HTTP 状态码 ----
    # 说明：ApiError.__init__ 对未登记 name 回退到 (40001, 500)，此前导致大量本应 4xx 的业务错误冒泡成 500。
    "ORB_DISABLED":            (40910, 409, "功能未开启（灰度中）"),
    "NO_STORY":                (20010, 422, "项目中暂无场次/分镜，请提供 story 文本"),
    "BATCH_CREATE_FAILED":     (40011, 500, "批量创建资产失败"),
    "GEN_FAILED":              (40012, 500, "生成失败"),
    "FACE_NO_IMAGE":           (20013, 400, "缺少人脸参考图"),
    "FACE_STORE_FAILED":       (40014, 500, "人脸向量提取成功但落库失败"),
    "ASSET_NOT_FOUND":         (40405, 404, "资产不存在"),
    "REFERENCE_SET_EMPTY":     (40406, 404, "该角色尚未生成参考图集"),
    "BAD_REF_KIND":            (10003, 400, "非法的参考图类型"),
    "READINESS_PROBE_FAILED":  (40015, 500, "就绪度探测失败"),
    "NOT_ROLE_ASSET":          (20016, 422, "仅支持角色卡导入"),
    "ROLE_IMPORT_FAILED":      (40016, 500, "角色卡导入失败"),
    "INVALID_TASK_TYPE":       (10004, 400, "非法的 task_type"),
    "TEMPLATE_TEST_FAILED":    (20017, 422, "模板不可用"),
    "SKILL_NOT_FOUND":         (40407, 404, "技能不存在"),
    "MODEL_SCAN_FAILED":       (40017, 500, "模型扫描失败"),
    "MODEL_REGISTER_FAILED":   (40018, 500, "模型注册失败"),
    "TASK_NOT_FOUND":          (40408, 404, "任务不存在"),
    "CLEAR_COMFY_FAILED":      (40019, 500, "清理 ComfyUI 缓存失败"),
    "FILE_TOO_LARGE":          (10005, 413, "文件过大"),
    "MAX_CHAPTERS":            (20018, 422, "章节数已达上限"),
    "AI_UNAVAILABLE":          (50005, 503, "AI 提供商不可用"),
    "ERR_INSUFFICIENT_PHOTOS": (20019, 400, "请至少上传 1 张人物参考照"),
    "BR_STORYBOARD_MISSING":   (20020, 422, "请先生成指导板"),
    "EMPTY_PATCH":             (20021, 422, "未提供任何身份卡字段"),
    "INVALID_ACTION":          (10006, 400, "非法的操作类型"),
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
