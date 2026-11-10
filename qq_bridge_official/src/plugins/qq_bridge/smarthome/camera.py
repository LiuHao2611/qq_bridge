# -*- coding: utf-8 -*-
"""smarthome.camera —— 摄像头快照/短视频（M8 智能家居中枢，2026-10-21）。

需求（roadmap §M8）：「看看客厅」→ 拉流转短视频发送。

两档能力：
  - snapshot：HA /api/camera_proxy/<entity> 直接拿 JPEG 静图（快、稳，
    大多数「看看」场景够用）；
  - clip：ffmpeg 拉 RTSP/HA stream 地址截 N 秒短视频（要动图的场景）。

纪律：
  - camera entity 过白名单 + is_sensitive 由调用方判主人（摄像头属高敏）；
  - ffmpeg 缺失/拉流失败 → 显式 CameraError 说清原因，**不回退成静图**
    （禁止降级：用户要视频就给视频，给不了就明说，别偷偷给张图）；
  - 显式超时（CLOSE-05）。
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from ..config import Config, get_logger, jlog

log = get_logger()


class CameraError(Exception):
    """显式化的摄像头失败（越权/不可达/ffmpeg 缺失/超时）。"""


@dataclass
class CameraGrab:
    """摄像头操作入口（HA client 注入，复用其白名单与 _request）。"""

    ha: object  # HAClient（duck typing：有 _check/is_sensitive/_request）

    async def snapshot(self, entity_id: str) -> bytes:
        """拉摄像头当前静帧（HA camera_proxy → JPEG bytes）。"""
        e = self.ha._check(entity_id)  # type: ignore[attr-defined]
        import httpx

        url = "%s/api/camera_proxy/%s" % (self.ha.base_url, e)  # type: ignore[attr-defined]
        headers = self.ha._headers()  # type: ignore[attr-defined]
        timeout = float(getattr(Config, "SMARTHOME_HTTP_TIMEOUT", 10) or 10)
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, headers=headers)
        except Exception as ex:  # noqa: BLE001
            raise CameraError("拉摄像头画面失败（%s）：%s" % (type(ex).__name__, ex)) from ex
        if r.status_code >= 400:
            raise CameraError("摄像头 %s 取不到画面（HTTP %d）——查 entity 是否"
                              "在 HA 里在线" % (e, r.status_code))
        data = r.content
        if not data:
            raise CameraError("摄像头 %s 返回了空画面" % e)
        log.info(jlog("smarthome_camera_snapshot", entity=e, bytes=len(data)))
        return data

    async def clip(self, entity_id: str, seconds: int = 5,
                   out_path: str = "") -> str:
        """拉流截短视频（ffmpeg）。返回文件路径；ffmpeg 缺失即显式报错。"""
        e = self.ha._check(entity_id)  # type: ignore[attr-defined]
        if shutil.which("ffmpeg") is None:
            raise CameraError("本机没装 ffmpeg，拉不了视频流——装一个 ffmpeg "
                              "加进 PATH，或先用「看看」拿静帧")
        stream_url = await self._stream_url(e)
        seconds = max(1, min(int(seconds or 5), 30))  # 上限 30s，防拖死
        out = out_path or "%s/cam_%s.mp4" % (
            getattr(Config, "PC_UPLOAD_DIR", ".") or ".", e.replace(".", "_"))
        cmd = ["ffmpeg", "-y", "-i", stream_url, "-t", str(seconds),
               "-c", "copy", out]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=seconds + 20)
        except asyncio.TimeoutError as ex:
            raise CameraError("拉流超时（%ds）——摄像头流地址可能不对或网络不通"
                              % (seconds + 20)) from ex
        except Exception as ex:  # noqa: BLE001
            raise CameraError("ffmpeg 执行失败（%s）：%s"
                              % (type(ex).__name__, ex)) from ex
        if proc.returncode != 0:
            detail = (err or b"")[-200:].decode("utf-8", "ignore")
            raise CameraError("ffmpeg 拉流失败（exit %d）：%s——RTSP/流地址确认在"
                              "HA 里能播" % (proc.returncode, detail))
        log.info(jlog("smarthome_camera_clip", entity=e, seconds=seconds, out=out))
        return out

    async def _stream_url(self, entity_id: str) -> str:
        """优先 HA 实体属性的 stream_source；取不到则诚实报错。"""
        st = await self.ha.get_state(entity_id)  # type: ignore[attr-defined]
        attrs = (st or {}).get("attributes") or {}
        url = attrs.get("stream_source") or ""
        if not url:
            raise CameraError("摄像头 %s 没有 stream_source 属性（HA 需配 "
                              "stream/camera 集成且摄像头支持流）" % entity_id)
        return str(url)
