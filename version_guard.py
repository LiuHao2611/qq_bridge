#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""version_guard.py — 本地误改/误删兜底工具（git 基线之外的第二重保险）

为什么需要它：
  即便有 git，一次手误覆盖（如把 routers.py 清空）若没及时 commit，
  仍只能靠工作区副本恢复。本工具在「改动前」自动快照关键文件，
  并提供一键回退，且与 git 互不冲突。

用法：
  python version_guard.py backup  <file> [--note "改前快照"]
  python version_guard.py list    <file>
  python version_guard.py restore <file> [--index N] [--which <timestamp>]
  python version_guard.py status

约定：
  - 备份存于仓库内 .backups/（已被 .gitignore 忽略，绝不入库）。
  - 每次 backup 复制文件并登记到 .backups/MANIFEST.jsonl。
  - restore 默认回退到该文件最新一份备份；--index 0=最新，1=次新 …
  - <file> 可用绝对路径或相对仓库根的路径。
"""
import argparse
import datetime
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
BACKUP_ROOT = os.path.join(REPO_ROOT, ".backups")
MANIFEST = os.path.join(BACKUP_ROOT, "MANIFEST.jsonl")

# 视为“关键文件”，backup 时给出醒目提示（不强制）
CRITICAL = {"server/routers.py", "server/db.py", "server/main.py",
            "server/launcher.py", "service_console.py", "src/main.js"}


def _now_ts():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _rel(path):
    try:
        return os.path.relpath(os.path.abspath(path), REPO_ROOT)
    except Exception:
        return os.path.basename(path)


def _load_manifest():
    if not os.path.exists(MANIFEST):
        return []
    rows = []
    with open(MANIFEST, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def _append_manifest(row):
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    with open(MANIFEST, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def cmd_backup(args):
    src = os.path.abspath(args.file)
    if not os.path.isfile(src):
        print(f"[ERROR] 文件不存在: {src}")
        return 2
    rel = _rel(src)
    ts = _now_ts()
    dest_dir = os.path.join(BACKUP_ROOT, os.path.dirname(rel))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{ts}__{os.path.basename(src)}")
    with open(src, "rb") as f:
        data = f.read()
    with open(dest, "wb") as f:
        f.write(data)
    row = {
        "ts": ts,
        "rel": rel,
        "src": src,
        "backup": dest,
        "size": len(data),
        "note": args.note or "",
    }
    _append_manifest(row)
    flag = "  [关键文件]" if rel.replace("\\", "/") in CRITICAL else ""
    print(f"[OK] 已备份 ({len(data)} bytes){flag}")
    print(f"     源: {src}")
    print(f"     副本: {dest}")
    return 0


def cmd_list(args):
    rel = _rel(os.path.abspath(args.file))
    rows = [r for r in _load_manifest() if r.get("rel", "").replace("\\", "/") == rel.replace("\\", "/")]
    if not rows:
        print(f"[INFO] 没有 {rel} 的备份记录")
        return 0
    print(f"=== {rel} 的备份（index 0 = 最新）===")
    for i, r in enumerate(reversed(rows)):
        print(f"  [{i}] {r['ts']}  {r['size']}B  {r.get('note','')}")
    return 0


def cmd_restore(args):
    rel = _rel(os.path.abspath(args.file))
    rows = [r for r in _load_manifest() if r.get("rel", "").replace("\\", "/") == rel.replace("\\", "/")]
    if not rows:
        print(f"[ERROR] 没有 {rel} 的备份可回退")
        return 2
    ordered = list(reversed(rows))
    if args.which:
        chosen = next((r for r in ordered if r["ts"] == args.which), None)
        if not chosen:
            print(f"[ERROR] 找不到时间戳为 {args.which} 的备份")
            return 2
    else:
        idx = args.index or 0
        if idx < 0 or idx >= len(ordered):
            print(f"[ERROR] index 越界（可用 0..{len(ordered)-1}）")
            return 2
        chosen = ordered[idx]
    # 回退前，先把当前文件再备一份，避免覆盖即丢失
    ts = _now_ts()
    safety = os.path.join(BACKUP_ROOT, os.path.dirname(rel),
                          f"{ts}__pre_restore__{os.path.basename(args.file)}")
    os.makedirs(os.path.dirname(safety), exist_ok=True)
    with open(args.file, "rb") as f:
        cur = f.read()
    with open(safety, "wb") as f:
        f.write(cur)
    with open(chosen["backup"], "rb") as f:
        data = f.read()
    with open(args.file, "wb") as f:
        f.write(data)
    _append_manifest({
        "ts": ts, "rel": rel, "src": os.path.abspath(args.file),
        "backup": safety, "size": len(cur), "note": f"auto pre-restore of {rel}",
    })
    print(f"[OK] 已回退 {rel} <- {chosen['ts']}")
    print(f"     回退前当前文件已自动备份到: {safety}")
    print(f"     若回退有误，可再 restore 该 safety 副本（list 可见）")
    return 0


def cmd_status(args):
    print(f"仓库根: {REPO_ROOT}")
    print(f"git 基线: ", end="")
    try:
        import subprocess
        out = subprocess.run(["git", "log", "--oneline", "-1"], cwd=REPO_ROOT,
                             capture_output=True, text=True)
        print(out.stdout.strip() or "(无提交)")
        tag = subprocess.run(["git", "tag", "--points-at", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True)
        if tag.stdout.strip():
            print(f"当前 HEAD 标签: {tag.stdout.strip()}")
    except Exception as e:
        print(f"(git 不可用: {e})")
    rows = _load_manifest()
    print(f"version_guard 备份份数: {len(rows)}")
    print(f"备份目录(.backups, 已被 .gitignore 忽略): {BACKUP_ROOT}")
    return 0


def main():
    p = argparse.ArgumentParser(description="本地误改/误删兜底工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", help="改动前快照一个文件")
    b.add_argument("file")
    b.add_argument("--note", default="")
    b.set_defaults(func=cmd_backup)

    l = sub.add_parser("list", help="列出某文件的备份")
    l.add_argument("file")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("restore", help="回退某文件到备份")
    r.add_argument("file")
    r.add_argument("--index", type=int, default=0, help="0=最新")
    r.add_argument("--which", default="", help="指定时间戳")
    r.set_defaults(func=cmd_restore)

    s = sub.add_parser("status", help="查看基线/备份状态")
    s.set_defaults(func=cmd_status)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
