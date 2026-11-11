# 游戏工具箱插件：二游攻略（角色/配队/养成/副本，覆盖原神·星铁·绝区零·鸣潮·异环）
# + 群聊小游戏。
#
# 设计要点：
#   * **攻略发富卡片**：pic/guide 模板（头像 + 【分区标题】高亮 + 长正文自适应高度）；
#     角色图鉴头像来自 wiki 页面 og:image；渲染失败逐级回落 guide→text→纯文本。
#   * **支持外号**：「雷神配队」「鲨鱼妹养成」→ data.ALIAS 归一成正式名再查。
#   * 配队内容含成员/思路/**手法循环/装备速览**（data.TEAM_TIPS），不再两句话打发。
#   * 触发：# 前缀或直接说口令；免 # 时只认命令词开头、参数 >30 字放行（防抢生图）。
#   * matcher 不 block、异常自吞：绝不拖垮机器人主流程。
import asyncio
import json
import os
import re
import time

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger
# 文件日志：nonebot.log.logger 不写 qq_bridge.log，关键路径改用项目 logger
# （jlog 带 [qq_bridge] 前缀，与主流程一致，落盘可排查）
try:
    from ..config import get_logger as _get_logger, jlog as _jlog
    _flog = _get_logger()
except Exception:  # noqa: BLE001 — 加载失败退化为 nonebot logger
    _flog = logger
    def _jlog(event: str, **fields) -> str:
        _f = " ".join("%s=%s" % (k, v) for k, v in fields.items())
        return "event=" + event + (" " + _f if _f else "")

from . import atlas, data, games, riven, warframe, wiki
from .. import planstore, queue  # 生图计划（草稿）查看（2026-09-28 新增）
# AI 模式（2026-10-19）：本模块是独立 matcher，与主流程并行收消息，
# 必须自己判断「本会话是否开着大模型陪聊」——否则群里一句话被双重回复
# （线上实测：AI 陪聊 + "hi" 误触 Rhino 卡）。
from ..ai_mode import ai_mode_store, event_scope

_matcher = None
PREFIXES = ("#", "＃", "/")

# 免前缀也可直接触发的明确命令词（首词精确匹配，避免「我计划…」误触发）。
# 与 dispatch 中「生图计划」分支的词表保持一致（2026-09-28 修复：@机器人 计划 落空）。
_PLAN_CMD_WORDS = ("计划", "我的计划", "草稿计划", "plan", "plans")
# 计划单独下发（真实提交生图，写入队列）：任何人可用，按请求者身份入队。
_DISPATCH_CMD_WORDS = ("下发计划", "下发", "runplan", "dispatch")

# 「命令词紧跟参数、无空格」的粘连写法（例：计划1 / 下发计划1 / plan1）。
# 仅当尾随部分像「序号(纯数字)」或「计划ID(≥6 位 hex)」才拆，避免「计划去玩」误触发。
_PLAN_ARG_RE = re.compile(r"^(\d+|[0-9a-fA-F]{6,})$")

# 紫卡分析「排队中」占位消息（"已加入分析队列，里面正噼里啪啦算数值，稍等！"）的
# message_id，按会话(scope) 累积。分析结果（成功/失败）发出后统一自动撤回，
# 保持聊天记录整洁（2026-11-04 新增）。只存机器人自己发出的有效 id，撤回 best-effort。
_riven_queue_ids: dict[str, list[int]] = {}


def _riven_scope_key(event: MessageEvent) -> str:
    """紫卡排队消息按「群+用户」维度归并，避免跨会话误撤回。"""
    gid = int(getattr(event, "group_id", 0) or 0)
    uid = int(getattr(event, "user_id", 0) or 0)
    return "g%d_u%d" % (gid, uid) if gid else "u%d" % uid


async def _riven_recall_queue(bot: Bot, event: MessageEvent) -> None:
    """撤回当前会话下所有未清理的紫卡排队占位消息（best-effort）。

    分析结果（成功或失败）发出后立即调用，把"已加入分析队列…稍等"这类临时提示
    清掉，避免聊天记录里留一堆过期占位。仅撤回机器人自己发的消息；撤回失败
    （如超过 QQ 2 分钟撤回时限、跨通道不支持）只记日志不阻断。
    """
    key = _riven_scope_key(event)
    ids = _riven_queue_ids.pop(key, [])
    if not ids:
        return
    for mid in ids:
        try:
            # 注意：本机 Bot.call_api 被 group_switch 出站闸包装为 (self, api, **data)，
            # 参数必须走关键字（不能传第二个位置 dict），否则 TypeError。
            await bot.call_api("delete_msg", message_id=mid)
            _flog.info(_jlog("gt_riven_queue_recalled", message_id=mid, scope=key))
        except Exception as _e:  # noqa: BLE001 — 撤回失败绝不拖垮出卡流程
            _flog.warning(_jlog("gt_riven_queue_recall_failed", message_id=mid,
                                scope=key, err=type(_e).__name__, detail=str(_e)[:120]))


def _split_glued_cmd(token: str, words):
    """命令词紧跟像「序号(纯数字)」或「计划ID(≥6 位 hex)」的尾随串时拆出 (cmd, arg)；否则 None。

    长词优先（"下发计划" 先于 "下发"），避免前缀词抢拆；精确命中亦返回 (cmd, "")。
    """
    low = token.lower()
    if low in words:  # 精确命中（含纯命令词无参）
        return (low, "")
    for w in sorted(words, key=len, reverse=True):
        if low.startswith(w) and len(low) > len(w):
            _a = token[len(w):].strip()
            if _PLAN_ARG_RE.match(_a):
                return (low[:len(w)], _a)
    return None


def _split_plan_if_glued(token: str):
    """计划1 / plan1 / 计划a1b2c3 → ('计划','1') / ('plan','1') / ('计划','a1b2c3')；否则 None。"""
    return _split_glued_cmd(token, _PLAN_CMD_WORDS)

#: 图片正文上限（guide 模板自身限 46 行，这里防超长输入）
PIC_BODY_MAX = 1800
#: 头像下载大小上限
AVATAR_MAX_BYTES = 5 * 1024 * 1024

HELP = """🛠 游戏工具箱（# 开头，或直接说口令）
覆盖：原神 / 星铁 / 绝区零 / 鸣潮 / 异环（攻略均为图片卡片）
+ 星际战甲 · 国际服（世界状态 / 市价，数据实时）

【二游攻略】
角色 <名> [游戏] —— 角色图鉴（wiki 实时抓取，带头像）
配队 <角色/外号> —— 配队推荐（成员/思路/手法/装备）
养成 <角色/定位> —— 毕业养成路线
副本 [游戏] —— 当期渊月螺旋/混沌回忆/危局强袭/逆境深塔（实时抓取）
活动 [游戏] —— 当期活动一览（实时抓取）
游戏可写：原神 / 星铁 / 绝区零 / 鸣潮 / 异环
外号支持：雷神、草神、岩王爷、鲨鱼妹、阮梅、将军……

【星际战甲 · 国际服】（#wf 或直接说口令）
#wf / 星际战甲 —— 帮助
#突击 / #wf 突击 —— 今日突击
#奸商 / #虚空商人 —— 虚空商人 Baro（来了没/地点/剩余）
#特惠 / #每日特惠 —— 每日特惠（-x%）
#裂隙 / #wf 裂隙 —— 虚空裂缝（按纪元分组）
#入侵 / #wf 入侵 —— 入侵进度
#仲裁 / #wf 仲裁 —— 当前仲裁
#电波 / #wf 电波 —— 电波赛季与本周挑战
#平原 / #wf 平原 —— 夜灵平原昼夜
#价格 <物品> —— Warframe.Market 市价（例：#价格 充沛）
#紫卡 <武器> —— 紫卡市价（例：#紫卡 绝路）
#紫卡全面 [武器] —— 655×1920 完整分析卡（数值/融合/评分/市价/击杀效率配卡；发截图走真实 OCR）
#赤毒 <武器> / #信条 <武器> —— 赤毒/信条武器市价
#wiki <词> —— 灰机 Wiki 搜索
#翻译 <词> —— 常用中英对照

【小游戏】
#猜数字 —— 开一局 1-100，用「#猜 数字」作答
#抽签 / #运势 / #仓鼠运势 —— 仓鼠今日运势（每天固定）
#黄历 / #黄道吉日 —— 仓鼠老黄历（宜忌+紫卡+任务，实时）
#模拟开卡 / #开卡 —— 模拟开一张紫卡（纯娱乐）
#紫卡趋势 <武器> —— 本地采样中位价走势（非成交价，首次只有 1 个点）
#词条价差 <武器> —— 正面词条同分位溢价排行（即时 WFM 挂牌）
#紫卡融合 [武器] —— 紫卡融合机制 + 融合费用表（Wiki 公式）+ 真实中位价（武器可空）
#接龙 <成语> —— 成语接龙
#24点 —— 出题，用「#24 表达式」作答
#答题 —— 二游知识问答，用「#答 A」作答
#结束 —— 结束当前游戏

【生图计划（草稿）】
#计划 —— 列出全部草稿计划 + 状态摘要（定时 / 最近执行归类）
#计划 <序号或计划ID> —— 某计划明细（定时 / 执行进度 / 前 8 条条目状态）
#明细 <序号或计划ID> —— 某计划最近批次的全部条目状态（深度下钻）
下发计划 <序号或计划ID> —— 把该计划的全部条目提交去生图（真实入队，占用队列+显卡，任何人可用）
（草稿在 Web 面板「计划」Tab 管理；#计划/#明细 仅查看，不下发不改动）

#帮助 —— 看这条说明"""

#: 需要发富卡片的"攻略类"命令 → 卡片标题
GUIDE_TITLES = {
    "配队": "配队推荐", "队伍": "配队推荐", "组队": "配队推荐",
    "养成": "养成路线", "培养": "养成路线", "毕业": "养成路线",
    "副本": "当期副本", "深渊": "当期副本", "混沌": "当期副本",
    "混沌回忆": "当期副本", "危局": "当期副本", "当期": "当期副本",
    "活动": "当期活动", "活动一览": "当期活动", "新活动": "当期活动",
    "帮助": "游戏工具箱", "菜单": "游戏工具箱", "help": "游戏工具箱",
    "功能": "游戏工具箱", "工具": "游戏工具箱",
    # ---- 星际战甲（国际服，2026-09-16 新增）----
    "wf": "星际战甲", "WF": "星际战甲", "星际战甲": "星际战甲", "战甲": "星际战甲",
    "突击": "星际战甲·突击", "奸商": "星际战甲·虚空商人", "虚空商人": "星际战甲·虚空商人",
    "仲裁": "星际战甲·仲裁", "特惠": "星际战甲·每日特惠", "每日特惠": "星际战甲·每日特惠",
    "裂隙": "星际战甲·虚空裂隙", "普通裂隙": "星际战甲·普通虚空裂隙",
    "钢铁裂隙": "星际战甲·钢铁虚空裂隙", "入侵": "星际战甲·入侵", "电波": "星际战甲·电波",
    "平原": "星际战甲·平原", "钢铁": "星际战甲·钢铁之路",
    "价格": "星际战甲·市价", "市价": "星际战甲·市价", "紫卡": "星际战甲·紫卡",
    "紫卡分析": "星际战甲·紫卡分析", "分析": "星际战甲·紫卡分析",
    "倾向": "星际战甲·紫卡倾向",
    "紫卡趋势": "星际战甲·紫卡趋势", "词条价差": "星际战甲·紫卡词条价差",
    "紫卡融合": "星际战甲·紫卡融合",
    "赤毒": "星际战甲·市价", "信条": "星际战甲·市价",
    "wiki": "星际战甲·Wiki", "翻译": "星际战甲·翻译",
    "赏金": "星际战甲·赏金", "钢铁赏金": "星际战甲·钢铁之路赏金",
    "钢铁之路": "星际战甲·钢铁之路赏金", "钢路": "星际战甲·钢铁之路赏金",
    "灵化": "星际战甲·灵化轮换", "科研": "星际战甲·科研任务",
    "九重天": "星际战甲·九重天",
    "周本": "星际战甲·周期任务", "轮换": "星际战甲·武器轮换",
    "执刑官": "星际战甲·执刑官猎杀", "执政官": "星际战甲·执刑官猎杀",
    "执行官": "星际战甲·执刑官猎杀",
    "战甲": "星际战甲·战甲信息", "frame": "星际战甲·战甲信息", "warframe": "星际战甲·战甲信息",
    "mod": "星际战甲·MOD", "卡": "星际战甲·MOD", "卡片": "星际战甲·MOD",
    "赋能": "星际战甲·赋能", "arcane": "星际战甲·赋能",
    "仓鼠运势": "星际战甲·仓鼠运势",
    "黄历": "星际战甲·仓鼠黄历", "仓鼠黄历": "星际战甲·仓鼠黄历", "黄道吉日": "星际战甲·仓鼠黄历",
    "模拟开卡": "星际战甲·模拟开卡", "开卡": "星际战甲·模拟开卡",
    "金垃圾": "星际战甲·金垃圾",
    "wm": "星际战甲·市价", "WM": "星际战甲·市价", "市场": "星际战甲·市价",
    # ---- 生图计划（草稿）查看（2026-09-28 卡片化：复用 guide 卡片渲染，不再纯文字）----
    "计划": "生图计划", "我的计划": "生图计划", "草稿计划": "生图计划",
    "plan": "生图计划", "plans": "生图计划",
    # ---- 生图计划明细详情（全部条目下钻，2026-09-28 新增 #明细）----
    "明细": "生图计划明细", "明细计划": "生图计划明细", "plandetail": "生图计划明细",
}

# 免 # 的自然语言口令（按长度降序匹配，避免"猜"吃掉"猜数字"）
NATURAL_CMDS = (
    "查角色", "角色", "图鉴", "配队", "队伍", "养成", "培养", "毕业",
    "副本", "深渊", "混沌", "危局", "活动", "接龙", "猜数字", "猜",
    "抽签", "运势", "24点", "24", "答题", "答", "结束", "帮助", "菜单", "功能",
    # ---- 星际战甲（2026-09-16 新增）----
    "突击", "奸商", "虚空商人", "仲裁", "特惠", "每日特惠", "裂隙", "入侵",
    "电波", "平原", "钢铁", "价格", "市价", "紫卡", "紫卡分析", "词条推荐", "洗卡推荐", "紫卡推荐", "倾向", "赤毒", "信条", "终幕", "coda", "tenet", "wiki", "翻译",
    "赏金", "钢铁赏金", "钢铁之路", "钢路", "灵化", "科研", "普通裂隙", "钢铁裂隙", "九重天", "周本", "轮换", "警报",
    "mod", "卡", "卡片", "赋能", "arcane", "配卡", "build", "配置",
    "仓鼠运势", "黄历", "仓鼠黄历", "黄道吉日", "模拟开卡", "开卡", "金垃圾",
    "wm", "WM", "市场",
    "wf", "WF", "星际战甲", "战甲", "frame", "warframe",
    "仲裁表",
    # ---- 裂隙任务订阅（2026-11-05）：长词在前，_match_natural 按长度降序匹配 ----
    "任务订阅列表", "取消订阅", "订阅列表", "我的订阅", "查看订阅", "订阅任务",
    "关注任务", "蹲任务", "取消关注", "退订", "订阅",
    # ---- 执刑官猎杀（2026-11-05）----
    "执政官猎杀", "执刑官猎杀", "执行官猎杀", "执政官", "执刑官", "执行官",
    "猎杀", "archon", "Archon",
    # ---- 紫卡趋势 / 词条价差（2026-11-05 P2，均需武器参数）----
    "紫卡趋势", "紫卡价差", "词条价差", "紫卡词条价差", "riven趋势",
    # ---- 紫卡融合（2026-11-05 P1，武器参数可选）----
    "紫卡融合", "riven融合",
    # ---- 紫卡全面分析（2026-11-06，像素级复刻 Rabbit 完整分析卡）----
    "紫卡全面", "紫卡全面分析", "全面分析", "紫卡完整", "完整分析",
)
ZERO_ARG_OK = {
    "抽签", "运势", "猜数字", "24点", "答题",
    "副本", "深渊", "混沌", "危局", "活动", "结束", "帮助", "菜单", "功能",
    "突击", "奸商", "虚空商人", "仲裁", "特惠", "每日特惠", "裂隙", "入侵",
    "电波", "平原", "钢铁", "wiki", "翻译", "wf", "WF", "星际战甲", "战甲",
    "配卡", "build", "配置", "词条推荐", "洗卡推荐", "紫卡推荐",
    "赏金", "钢铁赏金", "钢铁之路", "钢路", "灵化", "科研", "普通裂隙", "钢铁裂隙", "九重天", "周本", "轮换", "警报",
    # 信条 / 终幕是两个零参数出卡口令（带武器名才走市价）；2026-11-06 用户
    # 反馈「我发信条，没有回我」—— 词表有「信条」却没进零参数白名单，
    # 「终幕」连词表都没登记，两者都在 _handle_safe 里被静默 return。
    "信条", "终幕", "coda", "tenet",
    "仓鼠运势", "黄历", "仓鼠黄历", "黄道吉日", "模拟开卡", "开卡", "金垃圾",
    "仲裁表",
    "任务订阅列表", "订阅列表", "我的订阅", "查看订阅", "订阅任务", "关注任务",
    "蹲任务",
    "执政官猎杀", "执刑官猎杀", "执行官猎杀", "执政官", "执刑官", "执行官",
    "猎杀", "archon", "Archon",
    "紫卡融合", "riven融合",
    "紫卡全面", "紫卡全面分析", "全面分析",
}
NATURAL_ARG_MAX = 30

