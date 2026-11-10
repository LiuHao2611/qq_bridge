# -*- coding: utf-8 -*-
"""赏金卡生产数据补齐的验收测试（@skill:prove-it + @skill:card-design）。

用户 2026-11-08 两次纠正：
  * 「奖励内容呢，而且任务内容和别人机器人的也不一样，你先搞清楚别人的数据来源」
  * 「你是不是又忘记更新生产环境代码了」

本文件用 **2026-11-08 18:55 真实快照**（worldstate.wf.wiki RSC payload +
api.warframe.com worldState.php）验证生产链路确实把内容补回来了，而不是再次
只做调研 / 只 push 不部署。夹具：
  tests/fixtures/bounty_rsc_payload.json  （bountyBoard / groups / bounties）
  tests/fixtures/bounty_official.json     （SyndicateMissions jobType + 等级）

反例注入（prove-it「被纠正之后」）：把这次真实犯过的错放回去，检查必须报错：
  * RSC 用旧 ``replace('\\"','"')`` 解码 → bountyBoard 解析失败；
  * payload 丢失 bountyBoard → 不许编造 MOD / 债券，且必须显式告警；
  * 词库全空 → 不许编造中文任务名。

直接跑： python tests/test_bounty_live.py
pytest：  pytest tests/test_bounty_live.py -q
"""
from __future__ import annotations

import contextlib
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins.qq_bridge.pic.theme import LIGHT_THEME  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty as BOUNTY  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_atoms as A  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_icons as BI  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_layout as L  # noqa: E402
from src.plugins.qq_bridge.pic.templates import bounty_live as BL  # noqa: E402
from src.plugins.qq_bridge.pic.templates.bounty_model import build_model  # noqa: E402

OUT = Path(__file__).resolve().parent
TPL = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates"
FX = OUT / "fixtures"
SOURCE = "数据源：worldstate.wf.wiki / 官方 worldstate"

PL = json.loads((FX / "bounty_rsc_payload.json").read_text(encoding="utf-8"))
OFFICIAL = json.loads((FX / "bounty_official.json").read_text(encoding="utf-8"))

#: 别人机器人（Warframe Rabbit）卡上逐字出现的 8 MOD / 5 债券
REF_MODS = ["收割螺旋", "预言 探求", "角斗士 威猛", "私法 军备",
            "预言 神密", "角斗士 决心", "私法 活力", "猎鹰俯击"]
REF_BONDS = ["2x 培训债券", "2x 庇护债券", "2x 医疗债券",
             "2x 预支债券", "2x 家族债券"]
ALL_ITEMS = REF_MODS + REF_BONDS + [
    "Caliban 机体 蓝图", "赤炎流星 蓝图", "孢丝感染枪 枪管"]


# ------------------------------------------------------------ 夹具 ----
def _rsc_html(pl: dict) -> str:
    """把真实载荷重新编码成 RSC JS 字符串字面量，供 decode_rsc 端到端验证。

    必须 ``ensure_ascii=False``，否则中文变 \\uXXXX 会让图标索引拿不到中文名。
    """
    text = json.dumps(pl, ensure_ascii=False, separators=(",", ":"))
    return 'self.__next_f.push([1,"%s"])' % json.dumps(text, ensure_ascii=False)[1:-1]


def _broken_data() -> dict:
    """生产事故时 ``_bounty_card_data`` 的真实形状：奖励段整段消失、
    派系为空壳、深矿是硬编码文案。rotation 因 bounties 解析成功仍有图。"""
    return {
        "title": "赏金任务一览",
        "remain": "2小时19分23秒",
        "rotation": {
            "rot": "B",
            "rot_rewards": [{"name": "Caliban 机体 蓝图", "image": None},
                            {"name": "赤炎流星 蓝图", "image": None}],
            "vault": "A",
            "vault_rewards": [{"name": "孢丝感染枪 枪管", "image": None}],
        },
        "worlds": [{"name": n, "icon": "circle", "jobs": []}
                   for n in ("地球赏金", "金星赏金", "火卫二赏金")],
        "deep": {"name": "深矿赏金", "tag": "[钢铁之路]", "title": "花园除草",
                 "detail": "等级 130-140 · 5阶段 · 最终：歼灭",
                 "icon": "bolt", "color": "#F44336"},
        "factions": [{"name": n, "jobs": []}
                     for n in ("扎里曼赏金", "圣所赏金", "六人组赏金")],
    }


