# -*- coding: utf-8 -*-
"""pc —— 宿主机管家（M6 第一批，2026-10-20）。

调研来源（docs/ai_butler_roadmap.md §10）：
  - Desktop Commander MCP 23 工具分类学（监控/窗口/桌面/应用）；
  - computer-control-mcp：WGC 截图黑屏修复（第二批）；
  - dsh-qq-remote：QQ→宿主机控制链路先例。

安全模型（10.1 硬规则，**最高优先级**）：
  - 全部能力仅限「私聊 + 主人」——群聊/非主人调用泄露的是主人硬件配置；
  - 高危操作（kill/删除/电源）由调用方（__init__.py）叠加二次确认；
  - 本模块不 import nonebot：可单测；权限判定由参数注入。

平台实测（Windows 10 + psutil 7.2.2）：
  - psutil **无** `sensors_temperatures`（AttributeError）→ CPU 温度探测
    LibreHardwareMonitor HTTP(:8085)，未运行诚实告知；GPU 温度走 pynvml
    （nvidia-ml-py，本机 GTX 1660 Ti 实测可用）；
  - 台式机无 `sensors_battery` → 探测不到时输出「台式机」而非报错；
  - 截图用 mss（纯 Python）；WGC（GPU 窗口黑屏）列第二批。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
import time
from pathlib import Path

import psutil

# ---- Windows API（M6 第二批：窗口/剪贴板/电源/键鼠，零新依赖）----
# 非 Windows 平台这些为 None，所有相关函数入口先判 _WIN 诚实降级。
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    _u32 = ctypes.windll.user32
    _k32 = ctypes.windll.kernel32
    # 关键：HWND 是 64 位指针，ctypes 默认 c_int 仅 32 位——大 HWND（>2^31）
    # 会 OverflowError 导致窗口操作（focus/min/close/GetWindowText）崩溃。
    # 显式声明 argtypes 为 HWND（指针宽）一次性解决所有调用点。
    _HWND = wintypes.HWND
    _u32.IsWindowVisible.argtypes = [_HWND]
    _u32.GetWindowTextLengthW.argtypes = [_HWND]
    _u32.GetWindowTextW.argtypes = [_HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowRect.argtypes = [_HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.ShowWindow.argtypes = [_HWND, ctypes.c_int]
    _u32.SetForegroundWindow.argtypes = [_HWND]
    _u32.PostMessageW.argtypes = [_HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    # 内存/剪贴板句柄是 64 位指针，默认 c_int restype 会截断成 0 → memmove 写空指针崩溃
    _k32.GlobalAlloc.restype = ctypes.c_void_p
    _k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _k32.GlobalLock.argtypes = [ctypes.c_void_p]
    _k32.GlobalLock.restype = ctypes.c_void_p
    _k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _k32.GlobalUnlock.restype = wintypes.BOOL
    _u32.OpenClipboard.argtypes = [_HWND]
    _u32.OpenClipboard.restype = wintypes.BOOL
    _u32.EmptyClipboard.restype = wintypes.BOOL
    _u32.CloseClipboard.restype = wintypes.BOOL
    _u32.GetClipboardData.argtypes = [wintypes.UINT]
    _u32.GetClipboardData.restype = ctypes.c_void_p
    _u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    _u32.SetClipboardData.restype = ctypes.c_void_p
else:
    ctypes = None  # type: ignore
    wintypes = None  # type: ignore
    _u32 = None
    _k32 = None

from .config import Config, get_logger, jlog

log = get_logger()

#: 截图串行锁（防并发截屏打满磁盘/互相踩）
_shot_lock = asyncio.Lock()

#: pynvml 懒加载（import 失败/无 NVIDIA 驱动时诚实降级）
_nvml_ok: bool | None = None


def _fmt_bytes(n: int | float | None) -> str:
    if n is None:
        return "—"
    g = float(n) / 1024 ** 3
    return "%.1f GB" % g if g >= 1 else "%.0f MB" % (float(n) / 1024 ** 2)


def _fmt_uptime(sec: float) -> str:
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append("%d 天" % d)
    if h:
        parts.append("%d 小时" % h)
    parts.append("%d 分钟" % m)
    return "".join(parts)


# ===========================================================================
# 安全层
# ===========================================================================
def allowed(is_private: bool, user_id, is_owner: bool) -> tuple[bool, str]:
    """宿主机管家调用鉴权。返回 (ok, reason)。

    规则（roadmap 10.1）：
      1. 非私聊（群里）→ 一律拒绝（硬件配置是主人隐私）；
      2. 非主人 → 拒绝（`PC_OWNER_ONLY=true` 时；false 时白名单 QQ 放行——
         白名单由调用方经 Config.PRIVATE_WHITELIST 判断后以 is_owner 传入）。
    """
    if not is_private:
        return False, "group_denied"   # 群聊免谈
    if not is_owner:
        return False, "not_owner"
    return True, "ok"


# ===========================================================================
# 系统概览
# ===========================================================================
def system_overview() -> str:
    """一句话体检：CPU / 内存 / 磁盘 / 网络 / uptime / GPU。"""
    cpu = psutil.cpu_percent(interval=0.4)
    cpu_cnt = psutil.cpu_count()
    vm = psutil.virtual_memory()
    lines = [
        "CPU：%.0f%%（%d 核）" % (cpu, cpu_cnt or 0),
        "内存：%s / %s（%.0f%%）" % (
            _fmt_bytes(vm.used), _fmt_bytes(vm.total), vm.percent),
    ]
    try:
        for part in psutil.disk_partitions(all=False):
            if not part.mountpoint:
                continue
            try:
                du = psutil.disk_usage(part.mountpoint)
                lines.append("磁盘 %s：%s / %s（%.0f%%）" % (
                    part.mountpoint.rstrip("\\") or part.mountpoint,
                    _fmt_bytes(du.used), _fmt_bytes(du.total), du.percent))
            except Exception:  # noqa: BLE001 — 单分区失败不挡整体（光驱等）
                continue
    except Exception:  # noqa: BLE001
        pass
    try:
        io1 = psutil.net_io_counters()
        lines.append("网络：↑ %s / ↓ %s（累计）" % (
            _fmt_bytes(io1.bytes_sent), _fmt_bytes(io1.bytes_recv)))
    except Exception:  # noqa: BLE001
        pass
    try:
        lines.append("开机时长：%s" % _fmt_uptime(time.time() - psutil.boot_time()))
    except Exception:  # noqa: BLE001
        pass
    gpu = gpu_info()
    if gpu:
        lines.append(gpu)
    return "\n".join(lines)


def top_processes(n: int = 5) -> str:
    """按 CPU 排序的 Top N 进程（name / pid / cpu% / mem MB）。

    用两次采样法（psutil 标准范式）：先对全部进程打一次 cpu_percent 种子，
    间隔 ~0.6s 再取一次 → 才是真实区间占用。单次 interval=None 对多数进程
    返回 0%（首次无基线），会让「谁占 CPU」答非所问（System Idle 全 0%）。
    """
    n = max(1, min(20, int(n or 5)))
    seeded = []
    for p in psutil.process_iter(["name", "pid"]):
        try:
            # System Idle Process 的 cpu_percent = 100%×核数（跨核聚合的空闲值），
            # 对用户无意义，监控工具惯例直接排除。
            if (p.info.get("name") or "") == "System Idle Process":
                continue
            p.cpu_percent(interval=None)  # 种子
            seeded.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:  # noqa: BLE001
            continue
    time.sleep(0.6)
    rows = []
    for p in seeded:
        try:
            with p.oneshot():
                cpu = p.cpu_percent(interval=None)  # 第二次 = 区间占用
                mem = p.memory_info().rss
            rows.append((cpu, mem, p.info["name"] or "?", p.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda x: x[0], reverse=True)
    lines = ["CPU Top %d：" % n]
    for cpu, mem, name, pid in rows[:n]:
        lines.append("· %s（PID %d）CPU %.1f%% · 内存 %.0f MB"
                     % (name[:24], pid, cpu, mem / 1024 ** 2))
    return "\n".join(lines)


def find_port(port: int) -> str:
    """查询端口被谁占用（TCP）。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return "端口得是数字。"
    if not (1 <= port <= 65535):
        return "端口范围 1-65535。"
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception as e:  # noqa: BLE001 — 需管理员权限的场景
        return "查不了端口（%s）——可能要管理员权限。" % type(e).__name__
    hits = [c for c in conns if c.laddr and c.laddr.port == port]
    if not hits:
        return "端口 %d 没被占用。" % port
    lines = ["端口 %d 被占用：" % port]
    seen = set()
    for c in hits:
        pid = c.pid
        name = "?"
        if pid and pid not in seen:
            seen.add(pid)
            try:
                name = psutil.Process(pid).name()
            except Exception:  # noqa: BLE001
                name = "?"
            lines.append("· %s（PID %d）状态 %s" % (name[:24], pid, c.status))
        elif not pid:
            lines.append("· 未知进程（无 PID，状态 %s）" % c.status)
    return "\n".join(lines)


