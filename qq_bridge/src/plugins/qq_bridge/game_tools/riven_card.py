# -*- coding: utf-8 -*-
"""game_tools/riven_card.py —— 紫卡分析卡（逐字对齐 Warframe Rabbit「紫卡分析」版式）。

公式（与 koishi-plugin-warframe ``services/riven/analysis.ts`` 完全同源）：

    standard = |base| × disposition × factor × (rank+1)/9      # 截图一律按满级 rank=8
    区间      = [standard×0.9, standard×1.1]，两端都 round(…,1)
    偏差      = |value| / standard − 1     （负词条取惩罚量绝对值）
    综合评分  = 各词条 |偏差| 的均值；字母档用**方向敏感**均值（见 _grade）

校准证据（2026-11-02，用户提供的伯斯顿卡「+192.2/+105.2/+143.8/−54.9」对照
参考机器人截图，逐位吻合）：

    区间   167.1%-204.2% / 91.1%-111.4% / 121.5%-148.5% / -59.4%~-48.6%
    偏差   +3.54% / +3.90% / +6.52% / +1.68%
    综合   3.910(A-)

⚠️ 两条历史错误已纠正（2026-11-02）：
  1. 下限曾用 floor(…,1)，会输出 167.0/121.4/48.5 —— 参考卡是 round，差 0.1；
  2. 偏差基准曾是 ``mid / 0.99``（误以为以「2 正布局」为基准），系统性抬高所有偏差。

倾向口径：**官方 Hotfix 44.1.3 后全部紫卡统一按 1.2 计算**，导出里的
``omegaAttenuation`` 只作历史兼容。截图分析先用 1.2 试所有武器版本，全不匹配才
由 ``_fit_disp`` 依卡面词条反推（反推时卡面会显式提示，不静默改数）。

伤害模型为本项目实现（Rabbit 闭源，不冒充同值）：武器官方面板 + 标准配卡 DPS 模拟，
靶子口径照抄参考卡脚注（Lv170 钢铁之路凶魂百夫长 / 护甲 2700 / 弱点命中 50% / 病毒 /
三硬度节点 1.4s/4.5s/14.3s / 击杀效率取几何平均）。绝对值与 Rabbit 不同，
但同一模型内横向可比——参考卡脚注亦声明「仅适合在相同靶子和计算条件下比较」。

市场数据（REDUN-01 降级链）：Warframe.Market 拍卖（可达）→ 样本 <3 或接口失败
按模板填「暂无」，绝不编造价格（CLOSE-02 精神；DE 拍卖/riven.market 本机代理不可达，
预留 _auction_de 扩展位）。
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote as _quote

from nonebot.log import logger

from . import warframe

_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "warframe_items"

#: 词条组合修正（同 riven.py/_FIX：koishi rivenStatFixFactor）
FIX = {
    "2": {"buff": 0.99, "curse": 0.0},
    "21": {"buff": 1.2375, "curse": -0.495},
    "3": {"buff": 0.75, "curse": 0.0},
    "31": {"buff": 0.9375, "curse": -0.75},
}

#: 偏差口径说明（历史遗留常量已移除）：
#: 旧实现把偏差基准归一为 ``mid / 0.99``（误以为参考卡以「2 正布局」为基准），
#: 导致所有偏差被系统性抬高。参考卡实际口径是 dev = |value| / mid − 1，
#: mid 由本卡的**真实布局系数**决定（3 正 1 负 = 0.9375 / 0.75），见 _dev_text。

#: 负词条市场友好度（0~100，越大越不伤价；「推荐负面-伤害计算」排序也用它）
NEG_MARKET = {
    "Zoom": 100, "Ammo Maximum": 100, "Weapon Recoil": 100,
    "Damage to Grineer": 92, "Damage vs Grineer": 92,
    "Damage to Corpus": 92, "Damage vs Corpus": 92,
    "Damage to Infested": 92, "Damage vs Infested": 92,
    "Projectile Speed": 70, "Status Duration": 66,
    "Magazine Capacity": 60, "Combo Duration": 60, "Initial Combo": 60,
    "Fire Rate / Attack Speed": 30, "Fire Rate": 30, "Attack Speed": 30,
    "Reload Speed": 30, "Punch Through": 40, "Impact Damage": 26,
    "Puncture Damage": 26, "Slash Damage": 26, "Status Chance": 20,
    "Critical Chance": 8, "Critical Damage": 8, "Damage": 8, "Multishot": 8,
}

#: 词条市场权重（0~100，「市场 X」分类评分与市场推荐排序用；派系卡溢价高）
STAT_MARKET = {
    "Critical Chance": 88, "Critical Damage": 90, "Damage": 86, "Multishot": 92,
    "Status Chance": 55,
    "Cold Damage": 40, "Electricity Damage": 42, "Heat Damage": 45, "Toxin Damage": 44,
    "Fire Rate / Attack Speed": 48, "Fire Rate": 48, "Attack Speed": 48,
    "Reload Speed": 30, "Magazine Capacity": 24, "Punch Through": 26,
    "Projectile Speed": 18, "Status Duration": 20, "Combo Duration": 26,
    "Viral Damage": 95, "Corrosive Damage": 96, "Radiation Damage": 50,
    "Magnetic Damage": 45, "Blast Damage": 40, "Gas Damage": 35,
    "Range": 60, "Initial Combo": 40, "Heavy Attack Efficiency": 40,
    "Heavy Attack Damage": 40, "Slide Attack Critical Chance": 16,
    "Slide Attack Critical Damage": 16, "Impact Damage": 10, "Puncture Damage": 10,
    "Slash Damage": 10, "Zoom": 6, "Ammo Maximum": 8, "Weapon Recoil": 6,
    "Finisher Damage": 8, "Damage to Grineer": 96, "Damage vs Grineer": 96,
    "Damage to Corpus": 96, "Damage vs Corpus": 96,
    "Damage to Infested": 96, "Damage vs Infested": 96,
}

#: 负词条 DPS 影响度（「推荐负面/致命负」伤害维度；0 = 对 DPS 无影响）
NEG_DPS_IMPACT = {
    "Zoom": 0.0, "Ammo Maximum": 0.0, "Weapon Recoil": 0.0,
    "Damage to Grineer": 0.0, "Damage vs Grineer": 0.0,
    "Damage to Corpus": 0.0, "Damage vs Corpus": 0.0,
    "Damage to Infested": 0.0, "Damage vs Infested": 0.0,
    "Status Duration": 0.0, "Combo Duration": 0.0, "Initial Combo": 0.0,
    "Projectile Speed": 0.5, "Magazine Capacity": 1.5, "Punch Through": 1.5,
    "Reload Speed": 3.0, "Fire Rate": 5.0, "Attack Speed": 5.0,
    "Fire Rate / Attack Speed": 5.0, "Impact Damage": 6.0, "Puncture Damage": 6.0,
    "Slash Damage": 6.0, "Status Chance": 9.0, "Critical Chance": 14.0,
    "Critical Damage": 14.0, "Damage": 16.0, "Multishot": 18.0,
}

#: 靶子口径（照抄参考卡脚注，不改动数字）
_TARGET_TTK = (1.4, 4.5, 14.3)     # 三硬度节点（秒）
_ARMOR = 2700                       # 护甲参与减伤
_HEADSHOT_RATIO = 0.5               # 弱点命中占比（50% @2x → 平均 ×1.5）
_ELEMENT_PCT = 1.20                 # 元素固定为病毒（两个 60/60 合并 = +120% 基伤）

# ---------------------------------------------------------------------------
# 数据懒加载
# ---------------------------------------------------------------------------
_RAW: dict = {"weapons": None, "disp": None, "base": None, "zh": None}

# ---------------------------------------------------------------------------
# 市场数据（2026-11-04 22:25 重写，代码位置：本文件「市场」段）
# ---------------------------------------------------------------------------
class RivenMarketError(RuntimeError):
    """WFM 拍卖数据获取/校验失败。

    用户纪律：禁止降级、禁止兜底。失败必须显式上抛，绝不用静态权重表、
    热度分或「暂无」冒充真实市场数据。调用方负责把**错误原因**显示在卡面上。
    """

#: ⚠️ WFM /v1/auctions/search 必须用 API 型 UA。实测 2026-11-04 22:2x（生产容器
#: qq-bridge 内直连验证）：带 Chrome-like UA 会返回 200 但 ``auctions: []``（29 字节），
#: 带 curl UA + ``Platform: pc`` 返回完整 100 条（108247 字节）。旧代码走
#: ``warframe._market_get``（MARKET_UA=Chrome）＋ 全局 ``/v1/auctions``（100 条全武器），
#: 按武器过滤后经常 <3 条 → 市场行永远「暂无」。这里是该问题的根因修复。
_WFM_AUCTION_HEADERS = {
    "User-Agent": "curl/8.4.0",
    "accept": "application/json",
    "Platform": "pc",
    "Language": "en",
}
_WFM_PAGE_TTL = 600.0
_WFM_MIN_SAMPLES = 3
_AUCTION_CACHE: dict = {}   # (slug, sort_by) -> (ts, auctions)

#: WFM attributes 的 url_name → 中文短名（卡面用；未收录时显示原 slug，绝不编造译名）。
_WFM_ATTR_SHORT = {
    "ammo_maximum": "弹药", "base_damage_/_melee_damage": "基伤",
    "blast": "爆炸", "cold_damage": "冰伤", "corrosive": "腐蚀",
    "critical_chance": "暴率", "critical_damage": "暴伤",
    "damage_to_orokin": "对奥罗金", "damage_vs_corpus": "对C佬",
    "damage_vs_grineer": "对G佬", "damage_vs_infested": "对I佬",
    "electric_damage": "电伤", "fire_rate_/_attack_speed": "射速",
    "gas": "毒气", "heat_damage": "火伤", "impact_damage": "冲击",
    "magazine_capacity": "弹匣", "multishot": "多重",
    "projectile_speed": "弹速", "punch_through": "穿透",
    "recoil": "后坐", "reload_speed": "装填", "slash_damage": "切割",
    "status_chance": "触发", "status_damage": "异常伤",
    "status_duration": "异常持续", "toxin_damage": "毒伤", "viral": "病毒",
    "weak_point_critical_chance": "弱点暴率", "weak_point_damage": "弱点伤",
    "zoom": "变焦",
}

#: DE 官方词条名 → WFM ``attributes.url_name``（2026-11-04 23:0x 新增）。
#: WFM 的 ``positive_stats`` / ``negative_stats`` 只认 url_name，不认 DE 名。
#: 只列已核对过的映射；**映射不到的词条显式标注「无法精确匹配」**，绝不猜 slug。
_DE_TO_WFM = {
    "Ammo Maximum": "ammo_maximum",
    "Base Damage": "base_damage_/_melee_damage",
    "Melee Damage": "base_damage_/_melee_damage",
    "Cold Damage": "cold_damage",
    "Corrosive Damage": "corrosive",
    "Critical Chance": "critical_chance",
    "Critical Damage": "critical_damage",
    "Damage to Corpus": "damage_vs_corpus",
    "Damage to Grineer": "damage_vs_grineer",
    "Damage to Infested": "damage_vs_infested",
    "Electricity Damage": "electric_damage",
    "Fire Rate": "fire_rate_/_attack_speed",
    "Attack Speed": "fire_rate_/_attack_speed",
    "Heat Damage": "heat_damage",
    "Impact Damage": "impact_damage",
    "Magazine Capacity": "magazine_capacity",
    "Multishot": "multishot",
    "Projectile Speed": "projectile_speed",
    "Punch Through": "punch_through",
    "Weapon Recoil": "recoil",
    "Reload Speed": "reload_speed",
    "Slash Damage": "slash_damage",
    "Status Chance": "status_chance",
    "Toxin Damage": "toxin_damage",
    "Viral Damage": "viral",
    "Weak Point Damage": "weak_point_damage",
    "Zoom": "zoom",
}

#: 同实现节流：4 个请求/卡，避免把 WFM 打到 429（不是降级，不改变口径）。
_WFM_MIN_INTERVAL = 0.35
_WFM_LAST_REQ = [0.0]


def _load_raw_weapons() -> dict:
    """懒加载武器原始记录（含 damage 伤害类型分解，DPS 模型用）。

    ``SentinelWeapons.json`` 必须一起并入：DE 的 ``LotusRifleRandomModRare``
    ``compatibleItems`` 里确实收录了哨兵步枪（Verglas / 冰凇 就是典型案例，
    官方中文名 = 冰凇、type = Rifle），只读三件套会让这类卡整块 DPS 面板
    变成「武器面板数据缺失」（实测 2026-11-02）。
    """
    if _RAW["weapons"] is not None:
        return _RAW["weapons"]
    out: dict[str, dict] = {}
    for fname in ("Primary.json", "Secondary.json", "Melee.json",
                  "SentinelWeapons.json"):
        try:
            items = json.loads((_DATA_DIR / fname).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"riven_card_raw_load_failed {fname}: {type(e).__name__}: {e}")
            continue
        for it in items:
            en = (it.get("name") or "").strip()
            if en:
                out[en.lower()] = it
    _RAW["weapons"] = out
    return out


def _load_riven_tables() -> tuple[dict, dict, dict]:
    """懒加载倾向表 / 词条基础值表 / 词条中文名（与 riven.py 同源文件）。"""
    if _RAW["disp"] is None:
        for key, fn in (("disp", "riven_disp.json"), ("base", "riven_stat_base.json"),
                        ("zh", "riven_stat_zh.json")):
            try:
                _RAW[key] = json.loads((_DATA_DIR / fn).read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"riven_card_tbl_load_failed {fn}: {type(e).__name__}: {e}")
                _RAW[key] = {}
    return _RAW["disp"] or {}, _RAW["base"] or {}, _RAW["zh"] or {}


def _stat_zh(en: str) -> str:
    _, _, zh = _load_riven_tables()
    return (zh.get(en) or {}).get("zh") or en


def _fmt_val(s: dict) -> str:
    """词条数值显示：% 一位小数（参考卡口径 +148.0%）、x 两位小数、其余原样。"""
    if s["unit"] == "%":
        return f"{s['value']:.1f}%"
    if s["unit"] == "x":
        return f"{s['value']:.2f}"
    return f"{s['value']:g}"


def _neg_zh(en: str) -> str:
    """负词条显示名（推荐负面行，用词对齐参考卡：变焦/弹药最大值/武器后坐力）。"""
    override = {
        "Zoom": "变焦", "Ammo Maximum": "弹药最大值", "Weapon Recoil": "武器后坐力",
        "Projectile Speed": "弹道速度", "Status Duration": "异常持续时间",
        "Magazine Capacity": "弹匣容量", "Reload Speed": "装填速度",
        "Fire Rate": "射速", "Attack Speed": "攻速",
        "Fire Rate / Attack Speed": "射速", "Slash Damage": "切割",
        "Impact Damage": "冲击", "Puncture Damage": "穿刺",
        "Critical Chance": "暴击几率", "Critical Damage": "暴击伤害",
        "Damage": "基础伤害", "Multishot": "多重射击", "Status Chance": "触发几率",
    }
    return override.get(en) or _stat_zh(en)


def _stat_icon_color(en: str) -> str:
    """词条左侧彩色圆形图标配色（对齐 Warframe 词条图标色系，参考卡每条一个色圆）。

    无现成图标库时，用「按词条类别取色」的圆形图标还原参考卡观感
    （暴击=青、病毒=紫、多重=橙、物理切割=红、射速=黄、触发=绿、弹匣=蓝…）。
    取不到类别时回落中性灰，绝不抛异常。
    """
    s = en.lower()
    if "critical damage" in s:
        return "#27C4D9"          # 暴击伤害：青
    if "critical chance" in s:
        return "#3FD0E0"          # 暴击几率：青亮
    if "multishot" in s:
        return "#E8852B"          # 多重射击：橙
    if "viral" in s:
        return "#9B59B6"          # 病毒：紫
    if "corrosive" in s:
        return "#B7D33B"          # 腐蚀：黄绿
    if "radiation" in s:
        return "#E84393"          # 辐射：粉
    if "magnetic" in s:
        return "#3498DB"          # 磁力：蓝
    if "heat" in s or "fire" in s:
        return "#E74C3C"          # 火/热：红橙
    if "cold" in s or "ice" in s:
        return "#5DADE2"          # 冰：浅蓝
    if "electric" in s or "toxin" in s:
        return "#9B59B6"          # 电/毒：紫
    if "blast" in s:
        return "#E67E22"          # 爆炸：橙
    if "gas" in s:
        return "#58D68D"          # 毒气：绿
    if "slash" in s:
        return "#E74C3C"          # 切割：红
    if "impact" in s:
        return "#C0392B"          # 冲击：暗红
    if "puncture" in s:
        return "#A93226"          # 穿刺：暗红
    if "damage" in s:
        return "#D64545"          # 基础伤害：红
    if "fire rate" in s or "attack speed" in s:
        return "#F1C40F"          # 射速/攻速：黄
    if "status chance" in s or "status" in s:
        return "#2ECC71"          # 触发：绿
    if "reload" in s or "magazine" in s or "ammo" in s:
        return "#3498DB"          # 装填/弹匣/弹药：蓝
    if "zoom" in s or "recoil" in s or "projectile" in s:
        return "#95A5A6"          # 变焦/后坐/弹速：灰
    if "grineer" in s:
        return "#C0392B"          # g系：红
    if "corpus" in s:
        return "#2980B9"          # c系：蓝
    if "infested" in s:
        return "#8E44AD"          # i系：紫
    if "combo" in s:
        return "#16A085"          # 连击：青绿
    if "range" in s:
        return "#E67E22"          # 范围：橙
    if "punch" in s:
        return "#7F8C8D"          # 穿透：灰
    if "duration" in s:
        return "#BDC3C7"          # 持续：浅灰
    return "#9A9898"              # 兜底中性灰


def _short_stat(en: str) -> str:
    """词条短名（市场价差/推荐行用，对齐参考卡：暴击伤害→暴伤、多重射击→多重、触发→异常）。"""
    m = {
        "Critical Damage": "暴伤", "Multishot": "多重", "Damage": "基伤",
        "Critical Chance": "暴率", "Status Chance": "异常",
        "Electricity Damage": "电伤", "Heat Damage": "火伤", "Cold Damage": "冷伤",
        "Toxin Damage": "毒伤", "Fire Rate": "射速", "Attack Speed": "攻速",
        "Fire Rate / Attack Speed": "射速", "Reload Speed": "装填",
        "Slash Damage": "切割", "Impact Damage": "冲击", "Puncture Damage": "穿刺",
        "Weapon Recoil": "后坐", "Zoom": "变焦", "Ammo Maximum": "弹药",
        "Magazine Capacity": "弹匣", "Projectile Speed": "弹速",
        "Status Duration": "异常持续", "Range": "范围", "Punch Through": "穿透",
        "Viral Damage": "病毒", "Corrosive Damage": "腐蚀", "Radiation Damage": "辐射",
        "Magnetic Damage": "磁力", "Blast Damage": "爆炸", "Gas Damage": "毒气",
        "Damage to Grineer": "g系伤", "Damage vs Grineer": "g系伤",
        "Damage to Corpus": "c系伤", "Damage vs Corpus": "c系伤",
        "Damage to Infested": "i系伤", "Damage vs Infested": "i系伤",
    }
    if en in m:
        return m[en]
    z = _stat_zh(en)
    return z.replace("伤害", "伤") if z.endswith("伤害") else z


# ---------------------------------------------------------------------------
# 词条理论范围 / 偏差 / 百分位（公式见模块 docstring，全部经参考卡核验）
# ---------------------------------------------------------------------------
def _fix_of(parsed: dict) -> dict:
    pos = [s for s in parsed["stats"] if s["sign"] != "-"]
    neg = [s for s in parsed["stats"] if s["sign"] == "-"]
    sus = len(pos) >= 4 and not neg
    key = str(min(len(pos), 3)) + ("1" if (neg or sus) else "")
    return FIX.get(key, FIX["3"])


def _mid(s: dict, parsed: dict, fix: dict) -> float | None:
    """词条中值 = |base| × omega × fix（正词条 buff，负词条 |curse|）。
    base 表对某些词条（如 Weapon Recoil）记为负值，取绝对值统一按"惩罚/加成量"处理。"""
    _, base_tbl, _ = _load_riven_tables()
    t = base_tbl.get(parsed["type"]) or {}
    base = t.get(s["en"])
    if base is None and " vs " in s["en"]:
        base = t.get(s["en"].replace(" vs ", " to "))
    if base is None and " to " in s["en"]:
        base = t.get(s["en"].replace(" to ", " vs "))
    if not base:
        return None
    omega = float(parsed.get("disp") or 1.0)
    f = abs(fix["curse"]) if s["sign"] == "-" else fix["buff"]
    # 等级系数 (rank+1)/9 —— koishi analysis.ts 的 standard 公式里就有，旧实现漏了它，
    # 等于**假设所有卡都满级**。满级卡 rank=8 → ×1 无影响；而未满级卡（语料实测 6/12
    # 张是 Rank 0~3）区间会被放大 2~9 倍，导致怎么都对不上、被误判成"旧倾向"。
    rank = parsed.get("rank")
    rk = 8 if not isinstance(rank, int) else max(0, min(8, rank))
    return abs(base) * omega * f * (rk + 1) / 9.0


def _range_of(s: dict, parsed: dict, fix: dict) -> tuple[float, float] | None:
    mid = _mid(s, parsed, fix)
    if mid is None:
        return None
    return mid * 0.9, mid * 1.1


def _fmt_range(lo: float, hi: float, unit: str, is_neg: bool = False) -> str:
    """范围显示：两端都按 round(…,1)（经参考卡逐位核验）。

    伯斯顿样例（disp=1.2，3正1负）：
      Damage  185.6247×[0.9,1.1] = 167.062~204.187 → 167.1%~204.2%
      Crit    134.9966×[0.9,1.1] = 121.497~148.496 → 121.5%~148.5%
      Zoom     53.9946×[0.9,1.1] =  48.595~ 59.394 → -59.4%~-48.6%
    旧实现下限用 floor 会输出 167.0 / 121.4 / 48.5，与参考卡差 0.1——已修正。
    负词条：区间整体取负、更负（更大惩罚）在前，如 -59.4%~-48.6%。
    """
    lo_d = round(lo, 1)
    hi_d = round(hi, 1)
    if unit == "x":
        return f"x{1 + lo_d:.2f}-x{1 + hi_d:.2f}"
    if is_neg:
        return f"-{hi_d:.1f}%~-{lo_d:.1f}%"
    return f"{lo_d:.1f}%-{hi_d:.1f}%"


def _dev_text(v: float, mid: float) -> str:
    """「偏差 ±X.XX%」：词条 roll 相对理论中值(mid)的偏离（两位小数，参考卡口径）。

    dev = |v| / mid - 1：正 = 高于中值 / 惩罚更大（roll 更极端），
    负 = 低于中值 / 惩罚更小。正负词条统一用加成量绝对值比较，
    与参考卡「(落点-中值)/中值」一致。"""
    if not mid:
        return "—"
    dev = (abs(v) / mid - 1.0) * 100.0
    return f"{dev:+.2f}%"


def _percentile(v: float, lo: float, hi: float, is_neg: bool) -> float:
    """「数值X.X%」：距好端百分位（正词条=高 roll 好；负词条=惩罚越小越好）。
    负词条用惩罚量绝对值参与；结果钳制 [0,100]，避免低于范围最小惩罚时溢出。"""
    if hi <= lo:
        return 0.0
    if is_neg:
        p = (hi - abs(v)) / (hi - lo) * 100.0
    else:
        p = (v - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, p))


# ---------------------------------------------------------------------------
# DPS 模拟（本项目实现；靶子口径与参考卡脚注一致）
# ---------------------------------------------------------------------------
#: 标准配卡（显示名；EN 名仅注释）。近战走通用表，DPS 对近战为简化口径。
_BUILD = {
    "Pistol": ["彻甲弹药", "痉挛 Prime", "深层冷冻", "镀层 准确射手",
               "致命洪流", "镀层 弹头扩散", "磁吸巨力"],
    "Rifle": ["分裂膛室", "镀层 膛室", "死眼", "敏感",
              "镀层 会心", "猎者军火", "速度触发"],
    "Shotgun": ["地狱火 Prime", "镀层 地狱", "凶残 Prime", "炽炎",
                "镀层 巧艺", "寒冰之握", "撕碎 Prime"],
}
_MELEE_BUILD = ["压迫 Prime", "热毒 Prime", "渴血", "泣血",
                "角斗 威力", "粉碎者", "触手 Prime"]


def _weapon_raw(name: str) -> dict | None:
    return _load_raw_weapons().get((name or "").strip().lower())


def _base_bucket() -> dict:
    return {"base": 0.0, "cc": 0.0, "cm": 0.0, "ms": 0.0, "fr": 0.0,
            "elem": 0.0, "sc": 0.0, "mag": 0.0, "reload": 0.0,
            "phys_neg": {"impact": 0.0, "puncture": 0.0, "slash": 0.0}}


def _gun_std_bucket(w: dict) -> dict:
    """标准配卡桶：基伤 / 多重×2 / 暴率暴伤（暴击武器） / 病毒 / 射速。"""
    b = _base_bucket()
    cat = w.get("cat") or ""
    cc = float(w.get("criticalChance") or 0)
    b["base"] += 1.65 if cat == "主武器" else 2.20        # 分裂膛室 / 黄蜂蜇刺
    b["ms"] += 1.00 + (1.10 if cat == "副武器" else 0.60)  # 镀层膛室+速度触发 / 弹头扩散+致命洪流
    if cc >= 0.15:
        b["cc"] += 1.50   # 死眼
        b["cm"] += 1.20   # 敏感
    b["elem"] += _ELEMENT_PCT
    b["fr"] += 0.30
    return b


_ELEM_KEYS = ("Electricity Damage", "Heat Damage", "Cold Damage", "Toxin Damage")
_PHYS_KEYS = {"Impact Damage": "impact", "Puncture Damage": "puncture", "Slash Damage": "slash"}


def _riven_mag(s: dict, maxed: bool) -> float:
    """单词条带符号加成量（正 + / 负 −）。maxed=True 取最强 roll（正=上限，负=下限）。"""
    sign = 1.0 if s["sign"] != "-" else -1.0
    if maxed:
        rng = s.get("_rng")
        if rng:
            return sign * (rng[1] if sign > 0 else rng[0])
    v = s["value"] if s["unit"] != "x" else (s["value"] - 1.0)
    return sign * v


def _apply_riven(bucket: dict, stats: list[dict], maxed: bool = False) -> None:
    """把紫卡词条折算进配卡桶。"""
    for s in stats:
        mag = _riven_mag(s, maxed)
        k = s["en"]
        if k in ("Critical Chance",):
            bucket["cc"] += mag / 100.0
        elif k in ("Critical Damage",):
            bucket["cm"] += mag / 100.0
        elif k == "Damage":
            bucket["base"] += mag / 100.0
        elif k == "Multishot":
            bucket["ms"] += mag / 100.0
        elif k in ("Fire Rate", "Attack Speed", "Fire Rate / Attack Speed"):
            bucket["fr"] += mag / 100.0
        elif k in _ELEM_KEYS:
            bucket["elem"] += mag / 100.0
        elif k in _PHYS_KEYS:
            bucket["phys_neg"][ _PHYS_KEYS[k] ] += (-mag) / 100.0  # 负物理 → 减配比例
        elif k in ("Magazine Capacity", "Ammo Maximum"):
            bucket["mag"] += mag / 100.0          # 弹匣容量 → 持续 DPS 占空比
        elif k == "Reload Speed":
            bucket["reload"] += mag / 100.0       # 装填速度：正=更快（reload_mult 取倒数）
        # 其余（变焦/后坐/派系/触发等）不进 DPS（对齐参考卡「伤害+0.0%」）


def _dps(w: dict, bucket: dict) -> float:
    """面板 DPS（对 Lv170 护甲 2700 靶，病毒，弱点 50%——口径照抄参考卡脚注）。"""
    dmg = w.get("damage") or {}
    total = float(dmg.get("total") or 0)
    if total <= 0:
        return 0.0
    bm = 1.0 + bucket["base"]
    cc = min(1.0, max(0.0, float(w.get("criticalChance") or 0) * (1 + bucket["cc"])))
    cm = max(1.0, float(w.get("criticalMultiplier") or 1) * (1 + bucket["cm"]))
    ms = max(1.0, 1.0 + bucket["ms"])
    fr = max(0.1, float(w.get("fireRate") or 1) * (1 + bucket["fr"]))
    crit_exp = 1.0 + cc * (cm - 1.0)
    dr = _ARMOR / (_ARMOR + 300.0)
    hs = 1.0 + _HEADSHOT_RATIO
    phys_total = sum(float(dmg.get(k) or 0) for k in ("impact", "puncture", "slash"))
    per_hit = 0.0
    if phys_total > 0:
        for k in ("impact", "puncture", "slash"):
            part = float(dmg.get(k) or 0) * bm
            part *= max(0.0, 1.0 - bucket["phys_neg"].get(k, 0.0))
            per_hit += part * (1.0 if k == "slash" else (1.0 - dr))  # 切割流血无视护甲
        elem = phys_total * bm * bucket["elem"]
        per_hit += max(0.0, elem) * (1.0 - dr) * 2.0  # 病毒对生命 ×2
    else:
        per_hit = total * bm * (1.0 - dr) + total * bm * bucket["elem"] * (1.0 - dr) * 2.0
    return max(0.0, per_hit) * hs * crit_exp * ms * fr


def _sustain_ratio(w: dict, bucket: dict) -> float:
    """持续 DPS 占空比：弹匣打空时间 /（打空时间 + 装填时间）。

    突袭/持续输出口径：弹匣越大、装填越快，实际秒伤越接近面板；反之（如 Akarius
    弹匣仅 10、装填 3.4s）持续秒伤远低于面板。紫卡「弹匣容量 +X%」放大弹匣、
    「装填速度 ±Y%」改变装填耗时，二者都直接改变真实 TTK，必须进模型，否则
    这类紫卡会出现「伤害分 0.0 / 击杀效率被压平」的伪失败（实测 2026-11-06
    阿利乌双枪 Armadex 卡：三条词条全是触发/弹匣/装填，旧模型三者贡献全 0）。
    缺弹匣或装填字段（近战/无装填武器）时回落 1.0（即面板=持续）。
    """
    mag = float(w.get("magazineSize") or 0)
    reload = float(w.get("reloadTime") or 0)
    fr = max(0.1, float(w.get("fireRate") or 1))
    if mag <= 0 or reload <= 0:
        return 1.0
    mag_mult = max(0.1, 1.0 + bucket.get("mag", 0.0))
    reload_mult = 1.0 / max(0.1, 1.0 + bucket.get("reload", 0.0))  # 装填速度正→更快
    tpm = (mag * mag_mult) / fr
    rt = reload * reload_mult
    return tpm / (tpm + rt)


def _kill_eff(dps: float, dps_base: float) -> float:
    """击杀效率 = 三硬度节点击杀/分钟的几何平均（血量按无紫卡基线校准）。"""
    if dps_base <= 0 or dps <= 0:
        return 0.0
    kills = [60.0 * dps / (dps_base * t) for t in _TARGET_TTK]
    return math.exp(sum(math.log(k) for k in kills) / len(kills))


def _sim(parsed: dict, w: dict) -> dict:
    """完整模拟：无紫卡 / 当前紫卡 / 最强紫卡 + 每词条贡献。

    伤害分与击杀效率均基于**持续 DPS**（面板 × 占空比），占空比由弹匣容量 + 装填速度
    决定。这样「触发/弹匣/装填」类紫卡也能得到非零伤害分与不被压平的击杀效率，
    而不是旧的「三者贡献全 0 → 伤害分 0.0 / 击杀效率 13.4→13.4→13.4」伪失败。
    """
    stats = parsed["stats"]
    for s in stats:
        s.setdefault("_rng", _range_of(s, parsed, _fix_of(parsed)))
    std = _gun_std_bucket(w)
    cur_b = dict(std)
    best_b = dict(std)
    _apply_riven(cur_b, stats)
    _apply_riven(best_b, stats, maxed=True)
    dps_base = _dps(w, std)
    dps_cur = _dps(w, cur_b)
    dps_best = _dps(w, best_b)
    # 持续 DPS（占空比）
    rb = _sustain_ratio(w, std)
    rc_ = _sustain_ratio(w, cur_b)
    rbest = _sustain_ratio(w, best_b)
    sdps_base = dps_base * rb
    sdps_cur = dps_cur * rc_
    sdps_best = dps_best * rbest
    contrib = {}
    for s in stats:
        b2 = dict(std)
        _apply_riven(b2, [x for x in stats if x is not s])
        d2 = _dps(w, b2) * _sustain_ratio(w, b2)
        c = (sdps_cur - d2) / d2 * 100.0 if d2 > 0 else 0.0
        contrib[s["en"] + ("-" if s["sign"] == "-" else "+")] = \
            max(0.0, c) if s["sign"] == "-" else c
    return {"dps_base": sdps_base, "dps_cur": sdps_cur, "dps_best": sdps_best,
            "contrib": contrib,
            "ke_base": _kill_eff(sdps_base, sdps_base),
            "ke_cur": _kill_eff(sdps_cur, sdps_base),
            "ke_best": _kill_eff(sdps_best, sdps_base)}


# ---------------------------------------------------------------------------
# 市场（2026-11-04 22:25 重写：按武器真实拍卖聚合 + 按词条分组推荐）
# ---------------------------------------------------------------------------
def _weapon_slug(parsed: dict) -> str:
    """紫卡武器名 → WFM riven slug。

    ⚠️ 中文武器名在 WFM riven 列表里常缺（实测「冷冻光束步枪」被解析回中文原文），
    因此**英文官方名优先**；解析不出 ASCII slug 时显式报错，绝不拿中文串当 slug 查。
    """
    en = (parsed.get("weapon") or "").strip()
    zh = (parsed.get("weapon_zh") or "").strip()
    tried = []
    for q in (en, zh):
        if not q:
            continue
        slug = (warframe._resolve_riven_weapon(q) or "").strip().lower()
        tried.append((q, slug))
        if slug and re.fullmatch(r"[a-z0-9_\-]+", slug):
            return slug
    raise RivenMarketError(
        "无法把武器名解析成 WFM riven slug：en=%r zh=%r tried=%r" % (en, zh, tried))


def _auction_price(a: dict) -> float | None:
    """挂牌价：一口价优先，缺失用起拍价；无价返回 None（不编 0）。"""
    v = a.get("buyout_price")
    if v is None:
        v = a.get("starting_price")
    if v is None:
        return None
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def _auction_attrs(a: dict) -> list[dict]:
    return ((a.get("item") or {}).get("attributes") or [])


async def _fetch_auction_page(slug: str, sort_by: str, extra: str = "",
                              allow_empty: bool = False) -> list[dict]:
    """抓一页 WFM 紫卡拍卖（按武器 + 可选词条过滤）。

    ``extra`` 是已拼好的 query 片段（``negative_stats=`` / ``positive_stats=``）。
    ``allow_empty=True`` **只**用于「精确匹配查无同组合卡」这种合法空结果；
    其余场景空样本一律显式 :class:`RivenMarketError`，绝不静默返回空冒充成功。
    """
    key = (slug, sort_by, extra)
    hit = _AUCTION_CACHE.get(key)
    if hit and time.time() - hit[0] < _WFM_PAGE_TTL:
        return hit[1]
    url = (f"{warframe.MARKET_BASE}/v1/auctions/search"
           f"?type=riven&sort_by={sort_by}&buyout_policy=direct"
           f"&weapon_url_name={slug}{extra}")
    wait = _WFM_MIN_INTERVAL - (time.time() - _WFM_LAST_REQ[0])
    if wait > 0:
        await asyncio.sleep(wait)
    _WFM_LAST_REQ[0] = time.time()
    try:
        d = await asyncio.wait_for(
            warframe._fetch(url, headers=dict(_WFM_AUCTION_HEADERS),
                            verify_ssl=False, timeout=10.0, retry=2),
            timeout=14.0)
    except Exception as e:  # noqa: BLE001 — 上抛为领域错误，不吞
        raise RivenMarketError(
            "WFM 拍卖请求失败（%s / %s%s）：%s: %s"
            % (slug, sort_by, extra[:60], type(e).__name__, e)) from e
    if not isinstance(d, dict):
        raise RivenMarketError(
            "WFM 拍卖返回 %s（不是 JSON 对象）：%s / %s" % (type(d).__name__, slug, sort_by))
    if d.get("error"):
        raise RivenMarketError("WFM 拍卖接口报错：%r / %s" % (d["error"], sort_by))
    auc = (d.get("payload") or {}).get("auctions")
    if not isinstance(auc, list):
        raise RivenMarketError(
            "WFM 拍卖返回非列表（%s）：%s / %s" % (type(auc).__name__, slug, sort_by))
    if not auc and not allow_empty:
        raise RivenMarketError(
            "WFM 拍卖返回 0 条样本（武器 %s / %s%s）；检查 slug 与 Platform 头"
            % (slug, sort_by, extra[:60]))
    _AUCTION_CACHE[key] = (time.time(), auc)
    return auc


def _dedup(pages: list[list[dict]]) -> list[dict]:
    seen, out = set(), []
    for page in pages:
        for a in page:
            k = a.get("id") or id(a)
            if k in seen:
                continue
            seen.add(k)
            out.append(a)
    return out


def _rank_of(a: dict) -> int | None:
    """WFM 紫卡等级；缺失/异常返回 None（绝不猜 0 或 8）。"""
    v = (a.get("item") or {}).get("mod_rank")
    return v if isinstance(v, int) else None


def _same_rank(auctions: list[dict], rank: int) -> list[dict]:
    """只保留同等级挂牌：rank0 未洗卡与 rank8 满级卡的价格不可直接比较。"""
    return [a for a in auctions if _rank_of(a) == rank]


def _prices_of(auctions: list[dict]) -> list[float]:
    return sorted(p for p in (_auction_price(a) for a in auctions) if p is not None)


def _robust_center(prices: list[float]) -> float:
    """抗离群中心值 = **中位数**（唯一价格聚合入口）。

    2026-11-04 23:0x：旧版用算术均值，被 66666p 挂单拉到 +3949%。
    WFM 是「卖家要价」，离群挂单极多，均值没有参考价值。
    单独抽成函数是为了让联调门禁能注入离群反例（改回 mean 必须报错）。
    """
    if not prices:
        raise RivenMarketError("价格池为空，无法计算中心值")
    return statistics.median(prices)


def _hot_counts(auctions: list[dict], positive: bool) -> dict[str, int]:
    """词条**出现频次**（只看有没有，不看价格）。

    ``price_desc``（全站最贵 100 条）**只允许**通过本函数参与计算，
    用于判断「哪些词条出现在高价卡上」；它的价格绝不进入任何溢价/基线。
    """
    g: dict[str, int] = defaultdict(int)
    for a in auctions:
        for at in _auction_attrs(a):
            if bool(at.get("positive")) == positive and at.get("url_name"):
                g[at["url_name"]] += 1
    return dict(g)


def _short(slug: str) -> str:
    return _WFM_ATTR_SHORT.get(slug, slug)


async def fetch_market_bundle(parsed: dict) -> dict:
    """按武器抓真实 WFM 挂牌 → 基线 / 推荐词条 / 类似紫卡价。

    2026-11-04 23:0x 口径修订（用户质疑「和别人数据源一样吗」之后）：

      * **价格只取普通挂牌**：``price_asc`` 低价视图 +
        ``negative_stats=<推荐负>`` 的同负词条普通挂牌，都按
        ``mod_rank == parsed.rank``（缺省 8）过滤，保证同等级可比。
      * ``price_desc``（全站最贵 100 条，实测含 66666p 挂单）**只用于词条
        热度频次**，绝不参与任何价格计算。旧版拿它算均价得到 +3949%，
        是被离群挂单拉爆的伪溢价，不是真实溢价。
      * **同分位对比**（2026-11-04 23:25 第二次修订）：WFM 公开检索实测只暴露
        「最便宜 100 / 最贵 100」两个**截断视图**——``page`` / ``mod_rank_min`` /
        ``created_at_desc`` 等参数被忽略或直接 400，拿不到代表性中间样本。
        Glaxion 未过滤页的 rank8 只有 13 条（全部 90–100P），而 ``-zoom``
        过滤页的 rank8 有 84 条（150–1700P）：拿「全量负词条分布的中位数」
        比「通用池最便宜 13 条」，会得到 +700% 的**伪溢价**。
        修法：两池各取自身 ``price_asc`` 视图里**同样条数的最低价样本**
        （``pair_k = min(n_base, n_neg)``，同一低价分位），再用中位数对比。
        实测三武器由 +700/+100/+71% 变为 +100/+50/+29%。
      * **聚合统一用中位数**（:func:`_robust_center`），不用算术均值。
      * **匹配层级显式**：``positive_stats`` + ``negative_stats`` 精确匹配
        「正负面相同」；样本 >=3 才给「类似紫卡价」，否则明确写
        「无同组合样本」，不拿别的池冒充精确匹配。
      * 价格性质 = **WM 挂牌要价，非成交价**（WFM 公开 API 只有挂单：
        实测字段 buyout_price/starting_price/top_bid/closed/winner，无成交价）。

    任何失败一律 :class:`RivenMarketError`；不回退静态权重表、不写「暂无」。
    返回 dict 兼容旧 ``auc`` 字段（n/lo/hi/med/zero）。
    """
    slug = _weapon_slug(parsed)
    rank = parsed.get("rank")
    rank = rank if isinstance(rank, int) and 0 <= rank <= 8 else 8

    asc = await _fetch_auction_page(slug, "price_asc")
    desc = await _fetch_auction_page(slug, "price_desc")   # 仅词条热度

    base_prices = _prices_of(_same_rank(asc, rank))
    if len(base_prices) < _WFM_MIN_SAMPLES:
        raise RivenMarketError(
            "WFM 同等级(rank=%d)低价挂牌有效样本 %d 条 < %d（%s）"
            % (rank, len(base_prices), _WFM_MIN_SAMPLES, slug))
    n = len(base_prices)
    med = _robust_center(base_prices)
    lo = base_prices[max(0, int(n * 0.1))]
    hi = base_prices[min(n - 1, int(n * 0.9))]
    # rank0 未洗挂牌：没有样本就写 None（卡面显示「不可估价」），
    # 绝不拿 rank8 的最低价冒充 rank0——「拿别的池冒充」属于明令禁止的兜底。
    zero_ps = _prices_of(_same_rank(asc, 0))
    zero = zero_ps[0] if zero_ps else None

    # ---- 词条热度：只用 desc 的频次，不用它的价格 ----
    neg_hot = _hot_counts(desc, False)
    pos_hot = _hot_counts(desc, True)
    if not neg_hot:
        raise RivenMarketError("WFM 高价视图没有可用的负词条样本（%s）" % slug)
    neg_slug, neg_hot_n = max(
        ((k, v) for k, v in neg_hot.items() if v >= _WFM_MIN_SAMPLES),
        key=lambda kv: kv[1], default=(None, 0))
    if neg_slug is None:
        raise RivenMarketError(
            "WFM 样本不足：没有任何负词条达到 %d 条（%s）" % (_WFM_MIN_SAMPLES, slug))
    rec_pos = [k for k, _ in sorted(pos_hot.items(), key=lambda kv: -kv[1])[:3]]

    # ---- 溢价：同负词条 + 同等级普通挂牌的**同分位中位数对比** ----
    neg_extra = "&negative_stats=" + _quote(neg_slug, safe="")
    neg_pool = await _fetch_auction_page(slug, "price_asc", neg_extra)
    neg_prices = _prices_of(_same_rank(neg_pool, rank))
    if len(neg_prices) < _WFM_MIN_SAMPLES:
        raise RivenMarketError(
            "WFM 同负词条(%s)同等级挂牌仅 %d 条 < %d（%s），不编造溢价"
            % (neg_slug, len(neg_prices), _WFM_MIN_SAMPLES, slug))
    # ⚠️ 两池都来自 price_asc 截断视图，但样本量可能差 6 倍（Glaxion 13 vs 84）。
    # 直接用 neg 全量中位数比 base 全量中位数，等于拿「整个分布」比「最便宜 13 条」，
    # 2026-11-04 23:22 实测得到 +700% 伪溢价。统一取各自最低价的 k 条再比中位数。
    pair_k = min(len(base_prices), len(neg_prices))
    # 2026-11-04 23:29 真实事故：下游「DE 词条 → WFM slug」循环曾用变量名 ``k``，
    # 把这里的 int 覆盖成 str，source 的 %d 直接 TypeError，又被失败态吞掉。
    # 变量名改唯一 + 显式类型断言，杜绝同名覆盖复发。
    if not isinstance(pair_k, int) or pair_k < _WFM_MIN_SAMPLES:
        raise RivenMarketError(
            "同分位对比样本不足或类型异常：k=%r（base=%d / neg=%d，%s），"
            "不退回不对称口径"
            % (pair_k, len(base_prices), len(neg_prices), slug))
    base_med = _robust_center(base_prices[:pair_k])
    neg_median = _robust_center(neg_prices[:pair_k])
    delta = neg_median - base_med
    pct = delta / base_med * 100.0 if base_med > 0 else 0.0

    # ---- 「正负面相同」精确匹配（DE 词条名 → WFM url_name） ----
    stats = parsed.get("stats") or []
    pos_slugs, unknown = [], []
    for s in stats:
        if s.get("sign") == "-":
            continue
        wfm_slug = _DE_TO_WFM.get(s.get("en"))
        if wfm_slug:
            pos_slugs.append(wfm_slug)
        else:
            unknown.append(s.get("en"))
    exact = {"status": "unknown", "n": 0, "median": None, "reason": ""}
    if not stats:
        exact["reason"] = "未提供当前卡词条，无法精确匹配"
    elif unknown:
        exact["reason"] = "词条未收录 WFM 映射：" + "/".join(map(str, unknown))
    elif not pos_slugs:
        exact["reason"] = "当前卡无正向词条"
    else:
        p = _quote(",".join(sorted(set(pos_slugs))), safe=",")
        pool = await _fetch_auction_page(slug, "price_asc",
                                         neg_extra + "&positive_stats=" + p,
                                         allow_empty=True)
        ps = _prices_of(_same_rank(pool, rank))
        if len(ps) >= _WFM_MIN_SAMPLES:
            exact = {"status": "ok", "n": len(ps),
                     "median": _robust_center(ps), "reason": ""}
        else:
            exact = {"status": "few", "n": len(ps), "median": None,
                     "reason": "同组合同等级样本仅 %d 条 < %d，不给估价"
                               % (len(ps), _WFM_MIN_SAMPLES)}

    match_tier = "正负面相同" if exact["status"] == "ok" else "同负词条"
    pooled = _dedup([asc, desc, neg_pool])
    unrolled_n = sum(1 for a in pooled
                     if ((a.get("item") or {}).get("re_rolls")) == 0)
    source = ("WM 挂牌要价·非成交价｜同等级 rank%d｜同分位 k=%d｜匹配：%s｜样本 %d｜未洗 %d"
              % (rank, pair_k, match_tier, len(pooled), unrolled_n))
    return {
        "status": "ok", "slug": slug, "ts": time.strftime("%Y-%m-%d %H:%M"),
        "source": source, "price_kind": "挂牌要价·非成交价",
        "rank": rank, "match_tier": match_tier, "pair_k": pair_k,
        # 兼容旧 auc 字段
        "n": n, "lo": lo, "hi": hi, "med": med, "zero": zero,
        "sample_n": len(pooled), "base": med,
        "rec_pos": [_short(ps) for ps in rec_pos],
        "rec_neg": {"slug": neg_slug, "zh": _short(neg_slug),
                    "n": pair_k, "pool_n": len(neg_prices), "median": neg_median,
                    "delta": delta, "pct": pct, "hot_n": neg_hot_n},
        "exact": exact,
        # 显式标记：最贵视图的价格绝不参与计算（联调门禁会断言 + 离群反例）
        "desc_price_used": False,
    }


def market_error_bundle(exc: BaseException) -> dict:
    """把失败**显式**编码进卡片数据：卡面显示原因，不回退静态权重、不写「暂无」。

    失败分两类，source 文案必须如实区分，避免误导用户以为接口挂了：

      * **领域拒绝** ``RivenMarketError``：请求已发出并拿到 WFM 响应，但样本不足 /
        武器 slug 解析失败，我们**主动拒绝估算** → 标「数据不足，已拒绝估算」。
        （例：phantasma rank8 仅 2 条挂单 < 阈值 3，属此类，不是网络问题。）
      * **真网络/请求异常**（连接超时、断连、429 等 aiohttp/asyncio 原生异常）：
        → 标「请求失败」。
    """
    if isinstance(exc, RivenMarketError):
        src_fail = "数据不足，已拒绝估算"
    else:
        src_fail = "请求失败"
    return {
        "status": "error",
        "error": "%s: %s" % (type(exc).__name__, exc),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "source": "WM v1 /auctions/search（%s）" % src_fail,
        "price_kind": "挂牌要价·非成交价", "rank": None, "match_tier": "不可用",
        "pair_k": 0, "n": 0, "lo": 0.0, "hi": 0.0, "med": 0.0, "zero": None,
        "sample_n": 0, "base": 0.0, "rec_pos": [], "rec_neg": None,
        "exact": {"status": "unknown", "n": 0, "median": None,
                  "reason": "市场数据获取失败，无法精确匹配"},
        "desc_price_used": False,
    }


# 2026-11-04 23:0x **删除** ``_market_lines()``：它按旧口径拼「推荐负面 XX
# +dp/+p%（高价挂牌 N 条）」，把最贵 100 条的价格写进溢价。函数已无任何调用方，
# 留着会被照抄。文案统一由 build_card() / build_card_data() 从 bundle 现算。


def _conf_grade(n: int) -> str:
    return "A" if n >= 10 else "B" if n >= 6 else "C" if n >= 5 else "D"


# ---------------------------------------------------------------------------
# 评分与推荐（伤害维度 = 模拟；市场维度 = 启发式权重；大佬维度如实「暂无」）
# ---------------------------------------------------------------------------
def _letter(score: float) -> str:
    if score >= 85:
        return "S"
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    if score >= 35:
        return "C"
    if score >= 20:
        return "D"
    return "E"


def _grade(d: float) -> str:
    """评级字母：入参 ``d`` = 卡面打印的偏差（%），先换算「区间百分位」再分档。

    理论区间恒为标准值 ±10%，故 ``p = (10 + d) / 20``。

    刻度由**参考机器人两张卡共 10 个数据点**反推（2026-11-02）：
        +5.728% → A      +5.185% → A−     +6.521% → A
        +3.542% → A−     +3.901% → A−     +1.677% → B+
        −0.988% → B      −2.914% → B−
        综合 +3.910% → A−                综合 +1.753% → B+
    因此档位边界（p）为 .30/.42/.54/.66/.78（各 0.12 宽）+ A+ .90 + S .96，
    10 个点全部命中。

    ⚠️ 注意参考卡的分档只吃**偏差幅度**、不看词条正负——所以负词条惩罚越大
    （|偏差| 越大）反而评级越好。这是参考机器人自身的逻辑缺陷，此处**照抄以保持
    同一张卡两边字母一致**；进度条同样用幅度，卡内自洽。若哪天要改成方向敏感，
    只需把调用处传入 ``-d``（负词条）即可，档位表不用动。
    """
    p = (10.0 + float(d)) / 20.0
    if p >= 0.96:
        return "S"
    if p >= 0.90:
        return "A+"
    if p >= 0.78:
        return "A"
    if p >= 0.66:
        return "A-"
    if p >= 0.54:
        return "B+"
    if p >= 0.42:
        return "B"
    if p >= 0.30:
        return "B-"
    if p >= 0.18:
        return "C+"
    if p >= 0.08:
        return "C"
    return "D"


def _scores(parsed: dict, sim: dict) -> dict:
    stats = parsed["stats"]
    pos = [s for s in stats if s["sign"] != "-"]
    neg = [s for s in stats if s["sign"] == "-"]
    gain = sim["dps_cur"] / sim["dps_base"] - 1.0 if sim["dps_base"] > 0 else 0.0
    gain_max = sim["dps_best"] / sim["dps_base"] - 1.0 if sim["dps_base"] > 0 else 0.0
    dmg_score = gain / gain_max * 100.0 if gain_max > 0 else 0.0
    dmg_score = max(0.0, min(100.0, dmg_score))
    wsum = wsum_roll = 0.0
    for s in pos:
        mw = float(STAT_MARKET.get(s["en"], 30))
        rng = s.get("_rng")
        p = 0.5
        if rng and rng[1] > rng[0]:
            v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
            p = min(1.0, max(0.0, (v - rng[0]) / (rng[1] - rng[0])))
        wsum += mw
        wsum_roll += mw * (0.6 + 0.4 * p)
    mkt = wsum_roll / wsum * 100.0 if wsum else 0.0
    for s in neg:
        mkt -= (100.0 - float(NEG_MARKET.get(s["en"], 20))) * 0.22
    mkt = max(0.0, min(100.0, mkt))
    friendly = all(NEG_MARKET.get(s["en"], 20) >= 60 for s in neg)
    return {"gain": gain, "gain_max": gain_max, "dmg": dmg_score,
            "mkt": mkt, "type": "市场卡" if friendly else "伤害卡",
            "total": dmg_score}


def _recs(parsed: dict, w: dict) -> dict:
    """推荐洗卡 / 负面 / 致命负（伤害维度 = 模拟边际；市场维度 = 启发式权重）。"""
    _, base_tbl, _ = _load_riven_tables()
    cand = [e for e in (base_tbl.get(parsed["type"]) or {})
            if e not in ("Damage to Grineer", "Damage vs Grineer",
                         "Damage to Corpus", "Damage vs Corpus",
                         "Damage to Infested", "Damage vs Infested")]

    def _marginal(en: str) -> float:
        """该词条 +100% 对标准配卡 DPS 的边际贡献（排序启发）。"""
        if not w:
            return 0.0
        b = _gun_std_bucket(w)
        _apply_riven(b, [{"en": en, "value": 100.0, "unit": "%", "sign": "+"}])
        return _dps(w, b)

    cand_dps = sorted(cand, key=_marginal, reverse=True)
    rec_pos = " + ".join(_stat_zh(e) for e in cand_dps[:3]) or "暂无"
    #: 零影响负词条优先级（用词与顺序对齐参考卡：变焦/弹药最大值/武器后坐力）
    _NEG_PRIORITY = ["Zoom", "Ammo Maximum", "Weapon Recoil", "Status Duration",
                     "Projectile Speed", "Magazine Capacity", "Combo Duration",
                     "Initial Combo", "Damage to Grineer", "Damage to Corpus",
                     "Damage to Infested"]
    zero_neg = [e for e in _NEG_PRIORITY
                if e in cand and NEG_DPS_IMPACT.get(e, 10.0) <= 0.5]
    rec_neg = "/".join(_neg_zh(e) for e in zero_neg[:3]) or "暂无"
    deadly = sorted((e for e in cand if NEG_DPS_IMPACT.get(e, 10.0) >= 9.0),
                    key=lambda e: -NEG_DPS_IMPACT.get(e, 10.0))
    rec_deadly = "/".join(_neg_zh(e) for e in deadly[:3]) or "暂无"
    mkt_pos = sorted(cand, key=lambda e: -float(STAT_MARKET.get(e, 20)))
    return {"pos": rec_pos, "neg": rec_neg, "deadly": rec_deadly,
            "mkt_pos": [_short_stat(e) for e in mkt_pos[:3]]}


# 2026-11-04 22:25 **删除** ``_market_tiers()``：它按 STAT_MARKET/NEG_MARKET 静态
# 热度分产出「T0：…(热80)」，曾在 build_card() 里冒充「基于市场价差」写进推荐词条。
# 静态分不是市场数据，也没有检查能拦住它被再次接回卡面，所以按 prove-it「让它没
# 地方犯」直接删掉实现，而不是留着不调用。
# 真实市场推荐的唯一入口：fetch_market_bundle() → fetch_market_for_card() → recs。
# STAT_MARKET/NEG_MARKET 仍仅供 _scores() 的启发式「市场维度评分」，不得用于卡面
# 的价格、推荐洗卡、推荐负面、市场参考四行。


# ---------------------------------------------------------------------------
# 卡片组装（版式/文案逐字对齐参考卡）
# ---------------------------------------------------------------------------
def _dots(disp: float) -> str:
    return ""  # 旧分档实现已删除，任何调用点都拿不到星级点阵


def _fit_disp(parsed: dict, fix: dict) -> float | None:
    """由卡面词条反推倾向（通用兜底，**不特化任何武器**）。

    每条词条给出倾向可行区间 [v/(|base|·F·1.1), v/(|base|·F·0.9)]，
    真实倾向必须同时落在**所有**词条的区间内。当倾向表值不在 ≥2 条
    区间的交集中（倾向数据过期 / 变体倾向不同 / 表错行），取交集内
    中点替代；交集为空或词条不足时返回 None（维持表值）。

    实测案例（Nukor 卡，2026-10）：表值 1.45 时暴击/派系/弹匣三条词条
    全部落在理论区间外；四条词条区间交集 [0.498, 0.505] 非空 → 反推
    倾向 ≈0.50（恰为 Kuva Nukor 的 DE 官方 omegaAttenuation），
    四条词条全部自然落入区间，无需任何数值"修复"。
    """
    _, base_tbl, _ = _load_riven_tables()
    t = base_tbl.get(parsed["type"]) or {}
    omega = float(parsed.get("disp") or 1.0)
    ivs: list[tuple[float, float]] = []
    for s in parsed["stats"]:
        base = t.get(s["en"])
        if base is None and " vs " in s["en"]:
            base = t.get(s["en"].replace(" vs ", " to "))
        if base is None and " to " in s["en"]:
            base = t.get(s["en"].replace(" to ", " vs "))
        if not base:
            continue
        v = (s["value"] - 1.0) if s["unit"] == "x" else abs(s["value"])
        f = abs(fix["curse"]) if s["sign"] == "-" else fix["buff"]
        bf = abs(base) * f
        if bf <= 0:
            continue
        ivs.append((v / (bf * 1.1), v / (bf * 0.9)))
    if len(ivs) < 2:
        return None
    lo, hi = max(x[0] for x in ivs), min(x[1] for x in ivs)
    if lo > hi or lo <= 0:
        return None
    if lo <= omega <= hi:
        return None  # 表值本就自洽 → 不动
    return (lo + hi) / 2.0


#: base 值 ≥ 此阈值 → 该词条是百分比类（秒/倍率类词条的 base 都远小于 20）
_PCT_BASE_MIN = 20.0


def _normalize_stats(parsed: dict, fix: dict) -> None:
    """词条值规范化 —— ``build_card``（文字卡）与 ``build_card_data``（图片卡）共用。

    两条输出路径必须走同一套规范化，否则「同一张卡，文字版和图片版数字不一样」。

    只做两件事：
      1. **单位推断**：OCR 常整行丢掉「%」，使 65.3 变成"非百分比"。若该词条在官方
         base 表里的基础值 ≥20（百分比词条都是几十到几百；秒/倍率词条远小于此），
         补成 ``%``。
      2. 预算好 ``_rng`` 缓存（后续多处复用）。

    ⚠️ **不在这里按理论区间修数值**。历史版本会「值低于区间下界就 ×10 / 补首位」，
    但第三方语料实测：**未满级卡**（Rank 0~3）与**旧倾向卡**的真实数值天然远低于
    「1.2 倾向 + 满级」区间——实测 73.1% 暴击几率被"修"成 173.1%，11.1% 被改成 111%。
    数值可信度改由 ``riven._refine_values`` 用**卡内共识尺度**判定（不看区间）。
    """
    _, base_tbl, _ = _load_riven_tables()
    tbl = base_tbl.get(parsed.get("type") or "") or {}
    for s in parsed["stats"]:
        if s["unit"] == "":
            _base = abs(tbl.get(s["en"]) or tbl.get(s["en"].replace(" vs ", " to ")) or 0)
            if _base >= _PCT_BASE_MIN:
                logger.info(f"riven_card_unit_infer: {s['en']} '' -> '%' (base={_base:.1f})")
                s["unit"] = "%"
        s.setdefault("_rng", _range_of(s, parsed, fix))


def build_card(parsed: dict, market: dict) -> str:
    """紫卡分析卡（guide 标记文本，``_send_guide`` 渲染）。

    ``market`` **必填**，且必须是 :func:`fetch_market_for_card` 的返回值
    （成功态 ``status=ok`` 来自真实 WFM 拍卖；失败态 ``status=error`` 带显式原因）。

    2026-11-04 22:25 修复（本文件「市场」段 + 本函数）：旧签名 ``auc=None`` 允许
    调用方完全不传市场数据，卡面就永远写「WM 拍卖样本不足，暂无」——正是用户
    截图里的现象。现在漏传直接 :class:`RivenMarketError`，且市场行/推荐词条一律
    用真实挂牌数据；失败时把**错误原因**写在卡面上，不再用 ``STAT_MARKET`` 热度分
    或「词条价差预估价」冒充市场价。
    """
    if not isinstance(market, dict) or not market.get("status"):
        raise RivenMarketError(
            "build_card 需要 fetch_market_for_card() 的 market dict（含 status），收到 %r"
            % (market,))
    mkt = market
    _load_riven_tables()
    stats = parsed["stats"]
    fix = _fix_of(parsed)
    # 倾向自洽校验（通用规则）：词条与倾向表值矛盾时，以卡面词条反推倾向。
    # 放在最前——后续 _rng / ocr_val_fix / 模拟 / 评分全部依赖 parsed["disp"]。
    _fitted = _fit_disp(parsed, fix)
    if _fitted is not None:
        logger.warning(f"riven_card_disp_fit: {parsed.get('weapon')} disp "
                       f"{parsed.get('disp')} -> {_fitted:.3f} (表值与卡面词条矛盾)")
        # 反推倾向会改动区间/偏差/评分，必须在卡面上显式告知——不能静默替换官方数据。
        parsed["_disp_fitted"] = (float(parsed.get("disp") or 1.0), _fitted)
        parsed["disp"] = _fitted
    _normalize_stats(parsed, fix)
    w = _weapon_raw(parsed["weapon"]) or {}
    parsed["_w"] = w
    sim = _sim(parsed, w) if w.get("damage") else None
    sc = _scores(parsed, sim) if sim else None
    rec = _recs(parsed, w) if sim else None
    weapon_zh = parsed.get("weapon_zh") or parsed["weapon"]
    rname = parsed.get("riven_name") or "-"
    rank = parsed.get("rank")
    omega = float(parsed.get("disp") or 1.0)

    L: list[str] = []
    L.append("@ttl|紫卡分析|search")
    L.append("@sub|你的紫卡详细分析数据如下：")
    L.append(f"【{weapon_zh} {rname}】 等级:{rank if rank is not None else '?'} "
             f"倍率:{omega:.2f}(全武器统一)")
    if parsed.get("_disp_fitted"):
        _o, _n = parsed["_disp_fitted"]
        L.append(f"@alert|*注意：卡面词条与统一倍率 {_o:.2f} 对不上，已按卡面反推 "
                 f"{_n:.2f} 计算。原因通常是「旧倾向下洗出的老卡」或「未满级卡」"
                 f"（本图未读到有效等级徽章），两者都会让数值整体偏离 1.2/满级区间。")
    # ---- 词条行：+104.5% 暴击伤害 (91.0%-111.4%|高2.18%) ----
    for s in stats:
        if s["unit"] == "x":
            # 派系伤害：卡面显示为倍率（x1.19），无正负号（负向 = 倍率 <1）
            val = f"x{s['value']:.2f}"
        else:
            val = f"{'-' if s['sign'] == '-' else '+'}{_fmt_val(s)}"
        rng = s.get("_rng")
        if rng:
            lo, hi = rng
            mid = _mid(s, parsed, fix)
            rng_txt = _fmt_range(lo, hi, s["unit"], s["sign"] == "-")
            # 统一到"加成量"空间：x 类词条的 value 是倍率（1.19 = +0.19），
            # 而范围表/中值都是加成空间——直接拿倍率比会得出「高92%」式荒谬偏差
            _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
            _signed = -_v if s["sign"] == "-" else _v
            dev = _dev_text(_signed, mid or lo)
            L.append(f"@rstat|{val}|{s['zh']}|({rng_txt}|{dev})")
        else:
            L.append(f"@rstat|{val}|{s['zh']}|(无范围表)")
    L.append("@foot|*提示：高低值上下限为±11%，越靠近11%越极端。")
    # ---- 综合评级（参考卡口径：各词条**卡面偏差**的算术均值） ----
    # 参考卡实测：伯斯顿 (+3.542+3.901+6.521+1.677)/4 = +3.910% → A−
    #             玻之武杖 (+5.728+5.185-2.914-0.988)/4 = +1.753% → B+
    # 所以既不是「绝对偏差均值」也不是「方向敏感均值」，而是**打印值直接求平均**。
    _devs: list[float] = []
    for s in stats:
        rng = s.get("_rng")
        if not rng:
            continue
        _midv = _mid(s, parsed, fix)
        if not _midv:
            continue
        _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
        _devs.append((abs(_v) / _midv - 1.0) * 100.0)
    _overall = sum(_devs) / len(_devs) if _devs else 0.0
    L.append(f"@score|·紫卡综合评分 - |{_overall:.3f}({_grade(_overall)})"
             f"| - {parsed['type']}系（{len(stats)}词条偏差均值）")
    # ---- 分类评分 / 每词条贡献（伤害模拟 + 市场权重，DPS 面板存在时才有） ----
    if sc and sim:
        dmg_txt = f"{sc['dmg']:.1f} √" if sc["dmg"] >= 60 else f"{sc['dmg']:.1f}"
        L.append(f"分类评分：大佬 暂无 | 伤害 {dmg_txt} | 市场 {sc['mkt']:.1f}")
        L.append(f"伤害计算：配装增益 {sc['gain'] * 100:+.1f}%，"
                 f"参考最大增益 {sc['gain_max'] * 100:+.1f}%，"
                 f"伤害分 {sc['gain'] * 100:.1f}/{sc['gain_max'] * 100:.1f}={sc['dmg']:.1f}%")
        for s in stats:
            key = s["en"] + ("-" if s["sign"] == "-" else "+")
            c = sim["contrib"].get(key, 0.0)
            mkt_v = (100.0 - float(NEG_MARKET.get(s["en"], 20))) if s["sign"] == "-" \
                else float(STAT_MARKET.get(s["en"], 30))
            rng = s.get("_rng")
            _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
            _signed = -_v if s["sign"] == "-" else _v
            ptxt = (f" 数值{_percentile(_signed, rng[0], rng[1], s['sign'] == '-'):.1f}%"
                    if rng else "")
            L.append(f"@chipline|{s['zh']}|大佬暂无 | 伤害{c:+.1f}%{ptxt} | 市场+{mkt_v:.1f}%")
        L.append("@foot|*伤害：配卡不变、只去掉该词条时击杀效率的下降；词条之间有协同，"
                 "各项相加不等于总增益。")
    else:
        L.append("@score|·伤害/市场评分 - |暂无| - 武器面板缺失")
    # ---- 市场（2026-11-04 23:0x 修订口径：普通挂牌中位数，最贵视图不计价） ----
    ok = mkt.get("status") == "ok"
    if ok:
        L.append(f"价格预计：{mkt['lo']:.0f}~{mkt['hi']:.0f}P|中位{mkt['base']:.0f}P"
                 f"（同等级 rank{mkt['rank']} 低价视图 {mkt['n']} 条 / {mkt['ts']}）")
        L.append(f"匹配层级：{mkt['match_tier']}|样本：{mkt['sample_n']}条"
                 f"|同分位 k={mkt['pair_k']}|最近观测：{mkt['ts']}"
                 f"|置信度：{_conf_grade(mkt['n'])}")
        if mkt.get("zero") is not None:
            L.append(f"零洗基价：{mkt['zero']:.0f}P（rank0 最低挂牌）")
        else:
            L.append("零洗基价：无 rank0 挂牌样本，不可估价（不用 rank8 冒充）")
        ex = mkt.get("exact") or {}
        if ex.get("status") == "ok":
            L.append(f"类似紫卡（正负面相同）：{ex['n']} 条｜中位 {ex['median']:.0f}P"
                     f"（挂牌价·非成交）")
        else:
            L.append(f"类似紫卡（正负面相同）：不可估价（"
                     f"{ex.get('reason') or '无同组合样本'}），未用其他层级冒充")
    else:
        err = mkt.get("error") or "未知原因"
        L.append(f"价格预计：获取失败（{err}）")
        L.append("匹配层级：-|样本：0条|最近观测：-|置信度：-")
        L.append("零洗基价：获取失败")
    L.append(f"@foot|*市场来源：{mkt.get('source') or 'WM'}"
             f"｜{mkt.get('slug') or '-'}｜{mkt.get('ts') or ''}")
    L.append("@alert|*注意：伤害计算仅供参考，不要只根据评分买卖，多听群友意见。")
    # ---- 推荐洗卡词条（市场维度 = 最贵 100 条的词条频次，不参与计价） ----
    L.append("@title|·推荐洗卡词条")
    L.append("基于大佬推荐 - 暂无")
    L.append(f"基于伤害计算 - {rec['pos'] if rec else '暂无'}")
    if ok:
        pos_txt = "、".join(mkt.get("rec_pos") or []) or "样本不足"
        L.append(f"基于市场价差 - {pos_txt}（最贵 100 条词条频次，不参与计价）")
    else:
        L.append(f"基于市场价差 - 不可用（{mkt.get('error') or '市场数据获取失败'}）")
    # ---- 推荐负面词条 ----
    L.append("@title|·推荐负面词条")
    L.append("基于大佬推荐 - 暂无")
    L.append(f"基于伤害计算 - {rec['neg'] if rec else '暂无'}")
    neg = mkt.get("rec_neg") or {}
    if neg:
        L.append(f"基于市场价差 - {neg['zh']} {neg['delta']:+.1f}p/{neg['pct']:+.0f}%"
                 f"（同分位 rank{mkt['rank']} 各 {neg['n']} 条·中位数，"
                 f"挂牌价非成交价）")
    else:
        L.append(f"基于市场价差 - 不可用（{mkt.get('error') or '市场数据获取失败'}）")
    # ---- 致命负词条 ----
    L.append("@title|·致命负词条")
    L.append("基于大佬推荐 - 暂无")
    L.append(f"基于伤害计算 - {rec['deadly'] if rec else '暂无'}")
    L.append("基于市场价差 - WM 挂牌不含致命负标注，无法给出市场结论")
    # ---- 模拟说明（逐字对齐参考卡脚注口径） ----
    L.append("@foot|*靶子：Lv170 钢铁之路凶魂百夫长（去掉天生超宏，护甲 2700 参与）；"
             "弱点命中 50%，其余为身体命中；血量按本武器无紫卡基线的身体命中击杀时间取"
             "三个硬度节点（1.4s / 4.5s / 14.3s），每个节点 30s 窗口回放，击杀效率 取加权"
             "几何平均并计入血量展布；异常从零层叠起；同一武器的所有紫卡共用这组靶，"
             "增益可直接比较")
    L.append("@foot|*开火模式：使用模式 1，其余模式仅展示")
    L.append(f"@foot|*模拟武器 {weapon_zh}：弱点命中 50%，元素固定为病毒，异常从零层叠起，正常装填")
    # ---- 当前配卡 + 击杀效率 ----
    build = list(_MELEE_BUILD if parsed["type"] == "Melee"
                 else _BUILD.get(parsed["type"] or "Rifle", _BUILD["Rifle"]))
    chips = build[:7] + ["当前紫卡"]
    L.append(f"@title|·当前配卡 - 需极化{len(chips)}次 （含当前紫卡）")
    L.append("@chips|" + "|".join(chips))
    if sim and sc:
        L.append(f"击杀效率：无紫卡 {sim['ke_base']:.1f} → 当前紫卡 {sim['ke_cur']:.1f} "
                 f"→ 最强紫卡 {sim['ke_best']:.1f}")
        L.append(f"紫卡增益：当前紫卡 {sc['gain'] * 100:+.1f}%→最强紫卡 {sc['gain_max'] * 100:+.1f}%")
    else:
        L.append("击杀效率：暂无（武器面板数据缺失）")
    L.append("@foot|*击杀效率：模拟条件下每分钟可击杀的敌人数，数值越高表示清怪越快；"
             "仅适合在相同靶子和计算条件下比较。")
    # ---- footer 指令（版式对齐参考卡，指令换成本机器人可用口令） ----
    L.append("[指令]查看DE官方紫卡价格：")
    L.append(f"@cmd|#wf 价格 {weapon_zh}")
    L.append("[指令]查看类似紫卡价格：")
    L.append(f"@cmd|#wf 紫卡 {weapon_zh}")
    short_tags = " ".join(_short_stat(s["en"]) for s in stats if s["sign"] != "-") + " 带负"
    L.append(f"rm/wm? 格式：{short_tags} {weapon_zh}")
    L.append("@foot|PC | qq_bridge Warframe 助手 善用右键/长按提取文字")
    return "\n".join(L)


#: 武器类别中文名（参考卡「步枪 MR 0 满级」的第一个词）
_TYPE_ZH = {
    "Rifle": "步枪", "Shotgun": "霰弹枪", "Pistol": "手枪", "Melee": "近战",
    "Kitgun": "组合枪", "Zaw": "Zaw", "Archgun": "重武器",
}


def build_card_data(parsed: dict, market: dict,
                    weapon_image: str | None = None,
                    now: str | None = None) -> dict:
    """把 ``parsed`` 转成「紫卡分析卡」图片模板的数据结构（与参考卡 1:1 对应）。

    在原有 4 列（数值/区间/偏差/评级）基础上，按参考卡补齐：
      * 顶部「倍率 1.20 · 全武器统一」（DE 44.1.3 后不再画星级圆点）；
      * 标题旁「Lv N」等级徽章；
      * 每条词条 **彩色圆形图标**（按词条类别取色，对齐参考卡）；
      * 每条词条右侧「低值 均值 X.X% : 高/低 X.X% 高值」统计行（参考卡同款）；
      * 底部「市场参考」+「提示（±10%）」两行（参考卡同款脚注）。


    与 :func:`build_card`（文字卡）同源同公式，只是输出结构化字段：
      * 每条词条给出 value / name / range / dev（**三位小数**，对齐参考卡）/
        grade（**每条独立评级**，参考卡有）/ bar（进度条 0~1）/ neg；
      * 顶部给出综合评级 + 综合偏差（各词条**卡面偏差**的算术均值，
        参考卡 +3.910% / +1.753% 均此口径）；
      * ``meta`` = 「步枪 MR 0 满级」，MR 取紫卡自身段位要求（OCR 的「段位 N」）。

    ⚠️ 与 ``build_card`` 必须共用 :func:`_normalize_stats`（单位推断 + 漏读修复），
    否则同一张卡的文字版与图片版会出现不同数值（实测 65.3 被读成 6.3 时尤甚）。

    2026-11-04 22:25 修复：``market`` 改为**必填位置参数**（旧 ``auc=None`` 可省略，
    图片卡就是这么把市场/推荐词条整块丢掉的）。漏传直接 TypeError/RivenMarketError，
    不允许「没数据就写暂无」这种静默降级。``recs`` 结构化输出供 PNG 模板绘制
    「市场参考 / 推荐洗卡 / 推荐负面 / 数据来源」四行。
    """
    if not isinstance(market, dict) or market.get("status") not in ("ok", "error"):
        raise RivenMarketError(
            "build_card_data 需要 fetch_market_for_card() 的 market dict"
            "（status=ok/error），收到 %r；旧版图片卡漏传市场数据是"
            "2026-11-04 用户截图的根因" % (market,))
    _load_riven_tables()
    stats = parsed["stats"]
    fix = _fix_of(parsed)
    _fitted = _fit_disp(parsed, fix)
    if _fitted is not None:
        parsed["_disp_fitted"] = (float(parsed.get("disp") or 1.0), _fitted)
        parsed["disp"] = _fitted
    _normalize_stats(parsed, fix)

    rows: list[dict] = []
    devs: list[float] = []
    for s in stats:
        rng = s.get("_rng")
        neg = s["sign"] == "-"
        if s["unit"] == "x":
            val_txt = f"x{s['value']:.2f}"
        else:
            val_txt = f"{'-' if neg else '+'}{_fmt_val(s)}"
        row = {"value": val_txt, "name": s["zh"], "sign": "-" if neg else "+",
               "range": "", "dev": "", "grade": "", "bar": 0.0, "neg": neg,
               # num = 原始数值（评测/断言用；模板只用 value 字符串）
               "num": round(float(s["value"]), 4),
               # 新增：图标配色 + 参考卡式「低/均值/高」三值 + 方向词（模板绘制右侧统计行）
               "icon": _stat_icon_color(s["en"]),
               "low": "", "mean": "", "high": "", "dev_dir": "", "dev_mag": ""}
        if rng:
            lo, hi = rng
            mid = _mid(s, parsed, fix)
            # 区间按**单位**排版：% 带百分号、倍率换算到 x 空间、平坦值（攻击范围）
            # 不带任何单位。旧实现一律拼 "%"，把「+2.3 攻击范围」写成「2.3%」、
            # 把「x0.63」写成「0.5%」，卡面直接是错的。
            if s["unit"] == "x":
                a, b = (1 - hi, 1 - lo) if neg else (1 + lo, 1 + hi)
                row["range"] = f"x{a:.2f} − x{b:.2f}"
                row["low"] = f"x{1 - hi:.2f}" if neg else f"x{1 + lo:.2f}"
                row["high"] = f"x{1 - lo:.2f}" if neg else f"x{1 + hi:.2f}"
                row["mean"] = f"x{1 - mid:.2f}" if neg else f"x{1 + mid:.2f}"
            elif s["unit"] == "%":
                row["range"] = f"{abs(lo):.1f}% − {abs(hi):.1f}%"
                row["low"] = f"{abs(lo):.1f}%"
                row["high"] = f"{abs(hi):.1f}%"
                row["mean"] = f"{abs(mid):.1f}%"
            else:
                row["range"] = f"{abs(lo):.1f} − {abs(hi):.1f}"
                row["low"] = f"{abs(lo):.1f}"
                row["high"] = f"{abs(hi):.1f}"
                row["mean"] = f"{abs(mid):.1f}"
            if mid:
                _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
                dev = (abs(_v) / mid - 1.0) * 100.0
                devs.append(dev)
                row["dev"] = f"{dev:+.3f}%"
                row["grade"] = _grade(dev)
                # 进度条 = 该数值在标准值 ±10% 区间里的位置（参考卡实测吻合）
                row["bar"] = max(0.0, min(1.0, (dev + 10.0) / 20.0))
                row["dev_dir"] = "高" if dev >= 0 else "低"
                row["dev_mag"] = f"{abs(dev):.1f}%"
        rows.append(row)

    neg_ct = sum(1 for s in stats if s["sign"] == "-")
    pos_ct = len(stats) - neg_ct
    overall = sum(devs) / len(devs) if devs else 0.0
    type_zh = _TYPE_ZH.get(parsed.get("type", ""), parsed.get("type", ""))
    mr = parsed.get("mr")
    meta = f"{type_zh} MR {int(mr) if isinstance(mr, int) else 0} 满级"
    omega = float(parsed.get("disp") or 1.0)
    rank = parsed.get("rank")
    # ---- 市场 / 推荐词条区块（2026-11-04 23:0x 口径修订，代码位置：本函数末段） ----
    # 价格 = 同等级普通挂牌**中位数**；最贵 100 条只用于词条频次，绝不参与计价。
    # 失败态把错误原因原样写进卡面，绝不写「暂无」冒充成功、不回退静态权重表。
    if market["status"] == "ok":
        market_txt = (f"WM 低价视图中位 {market['base']:.0f}P｜区间 "
                      f"{market['lo']:.0f}–{market['hi']:.0f}P"
                      f"（同等级 rank{market['rank']} 样本 {market['n']} / {market['ts']}）")
        neg = market["rec_neg"]
        ex = market["exact"]
        if ex["status"] == "ok":
            exact_txt = (f"正负面相同 {ex['n']} 条｜中位 {ex['median']:.0f}P"
                         f"（挂牌价·非成交）")
        else:
            exact_txt = f"不可估价：{ex['reason']}（未用其他层级冒充）"
        recs = {
            "status": "ok",
            "market": market_txt,
            "pos": "、".join(market["rec_pos"]) + "（最贵 100 条词条频次·不计价）",
            "neg": (f"{neg['zh']} {neg['delta']:+.1f}p/{neg['pct']:+.0f}%"
                    f"（同分位 rank{market['rank']} 各 {neg['n']} 条·中位数·挂牌非成交）"),
            "exact": exact_txt,
            "source": f"{market['source']} · {market['slug']} · {market['ts']}",
        }
    else:
        err = market.get("error") or "未知原因"
        market_txt = f"获取失败（{err}）"
        recs = {
            "status": "error",
            "market": market_txt,
            "pos": "不可用（市场数据获取失败）",
            "neg": "不可用（市场数据获取失败）",
            "exact": "不可用（市场数据获取失败）",
            "source": f"{market.get('source') or 'WM'} · {market.get('ts') or ''}",
            "error": err,
        }

    return {
        "brand": "紫卡分析",
        "corner": "统一倍率",
        # 现行统一倍率（不再传星级点阵）
        "disp": omega,
        "disp_dots": "",  # 星级点阵已废弃
        "disp_value": f"{omega:.2f}",
        # 等级徽章（参考卡「Lv 8」）
        "level": int(rank) if isinstance(rank, int) else None,
        "weapon_zh": parsed.get("weapon_zh") or parsed["weapon"],
        "weapon_en": parsed["weapon"],
        "riven_name": parsed.get("riven_name") or "",
        "weapon_image": weapon_image,
        "weapon_type": parsed.get("type"),
        "overall_grade": _grade(overall),
        "overall_pct": f"{overall:+.3f}%",
        "layout": f"{pos_ct} 正 {neg_ct} 负",
        "meta": meta,
        "stats": rows,
        "market": market_txt,
        "recs": recs,
        "hint": "偏差以对应倾向的理论中值为基准，正常范围为 ±10%（卡面取整可能略有偏差）。",
        "foot_left": "紫卡分析",
        "foot_right": now or time.strftime("%Y-%m-%d %H:%M"),
    }


async def fetch_market_for_card(parsed: dict) -> dict:
    """**唯一市场入口**（2026-11-04 22:25，代码位置：本文件末尾）。

    ``riven.py::riven_img_analyze_full`` 只调这里一次，把同一份快照同时喂给文字卡
    :func:`build_card` 和图片卡 :func:`build_card_data`，避免两卡数据不一致，也避免
    旧版「一处抓、一处干脆不抓」。

    :func:`fetch_market_bundle` 对 ANY 失败（网络、UA、空样本、样本不足、slug 解析
    失败）都 ``raise RivenMarketError``；这里把异常**显式编码**成 ``status=error``
    交给卡面显示原因。这不是降级：成功态只用真实挂牌数据，失败态不产生任何价格/
    推荐，也绝不回退 ``STAT_MARKET`` 静态表。不直接 raise 到 dispatcher，是为了让
    「市场获取失败」这件事在卡面上可见，而不是让用户收到与紫卡无关的空回复。
    """
    try:
        return await fetch_market_bundle(parsed)
    except Exception as e:  # noqa: BLE001 — 转成可见失败态，不吞、不伪造成功
        # nonebot 的 logger 是 Loguru 风格，不认 logging 的 %s 占位符；
        # 用 f-string 才能把真实错误原因写进日志（2026-11-04 22:47 429 联调发现）。
        logger.error(f"riven_market_failed: {type(e).__name__}: {e}")
        return market_error_bundle(e)


async def build_card_async(parsed: dict, market: dict | None = None) -> str:
    """文字卡组装；``market`` 由调用方统一抓一次传入（缺省时走唯一入口现抓）。

    2026-11-04 22:25 修复：旧版调用**已被删除的** ``_auction_stats()``，NameError
    被 ``except`` 吞掉 → ``auc=None`` → 卡面永远「WM 拍卖样本不足，暂无」。
    """
    if market is None:
        market = await fetch_market_for_card(parsed)
    return build_card(parsed, market=market)
