# -*- coding: utf-8 -*-
"""紫卡「当前这张图」短 TTL 缓存（2026-11-11，仅官方副本）。

## 为什么需要它

QQ 官方「消息按钮点击」回调（``INTERACTION_CREATE``）只回传 ``button_data``
一个口令字符串，伪事件**不带原图**。所以紫卡分析结果图上的「全面分析」按钮
被点击时，服务端拿不到用户刚发的那张紫卡——除非把它缓存下来。

用户第 7 次修改原话：

    「别让我重新发紫卡，我点全面分析后，直接重新当前紫卡，输出全面分析卡片」

即：点「全面分析」按钮 → **服务端复用当前紫卡原图 → 直接出 655×1920 全面
分析卡**，不再要求用户重发截图。本模块就是这块「当前紫卡原图」的缓存。

## 设计要点（trust but verify 友好）

* **key 与 ``_riven_wait_key`` 同源**：调用方（game_tools）用
  ``(_sk(event), user_id)`` 作 key。发图事件与按钮伪事件经同一个
  ``OfficialLikeEvent.__init__`` + ``pseudo_id`` 派生，两侧 key 逐字节相同，
  天然跨群 / 跨用户隔离（A 群 B 用户绝读不到 C 群 D 用户的图）。
* **存原图 bytes 而非 parsed**：``riven_img_parse(data)`` 与发图链路的
  ``riven_img_analyze_full(data)`` 走**同一个** ``_parse_ocr(text, boxes,
  img_data)`` + rank badge 覆盖，点击时重新 OCR 与发图时同源同口径、零编造；
  且无需改 67KB 业务大文件 ``riven.py`` 的返回签名（业务大文件零改原则）。
* **短 TTL + 一次性消费**： TTL 覆盖「出分析卡 → 用户看到 → 点按钮」窗口，
  取一次即失效（防止重复点击重复出卡 / 占主动额度）。
* **有上限 + 过期懒清理**：防长跑进程内存膨胀；字典按插入序，超上限淘汰最旧。
* **零依赖**：纯标准库，不 import nonebot / 业务模块，宿主可独立真跑；时钟
  可注入（``now`` 参数）供 TTL 断言，无需 sleep。
* **失败不静默**：命中/过期/未命中都是明确返回值，由调用方记日志并兜底。
"""
from __future__ import annotations

import time
from typing import Any

#: 缓存有效期（秒）。覆盖「分析卡发出 → 用户阅读 → 点全面分析」的交互窗口；
#: 与 _RIVEN_WAIT_TTL(90s) 同量级但略宽，避免用户慢一点点就失效又要重发图。
RIVEN_CACHE_TTL = 180.0

#: 最多缓存多少「群+用户」的紫卡，超出淘汰最旧（防内存膨胀）。
RIVEN_CACHE_MAX = 128

# key -> (raw_image_bytes, expire_at_monotonic_ts)
_cache: dict[tuple, tuple[bytes, float]] = {}


def _now() -> float:
    return time.monotonic()


def _evict_expired(now: float) -> None:
    for k in [k for k, (_, exp) in _cache.items() if exp <= now]:
        _cache.pop(k, None)


def put(key: tuple, data: bytes, *, ttl: float = RIVEN_CACHE_TTL,
        now: float | None = None) -> bool:
    """缓存 key 对应的当前紫卡原图。返回是否成功写入。

    空 data / 非 tuple key 一律拒绝（不编造、不缓存垃圾）。写入前懒清过期项，
    超上限按插入序淘汰最旧。
    """
    if not isinstance(key, tuple) or not data:
        return False
    t = _now() if now is None else now
    _evict_expired(t)
    if key in _cache:
        del _cache[key]  # 同 key 覆盖：刷新插入序，避免误淘汰其它会话
    while len(_cache) >= RIVEN_CACHE_MAX:
        oldest = next(iter(_cache), None)
        if oldest is None:
            break
        _cache.pop(oldest, None)
    _cache[key] = (bytes(data), t + ttl)
    return True


def get(key: tuple, *, now: float | None = None) -> bytes | None:
    """取出并**消费** key 对应的原图；未命中 / 已过期均返回 None。

    一次性消费：取走即从缓存删除。TTL 判定用注入或单调时钟，不依赖 sleep。
    """
    if not isinstance(key, tuple):
        return None
    t = _now() if now is None else now
    item = _cache.pop(key, None)
    if item is None:
        return None
    data, exp = item
    if exp <= t:
        return None
    return data


def peek(key: tuple, *, now: float | None = None) -> bytes | None:
    """只读不消费地看一眼 key 是否命中（诊断 / 测试用）。过期返回 None。"""
    if not isinstance(key, tuple):
        return None
    t = _now() if now is None else now
    item = _cache.get(key)
    if item is None:
        return None
    data, exp = item
    return None if exp <= t else data


def clear() -> None:
    """清空缓存（测试隔离用）。"""
    _cache.clear()


def size(*, now: float | None = None) -> int:
    """当前有效条目数（会先懒清过期项）。"""
    _evict_expired(_now() if now is None else now)
    return len(_cache)
