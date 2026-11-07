# -*- coding: utf-8 -*-
"""斗破人物「动态形象」——把从游戏本体提取的待机动图接入 QQ 机器人。

资源来源（**全部取自游戏本体，未使用任何外部素材**）：
  * J2ME《斗破苍穹2-双帝之战》`emu/game.jar` → `c/h0`、`c/w0`
  * 《斗破苍穹HD》v1.4 `hd.apk` → `assets/c/h0`、`assets/c/n0`
  * 提取脚本：`D:/AI/doupo/tools/build_avatar_lib.py`
  * 完整 10 人物动作库：`D:/AI/doupo/人物待机动作库/`

全部为**透明背景、无限循环**的 GIF，按境界分档，越高级形象越进阶。
单图 20–105 KB，远低于 QQ 图片体积红线。

设计原则（CLOSE-02）：任何一步失败都返回 None，调用方照常发纯文本/卡片，绝不丢消息。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("qq_bridge.doupo.avatar")

#: 动图资源目录（随插件一起分发）
AVATAR_DIR = Path(__file__).parent / "assets" / "idle"

#: QQ 发图体积上限（字节）。超限则放弃动图，只发卡片，避免发送失败或刷屏
MAX_BYTES = 300 * 1024

#: 等级 -> 动图档位（lv 升序命中最后一个 <= lv 的档）
#  1-9    斗者 ~ 大斗师     J2ME h0     主角持巨剑正面站立
#  10-18  斗灵 ~ 斗王       J2ME w0     主角赤手战斗姿态
#  19-27  斗皇 ~ 斗尊       HD   h0     主角蓄力，双手蓝色能量球
#  28-29  斗圣巅峰 ~ 斗帝   HD   n0     骑乘/降服大型魔兽（终极形态）
#
# 选型说明：HD 版资源分辨率明显高于 J2ME 版，故放在高阶档位；
# J2ME n90（暗紫披风）经提亮+双色描边修复后仍偏糊，仅保留在动作库，不进档位。
TIERS: list[tuple[int, str]] = [
    (1, "h0.gif"),
    (10, "w0.gif"),
    (19, "hd_h0.gif"),
    (28, "hd_n0.gif"),
]

#: 兜底档（任何异常都至少能用这张）
FALLBACK = "h0.gif"


def available() -> bool:
    """动图资源是否可用。"""
    return AVATAR_DIR.is_dir() and any(AVATAR_DIR.glob("*.gif"))


def pick_avatar(lv: int) -> str:
    """按等级选动图文件名。"""
    try:
        lv = int(lv)
    except (TypeError, ValueError):
        lv = 1
    name = TIERS[0][1]
    for threshold, fn in TIERS:
        if lv >= threshold:
            name = fn
    return name


def avatar_bytes(lv: int) -> bytes | None:
    """读取该等级对应的动图字节；缺失/超限/异常一律返回 None。"""
    name = pick_avatar(lv)
    path = AVATAR_DIR / name
    try:
        if not path.is_file():
            path = AVATAR_DIR / FALLBACK
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > MAX_BYTES:
            log.warning("斗破动图 %s 体积 %d B 超过上限 %d B，本次放弃动图",
                        path.name, size, MAX_BYTES)
            return None
        with open(path, "rb") as f:
            data = f.read()
        # 机器校验：必须是合法 GIF，避免把损坏文件发出去
        if not (data[:6] in (b"GIF87a", b"GIF89a")):
            log.warning("斗破动图 %s 不是合法 GIF，放弃", path.name)
            return None
        return data
    except Exception as e:  # noqa: BLE001
        log.warning("读取斗破动图失败（降级为只发卡片）：%s: %s", type(e).__name__, e)
        return None


def avatar_for(p: dict) -> bytes | None:
    """按玩家存档取动图（内部取 lv）。"""
    if not isinstance(p, dict):
        return None
    return avatar_bytes(p.get("lv", 1))
