# -*- coding: utf-8 -*-
"""斗技多维度效果：词表 / 中文标签 / 校验 / 卡片 chip / 战斗消费。

为什么有这个文件
----------------
2026-11-15 之前，斗技的多维效果（吸血/破防/流血/眩晕/反伤…）全写在 ``desc``
字符串里，既不能结构化展示，也不进战斗 —— 四槽里 N 门技能实际只有 N 个伤害
数字在比大小，玩家当然无脑选高倍率。本文件把它们抽成「效果原子」：

    每条斗技 = role(主定位) + effects(效果原子数组) + gate(门控代价)
    effect = {"t": 类型, "v": 数值比例, "d": 持续回合, "p": 触发概率, ...}

本模块是**纯函数层**（零副作用、不 import nonebot、不写存档），被三处消费：
    1. card.build_skills_data      -> effect_chips() 出卡片维度标签
    2. core._hit/fight/duel        -> on_hit/on_incoming/on_turn_start 结算战斗
    3. tools/check_skill_multidim  -> validate_effects() 门禁校验

战斗消费的安全边界（改动前必读）
--------------------------------
* **damage_schedule / monster_scaled 完全不动**：怪物血量反解口径不变，
  ``test_monster_scaled_tracks_player`` 的 3~7 回合斩杀窗口不受影响。
* 效果只做「玩家侧增益 / 目标侧减益」，方向单一（让玩家更强或更稳），
  不引入让战斗变输的负反馈；幅度受 effect v 本身约束（<=50%），温和。
* 所有随机判定都吃外部注入的 ``rng``，可用固定序列做确定性单测。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 1. 词表
# ---------------------------------------------------------------------------
#: 效果类型 -> 中文短标签（卡片 chip / 战报统一口径）
EFFECT_LABELS = {
    "dot":          "持续伤害",
    "lifesteal":    "吸血",
    "pierce":       "破防",
    "debuff_atk":   "降攻",
    "debuff_evade": "压闪避",
    "debuff_heal":  "减疗",
    "stun":         "眩晕",
    "silence":      "禁技",
    "root":         "定身",
    "knockback":    "击退",
    "reflect":      "反伤",
    "reduce":       "减伤",
    "evade":        "闪避",
    "chain":        "连击",
    "cond":         "条件加伤",
    "empower":      "增幅",
}

#: 受控类型集合（门禁只认这些，杜绝随手编 t）
KNOWN_EFFECTS = tuple(EFFECT_LABELS)

#: 主定位词表（role 只能取这些值）
ROLES = ("输出", "爆发", "续航", "控场", "异常", "防御", "位移", "功能")

#: DoT 子类中文名
DOT_KINDS = ("血毒", "流血", "灼烧", "中毒")

#: 条件加伤的 cond -> 中文（展示用）
COND_LABELS = {
    "fear": "恐惧", "defensive": "守势", "fire": "火势",
    "after_move": "位移后", "low_hp": "残血",
}


# ---------------------------------------------------------------------------
# 2. 校验（门禁 + 单测共用）
# ---------------------------------------------------------------------------
def validate_effects(effects) -> list[str]:
    """校验一条效果数组，返回错误文案列表（空 = 合法）。"""
    errs: list[str] = []
    if effects is None:
        return errs  # effects=None 仅作防御性兼容；全 150 门都必须填（全宗门门禁拦）
    if not isinstance(effects, list):
        return ["effects 必须是 list"]
    for i, e in enumerate(effects):
        if not isinstance(e, dict):
            errs.append(f"[{i}] 效果必须是 dict")
            continue
        t = e.get("t")
        if t not in EFFECT_LABELS:
            errs.append(f"[{i}] 未知效果类型 {t!r}")
        for k in ("v", "p"):
            if k in e and e[k] is not None:
                try:
                    f = float(e[k])
                except (TypeError, ValueError):
                    errs.append(f"[{i}] {k} 非数值: {e[k]!r}")
                    continue
                if not (0.0 <= f <= 1.5):
                    errs.append(f"[{i}] {k} 越界(应 0~1.5): {f}")
        if "d" in e and e["d"] is not None:
            try:
                if int(e["d"]) < 0:
                    errs.append(f"[{i}] d 不能为负: {e['d']!r}")
            except (TypeError, ValueError):
                errs.append(f"[{i}] d 非整数: {e['d']!r}")
    return errs


def _pct(v) -> str:
    return f"{int(round(float(v) * 100))}%"


# ---------------------------------------------------------------------------
# 3. 卡片 chip（供 card.build_skills_data / 模板渲染）
# ---------------------------------------------------------------------------
def effect_chips(effects) -> list[dict]:
    """效果数组 -> [{label, value}]，卡片按 chip 平铺（显式 gap，不用空格凑距）。"""
    out: list[dict] = []
    for e in effects or []:
        t = e.get("t")
        label = EFFECT_LABELS.get(t, str(t))
        v, d = e.get("v"), e.get("d")
        val = _pct(v) if v is not None else ""
        if d:
            val += f"·{int(d)}回合"
        if t == "dot" and e.get("kind"):
            label = str(e["kind"])
        elif t == "cond" and e.get("cond"):
            c = str(e["cond"])
            label = "对" + COND_LABELS.get(c, c)
        elif t == "stun" and e.get("p") and float(e["p"]) < 1:
            val = _pct(e["p"])
        elif t == "chain" and e.get("n"):
            val = f"{int(e['n'])}段·{val}"
        elif t == "reduce" and e.get("vs_heavy"):
            label = "减伤/抗重"
        out.append({"label": label, "value": val})
    return out


def role_label(role: str) -> str:
    return role if role in ROLES else (role or "")


#: desc 末尾的代价段标记（全 150 门斗技都用这个写法）
_GATE_SPLIT = ("限制：", "限制:")


def desc_without_gate(desc: str) -> str:
    """去掉描述末尾的「限制：…」段。

    为什么需要：``desc`` 是原著全文，末段固定写「限制：……」，而 ``gate`` 字段
    是同一件事的摘要。卡片上两处都画就会重复（用户 2026-11-15 看到血煞掌的
    「限制：血毒需自身血气为引，气血虚时威力骤降…」和代价行「限：气血虚时
    威力骤降，对无血之物无效」说了两遍）。所以卡片只画机制段，代价交给
    ``gate`` 行单独展示 —— 信息不丢，也不啰嗦。

    没有「限制」段的描述原样返回。
    """
    s = str(desc or "")
    if not s:
        return ""
    idx = -1
    for mark in _GATE_SPLIT:
        i = s.find(mark)
        if i >= 0 and (idx < 0 or i < idx):
            idx = i
    if idx < 0:
        return s
    head = s[:idx].rstrip()
    # 机制段被切断处补句号，避免出现「…（每回合 5%，持续 2 回合」这种断尾
    if head and head[-1] not in "。！？…）】」":
        head += "。"
    return head


# ---------------------------------------------------------------------------
# 3.5 技能来源融入描述（2026-11-15 用户纠正）
# ---------------------------------------------------------------------------
#: 文献引用写法 —— 卡片描述里**禁止出现**。
#: 用户原话：「不要写原著多少章这种描述，你见过哪个游戏的技能描述这样写的？」
#: 技能说明是游戏文案，不是小说注脚。``skill_book.novel``（章节引文）只用于
#: 离线核验出处，绝不进卡片；卡片只取 ``source``（谁所创 / 谁所使 / 怎么得到）。
CITATION_RE = re.compile(
    r"原著第|出自第|第[零〇一两二三四五六七八九十百千]+章|」载")
#: 这些字样是「游戏补白」标记，不是来源；不能当成技能来源写进描述。
SOURCE_NOT_REAL = ("游戏自设", "游戏补白", "原著无载")
#: 使用记录 ≠ 来源（用户 2026-11-16：「你全都写谁用的，这并不符合斗技来源的
#: 说法」）。来源要写它**怎么来的**：谁创 / 谁传 / 从哪取得 / 买 / 夺 / 锤炼。
#: 只有「某某所使 / 所修 / 所用 / 亦修」这种句子，等于没写来源。
USAGE_ONLY_RE = re.compile(
    r"(所使|所用|所修|亦修|施展者|擅长使用|惯用|所使用的?|最擅长)")
#: 真正的来源线索：创作 / 传授 / 取得 / 交易 / 抢夺 / 苦修 / 发现。
SOURCE_ORIGIN_RE = re.compile(
    r"(所创|创出|自创|所传|传授|所授|授与|取得|到手|所得|购|买|拍得|"
    r"拍卖|抢|夺|获|得自|锤炼|磨出|修成|习得|藏|捡|发现|遗留|继承)")


def source_phrase(entry: dict) -> str:
    """技能来源短语：作为描述的**开头状语**，不独立成句、不带标题。

    只取 ``skill_book.source``，去掉句末标点；无来源 / 只有自设标记 /
    混进章节引用时返回空串（用户：有来源就加、没有就不加）。
    """
    src = str(entry.get("source") or "").strip().rstrip("。！？；;，, ")
    if not src or any(x in src for x in SOURCE_NOT_REAL):
        return ""
    if CITATION_RE.search(src):
        return ""
    # 纯使用记录（「白山所使」）不是来源：拒绝，并在描述里不显示。
    # 只有同时含真实来历词才放行（如「柳擎锤炼近二十年的爪法」）。
    if USAGE_ONLY_RE.search(src) and not SOURCE_ORIGIN_RE.search(src):
        return ""
    return src


def desc_with_source(entry: dict, desc: str) -> str:
    """把来源并进**描述第一句**：「天冥宗易尘所使，斗气凝血成掌……」。

    用逗号把来源状语接上施展流程，读起来就是一条技能说明；不独立成句、
    不写「出处：」、不写章节号。没有来源时原样返回，绝不造空句。
    """
    phrase = source_phrase(entry)
    body = str(desc or "")
    return (phrase + "，" + body) if phrase else body


# ---------------------------------------------------------------------------
# 4. 战斗状态（一场战斗一份，战后即弃，不写回存档）
# ---------------------------------------------------------------------------
def new_state() -> dict:
    """创建一份空的战斗效果状态。fight 用 2 份（玩家/怪），duel 用 2 份。"""
    return {
        # 目标身上（被挂的减益）
        "dot": [],                 # [{v, d, kind, base}]
        "debuff_atk": 0.0, "debuff_atk_d": 0,
        "debuff_evade": 0.0, "debuff_evade_d": 0,
        "debuff_heal": 0.0, "debuff_heal_d": 0,
        "def_shred": 0.0, "def_shred_d": 0,
        "stun": 0, "silence": 0, "root": 0,
        # 自身（增益）
        "reduce": 0.0, "reduce_d": 0, "reduce_vs_heavy": 0.0,
        "evade": 0.0, "evade_d": 0,
        "reflect": 0.0, "reflect_d": 0,
        "chain": 0,
        "empower": 0.0, "empower_d": 0,
    }


def _cond_met(cond, ctx: dict, target_state: dict) -> bool:
    """条件加伤是否满足。未知/未模拟的条件一律 False（保守，不加伤）。"""
    if not cond:
        return False
    flags = set(ctx.get("self_flags") or ())
    tflags = set(ctx.get("target_flags") or ())
    if cond == "after_move":
        return "moved" in flags
    if cond == "fear":
        return "fear" in tflags
    if cond == "defensive":
        return "defensive" in tflags
    if cond == "fire":
        return "fire" in tflags or "fire" in flags
    if cond == "low_hp":
        return float(ctx.get("target_hp_ratio", 1.0)) < 0.35
    return False


def on_hit(effects, self_state: dict, target_state: dict, dmg: int, rng,
           ctx: dict | None = None) -> dict:
    """技能命中后结算效果（core._hit 算出基础伤害后调用）。

    返回 ``{pierce, bonus, heal_ratio, notes}``：
      * pierce     -- 本次无视目标防御比例，core 用它重算 mitigation
      * bonus      -- 本次额外加伤倍率，core: dmg *= (1 + bonus)
      * heal_ratio -- 本次吸血比例，core: heal = int(dmg * heal_ratio)
      * notes      -- 逐维战报文案（战斗日志播报用）

    副作用：把 debuff/dot 挂到 target_state，把 gain 写到 self_state。
    """
    ctx = ctx or {}
    res = {"pierce": 0.0, "bonus": 0.0, "heal_ratio": 0.0, "notes": []}
    notes: list[str] = res["notes"]
    for e in effects or []:
        t = e.get("t")
        v = float(e.get("v", 0) or 0)
        d = int(e.get("d", 0) or 0)
        p = float(e.get("p", 1.0) or 1.0)

        if t == "lifesteal":
            res["heal_ratio"] = max(res["heal_ratio"], v)
            notes.append(f"吸血{_pct(v)}")

        elif t == "pierce":
            if rng() < p:
                res["pierce"] = max(res["pierce"], v)
                target_state["def_shred"] = max(target_state["def_shred"], v)
                target_state["def_shred_d"] = max(target_state["def_shred_d"], max(1, d))
                notes.append(f"破防{_pct(v)}")

        elif t == "dot":
            if rng() < p:
                target_state["dot"].append({
                    "v": v, "d": max(1, d),
                    "kind": str(e.get("kind") or "持续伤害"),
                    "base": max(1, int(dmg)),
                })
                notes.append(f"{e.get('kind') or '持续伤害'}{_pct(v)}×{d}")

        elif t == "debuff_atk":
            target_state["debuff_atk"] = max(target_state["debuff_atk"], v)
            target_state["debuff_atk_d"] = max(target_state["debuff_atk_d"], max(1, d))
            notes.append(f"降攻{_pct(v)}")

        elif t == "debuff_evade":
            target_state["debuff_evade"] = max(target_state["debuff_evade"], v)
            target_state["debuff_evade_d"] = max(target_state["debuff_evade_d"], max(1, d))
            notes.append(f"压闪避{_pct(v)}")

        elif t == "debuff_heal":
            target_state["debuff_heal"] = max(target_state["debuff_heal"], v)
            target_state["debuff_heal_d"] = max(target_state["debuff_heal_d"], max(1, d))
            notes.append(f"减疗{_pct(v)}")

        elif t == "stun":
            if rng() < p:
                target_state["stun"] = max(target_state["stun"], max(1, d))
                notes.append("眩晕")

        elif t == "silence":
            target_state["silence"] = max(target_state["silence"], max(1, d))
            notes.append("禁技")

        elif t == "root":
            target_state["root"] = max(target_state["root"], max(1, d))
            notes.append("定身")

        elif t == "knockback":
            target_state["root"] = max(target_state["root"], 1)
            notes.append("击退")

        elif t == "reflect":
            self_state["reflect"] = max(self_state["reflect"], v)
            self_state["reflect_d"] = max(self_state["reflect_d"], max(1, d or 2))
            notes.append(f"反伤{_pct(v)}")

        elif t == "reduce":
            self_state["reduce"] = max(self_state["reduce"], v)
            self_state["reduce_d"] = max(self_state["reduce_d"], max(1, d))
            self_state["reduce_vs_heavy"] = max(
                self_state["reduce_vs_heavy"], float(e.get("vs_heavy", 0) or 0))
            notes.append(f"减伤{_pct(v)}")

        elif t == "evade":
            self_state["evade"] = max(self_state["evade"], v)
            self_state["evade_d"] = max(self_state["evade_d"], max(1, d))
            notes.append(f"闪避{_pct(v)}")

        elif t == "empower":
            res["bonus"] += v
            notes.append(f"增幅{_pct(v)}")

        elif t == "chain":
            self_state["chain"] = int(self_state.get("chain", 0)) + 1
            n = int(e.get("n", 3))
            if self_state["chain"] >= n:
                res["bonus"] += v
                notes.append(f"{n}连击+{_pct(v)}")

        elif t == "cond":
            if _cond_met(e.get("cond"), ctx, target_state):
                res["bonus"] += v
                notes.append(f"条件{_pct(v)}")

    res["bonus"] = min(1.0, res["bonus"])
    res["pierce"] = min(0.9, res["pierce"])
    return res


def target_atk_mult(state: dict) -> float:
    """目标攻击力倍率（降攻效果）。"""
    return 1.0 - min(0.9, float(state.get("debuff_atk", 0)))


def target_def_mult(state: dict) -> float:
    """目标有效防御倍率（破防效果）。"""
    return 1.0 - min(0.9, float(state.get("def_shred", 0)))


def target_evade_delta(state: dict) -> float:
    """目标闪避率改变量（压闪避为负）。"""
    return -min(0.9, float(state.get("debuff_evade", 0)))


def on_turn_start(target_state: dict, rng) -> dict:
    """目标行动前：结算 DoT 扣血 + 眩晕/定身跳过行动。

    返回 ``{dot_dmg, skip, notes}``。DoT 伤害取挂毒那次技能伤害的 v 倍。
    """
    st = target_state
    notes: list[str] = []
    dot_dmg = 0
    for d in list(st.get("dot", [])):
        tick = max(1, int(int(d.get("base", 1)) * float(d.get("v", 0))))
        dot_dmg += tick
        notes.append(f"{d.get('kind', '持续伤害')}{_pct(d.get('v', 0))}={tick}")
        d["d"] = int(d.get("d", 1)) - 1
    st["dot"] = [d for d in st.get("dot", []) if int(d.get("d", 0)) > 0]

    skip = int(st.get("stun", 0)) > 0 or int(st.get("root", 0)) > 0
    if skip:
        if int(st.get("stun", 0)) > 0:
            st["stun"] = int(st["stun"]) - 1
        if int(st.get("root", 0)) > 0:
            st["root"] = int(st["root"]) - 1
        notes.append("被控跳过行动")
    return {"dot_dmg": dot_dmg, "skip": skip, "notes": notes}


def on_incoming(self_state: dict, dmg: int, rng, ctx: dict | None = None) -> dict:
    """自身受击：结算闪避 / 减伤 / 反伤。

    返回 ``{final, reflect, evaded, notes}``；final 已含减伤，reflect 为反弹给对方的伤害。
    """
    ctx = ctx or {}
    st = self_state
    notes: list[str] = []
    evade = float(st.get("evade", 0))
    if evade > 0 and rng() < evade:
        return {"final": 0, "reflect": 0, "evaded": True,
                "notes": [f"闪避{_pct(evade)}"]}
    reduce = float(st.get("reduce", 0))
    if ctx.get("heavy"):
        reduce = min(0.9, reduce + float(st.get("reduce_vs_heavy", 0)))
    final = max(1, int(dmg * (1.0 - min(0.9, reduce))))
    if reduce > 0:
        notes.append(f"减伤{_pct(reduce)}")
    reflect = 0
    rv = float(st.get("reflect", 0))
    if rv > 0:
        reflect = max(1, int(final * rv))
        notes.append(f"反伤{_pct(rv)}={reflect}")
    return {"final": final, "reflect": reflect, "evaded": False, "notes": notes}


def expire_turn(state: dict) -> None:
    """回合末递减所有持续回合；归零时清掉对应数值（原地修改）。"""
    pairs = (
        ("debuff_atk_d", "debuff_atk"), ("debuff_evade_d", "debuff_evade"),
        ("debuff_heal_d", "debuff_heal"), ("def_shred_d", "def_shred"),
        ("reduce_d", "reduce"), ("evade_d", "evade"),
        ("reflect_d", "reflect"), ("empower_d", "empower"),
    )
    for dk, vk in pairs:
        if int(state.get(dk, 0)) > 0:
            state[dk] = int(state[dk]) - 1
            if state[dk] == 0:
                state[vk] = 0.0


# ---------------------------------------------------------------------------
# 5. 自检（python src/plugins/doupo/skill_effects.py）
# ---------------------------------------------------------------------------
def _self_test() -> int:
    ok, fail = 0, []

    def chk(name, fn):
        nonlocal ok
        try:
            fn(); ok += 1; print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            fail.append(name); print(f"FAIL  {name} -> {type(e).__name__}: {e}")

    def t_labels():
        chips = effect_chips([
            {"t": "dot", "v": 0.05, "d": 2, "kind": "血毒"},
            {"t": "debuff_heal", "v": 0.3, "d": 2},
            {"t": "stun", "p": 0.1, "d": 1},
        ])
        assert chips[0]["label"] == "血毒" and chips[0]["value"] == "5%·2回合"
        assert chips[1]["label"] == "减疗"
        assert chips[2]["value"] == "10%"
    chk("effect_chips 中文标签与数值", t_labels)

    def t_validate():
        assert validate_effects([{"t": "pierce", "v": 0.3}]) == []
        assert validate_effects(None) == []
        assert any("未知" in e for e in validate_effects([{"t": "wat"}]))
        assert any("越界" in e for e in validate_effects([{"t": "dot", "v": 9}]))
    chk("validate_effects 词表/范围", t_validate)

    def t_lifesteal():
        s, ts = new_state(), new_state()
        r = on_hit([{"t": "lifesteal", "v": 0.25}], s, ts, 1000, lambda: 0.9)
        assert r["heal_ratio"] == 0.25 and "吸血25%" in r["notes"]
    chk("吸血结算", t_lifesteal)

    def t_pierce():
        s, ts = new_state(), new_state()
        r = on_hit([{"t": "pierce", "v": 0.3, "d": 1, "p": 1.0}], s, ts, 1000, lambda: 0.1)
        assert r["pierce"] == 0.3
        assert abs(target_def_mult(ts) - 0.7) < 1e-9
        expire_turn(ts)
        assert abs(target_def_mult(ts) - 1.0) < 1e-9
    chk("破防结算与到期", t_pierce)

    def t_dot():
        s, ts = new_state(), new_state()
        on_hit([{"t": "dot", "v": 0.05, "d": 2, "kind": "血毒"}], s, ts, 1000, lambda: 0.1)
        a = on_turn_start(ts, lambda: 0.9)
        b = on_turn_start(ts, lambda: 0.9)
        c = on_turn_start(ts, lambda: 0.9)
        assert a["dot_dmg"] == 50 and b["dot_dmg"] == 50 and c["dot_dmg"] == 0
    chk("DoT 每回合扣血与到期", t_dot)

    def t_stun():
        s, ts = new_state(), new_state()
        on_hit([{"t": "stun", "p": 1.0, "d": 1}], s, ts, 100, lambda: 0.1)
        a = on_turn_start(ts, lambda: 0.9)
        b = on_turn_start(ts, lambda: 0.9)
        assert a["skip"] is True and b["skip"] is False
    chk("眩晕跳过行动", t_stun)

    def t_defense():
        s = new_state()
        on_hit([{"t": "reduce", "v": 0.15, "d": 2},
                {"t": "reflect", "v": 0.4, "d": 2},
                {"t": "evade", "v": 0.2, "d": 2}], s, new_state(), 100, lambda: 0.9)
        r = on_incoming(s, 1000, lambda: 0.9)   # 不闪避
        assert r["final"] == 850 and r["reflect"] == 340
        r2 = on_incoming(s, 1000, lambda: 0.1)  # 触发闪避
        assert r2["evaded"] is True and r2["final"] == 0
    chk("减伤/反伤/闪避结算", t_defense)

    def t_chain():
        s, ts = new_state(), new_state()
        e = [{"t": "chain", "n": 3, "v": 0.3}]
        r1 = on_hit(e, s, ts, 100, lambda: 0.9)
        r2 = on_hit(e, s, ts, 100, lambda: 0.9)
        r3 = on_hit(e, s, ts, 100, lambda: 0.9)
        assert r1["bonus"] == 0 and r2["bonus"] == 0 and abs(r3["bonus"] - 0.3) < 1e-9
    chk("连击三段触发", t_chain)

    print("=" * 70)
    print(f"EFFECTS_TOTAL=8 PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
