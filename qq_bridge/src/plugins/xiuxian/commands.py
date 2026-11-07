# -*- coding: utf-8 -*-
"""修仙口令 · 业务逻辑层（纯函数 + 存储交互，零 nonebot 依赖，可直接单测）。

设计：每个 cmd_* 输入「store + 玩家/群标识 + 参数」，返回要回群的文字。
__init__.py 的 nonebot matcher 只负责：从 event 提取参数 → 调 cmd_* → finish(返回文字)。
这样全部口令都能脱离 QQ/nonebot 被机器验证（见 test_commands.py）。
"""

from __future__ import annotations

import re
import time

from . import config as cfg
from . import core

NOT_ENTERED = "你尚未入道，发送「我要修仙」开启仙途。"


# ---------------------------------------------------------------------------
# 菜单（口令总览）
# ---------------------------------------------------------------------------
#: 菜单数据 = **唯一源**：纯文本回复与图片卡都从这里渲染。
#: 结构：((分组名, ((口令原文, 一句话说明), ...)), ...)
#: 说明里的数值直接对齐 config.py（机缘 20~120 灵石/20h、抢夺 15%、偷取 8%、
#: 上限 5000/2000、Boss 奖池 3000 前 3 名），改数值时请同步这里，避免"说明与实际不符"。
MENU_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("入门 · 建立角色", (
        ("我要修仙", "创建角色，随机灵根，赠 50 灵石；已入道则看状态"),
        ("我的修仙", "状态卡：境界 / 修为 / 战力 / 灵石 / 寿元 / 战绩"),
        ("修仙菜单", "就是这条，随时回来看全部口令"),
        ("修仙帮助", "玩法细则卡：修为 / 突破 / 灵石 / 宗门 / Boss 的具体数值"),
        ("排行榜", "本群战力榜前 10"),
    )),
    ("修炼 · 涨修为升境界", (
        ("闭关", f"盘膝入定，之后每分钟 +{cfg.SECLUDE_EXP_PER_MIN} 修为（×灵根）；不闭关不涨"),
        ("出关", "结束闭关并结算修为；寿元耗尽会兵解重修"),
        ("突破", f"修为满时冲击下一境界，有失败率；成功后需调息 {cfg.BT_CD_SEC // 60} 分钟"),
        ("领取机缘", "每 20 小时一次随机灵石 20~120，连领越给越多"),
        ("秘境", "限时秘境开启后发一次即报名并自动探索；期间再发只看进度，结束自动播报总览"),
    )),
    ("悬赏 · 用时间换灵石", (
        ("悬赏", "看全部任务：成功率 / 耗时 / 报酬，一次只能接一个"),
        ("接悬赏 任务名", "接下来，到点发送「交悬赏」结算（可写简称，如 玄冰花）"),
        ("交悬赏", "到点后结算：按任务成功率掷骰，成败都给灵石"),
    )),
    ("炼丹 · 商店与丹药", (
        ("商店", "看在售：炼丹炉 + 本源药材（按主药类型 2~6）"),
        ("买 物品", "买 炼丹炉·寒铁铸心炉 / 买 药材2（发「商店」看全）"),
        ("炼丹 丹药名", "消耗炉+药材开炉，约 60 分钟成丹（如 炼丹 洗髓丹）"),
        ("收取丹药", "炼制到点后领取成品（丹炉越好产出越多）"),
        ("用丹 丹药名", "服用：加修为 / 加突破率 / 加攻击（有境界与每日上限）"),
    )),
    ("争斗 · 抢灵石", (
        ("切磋 @某人", "比战力，胜者夺对方 5% 灵石"),
        ("抢灵石 @某人", "赢则夺对方 15%（上限 5000），失手反赔"),
        ("偷灵石 @某人", "偷对方 8%（上限 2000），败露反赔灵石"),
        ("送灵石 100 @某人", "赠灵石给道友，结个善缘"),
    )),
    ("宗门 · 抱团", (
        ("创建宗门 名称", "开宗立派，你即是祖师"),
        ("加入宗门 名称", "加入一个已有宗门"),
        ("宗门", "查看本宗门成员与宗门战力"),
        ("宗门战 敌方宗门名", "两宗门比拼总战力，有小概率以弱胜强"),
    )),
    ("世界Boss · 全群出力", (
        ("世界boss", "查看本群 Boss 剩余气血"),
        ("讨伐boss", "出力打伤害，击杀后前 3 名瓜分 3000 灵石"),
    )),
    ("主人 · 管理", (
        ("开启秘境", "【仅主人】立即开启一场限时秘境；已有进行中的会提示剩余时间"),
    )),
)


