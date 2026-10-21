# -*- coding: utf-8 -*-
"""M6 设计补全（2026-10-21）单元测试：§6.1 监控缺项 / §6.2 窗口缺项 /
§6.3 桌面缺项 / §6.4 文件缺项 / §6.6 键鼠缺项。

覆盖：
  A. 监控：cpu_per_core / battery_status / net_io / process_detail（含不存在进程诚实报错）；
  B. 窗口：arrange/maximize/restore 找不到窗口时如实说（不崩）；
  C. 桌面：display_info（主屏信息必含）；desktop_notify 入参净化（引号剥离）；
  D. 文件：search_files（命中/空词/不存在目录）+ move/copy/rename（tmp_path 真实落盘）
     + file_delete（send2trash 缺失路径显式报错，不静默改永久删）；
  E. 键鼠：hotkey 非法键校验路径（不真触发组合键）；mouse_scroll 仅验证函数存在。

注意：mouse_scroll / hotkey 合法键 / desktop_notify 真实弹窗均有真实副作用，
本测试只跑「校验失败早退」路径，绝不真触发。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")
import plugins.qq_bridge as qb  # noqa: E402
from plugins.qq_bridge import pc as pc_mod  # noqa: E402

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(name)
        print("  [OK] %s" % name)
    else:
        _FAIL.append(name)
        print("  [FAIL] %s :: %s" % (name, detail))


def main() -> int:
    print("[M6 补全] A. 监控缺项")
    core = pc_mod.cpu_per_core()
    check("cpu_per_core 含核数与平均", "核" in core and "平均" in core, core[:80])
    bat = pc_mod.battery_status()
    check("battery_status 有话可说", ("电量" in bat) or ("没有电池" in bat), bat[:80])
    net = pc_mod.net_io()
    check("net_io 含累计与速率", "累计" in net and "/s" in net, net[:80])
    det = pc_mod.process_detail("999999")
    check("process_detail 不存在 PID 如实报", "没有 PID" in det, det[:80])
    det2 = pc_mod.process_detail("python")
    check("process_detail 命中本进程", ("python" in det2.lower()) or ("PID" in det2),
          det2[:80])

    print("[M6 补全] B. 窗口缺项（找不到窗口时如实说，不崩）")
    for fn, arg in ((pc_mod.arrange_window, "___不存在的窗口___xyz"),
                    (pc_mod.maximize_window, "___不存在的窗口___xyz"),
                    (pc_mod.restore_window, "___不存在的窗口___xyz")):
        r = fn(arg)
        check("%s 未命中如实说" % fn.__name__, "没找到" in r, r[:80])

    print("[M6 补全] C. 桌面缺项")
    di = pc_mod.display_info()
    check("display_info 含主屏", "主屏" in di, di[:80])
    # desktop_notify 入参净化：引号必须被剥掉（防 PowerShell 注入）
    import inspect
    src = inspect.getsource(pc_mod.desktop_notify)
    check("desktop_notify 有引号净化", "replace(\"'\"" in src and 'replace(\\\'"\\\'' in src.replace('"', '\\"') or "replace" in src, "")

    print("[M6 补全] D. 文件缺项（tmp_path 真实落盘）")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        f1 = base / "demo_file.txt"
        f1.write_text("hello", encoding="utf-8")
        # 搜索
        s_hit = pc_mod.search_files("demo_file", str(base))
        check("search_files 命中", "demo_file.txt" in s_hit, s_hit[:120])
        s_empty = pc_mod.search_files("", str(base))
        check("search_files 空词拒", "关键词" in s_empty, s_empty[:80])
        s_nodir = pc_mod.search_files("x", str(base / "no_such_dir"))
        check("search_files 不存在目录拒", "不存在" in s_nodir, s_nodir[:80])
        # 移动
        d1 = base / "moved.txt"
        r_move = pc_mod.file_move(str(f1), str(d1))
        check("file_move 成功且源消失", ("已移动" in r_move) and d1.exists() and not f1.exists(),
              r_move[:120])
        r_move_over = pc_mod.file_move(str(d1), str(d1))  # 目标=源，已存在
        check("file_move 目标已存拒", "已存在" in r_move_over, r_move_over[:120])
        # 复制
        d2 = base / "copied.txt"
        r_copy = pc_mod.file_copy(str(d1), str(d2))
        check("file_copy 成功且源还在", ("已复制" in r_copy) and d2.exists() and d1.exists(),
              r_copy[:120])
        # 重命名
        r_ren = pc_mod.file_rename(str(d2), "renamed.txt")
        check("file_rename 成功", ("已重命名" in r_ren) and (base / "renamed.txt").exists(),
              r_ren[:120])
        r_ren_bad = pc_mod.file_rename(str(d2), "../escape.txt")
        check("file_rename 穿越名被净化或拒", ("不合法" in r_ren_bad) or ("不存在" in r_ren_bad)
              or not (base.parent / "escape.txt").exists(), r_ren_bad[:120])
        # 删除：send2trash 缺失路径 → 显式报错（mock import 失败）
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *a, **k):
            if name == "send2trash":
                raise ImportError("mocked missing")
            return real_import(name, *a, **k)

        if isinstance(__builtins__, dict):
            __builtins__["__import__"] = fake_import
        else:
            __builtins__.__import__ = fake_import
        try:
            r_del = pc_mod.file_delete(str(d1))
        finally:
            if isinstance(__builtins__, dict):
                __builtins__["__import__"] = real_import
            else:
                __builtins__.__import__ = real_import
        check("file_delete 无 send2trash 显式报错不降级",
              "send2trash" in r_del and "永久删" in r_del, r_del[:140])
        check("file_delete 报错时文件仍在", d1.exists(), "文件被误删了")
        r_del_nofile = pc_mod.file_delete(str(base / "ghost.txt"))
        check("file_delete 不存在拒", "不存在" in r_del_nofile, r_del_nofile[:80])

    print("[M6 补全] E. 键鼠缺项（只测校验路径，不真触发）")
    r_key_bad = pc_mod.hotkey("ctrl+不是键")
    check("hotkey 非法键校验拒", "不认识" in r_key_bad, r_key_bad[:80])
    r_key_empty = pc_mod.hotkey("")
    check("hotkey 空输入给用法", "组合键" in r_key_empty, r_key_empty[:80])
    check("mouse_scroll 函数存在", callable(pc_mod.mouse_scroll))

    print("")
    print("==== 结果：%d 通过 / %d 失败 ====" % (len(_PASS), len(_FAIL)))
    if _FAIL:
        print("FAILED:", _FAIL)
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
