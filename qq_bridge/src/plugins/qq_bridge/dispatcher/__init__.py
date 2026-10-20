# -*- coding: utf-8 -*-
"""dispatcher（事件分发，设计文档 §3.2）。

职责：把 onebot 原始 message 事件标准化，并做权限过滤。

输入：nonebot onebot v11 的 MessageEvent（PrivateMessageEvent / GroupMessageEvent）
输出：标准化事件 QQEvent；被过滤时返回 None（调用方据此静默丢弃）

验收（§3.2）：
  - 群消息未 @机器人 且无 `/` 前缀 → 直接丢弃（不消耗额度、不调用模型）
  - 私聊黑名单用户 → 静默丢弃
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Config, get_logger, jlog
from ..session.choice import choice_sessions
from .. import gallery
from ..ai_mode import ai_mode_store

log = get_logger()

# 群消息必须真 @机器人（at_bot=True）才受理（2026-09-04 收紧）：
# 去掉原 / ! ！ . 前缀无 @ 也唤起的口子，避免群里别人随手发 /help
# 或暗号就被误触发刷屏；仅主人特权 is_owner() 仍保留。
# （旧 COMMAND_PREFIXES 常量已移除）


@dataclass
class QQEvent:
    """标准化内部事件（文档 §3.2 输出契约）。"""

    user_id: int
    group_id: int                 # 私聊为 0
    message_type: str             # private | group
    raw_text: str                 # 去掉 @ 段后的纯文本
    at_bot: bool = False          # 群消息是否 @了机器人
    force_t2i: bool = False       # 显式生图词命中，路由时优先生图（覆盖 /mode video）
    images: list = field(default_factory=list)   # 本条消息里的图片 url
    files: list = field(default_factory=list)    # 本条消息里的文件段（M6 文件互传：dict{file,name,size,url}）
    self_id: str = ""
    trace_id: str = ""
    raw: Any = None

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"

    @property
    def scope_key(self) -> str:
        """限流维度：群=g:<group_id>，私聊=u:<user_id>（§3.6）。"""
        return "g:%d" % self.group_id if self.is_group else "u:%d" % self.user_id

    @property
    def reply_key(self) -> str:
        """回复用 key：与 scope_key 同构，media 层据此选 send_group_msg/private。"""
        return self.scope_key


def _extract(segments) -> tuple[str, bool, list, str, list]:
    """从消息段序列里提取 (纯文本, 是否@机器人, 图片url列表, self_id_at, 文件段列表)。

    采用 duck typing 读取，便于单元测试直接喂假对象。
    文件段（OneBot v11 `file` 类型，NapCat 好友文件/群文件以消息段投递）提取为
    dict{file(文件id), name, size, url} —— 落盘与下载由 __init__.py 处理。
    """
    texts: list = []
    images: list = []
    files: list = []
    at_bot = False
    self_id_at = ""
    for seg in segments or []:
        seg_type = str(getattr(seg, "type", "") or "")
        data = getattr(seg, "data", None) or {}
        if seg_type == "text":
            texts.append(str(data.get("text") or ""))
        elif seg_type == "image":
            url = data.get("url") or data.get("file") or ""
            if url:
                images.append(url)
        elif seg_type == "file":
            files.append({
                "file": str(data.get("file") or data.get("file_id") or ""),
                "name": str(data.get("name") or data.get("file_name") or "unknown"),
                "size": int(data.get("size") or 0),
                "url": str(data.get("url") or ""),
            })
        elif seg_type == "at":
            self_id_at = str(data.get("qq") or "")
    return "".join(texts).strip(), at_bot, images, self_id_at, files


def normalize(event: Any, self_id: str = "", trace_id: str = "") -> QQEvent | None:
    """标准化 onebot 消息事件。返回 None 表示应被丢弃。

    丢弃规则（§3.2）：
      1) 群消息未 @机器人、且无命令前缀 → 丢弃
      2) 非 private/group 的其它 message_type → 丢弃
    """
    message_type = str(getattr(event, "message_type", "") or "")
    if message_type not in ("private", "group"):
        return None

    user_id = int(getattr(event, "user_id", 0) or 0)
    group_id = int(getattr(event, "group_id", 0) or 0)
    # 优先 original_message：nonebot 在 handler 执行前会把 event.message 里的
    # at 段改写成空文本段，导致群 @机器人 提取失败（线上主人因特权幸免）。
    # original_message 是适配器入站时的 deepcopy，保留完整段序列。
    segments = (
        getattr(event, "original_message", None)
        or getattr(event, "message", None)
        or []
    )
    self_id = self_id or str(getattr(event, "self_id", "") or "")

    raw_text, _, images, at_qq, files = _extract(segments)
    at_bot = bool(at_qq) and (at_qq == "all" or at_qq == self_id)

    if message_type == "group" and not at_bot:
        # 群消息必须真 @机器人才受理（去掉原 COMMAND_PREFIXES 自动唤起，2026-09-04）
        if is_owner(user_id):
            # 主人特权：NapCat 上报的群消息常把 @机器人 段剥离，
            # 群里只有主人会触发，故主人发的消息直接视作被唤起。
            at_bot = True
        elif choice_sessions.has(user_id):
            # §修复（2026-09-05）：用户在 pending choice 状态（已触发反问等待选择）→ 放行。
            # 否则群友回"1"/"本地" 等对反问的纯文字回答会被群 @ 门禁误丢，
            # 表现为机器人突然发一张大帮助文字（HELP_TEXT），用户体感"没反应/发了表情包"。
            at_bot = True
            log.info(jlog("dispatcher_pending_bypass", user_id=user_id, group_id=group_id, trace_id=trace_id))
        elif ai_mode_store.is_on("g:%d" % group_id):
            # AI 模式（2026-10-19）：该群已用 /ai on 开启大模型陪聊 →
            # **群里所有消息**（不再要求 @机器人）都放行进 _route，
            # 由 AI 模式拦截层决定走大模型还是正常命令路由。
            at_bot = True
            log.info(jlog("dispatcher_ai_bypass", user_id=user_id, group_id=group_id, trace_id=trace_id))
        elif gallery.active_count(user_id):
            # 画廊上下文特例（2026-09-11 起，2026-09-13 补翻页口令）：
            # 用户在画廊里回「3」选原图、回「下一页」翻页，都免 @——否则没 @的消息会被
            # 群门禁静默丢弃，体感"发了口令没反应"。仅「1~2 位纯数字」或「整句翻页口令」
            # 放行，画廊上下文里其它闲聊仍按原门禁丢，避免破口。
            _t = (raw_text or "").strip()
            if (_t.isdigit() and 1 <= len(_t) <= 2) or gallery.is_nav(_t):
                at_bot = True
                log.info(jlog("dispatcher_gallery_bypass", user_id=user_id, group_id=group_id, trace_id=trace_id, raw=_t))
            else:
                log.info(jlog("dispatcher_drop", reason="group_not_at", user_id=user_id, group_id=group_id, at_qq=at_qq, self_id=self_id, raw_message=str(getattr(event, "raw_message", ""))[:200]))
                return None
        else:
            log.info(jlog("dispatcher_drop", reason="group_not_at", user_id=user_id, group_id=group_id, at_qq=at_qq, self_id=self_id, raw_message=str(getattr(event, "raw_message", ""))[:200]))
            return None

    return QQEvent(
        user_id=user_id,
        group_id=group_id,
        message_type=message_type,
        raw_text=raw_text,
        at_bot=at_bot,
        images=images,
        files=files,
        self_id=self_id,
        trace_id=trace_id,
        raw=event,
    )


def is_owner(user_id: int) -> bool:
    return user_id in Config.BOT_OWNER_QQ


def allow(ev: QQEvent) -> tuple[bool, str]:
    """§8 权限过滤。返回 (ok, reason)。

    优先级：主人 > 黑名单 > 白名单。
    reason='silent' 表示静默丢弃（不回任何提示，避免被探测/骚扰）。
    """
    if is_owner(ev.user_id):
        return True, "owner"
    if ev.user_id in Config.USER_BLACKLIST:
        log.info(jlog("dispatcher_denied", reason="blacklist", user_id=ev.user_id, trace_id=ev.trace_id))
        return False, "silent"
    if ev.is_group:
        wl = Config.GROUP_WHITELIST
        if wl and ev.group_id not in wl:
            return False, "silent"  # 非监听群：整群静默
        # 监听群内：能走到这里说明已 @机器人 / 带命令前缀 / 主人发言，
        # 2026-09-04 用户决策：放行所有群友（黑名单检查已在前面完成）。
        return True, "group_ok"
    wl = Config.PRIVATE_WHITELIST
    if wl and ev.user_id not in wl:
        return False, "silent"
    if not wl and Config.STRICT_WHITELIST:
        return False, "silent"
    return True, "private_ok"
