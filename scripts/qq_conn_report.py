"""QQ 连接事件报表：一条命令看清掉线历史与在线时长曲线。

用法：
    python scripts/qq_conn_report.py            # 最近 20 条事件 + 统计
    python scripts/qq_conn_report.py --all      # 全部事件
    python scripts/qq_conn_report.py --days 2   # 只看最近 2 天

数据源刻意读两份：
  1) logs/qq_conn_events.log —— 2026-10-29 起新增的结构化事件，带 online/gap 时长；
  2) logs/qq_bridge.log*     —— 主日志，有 2026-10-27 以来的历史 attach/detach，
                                但只有时间戳。读它是为了把引入事件日志之前的曲线一起补齐，
                                这样「历史最好在线 17.27h」这类基线才有得比。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
EVENT_LOG = LOGS / "qq_conn_events.log"

# 主日志：2026-10-28 09:34:04 INFO [qq_bridge] event=connector_attached ...
RE_MAIN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*event=connector_(attached|detached)"
)
# 事件日志：2026-10-29 10:13:27  ATTACH  sid=2  gap=...  online_now=1  detach_total=1
RE_EVENT = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(ATTACH|DETACH|ALIVE)\s*(.*)$"
)


def human(sec: float) -> str:
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec / 60:.1f}m"
    if sec < 172800:
        return f"{sec / 3600:.2f}h"
    return f"{sec / 86400:.2f}d"


def event_log_files() -> list[Path]:
    """事件日志按时间顺序返回：.2 → .1 → 主文件。

    数字越大越老（与 connector._rotate_event_log 的切分顺序一致），
    所以拼接时先进老文件，保证时间线不乱。
    """
    return [
        p
        for p in (LOGS / "qq_conn_events.log.2", LOGS / "qq_conn_events.log.1", EVENT_LOG)
        if p.exists()
    ]


def collect() -> list[dict]:
    """合并两个数据源，按时间排序。同一时刻同类型只保留一条（事件日志优先，字段更全）。"""
    events: dict[tuple[str, str], dict] = {}

    # 主日志：.3 .2 .1 与当前文件，按时间自然衔接（轮转顺序即时间顺序）
    for name in ("qq_bridge.log.3", "qq_bridge.log.2", "qq_bridge.log.1", "qq_bridge.log"):
        p = LOGS / name
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                m = RE_MAIN.match(line)
                if not m:
                    continue
                ts, kind = m.group(1), m.group(2)
                key = (ts, "ATTACH" if kind == "attached" else "DETACH")
                events.setdefault(key, {"ts": ts, "kind": key[1], "detail": "", "src": "main"})
        except OSError:
            continue

    # 事件日志：字段更全，覆盖同键（轮转后最老的在 .2，主文件最新）
    for p in event_log_files():
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                m = RE_EVENT.match(line)
                if not m:
                    continue
                ts, kind, detail = m.group(1), m.group(2), m.group(3).strip()
                if kind == "ALIVE":
                    continue  # 心跳只用于判断存活断档，不参与在线时长配对
                key = (ts, kind)
                events[key] = {"ts": ts, "kind": kind, "detail": detail, "src": "event"}
        except OSError:
            pass

    return sorted(events.values(), key=lambda e: e["ts"])


def build_sessions(events: list[dict]) -> list[dict]:
    """把 ATTACH/DETACH 配成会话段。"""
    sessions: list[dict] = []
    cur: dict | None = None
    for e in events:
        if e["kind"] == "ATTACH":
            cur = {"start": e["ts"], "end": None, "detail": e["detail"]}
        elif e["kind"] == "DETACH":
            if cur is not None:
                cur["end"] = e["ts"]
                sessions.append(cur)
                cur = None
            else:
                # 只有 DETACH 没有对应 ATTACH（日志轮转截断），记一条孤立断开
                sessions.append({"start": None, "end": e["ts"], "detail": e["detail"]})
    if cur is not None:
        sessions.append(cur)  # 仍在线的当前会话
    return sessions


def dur(s: dict) -> float | None:
    if not s["start"] or not s["end"]:
        return None
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        return (
            time.mktime(time.strptime(s["end"], fmt))
            - time.mktime(time.strptime(s["start"], fmt))
        )
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="显示全部事件")
    ap.add_argument("--days", type=float, default=0, help="只看最近 N 天")
    args = ap.parse_args()

    events = collect()
    if not events:
        print("没有找到任何连接事件。确认 logs/ 下有 qq_bridge.log 或 qq_conn_events.log")
        return 1

    if args.days:
        cut = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - args.days * 86400))
        events = [e for e in events if e["ts"] >= cut]
        if not events:
            print(f"最近 {args.days} 天没有连接事件")
            return 0

    shown = events if args.all else events[-20:]

    print("=" * 78)
    print("连接事件时间线")
    print("=" * 78)
    for e in shown:
        mark = "↑ 连上" if e["kind"] == "ATTACH" else "↓ 断开"
        print(f"{e['ts']}  {mark}   {e['detail']}")
    if not args.all and len(events) > len(shown):
        print(f"... 省略更早的 {len(events) - len(shown)} 条（加 --all 看全部）")

    sessions = build_sessions(events)
    closed = [s for s in sessions if s["start"] and s["end"]]
    durations = [(s, d) for s in closed if (d := dur(s)) is not None and d >= 0]

    print()
    print("=" * 78)
    print("在线时长")
    print("=" * 78)
    for s, d in durations:
        print(f"{s['start']} → {s['end']}   {human(d):>8}")
    live = [s for s in sessions if s["start"] and not s["end"]]
    for s in live:
        fmt = "%Y-%m-%d %H:%M:%S"
        el = time.time() - time.mktime(time.strptime(s["start"], fmt))
        print(f"{s['start']} → 至今                 {human(el):>8}  ← 当前会话")

    print()
    print("=" * 78)
    print("统计")
    print("=" * 78)
    if durations:
        ds = [d for _, d in durations]
        best_s, best_d = max(durations, key=lambda x: x[1])
        print(f"完整会话数 : {len(ds)}")
        print(f"最长在线   : {human(best_d)}  ({best_s['start']} → {best_s['end']})")
        print(f"平均在线   : {human(sum(ds) / len(ds))}")
        print(f"中位在线   : {human(sorted(ds)[len(ds) // 2])}")
    det = len([e for e in events if e["kind"] == "DETACH"])
    print(f"断开次数   : {det}")

    today = time.strftime("%Y-%m-%d")
    t_det = len([e for e in events if e["kind"] == "DETACH" and e["ts"].startswith(today)])
    print(f"今日断开   : {t_det} 次")

    # 最后一条心跳 + 断档时长。心跳 10 分钟一条，断档 > 15m 即说明假死或停机。
    last_alive: str | None = None
    for p in event_log_files():
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                m = RE_EVENT.match(line)
                if m and m.group(2) == "ALIVE":
                    last_alive = m.group(1)  # 按文件时间顺序推进，最终留下最新一条
        except OSError:
            continue
    if last_alive:
        try:
            gap = time.time() - time.mktime(time.strptime(last_alive, "%Y-%m-%d %H:%M:%S"))
            print(
                f"最后心跳   : {last_alive}（{human(gap)}前；"
                "10 分钟一条，断档 >15m 即假死或停机）"
            )
        except ValueError:
            print(f"最后心跳   : {last_alive}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文
    except Exception:
        pass
    sys.exit(main())
