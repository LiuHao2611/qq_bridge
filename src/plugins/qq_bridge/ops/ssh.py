# -*- coding: utf-8 -*-
"""ops.ssh —— 远程主机 SSH 命令白名单执行（M9 运维面，2026-10-20）。

范式参考 dsh-qq-remote（2026-10-20 实读 README）：`/exec <shell>` 经 SSH
在远程执行并回传输出。其 README 自认「可执行任意命令」——本项目安全模型
**比它更严**（dev_plan §五：M9 远程 shell 严格走「仅主人 + 二次确认 +
审计」）：
  1. **主机白名单**：只连 `OPS_SSH_HOSTS` 里配置的别名主机（别名:user@host:port），
     拒绝任意地址；
  2. **命令白名单**：只放行 `OPS_SSH_CMD_WHITELIST` 配置的命令前缀
     （默认只读类：uptime/df/free/systemctl status/docker ps/journalctl 尾部…），
     命中才执行，否则显式拒绝；
  3. **显式超时**（CLOSE-05，Config.OPS_SSH_TIMEOUT 默认 30s）；
  4. **审计**：谁/何时/哪台主机/什么命令 → jlog + 调用方落账；
  5. **仅主人 + 二次确认**由调用方（/ops 命令族确认流）落实，本模块提供
     `describe()` 供确认文案组装。

paramiko 是同步库 → exec_command 经 asyncio.to_thread 包住，不堵事件循环。
失败显式 SshError，不静默、不重试（远程执行重试 = 重复副作用，禁止）。
"""
from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass

from ..config import Config, get_logger, jlog

log = get_logger()

#: 默认远程命令白名单（只读运维查询；可在配置页加）
DEFAULT_CMD_WHITELIST = (
    "uptime", "df -h", "free -h", "who", "last -n 5", "w",
    "systemctl status", "systemctl is-active", "systemctl is-enabled",
    "docker ps", "docker stats --no-stream", "journalctl -n 50",
    "tail -n 50", "cat /proc/loadavg", "hostname", "ip addr", "ip route",
    "ping -c 3", "ss -tulnp", "netstat -tulnp", "crontab -l",
)

#: 永远拒绝的危险模式（白名单之外的双保险）
_FORBIDDEN = re.compile(
    r"(rm\s+-|mkfs|dd\s+if=|:\(\)\{|shutdown|reboot|poweroff|halt|"
    r">\s*/dev/|chmod\s+777|curl\s+.*\|\s*(sh|bash)|wget\s+.*\|\s*(sh|bash)|"
    r"git\s+push|systemctl\s+(stop|restart|disable|mask))", re.I)


class SshError(Exception):
    """显式化的 SSH 执行失败。"""


@dataclass(frozen=True)
class SshHost:
    alias: str
    user: str
    host: str
    port: int = 22


def parse_hosts(raw: str) -> dict[str, SshHost]:
    """解析 OPS_SSH_HOSTS：`别名:user@host[:port]` 分号分隔（端口可省，默认 22）。

    坏条目跳过并记日志（不静默吞整个配置——单条坏不影响其它主机）。
    """
    out: dict[str, SshHost] = {}
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item:
            continue
        try:
            alias, rest = item.split(":", 1)
            # 端口判定：最后一段是纯数字才算端口（避免把 IPv6/无端口条目切坏）
            head, sep, tail = rest.rpartition(":")
            if sep and tail.isdigit():
                userhost, port = head, int(tail)
            else:
                userhost, port = rest, 22
            user, _, host = userhost.partition("@")
            if not alias or not user or not host:
                raise ValueError("字段缺失")
            out[alias.strip()] = SshHost(alias=alias.strip(), user=user,
                                         host=host, port=port)
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("ssh_host_parse_failed", item=item[:80],
                             error=type(e).__name__))
    return out


def parse_cmd_whitelist(raw: str) -> tuple[str, ...]:
    """OPS_SSH_CMD_WHITELIST → 前缀元组（空配置回落默认表）。"""
    items = tuple(x.strip() for x in (raw or "").split(";") if x.strip())
    return items or DEFAULT_CMD_WHITELIST


