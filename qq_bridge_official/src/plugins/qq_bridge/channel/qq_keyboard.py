# -*- coding: utf-8 -*-
"""QQ 官方通道「消息按钮键盘」生成器（2026-11-11，仅官方副本）。

设计依据（腾讯官方文档，多源核实）：
  * bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html
  * bot.q.qq.com/wiki/develop/api-v2/.../v2_groups_group_openid_messages.post.html
  * nonebot-adapter-qq 1.7.2 models/common.py: Action / Button / RenderData /
    InlineKeyboard / MessageKeyboard（字段逐一对齐）

为什么收敛成「一个生成器 + 一张口令表」而不是在每个 _send_* 里各写按钮：
  * game_tools / warframe 有 20+ 个分散发送出口，逐个挂按钮必然遗漏，且违反
    「warframe 两个大文件零改动」的既定边界；
  * 全部官方发送唯一出口是 ``OfficialChannel.send``，在那里根据「本次回复属于
    哪个口令」统一挂一张按钮键盘，一处改动 = 全量口令自动覆盖；
  * 本模块是纯函数，不 import nonebot / 不碰业务，离线可单测、可穷举覆盖。

按钮类型：action.type=1（回调按钮），data 里放触发口令；点击后平台推
``INTERACTION_CREATE`` 事件，由交互 handler 读 button_data 转回口令执行。
（不用 type=2 指令按钮：其 enter 自动发送「仅单聊」可用，群聊体验不一致。）

msg_type 约束：带 keyboard 的消息 msg_type=2，带图片的是 msg_type=7，二者
**互斥不能同条**（official.py::send 已按 msg_type 逐条拆发）。因此本键盘作为
「图片卡片之后追加的一条纯按钮消息」下发，主卡片内容完全不受影响。
"""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# 口令分组：每张回复卡片附带「同类高频命令」快捷按钮，禁止遗漏。
# 覆盖 game_tools.HELP / NATURAL_CMDS / GUIDE_TITLES 的全部一级指令；
# 键 = 归一化后的当前命令（不含 # 前缀、不含参数），值 = 一行按钮（label, 口令）。
# 每条口令最多 5 行 × 每行 5 按钮（平台硬限），这里用 2 行 × 3 按钮留足余量。
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Btn:
    label: str   # 按钮显示文字（≤10 字符，平台限制）
    cmd: str     # 点击后触发的口令（回调 button_data）
    primary: bool = False  # 主按钮用蓝底(style=1)，其余灰线框(style=0)


# 「返回主菜单」是每张卡片都带的兜底按钮，保证任何口令都能回到帮助。
_MENU = Btn("主菜单", "帮助")