def kill_process(target: str) -> str:
    """按 PID 或进程名结束进程。**高危：调用方必须先过确认流。**"""
    target = (target or "").strip()
    if not target:
        return "告诉本喵杀哪个（PID 或名字）。"
    killed = []
    if target.isdigit():
        pid = int(target)
        try:
            p = psutil.Process(pid)
            name = p.name()
            p.terminate()
            try:
                p.wait(timeout=3)
            except Exception:  # noqa: BLE001 — 3 秒不退就下狠手
                p.kill()
            killed.append("%s（PID %d）" % (name, pid))
        except psutil.NoSuchProcess:
            return "PID %d 已经没了。" % pid
        except psutil.AccessDenied:
            return "杀不了 PID %d（权限不够）。" % pid
    else:
        low = target.lower()
        for p in psutil.process_iter(["name", "pid"]):
            try:
                if low in (p.info["name"] or "").lower():
                    p.terminate()
                    killed.append("%s（PID %d）" % (p.info["name"], p.info["pid"]))
            except Exception:  # noqa: BLE001 — 单个失败不挡其它
                continue
    if not killed:
        return "没找到「%s」对应的进程。" % target
    return "已送走：%s" % "、".join(killed[:8])


# ===========================================================================
# GPU / 温度
# ===========================================================================
def _nvml():
    """懒加载 pynvml；不可用返回 None（调用方诚实降级）。"""
    global _nvml_ok
    if _nvml_ok is False:
        return None
    try:
        import pynvml
        if _nvml_ok is None:
            pynvml.nvmlInit()
            _nvml_ok = True
        return pynvml
    except Exception as e:  # noqa: BLE001 — 无 NVIDIA/驱动异常
        _nvml_ok = False
        log.info(jlog("pc_nvml_unavailable", error=type(e).__name__))
        return None