def check_command(cmd: str, whitelist: tuple[str, ...]) -> str:
    """命令白名单校验。返回错误文本；空串 = 放行。"""
    c = (cmd or "").strip()
    if not c:
        return "远程命令不能为空"
    if _FORBIDDEN.search(c):
        return "命令命中危险模式（rm/poweroff/git push 等），拒绝执行"
    if not any(c == p or c.startswith(p + " ") for p in whitelist):
        return ("命令不在白名单（%s…）。要加命令去「机器人配置 → 运维面 → "
                "OPS_SSH_CMD_WHITELIST」" % whitelist[0])
    return ""


class SshRunner:
    """SSH 命令执行器（paramiko，同步 API 经 to_thread）。"""

    def __init__(self, hosts: dict[str, SshHost] | None = None,
                 whitelist: tuple[str, ...] | None = None,
                 timeout: float | None = None) -> None:
        self.hosts = hosts if hosts is not None else parse_hosts(
            getattr(Config, "OPS_SSH_HOSTS", "") or "")
        raw_wl = whitelist if whitelist is not None else parse_cmd_whitelist(
            str(getattr(Config, "OPS_SSH_CMD_WHITELIST", "") or ""))
        self.whitelist = raw_wl
        self.timeout = float(timeout if timeout is not None
                             else getattr(Config, "OPS_SSH_TIMEOUT", 30.0))

    def get_host(self, alias: str) -> SshHost:
        h = self.hosts.get((alias or "").strip())
        if not h:
            raise SshError("没有名为「%s」的主机（已配置：%s）"
                           % (alias, "、".join(self.hosts) or "无"))
        return h

    def describe(self, alias: str, cmd: str) -> str:
        """确认文案（高危二次确认用）。"""
        h = self.get_host(alias)
        return "将在 %s@%s:%d 执行：\n%s" % (h.user, h.host, h.port, cmd[:300])

    async def run(self, alias: str, cmd: str, user_id: int = 0) -> str:
        """执行远程命令并返回输出（stdout+stderr 合并，截断 3000 字）。

        审计：jlog 记 谁/何时/主机/命令（LOG-04：不记密码，本设计本就无密码
        ——用 SSH key/agent 鉴权，见 OPS_SSH_KEY 配置）。
        """
        h = self.get_host(alias)
        err = check_command(cmd, self.whitelist)
        if err:
            log.warning(jlog("ssh_cmd_rejected", alias=alias, user_id=user_id,
                             detail=err[:120]))
            raise SshError(err)
        log.info(jlog("ssh_exec_start", alias=alias, host=h.host,
                      user_id=user_id, cmd=cmd[:120]))

        def _sync() -> str:
            try:
                import paramiko
            except Exception as e:  # noqa: BLE001
                raise SshError("paramiko 没装（%s）：pip install paramiko"
                               % type(e).__name__)
            client = paramiko.SSHClient()
            try:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                connect_kw = {
                    "hostname": h.host, "port": h.port, "username": h.user,
                    "timeout": self.timeout,
                }
                key_path = str(getattr(Config, "OPS_SSH_KEY", "") or "").strip()
                if key_path:
                    connect_kw["key_filename"] = key_path
                client.connect(**connect_kw)
                _stdin, stdout, stderr = client.exec_command(
                    cmd, timeout=int(self.timeout))
                out = (stdout.read() + b"\n" + stderr.read()).decode(
                    "utf-8", "replace").strip()
                rc = stdout.channel.recv_exit_status()
                return (out or "（命令无输出）") + (
                    "" if rc == 0 else "\n[退出码 %d]" % rc)
            except Exception as e:  # noqa: BLE001 — 显式失败，不重试
                raise SshError("SSH 执行失败（%s）：%s"
                               % (type(e).__name__, str(e)[:200]))
            finally:
                try:
                    client.close()
                except Exception:  # noqa: BLE001 — close 失败不掩盖主错误
                    pass

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_sync), timeout=self.timeout + 10.0)
        except asyncio.TimeoutError:
            raise SshError("SSH 执行超时（%ss）" % int(self.timeout))
        text = result[:3000]
        log.info(jlog("ssh_exec_done", alias=alias, user_id=user_id,
                      out_len=len(text)))
        return text


#: 模块级单例（单测各自 new 隔离）
ssh_runner = SshRunner()
