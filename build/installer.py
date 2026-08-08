# -*- coding: utf-8 -*-
"""ManjuAgent 自解压式安装器（PyInstaller onefile 入口）。

将构建产物 Layer1 目录（build/dist/ManjuAgent，含 python 运行时 + 应用 + ffmpeg.exe）
作为 datas 嵌入本 exe；运行时解压到 %LOCALAPPDATA%\\ManjuAgent，创建开始菜单/桌面快捷方式，
并询问是否立即启动。无需管理员权限。
"""
import os
import sys
import shutil
import time

APP_NAME = "ManjuAgent"
TARGET_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local"), APP_NAME)


def _embedded_payload():
    """返回被 PyInstaller 打入的 Layer1 目录（位于 _MEIPASS/ManjuAgent）。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ManjuAgent")


def _silent():
    return os.environ.get("MANJU_SILENT_INSTALL") == "1"


def _msg(title, text, flags=0x40):
    if _silent():
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        print(text)


def _create_shortcut(lnk_path, target_exe, work_dir):
    try:
        import win32com.client
        ws = win32com.client.Dispatch("WScript.Shell")
        sc = ws.CreateShortcut(lnk_path)
        sc.TargetPath = target_exe
        sc.WorkingDirectory = work_dir
        sc.IconLocation = target_exe + ",0"
        sc.Description = "AI 漫剧工坊 · ManjuAgent"
        sc.save()
        return True
    except Exception as e:
        print("shortcut failed:", e)
        return False


def _shortcuts(target_exe, work_dir):
    ok = True
    # 桌面
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    ok &= _create_shortcut(os.path.join(desktop, APP_NAME + ".lnk"), target_exe, work_dir)
    # 开始菜单
    start = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming"),
                         "Microsoft", "Windows", "Start Menu", "Programs")
    os.makedirs(start, exist_ok=True)
    ok &= _create_shortcut(os.path.join(start, APP_NAME + ".lnk"), target_exe, work_dir)
    return ok


def main():
    payload = _embedded_payload()
    if not os.path.isdir(payload):
        _msg("安装失败", "未找到内置程序包（ManjuAgent），可能是安装器损坏。", 0x10)
        sys.exit(1)

    _msg("ManjuAgent 安装", "即将把 AI 漫剧工坊安装到：\n%s\n\n点击确定继续（无需管理员权限）。" % TARGET_DIR, 0x40)

    try:
        if os.path.isdir(TARGET_DIR):
            shutil.rmtree(TARGET_DIR)
        shutil.copytree(payload, TARGET_DIR)
    except Exception as e:
        _msg("安装失败", "复制文件失败：%s" % e, 0x10)
        sys.exit(1)

    target_exe = os.path.join(TARGET_DIR, "ManjuAgent.exe")
    if not os.path.isfile(target_exe):
        _msg("安装失败", "未找到 %s" % target_exe, 0x10)
        sys.exit(1)

    _shortcuts(target_exe, TARGET_DIR)

    res = ctypes_confirm_launch()
    if res:
        import subprocess
        flags = getattr(os, "DETACHED_PROCESS", 0) | getattr(os, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen([target_exe], cwd=TARGET_DIR, creationflags=flags, close_fds=True)
        except Exception as e:
            _msg("启动失败", "无法启动：%s" % e, 0x10)
    _msg("安装完成", "AI 漫剧工坊已安装到 %s\n可通过桌面或开始菜单快捷方式启动。" % TARGET_DIR, 0x40)
    sys.exit(0)


def ctypes_confirm_launch():
    if _silent():
        return False
    try:
        import ctypes
        # 0x44 = MB_YESNO | MB_ICONQUESTION
        r = ctypes.windll.user32.MessageBoxW(0, "安装完成！是否立即启动 AI 漫剧工坊？", "ManjuAgent", 0x44)
        return r == 6  # IDYES
    except Exception:
        return False


if __name__ == "__main__":
    main()
