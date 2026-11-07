# -*- coding: utf-8 -*-
"""tools/build_docker_wheels.py —— 为 Linux Docker 镜像预拉离线 wheel。

为什么需要：
    本机 Docker 容器**出网受限**（`apt` 的 deb.debian.org 与 `pip` 均会卡死；
    宿主代理绑在 127.0.0.1:63040，容器够不着）。因此镜像不用现场 pip，
    而是 `--no-index` 从 docker/wheels/ 离线安装。本脚本在**宿主**（有网络/代理）
    上按目标平台把整棵依赖树拉齐。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\build_docker_wheels.py
    .venv\\Scripts\\python.exe tools\\build_docker_wheels.py --clean   # 清空重拉
    .venv\\Scripts\\python.exe tools\\build_docker_wheels.py --pyver 313 --abi cp313

产物：docker/wheels/*.whl（已在 .gitignore 中忽略，可随时重建）
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WHEELS = ROOT / "docker" / "wheels"
REQ = ROOT / "requirements.txt"

#: 目标平台（Debian trixie/glibc 2.41 可跑；同时兼容老 manylinux 包）
PLATFORMS = [
    "manylinux_2_28_x86_64",
    "manylinux_2_27_x86_64",
    "manylinux_2_24_x86_64",
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
]
#: 只在 Linux 安装、宿主是 Windows 时会被平台标记跳过的依赖（uvicorn[standard] 等）
LINUX_ONLY = ["uvloop", "httptools", "watchfiles", "PyYAML", "websockets"]


def _pip(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pip", *args],
                          capture_output=True, text=True)


def _download(pyver: str, abi: str) -> subprocess.CompletedProcess:
    args = ["download", "--only-binary=:all:", "--find-links", str(WHEELS)]
    for p in PLATFORMS:
        args += ["--platform", p]
    args += ["--python-version", pyver, "--implementation", "cp", "--abi", abi,
             "-d", str(WHEELS), "-r", str(REQ)]
    return _pip(args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pyver", default="313", help="目标 Python 版本（默认 313）")
    ap.add_argument("--abi", default="cp313", help="目标 ABI（默认 cp313）")
    ap.add_argument("--clean", action="store_true", help="先清空 docker/wheels")
    a = ap.parse_args()

    if a.clean and WHEELS.exists():
        shutil.rmtree(WHEELS)
    WHEELS.mkdir(parents=True, exist_ok=True)

    # 1) 主依赖树：遇到 sdist-only 的纯 Python 包（jieba/qrcode_terminal…）
    #    先在宿主预编成 py3-none-any wheel，再重试；最多 15 轮。
    for i in range(15):
        r = _download(a.pyver, a.abi)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            print(f"主依赖树就绪（第 {i + 1} 轮）")
            break
        m = re.search(r"No matching distribution found for ([A-Za-z0-9_.\-]+)", out)
        if not m:
            print("依赖解析失败：\n" + out[-2000:])
            return 1
        pkg = m.group(1)
        print(f"[{i}] 仅有源码包，预编 wheel：{pkg}")
        b = _pip(["wheel", pkg, "--no-deps", "-w", str(WHEELS)])
        if b.returncode != 0:
            print(f"{pkg} 预编失败：\n" + ((b.stdout or "") + (b.stderr or ""))[-1500:])
            return 1
    else:
        print("超过最大重试次数，依赖可能不完整")
        return 1

    # 2) Linux-only 依赖（宿主 Windows 解析时会因 sys_platform 标记跳过）
    r = _pip(["download", "--only-binary=:all:",
              "--platform", PLATFORMS[0], "--platform", PLATFORMS[3],
              "--python-version", a.pyver, "--implementation", "cp", "--abi", a.abi,
              "--no-deps", "-d", str(WHEELS), *LINUX_ONLY])
    if r.returncode != 0:
        print("Linux-only 依赖下载失败：\n" + ((r.stdout or "") + (r.stderr or ""))[-1500:])
        return 1

    n = len(list(WHEELS.glob("*.whl")))
    size = sum(p.stat().st_size for p in WHEELS.glob("*.whl")) / 1e6
    print(f"完成：{n} 个 wheel / {size:.0f} MB → {WHEELS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
