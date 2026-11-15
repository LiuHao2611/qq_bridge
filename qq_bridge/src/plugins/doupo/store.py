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

#: 存档结构版本。3 = 原著 13 阶九星制（斗之气 1-9 … 斗帝 103）。
#: 1 = 最初「每境界 3 星 / 满级 29」；2 = 九星制但缺 斗之气/斗尊巅峰/半圣（满级 83）。
#: 旧存档在 load 时按 `cfg.migrate_lv(old, src_ver)` 就地换算，并按经验曲线
#: 等比缩放修为（保住「离下一级还差多少」的手感），逐玩家打 `lv_ver` 标记 ——
#: 即使写盘失败也不会二次换算（换算不可重复执行）。
SCHEMA_VER = 3


def _log(level: str, msg: str) -> None:
    """写日志：优先 nonebot.log（线上 loguru），裸跑单测时退回 stdlib。

    单测会桩掉 nonebot（见 test_core.py 头部），此处不能硬依赖。
    只做日志降级，不影响任何业务分支。
    """
    try:
        from nonebot.log import logger as _lg
    except Exception:  # noqa: BLE001
        import logging

        _lg = logging.getLogger("doupo")
    getattr(_lg, level)(msg)


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
            self._migrate_if_needed()
            self._loaded = True

    def _migrate_if_needed(self) -> None:
        """旧存档 → 原著 13 阶九星制（斗之气 1-9 … 斗帝 103）。

        逐玩家打 `lv_ver=SCHEMA_VER` 标记后再换 `lv`：换算**不可重复执行**
        （20 级再算一次会被当成旧 20 级，跑飞），所以哪怕写盘失败，
        下次启动时旧值仍在磁盘上、没有标记，也就不会双重换算。

        修为也要跟着缩放：经验曲线是 400·lv²/9，等级整体抬高后
        「离下一级还差多少」的占比会变，直接沿用原数值会让老玩家凭空变穷/变富。
        按 need_new / need_old 等比放大，手感不变。
        """
        players = self._data.get("players") or {}
        migrated = []
        for qq, p in players.items():
            if not isinstance(p, dict):
                continue
            cur_ver = int(p.get("lv_ver", 1) or 1)
            if cur_ver >= SCHEMA_VER:
                continue
            old = int(p.get("lv", 1) or 1)
            new = cfg.migrate_lv(old, cur_ver)
            # 旧版经验需求：1 版是 400·lv²，2 版起是 400·lv²/9
            old_need = (400 * old * old) if cur_ver <= 1 else (400 * old * old // 9)
            new_need = cfg.exp_need(new)
            if old_need > 0 and new_need > 0:
                p["exp"] = int(int(p.get("exp", 0) or 0) * new_need // old_need)
            p["lv"] = new
            p["lv_ver"] = SCHEMA_VER
            if new != old:
                migrated.append(f"{qq}:{old}->{new}")
        self._data["ver"] = SCHEMA_VER
        if migrated:
            _log("info", "斗破存档升级到原著13阶 lv=" + ",".join(migrated))
            try:
                self._write()
            except Exception as e:  # noqa: BLE001 — 写盘失败不能让机器人起不来
                _log("error", "斗破存档升级后写盘失败（内存已生效，下次启动会重算）："
                     + type(e).__name__ + ": " + str(e))

    def _write(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    async def save(self) -> None:
        async with self._lock:
            self._write()

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
