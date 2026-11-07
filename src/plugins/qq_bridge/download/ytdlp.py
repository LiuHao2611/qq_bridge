# -*- coding: utf-8 -*-
"""download.ytdlp —— yt-dlp 视频下载封装（M7，2026-10-20）。

范式参考 StreamVault（2026-10-20 实读 README）：yt-dlp 作通用平台下载
引擎（B站/抖音/YouTube/Twitter 等全走它），下载完成经 Webhook 通知——
本项目通知出口 = 调用方注入的 on_complete 回调（QQ 发送）。

实现取舍：
  - yt-dlp 的 API 是同步阻塞的 → 用 asyncio.to_thread 包住，不堵事件循环；
  - 格式选择：默认 `bv*+ba/b`（最佳视频+最佳音轨合并，无音轨回落单文件），
    Config.DL_YTDLP_FORMAT 可覆盖；
  - 输出模板：`<DL_DIR>/%(title)s.%(ext)s`，标题清洗防路径穿越；
  - extract_info(download=False) 作「解析」模式（只拿元数据卡片）；
  - 失败显式抛 YtDlpError（CLOSE-02）：网络/地区限制/需登录/格式缺失
    都不静默，错误文本原样回给用户。

纪律：CLOSE-05 显式超时（socket timeout 走 Config.DL_YTDLP_TIMEOUT）；
本模块不 import nonebot / 不 import 主包：可裸单测（依赖注入 YoutubeDL）。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Callable

from ..config import Config, get_logger, jlog

log = get_logger()

#: 标题清洗：去掉路径穿越/保留 shell 安全的字符集（Windows + 中文）
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """标题 → 安全文件名（去路径分隔符与控制字符，限长保后缀空间）。"""
    s = _ILLEGAL.sub("_", (name or "").strip())
    s = s.strip(" .")  # Windows 不接受点/空格结尾
    return (s or "video")[:max_len]


class YtDlpError(Exception):
    """显式化的 yt-dlp 失败（message 即给上层的错误文本）。"""


class YtDlpDownloader:
    """yt-dlp 下载/解析封装（YoutubeDL 类可注入，便于单测）。"""

    def __init__(self, dl_dir: str | None = None, fmt: str | None = None,
                 ydl_cls: Any = None) -> None:
        self.dl_dir = Path(dl_dir if dl_dir is not None
                           else (getattr(Config, "DL_DIR", "") or "D:/Downloads"))
        self.fmt = fmt if fmt is not None else str(
            getattr(Config, "DL_YTDLP_FORMAT", "") or "bv*+ba/b")
        self._ydl_cls = ydl_cls  # 默认 None → 运行时 import yt_dlp

    def _resolve_cls(self):
        if self._ydl_cls is not None:
            return self._ydl_cls
        try:
            from yt_dlp import YoutubeDL
        except Exception as e:  # noqa: BLE001
            raise YtDlpError("yt-dlp 没装（%s）：pip install yt-dlp" % type(e).__name__)
        return YoutubeDL

    def _opts(self, download: bool, outtmpl: str | None = None) -> dict:
        timeout = float(getattr(Config, "DL_YTDLP_TIMEOUT", 120.0) or 120.0)
        opts: dict = {
            "format": self.fmt,
            "outtmpl": outtmpl or str(self.dl_dir / "%(title)s.%(ext)s"),
            "socket_timeout": int(timeout),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            # 播放列表默认只拿单条（防一个链接把整个收藏夹拖下来）
            "noplaylist": True,
        }
        if download:
            # 合并音视频需 ffmpeg（项目 build/ 已带，见 M5 合成链路）
            opts["merge_output_format"] = "mp4"
        return opts

    async def extract(self, url: str) -> dict:
        """只解析不下载：返回元数据（title/duration/uploader/thumbnail/…）。"""
        text = (url or "").strip()
        if not text:
            raise YtDlpError("链接不能为空")
        ydl_cls = self._resolve_cls()

        def _run() -> dict:
            with ydl_cls(self._opts(download=False)) as ydl:
                info = ydl.extract_info(text, download=False)
                return self._norm_info(info)

        try:
            return await asyncio.to_thread(_run)
        except YtDlpError:
            raise
        except Exception as e:  # noqa: BLE001 — yt-dlp 的 DownloadError 等
            raise YtDlpError("解析失败：%s" % self._friendly(e))

    async def download(self, url: str) -> dict:
        """下载视频，返回 {filepath, title, ...}。filepath 为最终文件。"""
        text = (url or "").strip()
        if not text:
            raise YtDlpError("链接不能为空")
        ydl_cls = self._resolve_cls()
        try:
            self.dl_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            raise YtDlpError("建不了下载目录 %s（%s）" % (self.dl_dir, type(e).__name__))

        def _run() -> dict:
            with ydl_cls(self._opts(download=True)) as ydl:
                info = ydl.extract_info(text, download=True)
                return self._norm_info(info, with_path=True)

        try:
            return await asyncio.to_thread(_run)
        except YtDlpError:
            raise
        except Exception as e:  # noqa: BLE001
            raise YtDlpError("下载失败：%s" % self._friendly(e))

    # ---------------- 内部 ----------------
    @staticmethod
    def _norm_info(info: Any, with_path: bool = False) -> dict:
        """yt-dlp info dict → 本项目统一结构（playlist 取第一条）。"""
        if info is None:
            raise YtDlpError("yt-dlp 什么也没返回")
        if isinstance(info, dict) and info.get("_type") == "playlist":
            entries = info.get("entries") or []
            if not entries:
                raise YtDlpError("这是个播放列表但里面没有条目")
            info = entries[0]
        if not isinstance(info, dict):
            raise YtDlpError("yt-dlp 返回结构异常（%s）" % type(info).__name__)
        out = {
            "title": str(info.get("title") or "未命名视频"),
            "duration": float(info.get("duration") or 0),
            "uploader": str(info.get("uploader") or info.get("channel") or ""),
            "thumbnail": str(info.get("thumbnail") or ""),
            "webpage_url": str(info.get("webpage_url") or ""),
            "ext": str(info.get("ext") or ""),
        }
        if with_path:
            fp = info.get("filepath") or info.get("_filename") or ""
            # 老版本 yt-dlp 用 requested_downloads 列表
            if not fp:
                reqs = info.get("requested_downloads") or []
                if reqs and isinstance(reqs, list):
                    fp = reqs[0].get("filepath") or ""
            if not fp:
                raise YtDlpError("下载器没给出输出文件路径")
            out["filepath"] = str(fp)
        return out

    @staticmethod
    def _friendly(e: Exception) -> str:
        """yt-dlp 异常 → 人话（保留原文关键信息，不静默）。"""
        msg = str(e)
        # yt-dlp 的 DownloadError 消息通常已含 "ERROR: xxx"
        return msg[:300] if msg else type(e).__name__


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
ytdlp_downloader = YtDlpDownloader()
