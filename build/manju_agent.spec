# -*- coding: utf-8 -*-
"""Layer1：应用包（onedir）。产物 dist/ManjuAgent/ManjuAgent.exe（含 python 运行时 + 应用 + ffmpeg）。
入口 build/launcher_app.py：托盘父进程，拉起 backend(7788)/frontend(8099) 子进程。
"""
import os

SPEC_DIR = os.path.dirname(os.path.abspath("build/manju_agent.spec"))
REPO = os.path.dirname(SPEC_DIR)            # D:/AI/manju_agent
SERVER = os.path.join(REPO, "server")
BUILD = SPEC_DIR

a = Analysis(
    [os.path.join(BUILD, "launcher_app.py")],
    pathex=[REPO, SERVER],
    binaries=[],
    datas=[
        (os.path.join(SERVER, "_vendor"), "_vendor"),
        (os.path.join(REPO, "frontend", "dist"), "frontend/dist"),
        (os.path.join(BUILD, "ffmpeg.exe"), "."),
        (os.path.join(BUILD, "manju_tray.png"), "."),
    ],
    hiddenimports=[
        "main", "web_app", "manju_agent",
        "db", "common", "routers", "memoir_routes", "worker",
        "points_engine", "realtime", "ai_local", "module_manager",
        "uvicorn", "pystray", "PIL", "PIL._imaging",
        "win32api", "win32gui", "win32con",
        "multipart", "websockets",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test", "pydoc", "doctest"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts,
    name="ManjuAgent",
    icon=os.path.join(BUILD, "manju_icon.ico"),
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    name="ManjuAgent",
)
