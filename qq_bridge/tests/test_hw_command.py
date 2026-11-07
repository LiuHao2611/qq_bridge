# -*- coding: utf-8 -*-
"""
test_hw_command.py —— QQ 机器人「硬件资源监控」命令验收（2026-09-14）。

覆盖（铁律：机器判定 assert + 非零退出码）：
  1. 意图路由：/hw /hardware /资源 及自然语言暗号（资源/硬件/占用/硬件占用/看看资源）
     均映射到 Route(kind="reply", text="__HW__")；
  2. 文本格式化：_fmt_hw / _fmt_bytes 正确渲染 CPU/内存/磁盘/GPU；
  3. 边界：空快照 / 无 GPU / 字节换算 GB/MB；
  4. _cmd_hw 失败路径：主项目不可达时返回带错误标题的回复文本（mock _manju_get）。

运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_hw_command.py -v
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.plugins.qq_bridge.intent import route, Route  # noqa: E402
from src.plugins.qq_bridge.session import Session  # noqa: E402


def _ev(text: str) -> object:
    """构造一个最小 QQEvent 替身。"""
    from dataclasses import dataclass, field

    @dataclass
    class _E:
        user_id: int = 1
        group_id: int = 0
        message_type: str = "private"
        raw_text: str = text
        at_bot: bool = True
        images: list = field(default_factory=list)

    return _E(raw_text=text)


def _sess() -> Session:
    return Session(user_id=1)


def test_route_hw_slash_commands():
    """斜杠指令统一映射到 __HW__。"""
    for cmd in ("/hw", "/hardware", "/资源"):
        r = route(_ev(cmd), _sess())
        assert r == Route(kind="reply", text="__HW__"), cmd


def test_route_hw_maid_aliases():
    """自然语言暗号映射到 __HW__。"""
    for text in ("资源", "硬件", "占用", "硬件占用", "看看资源"):
        r = route(_ev(text), _sess())
        assert r == Route(kind="reply", text="__HW__"), text


def test_fmt_bytes():
    from src.plugins.qq_bridge import _fmt_bytes  # noqa: E402

    assert _fmt_bytes(None) == "—"
    assert _fmt_bytes("abc") == "—"
    assert _fmt_bytes(1024 * 1024 * 512) == "512.0 MB"
    assert _fmt_bytes(1024 * 1024 * 1024 * 8) == "8.0 GB"


def test_fmt_hw_full():
    from src.plugins.qq_bridge import _fmt_hw  # noqa: E402

    snap = {
        "ts": 1694668800,
        "cpu": {"percent": 12.5, "count_physical": 4, "count_logical": 8},
        "mem": {"used": 1024 ** 3 * 10.8, "total": 1024 ** 3 * 15.9, "percent": 67.9},
        "disk": {"path": "D:", "used": 1024 ** 3 * 397.7, "total": 1024 ** 3 * 476.9, "percent": 83.4},
        "gpus": [
            {
                "index": 0, "name": "NVIDIA GeForce GTX 1660 Ti",
                "utilization": 23.0, "mem_used": 1024 ** 2 * 989,
                "mem_total": 1024 ** 3 * 6, "temperature": 55.0,
                "power_draw": 120.0, "power_limit": 130.0,
            }
        ],
    }
    text = _fmt_hw(snap)
    assert "CPU：12.5%" in text
    assert "4 核 8 线程" in text
    assert "内存：10.8 GB / 15.9 GB" in text
    assert "磁盘 D:" in text
    assert "GTX 1660 Ti" in text
    assert "23.0%" in text
    assert "989.0 MB" in text
    assert "55.0°C" in text
    assert "120.0 W" in text


def test_fmt_hw_no_gpu():
    from src.plugins.qq_bridge import _fmt_hw  # noqa: E402

    text = _fmt_hw({"cpu": {"percent": 5.0}, "mem": {"used": 1, "total": 2, "percent": 50.0}})
    assert "GPU：未检测到" in text


def test_fmt_hw_empty():
    from src.plugins.qq_bridge import _fmt_hw  # noqa: E402

    assert "空的硬件快照" in _fmt_hw({})


def test_cmd_hw_success():
    """_cmd_hw 成功时把格式化文本发给用户。"""
    import src.plugins.qq_bridge as bridge  # noqa: E402

    snap = {
        "cpu": {"percent": 10.0, "count_physical": 4, "count_logical": 8},
        "mem": {"used": 1, "total": 2, "percent": 50.0},
        "gpus": [],
    }
    sent = []

    async def _fake_reply(ctx, sess, text, *, title=""):
        sent.append((title, text))

    with patch.object(bridge, "_manju_get", new=AsyncMock(return_value=(snap, ""))):
        with patch.object(bridge, "_reply", new=_fake_reply):
            asyncio.run(bridge._cmd_hw(_ev("资源"), _sess()))

    assert len(sent) == 1
    assert sent[0][0] == "本地硬件占用"
    assert "CPU：10.0%" in sent[0][1]


def test_cmd_hw_failure():
    """_cmd_hw 主项目不可达时回错误提示。"""
    import src.plugins.qq_bridge as bridge  # noqa: E402

    sent = []

    async def _fake_reply(ctx, sess, text, *, title=""):
        sent.append((title, text))

    with patch.object(bridge, "_manju_get", new=AsyncMock(return_value=(None, "连不上主项目"))):
        with patch.object(bridge, "_reply", new=_fake_reply):
            asyncio.run(bridge._cmd_hw(_ev("资源"), _sess()))

    assert len(sent) == 1
    assert sent[0][0] == "硬件占用"
    assert "拉硬件占用失败" in sent[0][1]


if __name__ == "__main__":
    test_route_hw_slash_commands()
    test_route_hw_maid_aliases()
    test_fmt_bytes()
    test_fmt_hw_full()
    test_fmt_hw_no_gpu()
    test_fmt_hw_empty()
    test_cmd_hw_success()
    test_cmd_hw_failure()
    print("HW_COMMAND_OK")
