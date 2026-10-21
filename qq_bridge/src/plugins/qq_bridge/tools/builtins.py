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
#   download_add: async (url, ctx) -> str
#   download_list: async (ctx) -> str
#   download_cancel: async (ref, ctx) -> str
#   ops_git_status: async () -> str
#   ops_frp_status: async () -> str
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

    # ---- 下载媒体（M7，owner：占主人带宽/磁盘，限主人）----
    if callable(deps.get("download_add")):
        async def download_add(args: dict, ctx) -> str:
            return await deps["download_add"](args.get("url", ""), ctx)

        registry.register(_tool(
            "download_add",
            "把链接加入宿主机下载队列。支持 http/https/ftp 直链、magnet: 磁力"
            "（自动智能选最大文件）、本地 .torrent 路径、B站/抖音/YouTube 等"
            "视频页链接（yt-dlp 下载）。仅主人可用——下载会占用主人机器的带宽"
            "与磁盘。用户说「下载这个」「帮我下 XXX」时使用。",
            {
                "url": {"type": "string",
                        "description": "下载链接（http/ftp/magnet/.torrent 路径/视频页 URL）"},
            },
            ["url"],
            download_add,
            risk=RISK_OWNER,
        ))
        registered.append("download_add")

    if callable(deps.get("download_list")):
        async def download_list(args: dict, ctx) -> str:
            return await deps["download_list"](ctx)

        registry.register(_tool(
            "download_list",
            "查看当前会话的下载任务列表（进行中与最近完成的）。用户问「下的"
            "怎么样了」「下载列表」「我的下载」时使用。",
            {},
            [],
            download_list,
        ))
        registered.append("download_list")

    if callable(deps.get("download_cancel")):
        async def download_cancel(args: dict, ctx) -> str:
            return await deps["download_cancel"](str(args.get("ref", "")), ctx)

        registry.register(_tool(
            "download_cancel",
            "取消一个下载任务（传列表里的编号或序号）。用户说「不下了」「取消"
            "下载」时使用。仅移除任务，不删已下好的文件。",
            {
                "ref": {"type": "string",
                        "description": "任务编号（download_list 返回的）或序号"},
            },
            ["ref"],
            download_cancel,
            risk=RISK_OWNER,
        ))
        registered.append("download_cancel")

    # ---- 运维面（M9，owner：暴露宿主机/内网信息，仅主人）----
    if callable(deps.get("ops_git_status")):
        async def ops_git_status(args: dict, ctx) -> str:
            return await deps["ops_git_status"]()

        registry.register(_tool(
            "ops_git_status",
            "查看开发机 git 仓库状态：分支、未提交改动、未推送提交、最近提交。"
            "仅主人可用（暴露宿主机工作区）。用户问「仓库啥情况」「git 状态」"
            "「我改了多少文件」时使用。",
            {},
            [],
            ops_git_status,
            risk=RISK_OWNER,
        ))
        registered.append("ops_git_status")

    if callable(deps.get("ops_frp_status")):
        async def ops_frp_status(args: dict, ctx) -> str:
            return await deps["ops_frp_status"]()

        registry.register(_tool(
            "ops_frp_status",
            "查看 frp 内网穿透隧道在线状态（代理总数/在线/离线）。仅主人可用。"
            "用户问「穿透通不通」「隧道在线吗」「frp 状态」时使用。",
            {},
            [],
            ops_frp_status,
            risk=RISK_OWNER,
        ))
        registered.append("ops_frp_status")

    # ---- 智能家居（M8 2026-10-21：AI 对话自然语言控设备闭环）----
    # 安全模型与 /ha 命令族同口径：白名单内设备可控（HAClient._check 双保险），
    # 高敏设备（门锁/摄像头）的 owner 判定在 deps 回调内做（__init__.py 接线），
    # 未接线（deps 缺）→ 不注册（未接线 ≠ 假注册）。
    if callable(deps.get("ha_control")):
        async def ha_control(args: dict, ctx) -> str:
            return await deps["ha_control"](
                args.get("entity_id", ""), args.get("action", "turn_on"), ctx)

        registry.register(_tool(
            "ha_control",
            "控制智能家居设备开关。当用户说「把客厅灯打开」「关掉风扇」「切换开关」"
            "这类话时使用。entity_id 形如 light.living_room；只能控制已授权白名单"
            "内的设备（门锁/摄像头等高敏设备仅主人）。",
            {
                "entity_id": {"type": "string",
                              "description": "设备 ID，如 light.living_room"},
                "action": {"type": "string", "enum": ["turn_on", "turn_off", "toggle"],
                           "description": "turn_on=打开；turn_off=关闭；toggle=切换"},
            },
            ["entity_id"],
            ha_control,
        ))
        registered.append("ha_control")

    if callable(deps.get("ha_scene")):
        async def ha_scene(args: dict, ctx) -> str:
            return await deps["ha_scene"](args.get("name", ""), ctx)

        registry.register(_tool(
            "ha_scene",
            "执行智能家居场景脚本（如「洗澡模式」「睡眠模式」）。当用户说「开洗澡模式」"
            "「我要睡了」这类话时使用。场景名用配置里映射的中文名。",
            {
                "name": {"type": "string",
                         "description": "场景名（中文口语，如「洗澡模式」）"},
            },
            ["name"],
            ha_scene,
        ))
        registered.append("ha_scene")

    if callable(deps.get("ha_state")):
        async def ha_state(args: dict, ctx) -> str:
            return await deps["ha_state"](args.get("entity_id", ""), ctx)

        registry.register(_tool(
            "ha_state",
            "查询智能家居设备当前状态（开/关/温度/亮度等）。当用户问「灯关了吗」"
            "「客厅现在多少度」时使用。只读，不改变设备。",
            {
                "entity_id": {"type": "string",
                              "description": "设备 ID，如 sensor.living_temp"},
            },
            ["entity_id"],
            ha_state,
        ))
        registered.append("ha_state")

    log.info(jlog("builtin_tools_registered", tools=registered,
                  count=len(registered)))
    return registered
