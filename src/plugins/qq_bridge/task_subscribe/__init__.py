# -*- coding: utf-8 -*-
"""task_subscribe —— Warframe 裂隙任务订阅（2026-11-05）。

用户按「条件」订阅，例如「订阅 钢铁裂隙 后纪 生存任务」，地图不限；
后台轮询世界状态，匹配的裂隙**新刷新出来**时，往原会话推一张单任务卡片。

设计要点（与 download.subscribe 同款零主包依赖 + 回调注入）：
  * Tenno Tools 的裂隙记录没有 ``id``，只有 tier/missionType/hard/location/
    faction/end。用 ``(tier, missionType, hard, location, end)`` 自造去重键；
    ``end`` 变了就是新一轮任务，会再推一次。
  * 订阅建立时先 ``prime()`` 把「当下已存在」的匹配任务记入 seen，
    只推之后新出现的，避免订阅瞬间把当前列表刷屏（符合「刷新出这个任务」）。
  * seen 随订阅一起原子落盘，容器重启不重复推。
  * 单条推送失败 / 单 tick 异常都显式记日志，不杀循环（CLOSE-02 / REDUN-01）。

纪律：不 import nonebot，不 import 主包；可裸单测。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import get_logger, jlog

log = get_logger()

#: 轮询间隔（秒）。裂隙任务轮换以分钟计，60s 足够及时；_worldstate 自带 20s 快缓存。
INTERVAL = max(20.0, float(os.environ.get("WF_TASK_SUB_INTERVAL") or 60.0))
#: 每个订阅记住的历史任务键数量（滚动窗口，防重复推）
SEEN_MAX = 200
#: 单 tick 单订阅最多推几张（一次刷出多条时防刷屏）
MAX_PUSH_PER_TICK = 3
#: 每个会话最多订阅几条
CAP_PER_SCOPE = 8

#: 通配短语：显式写「无论什么地图」等，等价于不设该维度
_WILDCARD_PHRASES = (
    "无论什么地图", "无论什么星球", "不限地图", "不限星球", "任意地图", "任意星球",
    "任何地图", "任何星球", "所有地图", "不限节点", "地图不限", "不管什么图",
)
_WILDCARD_WORDS = {"不限", "任意", "所有", "任何", "都行", "无所谓", "全部", "随便"}

#: 模式别名 → steel / normal / any
_MODE_ALIASES = {
    "钢铁裂隙": "steel", "钢铁": "steel", "钢路": "steel", "steel": "steel", "sp": "steel",
    "普通裂隙": "normal", "普通": "normal", "normal": "normal",
    "裂隙": "any", "任意裂隙": "any", "所有裂隙": "any", "fissure": "any", "fissures": "any",
}
#: 纪元别名 → Tenno Tools tier
_TIER_ALIASES = {
    "古纪": "Lith", "lith": "Lith", "t1": "Lith", "古纪t1": "Lith",
    "前纪": "Meso", "meso": "Meso", "t2": "Meso", "前纪t2": "Meso",
    "中纪": "Neo", "neo": "Neo", "t3": "Neo", "中纪t3": "Neo",
    "后纪": "Axi", "axi": "Axi", "t4": "Axi", "后纪t4": "Axi",
    "安魂": "Requiem", "requiem": "Requiem", "t5": "Requiem",
    "全能": "Omnia", "omnia": "Omnia", "t6": "Omnia",
}
#: 任务类型别名 → Tenno Tools missionType（与 warframe._MISSION_ZH 对齐）
_MISSION_ALIASES = {
    "生存": "Survival", "survival": "Survival",
    "防御": "Defense", "defense": "Defense",
    "移动防御": "Mobile Defense", "mobile defense": "Mobile Defense",
    "拦截": "Interception", "interception": "Interception",
    "破坏": "Sabotage", "sabotage": "Sabotage",
    "救援": "Rescue", "rescue": "Rescue",
    "歼灭": "Exterminate", "exterminate": "Exterminate", "extermination": "Exterminate",
    "间谍": "Spy", "spy": "Spy",
    "捕获": "Capture", "capture": "Capture",
    "挖掘": "Excavation", "excavation": "Excavation",
    "中断": "Disruption", "disruption": "Disruption",
    "强袭": "Assault", "assault": "Assault",
    "虚空覆涌": "Void Cascade", "void cascade": "Void Cascade",
    "虚空洪流": "Void Flood", "void flood": "Void Flood",
    "虚空审判": "Void Armageddon", "void armageddon": "Void Armageddon",
    "镜像防御": "Mirror Defense", "mirror defense": "Mirror Defense",
    "结合生存": "Conjunction Survival", "conjunction survival": "Conjunction Survival",
    "炼金": "Alchemy", "alchemy": "Alchemy",
    "劫持": "Hijack", "hijack": "Hijack",
    "叛逃": "Defection", "defection": "Defection",
    "升天": "Ascension", "ascension": "Ascension",
    "清巢": "Hive", "hive": "Hive",
}
#: 派系别名 → Tenno Tools faction
_FACTION_ALIASES = {
    "g系": "Grineer", "克隆尼": "Grineer", "grineer": "Grineer",
    "c系": "Corpus", "科普斯": "Corpus", "corpus": "Corpus",
    "i系": "Infestation", "感染者": "Infestation", "infested": "Infestation",
    "o系": "Corrupted", "欧罗金": "Corrupted", "orokin": "Corrupted",
    "m系": "Mutalist", "突变体": "Mutalist",
}
#: 星球中文 → Tenno Tools location 前缀（常用；其余走 warframe._LOCATION_ZH 反查）
_PLANET_ALIASES = {
    "地球": "Earth", "金星": "Venus", "火星": "Mars", "水星": "Mercury",
    "木星": "Jupiter", "土星": "Saturn", "天王星": "Uranus", "海王星": "Neptune",
    "冥王星": "Pluto", "阋神星": "Eris", "赛德娜": "Sedna", "谷神星": "Ceres",
    "欧罗巴": "Europa", "火卫一": "Phobos", "火卫二": "Deimos", "月球": "Lua",
    "虚空": "Void", "扎里曼": "Zariman", "赤毒要塞": "Kuva Fortress",
}

_TIER_LABEL = {"Lith": "古纪", "Meso": "前纪", "Neo": "中纪", "Axi": "后纪",
               "Requiem": "安魂", "Omnia": "全能"}
_MODE_LABEL = {"steel": "钢铁裂隙", "normal": "普通裂隙", "any": "裂隙"}


class TaskSubscribeError(Exception):
    """订阅业务错误（条件写错 / 重复 / 超额 / 落盘失败）。"""


# ---------------------------------------------------------------- 条件解析 ----
def _strip_verb(text: str) -> str:
    """剥掉命令动词与前缀，只留条件串。容忍「订阅钢铁裂隙 后纪 生存」粘连写法。"""
    s = (text or "").strip()
    s = s.lstrip("#＃/ ").strip()
    # 去掉 #wf / wf 之类的战甲前缀
    s = re.sub(r"^(?:wf|WF|星际战甲|战甲)\s+", "", s).strip()
    # 动词：订阅 / 关注 / 蹲 / 盯 / 监控（可粘连）
    s = re.sub(r"^(?:订阅|关注|蹲|盯|监控|提醒我)\s*", "", s).strip()
    return s


def parse_condition(text: str) -> dict:
    """把「钢铁裂隙 后纪 生存」解析成条件 dict。

    未指定的维度 = 不限。显式通配词被忽略。无法识别的词直接报错并给出可选值
    （CLOSE-02：绝不静默忽略用户输入）。
    """
    raw = _strip_verb(text)
    if not raw:
        raise TaskSubscribeError("条件不能为空，例如：订阅 钢铁裂隙 后纪 生存")
    # 先把「无论什么地图」这类短语整体删掉，避免被切成未知词
    for phrase in _WILDCARD_PHRASES:
        raw = raw.replace(phrase, " ")
    raw = raw.replace("任务", "")          # 「生存任务」→「生存」
    raw = re.sub(r"[（）()【】\[\]]", " ", raw)
    tokens = [t for t in re.split(r"[\s,，、/|+·]+", raw) if t]
    if not tokens:
        raise TaskSubscribeError("没识别出任何条件，例如：订阅 钢铁裂隙 后纪 生存")

    cond: dict[str, str] = {}
    unknown: list[str] = []
    for raw_tok in tokens:
        tok = raw_tok.strip().lower()
        if not tok or tok in _WILDCARD_WORDS:
            continue
        key, val = None, None
        if tok in _MODE_ALIASES:
            key, val = "mode", _MODE_ALIASES[tok]
        elif tok in _TIER_ALIASES:
            key, val = "tier", _TIER_ALIASES[tok]
        elif tok in _MISSION_ALIASES:
            key, val = "mission", _MISSION_ALIASES[tok]
        elif tok in _FACTION_ALIASES:
            key, val = "faction", _FACTION_ALIASES[tok]
        elif tok in _PLANET_ALIASES:
            key, val = "planet", _PLANET_ALIASES[tok]
        else:
            # 英文星球直传（Void / Deimos / Kuva Fortress 等）
            for _zh, _en in _PLANET_ALIASES.items():
                if tok == _en.lower():
                    key, val = "planet", _en
                    break
        if key is None:
            unknown.append(raw_tok)
            continue
        if key in cond and cond[key] != val:
            # 「钢铁 裂隙」这类写法：具体模式优先于 any，不算冲突
            if key == "mode" and "any" in (cond[key], val):
                pair = (cond[key], val)
                cond[key] = "steel" if "steel" in pair else "normal"
                continue
            raise TaskSubscribeError("同一维度写了两个不同值（%s）：%s / %s"
                                     % (key, cond[key], val))
        cond[key] = val

    if unknown:
        raise TaskSubscribeError("没认出「%s」。可用条件：模式(钢铁裂隙/普通裂隙)、"
                                 "纪元(古纪/前纪/中纪/后纪/安魂/全能)、任务(生存/防御/中断/歼灭…)、"
                                 "派系(G系/C系/I系)、星球(火星/虚空/火卫二…)"
                                 % "、".join(unknown[:4]))
    if not any(k in cond for k in ("mode", "tier", "mission", "planet", "faction")):
        raise TaskSubscribeError("至少要给一个有效条件，例如：订阅 钢铁裂隙 后纪 生存")

    parts = []
    if cond.get("mode"):
        parts.append(_MODE_LABEL.get(cond["mode"], cond["mode"]))
    if cond.get("tier"):
        parts.append(_TIER_LABEL.get(cond["tier"], cond["tier"]))
    if cond.get("mission"):
        parts.append(_mission_zh(cond["mission"]))
    if cond.get("faction"):
        parts.append(cond["faction"])
    if cond.get("planet"):
        parts.append(_planet_zh(cond["planet"]))
    else:
        parts.append("不限地图")
    return {**cond, "label": " · ".join(parts)}


#: 反向查表：英文 → 中文显示（只收中文键，跳过英文别名）
_EN2MISSION_ZH = {v: k for k, v in _MISSION_ALIASES.items()
                  if "\u4e00" <= k[0] <= "\u9fff"}
_EN2PLANET_ZH = {v: k for k, v in _PLANET_ALIASES.items()}


def _mission_zh(en: str) -> str:
    return _EN2MISSION_ZH.get(en, en)


def _planet_zh(en: str) -> str:
    return _EN2PLANET_ZH.get(en, en)


# ------------------------------------------------------------ 匹配 / 去重 ----
def task_key(f: dict) -> str:
    """裂隙无 id，用五元组自造稳定键；end 变化 = 新一轮任务。"""
    return "%s|%s|%d|%s|%d" % (
        str(f.get("tier") or ""), str(f.get("missionType") or ""),
        1 if f.get("hard") else 0, str(f.get("location") or ""),
        int(f.get("end") or 0),
    )


def match_task(f: dict, cond: dict) -> bool:
    """条件逐维度 AND；未设的维度视为通配。"""
    mode = cond.get("mode")
    if mode == "steel" and not f.get("hard"):
        return False
    if mode == "normal" and f.get("hard"):
        return False
    if cond.get("tier") and str(f.get("tier") or "") != cond["tier"]:
        return False
    if cond.get("mission") and str(f.get("missionType") or "") != cond["mission"]:
        return False
    if cond.get("faction") and str(f.get("faction") or "") != cond["faction"]:
        return False
    if cond.get("planet"):
        planet = str(f.get("location") or "").split("/", 1)[0].strip()
        if planet != cond["planet"]:
            return False
    return True


def describe_task(f: dict) -> dict:
    """把原始裂隙记录换成出卡用的中文展示字段（取不到 warframe 映射时自兜底）。"""
    tier = str(f.get("tier") or "")
    mission_en = str(f.get("missionType") or "")
    faction = str(f.get("faction") or "")
    loc = str(f.get("location") or "")
    planet_en, _, node_en = loc.partition("/")

    try:
        from ..game_tools import warframe
        mission = warframe._zh_mission(mission_en)
        tier_zh = warframe._zh_tier(tier)
        node = warframe._fiss_node(loc)
        remain = warframe._fmt_fissure(int(f.get("end") or 0) - warframe._now())
        letter = warframe._FISS_FACTION_LETTER.get(faction, faction[:1])
    except Exception:  # noqa: BLE001 — 裸单测 / 数据表未就绪时用内置兜底
        mission = _mission_zh(mission_en)
        tier_zh = "%s %s" % (tier, _TIER_LABEL.get(tier, ""))
        node = "%s(%s)" % (_planet_zh(planet_en), node_en) if node_en else loc
        secs = max(0, int(f.get("end") or 0) - int(time.time()))
        remain = "%d分%d秒" % (secs // 60, secs % 60)
        letter = faction[:1]

    hard = bool(f.get("hard"))
    return {
        "title": "%s%s%s" % ((letter + "系") if letter else "", "钢铁" if hard else "普通", mission),
        "mode": "钢铁" if hard else "普通",
        "tier": tier_zh.strip(),
        "tier_short": _TIER_LABEL.get(tier, tier),
        "mission": mission,
        "faction": (letter + "系") if letter else faction,
        "node": node,
        "planet": _planet_zh(planet_en) or planet_en,
        "remain": remain,
        "end": int(f.get("end") or 0),
        "hard": hard,
    }


# ------------------------------------------------------------------ 存储 ----
def _store_path() -> Path:
    """订阅文件路径：环境变量 > 容器卷内 /app/data > 项目 DATA_DIR。

    坑：容器里 ``queue.DATA_DIR`` 算出来是 ``/app/src/data``（镜像层），
    而 compose 的 bind mount 是 ``./data:/app/data``。订阅必须跨容器重建存活，
    所以容器部署时优先写卷内目录；本机运行时 /app/data 不存在，走 DATA_DIR。
    """
    override = os.environ.get("WF_TASK_SUB_FILE")
    if override:
        return Path(override)
    vol = Path("/app/data")
    if os.name == "posix" and vol.is_dir():
        return vol / "task_subscriptions.json"
    from ..queue import DATA_DIR
    return Path(DATA_DIR) / "task_subscriptions.json"


class TaskSubscriptionStore:
    """订阅登记：原子 JSON 持久化（与 download.SubscriptionStore 同款）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else _store_path()
        self._subs: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            data = (json.loads(self.path.read_text(encoding="utf-8")) or []) \
                if self.path.exists() else []
            self._subs = [self._norm(s) for s in data if isinstance(s, dict)]
        except Exception as e:  # noqa: BLE001 — 坏文件退化为空，不杀启动
            log.warning(jlog("task_sub_load_failed", path=str(self.path),
                             error=type(e).__name__))
            self._subs = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._subs, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)          # 原子替换，防写一半崩
        except Exception as e:  # noqa: BLE001
            raise TaskSubscribeError("订阅登记落盘失败（%s）" % type(e).__name__)

    @staticmethod
    def _norm(s: dict) -> dict:
        s.setdefault("id", uuid.uuid4().hex[:8])
        s.setdefault("scope", "")
        s.setdefault("user_id", 0)
        s.setdefault("group_id", 0)
        s.setdefault("channel", "napcat")
        s.setdefault("cond", {})
        s.setdefault("label", "")
        s.setdefault("text", "")
        s.setdefault("seen", [])
        s.setdefault("primed", False)
        s.setdefault("enabled", True)
        s.setdefault("created_at", time.time())
        s.setdefault("last_check_at", 0.0)
        s.setdefault("last_push_at", 0.0)
        s.setdefault("push_count", 0)
        return s

    def list(self, scope: str) -> list[dict]:
        return sorted((s for s in self._subs if s.get("scope") == scope),
                      key=lambda s: float(s.get("created_at") or 0))

    def all_enabled(self) -> list[dict]:
        return [s for s in self._subs if s.get("enabled")]

    def get(self, sid: str) -> dict | None:
        sid = (sid or "").strip()
        return next((s for s in self._subs if s.get("id") == sid), None)

    def add(self, cond: dict, scope: str, text: str = "", user_id: int = 0,
            group_id: int = 0, channel: str = "napcat") -> dict:
        label = str(cond.get("label") or "")
        if any(s.get("scope") == scope and s.get("label") == label for s in self._subs):
            raise TaskSubscribeError("这个会话已经订阅过「%s」了" % label)
        if len(self.list(scope)) >= CAP_PER_SCOPE:
            raise TaskSubscribeError("每个会话最多订阅 %d 个，先取消几个吧" % CAP_PER_SCOPE)
        sub = self._norm({
            "id": uuid.uuid4().hex[:8], "scope": str(scope), "cond": dict(cond),
            "label": label, "text": str(text or "")[:80],
            "user_id": int(user_id or 0), "group_id": int(group_id or 0),
            "channel": str(channel or "napcat"), "created_at": time.time(),
        })
        self._subs.append(sub)
        self._save()
        log.info(jlog("task_sub_added", sid=sub["id"], scope=scope, label=label))
        return sub

    def remove(self, sid: str, scope: str) -> dict:
        sub = self.get(sid)
        if not sub or sub.get("scope") != scope:
            raise TaskSubscribeError("订阅 %s 不存在（只能删本会话的）" % sid)
        self._subs = [s for s in self._subs if s.get("id") != sid]
        self._save()
        log.info(jlog("task_sub_removed", sid=sid, scope=scope))
        return sub

    def remove_all(self, scope: str) -> int:
        before = len(self._subs)
        self._subs = [s for s in self._subs if s.get("scope") != scope]
        n = before - len(self._subs)
        if n:
            self._save()
        return n

    def mark_checked(self, sid: str, keys: list[str], primed: bool = True,
                     pushed: int = 0) -> None:
        """合并本轮见到的任务键（滚动窗口），记录检查/推送时间。"""
        sub = self.get(sid)
        if not sub:
            return
        merged = list(dict.fromkeys(list(keys) + list(sub.get("seen") or [])))[:SEEN_MAX]
        sub["seen"] = merged
        sub["primed"] = bool(primed)
        sub["last_check_at"] = time.time()
        if pushed:
            sub["last_push_at"] = time.time()
            sub["push_count"] = int(sub.get("push_count") or 0) + int(pushed)
        try:
            self._save()
        except TaskSubscribeError as e:
            log.warning(jlog("task_sub_mark_save_failed", sid=sid,
                             detail=str(e)[:160]))


