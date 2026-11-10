# -*- coding: utf-8 -*-
"""tools —— M2 工具总线（Function Calling，2026-10-20）。

组成：
  registry.py   工具注册表 + 分发器（风险三级 / 每日预算 / 入参校验 / 显式超时）
  llm.py        OpenAI 兼容 chat/completions 客户端（含 tools 载荷）
  fc.py         多回合 Function Calling 循环（单轮上限 8 次，串行分发）
  scheduler.py  提醒/定时任务（LLM 时间解析 + 原子领取 + 1s tick）
  builtins.py   首批工具注册（remind_* / queue_status / pc_overview）

依赖方向：tools 包**只依赖 ..config**（以及延迟导入的 ..queue.DATA_DIR），
不 import nonebot、不 import 主包、不 import ai_mode/pc——发送与宿主能力
全部经回调注入（register_builtin_tools(deps) / ReminderScheduler(on_fire)），
保证可裸单测、无循环 import。
"""
from .registry import (RISK_CONFIRM, RISK_OWNER, RISK_SAFE, Tool, ToolContext,
                       ToolRegistry, default_registry)
from .llm import LLMClient, LLMError
from .fc import FCRunner, get_runner
from .scheduler import (MAX_DELAY, MAX_PER_SCOPE, MIN_DELAY, ReminderError,
                        ReminderParser, ReminderScheduler, ReminderStore,
                        reminder_scheduler, reminder_store)
from .builtins import register_builtin_tools

__all__ = [
    "RISK_SAFE", "RISK_OWNER", "RISK_CONFIRM",
    "Tool", "ToolContext", "ToolRegistry", "default_registry",
    "LLMClient", "LLMError",
    "FCRunner", "get_runner",
    "ReminderStore", "ReminderParser", "ReminderScheduler", "ReminderError",
    "reminder_store", "reminder_scheduler",
    "MIN_DELAY", "MAX_DELAY", "MAX_PER_SCOPE",
    "register_builtin_tools",
]
