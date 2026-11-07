# -*- coding: utf-8 -*-
"""download —— M7 下载媒体中心（2026-10-20）。

组成：
  aria2.py      aria2 JSON-RPC 客户端（httpx 裸协议，不引 aria2p）
  btselect.py   BT 智能选文件纯函数（DownloadBot largest/smart 范式）
  ytdlp.py      yt-dlp 下载/解析封装（asyncio.to_thread 包同步 API）
  manager.py    下载编排：链接分类 / BT 元数据等待+选文件时序 /
                 aria2 完成轮询 / bridge 侧登记（data/downloads.json）
  subscribe.py  订阅监控（yt-dlp flat playlist 抽象，StreamVault 范式）

依赖方向：本包只依赖 ..config 与 ..queue.DATA_DIR，不 import nonebot、
不 import 主包——发送/通知全部经回调注入（manager.on_complete、
monitor.on_new、monitor.submit），可裸单测。

前置条件（roadmap §10.4）：aria2 需以 RPC 模式运行（本机已装 aria2c
1.37.0，但 Bridge **不内置拉起进程**——未检测到 RPC 时显式告知开启命令，
尊重用户机器主权）；yt-dlp 为 Python 依赖已入项目 venv。
"""
from .aria2 import Aria2Client, Aria2Error, aria2_client
from .btselect import (SELECT_ALL, SELECT_LARGEST, SELECT_SMART,
                       apply_strategy, select_file_indices, to_aria2_select_option)
from .ytdlp import YtDlpDownloader, YtDlpError, ytdlp_downloader
from .manager import (KIND_HTTP, KIND_MAGNET, KIND_TORRENT, KIND_VIDEO,
                      DownloadError, DownloadManager, classify, download_manager)
from .subscribe import (SubscriptionError, SubscriptionMonitor, SubscriptionStore,
                        subscription_monitor, subscription_store)

__all__ = [
    "Aria2Client", "Aria2Error", "aria2_client",
    "SELECT_LARGEST", "SELECT_SMART", "SELECT_ALL",
    "select_file_indices", "to_aria2_select_option", "apply_strategy",
    "YtDlpDownloader", "YtDlpError", "ytdlp_downloader",
    "DownloadManager", "DownloadError", "download_manager", "classify",
    "KIND_HTTP", "KIND_MAGNET", "KIND_TORRENT", "KIND_VIDEO",
    "SubscriptionStore", "SubscriptionMonitor", "SubscriptionError",
    "subscription_store", "subscription_monitor",
]
