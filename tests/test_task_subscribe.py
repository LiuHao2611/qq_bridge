# -*- coding: utf-8 -*-
"""裂隙任务订阅（2026-11-05）回归：条件解析 / 匹配去重 / 存储 / 轮询推送 / 卡片门禁。

验收口径：pytest exit code；门禁脚本必须 ``exit 0``，违规 ``exit 1``。
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from src.plugins.qq_bridge.task_subscribe import (
    MAX_PUSH_PER_TICK, TaskSubscribeError, TaskSubscriptionMonitor,
    TaskSubscriptionStore, describe_task, match_task, parse_condition, task_key,
)

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


def _f(tier="Axi", mission="Survival", hard=True, loc="Void/Oxomoco",
       faction="Corrupted", end=1_800_000_000):
    return {"tier": tier, "missionType": mission, "hard": hard,
            "location": loc, "faction": faction, "end": end}


# ------------------------------------------------------------------ 解析 ----
@pytest.mark.parametrize("text,expect", [
    ("订阅钢铁裂隙 后纪 生存任务", {"mode": "steel", "tier": "Axi", "mission": "Survival"}),
    ("订阅 钢铁裂隙 后纪 生存", {"mode": "steel", "tier": "Axi", "mission": "Survival"}),
    ("#wf 订阅 钢铁裂隙 后纪 生存", {"mode": "steel", "tier": "Axi", "mission": "Survival"}),
    ("订阅 后纪 生存", {"tier": "Axi", "mission": "Survival"}),
    ("订阅 钢铁 裂隙 后纪 生存", {"mode": "steel", "tier": "Axi", "mission": "Survival"}),
    ("订阅 钢铁裂隙 后纪 生存 不限地图", {"mode": "steel", "tier": "Axi", "mission": "Survival"}),
    ("订阅 钢铁裂隙 后纪 生存 无论什么地图",
     {"mode": "steel", "tier": "Axi", "mission": "Survival"}),
    ("订阅 普通裂隙 中断 虚空", {"mode": "normal", "mission": "Disruption", "planet": "Void"}),
])
def test_parse_condition(text, expect):
    cond = parse_condition(text)
    for k, v in expect.items():
        assert cond[k] == v
    assert cond["label"]
    # 未指定地图 = 不限
    if "planet" not in expect:
        assert "不限地图" in cond["label"]


def test_parse_rejects_bad_input():
    with pytest.raises(TaskSubscribeError):
        parse_condition("订阅")
    with pytest.raises(TaskSubscribeError, match="没认出"):
        parse_condition("订阅 瞎写 后纪")
    with pytest.raises(TaskSubscribeError, match="同一维度"):
        parse_condition("订阅 钢铁裂隙 普通裂隙 生存")


# ------------------------------------------------------------ 匹配 / 去重 ----
def test_match_task_dimensions():
    cond = parse_condition("订阅 钢铁裂隙 后纪 生存")
    assert match_task(_f(), cond)
    assert not match_task(_f(hard=False), cond)          # 模式
    assert not match_task(_f(tier="Neo"), cond)          # 纪元
    assert not match_task(_f(mission="Defense"), cond)   # 任务
    assert match_task(_f(loc="Mars/Anthe"), cond)        # 不限地图
    assert not match_task(_f(), parse_condition("订阅 钢铁裂隙 后纪 生存 火星"))


def test_key_changes_when_cycle_rolls():
    a = _f(end=1_800_000_000)
    assert task_key(a) == task_key(dict(a))
    assert task_key(a) != task_key(_f(end=1_800_003_600))   # 新一轮 = 新键
    assert task_key(a) != task_key(_f(loc="Mars/Anthe"))    # 换地图 = 新键


def test_describe_task_zh():
    d = describe_task(_f())
    assert d["hard"] is True
    assert d["mission"] == "生存"
    assert d["tier_short"] == "后纪"
    assert d["faction"] == "O系"
    assert "(" in d["node"] and d["remain"]


# ------------------------------------------------------------------ 存储 ----
def test_store_crud_and_reload(tmp_path):
    path = tmp_path / "subs.json"
    store = TaskSubscriptionStore(path=path)
    cond = parse_condition("订阅 钢铁裂隙 后纪 生存")
    sub = store.add(cond, "group_123", text="订阅 钢铁裂隙 后纪 生存",
                    user_id=7, group_id=123, channel="napcat")
    assert len(store.list("group_123")) == 1
    assert store.list("group_456") == []                 # 会话隔离
    with pytest.raises(TaskSubscribeError, match="已经订阅过"):
        store.add(cond, "group_123")
    store.mark_checked(sub["id"], ["k1", "k2"], primed=True, pushed=1)
    # 重新打开同一个文件 = 模拟进程重启
    again = TaskSubscriptionStore(path=path)
    reloaded = again.get(sub["id"])
    assert reloaded["seen"] == ["k1", "k2"] and reloaded["primed"] is True
    assert reloaded["push_count"] == 1
    with pytest.raises(TaskSubscribeError, match="只能删本会话"):
        again.remove(sub["id"], "group_999")
    assert again.remove(sub["id"], "group_123")["id"] == sub["id"]
    assert TaskSubscriptionStore(path=path).list("group_123") == []


# ------------------------------------------------------------ 轮询 / 推送 ----
def test_prime_then_only_new_tasks_push(tmp_path):
    current = [_f(), _f(tier="Neo", mission="Defense", loc="Mars/Anthe")]
    pushed: list[dict] = []

    async def fetch():
        return list(current)

    async def on_match(sub, f):
        pushed.append(f)

    store = TaskSubscriptionStore(path=tmp_path / "subs.json")
    cond = parse_condition("订阅 钢铁裂隙 后纪 生存")
    sub = store.add(cond, "group_123", group_id=123)
    monitor = TaskSubscriptionMonitor(store=store, on_match=on_match, fetch=fetch)

    assert run(monitor.prime(sub["id"])) == 1      # 当下只有 1 条匹配
    run(monitor._tick())
    assert pushed == []                            # 已存在的不推

    current.append(_f(loc="Eris/Kala-azar", end=1_800_003_600))
    run(monitor._tick())
    assert len(pushed) == 1                        # 新刷出 → 推一张单卡
    assert pushed[0]["location"] == "Eris/Kala-azar"

    run(monitor._tick())
    assert len(pushed) == 1                        # 同一任务不重复推

    # 同一条件新一轮（end 变化）→ 视为新任务再推
    current.append(_f(loc="Void/Oxomoco", end=1_800_007_200))
    run(monitor._tick())
    assert len(pushed) == 2


def test_push_capped_per_tick(tmp_path):
    current = []

    async def fetch():
        return list(current)

    async def on_match(sub, f):
        return None

    store = TaskSubscriptionStore(path=tmp_path / "subs.json")
    sub = store.add(parse_condition("订阅 后纪 生存"), "group_1")
    monitor = TaskSubscriptionMonitor(store=store, on_match=on_match, fetch=fetch)
    assert run(monitor.prime(sub["id"])) == 0
    for i in range(MAX_PUSH_PER_TICK + 3):
        current.append(_f(loc="Mars/Node%d" % i, end=1_800_000_000 + i))
    run(monitor._tick())
    assert store.get(sub["id"])["push_count"] == MAX_PUSH_PER_TICK
    run(monitor._tick())
    assert store.get(sub["id"])["push_count"] == MAX_PUSH_PER_TICK


def test_fetch_failure_does_not_kill_monitor(tmp_path):
    async def boom():
        raise RuntimeError("数据源抽风")

    store = TaskSubscriptionStore(path=tmp_path / "subs.json")
    sub = store.add(parse_condition("订阅 后纪 生存"), "group_1")
    monitor = TaskSubscriptionMonitor(store=store, fetch=boom)
    assert run(monitor.prime(sub["id"])) == -1     # 预热失败不阻断订阅
    run(monitor._tick())                           # 不抛 = 循环不会被杀


# ------------------------------------------------------------ 指令入口 ----
@pytest.mark.parametrize("cmd,arg,expect", [
    ("订阅", "钢铁裂隙 后纪 生存", "add"),
    ("订阅钢铁裂隙", "后纪 生存任务", "add"),
    ("取消订阅", "a1b2c3d4", "remove"),
    ("取消订阅a1b2", "", "remove"),          # 动词与订阅号粘连
    ("订阅列表", "", "list"),
    ("退订", "全部", "remove"),
    ("wf", "订阅 钢铁裂隙 后纪 生存", "add"),
    ("裂隙", "", None),                      # 普通裂隙口令不能被订阅抢走
])
def test_task_sub_intent(cmd, arg, expect):
    from src.plugins.qq_bridge.game_tools import _task_sub_intent
    got = _task_sub_intent(cmd, arg)
    assert (got[0] if got else None) == expect


def test_natural_cmds_allow_subscription_in_ai_mode():
    from src.plugins.qq_bridge.game_tools import is_game_command
    assert is_game_command("订阅 钢铁裂隙 后纪 生存")
    assert is_game_command("订阅列表")
    assert is_game_command("取消订阅 a1b2c3d4")


# ------------------------------------------------------------ 卡片门禁 ----
def test_card_design_gate():
    script = ROOT / "tools" / "check_task_alert_layout.py"
    r = subprocess.run([sys.executable, str(script), "--selftest"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest: PASS" in r.stdout
    r = subprocess.run([sys.executable, str(script), "--check"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "自检 PASS" in r.stdout
