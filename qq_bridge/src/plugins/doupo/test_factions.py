# -*- coding: utf-8 -*-
"""斗破门派系统数据层验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_factions.py     # 自带 runner
    pytest src/plugins/doupo/test_factions.py

用 importlib 按文件路径直接加载 factions.py，**不触发 doupo/__init__.py**，
以此证明它是零副作用的纯数据模块（不需要 nonebot、不需要 PIL）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_FP = Path(__file__).with_name("factions.py")
_spec = importlib.util.spec_from_file_location("doupo_factions_standalone", str(_FP))
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

VALID_SOURCES = {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "G1",
                "C1", "C2", "C3"}


def test_validate_is_clean():
    problems = F.validate()
    assert not problems, "数据自检失败：%s" % problems


def test_pool_sizes_and_tiers():
    assert len(F.FACTIONS) >= 25, "门派太少（剔除薄门派后应为 25）"
    for pool, minimum in (("starter", 8), ("advanced", 10), ("hidden", 6)):
        n = len(F.by_pool(pool))
        assert n >= minimum, "%s 池只有 %d 个" % (pool, n)
    for tier in F.TIERS:
        assert F.by_tier(tier), "缺 %s 期门派" % tier


def test_starter_pool_is_newbie_only():
    for fid, f in F.by_pool("starter").items():
        assert f["tier"] == "early", "%s 不该在新手池" % fid
        assert int(f["unlock_lv"]) == 1, "%s 新手不该有等级门槛" % fid
    assert all(int(f["unlock_lv"]) >= 40 for f in F.by_pool("advanced").values())
    assert all(int(f["unlock_lv"]) >= 90 for f in F.by_pool("hidden").values())


def test_every_faction_has_npcs_and_sources():
    for fid, f in F.FACTIONS.items():
        assert f["npcs"], "%s 无代表人物" % fid
        assert f["sources"], "%s 无来源" % fid
        unknown = set(f["sources"]) - VALID_SOURCES
        assert not unknown, "%s 有未知来源编号 %s" % (fid, unknown)
        assert f["base"] and f["name"]


def test_random_faction_is_deterministic_and_pool_scoped():
    first = F.random_faction("starter", rng=lambda: 0.0)
    last = F.random_faction("starter", rng=lambda: 0.999999)
    assert first and last and first != last
    for pool in F.POOLS:
        allowed = set(F.by_pool(pool))
        for i in range(200):
            got = F.random_faction(pool, rng=lambda: (i % 100) / 100.0)
            assert got in allowed, "%s 抽到了池外门派 %s" % (pool, got)
    assert F.random_faction("unknown-pool") is None


def test_starter_kit_is_complete():
    for fid in F.FACTIONS:
        kit = F.starter_kit(fid)
        assert kit["faction"] == fid and kit["faction_name"]
        for k in ("skill", "special", "item", "relic"):
            assert kit[k], "%s starter.%s 为空" % (fid, k)


def test_assign_only_adds_fields():
    p = {"lv": 1, "gold": 10000, "name": "测试"}
    before = dict(p)
    kit = F.assign_faction(p, rng=lambda: 0.0)
    assert kit and p["faction"] in F.FACTIONS
    for k, v in before.items():
        assert p[k] == v, "既有字段 %s 被改动" % k
    added = set(p) - set(before)
    assert added == {"faction", "faction_name", "skill", "special", "item", "relic"}
    assert F.faction_of(p) is F.FACTIONS[p["faction"]]
    assert F.faction_of({}) is None


def test_special_skills_unlock_by_level():
    f = F.FACTIONS["guzu"]
    lows = [s["unlock_lv"] for s in f["special_skills"]]
    assert min(lows) > 1, "古族特殊斗技不该 1 级全开"
    assert F.special_skills_at("guzu", 1) == []
    assert len(F.special_skills_at("guzu", 99)) == len(f["special_skills"])
    assert F.special_skills_at("no-such-faction", 99) == []


def test_key_factions_keep_authoritative_config():
    """防止整理数据时把权威设定改坏：抽查几派的关键斗技/器物/异火。"""
    checks = {
        "fengleige": ("三千雷幻身", "三千雷动"),
        "guzu": ("帝印决", "金帝焚天炎"),
        "danta": ("三千焱炎火", "星域"),
        "xingyunge": ("骨灵冷火", "黑魔鼎"),
        "fenyangu": ("九龙雷罡火", "山熔鼎"),
        "tianmingzong": ("天冥修罗手",),
        "jiuyou": ("黄泉天怒",),
        "yunlanzong": ("天道神雷剑诀", "天刑台"),
        "xiaojia": ("狂狮怒罡", "萧族印信"),
    }
    for fid, needles in checks.items():
        blob = str(F.FACTIONS[fid])
        for n in needles:
            assert n in blob, "%s 丢失关键配置 %s" % (fid, n)


def test_game_original_skills_are_marked():
    """游戏自造的绝学必须显式标注，不能被当原著设定。"""
    blob = str(F.FACTIONS["yunlanzong"]) + str(F.FACTIONS["motie"])
    for word in ("天道神雷剑诀", "寒冰屏障", "游戏原创"):
        assert word in blob, "游戏原创标注丢失：%s" % word


def test_no_protagonist_gear_in_faction_starters():
    """主角个人装备/自创斗技不能混入门派发放物。"""
    banned = ("玄重尺", "佛怒火莲", "焚诀", "异火恒古尺")
    for fid, f in F.FACTIONS.items():
        kit = " ".join(str(v) for v in f["starter"].values())
        for b in banned:
            assert b not in kit, "%s 的 starter 混入主角装备 %s" % (fid, b)


def test_community_sources_are_used():
    """用户要求补全社区/论坛/大佬帖子数据：C1/C2/C3 必须真正进入数据。"""
    blob = str(F.FACTIONS)
    for tag in ("C1", "C2", "C3"):
        assert ("[%s]" % tag) in blob, "社区来源 %s 未进入 factions 数据" % tag
    # 至少 50 处社区标注，证明不是空壳
    assert blob.count("[C") >= 50, "社区标注过少：%d" % blob.count("[C")


def test_community_key_skills_attributed():
    """抽查关键门派斗技来自社区整理（人物/门派归属），而非主角个人配置。"""
    checks = {
        "yunlanzong": ("玄岩盾", "风火木壁", "大悲撕风手"),
        "tianmingzong": ("嗜血化骨手", "大裂风", "天冥掌"),
        "hundian": ("九幽魂手", "黑色骷髅"),
        "jianan": ("血裂斩", "大裂山", "卸力水镜"),
        "xuezong": ("嗜血印", "雷蝠天翼"),
        "taixugulong": ("屠龙剑", "龙皇钟"),
        "jiuyou": ("黄泉大印", "枯叶大毒掌"),
        "guzu": ("龙蛇步法", "万兽枪法"),
        "fenyangu": ("纯阳绵手", "太乙火罗掌"),
        "fengleige": ("风雷爪", "圣像妖凰钟"),
    }
    for fid, needles in checks.items():
        blob = str(F.FACTIONS[fid])
        for n in needles:
            assert n in blob, "%s 缺少社区归属斗技 %s" % (fid, n)


def test_c1_attribution_guard():
    """C1（张谦方《斗破设定整理·斗技整理》）逐条核对后钉死的归属护栏，
    防止再次把斗技张冠李戴挂错门派。依据：bilibili opus 959287705904087041。"""
    # 正确归属（C1 原文）：血裂斩/影血闪=迦南吴昊；血煞掌/嗜血化骨手=天冥宗易尘；
    # 大寂灭指=古族；化血大法=魂殿四天尊；赤毒掌=神农老人。
    assert "血裂斩" in str(F.FACTIONS["jianan"]), "血裂斩应属迦南吴昊"
    assert "血裂斩" not in str(F.FACTIONS["xuezong"]), "血裂斩不是血宗范痨的，误挂血宗"
    assert "血煞掌" in str(F.FACTIONS["tianmingzong"]), "血煞掌应属天冥宗易尘"
    assert "血煞掌" not in str(F.FACTIONS["xuezong"]), "血煞掌不是血宗的，误挂血宗"
    assert "大寂灭指" in str(F.FACTIONS["guzu"]), "大寂灭指应属古族"
    assert "大寂灭指" not in str(F.FACTIONS["hunzu"]), "大寂灭指不是魂族的，误挂魂族"
    assert "化血大法" in str(F.FACTIONS["hunzu"]), "化血大法应属魂殿(魂族明面势力)"
    assert "赤毒掌" in str(F.FACTIONS["tianfu"]), "赤毒掌属神农老人，其为本盟 NPC"


def test_no_cross_faction_high_tier_skills():
    """原著原则：高级斗技一般只属一门派（[游戏核心] 主角通用注入不在此列）。
    下列斗技经 C1/百科逐条核对后各有唯一正主，不得跨门派重复。"""
    checks = [
        ("惊蛰雷法", "moyangu", "xiaojia"),        # 魔炎谷/萧厉(923章)，非萧家
        ("碎魂冥掌", "hunzu", "hundian"),          # 魂族魂厉(1349章)，非魂殿
        ("风之极·落日耀", "yunlanzong", "huazong"),  # 云岚宗云韵/纳兰嫣然(338章)
        ("飞絮", "yunlanzong", "huazong"),          # 云岚宗系(305章)
    ]
    for name, correct, wrong in checks:
        assert name in str(F.FACTIONS[correct]), "%s 应属 %s" % (name, correct)
        assert name not in str(F.FACTIONS[wrong]), "%s 误挂 %s（违反不跨门派原则）" % (name, wrong)
    # 碧蛇三花瞳 属天蛇府圣女青鳞（C1/百科一致），曾误挂蛇人族；天蛇府已移出游戏，
    # 此处钉死它不得回潮到蛇人族（防再次张冠李戴）。
    assert "碧蛇三花瞳" not in str(F.FACTIONS["sherenzu"]), "碧蛇三花瞳不得再误挂蛇人族"


def test_xiaojia_keeps_protagonist_core_only():
    """萧家是用户豁免的默认主角门派，保留 4 个 [游戏核心] 主角战斗斗技；
    其余 28 派一律按原著，不得再注入主角通用斗技（高级斗技不跨门派）。"""
    core = ["龙吟碎", "天火三玄变", "八极崩", "五轮离火法"]
    for c in core:
        assert any(s == c + "[游戏核心]" for s in F.FACTIONS["xiaojia"]["skills"]), \
            "萧家缺少核心战斗斗技 %s" % c
    for fid, f in F.FACTIONS.items():
        if fid == "xiaojia":
            continue
        for c in core:
            assert not any(s == c + "[游戏核心]" for s in f["skills"]), \
                "%s 不应含有主角通用斗技 %s（违反高级斗技不跨门派）" % (fid, c)


def test_playable_factions_meet_minimum_bar():
    """可入门派最低门槛：特殊斗技≥2 + 至少 1 件特殊物品。

    2026-11-15 修正：原断言要求「非稀疏派必须有异火/灵焰」，等于把原著里
    极稀有的异火写成了门派标配。现改为：异火允许为空；若该派确有传承异火，
    每条必须带「第X章「标题」」原著引用且不得自造（章节真实性另由
    _verify_flames.py 门禁对 1608 章原著正文逐条核验）。
    """
    import re
    cite = re.compile(r"第[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十百千\d]+章「[^」]+」")
    for fid, f in F.FACTIONS.items():
        sp = f["special_skills"]; fl = f["flames"]; it = f["items"]
        # 2026-11-15 梳理：炼药师公会 / 丹塔的「丹会」「大长老院表决」「丹会炼术」
        # 是炼药界典故而非斗技，已移到 lore。旧断言「special_skills>=2」会逼着
        # 把典故重新填回特殊斗技里，等于把错误固化 —— 改为「斗技 + 典故资料 >=2」。
        lore = f.get("lore", ())
        assert len(sp) + len(lore) >= 2, "%s 斗技/典故资料不足(%d)" % (fid, len(sp) + len(lore))
        assert len(it) >= 1, "%s 无特殊物品" % fid
        for e in fl:
            assert cite.search(e), "%s 异火归属缺原著章节引用: %s" % (fid, e[:40])
            assert "[游戏原创]" not in e and "游戏自设" not in e, \
                "%s 把游戏自造灵焰冒充原著异火: %s" % (fid, e[:40])


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    fail = []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print("PASS  %s" % name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("FACTIONS_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
