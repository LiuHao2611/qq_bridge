# -*- coding: utf-8 -*-
"""bounty_model.py —— 把 warframe.bounties_card_data() 适配成参考图的内容模型。

用户 2026-11-08 要求：「严格按照参考图去做，背景不用抄，只抄内容，全量对齐，
禁止遗漏」。这里的「内容」指**信息架构**：8 张卡、每张卡的标题 / 橙色轮换标题 /
两列奖励物品 / 绿色「赏金N」徽章 / Lv 徽章 / 任务类型词 / 目标行 / 底部剩余时间 /
脚注 / 平台与来源。具体数值与任务名一律来自实时数据，不把参考图快照写死。

数据源契约（warframe.py::_bounty_card_data，本轮不改该文件）：
    title / remain / rotation{rot,rot_rewards,vault,vault_rewards}
    worlds[{name,label,items,jobs[{lvl,type,name,narmer,icon,color}]}]
    deep{name,tag,title,detail,icon,color}
    factions[{name,jobs[{lvl,name,type,target,icon,color}]}]

参考图 → 数据契约的对应关系（这是本次「全量对齐」的核心）：
    合一众赏金轮换 B  ← rotation.rot + rotation.rot_rewards
    深矿赏金           ← deep（tag+title / 等级段 / 阶段 / 最终任务）
    地球赏金           ← worlds[地球赏金] 的 label + items + jobs
    金星赏金           ← worlds[金星赏金] 的 label + items + jobs
    火卫二赏金         ← worlds[火卫二赏金] 的 jobs + rotation.vault/vault_rewards
                          （隔离库轮换归火卫二，参考图即如此，旧模板却把它塞进顶部 pill）
    圣所/扎里曼/六人组 ← factions 三条，三行式（赏金徽章 / 类型+任务 / 目标）

两条**显式预留**，不编造数据：
  * ``job["tier"]``     —— 数据源给出真实赏金阶段编号时优先使用；缺省时按
                           ``_TIER_FALLBACK``（参考图口径的展示序号）呈现，保证
                           绿色「赏金N」徽章这一内容层级不缺失。
  * ``job["objective"]``/``job["mission"]`` —— 目标行 / 任务名。数据源补齐后
                           自动直读，无需改渲染层；缺省时目标行省略，不编文案。
"""
from __future__ import annotations

import re

from . import bounty_icons

# 参考图 8 张卡的行式网格顺序：每两个一组 = 一行（左列, 右列）
CARD_ORDER: tuple[tuple[str, str], ...] = (
    ("合一众赏金轮换", "rotation"),
    ("深矿赏金", "deep"),
    ("地球赏金", "world:地球赏金"),
    ("金星赏金", "world:金星赏金"),
    ("火卫二赏金", "world:火卫二赏金"),
    ("圣所赏金", "faction:圣所赏金"),
    ("扎里曼赏金", "faction:扎里曼赏金"),
    ("六人组赏金", "faction:六人组赏金"),
)

# 展示口径的赏金阶段序号（参考图同款）。仅作 tier 缺省值：
# 数据源一旦给出 job["tier"]，这里立刻让位，不参与任何业务判断。
_TIER_FALLBACK: dict[str, tuple[str, ...]] = {
    "地球赏金": ("赏金6", "赏金7"),
    "金星赏金": ("赏金6", "赏金7"),
    "火卫二赏金": ("赏金5", "赏金6"),
    "圣所赏金": ("赏金4", "赏金5"),
    "扎里曼赏金": ("赏金1", "赏金4", "赏金5"),
    "六人组赏金": ("赏金5", "赏金7"),
}

#: 轮换周期（秒），只用于底部进度条的**视觉比例**，不参与任何数据判断。
#: 参考图「剩余 1小时48分」时左侧已消逝段约占 1/5，反推周期约 2h15m。
BOUNTY_PERIOD_SEC = 8100

HINT_TOKENS = (
    ("*使用", "#2B3A4A", "regular"),
    ("“地球赏金”、“六人组”", "#6B3FA0", "bold"),
    ("等赏金名查询具体赏金细节", "#2B3A4A", "regular"),
)
SOURCE_TIP = "*善用右键/长按提取文字"
PLATFORM = "PC"


