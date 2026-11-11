# -*- coding: utf-8 -*-
"""QQ 官方机器人通道实现（docs/qq_official_bot_migration_plan.md §6.4~§6.7）。

适配器：nonebot-adapter-qq（NoneBot 官方维护，MIT）。

已核对的适配器 1.7.2 事实（与平台文档/GitHub README 有差异，勿照抄旧资料）：
  * 事件类名是**驼峰**：C2CMessageCreateEvent / GroupMessageCreateEvent /
    GroupAtMessageCreateEvent（GROUP_AT_MESSAGE_CREATE 是平台事件名，不是类名）；
  * 身份是 **openid**：群=author.member_openid、C2C=author.user_openid、群=event.group_openid；
  * **上传已内置**：post_group_upload 自动完成 prepare→并发 PUT→part_finish→合并，
    业务侧只需拼 `MessageSegment.file_image(bytes, file_name=...)`；
    ⚠️ file_name 必须给，否则走 URL 模式（本地图会失败）。
  * 被动回复靠 msg_id + msg_seq：**群聊 5 分钟有效、每条消息最多回 5 次**；
  * 主动消息日上限 1000 条/群 —— 本模块用 limiter 自限，避免撞平台 40034100。

日志铁律：平台调用必须记 request/response/error/duration_ms（用户要求），
但**绝不**记录 AppSecret / access_token（LOG-04）。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import Config, get_logger, jlog
from .base import Channel, Outgoing, make_ctx, current_official_ctx
from .limiter import official_limiter

log = get_logger()

#: 官方通道身份前缀：与 NapCat 的 "u:%d"/"g:%d" 明确区分，避免串号
USER_PREFIX = "official:u:"
GROUP_PREFIX = "official:g:"

#: 图片下载超时（主项目 7788 在本机，给足但绝不无限等，CLOSE-05）
_DOWNLOAD_TIMEOUT = 30.0


def _import_qq():
    """延迟导入官方适配器。

    为什么延迟：适配器缺失（未安装）时**绝不能**拖垮 NapCat 通道，
    因此所有官方相关的导入都放在函数内，失败时由调用方降级处理。
    """
    from nonebot.adapters.qq import (  # noqa: PLC0415
        Bot as QQBot,
    )
    from nonebot.adapters.qq import (  # noqa: PLC0415
        C2CMessageCreateEvent,
        GroupAtMessageCreateEvent,
        GroupMessageCreateEvent,
    )
    from nonebot.adapters.qq import (  # noqa: PLC0415
        Message as QQMessage,
    )
    from nonebot.adapters.qq import (  # noqa: PLC0415
        MessageSegment as QQSegment,
    )
    # 2026-11-11 修复 P0：本函数必须返回 6 元组。上一轮插入按钮函数时误把本
    # return 挤到 _build_button_segment 尾部（不可达 + 引用未定义名），导致
    # normalize()/send() 解包 None → TypeError → 官方 Bot 全瘫。
    return (
        QQBot, QQMessage, QQSegment,
        C2CMessageCreateEvent,
        GroupMessageCreateEvent,
        GroupAtMessageCreateEvent,
    )


def _build_button_segment(cmd: str, _QQMessage, _QQSegment):
    """把当前口令变成一条「快捷命令按钮」消息段（markdown + keyboard 同段）。

    返回 ``(message, btn_labels)`` 或 ``(None, [])``（口令为空/构造失败时降级不放按钮，
    绝不让按钮拖垮主卡片发送）。button 走 msg_type=2，与图片(msg_type=7)互斥，
    因此作为**独立的一条消息**在图片卡片之后发。

    2026-11-11 修复 40034011「无效 markdown content」：QQ 官方要求消息按钮只能
    挂在 msg_type=2 的 **markdown 消息**上（官方文档「发送带有按钮的消息」）。
    适配器 send_to_group 见 markdown 或 keyboard 都会置 msg_type=2，但只有
    keyboard、没有 markdown 时平台因缺合法 content 返回 40034011 —— 真实群实测
    三次主图成功、三次按钮段全被拒。故按钮段必须 markdown + keyboard 同段；
    markdown 段构造收敛在 qq_keyboard.make_button_message（可单测/可注入）。
    """
    if not cmd:
        return None, []
    try:
        from .qq_keyboard import make_button_message, make_keyboard  # noqa: PLC0415

        kb_model, btns = make_keyboard(cmd)
        seg = make_button_message(kb_model, _QQMessage, _QQSegment)
        return seg, [b.label for b in btns]
    except Exception as e:  # noqa: BLE001 — 按钮是增强项，失败只记日志不阻断发送
        log.warning(jlog("official_keyboard_build_failed", error=type(e).__name__,
                         detail=str(e)[:160], cmd=cmd))
        return None, []


def pseudo_id(key: str) -> int:
    """openid → 稳定的 **负数** int（供 int 型业务接口使用）。

    为什么必须做这层映射：现有业务层（session / choice / allow / queue）都以
    **int 型 QQ 号**为用户主键，而官方通道只有 openid（字符串、且无法反查 QQ 号）。

    为什么取负数：真实 QQ 号恒为正，**负数永不与真实用户串号**（避免 A 用户读到
    B 用户的会话/待编辑源图）。代价：官方用户在历史页显示的是负数 ID，
    这是当前阶段的取舍，后续若要做 openid↔QQ 号映射可再演进。
    """
    import hashlib

    h = hashlib.md5(str(key).encode("utf-8")).hexdigest()
    return -(int(h[:8], 16) % 900_000_000 + 100_000_000)


async def _download(url: str) -> bytes:
    """下载图片/视频字节。仅支持 http(s)；失败返回空 bytes（调用方降级）。"""
    if not (url.startswith("http://") or url.startswith("https://")):
        return b""
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as cli:
            resp = await cli.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
        log.warning(jlog("official_download_bad_status", status=resp.status_code))
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("official_download_failed", error=type(e).__name__,
                         detail=str(e)[:160]))
    return b""


def _extract_text_images(event: Any) -> tuple[str, list]:
    """从官方消息事件里提取纯文本与图片 URL（@ 段不落在文本里，适配器已剔除）。"""
    texts: list = []
    images: list = []
    try:
        for seg in event.get_message():
            t = str(getattr(seg, "type", "") or "")
            data = getattr(seg, "data", {}) or {}
            if t == "text":
                texts.append(str(data.get("text", "") or ""))
            elif t == "image":
                url = str(data.get("url") or data.get("file") or "")
                if url:
                    images.append(url)
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("official_extract_failed", error=type(e).__name__))
    return "".join(texts).strip(), images


class OfficialChannel(Channel):
    name = "official"

    # ---------- 归一化 ----------
    async def normalize(
        self, event: Any, self_id: str = "", trace_id: str = ""
    ) -> dict | None:
        """官方事件 → ctx v2。无法识别的事件返回 None（由上层忽略）。"""
        try:
            (
                _QQBot, _Msg, _Seg,
                C2CMessageCreateEvent,
                GroupMessageCreateEvent,
                GroupAtMessageCreateEvent,
            ) = _import_qq()
        except Exception as e:  # noqa: BLE001
            log.error(jlog("official_adapter_missing", error=type(e).__name__))
            return None

        # 只处理 C2C 私聊与群消息两类，其余（频道/互动/审核）本阶段不处理
        if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)):
            return None

        is_group = isinstance(event, GroupMessageCreateEvent)
        at_bot = isinstance(event, GroupAtMessageCreateEvent)

        if is_group:
            user_openid = str(getattr(event.author, "member_openid", "") or "")
            group_openid = str(getattr(event, "group_openid", "") or "")
            group_key = (GROUP_PREFIX + group_openid) if group_openid else ""
        else:
            user_openid = str(getattr(event.author, "user_openid", "") or "")
            group_openid = ""
            group_key = ""

        if not user_openid:
            # 字段名不能叫 event：jlog 的第一个形参就是 event（会撞名）
            log.warning(jlog("official_no_openid", event_type=type(event).__name__))
            return None

        return make_ctx(
            self.name,
            user_key=USER_PREFIX + user_openid,
            group_key=group_key,
            user_id=0,          # 官方通道没有 int 型 QQ 号
            group_id=0,
            self_id=self_id,
            is_group=is_group,
            at_bot=at_bot,
            trace_id=trace_id,
            # ts 用于判断是否还能被动回复（平台硬限 5 分钟）
            reply_ref={"msg_id": str(getattr(event, "id", "") or ""),
                       "msg_seq": 1, "ts": time.time()},
        )

    async def to_legacy_event(self, event: Any, self_id: str = "", trace_id: str = "") -> Any:
        """官方事件 → ``dispatcher.QQEvent``（业务层契约对象）。

        有了它，意图路由 / 队列 / 限流 / 会话 / 配额等业务逻辑可以**零改动复用**：
        它们只认 QQEvent，不关心事件来自哪个协议端。
        """
        from ..dispatcher import QQEvent

        ctx = await self.normalize(event, self_id=self_id, trace_id=trace_id)
        if not ctx:
            return None
        text, images = _extract_text_images(event)
        is_group = bool(ctx["is_group"])
        ev = QQEvent(
            user_id=pseudo_id(ctx["user_key"]),
            group_id=pseudo_id(ctx["group_key"]) if is_group else 0,
            message_type="group" if is_group else "private",
            raw_text=text,
            at_bot=bool(ctx["at_bot"]),
            images=images,
            self_id=self_id,
            trace_id=trace_id,
            raw=event,
        )
        # 通道标记：media 层据此决定用哪个协议端回传（见 media._send）
        setattr(ev, "channel", self.name)
        return ev

    # ---------- 发送 ----------
    async def send(self, ctx: dict, out: Outgoing) -> dict:
        """发送文本/图片/视频。

        拆分原则：**文本与每张图各占一条消息**——官方按 msg_type 互斥
        （0=文本 / 7=富媒体），不能像 OneBot 那样拼成一条。

        被动/主动策略：
          * 有 msg_id 且在 OFFICIAL_PASSIVE_MAX_AGE 内、且未超 5 条 → 被动回复（不占日额度）
          * 否则 → 主动消息（先过日额度 limiter，超限直接失败并给 reason）

        ctx 补全（2026-11-10 warframe 官方群适配）：老调用点（warframe 各 _send_*
        与主流程 _ctx()）自造的 ctx 只有 {user_id, group_id, channel}，缺
        group_key/user_key/reply_ref。这里从 ContextVar 取事件入口 normalize 出的
        完整 ctx，用 setdefault 语义补齐缺失字段（不覆盖调用方已显式给的），
        否则 group_key 空 → is_group=False → 走 send_to_c2c(openid="") → 必失败。
        主动推送场景没有 ContextVar → 不补全，行为完全不变。
        """
        full = current_official_ctx()
        if full:
            merged = dict(full)
            merged.update(ctx)  # 调用方显式字段优先，不覆盖
            ctx = merged
        try:
            (
                QQBot, QQMessage, QQSegment,
                C2CMessageCreateEvent,
                GroupMessageCreateEvent,
                GroupAtMessageCreateEvent,
            ) = _import_qq()
        except Exception as e:  # noqa: BLE001
            log.error(jlog("official_adapter_missing", error=type(e).__name__))
            return {"ok": False, "reason": "official adapter missing"}

        bot = _get_official_bot(QQBot)
        if bot is None:
            return {"ok": False, "reason": "no official bot online"}

        group_key = str(ctx.get("group_key") or "")
        is_group = group_key.startswith(GROUP_PREFIX)

        # ---- 组装待发段（文本 / 图片 / 视频）----
        # 2026-11-11 起每项为 (seg, kind)：kind ∈ {text, image, video, keyboard}，
        # 仅用于日志与额度策略；发送一律 message=seg，适配器按段内容定 msg_type。
        segments: list[tuple] = []
        if out.text:
            seg = QQMessage()
            if out.at_user and is_group:
                openid = str(ctx.get("user_key", "")).replace(USER_PREFIX, "")
                if openid:
                    seg += QQSegment.mention_user(openid)
            seg += QQSegment.text(out.text)
            segments.append((seg, "text"))

        for url in out.image_urls or []:
            data = await _download(url)
            if not data:
                log.warning(jlog("official_image_download_empty", trace_id=ctx.get("trace_id", "")))
                continue
            m = QQMessage()
            m += QQSegment.file_image(data, file_name="image.png")
            segments.append((m, "image"))

        for data in out.images or []:
            if not data:
                continue
            m = QQMessage()
            m += QQSegment.file_image(data, file_name="image.png")
            segments.append((m, "image"))

        if out.video:
            data = await _download(out.video) if out.video.startswith("http") else b""
            if data:
                m = QQMessage()
                m += QQSegment.file_video(data, file_name="video.mp4")
                segments.append((m, "video"))

        if not segments:
            return {"ok": True, "skipped": True}

        # ---- 追加「快捷命令按钮」消息（2026-11-11，全量口令卡片统一带按钮）----
        # 收敛点：所有官方发送都过这里，一处挂钩 = 全量 warframe/二游/小游戏/计划
        # 回复卡片自动带按钮，warframe 两个大文件零改动。
        # btn_cmd 优先取 Outgoing 显式字段，其次取 ctx（warframe 适配器/主流程入口
        # 可把当前口令塞进 ctx["keyboard_cmd"]）；两者都无则按 out.text 首词兜底。
        btn_cmd = str(
            out.keyboard_cmd
            or ctx.get("keyboard_cmd", "")
            or (out.text or "").strip().split("\n")[0]
        )
        btn_seg, btn_labels = _build_button_segment(btn_cmd, QQMessage, QQSegment)
        if btn_seg is not None:
            segments.append((btn_seg, "keyboard"))

        ref = ctx.get("reply_ref") or {}
        ref_id = str(ref.get("msg_id") or "")
        ref_ts = float(ref.get("ts") or 0)
        passive_ok = bool(ref_id) and (
            (time.time() - ref_ts) < max(1, int(Config.OFFICIAL_PASSIVE_MAX_AGE))
        )

        sent: list[str] = []
        modes: list[str] = []
        for i, (seg, seg_kind) in enumerate(segments):
            # 每条用户消息最多被动回 5 次（平台硬限），超出转主动
            use_passive = passive_ok and i < 5
            if not use_passive and is_group:
                # 按钮段是增强项：若主动额度已耗尽，跳过按钮、不阻断已发的主卡片。
                # limiter.allow() 只读判定不增计数（计数在 add()），天然等价 dry-run。
                if seg_kind == "keyboard" and not official_limiter.allow(group_key):
                    log.info(jlog("official_keyboard_skipped_quota", group_key=group_key,
                                  trace_id=ctx.get("trace_id", "")))
                    continue
                if not official_limiter.allow(group_key):
                    log.warning(jlog(
                        "official_quota_blocked",
                        group_key=group_key,
                        used=official_limiter.used(group_key),
                        trace_id=ctx.get("trace_id", ""),
                    ))
                    break
            t0 = time.time()
            try:
                if is_group:
                    ret = await bot.send_to_group(
                        group_openid=group_key[len(GROUP_PREFIX):],
                        message=seg,
                        msg_id=ref_id if use_passive else None,
                        msg_seq=(i + 1) if use_passive else None,
                    )
                else:
                    openid = str(ctx.get("user_key", "")).replace(USER_PREFIX, "")
                    ret = await bot.send_to_c2c(
                        openid=openid,
                        message=seg,
                        msg_id=ref_id if use_passive else None,
                        msg_seq=(i + 1) if use_passive else None,
                    )
            except Exception as e:  # noqa: BLE001 — 记全后返回原因，不静默吞（CLOSE-02）
                log.error(jlog(
                    "channel_send_failed",
                    channel=self.name,
                    mode="passive" if use_passive else "active",
                    error=type(e).__name__,
                    detail=str(e)[:200],
                    duration_ms=int((time.time() - t0) * 1000),
                ))
                return {"ok": False, "reason": "%s: %s" % (type(e).__name__, str(e)[:200])}

            if not use_passive and is_group:
                official_limiter.add(group_key)

            sent.append(str(getattr(ret, "id", "") or ""))
            modes.append("passive" if use_passive else "active")
            log.info(jlog(
                "official_sent",
                mode=modes[-1],
                is_group=is_group,
                kind=seg_kind,
                duration_ms=int((time.time() - t0) * 1000),
                msg_id=sent[-1],
                **( {"buttons": ",".join(btn_labels)} if seg_kind == "keyboard" else {} ),
            ))

        if not sent:
            return {"ok": False, "reason": "nothing sent (quota or empty)"}
        return {"ok": True, "msg_ids": sent, "modes": modes}


def _get_official_bot(QQBot):
    """从 nonebot 运行时取在线的官方 Bot 实例（找不到返回 None）。"""
    try:
        from nonebot import get_bots  # noqa: PLC0415

        for bot in get_bots().values():
            if isinstance(bot, QQBot):
                return bot
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("official_bot_lookup_failed", error=type(e).__name__))
    return None
