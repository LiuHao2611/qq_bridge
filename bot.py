"""qq_bridge 入口：NoneBot2 + OneBot V11 适配器。

⚠️ 最高优先级声明（不可违背，2026-09-16 封号事故后重写，取代 2026-09-04 版）：
   【核心原则】账号安全 >> 服务可用性。宁可机器人长期离线，绝不冒封号风险。
   1. 登录账号唯一来源 = .env 的 BOT_UIN；禁止硬编码任何默认/历史 QQ 号。
   2. 严禁硬编码 QQ 明文密码 / MD5；登录走扫码或用户自配凭据。
   3. 严禁自动重登：出现登录失败信号（Login Error / ErrCode 3 / 需扫码 /
      KickedOffline / 被封）立即熔断、只告警不重试。频繁重登是 QQ 风控
      最高危行为，即 2026-09-16 封号事故的直接根因。
   4. 自动重启守护（watchdog）默认关闭；启用时只做检测+告警，且设每日上限。
   5. 账号 3858741376 已于 2026-09-16 被封 → 永久禁止再登录该号。

拓扑（设计文档 §1）：qq_bridge 是 onebot **反向 WS 服务端**，
由 NapCat/LLOneBot 作为客户端主动连上来（配置见 data/config/onebot11.json（Docker 挂载卷）：
network.websocketClients[] → ws://127.0.0.1:8080/onebot/v11/ws）。
注：NapCat 4.x 只认 onebot11.json（登录后会另存 onebot11_<uin>.json），
旧版 onebot.json 的 network.websocketReverse 不会被加载。

本进程与主项目（后端 7788 / 前端 8099）完全独立：
  - 不 import 主项目任何模块，能力一律经 HTTP 调 7788（单向依赖铁律）；
  - 本进程崩溃/停止不影响主项目运行。
"""
import os

from dotenv import load_dotenv

# 必须在 nonebot.init() 之前手动加载 .env：nonebot 是在 init() 内部才读 .env，
# 若不在前面先 load_dotenv()，下面的 os.environ.get 会拿到 None。
load_dotenv()

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter  # noqa: E402

# 注意：**在 nonebot.load_plugins() 之前不要 import 插件包里的任何模块**
# （包括 src.plugins.qq_bridge.config）。一旦该包先作为普通模块进 sys.modules，
# nonebot 插件管理器就会报 "Module ... is not loaded as a plugin"。
# 日志初始化因此改由 config.get_logger() 在插件加载时按 LOG_LEVEL 惰性完成。

# 显式读取 .env 的 ONEBOT_V11_ACCESS_TOKEN 并传入，避免依赖 nonebot 隐式
# 环境变量名解析（默认字段名是 onebot_access_token / ONEBOT_ACCESS_TOKEN，
# 与本项目约定的 ONEBOT_V11_ACCESS_TOKEN 不一致会导致 token 永远读不到）。
ACCESS_TOKEN = os.environ.get("ONEBOT_V11_ACCESS_TOKEN", "")


