# -*- coding: utf-8 -*-
"""本地 ComfyUI 通道验收（设计文档 §7.1 + 主项目 /t2i/generate 契约）。

用 httpx.MockTransport 拦截请求，断言 QQ 侧提交给平台的本地生图参数
与主项目文生图页（useT2i.generate）语义一致。

最重要的一条契约（2026-09-04 核对主项目真实任务确认）：
  **配了模板时 steps / cfg / 尺寸不下发**，由后端取模板自带值
  （routers.py t2i_generate：请求里 steps<=0、cfg<=0 或未给尺寸即用模板值）。
  蒸馏类模板自带 steps=8、cfg=1.0，若硬套 SD1.5 的 20/7.0 会采样发散出废图。

覆盖：
  1) QQ 通道必须配模板：无模板时不再下发 SD1.5 兜底值，由后端直接报错（422 IMG_NO_TEMPLATE）
  2) 有模板时 steps/cfg/尺寸不下发，交由模板决定（防蒸馏模型发散）
  3) 有模板且显式指定了尺寸时才下发尺寸
  4) template_id 仅在配置了模板时下发
  5) 图生图表单字段齐全（/t2i/edit 无「模板优先」逻辑，steps/cfg 必须显式给）
  6) 提交前等平台本地任务空闲（避免与前端页面互相取消）
  7) 排队超时给出明确错误且不提交
"""
import json

import httpx
import pytest

from src.plugins.qq_bridge.config import Config
from src.plugins.qq_bridge.dispatcher import QQEvent
from src.plugins.qq_bridge.intent import Route
from src.plugins.qq_bridge.session import Session
from src.plugins.qq_bridge.taskmgr import TaskMgr

from conftest import run

API = "http://manju.test/api/v1"
P_ACTIVE = "/api/v1/t2i/active-task"
P_GEN = "/api/v1/t2i/generate"
P_EDIT = "/api/v1/t2i/edit"
P_QQ_GEN = "/api/v1/qq/image-generate"


def _mk_ev(user_id=10001):
    return QQEvent(
        user_id=user_id, group_id=0, message_type="private",
        raw_text="猫", trace_id="trace-local",
    )


def _mk_sess():
    s = Session(user_id=10001)
    s.pending_image = API + "/img/source.png"
    return s


