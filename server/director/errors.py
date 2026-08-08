# -*- coding: utf-8 -*-
"""导演调度 Agent 错误码体系（执行手册 §6 · Error Code & Retry Mechanism）。

与 common.ERROR_CODES 并存：common 现有的 5 位码（10001/20001/...）服务于旧接口，
本模块按手册 §6 的 4 位分段分类（1000-1999 / 2000-2999 / 3000-3999 / 4000-4999 /
5000-5999）定义 Director Agent Core 的错误分类与重试策略，供 infra/director 包统一使用。

映射关系（手册 §6.1）：
  Parameter Error 1000-1999  不重试，直接返回参数错误
  Asset Error     2000-2999  不重试，返回资产不可用
  Model Error     3000-3999  自动重试 2 次，间隔 5s，失败降级模型
  Schedule Error  4000-4999  自动重试 3 次，指数退避，失败降级队列
  System Error    5000-5999  自动重试 1 次，失败告警人工介入
"""
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class ErrorCategory(IntEnum):
    PARAMETER = 1000
    ASSET = 2000
    MODEL = 3000
    SCHEDULE = 4000
    SYSTEM = 5000


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int          # 0 = 不重试
    backoff_base: float       # 秒；指数退避时为 base
    backoff_cap: float        # 单次退避上限（秒）
    backoff_strategy: str     # constant | exponential
    degrade_on_exhaust: str   # none | model | queue
    retryable: bool


RETRY_POLICIES: dict[ErrorCategory, RetryPolicy] = {
    ErrorCategory.PARAMETER: RetryPolicy(0, 0, 0, "constant", "none", False),
    ErrorCategory.ASSET:     RetryPolicy(0, 0, 0, "constant", "none", False),
    ErrorCategory.MODEL:     RetryPolicy(2, 5, 5, "constant", "model", True),
    ErrorCategory.SCHEDULE:  RetryPolicy(3, 1, 4, "exponential", "queue", True),
    ErrorCategory.SYSTEM:    RetryPolicy(1, 2, 2, "constant", "none", True),
}


# 具体错误键 → (code, 默认消息)。code 落在对应 category 区间内。
AGENT_ERRORS: dict[str, tuple[int, str]] = {
    # Parameter（不重试）
    "INVALID_PARAMETER":         (ErrorCategory.PARAMETER + 1, "参数校验失败"),
    "MISSING_SCRIPT":            (ErrorCategory.PARAMETER + 2, "缺少待解析的剧本/分镜文本"),
    # Asset（不重试）
    "ASSET_BIND_FAILED":         (ErrorCategory.ASSET + 1, "资产自动绑定失败（相似度低于阈值，待人工确认）"),
    "ASSET_NOT_FOUND":           (ErrorCategory.ASSET + 2, "资产不存在"),
    "ASSET_PERMISSION_DENIED":   (ErrorCategory.ASSET + 3, "资产权限校验未通过，禁止注入"),
    # Model（重试2次 → 降级模型）
    "SCRIPT_PARSE_FAILED":       (ErrorCategory.MODEL + 1, "脚本结构化解析失败"),
    "VISUAL_ENHANCE_FAILED":     (ErrorCategory.MODEL + 2, "全局视觉参数注入失败"),
    "REAL_ASSET_INJECT_FAILED":  (ErrorCategory.MODEL + 3, "真人资产特征注入失败"),
    "SHOT_GENERATE_FAILED":      (ErrorCategory.MODEL + 4, "分镜视频生成失败"),
    "QUALITY_CHECK_FAILED":      (ErrorCategory.MODEL + 5, "单分镜画质/一致性校验失败"),
    "AUDIO_SYNTH_FAILED":        (ErrorCategory.MODEL + 6, "音频合成失败"),
    # Schedule（重试3次指数退避 → 降级队列）
    "SCHEDULE_FAILED":           (ErrorCategory.SCHEDULE + 1, "时序编排/算力调度失败"),
    "TAIL_FRAME_ANCHOR_FAILED":  (ErrorCategory.SCHEDULE + 2, "首尾帧锚定失败"),
    "DISPATCH_FAILED":           (ErrorCategory.SCHEDULE + 3, "任务分发失败"),
    # System（重试1次 → 告警）
    "COMPOSITION_FAILED":        (ErrorCategory.SYSTEM + 1, "视频合成失败"),
    "INTERNAL_PIPELINE_ERROR":   (ErrorCategory.SYSTEM + 2, "流水线内部错误"),
}


class AgentError(Exception):
    """导演调度层统一异常，携带 category / code / 重试策略，便于上层按 §6 处理。"""

    def __init__(self, key: str, detail: Optional[str] = None, *, cause: Optional[Exception] = None, **extra):
        code, msg = AGENT_ERRORS.get(key, (ErrorCategory.SYSTEM + 2, "未知导演调度错误"))
        self.key = key
        self.code = code
        self.category = (code // 1000) * 1000
        self.category_enum = ErrorCategory(self.category)
        self.message = detail or msg
        self.policy = RETRY_POLICIES[self.category_enum]
        self.extra = extra
        self.__cause__ = cause
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "code": self.code,
            "category": self.category_enum.name,
            "message": self.message,
            "retryable": self.policy.retryable,
            "max_retries": self.policy.max_retries,
            **self.extra,
        }


def retry_after(err: "AgentError", attempt: int) -> float:
    """计算第 attempt 次失败后需等待的秒数（attempt 从 0 开始）。

    尊重手册 §6.2 / REDUN-02：指数退避 1s/2s/4s（或模型类常量 5s）、上限 3 次、
    加 ±20% 抖动避免惊群；不可重试或已达上限返回 0。
    """
    import random
    p = err.policy
    if not p.retryable or attempt >= p.max_retries:
        return 0.0
    if p.backoff_strategy == "exponential":
        delay = p.backoff_base * (2 ** attempt)
    else:
        delay = p.backoff_base * (attempt + 1)
    return min(p.backoff_cap, delay * (0.8 + 0.4 * random.random()))
