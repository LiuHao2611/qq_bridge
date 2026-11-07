# -*- coding: utf-8 -*-
"""riven_trend —— 紫卡趋势 / 紫卡词条价差（2026-11-05 P2）。

数据源前提（实测）：
  Warframe.Market 紫卡侧**没有历史成交价接口**（/v1/riven/items、
  /v1/riven/auctions/history、/v1/items/<x>/history 全部 404）；
  /v1/auctions/search 只给当前在售挂单（price_asc / price_desc 各约 100 条，
  page 参数被忽略）。普通物品的 /v1/items/<x>/statistics 不适用于紫卡。

因此本模块绝不编造历史价，分两条真实数据路径：

  1. **紫卡趋势**：自建定时采样器，把每次 `price_asc` 低价视图的
     「同等级 rank8 · 一口价/起拍价中位数」落盘成时间序列。
     只有 1 个点时如实显示「第 1 个采样点，趋势需积累」，不画假折线。
  2. **紫卡词条价差**：即时可算。对当前挂单里出现频次最高的若干正面词条，
     各抓一池 `&positive_stats=<slug>`，沿用 AGENTS.md 的
     「同等级 + 同分位 pair_k 对称 + 中位数」口径（与 check_riven_market.py G4
     同源），算出相对基准池的溢价排行。

口径红线（与 riven_card 一致）：
  * 价格一律取 price_asc 低价视图，price_desc 只用于词条热度、绝不取价；
  * 挂牌要价，**非成交价**；中心值只走 `_robust_center`（中位数）；
  * 样本不足 / 请求失败一律显式报错，不降级、不填「暂无」、不估算。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

from ..config import get_logger, jlog
from . import riven_card as rc
from . import warframe

log = get_logger()

#: 采样口径
RANK = 8                       # 统一比较满级卡，rank0 与 rank8 不可混算
MIN_SAMPLES = 3                # 少于 3 条不计算中位数
MAX_POINTS = 120               # 每武器保留采样点数（2h × 120 = 10 天）
MAX_TRACKED = 40               # 后台最多跟踪武器数（防把 WFM 打到 429）
SAMPLE_INTERVAL = 2 * 3600.0   # 后台采样间隔
MIN_POINT_GAP = 30 * 60.0      # 相邻两个采样点的最小时间差（查询触发的采样也遵守）
SPREAD_TOP_N = 5               # 词条价差最多比较几个正面词条

#: 从当前挂单里挑候选词条时的白名单（融合词条/弱点词条是 DE 44.1 新增，WFM 有 slug）
_CANDIDATE_ATTRS = (
    "critical_chance", "critical_damage", "multishot",
    "base_damage_/_melee_damage", "status_chance", "status_damage",
    "heat_damage", "cold_damage", "electric_damage", "toxin_damage",
    "fire_rate_/_attack_speed", "damage_vs_grineer", "damage_vs_corpus",
    "weak_point_damage", "weak_point_critical_chance", "ammo_maximum",
    "punch_through", "reload_speed", "magazine_capacity", "projectile_speed",
)


class RivenTrendError(RuntimeError):
    """趋势/价差业务的显式错误（不吞、不降级）。"""


# ------------------------------------------------------------------ 路径 ----
def _data_dir() -> Path:
    """数据目录：宿主与容器统一指向项目根 ``data/``（容器里它是 bind mount）。

    坑：``queue.DATA_DIR`` 用 parents[3]，在容器算出 ``/app/src/data``（镜像层，
    recreate 即丢），在宿主算出 ``src/data``。这里改用 parents[4]（项目根），
    与 ``warframe.py`` 的 ``_DATA_DIR`` 同源；仍支持 QQ_DATA_DIR 覆盖供测试隔离。
    """
    override = os.environ.get("QQ_DATA_DIR")
    if override:
        return Path(override).resolve()
    vol = Path("/app/data")
    if os.name == "posix" and vol.is_dir():
        return vol
    return Path(__file__).resolve().parents[4] / "data"


def _store_path() -> Path:
    override = os.environ.get("WF_RIVEN_TREND_FILE")
    if override:
        return Path(override)
    return _data_dir() / "riven_trends.json"


def _slug_map_path() -> Path:
    """WFM 紫卡武器 slug 静态映射（WFCD warframe-items 元数据，414 条）。"""
    return _data_dir() / "warframe_items" / "riven_wfm_slugs.json"


# ------------------------------------------------------------ slug 解析 ----
def _load_slugs() -> dict[str, str]:
    try:
        return json.loads(_slug_map_path().read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        raise RivenTrendError("WFM 紫卡 slug 映射不可用：%s" % type(e).__name__)


def resolve_weapon(weapon: str) -> dict | None:
    """中文名 / 别名 / 英文名 → {slug, en, zh}。

    WFM 紫卡 slug 是**基础武器族名**（如 `rubico`），不带 Prime/Wraith 等后缀；
    中文先过 `_resolve_weapon()` 拿官方英文名，再逐段去后缀匹配静态 slug 表。
    """
    q = (weapon or "").strip()
    if not q:
        return None
    slugs = _load_slugs()
    candidates: list[str] = []
    info = warframe._resolve_weapon(q)
    if info and info.get("en"):
        candidates.append(str(info["en"]))
    candidates.append(q)
    # 已直接给 WFM slug 的情况
    low = re.sub(r"[^a-z0-9]+", "_", q.lower()).strip("_")
    candidates.append(low)

    for cand in candidates:
        s = re.sub(r"[^a-z0-9]+", "_", str(cand).lower()).strip("_")
        parts = s.split("_")
        while parts:
            key = "_".join(parts)
            if key in slugs:
                return {"slug": key, "en": slugs[key],
                        "zh": warframe._weapon_zh(slugs[key])}
            parts.pop()
    return None


# -------------------------------------------------------------- 存储层 ----
def _norm_point(p: dict) -> dict:
    return {"ts": float(p.get("ts") or 0), "median": float(p.get("median") or 0),
            "n": int(p.get("n") or 0), "lo": float(p.get("lo") or 0),
            "hi": float(p.get("hi") or 0), "pool": int(p.get("pool") or 0)}


class RivenTrendStore:
    """采样时间序列 + 跟踪武器表，原子 JSON 持久化。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else _store_path()
        self._data: dict = {"version": 1, "tracked": {}, "series": {}}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            self._data = {
                "version": 1,
                "tracked": {k: dict(v) for k, v in (raw.get("tracked") or {}).items()
                            if isinstance(v, dict)},
                "series": {k: [_norm_point(p) for p in (v or []) if isinstance(p, dict)]
                           for k, v in (raw.get("series") or {}).items()},
            }
        except Exception as e:  # noqa: BLE001 — 坏文件退化为空，不杀启动
            log.warning(jlog("riven_trend_load_failed", path=str(self.path),
                             error=type(e).__name__))
            self._data = {"version": 1, "tracked": {}, "series": {}}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            raise RivenTrendError("趋势快照落盘失败（%s）" % type(e).__name__)

    def track(self, info: dict) -> None:
        """查询即加入跟踪列表；超过上限按最近查询时间淘汰（LRU）。"""
        slug = info["slug"]
        t = self._data["tracked"]
        t[slug] = {"en": info["en"], "zh": info["zh"], "last_query": time.time()}
        if len(t) > MAX_TRACKED:
            keep = sorted(t, key=lambda k: -(t[k].get("last_query") or 0))[:MAX_TRACKED]
            self._data["tracked"] = {k: t[k] for k in keep}
        self._save()

    def tracked(self) -> list[str]:
        return list(self._data["tracked"].keys())

    def points(self, slug: str) -> list[dict]:
        return list(self._data["series"].get(slug) or [])

    def append(self, slug: str, point: dict) -> bool:
        """写入一个采样点；与上一个点间隔不足则忽略（防查询把曲线刷平）。"""
        pts = self._data["series"].setdefault(slug, [])
        if pts and point["ts"] - pts[-1]["ts"] < MIN_POINT_GAP:
            return False
        pts.append(_norm_point(point))
        if len(pts) > MAX_POINTS:
            self._data["series"][slug] = pts[-MAX_POINTS:]
        self._save()
        return True


