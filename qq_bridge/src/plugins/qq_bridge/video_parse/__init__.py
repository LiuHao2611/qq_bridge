# 视频链接解析插件入口：注册 on_message matcher，自动解析群聊/主人私聊转发的视频链接，
# 解析出标题/分区/UP 等信息后以卡片消息回复（含封面图）。
#
# 架构来源：fllesser/nonebot-plugin-parser (MIT) —— @handle 正则注册 + 统一 result 模型；
# 这里去掉 Alconna/uninfo 依赖，改用本项目标准 on_message + bot.send 发送。
from __future__ import annotations

import asyncio
import json
import os
import re

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.log import logger

from .. import history
from ..config import Config
# AI 模式（2026-10-19）：本模块也是独立 matcher，与主流程并行收消息；
# 会话开着大模型陪聊时视频链接归 AI 回，避免解析卡 + AI 回复双重刷屏
# （与 game_tools / 主流程 _route 的豁免口径一致）。
from ..ai_mode import ai_mode_store, event_scope
from .base import BaseParser, VideoParseResult, fmt_duration

# 触发各平台解析器子类注册（import 即收集到 BaseParser._registry）
from . import bilibili, douyin, kuaishou, xiaohongshu  # noqa: F401,E402
from .bilibili import BilibiliParser  # noqa: F401
from .douyin import DouyinParser  # noqa: F401
from .kuaishou import KuaishouParser  # noqa: F401
from .xiaohongshu import XiaohongshuParser  # noqa: F401

# 消息文本里的 URL（排除中文与常见中文标点，避免截断）
URL_RE = re.compile(r"https?://[^\s一-鿿，。、！？；：""''（）【】\]]+")
# 手机 QQ 的 B站分享卡片偶尔使用 bilibili:// 私有协议（而非 http 链接）
BILI_PRIVATE_RE = re.compile(r"bilibili://(?:video|bangumi|play)/?(?:BV[0-9A-Za-z]+|av\d+)", re.I)


