# -*- coding: utf-8 -*-
"""test_plan_commands.py —— 生图计划（草稿）查看口令验收（2026-09-28）

覆盖 game_tools.dispatch 新增的「#计划」口令：
  · 列表（#计划）：全部草稿计划 + 定时 + 最近执行归类
  · 明细（#计划 <序号|ID>）：定时 + 执行统计 + 条目状态
  · 边界：序号越界 / 非法参数 / 未下发计划 / 空列表

机器判定：pytest assert + 非零退出码（monkeypatch 两个单例，零真实文件 I/O）。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_plan_commands.py -v
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import src.plugins.qq_bridge.game_tools as gt  # noqa: E402
from src.plugins.qq_bridge.game_tools import dispatch, _handle  # noqa: E402


# ---------------- 内存假数据（替代真实 planstore / queue 单例）----------------
_TS = 1789834514.0  # 2026-09-25 某时刻


class FakePlanMgr:
    def __init__(self, plans):
        self._plans = plans
        self.marked = None  # (plan_id, batch_id) 捕获 mark_run

    def list(self):
        return sorted(self._plans, key=lambda p: float(p.get("created_at") or 0), reverse=True)

    def get(self, plan_id):
        return next((p for p in self._plans if p.get("plan_id") == plan_id), None)

    def mark_run(self, plan_id, batch_id):
        self.marked = (plan_id, batch_id)


class FakeQueueMgr:
    def __init__(self, summaries, details):
        self._summaries = summaries
        self._details = details  # batch_id -> {"items": [...]}
        self.enqueued = []  # 捕获 enqueue_batch 调用

    def batches_summary(self):
        return self._summaries

    def batch_detail(self, batch_id):
        d = self._details.get(batch_id)
        if not d:
            return {"ok": False, "error": "计划不存在"}
        return {"ok": True, "batch_id": batch_id, "total": len(d["items"]), "items": d["items"]}

    async def enqueue_batch(self, items, channel, user_id, group_id=0, scene="web",
                            batch_id=None, name="", plan_id="", scheduled_at=0):
        self.enqueued.append({
            "items": items, "channel": channel, "user_id": user_id, "group_id": group_id,
            "scene": scene, "name": name, "plan_id": plan_id,
        })
        return {"ok": True, "batch_id": "batch_X", "total": len(items),
                "accepted": len(items), "rejected": 0, "errors": [], "jobs": []}


PLAN_A = {
    "plan_id": "a1b2c3d4e5f6", "name": "生图计划_已下发", "channel": "local",
    "json_text": '[{"prompt": "画一只猫"}, {"prompt": "画一只狗"}]',
    "created_at": _TS, "schedule_enabled": True, "schedule_at": _TS + 86400,
    "schedule_repeat": "daily", "schedule_last_fired_at": _TS,
}
PLAN_B = {
    "plan_id": "b2c3d4e5f6a1", "name": "草稿_未下发", "channel": "online",
    "json_text": '[{"prompt": "只画一张图"}]',
    "created_at": _TS + 10, "schedule_enabled": False, "schedule_at": 0,
    "schedule_repeat": "none", "schedule_last_fired_at": 0,
}


def _install(monkeypatch):
    summaries = [
        {"plan_id": "a1b2c3d4e5f6", "batch_id": "batch_A", "total": 2, "done": 1,
         "failed": 0, "running": 0, "queued": 1, "canceled": 0, "progress": 50.0,
         "bucket": "partial", "started_at": _TS, "created_at": _TS, "finished_at": 0,
         "paused": False},
    ]
    details = {
        "batch_A": {"items": [
            {"prompt": "画一只猫", "state": "done"},
            {"prompt": "画一只狗", "state": "queued"},
        ]},
    }
    monkeypatch.setattr(gt.planstore, "plan_mgr", FakePlanMgr([PLAN_A, PLAN_B]))
    monkeypatch.setattr(gt.queue, "queue_mgr", FakeQueueMgr(summaries, details))
    # 隔离 AI 模式门禁（2026-10-19 加入 _handle）：本文件用例写于门禁之前，假事件
    # 用的真实群号可能在 ai_mode.json 里处于 on → _handle 提前 return 导致 0 调用。
    # 强制关断做纯净验证（门禁本身由 test_ai_mode 相关用例覆盖）。
    monkeypatch.setattr(gt.ai_mode_store, "is_on", lambda scope: False)


def _run(coro):
    return asyncio.run(coro)


def test_plan_list_shows_all_with_status(monkeypatch):
    _install(monkeypatch)
    txt = _run(dispatch("计划", "", "sk"))
    assert "共 2 个" in txt, txt
    assert "生图计划_已下发" in txt and "草稿_未下发" in txt
    assert "条目2" in txt and "条目1" in txt  # 条数统计
    assert "定时:每天" in txt               # A 已启用 daily
    assert "定时:未设" in txt               # B 未启用
    assert "执行 1/2完成(已执行未完成)" in txt   # A 归类
    assert "尚未下发" in txt               # B 未下发


def test_plan_detail_by_index(monkeypatch):
    _install(monkeypatch)
    # 列表按创建时间倒序（最新在前）：序号 1 = PLAN_B（草稿_未下发），序号 2 = PLAN_A（生图计划_已下发）
    txt1 = _run(dispatch("计划", "1", "sk"))
    assert "计划明细：草稿_未下发" in txt1, txt1
    txt = _run(dispatch("计划", "2", "sk"))
    assert "计划明细：生图计划_已下发" in txt, txt
    assert "条目数：2" in txt
    assert "定时：已启用（每天）" in txt
    assert "执行统计：总 2 / 完成 1 / 失败 0 / 运行 0 / 排队 1 / 取消 0" in txt
    assert "进度：50% · 归类：已执行未完成" in txt
    assert "前 2 条明细" in txt
    assert "✅完成" in txt and "⏳排队" in txt


def test_plan_detail_by_id(monkeypatch):
    _install(monkeypatch)
    txt = _run(dispatch("计划", "a1b2c3d4e5f6", "sk"))
    assert "计划明细：生图计划_已下发" in txt, txt
    assert "· ID：a1b2c3d4e5f6" in txt


def test_plan_detail_not_dispatched(monkeypatch):
    _install(monkeypatch)
    txt = _run(dispatch("计划", "b2c3d4e5f6a1", "sk"))
    assert "计划明细：草稿_未下发" in txt, txt
    assert "执行：尚未下发" in txt
    assert "定时：未启用" in txt


def test_plan_detail_index_out_of_range(monkeypatch):
    _install(monkeypatch)
    txt = _run(dispatch("计划", "99", "sk"))
    assert "序号超出范围" in txt, txt
    assert "共 2 个" in txt


def test_plan_detail_bad_arg_usage(monkeypatch):
    _install(monkeypatch)
    txt = _run(dispatch("计划", "不是序号也不是id", "sk"))
    assert "用法：#计划" in txt, txt


def test_plan_list_empty(monkeypatch):
    monkeypatch.setattr(gt.planstore, "plan_mgr", FakePlanMgr([]))
    monkeypatch.setattr(gt.queue, "queue_mgr", FakeQueueMgr([], {}))
    txt = _run(dispatch("计划", "", "sk"))
    assert "还没有任何草稿计划" in txt, txt


def test_help_mentions_plan_command(monkeypatch):
    _install(monkeypatch)
    help_txt = gt.HELP
    assert "#计划" in help_txt
    assert "生图计划（草稿）" in help_txt


# ---------------- 路由层测试：裸词「计划」免前缀也能命中（修复 @机器人 计划 落空）----------------
class _Seg:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class _FakeEvent:
    # 群号/用户号必须是**不可能出现在 GROUP_WHITELIST 里**的假值。
    # 这里原本写的是真群 1075851417 + 真实用户 3287416492，导致 pytest 跑一遍
    # 就把这批伪造消息写进生产 logs/qq_bridge.log，和真群消息无法区分
    # （2026-11-05 排查时误判成群聊总闸失效）。
    def __init__(self, text):
        self.original_message = [_Seg("text", {"text": text})]
        self.message_type = "group"
        self.group_id = 900000001
        self.user_id = 900000002


class _FakeBot:
    def __init__(self):
        self.sent = []
        self.self_id = "3949742250"

    async def send(self, event, message):
        self.sent.append(message)


def _run_handle(text, monkeypatch):
    """驱动真实 _handle；捕获卡片渲染入口 _send_guide（不真跑 Pillow/HTTP）。"""
    _install(monkeypatch)
    # 隔离 warframe 资料卡查询，避免测试依赖战甲数据表
    monkeypatch.setattr(gt.warframe, "_resolve_card", lambda *a, **k: False)
    calls = []  # (title, body, avatar_url)

    async def _fake_send_guide(bot, event, title, text, avatar_url=""):
        calls.append((title, text, avatar_url))

    monkeypatch.setattr(gt, "_send_guide", _fake_send_guide)
    bot = _FakeBot()
    event = _FakeEvent(text)
    asyncio.run(_handle(bot, event))
    return bot, calls


def test_handle_bare_plan_lists(monkeypatch):
    # 裸词「计划」（无 # 前缀）应命中列表并走卡片渲染，不再发纯文字
    bot, calls = _run_handle("计划", monkeypatch)
    assert bot.sent == [], bot.sent
    assert len(calls) == 1, calls
    title, body, _av = calls[0]
    assert title == "生图计划", title
    assert "共 2 个" in body, body


def test_handle_bare_plan_detail_by_id(monkeypatch):
    # 裸词「计划 <ID>」应命中明细卡片
    bot, calls = _run_handle("计划 a1b2c3d4e5f6", monkeypatch)
    assert bot.sent == [], bot.sent
    assert len(calls) == 1, calls
    title, body, _av = calls[0]
    assert title == "生图计划", title
    assert "计划明细" in body, body
    assert "a1b2c3d4e5f6" in body, body


def test_handle_bare_plan_no_false_trigger(monkeypatch):
    # 首词非精确匹配（"计划去玩"）不应误触发命令（不发文字也不发卡片）
    bot, calls = _run_handle("计划去玩", monkeypatch)
    assert bot.sent == [], bot.sent
    assert calls == [], calls


def test_handle_prefix_plan_still_works(monkeypatch):
    # 原有 # 前缀形态不受影响，同样走卡片
    bot, calls = _run_handle("#计划", monkeypatch)
    assert bot.sent == [], bot.sent
    assert len(calls) == 1, calls
    assert calls[0][0] == "生图计划", calls[0]
    assert "共 2 个" in calls[0][1], calls[0][1]


def test_handle_bare_plan_glued_index(monkeypatch):
    # 粘连写法「计划1」（命令词与序号无空格）应命中明细卡片，而非静默无响应
    bot, calls = _run_handle("计划1", monkeypatch)
    assert bot.sent == [], bot.sent
    assert len(calls) == 1, calls
    title, body, _av = calls[0]
    assert title == "生图计划", title
    assert "计划明细" in body, body          # 走明细而非列表
    assert "草稿_未下发" in body, body        # 序号 1 = 最新（PLAN_B）


def test_handle_prefix_plan_glued_index(monkeypatch):
    # 前缀粘连写法「#计划1」同样应命中明细卡片
    bot, calls = _run_handle("#计划1", monkeypatch)
    assert bot.sent == [], bot.sent
    assert len(calls) == 1, calls
    assert calls[0][0] == "生图计划", calls[0]
    assert "计划明细" in calls[0][1], calls[0][1]
    assert "草稿_未下发" in calls[0][1], calls[0][1]


def test_handle_bare_plan_glued_id(monkeypatch):
    # 粘连写法「计划<16位ID>」应命中对应明细卡片
    bot, calls = _run_handle("计划a1b2c3d4e5f6", monkeypatch)
    assert bot.sent == [], bot.sent
    assert len(calls) == 1, calls
    assert calls[0][0] == "生图计划", calls[0]
    assert "计划明细：生图计划_已下发" in calls[0][1], calls[0][1]
    assert "a1b2c3d4e5f6" in calls[0][1], calls[0][1]


def test_handle_bare_plan_glued_no_false_trigger(monkeypatch):
    # 「计划去玩」粘连部分非序号/ID，不应拆出误触发（仍静默、无卡片）
    bot, calls = _run_handle("计划去玩", monkeypatch)
    assert bot.sent == [], bot.sent
    assert calls == [], calls


# ---------------- 计划明细详情下钻（#明细 看全部条目）----------------
def test_dispatch_detail_full_shows_all_items(monkeypatch):
    _install(monkeypatch)
    # 序号 2 = PLAN_A（2 条条目，已有 batch_A）→ 明细应展示全部 2 条（非截断 8 条）
    txt = _run(dispatch("明细", "2", "sk"))
    assert "全部 2 条" in txt, txt
    assert "画一只猫" in txt and "画一只狗" in txt, txt
    assert "尚未下发" not in txt, txt


def test_dispatch_detail_full_no_batch(monkeypatch):
    _install(monkeypatch)
    # 序号 1 = PLAN_B（草稿_未下发，无批次）→ 明细提示尚未下发
    txt = _run(dispatch("明细", "1", "sk"))
    assert "尚未下发" in txt, txt


def test_dispatch_detail_full_bad_arg(monkeypatch):
    _install(monkeypatch)
    txt = _run(dispatch("明细", "不是序号也不是id", "sk"))
    assert "用法：#明细" in txt, txt


# ---------------- 计划单独下发（#下发计划 <序号|ID>，真实入队）----------------
def test_handle_dispatch_plan_by_index(monkeypatch):
    _install(monkeypatch)
    bot, calls = _run_handle("下发计划 2", monkeypatch)
    assert bot.sent == [], bot.sent
    assert len(calls) == 1, calls
    title, body, _av = calls[0]
    assert title == "生图计划·下发", title
    assert "已下发计划" in body and "已入队" in body, body
    qm = gt.queue.queue_mgr
    assert len(qm.enqueued) == 1, qm.enqueued
    kw = qm.enqueued[0]
    assert kw["plan_id"] == "a1b2c3d4e5f6", kw  # PLAN_A
    assert kw["channel"] == "local", kw
    assert kw["name"] == "生图计划_已下发", kw
    assert len(kw["items"]) == 2, kw  # 2 条条目全部入队
    assert gt.planstore.plan_mgr.marked == ("a1b2c3d4e5f6", "batch_X"), gt.planstore.plan_mgr.marked


def test_handle_dispatch_plan_by_id(monkeypatch):
    _install(monkeypatch)
    bot, calls = _run_handle("下发计划 a1b2c3d4e5f6", monkeypatch)
    assert bot.sent == [], bot.sent
    assert calls[0][0] == "生图计划·下发", calls[0]
    assert len(gt.queue.queue_mgr.enqueued) == 1, gt.queue.queue_mgr.enqueued
    assert gt.queue.queue_mgr.enqueued[0]["plan_id"] == "a1b2c3d4e5f6"


def test_handle_dispatch_plan_glued_index(monkeypatch):
    _install(monkeypatch)
    bot, calls = _run_handle("下发计划2", monkeypatch)
    assert bot.sent == [], bot.sent
    assert calls[0][0] == "生图计划·下发", calls[0]
    assert len(gt.queue.queue_mgr.enqueued) == 1, gt.queue.queue_mgr.enqueued
    assert gt.queue.queue_mgr.enqueued[0]["plan_id"] == "a1b2c3d4e5f6"


def test_handle_dispatch_plan_empty_items(monkeypatch):
    _install(monkeypatch)
    # 把 PLAN_B 的 json_text 置空，验证「无条目」报错分支（仍走卡片，不静默）
    for p in gt.planstore.plan_mgr._plans:
        if p["plan_id"] == "b2c3d4e5f6a1":
            p["json_text"] = "[]"
    bot, calls = _run_handle("下发计划 b2c3d4e5f6a1", monkeypatch)
    assert bot.sent == [], bot.sent
    assert "没有任何条目" in calls[0][1], calls[0][1]
    assert len(gt.queue.queue_mgr.enqueued) == 0, gt.queue.queue_mgr.enqueued


def test_handle_dispatch_plan_bad_arg(monkeypatch):
    _install(monkeypatch)
    bot, calls = _run_handle("下发计划 不是序号", monkeypatch)
    assert bot.sent == [], bot.sent
    assert "用法：下发计划" in calls[0][1], calls[0][1]
    assert len(gt.queue.queue_mgr.enqueued) == 0, gt.queue.queue_mgr.enqueued


def test_handle_dispatch_plan_no_false_trigger(monkeypatch):
    # 「下发去玩」粘连部分非序号/ID，不应拆出误触发（仍静默、不真入队）
    _install(monkeypatch)
    bot, calls = _run_handle("下发去玩", monkeypatch)
    assert bot.sent == [], bot.sent
    assert calls == [], calls
    assert len(gt.queue.queue_mgr.enqueued) == 0, gt.queue.queue_mgr.enqueued
