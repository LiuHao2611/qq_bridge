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
    🔴 且同一 msg_id 下第 N 次被动回复的 msg_seq **必须严格 == N**（适配器
    ``Bot.send`` 维护 ``event._reply_seq += 1``，bot.py:691/706）——本模块绕过
    Bot.send 直调 send_to_group，故由 ``passive_seq.next_msg_seq`` 按共享
    reply_ref 跨 send 递增；重复 seq 会被平台去重拒收 ``40054005``（2026-11-11
    紫卡分析图已渲染却「没出结果」的根因）；
  * 主动消息日上限 1000 条/群 —— 本模块用 limiter 自限，避免撞平台 40034100。

日志铁律：平台调用必须记 request/response/error/duration_ms（用户要求），
但**绝不**记录 AppSecret / access_token（LOG-04）。
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..config import Config, get_logger, jlog
from .base import Channel, Outgoing, make_ctx, current_official_ctx
from .limiter import official_limiter
from .passive_seq import next_msg_seq, peek_msg_seq

log = get_logger()

#: 图片+按钮合并成一条（2026-11-11 用户「图片要和按钮一起发」）。
#: 默认开；设 OFFICIAL_IMAGE_BUTTON_MERGE=false 可一键回退到「图片条+按钮条」两条。
_IMAGE_BUTTON_MERGE = os.getenv("OFFICIAL_IMAGE_BUTTON_MERGE", "true").strip().lower() not in (
    "false", "0", "no", "off",
)

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
        # 第四次修改：单档卡/非口令零按钮 → make_keyboard 返 (None,())。
        # 绝不能用空 model 构造段下发（空 keyboard → 40034011「无效 markdown content」）。
        if kb_model is None or not btns:
            return None, []
        seg = make_button_message(kb_model, _QQMessage, _QQSegment)
        return seg, [b.label for b in btns]
    except Exception as e:  # noqa: BLE001 — 按钮是增强项，失败只记日志不阻断发送
        log.warning(jlog("official_keyboard_build_failed", error=type(e).__name__,
                         detail=str(e)[:160], cmd=cmd))
        return None, []


