# -*- coding: utf-8 -*-
"""2026-09-07：/status 真实进度汇报验收（修复 task_id 全代码 0 写入 bug）。

背景：用户反馈"正在进行生图任务时问进度，机器人说没事干"。根因：
  - Session.task_id 字段存在但全代码 0 处写入 → /status 永远回"闲着呢"
  - 即便发起人写入了，旁观者也查不到别人会话的 task_id

修复：TaskMgr 加全局 _current_tasks 簿 + sess.task_id 双写 + get_active_for / get_any_active
查询接口；_handle_reply __STATUS__ 改为先查自己 → 再查全局任务簿 → 闲。

覆盖：
  1) 簿 add/pop 完整路径：成功后 _current_tasks 空、sess.task_id 归 None
  2) 失败路径：try/finally 仍清簿（防假阳性"在跑"）
  3) get_active_for：按 user_id 匹配、同用户多任务返回最早开始、返回 dict 拷贝
  4) get_any_active：默认按 started_at desc 返回最新、exclude_user_id 排除自己、
     簿只有自己时排除后 None、空簿返 None

不直接测 _handle_reply 文案（依赖 _say → connector.call_action → OneBot 真连，难 mock）；
通过 task_mgr 查询接口 + _current_tasks 簿状态机间接覆盖 _handle_reply 的输入。
"""
import httpx
import pytest

from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.dispatcher import QQEvent
from src.plugins.qq_bridge.intent import Route
from src.plugins.qq_bridge.session import Session
from src.plugins.qq_bridge.taskmgr import TaskMgr

from conftest import run

API = "http://manju.test/api/v1"


def _mk_ev(user_id=10001, trace="trace-status"):
    return QQEvent(
        user_id=user_id, group_id=20001, message_type="group",
        raw_text="cup", trace_id=trace,
    )


def _mk_sess(user_id=10001):
    return Session(user_id=user_id)


def _entry(task_id, user_id, started_at, kind="t2i", prompt="p", trace="tr", group=0, channel="local"):
    """构造一个 _current_tasks 簿条目（与 _run_t2i / _run_i2i 写入的字段一致）。"""
    return {
        "task_id": task_id, "user_id": user_id, "group_id": group,
        "kind": kind, "prompt": prompt, "trace_id": trace,
        "channel": channel, "started_at": started_at,
    }