_GAME_ALIAS = {
    "原神": "ys", "genshin": "ys", "ys": "ys",
    "星穹铁道": "sr", "崩铁": "sr", "星铁": "sr", "sr": "sr",
    "绝区零": "zzz", "zzz": "zzz", "zenless": "zzz",
    "鸣潮": "ww", "wuwa": "ww", "wuthering": "ww", "ww": "ww",
    "异环": "yh", "nte": "yh", "yh": "yh",
}


async def _handle_safe(bot: Bot, event: MessageEvent) -> None:
    """_handle 安全包装：任何异常落文件日志 + 降级纯文本，绝不静默。

    2026-09-16 线上「发了没反应」第二根因：_handle 直接挂 matcher，
    NameError(os 未 import) 被 nonebot 吞掉，不落 qq_bridge.log，用户零反馈。
    """
    try:
        await _handle(bot, event)
    except Exception as e:  # noqa: BLE001 — 兜底：绝不静默失败
        _flog.error(_jlog("gt_handler_crash", err=type(e).__name__, detail=str(e)[:200]))
        try:
            await asyncio.wait_for(bot.send(event, "这个功能刚才出错了（已记录日志），稍后再试～"),
                                  timeout=8.0)
        except Exception:  # noqa: BLE001
            pass


def register() -> None:
    global _matcher
    if _matcher is not None:
        return
    # 启动预热：warframe 数据表/翻译表首次同步加载可能阻塞事件循环几秒，
    # 若不预热，重启后第一条战甲消息的渲染会被拖慢甚至超时（2026-09-16）
    try:
        warframe._load_cards()
        warframe._load_translate_data()
        warframe._load_augments()  # 集团卡映射首次构建 ~6s，必须预热
    except Exception:  # noqa: BLE001 — 预热失败不影响启动
        pass
    _matcher = on_message(priority=5, block=False)
    _matcher.handle()(_handle_safe)
    logger.info("game_tools registered (五款二游攻略 + 星际战甲国际服 + 小游戏)")

    # QQ 官方通道 warframe 适配（2026-11-10，只改副本，原件只读）：
    # 官方群事件是 nonebot-adapter-qq 类型，上面的 OneBot _matcher 注入不到；
    # 这里额外注册官方专用 matcher（priority=5, block=True），用适配器复用同一套
    # _handle_safe 命令分发/渲染/降级。注册失败只记日志，NapCat 链路零影响。
    try:
        from .official_bridge import register as _official_bridge_register
        _official_bridge_register()
    except Exception as e:  # noqa: BLE001 — 官方适配失败绝不能拖垮 NapCat game_tools
        logger.warning(f"gt_official_bridge_register_failed: {type(e).__name__}: {e}")

    # QQ 官方消息按钮点击回调（2026-11-11，只改副本）：用户点卡片上的快捷命令
    # 按钮 → INTERACTION_CREATE → 复用同一套 warframe 分发。注册失败只记日志。
    try:
        from ..channel.qq_interaction import register as _qq_interaction_register
        _qq_interaction_register()
    except Exception as e:  # noqa: BLE001 — 按钮回调失败绝不能拖垮 NapCat game_tools
        logger.warning(f"gt_qq_interaction_register_failed: {type(e).__name__}: {e}")


def _sk(event: MessageEvent) -> str:
    mt = str(getattr(event, "message_type", "") or "private")
    pid = int(getattr(event, "group_id", 0) or getattr(event, "user_id", 0) or 0)
    return f"{mt}_{pid}"


#: 紫卡截图分析「先文字预约、再发图」等待窗口（秒）。
_RIVEN_WAIT_TTL = 90.0
#: {(message_type_chat_id, user_id): timestamp}
_riven_wait: dict[tuple[str, int], float] = {}
# 「紫卡全面」专用预约：NapCat 常把「文字 + 图片」拆成两条独立消息，
# 纯图那条没有任何文字关键字，靠同条消息的 _full_kw 永远命中不了。
_riven_full_wait: dict[tuple[str, int], float] = {}
_FULL_WAIT_WORDS = ("紫卡全面", "紫卡全面分析", "全面分析", "紫卡完整",
                    "完整分析", "紫卡全析")


def _riven_wait_key(event: MessageEvent) -> tuple[str, int]:
    """预约状态键：按聊天上下文 + 用户隔离。"""
    return (_sk(event), int(getattr(event, "user_id", 0) or 0))


def _set_riven_wait(event: MessageEvent) -> None:
    """登记用户正在等待发紫卡截图。"""
    _riven_wait[_riven_wait_key(event)] = time.time()


def _pop_riven_wait(event: MessageEvent) -> bool:
    """取出并校验预约：在 TTL 内返回 True 并清除状态；过期/无记录返回 False。"""
    key = _riven_wait_key(event)
    ts = _riven_wait.pop(key, None)
    if ts is None:
        return False
    if time.time() - ts > _RIVEN_WAIT_TTL:
        return False
    return True


def _set_riven_full_wait(event: MessageEvent) -> None:
    """登记「紫卡全面」预约：下一张纯图走真实 OCR 全量分析卡。"""
    _riven_full_wait[_riven_wait_key(event)] = time.time()


def _pop_riven_full_wait(event: MessageEvent) -> bool:
    """取出并校验全面分析预约（TTL 同 _RIVEN_WAIT_TTL），命中后清除。"""
    key = _riven_wait_key(event)
    ts = _riven_full_wait.pop(key, None)
    if ts is None or time.time() - ts > _RIVEN_WAIT_TTL:
        return False
    return True


def _split_game(arg: str) -> tuple[str, str]:
    for kw, g in _GAME_ALIAS.items():
        if kw in arg:
            return g, arg.replace(kw, "").strip()
    return "", arg.strip()


def _event_segments(event: MessageEvent) -> list:
    """消息段序列。必须优先 original_message：nonebot 在 handler 执行前会把
    event.message 里的 at 段改写成空文本段（dispatcher 同款注释），
    导致 @机器人 提取恒失败——主流程已因此踩过坑，这里保持一致。"""
    return (getattr(event, "original_message", None)
            or getattr(event, "message", None)
            or [])


def _plain_text(event: MessageEvent) -> str:
    """纯文本（去掉 at 段）。必须基于 original_message：
    nonebot 会把 event.message 的 at 段改写成空文本段（改写后 get_plaintext
    恰好只剩文字），但 NapCat 等适配器不保证改写——get_plaintext 可能返回
    「@9984电男」，导致自然语言口令静默。这里与 dispatcher 同源提取 text 段。"""
    try:
        parts = []
        for seg in _event_segments(event):
            if seg.type == "text":
                parts.append(str(seg.data.get("text") or ""))
        return "".join(parts).strip()
    except Exception:
        try:
            return (event.get_plaintext() or "").strip()
        except Exception:
            return ""


def _extract_images(event: MessageEvent) -> list[str]:
    """提取消息里的图片来源（OneBot v11 image 段）。

    取图优先级（紫卡识别必须拿**原图**，缩略图 292px OCR 必崩）：
      1) path  —— NapCat 主机本地缓存的原始文件（同机可直接读，最稳）；
      2) url   —— CDN 原图直链（可能带 _720 缩略后缀，下载时再升级）；
      3) file  —— 兜底文件名/路径。
    """
    try:
        urls = []
        for seg in _event_segments(event):
            if seg.type != "image":
                continue
            _p = (seg.data.get("path") or "").strip()
            _u = (seg.data.get("url") or "").strip()
            _f = (seg.data.get("file") or "").strip()
            # path 只有**本进程可读**时才用（原生同机最有价值：零外呼拿原图）。
            # ⚠️ 容器化后 NapCat 在另一个容器，path 指向它自己的缓存目录，本进程读不到；
            # 旧实现「path 非空就 break」会让 _download_riven_image 收到非 HTTP 路径后
            # 直接返回 None → 整条紫卡分析静默失败，只回一句"请发截图"
            # （实测 2026-11-02 16:45 三宝聚卡复现）。故读不到 path 必须回退 url。
            if _p and os.path.isfile(_p):
                urls.append(_p)
            elif _u:
                urls.append(_u)
            elif _p:
                urls.append(_p)
            elif _f:
                urls.append(_f)
        return urls
    except Exception:
        return []


def _at_bot(event: MessageEvent, bot: Bot) -> bool:
    """消息里是否 @ 了机器人（基于 original_message，见 _event_segments）。"""
    try:
        self_id = str(getattr(bot, "self_id", "") or "")
        for seg in _event_segments(event):
            if seg.type == "at":
                q = str(seg.data.get("qq", "") or "")
                if q == self_id or q == "all":
                    return True
    except Exception:
        pass
    return False


def is_game_command(text: str) -> bool:
    """AI 模式下判定裸词是否为「已识别的游戏指令关键词」。

    用于让游戏指令在 AI 陪聊开启时仍可被本模块处理（不被大模型吞掉）。
    仅匹配 NATURAL_CMDS 显式词表（精确 / 前缀），**不含** _match_natural 末尾的
    WF 实体名兜底（毒妈/咖喱棒/充沛 等），避免把闲聊词误判成指令。
    """
    t = (text or "").strip()
    if not t:
        return False
    for cmd in sorted(NATURAL_CMDS, key=len, reverse=True):
        if t == cmd:
            return True
        if t.startswith(cmd) and t[len(cmd):].strip():
            return True
    return False


def _match_natural(text: str) -> tuple[str, str] | None:
    t = (text or "").strip()
    if not t:
        return None
    for cmd in sorted(NATURAL_CMDS, key=len, reverse=True):
        if t == cmd:
            return (cmd, "") if cmd in ZERO_ARG_OK else None
        if t.startswith(cmd):
            arg = t[len(cmd):].strip()
            if arg and len(arg) <= NATURAL_ARG_MAX:
                return cmd, arg
    # WF 别名/资料名兜底：直接说「毒妈/咖喱棒/充沛」也能查（>=2 字防误伤聊天）
    try:
        if len(t) >= 2:
            for _tbl in ("wf", "mod", "arc"):
                if warframe._resolve_card(t, warframe._load_cards()[_tbl]):
                    return (t[:30], "")
    except Exception:
        pass
    return None


async def _download_image(url: str) -> bytes | None:
    """下载头像（og:image / 本地半身像缓存），失败返回 None（卡片退化为无头像布局）。"""
    if not url:
        return None
    if os.path.isfile(url):  # 本地缓存（战甲半身像 portraits/）直接读，零外呼
        try:
            with open(url, "rb") as f:
                b = f.read()
            if not b or len(b) > AVATAR_MAX_BYTES:
                return None
            return b
        except Exception:  # noqa: BLE001
            return None
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=10.0
        ) as c:
            r = await c.get(url)
            r.raise_for_status()
            if not r.content or len(r.content) > AVATAR_MAX_BYTES:
                return None
            return r.content
    except Exception:  # noqa: BLE001
        return None


# 紫卡截图可能是 1080p 全窗口 PNG（数 MB），放宽上限避免被头像 5MB 限制误杀
RIVEN_MAX_BYTES = 25 * 1024 * 1024


def _riven_orig_url(url: str) -> str:
    """QQ CDN 缩略图 → 原图直链升级。

    QQ 图床对群图默认按尺寸命名：``md5_720.jpg`` 是 720px 缩略，去掉尺寸标记
    ``md5.jpg`` 即回原图；``multimedia.nt.qq.com.cn`` 新式图床同理。
    无尺寸标记则原样返回（已是原图）。
    """
    if not url:
        return url
    # 仅在"尺寸标记 + 扩展名/结尾"处剥离，避免误伤正常文件名
    return re.sub(r"_(?:720|thumb|min|orj360|orjRank)(?=\.[a-zA-Z]{2,4}|$)", "", url)


async def _download_riven_image(url: str) -> bytes | None:
    """紫卡专用下载：优先本地 path（NapCat 原图缓存），
    其次 CDN 原图（自动剥离 _720 等缩略后缀升级），失败时回退原始 url。"""
    if not url:
        logger.warning("riven_img_download_skip: empty source")
        return None
    if os.path.isfile(url):  # NapCat path 字段：同机本地原图，零外呼
        try:
            with open(url, "rb") as f:
                b = f.read()
            if not b or len(b) > RIVEN_MAX_BYTES:
                logger.warning(f"riven_img_local_bad: size={len(b or b'')} path={url[:120]}")
                return None
            return b
        except Exception as e:  # noqa: BLE001
            logger.warning(f"riven_img_local_failed: {type(e).__name__}: {e} path={url[:120]}")
            return None
    if not (url.startswith("http://") or url.startswith("https://")):
        # 最常见于「容器里拿到 NapCat 容器的 path」：不是 HTTP、本进程又读不到。
        logger.warning(f"riven_img_unreadable_source: path={url[:160]}")
        return None
    cand = []
    up = _riven_orig_url(url)
    if up != url:
        cand = [up, url]  # 先试原图，失败回退缩略
    else:
        cand = [url]
    for c in cand:
        for attempt in range(3):  # 网络类异常（容器重建后 DNS 未稳定等）退避重试
            try:
                async with httpx.AsyncClient(
                    headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=20.0
                ) as cli:
                    r = await cli.get(c)
                if r.status_code == 200 and r.content and len(r.content) <= RIVEN_MAX_BYTES:
                    return r.content
                logger.warning(f"riven_img_download_bad: status={r.status_code} "
                               f"len={len(r.content or b'')} url={c[:140]}")
                break  # 状态码明确（URL 过期/404 等），重试无用，直接下一候选
            except Exception as e:  # noqa: BLE001 — 网络抖动退避重试
                logger.warning(f"riven_img_download_failed: attempt={attempt + 1}/3 "
                               f"{type(e).__name__}: {str(e)[:120]} url={c[:140]}")
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
    logger.warning(f"riven_img_download_giveup: candidates={len(cand)} first={url[:140]}")
    return None


