# -*- coding: utf-8 -*-
"""M9 WoL 魔术包 / frp 状态查询验收（2026-10-20，ops/wol.py + ops/frp.py）。

wol 覆盖：
  1. build_magic_packet：6×0xFF + 16×MAC（标准 WoL 结构）；
  2. MAC 归一化（: 与 - 分隔符都吃）；坏 MAC 显式 WolError；
  3. parse_hosts：别名:MAC[:广播[:端口]]；坏条目跳过；
  4. wake：白名单外别名拒绝；发送成功返回人话（含 BIOS 排查指引）；
  5. 发送失败（注入坏 socket 行为）显式 WolError。

frp 覆盖：
  6. status：MockTransport 200 JSON → 原样返回；连不上 → FrpError 含
     开启指引；HTTP 4xx / 非 JSON → 显式错误（不返回假在线状态）；
  7. format_status：人话含代理总数/在线/离线。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_ops_wol_frp.py -v
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.ops.frp import FrpError, FrpStatus  # noqa: E402
from qq_bridge.ops.wol import WolError, WolHost, WolSender, build_magic_packet  # noqa: E402

_MAC = "AA:BB:CC:DD:EE:FF"
_MAC_BYTES = bytes.fromhex("AABBCCDDEEFF")


# ---------------- 1/2. 魔术包结构 ----------------
def test_magic_packet_structure():
    pkt = build_magic_packet(_MAC)
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:] == _MAC_BYTES * 16
    assert len(pkt) == 6 + 16 * 6


def test_magic_packet_dash_mac_normalized():
    assert build_magic_packet("aa-bb-cc-dd-ee-ff") == build_magic_packet(_MAC)


def test_bad_mac_raises():
    for bad in ("", "aabbcc", "AA:BB:CC:DD:EE", "ZZ:BB:CC:DD:EE:FF"):
        with pytest.raises(WolError):
            build_magic_packet(bad)


# ---------------- 3. parse_hosts ----------------
def test_parse_wol_hosts():
    hs = WolSender(hosts=None).hosts  # 空配置不炸
    assert hs == {}
    from qq_bridge.ops.wol import parse_hosts
    out = parse_hosts("客厅:%s;书房:11:22:33:44:55:66@192.168.1.255/7" % _MAC)
    assert set(out) == {"客厅", "书房"}
    assert out["书房"].broadcast == "192.168.1.255"
    assert out["书房"].port == 7
    assert out["客厅"].broadcast == "255.255.255.255"  # 默认广播
    assert parse_hosts("坏条目:xyz;好:%s" % _MAC)  # 坏的跳过


# ---------------- 4/5. wake ----------------
@pytest.mark.anyio
async def test_wake_unknown_alias():
    sender = WolSender(hosts={"客厅": WolHost("客厅", _MAC)})
    with pytest.raises(WolError) as ei:
        await sender.wake("卧室")
    assert "客厅" in str(ei.value)


@pytest.mark.anyio
async def test_wake_sends_packet(monkeypatch):
    sent = {}

    class FakeSock:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def setsockopt(self, *a):
            pass

        def settimeout(self, t):
            pass

        def sendto(self, packet, addr):
            sent["packet"] = packet
            sent["addr"] = addr

    import qq_bridge.ops.wol as wol_mod
    monkeypatch.setattr(wol_mod.socket, "socket", FakeSock)
    sender = WolSender(hosts={"客厅": WolHost("客厅", _MAC, "192.168.1.255", 9)})
    msg = await sender.wake("客厅", user_id=1)
    assert sent["packet"] == build_magic_packet(_MAC)
    assert sent["addr"] == ("192.168.1.255", 9)
    assert "魔术包已发往" in msg and "BIOS" in msg  # 排查指引随附


@pytest.mark.anyio
async def test_wake_send_failure_explicit(monkeypatch):
    class BadSock:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def setsockopt(self, *a):
            pass

        def settimeout(self, t):
            pass

        def sendto(self, packet, addr):
            raise OSError("network unreachable")

    import qq_bridge.ops.wol as wol_mod
    monkeypatch.setattr(wol_mod.socket, "socket", BadSock)
    sender = WolSender(hosts={"客厅": WolHost("客厅", _MAC)})
    with pytest.raises(WolError) as ei:
        await sender.wake("客厅", user_id=1)
    assert "Wake-on-LAN" in str(ei.value)  # 失败原因 + BIOS 提示


# ---------------- 6/7. frp ----------------
def _frp(handler):
    return FrpStatus(admin_url="http://127.0.0.1:7400", timeout=3.0,
                     transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_frp_status_ok():
    def handler(request):
        return httpx.Response(200, json={"total": {"proxies": 5,
                                                   "online_proxies": 4,
                                                   "offline_proxies": 1}})

    fs = _frp(handler)
    data = await fs.status()
    assert data["total"]["online_proxies"] == 4
    text = fs.format_status(data)
    assert "5" in text and "4" in text and "1" in text


@pytest.mark.anyio
async def test_frp_unreachable_gives_hint():
    def handler(request):
        raise httpx.ConnectError("refused")

    fs = _frp(handler)
    with pytest.raises(FrpError) as ei:
        await fs.status()
    assert "adminAddr" in str(ei.value)  # 开启指引


@pytest.mark.anyio
async def test_frp_http_error_and_non_json():
    fs = _frp(lambda r: httpx.Response(502, text="bad gateway"))
    with pytest.raises(FrpError) as ei:
        await fs.status()
    assert "502" in str(ei.value)
    fs2 = _frp(lambda r: httpx.Response(200, text="not json"))
    with pytest.raises(FrpError) as ei2:
        await fs2.status()
    assert "JSON" in str(ei2.value)