def _enriched() -> dict:
    return BL.enrich(_broken_data(), official=OFFICIAL, payload=PL)


@contextlib.contextmanager
def _fixture_icon_index():
    """用本轮真实载荷重建图标索引，避免旧磁盘索引缺「孢丝感染枪 枪管」。

    必须恢复原状态：模块级 ``BI._INDEX`` 是进程共享的，直接覆盖会污染
    ``tests/test_bounty_card.py`` 里依赖旧索引的「孢丝感染枪 枪托」用例。
    """
    saved = (dict(BI._INDEX), BI._INDEX_TS, set(BI._NEG))
    idx = BI.index_from_html(_rsc_html(PL))
    assert idx, "真实载荷没解析出任何官方图标 URL"
    BI._INDEX, BI._INDEX_TS = idx, time.time()
    BI._NEG.clear()
    try:
        yield idx
    finally:
        BI._INDEX, BI._INDEX_TS, BI._NEG = saved[0], saved[1], saved[2]


# --------------------------------------------------- RSC 解码 / 反例 ----
def test_decode_rsc_recovers_all_three_keys() -> None:
    pl = BL.parse_payload(_rsc_html(PL))
    assert len(pl.get("bountyBoard") or []) == 3
    assert len(pl.get("groups") or []) == 3
    assert isinstance(pl.get("bounties"), dict)
    assert pl["bounties"]["rotRewards"] and pl["bounties"]["vaultRewards"]


def test_legacy_replace_decode_is_the_real_bug() -> None:
    """把旧 ``replace('\\"','"')`` 放回去：带转义引号的 reward 名必须让它炸。"""
    pl = copy.deepcopy(PL)
    pl["bountyBoard"][0]["jobs"][0]["rewards"].append(
        {"name": '"混乱"残留物', "icon": "https://content.warframe.com/x.png", "count": 1})
    html = _rsc_html(pl)
    # 新写法：逐块按 JS 字符串解码，转义完整保留（JSON 里的 \" 仍在）
    good = BL.decode_rsc(html)
    assert r'\"混乱\"残留物' in good, "新解码必须保留 JSON 字符串里的转义引号"
    assert BL.parse_payload(html)["bountyBoard"], "新解码必须拿到 bountyBoard"

    legacy = "".join(BL._BLOCK_RE.findall(html)).replace('\\"', '"')
    i = legacy.find('"bountyBoard":')
    body = legacy[i + len('"bountyBoard"'):].lstrip()[1:].lstrip()
    try:
        json.JSONDecoder().raw_decode(body)
    except json.JSONDecodeError:
        pass  # 期望：旧写法解析失败 → 生产里 except: pass 就会静默丢掉奖励
    else:
        raise AssertionError("旧 replace 写法没有被拦下，反例注入失效")


# ------------------------------------------------------- 内容补齐 ----
def test_enrich_restores_mod_and_bond_rewards() -> None:
    d = _enriched()
    w = {x["name"]: x for x in d["worlds"]}
    assert w["地球赏金"]["label"] == "MOD奖励轮换"
    assert w["地球赏金"]["items"] == REF_MODS
    assert w["金星赏金"]["label"] == "债券轮换"
    assert w["金星赏金"]["items"] == REF_BONDS
    m = build_model(d)
    earth = next(c for c in m["cards"] if c["title"] == "地球赏金")
    venus = next(c for c in m["cards"] if c["title"] == "金星赏金")
    assert [i["text"] for i in earth["items"]] == REF_MODS
    assert [i["text"] for i in venus["items"]] == REF_BONDS
    assert earth["section"] == "MOD奖励轮换" and venus["section"] == "债券轮换"


def test_world_jobs_match_other_bot_reference_card() -> None:
    """别人卡：找出遗失的器物/缓存、带他们回家 合一众/防御、掘地三尺、
    核心样本/挖掘、蛮暴之力/生存；火卫二顺序 40-60 在 100-100 之前。"""
    d = _enriched()
    w = {x["name"]: x for x in d["worlds"]}
    assert [(j["lvl"], j["type"], j["name"], j["narmer"])
            for j in w["地球赏金"]["jobs"]] == [
        ("100-100", "缓存", "找出遗失的器物", False),
        ("50-70", "防御", "带他们回家", True)]
    assert [(j["lvl"], j["type"], j["name"], j["narmer"])
            for j in w["金星赏金"]["jobs"]] == [
        ("100-100", "挖掘", "资源窃取", False),
        ("50-70", "挖掘", "掘地三尺", True)]
    assert [(j["lvl"], j["type"], j["name"])
            for j in w["火卫二赏金"]["jobs"]] == [
        ("40-60", "挖掘", "核心样本"),
        ("100-100", "生存", "蛮暴之力")]


