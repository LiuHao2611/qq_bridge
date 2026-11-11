# -*- coding: utf-8 -*-
"""QQ 官方「素材管理」客户端：把图片字节上传成可在 Markdown 内嵌的引用。

为什么需要（2026-11-11，用户「图片要和按钮一起发」）：
  官方群消息的 ``msg_type`` 互斥——富媒体图片走 7、markdown+按钮走 2，
  一条消息无法既带 ``file_image`` 又带 ``keyboard``。要把「一张图 + 多个按钮」
  合成**一条**消息，唯一官方途径是：把图片先上传到素材库，换成
  ``qqbot-media://image/v1/<token>`` 引用，内嵌进 markdown content：
      ![ #800px #1019px](qqbot-media://image/v1/<token>)
  然后 markdown content 与 keyboard 同段下发（msg_type=2）。

官方素材管理 API（bot.q.qq.com/.../material-management.html，三步）：
  1. ``POST /v2/media-images/prepare``  申请上传 → upload_id + presigned_url
  2. ``PUT <presigned_url>``            直传图片二进制 → ETag(md5)
  3. ``POST /v2/media-images/complete`` 完成 → url(qqbot-media://) + image_id
  引用：``![说明 #宽px #高px](<url>)``；单机器人上限 100 张、滑动 90 天过期。

适配器 nonebot-adapter-qq 1.7.2 **未封装** material-images API，故此处裸调：
  * prepare/complete 走 ``bot._request(Request(...))`` —— 自带 access_token
    鉴权且过期自动刷新（适配器内部就这么用）；URL 用
    ``bot.adapter.get_api_base().joinpath(...)``；
  * PUT 直传用 ``bot.put_upload_part(presigned_url=..., data=...)`` —— 适配器
    现成封装（POST 上传 COS 分片时内部用的就是它）。
不引入新网络依赖；PIL 仅用于解析图片宽高（容器已有 pillow）。

降级契约：本模块任何失败都抛 :class:`MaterialUploadError`，由 ``official.py::send``
捕获后**回退原「图片条 + 按钮条」两条发送**，绝不让素材上传拖垮主卡片，也绝不
静默吞错（记 warning + 原因）。
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict

try:  # 正常包内 import
    from ..config import get_logger, jlog
except (ImportError, ValueError):  # 单文件直载（离线单测）时兜底到纯 logging
    import logging

    def get_logger():  # type: ignore[no-redef]
        return logging.getLogger("qq_bridge.media_asset")

    def jlog(evt, **kw):  # type: ignore[no-redef]
        return {"event": evt, **kw}

log = get_logger()

#: 单机器人素材上限 100 张，留 8 张安全余量
MAX_MATERIALS = 92


class MaterialUploadError(RuntimeError):
    """素材上传任一步失败。上层据此降级回两条发送，不静默吞。"""


class MaterialStore:
    """md5 → qqbot-media 引用 的缓存 + LRU 容量管理。

    缓存命中直接复用（官方素材 90 天内被引用即续期，重复图不必重传）。
    LRU：超过 MAX_MATERIALS 时删最久未用的（调 ``DELETE /v2/media-images/{
    image_id}/delete`` 释放配额），删除失败只记日志不影响本次发送。
    """

    def __init__(self, max_items: int = MAX_MATERIALS):
        self.max_items = max(1, int(max_items))
        # md5 -> {"url":..., "width":..., "height":..., "image_id":...}
        self._items: OrderedDict[str, dict] = OrderedDict()

    def get(self, md5: str) -> dict | None:
        if md5 not in self._items:
            return None
        self._items.move_to_end(md5)  # LRU：命中即最近使用
        return self._items[md5]

    def put(self, md5: str, item: dict) -> None:
        self._items[md5] = item
        self._items.move_to_end(md5)

    def _evict_candidates(self) -> list[dict]:
        """超出容量时最久未用的条目（含 image_id 供删除）。"""
        excess = len(self._items) - self.max_items
        if excess <= 0:
            return []
        evicted = []
        for _ in range(excess):
            md5, item = self._items.popitem(last=False)
            evicted.append(item)
        return evicted

    def __len__(self) -> int:
        return len(self._items)


#: 进程级单例（官方 Bot 单连接，素材库按 AppID 全局）
_store = MaterialStore()


def _new_request(method: str, url, **kw):
    """构造 ``nonebot.drivers.Request``。

    收口到本函数、而非在三处端点里各写 ``from nonebot.drivers import Request``：
      * 生产：延迟导入（适配器缺失时不拖垮模块顶层导入）；
      * 测试：monkeypatch 本函数即可注入假 Request，**不污染 sys.modules**，
        宿主（无 nonebot）与容器（真 nonebot）共用同一套测试代码。
    """
    from nonebot.drivers import Request  # noqa: PLC0415

    return Request(method, url, **kw)


def image_size(data: bytes) -> tuple[int, int]:
    """从图片字节解析 (宽, 高)；失败抛 MaterialUploadError。"""
    try:
        from io import BytesIO

        from PIL import Image  # noqa: PLC0415 — 容器已装 pillow

        with Image.open(BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("image_size_failed: %s" % type(e).__name__) from e


async def _prepare(bot, file_name: str, data: bytes) -> dict:
    """POST /v2/media-images/prepare → {upload_id, presigned_url, ...}。"""
    base = bot.adapter.get_api_base()
    req = _new_request(
        "POST",
        base.joinpath("v2", "media-images", "prepare"),
        json={
            "file_name": file_name,
            "file_size": len(data),
            "content_type": "image/png",
            "md5": hashlib.md5(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest(),
        },
    )
    try:
        ret = await bot._request(req)
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("prepare_failed: %s" % type(e).__name__) from e
    if not isinstance(ret, dict) or not ret.get("upload_id") or not ret.get("presigned_url"):
        raise MaterialUploadError("prepare_bad_response: missing upload_id/presigned_url")
    return ret


async def _put_bytes(bot, presigned_url: str, data: bytes) -> str:
    """PUT 预签名 URL 直传字节 → 返回 ETag(md5)。"""
    try:
        # 适配器现成封装：Request("PUT", url, content=data) 经 _request 发出。
        # 它不返回响应，ETag 用我们自己的 md5（官方 complete 以 COS 实际 ETag
        # 比对，而 COS 对单块 PUT 的 ETag 就是内容 md5）。
        await bot.put_upload_part(presigned_url=presigned_url, data=data)
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("put_failed: %s" % type(e).__name__) from e
    return hashlib.md5(data).hexdigest()


async def _complete(bot, upload_id: str, md5_hex: str) -> dict:
    """POST /v2/media-images/complete → {url, image_id, ...}。"""
    base = bot.adapter.get_api_base()
    req = _new_request(
        "POST",
        base.joinpath("v2", "media-images", "complete"),
        json={"upload_id": upload_id, "md5": md5_hex},
    )
    try:
        ret = await bot._request(req)
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("complete_failed: %s" % type(e).__name__) from e
    if not isinstance(ret, dict) or not ret.get("url"):
        raise MaterialUploadError("complete_bad_response: missing url")
    return ret


async def _delete(bot, image_id: str) -> None:
    """DELETE /v2/media-images/{image_id}/delete（LRU 释放配额），失败只记日志。"""
    try:
        base = bot.adapter.get_api_base()
        # 官方素材删除是 DELETE /v2/media-images/{image_id}/delete（不是 POST）。
        # 2026-11-11 静态核对修正：误用 POST 会拿 404/405，LRU 配额释放不掉。
        req = _new_request("DELETE", base.joinpath("v2", "media-images", image_id, "delete"))
        await bot._request(req)
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("material_lru_delete_failed", error=type(e).__name__,
                         image_id=str(image_id)[:24]))


async def ensure_material(bot, data: bytes, file_name: str = "card.png") -> dict:
    """确保图片字节在素材库有引用，返回 ``{"url",width",height",md5"}``。

    缓存命中不上传；未命中走 prepare→PUT→complete 三步。任一失败抛
    :class:`MaterialUploadError`（由上层降级，不静默吞）。成功后做 LRU 淘汰。
    """
    if not data:
        raise MaterialUploadError("empty_image_bytes")
    md5 = hashlib.md5(data).hexdigest()
    cached = _store.get(md5)
    if cached:
        return {**cached, "md5": md5, "cached": True}

    width, height = image_size(data)
    prepared = await _prepare(bot, file_name, data)
    md5_hex = await _put_bytes(bot, prepared["presigned_url"], data)
    completed = await _complete(bot, prepared["upload_id"], md5_hex)
    item = {
        "url": completed["url"],
        "image_id": str(completed.get("image_id") or ""),
        "width": width,
        "height": height,
    }
    _store.put(md5, item)
    for old in _store._evict_candidates():
        if old.get("image_id"):
            await _delete(bot, old["image_id"])
    log.info(jlog("material_uploaded", md5=md5[:12], width=width, height=height,
                  cached_store=len(_store)))
    return {**item, "md5": md5, "cached": False}


def markdown_image(url: str, width: int, height: int, alt: str = "card") -> str:
    """构造官方 Markdown 内嵌图片语法：``![alt #宽px #高px](url)``。"""
    return "![%s #%dpx #%dpx](%s)" % (alt, int(width), int(height), url)


def reset_store() -> None:
    """清空缓存（仅供测试隔离）。"""
    _store._items.clear()
