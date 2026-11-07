# -*- coding: utf-8 -*-
"""修仙文字游戏 · 数据存储层。

零额外依赖：用标准库 json + asyncio.Lock 做单机持久化。
asyncio 单事件循环下，Lock 能保证 read-modify-write 的原子性，
多玩家并发安全。数据落盘为单个 JSON 文件（原子写：先写临时再 rename）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config as cfg


class Store:
    def __init__(self, path: str = cfg.DATA_PATH):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {
            "players": {},      # qq(str) -> player dict
            "sects": {},        # sect_id(str) -> sect dict
            "enabled": [],      # 已启用游戏的群 group_id(str) 列表
            "world": {},        # group_id(str) -> {boss:{...}, secret:{...}}
        }
        self._loaded = False

    # ---- 生命周期 ----
    async def load(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                # 兼容缺字段
                for k in ("players", "sects", "enabled", "world"):
                    self._data.setdefault(k, {} if k != "enabled" else [])
            except FileNotFoundError:
                pass
            except Exception:
                # 损坏文件不致命：保留内存空库，下次保存覆盖
                pass
            self._loaded = True

    async def save(self) -> None:
        async with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    # ---- 玩家 ----
    async def get_player(self, qq: str) -> Optional[dict]:
        async with self._lock:
            return self._data["players"].get(qq)

    async def put_player(self, p: dict) -> None:
        async with self._lock:
            self._data["players"][str(p["qq"])] = p
        await self.save()

    async def all_players(self, group: Optional[str] = None) -> List[dict]:
        async with self._lock:
            ps = list(self._data["players"].values())
        if group is not None:
            ps = [p for p in ps if p.get("group") == group]
        return ps

    # ---- 宗门 ----
    async def get_sect(self, sid: str) -> Optional[dict]:
        async with self._lock:
            return self._data["sects"].get(sid)

    async def put_sect(self, s: dict) -> None:
        async with self._lock:
            self._data["sects"][str(s["id"])] = s
        await self.save()

    async def all_sects(self) -> List[dict]:
        async with self._lock:
            return list(self._data["sects"].values())

    # ---- 群启用状态（后台随机事件需要知道往哪发）----
    async def enable_group(self, group: str) -> None:
        async with self._lock:
            if group not in self._data["enabled"]:
                self._data["enabled"].append(group)
        await self.save()

    async def enabled_groups(self) -> List[str]:
        async with self._lock:
            return list(self._data["enabled"])

    # ---- 世界事件状态（世界Boss / 秘境）----
    async def get_world(self, group: str) -> dict:
        async with self._lock:
            return self._data["world"].setdefault(group, {})

    async def set_world(self, group: str, world: dict) -> None:
        async with self._lock:
            self._data["world"][group] = world
        await self.save()

    async def touch(self, group: str) -> None:
        """确保 world 键存在（后台循环用）。"""
        async with self._lock:
            self._data["world"].setdefault(group, {})


def now() -> float:
    return time.time()
