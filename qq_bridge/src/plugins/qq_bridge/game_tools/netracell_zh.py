# -*- coding: utf-8 -*-
"""科研（Netracell：深层 CT_LAB / 时光 CT_HEX）官方简中解析器 —— 单一事实来源。

数据来源：**游戏官方本地化文件** zh.json（calamity-inc/warframe-languages-bin-data，
随游戏版本自动更新），抽取 Conquest 全部 296 条 + MissionName 67 条落盘为同目录
conquest_zh.json。所有中文名/描述都按官方 key 直接取值，不再手抄、不再对截图 OCR。

**每周快照策略（用户要求）**：
  * 每个游戏周（以官方 Conquests 的 Activation 为周键）**第一次取卡**时，拉一次官方
    zh.json 重建快照；快照 `_meta.week_key` 记录所属周。
  * 同一周内的后续请求只读本地快照，**不再联网下载**。
  * 跨周（Activation 变化）后第一次取卡自动重新拉取，如此循环。
  * 拉取失败不伪造成功：记 WARNING 并沿用现有快照（同一份官方数据，仅可能旧一周）。

key 规则（已实测）：
  * 任务名   /Lotus/Language/Conquest/MissionVariant_{Lab|Hex}Conquest_{code}
  * 任务说明 同 key + _Desc
  * 风险变量 /Lotus/Language/Conquest/Condition_{code} (+ _Desc)
  * 额外选项 /Lotus/Language/Conquest/PersonalMod_{code} (+ _Desc)
  * 任务类型 /Lotus/Language/Missions/MissionName_{TypeKey}
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_BUNDLE = Path(__file__).with_name("conquest_zh.json")
#: 包目录不可写时的兜底缓存位置（保证刷新仍能落盘）
_CACHE = Path(tempfile.gettempdir()) / "manju_conquest_zh.json"
_ZH_URL = "https://cdn.jsdelivr.net/gh/calamity-inc/warframe-languages-bin-data@master/zh.json"
_CQ = "/Lotus/Language/Conquest/"
_MS = "/Lotus/Language/Missions/MissionName_"

#: 征服类型 → 区块高亮词（「本周{高亮}科研任务」）
HIGHLIGHT_ZH: dict[str, str] = {"CT_LAB": "深层", "CT_HEX": "时光"}

#: worldstate 代码与本地化 key 后缀不一致的少量特例（官方 key 实测）
_CODE_ALIAS: dict[str, str] = {
    "EMPBlackHole": "MagneticHounds",       # 迷人弧犬
    "DullBlades": "ComboCountChance",       # 钝重兵刃
}

#: (征服类型, 任务类型) → MissionName_ 后缀（同 MT_DEFENSE 在深层=镜像防御、时光=防御）
_TYPE_KEY: dict[tuple[str, str], str] = {
    ("CT_LAB", "MT_DEFENSE"): "DualDefense",
    ("CT_LAB", "MT_ARTIFACT"): "Artifact",
    ("CT_LAB", "MT_EXTERMINATION"): "Exterminate",
    ("CT_LAB", "MT_ALCHEMY"): "Alchemy",
    ("CT_HEX", "MT_SURVIVAL"): "Survival",
    ("CT_HEX", "MT_DEFENSE"): "Defense",
    ("CT_HEX", "MT_ALCHEMY"): "Alchemy",
    ("CT_HEX", "MT_ENDLESS_CAPTURE"): "EndlessCapture",
    ("CT_HEX", "MT_ASSASSINATION"): "Assassination",
}

#: 本轮解析中未命中官方表的 key（供诊断；不含中文，不会误报）
MISSING: set[str] = set()

_lock = threading.Lock()
_raw: dict | None = None     # 原始 bundle（含 _meta）
_data: dict | None = None    # 展平后的 key → 文本


def _active_path() -> Path:
    """优先用较新的那份快照（包内 bundle 或临时缓存）。"""
    if _CACHE.exists():
        if not _BUNDLE.exists() or _CACHE.stat().st_mtime > _BUNDLE.stat().st_mtime:
            return _CACHE
    return _BUNDLE


def _load_raw() -> dict:
    global _raw, _data
    if _raw is None:
        b = json.loads(_active_path().read_text(encoding="utf-8"))
        _raw = b
        _data = {**b.get("conquest", {}), **b.get("missions", {})}
    return _raw


def _load() -> dict:
    _load_raw()
    return _data or {}


def _clean(code) -> str:
    """去掉内部路径/前缀，保留干净英文代号（仅作未收录时的可见回退）。"""
    s = str(code or "").strip()
    if "/Lotus/" in s:
        s = s.rsplit("/", 1)[-1]
    for p in ("PersonalMod_", "Mod_", "Conquest_", "Mission_"):
        if s.startswith(p):
            s = s[len(p):]
    return s


def _key_suffix(code) -> str:
    c = _clean(code)
    return _CODE_ALIAS.get(c, c)


def _get(key: str) -> str:
    v = _load().get(key)
    if not v:
        MISSING.add(key)
        return ""
    return v


def mission_type(ct: str, mt: str) -> str:
    """任务类型代码 → 官方中文（查不到返回原代码）。"""
    tk = _TYPE_KEY.get((ct, _clean(mt)))
    return (_get(_MS + tk) if tk else "") or _clean(mt) or mt


def mission_name(ct: str, code) -> str:
    ns = "Lab" if ct == "CT_LAB" else "Hex"
    return _get(f"{_CQ}MissionVariant_{ns}Conquest_{_key_suffix(code)}") or _clean(code)


def mission_desc(ct: str, code) -> str:
    ns = "Lab" if ct == "CT_LAB" else "Hex"
    return _get(f"{_CQ}MissionVariant_{ns}Conquest_{_key_suffix(code)}_Desc")


def risk_name(code) -> str:
    return _get(f"{_CQ}Condition_{_key_suffix(code)}") or _clean(code)


def risk_desc(code) -> str:
    return _get(f"{_CQ}Condition_{_key_suffix(code)}_Desc")


def modifier_name(code) -> str:
    return _get(f"{_CQ}PersonalMod_{_key_suffix(code)}") or _clean(code)


def modifier_desc(code) -> str:
    return _get(f"{_CQ}PersonalMod_{_key_suffix(code)}_Desc")


# ---------------------------------------------------------------- 每周快照
def week_key_of(cs: list) -> str:
    """Conquests → 游戏周键（官方 Activation 毫秒时间戳；缺失时退化为 ISO 年-周）。"""
    for c in cs or []:
        a = ((c.get("Activation") or {}).get("$date") or {}).get("$numberLong")
        if a:
            return str(a)
    y, w, _ = datetime.now(timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def snapshot_week() -> str:
    """当前本地快照所属的游戏周键（无则空串）。"""
    return str((_load_raw().get("_meta") or {}).get("week_key") or "")


def refresh_from_official(week_key: str | None = None, timeout: float = 60.0) -> int:
    """下载官方 zh.json 并重建快照（原子写入）。返回 Conquest key 数。"""
    global _raw, _data, _BUNDLE
    req = urllib.request.Request(_ZH_URL, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    con = {k: v for k, v in d.items() if k.startswith(_CQ)}
    mis = {k: v for k, v in d.items() if k.startswith(_MS)}
    if not con:
        raise RuntimeError("官方 zh.json 未包含 Conquest 段，拒绝覆盖本地快照")
    out = {
        "_meta": {
            "source": "calamity-inc/warframe-languages-bin-data zh.json",
            "week_key": week_key or "",
            "fetched_at": int(time.time()),
        },
        "conquest": con,
        "missions": mis,
    }
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    target = _BUNDLE
    try:
        tmp = target.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as e:  # 包目录只读 → 写临时缓存
        target = _CACHE
        target.write_text(text, encoding="utf-8")
        _BUNDLE = target
        _log.warning("bundle 不可写，改存缓存 %s（%s）", target, e)
    _raw = out
    _data = {**con, **mis}
    MISSING.clear()
    _log.info("netracell official zh snapshot rebuilt: %d keys, week=%s", len(con), week_key)
    return len(con)


def ensure_weekly(week_key: str, timeout: float = 60.0) -> bool:
    """确保快照属于给定游戏周：不同周则刷新一次；同周直接复用。返回是否刷新。"""
    with _lock:
        if snapshot_week() == week_key:
            return False
        try:
            refresh_from_official(week_key=week_key, timeout=timeout)
            return True
        except Exception as e:  # noqa: BLE001 —— 如实记警告，沿用旧快照，不伪造成功
            _log.warning("本周(%s)官方快照刷新失败，沿用现有快照：%s", week_key, e)
            return False
