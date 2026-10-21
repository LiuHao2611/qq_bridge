# -*- coding: utf-8 -*-
"""M8 智能家居中枢单元测试（仅测 smarthome 包；不依赖 nonebot/真 HA）。

覆盖：
  1) 白名单/场景映射解析（含坏条目跳过，不静默吞全配置）
  2) HAClient 白名单双保险（白名单外一律 HaError）
  3) match_scene 精确/包含匹配
  4) converse（MockTransport）：成功抽话术 / 空指令拒 / 无话术显式报错
  5) call_service 透传错误码（401/404/5xx → HaError 说清原因）
  6) webhook：parse_event / format_event_text / create_app
     （token 校验 403 / 正常 200 / 回调失败 500 / 未接线 503）
  7) camera：snapshot 成功 / HTTP 失败显式报错；clip 在无 ffmpeg 时
     显式报错（禁止降级回退静图）

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_smarthome.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.smarthome import (  # noqa: E402
    SYSTEM_PROMPT, CameraError, HAClient, HaError, create_app,
    format_event_text, parse_event, parse_scene_map, parse_whitelist,
)


def _client(handler, **kw) -> HAClient:
    """造一个打 MockTransport 的 HAClient（免真 HA）。"""
    c = HAClient(base_url="http://ha.test", token="tok",
                 whitelist={"light.living", "switch.fan", "script.bath"},
                 sensitive={"lock.front", "camera.living"},
                 scenes={"洗澡模式": "script.bath"}, **kw)
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ha.test")
    return c


# ---------------- 1) 解析 ----------------

def test_parse_whitelist_valid_and_bad():
    wl = parse_whitelist("light.a; switch.b，camera.c\nbad_entry;;light.d")
    assert wl == {"light.a", "switch.b", "camera.c", "light.d"}


def test_parse_scene_map_valid_and_bad():
    m = parse_scene_map("# 注释\n洗澡模式=script.bath\n睡眠模式=script.sleep\n"
                        "坏条目=notanentity\n空行\n")
    assert m == {"洗澡模式": "script.bath", "睡眠模式": "script.sleep"}


def test_system_prompt_contract():
    # 搬 HAQBot 的管家 prompt 规范四要素必须在
    assert "无条件" in SYSTEM_PROMPT and "如实告知" in SYSTEM_PROMPT
    assert "json" in SYSTEM_PROMPT.lower()


# ---------------- 2) 白名单双保险 ----------------

def test_call_service_rejects_non_whitelisted():
    c = _client(lambda r: httpx.Response(200, json=[]))
    with pytest.raises(HaError) as ei:
        import asyncio
        asyncio.run(c.call_service("light.secret_room"))
    assert "白名单" in str(ei.value)


def test_call_service_rejects_malformed_entity():
    c = _client(lambda r: httpx.Response(200, json=[]))
    with pytest.raises(HaError) as ei:
        import asyncio
        asyncio.run(c.call_service("不是entity"))
    assert "格式" in str(ei.value)


def test_is_sensitive():
    c = _client(lambda r: httpx.Response(200, json=[]))
    assert c.is_sensitive("lock.front") is True
    assert c.is_sensitive("light.living") is False


def test_match_scene_exact_and_contains():
    c = _client(lambda r: httpx.Response(200, json=[]))
    assert c.match_scene("洗澡模式") == "script.bath"
    assert c.match_scene("执行洗澡模式吧") == "script.bath"
    assert c.match_scene("随便说说") is None


# ---------------- 3) converse ----------------

def test_converse_extracts_speech():
    def handler(request):
        assert request.url.path == "/api/conversation/process"
        body = __import__("json").loads(request.content)
        assert body["text"] == "把客厅灯打开" and body["agent_id"]
        return httpx.Response(200, json={"response": {"speech": {
            "plain": {"speech": "好的主人，已经打开啦喵"}}}})

    import asyncio
    out = asyncio.run(_client(handler).converse("把客厅灯打开"))
    assert out["speech"] == "好的主人，已经打开啦喵"


def test_converse_empty_text_rejected():
    c = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(HaError):
        import asyncio
        asyncio.run(c.converse("  "))


def test_converse_no_speech_honest_error():
    c = _client(lambda r: httpx.Response(200, json={"response": {}}))
    with pytest.raises(HaError) as ei:
        import asyncio
        asyncio.run(c.converse("开灯"))
    assert "没回话" in str(ei.value)


# ---------------- 4) 错误码显式化 ----------------

@pytest.mark.parametrize("status,needle", [
    (401, "令牌"), (403, "令牌"), (404, "没有这个"), (500, "报错"),
])
def test_request_errors_are_explicit(status, needle):
    c = _client(lambda r: httpx.Response(status, text="boom"))
    with pytest.raises(HaError) as ei:
        import asyncio
        asyncio.run(c.get_state("light.living"))
    assert needle in str(ei.value)


def test_network_failure_is_explicit():
    def handler(request):
        raise httpx.ConnectError("refused")
    c = _client(handler)
    with pytest.raises(HaError) as ei:
        import asyncio
        asyncio.run(c.get_state("light.living"))
    assert "连不上" in str(ei.value)


# ---------------- 5) webhook ----------------

def test_parse_event_defaults():
    ev = parse_event({"event": "leak", "entity_id": "sensor.kitchen"})
    assert ev.event_type == "leak" and ev.entity_id == "sensor.kitchen"
    assert ev.level == "info" and ev.message == ""


def test_parse_event_full():
    ev = parse_event({"event_type": "motion", "message": "客厅有人",
                      "camera_entity": "camera.living", "level": "warn"})
    assert ev.event_type == "motion" and ev.camera_entity == "camera.living"
    assert ev.level == "warn"


def test_format_event_text():
    ev = parse_event({"event_type": "temperature_high", "level": "error"})
    txt = format_event_text(ev)
    assert "🚨" in txt and "temperature_high" in txt
    ev2 = parse_event({"event_type": "x", "message": "厨房漏水了！"})
    assert format_event_text(ev2) == "📢 厨房漏水了！"


def test_webhook_app_token_and_flow():
    from fastapi.testclient import TestClient

    got = []

    async def on_event(ev):
        got.append(ev)

    app = create_app(on_event, token="s3cret")
    tc = TestClient(app)

    r = tc.post("/ha/webhook", json={"event_type": "motion"})
    assert r.status_code == 403  # 无 token 拒

    r = tc.post("/ha/webhook?token=wrong", json={"event_type": "motion"})
    assert r.status_code == 403

    r = tc.post("/ha/webhook?token=s3cret",
                json={"event_type": "motion", "message": "客厅有人喵"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(got) == 1 and got[0].message == "客厅有人喵"

    r = tc.get("/ha/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_webhook_callback_failure_is_500():
    from fastapi.testclient import TestClient

    async def on_event(ev):
        raise RuntimeError("QQ 发送失败")

    tc = TestClient(create_app(on_event, token=""))
    r = tc.post("/ha/webhook", json={"event_type": "leak"})
    assert r.status_code == 500 and "发送失败" in r.json()["error"]


def test_webhook_not_wired_is_503():
    from fastapi.testclient import TestClient

    tc = TestClient(create_app(None, token=""))
    r = tc.post("/ha/webhook", json={"event_type": "leak"})
    assert r.status_code == 503


def test_webhook_bad_body_is_400():
    from fastapi.testclient import TestClient

    tc = TestClient(create_app(lambda ev: _noop(), token=""))
    r = tc.post("/ha/webhook?token=", content=b"not-json",
                headers={"content-type": "application/json"})
    assert r.status_code == 400


async def _noop():
    return None


# ---------------- 6) camera ----------------

class _FakeHA:
    """duck typing 的 HA client（只实现 camera 用到的几个方法/属性）。"""

    def __init__(self, status=200, content=b"\xff\xd8JPEGDATA", state=None):
        self._status, self._content, self._state = status, content, state
        self.base_url = "http://ha.test"

    def _check(self, e):
        return e

    def _headers(self):
        return {"Authorization": "Bearer tok"}

    async def get_state(self, e):
        if self._state is None:
            return {"attributes": {"stream_source": "rtsp://ha/cam"}}
        return self._state


def test_camera_snapshot_ok():
    from qq_bridge.smarthome import CameraGrab

    ha = _FakeHA()

    def handler(request):
        assert request.url.path == "/api/camera_proxy/camera.living"
        return httpx.Response(200, content=b"\xff\xd8JPEGDATA")

    import asyncio
    from qq_bridge.smarthome.camera import CameraGrab as CG

    async def run():
        real_get = httpx.AsyncClient.get

        async def fake_get(self, url, **kw):
            return handler(httpx.Request("GET", str(url)))

        httpx.AsyncClient.get = fake_get
        try:
            return await CG(ha).snapshot("camera.living")
        finally:
            httpx.AsyncClient.get = real_get

    data = asyncio.run(run())
    assert data == b"\xff\xd8JPEGDATA"


def test_camera_snapshot_http_error_explicit():
    import asyncio
    from qq_bridge.smarthome.camera import CameraGrab as CG

    ha = _FakeHA(status=500)

    async def run():
        real_get = httpx.AsyncClient.get

        async def fake_get(self, url, **kw):
            return httpx.Response(500, text="down")

        httpx.AsyncClient.get = fake_get
        try:
            await CG(ha).snapshot("camera.living")
        finally:
            httpx.AsyncClient.get = real_get

    with pytest.raises(CameraError) as ei:
        asyncio.run(run())
    assert "取不到画面" in str(ei.value)


def test_camera_clip_without_ffmpeg_is_explicit_no_fallback():
    """无 ffmpeg → 显式报错，禁止降级成静图（用户要视频就给视频）。"""
    import asyncio
    from qq_bridge.smarthome.camera import CameraGrab as CG

    ha = _FakeHA()
    import qq_bridge.smarthome.camera as cam_mod
    orig = cam_mod.shutil.which
    cam_mod.shutil.which = lambda name: None  # 假装没装 ffmpeg
    try:
        with pytest.raises(CameraError) as ei:
            asyncio.run(CG(ha).clip("camera.living", seconds=3))
    finally:
        cam_mod.shutil.which = orig
    assert "ffmpeg" in str(ei.value)


def test_camera_clip_no_stream_source_explicit():
    import asyncio
    from qq_bridge.smarthome.camera import CameraGrab as CG

    ha = _FakeHA(state={"attributes": {}})
    import qq_bridge.smarthome.camera as cam_mod
    orig = cam_mod.shutil.which
    cam_mod.shutil.which = lambda name: "/usr/bin/ffmpeg"  # 假装有
    try:
        with pytest.raises(CameraError) as ei:
            asyncio.run(CG(ha).clip("camera.living", seconds=3))
    finally:
        cam_mod.shutil.which = orig
    assert "stream_source" in str(ei.value)