# 按命令族分组的快捷按钮；当前命令命中哪一组，就发哪一组的按钮。
# 覆盖清单（与 game_tools.NATURAL_CMDS / HELP 逐条核对，见测试 test_qq_keyboard.py）：
#   世界状态类 / 市价类 / 紫卡类 / 二游攻略类 / 小游戏类 / 计划类 / 星际战甲其它
_GROUPS: dict[str, tuple[Btn, ...]] = {
    # ---- 星际战甲：世界状态 / 周期任务 ----
    "赏金": (Btn("赏金", "赏金", True), Btn("钢铁赏金", "钢铁赏金"), Btn("裂隙", "裂隙"), _MENU),
    "钢铁赏金": (Btn("赏金", "赏金"), Btn("钢铁赏金", "钢铁赏金", True), Btn("仲裁", "仲裁"), _MENU),
    "裂隙": (Btn("裂隙", "裂隙", True), Btn("钢铁裂隙", "钢铁裂隙"), Btn("入侵", "入侵"), _MENU),
    "钢铁裂隙": (Btn("裂隙", "裂隙"), Btn("钢铁裂隙", "钢铁裂隙", True), Btn("警报", "警报"), _MENU),
    "普通裂隙": (Btn("裂隙", "裂隙", True), Btn("钢铁裂隙", "钢铁裂隙"), Btn("突击", "突击"), _MENU),
    "入侵": (Btn("入侵", "入侵", True), Btn("突击", "突击"), Btn("警报", "警报"), _MENU),
    "突击": (Btn("突击", "突击", True), Btn("奸商", "奸商"), Btn("特惠", "特惠"), _MENU),
    "奸商": (Btn("奸商", "奸商", True), Btn("虚空商人", "虚空商人"), Btn("特惠", "特惠"), _MENU),
    "虚空商人": (Btn("奸商", "奸商", True), Btn("特惠", "特惠"), Btn("裂隙", "裂隙"), _MENU),
    "特惠": (Btn("特惠", "特惠", True), Btn("奸商", "奸商"), Btn("每日特惠", "每日特惠"), _MENU),
    "每日特惠": (Btn("特惠", "特惠"), Btn("每日特惠", "每日特惠", True), Btn("奸商", "奸商"), _MENU),
    "警报": (Btn("警报", "警报", True), Btn("赏金", "赏金"), Btn("仲裁", "仲裁"), _MENU),
    "仲裁": (Btn("仲裁", "仲裁", True), Btn("仲裁表", "仲裁表"), Btn("赏金", "赏金"), _MENU),
    "仲裁表": (Btn("仲裁表", "仲裁表", True), Btn("仲裁", "仲裁"), Btn("钢铁赏金", "钢铁赏金"), _MENU),
    "电波": (Btn("电波", "电波", True), Btn("九重天", "九重天"), Btn("周本", "周本"), _MENU),
    "九重天": (Btn("九重天", "九重天", True), Btn("周本", "周本"), Btn("电波", "电波"), _MENU),
    "周本": (Btn("周本", "周本", True), Btn("轮换", "轮换"), Btn("九重天", "九重天"), _MENU),
    "轮换": (Btn("轮换", "轮换", True), Btn("周本", "周本"), Btn("灵化", "灵化"), _MENU),
    "灵化": (Btn("灵化", "灵化", True), Btn("轮换", "轮换"), Btn("科研", "科研"), _MENU),
    "科研": (Btn("科研", "科研", True), Btn("灵化", "灵化"), Btn("赏金", "赏金"), _MENU),
    "平原": (Btn("平原", "平原", True), Btn("赏金", "赏金"), Btn("裂隙", "裂隙"), _MENU),
    "钢铁": (Btn("钢铁赏金", "钢铁赏金"), Btn("钢铁裂隙", "钢铁裂隙"), Btn("钢铁之路", "钢铁之路", True), _MENU),
    "钢铁之路": (Btn("钢铁赏金", "钢铁赏金"), Btn("钢铁裂隙", "钢铁裂隙"), Btn("钢铁之路", "钢铁之路", True), _MENU),
    "钢路": (Btn("钢铁赏金", "钢铁赏金"), Btn("钢铁裂隙", "钢铁裂隙"), Btn("钢铁之路", "钢铁之路", True), _MENU),
    "金垃圾": (Btn("金垃圾", "金垃圾", True), Btn("赏金", "赏金"), Btn("钢铁赏金", "钢铁赏金"), _MENU),
    # ---- 执刑官 / 九重天猎杀 ----
    "执政官": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    "执刑官": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    "执行官": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    "执政官猎杀": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    "执刑官猎杀": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    "执行官猎杀": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    "猎杀": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    "archon": (Btn("执政官猎杀", "执政官猎杀", True), Btn("仲裁", "仲裁"), Btn("赏金", "赏金"), _MENU),
    # ---- 市价 / 交易 ----
    "价格": (Btn("市价", "市价", True), Btn("紫卡", "紫卡"), Btn("赤毒", "赤毒"), _MENU),
    "市价": (Btn("市价", "市价", True), Btn("紫卡", "紫卡"), Btn("赤毒", "赤毒"), _MENU),
    "wm": (Btn("市价", "市价", True), Btn("紫卡", "紫卡"), Btn("赤毒", "赤毒"), _MENU),
    "市场": (Btn("市价", "市价", True), Btn("紫卡", "紫卡"), Btn("赤毒", "赤毒"), _MENU),
    "赤毒": (Btn("赤毒", "赤毒", True), Btn("信条", "信条"), Btn("市价", "市价"), _MENU),
    "信条": (Btn("信条", "信条", True), Btn("赤毒", "赤毒"), Btn("市价", "市价"), _MENU),
    "终幕": (Btn("终幕", "终幕", True), Btn("信条", "信条"), Btn("市价", "市价"), _MENU),
    "coda": (Btn("终幕", "终幕", True), Btn("信条", "信条"), Btn("市价", "市价"), _MENU),
    "tenet": (Btn("信条", "信条", True), Btn("赤毒", "赤毒"), Btn("市价", "市价"), _MENU),
    # ---- 紫卡族 ----
    "紫卡": (Btn("紫卡", "紫卡", True), Btn("紫卡全面", "紫卡全面"), Btn("紫卡趋势", "紫卡趋势"), _MENU),
    "紫卡分析": (Btn("紫卡全面", "紫卡全面", True), Btn("紫卡", "紫卡"), Btn("紫卡融合", "紫卡融合"), _MENU),
    "紫卡全面": (Btn("紫卡全面", "紫卡全面", True), Btn("紫卡", "紫卡"), Btn("紫卡融合", "紫卡融合"), _MENU),
    "紫卡全面分析": (Btn("紫卡全面", "紫卡全面", True), Btn("紫卡", "紫卡"), Btn("紫卡融合", "紫卡融合"), _MENU),
    "紫卡完整": (Btn("紫卡全面", "紫卡全面", True), Btn("紫卡", "紫卡"), Btn("紫卡趋势", "紫卡趋势"), _MENU),
    "完整分析": (Btn("紫卡全面", "紫卡全面", True), Btn("紫卡", "紫卡"), Btn("紫卡趋势", "紫卡趋势"), _MENU),
    "全面分析": (Btn("紫卡全面", "紫卡全面", True), Btn("紫卡", "紫卡"), Btn("紫卡趋势", "紫卡趋势"), _MENU),
    "紫卡趋势": (Btn("紫卡趋势", "紫卡趋势", True), Btn("词条价差", "词条价差"), Btn("紫卡", "紫卡"), _MENU),
    "riven趋势": (Btn("紫卡趋势", "紫卡趋势", True), Btn("词条价差", "词条价差"), Btn("紫卡", "紫卡"), _MENU),
    "紫卡价差": (Btn("词条价差", "词条价差", True), Btn("紫卡趋势", "紫卡趋势"), Btn("紫卡", "紫卡"), _MENU),
    "词条价差": (Btn("词条价差", "词条价差", True), Btn("紫卡趋势", "紫卡趋势"), Btn("紫卡", "紫卡"), _MENU),
    "紫卡词条价差": (Btn("词条价差", "词条价差", True), Btn("紫卡趋势", "紫卡趋势"), Btn("紫卡", "紫卡"), _MENU),
    "紫卡融合": (Btn("紫卡融合", "紫卡融合", True), Btn("紫卡全面", "紫卡全面"), Btn("紫卡", "紫卡"), _MENU),
    "riven融合": (Btn("紫卡融合", "紫卡融合", True), Btn("紫卡全面", "紫卡全面"), Btn("紫卡", "紫卡"), _MENU),
    "词条推荐": (Btn("词条推荐", "词条推荐", True), Btn("洗卡推荐", "洗卡推荐"), Btn("紫卡", "紫卡"), _MENU),
    "洗卡推荐": (Btn("洗卡推荐", "洗卡推荐", True), Btn("词条推荐", "词条推荐"), Btn("紫卡", "紫卡"), _MENU),
    "紫卡推荐": (Btn("词条推荐", "词条推荐", True), Btn("洗卡推荐", "洗卡推荐"), Btn("紫卡", "紫卡"), _MENU),
    "倾向": (Btn("倾向", "倾向", True), Btn("紫卡", "紫卡"), Btn("紫卡趋势", "紫卡趋势"), _MENU),
    "模拟开卡": (Btn("模拟开卡", "模拟开卡", True), Btn("开卡", "开卡"), Btn("抽签", "抽签"), _MENU),
    "开卡": (Btn("模拟开卡", "模拟开卡", True), Btn("抽签", "抽签"), Btn("黄历", "黄历"), _MENU),
    # ---- 战甲 / MOD / 赋能 / 配卡 ----
    "战甲": (Btn("战甲", "战甲", True), Btn("mod", "mod"), Btn("赋能", "赋能"), _MENU),
    "frame": (Btn("战甲", "战甲", True), Btn("mod", "mod"), Btn("赋能", "赋能"), _MENU),
    "warframe": (Btn("战甲", "战甲", True), Btn("mod", "mod"), Btn("赋能", "赋能"), _MENU),
    "wf": (Btn("战甲", "战甲", True), Btn("赏金", "赏金"), Btn("裂隙", "裂隙"), _MENU),
    "星际战甲": (Btn("战甲", "战甲", True), Btn("赏金", "赏金"), Btn("市价", "市价"), _MENU),
    "mod": (Btn("mod", "mod", True), Btn("赋能", "赋能"), Btn("战甲", "战甲"), _MENU),
    "卡": (Btn("mod", "mod", True), Btn("赋能", "赋能"), Btn("战甲", "战甲"), _MENU),
    "卡片": (Btn("mod", "mod", True), Btn("赋能", "赋能"), Btn("战甲", "战甲"), _MENU),
    "赋能": (Btn("赋能", "赋能", True), Btn("mod", "mod"), Btn("战甲", "战甲"), _MENU),
    "arcane": (Btn("赋能", "赋能", True), Btn("mod", "mod"), Btn("战甲", "战甲"), _MENU),
    "配卡": (Btn("配卡", "配卡", True), Btn("build", "build"), Btn("战甲", "战甲"), _MENU),
    "build": (Btn("build", "build", True), Btn("配卡", "配卡"), Btn("战甲", "战甲"), _MENU),
    "配置": (Btn("配卡", "配卡", True), Btn("build", "build"), Btn("战甲", "战甲"), _MENU),
    "wiki": (Btn("wiki", "wiki", True), Btn("翻译", "翻译"), Btn("战甲", "战甲"), _MENU),
    "翻译": (Btn("翻译", "翻译", True), Btn("wiki", "wiki"), Btn("战甲", "战甲"), _MENU),
    # ---- 小游戏 ----
    "抽签": (Btn("抽签", "抽签", True), Btn("黄历", "黄历"), Btn("仓鼠运势", "仓鼠运势"), _MENU),
    "运势": (Btn("抽签", "抽签", True), Btn("黄历", "黄历"), Btn("仓鼠运势", "仓鼠运势"), _MENU),
    "仓鼠运势": (Btn("仓鼠运势", "仓鼠运势", True), Btn("抽签", "抽签"), Btn("黄历", "黄历"), _MENU),
    "黄历": (Btn("黄历", "黄历", True), Btn("抽签", "抽签"), Btn("仓鼠运势", "仓鼠运势"), _MENU),
    "仓鼠黄历": (Btn("黄历", "黄历", True), Btn("抽签", "抽签"), Btn("黄道吉日", "黄道吉日"), _MENU),
    "黄道吉日": (Btn("黄历", "黄历", True), Btn("抽签", "抽签"), Btn("仓鼠运势", "仓鼠运势"), _MENU),
    "猜数字": (Btn("猜数字", "猜数字", True), Btn("24点", "24点"), Btn("答题", "答题"), _MENU),
    "猜": (Btn("猜数字", "猜数字", True), Btn("24点", "24点"), Btn("答题", "答题"), _MENU),
    "24点": (Btn("24点", "24点", True), Btn("猜数字", "猜数字"), Btn("答题", "答题"), _MENU),
    "24": (Btn("24点", "24点", True), Btn("猜数字", "猜数字"), Btn("答题", "答题"), _MENU),
    "答题": (Btn("答题", "答题", True), Btn("猜数字", "猜数字"), Btn("24点", "24点"), _MENU),
    "答": (Btn("答题", "答题", True), Btn("猜数字", "猜数字"), Btn("24点", "24点"), _MENU),
    "接龙": (Btn("接龙", "接龙", True), Btn("答题", "答题"), Btn("结束", "结束"), _MENU),
    "结束": (Btn("结束", "结束", True), Btn("主菜单", "帮助"), Btn("抽签", "抽签"), _MENU),
    # ---- 二游攻略 ----
    "角色": (Btn("角色", "角色", True), Btn("配队", "配队"), Btn("养成", "养成"), _MENU),
    "查角色": (Btn("角色", "角色", True), Btn("配队", "配队"), Btn("养成", "养成"), _MENU),
    "图鉴": (Btn("角色", "角色", True), Btn("配队", "配队"), Btn("养成", "养成"), _MENU),
    "配队": (Btn("配队", "配队", True), Btn("养成", "养成"), Btn("副本", "副本"), _MENU),
    "队伍": (Btn("配队", "配队", True), Btn("养成", "养成"), Btn("活动", "活动"), _MENU),
    "组队": (Btn("配队", "配队", True), Btn("养成", "养成"), Btn("活动", "活动"), _MENU),
    "养成": (Btn("养成", "养成", True), Btn("配队", "配队"), Btn("副本", "副本"), _MENU),
    "培养": (Btn("养成", "养成", True), Btn("配队", "配队"), Btn("副本", "副本"), _MENU),
    "毕业": (Btn("养成", "养成", True), Btn("配队", "配队"), Btn("副本", "副本"), _MENU),
    "副本": (Btn("副本", "副本", True), Btn("活动", "活动"), Btn("配队", "配队"), _MENU),
    "深渊": (Btn("副本", "副本", True), Btn("活动", "活动"), Btn("养成", "养成"), _MENU),
    "混沌": (Btn("副本", "副本", True), Btn("活动", "活动"), Btn("养成", "养成"), _MENU),
    "危局": (Btn("副本", "副本", True), Btn("活动", "活动"), Btn("养成", "养成"), _MENU),
    "活动": (Btn("活动", "活动", True), Btn("副本", "副本"), Btn("配队", "配队"), _MENU),
    # ---- 生图计划 ----
    "计划": (Btn("计划", "计划", True), Btn("明细", "明细"), Btn("帮助", "帮助"), _MENU),
    "我的计划": (Btn("计划", "计划", True), Btn("明细", "明细"), _MENU),
    "草稿计划": (Btn("计划", "计划", True), Btn("明细", "明细"), _MENU),
    "plan": (Btn("计划", "计划", True), Btn("明细", "明细"), _MENU),
    "明细": (Btn("明细", "明细", True), Btn("计划", "计划"), _MENU),
    "plandetail": (Btn("明细", "明细", True), Btn("计划", "计划"), _MENU),
    # ---- 计划真实下发（_DISPATCH_CMD_WORDS：下发计划/下发/runplan/dispatch）----
    "下发": (Btn("下发计划", "下发计划", True), Btn("计划", "计划"), Btn("明细", "明细"), _MENU),
    "下发计划": (Btn("下发计划", "下发计划", True), Btn("计划", "计划"), Btn("明细", "明细"), _MENU),
    "runplan": (Btn("下发计划", "下发计划", True), Btn("计划", "计划"), Btn("明细", "明细"), _MENU),
    "dispatch": (Btn("下发计划", "下发计划", True), Btn("计划", "计划"), Btn("明细", "明细"), _MENU),
    # ---- 任务订阅 ----
    "订阅": (Btn("订阅列表", "订阅列表", True), Btn("订阅任务", "订阅任务"), Btn("取消订阅", "取消订阅"), _MENU),
    "订阅任务": (Btn("订阅列表", "订阅列表", True), Btn("我的订阅", "我的订阅"), Btn("取消订阅", "取消订阅"), _MENU),
    "订阅列表": (Btn("订阅列表", "订阅列表", True), Btn("我的订阅", "我的订阅"), Btn("取消订阅", "取消订阅"), _MENU),
    "我的订阅": (Btn("订阅列表", "订阅列表", True), Btn("取消订阅", "取消订阅"), _MENU),
    "查看订阅": (Btn("订阅列表", "订阅列表", True), Btn("取消订阅", "取消订阅"), _MENU),
    "关注任务": (Btn("订阅任务", "订阅任务", True), Btn("订阅列表", "订阅列表"), Btn("取消关注", "取消关注"), _MENU),
    "蹲任务": (Btn("订阅任务", "订阅任务", True), Btn("订阅列表", "订阅列表"), _MENU),
    "取消订阅": (Btn("订阅列表", "订阅列表"), Btn("取消订阅", "取消订阅", True), _MENU),
    "取消关注": (Btn("订阅列表", "订阅列表"), Btn("取消订阅", "取消订阅", True), _MENU),
    "退订": (Btn("订阅列表", "订阅列表"), Btn("取消订阅", "取消订阅", True), _MENU),
    "任务订阅列表": (Btn("订阅列表", "订阅列表", True), Btn("取消订阅", "取消订阅"), _MENU),
    # ---- 帮助 / 菜单（兜底主菜单）----
    "帮助": (Btn("赏金", "赏金", True), Btn("裂隙", "裂隙"), Btn("市价", "市价"), Btn("抽签", "抽签"), _MENU),
    "菜单": (Btn("赏金", "赏金", True), Btn("裂隙", "裂隙"), Btn("市价", "市价"), Btn("抽签", "抽签"), _MENU),
    "功能": (Btn("赏金", "赏金", True), Btn("裂隙", "裂隙"), Btn("市价", "市价"), Btn("抽签", "抽签"), _MENU),
    "help": (Btn("赏金", "赏金", True), Btn("裂隙", "裂隙"), Btn("市价", "市价"), Btn("抽签", "抽签"), _MENU),
    "工具": (Btn("赏金", "赏金", True), Btn("裂隙", "裂隙"), Btn("市价", "市价"), Btn("抽签", "抽签"), _MENU),
}