async def _build_merged_card_segment(bot, image_datas: list[bytes],
                                     cmd: str, _QQMessage, _QQSegment):
    """把「一张图 + 多个按钮」合成**一条** msg_type=2 markdown 消息。

    图片字节先经素材管理 API 换成 ``qqbot-media://`` 引用，内嵌进 markdown
    content（``![ #宽px #高px](url)``），再与 keyboard 同段。这样图片与按钮
    在用户端是同一条消息（2026-11-11 用户「一个口令一张图+多个按钮一起发」）。

    返回 ``(message, btn_labels)``；任一素材上传失败抛
    :class:`media_asset.MaterialUploadError`，由 send() 捕获降级回两条发送。

    第四次修改（零按钮）：单档卡/非口令的 ``make_keyboard`` 返 ``(None,())``，
    此时**只发内嵌图片的 markdown 段、不挂空 keyboard**（空 keyboard 会被平台以
    40034011 拒收，且正是「去掉自己+主菜单」后单档卡必然遇到的路径）。有按钮才
    在同段追加 keyboard，维持「图片+按钮一条」的既有语义。
    """
    from .media_asset import ensure_material, markdown_image  # noqa: PLC0415
    from .qq_keyboard import make_keyboard  # noqa: PLC0415

    kb_model, btns = make_keyboard(cmd)
    parts: list[str] = []
    for i, data in enumerate(image_datas):
        mat = await ensure_material(bot, data, file_name="card_%d.png" % i)
        parts.append(markdown_image(mat["url"], mat["width"], mat["height"]))
    seg = _QQMessage()
    seg += _QQSegment.markdown("\n".join(parts))  # 合法 markdown content（图片）
    if kb_model is not None and btns:              # 有按钮才挂，零按钮绝不发空 keyboard
        seg += _QQSegment.keyboard(kb_model)       # 同段挂按钮，平台 msg_type=2
    return seg, [b.label for b in btns]


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

        # ---- 组装待发段（文本 / 图片 / 视频 / 按钮）----
        # 2026-11-11 每项为 (seg, kind)：kind ∈ {text, image, video, keyboard,
        # merged_card}，仅用于日志与额度策略；发送一律 message=seg。
        # 「一个口令一张图 + 多个按钮一起发」：若有图片字节，优先把图片经素材
        # 管理 API 换成 qqbot-media:// 引用、内嵌 markdown 并与 keyboard 同段
        # （msg_type=2），用户端就是**一条**消息；失败则降级回「图片条 + 按钮条」。
        segments: list[tuple] = []
        btn_labels: list[str] = []
        if out.text:
            seg = QQMessage()
            if out.at_user and is_group:
                openid = str(ctx.get("user_key", "")).replace(USER_PREFIX, "")
                if openid:
                    seg += QQSegment.mention_user(openid)
            seg += QQSegment.text(out.text)
            segments.append((seg, "text"))

        # 先把所有图片字节收齐（不再逐个 append file_image 段，交给合并逻辑决定）
        image_datas: list[bytes] = []
        for url in out.image_urls or []:
            data = await _download(url)
            if not data:
                log.warning(jlog("official_image_download_empty", trace_id=ctx.get("trace_id", "")))
                continue
            image_datas.append(data)
        for data in out.images or []:
            if data:
                image_datas.append(data)

        if out.video:
            data = await _download(out.video) if out.video.startswith("http") else b""
            if data:
                m = QQMessage()
                m += QQSegment.file_video(data, file_name="video.mp4")
                segments.append((m, "video"))

        # btn_cmd 优先取 Outgoing 显式字段，其次取 ctx（warframe/主流程入口注入），
        # 否则按 out.text 首词兜底。
        btn_cmd = str(
            out.keyboard_cmd
            or ctx.get("keyboard_cmd", "")
            or (out.text or "").strip().split("\n")[0]
        )

        # 第四次修改：先判「该口令是否有同卡其他档位按钮」。单档卡/非口令/
        # 市价裸口令去掉自己+主菜单后是**零按钮**——此时绝不能走「图片+按钮合并」
        # （会上传素材换 qqbot-media 却挂空 keyboard，或下发空 rows 被 40034011 拒）。
        # 零按钮直接走下面的图片独立段（msg_type=7 富媒体），用户只看到干净的图片卡。
        try:
            from .qq_keyboard import buttons_for as _buttons_for  # noqa: PLC0415
            has_buttons = bool(btn_cmd) and bool(_buttons_for(btn_cmd))
        except Exception as e:  # noqa: BLE001 — 判不出就当无按钮，宁可少发按钮不阻断图片
            log.warning(jlog("official_has_buttons_failed", error=type(e).__name__,
                             cmd=btn_cmd))
            has_buttons = False

        # ---- 图片 + 按钮合一（默认开；失败降级两条；零按钮时跳过）----
        merged_done = False
        if _IMAGE_BUTTON_MERGE and image_datas and btn_cmd and has_buttons:
            try:
                merged_seg, btn_labels = await _build_merged_card_segment(
                    bot, image_datas, btn_cmd, QQMessage, QQSegment,
                )
                segments.append((merged_seg, "merged_card"))
                merged_done = True
                log.info(jlog("official_image_button_merged", images=len(image_datas),
                              cmd=btn_cmd, trace_id=ctx.get("trace_id", "")))
            except Exception as e:  # noqa: BLE001 — 素材失败降级，不吞错（记 warning）
                log.warning(jlog("official_merge_fallback_two_messages",
                                 error=type(e).__name__, detail=str(e)[:160],
                                 images=len(image_datas), cmd=btn_cmd))

        if not merged_done:
            # 降级路径（或本就没有图片/口令）：图片走 msg_type=7 富媒体独立段
            for data in image_datas:
                m = QQMessage()
                m += QQSegment.file_image(data, file_name="image.png")
                segments.append((m, "image"))
            # 按钮走 markdown+keyboard（msg_type=2）独立段；零按钮卡不发这段。
            if has_buttons:
                btn_seg, btn_labels = _build_button_segment(btn_cmd, QQMessage, QQSegment)
                if btn_seg is not None:
                    segments.append((btn_seg, "keyboard"))

        if not segments:
            return {"ok": True, "skipped": True}

        ref = ctx.get("reply_ref") or {}
        ref_id = str(ref.get("msg_id") or "")
        # 2026-11-11 修 40034105：按钮点击 INTERACTION_CREATE 没有原消息 msg_id，
        # 官方给的是 event_id（INTERACTION_CREATE:<uuid>），必须用它做被动回复。
        # 有 msg_id（普通消息）或 event_id（按钮/事件回访）任一即可被动回复。
        ref_event_id = str(ref.get("event_id") or "")
        ref_ts = float(ref.get("ts") or 0)
        passive_ok = (bool(ref_id) or bool(ref_event_id)) and (
            (time.time() - ref_ts) < max(1, int(Config.OFFICIAL_PASSIVE_MAX_AGE))
        )

        sent: list[str] = []
        modes: list[str] = []
        for i, (seg, seg_kind) in enumerate(segments):
            # 2026-11-11 修 40054005（紫卡「没出结果」）：msg_seq 必须按共享
            # reply_ref **跨 send 严格递增**，绝不能用段内下标 (i+1)——那样每次
            # send 都从 1 起，同一 msg_id 下第二次 send（如紫卡「排队话术」之后的
            # 「结果图」）重复 seq=1 → 平台去重拒收。对齐适配器 event._reply_seq
            # 语义：同一 reply_ref 第 N 次被动回复拿 seq=N；累计 >5 转主动。
            # 只有真正走被动的段才消耗序号（主动段不调，避免无谓推进计数）。
            msg_seq = next_msg_seq(ref) if passive_ok else None
            use_passive = msg_seq is not None
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
                # 官方要求 msg_id 与 event_id「二选一」，且 exclude_none 不排空串：
                # 必须把空串归一为 None，否则按钮场景 (msg_id="", event_id=xxx)
                # 会同时带两个字段被平台拒。用 `or None` 归一。
                passive_msg_id = (ref_id or None) if use_passive else None
                passive_event_id = (ref_event_id or None) if use_passive else None
                if is_group:
                    ret = await bot.send_to_group(
                        group_openid=group_key[len(GROUP_PREFIX):],
                        message=seg,
                        msg_id=passive_msg_id,
                        msg_seq=msg_seq,
                        event_id=passive_event_id,
                    )
                else:
                    openid = str(ctx.get("user_key", "")).replace(USER_PREFIX, "")
                    ret = await bot.send_to_c2c(
                        openid=openid,
                        message=seg,
                        msg_id=passive_msg_id,
                        msg_seq=msg_seq,
                        event_id=passive_event_id,
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
                msg_seq=msg_seq,
                reply_seq=peek_msg_seq(ref),
                **( {"buttons": ",".join(btn_labels)}
                    if seg_kind in ("keyboard", "merged_card") else {} ),
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