riven_trend_store = RivenTrendStore()


# -------------------------------------------------------------- 采样层 ----
async def sample(slug: str) -> dict:
    """抓一次真实挂牌 → 同等级 rank8 中位数采样点。样本不足直接抛错。"""
    auctions = await rc._fetch_auction_page(slug, "price_asc", allow_empty=True)
    prices = rc._prices_of(rc._same_rank(auctions, RANK))
    if len(prices) < MIN_SAMPLES:
        raise RivenTrendError(
            "WFM 同等级 rank%d 有效挂牌仅 %d 条（需 ≥%d），拒绝估算中位价"
            % (RANK, len(prices), MIN_SAMPLES))
    return {"ts": time.time(), "median": rc._robust_center(prices),
            "n": len(prices), "lo": prices[0], "hi": prices[-1],
            "pool": len(auctions)}


async def trend_data(weapon: str) -> dict:
    """`#紫卡趋势 <武器>` 的数据。首次查询会真实采样一次并明确标注只有 1 个点。"""
    info = resolve_weapon(weapon)
    if not info:
        raise RivenTrendError("没找到武器「%s」的 WFM 紫卡 slug（试试中文全名或英文名）" % weapon)
    store = riven_trend_store
    store.track(info)
    slug = info["slug"]
    pts = store.points(slug)
    # 查询也触发一次采样；距上一点 < MIN_POINT_GAP 时 append 会自行忽略
    point = await sample(slug)
    added = store.append(slug, point)
    pts = store.points(slug)
    first, last = pts[0]["median"], pts[-1]["median"]
    change = (last / first - 1.0) * 100.0 if first > 0 else 0.0
    span_days = (pts[-1]["ts"] - pts[0]["ts"]) / 86400.0
    return {
        "weapon_zh": info["zh"], "weapon_en": info["en"], "slug": slug,
        "points": pts[-12:], "n_points": len(pts),
        "current": point, "change_pct": change,
        "span_days": span_days,
        "just_sampled": added,
        "enough_for_trend": len(pts) >= 2,
        "source": ("WFM 紫卡挂牌要价·非成交价｜price_asc 低价视图｜同等级 rank8｜"
                   "中位数｜本地采样 %d 点（%.1f 天）" % (len(pts), span_days)),
    }


