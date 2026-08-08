# -*- coding: utf-8 -*-
"""ManjuAgent 自解压式安装器（PyInstaller onefile 入口）。

将构建产物 Layer1 目录（build/dist/ManjuAgent，含 python 运行时 + 应用 + ffmpeg.exe）
作为 datas 嵌入本 exe；运行时解压到「用户自选」的安装目录（默认优先 D:，不强制 C 盘），
创建开始菜单/桌面快捷方式，并询问是否立即启动。无需管理员权限。
"""
import os
import sys
import shutil

APP_NAME = "ManjuAgent"


# -----------------------------------------------------------------------------
# 内嵌负载 / 静默模式
# -----------------------------------------------------------------------------
def _embedded_payload():
    """返回被 PyInstaller 打入的 Layer1 目录（位于 _MEIPASS/ManjuAgent）。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ManjuAgent")


def _silent():
    return os.environ.get("MANJU_SILENT_INSTALL") == "1"


def _default_target():
    """默认安装目录：优先非系统盘（D:），尊重「不要强制装在 C 盘」。"""
    if os.path.exists("D:\\"):
        return "D:\\ManjuAgent"
    return "C:\\ManjuAgent"


# -----------------------------------------------------------------------------
# 系统对话框（ctypes，无需额外依赖）
# -----------------------------------------------------------------------------
def _msg(title, text, flags=0x40):
    if _silent():
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        print(text)


def _ask_yes(title, text):
    """MB_YESNO | MB_ICONQUESTION，返回是否选「是」。"""
    if _silent():
        return False
    try:
        import ctypes
        r = ctypes.windll.user32.MessageBoxW(0, text, title, 0x44)
        return r == 6  # IDYES
    except Exception:
        return False


def _browse_folder(title, default_path=None):
    """弹出系统「选择文件夹」对话框，返回选中路径或 None（取消）。

    使用 SHBrowseForFolderW；若提供 default_path，则初始化时自动选中它。
    """
    import ctypes
    from ctypes import wintypes
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    ole32.CoInitialize(None)
    try:
        BIF_RETURNONLYFSDIRS = 0x0001
        BIF_NEWDIALOGSTYLE = 0x0040
        BIF_EDITBOX = 0x0010
        BFFM_INITIALIZED = 1
        BFFM_SETSELECTIONW = 0x400 + 103  # WM_USER + 103

        CB = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, ctypes.c_uint, wintypes.LPARAM)

        class BROWSEINFO(ctypes.Structure):
            _fields_ = [
                ("hwndOwner", wintypes.HWND),
                ("pidlRoot", wintypes.LPCVOID),
                ("pszDisplayName", wintypes.LPWSTR),
                ("lpszTitle", wintypes.LPCWSTR),
                ("ulFlags", ctypes.c_ulong),
                ("lpfn", CB),
                ("lParam", wintypes.LPARAM),
                ("iImage", ctypes.c_int),
            ]

        bi = BROWSEINFO()
        bi.hwndOwner = 0
        bi.pidlRoot = None
        bi.pszDisplayName = ctypes.create_unicode_buffer(1024)
        bi.lpszTitle = title
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
        bi.lpfn = None
        bi.lParam = 0
        bi.iImage = 0

        if default_path:
            default_buf = ctypes.create_unicode_buffer(default_path)

            def _cb(hwnd, msg, lp):
                if msg == BFFM_INITIALIZED:
                    try:
                        shell32.SendMessageW(hwnd, BFFM_SETSELECTIONW, 1, default_buf)
                    except Exception:
                        pass
                return 0

            bi.lpfn = CB(_cb)

        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return None
        path_buf = ctypes.create_unicode_buffer(1024)
        shell32.SHGetPathFromIDListW(pidl, path_buf)
        try:
            ole32.CoTaskMemFree(pidl)
        except Exception:
            pass
        return path_buf.value or None
    except Exception as e:
        print("browse failed:", e)
        return None
    finally:
        ole32.CoUninitialize()


# -----------------------------------------------------------------------------
# 快捷方式
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
def main():
    payload = _embedded_payload()
    if not os.path.isdir(payload):
        _msg("安装失败", "未找到内置程序包（ManjuAgent），可能是安装器损坏。", 0x10)
        sys.exit(1)

    # 1) 选择安装目录
    if _silent():
        target = _default_target()
    else:
        picked = _browse_folder(
            "选择 AI 漫剧工坊（ManjuAgent）的安装目录：", _default_target())
        if not picked:
            sys.exit(0)  # 用户取消
        target = picked
        if not _ask_yes("ManjuAgent 安装确认",
                        "即将把 AI 漫剧工坊安装到：\n%s\n\n点击「是」继续（无需管理员权限；可装在任意盘）。"
                        % target):
            sys.exit(0)

    # 2) 解压
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(payload, target)
    except Exception as e:
        _msg("安装失败", "复制文件失败：%s" % e, 0x10)
        sys.exit(1)

    target_exe = os.path.join(target, "ManjuAgent.exe")
    if not os.path.isfile(target_exe):
        _msg("安装失败", "未找到 %s" % target_exe, 0x10)
        sys.exit(1)

    # 3) 快捷方式
    _shortcuts(target_exe, target)

    # 4) 是否立即启动
    if _ask_yes("安装完成", "AI 漫剧工坊已安装到 %s\n是否立即启动？" % target):
        import subprocess
        flags = getattr(os, "DETACHED_PROCESS", 0) | getattr(os, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen([target_exe], cwd=target, creationflags=flags, close_fds=True)
        except Exception as e:
            _msg("启动失败", "无法启动：%s" % e, 0x10)
    sys.exit(0)


if __name__ == "__main__":
    main()
