# -*- coding: utf-8 -*-
"""ManjuAgent 冻结版入口（PyInstaller onedir）。

运行模式：
  ManjuAgent.exe                 -> 托盘父进程：拉起 backend/frontend 子进程 + 系统托盘 + 自动开浏览器
  ManjuAgent.exe --mode backend  -> 仅启动 FastAPI 后端 (127.0.0.1:7788)
  ManjuAgent.exe --mode frontend -> 仅启动前端网关 (0.0.0.0:8099)

路径策略（与知识库 MANJU_* 覆盖变量一致）：
  - MANJU_APP_ROOT  安装根（含 server/ frontend/dist ffmpeg.exe），冻结时 = _internal
  - MANJU_DB_PATH   可写 SQLite（默认 安装根/data/app.db —— 随安装路径走，不强制 C 盘）
  - MANJU_LOG_DIR   日志目录（默认 安装根/logs）
  - MANJU_FRONTEND_DIR 前端产物目录（默认 安装根/frontend/dist）
  - 安装根加入 PATH，使 subprocess.run(["ffmpeg", ...]) 能命中随包 ffmpeg.exe
  - 数据/日志落在「用户自选的安装目录」下，从而支持装到任意盘。
"""
import os
import sys
import time
import shutil
import subprocess
import importlib.util
import webbrowser
import threading

APP_NAME = "ManjuAgent"
BACKEND_PORT = 7788
FRONTEND_PORT = 8099
FRONTEND_URL = "http://127.0.0.1:%d/" % FRONTEND_PORT


def _app_root():
    if getattr(sys, "frozen", False):
        # PyInstaller 6.x onedir：所有模块与 datas 位于 _internal/，运行时 sys._MEIPASS 指向它
        return sys._MEIPASS
    # 开发态：本文件在 build/ 下，仓库根为其父目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _local_appdata():
    return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")