def menu_total() -> int:
    """口令总条数（菜单标题用，避免手写数字与列表脱节）。"""
    return sum(len(rows) for _, rows in MENU_GROUPS)


def menu_text() -> str:
    """菜单纯文本版（图片渲染失败时的兜底，信息与图片等价）。"""
    out = [f"📖 修仙菜单 · 共 {menu_total()} 条口令"]
    for group, rows in MENU_GROUPS:
        out.append("")
        out.append(f"【{group}】")
        for cmd, desc in rows:
            out.append(f"{cmd} —— {desc}")
    out.append("")
    out.append("口令直接发在群里即可；写成 @某人 的地方要真的 @ 对方。")
    return "\n".join(out)


def cmd_menu() -> str:
    """菜单不需要玩家数据，故为同步函数（与其它 cmd_* 一样可脱离 nonebot 单测）。"""
    return menu_text()


# ---------------------------------------------------------------------------
# 帮助（玩法细则）—— 菜单只列"有哪些口令"，这里讲"规则怎么算"
# ---------------------------------------------------------------------------
#: 悬赏数据派生量（帮助卡文案用，避免在长字符串里重复推导）
_B_HI = max(cfg.BOUNTIES, key=lambda r: r[2])    # 成功率最高的任务
_B_LO = min(cfg.BOUNTIES, key=lambda r: r[2])    # 成功率最低的任务
_B_PAY = (min(r[4] for r in cfg.BOUNTIES), max(r[4] for r in cfg.BOUNTIES))   # 成功报酬区间
_B_FAIL = (min(r[5] for r in cfg.BOUNTIES), max(r[5] for r in cfg.BOUNTIES))  # 失败报酬区间