def _collect_bili_urls(obj, out: list[str]) -> None:
    """递归从 json/xml 卡片结构中收集所有 bilibili 链接（http 短链/网页链/私有协议）。"""
    if isinstance(obj, str):
        out.extend(URL_RE.findall(obj))
        out.extend(BILI_PRIVATE_RE.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_bili_urls(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_bili_urls(v, out)


_matcher = None


def register():
    """在 qq_bridge 的 register() 里调用，注册消息监听。幂等。"""
    global _matcher
    if _matcher is not None:
        return
    _matcher = on_message(priority=12, block=False)
    _matcher.handle()(_handle)
    names = ", ".join(sorted(p.platform.display_name for p in BaseParser.get_parser_classes()))
    logger.info(f"video_parse registered, platforms={names}")


async def _handle(bot: Bot, event: MessageEvent) -> None:
    if not Config.VIDEO_PARSE_ENABLED:
        return

    scene = getattr(event, "message_type", "private")
    if scene == "group":
        if not Config.VIDEO_PARSE_GROUP_AUTO:
            return
    else:
        owners = Config.VIDEO_PARSE_OWNERS
        if owners and str(event.user_id) not in owners:
            return

    # AI 模式让位（2026-10-19）：该会话开着大模型陪聊 → 链接也交给 AI 回，
    # 本模块静默跳过，杜绝「解析卡 + AI 回复」双重消息
    _scope = event_scope(event)
    if ai_mode_store.is_on(_scope):
        logger.info(f"video_parse_ai_mode_skip scope={_scope}")
        return

    # 从纯文本 + QQ 小程序/图文卡片（json/xml 段）中提取链接。
    # B站分享卡片是 json 段：data.data 是 JSON 字符串，链接在
    # meta.detail_1.qqdocurl（小程序）或 meta.news.jumpUrl（图文），通常是 b23.tv 短链。
    sources: list[str] = []
    plain = event.get_plaintext()
    if plain:
        sources.append(plain)
    for seg in event.message:
        if seg.type in ("json", "xml"):
            payload = seg.data.get("data")
            parsed = payload
            if isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                except Exception:
                    parsed = payload  # 退化：当作纯文本扫描
            # 优先取小程序/图文卡片的标准链接字段（多为 b23.tv 短链或 bilibili.com 链接）
            if isinstance(parsed, dict):
                meta = parsed.get("meta")
                if isinstance(meta, dict):
                    detail = meta.get("detail_1") or {}
                    news = meta.get("news") or {}
                    for key in ("qqdocurl", "url", "jumpUrl", "prefixurl"):
                        for node in (detail, news):
                            if isinstance(node, dict) and isinstance(node.get(key), str):
                                sources.append(node[key])
            # 兜底：递归扫描整个结构的任何字符串，抓出所有 bilibili 链接
            _collect_bili_urls(parsed, sources)

    raw_text = " ".join(sources)
    urls = URL_RE.findall(raw_text)
    urls += BILI_PRIVATE_RE.findall(raw_text)
    # bilibili:// 私有协议 → 还原成可解析的 http 链接
    norm: list[str] = []
    for u in urls:
        if u.startswith("bilibili://"):
            m = re.search(r"(BV[0-9A-Za-z]+|av\d+)", u, re.I)
            if m:
                norm.append("https://www.bilibili.com/video/" + m.group(0))
        else:
            norm.append(u)
    urls = norm
    # 去重，保持顺序
    urls = list(dict.fromkeys(urls))

    logger.info(f"video_parse_handle scene={scene} user={event.user_id} urls={urls} raw_len={len(raw_text)}")
    if not urls:
        for seg in event.message:
            if seg.type in ("json", "xml"):
                d = seg.data.get("data")
                logger.warning(f"video_parse_no_url seg_type={seg.type} data_type={type(d).__name__} data_repr={repr(d)[:2000]}")
        logger.warning(f"video_parse_no_url plaintext={repr(plain)}")
        return

    for url in urls:
        result = await _try_parse_one(url)
        if result:
            card = render_card(result)
            try:
                sent = await bot.send(event, Message(card))
            except Exception as e:  # noqa: BLE001 — 发送失败绝不拖垮消息循环
                logger.error("video_parse_send_failed: %s", e)
                sent = None
            # 落盘：管理页聊天记录里能看到机器人回了什么
            await _record_out(event, card, sent)
            # 统一走各平台 download_video 下载并发送视频（失败/不支持则只发文字卡）
            parser_cls = BaseParser.get_parser_by_name(result.platform)
            if parser_cls is not None:
                try:
                    path = await parser_cls().download_video(result)
                    if path and os.path.exists(path):
                        size = os.path.getsize(path)
                        logger.info(f"video_download_ok platform={result.platform} size={size} path={path}")
                        if size > 50 * 1024 * 1024:
                            logger.warning(f"video_too_large_skip size={size}")
                        else:
                            sent_v = await bot.send(event, Message(MessageSegment.video(file=path)))
                            await _record_out(event, f"[CQ:video,file={path}]", sent_v)
                            logger.info(f"video_send_ok platform={result.platform} path={path}")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"video_send_failed platform={result.platform} error={type(e).__name__}: {e}")
            break  # 一条消息只回一个解析卡片，避免刷屏


async def _record_out(event: MessageEvent, raw: str, sent=None) -> None:
    """把机器人自己发出的消息写进历史（与入站同一套 sqlite），管理页聊天记录可见。"""
    try:
        mid = 0
        if isinstance(sent, dict):
            mid = int(sent.get("message_id") or 0)
        elif isinstance(sent, int):
            mid = sent
        ctx = {
            "group_id": int(getattr(event, "group_id", 0) or 0),
            "user_id": int(getattr(event, "user_id", 0) or 0),
        }
        self_id = str(getattr(event, "self_id", "") or "")
        await asyncio.to_thread(
            history.record_outgoing, ctx, raw, self_id, mid
        )
    except Exception as e:  # noqa: BLE001 — 旁路能力，失败不影响主流程
        logger.warning("video_parse_history_failed: %s", e)


async def _try_parse_one(url: str) -> VideoParseResult | None:
    for cls in BaseParser.get_parser_classes():
        try:
            keyword, m = cls.search_url(url)
        except Exception:
            continue
        try:
            return await cls().parse(keyword, m)
        except Exception as e:  # noqa: BLE001
            logger.error("video_parse_failed platform=%s: %s", getattr(cls.platform, "name", "?"), e)
            continue
    return None


def render_card(r: VideoParseResult) -> str:
    lines = [f"【{r.platform_display}】"]
    if r.title:
        lines.append(f"标题：{r.title}")
    if r.author_name:
        a = f"UP：{r.author_name}"
        if r.author_id:
            a += f"（{r.author_id}）"
        lines.append(a)
    if r.category:
        lines.append(f"分区：{r.category}")
    if r.duration:
        d = fmt_duration(r.duration)
        if d:
            lines.append(f"时长：{d}")
    if r.stats:
        lines.append("  ".join(f"{k} {v}" for k, v in r.stats.items()))
    if r.text:
        t = (r.text or "").strip()
        if t:
            lines.append(f"简介：{t[:200]}")
    if r.url:
        lines.append(r.url)

    msg = "\n".join(lines)
    if r.cover_url:
        msg = f"[CQ:image,file={r.cover_url}]\n" + msg
    return msg
