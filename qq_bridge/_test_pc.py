# -*- coding: utf-8 -*-
"""M6 宿主机管家单元测试（2026-10-20）。

纪律：纯函数 mock 外部依赖（psutil/pynvml/mss）+ 路由层 import 真实插件、
捕获 _reply、打桩 kill_process。覆盖：安全层 / 系统概览 / 进程 / 端口 /
kill / 温度 / 目录白名单 / 截图 / 自然语言直通车 / 高危确认流。
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

sys.path.insert(0, "src")

# ---- 先建 mss 假模块，让 pc.screenshot 真实路径可跑（headless 无显示器）----
_mss = types.ModuleType("mss")
_mss.tools = types.ModuleType("mss.tools")


class _FakeMSS:
    monitors = [{"left": 0, "top": 0, "width": 100, "height": 100}]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def grab(self, mon):
        return types.SimpleNamespace(rgb=b"x" * (100 * 100 * 3), size=(100, 100))


_mss.mss = _FakeMSS


def _to_png_big(rgb, size):
    return b"\x89PNG" + b"\x00" * 2048  # >1024 字节，绕过黑屏校验


def _to_png_small(rgb, size):
    return b"\x89P"  # <1024，触发黑屏诚实报错


_mss.tools.to_png = _to_png_big
sys.modules["mss"] = _mss
sys.modules["mss.tools"] = _mss.tools

import plugins.qq_bridge as qb
from plugins.qq_bridge import pc as pc_mod

# ---- fake psutil / pynvml 注入 pc 模块 ----
_NS = types.SimpleNamespace


class _FakeProc:
    def __init__(self, name, pid, cpu, mem):
        self.info = {"name": name, "pid": pid}
        self._cpu = cpu
        self._mem = mem

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def oneshot(self):
        return self

    def cpu_percent(self, interval=None):
        return self._cpu

    def memory_info(self):
        return _NS(rss=self._mem)

    def name(self):
        return self.info["name"]


class _FakePsutil:
    @staticmethod
    def cpu_percent(interval=None):
        return 35.0

    @staticmethod
    def cpu_count():
        return 12

    @staticmethod
    def virtual_memory():
        return _NS(used=8e9, total=16e9, percent=50.0)

    @staticmethod
    def disk_partitions(all=False):
        return [_NS(mountpoint="C:\\")]

    @staticmethod
    def disk_usage(p):
        return _NS(used=100e9, total=500e9, percent=20.0)

    @staticmethod
    def net_io_counters():
        return _NS(bytes_sent=1e9, bytes_recv=2e9)

    @staticmethod
    def boot_time():
        return time.time() - 3600

    @staticmethod
    def net_connections(kind="tcp"):
        return [_NS(laddr=_NS(port=8080), pid=1234, status="LISTEN")]

    @staticmethod
    def process_iter(attrs=None):
        return iter([
            _FakeProc("notepad.exe", 111, 12.0, 50e6),
            _FakeProc("python.exe", 222, 5.0, 200e6),
        ])

    class Process:
        def __init__(self, pid):
            # 测试用：未知 PID 抛 NoSuchProcess（对齐 kill 不存在PID 的诚实返回）
            raise _FakePsutil.NoSuchProcess("no such %s" % pid)

        def name(self):
            return self._name

        def terminate(self):
            pass

        def wait(self, timeout=None):
            pass

        def kill(self):
            pass

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass


_fake_pynvml = _NS(
    NVML_TEMPERATURE_GPU=0,
    nvmlInit=lambda: None,
    nvmlDeviceGetCount=lambda: 1,
    nvmlDeviceGetHandleByIndex=lambda i: "h",
    nvmlDeviceGetName=lambda h: "FakeGPU 1660Ti",
    nvmlDeviceGetTemperature=lambda h, t: 65,
    nvmlDeviceGetUtilizationRates=lambda h: _NS(gpu=50),
    nvmlDeviceGetMemoryInfo=lambda h: _NS(used=2e9, total=6e9),
)


def _fake_nvml():
    return _fake_pynvml


pc_mod.psutil = _FakePsutil
pc_mod._nvml = _fake_nvml

# ===========================================================================
# 测试框架
# ===========================================================================
_PASS = []
_FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(name)
        print("  ✓ %s" % name)
    else:
        _FAIL.append(name)
        print("  ✗ %s  %s" % (name, detail))


# ===========================================================================
# Part A：pc.py 纯函数 + 安全层
# ===========================================================================
def test_pc_pure() -> None:
    print("[A] pc.py 纯函数 + 安全层")

    # 1. 安全层
    ok, _ = pc_mod.allowed(is_private=False, user_id=1, is_owner=True)
    check("群聊一律拒绝", not ok)
    ok, _ = pc_mod.allowed(is_private=True, user_id=2, is_owner=False)
    check("非主人私聊拒绝", not ok)
    ok, r = pc_mod.allowed(is_private=True, user_id=3, is_owner=True)
    check("私聊主人放行", ok and r == "ok")

    # 2. 系统概览
    ov = pc_mod.system_overview()
    check("系统概览含 CPU", "CPU" in ov, ov[:60])
    check("系统概览含 GPU", "GPU" in ov, ov[:60])

    # 3. 进程 Top
    tp = pc_mod.top_processes(5)
    check("进程Top含标题", "CPU Top 5" in tp)
    check("进程Top含进程名", "notepad.exe" in tp, tp[:60])

    # 4. 端口查询
    fp = pc_mod.find_port(8080)
    check("端口查询命中PID", "8080" in fp and "PID" in fp, fp[:60])
    check("端口越界报错", "范围" in pc_mod.find_port(99999))

    # 5. kill 不存在的 PID（安全，不真杀）
    kp = pc_mod.kill_process("999999")
    check("kill 不存在PID诚实", "已经没了" in kp, kp)

    # 6. 温度（GPU 走假 pynvml）
    tm = pc_mod.temperatures()
    check("温度含GPU行", "GPU" in tm and "65°C" in tm, tm[:60])
    check("温度诚实降级CPU", "LibreHardwareMonitor" in tm, tm[:60])

    # 7. 目录体积（用临时小目录，避免递归 D:\AI 大目录阻塞测试）
    import tempfile
    _td = tempfile.mkdtemp()
    _f1 = types.SimpleNamespace(name="a.txt")
    with open(_td + "/a.txt", "w") as _fh:
        _fh.write("hello" * 100)
    dt = pc_mod.disk_tree(_td)
    check("目录速览返回文本", isinstance(dt, str) and len(dt) > 0, dt[:60])
    check("目录不存在报错", "不存在" in pc_mod.disk_tree("Z:\\no_such_path_xyz"))

    # 8. 宿主机信息
    hi = pc_mod.hostname_info()
    check("宿主机信息含标题", "宿主机" in hi, hi[:60])

    # 9. 截图（真实路径：mss 被 mock）
    png, err = asyncio.run(pc_mod.screenshot())
    check("截图成功返回bytes", png is not None and len(png) > 1024, err)
    _mss.tools.to_png = _to_png_small
    png2, err2 = asyncio.run(pc_mod.screenshot())
    check("截图过小诚实报错", png2 is None and "黑屏" in err2, err2)
    _mss.tools.to_png = _to_png_big  # 复原


# ===========================================================================
# Part B：路由守卫 + 自然语言直通车 + 高危确认流（真实插件）
# ===========================================================================
class _Ev:
    def __init__(self, user_id, raw_text, is_group=False, group_id=0):
        self.user_id = user_id
        self.raw_text = raw_text
        self.is_group = is_group
        self.group_id = group_id


async def test_pc_route() -> None:
    print("[B] 路由守卫 + 自然语言 + 高危确认流")
    OWNER = 10001
    qb.Config.BOT_OWNER_QQ = (OWNER,)
    qb.Config.PC_ENABLE = True
    qb.Config.PC_KILL_CONFIRM = True
    qb.Config.PC_DISK_ROOTS = r"D:\AI"
    qb.Config.PC_SHOT_MAX_MB = 8

    captured: list = []

    async def _cap(ctx, sess, text, *, template="text", title=""):
        captured.append((title, text))
        return None

    orig_reply = qb._reply
    qb._reply = _cap

    # 10. 目录白名单
    check("目录白名单命中", qb._pc_disk_allowed(r"D:\AI\sub"))
    check("目录白名单拒绝越界", not qb._pc_disk_allowed(r"C:\Windows"))

    # 11. 守卫
    check("守卫：群聊拒", not qb._pc_guard(_Ev(OWNER, "/pc", is_group=True)))
    check("守卫：非主人拒", not qb._pc_guard(_Ev(99999, "/pc")))
    check("守卫：主人私聊过", qb._pc_guard(_Ev(OWNER, "/pc")))

    # 12. 自然语言直通车
    captured.clear()
    r = await qb._try_pc_natural(_Ev(OWNER, "电脑咋样"), "t")
    check("自然语言主人命中", r is True and captured and "宿主机体检" in captured[0][0])
    check("自然语言群聊False", await qb._try_pc_natural(_Ev(OWNER, "电脑咋样", is_group=True), "t") is False)
    check("自然语言非主人False", await qb._try_pc_natural(_Ev(99999, "电脑咋样"), "t") is False)

    # 13a. kill 触发确认
    captured.clear()
    qb._pc_pending.clear()
    await qb._handle_pc_command(_Ev(OWNER, "/pc kill notepad"), "t1")
    check("kill 进入待确认", OWNER in qb._pc_pending)
    check("kill 回复高危确认", captured and "高危确认" in captured[0][1], str(captured))

    # 13b. 确认执行
    captured.clear()
    calls = []

    def _fake_kill(target):
        calls.append(target)
        return "已送走：%s" % target

    qb.pc_mod.kill_process = _fake_kill
    await qb._handle_pc_command(_Ev(OWNER, "确认"), "t2")
    check("确认后执行kill", calls == ["notepad"])
    check("确认后回复结果", captured and "已送走" in captured[0][1], str(captured))
    check("确认后待确认清空", OWNER not in qb._pc_pending)

    # 13c. 过期确认
    qb._pc_pending[OWNER] = ("kill", "x", time.time() - 5)
    captured.clear()
    await qb._handle_pc_command(_Ev(OWNER, "确认"), "t3")
    check("过期确认被拒", captured and "没有待确认" in captured[0][1], str(captured))

    qb._reply = orig_reply


if __name__ == "__main__":
    test_pc_pure()
    asyncio.run(test_pc_route())
    print("\n==== 结果：%d 通过 / %d 失败 ====" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("失败项：", _FAIL)
        sys.exit(1)
    print("ALL_OK")
