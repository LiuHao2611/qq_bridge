# -*- coding: utf-8 -*-
"""smarthome —— M8 智能家居中枢（2026-10-21，HAQBot 同构先例）。

组成：
  ha.py        Home Assistant 桥接核心：REST 客户端 + 设备白名单 +
               场景脚本映射 + Conversation Agent + HA 管家 prompt 规范
  webhook.py   Webhook 反向告警接收器（HA 自动化 → 回调 → QQ 群推送）
  camera.py    摄像头快照（camera_proxy JPEG）/ 短视频（ffmpeg 拉流截段）

需求（roadmap §M8）：自然语言控灯/空调/开关/场景脚本；高敏设备（门锁/
摄像头）仅主人；HA 自动化反向告警；「看看客厅」出画面；ASR 语音指令
（复用 M4 multimodal.audio.transcribe——不重复造轮子）。

安全总原则（对齐 ops/ 的严格度）：
  - 设备白名单双保险（HA 侧配错也拦）；
  - 高敏设备仅主人（调用方判，本模块提供 is_sensitive）；
  - 场景映射本地确定性快路径（不等 LLM）；
  - 所有外部调用显式超时；失败显式上抛，禁静默（CLOSE-02）；
  - 禁止降级：要视频给不了就明说，不回退成静图。

依赖方向：本包只依赖 ..config，不 import nonebot / 不 import 主包——
发送出口（QQ）与 webhook 回调全部由 __init__.py 注入，可裸单测。
"""
from .ha import (SYSTEM_PROMPT, HAClient, HaError, HaEvent, build_attr_payload,
                 ha_client, parse_scene_map, parse_whitelist)
from .webhook import (EventCallback, WebhookReceiver, create_app,
                      format_event_text, parse_event)
from .camera import CameraError, CameraGrab

__all__ = [
    # ha
    "HAClient", "ha_client", "HaError", "HaEvent", "parse_whitelist",
    "parse_scene_map", "build_attr_payload", "SYSTEM_PROMPT",
    # webhook
    "create_app", "parse_event", "format_event_text", "WebhookReceiver",
    "EventCallback",
    # camera
    "CameraGrab", "CameraError",
]
