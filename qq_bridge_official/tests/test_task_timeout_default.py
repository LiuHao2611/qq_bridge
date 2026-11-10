"""任务看门狗超时默认值为 10 分钟（600 秒）。

背景：用户要求把生图任务的最长等待时长由 1800 秒（30 分钟）改为 10 分钟，
避免单个任务长时间占用队列，导致后续任务一直「排队中」。

覆盖点：
1. Config 类属性 TASK_TIMEOUT 默认 600（队列 wait_task 的看门狗读取该值）；
2. 管理页配置项定义里的默认值同步为 600，避免设置页仍显示 1800 造成认知不一致。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

EXPECTED = 600  # 10 分钟


def _config_module():
    from plugins.qq_bridge import config  # noqa: WPS433（测试内局部导入）

    return config


def test_task_timeout_default_is_600():
    cfg = _config_module()
    assert getattr(cfg.Config, "TASK_TIMEOUT", None) == EXPECTED, (
        "TASK_TIMEOUT 默认应为 %s 秒（10 分钟），实际 %r"
        % (EXPECTED, getattr(cfg.Config, "TASK_TIMEOUT", None))
    )


def test_config_api_default_is_600():
    """管理页「任务看门狗(秒)」配置项默认值必须同步为 600。"""
    from plugins.qq_bridge import config_api  # noqa: WPS433

    items = getattr(config_api, "SCHEMA", None) or getattr(
        config_api, "CONFIG_ITEMS", None
    )
    assert items, "config_api 里未找到配置 SCHEMA，无法校验设置页默认值"
    hit = [it for it in items if len(it) > 2 and it[1] == "TASK_TIMEOUT"]
    assert hit, "配置项定义里未找到 TASK_TIMEOUT，无法保证设置页默认值一致"
    assert str(hit[0][7]) == str(EXPECTED), (
        "设置页 TASK_TIMEOUT 默认值应为 %s，实际 %r" % (EXPECTED, hit[0][7])
    )
