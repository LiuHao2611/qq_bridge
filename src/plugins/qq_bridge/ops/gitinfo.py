# -*- coding: utf-8 -*-
"""ops.gitinfo —— 开发机 git 仓库状态（M9 运维面，2026-10-20）。

需求（roadmap §M9）：「git status」/「CI 过没过」→ 群推送（本机 manju_agent 仓库）。
本机没有 CI runner（个人开发机），一期把「CI 过没过」实现为**可运行的
测试套件状态**（pytest 收集计数/最近退出码缓存），二期再接真 CI API。

安全（比 dsh-qq-remote 的 /exec 更严）：
  - **只跑只读 git 子命令白名单**（status/diff --stat/log/branch/rev-parse/
    show --stat），拒绝一切写操作（add/commit/push/checkout…）；
  - subprocess 显式超时（CLOSE-05）+ 参数数组化（不经 shell，防注入）；
  - 失败显式 GitInfoError，不返回假数据。

纪律：本模块不 import nonebot / 不 import 主包：可裸单测（git 可执行路径
可注入）。
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

from ..config import Config, get_logger, jlog

log = get_logger()

#: 只读 git 子命令白名单（键 = 子命令，值 = 允许的参数前缀；空 tuple = 任意参数）
_READONLY_SUBCMDS: dict[str, tuple[str, ...]] = {
    "status": ("--short", "--porcelain", "--branch", "-s", "-b", ""),
    "diff": ("--stat", "--numstat", "--name-only", "--cached", ""),
    "log": ("--oneline", "--stat", "-n", "-1", "-5", "-10", "-20", ""),
    "branch": ("--show-current", "-a", "-v", ""),
    "rev-parse": ("HEAD", "--abbrev-ref", "HEAD~1", ""),
    "show": ("--stat", "--oneline", "HEAD", ""),
    "remote": ("-v", ""),
    "stash": ("list",),
}

#: git 输出里出现的危险写操作关键词（双保险：白名单之外再挡一层）
_FORBIDDEN = re.compile(r"\b(add|commit|push|pull|reset|checkout|switch|merge|"
                        r"rebase|clean|restore|stash\s+(?:push|pop|drop)|"
                        r"cherry-pick|revert|tag|fetch|clone|init)\b", re.I)


class GitInfoError(Exception):
    """显式化的 git 查询失败。"""


def validate_git_args(args: list[str]) -> str:
    """校验 git 参数只含只读子命令。返回错误文本；空串 = 放行。"""
    if not args:
        return "git 参数不能为空"
    sub = args[0].strip().lower()
    if sub not in _READONLY_SUBCMDS:
        return "只支持只读 git 子命令（%s），写操作请主人自己在终端敲"
    rest = [a for a in args[1:] if not a.startswith("-")]
    allowed = _READONLY_SUBCMDS[sub]
    for a in rest:
        if not any(a == p or (p and a.startswith(p)) for p in allowed if p != ""):
            return "git %s 不接受参数 %s（只读白名单）" % (sub, a)
    joined = " ".join(args)
    if _FORBIDDEN.search(joined) and sub not in ("log", "show", "diff"):
        # log/show/diff 的消息文本里可能出现这些词（提交信息），不误伤
        return "检测到写操作关键词，拒绝执行"
    return ""


class GitInfo:
    """本机仓库状态查询（默认 manju_agent 仓库，Config.OPS_GIT_REPO 可配）。"""

    def __init__(self, repo: str | None = None, git_bin: str = "git") -> None:
        self.repo = Path(repo if repo is not None
                         else (getattr(Config, "OPS_GIT_REPO", "") or "D:/AI/manju_agent"))
        self.git_bin = git_bin

    async def _run(self, args: list[str], timeout: float = 15.0) -> str:
        err = validate_git_args(args)
        if err:
            raise GitInfoError(err)

        def _sync() -> str:
            try:
                p = subprocess.run(
                    [self.git_bin, "-C", str(self.repo), *args],
                    capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace",
                )
            except subprocess.TimeoutExpired:
                raise GitInfoError("git %s 超时（%ss）" % (args[0], int(timeout)))
            except FileNotFoundError:
                raise GitInfoError("找不到 git 可执行文件（%s）" % self.git_bin)
            except Exception as e:  # noqa: BLE001
                raise GitInfoError("git 执行失败（%s）" % type(e).__name__)
            if p.returncode != 0:
                raise GitInfoError("git %s 报错：%s"
                                   % (args[0], (p.stderr or "").strip()[:200]))
            return (p.stdout or "").strip()

        return await asyncio.to_thread(_sync)

    async def status(self) -> dict:
        """仓库状态总览：分支/脏改动数/未推送数/最近提交。"""
        if not self.repo.exists():
            raise GitInfoError("仓库目录不存在：%s" % self.repo)
        branch = await self._run(["branch", "--show-current"])
        porcelain = await self._run(["status", "--porcelain"])
        dirty = [ln for ln in porcelain.splitlines() if ln.strip()]
        unpushed = 0
        try:
            # 与上游的差异 commits（没有上游时显式跳过，不报错）
            cnt = await self._run(["rev-parse", "--abbrev-ref", "HEAD@{upstream}"])
            _ = cnt  # 上游存在即可查询 ahead
            log_out = await self._run(["log", "--oneline", "@{upstream}..HEAD"])
            unpushed = len([ln for ln in log_out.splitlines() if ln.strip()])
        except GitInfoError:
            unpushed = 0  # 无上游分支：不算未推送（诚实态，不当错误）
        last = await self._run(["log", "--oneline", "-1"])
        return {
            "repo": str(self.repo),
            "branch": branch or "(detached)",
            "dirty_count": len(dirty),
            "dirty_files": [ln[3:] for ln in dirty[:15]],
            "unpushed": unpushed,
            "last_commit": last,
        }

    async def raw(self, args: list[str]) -> str:
        """白名单内的原始 git 查询（供 /ops git <子命令> 用）。"""
        return await self._run(args)

    def format_status(self, st: dict) -> str:
        """状态 dict → 人话文本。"""
        lines = [
            "仓库：%s" % st["repo"],
            "分支：%s" % st["branch"],
            "未提交改动：%d 个文件" % st["dirty_count"],
        ]
        if st["dirty_files"]:
            lines.append("· " + "\n· ".join(st["dirty_files"]))
            if st["dirty_count"] > len(st["dirty_files"]):
                lines.append("· …还有 %d 个" % (st["dirty_count"] - len(st["dirty_files"])))
        lines.append("未推送提交：%d" % st["unpushed"])
        lines.append("最近提交：%s" % st["last_commit"])
        return "\n".join(lines)


#: 模块级单例（单测各自 new 隔离）
git_info = GitInfo()
