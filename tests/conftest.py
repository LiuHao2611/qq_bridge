# -*- coding: utf-8 -*-
"""pytest 公共 fixture 与同步协程入口。

两件事：
  1. `run(coro)` —— 同步执行协程，供**同步风格**的用例调用 async 业务逻辑
     （test_taskmgr / test_taskmgr_local / test_status_reporting 依赖它）。
     用 `asyncio.run`：每次新建事件循环，用例之间互不污染。
  2. `anyio_backend` fixture —— 异步用例（标 `@pytest.mark.anyio`）用 asyncio 后端。
     用 anyio 而非 pytest-asyncio：anyio 已随依赖装好且自带 pytest 插件，**零新增依赖**。

另：把 qq_bridge 根与 src 加入 sys.path，让 `src.plugins.qq_bridge.*` 可直接导入
（queue / intent / taskmgr 等均不依赖 nonebot，可裸跑单测）。
"""
import asyncio
import os
import tempfile

import pytest

# ---------------------------------------------------------------------------
# 日志隔离（必须在任何测试模块 import src.plugins.qq_bridge 之前生效）
# ---------------------------------------------------------------------------
# config.LOG_DIR 在 **import 时** 就按 QQ_LOG_DIR 定死了，晚一步就来不及换目录。
# conftest 由 pytest 最先加载，所以只能放这里。
#
# 不隔离的后果（2026-11-05 实测）：tests/test_plan_commands.py 直接调 game_tools
# 的 handler，伪造的群消息（group_id=1075851417、user_id=3287416492）连同真实
# 群号一起写进生产 logs/qq_bridge.log。事后排查时那批假消息和真群消息长得一模
# 一样，差点被判成「群聊总闸被绕过」。
os.environ.setdefault("QQ_LOG_DIR", tempfile.mkdtemp(prefix="qq_bridge_pytest_logs_"))

# 注意：**不要在这里往 sys.path 塞路径**。
# pyproject.toml 已配 pythonpath=["."]，pytest 会自动把 qq_bridge 根加入，
# `src.plugins.qq_bridge.*` 正常可导入。额外插入 ROOT/src 会让同一模块以
# 不同限定名被重复加载 → 出现「两份 Config 类」（setattr 改 A 份、业务读 B 份），
# 热生效类用例集体假失败。需要隔离路径的用例请各自在文件内 insert。


def run(coro):
    """同步跑一个协程并返回结果（测试专用）。"""
    return asyncio.run(coro)


@pytest.fixture
def anyio_backend():
    """固定用 asyncio 作为 anyio 后端。"""
    return "asyncio"
