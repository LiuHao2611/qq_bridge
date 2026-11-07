# -*- coding: utf-8 -*-
"""M9 git 仓库状态验收（2026-10-20，ops/gitinfo.py）。

覆盖：
  1. validate_git_args 只读白名单：放行 status/diff/log/branch；拒绝
     add/commit/push/checkout 等写操作；危险关键词双保险；
  2. GitInfo.status：真实仓库跑一轮（D:/AI/manju_agent），字段齐全；
  3. 仓库不存在 → 显式 GitInfoError；
  4. format_status：人话文本含分支/脏改动/最近提交。

机器判定：pytest assert + 非零退出码。
运行：cd qq_bridge && .venv/Scripts/python.exe -m pytest tests/test_ops_gitinfo.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

from qq_bridge.ops.gitinfo import (  # noqa: E402
    GitInfo, GitInfoError, validate_git_args)


# ---------------- 1. 白名单校验 ----------------
def test_readonly_whitelist_allows():
    for args in (["status", "--porcelain"], ["diff", "--stat"],
                 ["log", "--oneline", "-1"], ["branch", "--show-current"],
                 ["rev-parse", "HEAD"], ["show", "--stat"],
                 ["remote", "-v"], ["stash", "list"]):
        assert validate_git_args(args) == "", args


def test_write_ops_rejected():
    for args in (["add", "."], ["commit", "-m", "x"], ["push"],
                 ["checkout", "main"], ["reset", "--hard"],
                 ["merge", "dev"], ["clean", "-fd"], ["restore", "."]):
        err = validate_git_args(args)
        assert err, args
        assert "只读" in err or "危险" in err, args


def test_forbidden_pattern_in_status_rejected():
    # status 本应放行，但显式带写操作关键词照样拒（双保险）
    assert validate_git_args(["status", "&&", "git", "push"]) != ""


def test_empty_args_rejected():
    assert validate_git_args([]) != ""


def test_unknown_subcommand_rejected():
    assert validate_git_args(["bisect", "start"]) != ""


# ---------------- 2. 真实仓库 status ----------------
@pytest.mark.anyio
async def test_real_repo_status():
    gi = GitInfo()  # 默认 D:/AI/manju_agent
    st = await gi.status()
    assert st["branch"], "分支名不应为空"
    assert st["dirty_count"] >= 0
    assert st["unpushed"] >= 0
    assert st["last_commit"]
    text = gi.format_status(st)
    assert st["branch"] in text and "未提交改动" in text


@pytest.mark.anyio
async def test_raw_readonly_query():
    gi = GitInfo()
    out = await gi.raw(["branch", "--show-current"])
    assert out.strip()


@pytest.mark.anyio
async def test_raw_write_rejected():
    gi = GitInfo()
    with pytest.raises(GitInfoError):
        await gi.raw(["commit", "-m", "不应执行"])


# ---------------- 3. 仓库不存在 ----------------
@pytest.mark.anyio
async def test_missing_repo_explicit_error():
    gi = GitInfo("Z:/no/such/repo")
    with pytest.raises(GitInfoError) as ei:
        await gi.status()
    assert "不存在" in str(ei.value)


# ---------------- 4. format ----------------
def test_format_status_text():
    gi = GitInfo()
    st = {"repo": "D:/x", "branch": "master", "dirty_count": 2,
          "dirty_files": ["a.py", "b.py"], "unpushed": 1,
          "last_commit": "abc123 feat: x"}
    text = gi.format_status(st)
    assert "master" in text and "2 个文件" in text and "a.py" in text