def _tier(card_title: str, idx: int, job: dict) -> str:
    t = str(job.get("tier") or "").strip()
    if t:
        return t
    seq = _TIER_FALLBACK.get(card_title, ())
    return seq[idx] if idx < len(seq) else f"赏金{idx + 1}"


def _norm_lvl(lvl) -> str:
    """"100-100" → "100"；"50-70" 原样；None/空 → ""。"""
    s = str(lvl or "").strip()
    if not s:
        return ""
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m and m.group(1) == m.group(2):
        return m.group(1)
    return s


def _lv_color(lvl: str) -> str:
    """参考图口径：100 级档紫、其余蓝；深矿单独走橙。"""
    try:
        hi = max(int(x) for x in re.findall(r"\d+", lvl)) if lvl else 0
    except ValueError:
        hi = 0
    return "#6741A5" if hi >= 100 else "#1668B3"


def _items_from_rewards(rewards: list) -> list[dict]:
    """轮换奖励：优先用数据源已解析的本地图；缺失时按中文名解析 DE 官方图标。"""
    out = []
    for r in rewards or []:
        if not isinstance(r, dict):
            continue
        nm = str(r.get("name") or "").strip()
        if nm:
            out.append({"text": nm,
                        "image": r.get("image") or bounty_icons.ensure(nm)})
    return out


def _items_from_names(names: list) -> list[dict]:
    """MOD / 债券：数据源只给中文名，图标由 bounty_icons 按官方 CDN 解析。

    绝不回落几何方块 —— 解析失败会返回 None，渲染层必须显式登记 missing_icons
    并告警，测试里用 ``gate_icons(strict=True)`` 拦在交差前。
    """
    return [{"text": str(n).strip(), "image": bounty_icons.ensure(str(n).strip())}
            for n in (names or []) if str(n).strip()]


def _deep_hero(deep: dict) -> dict | None:
    """解析 deep.detail「等级 130-140 · 5阶段 · 最终：歼灭」→ 三个展示字段。

    解析不出来时保留原始 detail 作为弱信息，绝不丢内容。
    """
    if not deep:
        return None
    tag = str(deep.get("tag") or "").strip()
    title = str(deep.get("title") or "").strip()
    detail = str(deep.get("detail") or "").strip()
    lvl = ""
    stages = ""
    final = ""
    m = re.search(r"等级\s*([\d\-]+)", detail)
    if m:
        lvl = m.group(1)
    m = re.search(r"(\d+\s*阶段)", detail)
    if m:
        stages = m.group(1)
    m = re.search(r"最终\s*[：:]\s*([^\s·]+)", detail)
    if m:
        final = m.group(1)
    return {
        "section": (tag + title) if (tag or title) else "",
        "lvl": lvl,
        "stages": stages,
        "final": final,
        "detail": detail,
        "icon": str(deep.get("icon") or "bolt"),
    }


def _world_jobs(card_title: str, world: dict) -> list[dict]:
    """两行式任务行（参考图地球/金星/火卫二）：
    第一行 绿色「赏金N」徽章 + 任务名；第二行 Lv 徽章 + 蓝色类型词 + 图标。
    """
    out = []
    for i, j in enumerate(world.get("jobs") or []):
        if not isinstance(j, dict):
            continue
        lvl = _norm_lvl(j.get("lvl"))
        nm = str(j.get("name") or "").strip()
        if j.get("narmer"):
            nm = (nm + " 合一众").strip()
        out.append({
            "layout": "pair",
            "tier": _tier(card_title, i, j),
            "name": nm,
            "lvl": lvl,
            "lvl_color": _lv_color(lvl),
            "type": str(j.get("type") or "").strip(),
            "mission": str(j.get("mission") or "").strip(),
            "objective": str(j.get("objective") or "").strip(),
            "icon": str(j.get("icon") or "circle"),
        })
    return out