# 兜底按钮组：命令未命中任何分组时（如主流程 AI 回复）也带一组通用入口，
# 保证「每一条口令回复卡片都有按钮、零遗漏」。
_FALLBACK: tuple[Btn, ...] = (
    Btn("赏金", "赏金", True), Btn("裂隙", "裂隙"), Btn("市价", "市价"), _MENU,
)

# 别名 → 规范口令键。game_tools 的真实词表不止 NATURAL_CMDS：GUIDE_TITLES /
# ZERO_ARG_OK / _PLAN_CMD_WORDS / _DISPATCH_CMD_WORDS / HELP 里还有大量别名
# （如「混沌回忆」→副本、「分析」→紫卡分析、「WM」→市价、「plans」→计划）。
# 用一张映射收敛，避免给每个别名复制整组按钮（必然遗漏）；buttons_for 查表前
# 先归一，测试从源码 AST 抽全量词表逐一校验「每个别名都能命中专属组」。
# 键统一为 normalize_cmd 的输出形态（去 #/＃// 前缀、首词、ascii 小写）。
_ALIASES: dict[str, str] = {
    # 二游攻略别名（GUIDE_TITLES）
    "混沌回忆": "副本", "当期": "副本", "活动一览": "活动", "新活动": "活动",
    "角色": "角色", "查角色": "角色",
    # 星际战甲别名
    "分析": "紫卡分析", "紫卡词条价差": "词条价差", "终幕": "信条",
    "钢路": "钢铁之路", "钢铁": "钢铁之路",
    "wm": "市价", "w M": "市价", "市场": "市价", "价格": "市价",
    "warframe": "战甲", "frame": "战甲", "星际战甲": "战甲",
    "卡": "mod", "卡片": "mod", "arcane": "赋能",
    "配置": "配卡", "build": "配卡",
    # 执刑官/执政官/执行官 → 统一到猎杀组
    "执政官": "执政官猎杀", "执刑官": "执政官猎杀", "执行官": "执政官猎杀",
    "猎杀": "执政官猎杀", "archon": "执政官猎杀",
    # 小游戏别名
    "猜": "猜数字", "答": "答题", "运势": "抽签", "仓鼠运势": "抽签",
    "仓鼠黄历": "黄历", "黄道吉日": "黄历", "24": "24点",
    # 生图计划别名（_PLAN_CMD_WORDS）
    "plans": "计划", "我的计划": "计划", "草稿计划": "计划",
    "明细计划": "明细", "plandetail": "明细",
    # 任务订阅别名
    "任务订阅列表": "订阅列表", "我的订阅": "订阅列表", "查看订阅": "订阅列表",
    "蹲任务": "订阅任务", "关注任务": "订阅任务", "退订": "取消订阅", "取消关注": "取消订阅",
    # 菜单/帮助别名
    "fhelp": "帮助", "help": "帮助", "菜单": "帮助", "功能": "帮助", "工具": "帮助",
}


