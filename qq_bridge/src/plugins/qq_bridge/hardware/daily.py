# -*- coding: utf-8 -*-
"""hardware.daily —— 每日行情快照（存储 / 涨跌对比 / 定时）。

数据来自 `zol.collect()`（ZOL 公开报价），每天存一份 JSON：

    data/hardware_daily/2026-11-19.json
    {"date": "...", "fetched_at": "2026-11-19T10:10:12+08:00", "source": "zol",
     "items": {"gpu:RTX 5070": {"price": 4599, "samples": 15, ...}, ...},
     "errors": [...], "sources": 21}

为什么自己在本地存快照：ZOL 页面只有「当前参考价」，没有可机读的日线；
涨跌只能靠**我们每天采一次**积累。第一天是基线（卡面写「首日基线」），
第二天开始才有「今日 vs 昨日」。绝不拿月度 frocloud 的环比冒充日环比。

调度口径：默认每天 **10:10**（早间电商调价已完成、又避开 0-8 点维护与大促零点抖动），
可用环境变量 `HW_DAILY_AT=HH:MM` 覆盖；服务重启时若当天还没采、且已过点，会补采一次。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import dataset, market, zol

#: 快照目录（容器内 cwd=/app → /app/data 是持久卷；可用 HW_DAILY_DIR 覆盖）
DATA_DIR = Path(os.environ.get("HW_DAILY_DIR") or os.path.join("data", "hardware_daily"))
DEFAULT_AT = "10:10"
KEEP_DAYS = 180

_fetch_lock = threading.Lock()

#: 统一用北京时间（容器 TZ 可能是 UTC；10:10 不能变成北京时间 18:10）
CST = timezone(timedelta(hours=8))


def now() -> datetime:
    """当前北京时间（naive，便于与快照里的 ISO 字符串直接比较）。"""
    return datetime.now(timezone.utc).astimezone(CST).replace(tzinfo=None)


class DailyError(Exception):
    """采集/落盘失败（显式上抛，调用方必须让用户看见）。"""


# ---------------------------------------------------------------------------
# 目录 / 读写
# ---------------------------------------------------------------------------
def _today() -> str:
    return now().date().isoformat()


def _path(day: str) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        raise DailyError("非法日期：%r" % (day,))
    return DATA_DIR / ("%s.json" % day)


def load(day: str | None = None) -> dict | None:
    p = _path(day or _today())
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 坏文件显式报错，不当作「没数据」
        raise DailyError("快照损坏 %s：%s" % (p.name, e)) from e
    if not isinstance(data.get("items"), dict):
        raise DailyError("快照缺 items：%s" % p.name)
    return data


def days() -> list[str]:
    if not DATA_DIR.is_dir():
        return []
    return sorted(p.stem for p in DATA_DIR.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))


def latest_before(day: str) -> tuple[str, dict] | tuple[None, None]:
    for d in reversed(days()):
        if d < day:
            info = load(d)
            if info:
                return d, info
    return None, None


def save(day: str, payload: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(day)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _prune()
    return p


def _prune() -> None:
    for d in days()[:-KEEP_DAYS]:
        try:
            _path(d).unlink()
        except OSError:
            pass


def age_hours(snap: dict, now: datetime | None = None) -> float:
    ts = str(snap.get("fetched_at") or "")
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return 1e9
    cur = now or globals()["now"]()
    if (cur.tzinfo is None) != (t.tzinfo is None):
        cur = cur.replace(tzinfo=t.tzinfo)
    return max(0.0, (cur - t).total_seconds() / 3600.0)


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def cpu_models() -> list[str]:
    """日更**暂不追 CPU**（2026-11-19 实测结论）。

    ZOL 的 CPU 列表/家族页只公开十来个「热门」型号且多数标「暂无报价」：拿当前
    24 个在售型号去匹配只命中 3 个（9600X / 9700X / 9800X3D）。只报 3 款会让
    「几涨几跌」变成误导，所以日更先只做显卡（12 个系列，样本 4~23 条/系列），
    CPU 与内存存储继续走 frocloud 月度档案（`硬件行情 月报`）。
    要全量日更 CPU，只能上登录态浏览器（京东/淘宝），账号风控与本项目红线冲突。
    """
    return []


def fetch_today(force: bool = False) -> dict:
    """采一次并落盘。已有当天快照且 force=False 时直接返回（幂等）。"""
    day = _today()
    with _fetch_lock:
        if not force:
            cur = load(day)
            if cur:
                return cur
        got = zol.collect(cpu_models())
        payload = {
            "date": day,
            "fetched_at": now().isoformat(timespec="seconds"),
            "source": "zol",
            "items": got["items"],
            "errors": got["errors"],
            "sources": got["sources"],
        }
        if not got["items"]:
            raise DailyError("ZOL 采集 0 条有效报价（errors=%d）：%s"
                             % (len(got["errors"]), got["errors"][:2]))
        save(day, payload)
        return payload


# ---------------------------------------------------------------------------
# 卡面 payload
# ---------------------------------------------------------------------------
def _label(key: str) -> str:
    return key.split(":", 1)[-1]


def _bucket_of(key: str) -> str:
    return key.split(":", 1)[0]


def compare(today: dict, prev: dict | None) -> dict:
    """今日 vs 上一份快照：分类涨跌家数 + 全市场涨跌榜。"""
    cur = today["items"]
    old = (prev or {}).get("items") or {}
    buckets = []
    for key, name in (("gpu", "显卡"), ("cpu", "CPU")):
        items = {k: v for k, v in cur.items() if _bucket_of(k) == key}
        pairs = [(k, items[k]["price"], old[k]["price"]) for k in items
                 if k in old and old[k].get("price")]
        up = [x for x in pairs if _pct(x[1], x[2]) > 0.005]
        down = [x for x in pairs if _pct(x[1], x[2]) < -0.005]
        flat = [x for x in pairs if x not in up and x not in down]
        if not items:
            continue
        buckets.append({"key": key, "name": name, "tracked": len(items),
                        "compared": len(pairs), "up": len(up), "down": len(down),
                        "flat": len(flat)})
    movers = []
    for k, now, before in [(k, cur[k]["price"], old[k]["price"]) for k in cur if k in old]:
        movers.append({"key": k, "label": _label(k), "latest": now, "unit": "元",
                       "prev": before, "pct": _pct(now, before)})
    gain = sorted([m for m in movers if m["pct"] > 0.005],
                  key=lambda m: m["pct"], reverse=True)[:3]
    lose = sorted([m for m in movers if m["pct"] < -0.005],
                  key=lambda m: m["pct"])[:3]
    return {"buckets": buckets, "gainers": gain, "losers": lose,
            "missing": sum(1 for b in buckets if b["tracked"] == 0)}


def _pct(now: float, before: float) -> float:
    return (now - before) / before * 100.0 if before else 0.0


def overview_payload(snap: dict | None = None) -> dict:
    """给卡面用的今日总览 payload。没有当天快照时用最近一份并标 stale。"""
    snap = snap or load()
    if not snap:
        raise DailyError("还没有任何每日快照（先跑一次采集）")
    day = snap["date"]
    prev_day, prev = latest_before(day)
    cmp = compare(snap, prev)
    today_n = sum(1 for k in snap["items"] if k.startswith("gpu:"))
    cpu_n = sum(1 for k in snap["items"] if k.startswith("cpu:"))
    return {
        "view": "daily",
        "date": day,
        "fetched_at": str(snap.get("fetched_at") or "")[11:16],
        "prev_date": prev_day or "",
        "buckets": cmp["buckets"],
        "gainers": cmp["gainers"],
        "losers": cmp["losers"],
        "gpu_n": today_n,
        "cpu_n": cpu_n,
        "missing": cmp["missing"],
        "stale_hours": round(age_hours(snap), 1),
        "errors": len(snap.get("errors") or []),
        "at": "%02d:%02d" % schedule_at(),
        "note": "CPU / 内存存储：ZOL 公开列表无稳定日价，发「硬件行情 月报」看月度档案",
        "samples": sorted(
            ({"label": _label(k), "price": v["price"], "samples": v.get("samples", 0)}
             for k, v in snap["items"].items() if k.startswith("gpu:")),
            key=lambda x: x["price"], reverse=True)[:6],
        "asof": day,
    }


def search(q: str) -> list[str]:
    """在今日快照里模糊查型号（与 market.search 同一套打分：相等 > 前缀 > 包含）。"""
    snap = load()
    if not snap:
        return []
    nq = market.normalize(q)
    if not nq:
        return []
    scored = []
    for k in snap["items"]:
        label = k.split(":", 1)[-1]
        nl = market.normalize(label)
        if nl == nq:
            sc = 0
        elif nl.startswith(nq):
            sc = 1
        elif nq in nl:
            sc = 2
        elif nl in nq:
            sc = 3
        else:
            continue
        scored.append((sc, len(label), label))
    if not scored:
        return []
    scored.sort()
    top = scored[0][0]
    if top <= 1:
        scored = [x for x in scored if x[0] == top]
    return [x[2] for x in scored]


def model_payload(label: str, snap: dict | None = None) -> dict | None:
    """单型号的今日 payload（兼容 hw_market.build_model）。

    日线历史来自本地快照；不足 2 天时 pct=None（卡面写「首日基线」）。
    """
    snap = snap or load()
    if not snap:
        return None
    key = next((k for k in snap["items"] if _label(k) == label), None)
    if not key:
        return None
    hist = []
    for d in days():
        info = load(d)
        if info and key in info["items"]:
            hist.append((d, info["items"][key]["price"]))
    hist.sort()
    cur = snap["items"][key]["price"]
    prev = None
    for d, p in reversed(hist):
        if d < snap["date"]:
            prev = (d, p)
            break
    pct = None if not prev else _pct(cur, prev[1])
    lows = min(hist, key=lambda x: x[1]) if hist else (snap["date"], cur)
    highs = max(hist, key=lambda x: x[1]) if hist else (snap["date"], cur)
    pts = hist[-30:]
    k0, k1 = pts[0][0], pts[-1][0]
    ds = dataset.load()
    tier = _tier_for(key.split(":", 1)[0], label, ds)
    return {
        "view": "model",
        "source": "daily",
        "asof": snap["date"],
        "days_old": 0,
        "bucket": _bucket_of(key),
        "bucket_name": "显卡" if key.startswith("gpu:") else "CPU",
        "label": label,
        "sub": "ZOL 公开报价 · 当日样本最低价",
        "unit": "元",
        "latest": cur,
        "latest_month": snap["date"],
        "prev": prev[1] if prev else None,
        "prev_month": prev[0] if prev else "",
        "delta": (cur - prev[1]) if prev else None,
        "pct": pct,
        "low": lows[1], "low_month": lows[0] if len(hist) > 1 else "",
        "high": highs[1], "high_month": highs[0] if len(hist) > 1 else "",
        "vs_low_pct": _pct(cur, lows[1]) if lows[1] else None,
        "kit_gb": None, "kit_price": None,
        "chart": [{"t": (_idx(d, k0, k1)), "p": p, "m": d[5:]} for d, p in pts],
        "tier": ({"tier": tier.tier, "vendor": tier.vendor, "extra": tier.extra,
                  "score_main": tier.score_main, "score_sub": tier.score_sub,
                  "note": tier.note} if tier else None),
        "samples": snap["items"][key].get("samples"),
        "hist_days": len(hist),
    }


def _tier_for(bucket: str, label: str, ds) -> object | None:
    """日更展示名（RTX 5070 / R79800X3D）→ frocloud 天梯键的大小写不敏感查找。"""
    if bucket == "gpu":
        want = str(label).upper().replace("RTX", "").replace("RX", "").replace(" ", "")
    else:
        want = str(label).upper().replace(" ", "")
    for k, v in ds.tiers.items():
        if k.split(":", 1)[0] != bucket:
            continue
        have = k.split(":", 1)[1].upper().replace(" ", "")
        if have == want:
            return v
    return None


def _idx(d: str, d0: str, d1: str) -> float:
    if d0 == d1:
        return 0.5
    t0 = date.fromisoformat(d0).toordinal()
    t1 = date.fromisoformat(d1).toordinal()
    return (date.fromisoformat(d).toordinal() - t0) / max(1, t1 - t0)


# ---------------------------------------------------------------------------
# 调度
# ---------------------------------------------------------------------------
def schedule_at() -> tuple[int, int]:
    raw = os.environ.get("HW_DAILY_AT", DEFAULT_AT)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(raw).strip())
    if not m:
        raise DailyError("HW_DAILY_AT 格式应为 HH:MM，收到 %r" % (raw,))
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise DailyError("HW_DAILY_AT 越界：%r" % (raw,))
    return h, mi


def next_run(now: datetime | None = None) -> datetime:
    now = now or globals()["now"]()
    h, mi = schedule_at()
    target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target
