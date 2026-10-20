# -*- coding: utf-8 -*-
"""ops.wol —— Wake-on-LAN 魔术包唤醒（M9 运维面，2026-10-20）。

需求（roadmap §M9）：「开机 客厅那台」→ 魔术包唤醒。
原理（标准 WoL 协议，无需外部依赖）：6 字节 0xFF + 16 遍目标 MAC 的
UDP 广播包，发往 7/9 端口。子网广播地址可配（OPS_WOL_BROADCAST，
默认 255.255.255.255——路由跨网段时改成目标子网的广播地址如 192.168.1.255）。

安全：
  - **主机白名单**：只唤醒 OPS_WOL_HOSTS 里配置的别名（别名:MAC[:广播地址]）；
  - 唤醒是开机动作（有副作用但低危）→ 仅主人（调用方落实），本模块做白名单；
  - socket 显式超时 + 失败显式 WolError（禁静默——「好像没唤醒成功」是最
    难排查的问题，必须明说发没发出去）。

本模块不 import nonebot / 不 import 主包：可裸单测（sender 可注入）。
"""
from __future__ import annotations

import re
import socket
from dataclasses import dataclass

from ..config import Config, get_logger, jlog

log = get_logger()

#: MAC 地址（aa:bb:cc:dd:ee:ff 或 aa-bb-cc-dd-ee-ff）
_MAC_RE = re.compile(r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$")

DEFAULT_BROADCAST = "255.255.255.255"
DEFAULT_PORT = 9


class WolError(Exception):
    """显式化的 WoL 失败。"""


@dataclass(frozen=True)
class WolHost:
    alias: str
    mac: str
    broadcast: str = DEFAULT_BROADCAST
    port: int = DEFAULT_PORT


def parse_hosts(raw: str) -> dict[str, WolHost]:
    """解析 OPS_WOL_HOSTS：`别名:MAC[@广播地址[/端口]]` 分号分隔。

    格式说明：MAC 自身含冒号，故广播地址用 `@` 分隔（不用冒号——歧义），
    端口用 `/`（可省，默认 9）。例：
      客厅:AA:BB:CC:DD:EE:FF
      书房:11:22:33:44:55:66@192.168.1.255/7
    坏条目跳过并记日志（不静默吞整个配置）。
    """
    out: dict[str, WolHost] = {}
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item:
            continue
        try:
            alias, mac_part = item.split(":", 1)
            mac, _, bcast_part = mac_part.partition("@")
            mac = mac.strip()
            bcast = DEFAULT_BROADCAST
            port = DEFAULT_PORT
            if bcast_part:
                addr, _, port_s = bcast_part.partition("/")
                bcast = addr.strip() or DEFAULT_BROADCAST
                port = int(port_s) if port_s.strip().isdigit() else DEFAULT_PORT
            if not alias.strip() or not _MAC_RE.match(mac):
                raise ValueError("MAC 格式不对")
            out[alias.strip()] = WolHost(alias=alias.strip(), mac=mac,
                                         broadcast=bcast, port=port)
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("wol_host_parse_failed", item=item[:80],
                             error=type(e).__name__))
    return out


def build_magic_packet(mac: str) -> bytes:
    """构造魔术包：6×0xFF + 16×MAC（标准 WoL）。MAC 先归一化去分隔符。"""
    if not _MAC_RE.match(mac or ""):
        raise WolError("MAC 地址格式不对：%s" % mac)
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    return b"\xff" * 6 + mac_bytes * 16


class WolSender:
    """魔术包发送器（socket 可注入，便于单测不打真广播）。"""

    def __init__(self, hosts: dict[str, WolHost] | None = None) -> None:
        self.hosts = hosts if hosts is not None else parse_hosts(
            getattr(Config, "OPS_WOL_HOSTS", "") or "")

    def get_host(self, alias: str) -> WolHost:
        h = self.hosts.get((alias or "").strip())
        if not h:
            raise WolError("没有名为「%s」的唤醒目标（已配置：%s）"
                           % (alias, "、".join(self.hosts) or "无"))
        return h

    async def wake(self, alias: str, user_id: int = 0) -> str:
        """发送魔术包。返回人话结果（发送成功≠机器一定亮——BIOS 需开 WoL）。"""
        h = self.get_host(alias)
        packet = build_magic_packet(h.mac)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(3.0)
                s.sendto(packet, (h.broadcast, h.port))
        except Exception as e:  # noqa: BLE001 — 发送失败显式报错
            log.warning(jlog("wol_send_failed", alias=alias, user_id=user_id,
                             error=type(e).__name__))
            raise WolError("魔术包发送失败（%s）：%s（目标 BIOS 需开启 Wake-on-LAN，"
                           "且网卡/路由器要放行广播）" % (type(e).__name__, e))
        log.info(jlog("wol_sent", alias=alias, mac=h.mac, user_id=user_id,
                      broadcast=h.broadcast, port=h.port))
        return ("魔术包已发往 %s（MAC %s，%s:%d）\n"
                "机器没亮的话查三处：BIOS 里 Wake-on-LAN 开没开、Windows 网卡"
                "高级设置里「唤醒魔术包」勾没勾、路由器放不放行该广播" % (
                    alias, h.mac, h.broadcast, h.port))


#: 模块级单例（单测各自 new 隔离）
wol_sender = WolSender()
