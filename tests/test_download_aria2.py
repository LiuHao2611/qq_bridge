# -*- coding: utf-8 -*-
"""M7 aria2 JSON-RPC 客户端验收（2026-10-20，download/aria2.py）。

覆盖（契约 2026-10-20 实读 DownloadBot Aria2.go + aria2 RPC 文档验证）：
  1. _call 协议结构：jsonrpc/id/method(aria2.前缀)/params(token:secret 开头)；
  2. 响应处理：result 直返；error 字段 → Aria2Error（含 message）；
     HTTP 4xx/5xx → 显式错误；非 JSON → 显式错误；
  3. REDUN-02：ConnectError 指数退避重试封顶 3 次；超时不重试；
  4. 高层方法：addUri/addTorrent(base64)/tellStatus/getFiles/pause/remove 等；
  5. check()：连通返回版本；连不上返回开启指引（含 aria2c 启动命令）；
  6. list_tasks：active+waiting+stopped 合并；RPC 失败显式上抛不返回空列表。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_download_aria2.py -v
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
# 与 test_config_hot_reload 同款命名空间：避免两份 Config 类污染其热生效用例
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.download.aria2 import Aria2Client, Aria2Error  # noqa: E402


def _client(handler, secret="s3cr3t"):
    """造一个打假服务的 Aria2Client（httpx MockTransport）。"""
    transport = httpx.MockTransport(handler)
    return Aria2Client(url="http://127.0.0.1:6800/jsonrpc", secret=secret,
                       timeout=5.0, transport=transport)


def _ok(result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": "qq_bridge",
                                     "result": result})


# ---------------- 1. 协议结构 ----------------
@pytest.mark.anyio
async def test_call_protocol_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok("gid-123")

    cli = _client(handler)
    gid = await cli.add_uri(["http://example.com/a.zip"], {"dir": "D:/dl"})
    assert gid == "gid-123"
    assert seen["url"] == "http://127.0.0.1:6800/jsonrpc"
    body = seen["body"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "aria2.addUri"
    assert body["params"][0] == "token:s3cr3t"      # secret 传法
    assert body["params"][1] == ["http://example.com/a.zip"]
    assert body["params"][2] == {"dir": "D:/dl"}


@pytest.mark.anyio
async def test_add_torrent_base64():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok("gid-bt")

    cli = _client(handler)
    tmp = Path(__file__).parent / ".tmp_bt.torrent"
    tmp.write_bytes(b"d8:announce3:foo4:infod6:lengthi1eeee")
    try:
        gid = await cli.add_torrent(tmp)
        assert gid == "gid-bt"
        assert seen["body"]["method"] == "aria2.addTorrent"
        # torrent 内容经 base64 传输（aria2 RPC 契约）
        assert base64.b64decode(seen["body"]["params"][1]) == \
            b"d8:announce3:foo4:infod6:lengthi1eeee"
        assert seen["body"]["params"][2] == []      # uris 空
    finally:
        tmp.unlink(missing_ok=True)


# ---------------- 2. 错误显式化 ----------------
@pytest.mark.anyio
async def test_rpc_error_raises():
    def handler(request):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x",
                                         "error": {"code": 1,
                                                   "message": "No such file"}})

    cli = _client(handler)
    with pytest.raises(Aria2Error) as ei:
        await cli.get_files("bad-gid")
    assert "No such file" in str(ei.value)


@pytest.mark.anyio
async def test_http_error_raises():
    def handler(request):
        return httpx.Response(404, text="not found")

    cli = _client(handler)
    with pytest.raises(Aria2Error) as ei:
        await cli.get_version()
    assert "404" in str(ei.value)


@pytest.mark.anyio
async def test_non_json_raises():
    def handler(request):
        return httpx.Response(200, text="<html>oops</html>")

    cli = _client(handler)
    with pytest.raises(Aria2Error) as ei:
        await cli.get_version()
    assert "不是 JSON" in str(ei.value)


@pytest.mark.anyio
async def test_missing_result_raises():
    def handler(request):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x"})

    cli = _client(handler)
    with pytest.raises(Aria2Error) as ei:
        await cli.get_version()
    assert "result" in str(ei.value)


# ---------------- 3. REDUN-02 重试 ----------------
@pytest.mark.anyio
async def test_connect_error_retries_then_fails():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    cli = _client(handler)
    with pytest.raises(Aria2Error) as ei:
        await cli.get_version()
    assert calls["n"] == 3            # 总尝试 3 次（REDUN-02 封顶）
    assert "连不上" in str(ei.value)


@pytest.mark.anyio
async def test_timeout_no_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.TimeoutException("too slow")

    cli = _client(handler)
    with pytest.raises(Aria2Error) as ei:
        await cli.get_version()
    assert calls["n"] == 1            # 超时不重试
    assert "超时" in str(ei.value)


# ---------------- 4. 高层方法 ----------------
@pytest.mark.anyio
async def test_task_operations():
    routes = {}

    def handler(request):
        body = json.loads(request.content)
        routes[body["method"]] = body["params"]
        m = body["method"]
        if m == "aria2.tellStatus":
            return _ok({"gid": body["params"][1], "status": "active",
                        "totalLength": "100", "completedLength": "50"})
        if m == "aria2.getFiles":
            return _ok([{"path": "[bt]/a.mkv", "length": "100",
                         "selected": "true"}])
        if m == "aria2.tellActive":
            return _ok([{"gid": "g1"}])
        if m == "aria2.tellWaiting":
            return _ok([{"gid": "g2"}])
        if m == "aria2.tellStopped":
            return _ok([{"gid": "g3"}])
        return _ok(None)

    cli = _client(handler)
    st = await cli.tell_status("g1")
    assert st["status"] == "active"
    files = await cli.get_files("g1")
    assert files[0]["selected"] == "true"
    tasks = await cli.list_tasks()
    assert [t["gid"] for t in tasks] == ["g1", "g2", "g3"]
    await cli.pause("g1")
    await cli.remove("g1")
    assert "aria2.pause" in routes and "aria2.remove" in routes


@pytest.mark.anyio
async def test_change_option_select_file():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return _ok(None)

    cli = _client(handler)
    await cli.change_option("gid", {"select-file": "2,3"})
    assert seen["params"] == ["token:s3cr3t", "gid", {"select-file": "2,3"}]


# ---------------- 5. check() 开启指引 ----------------
@pytest.mark.anyio
async def test_check_ok():
    def handler(request):
        return _ok({"version": "1.37.0", "enabledFeatures": []})

    cli = _client(handler)
    ok, detail = await cli.check()
    assert ok and "1.37.0" in detail


@pytest.mark.anyio
async def test_check_unreachable_gives_start_hint():
    def handler(request):
        raise httpx.ConnectError("refused")

    cli = _client(handler)
    ok, detail = await cli.check()
    assert not ok
    # 显式开启指引（不内置进程，尊重机器主权）
    assert "--enable-rpc" in detail
    assert "--rpc-secret" in detail
    assert "aria2c" in detail


# ---------------- 6. list_tasks 失败显式上抛 ----------------
@pytest.mark.anyio
async def test_list_tasks_failure_raises():
    def handler(request):
        raise httpx.ConnectError("refused")

    cli = _client(handler)
    with pytest.raises(Aria2Error):
        await cli.list_tasks()   # 不返回空列表冒充成功（禁止兜底）