def _handler_submit_ok_then_succeed(task_id="loc_x"):
    """首次 /qq/image-generate 返 task_id；轮询返 succeeded。"""
    def h(request):
        p = request.url.path
        if p.endswith("/image-generate") or p.endswith("/t2i/generate") or p.endswith("/t2i/edit"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": task_id}})
        if "/t2i/tasks/" in p:
            return httpx.Response(200, json={"code": 0, "data": {
                "task": {"status": "succeeded", "progress": 100,
                         "result": {"images": [{"url": "/file?name=ok.png"}]}}}})
        if p.endswith("/t2i/active-task"):
            return httpx.Response(200, json={"code": 0, "data": {"task": None}})
        if p.startswith("/api/v1/img/"):
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nFAKE")
        return httpx.Response(404, json={"detail": "Not Found"})
    return h


def _handler_submit_ok_then_fail(task_id="loc_fail"):
    """提交成功但轮询返 failed（验 finally 仍清簿）。"""
    def h(request):
        p = request.url.path
        if p.endswith("/image-generate") or p.endswith("/t2i/generate") or p.endswith("/t2i/edit"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": task_id}})
        if "/t2i/tasks/" in p:
            return httpx.Response(200, json={"code": 0, "data": {
                "task": {"status": "failed", "error_message": "ComfyUI OOM"}}})
        if p.endswith("/t2i/active-task"):
            return httpx.Response(200, json={"code": 0, "data": {"task": None}})
        return httpx.Response(404, json={"detail": "Not Found"})
    return h


def _local_mgr(handler):
    return TaskMgr(api_base=API, channel="local", transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _env():
    Config.MANJU_API_BASE = API
    Config.POLL_INTERVAL = 0.01
    Config.LOCAL_QUEUE_INTERVAL = 0.01
    Config.LOCAL_QUEUE_TIMEOUT = 1.0
    Config.LOCAL_TEMPLATE_ID = ""  # 走无模板安全兜底
    yield


# ================ 簿生命周期（add/pop 完整路径）================

def test_local_t2i_success_clears_book_and_session():
    """成功完成后：_current_tasks 空 + sess.task_id 归 None（旁观者再问应回"闲着呢"）。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    r = Route(kind="t2i", api="/t2i/generate", payload={"prompt": "猫"})
    sess = _mk_sess()

    out = run(mgr.run(r, _mk_ev(), sess))

    assert out["ok"] is True, out.get("error")
    assert mgr._current_tasks == {}, "完成后簿应清空：%r" % (mgr._current_tasks,)
    assert sess.task_id is None, "完成后 sess.task_id 应归 None：%r" % (sess.task_id,)


def test_local_t2i_failure_still_clears_book_and_session():
    """失败时 try/finally 仍清簿（防假阳性"在跑"——之前 bug 是失败残留 task_id）。"""
    mgr = _local_mgr(_handler_submit_ok_then_fail())
    r = Route(kind="t2i", api="/t2i/generate", payload={"prompt": "猫"})
    sess = _mk_sess()

    out = run(mgr.run(r, _mk_ev(), sess))

    assert out["ok"] is False
    assert "ComfyUI OOM" in out["error"]
    assert mgr._current_tasks == {}
    assert sess.task_id is None


def test_local_i2i_success_clears_book_and_session():
    """图生图同样双写双清（与 t2i 共用同一簿）。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed(task_id="loc_i2i"))
    r = Route(kind="i2i", api="/t2i/edit",
              payload={"prompt": "改成水彩", "mode": "img2img"}, consume_image=True)
    ev = _mk_ev()
    ev.images = [API + "/img/source.png"]
    sess = _mk_sess()
    sess.pending_image = API + "/img/source.png"

    out = run(mgr.run(r, ev, sess))

    assert out["ok"] is True, out.get("error")
    assert mgr._current_tasks == {}
    assert sess.task_id is None


# ================ get_active_for（按 user_id 查）================

def test_get_active_for_matches_by_user_id():
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    mgr._current_tasks["t1"] = _entry("t1", 10001, 100.0, prompt="a")
    mgr._current_tasks["t2"] = _entry("t2", 20002, 200.0, kind="i2i", prompt="b", group=30003)

    assert mgr.get_active_for(10001)["task_id"] == "t1"
    assert mgr.get_active_for(20002)["task_id"] == "t2"
    assert mgr.get_active_for(99999) is None


def test_get_active_for_returns_earliest_when_user_has_multiple():
    """同用户多任务：返回最早开始的（started_at 升序），保证结果稳定。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    mgr._current_tasks["a"] = _entry("a", 10001, 100.0, prompt="p1")
    mgr._current_tasks["b"] = _entry("b", 10001, 50.0, kind="i2i", prompt="p2")  # 更早

    assert mgr.get_active_for(10001)["task_id"] == "b"


def test_get_active_for_returns_copy_not_reference():
    """返回 dict 拷贝：外部改不影响簿（防误改源数据）。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    mgr._current_tasks["t1"] = _entry("t1", 10001, 1.0)

    info = mgr.get_active_for(10001)
    info["task_id"] = "MUTATED"
    info["prompt"] = "MUTATED"

    assert mgr._current_tasks["t1"]["task_id"] == "t1"
    assert mgr._current_tasks["t1"]["prompt"] == "p"


def test_get_active_for_empty_book_returns_none():
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    assert mgr.get_active_for(10001) is None


# ================ get_any_active（旁观者查任意）================

def test_get_any_active_returns_latest_when_no_exclude():
    """不排除时：按 started_at desc 返回最新开始的任务（"当前最热"）。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    mgr._current_tasks["old"] = _entry("old", 10001, 1.0, prompt="p")
    mgr._current_tasks["new"] = _entry("new", 20002, 2.0, kind="i2i", prompt="p")

    assert mgr.get_any_active()["task_id"] == "new"


def test_get_any_active_excludes_self_for_bystander():
    """旁观者问进度：排除自己后选下一个（避免发起人问"自己进度"被回成"别人的"）。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    mgr._current_tasks["a"] = _entry("a", 10001, 1.0, prompt="p")
    mgr._current_tasks["b"] = _entry("b", 20002, 2.0, prompt="p")

    # 10001 问：排除自己 → 拿 b
    assert mgr.get_any_active(exclude_user_id=10001)["task_id"] == "b"
    # 20002 问：排除自己 → 拿 a
    assert mgr.get_any_active(exclude_user_id=20002)["task_id"] == "a"


def test_get_any_active_empty_book_returns_none():
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    assert mgr.get_any_active() is None
    assert mgr.get_any_active(exclude_user_id=123) is None


def test_get_any_active_only_self_returns_none():
    """簿里只有自己的任务时，排除后返 None——旁观者应走 get_active_for 先查自己。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    mgr._current_tasks["a"] = _entry("a", 10001, 1.0, prompt="p")

    assert mgr.get_any_active(exclude_user_id=10001) is None
    # 不排除能看到自己
    assert mgr.get_any_active()["task_id"] == "a"


def test_get_any_active_returns_copy_not_reference():
    """返回 dict 拷贝：外部改不影响簿（与 get_active_for 一致）。"""
    mgr = _local_mgr(_handler_submit_ok_then_succeed())
    mgr._current_tasks["t1"] = _entry("t1", 10001, 1.0, prompt="orig")

    info = mgr.get_any_active()
    info["task_id"] = "MUTATED"

    assert mgr._current_tasks["t1"]["task_id"] == "t1"