def _faction_jobs(card_title: str, fac: dict) -> list[dict]:
    """三行式任务行（参考图圣所/扎里曼/六人组）：
    第一行 绿色「赏金N」徽章 + 蓝色节点名；
    第二行 蓝色类型词 + 深灰任务名 + 图标；
    第三行 └ 目标：xxx（objective 缺失则省略该行，不编文案）。
    """
    out = []
    for i, j in enumerate(fac.get("jobs") or []):
        if not isinstance(j, dict):
            continue
        target = str(j.get("target") or "").strip()
        out.append({
            "layout": "triple",
            "tier": _tier(card_title, i, j),
            "name": str(j.get("name") or "").strip(),
            "lvl": _norm_lvl(j.get("lvl")),
            "lvl_color": _lv_color(str(j.get("lvl") or "")),
            "type": str(j.get("type") or "").strip(),
            "mission": str(j.get("mission") or "").strip() or target,
            "objective": str(j.get("objective") or "").strip(),
            "icon": str(j.get("icon") or "star"),
        })
    return out


def _remain_short(remain: str) -> tuple[str, float]:
    """「1小时48分23秒」→ ("1小时48分", 剩余秒数)。用于脚注与进度条。"""
    s = str(remain or "").strip()
    h = m = sec = 0
    mh = re.search(r"(\d+)\s*小时", s)
    mm = re.search(r"(\d+)\s*分", s)
    ms = re.search(r"(\d+)\s*秒", s)
    if mh:
        h = int(mh.group(1))
    if mm:
        m = int(mm.group(1))
    if ms:
        sec = int(ms.group(1))
    total = h * 3600 + m * 60 + sec
    if h:
        label = f"{h}小时{m}分"
    elif m:
        label = f"{m}分"
    else:
        label = s
    return label, float(total)


def build_model(data: dict | None, source: str | None = None) -> dict:
    """返回渲染层唯一消费的内容模型。data 为空也保证 8 张卡齐全（禁止遗漏）。"""
    d = data or {}
    rot = d.get("rotation") or {}
    worlds = {str(w.get("name") or ""): w
              for w in (d.get("worlds") or []) if isinstance(w, dict)}
    factions = {str(f.get("name") or ""): f
                for f in (d.get("factions") or []) if isinstance(f, dict)}
    deep = d.get("deep") or {}

    vault = str(rot.get("vault") or "").strip()
    vault_items = _items_from_rewards(rot.get("vault_rewards"))
    rot_items = _items_from_rewards(rot.get("rot_rewards"))

    cards: list[dict] = []
    for title, key in CARD_ORDER:
        card: dict = {"title": title, "section": "", "items": [],
                      "hero": None, "jobs": [], "empty": "", "more": False}
        if key == "rotation":
            card["title"] = f"{title} {str(rot.get('rot') or '').strip()}".strip()
            card["items"] = rot_items
        elif key == "deep":
            card["hero"] = _deep_hero(deep)
            if card["hero"]:
                card["section"] = card["hero"]["section"]
        elif key.startswith("world:"):
            name = key.split(":", 1)[1]
            w = worlds.get(name) or {}
            card["title"] = name
            card["section"] = str(w.get("label") or "").strip()
            items = _items_from_names(w.get("items"))
            if name == "火卫二赏金":
                # 参考图：隔离库轮换 C + 孢丝感染枪 枪托，归在火卫二卡内。
                # vault 为空时不能拼出「隔离库轮换」这种半截标题（会让空卡
                # 误判为有内容，丢掉「暂无数据」占位）。
                if vault and not card["section"]:
                    card["section"] = f"隔离库轮换 {vault}"
                items = items or vault_items
            card["items"] = items
            card["jobs"] = _world_jobs(name, w)
        elif key.startswith("faction:"):
            name = key.split(":", 1)[1]
            f = factions.get(name) or {}
            card["title"] = name
            card["jobs"] = _faction_jobs(name, f)

        if not (card["items"] or card["jobs"] or card["hero"] or card["section"]):
            card["empty"] = "暂无数据"
        card["more"] = bool(card["jobs"])
        cards.append(card)

    remain_label, remain_sec = _remain_short(d.get("remain") or "")
    elapsed = 1.0 - remain_sec / BOUNTY_PERIOD_SEC if remain_sec > 0 else 0.0
    elapsed = max(0.0, min(1.0, elapsed))
    return {
        "title": str(d.get("title") or "赏金任务一览"),
        "cards": cards,
        "footer": {
            "remain": remain_label,
            "progress": elapsed,
            "hint": HINT_TOKENS,
            "platform": PLATFORM,
            "source": str(source or "").strip(),
            "tip": SOURCE_TIP,
        },
    }