def gpu_info() -> str | None:
    """GPU 温度/负载/显存一行摘要；无 NVIDIA 返回 None。"""
    nvml = _nvml()
    if nvml is None:
        return None
    try:
        n = nvml.nvmlDeviceGetCount()
        if not n:
            return None
        h = nvml.nvmlDeviceGetHandleByIndex(0)
        name = nvml.nvmlDeviceGetName(h)
        temp = nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)
        util = nvml.nvmlDeviceGetUtilizationRates(h).gpu
        mem = nvml.nvmlDeviceGetMemoryInfo(h)
        return "GPU：%s %d°C · 负载 %d%% · 显存 %d/%d MiB" % (
            name, temp, util, mem.used // 1024 ** 2, mem.total // 1024 ** 2)
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("pc_gpu_info_failed", error=type(e).__name__))
        return None


def _cpu_temperatures() -> list[str]:
    """CPU 温度：LibreHardwareMonitor HTTP(:8085) 探测；未运行返回空列表。"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8085/data.json", timeout=1.5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — LHM 没开是常态，静默走诚实降级
        return []

    out: list[str] = []

    def _walk(node):
        text = str(node.get("Text", ""))
        val = node.get("Value")
        if "CPU Package" in text or text == "CPU Core":
            try:
                out.append("%s %.0f°C" % (text, float(str(val).replace(" °C", ""))))
            except (TypeError, ValueError):
                pass
        for ch in node.get("Children") or []:
            _walk(ch)

    try:
        for hw in data.get("Children") or []:
            _walk(hw)
    except Exception:  # noqa: BLE001
        return []
    return out[:4]


def temperatures() -> str:
    """温度报告：GPU（pynvml）+ CPU（LHM）；都取不到就诚实说明。"""
    lines: list[str] = []
    gpu = gpu_info()
    if gpu:
        lines.append(gpu)
    cpu = _cpu_temperatures()
    if cpu:
        lines.append("CPU：" + " · ".join(cpu))
    elif not lines:
        return ("温度看不了：GPU 没读到（非 NVIDIA？），CPU 温度要装 "
                "LibreHardwareMonitor 并开着（它把数据挂在 127.0.0.1:8085）。"
                "本喵不编数据。")
    elif not cpu:
        lines.append("CPU 温度要装 LibreHardwareMonitor 并开着才能读。")
    return "\n".join(lines)


# ===========================================================================
# 截图
# ===========================================================================
async def screenshot() -> tuple[bytes | None, str]:
    """全屏截图 → (PNG bytes, err)。串行锁防并发；mss 失败显式报错。

    注意（roadmap 10.2）：需要交互式桌面会话——服务化部署时会拿到黑屏，
    届时 mss 仍可能成功但内容为空，交由上层校验（文件过小即拒绝）。
    """
    async with _shot_lock:
        try:
            import mss
            import mss.tools

            def _grab() -> bytes:
                with mss.mss() as sct:
                    monitor = sct.monitors[0] if sct.monitors else None
                    raw = sct.grab(monitor) if monitor else None
                    if raw is None:
                        raise RuntimeError("no monitor")
                    return mss.tools.to_png(raw.rgb, raw.size)

            png = await asyncio.to_thread(_grab)
            if len(png) < 1024:
                return None, "截到的图小得不对劲（黑屏？服务化部署没有桌面会话）"
            return png, ""
        except ImportError:
            return None, "截图组件没装（mss）。"
        except Exception as e:  # noqa: BLE001 — CLOSE-02：显式失败
            return None, "截图失败（%s）" % type(e).__name__


def screenshot_dxgi() -> tuple[bytes | None, str]:
    """全屏截图（DXGI Desktop Duplication）→ (PNG bytes, err)。

    M6 WGC 黑屏修复（2026-10-20）：mss 走 GDI/BitBlt，对 GPU 全屏/硬件加速窗口
    只能截到黑屏；**DXGI Desktop Duplication** 抓取桌面合成器输出（含 D3D/OpenGL
    全屏内容），是真机能截到非黑屏图的方案。

    实现用 `windows-capture` 库的 DxgiDuplicationSession（Rust 封装，内部处理好
    D3D11/线程，比手写 winrt WGC 健壮）。注意：同库的 WindowsCapture(WGC) 在本机
    Win10 21H2 因「Toggling the capture border is not supported」整体不可用，
    故只用 DXGI 路径；窗口级 GPU 捕获仍受此 OS 限制（window_screenshot 维持 mss）。

    同步阻塞（最长约 5s 等帧），调用方用 asyncio.to_thread 包一层。
    """
    try:
        from windows_capture import DxgiDuplicationSession
    except Exception as e:  # noqa: BLE001 — 组件未装 → 诚实告知，交给上层回退 mss
        return None, "windows-capture 未装（DXGI 捕获不可用）：%s" % type(e).__name__
    import os
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "pc_dxgi_%d.png" % os.getpid())
    try:
        sess = DxgiDuplicationSession()
        # 多取几帧、优先选非黑帧：DXGI 首帧偶为空白/黑帧（桌面尚未重绘），
        # 单取首帧会截到黑图；轮询若干次，一旦拿到非黑帧即采用，最稳。
        chosen: bytes | None = None
        last_path: str | None = None
        for _ in range(12):
            f = sess.acquire_frame(timeout_ms=500)
            if f is None:
                continue
            try:
                f.save_as_image(tmp)
            except Exception:  # noqa: BLE001 — 该帧写不出，跳过
                continue
            if not os.path.isfile(tmp):
                continue
            # 非黑帧优先：立即采用并结束
            if not _dxgi_frame_black(tmp):
                with open(tmp, "rb") as fh:
                    chosen = fh.read()
                break
            last_path = tmp
        if chosen is None:
            # 全程只截到黑帧（可能桌面确为黑/锁屏，或 DXGI 拿不到内容）
            if last_path and os.path.isfile(last_path):
                with open(last_path, "rb") as fh:
                    chosen = fh.read()
        if not chosen:
            return None, "DXGI 未在限时内取到帧（桌面可能静止或被锁屏）"
        if len(chosen) < 1024:
            return None, "DXGI 截到的图过小（黑屏？）"
        return chosen, ""
    except Exception as e:  # noqa: BLE001 — CLOSE-02
        return None, "DXGI 捕获失败（%s）" % type(e).__name__
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _dxgi_frame_black(png_path: str) -> bool:
    """判断 DXGI 写出的 PNG 是否基本全黑（黑屏/空白帧）。"""
    try:
        from PIL import Image
        im = Image.open(png_path).convert("RGB")
        px = list(im.getdata())
        if not px:
            return True
        return all(r == 0 and g == 0 and b == 0 for (r, g, b) in px[:20000])
    except Exception:  # noqa: BLE001 — 解析失败当非黑，交给上层按大小校验
        return False


def disk_tree(path: str, depth: int = 2) -> str:
    """目录体积速览（M6 第二批的文件管理预研，先落地查询态）。"""
    p = Path(path)
    if not p.exists():
        return "路径不存在：%s" % path
    if p.is_file():
        return "%s：%s" % (p.name, _fmt_bytes(p.stat().st_size))
    rows = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: -(_size(x))):
            rows.append("· %s %s" % (child.name[:28], _fmt_bytes(_size(child))))
            if len(rows) >= 12:
                break
    except Exception as e:  # noqa: BLE001
        return "读不了目录（%s）" % type(e).__name__
    total = _fmt_bytes(_size(p))
    return "%s 共 %s：\n%s" % (str(p), total, "\n".join(rows))


def _size(p: Path) -> int:
    """目录体积速览：递归求和但**有上限**（防大目录阻塞主线程）。

    上限（roadmap 10.3 性能红线）：最多累计 3000 个文件即截断——速览场景下
    精确总数无意义，宁可少算也不能让宿主机的 QQ 机器人卡死。
    """
    MAX_FILES = 3000
    total = 0
    count = 0
    try:
        if p.is_file():
            return p.stat().st_size
        for f in p.rglob("*"):
            if count >= MAX_FILES:
                break
            count += 1
            try:
                if f.is_file():
                    total += f.stat().st_size
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return total  # 部分可用也返回，不静默归零
    return total


def hostname_info() -> str:
    """宿主机名 + 本地 IP（内网定位用）。"""
    try:
        name = socket.gethostname()
    except Exception:  # noqa: BLE001
        name = "?"
    ips = []
    try:
        for info in psutil.net_if_addrs().values():
            for a in info:
                if getattr(a, "family", None) == socket.AF_INET and not a.address.startswith("127."):
                    ips.append(a.address)
    except Exception:  # noqa: BLE001
        pass
    return "宿主机：%s · IP：%s" % (name, "/".join(sorted(set(ips))[:4]) or "?")


def free_space_summary() -> str:
    """各磁盘剩余空间一行流。"""
    rows = []
    try:
        for part in psutil.disk_partitions(all=False):
            try:
                du = psutil.disk_usage(part.mountpoint)
                rows.append("%s 剩 %s" % (part.mountpoint.rstrip("\\") or part.mountpoint,
                                          _fmt_bytes(du.free)))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return " · ".join(rows) if rows else "磁盘信息读不到"


# ===========================================================================
# M6 第二批（2026-10-20）：窗口 / 剪贴板 / 电源 / 应用 / 键鼠 / 屏幕 OCR
# 全部 Windows-only；非 Windows 平台函数入口判 _WIN 后诚实降级（返回提示）。
# 依赖：ctypes（标准库）/ winrt.Windows.Media.Ocr（系统自带 OCR 引擎）。
# ===========================================================================
def _win() -> bool:
    return _u32 is not None


_SW_RESTORE = 9
_SW_MINIMIZE = 6


# ---------------------------------------------------------------------------
# 窗口管理
# ---------------------------------------------------------------------------
def list_windows() -> list[str]:
    """可见窗口标题列表（去重、去空）。"""
    if not _win():
        return []
    titles: list[str] = []
    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if _u32.IsWindowVisible(hwnd):
            n = _u32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                _u32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value.strip()
                if t and t not in titles:
                    titles.append(t)
        return 1

    _u32.EnumWindows(_EnumWindowsProc(_cb), 0)
    return titles


def _find_hwnd(sub: str) -> list[tuple[int, str]]:
    sub_l = (sub or "").lower()
    hits: list[tuple[int, str]] = []
    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if _u32.IsWindowVisible(hwnd):
            n = _u32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                _u32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value.strip()
                if t and sub_l in t.lower():
                    hits.append((int(hwnd), t))
        return 1

    _u32.EnumWindows(_EnumWindowsProc(_cb), 0)
    return hits


def focus_window(title: str) -> str:
    hits = _find_hwnd(title)
    if not hits:
        return "没找到标题含「%s」的窗口。" % title
    hwnd, t = hits[0]
    try:
        _u32.ShowWindow(hwnd, _SW_RESTORE)
        _u32.SetForegroundWindow(hwnd)
    except Exception as e:  # noqa: BLE001
        return "提窗口失败（%s）" % type(e).__name__
    return "已把「%s」提到最前。" % t


def minimize_window(title: str) -> str:
    hits = _find_hwnd(title)
    if not hits:
        return "没找到标题含「%s」的窗口。" % title
    try:
        _u32.ShowWindow(hits[0][0], _SW_MINIMIZE)
    except Exception as e:  # noqa: BLE001
        return "最小化失败（%s）" % type(e).__name__
    return "已最小化「%s」。" % hits[0][1]


def close_window(title: str) -> str:
    hits = _find_hwnd(title)
    if not hits:
        return "没找到标题含「%s」的窗口。" % title
    try:
        _u32.PostMessageW(hits[0][0], 0x0010, 0, 0)  # WM_CLOSE
    except Exception as e:  # noqa: BLE001
        return "关闭失败（%s）" % type(e).__name__
    return "已给「%s」发送关闭信号。" % hits[0][1]


def window_screenshot(title: str) -> tuple[bytes | None, str]:
    """截取指定窗口（按窗口矩形从全屏截图中裁切，复用 mss）。

    注：GPU/游戏全屏窗口仍可能黑屏（WGC 修复列后续），普通窗口正常。
    """
    hits = _find_hwnd(title)
    if not hits:
        return None, "没找到标题含「%s」的窗口。" % title
    if not _win():
        return None, "非 Windows 平台不支持。"
    try:
        import mss
        r = wintypes.RECT()
        _u32.GetWindowRect(hits[0][0], ctypes.byref(r))
        x, y, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return None, "窗口尺寸异常。"
        with mss.mss() as sct:
            raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
            png = mss.tools.to_png(raw.rgb, raw.size)
        if len(png) < 1024:
            return None, "截到的图太小（黑屏？全屏游戏窗口需 WGC）。"
        return png, ""
    except Exception as e:  # noqa: BLE001 — CLOSE-02
        return None, "窗口截图失败（%s）" % type(e).__name__


# ---------------------------------------------------------------------------
# 剪贴板
# ---------------------------------------------------------------------------
def clipboard_get() -> str:
    if not _win():
        return ""
    try:
        _ole32 = ctypes.windll.ole32
        _ole32.OleInitialize(None)
        try:
            if not _u32.OpenClipboard(0):
                return ""
            h = _u32.GetClipboardData(13)  # CF_UNICODETEXT
            if not h:
                return ""
            ptr = _k32.GlobalLock(h)
            text = ctypes.wstring_at(ptr) if ptr else ""
            _k32.GlobalUnlock(h)
            return text or ""
        finally:
            _u32.CloseClipboard()
            _ole32.OleUninitialize()
    except Exception as e:  # noqa: BLE001
        return ""


def clipboard_set(text: str) -> str:
    if not _win():
        return "非 Windows 平台不支持。"
    if not text:
        return "没东西可写。"
    try:
        _ole32 = ctypes.windll.ole32
        _ole32.OleInitialize(None)
        try:
            _u32.OpenClipboard(0)
            _u32.EmptyClipboard()
            buf = (text + "\0").encode("utf-16-le")
            h = _k32.GlobalAlloc(0x42, len(buf))  # GMEM_MOVEABLE|GMEM_ZEROINIT
            ptr = _k32.GlobalLock(h)
            ctypes.memmove(ptr, ctypes.create_string_buffer(buf, len(buf)), len(buf))
            _k32.GlobalUnlock(h)
            _u32.SetClipboardData(13, h)  # CF_UNICODETEXT
            _u32.CloseClipboard()
        finally:
            _ole32.OleUninitialize()
        return "已写入剪贴板（%d 字符）。" % len(text)
    except Exception as e:  # noqa: BLE001
        return "写剪贴板失败（%s）" % type(e).__name__


# ---------------------------------------------------------------------------
# 电源（高危：关机等由调用方叠加二次确认 + 5s 可取消倒计时）
# ---------------------------------------------------------------------------
def lock_screen() -> str:
    if not _win():
        return "非 Windows 平台不支持。"
    try:
        _u32.LockWorkStation()
        return "已锁屏。"
    except Exception as e:  # noqa: BLE001
        return "锁屏失败（%s）" % type(e).__name__


def sleep_pc() -> str:
    if not _win():
        return "非 Windows 平台不支持。"
    try:
        _u32.SetSuspendState(0, 0, 0)  # (Hibernate=0, Force=0, WakeEvents=0) → 睡眠
        return "正在睡眠…"
    except Exception as e:  # noqa: BLE001
        return "睡眠失败（%s）" % type(e).__name__


def _do_power(action: str) -> str:
    """执行关机/重启（经系统 shutdown.exe，普通用户权限即可）。"""
    import subprocess
    if action == "关机":
        subprocess.Popen(["shutdown", "/s", "/t", "0"], shell=False)
        return "已下达关机指令（系统随后执行）。"
    if action == "重启":
        subprocess.Popen(["shutdown", "/r", "/t", "0"], shell=False)
        return "已下达重启指令（系统随后执行）。"
    return "未知电源操作：%s" % action


# ---------------------------------------------------------------------------
# 应用：启动 / 已装列表 / 开机项
# ---------------------------------------------------------------------------
def launch_app(target: str) -> str:
    if not target:
        return "告诉本喵启动啥。"
    import os
    import subprocess
    try:
        if os.path.exists(target):
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        return "已尝试启动：%s" % target
    except Exception as e:  # noqa: BLE001
        return "启动失败（%s）：%s" % (type(e).__name__, target)


def list_installed(limit: int = 30) -> list[str]:
    """已装程序名（扫描 Program Files + 开始菜单，去重）。"""
    import os
    roots = [
        os.environ.get("ProgramFiles", r"C:/Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:/Program Files (x86)"),
        os.path.join(os.environ.get("ProgramData", r"C:/ProgramData"),
                     "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith((".exe", ".lnk")):
                    name = f[:-4]
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
            if len(out) >= limit * 3:  # 超额即停，避免遍历过深
                break
        if len(out) >= limit * 3:
            break
    return out[:limit]


def startup_items() -> list[str]:
    """开机自启项（HKCU/HKLM Run 注册表）。"""
    import winreg  # type: ignore
    items: list[str] = []
    for hive, path in ((winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Run"),
                       (winreg.HKEY_LOCAL_MACHINE,
                        r"Software\Microsoft\Windows\CurrentVersion\Run")):
        try:
            with winreg.OpenKey(hive, path) as k:
                i = 0
                while True:
                    name, val, _ = winreg.EnumValue(k, i)
                    items.append("%s = %s" % (name, val))
                    i += 1
        except OSError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return items


# ---------------------------------------------------------------------------
# 键鼠（ctypes SendInput / mouse_event）
# ---------------------------------------------------------------------------
def type_text(text: str) -> str:
    if not _win():
        return "非 Windows 平台不支持。"
    if not text:
        return "没东西可打。"
    try:
        _INPUT = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        _INPUTUNION = ctypes.c_ulong * (2 if ctypes.sizeof(ctypes.c_void_p) == 8 else 1)
        class _KI(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
        class _IN(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("ki", _KI)]
        arr = []
        for ch in text:
            kv = ctypes.create_unicode_buffer(ch, 2)
            inp = _IN()
            inp.type = 1  # INPUT_KEYBOARD (Unicode)
            inp.ki.wScan = ctypes.cast(kv, ctypes.POINTER(wintypes.WORD))[0]
            inp.ki.dwFlags = 0x4  # KEYEVENTF_UNICODE
            arr.append(inp)
            up = _IN()
            up.type = 1
            up.ki.wScan = inp.ki.wScan
            up.ki.dwFlags = 0x4 | 0x2  # UNICODE | KEYUP
            arr.append(up)
        _arr = (_IN * len(arr))(*arr)
        _u32.SendInput(len(arr), ctypes.byref(_arr), ctypes.sizeof(_IN))
        return "已敲入 %d 字符。" % len(text)
    except Exception as e:  # noqa: BLE001
        return "键入失败（%s）" % type(e).__name__


def mouse_click(x: int, y: int) -> str:
    if not _win():
        return "非 Windows 平台不支持。"
    try:
        _u32.SetCursorPos(int(x), int(y))
        _u32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        _u32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        return "已在 (%d, %d) 点了一下。" % (x, y)
    except Exception as e:  # noqa: BLE001
        return "点击失败（%s）" % type(e).__name__


# ---------------------------------------------------------------------------
# 屏幕 OCR（winrt.Windows.Media.Ocr，Windows 自带，零安装）
# ---------------------------------------------------------------------------
def screen_ocr() -> tuple[str, str]:
    """全屏截图 → Windows OCR 提取文字（zh-CN + en-US 兜底）。

    同步函数（内部 asyncio.run 跑 winrt 协程）；调用方用 asyncio.to_thread 包。
    未装/不可用 → 诚实返回错误字符串，不静默。
    """
    try:
        import asyncio
        async def _ocr(png: bytes) -> str:
            import io
            from PIL import Image  # 项目已装 Pillow（图像预处理共用）
            import winrt.windows.media.ocr as wocr  # type: ignore
            import winrt.windows.graphics.imaging as gimg  # type: ignore
            import winrt.windows.storage.streams as wstreams  # type: ignore
            import winrt.windows.globalization as wg  # type: ignore
            # 本机 winrt 投影无 BitmapDecoder.CreateAsync，改走 Pillow 解码 →
            # RGBA→BGRA 交换 → SoftwareBitmap(BGRA8) + DataWriter.detach_buffer()。
            img = Image.open(io.BytesIO(png)).convert("RGBA")
            w, h = img.size
            r, g, b, a = img.split()
            img = Image.merge("RGBA", (b, g, r, a))  # → BGRA（OcrEngine 预期）
            pix = img.tobytes()
            dw = wstreams.DataWriter()
            dw.write_bytes(pix)
            buf = dw.detach_buffer()
            bmp = gimg.SoftwareBitmap(
                gimg.BitmapPixelFormat.BGRA8, w, h,
                gimg.BitmapAlphaMode.PREMULTIPLIED)
            bmp.copy_from_buffer(buf)
            eng = wocr.OcrEngine.try_create_from_language(wg.Language("zh-CN"))
            if eng is None:
                return "（该系统的 OCR 语言包不可用）"
            res = await eng.recognize_async(bmp)
            return res.text or ""

        png, err = asyncio.run(screenshot())
        if err:
            return "", err
        text = asyncio.run(_ocr(png))
        return text, ""
    except Exception as e:  # noqa: BLE001 — OCR 依赖系统组件，失败必须显式
        return "", "OCR 失败（%s）：可能未装 Windows  OCR 语言包" % type(e).__name__


# ======================================================================
# 文件互传（M6 文件管理，2026-10-20 第二批收尾）
#   本模块只做「本地 IO 与文件名安全」，不含任何 QQ/网络逻辑——网络下载
#   （get_file API / url 直下）由 __init__.py 负责，落盘前统一调用这里。
#   安全：文件名清洗防路径穿越；落盘防覆盖（自动加 (1)/(2)…）；大小上限由调用方给字节数。
# ======================================================================

def _safe_name(name: str) -> str:
    """清洗文件名：去路径分隔符、去不可打印字符、限长，防 ../ 穿越。"""
    name = (name or "").strip().replace("\\", "_").replace("/", "_").replace("..", "_")
    name = "".join(c for c in name if c.isprintable())
    name = name.strip(" .")
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        name = base[:200 - len(ext)] + ext
    return name or "file"


def _unique_inbox_path(dest_dir: str, name: str) -> str:
    """在 dest_dir 内算出不存在的目标路径；重名自动加 (1)/(2)…（防覆盖）。"""
    dest = os.path.join(dest_dir, name)
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(os.path.join(dest_dir, "%s(%d)%s" % (base, i, ext))):
        i += 1
    return os.path.join(dest_dir, "%s(%d)%s" % (base, i, ext))


def inbox_save(src_path: str, dest_dir: str, name: str, max_bytes: int) -> dict:
    """QQ→宿主：把已下载到本地的源文件**复制**进 dest_dir（防覆盖）。

    返回 {"ok": bool, "path": str, "name": str, "size": int, "error": str}。
    纯本地 IO，可单测（mock shutil/os 即可）。
    """
    try:
        if not os.path.isfile(src_path):
            return {"ok": False, "error": "源文件不存在（下载失败？）"}
        size = os.path.getsize(src_path)
        if max_bytes and size > max_bytes:
            return {"ok": False, "error": "文件 %d MB 超过上限 %d MB"
                    % (size // 1048576, max_bytes // 1048576)}
        os.makedirs(dest_dir, exist_ok=True)
        safe = _safe_name(name)
        dest = _unique_inbox_path(dest_dir, safe)
        shutil.copy2(src_path, dest)
        return {"ok": True, "path": dest, "name": os.path.basename(dest), "size": size}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def inbox_write_bytes(data: bytes, dest_dir: str, name: str, max_bytes: int) -> dict:
    """QQ→宿主：把从 url 下载到的内存 bytes 直接写入 dest_dir（防覆盖）。

    与 inbox_save 共用文件名安全 + 防覆盖逻辑；返回结构相同。
    """
    try:
        size = len(data)
        if max_bytes and size > max_bytes:
            return {"ok": False, "error": "文件 %d MB 超过上限 %d MB"
                    % (size // 1048576, max_bytes // 1048576)}
        os.makedirs(dest_dir, exist_ok=True)
        safe = _safe_name(name)
        dest = _unique_inbox_path(dest_dir, safe)
        with open(dest, "wb") as fh:
            fh.write(data)
        return {"ok": True, "path": dest, "name": os.path.basename(dest), "size": size}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def file_read_info(path: str) -> dict:
    """PC→QQ：读取前先 stat（不读内容，仅元信息）。白名单由调用方负责。

    返回 {"exists": bool, "is_file": bool, "size": int, "name": str, "error": str}。
    """
    try:
        if not os.path.exists(path):
            return {"exists": False, "error": "文件不存在"}
        if not os.path.isfile(path):
            return {"exists": True, "is_file": False, "error": "这不是文件（可能是目录）"}
        size = os.path.getsize(path)
        return {"exists": True, "is_file": True, "size": size,
                "name": os.path.basename(path)}
    except Exception as e:  # noqa: BLE001
        return {"exists": False, "error": "%s: %s" % (type(e).__name__, e)}
