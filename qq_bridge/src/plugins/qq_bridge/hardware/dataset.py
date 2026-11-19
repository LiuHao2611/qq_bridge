# -*- coding: utf-8 -*-
"""hardware.dataset —— DIY 硬件行情的**只读**数据模型与载入（纯标准库）。

按功能拆分：本模块只管「把上游 CSV 读成 Instrument/Dataset + 月份与容量换算」；
涨跌聚合、检索、卡面 payload 在 ``market.py``（那边只 import 本模块，不反向依赖）。

数据来源：`vendored_frocloud/data/*.csv`（上游 frocloud-DIYPCmarket 全量搬运，
CC BY 4.0）。**只读**——本模块不写任何文件，也不提供任何写入接口；数据更新只能
走 `python tools/vendor_frocloud.py --download` 重新搬运，任何本地手改都会被
`--verify` 的 sha256 清单抓出。

口径（照抄上游 README，不自行发挥）：
  * 显卡 / CPU：人民币元，全新卡个人购买途径最低价；
  * 内存 / 固态 / 机械：**元每 GB**（1GB=1000MB），不是整条价 —— 展示时必须标单位，
    整条价由 spec（16G×2 / 1TB）换算，并在卡面注明"折算"；
  * 时间：月度快照，**不是日内实时报价**。GPU 表另有一期 `2026-07-下旬`（上游原样
    保留的非标准月份标签，本模块按上/中/下旬给排序权重，绝不改写成 `2026-07`）。

涨跌定义：同一标的「最新月价 vs 上一期价」的百分比变化；
`abs(pct) < 0.05%` 视作持平（按 0.1% 显示精度判定，卡面写"0.0%"就不是涨/跌）。
两期缺一期的标的不参与涨跌统计，覆盖数如实写进 `matched / active`。
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendored_frocloud"
DATA = VENDOR / "data"
UPSTREAM = ROOT / "UPSTREAM.json"

FLAT_EPS = 0.05  # 百分点；|环比| 小于它按持平，与卡面 0.1% 精度一致

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})(?:-(上旬|中旬|下旬))?$")
_TAIL_W = {"上旬": 0.25, "中旬": 0.5, "下旬": 0.75}
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)(G|T)B?$", re.I)


class DatasetError(Exception):
    """数据缺失/格式非法。一律显式上抛，绝不返回空数据冒充成功。"""


# ---------------------------------------------------------------------------
# 月份
# ---------------------------------------------------------------------------
def month_key(m: str) -> float:
    """'2026-10' → 2026.833；'2026-07-下旬' → 2026.583。

    用浮点把年月折算成可比较的标量（仅用于排序/走势横轴，不做日期运算）。
    """
    mm = _MONTH_RE.match(str(m or "").strip())
    if not mm:
        raise DatasetError("非法月份标签：%r（只接受 YYYY-MM 或 YYYY-MM-上/中/下旬）" % (m,))
    y, mo, tail = int(mm.group(1)), int(mm.group(2)), mm.group(3)
    if not 1 <= mo <= 12:
        raise DatasetError("月份越界：%r" % (m,))
    base = y + (mo - 1) / 12.0
    if tail:
        base += _TAIL_W[tail] / 12.0
    return base


def month_label(m: str) -> str:
    """展示用短标签：`2026-10` → `2026-10`；`2026-07-下旬` → `2026-07下`。"""
    mm = _MONTH_RE.match(str(m or "").strip())
    if not mm:
        raise DatasetError("非法月份标签：%r" % (m,))
    return "%s-%s" % (mm.group(1), mm.group(2)) + (mm.group(3)[0] if mm.group(3) else "")


# ---------------------------------------------------------------------------
# 规格换算（内存/存储：元/GB → 整条价）
# ---------------------------------------------------------------------------
def capacity_gb(spec: str) -> float | None:
    """'16G×2' → 32；'1TB' → 1000；'AVG' → None（无法换算，不猜）。"""
    s = str(spec or "").strip().replace("＊", "×").replace("*", "×")
    if not s:
        return None
    if "×" in s:
        cap, mult = 0.0, 1.0
        for p in re.split(r"[×xX]", s):
            p = p.strip()
            mm = _SIZE_RE.match(p)
            if mm:
                cap += float(mm.group(1)) * (1000.0 if mm.group(2).upper() == "T" else 1.0)
            elif re.fullmatch(r"\d+(?:\.\d+)?", p):
                mult *= float(p)  # 纯数字 = 条数，不是第二段容量
            else:
                return None
        return (cap * mult) or None
    mm = _SIZE_RE.match(s)
    if not mm:
        return None
    val = float(mm.group(1))
    return val * 1000.0 if mm.group(2).upper() == "T" else val


# ---------------------------------------------------------------------------
# 标的
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    """一条可跟踪的价格序列（显卡型号 / CPU 型号 / 内存存储规格）。"""

    key: str                 # 稳定标识（bucket:label）
    bucket: str              # gpu / cpu / mem
    bucket_name: str         # 显卡 / CPU / 内存 / 存储
    label: str               # 卡面主名（如 5090 / R79800X3D / DDR5 · 32G×2）
    sub: str                 # 副名（架构 / 平台 / 原始分类）
    unit: str                # 元 或 元/GB
    spec: str = ""           # 原始规格串（内存/存储用于元/GB→整条价换算）
    points: tuple = ()       # ((month, price), ...) 按月份升序

    @property
    def latest_month(self) -> str:
        return self.points[-1][0] if self.points else ""

    @property
    def latest(self) -> float:
        return self.points[-1][1] if self.points else 0.0

    @property
    def prev(self) -> tuple[str, float] | None:
        return self.points[-2] if len(self.points) >= 2 else None

    @property
    def delta(self) -> float | None:
        p = self.prev
        return None if p is None else self.latest - p[1]

    @property
    def pct(self) -> float | None:
        p = self.prev
        if p is None or p[1] == 0:
            return None
        return (self.latest - p[1]) / p[1] * 100.0

    @property
    def low(self) -> tuple[str, float]:
        return min(self.points, key=lambda mp: mp[1])

    @property
    def high(self) -> tuple[str, float]:
        return max(self.points, key=lambda mp: mp[1])

    @property
    def vs_low_pct(self) -> float | None:
        lo = self.low[1]
        if not lo:
            return None
        return (self.latest - lo) / lo * 100.0

    @property
    def kit_gb(self) -> float | None:
        return capacity_gb(self.spec) if self.bucket == "mem" else None


@dataclass(frozen=True)
class Tier:
    """天梯/跑分（上游 gpu-tier / cpu-tier）。缺字段留空，绝不补零。"""

    tier: str
    vendor: str
    extra: str            # 架构/平台
    score_main: str       # TimeSpy / CPUMark
    score_sub: str        # 总分 / 单核
    note: str = ""


@dataclass
class Dataset:
    instruments: list = field(default_factory=list)
    tiers: dict = field(default_factory=dict)   # (bucket, model) -> Tier
    meta: dict = field(default_factory=dict)

    @property
    def data_asof(self) -> str:
        v = str(self.meta.get("data_asof") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise DatasetError("UPSTREAM.json 缺少合法 data_asof：%r" % (v,))
        return v

    @property
    def days_old(self) -> int:
        y, m, d = (int(x) for x in self.data_asof.split("-"))
        return max(0, (date.today() - date(y, m, d)).days)

    def by_bucket(self, bucket: str) -> list:
        return [i for i in self.instruments if i.bucket == bucket]


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------
def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise DatasetError("上游数据缺失：%s（先跑 tools/vendor_frocloud.py）" % path)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise DatasetError("上游数据为空：%s" % path)
    return rows


def _price(raw: str, where: str) -> float:
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        raise DatasetError("价格不是数字：%s → %r" % (where, raw)) from None
    if v <= 0:
        raise DatasetError("价格非正数：%s → %r" % (where, raw))
    return v


def _build_price_series(rows, bucket, bucket_name, unit, label_of) -> list[Instrument]:
    groups: dict[str, dict] = {}
    for r in rows:
        month = str(r.get("date") or "").strip()
        month_key(month)  # 非法月份立即报错，绝不跳过
        label, sub, spec = label_of(r)
        key = "%s:%s" % (bucket, label)
        g = groups.setdefault(key, {"label": label, "sub": sub, "spec": spec, "pts": {}})
        if month in g["pts"]:
            raise DatasetError("同一标的同月重复报价：%s @ %s" % (label, month))
        g["pts"][month] = _price(r.get("price_cny"), "%s@%s" % (label, month))
    out = []
    for key, g in groups.items():
        pts = tuple(sorted(g["pts"].items(), key=lambda mp: month_key(mp[0])))
        out.append(Instrument(key=key, bucket=bucket, bucket_name=bucket_name,
                              label=g["label"], sub=g["sub"], unit=unit,
                              spec=g["spec"], points=pts))
    return out


def _gpu_label(r):
    return str(r["model"]).strip(), str(r.get("spec") or "").strip(), ""


def _cpu_label(r):
    return str(r["model"]).strip(), "", ""


def _mem_label(r):
    cat = str(r["category"]).strip()
    spec = str(r.get("spec") or "").strip()
    short = cat.replace("内存", "").strip() or cat  # 内存 DDR5 → DDR5
    label = ("%s · %s" % (short, spec)) if spec else short
    return label, cat, spec


def _load_tiers() -> dict:
    tiers: dict[str, Tier] = {}
    for r in _read_csv(DATA / "gpu-tier.csv"):
        model = str(r.get("model") or "").strip()
        if not model:
            raise DatasetError("gpu-tier.csv 存在空 model 行")
        tiers["gpu:" + model] = Tier(
            tier=str(r.get("tier") or "").strip(),
            vendor=str(r.get("vendor") or "").strip(),
            extra=str(r.get("series") or "").strip(),
            score_main=str(r.get("score_timespy") or "").strip(),
            score_sub=str(r.get("score_total") or "").strip(),
        )
    for r in _read_csv(DATA / "cpu-tier.csv"):
        model = str(r.get("model") or "").strip()
        if not model:
            raise DatasetError("cpu-tier.csv 存在空 model 行")
        cores, threads = str(r.get("cores") or "").strip(), str(r.get("threads") or "").strip()
        tiers["cpu:" + model] = Tier(
            tier=str(r.get("tier") or "").strip(),
            vendor=str(r.get("vendor") or "").strip(),
            extra=str(r.get("platform") or "").strip(),
            score_main=str(r.get("score_cpumark") or "").strip(),
            score_sub=str(r.get("score_single") or "").strip(),
            note=str(r.get("note") or "").strip() + (
                " · %s核%s线程" % (cores, threads) if cores and threads else ""),
        )
    return tiers


@lru_cache(maxsize=1)
def load() -> Dataset:
    if not UPSTREAM.is_file():
        raise DatasetError("缺少 UPSTREAM.json（搬运清单），先跑 tools/vendor_frocloud.py")
    meta = json.loads(UPSTREAM.read_text(encoding="utf-8"))
    insts = []
    insts += _build_price_series(_read_csv(DATA / "gpu-prices.csv"), "gpu", "显卡", "元", _gpu_label)
    insts += _build_price_series(_read_csv(DATA / "cpu-prices.csv"), "cpu", "CPU", "元", _cpu_label)
    insts += _build_price_series(_read_csv(DATA / "memory-prices.csv"), "mem", "内存 / 存储",
                                 "元/GB", _mem_label)
    ds = Dataset(instruments=insts, tiers=_load_tiers(), meta=meta)
    ds.data_asof  # noqa: B018 — 启动即校验口径字段，缺了立刻炸
    return ds