def test_factions_come_from_groups_with_challenge_and_target_lines() -> None:
    """别人卡扎里曼：唤醒与驱逐 / 目标：唤醒一个休眠的天使并击败它。"""
    d = _enriched()
    f = {x["name"]: x for x in d["factions"]}
    groups = {g["tag"]: g for g in PL["groups"]}
    jobs = f["扎里曼赏金"]["jobs"]
    assert [j["name"] for j in jobs] == ["哈拉科防线", "永视弧域", "奥金工场"]
    assert [j["type"] for j in jobs] == ["歼灭", "虚空洪流", "虚空决战"]
    assert [j["mission"] for j in jobs] == ["唤醒与驱逐", "窃取新力量", "积累氩烯"]
    # 目标行逐字等于 groups.challenge，不是手写清单
    z = [j for j in groups["ZarimanSyndicate"]["jobs"] if j["node"] == "哈拉科防线"][0]
    assert jobs[0]["objective"] == z["challenge"]
    for name, tag in (("圣所赏金", "EntratiLabSyndicate"),
                      ("六人组赏金", "HexSyndicate")):
        assert f[name]["jobs"], f"{name} 不能是空卡"
        for j in f[name]["jobs"]:
            assert j["objective"], "目标行必须来自 groups.challenge"
            assert j["mission"], "第二行挑战名必须来自 WFA 词库"
    assert "的赏金" in f["六人组赏金"]["jobs"][0]["mission"], \
        "六人组 ally 字段必须体现在第二行（词库 |ALLY|的赏金任务 模板）"


def test_deep_rotation_and_final_task_come_from_official_expiry() -> None:
    """用户纠正：深矿轮换名与「最终任务」必须整行保留，不许删、不许硬编码。"""
    from src.plugins.qq_bridge.pic.templates import bounty_deep as BD
    d = _enriched()
    deep = d["deep"]
    assert deep["tag"] == "[钢铁之路]"
    assert deep["title"] == "企业重组", "当前 Expiry 应为企业重组（别人卡 880f）"
    assert deep["detail"] == "等级 130-140 · 5阶段 · 最终：刺杀"
    hero = next(c for c in build_model(d)["cards"]
                if c["title"] == "深矿赏金")["hero"]
    assert hero["section"] == "[钢铁之路]企业重组"
    assert hero["final"] == "刺杀", "最终任务行禁止遗漏"
    # 上一轮次 16:50 用户原始参考图 f8650：解放小动物 / 救援
    exp = BD.official_expiry(OFFICIAL, PL)
    assert BD.ROTATIONS[BD.rotation_index(exp - 9000 * 1000)] == \
        ("解放小动物", "救援")


def test_rotation_images_are_not_overwritten() -> None:
    """旧 enrich 曾用 bounties.vaultRewards 覆盖并丢掉 image，必须锁死。"""
    before = _broken_data()["rotation"]
    after = _enriched()["rotation"]
    assert after == before
    assert after["vault_rewards"][0]["name"] == "孢丝感染枪 枪管"


# ------------------------------------------------------- 词库覆盖 ----
def test_wfa_job_name_covers_every_official_job() -> None:
    total = hit = 0
    for sy in OFFICIAL["SyndicateMissions"]:
        for j in sy["Jobs"]:
            leaf = str(j.get("jobType") or "").rsplit("/", 1)[-1]
            if not leaf:
                continue
            total += 1
            if BL.job_name(leaf, "/Narmer/" in str(j.get("jobType") or "")):
                hit += 1
    assert total == 20 and hit == total, f"WFA 词库覆盖 {hit}/{total}"
    assert BL.job_name("ReclamationBountyCache") == "找出遗失的器物"
    assert BL.job_name("AttritionBountyLib", True) == "带他们回家"
    assert BL.job_name("DeimosExcavateBounty") == "核心样本"
    assert BL.job_name("DeimosGrnSurvivorBounty") == "蛮暴之力"