task_subscription_store = TaskSubscriptionStore()


# ------------------------------------------------------------------ 监控 ----
class TaskSubscriptionMonitor:
    """裂隙订阅轮询：到期检查 → 新任务 → on_match 回调推卡。"""

    def __init__(self, store: TaskSubscriptionStore | None = None,
                 on_match: Callable[[dict, dict], Awaitable[None]] | None = None,
                 fetch: Callable[[], Awaitable[list[dict]]] | None = None) -> None:
        self.store = store or task_subscription_store
        self.on_match = on_match
        self._fetch_impl = fetch
        self._task: "asyncio.Task | None" = None
        self._stopped = True

    async def fetch(self) -> list[dict]:
        """拉当前全部裂隙原始记录（普通 + 钢铁，不做速览筛选）。"""
        if self._fetch_impl is not None:
            return await self._fetch_impl()
        from ..game_tools import warframe
        d = await warframe._worldstate(fast=True)
        return list(warframe._slice(d, "fissures") or [])

    async def prime(self, sid: str) -> int:
        """订阅建立时预热：把当下已存在的匹配任务记入 seen，返回当前匹配数。

        只推「之后新刷新出来」的任务，避免订阅瞬间把当前列表刷屏。
        """
        sub = self.store.get(sid)
        if not sub:
            return 0
        try:
            fs = await self.fetch()
        except Exception as e:  # noqa: BLE001 — 预热失败不阻断订阅，下轮 tick 会补
            log.warning(jlog("task_sub_prime_failed", sid=sid,
                             error=type(e).__name__, detail=str(e)[:160]))
            return -1
        keys = [task_key(f) for f in fs if match_task(f, sub.get("cond") or {})]
        self.store.mark_checked(sid, keys, primed=True)
        return len(keys)

    async def _tick(self) -> None:
        subs = self.store.all_enabled()
        if not subs:
            return
        try:
            fs = await self.fetch()
        except Exception as e:  # noqa: BLE001 — 检查失败显式记录，下轮重试
            log.warning(jlog("task_sub_fetch_failed", error=type(e).__name__,
                             detail=str(e)[:160]))
            return

        for sub in list(subs):
            cond = sub.get("cond") or {}
            hits = [f for f in fs if match_task(f, cond)]
            keys = [task_key(f) for f in hits]
            seen = set(sub.get("seen") or [])
            primed = bool(sub.get("primed"))
            fresh = [f for f in hits if task_key(f) not in seen] if primed else []
            pushed = 0
            for f in fresh[:MAX_PUSH_PER_TICK]:
                if self.on_match is None:
                    break
                try:
                    await self.on_match(sub, f)
                    pushed += 1
                except Exception as e:  # noqa: BLE001 — 单条失败不阻其它订阅
                    log.warning(jlog("task_sub_notify_failed", sid=sub.get("id", ""),
                                     error=type(e).__name__, detail=str(e)[:160]))
            if len(fresh) > MAX_PUSH_PER_TICK:
                log.info(jlog("task_sub_push_capped", sid=sub.get("id", ""),
                              dropped=len(fresh) - MAX_PUSH_PER_TICK))
            if pushed:
                log.info(jlog("task_sub_pushed", sid=sub.get("id", ""), count=pushed))
            # 首次 tick 只预热（不推），之后只推新键
            self.store.mark_checked(sub["id"], keys, primed=True, pushed=pushed)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.ensure_future(self._loop())
        log.info(jlog("task_sub_monitor_started", interval=INTERVAL,
                      subs=len(self.store.all_enabled())))

    async def stop(self) -> None:
        self._stopped = True
        t, self._task = self._task, None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        log.info(jlog("task_sub_monitor_stopped"))

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 单 tick 失败不杀循环
                log.warning(jlog("task_sub_tick_failed", error=type(e).__name__,
                                 detail=str(e)[:160]))
            await asyncio.sleep(INTERVAL)


task_subscription_monitor = TaskSubscriptionMonitor()

__all__ = [
    "CAP_PER_SCOPE", "INTERVAL", "MAX_PUSH_PER_TICK", "TaskSubscribeError",
    "TaskSubscriptionMonitor", "TaskSubscriptionStore", "describe_task",
    "match_task", "parse_condition", "task_key",
    "task_subscription_monitor", "task_subscription_store",
]