def _guide_meta(title: str):
    """(数据源名, 时间前缀, 数据最后更新时间)。
    时间来自数据源自身返回的更新字段（Tenno.Tools section.time / Market last_update），
    取不到就留空——绝不用机器人本机时间冒充数据时间。"""
    t = title or ""
    if t.startswith("星际战甲"):
        if "裂隙" in t:
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("fissures")
        if "突击" in t:
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("sorties")
        if ("奸商" in t) or ("虚空商人" in t):
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("voidtraders")
        if "特惠" in t:
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("dailydeals")
        if "入侵" in t:
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("invasions")
        if "仲裁" in t:
            # 2026-04-11 起 Tenno.Tools 的 `arbitrations` 板块停更（data 恒为空），
            # 仲裁改走官方世界状态 api.warframe.com（ArbitersSyndicate）+ wf.wiki 节点名。
            return "官方世界状态 api.warframe.com + wf.wiki", "更新于", warframe._src_time("arbitration_official")
        if "电波" in t:
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("nightwave")
        if "平原" in t:
            return "Tenno.Tools + wf.wiki 世界状态", "更新于", warframe._src_time("daynight")
        if "赏金" in t or "钢铁" in t:
            return "worldstate.wf.wiki 世界状态", "更新于", warframe._src_time("bounties")
        if "九重天" in t:
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("voidstorms")
        if "执刑官" in t or "执政官" in t or "执行官" in t:
            return "DE 官方世界状态（LiteSorties）", "更新于", warframe._src_time("latest")
        if ("周本" in t) or ("钢铁" in t):
            return "Tenno.Tools 世界状态", "更新于", warframe._src_time("latest")
        if "紫卡评估" in t:
            return "本地 OCR + warframe-items 数据", "数据构建", warframe._src_time("local")
        if "紫卡趋势" in t or "词条价差" in t or "紫卡价差" in t:
            return "WFM 真实挂牌 + 本地落盘采样", "最近采样", warframe._src_time("local")
        if "紫卡融合" in t:
            return "WFM 真实挂牌 + Warframe Wiki《Fusion》", "实时", warframe._src_time("live")
        if "倾向" in t:
            # 倾向卡是离线规则说明，不能因为标题含「紫卡」被误标成市场实时行情
            return "DE 官方热修 44.1.3（2026-10-22 起统一 1.2）", "规则生效", warframe._src_time("local")
        if ("价格" in t) or ("市价" in t) or ("紫卡" in t) or ("赤毒" in t) or ("信条" in t):
            return "Warframe.Market 实时行情", "订单更新", warframe._src_time("market")
        if "轮换" in t:
            return "灰机 Wiki 世界状态", "", ""  # 页面无更新时间字段，如实不标
        if ("战甲信息" in t) or ("MOD" in t) or ("赋能" in t) or ("资料" in t):
            return "warframe-items 官方数据", "数据构建", warframe._src_time("local")
        if "翻译" in t:
            return "本地中英词库", "数据构建", warframe._src_time("local")
        return "Tenno.Tools 实时数据", "更新于", warframe._src_time("latest")
    if "角色图鉴" in t:
        return "官方 Wiki / 米游社攻略组", "", ""
    if "生图计划" in t:
        return "本机生图计划库（planstore / queue）", "", ""
    return "实时获取", "", ""


#: 卡片标记 DSL → 纯文本时需剥离的前缀（新增标记记得同步登记）
_MARKER_PREFIXES = ("@ttl|", "@title|", "@sub|", "@stat|", "@mstat|", "@w|", "@shout|",
                      "@foot|", "@page|", "@score|", "@rstat|", "@chips|",
                      "@chipline|", "@cmd|", "@alert|", "@disp|", "@hdr|", "@row|", "@wmhdr|")


def _strip_markers(body: str) -> str:
    """卡片标记还原成可读纯文本（text 模板 / 文字降级兜底用）。

    2026-09-30：guide 渲染失败会降到 text 模板，而 text 模板不认标记——
    不剥离的话用户会看到 `@ttl|武器倾向|book` 这种原始标记。
    """
    out: list[str] = []
    for ln in (body or "").split("\n"):
        if ln.startswith("@disp|"):
            # 2026-10-22 后现行倍率全武器统一，不再输出星级 ●○ 点阵。
            # 字段：name | cat | 现行倍率 | 改动前对照文案(可空) | 图标(可空)
            _f = ln[6:].split("|")
            _cur = _f[2] if len(_f) > 2 else "1.20"
            _before = _f[3] if len(_f) > 3 else ""
            out.append(f"[{_f[0]}] {_f[1]} -  现行倍率 {_cur}（全武器统一）"
                       + ("　" + _before if _before else ""))
            continue
        if ln.startswith("@ttl|"):
            # `@ttl|标题|图标`：只取标题，图标名不进纯文本
            out.append(ln[5:].split("|")[0])
            continue
        if ln.startswith("@sub|"):
            # `@sub|文本` 或 `@sub|文本|字号`：只取文本，丢掉字号字段
            out.append(ln[5:].split("|")[0])
            continue
        if ln.startswith("@wmhdr|"):
            # 头条块：取前 7 个可读字段（末位图标路径不进纯文本）
            _f = ln.split("|")[1:8]
            out.append(" ".join(_f))
            continue
        if ln.startswith("@hdr|") or ln.startswith("@row|"):
            out.append(ln.split("|", 1)[1].replace("|", "  "))
            continue
        for _pre in _MARKER_PREFIXES:
            if ln.startswith(_pre):
                ln = ln[len(_pre):]
                break
        out.append(ln)
    return "\n".join(out)


async def _collect_market_avatars(body: str) -> dict:
    """从市价卡 @row| 标记解析玩家，返回 uid→头像字节（人人有图）。

    - 有 WM 头像(avatar_rel)的：并发拉真实图；
    - 无 WM 头像的（约一半玩家，API 直接省略 avatar 字段）：确定性生成头像兜底，
      保证每个玩家行都有一张图（生成的是图标/机器人，非真人照片，明确标注）。
    best-effort，整体失败返回 {}。
    """
    rows: list[tuple[str, str, str]] = []  # (uid, avatar, name)
    for ln in (body or "").split("\n"):
        if not ln.startswith("@row|"):
            continue
        cols = ln.split("|")[1:]
        if len(cols) >= 7 and cols[-2]:
            rows.append((cols[-2], cols[-1], cols[0]))
    if not rows:
        return {}
    real = [(u, r) for u, r, _ in rows if r]
    out: dict[str, bytes] = {}
    if real:
        fetched = await warframe.fetch_market_avatars(real)
        out.update({u: b for u, b in fetched.items() if b})  # 仅保留成功取到的真图
    # 其余（无 avatar_rel，或真图取失败）：优先游戏内浮印（Glyph），失败回落确定性生成头像，绝不留空
    need = [(u, n) for u, r, n in rows if out.get(u) is None]
    if need:
        sem = asyncio.Semaphore(warframe._AVATAR_CONCURRENCY)
        gens = await asyncio.gather(
            *(warframe._glyph_or_generated(u, n, sem) for u, n in need),
            return_exceptions=True)
        for (u, _n), g in zip(need, gens):
            out[u] = g if not isinstance(g, Exception) else warframe._local_identicon(u or _n or "anon")
    return out


async def _collect_market_item_icon(body: str) -> bytes | None:
    """从市价卡 @wmhdr| 标记解析图标，best-effort 返回字节（失败 None）。

    取图策略：
      * 武器/战甲套装 → 官方 WFCD 512px 全图（WM 方形图会裁枪管/截半身）；
      * 部件蓝图 → Fandom 部位图（区分部位）；
      * 总图/MOD/赋能 → WM 商品自身 icon。总图是 Atlas Prime 立绘，绝不能走
        Fandom 通用 Blueprint.png（与商品无关，2026-11-09「总图取的不对」）。
    渲染层按真实 AR 等比自适应。
    """
    for ln in (body or "").split("\n"):
        if not ln.startswith("@wmhdr|"):
            continue
        cols = ln.split("|")[1:]
        if len(cols) < 8:
            return None
        rel = cols[7].strip()
        en = (cols[1] if len(cols) > 1 else "").strip()
        cat = (cols[2] if len(cols) > 2 else "").strip()
        # MOD/赋能/集团卡/架势/光环/安魂：卡面图，绝不能用武器全图
        _mod_like = any(k in cat for k in ("MOD", "赋能", "集团卡", "架势", "光环", "安魂"))
        # 仅部件蓝图走 Fandom 部位图；总图（蓝图且非部件）必须落商品自身 icon
        _component = ("部件" in cat) or ("蓝图" in cat)
        if "部件" in cat and rel:
            art = await warframe.fetch_part_art(rel)
            if art:
                return art
        if en and not _mod_like and not _component and ("武器" in cat or "战甲" in cat):
            # 套装英文名是「Sicarus Prime Set」，官方图文件名是「Sicarus Prime」→ 去掉 Set 后缀
            _art_name = re.sub(r"\s+Set$", "", en, flags=re.I).strip()
            art = await warframe.fetch_official_art(_art_name)
            if art:
                return art
        if not rel:
            return None
        try:
            return await warframe.fetch_item_icon(rel)
        except Exception:  # noqa: BLE001
            return None
    return None


async def _send_guide(bot: Bot, event: MessageEvent, title: str, text: str,
                      avatar_url: str = "", card_data: dict | None = None,
                      avatars: dict | None = None) -> None:
    """攻略富卡片：guide 模板（头像+分区）→ text 模板 → 纯文本，逐级降级。

    ``card_data`` 非空时优先用**专属图片模板**渲染（目前只有紫卡分析卡：逐像素复刻
    参考卡）；该模板失败仍逐级降级到 guide → text → 纯文本，绝不因此不回话。
    """
    body = (text or "").strip()
    if len(body) > PIC_BODY_MAX:
        body = body[:PIC_BODY_MAX] + "\n…（内容较长已截断）"
    from .. import pic as pic_mod
    from ..media import send_pic

    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    if card_data:
        try:
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(
                pic_mod.render("riven_analysis", theme_name=None,
                               card=title, data=card_data),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="riven_analysis",
                              elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            # 2026-11-11 第六次按钮修改：紫卡分析结果图**专属**挂一个「全面分析」
            # 按钮。用内部哨兵写进局部 ctx["keyboard_cmd"]：official.py::send 合并
            # ctx 时「调用方显式字段优先」，btn_cmd 兜底链会读到该哨兵 →
            # qq_keyboard.buttons_for 命中专用分支 → 只此卡挂「全面分析」按钮，
            # 与图片合并同条。全面分析卡（_send_riven_full_parsed）自造 ctx 不含
            # 此键 → btn_cmd 空 → 零按钮（用户「全面分析卡不附带按钮」）。NapCat
            # 通道忽略 keyboard_cmd，行为完全不变。哨兵非常规文本口令，零编造安全。
            ctx["keyboard_cmd"] = "__riven_analysis__"
            await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
            return
        except Exception as e:  # noqa: BLE001 — 专属模板失败 → 继续走 guide，绝不静默
            logger.warning(f"game_tools_card_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_card_render_failed", err=type(e).__name__,
                                detail=str(e)[:160]))
    # 数据源脚注失败绝不拖垮整卡（2026-10-09：def 行曾被误删导致 NameError
    # 让所有 guide 卡发送全挂，这里加兜底防御回归）
    try:
        _src, _pre, _ts = _guide_meta(title)
    except Exception as _e:  # noqa: BLE001 — 脚注缺失不影响出卡
        logger.warning(f"guide_meta_failed: {type(_e).__name__}: {_e}")
        _src, _pre, _ts = "实时获取", "", ""
    if _ts and _pre:
        _foot = "数据源：%s · %s %s" % (_src, _pre, _ts)
    else:
        _foot = "数据源：%s" % _src
    avatar = await _download_image(avatar_url)
    # 市价卡：解析 @row| 里的 (uid, avatar_rel)，并发拉真实头像（best-effort，过 CF 才取得到，
    # 取不到回落生成头像）。单独 wait_for 包住——绝不因头像卡住而拖垮整卡出图。
    if avatars is None and "@row|" in body:
        try:
            avatars = await asyncio.wait_for(_collect_market_avatars(body), timeout=10.0)
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"gt_collect_avatars_failed: {type(_e).__name__}: {_e}")
            avatars = None
    # 市价卡头条图标：解析 @wmhdr| 末位图标路径，单独 wait_for 拉取（同样 best-effort，
    # 取不到回落几何占位，绝不因图标卡住而拖垮整卡出图）。
    item_icon = None
    if "@wmhdr|" in body:
        try:
            item_icon = await asyncio.wait_for(_collect_market_item_icon(body), timeout=10.0)
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"gt_collect_item_icon_failed: {type(_e).__name__}: {_e}")
            item_icon = None
    # 卡片自带头条（body 以 @title| / @ttl| / @wmhdr| 开头：wm 市价卡头条、倾向卡居中大标题）
    # → 不再重复画外层标题栏，与参考卡片（单行头条）保持一致；其余卡片标题栏不变。
    _card_title = "" if (body.startswith("@title|") or body.startswith("@ttl|")
                         or body.startswith("@wmhdr|")) else title
    try:
        # 全链路超时保护（2026-09-16：wikia 头像下载/渲染/发送任一处挂起
        # 都会吞掉整条回复，表现为「发了没反应」）——超时即降级，绝不静默
        _t0 = asyncio.get_running_loop().time()
        if avatars or item_icon:
            # 真实头像/物品图标走 render_sync（不走磁盘缓存：bytes 进不了缓存 key，且图会变）
            png = await asyncio.wait_for(
                asyncio.to_thread(pic_mod.render_sync, "guide", theme_name=None, card=title,
                                 body=body, title=_card_title, avatar=avatar,
                                 avatar_vertical=bool(avatar), source=_foot,
                                 avatars=avatars, item_icon=item_icon),
                timeout=20.0)
        else:
            png = await asyncio.wait_for(
                pic_mod.render("guide", theme_name=None, card=title, body=body, title=_card_title,
                               avatar=avatar, avatar_vertical=bool(avatar), source=_foot),
                timeout=20.0)
        _flog.info(_jlog("gt_render_done", kind="guide",
                          elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
        # 紫卡卡可达 1.25MB+，NapCat 上传常需 15~25s；20s 会撞超时触发文字兜底，
        # 故发送超时放宽到 60s（渲染仍 20s）。
        await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"game_tools_guide_render_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_guide_render_failed", err=type(e).__name__, detail=str(e)[:160]))
    try:
        _t0 = asyncio.get_running_loop().time()
        png = await asyncio.wait_for(
            pic_mod.render("text", theme_name=None, card=title,
                           body=_strip_markers(body)[:900], title=title, source=_foot),
            timeout=20.0)
        _flog.info(_jlog("gt_render_done", kind="text",
                          elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
        await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"game_tools_text_render_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_text_render_failed", err=type(e).__name__, detail=str(e)[:160]))
    try:
        _plain = _strip_markers(body[:1200])
        await asyncio.wait_for(bot.send(event, f"【{title}】\n{_plain}\n—— {_foot}"),
                              timeout=15.0)
    except Exception as e:  # noqa: BLE001
        logger.error(f"game_tools_send_failed: {type(e).__name__}: {e}")
        _flog.error(_jlog("gt_send_failed", err=type(e).__name__, detail=str(e)[:160]))