def _top_attrs(auctions: list[dict]) -> list[str]:
    """从当前 rank8 挂单里挑出现频次最高的正面词条（候选有真实依据，不拍脑袋）。"""
    cnt: dict[str, int] = {}
    for a in rc._same_rank(auctions, RANK):
        for at in rc._auction_attrs(a):
            if at.get("positive") and at.get("url_name") in _CANDIDATE_ATTRS:
                k = str(at["url_name"])
                cnt[k] = cnt.get(k, 0) + 1
    return [k for k, _ in sorted(cnt.items(), key=lambda kv: -kv[1])[:SPREAD_TOP_N]]


def paired_pct(base_prices: list[float], stat_prices: list[float]) -> tuple[float, int] | None:
    """同等级 + 同分位对称溢价（与 check_riven_market.py::G4 同源）。

    pair_k = min(两池样本数)，各取前 pair_k 条最低价再比中位数；样本不足返回 None
    （绝不硬算）。不对称全量对比会造出 Glaxion +100% → +700% 的伪溢价。
    """
    b, st = sorted(base_prices), sorted(stat_prices)
    pair_k = min(len(b), len(st))
    if pair_k < MIN_SAMPLES:
        return None
    base_med = rc._robust_center(b[:pair_k])
    stat_med = rc._robust_center(st[:pair_k])
    if base_med <= 0:
        return None
    return (stat_med / base_med - 1.0) * 100.0, pair_k


