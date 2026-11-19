# -*- coding: utf-8 -*-
"""把上游 frocloud-DIYPCmarket 全量搬运进仓库（唯一搬运入口）。

为什么需要它：
  * 上游是**人工维护的月度行情档案**（Excel 手搓 → 导出 CSV），本项目只读它的
    `data/*.csv`，绝不在本地二次编辑数据；
  * 「全量搬运」必须可复现：谁、从哪个 commit、搬了哪些文件、每个文件 sha256 多少，
    全部落进 `UPSTREAM.json`，任何手改都会被 `--verify` 抓出来；
  * 上游 `AGENTS.md` 第 0 条是**绝对红线**：`frocloud.xlsx` 只准人碰，任何脚本都不得
    创建/修改/重写/移动/删除它。所以本工具**显式排除** xlsx，不搬、不读、不写。

用法：
    # 1) 从本机已解压的上游目录搬运（离线，需显式给 commit）
    python tools/vendor_frocloud.py --from .tmp/frocloud_dl/frocloud-DIYPCmarket-main \
        --commit 53a43827db0e97a63091858f5032f28012d53a41

    # 2) 直接从 GitHub 下载 main 最新 tarball（需要网络）
    python tools/vendor_frocloud.py --download

    # 3) 校验本地副本与清单一致（离线，门禁/测试都调它）
    python tools/vendor_frocloud.py --verify

绝不降级：缺文件、多文件、哈希不符、commit 缺失、README 里找不到数据截止日，
一律非零退出并打印差异，不自动"修复"、不忽略。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "plugins" / "qq_bridge" / "hardware" / "vendored_frocloud"
UPSTREAM_JSON = ROOT / "src" / "plugins" / "qq_bridge" / "hardware" / "UPSTREAM.json"

REPO = "https://github.com/x1shang/frocloud-DIYPCmarket"
API = "https://api.github.com/repos/x1shang/frocloud-DIYPCmarket"
TARBALL = "https://codeload.github.com/x1shang/frocloud-DIYPCmarket/tar.gz/refs/heads/main"

#: 绝不搬运的文件（上游红线 + 非源码二进制）。见上游 AGENTS.md §0。
EXCLUDED = {
    "frocloud.xlsx": "上游 AGENTS.md §0 绝对红线：xlsx 只准人碰，脚本不得创建/修改/移动/删除；"
                     "本项目只读 data/*.csv，不搬二进制设计稿",
}

#: 必须存在的关键数据文件（少一份就说明搬运不完整）
REQUIRED = (
    "data/gpu-prices.csv",
    "data/cpu-prices.csv",
    "data/memory-prices.csv",
    "data/gpu-tier.csv",
    "data/cpu-tier.csv",
    "data/laptop-cpu-tier.csv",
    "data/gpu-brands.csv",
    "README.md",
    "LICENSE",
    "LICENSES/CC-BY-4.0.txt",
    "LICENSES/MIT.txt",
    "THIRD_PARTY_NOTICES.md",
    "DISCLAIMER.md",
    "AGENTS.md",
    "tools/check_csv.py",
    "tools/export_csv.py",
    "tools/test_materialize.py",
)

_ASOF_RE = re.compile(r"数据截止日[：:]\s*(\d{4})\.(\d{1,2})\.(\d{1,2})")


class VendorError(Exception):
    """搬运/校验失败（显式上抛，调用方非零退出）。"""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree(base: Path) -> dict[str, Path]:
    """列出上游全部普通文件（跳过 .git 与 EXCLUDED）。"""
    out: dict[str, Path] = {}
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base).as_posix()
        if rel.startswith(".git/"):
            continue
        if rel in EXCLUDED:
            continue
        out[rel] = p
    return out


def _data_asof(readme: Path) -> str:
    text = readme.read_text(encoding="utf-8")
    m = _ASOF_RE.search(text)
    if not m:
        raise VendorError("README.md 里找不到「数据截止日：YYYY.M.D」，拒绝猜测数据时效")
    y, mo, d = (int(x) for x in m.groups())
    return "%04d-%02d-%02d" % (y, mo, d)


def _api_commit() -> tuple[str, str]:
    req = urllib.request.Request(API + "/commits/main", headers={"User-Agent": "qq_bridge-vendor"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — 固定 https 官方 API
        d = json.load(r)
    return d["sha"], d["commit"]["author"]["date"]


def _download(dest: Path) -> tuple[str, str]:
    req = urllib.request.Request(TARBALL, headers={"User-Agent": "qq_bridge-vendor"})
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
        blob = r.read()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        tf.extractall(dest)  # noqa: S202 — 官方 tarball，已限定目标目录
    roots = [p for p in dest.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise VendorError("tarball 解压出 %d 个顶层目录，预期 1 个" % len(roots))
    return str(roots[0]), ""


def _write_upstream(meta: dict) -> None:
    UPSTREAM_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = UPSTREAM_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, UPSTREAM_JSON)


def vendor(src: Path, commit: str, commit_date: str) -> int:
    if not src.is_dir():
        raise VendorError("源目录不存在：%s" % src)
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise VendorError("必须给 40 位 commit sha（40 位小写 hex），收到：%r" % (commit,))
    files = _tree(src)
    missing = [r for r in REQUIRED if r not in files]
    if missing:
        raise VendorError("上游缺少必需文件：%s" % ", ".join(missing))
    if "frocloud.xlsx" in files:  # EXCLUDED 过滤在 _tree()，这条防的是有人删了过滤规则
        raise VendorError("EXCLUDED 未生效：待搬列表仍包含 frocloud.xlsx（上游红线）")

    asof = _data_asof(files["README.md"])

    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    entries = []
    for rel, p in files.items():
        dst = TARGET / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst)
        entries.append({"path": rel, "size": dst.stat().st_size, "sha256": _sha256(dst)})

    meta = {
        "repo": REPO,
        "branch": "main",
        "commit": commit,
        "commit_date": commit_date,
        "vendored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_asof": asof,
        "license": {"data_and_docs": "CC BY 4.0", "tools": "MIT",
                    "see": "vendored_frocloud/LICENSE"},
        "excluded": [{"path": k, "reason": v} for k, v in EXCLUDED.items()],
        "file_count": len(entries),
        "total_bytes": sum(e["size"] for e in entries),
        "files": entries,
    }
    _write_upstream(meta)
    print("vendor ok: %d files, %d bytes, commit %s, data_asof %s"
          % (len(entries), meta["total_bytes"], commit[:12], asof))
    return 0


def verify(verbose: bool = True) -> list[str]:
    """校验本地副本 = UPSTREAM.json 清单。返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    if not UPSTREAM_JSON.is_file():
        return ["缺少清单 %s" % UPSTREAM_JSON]
    meta = json.loads(UPSTREAM_JSON.read_text(encoding="utf-8"))
    if not TARGET.is_dir():
        return ["缺少搬运目录 %s" % TARGET]
    want = {e["path"]: e for e in meta.get("files", [])}
    if not want:
        return ["清单 files 为空"]
    have = {p.relative_to(TARGET).as_posix(): p for p in TARGET.rglob("*") if p.is_file()}
    for rel in sorted(want):
        if rel not in have:
            problems.append("缺失：%s" % rel)
            continue
        p = have[rel]
        if p.stat().st_size != want[rel]["size"]:
            problems.append("大小不符：%s（%d ≠ %d）" % (rel, p.stat().st_size, want[rel]["size"]))
            continue
        if _sha256(p) != want[rel]["sha256"]:
            problems.append("哈希不符：%s（本地被改动过）" % rel)
    for rel in sorted(set(have) - set(want)):
        problems.append("多余：%s（清单里没有，禁止手工加文件）" % rel)
    if "frocloud.xlsx" in have:
        problems.append("红线：vendored 目录内出现 frocloud.xlsx")
    if meta.get("file_count") != len(want):
        problems.append("file_count 与清单条数不一致：%r ≠ %d" % (meta.get("file_count"), len(want)))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(meta.get("data_asof", ""))):
        problems.append("data_asof 非法：%r" % (meta.get("data_asof"),))
    if verbose:
        for p in problems:
            print("PROBLEM", p)
        if not problems:
            print("verify ok: %d files, commit %s, data_asof %s"
                  % (len(want), str(meta.get("commit"))[:12], meta.get("data_asof")))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="全量搬运 / 校验上游 frocloud-DIYPCmarket")
    ap.add_argument("--from", dest="src", default="", help="本机已解压的上游目录")
    ap.add_argument("--download", action="store_true", help="从 GitHub 下载 main tarball 后搬运")
    ap.add_argument("--commit", default="", help="上游 commit sha（--from 时必填）")
    ap.add_argument("--commit-date", default="", help="上游 commit 时间（可选）")
    ap.add_argument("--verify", action="store_true", help="校验本地副本")
    args = ap.parse_args()

    try:
        if args.verify:
            return 1 if verify() else 0
        if args.download:
            tmp = ROOT / ".tmp" / "frocloud_vendor_dl"
            if tmp.exists():
                shutil.rmtree(tmp)
            src, _ = _download(tmp)
            commit, cdate = _api_commit()
            return vendor(Path(src), commit, cdate)
        if args.src:
            if not args.commit:
                raise VendorError("--from 必须同时给 --commit（tarball 目录不含 git 元数据）")
            return vendor(Path(args.src), args.commit, args.commit_date)
        ap.print_help()
        return 2
    except VendorError as e:
        print("VENDOR FAILED:", e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