async def _maybe_send_mod_card(bot: Bot, event: MessageEvent, arg: str) -> None:
    """wf 命令命中 MOD 时，除文字资料外再发一张忠实复刻的 mod 卡图（WFCD mod-generator 风格）。

    纯 best-effort：解析失败 / 渲染失败 / 发送失败都静默降级，绝不拖垮文字回复。
    卡图由 tools/gen_mod_cards.render_mod_card 生成（离线卡框 + 真实 DE 立绘缓存）。
    """
    try:
        a = (arg or "").strip()
        parts = a.split(None, 1)
        sub = parts[0].strip().lower()
        query = parts[1].strip() if (len(parts) > 1 and sub in ("mod", "卡", "卡片")) else a
        if not query:
            return
        res = warframe._resolve_card(query, warframe._load_cards()["mod"])
        if not res:
            return
        key, _ = res
        from tools.gen_mod_cards import render_mod_card
        png = render_mod_card(key)
        if not png:
            return
        ctx = {
            "user_id": getattr(event, "user_id", 0),
            "group_id": getattr(event, "group_id", 0),
            "channel": getattr(event, "channel", "napcat"),
        }
        from ..media import send_pic
        await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
        _flog.info(_jlog("gt_mod_card_sent", key=key, bytes=len(png)))
    except Exception as e:  # noqa: BLE001 — 卡图是增强项，失败不影响文字资料
        logger.warning(f"gt_mod_card_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_mod_card_failed", err=type(e).__name__, detail=str(e)[:160]))


