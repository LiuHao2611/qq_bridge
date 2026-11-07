# -*- coding: utf-8 -*-
"""Launcher 注入链路验收 —— 机器可判定，退出码即结论。

为什么需要单独一个验收脚本：

我们换上 LD_PRELOAD 方案，本质是把「QQ 的 package.json 被改过」这个磁盘特征
抹掉。但这个方案有一个很隐蔽的失败模式：**如果 .so 里的 ORIGINAL_MAIN 与
当前 QQ 版本的实际值对不上，`get_modified_packagejson()` 会原样返回真实文件
内容，于是 QQ 按原生入口启动、完全正常，只是 NapCat 根本没加载**。
表现就是「容器起来了、QQ 进程在、但机器人永远不连桥」，从日志上看极易被误判
成网络问题。所以必须有一步显式的断言。

本脚本校验 5 条不变量：
  1. 磁盘上的 package.json.main 是腾讯原值（= 篡改已被还原）
  2. QQ 应用目录里没有 loadNapCat.js（= 注入脚本不在磁盘）
  3. /app/napcat/loadNapCat.js 存在（= .so 构造器确实跑过，说明库被加载）
  4. 有进程的环境变量里带 libnapcat_launcher（= LD_PRELOAD 生效）
  5. 容器日志里出现过 NapCat.Core Version（= napcat.mjs 真的被 import 了）

用法：
    python scripts/verify_launcher.py
    python scripts/verify_launcher.py --container qq-bridge-napcat
退出码：0 = 全部通过；1 = 有不变量不成立。
"""

from __future__ import annotations

import argparse
import subprocess
import sys

ORIGINAL_MAIN = '"main": "./application.asar/app_launcher/index.js"'
TAMPERED_MAIN = '"main": "./loadNapCat.js"'
QQ_APP = "/opt/QQ/resources/app"


def _sh(container: str, script: str, timeout: int = 20, user: str | None = None) -> tuple[int, str]:
    """在容器内跑一段 sh，返回 (退出码, stdout+stderr)。

    user 不为 None 时用 `docker exec -u <user>` 执行。读 /proc/<pid>/environ
    必须用进程属主身份：容器默认没有 CAP_SYS_PTRACE，root 也读不到 napcat
    用户进程的 environ（会报 Permission denied）。
    """
    cmd = ["docker", "exec"]
    if user:
        cmd += ["-u", user]
    cmd += [container, "sh", "-c", script]
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check_disk_main(container: str) -> tuple[bool, str]:
    code, out = _sh(container, f"grep -c '{ORIGINAL_MAIN}' {QQ_APP}/package.json || true")
    if code != 0:
        return False, f"读取 package.json 失败：{out.strip()}"
    ok = out.strip().splitlines()[-1] == "1" if out.strip() else False
    if ok:
        return True, "package.json.main = 腾讯原值"
    code2, out2 = _sh(container, f"grep -o '\"main\": \"[^\"]*\"' {QQ_APP}/package.json || true")
    return False, f"main 不是腾讯原值，实际：{out2.strip() or '读取失败'}"


def check_no_injected_js(container: str) -> tuple[bool, str]:
    code, out = _sh(container, f"test -e {QQ_APP}/loadNapCat.js && echo EXISTS || echo ABSENT")
    if "ABSENT" in out:
        return True, "QQ 应用目录无 loadNapCat.js"
    return False, f"QQ 应用目录仍残留 loadNapCat.js（{out.strip()}）"


def check_launcher_generated(container: str) -> tuple[bool, str]:
    # 预编译 .so 的构造器把 loadNapCat.js 写在 cwd，而 entrypoint 的 cwd 是
    # /app/napcat（因为注入 JS 里 import 的是 cwd 同级的 './napcat.mjs'）。
    code, out = _sh(container, "test -f /app/napcat/loadNapCat.js && echo EXISTS || echo ABSENT")
    if "EXISTS" in out:
        return True, "/app/napcat/loadNapCat.js 已由 launcher 构造器生成"
    return False, "/app/napcat/loadNapCat.js 不存在 —— .so 可能没被加载"


def check_preload_env(container: str) -> tuple[bool, str]:
    script = (
        "hit=''; "
        "for p in /proc/[0-9]*; do "
        "  if tr '\\0' '\\n' < \"$p/environ\" 2>/dev/null "
        "     | grep -q 'LD_PRELOAD=.*libnapcat_launcher'; then "
        "    hit=$(basename $p); break; "
        "  fi; "
        "done; "
        "if [ -n \"$hit\" ]; then echo \"PID=$hit\"; "
        "  tr '\\0' '\\n' < /proc/$hit/environ | grep 'LD_PRELOAD='; "
        "else echo NONE; fi"
    )
    # 以 napcat 身份执行：容器默认无 CAP_SYS_PTRACE，root 读不到 napcat 进程的 environ
    code, out = _sh(container, script, timeout=30, user="napcat")
    if "LD_PRELOAD=" in out:
        pid = out.split("PID=")[1].split()[0] if "PID=" in out else "?"
        return True, f"进程 {pid} 环境含 libnapcat_launcher"
    return False, "没有任何进程的 environ 含 libnapcat_launcher"


def check_napcat_loaded(container: str) -> tuple[bool, str]:
    p = subprocess.run(
        ["docker", "logs", "--tail", "400", container],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    logs = (p.stdout or "") + (p.stderr or "")
    for line in logs.splitlines():
        if "NapCat.Core Version" in line:
            return True, line.strip()
    return False, "容器日志里没有 NapCat.Core Version —— napcat.mjs 未被 import"


CHECKS = [
    ("磁盘 package.json 已还原", check_disk_main),
    ("无 loadNapCat.js 残留", check_no_injected_js),
    ("launcher 构造器已运行", check_launcher_generated),
    ("LD_PRELOAD 已生效", check_preload_env),
    ("NapCat 已加载", check_napcat_loaded),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default="qq-bridge-napcat")
    args = ap.parse_args()

    print(f"验收目标容器：{args.container}")
    print("=" * 72)

    failed = 0
    for name, fn in CHECKS:
        try:
            ok, detail = fn(args.container)
        except subprocess.TimeoutExpired:
            ok, detail = False, "检查超时"
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        print(f"      {detail}")
        failed += 0 if ok else 1

    print("=" * 72)
    print(f"LAUNCHER_CHECKS={len(CHECKS)} FAILED={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