def _collector(state, busy_times=0, qq_404=False):
    """构造 MockTransport handler：记录调用与提交体。

    busy_times: /t2i/active-task 前 N 次返回「有本地任务在跑」，之后返回空闲；
                传 999 表示永远忙（用于排队超时用例）。
    qq_404: /qq/image-generate 返回 404，模拟后端尚未加载新端点（回退路径）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        state.setdefault("calls", []).append(p)

        if p == P_QQ_GEN:
            if qq_404:
                return httpx.Response(404, json={"detail": "Not Found"})
            state["json"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "loc_1"}})

        if p == P_ACTIVE:
            state["active"] = state.get("active", 0) + 1
            if state["active"] <= busy_times:
                return httpx.Response(200, json={"code": 0, "data": {
                    "task": {"task_id": "other_1", "status": "running"}}})
            return httpx.Response(200, json={"code": 0, "data": {"task": None}})

        if p == P_GEN:
            state["json"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "loc_1"}})

        if p == P_EDIT:
            state["form"] = request.content.decode("utf-8", errors="replace")
            return httpx.Response(200, json={"code": 0, "data": {
                "task_id": "loc_2", "mode": "outpaint"}})

        if "/t2i/tasks/" in p:
            return httpx.Response(200, json={"code": 0, "data": {"task": {
                "status": "succeeded", "progress": 100,
                "result": {"images": [{"url": "/file?name=loc.png"}]}}}})

        if p.startswith("/api/v1/img/"):
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nFAKE")

        return httpx.Response(404, json={"detail": "Not Found"})

    return handler


def _local_mgr(busy_times=0, state=None, qq_404=False):
    state = state if state is not None else {}
    return TaskMgr(api_base=API, channel="local",
                   transport=httpx.MockTransport(_collector(state, busy_times, qq_404)))


def _run_t2i(mgr, state, prompt="一只赛博朋克猫"):
    r = Route(kind="t2i", api="/t2i/generate", payload={"prompt": prompt})
    return run(mgr.run(r, _mk_ev(), _mk_sess()))


@pytest.fixture(autouse=True)
def _env():
    Config.MANJU_API_BASE = API
    Config.POLL_INTERVAL = 0.01
    Config.LOCAL_QUEUE_INTERVAL = 0.01
    Config.LOCAL_TEMPLATE_ID = ""
    Config.LOCAL_STEPS = 0
    Config.LOCAL_CFG = 0.0
    Config.LOCAL_WIDTH = 0
    Config.LOCAL_HEIGHT = 0
    yield


# ---------------- 无模板：显式下发安全兜底值 ----------------

def test_without_template_sends_safe_fallback_params():
    """未配模板走后端内置流程：必须显式给值，否则后端默认 1024x576（6GB 显存有 OOM 风险）。"""
    state = {}
    mgr = _local_mgr(state=state)
    out = _run_t2i(mgr, state)

    assert out["ok"] is True, out.get("error")
    body = state["json"]
    for k in ("prompt", "negative", "steps", "cfg", "seed", "style",
              "enhance", "orchestrator", "count", "width", "height"):
        assert k in body, "本地提交体缺字段：%s" % k
    assert body["prompt"] == "一只赛博朋克猫"
    assert body["negative"] == Config.LOCAL_NEGATIVE_PROMPT
    assert body["steps"] == 20
    assert body["cfg"] == 7.0
    assert isinstance(body["seed"], int) and body["seed"] > 0


def test_without_template_default_size_is_512_to_avoid_oom():
    """OOM 回归保护：无模板时本地默认必须 512x512（720p 在 6GB 显存会 OOM）。"""
    state = {}
    mgr = _local_mgr(state=state)
    _run_t2i(mgr, state)

    assert state["json"]["width"] == 512
    assert state["json"]["height"] == 512


# ---------------- 有模板：参数跟随模板（核心契约）----------------

def test_template_params_are_not_hardcoded():
    """核心回归：有模板时不下发 steps/cfg/尺寸，交由后端取模板值。

    蒸馏类模板自带 steps=8、cfg=1.0；若这里硬发 20/7.0，后端会当成
    「用户显式指定」而覆盖模板值，直接采样发散出废图。
    """
    Config.LOCAL_TEMPLATE_ID = "tpl_0e334372"
    state = {}
    mgr = _local_mgr(state=state)
    out = _run_t2i(mgr, state)

    assert out["ok"] is True, out.get("error")
    body = state["json"]
    assert body["template_id"] == "tpl_0e334372"
    for k in ("steps", "cfg", "width", "height"):
        assert k not in body, "有模板时不应下发 %s（应跟随模板自带值）" % k
    # 其余字段照发
    assert body["prompt"] and body["negative"] and body["seed"] > 0


def test_template_explicit_size_is_sent_when_configured():
    """显式指定尺寸时才下发（例如想出竖屏 720x1280，或想用 512 提速）。"""
    Config.LOCAL_TEMPLATE_ID = "tpl_0e334372"
    Config.LOCAL_WIDTH = 720
    Config.LOCAL_HEIGHT = 1280
    state = {}
    mgr = _local_mgr(state=state)
    _run_t2i(mgr, state)

    body = state["json"]
    assert body["width"] == 720 and body["height"] == 1280
    assert "steps" not in body and "cfg" not in body   # 仍跟随模板


def test_template_id_sent_only_when_configured():
    """未配模板时不下发 template_id（走后端内置流程）。"""
    state = {}
    mgr = _local_mgr(state=state)
    _run_t2i(mgr, state)
    assert "template_id" not in state["json"]


# ---------------- 图生图 ----------------

def test_local_i2i_form_carries_edit_params():
    """/t2i/edit 没有「模板优先」逻辑，steps/cfg 必须显式下发（0 步会出废图）。"""
    state = {}
    mgr = _local_mgr(state=state)
    r = Route(kind="i2i", api="/t2i/edit",
              payload={"prompt": "改成水彩风", "mode": "outpaint", "left": 128, "top": 0},
              consume_image=True)
    ev = _mk_ev()
    ev.images = [API + "/img/source.png"]
    out = run(mgr.run(r, ev, _mk_sess()))

    assert out["ok"] is True, out.get("error")
    form = state["form"]
    for token in ('name="mode"', 'name="negative"', 'name="steps"',
                  'name="cfg"', 'name="seed"', 'name="strength"', 'name="left"'):
        assert token in form, "图生图表单缺字段：%s" % token
    assert "outpaint" in form
    assert "20" in form          # steps 兜底值确实发出去了，不是 0


# ---------------- §7.1 本地独占：排队 ----------------

def test_local_submit_waits_until_platform_idle():
    """平台有本地任务在跑（含前端页面提交的）时必须先等，否则互相取消。"""
    state = {}
    mgr = _local_mgr(busy_times=1, state=state)
    out = _run_t2i(mgr, state)

    assert out["ok"] is True, out.get("error")
    assert state["calls"].count(P_ACTIVE) == 2       # 第 1 次忙、第 2 次空闲
    assert state["calls"].index(P_ACTIVE) < state["calls"].index(P_QQ_GEN)


def test_local_queue_timeout_reports_and_does_not_submit():
    """排队超时：明确回传错误，且绝不提交（提交会把别人正在跑的图取消掉）。"""
    Config.LOCAL_QUEUE_TIMEOUT = 0
    state = {}
    mgr = _local_mgr(busy_times=999, state=state)
    out = _run_t2i(mgr, state)

    assert out["ok"] is False
    assert "正忙" in out["error"]
    assert P_GEN not in state["calls"], "排队超时后不应提交任务"


# ---------------- QQ 专用端点与回退 ----------------

def test_local_t2i_prefers_qq_endpoint():
    """后端有 /qq/image-generate 时优先用它（忙时 409 拒绝，不会取消别人的图）。"""
    state = {}
    mgr = _local_mgr(state=state)
    out = _run_t2i(mgr, state)

    assert out["ok"] is True, out.get("error")
    assert P_QQ_GEN in state["calls"]
    assert P_GEN not in state["calls"], "新端点可用时不应回退到页面端点"


def test_local_t2i_no_fallback_to_sd15():
    """QQ 端点不可用（404/连接失败）时不再回退 /t2i/generate，避免退化为 SD1.5；直接报错。"""
    state = {}
    mgr = _local_mgr(qq_404=True, state=state)
    out = _run_t2i(mgr, state)

    assert out["ok"] is False, "无回退时应失败而非伪造成功"
    assert out["error"], "应透传端点错误"
    assert P_GEN not in state["calls"], "绝不应回退到页面端点（否则会退化为 SD1.5 出废图）"