#: 结构与 MENU_GROUPS 一致：((分组名, ((要点, 细则), ...)), ...)。
#: 每条细则都对照 config.py / core.py 的真实实现写，不写设计意图（见 README「已知不一致」）。
HELP_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("成长 · 修为与境界", (
        ("修为", f"不闭关不涨：发「闭关」后每分钟 +{cfg.SECLUDE_EXP_PER_MIN}（再乘灵根倍率），出关才结算"),
        ("闭关", f"上限 = 下一境界需求 ×{cfg.SECLUDE_CAP_RATIO:g}，到顶先「出关」别干等"),
        ("突破", f"基础成功率随境界递减：练气境初期 {cfg.REALMS[1]['rate']:.0f}% → 渡劫境圆满 {cfg.REALMS[27]['rate']:.0f}%；成功后需调息 {cfg.BT_CD_SEC // 60} 分钟"),
        ("突破失败", f"扣 {cfg.BT_PENALTY_MIN * 100:.0f}%~{cfg.BT_PENALTY_MAX * 100:.0f}% 修为、损寿 {cfg.BT_LIFE_COST_ON_FAIL} 年，但下次成功率 ×{1 + cfg.BT_RATE_BONUS:g}（可累积）"),
        ("境界", f"江湖好手 → 练气 → 筑基 → … → {cfg.REALMS[cfg.MAX_REALM]['name']}，共 {len(cfg.REALMS)} 层（境界表/突破率搬自开源项目 nonebot_plugin_xiuxian）"),
        ("灵根", "开局随机：伪 0.6× / 双 1.0× / 单 1.3× / 变异 1.6× / 天 2.2× / 混沌 3.0×，越高闭关越快"),
        ("寿元", "初始寿命 80 年、起始年龄 16；只随突破增减；耗尽后出关会兵解重修、境界跌一层，灵石保留"),
    )),
    ("机缘 · 秘境", (
        ("机缘", "20 小时一次，随机 20~120 灵石；连续领每天多 15%，封顶 +100%"),
        ("秘境", "限时秘境（约 30 分钟）开启后，发一次「秘境」即自动入内探索；途中六类节点（灵石矿/灵药/妖兽/古迹/奇遇/空）按倒计时自动随机触发，无需反复发口令；期间再发「秘境」只看进度；倒计时结束自动播报每人收获总览"),
    )),
    ("悬赏 · 用时间换灵石", (
        ("任务来源", f"共 {len(cfg.BOUNTIES)} 个任务，分采药（60 分钟）/ 捉妖（120 分钟）/ 暗杀（180 分钟）三类，数值搬自开源项目"),
        ("接取", "一次只能接 1 个；没接任务时发「接悬赏 任务名」，可写简称（玄冰花 / 涂山苏苏）"),
        ("结算", "到点发「交悬赏」按任务成功率掷骰；成功给全额报酬，失败只给辛苦钱"),
        ("报酬", f"成功 {_B_PAY[0]}~{_B_PAY[1]} 灵石，失败给 {_B_FAIL[0]}~{_B_FAIL[1]} 灵石辛苦钱；"
                 f"成功率越高给得越少（{_B_HI[2]}% 的「{_B_HI[1]}」{_B_HI[4]} 灵石，"
                 f"{_B_LO[2]}% 的「{_B_LO[1]}」{_B_LO[4]} 灵石）"),
        ("无需境界", "任务不设境界门槛，唯一成本是时间；接了就占着，不能同时接第二个"),
    )),
    ("炼丹 · 商店与丹药", (
        ("商店", f"NPC 售炼丹炉（{cfg.SHOP_FURNACE_PRICE[4003]}/{cfg.SHOP_FURNACE_PRICE[4002]}/{cfg.SHOP_FURNACE_PRICE[4001]} 灵石）+ 本源药材（{cfg.SHOP_HERB_PRICE} 灵石/株，按主药类型 2~6）"),
        ("炼丹", "先有炉+对应药材：开炉约 60 分钟成丹；丹炉越好(+0/+1/+2)产出越多"),
        ("丹药种类", f"共 {len(cfg.ELIXIRS)} 种，三类：exp_up 加修为 / level_up_big 加突破率 / atk_buff 加攻击"),
        ("用丹限制", "每丹有每日上限(1~10)与累计上限(1~200)，且需达到配方标注的境界才能用"),
        ("修为来源", "exp_up 丹是除闭关外唯一主动加修为的途径；高品丹需海量药材（源仓靠灵田种植，此处用灵石买）"),
    )),
    ("对抗 · 灵石怎么算", (
        ("切磋", "胜者夺对方 5% 灵石（上限 2000）；输了同样赔 5% 给对面"),
        ("抢灵石", "赢拿 15%（上限 5000），失手反赔同样比例给对方"),
        ("偷灵石", "先比战力，赢了再赌 90%；成功偷 8%（上限 2000），败露自己损 3%"),
        ("安全性", "对抗只动灵石与胜负记录，不会掉境界、不会死"),
        ("送灵石", "数量写前面、@ 放后面，如「送灵石 100 @张三」"),
    )),
    ("宗门", (
        ("一人一门", "需先入道；一个人只能属于一个宗门"),
        ("战力", "宗门战力 = 全体成员个人战力之和"),
        ("宗门战", "双方叠加 0.6~1.4 随机扰动，纸面弱的一方也可能赢"),
    )),
    ("世界Boss", (
        ("降临", "后台随机事件推送，血量 = 5000 × 群内修仙人数"),
        ("出手", "每次 50~800 伤害，战力越高打得越狠"),
        ("奖励", "击杀后前 3 名按贡献瓜分 3000 灵石奖池"),
    )),
    ("其他", (
        ("群广播", "「限时秘境开启」广播后发一次「秘境」即可入内自动探索，结束自动播报总览；奇遇 / Boss 降临是氛围提醒"),
        ("背包", "古迹道具（残破玉简 / 古修遗骸 / 神秘碎片）目前只能收藏，暂无用途"),
        ("存档", "数据存在插件目录 data/xiuxian.json，重启不丢"),
    )),
)


def help_total() -> int:
    return sum(len(rows) for _, rows in HELP_GROUPS)


def help_text() -> str:
    """玩法细则纯文本版（图片渲染失败时的兜底）。"""
    out = [f"📖 修仙玩法细则 · 共 {help_total()} 条"]
    for group, rows in HELP_GROUPS:
        out.append("")
        out.append(f"【{group}】")
        for name, desc in rows:
            out.append(f"{name}：{desc}")
    out.append("")
    out.append("发「修仙菜单」看全部口令。")
    return "\n".join(out)


