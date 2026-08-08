# -*- coding: utf-8 -*-
"""Layer2：自解压式安装器（onefile）。产物 dist/Setup.exe。
将 Layer1 目录（dist/ManjuAgent）整体作为 datas 嵌入，运行时解压到 %LOCALAPPDATA%\\ManjuAgent，
创建开始菜单/桌面快捷方式并询问是否启动。无需管理员权限。
"""
import os

SPEC_DIR = os.path.dirname(os.path.abspath("build/installer.spec"))
REPO = os.path.dirname(SPEC_DIR)
BUILD = SPEC_DIR

a = Analysis(
    [os.path.join(BUILD, "installer.py")],
    pathex=[BUILD, REPO],
    binaries=[],
    # 嵌入已构建好的 Layer1 应用目录
    datas=[
        (os.path.join(BUILD, "dist", "ManjuAgent"), "ManjuAgent"),
    ],
    hiddenimports=[
        "win32com", "win32com.client", "win32com.shell",
        "win32api", "win32gui", "win32con", "pythoncom",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test", "pydoc", "doctest"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="Setup",
    icon=os.path.join(BUILD, "manju_icon.ico"),
    console=False,
    disable_windowed_traceback=False,
    onefile=True,
)
