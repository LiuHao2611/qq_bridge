# -*- coding: utf-8 -*-
"""bounty_deep.py —— 深矿轮换推导 / 赏金编号 / WFA 词库旧译校正。

2026-11-08 用户「一个字一个字对齐，禁止遗漏」纠正后新增。上一轮把深矿
「[钢铁之路]企业重组」与「最终任务：刺杀」整行删掉，理由是「无权威源」；
实际权威源齐备：

  * DE 官方《更新 40：谷底之群智》补丁说明 + 灰机 Wiki「赏金」：深矿三类
    常驻赏金「解放小动物 / 企业重组 / 花园除草」每 2.5 小时轮换，钢铁之路
    130-140 级、5 阶段；
  * 英文 Wiki ``Deepmines/Quotes``：``Corporate Restructuring`` 的 final stage
    就是 ``Assassinate the Scrofa`` → 中文「刺杀」。三类赏金各有固定最终阶段。

轮换推导（用官方 ``worldState.php`` 的 ``SyndicateMissions[].Expiry``，毫秒）：

    cycle = Expiry_ms // 1000 // 9000       # 2.5h = 9000s
    ROTATIONS[cycle % 3] = (赏金名, 最终任务)

两张用户截图交叉验证（UTC+8，剩余时间反推 Expiry）：

  * 16:50 ``f8650`` 剩 1小时48分 → Expiry≈18:38 → cycle%3=1 → 解放小动物/救援
  * 18:50 ``880f``  剩 2小时19分 → Expiry≈21:09 → cycle%3=2 → 企业重组/刺杀
  * 项目旧实现硬编码「花园除草/歼灭」= 第三轮，与灰机 Wiki 三类轮换吻合
  * 夹具 Expiry=1794143365798 → cycle=199349 → %3=2 → 企业重组/刺杀 ✅

赏金编号：绿色「赏金N」= 该组 jobs 数组 **0-based 索引 + 1**。六组实证：

    Cetus[5,6]→6/7   Solaris[5,6]→6/7   Entrati[4,5]→5/6
    Zariman[2,3,4]→3/4/5   EntratiLab[3,4]→4/5   Hex[4,6]→5/7

禁止再写死编号表：上一轮 ``bounty_model._TIER_FALLBACK`` 把 16:50 快照的
「赏金1」写死，18:50 应是「赏金3」；这正是用户逐字比对抓到的错。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("qq_bridge")

#: 2.5 小时 = 9000 秒（官方补丁说明：深矿赏金每 2.5 小时轮换一次）。
PERIOD_SEC = 9000

#: cycle % 3 → (赏金名, 最终任务)。顺序由上面两张截图 + 旧实现三方定案，
#: 不是按参考图快照写死；``patch_deep`` 每轮从官方 Expiry 现算。
ROTATIONS: tuple[tuple[str, str], ...] = (
    ("花园除草", "歼灭"),
    ("解放小动物", "救援"),
    ("企业重组", "刺杀"),
)

#: WFA_Lexicon 词库（2019~2021 旧译）与当前官方简中 / 别人卡的差异校正。
#: 键是**词库给出的中文值**，不是任务名，避免又变成手写任务名清单。
#: 「资源窃取」见 18:50 别人卡 880f 金星 Lv100；词库旧值 ``TheftJobExcavationTitle``
#: =「资源搜掠」。只校正有外部截图实证的词条，查不到的一律不碰。
JOB_NAME_FIX: dict[str, str] = {"资源搜掠": "资源窃取"}


def _as_ms(v) -> int:
    """官方 Expiry 两种形状：``{"$date":{"$numberLong":"..."}}`` 或直接毫秒数。"""
    if isinstance(v, dict):
        v = (v.get("$date") or {}).get("$numberLong")
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        return 0
    return n


def official_expiry(official: dict | None, payload: dict | None) -> int:
    """取当前赏金轮换的 Expiry（毫秒）；优先官方，回落到 RSC bountyBoard。"""
    for sy in ((official or {}).get("SyndicateMissions") or []):
        if sy.get("Tag") in ("CetusSyndicate", "SolarisSyndicate",
                             "EntratiSyndicate"):
            ms = _as_ms(sy.get("Expiry"))
            if ms:
                return ms
    for b in ((payload or {}).get("bountyBoard") or []):
        ms = _as_ms(b.get("expiry"))
        if ms:
            return ms
    return 0


def rotation_index(expiry_ms: int) -> int:
    """官方 Expiry → 轮换下标；无 Expiry 返回 -1（调用方必须显式告警）。"""
    if not expiry_ms:
        return -1
    return (expiry_ms // 1000 // PERIOD_SEC) % len(ROTATIONS)


def patch_deep(deep: dict, official: dict | None = None,
               payload: dict | None = None) -> dict:
    """补回深矿卡被上一轮删掉的两层内容：轮换名 + 最终任务行。

    无官方 Expiry 时**保留原值并显式告警**，不编造、不静默删行。
    ``detail`` 末尾的「最终：xxx」由 ``bounty_model._deep_hero`` 解析成
    ``hero["final"]``，``bounty_layout`` 据此画出「最终任务：」整行。
    """
    d = dict(deep or {})
    idx = rotation_index(official_expiry(official, payload))
    if idx < 0:
        log.warning("bounty_deep_no_expiry — 深矿轮换无法推导，保留原值不编造")
        return d
    name, final = ROTATIONS[idx]
    d["tag"] = "[钢铁之路]"
    d["title"] = name
    d["detail"] = "等级 130-140 · 5阶段 · 最终：%s" % final
    return d


def correct_job_name(name: str) -> str:
    """词库旧译 → 当前官方简中；未登记的词条原样返回。"""
    s = str(name or "").strip()
    return JOB_NAME_FIX.get(s, s)


def tier(idx: int) -> str:
    """jobs 数组 0-based 索引 → 绿色赏金编号。唯一权威口径，禁止另立表。"""
    return "赏金%d" % (idx + 1)


def banner_from_templates(desc: str, tpls) -> str:
    """ally 缺失时，从 ``Challenge_*_Desc`` 模板取首行固定横幅。

    ``tpls`` 是 ``bounty_live._CH_TPLS``（``(长度, 正则, 挑战名, banner)``）。
    1999 六人组「传承种收割 / 从高处击杀 50 名敌人」没有 ally，但别人卡仍显示
    「天降正义 · 杀毒赏金任务」，横幅就来自该模板首行，不能凭空丢掉。
    """
    compact = re.sub(r"\s+", "", str(desc or ""))
    for _length, rx, _nm, banner in (tpls or []):
        if rx.search(compact):
            return banner
    return ""


def select_by_tier(jobs: list, tiers: tuple[int, ...], label: str) -> list[tuple[int, dict]]:
    """按 ``idx+1`` 取指定赏金等级；取不齐则显式告警并回落末尾 N 条。

    返回 ``[(1-based 编号, job)]``，调用方据此写 ``job["tier"]``。
    六人组的 (5,7) 有两个独立时刻的别人卡实证（16:50 f8650、18:50 880f），
    其余派系的等级集合来自 18:50 别人卡 880f。
    """
    indexed = [(i + 1, j) for i, j in enumerate(jobs or [])
               if isinstance(j, dict)]
    picked = [p for p in indexed if p[0] in tiers]
    if len(picked) < len(tiers):
        log.warning("bounty_tier_missing tag=%s want=%s got=%s — 回落末尾 %d 条",
                    label, list(tiers), [p[0] for p in picked], len(tiers))
        picked = indexed[-len(tiers):]
    return picked
