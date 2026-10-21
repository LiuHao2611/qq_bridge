# -*- coding: utf-8 -*-
"""media（媒体回传，设计文档 §3.7 / §5.2 / §5.3 / §7.3）。

职责：把平台产出的图片/视频转成 onebot 消息段并回传。

关键接口：send_text(ctx, text) / send_image(ctx, file) / send_video(ctx, file)
  其中 ctx = {"user_id": int, "group_id": int(私聊为 0)}

验收（§3.7）：
  - 图片 URL 直发成功；
  - 视频超大时改发下载链接 + 封面（§7.3）。

实现要点：
  - 用 nonebot 的 MessageSegment 构造消息段（等价于文档里的 CQ 码，
    但由框架处理 file 字段的转义，避免手工拼 CQ 码踩坑）；
  - 回传前加 1~3s 随机延迟（§9 风控 #4）。
"""
from __future__ import annotations

import asyncio
import base64
import random

import httpx
from nonebot.adapters.onebot.v11 import MessageSegment

from ..config import Config, get_logger, jlog
from ..connector import connector
from .. import history

log = get_logger()


async def _download_bytes(url: str) -> bytes:
    """P1 回退：bridge 自下载图片 bytes（仅 http/https）。失败返回空。"""
    if not (url.startswith("http://") or url.startswith("https://")):
        return b""
    try:
        async with httpx.AsyncClient(timeout=30.0) as cli:
            resp = await cli.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as e:
        log.warning(jlog("image_download_failed", detail=str(e)[:160]))
    return b""


def _delay() -> float:
    lo, hi = Config.REPLY_DELAY_MIN, Config.REPLY_DELAY_MAX
    if hi <= lo:
        return max(0.0, lo)
    return random.uniform(lo, hi)


def _target(ctx: dict) -> tuple[str, dict]:
    """根据 ctx 决定动作与基础参数（§5.2）。"""
    group_id = int(ctx.get("group_id") or 0)
    user_id = int(ctx.get("user_id") or 0)
    if group_id:
        return "send_group_msg", {"group_id": group_id}
    return "send_private_msg", {"user_id": user_id}


async def _send(ctx: dict, message: str, at_user: bool = False) -> None:
    action, params = _target(ctx)
    if at_user and int(ctx.get("group_id") or 0):
        # 群聊回传时 @发起人（§5.2 示例）
        message = str(MessageSegment.at(int(ctx["user_id"]))) + " " + message
    params = dict(params, message=message)

    await asyncio.sleep(_delay())
    ret = await connector.call_action(action, params)
    log.info(jlog("media_sent", action=action, msg_len=len(message)))

    # 对话历史（旁路，2026-09-07）：机器人自己发出去的也要进历史，
    # 否则页面只有用户单边发言，不像聊天记录。发送成功后才记。
    # 2026-09-07：回传 OneBot 的 message_id，使「撤回」功能可定位机器人自己发的消息。
    try:
        bot = connector.first_bot()
        self_id = str(getattr(bot, "self_id", "") or "")
        msg_id = (ret or {}).get("message_id") if isinstance(ret, dict) else None
        await asyncio.to_thread(history.record_outgoing, ctx, params.get("message") or "", self_id, int(msg_id or 0))
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("history_out_failed", error=type(e).__name__, detail=str(e)[:120]))


# ---- 双轨并行：非 NapCat 通道交给对应 Channel 实现 ----
# （docs/qq_official_bot_migration_plan.md §6.5）
def _foreign_channel(ctx: dict) -> str:
    """返回非 NapCat 的通道名；NapCat（或老调用点没带 channel 字段）返回空串。

    缺省即 "napcat"：保证所有既有调用点（历史/监控/队列通知）行为完全不变。
    """
    if not isinstance(ctx, dict):
        return ""
    ch = str(ctx.get("channel") or "napcat").strip().lower()
    return "" if ch == "napcat" else ch


async def _send_via_channel(ctx: dict, out) -> None:
    """走通道层发送。失败只记日志不抛——与 NapCat 侧尽力而为的语义一致。"""
    from ..channel import get_channel

    ch = _foreign_channel(ctx)
    try:
        ret = await get_channel(ch).send(ctx, out)
    except Exception as e:  # noqa: BLE001 — CLOSE-02：记全，不让异常冒到事件循环
        log.error(jlog("media_channel_send_failed", channel=ch,
                       error=type(e).__name__, detail=str(e)[:200]))
        return
    if not ret.get("ok"):
        log.warning(jlog("media_channel_send_rejected", channel=ch,
                         reason=str(ret.get("reason", ""))[:160]))


async def send_text(ctx: dict, text: str, at_user: bool = False) -> None:
    if _foreign_channel(ctx):
        from ..channel.base import Outgoing

        await _send_via_channel(ctx, Outgoing(text=str(text or ""), at_user=at_user))
        return
    await _send(ctx, str(text or ""), at_user=at_user)


async def send_image(ctx: dict, file: str, at_user: bool = False) -> None:
    """file 支持 URL / 本地路径 / base64://（§5.3）。

    P1 增强：先直发 URL；若 NapCat 下载失败（跨进程拉取 7788/file 异常），
    改由 bridge 自下载转 base64:// 回退，解耦且报错精确（CLOSE-02）。
    """
    if _foreign_channel(ctx):
        from ..channel.base import Outgoing

        await _send_via_channel(ctx, Outgoing(image_urls=[file], at_user=at_user))
        return
    try:
        await _send(ctx, str(MessageSegment.image(file)), at_user=at_user)
    except Exception as e:
        log.warning(jlog("image_url_send_failed", detail=str(e)[:160]))
        data = await _download_bytes(file)
        if data:
            b64 = "base64://" + base64.b64encode(data).decode("ascii")
            log.info(jlog("image_base64_fallback", bytes=len(data)))
            await _send(ctx, str(MessageSegment.image(b64)), at_user=at_user)
        else:
            raise


