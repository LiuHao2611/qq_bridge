# -*- coding: utf-8 -*-
"""pic/gen_sticker.py —— 调主项目生图接口生成装饰贴纸/背景图。

方向铁律（memory 86812403）：qq_bridge **只经 HTTP 单向调用**主项目 7788，
绝不 import 主项目任何模块。这里复用主项目专用端点 `/api/v1/qq/image-generate`
（与页面 /t2i/generate 正交：本地 ComfyUI 忙时直接 409 拒绝，不取消别人任务）。

流程：
  1. POST {MANJU_API_BASE}/qq/image-generate  {prompt, ...} → data.task_id
  2. 轮询 GET {MANJU_API_BASE}/t2i/tasks/{task_id} 直到终态（OPS-04 看门狗）
  3. 从结果取图片 URL（data.task.result.images[].url，相对路径拼 MANJU_FILE_BASE）
  4. 下载 → 合规校验（复用 assets.validate_and_open）→ 落盘 → 返回文件名

不变量：所有落盘走 pic/assets.save（白名单文件名 + 合规校验），绝不裸写。
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..config import Config
from . import assets

_log = logging.getLogger("qq_bridge.pic.gen_sticker")


class GenStickerError(Exception):
    """调主项目生图生成贴纸失败。"""


async def generate(prompt: str, target: str = "sticker", timeout: float | None = None) -> str:
    """用主项目生图接口生成一张装饰图，落盘后返回资源文件名。

    :param prompt: 生图提示词（必填，非空）
    :param target: 用途，决定返回图后续绑到哪个主题字段——
                   "bg"=背景图 / "corner"=角落贴纸图 / "sticker"=背景贴纸图
    :param timeout: 总超时（秒），默认 Config.HTTP_TIMEOUT_IMG
    :returns: 资源文件名（uuid.ext）
    :raises GenStickerError: 任何失败（含主项目 409/无模板/超时）
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise GenStickerError("提示词不能为空")
    if target not in ("bg", "corner", "sticker"):
        raise GenStickerError("target 非法：%s" % target)

    _log.info("event=sticker_gen_start target=%s prompt=%q len=%d", target, prompt, len(prompt))
    base = Config.MANJU_API_BASE.rstrip("/")
    file_base = Config.MANJU_FILE_BASE.rstrip("/")
    total = float(timeout or Config.HTTP_TIMEOUT_IMG)
    deadline = asyncio.get_running_loop().time() + total

    # 1) 提交生图任务
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=total)) as cli:
            resp = await cli.post(base + "/qq/image-generate", json={"prompt": prompt})
    except httpx.HTTPError as e:
        raise GenStickerError("主项目不可达：%s" % type(e).__name__)
    if resp.status_code >= 400:
        raise GenStickerError("主项目拒绝（HTTP %s）" % resp.status_code)
    try:
        j = resp.json()
    except Exception:
        raise GenStickerError("主项目返回非 JSON")
    if j.get("code", 0) not in (0,):
        raise GenStickerError("主项目业务错误：%s" % (j.get("message") or j.get("data") or ""))
    task_id = (j.get("data") or {}).get("task_id")
    if not task_id:
        raise GenStickerError("主项目未返回 task_id")
    _log.info("event=sticker_gen_submitted task_id=%s", task_id)

    # 2) 轮询直到终态
    last_err = ""
    while True:
        if asyncio.get_running_loop().time() > deadline:
            raise GenStickerError("生成超时（>%d 秒）" % int(total))
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=30.0)) as cli:
                r2 = await cli.get(base + "/t2i/tasks/%s" % task_id)
        except httpx.HTTPError as e:
            last_err = type(e).__name__
            await asyncio.sleep(Config.POLL_INTERVAL)
            continue
        if r2.status_code >= 400:
            last_err = "HTTP %s" % r2.status_code
            await asyncio.sleep(Config.POLL_INTERVAL)
            continue
        try:
            tj = r2.json()
        except Exception:
            await asyncio.sleep(Config.POLL_INTERVAL)
            continue
        data = tj.get("data") or tj
        task = data.get("task") or data
        status = str((task.get("status") or ""))
        if status in ("succeeded", "success"):
            url = _first_image_url(task)
            _log.info("event=sticker_gen_done task_id=%s status=%s has_url=%s", task_id, status, bool(url))
            break
        if status in ("failed", "canceled", "cancelled"):
            _log.warning("event=sticker_gen_failed task_id=%s error=%s", task_id, task.get("error_message") or "未知")
            raise GenStickerError("主项目生图失败：%s" % (task.get("error_message") or "未知"))
        await asyncio.sleep(Config.POLL_INTERVAL)

    # 3) 下载结果图
    if not url:
        raise GenStickerError("生图成功但未返回图片地址")
    if url.startswith("/"):
        url = file_base + url
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=60.0),
                                     follow_redirects=True) as cli:
            r3 = await cli.get(url)
    except httpx.HTTPError as e:
        raise GenStickerError("结果图下载失败：%s" % type(e).__name__)
    if r3.status_code >= 400 or not r3.content:
        raise GenStickerError("结果图下载失败（HTTP %s）" % r3.status_code)

    # 4) 合规校验 + 落盘（复用 assets.save 的白名单文件名 + 校验）
    fname = assets.save(r3.content)
    _log.info("event=sticker_gen_saved target=%s filename=%s size=%d", target, fname, len(r3.content))
    return fname


def _first_image_url(task: dict) -> str:
    result = task.get("result") or {}
    images = result.get("images") or []
    for it in images:
        if isinstance(it, dict):
            u = it.get("url") or it.get("local_url") or ""
        else:
            u = str(it)
        if u:
            return u
    return ""