def _as_bool(raw, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _official_bots() -> list:
    """构造 nonebot-adapter-qq 需要的机器人配置。

    注意：这里**刻意不 import 插件包的 config**——bot.py 在 nonebot.load_plugins()
    之前 import 插件子模块会让 nonebot 报 "not loaded as a plugin"（见文件头说明），
    因此配置一律直接读环境变量（与 ACCESS_TOKEN 的取法一致）。

    未启用或配置不全时返回空列表 → 不注册官方适配器 → NapCat 零影响。
    """
    import json

    if not _as_bool(os.environ.get("OFFICIAL_ENABLE")):
        return []
    appid = (os.environ.get("OFFICIAL_APPID") or "").strip()
    secret = (os.environ.get("OFFICIAL_SECRET") or "").strip()
    if not (appid and secret):
        print("[qq_bridge] OFFICIAL_ENABLE=true 但缺少 APPID/SECRET，跳过官方通道", flush=True)
        return []
    try:
        intent = json.loads(
            os.environ.get("OFFICIAL_INTENT")
            or '{"c2c_group_at_messages": true, "group_members": true}'
        )
    except Exception:  # noqa: BLE001 — 配置写错不能让进程起不来，回退默认 intent
        intent = {"c2c_group_at_messages": True, "group_members": True}
    return [{
        "id": appid,
        # 适配器 BotInfo 仍保留 token 字段（历史遗留）；1.7.x 实际用 appid+secret
        # 换 access token 鉴权，token 传空串即可。
        "token": "",
        "secret": secret,
        "intent": intent,
        "use_websocket": _as_bool(os.environ.get("OFFICIAL_USE_WEBSOCKET"), True),
    }]


OFFICIAL_BOTS = _official_bots()

_init_kwargs = {"onebot_access_token": ACCESS_TOKEN}
if OFFICIAL_BOTS:
    _init_kwargs["qq_bots"] = OFFICIAL_BOTS
    _init_kwargs["qq_is_sandbox"] = _as_bool(os.environ.get("OFFICIAL_SANDBOX"), True)

nonebot.init(**_init_kwargs)
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 官方适配器：只有真的配了机器人才注册（未配置时连 import 都不做，
# 保证 nonebot-adapter-qq 缺失也不会影响 NapCat 通道）。
if OFFICIAL_BOTS:
    try:
        from nonebot.adapters.qq import Adapter as QQAdapter  # noqa: PLC0415

        driver.register_adapter(QQAdapter)
        print("[qq_bridge] QQ official adapter registered (bots=%d)" % len(OFFICIAL_BOTS), flush=True)
    except Exception as e:  # noqa: BLE001 — 注册失败不能拖垮 NapCat
        print("[qq_bridge] QQ official adapter register failed: %s" % e, flush=True)

# ---------------------------------------------------------------------------
# 群聊总闸（2026-10-29）
# ---------------------------------------------------------------------------
# 背景：账号 2503891418 于 2026-10-28 被 QQ 安全中心标记为
#   「设备存在外挂或其他软件影响QQ正常使用」，次日 11:16 被强制下线。
#
# 群聊是风控暴露面最大的场景：一条回复几十上百人可见，任何人点一次举报都会
# 直接抬高账号风险等级（社区原话：「就算是大号随便来两个人举报也会很快风控」）。
# 私聊只有对话双方可见，暴露面小一个量级。所以先收窄到只响应私聊，观察风险。
#
# 为什么这道闸放在 bot.py 的 event_preprocessor，而不是各插件里逐个改：
#   · doupo / xiuxian 的 matcher 是 priority=5 + block=True，命中即阻断后续传播，
#     根本走不到 qq_bridge 的 dispatcher（priority=10）——
#     所以 dispatcher 里的 GROUP_WHITELIST 关不住这两个插件。
#   · event_preprocessor 在所有 matcher 之前执行，是唯一能一次性覆盖全部三个插件的位置。
#
# 这是显式的运行挡位，不是兜底：GROUP_ENABLED 默认 true（群聊正常）。
# 要关闭群聊 → .env 里设 GROUP_ENABLED=false 并重启 bridge；
# 要恢复 → 改回 true 再重启，代码零改动。
# 开关的唯一事实来源在 src/group_switch.py：插件侧（xiuxian 广播）也读它，
# 否则各处各写一个 os.environ.get，早晚漂移成「入站关了、出站还开着」。
from src.group_switch import group_enabled  # noqa: E402

GROUP_ENABLED = group_enabled()

if not GROUP_ENABLED:
    from nonebot.adapters import Event
    from nonebot.message import IgnoredException, event_preprocessor

    _grp_dropped = 0

    # 参数只能取 EVENT_PCS_PARAMS 允许的类型（DependParam / BotParam / EventParam /
    # StateParam / DefaultParam）。**多写一个 **kwargs 就会在启动时抛
    # ValueError: Unknown parameter kwargs**，且是在 nonebot.init() 之后的 import 期，
    # 表现为进程直接退出、端口不监听（2026-10-29 实测踩过）。
    # 这里只声明 event，够用且不留未使用参数。
    @event_preprocessor
    async def _block_group_messages(event: Event):
        """GROUP_ENABLED=false 时，群消息在进入任何 matcher 之前被丢弃。

        判定用 group_id 而非 message_type：OneBot V11 与官方 QQ 适配器对
        message_type 的取值不完全一致，而「带 group_id」在两边都只有群聊才成立。
        心跳/生命周期等元事件没有 group_id，会原样放行。

        丢弃是**完全静默**的：连「请在群聊中使用」这类提示都不回。
        只回一句免责话术也等于告诉群里「这个号是机器人在被限流」。

        抛出点由 nonebot.message._apply_event_preprocessors 捕获（它 catch 了
        IgnoredException），因此 handle_event 直接 return，三个插件一个都收不到。
        """
        global _grp_dropped
        if not getattr(event, "group_id", None):
            return
        _grp_dropped += 1
        # 只在首条和每 200 条打一次，避免群多时刷爆 bridge.log
        if _grp_dropped == 1 or _grp_dropped % 200 == 0:
            print(
                "[qq_bridge] GROUP_ENABLED=false 已忽略群消息 %d 条（%s）"
                % (_grp_dropped, getattr(event, "get_event_name", lambda: "?")()),
                flush=True,
            )
        raise IgnoredException("group_disabled")

    # 上面那道只管进，不管出。2026-10-29 实测：入站被拦死了，
    # 机器人照样在 13:53 / 14:33 / 15:26 往群里发主动广播（xiuxian 的世界事件循环
    # 直接调 bots[0].send_group_msg，根本不经过事件链路）。
    # 出站闸挂在 Bot.call_api 上，覆盖所有走 OneBot API 的群发送。
    from src.group_switch import install_outbound_guard

    install_outbound_guard()

    print(
        "[qq_bridge] 群聊总闸已开启（GROUP_ENABLED=false）：群消息一律静默丢弃，仅响应私聊",
        flush=True,
    )

# 加载插件目录（相对运行目录 qq_bridge/）
# 注：load_plugins 只扫一层，src/plugins/qq_bridge 作为唯一插件入口，
# 其下的 connector/dispatcher/... 是内部模块，不会被重复注册为插件。
nonebot.load_plugins("src/plugins")

# 插件把 matcher 注册收敛在 register() 内，必须在 nonebot.init() 之后再调用，
# 否则顶层 get_driver() 会因尚未初始化而抛 ValueError。
from src.plugins.qq_bridge import register  # noqa: E402

register()

if __name__ == "__main__":
    nonebot.run()