async def _send_build(bot: Bot, event: MessageEvent, word: str) -> None:
    """配卡图片卡：本地库命中 → 横版卡出图；未命中/渲染失败 → 文本（含 overframe 引导）。

    版式复刻 Warframe Rabbit「配卡推荐」，MOD 区为**横版缩略卡**（官方卡框资产 +
    DE 官方立绘由 tools/gen_mod_cards 合成）。overframe 本机 403 不实时抓取，
    未收录武器如实引导，绝不编造。
    """
    from .build_card import render_build
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    data = None
    try:
        data = warframe.build_card_data(word)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_build_data_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_build_data_failed", err=type(e).__name__, detail=str(e)[:160]))
    # 未收录 → 按需抓取（warframe.today 武器页）并可写回 builds.json 缓存。
    # 这是「几百把武器不能挨个手工加」的正解：只在**首次**查某把武器时联网，
    # 之后走本地缓存；抓取结果逐条对照本地官方数据校验，校验不过就诚实回落文本。
    if not data:
        try:
            from .build_fetch import ensure_build
            _t1 = asyncio.get_running_loop().time()
            got = await asyncio.wait_for(ensure_build(word), timeout=30.0)
            if got:
                _flog.info(_jlog("gt_build_fetched", key=got,
                                 elapsed="%.2f" % (asyncio.get_running_loop().time() - _t1)))
                data = warframe.build_card_data(word)
        except Exception as e:  # noqa: BLE001 — 抓取失败只回落文本，不影响主流程
            logger.warning(f"gt_build_fetch_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_build_fetch_failed", err=type(e).__name__,
                               detail=str(e)[:160]))
    if data:
        try:
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(asyncio.to_thread(render_build, data), timeout=30.0)
            _flog.info(_jlog("gt_build_render_done", zh=data.get("zh"),
                             elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
            return
        except Exception as e:  # noqa: BLE001 — 出图失败回落文本，绝不静默
            logger.warning(f"gt_build_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_build_render_failed", err=type(e).__name__,
                               detail=str(e)[:160]))
    # 降级：文本配卡建议（build_text 已含 overframe 链接引导）
    try:
        txt = await warframe.build_text(word)
        avail = warframe.list_build_weapons()
        if avail:
            # 缓存会随「按需抓取」不断变长（几十上百把），整段铺开会把消息撑爆，
            # 这里只给最近收录的几个做提示，其余让用户直接查。
            _show = 8
            if len(avail) > _show:
                txt += ("\n\n已收录配卡 %d 把（可直查）：%s 等"
                        % (len(avail), "、".join(avail[:_show])))
            else:
                txt += "\n\n已收录本地配卡：" + "、".join(avail)
        await asyncio.wait_for(bot.send(event, txt[:1500]), timeout=20.0)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_build_text_failed: {type(e).__name__}: {e}")


async def _send_riven_recommend(bot: Bot, event: MessageEvent, word: str) -> None:
    """紫卡洗卡词条推荐卡（击杀效率 + 市场挂牌分层双维度，照 Rabbit 参考卡版式）。

    数据：本仓库 riven_card DPS 模型（词条满 roll 边际贡献）+ 实时 Warframe.Market
    挂牌分层（高价层/底价层各约 100 条的出现率）。融合词条实盘无独立属性字段，
    如实标注不统计；接口失败/样本不足如实回错或标原因，绝不编数。出图失败降级纯文本。
    """
    w = (word or "").strip()
    if not w:
        try:
            await bot.send(event,
                           "紫卡洗卡词条推荐用法：\n"
                           "  #词条推荐 凤殁\n"
                           "  #洗卡推荐 绝路\n"
                           "  #wf 推荐 凤殁\n"
                           "（本仓库 DPS 模型 + 实时 Warframe.Market 挂牌分层）")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_riven_recommend_usage_failed: {type(e).__name__}: {e}")
        return
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    try:
        from .riven_recommend import recommend_data, recommend_text
        data = await asyncio.wait_for(recommend_data(w), timeout=90.0)
    except Exception as e:  # noqa: BLE001 — 数据失败如实回错，不静默
        logger.warning(f"gt_riven_recommend_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_riven_recommend_failed", err=type(e).__name__,
                            detail=str(e)[:160]))
        try:
            await bot.send(event, "词条推荐生成失败：%s: %s" % (type(e).__name__, e))
        except Exception:  # noqa: BLE001
            pass
        return
    if data.get("status") != "ok":
        try:
            await bot.send(event, data.get("msg") or "未收录该武器")
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        _t0 = asyncio.get_running_loop().time()
        png = await asyncio.wait_for(
            pic_mod.render("riven_recommend", theme_name=None,
                           card="星际战甲·紫卡推荐", data=data),
            timeout=30.0)
        _flog.info(_jlog("gt_riven_recommend_render_done", key=data.get("slug"),
                         elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
        await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
        return
    except Exception as e:  # noqa: BLE001 — 出图失败回落文本，绝不静默
        logger.warning(f"gt_riven_recommend_render_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_riven_recommend_render_failed", err=type(e).__name__,
                            detail=str(e)[:160]))
    try:
        await bot.send(event, recommend_text(data)[:1800])
    except Exception as e:  # noqa: BLE001
        logger.error(f"gt_riven_recommend_text_failed: {type(e).__name__}: {e}")


async def _send_netracell(bot: Bot, event: MessageEvent) -> None:
    """科研专属卡片：pic 模板 netracell（浅色白卡+蓝色气泡）→ guide → 纯文本，逐级降级。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = "星际战甲·科研任务"
    try:
        data = await warframe.netracell_data()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_netracell_data_failed: {type(e).__name__}: {e}")
        data = {}
    if data.get("sections"):
        try:
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(
                pic_mod.render("netracell", theme_name=None, card=title,
                               sections=data["sections"], title=data.get("title", "本周科研任务"),
                               remain=data.get("remain", "")),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="netracell",
                             elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_netracell_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_netracell_render_failed", err=type(e).__name__, detail=str(e)[:160]))
    # 降级：guide 模板纯文本
    try:
        txt = await warframe.netracell_text()
    except Exception:  # noqa: BLE001
        txt = "科研数据暂时没拉到，稍后再试～"
    await _send_guide(bot, event, title, txt)


async def _send_incarnon(bot: Bot, event: MessageEvent) -> None:
    """灵化周常专属卡：pic 模板 incarnon（Rabbit 版式）→ guide → 纯文本，逐级降级。

    2026-10-22 重做：原「#wf 灵化」只回纯文本，且轮换译名/顺序/倾向与 Rabbit
    原卡不一致；现按原卡逐区域出图（战甲 3 卡 + 武器 5 卡 + 9×5 轮回表 + 倒计时）。
    """
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = "星际战甲·灵化轮换"
    try:
        data = warframe.incarnon_card_data()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_incarnon_data_failed: {type(e).__name__}: {e}")
        data = {}
    if data.get("weapons"):
        try:
            # 出图前把本周 3 张真实战甲立绘预取齐（冷缓存自动补，别名见
            # warframe._FRAME_IMAGE_ALIAS）；缺图直接抛错，绝不画几何占位/静默降级。
            names = [str(f.get("name") or "").strip()
                     for f in (data.get("frames") or [])]
            names = [n for n in names if n]
            fetched = await asyncio.gather(
                *(warframe.ensure_frame_icon(n) for n in names))
            missing = [n for n, path in zip(names, fetched) if not path]
            if missing:
                raise FileNotFoundError("灵化卡战甲立绘缺失：%s" % ", ".join(missing))
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(
                pic_mod.render("incarnon", theme_name=None, card=title, data=data),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="incarnon",
                             elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_incarnon_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_incarnon_render_failed", err=type(e).__name__,
                                detail=str(e)[:160]))
    # 降级：guide 模板纯文本
    try:
        txt = warframe.incarnon_text()
    except Exception:  # noqa: BLE001
        txt = "灵化轮换表还没加载，稍后再试～"
    await _send_guide(bot, event, title, txt)


async def _send_alerts(bot: Bot, event: MessageEvent) -> None:
    """警报任务专属卡：pic 模板 alerts（Rabbit 版式：派系胶囊+节点+倒计时胶囊+奖励/现金）→ guide 降级。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = "星际战甲·警报任务"
    try:
        data = await warframe.alerts_data()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_alerts_data_failed: {type(e).__name__}: {e}")
        data = {"alerts": [], "empty": True}
    if data.get("alerts"):
        # 出图前预取真实图标（福马/月华腩/星球奖杯/现金），失败仅回落几何占位，不影响出图
        try:
            from ..pic import item_icons as _item_icons
            await _item_icons.ensure_alerts_icons(data["alerts"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_alerts_icon_prefetch_failed: {type(e).__name__}: {e}")
        try:
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(
                pic_mod.render("alerts", theme_name=None, card=title,
                               alerts=data["alerts"], title="警报任务"),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="alerts",
                             elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_alerts_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_alerts_render_failed", err=type(e).__name__, detail=str(e)[:160]))
    # 降级
    await _send_guide(bot, event, title, await warframe.alert_text())


async def _send_archon(bot: Bot, event: MessageEvent) -> None:
    """本周执刑官猎杀专属卡（Rabbit 参考图逐像素复刻）→ guide 文本降级。

    数据来自 DE 官方 worldState.php 的 LiteSorties；出图失败/取数失败都
    显式记日志并降级为纯文本，绝不静默（CLOSE-02 / REDUN-01）。
    """
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = "星际战甲·执刑官猎杀"
    try:
        data = await warframe.archon_hunt_data()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_archon_data_failed: {type(e).__name__}: {e}")
        data = None
    if data:
        try:
            png = await asyncio.wait_for(
                pic_mod.render("archon", theme_name=None, card=title, data=data),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="archon",
                             boss=data.get("boss_zh", "")))
            await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_archon_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_archon_render_failed", err=type(e).__name__,
                                detail=str(e)[:160]))
    await _send_guide(bot, event, title, await warframe.archon_hunt_text())


async def _send_riven_trend(bot: Bot, event: MessageEvent,
                            mode: str, weapon: str) -> None:
    """紫卡趋势 / 词条价差专属卡：真联网取数，失败把原因写进卡面（不编数）。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    from . import riven_trend as rt

    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    w = (weapon or "").strip()
    if not w:
        await bot.send(event, "格式：紫卡趋势 <武器> ｜ 词条价差 <武器>（例：紫卡趋势 绝路）")
        return
    title = "星际战甲·紫卡趋势" if mode == "trend" else "星际战甲·紫卡词条价差"
    try:
        data = (await rt.trend_data(w)) if mode == "trend" else (await rt.spread_data(w))
    except Exception as e:  # noqa: BLE001 — 显式失败态，绝不填「暂无」/估算
        logger.warning(f"gt_riven_trend_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_riven_trend_failed", mode=mode,
                            err=type(e).__name__, detail=str(e)[:160]))
        data = {"weapon_zh": w, "weapon_en": "", "points": [], "rows": [],
                "current": {}, "base_median": None, "base_n": 0,
                "enough_for_trend": False,
                "error": "获取失败：%s：%s" % (type(e).__name__, str(e)[:110]),
                "source": "WFM 紫卡挂牌要价·非成交价｜获取失败，未生成任何估算数据"}
    try:
        png = await asyncio.wait_for(
            pic_mod.render("riven_trend", theme_name=None, card=title,
                           data=data, mode=mode), timeout=20.0)
        await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_riven_trend_render_failed: {type(e).__name__}: {e}")
        await bot.send(event, "紫卡趋势出图失败：%s：%s" % (type(e).__name__, str(e)[:110]))



# ---------------------------------------------------------------------------
# 紫卡全面分析（2026-11-06）：655x1920 像素级复刻 Warframe Rabbit 完整分析卡
# ---------------------------------------------------------------------------
async def _send_riven_full(bot: Bot, event: MessageEvent, word: str) -> None:
    """紫卡全面分析卡：武器名 / 样例 / 空参数（登记预约）三种入口。"""
    w = (word or "").strip()
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    try:
        from .riven_full import (reference_sample, riven_full_data, full_text,
                                RivenFullError)
        if w.lower() in ("样例", "sample", "demo", "参考卡"):
            data = reference_sample()
        elif not w:
            # 空参数＝预约下一张截图，而不是只丢一句用法
            _set_riven_full_wait(event)
            await bot.send(event,
                           "紫卡全面分析用法：\n"
                           "  #紫卡全面 <武器> → 真实 WFM 市价 + 本地配卡\n"
                           "  #紫卡全面 样例 → 参考卡版式复刻（非实时数据）\n"
                           "  直接发紫卡截图 → 真实 OCR 全量分析\n"
                           "已登记预约，请直接发送紫卡截图～")
            return
        else:
            market = None
            try:
                # 武器名路径：构造最小 parsed 抓一次市场快照（rank 默认 8），
                # 让「市场价格预计」显示真实挂牌中位；失败编码成 status=error 落卡面。
                # ⚠️ 旧版调已删除的 _auction_stats() → NameError 被吞 → 价格永远空。
                from . import riven_card as _rc
                market = await asyncio.wait_for(
                    _rc.fetch_market_for_card({"weapon": w, "weapon_zh": w, "rank": 8}),
                    timeout=15.0)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"gt_riven_full_wfm_failed: {type(e).__name__}: {e}")
            data = riven_full_data(weapon=w, market=market)
    except Exception as e:  # noqa: BLE001 — 数据失败如实回错，不静默
        logger.warning(f"gt_riven_full_failed: {type(e).__name__}: {e}")
        try:
            await bot.send(event, "紫卡全面分析生成失败：%s: %s" % (type(e).__name__, e))
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        png = await asyncio.wait_for(
            pic_mod.render("riven_full", theme_name=None,
                           card="星际战甲·紫卡全面分析", data=data,
                           width=655, use_cache=False),
            timeout=30.0)
        await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
        return
    except Exception as e:  # noqa: BLE001 — 出图失败回落文本，绝不静默
        logger.warning(f"gt_riven_full_render_failed: {type(e).__name__}: {e}")
    try:
        await bot.send(event, full_text(data)[:1800])
    except Exception as e:  # noqa: BLE001
        logger.error(f"gt_riven_full_text_failed: {type(e).__name__}: {e}")


async def _send_riven_full_parsed(bot: Bot, event: MessageEvent, parsed: dict) -> None:
    """紫卡截图 OCR 结果 → 紫卡全面分析卡（真实词条/评分/击杀效率，零编造）。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    try:
        from .riven_full import riven_full_data, full_text
        market = None
        try:
            # 市场快照是 build_card_data 的必填参数（2026-11-04 起），必须在此统一抓一次。
            # ⚠️ 旧版调已删除的 _auction_stats() → NameError 被吞 → 其后 market 抓取
            # 整段被跳过 → 卡面静默空壳（2026-11-06 用户「差太远了」根因）。
            # 现只用唯一入口 fetch_market_for_card，失败也编码成 status=error 落卡面，不静默。
            from . import riven_card as _rc
            market = await asyncio.wait_for(
                _rc.fetch_market_for_card(parsed), timeout=15.0)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_riven_full_parsed_market_failed: {type(e).__name__}: {e}")
        data = riven_full_data(parsed=parsed, market=market)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_riven_full_parsed_data_failed: {type(e).__name__}: {e}")
        try:
            await bot.send(event, "紫卡全面分析生成失败：%s: %s" % (type(e).__name__, e))
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        png = await asyncio.wait_for(
            pic_mod.render("riven_full", theme_name=None,
                           card="星际战甲·紫卡全面分析", data=data,
                           width=655, use_cache=False),
            timeout=30.0)
        await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_riven_full_parsed_render_failed: {type(e).__name__}: {e}")
    try:
        await bot.send(event, full_text(data)[:1800])
    except Exception as e:  # noqa: BLE001
        logger.error(f"gt_riven_full_parsed_text_failed: {type(e).__name__}: {e}")


async def _send_riven_fusion(bot: Bot, event: MessageEvent, weapon: str) -> None:
    """紫卡融合专属卡：真联网取数（可选武器），失败把原因写进卡面（不编数）。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    from . import riven_fusion as rf

    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    w = (weapon or "").strip()
    title = "星际战甲·紫卡融合"
    try:
        data = await rf.fusion_data(w)
    except Exception as e:  # noqa: BLE001 — 显式失败态，绝不填「暂无」/估算
        logger.warning(f"gt_riven_fusion_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_riven_fusion_failed", err=type(e).__name__,
                            detail=str(e)[:160]))
        data = {"weapon_zh": w or "通用指南", "weapon_en": "", "slug": "",
                "median": None, "n": 0, "cost_rows": rf.fusion_cost_table(),
                "error": "获取失败：%s：%s" % (type(e).__name__, str(e)[:110]),
                "source": "WFM 紫卡挂牌要价·非成交价｜获取失败，未生成任何估算数据"}
    try:
        png = await asyncio.wait_for(
            pic_mod.render("riven_fusion", theme_name=None, card=title,
                           data=data), timeout=20.0)
        await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_riven_fusion_render_failed: {type(e).__name__}: {e}")
        await bot.send(event, "紫卡融合出图失败：%s：%s" % (type(e).__name__, str(e)[:110]))


async def _send_fissure(bot: Bot, event: MessageEvent, mode: str = "quick") -> None:
    """裂隙专属卡（速览/普通/钢铁）→ guide 降级。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = {"quick": "星际战甲·速览虚空裂隙",
             "normal": "星际战甲·普通虚空裂隙",
             "steel": "星际战甲·钢铁虚空裂隙"}.get(mode, "星际战甲·虚空裂隙")
    try:
        data = await warframe.fissures_data(mode)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_fiss_data_failed: {type(e).__name__}: {e}")
        data = {}
    if data.get("groups"):
        try:
            # 出图前预取真实星球奖杯图标（失败仅回落灰蓝圆点，不影响出图）
            try:
                from ..pic import item_icons as _item_icons
                await _item_icons.ensure_fissure_icons(data["groups"])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"gt_fiss_icon_prefetch_failed: {type(e).__name__}: {e}")
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(
                pic_mod.render("fissure", theme_name=None, card=title,
                               groups=data["groups"], title=data["title"],
                               intro=data.get("intro", ""), footers=data.get("footers", [])),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="fissure",
                             elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_fiss_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_fiss_render_failed", err=type(e).__name__, detail=str(e)[:160]))
    # 降级
    await _send_guide(bot, event, title, await warframe.fissures_text())


# ===========================================================================
# 裂隙任务订阅（2026-11-05）
#   口令：订阅 <条件> / 订阅列表 / 取消订阅 <编号|全部>
#   条件：模式(钢铁裂隙/普通裂隙) + 纪元(古纪…后纪) + 任务(生存/防御/中断…) +
#         可选派系/星球；不写地图 = 不限地图（用户要的「无论什么地图」）。
#   命中新任务时由后台 monitor 回调 _task_sub_notify 推单卡回原会话。
# ===========================================================================
_SUB_ADD_WORDS = ("订阅", "订阅任务", "关注任务", "蹲任务")
_SUB_DEL_WORDS = ("取消订阅", "退订", "取消关注")
_SUB_LIST_WORDS = ("订阅列表", "我的订阅", "查看订阅", "任务订阅列表")


def _task_sub_intent(cmd: str, arg: str) -> tuple[str, str] | None:
    """识别订阅意图，兼容 #wf 订阅 / 裸口令 / 动词粘连参数三种写法。"""
    c, a = (cmd or "").strip(), (arg or "").strip()
    # 「#wf 订阅 钢铁裂隙 后纪 生存」：把首词从 arg 提到 cmd
    if c.lower() in ("wf", "星际战甲", "战甲", "frame", "warframe") and a.split():
        head = a.split()[0]
        if head in _SUB_ADD_WORDS + _SUB_DEL_WORDS + _SUB_LIST_WORDS + ("任务订阅",):
            c, a = head, " ".join(a.split()[1:])
    # 「#订阅钢铁裂隙 后纪 生存」：动词和第一个条件粘连。
    # 已经是完整命令词（如「订阅列表」）的绝不拆——否则会被短词「订阅」误切成 add。
    _known = _SUB_ADD_WORDS + _SUB_DEL_WORDS + _SUB_LIST_WORDS
    if c not in _known:
        for word in ("取消订阅", "订阅列表", "我的订阅", "订阅任务", "订阅"):
            if c.startswith(word) and c != word:
                c, a = word, (c[len(word):] + " " + a).strip()
                break
    if c in _SUB_ADD_WORDS:
        return "add", a
    if c in _SUB_DEL_WORDS:
        return "remove", a
    if c in _SUB_LIST_WORDS:
        return "list", a
    return None


async def _handle_task_sub(bot: Bot, event: MessageEvent,
                           action: str, payload: str) -> None:
    """订阅 / 取消 / 列表的会话内交互（订阅归属当前会话，推送回当前会话）。"""
    from ..task_subscribe import (CAP_PER_SCOPE, TaskSubscribeError,
                                  parse_condition, task_subscription_monitor,
                                  task_subscription_store)
    scope = _sk(event)
    uid = int(getattr(event, "user_id", 0) or 0)
    gid = int(getattr(event, "group_id", 0) or 0)
    channel = str(getattr(event, "channel", "napcat") or "napcat")
    try:
        if action == "add":
            cond = parse_condition(payload)
            sub = task_subscription_store.add(cond, scope, text=payload,
                                              user_id=uid, group_id=gid,
                                              channel=channel)
            # 预热：把当下已存在的匹配任务记入 seen，只推「之后新刷出来」的
            try:
                n = await asyncio.wait_for(
                    task_subscription_monitor.prime(sub["id"]), timeout=15.0)
            except Exception as e:  # noqa: BLE001 — 预热失败不阻断订阅
                logger.warning(f"gt_task_sub_prime_failed: {type(e).__name__}: {e}")
                n = -1
            cur = ("当前已有 %d 条匹配（已在监控中，不会重复推）" % n) if n >= 0 \
                else "当前状态暂时取不到，监控已开启"
            msg = ("已订阅：%s\n订阅号 %s｜%s\n之后只要刷新出符合条件的新任务，"
                   "就会自动把单张任务卡发到这里。\n取消：取消订阅 %s"
                   % (cond["label"], sub["id"], cur, sub["id"]))
        elif action == "remove":
            p = (payload or "").strip()
            if p in ("全部", "all", "*", "所有"):
                n = task_subscription_store.remove_all(scope)
                msg = "已取消本会话的 %d 条订阅。" % n
            elif p:
                sub = task_subscription_store.remove(p, scope)
                msg = "已取消订阅：%s（%s）" % (sub["label"], sub["id"])
            else:
                msg = "格式：取消订阅 <订阅号>｜清空：取消订阅 全部"
        else:  # list
            subs = task_subscription_store.list(scope)
            if not subs:
                msg = ("本会话还没有任务订阅。\n"
                       "例：订阅 钢铁裂隙 后纪 生存（不限地图）\n"
                       "也支持 普通裂隙 / 中断 / 虚空 等条件组合。")
            else:
                lines = ["本会话的任务订阅（%d/%d）：" % (len(subs), CAP_PER_SCOPE)]
                for i, s in enumerate(subs, 1):
                    lines.append(" %d. %s  %s  已推 %s 次"
                                 % (i, s["id"], s["label"], s.get("push_count", 0)))
                lines.append("取消：取消订阅 <订阅号>｜清空：取消订阅 全部")
                msg = "\n".join(lines)
    except TaskSubscribeError as e:
        msg = "订阅失败：%s" % e
    except Exception as e:  # noqa: BLE001 — 显式报错，绝不静默
        logger.warning(f"gt_task_sub_failed: {type(e).__name__}: {e}")
        _flog.warning(_jlog("gt_task_sub_failed", err=type(e).__name__,
                            detail=str(e)[:160]))
        msg = "订阅功能刚才出错了（已记录日志），稍后再试～"
    await bot.send(event, msg)


async def _task_sub_notify(sub: dict, f: dict) -> None:
    """monitor.on_match 回调：命中新任务 → 渲染单卡 → 推回订阅发起的会话。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    from ..task_subscribe import describe_task
    ctx = {
        "user_id": int(sub.get("user_id") or 0),
        "group_id": int(sub.get("group_id") or 0),
        "channel": sub.get("channel") or "napcat",
    }
    task = describe_task(f)
    png = await asyncio.wait_for(
        pic_mod.render("task_alert", theme_name=None, card="星际战甲·任务订阅",
                       task=task, sub_id=str(sub.get("id") or ""),
                       cond_label=str(sub.get("label") or "")),
        timeout=20.0)
    await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
    _flog.info(_jlog("gt_task_sub_pushed", sid=sub.get("id", ""),
                     node=task.get("node", "")[:40]))


async def _send_bounty(bot: Bot, event: MessageEvent, steel: bool = False) -> None:
    """赏金任务一览专属卡：pic 模板 bounty（Rabbit 版式）→ guide → 纯文本，逐级降级。"""
    from .. import pic as pic_mod
    from ..media import send_pic

    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = "星际战甲·钢铁之路赏金" if steel else "星际战甲·赏金"
    try:
        data = await warframe.bounties_card_data()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_bounty_data_failed: {type(e).__name__}: {e}")
        data = None
    if data:
        try:
            _src, _pre, _ts = _guide_meta(title)
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"guide_meta_failed: {type(_e).__name__}: {_e}")
            _src, _pre, _ts = "实时获取", "", ""
        foot = "数据源：%s" % _src
        if _ts and _pre:
            foot = "数据源：%s · %s %s" % (_src, _pre, _ts)
        try:
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(
                pic_mod.render("bounty", theme_name=None, card=title,
                               data=data, source=foot),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="bounty",
                             elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_bounty_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_bounty_render_failed", err=type(e).__name__, detail=str(e)[:160]))
    # 降级：guide 模板纯文本
    try:
        txt = await warframe.steel_bounties_text() if steel else await warframe.bounties_text()
    except Exception:  # noqa: BLE001
        txt = "赏金数据暂时没拉到，稍后再试～"
    await _send_guide(bot, event, title, txt)


async def _send_tenet(bot: Bot, event: MessageEvent, kind: str = "tenet") -> None:
    """信条 / 终幕武器轮换卡：**两个口令各走各的专属模板**。

    ``#wf 信条`` → ``tenet``（单列粉紫 Rabbit 版式）；
    ``#wf 终幕`` → ``coda``（800×998 参考图两列版式）。
    2026-11-06 用户纠正「信条和终幕是两个口令」后拆开，禁止再合并 build/_plan。

    2026-11-06 起**禁止降级**：原来「数据失败 / 出图失败 → 悄悄换成 guide 文字卡」
    属于次品路径，违背用户最高声明。现在任何失败都显式上抛，由分发层记录日志并
    如实告知；缺真实武器/元素图标由 ``tenet_card_data`` 与模板直接 ``raise``。
    """
    from .. import pic as pic_mod
    from ..media import send_pic

    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = ("星际战甲·佩兰数列信条武器" if kind == "tenet"
             else "星际战甲·终幕信条武器")
    data = await warframe.tenet_card_data(kind)
    if not data:
        raise RuntimeError("信条/终幕 worldstate 不可用：不回退文字卡（禁止降级）")

    _src = data.get("source", "wf.wiki")
    _ts = data.get("snapshot", "")
    foot = "数据源：%s" % _src
    if _ts:
        foot = "数据源：%s · 快照 %s" % (_src, _ts)
    # 两个口令各自独立模板：终幕走参考图两列版，信条走单列粉紫版。
    tpl = "coda" if kind == "coda" else "tenet"
    _t0 = asyncio.get_running_loop().time()
    png = await asyncio.wait_for(
        pic_mod.render(tpl, theme_name=None, card=title,
                       data=data, source=foot),
        timeout=20.0)
    _flog.info(_jlog("gt_render_done", kind=kind,
                     elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
    await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)


async def _send_arbitration_table(bot: Bot, event: MessageEvent, arg: str = "") -> None:
    """仲裁时间表专属卡：pic 模板 arbitration_table（Rabbit 版式）→ guide 文本 → 纯文本，逐级降级。

    对照 Warframe Rabbit「仲裁时间表」：5 列班次表（时间/任务/地图名/生息效率/评级）+ 脚注。
    长图模式（无分页）："" / "所有"(all/全部) / "-N" 等价，统一输出全量轮换班次（21 行）。
    """
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = "星际战甲·仲裁时间表"
    try:
        data = await warframe.arbitration_table_data(arg)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_arb_table_data_failed: {type(e).__name__}: {e}")
        data = {"rows": [], "title": "仲裁时间表",
                "footer": "仲裁数据暂时没拉到，稍后再试～", "page": "", "hint": ""}
    if data.get("rows"):
        try:
            _t0 = asyncio.get_running_loop().time()
            png = await asyncio.wait_for(
                pic_mod.render("arbitration_table", theme_name=None, card=title,
                               title=data.get("title", "仲裁时间表"),
                               rows=data["rows"], footer=data.get("footer", ""),
                               page=data.get("page", ""), hint=data.get("hint", "")),
                timeout=20.0)
            _flog.info(_jlog("gt_render_done", kind="arbitration_table",
                             elapsed="%.2f" % (asyncio.get_running_loop().time() - _t0)))
            await asyncio.wait_for(send_pic(ctx, png), timeout=60.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_arb_table_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_arb_table_render_failed", err=type(e).__name__,
                                detail=str(e)[:160]))
    # 降级：文本表（arbitration_table_text 与 data 同源）
    try:
        txt = await warframe.arbitration_table_text(arg)
    except Exception:  # noqa: BLE001
        txt = "仲裁时间表数据暂时没拉到，稍后再试～"
    await _send_guide(bot, event, title, txt)


async def _send_plain(bot: Bot, event: MessageEvent) -> None:
    """平原状态和时间专属卡（逐像素复刻 Rabbit 参考卡）→ guide 文本降级。"""
    from .. import pic as pic_mod
    from ..media import send_pic
    ctx = {
        "user_id": getattr(event, "user_id", 0),
        "group_id": getattr(event, "group_id", 0),
        "channel": getattr(event, "channel", "napcat"),
    }
    title = "星际战甲·平原状态"
    try:
        data = await warframe.daynight_data()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gt_plain_data_failed: {type(e).__name__}: {e}")
        data = {"rows": [], "source": ""}
    if data.get("rows"):
        try:
            png = await asyncio.wait_for(
                pic_mod.render("plain", theme_name=None, card=title,
                               rows=data["rows"], title="平原状态和时间",
                               footer_name="曼珠"),
                timeout=20.0)
            await asyncio.wait_for(send_pic(ctx, png), timeout=20.0)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gt_plain_render_failed: {type(e).__name__}: {e}")
            _flog.warning(_jlog("gt_plain_render_failed", err=type(e).__name__,
                               detail=str(e)[:160]))
    # 降级：纯文本（daynight_text 与 daynight_data 同源）
    await _send_guide(bot, event, title, await warframe.daynight_text(""))


async def _handle(bot: Bot, event: MessageEvent) -> None:
    # ---- AI 模式门禁（2026-10-19）：开着大模型陪聊的会话，本模块只接命令 ----
    # 本模块是独立 matcher（priority=5, block=False），与主流程并行收消息。
    # 线上事故：群里发 "hi"，主流程 AI 陪聊回完，本模块又因 "hi" 是
    # rhino prime 的子串（r-HI-no）误触发战甲卡——一条消息两条回复。
    # 口径与主流程 _route 的 AI 拦截一致：PREFIXES（#/＃//）开头的显式命令
    # 仍走本模块；其余消息（含带图闲聊）一律让位给大模型，从根上杜绝双重回复。
    # 纯图无文字不拦——@机器人+紫卡图 的显式分析流在 AI 模式下仍可用
    # （主流程对无文字消息静默，不会双重回复）。
    _ai_text = _plain_text(event)
    if _ai_text and not _ai_text.startswith(PREFIXES):
        _scope = event_scope(event)
        if ai_mode_store.is_on(_scope) and not is_game_command(_ai_text):
            _flog.info(_jlog("gt_ai_mode_skip", scope=_scope,
                             user_id=getattr(event, "user_id", 0),
                             group_id=getattr(event, "group_id", 0)))
            return

    # ---- 紫卡截图分析：发紫卡图（@机器人 / 文本含关键字 / 已提前说「紫卡分析」预约）----
    try:
        imgs = _extract_images(event)
        if imgs:
            _raw = _plain_text(event)
            _kw = bool(re.search(r"紫卡|裂罅|riven", _raw, re.I))
            _pending = _pop_riven_wait(event)
            # 「紫卡全面」预约必须先 pop：纯图那条 text_len=0、无 @、无关键字，
            # 只能靠预约把分支拉进来（线上 19:43:38 日志实证）。
            _full_pending = _pop_riven_full_wait(event)
            _full_kw = bool(re.search(r"紫卡全面|全面分析|紫卡完整|完整分析", _raw, re.I))
            # 「全面分析」口令 + 图片 走与「紫卡分析」+ 图片完全相同的入口：
            # 关键字命中（带前缀排除，避免与 #紫卡全面 <武器> 文本命令重复触发）
            # 即直接进入 OCR 全量分析，而不是落到下面 1788 行的「等图预约」。
            if (_at_bot(event, bot) or _pending or _full_pending
                    or (_kw and not _raw.startswith(PREFIXES))
                    or (_full_kw and not _raw.startswith(PREFIXES))):
                data = await _download_riven_image(imgs[0])
                if data and (_full_kw or _full_pending):
                    # 「紫卡全面」+ 截图 → 真实 OCR 全量分析卡（像素级复刻版式）
                    try:
                        _parsed = await asyncio.wait_for(
                            riven.riven_img_parse(data), timeout=60.0)
                        if _parsed:
                            await _send_riven_full_parsed(bot, event, _parsed)
                        else:
                            await bot.send(event,
                                           "没识别出紫卡内容，换张清晰点的截图试试～"
                                           "（本卡不编造词条数值）")
                    except Exception as _e:  # noqa: BLE001 — 显式报错，绝不静默
                        logger.warning(f"gt_riven_full_ocr_failed: {type(_e).__name__}: {_e}")
                        await bot.send(event, "紫卡全面分析失败：%s: %s"
                                       % (type(_e).__name__, _e))
                    await _riven_recall_queue(bot, event)
                    return
                if data:
                    # 排队话术（对齐 Warframe Rabbit：先回一句再算，覆盖模拟耗时）。
                    # 记下 message_id，待结果（成败皆可）发出后自动撤回，保持聊天整洁。
                    try:
                        _qr = await bot.send(event, "已加入分析队列，里面正噼里啪啦算数值，稍等！")
                        _qmid = int((_qr or {}).get("message_id") or 0) if isinstance(_qr, dict) else 0
                        if _qmid:
                            _riven_queue_ids.setdefault(_riven_scope_key(event), []).append(_qmid)
                    except Exception as _e:  # noqa: BLE001 — 排队话术失败不阻断出卡
                        logger.warning(f"game_tools_riven_queue_msg_failed: {type(_e).__name__}: {_e}")
                    try:
                        rv, _card = await riven.riven_img_analyze_full(data)
                        await _send_guide(bot, event, "星际战甲·紫卡评估", rv,
                                          card_data=_card)
                    except Exception as _e:  # noqa: BLE001 — 显式报错，绝不静默
                        logger.warning(f"game_tools_riven_analyze_failed: "
                                       f"{type(_e).__name__}: {_e}")
                        await bot.send(event, "紫卡分析出错了：%s: %s"
                                       % (type(_e).__name__, str(_e)[:120]))
                    finally:
                        # 结果已出 → 撤回排队占位消息，避免聊天记录里留过期提示
                        await _riven_recall_queue(bot, event)
                    try:
                        event.get_matcher().stop_propagation()  # 防主流程当图生图
                    except Exception:
                        pass
                    return
    except Exception as e:  # noqa: BLE001 — 图片旁路，绝不拖垮消息循环
        logger.warning(f"game_tools_riven_img_failed: {type(e).__name__}: {e}")

    text = _plain_text(event)
    try:
        _segs = str([(s.type, dict(s.data or {})) for s in _event_segments(event)])[:200]
    except Exception:
        _segs = "?"
    _flog.info(_jlog("gt_msg", user_id=getattr(event, "user_id", 0),
                      group_id=getattr(event, "group_id", 0),
                      text_len=len(text), text=text[:30], segs=_segs))
    if not text:
        return

    # 「紫卡全面」预约：必须放在 _match_natural 之前，否则「紫卡全面分析」
    # 会被更短的「紫卡」前缀吞掉 → cmd=紫卡 arg=全面分析 → 跑去查市价。
    if text in _FULL_WAIT_WORDS:
        _set_riven_full_wait(event)
        try:
            await bot.send(event, "已登记紫卡全面分析，请直接发送紫卡截图～"
                                  "（真实 OCR 词条/评分/击杀效率，不编造数值）")
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"gt_riven_full_wait_msg_failed: {type(_e).__name__}: {_e}")
        _flog.info(_jlog("gt_riven_full_wait_set",
                         user_id=getattr(event, "user_id", 0)))
        return

    # 紫卡截图分析预约：先说「紫卡分析」（不带图、无参数），下一张图自动进入 OCR。
    if text in ("紫卡分析", "紫卡", "裂罅分析") or text.lower() in ("riven分析", "riven"):
        _set_riven_wait(event)
        try:
            _wr = await bot.send(event, "已加入分析队列，请直接发送紫卡截图，里面正噼里啪啦算数值，稍等！")
            _wmid = int((_wr or {}).get("message_id") or 0) if isinstance(_wr, dict) else 0
            if _wmid:
                # 占位消息记下，等用户真发图、分析完成后一并撤回
                _riven_queue_ids.setdefault(_riven_scope_key(event), []).append(_wmid)
        except Exception as _e:  # noqa: BLE001 — 发送失败不影响预约状态
            logger.warning(f"game_tools_riven_wait_msg_failed: {type(_e).__name__}: {_e}")
        return

    if text[0] in PREFIXES:
        body = text[1:].strip()
        if not body:
            return
        parts = body.split(None, 1)
        cmd = parts[0].strip()
        arg = parts[1].strip() if len(parts) > 1 else ""
        # 命令词紧跟参数无空格（如 #计划1 / #下发计划1 / #plan1）也支持
        if not arg:
            _glued = _split_glued_cmd(cmd, _PLAN_CMD_WORDS + _DISPATCH_CMD_WORDS)
            if _glued:
                cmd, arg = _glued
    else:
        hit = _match_natural(text)
        if not hit:
            # 明确命令词免前缀：@机器人 计划 / 计划 也直接命中（首词精确匹配防误触）
            _parts = text.strip().split(None, 1)
            _first = _parts[0] if _parts else ""
            _all_words = _PLAN_CMD_WORDS + _DISPATCH_CMD_WORDS
            if len(_parts) > 1 and _first.lower() in _all_words:
                # 有空格分隔：首词精确命中即可（计划 <序号|ID> / 下发计划 <序号|ID>）
                hit = (_first.lower(), _parts[1].strip())
            else:
                # 无空格：尝试拆解「命令词+粘连参数」（计划1 / 下发计划1 / plan1），否则不触发
                _glued = _split_glued_cmd(_first, _all_words)
                if _glued:
                    hit = _glued
                else:
                    return
        cmd, arg = hit

    _flog.info(_jlog("gt_match", cmd=cmd, arg=arg[:20]))

    # 裂隙任务订阅（2026-11-05）：必须在 dispatch 之前拦截 —— 这些口令 dispatch
    # 没有分支，落到「if not reply: return」会静默无响应。
    _ts = _task_sub_intent(cmd, arg)
    if _ts:
        await _handle_task_sub(bot, event, _ts[0], _ts[1])
        return
    try:
        # 角色图鉴：优先发 Atlas 成品攻略图（米游社攻略组出品，效果同 Yunzai 生态），
        # 无库/未命中（鸣潮、异环等）回落 wiki 富卡片
        if cmd in ("角色", "图鉴", "查角色"):
            if not arg:
                await _send_guide(bot, event, "角色图鉴",
                                  "格式：角色 角色名（例：角色 胡桃 / 角色 流萤 星铁）\n"
                                  "支持外号：雷神、草神、岩王爷、鲨鱼妹、鸭鸭、将军……\n"
                                  "原神/星铁/绝区零发攻略组成品图；鸣潮/异环发 wiki 汇总卡。")
                return
            game, name = _split_game(arg)
            nm = data.norm_name(name or arg)
            from ..media import send_image

            guide_url = await atlas.guide_image_url(game, nm)
            if guide_url:
                try:
                    await send_image({
                        "user_id": getattr(event, "user_id", 0),
                        "group_id": getattr(event, "group_id", 0),
                        "channel": getattr(event, "channel", "napcat"),
                    }, guide_url)
                    logger.info(f"atlas_guide_sent game={game or 'ys'} name={nm}")
                    return
                except Exception as e:  # noqa: BLE001 — 图片发送失败回落 wiki 卡
                    logger.warning(f"atlas_send_failed: {type(e).__name__}: {e}")
            body_text, avatar_url = await wiki.character_summary(nm, game or "ys")
            await _send_guide(bot, event, "角色图鉴", body_text, avatar_url)
            return

        # 模拟开卡：优先发游戏紫卡样式图（失败自动降级 dispatch 文本）
        if cmd in ("模拟开卡", "开卡"):
            _riven_png = await warframe.render_sim_riven()
            if _riven_png:
                from ..media import send_pic as _send_pic
                _ctx = {
                    "user_id": getattr(event, "user_id", 0),
                    "group_id": getattr(event, "group_id", 0),
                    "channel": getattr(event, "channel", "napcat"),
                }
                try:
                    await asyncio.wait_for(_send_pic(_ctx, _riven_png), timeout=20.0)
                    _flog.info(_jlog("gt_sim_riven_sent", bytes=len(_riven_png)))
                    return
                except Exception as _e:  # noqa: BLE001
                    logger.warning(f"game_tools_sim_riven_send_failed: {type(_e).__name__}: {_e}")
        # 计划单独下发（真实提交生图，需 event 的 user/group，在 dispatch 之前特判）
        if cmd.lower() in ("下发计划", "下发", "runplan", "dispatch"):
            await _handle_dispatch_plan(bot, event, arg)
            return
        reply = await dispatch(cmd, arg, _sk(event))
    except Exception as e:  # noqa: BLE001 — 工具是旁路，绝不拖垮消息循环
        logger.error(f"game_tools_error cmd={cmd} error={type(e).__name__}: {e}")
        reply = "这个功能刚才出错了，已记录日志，稍后再试～"
        avatar_url = ""
        if cmd in GUIDE_TITLES or cmd in ("角色", "图鉴", "查角色"):
            await _send_guide(bot, event, GUIDE_TITLES.get(cmd, "游戏工具箱"), reply)
            return
        try:
            await bot.send(event, reply[:1500])
        except Exception as e2:  # noqa: BLE001
            logger.error(f"game_tools_send_failed: {type(e2).__name__}: {e2}")
        return

    # MOD 卡图：wf 命令若命中 MOD，文字资料之外再补一张忠实复刻的 mod 卡图
    if cmd in ("wf", "WF", "星际战甲", "战甲"):
        await _maybe_send_mod_card(bot, event, arg)

    # 自定义专属卡片口令：不依赖 dispatch 的 reply（这些口令 dispatch 无分支，
    # reply 为空），必须在「if not reply: return」之前拦截，否则静默无响应。
    # 灵化虽在 dispatch 里有纯文本分支，但要出专属卡 → 同样在此拦截（2026-10-22）。
    if cmd in ("灵化", "灵化武器", "灵化轮换"):
        await _send_incarnon(bot, event)
        return
    if cmd in ("科研", "科研任务", "netracell"):
        await _send_netracell(bot, event)
        return
    if cmd in ("裂隙", "普通裂隙", "钢铁裂隙"):
        _fm = {"普通裂隙": "normal", "钢铁裂隙": "steel"}.get(cmd, "quick")
        await _send_fissure(bot, event, _fm)
        return
    if cmd in ("赏金",):
        await _send_bounty(bot, event, steel=False)
        return
    if cmd in ("钢铁赏金", "钢铁之路", "钢路", "钢铁"):
        await _send_bounty(bot, event, steel=True)
        return
    if cmd in ("信条", "tenet") and not arg:
        await _send_tenet(bot, event, "tenet")
        return
    if cmd in ("终幕", "coda") and not arg:
        await _send_tenet(bot, event, "coda")
        return
    if cmd in ("仲裁表",):
        await _send_arbitration_table(bot, event, arg)
        return
    if cmd in ("平原",):
        await _send_plain(bot, event)
        return
    # 配卡图片卡：裸口令「配卡 奏凯」或「#wf 配卡 奏凯」均走此分支（出横版卡）。
    if cmd in ("配卡", "build", "配置") or (
            cmd in ("wf", "WF") and arg.split()
            and arg.split()[0].lower() in ("配卡", "build", "配置")):
        _bw = arg if cmd in ("配卡", "build", "配置") else " ".join(arg.split()[1:])
        await _send_build(bot, event, _bw)
        return
    # 紫卡洗卡词条推荐卡：裸口令「词条推荐 凤殁」「洗卡推荐 绝路」或「#wf 推荐 凤殁」。
    if cmd in ("词条推荐", "洗卡推荐", "紫卡推荐") or (
            cmd in ("wf", "WF", "星际战甲", "战甲") and arg.split()
            and arg.split()[0].lower() in ("推荐", "词条推荐", "洗卡推荐", "紫卡推荐")):
        _rw = arg if cmd in ("词条推荐", "洗卡推荐", "紫卡推荐") else " ".join(arg.split()[1:])
        await _send_riven_recommend(bot, event, _rw)
        return
    # 警报任务卡：裸口令「警报」或「#wf 警报」「#警报」
    if cmd in ("警报", "alerts", "alert") or (
            cmd in ("wf", "WF", "星际战甲", "战甲") and arg.split()
            and arg.split()[0].lower() in ("警报", "alerts", "alert")):
        await _send_alerts(bot, event)
        return
    # 本周执刑官猎杀（2026-11-05）：Rabbit 参考图逐像素卡
    if cmd in ("执政官", "执刑官", "执行官", "猎杀", "执政官猎杀",
               "执刑官猎杀", "执行官猎杀", "archon", "Archon") or (
            cmd in ("wf", "WF", "星际战甲", "战甲") and arg.split()
            and arg.split()[0].lower() in ("执政官", "执刑官", "执行官", "猎杀",
                                           "执政官猎杀", "执刑官猎杀", "archon")):
        await _send_archon(bot, event)
        return
    # 紫卡趋势 / 词条价差（2026-11-05 P2）：WFM 真实挂牌，本地采样落盘
    _tr_words = ("紫卡趋势", "riven趋势")
    _sp_words = ("词条价差", "紫卡价差", "紫卡词条价差")
    _tr_arg = arg
    if cmd in ("wf", "WF", "星际战甲", "战甲") and arg.split():
        _head, _tail = arg.split()[0], " ".join(arg.split()[1:])
        if _head in _tr_words:
            cmd, _tr_arg = "紫卡趋势", _tail
        elif _head in _sp_words:
            cmd, _tr_arg = "词条价差", _tail
    if cmd in _tr_words:
        await _send_riven_trend(bot, event, "trend", _tr_arg)
        return
    if cmd in _sp_words:
        await _send_riven_trend(bot, event, "spread", _tr_arg)
        return
    # 紫卡融合（2026-11-05 P1）：WFM 真实中位价 + Wiki 融合费用表，武器参数可选
    _fusion_words = ("紫卡融合", "riven融合")
    _fusion_arg = arg
    if cmd in ("wf", "WF", "星际战甲", "战甲") and arg.split():
        _fhead, _ftail = arg.split()[0], " ".join(arg.split()[1:])
        if _fhead in _fusion_words:
            cmd, _fusion_arg = "紫卡融合", _ftail
    if cmd in _fusion_words:
        await _send_riven_fusion(bot, event, _fusion_arg)
        return
    # 紫卡全面分析卡（2026-11-06）
    _full_words = ("紫卡全面", "紫卡全面分析", "全面分析")
    _full_arg = arg
    if cmd in ("wf", "WF", "星际战甲", "战甲") and arg.split():
        _fhead, _ftail = arg.split()[0], " ".join(arg.split()[1:])
        if _fhead in _full_words + ("紫卡完整", "完整分析"):
            cmd, _full_arg = "紫卡全面", _ftail
    if cmd in _full_words:
        await _send_riven_full(bot, event, _full_arg)
        return
    if not reply:
        _flog.info(_jlog("gt_dispatch_empty", cmd=cmd))
        return
    _flog.info(_jlog("gt_dispatch", cmd=cmd, rlen=len(reply)))
    if cmd in GUIDE_TITLES:
        _avatar = ""
        if cmd in ("wf", "WF", "星际战甲", "战甲"):
            # 战甲子命令/别名命中 wf 表 → 带半身像；MOD/赋能/其它返回 None 零外呼
            try:
                _avatar = await warframe._portrait_path(arg) or ""
            except Exception:
                _avatar = ""
        await _send_guide(bot, event, GUIDE_TITLES[cmd], reply, avatar_url=_avatar)
    else:
        # WF 别名/资料兜底：直接说「水男/毒妈/充沛」也发卡片
        _is_wf = False
        _is_wf_tbl = ""
        try:
            for _tbl in ("wf", "mod", "arc"):
                if warframe._resolve_card(cmd, warframe._load_cards()[_tbl], exact_only=True):
                    _is_wf = True
                    _is_wf_tbl = _tbl
                    break
        except Exception:
            _is_wf = False
        if _is_wf:
            _avatar = ""
            if _is_wf_tbl == "wf":
                try:
                    _avatar = await warframe._portrait_path(cmd) or ""
                except Exception:
                    _avatar = ""
            await _send_guide(bot, event, "星际战甲·资料", reply, avatar_url=_avatar)
            return
        try:
            await bot.send(event, reply[:1500])
        except Exception as e:  # noqa: BLE001
            logger.error(f"game_tools_send_failed: {type(e).__name__}: {e}")


async def dispatch(cmd: str, arg: str, sk: str) -> str:
    """命令分发：返回要回复的文本（空串表示不回复）。角色命令在 _handle 特判。"""
    if cmd in ("帮助", "菜单", "help", "功能", "工具"):
        return HELP

    # ---------------- 生图计划（草稿）查看（2026-09-28 新增）----------------
    if cmd in ("计划", "我的计划", "草稿计划", "plan", "plans"):
        if arg:
            return await _plan_view_detail(arg)
        return await _plan_view_list()

    # ---------------- 生图计划明细详情下钻（2026-09-28 新增：#明细 看全部条目）----------------
    if cmd in ("明细", "明细计划", "plandetail"):
        return await _plan_detail_full(arg)

    # ---------------- 二游攻略 ----------------
    if cmd in ("配队", "队伍", "组队"):
        if not arg:
            return "格式：配队 角色名/外号（例：配队 忌炎 / 配队 雷神）"
        game, kw = _split_game(arg)
        hits = data.find_teams(kw or arg, game)
        if not hits:
            alias = data.ALIAS.get((kw or "").strip(), "")
            hint = f"（「{kw}」按「{alias}」也没找到）" if alias else ""
            return (f"没找到含「{kw}」的配队。{hint}\n"
                    f"支持的例子：胡桃、雷神、卡芙卡、黄泉、艾莲、忌炎……\n"
                    f"异环公测初期请用「角色 <角色名> 异环」查 wiki 实时数据。")
        blocks = []
        for g, t in hits[:4]:
            tip = data.team_tips(t["name"])
            block = (f"【{data.GAMES.get(g, g)}】{t['name']}\n"
                     f"成员：{t['members'].replace('/', ' / ')}\n"
                     f"思路：{t['desc']}")
            if tip:
                block += f"\n手法/装备：{tip}"
            blocks.append(block)
        return "\n\n".join(blocks)

    if cmd in ("养成", "培养", "毕业"):
        if not arg:
            return "格式：养成 角色名/定位（例：养成 忌炎 / 养成 主C）"
        game, kw = _split_game(arg)
        hits = data.find_build(kw or arg, game)
        if hits:
            return "\n\n".join(f"【{data.GAMES.get(g, g)}·{c}】\n{t}" for g, c, t in hits[:4])
        gen = data.generic_build(kw or arg, game)
        if gen:
            lines = [f"【{data.GAMES.get(g, g)}·{r}】{t}" for g, r, t in gen[:6]]
            return "没收录该角色的详细养成，给你通用路线：\n" + "\n".join(lines)
        return "没找到养成信息，试试角色全名或定位（主C/副C/辅助/生存/击破/支援）"

    if cmd in ("副本", "深渊", "混沌", "混沌回忆", "危局", "当期"):
        game, _ = _split_game(arg)
        text, _avatar = await wiki.abyss_summary(game or "ys")
        return text

    if cmd in ("活动", "活动一览", "新活动"):
        game, _ = _split_game(arg)
        text, _avatar = await wiki.event_summary(game or "ys")
        return text

    # ---------------- 星际战甲（国际服，2026-09-16 新增）----------------
    if cmd in ("wf", "WF", "星际战甲", "战甲"):
        return await _wf_dispatch(arg)
    if cmd in ("突击",):
        return await warframe.sortie_text()
    if cmd in ("奸商", "虚空商人"):
        return await warframe.void_trader_text()
    if cmd in ("特惠", "每日特惠"):
        return await warframe.daily_deals_text()
    if cmd in ("裂隙",):
        return await warframe.fissures_text()
    if cmd in ("入侵",):
        return await warframe.invasions_text()
    if cmd in ("仲裁",):
        return await warframe.arbitration_text()
    if cmd in ("电波",):
        return await warframe.nightwave_text()
    if cmd in ("平原",):
        return await warframe.daynight_text("")
    if cmd in ("钢铁",):
        return ("钢铁之路（Steel Path）：通关全部星图节点后解锁，敌人 +100 等级与修正，\n"
                " 掉落带钢铁精华（Steel Essence），可换 Teshin 商店物品。\n"
                "当前数据源未直接暴露钢铁之路活动，详情 #wiki 钢铁之路")
    if cmd in ("价格", "市价", "wm", "WM", "市场"):
        _m = (arg or "").strip()
        if _m in ("分类", "类别", "category"):
            return warframe.categories_text()
        _pg = 1
        _mm = __import__("re").search(r"(.*?)\s*-\s*(\d+)\s*$", _m)
        if _mm:
            _m, _pg = _mm.group(1).strip(), int(_mm.group(2))
        return await warframe.market_text(_m, _pg)
    if cmd in ("紫卡",):
        return await warframe.riven_text(arg)
    if cmd in ("紫卡趋势", "riven趋势"):
        return "紫卡趋势需出图：发「紫卡趋势 <武器>」（WFM 挂牌 + 本地采样，非成交价）"
    if cmd in ("词条价差", "紫卡价差", "紫卡词条价差"):
        return "词条价差需出图：发「词条价差 <武器>」（同等级 rank8 + 同分位对称对比）"
    if cmd in ("紫卡融合", "riven融合"):
        return "紫卡融合需出图：发「紫卡融合 [武器]」（WFM 真实中位价 + Wiki 融合费用表，武器可空）"
    if cmd in ("紫卡分析", "分析"):
        return await warframe.riven_analysis_text(arg)
    if cmd in ("紫卡全面", "紫卡全面分析", "全面分析"):
        return ("紫卡全面分析：#紫卡全面 <武器> / #紫卡全面 样例 / 发紫卡截图 + #紫卡全面\n"
                "输出 655×1920 完整分析卡；词条依赖截图 OCR，不编造数值。")
    if cmd in ("倾向",):
        return await warframe.riven_disposition_text(arg)
    if cmd in ("赤毒", "信条"):
        return await warframe.market_text(arg, 1)
    if cmd in ("wiki",):
        return warframe.wiki_text(arg)
    if cmd in ("翻译",):
        return warframe.translate_text(arg)
    if cmd in ("金垃圾",):
        return await warframe.junk_text()
    if cmd in ("赏金",):
        return await warframe.bounties_text()
    if cmd in ("钢铁赏金", "钢铁之路", "钢路", "钢铁"):
        return await warframe.steel_bounties_text()
    if cmd in ("灵化", "灵化武器", "灵化轮换"):
        return warframe.incarnon_text()
    if cmd in ("科研", "科研任务", "netracell"):
        return await warframe.netracell_text()
    if cmd in ("九重天",):
        return await warframe.voidstorms_text()
    if cmd in ("周本",):
        return warframe.weekly_text()
    if cmd in ("执政官", "执刑官", "执行官", "猎杀", "执政官猎杀",
               "执刑官猎杀", "执行官猎杀", "archon", "Archon"):
        return await warframe.archon_hunt_text()
    if cmd in ("轮换",):
        return await warframe.rotation_text()

    # ---------------- 小游戏 ----------------
    if cmd in ("猜数字", "猜数"):
        return await games.guess_start(sk)
    if cmd == "猜":
        return await games.guess_play(sk, arg)
    if cmd in ("抽签", "运势", "今日运势", "运气", "仓鼠运势"):
        return await warframe.hamster_fortune_text(arg)
    if cmd in ("黄历", "仓鼠黄历", "黄道吉日"):
        return await warframe.almanac_text()
    if cmd in ("模拟开卡", "开卡"):
        return await warframe.sim_riven_text()
    if cmd in ("接龙", "成语接龙"):
        return await games.idiom(sk, arg)
    if cmd in ("24点", "24"):
        return await games.game24_play(sk, arg) if arg else await games.game24_start(sk)
    if cmd in ("答题", "问答"):
        return await games.quiz_start(sk)
    if cmd in ("答", "作答"):
        return await games.quiz_play(sk, arg)
    if cmd in ("结束", "认输", "放弃", "退出"):
        return await games.stop(sk)

    # WF 别名/资料名兜底：直接说「毒妈/咖喱棒/聚精会神/充沛」也能查
    # 先精确三表（充沛→赋能而非「充沛射击」MOD），再无精确时包含匹配
    try:
        for _tbl, _fn in (("wf", warframe.warframe_info_text),
                          ("mod", warframe.mod_text),
                          ("arc", warframe.arcane_text)):
            if warframe._resolve_card(cmd, warframe._load_cards()[_tbl], exact_only=True):
                return await _fn(cmd)
        for _tbl, _fn in (("wf", warframe.warframe_info_text),
                          ("mod", warframe.mod_text),
                          ("arc", warframe.arcane_text)):
            if warframe._resolve_card(cmd, warframe._load_cards()[_tbl]):
                return await _fn(cmd)
    except Exception:
        pass

    return ""


# ===================== 生图计划（草稿）查看（2026-09-28 新增）=====================
# 只读查询，无创：只展示 planstore（草稿）与 queue（执行批次）的现有数据，不做任何写操作。
def _plan_fmt_ts(ts: float) -> str:
    """Unix 秒 → 本地『YYYY-MM-DD HH:MM』；非法/0 → 『—』。"""
    if not ts:
        return "—"
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


_PLAN_BUCKET_ZH = {
    "running": "运行中", "queued": "排队中",
    "partial": "已执行未完成", "failed": "失败", "done": "已完成",
}
_PLAN_STATE_ZH = {
    "queued": "⏳排队", "running": "🔄运行",
    "done": "✅完成", "failed": "❌失败", "canceled": "🚫取消",
}


def _plan_prog_s(prog) -> str:
    """进度百分比：整数去小数（50.0→50），非整数保留（33.3→33.3）。"""
    if isinstance(prog, (int, float)):
        if float(prog).is_integer():
            return str(int(prog))
        return ("%g" % prog)
    return str(prog)


async def _plan_view_list() -> str:
    """列出全部草稿计划 + 状态摘要（定时 + 最近一次执行归类）。"""
    plans = planstore.plan_mgr.list()
    if not plans:
        return ("📋 还没有任何草稿计划。\n"
                "在 Web 面板「计划」Tab 建好草稿后，这里就能查看状态～")
    # 计划 ID → 最近一次执行批次（按开始时间倒序，取最新）
    by_plan: dict = {}
    for g in queue.queue_mgr.batches_summary():
        pid = g.get("plan_id") or ""
        if pid:
            by_plan.setdefault(pid, []).append(g)
    lines = ["📋 我的生图计划（草稿）共 %d 个：" % len(plans)]
    for i, p in enumerate(plans, 1):
        pid = p.get("plan_id")
        name = p.get("name") or "未命名计划"
        ch = "在线" if (p.get("channel") or "local") == "online" else "本地"
        try:
            items = len(json.loads(p.get("json_text") or "[]")) \
                if isinstance(p.get("json_text"), str) else 0
        except Exception:
            items = 0
        if p.get("schedule_enabled"):
            at = float(p.get("schedule_at") or 0)
            rep = "每天" if p.get("schedule_repeat") == "daily" else "一次"
            sched = "定时:%s %s" % (rep, _plan_fmt_ts(at))
        else:
            sched = "定时:未设"
        gs = sorted(by_plan.get(pid, []),
                    key=lambda x: float(x.get("started_at") or x.get("created_at") or 0),
                    reverse=True)
        if gs:
            g0 = gs[0]
            exec_s = "· 执行 %d/%d完成(%s)" % (
                g0.get("done", 0), g0.get("total", 0),
                _PLAN_BUCKET_ZH.get(g0.get("bucket"), g0.get("bucket")))
        else:
            exec_s = "· 尚未下发"
        lines.append("%d. %s 〔%s〕条目%d %s %s" % (i, name, ch, items, sched, exec_s))
    lines.append("—— 看某计划明细：#计划 <序号或计划ID>")
    return "\n".join(lines)


def _resolve_plan_pid(arg: str):
    """把 <序号 或 计划ID> 解析成 plan_id；解析不出返回 None。

    · 6+ 位 hex → 按 ID 取（须真实存在）；· 纯数字 → 列表序号（1 基）；· 其它 → None。
    被 _plan_view_detail / _plan_detail_full / _plan_dispatch 共用，避免三处重复解析。
    """
    arg = (arg or "").strip()
    if not arg:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{6,}", arg):
        if planstore.plan_mgr.get(arg):
            return arg
    if arg.isdigit():
        plans = planstore.plan_mgr.list()
        idx = int(arg)
        if 1 <= idx <= len(plans):
            return plans[idx - 1].get("plan_id")
    return None


async def _plan_view_detail(arg: str) -> str:
    """某草稿计划的明细：定时 + 执行进度 + 前若干条条目状态。"""
    arg = (arg or "").strip()
    pid = _resolve_plan_pid(arg)
    if pid is None:
        if arg.isdigit():
            total = len(planstore.plan_mgr.list())
            return "序号超出范围（共 %d 个计划）。先发「#计划」看列表～" % total
        return ("用法：#计划 <序号 或 计划ID>\n"
                "例：#计划 2   /   #计划 a1b2c3...\n"
                "先发「#计划」看列表与序号。")
    p = planstore.plan_mgr.get(pid)
    if not p:
        return "没找到这个计划（ID 不对或已删除）。"
    name = p.get("name") or "未命名计划"
    ch = "在线" if (p.get("channel") or "local") == "online" else "本地"
    created = _plan_fmt_ts(float(p.get("created_at") or 0)) if p.get("created_at") else "—"
    try:
        items = len(json.loads(p.get("json_text") or "[]")) \
            if isinstance(p.get("json_text"), str) else 0
    except Exception:
        items = 0
    lines = ["📋 计划明细：%s" % name,
             "· ID：%s" % pid,
             "· 通道：%s" % ch,
             "· 创建：%s" % created,
             "· 条目数：%d" % items]
    if p.get("schedule_enabled"):
        at = float(p.get("schedule_at") or 0)
        rep = "每天" if p.get("schedule_repeat") == "daily" else "单次"
        fired = _plan_fmt_ts(float(p.get("schedule_last_fired_at") or 0)) \
            if p.get("schedule_last_fired_at") else "无"
        lines.append("· 定时：已启用（%s）下次 %s" % (rep, _plan_fmt_ts(at)))
        lines.append("· 上次触发：%s" % fired)
    else:
        lines.append("· 定时：未启用")
    groups = sorted([g for g in queue.queue_mgr.batches_summary()
                     if (g.get("plan_id") or "") == pid],
                    key=lambda x: float(x.get("started_at") or x.get("created_at") or 0),
                    reverse=True)
    if not groups:
        lines.append("· 执行：尚未下发（Web 面板点「下发」或设好定时即可）")
        return "\n".join(lines)
    g = groups[0]
    lines.append("· 最近批次：%s" % (g.get("batch_id") or "—"))
    lines.append("· 执行统计：总 %d / 完成 %d / 失败 %d / 运行 %d / 排队 %d / 取消 %d" % (
        g.get("total", 0), g.get("done", 0), g.get("failed", 0),
        g.get("running", 0), g.get("queued", 0), g.get("canceled", 0)))
    lines.append("· 进度：%s%% · 归类：%s%s" % (
        _plan_prog_s(g.get("progress", 0)), _PLAN_BUCKET_ZH.get(g.get("bucket"), g.get("bucket")),
        "（已暂停）" if g.get("paused") else ""))
    started = _plan_fmt_ts(float(g.get("started_at") or 0)) if g.get("started_at") else "—"
    fin = _plan_fmt_ts(float(g.get("finished_at") or 0)) if g.get("finished_at") else "—"
    lines.append("· 开始：%s · 最近完成：%s" % (started, fin))
    bd = queue.queue_mgr.batch_detail(g.get("batch_id") or "")
    if bd.get("ok"):
        its = bd.get("items") or []
        top = its[:8]
        if top:
            lines.append("· 前 %d 条明细：" % len(top))
            for it in top:
                st = _PLAN_STATE_ZH.get(it.get("state"), it.get("state") or "—")
                pr = (it.get("prompt") or "").replace("\n", " ")[:22]
                lines.append("   %s %s" % (st, pr))
            if len(its) > len(top):
                lines.append("   …还有 %d 条" % (len(its) - len(top)))
    return "\n".join(lines)


async def _plan_detail_full(arg: str) -> str:
    """某计划的「全部条目」明细下钻（不限 8 条）：最近批次的每一条状态 + prompt + 图数。

    与 #计划 <ID>（摘要 + 前 8 条）区分——这里展示完整条目列表。
    """
    arg = (arg or "").strip()
    pid = _resolve_plan_pid(arg)
    if pid is None:
        return ("用法：#明细 <序号 或 计划ID>\n"
                "例：明细 2   /   明细 a1b2c3...\n"
                "先发「#计划」看列表与序号。")
    p = planstore.plan_mgr.get(pid)
    if not p:
        return "没找到这个计划（ID 不对或已删除）。"
    name = p.get("name") or "未命名计划"
    groups = sorted([g for g in queue.queue_mgr.batches_summary()
                     if (g.get("plan_id") or "") == pid],
                    key=lambda x: float(x.get("started_at") or x.get("created_at") or 0),
                    reverse=True)
    if not groups:
        return ("📋 计划明细(全部条目)：%s\n"
                "· 执行：尚未下发（Web 面板点「下发」或发「下发计划 %s」即可开始）"
                % (name, "<序号或ID>"))
    g = groups[0]
    bid = g.get("batch_id") or "—"
    lines = ["📋 计划明细(全部条目)：%s" % name,
             "· 批次：%s" % bid,
             "· 执行统计：总 %d / 完成 %d / 失败 %d / 运行 %d / 排队 %d / 取消 %d" % (
                 g.get("total", 0), g.get("done", 0), g.get("failed", 0),
                 g.get("running", 0), g.get("queued", 0), g.get("canceled", 0)),
             "· 进度：%s%% · 归类：%s%s" % (
                 _plan_prog_s(g.get("progress", 0)),
                 _PLAN_BUCKET_ZH.get(g.get("bucket"), g.get("bucket")),
                 "（已暂停）" if g.get("paused") else ""),
             "—— 全部 %d 条：" % g.get("total", 0)]
    bd = queue.queue_mgr.batch_detail(bid)
    if bd.get("ok"):
        for i, it in enumerate(bd.get("items") or [], 1):
            st = _PLAN_STATE_ZH.get(it.get("state"), it.get("state") or "—")
            pr = (it.get("prompt") or "").replace("\n", " ")[:40]
            imgs = len(it.get("images") or [])
            lines.append("   %d. %s %s%s" % (i, st, pr, (" 🖼%d" % imgs) if imgs else ""))
    else:
        lines.append("   （批次详情读取失败：%s）" % bd.get("error", ""))
    return "\n".join(lines)


async def _plan_dispatch(arg: str, user_id: int, group_id: int, scene: str) -> str:
    """计划单独下发（真实提交生图）：解析计划条目 → 入队 → 标记已运行。

    任何人可用，按请求者身份入队（与单张生图口令一致）。返回给用户的中文摘要。
    """
    pid = _resolve_plan_pid(arg)
    if pid is None:
        return ("用法：下发计划 <序号 或 计划ID>\n"
                "例：下发计划 2   /   下发计划 a1b2c3...\n"
                "先发「#计划」看列表与序号。")
    p = planstore.plan_mgr.get(pid)
    if not p:
        return "没找到这个计划（ID 不对或已删除）。"
    name = p.get("name") or "未命名计划"
    channel = str(p.get("channel") or "local").strip().lower() or "local"
    try:
        items = json.loads(p.get("json_text") or "[]")
    except Exception:
        return "该计划的条目数据损坏，无法下发（json_text 无法解析）。"
    if not isinstance(items, list) or not items:
        return ("该计划没有任何条目（json_text 为空），先在 Web 面板「计划」Tab 编辑内容再下发。"
                "（计划：%s）" % name)
    try:
        rv = await queue.queue_mgr.enqueue_batch(
            items, channel=channel, user_id=int(user_id or 0),
            group_id=int(group_id or 0), scene=scene or "private",
            name=name, plan_id=pid, scheduled_at=0)
    except Exception as e:  # noqa: BLE001 — 下发失败如实告知，不静默
        return "下发失败：%s" % (str(e)[:160])
    if not rv.get("ok"):
        return "下发失败：%s" % (rv.get("error") or "未知错误")
    bid = rv.get("batch_id") or ""
    total = rv.get("total", 0)
    accepted = rv.get("accepted", 0)
    rejected = rv.get("rejected", 0)
    errs = rv.get("errors") or []
    try:
        planstore.plan_mgr.mark_run(pid, bid)
    except Exception:  # noqa: BLE001 — 标记失败不影响已入队的任务
        pass
    lines = ["🚀 已下发计划：%s" % name,
             "· 批次：%s" % bid,
             "· 条目总数：%d" % total,
             "· 已入队：%d 条" % accepted,
             "· 通道：%s" % ("在线" if channel == "online" else "本地")]
    if rejected:
        lines.append("· 跳过/拒绝：%d 条" % rejected)
        for e in errs[:10]:
            lines.append("   - %s" % e)
    lines.append("—— 用「#明细 <序号或ID>」看全部条目执行状态，或「队列」看排队情况")
    return "\n".join(lines)


async def _handle_dispatch_plan(bot, event, arg):
    """计划下发入口（在 _handle 内、dispatch 之前特判，因为需要 event 的 user/group）。"""
    user_id = int(getattr(event, "user_id", 0) or 0)
    group_id = int(getattr(event, "group_id", 0) or 0)
    scene = "group" if group_id else "private"
    res = await _plan_dispatch(arg, user_id, group_id, scene)
    await _send_guide(bot, event, "生图计划·下发", res)


async def _wf_dispatch(arg: str) -> str:
    """星际战甲子命令分发：#wf <子命令> [参数]。"""
    a = (arg or "").strip()
    if not a:
        return warframe.help_text()
    parts = a.split(None, 1)
    sub = parts[0].strip().lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub in ("执政官", "执刑官", "执行官", "猎杀", "执政官猎杀",
               "执刑官猎杀", "执行官猎杀", "archon"):
        return await warframe.archon_hunt_text()
    m = {
        "帮助": warframe.help_text(), "help": warframe.help_text(), "菜单": warframe.help_text(),
        "突击": await warframe.sortie_text(),
        "奸商": await warframe.void_trader_text(), "虚空商人": await warframe.void_trader_text(),
        "特惠": await warframe.daily_deals_text(), "每日特惠": await warframe.daily_deals_text(),
        "裂隙": await warframe.fissures_text(),
        "入侵": await warframe.invasions_text(),
        "仲裁": await warframe.arbitration_text(),
        "仲裁表": await warframe.arbitration_table_text(rest),
        "电波": await warframe.nightwave_text(),
        "平原": await warframe.daynight_text(""),
        "赏金": await warframe.bounties_text(),
        "九重天": await warframe.voidstorms_text(),
        "警报": await warframe.alert_text(),
        "周本": warframe.weekly_text(),
        "轮换": await warframe.rotation_text(),
        "钢铁": ("钢铁之路：通关全部星图节点解锁，敌人 +100 级修正，掉落钢铁精华。\n"
                  "详情 #wiki 钢铁之路"),
    }
    if sub in m:
        return m[sub]
    if sub in ("价格", "市价", "price", "wm"):
        if (rest or "").strip() in ("分类", "类别", "category"):
            return warframe.categories_text()
        _pg = 1
        _mm = __import__("re").search(r"(.*?)\s*-\s*(\d+)\s*$", rest or "")
        if _mm:
            rest, _pg = _mm.group(1).strip(), int(_mm.group(2))
        return await warframe.market_text(rest, _pg)
    if sub in ("紫卡", "riven", "zk"):
        return await warframe.riven_text(rest)
    if sub in ("分类", "类别", "category"):
        return warframe.categories_text()
    if sub in ("紫卡分析", "分析", "评估"):
        return await warframe.riven_analysis_text(rest)
    if sub in ("紫卡趋势", "riven趋势", "词条价差", "紫卡价差"):
        return "紫卡趋势/词条价差请直接发「#紫卡趋势 <武器>」「#词条价差 <武器>」出图。"
    if sub in ("紫卡融合", "融合", "riven融合"):
        return "紫卡融合请直接发「#紫卡融合 [武器]」出图（WFM 真实中位价 + Wiki 融合费用表，武器可空）。"
    if sub in ("紫卡全面", "紫卡全面分析", "全面分析", "紫卡完整", "完整分析"):
        return "紫卡全面分析：发紫卡截图 + #紫卡全面，或 #紫卡全面 样例（非实时数据）。"
    if sub in ("推荐", "词条推荐", "洗卡推荐", "紫卡推荐"):
        # 出图命令在主 handler 拦截；此处仅作纯文本入口的用法提示
        return ("紫卡洗卡词条推荐：#词条推荐 <武器>（如 #词条推荐 凤殁）\n"
                "输出「击杀效率 + 市场挂牌分层」双维度推荐卡；融合词条实盘无属性字段，如实标不统计。")
    if sub in ("倾向",):
        return await warframe.riven_disposition_text(rest)
    if sub in ("赤毒", "kuva"):
        return await warframe.market_text(rest, 1)
    if sub in ("信条", "tenet"):
        return await warframe.market_text(rest, 1)
    if sub in ("终幕", "coda"):
        return await _send_tenet(bot, event, "coda")
    if sub in ("wiki", "维基"):
        return warframe.wiki_text(rest)
    if sub in ("翻译", "translate"):
        return warframe.translate_text(rest)
    if sub in ("战甲", "frame", "warframe"):
        return await warframe.warframe_info_text(rest)
    if sub in ("mod", "卡", "卡片"):
        return await warframe.mod_text(rest)
    if sub in ("赋能", "arcane"):
        return await warframe.arcane_text(rest)
    if sub in ("掉落", "drop"):
        return await warframe.drop_text(rest)
    if sub in ("遗物", "核桃", "relic"):
        return await warframe.relic_text(rest)
    if sub in ("配卡", "build", "配置"):
        return await warframe.build_text(rest)
    if sub in ("金垃圾", "垃圾", "杜卡德"):
        return await warframe.junk_text()
    if sub in ("结合目标", "大黄脸", "扫描", "synthesis"):
        return warframe.synthesis_text(rest)
    # 别名/黑话兜底：#wf 毒妈 / #wf 聚精会神 / #wf 充沛（精确优先再包含）
    q = rest or sub
    try:
        for _tbl, _fn in (("wf", warframe.warframe_info_text),
                          ("mod", warframe.mod_text),
                          ("arc", warframe.arcane_text)):
            if warframe._resolve_card(q, warframe._load_cards()[_tbl], exact_only=True):
                return await _fn(q)
        for _tbl, _fn in (("wf", warframe.warframe_info_text),
                          ("mod", warframe.mod_text),
                          ("arc", warframe.arcane_text)):
            if warframe._resolve_card(q, warframe._load_cards()[_tbl]):
                return await _fn(q)
    except Exception:
        pass
    return "未知的星际战甲命令：「%s」。说 #wf 看用法" % sub