def _install_dir(app_root):
    """安装根目录（用户自选）：冻结时 = _internal 的父目录（即 ManjuAgent 文件夹），
    开发态 = 仓库根。数据/日志默认放在安装根下，避免强制落到 C 盘。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(app_root)  # app_root=_MEIPASS=.../ManjuAgent/_internal
    return app_root


def setup_env(app_root):
    """在导入业务模块之前设置好所有 MANJU_* 覆盖变量，并扩展 PATH。

    数据目录(MANJU_DB_PATH)与日志目录(MANJU_LOG_DIR)默认放在「安装根目录」下，
    而非强制 %LOCALAPPDATA%，从而尊重安装器给出的自定义路径（可装在任意盘）。
    """
    os.environ.setdefault("MANJU_APP_ROOT", app_root)
    inst = _install_dir(app_root)
    data_dir = os.path.join(inst, "data")
    log_dir = os.path.join(inst, "logs")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    os.environ.setdefault("MANJU_DB_PATH", os.path.join(data_dir, "app.db"))
    os.environ.setdefault("MANJU_LOG_DIR", log_dir)
    os.environ.setdefault("MANJU_FRONTEND_DIR", os.path.join(app_root, "frontend", "dist"))
    # 随包 ffmpeg.exe 所在目录加入 PATH，覆盖 bare "ffmpeg" 调用
    if app_root not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = app_root + os.pathsep + os.environ.get("PATH", "")
    return data_dir, log_dir


def _load_module(mod_name, file_path):
    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_backend(app_root):
    if getattr(sys, "frozen", False):
        import main as _server_main  # 冻结版：模块已扁平化到 _internal，直接 import
    else:
        _server_main = _load_module("manju_server_main", os.path.join(app_root, "server", "main.py"))
    import uvicorn
    uvicorn.run(_server_main.app, host="127.0.0.1", port=BACKEND_PORT, log_level="info")


def run_frontend(app_root):
    if getattr(sys, "frozen", False):
        import web_app as _web_mod
    else:
        _web_mod = _load_module("manju_web_app", os.path.join(app_root, "web_app.py"))
    # 基线 web_app 提供 main() 启动网关；冻结版托盘以 PID 管理子进程，无需 run_frontend/stop_frontend
    _web_mod.main()


# ----------------------------- 服务探测（Ollama/ComfyUI） -----------------------------
def _probe(host, port, timeout=1.5):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def detect_external_services():
    ollama = _probe("127.0.0.1", 11434)
    comfy = _probe("127.0.0.1", 8188)
    return ollama, comfy


# ----------------------------- 子进程管理 -----------------------------
_CHILDREN = []


def _spawn_child(app_root, mode, log_dir):
    log_path = os.path.join(log_dir, "%s.out.log" % mode)
    flags = getattr(os, "DETACHED_PROCESS", 0) | getattr(os, "CREATE_NEW_PROCESS_GROUP", 0)
    with open(log_path, "ab") as lf:
        proc = subprocess.Popen(
            [sys.executable, "--mode", mode],
            cwd=app_root, env=os.environ.copy(),
            stdout=lf, stderr=lf, creationflags=flags, close_fds=True)
    _CHILDREN.append(proc)
    return proc


def _stop_children():
    for proc in _CHILDREN:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ----------------------------- 托盘 -----------------------------
def _tray_icon_image():
    try:
        # 优先用构建期生成的托盘 PNG（若被 PyInstaller 作为 datas 打入）
        here = os.path.dirname(os.path.abspath(__file__))
        png = os.path.join(here, "manju_tray.png")
        if os.path.isfile(png):
            from PIL import Image
            return Image.open(png).convert("RGBA")
    except Exception:
        pass
    # 兜底：运行时用 PIL 画一个简单图标
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (124, 58, 237, 255))
    d = ImageDraw.Draw(img)
    d.polygon([(24, 20), (24, 44), (46, 32)], fill=(255, 255, 255, 255))
    return img


def _open_browser():
    try:
        webbrowser.open(FRONTEND_URL)
    except Exception:
        pass


def _build_tray(app_root, log_dir):
    import pystray
    from pystray import Menu, MenuItem

    icon = pystray.Icon(APP_NAME)
    icon.icon = _tray_icon_image()
    icon.title = "AI 漫剧工坊 · ManjuAgent"

    def on_open(icon, item):
        _open_browser()

    def on_restart(icon, item):
        _stop_children()
        time.sleep(1)
        _spawn_child(app_root, "backend", log_dir)
        _spawn_child(app_root, "frontend", log_dir)
        time.sleep(2)
        _open_browser()

    def on_quit(icon, item):
        icon.stop()
        _stop_children()
        os._exit(0)

    icon.menu = Menu(
        MenuItem("打开浏览器", on_open, default=True),
        MenuItem("重启服务", on_restart),
        Menu.SEPARATOR,
        MenuItem("退出", on_quit),
    )
    return icon


def run_tray(app_root, log_dir):
    _spawn_child(app_root, "backend", log_dir)
    _spawn_child(app_root, "frontend", log_dir)

    # 等待服务起来后自动开浏览器
    threading.Timer(2.5, _open_browser).start()

    ollama, comfy = detect_external_services()
    if not (ollama and comfy):
        missing = []
        if not ollama:
            missing.append("Ollama (:11434)")
        if not comfy:
            missing.append("ComfyUI (:8188)")
        msg = ("以下本地 AI 引擎未运行，AI 生成功能将不可用：\n" + "、".join(missing) +
               "\n请先启动它们后再使用生成功能（本安装包不含这两个引擎）。")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "ManjuAgent 依赖提示", 0x40)
        except Exception:
            pass

    icon = _build_tray(app_root, log_dir)
    icon.run()


def main():
    app_root = _app_root()
    setup_env(app_root)

    mode = None
    if "--mode" in sys.argv:
        i = sys.argv.index("--mode")
        if i + 1 < len(sys.argv):
            mode = sys.argv[i + 1]

    if mode == "backend":
        run_backend(app_root)
        return
    if mode == "frontend":
        run_frontend(app_root)
        return

    # 默认：托盘父进程
    try:
        log_dir = os.environ["MANJU_LOG_DIR"]
    except KeyError:
        log_dir = os.path.join(_local_appdata(), APP_NAME, "logs")
    run_tray(app_root, log_dir)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            log_dir = os.environ.get("MANJU_LOG_DIR") or os.path.join(
                os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local"), APP_NAME, "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "launcher_crash.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + tb + "\n")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "ManjuAgent 启动失败：\n" + tb[-1500:], "ManjuAgent 错误", 0x10)
        except Exception:
            pass
        sys.exit(1)
