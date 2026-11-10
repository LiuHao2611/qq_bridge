# -*- coding: utf-8 -*-
"""斗破人物形象资源：动态待机 GIF（建号展示）+ 静态头像（嵌进人物卡）。

资源来源（**全部取自游戏本体，未使用任何外部素材**）：
  * J2ME《斗破苍穹2-双帝之战》`emu/game.jar` → `c/h0`、`c/w0`
  * 《斗破苍穹HD》v1.4 `hd.apk` → `assets/c/h0`、`assets/c/n0`
  * 提取脚本：`D:/AI/doupo/tools/build_avatar_lib.py`
  * 完整 10 人物动作库：`D:/AI/doupo/人物待机动作库/`

全部为**透明背景、无限循环**的 GIF，按境界分档，越高级形象越进阶。
单图 20–105 KB，远低于 QQ 图片体积红线。

2026-11-07 22:xx 用户要求：「人物卡片里不用放动态人物小人了，截图一个头像就行，
放在卡片里」。因此新增「静态头像」分支：运行时取对应档位 GIF 的**首帧**，
按 HEADSHOT_BOXES 裁出头部，用 NEAREST 整数倍放大后居中放进 96×96 透明 PNG，
由 card.build_data() 以 base64 传给人物卡模板绘制在卡片内。

为什么运行时裁、不落新的 PNG 素材文件：本仓 `.gitignore` 明确忽略 `*.png` / `*.gif`
（二进制运行时绝不入库），连原有 idle GIF 都不在 git 里。再塞 4 张 PNG 只会变成
「本机有、clone 下来没有」的幽灵资源。运行时从 GIF 现裁，代码自身可完整上传，
GIF 缺失时模板画占位头像，绝不丢卡片。

设计原则（CLOSE-02）：任何一步失败都返回 None，调用方照常发纯文本/卡片，绝不丢消息。
"""
from __future__ import annotations

import base64
import io
import logging
import os
from functools import lru_cache
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

# ---------------------------------------------------------------------------
# 静态头像（人物卡内嵌）——从上面各档 GIF 的**首帧**裁头部
# ---------------------------------------------------------------------------
#: 卡片里头像框的边长（像素），模板侧 AVATAR_SIZE 必须与此一致
HEADSHOT_SIZE = 96

#: 各档裁剪框（原始 GIF 像素坐标，左/上/右/下）+ 整数放大倍数 + 形象说明。
#: 全部经网格图逐格目视校准：发顶、下巴完整，尽量避开武器/火焰杂边；
#: 放大一律用 NEAREST 整数倍，保持像素画的方块边缘不糊。
HEADSHOT_BOXES: dict[str, tuple[tuple[int, int, int, int], int, str]] = {
    "h0.gif":    ((78, 20, 158, 100), 1, "持剑少年"),
    "w0.gif":    ((56, 4, 128, 76), 1, "赤手斗者"),
    "hd_h0.gif": ((66, 26, 162, 122), 1, "蓄力斗皇"),
    "hd_n0.gif": ((104, 96, 152, 144), 2, "降魔斗帝"),
}


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


# ---------------------------------------------------------------------------
# 静态头像：给「人物 / 属性 / 面板」卡片内嵌用（不再单独发动图小人）
# ---------------------------------------------------------------------------
def headshot_caption(lv: int) -> str:
    """头像下方的形象说明（跟着境界档位变）。"""
    try:
        return HEADSHOT_BOXES.get(pick_avatar(lv), ("", 1, "斗气修行者"))[2]
    except Exception:  # noqa: BLE001
        return "斗气修行者"


@lru_cache(maxsize=8)
def _headshot_b64(gif_name: str) -> str | None:
    """裁出该档 96×96 透明头像并返回 PNG 的 base64；任一步失败返回 None。

    用 lru_cache 缓存（4 档固定），避免每次发人物卡都重新解码 GIF + 编码 PNG。
    """
    spec = HEADSHOT_BOXES.get(gif_name)
    if not spec:
        return None
    try:
        from PIL import Image

        path = AVATAR_DIR / gif_name
        if not path.is_file():
            path = AVATAR_DIR / FALLBACK
        if not path.is_file():
            return None
        im = Image.open(path)
        im.seek(0)                       # 只取首帧：静态头像，不要动画
        box, scale, _label = spec
        crop = im.convert("RGBA").crop(box)
        if scale != 1:
            crop = crop.resize((crop.width * scale, crop.height * scale), Image.NEAREST)
        out = Image.new("RGBA", (HEADSHOT_SIZE, HEADSHOT_SIZE), (0, 0, 0, 0))
        out.alpha_composite(crop, ((HEADSHOT_SIZE - crop.width) // 2,
                                   (HEADSHOT_SIZE - crop.height) // 2))
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:  # noqa: BLE001
        log.warning("裁头像失败（卡片画占位头像）：%s: %s", type(e).__name__, e)
        return None


def headshot_bytes(lv: int) -> bytes | None:
    """该等级对应的静态头像 PNG 字节（供测试 / 其他调用方）。"""
    b64 = _headshot_b64(pick_avatar(lv))
    if not b64:
        return None
    try:
        return base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return None


def headshot_for(p: dict) -> str | None:
    """按玩家存档取头像 base64；非 dict / 资源缺失一律返回 None。

    返回的是**纯 base64 字符串**（不含 data URI 前缀），直接可放进模板参数，
    JSON 序列化友好 —— pic 的磁盘缓存 key 依赖参数可 json.dumps，
    这里绝不能塞 PIL.Image 这类带内存地址的对象（会导致缓存永久 miss）。
    """
    if not isinstance(p, dict):
        return None
    return _headshot_b64(pick_avatar(p.get("lv", 1)))
