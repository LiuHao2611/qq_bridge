# -*- coding: utf-8 -*-
"""M2 提醒/定时任务验收（2026-10-20，tools/scheduler.py）。

覆盖：
  ReminderStore：
  1. create 校验：空文本/间隔 <10s/超过 7 天/repeat 非法/每 scope 上限 → 显式 ReminderError
  2. list/get/pending_count；跨 scope 隔离（只能看/改本会话的）
  3. modify/cancel 状态机：非 pending 拒绝；改时间重新参与领取
  4. claim_due 原子领取：未到点不领；到点领一次（重复 tick 不重复触发）；
     一次性 → done；daily → 推进到未来同一时刻（漏多个周期不补跑）；
     超过 5 分钟 → expired 不补发
  5. mark_result：ok → done + fired_at；失败 → failed + last_error 留痕
  6. 持久化：tmp+replace 原子写；new store 读同文件恢复（重启不丢）
  ReminderParser：
  7. _validate（REDUN-03）：action 枚举/fire_at 范围/必填项
  8. parse：LLM 返回 JSON → 计划；坏 JSON → 显式错误（禁正则兜底）；
     markdown 围栏宽容剥壳；LLM 失败显式上抛
  ReminderScheduler：
  9. tick 到点经 on_fire 回调发送；发送失败记 failed 不重试；stop 幂等

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_reminders.py -v
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# 与 test_config_hot_reload.py 同款命名空间：插 src/plugins 让 `qq_bridge.*`
# 可导入。**不用 src.plugins.qq_bridge.* 限定名**——那会加载第二份 Config 类，
# 使 config_api._find_config_class 命中错的那份，热生效用例假失败
# （既有缺陷全量跑必红，见 AGENTS.md F12；本文件保持单份命名空间不加剧）。
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.tools.scheduler import (  # noqa: E402
    MAX_DELAY, MIN_DELAY, ReminderError, ReminderParser, ReminderScheduler,
    ReminderStore)


@pytest.fixture()
def store(tmp_path):
    return ReminderStore(tmp_path / "reminders.json", expire=300.0,
                         max_per_scope=3)


def _mk(store, scope="g:1", delay=60, text="喝水", repeat="none", **kw):
    now = time.time()
    return store.create(text=text, fire_at=now + delay, repeat=repeat,
                        scope=scope, user_id=42, group_id=1, **kw)


# ---------------- 1. create 校验 ----------------
def test_create_ok(store):
    r = _mk(store)
    assert r["status"] == "pending" and r["text"] == "喝水"
    assert r["fire_at"] > time.time()
    assert len(r["id"]) == 8


def test_create_rejects_bad_input(store):
    now = time.time()
    with pytest.raises(ReminderError):
        store.create(text="  ", fire_at=now + 60, repeat="none", scope="g:1")
    with pytest.raises(ReminderError):  # 间隔 <10s
        store.create(text="x", fire_at=now + 5, repeat="none", scope="g:1")
    with pytest.raises(ReminderError):  # 超过 7 天
        store.create(text="x", fire_at=now + MAX_DELAY + 60, repeat="none",
                     scope="g:1")
    with pytest.raises(ReminderError):  # repeat 非法
        store.create(text="x", fire_at=now + 60, repeat="weekly", scope="g:1")
    with pytest.raises(ReminderError):  # 时间非数字
        store.create(text="x", fire_at="明天", repeat="none", scope="g:1")


def test_create_scope_cap(store):
    for i in range(3):
        _mk(store, text="t%d" % i)
    with pytest.raises(ReminderError) as ei:
        _mk(store, text="t4")
    assert "满 3 条" in str(ei.value)
    # 别的 scope 不受影响（隔离）
    _mk(store, scope="g:2", text="other")


def test_create_long_text_truncated(store):
    r = _mk(store, text="长" * 500)
    assert len(r["text"]) == 200


# ---------------- 2/3. 查询与状态机 ----------------
def test_list_and_scope_isolation(store):
    _mk(store, scope="g:1")
    _mk(store, scope="g:2")
    assert len(store.list("g:1")) == 1
    assert len(store.list("g:2")) == 1
    assert len(store.list("g:3")) == 0


def test_modify_and_cancel(store):
    r = _mk(store)
    now = time.time()
    # 改时间 + 改内容
    m = store.modify(r["id"], "g:1", text="喝水plus",
                     fire_at=now + 120, repeat="daily")
    assert m["text"] == "喝水plus" and m["repeat"] == "daily"
    # 非 pending 拒绝（先取消再改）
    c = store.cancel(r["id"], "g:1")
    assert c["status"] == "cancelled"
    with pytest.raises(ReminderError):
        store.modify(r["id"], "g:1", fire_at=now + 120)
    with pytest.raises(ReminderError):
        store.cancel(r["id"], "g:1")


def test_cross_scope_rejected(store):
    r = _mk(store, scope="g:1")
    with pytest.raises(ReminderError):  # 别的会话改不了
        store.cancel(r["id"], "g:2")
    with pytest.raises(ReminderError):
        store.modify(r["id"], "g:2", fire_at=time.time() + 120)


def test_modify_revalidates_time(store):
    r = _mk(store)
    with pytest.raises(ReminderError):
        store.modify(r["id"], "g:1", fire_at=time.time() + 5)  # <10s


# ---------------- 4. claim_due 原子领取 ----------------
def test_claim_due_lifecycle(store):
    now = time.time()
    r = _mk(store, delay=30)
    # 未到点不领
    assert store.claim_due(now + 10) == []
    # 到点领一次
    due = store.claim_due(now + 31)
    assert [d["id"] for d in due] == [r["id"]]
    # 重复 tick 不重复触发（一次性已转 done）
    assert store.claim_due(now + 32) == []
    # 发送结果回填 → done
    store.mark_result(r["id"], True)
    assert store.get(r["id"])["status"] == "done"


def test_claim_due_once_becomes_done_via_mark(store):
    now = time.time()
    r = _mk(store, delay=30)
    due = store.claim_due(now + 31)
    assert len(due) == 1
    assert due[0]["status"] == "claimed"  # 一次性：等发送结果落地才 done
    assert store.claim_due(now + 31.5) == []  # 已 claimed，不再触发


def test_claim_due_daily_advances(store):
    now = time.time()
    r = _mk(store, delay=30, repeat="daily")
    due = store.claim_due(now + 31)
    assert len(due) == 1
    item = store.get(r["id"])
    assert item["status"] == "pending"  # daily 保持 pending
    assert item["fire_at"] > now + 31   # 已推进到明天
    # 推进后未到点 → 不再触发
    assert store.claim_due(now + 32) == []


def test_claim_due_expired_no_backfill(store):
    now = time.time()
    r = _mk(store, delay=30)
    # 迟到 6 分钟（超过 expire=300s）→ 过期不补发
    due = store.claim_due(now + 30 + 360)
    assert due == []
    assert store.get(r["id"])["status"] == "expired"


def test_claim_due_within_expire_fires(store):
    now = time.time()
    r = _mk(store, delay=30)
    # 迟到 4 分钟（< 300s）→ 照常触发（进程重启场景）
    due = store.claim_due(now + 30 + 240)
    assert [d["id"] for d in due] == [r["id"]]


# ---------------- 5. mark_result ----------------
def test_mark_result_failed_keeps_trace(store):
    r = _mk(store)
    store.claim_due(time.time() + 61)
    store.mark_result(r["id"], False, "发送失败：timeout")
    item = store.get(r["id"])
    assert item["status"] == "failed"
    assert item["last_error"] == "发送失败：timeout"


# ---------------- 6. 持久化 ----------------
def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "reminders.json"
    s1 = ReminderStore(p)
    _mk(s1, scope="g:1", text="重启后还在")
    s2 = ReminderStore(p)  # 模拟重启
    items = s2.list("g:1")
    assert len(items) == 1 and items[0]["text"] == "重启后还在"
    # 原子写：不残留 .tmp
    assert not (tmp_path / "reminders.json.tmp").exists()


def test_legacy_file_gets_defaults(tmp_path):
    p = tmp_path / "reminders.json"
    p.write_text(json.dumps([{"id": "old1", "scope": "g:1", "text": "老提醒",
                              "fire_at": time.time() + 60}]))
    s = ReminderStore(p)
    r = s.get("old1")
    assert r["status"] == "pending" and r["repeat"] == "none"
    assert r["channel"] == "napcat"  # 老文件补 channel 字段


# ---------------- 7/8. ReminderParser ----------------
class _FakeLLM:
    def __init__(self, reply, raises=None):
        self.reply = reply
        self.raises = raises
        self.seen = None

    async def chat(self, messages, tools=None, temperature=None):
        self.seen = messages
        if self.raises:
            raise self.raises
        return {"role": "assistant", "content": self.reply, "tool_calls": []}


def _valid_plan(**over):
    now = time.time()
    plan = {"action": "create", "text": "喝水", "repeat": "none",
            "id": "", "fire_at": now + 600}
    plan.update(over)
    return plan


@pytest.mark.anyio
async def test_parser_validate_rules():
    p = ReminderParser.__new__(ReminderParser)
    now = time.time()
    with pytest.raises(ReminderError):
        p._validate({"action": "sing"}, now)
    with pytest.raises(ReminderError):
        p._validate({"action": "create", "text": "x"}, now)  # 缺 fire_at
    with pytest.raises(ReminderError):
        p._validate({"action": "create", "text": "x", "fire_at": now + 5}, now)
    with pytest.raises(ReminderError):
        p._validate({"action": "create", "text": "x",
                     "fire_at": now + MAX_DELAY + 5}, now)
    with pytest.raises(ReminderError):
        p._validate({"action": "create", "fire_at": now + 600}, now)  # 缺 text
    ok = p._validate(_valid_plan(repeat="weekly"), now)
    assert ok["repeat"] == "none"  # 非法 repeat 回落 none（宽容但不静默）


@pytest.mark.anyio
async def test_parser_parse_ok():
    plan = _valid_plan(text="开会")
    parser = ReminderParser(_FakeLLM(json.dumps(plan, ensure_ascii=False)))
    out = await parser.parse("下午三点提醒我开会")
    assert out["action"] == "create" and out["text"] == "开会"
    # system 提示注入了当前时间（模型靠它算绝对时间戳）
    assert "Unix 秒" in parser.client.seen[0]["content"]


@pytest.mark.anyio
async def test_parser_parse_strips_codefence():
    plan = _valid_plan()
    raw = "```json\n%s\n```" % json.dumps(plan, ensure_ascii=False)
    parser = ReminderParser(_FakeLLM(raw))
    out = await parser.parse("半小时后提醒我")
    assert out["action"] == "create"


@pytest.mark.anyio
async def test_parser_parse_bad_json_errors():
    parser = ReminderParser(_FakeLLM("我觉得你是想设个提醒吧"))
    with pytest.raises(ReminderError) as ei:
        await parser.parse("半小时后提醒我")
    assert "换个说法" in str(ei.value)  # 禁正则兜底：显式错误


@pytest.mark.anyio
async def test_parser_parse_llm_failure_explicit():
    from qq_bridge.tools.llm import LLMError
    parser = ReminderParser(_FakeLLM(None, raises=LLMError("no_key")))
    with pytest.raises(ReminderError) as ei:
        await parser.parse("半小时后提醒我")
    assert "no_key" in str(ei.value)


@pytest.mark.anyio
async def test_parser_empty_input():
    parser = ReminderParser(_FakeLLM("{}"))
    with pytest.raises(ReminderError):
        await parser.parse("   ")


# ---------------- 9. ReminderScheduler ----------------
@pytest.mark.anyio
async def test_scheduler_fires_via_callback(tmp_path):
    st = ReminderStore(tmp_path / "reminders.json")
    fired = []

    async def on_fire(item):
        fired.append(item["id"])

    sched = ReminderScheduler(st, on_fire=on_fire, tick=0.05)
    r = _mk(st, delay=30)
    r["fire_at"] = time.time() - 1  # 伪造「已到点 1s」（未过期）
    st._save()
    await sched._tick()
    assert fired == [r["id"]]
    assert st.get(r["id"])["status"] == "done"


@pytest.mark.anyio
async def test_scheduler_fire_failure_marks_failed(tmp_path):
    st = ReminderStore(tmp_path / "reminders.json")

    async def on_fire(item):
        raise ConnectionError("ws 断了")

    sched = ReminderScheduler(st, on_fire=on_fire, tick=0.05)
    r = _mk(st, delay=30)
    r["fire_at"] = time.time() - 1
    st._save()
    await sched._tick()
    item = st.get(r["id"])
    assert item["status"] == "failed" and "ws 断了" in item["last_error"]


@pytest.mark.anyio
async def test_scheduler_start_stop_idempotent(tmp_path):
    st = ReminderStore(tmp_path / "reminders.json")
    sched = ReminderScheduler(st, tick=0.05)
    await sched.start()
    assert sched.running
    await sched.start()  # 幂等
    assert sched.running
    await sched.stop()
    assert not sched.running
    await sched.stop()  # 幂等
    assert not sched.running