def test_challenge_name_covers_every_group_job() -> None:
    total = hit = 0
    for g in PL["groups"]:
        for j in g["jobs"]:
            ch = str(j.get("challenge") or "")
            if ch == "$undefined":
                continue
            total += 1
            if BL.challenge_name(ch):
                hit += 1
    assert total == 17 and hit == total, f"挑战名反查 {hit}/{total}"
    assert BL.challenge_name("唤醒一个休眠的天使并击败它") == "唤醒与驱逐"
    assert BL.challenge_name("从上方击杀 50 名敌人") == "天降正义"


# --------------------------------------------------- 反例注入闸门 ----
def test_missing_payload_never_fabricates_rewards_and_warns() -> None:
    """把这次真实犯过的错放回去：bountyBoard 丢失，必须不编造 + 显式告警。"""
    events: list[str] = []
    orig = BL.log.warning
    BL.log.warning = lambda *a, **k: events.append(str(a[0]) if a else "")
    try:
        d = BL.enrich(_broken_data(), official=OFFICIAL, payload={"groups": []})
    finally:
        BL.log.warning = orig
    assert all(not w.get("items") for w in d["worlds"])
    assert any("bounty_live_no_payload" in e for e in events), events


def test_empty_lexicon_never_fabricates_chinese_names() -> None:
    saved = (BL._LEAF, BL._DEIMOS, BL._NARMER, BL._NARV, BL._CH_TPLS)
    BL._LEAF, BL._DEIMOS, BL._NARMER, BL._NARV, BL._CH_TPLS = {}, None, None, None, None
    try:
        assert BL.job_name("ReclamationBountyCache") == ""
        d = BL.enrich(_broken_data(), official=OFFICIAL, payload=PL)
        # 奖励仍来自 bountyBoard（不依赖词库），任务名只回落到 RSC objective
        assert d["worlds"][0]["items"] == REF_MODS
        assert d["worlds"][0]["jobs"][0]["name"] == "回收"
        assert d["factions"][0]["jobs"][0]["mission"] == ""
    finally:
        (BL._LEAF, BL._DEIMOS, BL._NARMER,
         BL._NARV, BL._CH_TPLS) = saved


def test_registered_bounty_build_calls_enrich() -> None:
    """注册入口必须真的调 enrich，不能又变成「调研完没接线」。"""
    calls: list[str] = []
    orig = BL.enrich
    BL.enrich = lambda data: (calls.append("enrich"), data)[1]
    try:
        BOUNTY.build(LIGHT_THEME, data=_broken_data(), width=800, source=SOURCE)
    finally:
        BL.enrich = orig
    assert calls == ["enrich"]


# ------------------------------------------------------ 渲染 / 截图 ----
def test_all_bounty_template_files_under_20kb() -> None:
    for name in ("bounty.py", "bounty_live.py", "bounty_deep.py",
                 "bounty_model.py", "bounty_atoms.py",
                 "bounty_layout.py", "bounty_icons.py"):
        size = (TPL / name).stat().st_size
        assert size <= 20480, f"{name} 超出 20KB 硬门禁: {size}B"
    assert __file__ and Path(__file__).stat().st_size <= 20480


def test_render_real_fixture_strict() -> None:
    """真实载荷补齐后出图：排版 0 违规、图标 0 缺失，并落盘供肉眼核对。"""
    with _fixture_icon_index():
        assert all(BI.ensure(n) for n in ALL_ITEMS), \
            [n for n in ALL_ITEMS if not BI.ensure(n)]
        d = _enriched()
        c = L.build(LIGHT_THEME, data=d, width=800, source=SOURCE)
        problems = list(getattr(c, "layout_problems", []))
        A.gate((16, 0, c.width - 16, c.height), getattr(c, "layout_boxes", []),
               min_gap=2, strict=True)
        L.gate_icons(c, strict=True)
        assert not problems, problems[:5]
        assert c.missing_icons == []
        finals = [b for b in c.layout_boxes if b[0].endswith(":final")]
        assert finals, "深矿「最终任务：」整行必须画出来，禁止删行"
        assert c.width == 800 and c.height > 1200
        path = OUT / "_shot_bounty_live.png"
        c.save(str(path))
        print(f"[shot] {path}  {c.width}x{c.height}  "
              f"problems={len(problems)} missing_icons={len(c.missing_icons)}")
        assert path.is_file() and path.stat().st_size > 10000


def test_registered_render_sync_with_enriched_data() -> None:
    from src.plugins.qq_bridge.pic import render_sync
    with _fixture_icon_index():
        png = render_sync("bounty", card="星际战甲·赏金",
                          data=_enriched(), source=SOURCE, width=800)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 10000


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"ALL PASS ({len(fns)})")