async def spread_data(weapon: str) -> dict:
    """`#紫卡价差 <武器>` 的数据：正面词条同分位溢价排行。

    与 check_riven_market.py::G4 同源：pair_k = min(n_base, n_stat)，
    两池各取前 pair_k 条最低价再比中位数，避免不对称对比造成伪溢价。
    """
    info = resolve_weapon(weapon)
    if not info:
        raise RivenTrendError("没找到武器「%s」的 WFM 紫卡 slug" % weapon)
    slug = info["slug"]
    base_auctions = await rc._fetch_auction_page(slug, "price_asc", allow_empty=True)
    base_prices = rc._prices_of(rc._same_rank(base_auctions, RANK))
    if len(base_prices) < MIN_SAMPLES:
        raise RivenTrendError("基准池 rank8 有效挂牌仅 %d 条（需 ≥%d），拒绝估算价差"
                              % (len(base_prices), MIN_SAMPLES))
    base_med_all = rc._robust_center(base_prices)
    rows: list[dict] = []
    for attr in _top_attrs(base_auctions):
        extra = "&positive_stats=" + quote(attr, safe="")
        pool = await rc._fetch_auction_page(slug, "price_asc", extra, allow_empty=True)
        stat_prices = rc._prices_of(rc._same_rank(pool, RANK))
        got = paired_pct(base_prices, stat_prices)
        if got is None:
            continue                       # 样本不足不硬算，如实跳过
        pct, pair_k = got
        # 卡面「词条池中位价」必须与溢价同源：都用前 pair_k 条最低价，
        # 否则显示全量中位价却按对称子集算溢价，用户会算不出这个百分比。
        rows.append({"attr": attr, "zh": rc._short(attr), "pair_k": pair_k,
                     "pool_n": len(stat_prices),
                     "median": rc._robust_center(sorted(stat_prices)[:pair_k]),
                     "base_med": rc._robust_center(sorted(base_prices)[:pair_k]),
                     "pct": pct})
    rows.sort(key=lambda r: -r["pct"])
    return {
        "weapon_zh": info["zh"], "weapon_en": info["en"], "slug": slug,
        "base_median": base_med_all, "base_n": len(base_prices),
        "rows": rows,
        "source": ("WFM 紫卡挂牌要价·非成交价｜price_asc 低价视图｜同等级 rank8｜"
                   "同分位配对对称对比｜中位数｜非成交价"),
    }


# ------------------------------------------------------------ 后台采样 ----
class RivenTrendMonitor:
    """后台每 SAMPLE_INTERVAL 秒采样一次已跟踪武器（失败记日志，不杀循环）。"""

    def __init__(self, store: RivenTrendStore | None = None) -> None:
        self.store = store or riven_trend_store
        self._task: "asyncio.Task | None" = None
        self._stopped = True

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.ensure_future(self._loop())
        log.info(jlog("riven_trend_monitor_started", interval=SAMPLE_INTERVAL,
                      tracked=len(self.store.tracked())))

    async def stop(self) -> None:
        self._stopped = True
        t, self._task = self._task, None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        log.info(jlog("riven_trend_monitor_stopped"))

    async def _tick(self) -> None:
        for slug in self.store.tracked():
            try:
                point = await sample(slug)
                self.store.append(slug, point)
            except Exception as e:  # noqa: BLE001 — 单个武器失败不阻其它
                log.warning(jlog("riven_trend_sample_failed", slug=slug,
                                 error=type(e).__name__, detail=str(e)[:160]))
            await asyncio.sleep(1.0)

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning(jlog("riven_trend_tick_failed",
                                 error=type(e).__name__, detail=str(e)[:160]))
            await asyncio.sleep(SAMPLE_INTERVAL)


riven_trend_monitor = RivenTrendMonitor()

__all__ = [
    "MAX_POINTS", "MIN_SAMPLES", "RANK", "RivenTrendError", "RivenTrendMonitor",
    "RivenTrendStore", "resolve_weapon", "riven_trend_monitor", "riven_trend_store",
    "paired_pct", "sample", "spread_data", "trend_data",
]