def cmd_help() -> str:
    return help_text()


async def _player(store, qq):
    return await store.get_player(str(qq))


# ---------------------------------------------------------------------------
# 入道 / 状态
# ---------------------------------------------------------------------------
async def cmd_enter(store, qq, name, gid) -> str:
    qq = str(qq)
    p = await store.get_player(qq)
    if p:
        return "你已踏入仙途：\n" + core.status_text(p)
    p = core.create_player(qq, name, gid)
    await store.put_player(p)
    await store.enable_group(str(gid))
    return (
        f"🌱 你以【{p['root']}】（修炼×{p['root_mult']}）之姿入道！\n"
        + core.status_text(p)
        + "\n💡 发送「闭关」翻倍修炼，或去「抢灵石@某人」扬名立万～"
    )


async def cmd_status(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)
    await store.put_player(p)
    return core.status_text(p)


# ---------------------------------------------------------------------------
# 闭关 / 出关 / 突破
# ---------------------------------------------------------------------------
async def cmd_seclude(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if p["secluding"]:
        return "你已在闭关中，发送「出关」结算。"
    core.start_seclude(p)
    await store.put_player(p)
    cap = core.seclude_cap(p)
    return (f"🧘 你盘膝入定（每分钟 +{core.seclude_rate(p):.0f} 修为，上限 {cap}）。\n"
            f"修炼不会自己涨，发「出关」结算 —— 记得回来。")


async def cmd_unseclude(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not p["secluding"]:
        return "你并未闭关，发送「闭关」开始修炼。"
    res = core.stop_seclude(p)
    await store.put_player(p)
    msg = f"⛰️ 出关！获得修为 +{res['gain']}。"
    if res.get("capped"):
        msg += "\n📦 修为已到本境界上限（下境界需求的 1.5 倍），先去「突破」吧。"
    if res.get("died"):
        msg += "\n💀 寿元耗尽，你兵解重修，境界跌落一层（灵石已留）。"
    return msg


async def cmd_breakthrough(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)                      # 闭关中先结算，避免"打坐半小时却按零算"
    need = core.breakthrough_need(p)
    if not core.can_breakthrough(p):
        await store.put_player(p)
        return (f"修为不足，距下一境界还差 {need - p['exp']} 修为。\n"
                f"发「闭关」打坐攒修为（每分钟 +{core.seclude_rate(p):.0f}），不闭关不会涨。")
    left = core.bt_cd_left(p)
    if left > 0:
        await store.put_player(p)
        return f"破镜需调息：还需等待约 {max(1, left // 60)} 分钟才能再次冲境。"
    rate = core.bt_rate(p)
    res = core.do_breakthrough(p)
    await store.put_player(p)
    if res["ok"]:
        return (f"🌟 突破成功！晋入【{res['realm']}】，寿元 +{res['life_gain']} 年。\n"
                f"破镜需静养 {cfg.BT_CD_SEC // 60} 分钟，期间不可再破。")
    return (f"⚡ 突破失败（成功率 {rate:.0f}%），修为折损 {res['penalty'] * 100:.0f}%，损寿 1 年。\n"
            f"再接再厉 —— 当前成功率已提升至 {res['next_rate']:.0f}%。")


# ---------------------------------------------------------------------------
# 机缘 / 秘境
# ---------------------------------------------------------------------------
async def cmd_claim(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.claim(p)
    if not res["ok"]:
        return f"机缘未到，还需等待约 {res.get('remain', 0) // 60} 分钟。"
    await store.put_player(p)
    extra = f"（连续 {res['streak']} 天，加成 +{int(res['bonus'] * 100)}%）" if res["streak"] > 1 else ""
    return f"🎁 你偶得机缘，获得 {res['amt']} 灵石 {extra}"


async def cmd_explore(store, qq, gid=None) -> str:
    """限时秘境：无开启→提示；开启未报名→报名并自动开始；已报名→只看进度。

    事件不由口令触发（由后台 _secret_tick 按倒计时自动推进），口令只负责
    "报名一次 / 查看进度"，避免无限刷灵石。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    gid = str(gid if gid is not None else (p.get("group") or ""))
    world = await store.get_world(gid)
    secret = world.get("secret")
    now_ts = time.time()

    if not core.secret_is_open(secret, now_ts):
        return "🌙 当前没有开启的限时秘境。静候「限时秘境开启」广播后再来探索～"

    part = (secret.get("participants") or {}).get(str(qq))
    if part:
        return core.secret_progress_text(secret, p, now_ts)

    core.secret_join(secret, p, now_ts)
    await store.set_world(gid, world)
    mm = cfg.SECRET_DURATION_SEC // 60
    return (
        f"🧭 你踏入限时秘境，自动探索正式开始（限时 {mm} 分钟）！\n"
        "途中机缘会按倒计时自动随机触发，无需反复发口令；\n"
        "随时发送「秘境」查看当前进度，秘境结束会自动播报总览。"
    )


async def cmd_open_secret(store, gid, qq) -> str:
    """仅主人：立即开启一场限时秘境（已有进行中的则提示剩余，不重复开）。"""
    if not cfg.is_owner(qq):
        return "⛔ 只有主人可以开启秘境。"
    gid = str(gid)
    world = await store.get_world(gid)
    cur = world.get("secret")
    now_ts = time.time()
    if cur and core.secret_is_open(cur, now_ts):
        left = max(0, int(float(cur.get("ends_at", now_ts)) - now_ts))
        return f"⚡ 本群已有一场限时秘境进行中，剩余约 {left // 60} 分{left % 60}秒。"
    world["secret"] = core.secret_new(now_ts)
    await store.set_world(gid, world)
    mm = cfg.SECRET_DURATION_SEC // 60
    return (f"🌟【限时秘境开启】限时 {mm} 分钟！\n"
            "发送一次「秘境」即可入内自动探索，结束自动播报总览。")


# ---------------------------------------------------------------------------
# 悬赏（搬开源 悬赏令.json：接取 → 计时 → 判定 → 灵石）
# ---------------------------------------------------------------------------
async def cmd_bounty(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    info = core.bounty_info(p)
    out = [f"📜 悬赏榜 · 共 {len(cfg.BOUNTIES)} 个任务"]
    if info:
        if info["ready"]:
            out.append(f"✅ 你手上的「{info['name']}」已到期，发「交悬赏」结算")
        else:
            out.append(f"⏳ 进行中：{info['name']}，还需 {info['remain'] // 60} 分钟（发「交悬赏」结算）")
        out.append("（一次只能接一个，先把手上的交掉再谈别的）")
        out.append("")
    cur_cat = None
    for cat, name, rate, minutes, thank, fail_thank, _s, _f in cfg.BOUNTIES:
        if cat != cur_cat:
            out.append(f"【{cat}】{minutes} 分钟 · 失败保底 {fail_thank} 灵石")
            cur_cat = cat
        out.append(f"　{name} · 成功率 {rate}% · 报酬 {thank} 灵石")
    out.append("")
    out.append("接取：接悬赏 任务名（可写简称）　结算：交悬赏")
    return "\n".join(out)


async def cmd_take_bounty(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not name or not str(name).strip():
        return "接悬赏要带任务名，例如：接悬赏 玄冰花（发「悬赏」看完整榜单）"
    res = core.start_bounty(p, name)
    if not res["ok"]:
        if res["reason"] == "busy":
            info = res.get("info") or {}
            if info.get("ready"):
                return f"你手上「{info.get('name')}」已经到期了，先发「交悬赏」结算。"
            return f"你还在做「{info.get('name')}」，还需 {info.get('remain', 0) // 60} 分钟；一次只能接一个。"
        return f"没有叫「{str(name).strip()}」的悬赏（发「悬赏」看完整榜单）。"
    await store.put_player(p)
    return (f"📜 接下【{res['cat']}·{res['name']}】：耗时 {res['minutes']} 分钟，"
            f"成功率 {res['rate']}%，成功可得 {res['thank']} 灵石。\n"
            f"到点后发「交悬赏」结算。")


async def cmd_finish_bounty(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.finish_bounty(p)
    if not res["ok"]:
        if res["reason"] == "none":
            return "你手上没有悬赏任务，发「悬赏」挑一个。"
        return f"「{res['name']}」还没到期，还需 {res['remain'] // 60} 分钟。"
    await store.put_player(p)
    head = "✅" if res["win"] else "❌"
    return (f"{head} 【{res['cat']}·{res['name']}】{res['msg']}\n"
            f"报酬 +{res['amt']} 灵石（当前 {res['ling_shi']}）。")


# ---------------------------------------------------------------------------
# 商店 / 炼丹 / 用丹（数据搬自 Sam5440 的 炼丹丹药.json / 药材.json / 炼丹炉.json）
# ---------------------------------------------------------------------------
async def cmd_shop(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    out = [f"🏪 商店（你的灵石：{p['ling_shi']}）"]
    out.append("【炼丹炉】（产出 +0/+1/+2 枚）")
    for it in cfg.SHOP_ITEMS:
        if it["cat"] == "炼丹炉":
            owned = " ✅持有" if p.get("furnace") == it["name"].replace("炼丹炉·", "") else ""
            out.append(f"　{it['name']} · {it['price']} 灵石{owned}")
    out.append("【本源药材】（按主药类型，炼丹配方按类型匹配）")
    for it in cfg.SHOP_ITEMS:
        if it["cat"] == "药材":
            out.append(f"　{it['name']} · {it['price']} 灵石/株")
    out.append("")
    out.append("购买：买 炼丹炉·寒铁铸心炉　买 药材2（发「炼丹 丹药名」开炉）")
    return "\n".join(out)


async def cmd_buy(store, qq, key) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not key or not str(key).strip():
        return "用法：买 物品名（发「商店」看在售商品）"
    k = str(key).strip()
    m = re.search(r"药材(\d+)", k)
    if m:
        k = f"herb:{m.group(1)}"
    elif k.startswith("炼丹炉"):
        hit = next((it for it in cfg.SHOP_ITEMS
                    if it["cat"] == "炼丹炉" and it["name"] == k), None)
        if not hit:
            return f"没有叫「{k}」的炼丹炉（发「商店」看在售商品）。"
        k = hit["key"]
    else:
        hit = next((it for it in cfg.SHOP_ITEMS if it["key"] == k or it["name"] == k), None)
        if not hit:
            return f"没有叫「{k}」的商品（发「商店」看在售商品）。"
        k = hit["key"]
    res = core.buy_shop_item(p, k)
    if not res["ok"]:
        if res["reason"] == "poor":
            return f"灵石不足，需要 {res['price']} 灵石（当前 {p['ling_shi']}）。"
        return "购买失败。"
    await store.put_player(p)
    if res["cat"] == "炼丹炉":
        return f"🏺 购入【{res['name']}】，可开始炼丹！（发「炼丹 丹药名」开炉）"
    return f"🌿 购入【{res['name']}】×1，当前灵石 {p['ling_shi']}。"


async def cmd_mix(store, qq, pill_name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not pill_name or not str(pill_name).strip():
        return "用法：炼丹 丹药名（如 炼丹 洗髓丹）。发「商店」看药材，先买炉与药材。"
    res = core.mix_elixir_start(p, str(pill_name).strip())
    if not res["ok"]:
        if res["reason"] == "busy":
            return f"你正在炼制「{res['name']}」，还需 {res['remain'] // 60} 分钟，发「收取丹药」领取。"
        if res["reason"] == "noent":
            return f"没有「{str(pill_name).strip()}」这种丹药（发「商店」无此列表）。"
        if res["reason"] == "realm":
            return f"境界不足，炼制需【{res['need']}】。"
        if res["reason"] == "nofurnace":
            return "你还没有炼丹炉，发「商店」买一口（买 炼丹炉·寒铁铸心炉）。"
        if res["reason"] == "herb":
            lack = "、".join(f"药材{t}×{n}" for t, n in res["lack"])
            return f"药材不足，缺：{lack}（发「商店」购买本源药材）。"
    await store.put_player(p)
    extra = f"，丹炉加成 +{res['buff']} 枚" if res["buff"] else ""
    return (f"🔥 开炉炼制【{res['name']}】，约 {res['minutes']} 分钟成丹{extra}。\n"
            f"到点发「收取丹药」领取。")


async def cmd_mix_collect(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.mix_elixir_collect(p)
    if not res["ok"]:
        if res["reason"] == "none":
            return "没有正在炼制的丹药。"
        return f"「{res['name']}」尚未炼成，还需 {res['remain'] // 60} 分钟。"
    await store.put_player(p)
    return f"🏅 炼成【{res['name']}】×{res['count']}！发「用丹 {res['name']}」服用。"


async def cmd_use_elixir(store, qq, pill_name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not pill_name or not str(pill_name).strip():
        return "用法：用丹 丹药名（如 用丹 洗髓丹）。"
    res = core.use_elixir(p, str(pill_name).strip())
    if not res["ok"]:
        if res["reason"] == "noent":
            return f"没有「{str(pill_name).strip()}」这种丹药。"
        if res["reason"] == "none":
            return f"你身上没有【{str(pill_name).strip()}】，先去「炼丹」炼一枚。"
        if res["reason"] == "realm":
            return f"境界不足，服用需【{res['need']}】。"
        if res["reason"] == "daylimit":
            return f"该丹药每日最多服用 {res['day_num']} 枚，今日已用完，明日再来。"
        if res["reason"] == "alllimit":
            return f"该丹药一生最多服用 {res['all_num']} 枚，已到上限。"
    await store.put_player(p)
    return f"💊 服下【{res['name']}】，{res['effect']}！"


# ---------------------------------------------------------------------------
# 对抗：切磋 / 抢 / 偷 / 送
# ---------------------------------------------------------------------------
async def cmd_pvp(store, qq, target) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target:
        return "切磋需 @ 一位道友，例如：切磋@张三"
    vic = await store.get_player(str(target))
    if not vic:
        return "对方尚未入道，拉他「我要修仙」再来切磋。"
    res = core.pvp(p, vic)
    await store.put_player(p)
    await store.put_player(vic)
    if res["win"]:
        return f"🏆 你以战力 {res['atk_pow']} 胜 {res['def_pow']}，夺 {res['ling_shi']} 灵石，威名更盛！"
    return f"💢 不敌（战力 {res['atk_pow']} vs {res['def_pow']}），损 {res['ling_shi']} 灵石，下次再战！"


async def cmd_rob(store, qq, target) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target:
        return "抢灵石需 @ 一位道友：抢灵石@张三"
    vic = await store.get_player(str(target))
    if not vic:
        return "对方尚未入道。"
    if vic["ling_shi"] <= 0:
        return "对方囊中空空，无从下手。"
    res = core.rob(p, vic)
    await store.put_player(p)
    await store.put_player(vic)
    if res["win"]:
        return f"😈 抢夺成功！夺 {res['amt']} 灵石。对方必来报复，小心了！"
    return f"😱 抢夺失手反被击退，损 {res['amt']} 灵石。"


async def cmd_steal(store, qq, target) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target:
        return "偷灵石需 @ 一位道友：偷灵石@张三"
    vic = await store.get_player(str(target))
    if not vic:
        return "对方尚未入道。"
    res = core.steal(p, vic)
    await store.put_player(p)
    await store.put_player(vic)
    if res["win"]:
        return f"🤫 神不知鬼不觉，顺走 {res['amt']} 灵石。"
    return f"🚨 行迹败露，慌乱中损 {res['amt']} 灵石。"


async def cmd_give(store, qq, target, amount) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if amount is None or int(amount) <= 0:
        return "用法：送灵石 100 @张三"
    if not target:
        return "用法：送灵石 100 @张三"
    to = await store.get_player(str(target))
    if not to:
        return "对方尚未入道。"
    res = core.give(p, to, int(amount))
    if not res["ok"]:
        return "灵石不足，或数量无效。" if res["reason"] == "poor" else "数量无效。"
    await store.put_player(p)
    await store.put_player(to)
    return f"🎁 你赠予对方 {res['amt']} 灵石，善缘结下。"


# ---------------------------------------------------------------------------
# 排行榜
# ---------------------------------------------------------------------------
async def cmd_rank(store, gid) -> str:
    players = await store.all_players(str(gid))
    if not players:
        return "本群尚无人修仙，速去「我要修仙」！"
    players.sort(key=core.power, reverse=True)
    lines = ["📜 本群战力榜（前10）："]
    for i, p in enumerate(players[:10], 1):
        lines.append(f"{i}. {p['name']}｜{cfg.REALMS[p['realm']]['name']}｜战力 {core.power(p)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 宗门
# ---------------------------------------------------------------------------
async def cmd_create_sect(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：创建宗门 名称"
    res = core.create_sect(p, name)
    if not res["ok"]:
        return "你已有宗门，不可再立。" if res["reason"] == "already" else "创建失败。"
    await store.put_player(p)
    await store.put_sect(res["sect"])
    return f"🏛️ 宗门【{name}】立！你是开派祖师，快「加入宗门 {name}」招揽道友。"


async def cmd_join_sect(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：加入宗门 名称"
    sects = await store.all_sects()
    sect = next((s for s in sects if s["name"] == name), None)
    if not sect:
        return f"未找到宗门【{name}】。"
    res = core.join_sect(p, sect)
    if not res["ok"]:
        return "你已有宗门。" if res["reason"] == "already" else "加入失败。"
    await store.put_player(p)
    await store.put_sect(sect)
    return f"🤝 你加入宗门【{name}】，共谋大业！"


async def cmd_sect_info(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not p["sect"]:
        return "你尚未加入任何宗门。"
    sect = await store.get_sect(p["sect"])
    if not sect:
        return "宗门数据缺失。"
    members = await store.all_players()
    mem = [m for m in members if m["sect"] == sect["id"]]
    pw = core.sect_power(sect, mem)
    return (
        f"🏛️ 宗门【{sect['name']}】\n"
        f"成员：{len(mem)} 人｜宗门战力：{pw}\n"
        f"成员列表：{'、'.join(m['name'] for m in mem) or '（空）'}"
    )


async def cmd_sect_war(store, qq, enemy_name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not p["sect"]:
        return "你需先加入宗门才能宣战。"
    enemy_name = (enemy_name or "").strip()
    if not enemy_name:
        return "用法：宗门战 敌方宗门名"
    sects = await store.all_sects()
    enemy = next((s for s in sects if s["name"] == enemy_name), None)
    if not enemy or enemy["id"] == p["sect"]:
        return f"未找到敌方宗门【{enemy_name}】。"
    my = await store.get_sect(p["sect"])
    allp = await store.all_players()
    mem_a = [m for m in allp if m["sect"] == my["id"]]
    mem_b = [m for m in allp if m["sect"] == enemy["id"]]
    if not mem_a or not mem_b:
        return "双方需各有成员方可开战。"
    res = core.sect_war(my, mem_a, enemy, mem_b)
    winner = my["name"] if res["a_wins"] else enemy["name"]
    return (
        f"⚔️ 宗门战：【{my['name']}】 vs 【{enemy['name']}】\n"
        f"战力 {res['pa']} vs {res['pb']}（实战 {res['pa_eff']} vs {res['pb_eff']}）\n"
        f"🏆 胜者：{winner}！"
    )


# ---------------------------------------------------------------------------
# 世界Boss
# ---------------------------------------------------------------------------
async def cmd_boss(store, gid) -> str:
    world = await store.get_world(str(gid))
    boss = world.get("boss")
    if not boss or boss.get("hp", 0) <= 0:
        return "当前本群无世界Boss。静候天降凶兽～"
    return f"⚠️ 世界Boss 气血剩余 {boss['hp']}/{boss['max_hp']}，发送「讨伐boss」出力！"


async def cmd_hit_boss(store, qq, gid) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    gid = str(gid)
    world = await store.get_world(gid)
    boss = world.get("boss")
    if not boss or boss.get("hp", 0) <= 0:
        return "本群暂无世界Boss。"
    res = core.boss_hit(p, boss)
    if res["dead"]:
        for q, amt in res["rewards"].items():
            wp = await store.get_player(q)
            if wp:
                wp["ling_shi"] += amt
                await store.put_player(wp)
        world["boss"] = {"hp": 0, "max_hp": boss["max_hp"], "contributors": {}, "expires": 0}
        await store.set_world(gid, world)
        top = "、".join(f"{q} +{amt}" for q, amt in res["rewards"].items())
        return f"💥 世界Boss 伏诛！出力者瓜分奖池：{top}"
    await store.set_world(gid, world)
    return f"🗡️ 你出手造成 {res['dmg']} 伤害，Boss 剩 {res['hp_left']} 气血！"
