# -*- coding: utf-8 -*-
"""download.aria2 —— aria2 JSON-RPC 客户端（M7 下载媒体中心，2026-10-20）。

契约验证（trust but verify，2026-10-20 实读 DownloadBot Aria2.go +
aria2 官方 RPC 文档）：
  - 端点：`http://127.0.0.1:6800/jsonrpc`（默认 HTTP；DownloadBot 用 WS，
    本地桥接选 HTTP 更简单——单进程内无长连接需求）；
  - 鉴权：`rpc-secret` 以 `token:<secret>` 作 params[0]；
  - 方法（`aria2.` 前缀）：addUri / addTorrent / tellStatus / getFiles /
    changeOption / tellActive / tellWaiting / tellStopped / pause / unpause /
    remove / forceRemove / getGlobalStat / getVersion / purgeDownloadResult；
  - BT 智能选文件范式（DownloadBot）：磁力 addUri → aria2 先取元数据 →
    tellStatus 见 `bittorrent.info.name` → pause → getFiles 挑文件 →
    changeOption `select-file`（1 起的序号串）→ unpause。

纪律：
  - CLOSE-05：httpx 显式超时（Config.DL_ARIA2_TIMEOUT，默认 15s）；
  - CLOSE-02：连不上/方法错误/RPC 应用错误 → 显式 Aria2Error，错误文本含
    「如何开启 RPC」指引（roadmap §10.4：未检测到则显式提示，不内置进程，
    尊重用户机器主权——**不自动拉起 aria2 进程**）；
  - REDUN-02：仅 ConnectError 指数退避重试（1s/2s+抖动，封顶 3 次）；
  - 本模块不 import nonebot / 不 import 主包：可裸单测。
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
from pathlib import Path
from typing import Any

import httpx

from ..config import Config, get_logger, jlog

log = get_logger()

#: aria2 RPC 默认端点（env DL_ARIA2_RPC 可覆盖）
DEFAULT_RPC = "http://127.0.0.1:6800/jsonrpc"


class Aria2Error(Exception):
    """显式化的 aria2 RPC 失败（message 即给上层的错误文本）。"""


def start_hint(url: str, secret: str, dl_dir: str) -> str:
    """RPC 未开时的开启指引（显式告知，不内置进程、不静默）。"""
    return ("连不上 aria2 RPC（%s）——本机需先以 RPC 模式启动 aria2，例如：\n"
            "aria2c --enable-rpc --rpc-listen-port=6800 --rpc-secret=%s "
            "--dir=\"%s\"\n"
            "（%s 已装在 WinGet 目录；Bridge 不会自带拉起 aria2 进程，"
            "尊重你的机器主权喵）" % (url, secret or "<你的secret>", dl_dir,
                                  "aria2c 1.37.0"))


class Aria2Client:
    """aria2 JSON-RPC over HTTP 客户端（单例见 aria2_client）。"""

    def __init__(self, url: str | None = None, secret: str | None = None,
                 timeout: float | None = None, transport=None) -> None:
        self.url = (url if url is not None
                    else (getattr(Config, "DL_ARIA2_RPC", "") or DEFAULT_RPC)).strip()
        self.secret = (secret if secret is not None
                       else str(getattr(Config, "DL_ARIA2_SECRET", "") or ""))
        self.timeout = float(timeout if timeout is not None
                             else getattr(Config, "DL_ARIA2_TIMEOUT", 15.0))
        #: 可注入 httpx transport（单测定制响应，不打真服务）
        self._transport = transport

    # ---------------- 协议层 ----------------
    async def _call(self, method: str, *params: Any) -> Any:
        """一次 JSON-RPC 调用。成功返回 result；失败抛 Aria2Error（显式）。"""
        payload = {
            "jsonrpc": "2.0",
            "id": "qq_bridge",
            "method": "aria2." + method,
            "params": ["token:%s" % self.secret, *params],
        }
        retries = 2  # REDUN-02：仅 ConnectError 重试，总尝试 3 次
        last_err = ""
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self.timeout), trust_env=False,
                        transport=self._transport) as cli:
                    resp = await cli.post(self.url, json=payload)
            except httpx.ConnectError:
                last_err = "连不上 aria2 RPC（%s）" % self.url
            except httpx.TimeoutException:
                raise Aria2Error("aria2 RPC 响应超时（%ss）" % int(self.timeout))
            except Exception as e:  # noqa: BLE001 — CLOSE-02 显式失败
                raise Aria2Error("aria2 RPC 调用失败（%s）" % type(e).__name__)
            else:
                sc = resp.status_code
                if sc >= 400:
                    raise Aria2Error("aria2 RPC 端口报错 HTTP %d（检查 "
                                     "DL_ARIA2_RPC 配置）" % sc)
                try:
                    body = resp.json()
                except Exception as e:  # noqa: BLE001
                    raise Aria2Error("aria2 RPC 返回不是 JSON（%s）" % type(e).__name__)
                if isinstance(body, dict) and body.get("error"):
                    err = body["error"]
                    raise Aria2Error("aria2 拒绝执行 %s：%s"
                                     % (method, str(err.get("message") or err)[:200]))
                if not isinstance(body, dict) or "result" not in body:
                    raise Aria2Error("aria2 RPC 返回缺少 result 字段")
                return body["result"]
            # ConnectError：指数退避 1s/2s + 抖动（REDUN-02）
            if attempt < retries:
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.5))
        raise Aria2Error(last_err + "（重试 %d 次仍失败）" % retries)

    # ---------------- 探测 ----------------
    async def get_version(self) -> str:
        """连通性 + 版本探测（返回版本号字符串）。"""
        v = await self._call("getVersion")
        return str((v or {}).get("version") or "unknown")

    async def check(self) -> tuple[bool, str]:
        """探测 RPC 可用性。返回 (ok, detail)——ok=False 时 detail 是开启指引。"""
        try:
            ver = await self.get_version()
            return True, "aria2 %s" % ver
        except Aria2Error as e:
            if "连不上" in str(e):
                dl_dir = str(getattr(Config, "DL_DIR", "") or "D:/Downloads")
                return False, start_hint(self.url, self.secret, dl_dir)
            return False, str(e)

    # ---------------- 任务操作 ----------------
    async def add_uri(self, uris: list[str], options: dict | None = None) -> str:
        """HTTP/FTP/磁力下载。返回 gid。"""
        if not uris:
            raise Aria2Error("下载链接不能为空")
        return str(await self._call("addUri", uris, options or {}))

    async def add_torrent(self, torrent: str | Path, options: dict | None = None) -> str:
        """本地 .torrent 文件下载（读文件 → base64 → addTorrent）。返回 gid。"""
        p = Path(torrent)
        try:
            raw = p.read_bytes()
        except Exception as e:  # noqa: BLE001
            raise Aria2Error("读不了种子文件 %s（%s）" % (p, type(e).__name__))
        b64 = base64.b64encode(raw).decode("ascii")
        return str(await self._call("addTorrent", b64, [], options or {}))

    async def tell_status(self, gid: str, keys: list[str] | None = None) -> dict:
        """任务状态（keys 限定返回字段，省带宽；默认关键字段集）。"""
        ks = keys or ["gid", "status", "totalLength", "completedLength",
                      "downloadSpeed", "uploadSpeed", "files", "dir",
                      "bittorrent", "errorMessage", "followingGid"]
        return dict(await self._call("tellStatus", gid, ks) or {})

    async def get_files(self, gid: str) -> list[dict]:
        return list(await self._call("getFiles", gid) or [])

    async def change_option(self, gid: str, options: dict) -> None:
        await self._call("changeOption", gid, options)

    async def tell_active(self) -> list[dict]:
        return list(await self._call("tellActive") or [])

    async def tell_waiting(self, offset: int = 0, num: int = 50) -> list[dict]:
        return list(await self._call("tellWaiting", offset, num) or [])

    async def tell_stopped(self, offset: int = 0, num: int = 50) -> list[dict]:
        return list(await self._call("tellStopped", offset, num) or [])

    async def pause(self, gid: str) -> None:
        await self._call("pause", gid)

    async def unpause(self, gid: str) -> None:
        await self._call("unpause", gid)

    async def remove(self, gid: str) -> None:
        """移除任务（不删文件）。"""
        await self._call("remove", gid)

    async def force_remove(self, gid: str) -> None:
        await self._call("forceRemove", gid)

    async def get_global_stat(self) -> dict:
        return dict(await self._call("getGlobalStat") or {})

    async def purge_download_result(self) -> None:
        """清理已完成/出错的旧记录（tellStopped 不无限增长，OPS-02）。"""
        await self._call("purgeDownloadResult")

    # ---------------- 高层：任务列表（统一视图） ----------------
    async def list_tasks(self) -> list[dict]:
        """active + waiting + 最近 stopped（complete/error）合并列表。"""
        try:
            active = await self.tell_active()
            waiting = await self.tell_waiting()
            stopped = await self.tell_stopped(0, 20)
        except Aria2Error:
            raise  # 显式上抛，不返回空列表冒充成功（禁止兜底）
        return [*active, *waiting, *stopped]


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
aria2_client = Aria2Client()
