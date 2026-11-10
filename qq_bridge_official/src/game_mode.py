# -*- coding: utf-8 -*-
"""跨游戏「当前模式」共享存储 + 口令探测。

斗破 / 修仙 现在都用纯中文口令，靠「按用户记录的当前游戏」来分流：
  * 谁发了 修仙菜单 → 他后续口令走修仙；发了 斗破菜单 → 走斗破。
  * 只有菜单（及 入道 / 我要修仙 这类入口）会切换模式；
    其它口令按模式命中对应游戏，打错游戏或没选模式给友好提示。

落盘：data/game_mode.json（相对 cwd；容器内 cwd=/app 即挂载卷 ./data）。
环境红线：绝不写 C 盘用户目录，路径默认落在仓库/容器数据目录。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Dict, List, Optional

DATA_PATH = os.environ.get("GAME_MODE_PATH", os.path.join("data", "game_mode.json"))

GAME_LABELS = {"xiuxian": "修仙", "doupo": "斗破"}

_lock = asyncio.Lock()
_modes: Dict[str, str] = {}
_loaded = False
_keywords: Dict[str, List[str]] = {}


async def _ensure_loaded() -> None:
    global _modes, _loaded
    if _loaded:
        return
    async with _lock:
        if _loaded:
            return
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                _modes = json.load(f)
        except FileNotFoundError:
            _modes = {}
        except Exception:
            # 损坏文件不致命：内存空库，下次保存覆盖
            _modes = {}
        _loaded = True


async def get_mode(uid: str) -> Optional[str]:
    """返回用户当前游戏模式（'xiuxian' / 'doupo' / None）。"""
    await _ensure_loaded()
    return _modes.get(str(uid))


async def set_mode(uid: str, game: str) -> None:
    """设定用户当前游戏模式。"""
    await _ensure_loaded()
    async with _lock:
        _modes[str(uid)] = game
        _save()


def _save() -> None:
    d = os.path.dirname(DATA_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_modes, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_PATH)


def register_keywords(game: str, kws: List[str]) -> None:
    """各游戏插件在 import 时登记自己的口令关键字，供兜底路由探测用。"""
    _keywords[game] = kws


def all_keywords() -> List[str]:
    out: List[str] = []
    for kws in _keywords.values():
        out.extend(kws)
    # 长关键字优先，避免短前缀（如「送」）吞掉更长口令
    return sorted(set(out), key=len, reverse=True)


def looks_like_command(text: str) -> bool:
    """消息去掉前导 CQ 码 / 空白 / # 后，是否以某个游戏口令开头。

    用于兜底路由判断「这条消息像不像在打游戏口令」，从而只在像口令时才给提示，
    不误伤闲聊。
    """
    t = re.sub(r"^(?:\[CQ:[^\]]*\]|\s)*#?\s*", "", text or "")
    return any(t.startswith(kw) for kw in all_keywords())