async def send_video(ctx: dict, file: str, cover: str = "", at_user: bool = False) -> None:
    """视频回传。file 为 URL/路径；cover 为封面图（可选）。"""
    if _foreign_channel(ctx):
        from ..channel.base import Outgoing

        await _send_via_channel(ctx, Outgoing(
            video=file, image_urls=[cover] if cover else [], at_user=at_user))
        return
    msg = str(MessageSegment.video(file))
    if cover:
        msg += str(MessageSegment.image(cover))
    await _send(ctx, msg, at_user=at_user)


async def send_file(ctx: dict, file: str, at_user: bool = False) -> None:
    """发送本地文件给 QQ（M6 文件互传：PC → QQ）。

    OneBot `file` 段支持本地路径 / URL / base64。这里优先传本地路径——
    NapCat 直接读磁盘，**不受 WS 单帧 16MB 限制**（仅 base64/URL 模式受此限）。
    超 500MB 拒绝（防卡顿/协议异常）。
    """
    if _foreign_channel(ctx):
        from ..channel.base import Outgoing

        await _send_via_channel(ctx, Outgoing(file=file, at_user=at_user))
        return
    import os

    try:
        size = os.path.getsize(file) if os.path.isfile(file) else 0
    except Exception:  # noqa: BLE001
        size = 0
    if size > 500 * 1024 * 1024:
        await send_text(ctx, "这文件 %d MB 太大了，本喵不敢往 QQ 塞（怕把通道撑爆）。"
                        % (size // 1024 // 1024), at_user=at_user)
        return
    try:
        await _send(ctx, str(MessageSegment.file(file)), at_user=at_user)
    except Exception as e:  # noqa: BLE001 — CLOSE-02
        log.warning(jlog("file_send_failed", detail=str(e)[:160]))
        await send_text(ctx, "文件发不出去（%s）。可能路径不对或 QQ 端不支持该类型。"
                        % type(e).__name__, at_user=at_user)


async def send_video_link(ctx: dict, link: str, cover: str = "", at_user: bool = False) -> None:
    """§7.3：视频超限（或端点暂不支持直传）时改发下载链接 + 封面。

    官方通道特殊处理：**不贴 URL** —— 平台禁止发送未预配置的链接
    （错误码 40054010），贴了整条消息发不出去，因此只给提示。
    """
    if _foreign_channel(ctx):
        from ..channel.base import Outgoing

        await _send_via_channel(ctx, Outgoing(
            text="视频画好了，去「任务中心」页面看吧（QQ 官方通道不让贴链接，见谅）。",
            image_urls=[cover] if cover else [], at_user=at_user))
        return
    msg = "视频画好了，下载链接：%s（慢点下，别又来烦本喵）。" % link
    if cover:
        msg += str(MessageSegment.image(cover))
    await _send(ctx, msg, at_user=at_user)


async def send_images(ctx: dict, files: list, at_user: bool = False) -> None:
    """多张图逐张发送（§7.3：多张建议拼图或逐张）。"""
    for f in files or []:
        await send_image(ctx, f, at_user=at_user)


async def send_record(ctx: dict, data: bytes, at_user: bool = False) -> None:
    """发送语音消息（M4 TTS 出口，2026-10-20）。

    data：音频 bytes（mp3，由 multimodal.audio 合成）。走 base64:// 直发
    （与 send_pic 同款理由：内存产物不经中转最稳）。空数据显式 ValueError。
    """
    if not data:
        raise ValueError("send_record 收到空音频数据")
    if _foreign_channel(ctx):
        # 官方通道不支持语音段 → 发文字提示，不静默丢弃音频
        await send_text(ctx, "（语音合成好了，但当前通道发不了语音消息）",
                        at_user=at_user)
        return
    payload = "base64://" + base64.b64encode(data).decode("ascii")
    await _send(ctx, str(MessageSegment.record(payload)), at_user=at_user)
    log.info(jlog("media_record_sent", bytes=len(data)))


async def send_pic(ctx: dict, data: bytes, at_user: bool = False) -> None:
    """发送**机器人自绘的图片**（pic/ 渲染出的 PNG bytes）。

    为什么不落盘再发 URL：自绘图是**内存产物**，走 7788/file 需要主项目中转，
    而 NapCat 拉本地 URL 偶发失败（项目已有踩坑记录）。直接 base64:// 直发最稳，
    与表情包的做法一致（`maybe_send_sticker`）。
    """
    if not data:
        raise ValueError("send_pic 收到空图片数据")
    if _foreign_channel(ctx):
        from ..channel.base import Outgoing

        # 自绘图是内存产物 → 直接给 bytes，由官方通道走本地文件上传
        await _send_via_channel(ctx, Outgoing(images=[data], at_user=at_user))
        return
    payload = "base64://" + base64.b64encode(data).decode("ascii")
    await _send(ctx, str(MessageSegment.image(payload)), at_user=at_user)
    log.info(jlog("media_pic_sent", bytes=len(data)))
