# -*- coding: utf-8 -*-
"""M6 WGC 黑屏修复（DXGI Desktop Duplication）单元测试 + 真机抽样（2026-10-20）。

覆盖：
  A. 纯函数：_is_blackish（全黑/非黑/损坏PNG 容错）；
  B. pc.screenshot_dxgi 未装组件时诚实报错（mock import）；
  C. __init__._pc_shot_bytes 方法选择：auto(优先DXGI, 失败/全黑回退mss) /
     dxgi(仅DXGI, 失败不回退) / mss(仅mss)；
  D. 真机 DXGI 捕获（仅当 windows-capture 可导入）：产出非黑屏 PNG。
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")
import plugins.qq_bridge as qb
from plugins.qq_bridge import pc as pc_mod
from io import BytesIO
from PIL import Image

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(name)
        print("  ✓ %s" % name)
    else:
        _FAIL.append(name)
        print("  ✗ %s  %s" % (name, detail))


def _black_png() -> bytes:
    im = Image.new("RGB", (32, 32), (0, 0, 0))
    b = BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _color_png() -> bytes:
    im = Image.new("RGB", (32, 32), (200, 100, 50))
    b = BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


async def test_pc4() -> None:
    print("[M6 截图 WGC/DXGI] A. _is_blackish")
    check("全黑→True", qb._is_blackish(_black_png()))
    check("彩色→False", not qb._is_blackish(_color_png()))
    check("损坏PNG容错", not qb._is_blackish(b"not a png"))

    print("[M6 截图 WGC/DXGI] B. screenshot_dxgi 未装组件")
    real_wc = sys.modules.get("windows_capture")
    sys.modules["windows_capture"] = None  # 让函数内 import 抛 ImportError
    try:
        data, err = pc_mod.screenshot_dxgi()
        check("未装→诚实报错", data is None and "windows-capture" in err, str((data, err)))
    finally:
        if real_wc is None:
            sys.modules.pop("windows_capture", None)
        else:
            sys.modules["windows_capture"] = real_wc

    print("[M6 截图 WGC/DXGI] C. _pc_shot_bytes 方法选择")
    OWNER = 10001
    qb.Config.BOT_OWNER_QQ = (OWNER,)
    qb.Config.PC_ENABLE = True

    orig_dxgi = pc_mod.screenshot_dxgi
    orig_mss = pc_mod.screenshot
    try:
        # auto + DXGI 成功 → 返回 DXGI 图
        pc_mod.screenshot_dxgi = lambda: (b"dxgi-bytes", "")
        async def _mss_ok():
            return (b"mss-bytes", "")
        pc_mod.screenshot = _mss_ok
        qb.Config.PC_SCREENSHOT_METHOD = "auto"
        d, e = await qb._pc_shot_bytes()
        check("auto DXGI 成功", d == b"dxgi-bytes" and not e, str((len(d or b""), e)))

        # auto + DXGI 失败 → 回退 mss
        pc_mod.screenshot_dxgi = lambda: (None, "dxgi err")
        d, e = await qb._pc_shot_bytes()
        check("auto DXGI 失败回退 mss", d == b"mss-bytes", str((len(d or b""), e)))

        # auto + DXGI 全黑 → 回退 mss
        pc_mod.screenshot_dxgi = lambda: (_black_png(), "")
        d, e = await qb._pc_shot_bytes()
        check("auto DXGI 全黑回退 mss", d == b"mss-bytes", str((len(d or b""), e)))

        # dxgi 模式 + DXGI 失败 → 不回退，报错
        qb.Config.PC_SCREENSHOT_METHOD = "dxgi"
        pc_mod.screenshot_dxgi = lambda: (None, "dxgi err")
        d, e = await qb._pc_shot_bytes()
        check("dxgi 模式失败不回退", d is None and "dxgi err" in e, str((d, e)))
        # dxgi 模式 + DXGI 成功（非黑）→ 返回 DXGI
        pc_mod.screenshot_dxgi = lambda: (_color_png(), "")
        d, e = await qb._pc_shot_bytes()
        check("dxgi 模式成功", d == _color_png() and not e, str((len(d or b""), e)))

        # mss 模式 → 只用 mss
        qb.Config.PC_SCREENSHOT_METHOD = "mss"
        pc_mod.screenshot_dxgi = lambda: (_color_png(), "")  # 即便 DXGI 可用也应忽略
        d, e = await qb._pc_shot_bytes()
        check("mss 模式忽略 DXGI", d == b"mss-bytes" and not e, str((len(d or b""), e)))
    finally:
        pc_mod.screenshot_dxgi = orig_dxgi
        pc_mod.screenshot = orig_mss

    print("[M6 截图 WGC/DXGI] D. 真机 DXGI 捕获（仅当组件可导入）")
    try:
        import windows_capture  # noqa: F401
        real_dxgi = pc_mod.screenshot_dxgi
        try:
            data, err = pc_mod.screenshot_dxgi()
            check("真机 DXGI 捕获", data is not None and not err and not qb._is_blackish(data),
                  str((None if data is None else len(data), err)))
            if data:
                print("    真机截图字节数:", len(data), "全黑:", qb._is_blackish(data))
        finally:
            pc_mod.screenshot_dxgi = real_dxgi
    except ImportError:
        print("  ⏭ windows-capture 未安装，跳过真机捕获（CI/无组件环境）")


if __name__ == "__main__":
    asyncio.run(test_pc4())
    print("\n==== 结果：%d 通过 / %d 失败 ====" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("失败项：", _FAIL)
        sys.exit(1)
    print("ALL_OK")
