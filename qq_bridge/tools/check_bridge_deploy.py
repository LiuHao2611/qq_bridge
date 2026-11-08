#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署一致性闸门：宿主磁盘 / 镜像层 / 运行容器，三层必须一致。

背景（2026-11-08 事故）：赏金卡代码 commit + push 完成、`git status` 干净，
但没有 rebuild —— 运行容器仍挂 20 小时前的旧镜像，用户发「赏金」收到的还是旧卡。
**git 状态干净 ≠ 正式环境已更新**。

检查项：
  P1 运行容器挂载的镜像 ID == `qq-bridge:latest` 当前 ID（是否真的 recreate）
  P2 本次改动涉及的 .py 在宿主 / 镜像层 / 运行容器三层 md5 全等
  P3 `docker diff qq-bridge` 无 /app/src、/app/tools 覆盖（禁止 docker cp 热贴）

用法（在宿主 qq_bridge 目录）：
  python tools/check_bridge_deploy.py              # 默认取 HEAD~1..HEAD 改动的代码文件
  python tools/check_bridge_deploy.py --all       # 比对 src/ 下全部 .py（慢，但最全）
  python tools/check_bridge_deploy.py --files src/x.py tools/y.py
  python tools/check_bridge_deploy.py --selftest  # 注入三类反例，验证闸门会报错

退出码：0=通过；1=不一致（或注入未被拦住）；2=docker/git 不可用等前置错误。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # qq_bridge/
CONTAINER = "qq-bridge"
IMAGE = "qq-bridge:latest"
APP = "/app/"


