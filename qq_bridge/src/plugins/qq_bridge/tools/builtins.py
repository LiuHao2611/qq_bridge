# -*- coding: utf-8 -*-
"""tools.builtins —— M2 首批 Function Calling 工具注册（2026-10-20）。

首批 curated 子集（dev_plan §〇.2 安全总则：权限分层）：
  safe（所有会话可用，群聊/私聊）：
    remind_create / remind_list / remind_cancel  —— 提醒三件套（本模块直连）
    queue_status                                  —— 生图任务队列状态（deps 注入）
  owner（仅主人私聊）：
    pc_overview                                   —— 宿主机概览（deps 注入）
**confirm 级（kill/电源/删文件/剪贴板写）第一轮不注册**：继续走 /pc 命令通道
的既有确认流。这是发布范围决策，不是功能降级——两条通道能力全集不变。

依赖注入（deps）：pc/queue 等宿主能力由调用方（__init__.py register）以
回调形式注入，tools 包**不 import pc / queue / 主包**——保持可裸单测，
也防循环 import。deps 缺某个回调 → 对应工具不注册（未接线 ≠ 假注册）。

工具描述是「模型选不选它」的唯一依据：写清楚适用场景与返回内容，
不写实现细节。
"""
from __future__ import annotations

from typing import Awaitable, Callable

from ..config import get_logger, jlog
from .registry import RISK_OWNER, RISK_SAFE, Tool, ToolRegistry
from .scheduler import make_fc_handlers

log = get_logger()

#: deps 回调签名约定：
#   pc_overview: async () -> str
#   queue_status: async (ctx) -> str
Deps = dict


def _tool(name: str, desc: str, props: dict, required: list,
          handler: Callable, risk: str = RISK_SAFE) -> Tool:
    return Tool(
        name=name,
        description=desc,
        parameters={"type": "object", "properties": props,
                    "required": required},
        handler=handler,
        risk=risk,
    )


def register_builtin_tools(registry: ToolRegistry, deps: Deps | None = None) -> list[str]:
    """向 registry 注册 M2 首批工具，返回成功注册的工具名列表。

    幂等：重复调用 = 覆盖注册（热更新语义，供测试）。
    """
    deps = deps or {}
    registered: list[str] = []

    # ---- 提醒三件套（safe，直连 reminder_store）----
    fc_handlers = make_fc_handlers()
    registry.register(_tool(
        "remind_create",
        "创建一条提醒/定时任务。当用户说「X 分钟后/明早/下周一 提醒我……」，"
        "「定个闹钟」「到点叫我」这类话时使用。时间由你算成 Unix 时间戳"
        "（系统提示里注入了当前时间）。",
        {
            "text": {"type": "string", "description": "提醒内容，如「喝水」「开会」"},
            "fire_at": {"type": "integer",
                        "description": "触发时刻的 Unix 时间戳（秒），必须是当前时间"
                                       "+10 秒到 +7 天之间"},
            "repeat": {"type": "string", "enum": ["none", "daily"],
                       "description": "none=只响一次；daily=每天同一时刻重复"},
        },
        ["text", "fire_at"],
        fc_handlers["remind_create"],
    ))
    registered.append("remind_create")

    registry.register(_tool(
        "remind_list",
        "查看当前会话（这个群/这个私聊）待触发的提醒列表。当用户问「我有哪些"
        "提醒」「我的闹钟」「看看提醒」时使用。",
        {},
        [],
        fc_handlers["remind_list"],
    ))
    registered.append("remind_list")

    registry.register(_tool(
        "remind_cancel",
        "取消一条提醒。用户说「取消提醒」「不用提醒了」「把那个闹钟关了」时使用；"
        "优先传编号 id（用户可能说编号或序号），没有 id 时传 list 里的序号 index。",
        {
            "id": {"type": "string", "description": "提醒编号（remind_list 返回的）"},
            "index": {"type": "integer", "description": "提醒列表里的序号（第几条）"},
        },
        [],
        fc_handlers["remind_cancel"],
    ))
    registered.append("remind_cancel")

    # ---- 生图任务队列状态（safe，deps 注入）----
    if callable(deps.get("queue_status")):
        async def queue_status(args: dict, ctx) -> str:
            return await deps["queue_status"](ctx)

        registry.register(_tool(
            "queue_status",
            "查看生图任务队列状态：当前排队数量、自己的任务排第几、预计还要等多久。"
            "用户问「我的图呢」「还要等多久」「队列里几个人」时使用。",
            {},
            [],
            queue_status,
        ))
        registered.append("queue_status")

    # ---- 宿主机概览（owner：仅主人私聊；群聊调用会被权限层拒绝）----
    if callable(deps.get("pc_overview")):
        async def pc_overview(args: dict, ctx) -> str:
            return await deps["pc_overview"]()

        registry.register(_tool(
            "pc_overview",
            "查看宿主机（运行机器人的那台电脑）概况：CPU/内存/磁盘/网络/开机时长。"
            "仅主人私聊可用。用户问「电脑怎么样」「机器卡不卡」「看看配置」时使用。",
            {},
            [],
            pc_overview,
            risk=RISK_OWNER,
        ))
        registered.append("pc_overview")

    log.info(jlog("builtin_tools_registered", tools=registered,
                  count=len(registered)))
    return registered
