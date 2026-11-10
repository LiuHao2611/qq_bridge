# -*- coding: utf-8 -*-
"""赏金卡「一个字一个字对齐」基准测试（@skill:prove-it）。

用户 2026-11-08 纠正：「你一个字一个字对齐了看…让你认真点禁止遗漏你听不懂吗」，
并否定上一轮把差异写成「口径差异 / 实时轮换 / 深矿无权威源」的交差方式。

本文件的标准答案 **逐字抄自用户给的两张截图**，不是照抄代码输出：

  * ``880f729e…png`` 18:50 别人机器人（Warframe Rabbit）卡 —— 主基准；
  * ``f8650ae4…png`` 16:50 用户原始参考图 —— 深矿上一轮次与派系编号佐证。

``REF_880F`` 是外部基准常量，改代码不许改这张表。圣所挑战名在两张图之间
已随 wf.wiki 快照轮换，故另备 ``bounty_reference_groups.json``（18:50 时刻的
groups，文本取自 WFA 词库 ``Challenge_*_Desc`` 模板）验证**同一套代码**在该时刻
确实产出别人卡的逐字文案；生产仍用实时快照，不用夹具顶替。

反例注入（prove-it「被纠正之后」）：把这次真实犯过的错放回去，基准必须报错：
  * 手写 ``_TIER_FALLBACK`` 快照编号 → 扎里曼「赏金1」；
  * 删深矿 title/final → 整行遗漏；
  * 词库旧译不校正 → 「资源搜掠」；
  * ``[-take:]`` 切片 → 六人组「歼灭：科腐者」错占赏金5。

pytest：  pytest tests/test_bounty_reference.py -q
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins.qq_bridge.pic.templates import bounty_deep as BD  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_live as BL  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_model as M  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures"
PL = json.loads((FX / "bounty_rsc_payload.json").read_text(encoding="utf-8"))
OFFICIAL = json.loads((FX / "bounty_official.json").read_text(encoding="utf-8"))
REF_GROUPS = json.loads(
    (FX / "bounty_reference_groups.json").read_text(encoding="utf-8"))["groups"]

#: 18:50 别人卡 880f 逐字抄录。目标行按 wf.wiki 完整 challenge 记录
#: （图上因列宽显示为「…」，不是内容缺失）。lvl 去掉徽章的「Lv」前缀。
REF_880F = {
    "合一众赏金轮换 B": {
        "items": ["Caliban 机体 蓝图", "赤炎流星 蓝图"]},
    "深矿赏金": {
        "section": "[钢铁之路]企业重组",
        "hero": {"section": "[钢铁之路]企业重组", "lvl": "130-140",
                 "stages": "5阶段", "final": "刺杀"}},
    "地球赏金": {
        "section": "MOD奖励轮换",
        "items": ["收割螺旋", "预言 探求", "角斗士 威猛", "私法 军备",
                  "预言 神密", "角斗士 决心", "私法 活力", "猎鹰俯击"],
        "jobs": [("赏金6", "找出遗失的器物", "缓存", "", "", "100"),
                 ("赏金7", "带他们回家 合一众", "防御", "", "", "50-70")]},
    "金星赏金": {
        "section": "债券轮换",
        "items": ["2x 培训债券", "2x 庇护债券", "2x 医疗债券",
                  "2x 预支债券", "2x 家族债券"],
        "jobs": [("赏金6", "资源窃取", "挖掘", "", "", "100"),
                 ("赏金7", "掘地三尺 合一众", "挖掘", "", "", "50-70")]},
    "火卫二赏金": {
        "section": "隔离库轮换 A",
        "items": ["孢丝感染枪 枪管"],
        "jobs": [("赏金5", "核心样本", "挖掘", "", "", "40-60"),
                 ("赏金6", "蛮暴之力", "生存", "", "", "100")]},
    "圣所赏金": {
        "jobs": [("赏金4", "恶涌", "刺杀", "虚空狂潮", "激活 3 个虚空泉脉", ""),
                 ("赏金5", "孽杀", "歼灭", "致命低语",
                  "击败嘲讽低语或尖刻低语", "")]},
    "扎里曼赏金": {
        "jobs": [("赏金3", "哈拉科防线", "歼灭", "唤醒与驱逐",
                  "唤醒一个休眠的天使并击败它", ""),
                 ("赏金4", "永视弧域", "虚空洪流", "窃取新力量",
                  "找到并激活 2 个虚空泉脉", ""),
                 ("赏金5", "奥金工场", "虚空决战", "积累氩烯",
                  "收集 9 个氩烯罐", "")]},
    "六人组赏金": {
        "jobs": [("赏金5", "至日广场", "防御", "天降正义·埃莉诺的赏金",
                  "从上方击杀 50 名敌人", ""),
                 ("赏金7", "传承种收割", "传承种收割", "天降正义·杀毒赏金任务",
                  "从高处击杀 50 名敌人", "")]},
}

_JOB_KEYS = ("tier", "name", "type", "mission", "objective", "lvl")


def _broken_data() -> dict:
    """生产事故时 ``_bounty_card_data`` 的形状：奖励段空、派系空壳、深矿硬编码。"""
    return {
        "title": "赏金任务一览", "remain": "2小时19分23秒",
        "rotation": {"rot": "B",
                     "rot_rewards": [{"name": "Caliban 机体 蓝图", "image": None},
                                     {"name": "赤炎流星 蓝图", "image": None}],
                     "vault": "A",
                     "vault_rewards": [{"name": "孢丝感染枪 枪管", "image": None}]},
        "worlds": [{"name": n, "icon": "circle", "jobs": []}
                   for n in ("地球赏金", "金星赏金", "火卫二赏金")],
        "deep": {"name": "深矿赏金", "tag": "[钢铁之路]", "title": "花园除草",
                 "detail": "等级 130-140 · 5阶段 · 最终：歼灭",
                 "icon": "bolt", "color": "#F44336"},
        "factions": [{"name": n, "jobs": []}
                     for n in ("扎里曼赏金", "圣所赏金", "六人组赏金")],
    }


def _snapshot(groups=None) -> dict:
    """走完整生产链路 enrich → build_model，抽出用户看得见的文字。"""
    pl = dict(PL)
    if groups is not None:
        pl = copy.deepcopy(PL)
        pl["groups"] = groups
    data = BL.enrich(_broken_data(), official=OFFICIAL, payload=pl)
    model = M.build_model(data)
    out: dict = {}
    for c in model["cards"]:
        rec: dict = {}
        if c.get("section"):
            rec["section"] = c["section"]
        if c.get("items"):
            rec["items"] = [i["text"] for i in c["items"]]
        h = c.get("hero")
        if h:
            rec["hero"] = {k: h[k] for k in ("section", "lvl", "stages", "final")}
        if c.get("jobs"):
            rec["jobs"] = [tuple(str(j.get(k) or "") for k in _JOB_KEYS)
                           for j in c["jobs"]]
        out[c["title"]] = rec
    return out


# ------------------------------------------------------------- 主基准 ----
def test_reference_card_matches_other_bot_word_for_word() -> None:
    """18:50 别人卡 8 张卡逐字相等；这是本轮用户要求的核心断言。"""
    assert _snapshot(REF_GROUPS) == REF_880F


def test_live_snapshot_differs_only_in_rotated_lab_challenge() -> None:
    """生产用实时快照：圣所挑战名会轮换，其余 7 卡必须与别人卡逐字一致。

    这不是「口径差异」的借口：同一套代码喂 18:50 快照就产出别人卡逐字文案
    （上一条用例），喂当前快照则按当前 groups.challenge 走词库反查。
    """
    live, ref = _snapshot(), REF_880F
    assert set(live) == set(ref)
    for title in ref:
        if title != "圣所赏金":
            assert live[title] == ref[title], "%s 与别人卡不一致" % title
    lab = live["圣所赏金"]["jobs"]
    assert [j[0] for j in lab] == ["赏金4", "赏金5"]
    assert [j[1] for j in lab] == ["恶涌", "孽杀"]
    # 当前挑战名是词库里另一组合法条目，且目标行逐字等于实时 groups.challenge
    cur = {g["tag"]: g for g in PL["groups"]}["EntratiLabSyndicate"]["jobs"]
    assert [j[2] for j in lab] == [cur[3]["mission"], cur[4]["mission"]]
    assert [j[4] for j in lab] == [cur[3]["challenge"], cur[4]["challenge"]]
    assert all(j[3] == BL.challenge_name(j[4]) for j in lab), \
        "第二行必须来自 WFA 词库反查，不能手写"


# --------------------------------------------------------- 轮换 / 编号 ----
def test_deep_rotation_matches_both_user_screenshots() -> None:
    """16:50 f8650 解放小动物/救援；18:50 880f 企业重组/刺杀；第三轮花园除草/歼灭。"""
    expiry = BD.official_expiry(OFFICIAL, PL)
    assert expiry == 1794143365798
    assert BD.ROTATIONS[BD.rotation_index(expiry)] == ("企业重组", "刺杀")
    assert BD.ROTATIONS[BD.rotation_index(expiry - 9000 * 1000)] == \
        ("解放小动物", "救援")
    assert BD.ROTATIONS[BD.rotation_index(expiry - 18000 * 1000)] == \
        ("花园除草", "歼灭")
    assert BD.rotation_index(0) == -1, "无 Expiry 必须显式判为不可推导"


def test_every_tier_is_jobs_array_index_plus_one() -> None:
    """六组绿色编号全部来自数组索引，禁止任何本地回退表。"""
    assert not hasattr(M, "_TIER_FALLBACK")
    data = BL.enrich(_broken_data(), official=OFFICIAL, payload=PL)
    boards = {b["tag"]: b for b in PL["bountyBoard"]}
    for tag, name, tiers in (
        ("CetusSyndicate", "地球赏金", (6, 7)),
        ("SolarisSyndicate", "金星赏金", (6, 7)),
        ("EntratiSyndicate", "火卫二赏金", (5, 6)),
    ):
        jobs = [x for x in boards[tag]["jobs"] if x.get("isVault") is not True]
        assert [BD.tier(i) for i, _ in enumerate(jobs)][-2:] == \
            [BD.tier(n - 1) for n in tiers]
        w = next(x for x in data["worlds"] if x["name"] == name)
        assert [j["tier"] for j in w["jobs"]] == [BD.tier(n - 1) for n in tiers]
    groups = {g["tag"]: g for g in PL["groups"]}
    for tag, (name, tiers, _) in BL._FACTION_BY_TAG.items():
        f = next(x for x in data["factions"] if x["name"] == name)
        assert [j["tier"] for j in f["jobs"]] == [BD.tier(n - 1) for n in tiers]
        assert all(n <= len(groups[tag]["jobs"]) for n in tiers)


# ------------------------------------------------------------ 反例闸门 ----
def _assert_ref_breaks(mutate, restore, broken_field: str) -> None:
    """把旧错放回去 → 基准必须不相等；改回来 → 必须重新相等。"""
    assert _snapshot(REF_GROUPS) == REF_880F, "前置条件：修好后必须全绿"
    mutate()
    try:
        got = _snapshot(REF_GROUPS)
    finally:
        restore()
    assert got != REF_880F, "把旧错放回去，逐字基准竟然没报错"
    assert _snapshot(REF_GROUPS) == REF_880F, "改回来必须恢复全绿"
    assert broken_field


def test_old_hardcoded_tier_table_is_caught() -> None:
    """旧 ``_TIER_FALLBACK`` 把 16:50 的「赏金1」写死 → 扎里曼编号必错。"""
    saved = M._tier
    old = {"扎里曼赏金": ("赏金1", "赏金4", "赏金5"),
           "地球赏金": ("赏金6", "赏金7"), "金星赏金": ("赏金6", "赏金7"),
           "火卫二赏金": ("赏金5", "赏金6"), "圣所赏金": ("赏金4", "赏金5"),
           "六人组赏金": ("赏金5", "赏金7")}

    def legacy(title, idx, job):
        seq = old.get(title, ())
        return seq[idx] if idx < len(seq) else "赏金%d" % (idx + 1)

    _assert_ref_breaks(lambda: setattr(M, "_tier", legacy),
                       lambda: setattr(M, "_tier", saved), "扎里曼赏金")


def test_deleting_deep_title_and_final_is_caught() -> None:
    """上一轮把深矿轮换名 + 最终任务整行删掉，必须被基准拦下。"""
    saved = BD.patch_deep

    def legacy(deep, official=None, payload=None):
        d = dict(deep or {})
        d.pop("title", None)
        d["tag"] = "[钢铁之路]"
        d["detail"] = "等级 130-140 · 5阶段"
        return d

    _assert_ref_breaks(lambda: setattr(BD, "patch_deep", legacy),
                       lambda: setattr(BD, "patch_deep", saved), "深矿赏金")


def test_old_lexicon_translation_is_caught() -> None:
    """词库旧译「资源搜掠」不校正 → 金星与别人卡差一个字。"""
    saved = dict(BD.JOB_NAME_FIX)
    _assert_ref_breaks(lambda: BD.JOB_NAME_FIX.clear(),
                       lambda: BD.JOB_NAME_FIX.update(saved), "金星赏金")


def test_tail_slice_is_caught() -> None:
    """旧 ``[-take:]`` 会把六人组 idx5「歼灭：科腐者」错当成赏金5。"""
    saved = BD.select_by_tier

    def legacy(jobs, tiers, label):
        indexed = [(i + 1, j) for i, j in enumerate(jobs or [])]
        return indexed[-len(tiers):]

    _assert_ref_breaks(lambda: setattr(BD, "select_by_tier", legacy),
                       lambda: setattr(BD, "select_by_tier", saved), "六人组赏金")


def test_narmer_cull_job_uses_lexicon_key_not_objective_fallback() -> None:
    """容器实时轮换出现 Cull 任务时也必须查到中文名，不能回落目标原文。"""
    assert BL.job_name("NarmerVenusCullJobExterminate", True) == "猎人杀手"
    assert BL.job_name("NarmerVenusTheftJobExcavation", True) == "掘地三尺"
    assert BL.job_name("AttritionBountyLib", True) == "带他们回家"


def test_all_bounty_template_files_under_20kb() -> None:
    tpl = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates"
    for name in ("bounty.py", "bounty_live.py", "bounty_deep.py",
                 "bounty_model.py", "bounty_atoms.py",
                 "bounty_layout.py", "bounty_icons.py"):
        size = (tpl / name).stat().st_size
        assert size <= 20480, "%s 超出 20KB 硬门禁: %dB" % (name, size)
    assert Path(__file__).stat().st_size <= 20480


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS (%d)" % len(fns))