def _run(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _container_image_id() -> str:
    r = _run(["docker", "inspect", CONTAINER, "--format", "{{.Image}}"])
    return r.stdout.strip() if r.returncode == 0 else ""


def _latest_image_id() -> str:
    r = _run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    return r.stdout.strip() if r.returncode == 0 else ""


def _host_files(explicit: list[str] | None, all_py: bool) -> list[str]:
    """返回相对 /app 的路径（如 src/... / tools/... / bot.py）。"""
    if explicit:
        out = [p.replace("\\", "/").lstrip("/") for p in explicit]
    elif all_py:
        out = [str(p.relative_to(ROOT)).replace("\\", "/")
               for p in sorted((ROOT / "src").rglob("*.py"))]
    else:
        git = _run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"])
        if git.returncode != 0:
            raise RuntimeError("git 不可用：" + git.stderr.strip())
        gitroot = Path(git.stdout.strip())
        prefix = ROOT.relative_to(gitroot).as_posix()
        d = _run(["git", "-C", str(gitroot), "diff", "--name-only", "HEAD~1", "HEAD",
                  "--", prefix + "/src", prefix + "/tools", prefix + "/bot.py"])
        out = [p[len(prefix) + 1:]
               for p in d.stdout.splitlines()
               if p.startswith(prefix + "/") and p.endswith(".py")]
        # 哨兵：即便本次没改这些入口，也要确认镜像/容器不是整体过期
        for sentinel in ("bot.py", "src/plugins/qq_bridge/pic/__init__.py"):
            if sentinel not in out:
                out.append(sentinel)
    return [p for p in dict.fromkeys(out) if (ROOT / p).is_file()]


def _md5_host(files: list[str]) -> dict[str, str]:
    return {p: hashlib.md5((ROOT / p).read_bytes()).hexdigest() for p in files}


def _parse_md5sum(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 32:
            continue
        path = parts[1].strip().lstrip("*").strip()
        if path.startswith(APP):
            out[path[len(APP):]] = parts[0]
        else:
            out[path] = parts[0]
    return out


def _md5_layer(files: list[str], kind: str) -> dict[str, str]:
    paths = [APP + p for p in files]
    if not paths:
        return {}
    if kind == "container":
        cmd = ["docker", "exec", CONTAINER, "md5sum", *paths]
    else:
        cmd = ["docker", "run", "--rm", "--entrypoint", "md5sum", IMAGE, *paths]
    r = _run(cmd, timeout=180)
    if r.returncode != 0:
        raise RuntimeError("%s md5sum 失败：%s" % (kind, r.stderr.strip()[:300]))
    return _parse_md5sum(r.stdout)


def _diff_lines() -> list[str]:
    r = _run(["docker", "diff", CONTAINER])
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def problems(image_container: str, image_latest: str,
             host: dict[str, str], image: dict[str, str],
             container: dict[str, str], diff: list[str]) -> list[str]:
    """纯判定函数：--selftest 直接注入反例，不碰真实 docker。"""
    out: list[str] = []
    if not image_container or not image_latest or image_container != image_latest:
        out.append("P1 容器镜像 ID 与 %s 不一致（未 recreate）：container=%s latest=%s"
                   % (IMAGE, image_container or "<无>", image_latest or "<无>"))
    for rel in sorted(host):
        h, i, c = host[rel], image.get(rel), container.get(rel)
        if i != h or c != h:
            out.append("P2 三层 md5 不一致 %s：host=%s image=%s container=%s"
                       % (rel, h[:8], (i or "<缺失>")[:8], (c or "<缺失>")[:8]))
    for ln in diff:
        if "/app/src" in ln or "/app/tools" in ln:
            out.append("P3 运行容器有源码热贴覆盖（docker diff）：%s" % ln.strip())
    return out


def selftest() -> int:
    h = {"src/a.py": "a" * 32, "src/b.py": "b" * 32}
    good = ("sha256:same", "sha256:same", h, dict(h), dict(h), [])
    cases = [
        ("容器仍挂旧镜像", good[:1] + ("sha256:new",) + good[2:], "P1"),
        ("三层 md5 不一致", good[:4] + ({"src/a.py": "c" * 32, "src/b.py": "b" * 32},) + good[5:], "P2"),
        ("docker cp 热贴源码", good[:5] + (["C /app/src/plugins/x.py"],), "P3"),
    ]
    if problems(*good):
        print("SELFTEST FAIL：正常部署被误判为有问题")
        return 1
    for name, args, expect in cases:
        p = problems(*args)
        if not any(x.startswith(expect) for x in p):
            print("SELFTEST FAIL：注入「%s」没有被拦住" % name)
            return 1
        print("SELFTEST OK：注入「%s」→ %s" % (name, p[0][:90]))
    print("SELFTEST PASS (3/3 注入全部被拦下)")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    explicit: list[str] = []
    if "--files" in argv:
        i = argv.index("--files")
        explicit = [a for a in argv[i + 1:] if not a.startswith("-")]
    try:
        files = _host_files(explicit or None, "--all" in argv)
        if not files:
            print("FAIL：没有可比对的 .py 文件（用 --files 或 --all 指定）")
            return 1
        host = _md5_host(files)
        image = _md5_layer(files, "image")
        container = _md5_layer(files, "container")
        p = problems(_container_image_id(), _latest_image_id(),
                     host, image, container, _diff_lines())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        print("ERROR：%s: %s" % (type(e).__name__, e))
        return 2
    print("=== 部署一致性：%d 个文件 ===" % len(files))
    print("| 文件 | 宿主 | 镜像 | 容器 |")
    print("|---|---|---|---|")
    for rel in sorted(host):
        i, c = image.get(rel, ""), container.get(rel, "")
        print("| %s | %s | %s | %s |" % (rel, host[rel][:8], (i[:8] or "-"), (c[:8] or "-")))
    if p:
        print("\nFAIL (%d)：" % len(p))
        for x in p:
            print("  - " + x)
        print("\n修复：docker compose -f docker-compose.bridge.yml up -d --build --force-recreate bridge")
        return 1
    print("\nPASS：容器已挂最新镜像，三层 md5 全等，无源码热贴")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
