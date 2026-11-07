# -*- coding: utf-8 -*-
"""下发给主项目的任务超时必须夹在平台允许区间（2026-09-13）。

背景（真 bug）：主项目 server/routers.py 的 /t2i/edit 有 `timeout` 上限校验——
`if timeout <= 0 or timeout > 18000: timeout = 1800`。QQ 端若把用户配置的
TASK_TIMEOUT（例如 999999999）原样下发，会被平台**静默改成 1800（30 分钟）**，
用户以为"调大了"其实没生效 —— 表现为"任务还是 30 分钟就超时失败"。

修复：queue._platform_timeout() 把下发值夹进 [60, 18000]。

运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_platform_timeout_clamp.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from plugins.qq_bridge import queue as q  # noqa: E402


@pytest.mark.parametrize("configured,expect", [
    (1800, 1800),          # 常规值 → 原样
    (600, 600),            # 平台/历史默认 → 原样
    (18000, 18000),        # 平台上限 → 允许
    (999999999, 18000),    # 用户填超大值 → 夹到上限（而不是被平台改成 1800）
    (30, 60),              # 过小 → 抬到下限
    (0, 600),              # 0/空视为「未配置」→ 兜底 600（不是夹到下限）
    ("abc", 600),          # 脏值 → 兜底 600
])
def test_platform_timeout_clamped(monkeypatch, configured, expect):
    monkeypatch.setattr(q, "_cfg", lambda name, default=None: configured)
    assert q._platform_timeout() == expect
