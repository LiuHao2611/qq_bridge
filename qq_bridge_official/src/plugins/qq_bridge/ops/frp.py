# -*- coding: utf-8 -*-
"""ops.frp —— frp/nps 内网穿透隧道状态查询（M9 运维面，2026-10-20）。

需求（roadmap §M9）：frp/nps 隧道在线状态查询。参考 frp（fatedier）的
admin 能力：frpc 的 `admin_addr`/`admin_port`（默认 127.0.0.1:7400）提供
`/api/status`（GET，返回 JSON：total/online proxies 概况）与 `/api/reload`。

实现取舍：
  - 只查 frpc 本地 admin API（HTTP GET /api/status）——**不引 frp 源码**，
    协议即两个 GET 端点；
  - nps 用户请配 `OPS_FRP_ADMIN` 指向 nps 的 web API（同构 JSON，一期不
    单独适配，未配置/不通时**诚实告知缺什么**，不返回假在线状态；
  - CLOSE-05 显式超时；失败显式 FrpError。

本模块不 import nonebot / 不 import 主包：可裸单测（transport 可注入）。
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import Config, get_logger, jlog

log = get_logger()

#: frpc admin API 默认地址（frpc.toml 的 admin_addr/admin_port）
DEFAULT_ADMIN = "http://127.0.0.1:7400"


class FrpError(Exception):
    """显式化的 frp 状态查询失败。"""


class FrpStatus:
    """frpc admin API 客户端。"""

    def __init__(self, admin_url: str | None = None,
                 timeout: float | None = None, transport: Any = None) -> None:
        self.admin_url = (admin_url if admin_url is not None
                          else (getattr(Config, "OPS_FRP_ADMIN", "") or DEFAULT_ADMIN)
                          ).strip().rstrip("/")
        self.timeout = float(timeout if timeout is not None
                             else getattr(Config, "OPS_FRP_TIMEOUT", 10.0))
        self._transport = transport

    async def status(self) -> dict:
        """GET /api/status。返回原始 JSON（含 total/online 概览）。

        未配置/连不上 → 显式 FrpError（含排查指引），不返回假数据。
        """
        try:
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout), trust_env=False,
                    transport=self._transport) as cli:
                resp = await cli.get(self.admin_url + "/api/status")
        except httpx.ConnectError:
            raise FrpError(
                "连不上 frp admin API（%s）——frpc 跑着吗？frpc.toml 里得有 "
                "adminAddr/adminPort（webServer 面板也行），然后把地址填到"
                "「机器人配置 → 运维面 → OPS_FRP_ADMIN」" % self.admin_url)
        except httpx.TimeoutException:
            raise FrpError("frp admin API 响应超时（%ss）" % int(self.timeout))
        except Exception as e:  # noqa: BLE001 — CLOSE-02
            raise FrpError("frp admin API 调用失败（%s）" % type(e).__name__)
        if resp.status_code >= 400:
            raise FrpError("frp admin API 报错 HTTP %d" % resp.status_code)
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            raise FrpError("frp admin API 返回不是 JSON（%s）" % type(e).__name__)

    def format_status(self, data: dict) -> str:
        """frpc /api/status JSON → 人话。"""
        total = data.get("total") or {}
        lines = ["frp 隧道状态（%s）：" % self.admin_url]
        lines.append("代理总数 %s · 在线 %s · 离线 %s" % (
            total.get("proxies", "?"), total.get("online_proxies", "?"),
            total.get("offline_proxies", "?")))
        return "\n".join(lines)


#: 模块级单例（单测各自 new 隔离）
frp_status = FrpStatus()
