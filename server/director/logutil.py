# -*- coding: utf-8 -*-
"""导演调度层结构化日志工具（AGENTS.md §08 LOG-01/02/03/04）。

LOG-01  trace_id 贯穿全链路
LOG-02  级别规范：info=状态迁移 / warn=降级重试 / error=需人工介入
LOG-03  event 使用 snake_case，全局唯一
LOG-04  敏感信息（password/token/apiKey/secret/key）禁止落日志
"""
import json
import logging
import time

logger = logging.getLogger("director")


def blog(trace_id, module, event, level="info", **kw):
    """输出一条结构化 JSON 日志。返回该记录字典（便于测试断言）。

    :param trace_id: 链路追踪 ID（LOG-01）
    :param module:   产生日志的模块/agent 标识
    :param event:    snake_case 事件名（LOG-03）
    :param level:    info | warn | error（LOG-02）
    """
    rec = {
        "ts": int(time.time() * 1000),
        "trace_id": trace_id or "-",
        "module": module,
        "event": event,
        "level": level,
    }
    rec.update(kw)
    # LOG-04：剔除敏感字段
    for k in ("password", "token", "apiKey", "api_key", "secret", "key", "apiKey"):
        rec.pop(k, None)
    logger.info(json.dumps(rec, ensure_ascii=False))
    return rec
