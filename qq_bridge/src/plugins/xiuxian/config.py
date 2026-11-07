# -*- coding: utf-8 -*-
"""修仙文字游戏 · 配置与数值表。

⚠️ 数值来源原则（2026-10-26 起）：**成长与突破相关的数值/机制一律搬自开源项目，不自行设计**。
  单一来源：Sam5440/nonebot_plugin_xiuxian（MIT），避免"两套表混搭"导致量级错位。
  * 境界/修为/突破需求/突破率：`data/xiuxian/境界.json` + `突破概率.json` → config_open.py（逐行照抄）
  * 机制参数（闭关产出、修为上限、破镜 CD、失败惩罚、失败加成）：
    `data/xiuxian/config.yaml` → 本文件下方逐条注明 key 名。
  * QingMuCat/nonebot_plugin_xiuxian_2 是同源 fork，`closing_exp=30`、`level_punishment_limit=10`
    与源不同；本实现取**源项目**值，保持与境界表同源。

背景（为什么换表）：旧表自己拍的「需求 ×2.5 / 产出 ×2」，净效果是越升越快 ——
单灵根纯挂机 1.93 小时从凡人到真仙，且突破没有 CD，玩家能连着破，
用户实测 12 分钟从筑基冲到金丹 3607/2000，观感是"还修行啥"。开源表需求跨 6 个数量级。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config_open as open_cfg

# ---------------------------------------------------------------------------
# 数据落盘位置（遵守红线：默认插件内 data/，可经环境变量改到 D 盘）
# ---------------------------------------------------------------------------
DATA_PATH = os.environ.get(
    "XIUXIAN_DATA_PATH",
    str(Path(__file__).resolve().parent / "data" / "xiuxian.json"),
)


# ---------------------------------------------------------------------------
# 主人识别（用于「开启秘境」等管理口令）
# BOT_OWNER_QQ 由 .env 提供（bot.py 启动时 load_dotenv() 已写入 os.environ）；
# 支持逗号（中英文均可）分隔多个主人。fail-closed：未配置则无人是主人。
# ---------------------------------------------------------------------------
def _parse_owners(raw: str) -> set:
    out = set()
    for x in (raw or "").replace("，", ",").split(","):
        x = x.strip()
        if x.isdigit():
            out.add(x)
    return out


OWNER_QQS = _parse_owners(os.environ.get("BOT_OWNER_QQ", ""))


def is_owner(qq) -> bool:
    return str(qq) in OWNER_QQS

# ---------------------------------------------------------------------------
# 境界体系：**搬自开源表**，共 38 级（江湖好手 → 太乙境圆满）
#
# 字段说明（沿用旧名，避免 core/commands 大改）：
#   name         境界名
#   exp_to_next  该境界的修为数（开源 境界.json 的 exp 字段，仅作显示/对照）
#   need_exp     突破到**该境界**所需修为（开源 境界.json 的 power 字段）
#                ⚠️ 源项目门槛取的是 power 不是 exp ——
#                xiuxian2_handle.set_closing_type(): need_exp = get_level_power(下一境界)
#                两者只在「结丹境初期」不同（power 30000 / exp 20000），照源用 power。
#   rate         突破基础成功率（%，来自 突破概率.json，境界.json 里的 rate 全是 100）
#   bt_prob      同上换算成小数（兼容旧调用）
#   power_base   战力基数（沿用同一 power 数值，本项目战力公式的下限）
#   spend        境界倍率（开源表 spend，暂未接入）
#   life_gain    突破成功加寿（开源表无寿元机制，沿用本项目原值，见 config_open）
# ---------------------------------------------------------------------------
REALMS = [
    {
        "name": r[0],
        "exp_to_next": r[1],
        "need_exp": r[3],
        "rate": r[2],
        "bt_prob": r[2] / 100.0,
        "power_base": r[3],
        "spend": r[4],
        "life_gain": open_cfg.life_gain_of(r[0]),
    }
    for r in open_cfg.OPEN_REALMS
]
MAX_REALM = len(REALMS) - 1

# ---------------------------------------------------------------------------
# 成长与突破机制 —— 参数全部对应 Sam5440/nonebot_plugin_xiuxian 的 data/xiuxian/config.yaml
# 抓取自 2026-10-26，源文件原值逐条对照（QingMuCat 的 fork 里 closing_exp=30、
# level_punishment_limit=10 与源不同；本实现以**源项目**为准，保持与境界表同源）：
#   level_up_cd             = 60    → BT_CD_SEC（分钟 → 秒）
#   closing_exp             = 10    → SECLUDE_EXP_PER_MIN（每分钟修为）
#   closing_exp_upper_limit = 1.5   → SECLUDE_CAP_RATIO（闭关上限 = 下境界需求 ×1.5）
#   level_punishment_floor  = 1     → BT_PENALTY_MIN（%）
#   level_punishment_limit  = 5     → BT_PENALTY_MAX（%）
#   level_up_probability    = 0.3   → BT_RATE_BONUS（失败后成功率 ×(1+0.3)，可叠加）
# ---------------------------------------------------------------------------
SECLUDE_EXP_PER_MIN = 10      # 闭关每分钟修为（源 closing_exp；fork 版为 30）
SECLUDE_CAP_RATIO = 1.5       # 闭关修为上限倍率（closing_exp_upper_limit）
BT_CD_SEC = 60 * 60           # 破镜 CD：60 分钟（level_up_cd）
BT_PENALTY_MIN = 0.01         # 突破失败扣修为下限 1%（level_punishment_floor）
BT_PENALTY_MAX = 0.05         # 突破失败扣修为上限 5%（level_punishment_limit；fork 版为 10）
BT_RATE_BONUS = 0.3           # 失败一次，成功率 ×1.3（level_up_probability）
BT_RATE_CAP = 100.0           # 成功率上限（%）
BT_LIFE_COST_ON_FAIL = 1      # 突破失败损寿 1 年（本项目寿元机制，开源表无此设定）

# ---------------------------------------------------------------------------
# 悬赏任务表 —— 原样搬自 Sam5440 的 data/xiuxian/悬赏令.json（16 条，见 config_open）
# 无境界门槛：源数据里只有 rate / time / succeed_thank / fail_thank，加门槛就是自己设计。
# ---------------------------------------------------------------------------
BOUNTIES = open_cfg.OPEN_BOUNTIES
find_bounty = open_cfg.find_bounty
BOUNTY_CATS = ("采药", "捉妖", "暗杀")

# ---------------------------------------------------------------------------
# 炼丹丹药 / 炼丹炉 / 商店 —— 数据搬自 Sam5440（见 config_open），机制忠实源表。
# 药材/丹炉在源仓无价格（源靠灵田种植），SHOP 价格是按源品级透明换算的唯一适配（见 config_open）。
# ---------------------------------------------------------------------------
ELIXIRS = [
    {
        "id": r[0], "name": r[1], "desc": r[2], "buff_type": r[3], "buff": r[4],
        "day_num": r[5], "all_num": r[6], "rank": r[7], "realm": r[8],
        "mix_need_time": r[9],
        "recipe": {int(k): int(v) for k, v in r[10].items()},   # 主药 type → 数量
    }
    for r in open_cfg.OPEN_ELIXIRS
]
FURNACES = [
    {"id": r[0], "name": r[1], "desc": r[2], "buff": r[3], "rank": r[4],
     "price": open_cfg.SHOP_FURNACE_PRICE[r[0]]}
    for r in open_cfg.OPEN_FURNACES
]
# 商店出售的本源药材：按「主药 type」分 6 档（炼丹配方 elixir_config 就是按 type 匹配）
HERB_TYPES = (2, 3, 4, 5, 6)
HERB_INV_KEY = lambda t: f"药材{t}"
SHOP_HERB_PRICE = open_cfg.SHOP_HERB_PRICE
SHOP_FURNACE_PRICE = open_cfg.SHOP_FURNACE_PRICE

# 完整在售条目（#商店 展示 / #买 解析都从这里取，单一源）
SHOP_ITEMS = []
for _f in FURNACES:
    SHOP_ITEMS.append({
        "key": f"furnace:{_f['id']}", "name": f"炼丹炉·{_f['name']}",
        "cat": "炼丹炉", "price": _f["price"], "desc": _f["desc"],
    })
for _t in HERB_TYPES:
    SHOP_ITEMS.append({
        "key": f"herb:{_t}", "name": f"本源药材·{_t}型",
        "cat": "药材", "price": SHOP_HERB_PRICE,
        "desc": f"炼丹主药类型 {_t}（配方按类型匹配，非指定名字）",
    })


def find_elixir(name: str):
    """按丹药名精确匹配（炼丹/用丹用），返回 ELIXIRS 里的 dict。"""
    name = (name or "").strip()
    if not name:
        return None
    for e in ELIXIRS:
        if e["name"] == name:
            return e
    return None


def realm_index_of(prefix: str) -> int:
    """境界名前缀 → 境界序号。炼丹丹药的「境界」门槛（如「元婴境初期」）据此比对。

    源 炼丹丹药.json 的 境界 字段与本项目境界名完全一致（同一套命名），
    故前缀匹配即可；找不到返回 -1。
    """
    prefix = (prefix or "").strip()
    for i, r in enumerate(REALMS):
        if r["name"].startswith(prefix):
            return i
    return -1

# ---------------------------------------------------------------------------
# 灵根：加权随机。倍率作用于修为产出与突破成功率加成
# ---------------------------------------------------------------------------
ROOT_POOL = [
    ("伪灵根", 0.6, 0.02),
    ("双灵根", 1.0, 0.30),
    ("单灵根", 1.3, 0.35),
    ("变异灵根", 1.6, 0.20),
    ("天灵根", 2.2, 0.10),
    ("混沌灵根", 3.0, 0.03),
]
# 提取为 (name, mult, weight)
ROOT_WEIGHTS = [(r[0], r[1], r[2]) for r in ROOT_POOL]

# ---------------------------------------------------------------------------
# 经济与对抗参数
# ---------------------------------------------------------------------------
START_LINGSHI = 50          # 创号赠灵石
CLAIM_COOLDOWN = 20 * 3600  # 领取机缘冷却（秒，约一天）
EXPLORE_CD_SEC = 30 * 60    # 秘境探索冷却（秒，30 分钟）—— 防止 #秘境 无限刷灵石/修为
                          # 与后台「秘境开启 限时30分钟」广播口径一致；源项目均有探索 CD
CLAIM_MIN, CLAIM_MAX = 20, 120   # 机缘灵石区间（随机惊喜）
CLAIM_STREAK_BONUS = 0.15        # 连续领取额外加成（每 days，封顶见下）
CLAIM_STREAK_CAP = 1.0            # 连领加成上限 +100%

ROB_PCT = 0.15              # 抢灵石：赢家拿走输家灵石比例
STEAL_PCT = 0.08            # 偷灵石：比例更低但被发现惩罚小
PVP_LOSE_LINGSHI_PCT = 0.05 # 切磋输家也掉一点灵石（制造摩擦）
ROB_CAP = 5000              # 单次抢夺灵石上限
STEAL_CAP = 2000

# 战力公式权重（战力主体来自开源表的 power_base，这里只加一点个人成长差异）
POWER_PER_EXP = 0.0008
POWER_PER_REALM = 1.0       # 已在 power_base 体现

# ---------------------------------------------------------------------------
# 世界事件
# ---------------------------------------------------------------------------
EVENT_INTERVAL_MIN = 25 * 60   # 后台随机事件最小间隔（秒）
EVENT_INTERVAL_MAX = 75 * 60   # 最大间隔
BOSS_BASE_HP = 5000            # 世界Boss 基础血量（随启用群人数缩放）
BOSS_DMG_PER_HIT_MIN, BOSS_DMG_PER_HIT_MAX = 50, 800
BOSS_REWARD_POOL = 3000        # 击杀总灵石奖池
BOSS_TOP_N_REWARD = 3          # 前 N 名出力者分奖

# 秘境探索节点类型
SECRET_NODES = ["灵石矿", "妖兽", "灵药", "残破古迹", "空", "奇遇"]

# 限时秘境 · 自动探索（2026-10-26 改版：报名即自动推进，事件按倒计时随机触发，
# 不再靠反复发「秘境」口令抽奖；口令改为"报名一次 / 之后只看进度"）
SECRET_DURATION_SEC = 30 * 60        # 单次限时秘境持续时长（秒）
SECRET_EVENT_INTERVAL_MIN = 60       # 自动随机事件最小间隔（秒）
SECRET_EVENT_INTERVAL_MAX = 150      # 自动随机事件最大间隔（秒）
SECRET_TICK_SEC = 5                  # 后台巡检步长（秒）：驱动事件计时与关闭播报

# ---------------------------------------------------------------------------
# 奇遇文案池（无 LLM，纯模板随机）
# ---------------------------------------------------------------------------
QIYU_POOL = [
    "你在路边捡到一枚古朴玉简，参悟后修为小进。",
    "一位邋遢老者看你顺眼，随手点化，你灵台清明。",
    "洞府深处传来龙吟，你虽未得宝，却悟得一丝天道。",
    "妖兽袭来又被你惊走，你拾得它掉落的灵草。",
    "暴雨夜雷光入体，你险些走火入魔，却意外打通经脉。",
    "你在坊市淘到一本残卷，拼凑出半句上古心法。",
]
