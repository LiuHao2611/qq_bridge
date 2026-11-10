# -*- coding: utf-8 -*-
"""斗破苍穹 · 数据存储层。

零额外依赖：标准库 json + asyncio.Lock 做单机持久化。
asyncio 单事件循环下，Lock 保证 read-modify-write 原子性；落盘用临时文件 + os.replace。
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
        self._data: Dict[str, Any] = {"players": {}, "groups": []}
        self._loaded = False

    # ---- 生命周期 ----
    async def load(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                for k, dflt in (("players", {}), ("groups", [])):
                    self._data.setdefault(k, dflt)
            except FileNotFoundError:
                pass
            except Exception:
                # 存档损坏不致命：保留内存空库，下次写盘覆盖
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
            return self._data["players"].get(str(qq))

    async def put_player(self, p: dict) -> None:
        async with self._lock:
            self._data["players"][str(p["qq"])] = p
        await self.save()

    async def all_players(self, group: Optional[str] = None) -> List[dict]:
        async with self._lock:
            ps = list(self._data["players"].values())
        if group is not None:
            ps = [p for p in ps if str(p.get("group")) == str(group)]
        return ps

    # ---- 群启用 ----
    async def enable_group(self, group: str) -> None:
        async with self._lock:
            if str(group) not in self._data["groups"]:
                self._data["groups"].append(str(group))
        await self.save()

    async def groups(self) -> List[str]:
        async with self._lock:
            return list(self._data["groups"])


def now() -> float:
    return time.time()
