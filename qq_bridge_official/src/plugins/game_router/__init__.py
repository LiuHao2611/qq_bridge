# -*- coding: utf-8 -*-
"""模式路由兜底：用户没选模式、或打了另一游戏的口令时，给友好提示。

只有当「没有游戏 matcher 抢到这条消息」时才会落到这里——游戏 matcher 命中后会
finish + block，本兜底就不会再跑。故本兜底只响应"看起来像游戏口令但没人接"的情况，
绝不误伤闲聊。

优先级 6：低于游戏 matcher 的 5（保证游戏命中后本兜底被 block），高于主聊天处理器 10。
"""
from __future__ import annotations

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent

from src.game_mode import get_mode, looks_like_command, GAME_LABELS

PRI = 6


def _is_command(event: MessageEvent) -> bool:
    return looks_like_command(event.get_plaintext())


router = on_regex(r".+", priority=PRI, block=True, rule=_is_command)


@router.handle()
async def _(event: MessageEvent):
    m = await get_mode(str(event.user_id))
    if m is None:
        await router.finish("请先发送「修仙菜单」或「斗破菜单」选择你想玩的游戏～")
    label = GAME_LABELS.get(m, m)
    menu = "修仙菜单" if m == "xiuxian" else "斗破菜单"
    await router.finish(f"当前为{label}模式，未识别该口令。发送「{menu}」查看可用口令～")