def normalize_cmd(text: str) -> str:
    """把用户输入归一化成口令键：去 #/＃// 前缀、去首尾空白、取首个词、转小写。

    只用于「选哪一组按钮」，不改业务分发。例：``#赏金 钢铁``→``赏金``、
    ``紫卡趋势 绝路``→``紫卡趋势``、``/help``→``help``。
    """
    t = (text or "").strip()
    while t and t[0] in "#＃/":
        t = t[1:].strip()
    if not t:
        return ""
    parts = t.split()
    head = parts[0] if parts else t
    # 中文口令大小写无关；英文口令统一小写比对（表内英文键均为小写/literal）
    return head.lower() if head.isascii() else head


def buttons_for(text: str) -> tuple[Btn, ...]:
    """按当前回复所属口令取按钮组；未命中给兜底组（保证必有按钮）。

    查表顺序：归一化 → 别名映射(_ALIASES) → 专属组(_GROUPS) → 兜底(_FALLBACK)。
    这样「命令词表里的任一别名」都能落到它所属命令族的按钮组，而非兜底。
    """
    cmd = normalize_cmd(text)
    low = cmd.lower()
    # 别名归一：先把原始键/小写键映射到规范口令
    canonical = _ALIASES.get(cmd, _ALIASES.get(low, cmd))
    canon_low = canonical.lower()
    return _GROUPS.get(
        canonical, _GROUPS.get(canon_low, _GROUPS.get(low, _FALLBACK))
    )


def make_keyboard(text: str):
    """根据当前口令文本构造 ``MessageKeyboard``（供 official.py::send 调用）。

    延迟 import：让本模块可在无 nonebot 环境（纯单测）里 import 且测按钮表。
    每行按钮数 ≤5、行数 ≤5（平台硬限）；这里一行放 4 个。
    纯函数——只依赖入参 text，无全局态，天然并发安全。
    """
    from nonebot.adapters.qq.models.common import (
        Action, Button, InlineKeyboard, InlineKeyboardRow, MessageKeyboard,
        Permission, RenderData,
    )

    btns = buttons_for(text)
    row_buttons = []
    for b in btns[:5]:
        row_buttons.append(Button(
            id="qb_%s" % (b.cmd or "menu"),
            render_data=RenderData(
                label=b.label[:10],
                visited_label=b.label[:10],
                style=1 if b.primary else 0,
            ),
            action=Action(
                type=1,
                permission=Permission(type=2),
                data=b.cmd,
                reply=False,
                enter=False,
            ),
        ))
    kb = MessageKeyboard(content=InlineKeyboard(rows=[InlineKeyboardRow(buttons=row_buttons)]))
    return kb, btns
