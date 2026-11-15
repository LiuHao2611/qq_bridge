# -*- coding: utf-8 -*-
"""斗破·任务途中的「机遇」专项验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_chance.py     # 自带 runner，不依赖 pytest
    pytest src/plugins/doupo/test_chance.py

验证两件事：
  1. 事件文案确实逐字来自原版静态文本资源（带 src 出处），表结构完整；
  2. 概率触发、条件分支（丹药不足 / 金币不足）、奖励结算、一次动作只掷一次、
     没任务不触发、命令层真的把机遇段拼进回复 —— 全部用注入 rng 做确定性验证。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 本脚本要验证「必触发」路径；其它 test_*.py 在 import 前置 0 来规避 flaky。
os.environ["DOUPO_CHANCE_RATE"] = "1.0"


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg  # noqa: E402
from doupo.store import Store  # noqa: E402

ALWAYS = lambda: 0.0   # noqa: E731  —— 必触发且抽到第一条事件
NEVER = lambda: 1.0    # noqa: E731  —— 概率判定不过


def _player(**kw):
    p = core.create_player("10001", "萧炎", "900000001")
    p.update(kw)
    return p


def _rate(rate: float):
    """临时改概率，返回恢复函数。"""
    old = cfg.CHANCE_RATE

    def restore():
        cfg.CHANCE_RATE = old

    cfg.CHANCE_RATE = rate
    return restore


# ---------------------------------------------------------------------------
# 1. 事件表：结构 + 原版出处 + 逐字文案
# ---------------------------------------------------------------------------
def test_event_table_is_well_formed():
    evs = cfg.CHANCE_EVENTS
    assert len(evs) >= 15, "事件太少，原版随机事件没整理全"
    tones = set()
    for e in evs:
        for k in ("name", "text", "tone", "weight", "reward", "src"):
            assert k in e, "事件缺字段 %s：%s" % (k, e)
        assert e["tone"] in ("good", "bad", "neutral")
        assert int(e["weight"]) > 0
        assert len(e["text"]) >= 8
        assert ".hy.txt" in e["src"], "必须标注原版资源出处"
        for bad in ("{", "}", "None", "nan", "\\1", "\\0", "\r"):
            assert bad not in e["text"], "文案残留控制符/占位符：%s" % bad
        tones.add(e["tone"])
    assert tones == {"good", "bad", "neutral"}, "正/负/中性事件都要有"
    assert any(e.get("need") for e in evs), "原版的神秘老者索丹分支丢了"
    assert any(e.get("guard_gold") for e in evs), "原版的「嫌你穷」分支丢了"
    assert any(e.get("quiz") for e in evs), "原版有奖竞猜丢了"


def test_original_lines_are_verbatim():
    """抽查原版原句，防止「整理」变成自己编文案。"""
    haystack = "\n".join(e["text"] + "\n" + str(e.get("poor_text") or "")
                         + "\n" + str(e.get("missing_text") or "") + "\n"
                         + "\n".join(e.get("quiz") or []) for e in cfg.CHANCE_EVENTS)
    quotes = [
        "萧炎在地上找到了一颗炎星石",              # s__d0:37
        "偶遇“张大善人”沿路施舍穷人",              # s__d0:40
        "匪头圣哥嫌弃萧炎太穷，将其踹走",          # s__d0:43
        "神秘老者狡诈的一笑，带着丹药转身不见了",  # s__d0:83
        "富贵险中求！走起！！",                    # s__d0:144
        "能在茫茫人海中寻找到散财童子",            # s__t:68
        "魂淡赠予你“丹方”",                        # s__t:75
        "抢来了真正的藏宝图",                      # s__t:76
        "运气好的话还能获得稀有材料",              # s__d14:58
    ]
    for q in quotes:
        assert q in haystack, "原版文案被改动或漏收：%s" % q


# ---------------------------------------------------------------------------
# 2. 概率 / 奖励 / 条件分支
# ---------------------------------------------------------------------------
def test_roll_respects_rate_and_rng():
    restore = _rate(0.0)
    try:
        assert core.roll_chance(_player(), rng=ALWAYS) is None, "概率 0 不该触发"
    finally:
        restore()
    restore = _rate(1.0)
    try:
        assert core.roll_chance(_player(), rng=NEVER) is None, "rng=1.0 不该命中"
        got = core.roll_chance(_player(), rng=ALWAYS)
        assert got and got["name"] == cfg.CHANCE_EVENTS[0]["name"]
    finally:
        restore()


def test_rewards_are_really_applied():
    restore = _rate(1.0)
    try:
        p = _player()
        before = core.count_item(p, "炎星石")
        got = core.roll_chance(p, rng=ALWAYS)
        assert got["tone"] == "good" and "炎星石 ×1" in got["gain"]
        assert core.count_item(p, "炎星石") == before + 1
    finally:
        restore()


def test_need_missing_branch_does_not_charge():
    ev = next(e for e in cfg.CHANCE_EVENTS if e.get("need") and e["tone"] == "good")
    p = _player()
    gold = int(p["gold"])
    bag_before = dict(p.get("bag") or {})
    got = core._apply_chance(p, ev, ALWAYS)
    assert got["tone"] == "neutral" and got["gain"] == "一无所获"
    assert "悻悻" in got["text"], "应走原版「丹不够，老者走开」分支"
    assert int(p["gold"]) == gold and dict(p.get("bag") or {}) == bag_before, \
        "不该扣钱也不该扣物"


def test_poor_guard_never_makes_gold_negative():
    ev = next(e for e in cfg.CHANCE_EVENTS if e.get("guard_gold"))
    p = _player(gold=10)
    got = core._apply_chance(p, ev, ALWAYS)
    assert got["tone"] == "neutral" and got["gain"] == "毫发无伤"
    assert "嫌弃萧炎太穷" in got["text"]
    assert int(p["gold"]) == 10, "穷分支不能倒扣金币"

    p2 = _player(gold=10000)
    got2 = core._apply_chance(p2, ev, ALWAYS)
    assert got2["tone"] == "bad" and int(p2["gold"]) == 10000 + int(ev["reward"]["gold"])
    assert int(p2["gold"]) >= 0


# ---------------------------------------------------------------------------
# 3. 任务链路：一次动作只掷一次，没任务不触发
# ---------------------------------------------------------------------------
def test_advance_task_rolls_once_and_no_task_no_roll():
    restore = _rate(1.0)
    try:
        calls = []

        def rng():
            calls.append(1)
            return 0.0

        p = _player()
        core.task_accept(p, rng=ALWAYS)   # 接一条支线
        got = core.advance_task(p, {"kill": 1, "herb": 2}, rng=rng)
        assert got is not None
        assert len(calls) == 2, "一次动作只能掷一次（判定+抽事件），实测 %d 次" % len(calls)
        assert int(p["task"]["progress"]) == 2, "支线 herb 进度没推进 2"
        assert int(p.get("main_progress") or 0) == 1, "主线 kill 进度没推进 1"

        calls.clear()
        p2 = _player(main_idx=len(cfg.MAIN_TASKS), task=None)
        assert core.advance_task(p2, "kill", 1, rng=rng) is None
        assert not calls, "没在任务途中不该掷机遇"
    finally:
        restore()


def test_task_progress_pure_functions_are_unchanged():
    """既有测试直接调 task_progress/main_progress，语义不能被动过。"""
    p = _player()
    core.task_accept(p, rng=ALWAYS)
    t = p["task"]
    before = int(t.get("progress", 0))
    assert core.task_progress(p, t["kind"], 2) is None
    assert int(t["progress"]) == before + 2
    assert core.main_progress(p, "kill", 1) is None


# ---------------------------------------------------------------------------
# 4. 命令层：机遇段真的进回复，关掉概率就不出现
# ---------------------------------------------------------------------------
def _fight_with_patched_win(store, qq):
    """把战斗结果固定为胜利，去掉随机性，只验机遇段的拼接。"""
    orig = core.fight

    def fake_fight(p, mon):
        return {"win": True, "rounds": 3, "taken": 12, "exp": 100, "gold": 50,
                "drops": [("血灵草", 1)],
                "log": [{"kind": "普攻", "crit": False, "dmg": 10}]}

    core.fight = fake_fight
    try:
        return asyncio.run(commands.cmd_fight(store, qq))
    finally:
        core.fight = orig


def test_command_reply_contains_chance_block():
    restore = _rate(1.0)
    store = Store(os.path.join(tempfile.mkdtemp(prefix="doupo_chance_"), "doupo.json"))
    asyncio.run(store.load())
    try:
        core.set_chance_rng(ALWAYS)
        p = _player()
        asyncio.run(store.put_player(p))
        r = _fight_with_patched_win(store, "10001")
        assert "⚔️" in r and "修为" in r, "原有战报不能被动掉"
        assert "机遇 ·" in r and "结算：" in r, "机遇段没拼进回复：%s" % r[-160:]
    finally:
        core.set_chance_rng(None)
        restore()


def test_chance_reply_format_is_clean():
    """机遇段拼进回复后仍要满足「输出格式无异常」：无占位符、无连续空行、无超长行。"""
    restore = _rate(1.0)
    store = Store(os.path.join(tempfile.mkdtemp(prefix="doupo_chance_"), "doupo.json"))
    asyncio.run(store.load())
    try:
        core.set_chance_rng(ALWAYS)
        asyncio.run(store.put_player(_player()))
        r = _fight_with_patched_win(store, "10001")
        assert r.count("机遇 ·") == 1 and r.count("结算：") == 1
        for bad in ("None", "nan", "{", "}", "\r", "未识别", "请先发送"):
            assert bad not in r, "回复残留异常串：%s" % bad
        lines = r.split("\n")
        assert max(len(x) for x in lines) < 160, "出现超长行"
        for i in range(len(lines) - 1):
            assert not (not lines[i].strip() and not lines[i + 1].strip()), "连续空行"
    finally:
        core.set_chance_rng(None)
        restore()


def test_command_reply_clean_when_chance_off():
    restore = _rate(0.0)
    store = Store(os.path.join(tempfile.mkdtemp(prefix="doupo_chance_"), "doupo.json"))
    asyncio.run(store.load())
    try:
        p = _player()
        asyncio.run(store.put_player(p))
        r = _fight_with_patched_win(store, "10001")
        assert "机遇" not in r, "概率关掉后不该有机遇段"
    finally:
        restore()


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
    print("CHANCE_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
