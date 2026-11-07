# -*- coding: utf-8 -*-
"""M6 第二批路由 + 电源确认流 单元测试（2026-10-20）。

覆盖：窗口/剪贴板/应用/开机项/锁屏/OCR 命令分支、自然语言新触发、
电源二次确认 + 5s 可取消倒计时（mock ctypes/pc 函数 + 捕获 _reply）。
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")
import plugins.qq_bridge as qb
from plugins.qq_bridge import pc as pc_mod

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(name)
        print("  ✓ %s" % name)
    else:
        _FAIL.append(name)
        print("  ✗ %s  %s" % (name, detail))


class _Ev:
    def __init__(self, user_id, raw_text, is_group=False, group_id=0):
        self.user_id = user_id
        self.raw_text = raw_text
        self.is_group = is_group
        self.group_id = group_id


async def test_pc2() -> None:
    print("[M6 第二批] 路由 + 电源确认流")
    OWNER = 10001
    qb.Config.BOT_OWNER_QQ = (OWNER,)
    qb.Config.PC_ENABLE = True
    qb.Config.PC_KILL_CONFIRM = True

    captured: list = []

    async def _cap(ctx, sess, text, *, template="text", title=""):
        captured.append((title, text))
        return None

    orig_reply = qb._reply
    qb._reply = _cap

    # mock pc 函数（避免真实操作桌面/剪贴板/电源）
    pc_mod.list_windows = lambda: ["Notepad", "Brave"]
    pc_mod.focus_window = lambda t: "已把「%s」提到最前。" % t
    pc_mod.minimize_window = lambda t: "已最小化「%s」。" % t
    pc_mod.close_window = lambda t: "已给「%s」关闭。" % t
    pc_mod.clipboard_get = lambda: "复制的内容xyz"
    pc_mod.clipboard_set = lambda t: "已写入剪贴板（%d 字符）。" % len(t)
    pc_mod.lock_screen = lambda: "已锁屏。"
    pc_mod.sleep_pc = lambda: "正在睡眠…"
    pc_mod.list_installed = lambda limit=30: ["AppA", "AppB"]
    pc_mod.startup_items = lambda: ["qq = qq.exe"]
    pc_mod.launch_app = lambda t: "已尝试启动：%s" % t
    pc_mod.screen_ocr = lambda: ("屏幕识别文字", "")
    power_calls: list = []

    def _fake_do_power(action):
        power_calls.append(action)
        return "已下达%s指令。" % action

    pc_mod._do_power = _fake_do_power

    async def run(raw):
        captured.clear()
        await qb._handle_pc_command(_Ev(OWNER, raw), "t")

    # 窗口
    await run("/pc win")
    check("窗口列表分支", captured and captured[0][0] == "窗口列表"
          and "Notepad" in captured[0][1], str(captured))
    await run("/pc focus notepad")
    check("窗口提前分支", captured and "notepad" in captured[0][1])
    await run("/pc min notepad")
    check("窗口最小化分支", captured and "最小化" in captured[0][1])
    await run("/pc close notepad")
    check("窗口关闭分支", captured and "关闭" in captured[0][1])

    # 剪贴板
    await run("/pc clip")
    check("剪贴板读取分支", captured and "复制的内容xyz" in captured[0][1])
    await run("/pc clipset 你好喵")
    check("剪贴板写入分支", captured and "已写入剪贴板" in captured[0][1])

    # 应用 / 开机项
    await run("/pc apps")
    check("已装程序分支", captured and captured[0][0] == "已装程序")
    await run("/pc startup")
    check("开机自启分支", captured and "qq = qq.exe" in captured[0][1])
    await run("/pc app notepad")
    check("启动应用分支", captured and "已尝试启动：notepad" in captured[0][1])

    # 锁屏 / OCR
    await run("/pc lock")
    check("锁屏分支", captured and "已锁屏" in captured[0][1])
    await run("/pc ocr")
    check("OCR 分支", captured and captured[0][0] == "屏幕 OCR"
          and "屏幕识别文字" in captured[0][1])

    # 电源：二次确认设置
    await run("/pc power 关机")
    pend = qb._pc_pending.get(OWNER)
    check("电源确认待设置", pend is not None and pend[0] == "power"
          and pend[1] == "关机", str(pend))
    check("电源确认回复", captured and "高危确认" in captured[0][1]
          and "关机" in captured[0][1])
    await run("/pc power 重启")
    check("电源重启目标", qb._pc_pending.get(OWNER)[1] == "重启")

    # 电源确认执行（mock sleep 即时，验证倒计时后执行）
    qb._pc_pending[OWNER] = ("power", "关机", __import__("time").time() + 30)
    real_sleep = qb.asyncio.sleep
    qb.asyncio.sleep = lambda s: real_sleep(0)  # 即时，不真等 5s（用真实 sleep(0) 避免自递归）
    captured.clear(); power_calls.clear()
    await qb._handle_pc_command(_Ev(OWNER, "确认"), "t2")
    qb.asyncio.sleep = real_sleep
    check("电源确认后执行", power_calls == ["关机"], str(power_calls))
    check("电源执行回复", captured and "已下达关机指令" in captured[-1][1], str(captured))

    # 电源确认取消（倒计时内取消）
    qb._pc_pending[OWNER] = ("power", "重启", __import__("time").time() + 30)
    qb._pc_power_cancel[OWNER] = False
    power_calls.clear()
    real_sleep = qb.asyncio.sleep

    async def _sleep_cancel(s):
        qb._pc_power_cancel[OWNER] = True  # 第一次 sleep 即取消
        return None

    qb.asyncio.sleep = _sleep_cancel
    captured.clear()
    await qb._handle_pc_command(_Ev(OWNER, "确认"), "t3")
    qb.asyncio.sleep = real_sleep
    check("电源取消不执行", power_calls == [], str(power_calls))
    check("电源取消回复", captured and "已取消重启" in captured[-1][1], str(captured))

    # 自然语言新触发
    captured.clear()
    r = await qb._try_pc_natural(_Ev(OWNER, "剪贴板里是啥"), "t")
    check("NL 剪贴板命中", r is True and captured and "复制的内容xyz" in captured[0][1])
    captured.clear()
    r = await qb._try_pc_natural(_Ev(OWNER, "帮我锁屏"), "t")
    check("NL 锁屏命中", r is True and captured and "已锁屏" in captured[0][1])
    captured.clear()
    r = await qb._try_pc_natural(_Ev(OWNER, "屏幕上的字念给我听"), "t")
    check("NL OCR 命中", r is True and captured and "屏幕识别文字" in captured[0][1])
    r = await qb._try_pc_natural(_Ev(OWNER, "群聊里说电脑", is_group=True), "t")
    check("NL 群聊不命中", r is False)

    qb._reply = orig_reply


if __name__ == "__main__":
    asyncio.run(test_pc2())
    print("\n==== 结果：%d 通过 / %d 失败 ====" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("失败项：", _FAIL)
        sys.exit(1)
    print("ALL_OK")
