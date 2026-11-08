# -*- coding: utf-8 -*-
"""bounty_live.py —— 从 worldstate.wf.wiki RSC payload 补回赏金卡真实数据。

为什么在渲染侧补齐（2026-11-08 事故）：``game_tools/warframe.py`` 有 280KB，
受 20KB 门禁约束不能改大；而 ``_wfwiki_payload`` 用 ``replace('\\"','"')`` 反解
RSC 字符串，破坏转义后 ``except: pass`` 静默吞掉 ``JSONDecodeError``，导致
``bountyBoard`` 永远为空 → MOD / 债券奖励整段消失。同页 ``groups`` 虽解析成功，
``_bounty_card_data`` 却改用硬编码 ``_BOUNTY_GROUPS`` + 44 条手写 ``_BOUNTY_NAMES``
→ 任务名、目标行与别人机器人对不上。

本模块不改任何数据源文件，只读两个已在请求内就绪的进程缓存：
  * ``warframe._wfwiki_cache["html"]``  —— ``bounties_card_data()`` 刚抓的页面
  * ``warframe._official_cache["data"]`` —— 官方 worldstate（jobType / 等级）
再按 **JS 字符串字面量** 正确解码 RSC，补出：
  * ``worlds[地球].label/items``   ← bountyBoard.CetusSyndicate rewards（8 MOD）
  * ``worlds[金星].label/items``   ← bountyBoard.SolarisSyndicate（5 债券）
  * ``worlds[].jobs``              ← bountyBoard 最后 2 个非隔离库 job（等级 /
                                      类型 / 合一众）+ WFA 词库中文任务名
  * ``factions[]``                 ← groups 的 node / mission / challenge / ally
                                      （第二行挑战名走词库，第三行「目标：」）
  * ``deep``                      ← 移除不可验证的硬编码轮换名与最终任务

任务名 / 挑战名一律取自 WFA_Lexicon 同源中文词库 ``dict.zh.json``，本模块只做
**键构造与同族词干匹配**，不手写任何中文任务名。查不到就保留原值 + 显式告警，
绝不编造。深矿具体轮换名在 RSC / 官方 worldstate / 词库里都无对应字段，故只留
可验证的「[钢铁之路] / 等级 130-140 / 5阶段」。
"""
from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("qq_bridge")

_BLOCK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
_KEYS = ("bountyBoard", "groups", "bounties")
_ROOT = Path(__file__).resolve().parents[5]  # .../qq_bridge
_LEX_FILE = _ROOT / "data" / "warframe_items" / "dict.zh.json"

_TAG_BY_WORLD = {
    "地球赏金": "CetusSyndicate",
    "金星赏金": "SolarisSyndicate",
    "火卫二赏金": "EntratiSyndicate",
}
_WORLD_META = {
    "地球赏金": ("circle", "#4CAF50"),
    "金星赏金": ("diamond", "#FF9800"),
    "火卫二赏金": ("triangle", "#9C27B0"),
}
_FACTION_BY_TAG = {
    "ZarimanSyndicate": ("扎里曼赏金", 3, ("star", "#FFC107")),
    "EntratiLabSyndicate": ("圣所赏金", 2, ("round_square", "#00BCD4")),
    "HexSyndicate": ("六人组赏金", 2, ("square", "#607D8B")),
}

#: jobType 词干 → 任务类型词（长词干在前，避免 Ass 抢在 Assassinate 前面）。
#: 这是任务类型术语表，不是任务名表；任务名一律走 WFA 词库。
_MISSION_TOKENS = (
    ("Excavation", "挖掘"), ("Exterminate", "歼灭"), ("Assassinate", "刺杀"),
    ("EndlessAreaDef", "防御"), ("AreaDefense", "防御"), ("AreaDef", "防御"),
    ("Preservation", "防御"), ("Defense", "防御"), ("Survivor", "生存"),
    ("Survival", "生存"), ("GrnSurv", "生存"), ("EndlessPurify", "净化"),
    ("Purify", "净化"), ("Excav", "挖掘"), ("Recovery", "回收"),
    ("Resource", "资源"), ("KeyPieces", "探寻"), ("Keys", "探寻"),
    ("Cache", "缓存"), ("Cap", "捕获"), ("Ass", "刺杀"), ("Ext", "歼灭"),
    ("Lib", "防御"), ("Sab", "破坏"), ("Resc", "救援"), ("Spy", "间谍"),
    ("Theft", "挖掘"), ("Intel", "情报"),
)

_LEAF: dict[str, str] | None = None
_DEIMOS: dict[str, str] | None = None
_NARMER: dict[str, str] | None = None
_NARV: dict[str, str] | None = None
_CH_TPLS: list[tuple[int, re.Pattern, str]] | None = None


# ------------------------------------------------------------ RSC 解码 ----
def decode_rsc(html: str) -> str:
    """RSC block 是 JS 字符串字面量，必须逐块按 JSON 字符串解码。

    禁止 ``replace('\\"','"')``：会把 ``"name":"\\"秩序\\"残留物"`` 变成非法 JSON。
    """
    return "".join(json.loads('"' + b + '"') for b in _BLOCK_RE.findall(html or ""))


def parse_payload(html: str) -> dict:
    """解析出 bountyBoard / groups / bounties；缺字段显式告警，不静默吞异常。"""
    text = decode_rsc(html)
    out: dict = {}
    dec = json.JSONDecoder()
    for key in _KEYS:
        i = text.find('"%s":' % key)
        if i < 0:
            log.warning("bounty_rsc_missing_key key=%s", key)
            continue
        body = text[i + len('"%s"' % key):].lstrip()
        if body.startswith(":"):
            body = body[1:].lstrip()
        try:
            out[key] = dec.raw_decode(body)[0]
        except Exception as e:  # noqa: BLE001 — 不静默：告警后该键视为缺失
            log.warning("bounty_rsc_decode_failed key=%s err=%s:%s",
                        key, type(e).__name__, e)
    return out


def _wf():
    """延迟导入 warframe：模板层不与 game_tools 在导入期耦合。"""
    from src.plugins.qq_bridge.game_tools import warframe
    return warframe


# -------------------------------------------------------------- 词库 ----
def _lexicon() -> dict[str, str]:
    """WFA_Lexicon：key 尾段 → 中文（进程内只建一次，3.8MB 只读一次）。"""
    global _LEAF
    if _LEAF is not None:
        return _LEAF
    try:
        raw = json.loads(_LEX_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 显式告警，不静默
        log.warning("bounty_lexicon_load_failed path=%s err=%s:%s",
                    _LEX_FILE, type(e).__name__, e)
        raw = {}
    _LEAF = {}
    for k, v in raw.items():
        if isinstance(v, str):
            _LEAF.setdefault(str(k).rsplit("/", 1)[-1], v)
    return _LEAF


def _lcp(a: str, b: str) -> int:
    """最长公共前缀长度（大小写不敏感），用于同族词干匹配。"""
    n = 0
    for x, y in zip(a.lower(), b.lower()):
        if x != y:
            break
        n += 1
    return n


def _best_stem(stem: str, pool: dict[str, str]) -> str:
    """同族词干取最长公共前缀；低于 3 字符或并列第一则判为查不到，绝不猜。"""
    scored = sorted(((_lcp(stem, s), v) for s, v in pool.items()), reverse=True)
    if not scored or scored[0][0] < 3:
        return ""
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return ""
    return str(scored[0][1])


def _families() -> tuple[dict, dict, dict]:
    """三族词库键：火卫二 / 合一众地球 / 合一众金星。值全部来自词库。"""
    global _DEIMOS, _NARMER, _NARV
    if _DEIMOS is not None:
        return _DEIMOS, _NARMER, _NARV
    leaf = _lexicon()
    _DEIMOS, _NARMER, _NARV = {}, {}, {}
    for k, v in leaf.items():
        if not isinstance(v, str) or len(v) > 30 or "|" in v:
            continue
        if k.startswith("DeimosBounty") and k.endswith("Name"):
            _DEIMOS[k[len("DeimosBounty"):-4]] = v
        elif k.startswith("NarmerBountyVenus") and k.endswith("Title"):
            _NARV[k[len("NarmerBountyVenus"):-5]] = v
        elif k.startswith("NarmerBounty") and k.endswith("Title"):
            _NARMER[k[len("NarmerBounty"):-5]] = v
    return _DEIMOS, _NARMER, _NARV


def job_name(leaf: str, narmer: bool = False) -> str:
    """官方 jobType 尾段 → WFA 中文任务名；查不到返回空串（调用方显式告警）。"""
    if not leaf:
        return ""
    d = _lexicon()
    deimos, narmer_pool, narv = _families()
    # 合一众优先走 Narmer 词族：否则 AttritionBountyLib 会被普通键抢成
    # 「削弱 Grineer 的据点」，而别人卡是「带他们回家 合一众」。
    if narmer:
        if leaf.startswith("NarmerVenus"):
            stem = leaf[len("NarmerVenus"):].replace("Job", "")
            nm = _best_stem(stem, narv)
        else:
            stem = leaf.replace("AttritionBounty", "").replace("Bounty", "")
            nm = _best_stem(stem, narmer_pool)
        if nm:
            return nm.replace("（合一众）", "").strip()
    for key in (leaf + "Title", leaf + "Name"):
        if key in d and len(d[key]) <= 30 and "|" not in d[key]:
            return d[key]
    if leaf.startswith("Venus"):
        for key in (leaf[5:] + "Title", leaf[5:] + "Name"):
            if key in d and len(d[key]) <= 30 and "|" not in d[key]:
                return d[key]
    if leaf.startswith("Deimos") and leaf.endswith("Bounty"):
        return _best_stem(leaf[6:-6], deimos)
    return ""


def mission_type(leaf: str) -> str:
    """jobType 词干 → 任务类型词（缓存 / 防御 / 挖掘 / 生存 …）。"""
    for token, zh in _MISSION_TOKENS:
        if token in leaf:
            return zh
    return ""


def challenge_name(desc: str) -> str:
    """groups.challenge 描述 → 词库 ``Challenge_*_Name``（通用模板反查）。

    ``Challenge_*_Desc`` 里 ``|COUNT|`` 是数量占位；按行拆模板、去掉颜色 /
    ALLY 标记后按长度降序匹配，最长模板优先，避免「击杀 N 名敌人」这种短
    模板误抢「从上方击杀 N 名敌人」。查不到返回空串，不编造。
    """
    global _CH_TPLS
    if _CH_TPLS is None:
        leaf = _lexicon()
        tpls: list[tuple[int, re.Pattern, str]] = []
        for k, v in leaf.items():
            if not (k.startswith("Challenge_") and k.endswith("_Desc")):
                continue
            nm = leaf.get(k[:-5] + "_Name")
            if not nm or "|" in nm:
                continue
            for line in re.split(r"[\r\n]+", v):
                raw = re.sub(r"\|(?!COUNT)[A-Z_]+\|", "", line).strip()
                raw = re.sub(r"\s+", "", raw)
                if len(raw) < 6:
                    continue
                pat = re.escape(raw).replace(re.escape("|COUNT|"), r"\d+")
                try:
                    tpls.append((len(raw), re.compile(pat), nm))
                except re.error:
                    continue
        tpls.sort(key=lambda x: -x[0])
        _CH_TPLS = tpls
    compact = re.sub(r"\s+", "", str(desc or ""))
    for _, rx, nm in _CH_TPLS:
        if rx.search(compact):
            return nm
    return ""


# -------------------------------------------------------------- 世界 ----
def _reward_names(board: dict, kind: str) -> list[str]:
    """复用 warframe.py 的纯函数，从正确的 bountyBoard 还原 MOD / 债券。"""
    jobs = board.get("jobs") or []
    W = _wf()
    if kind == "mods":
        return W._bounty_mods(jobs)
    return [nm.replace("债务债券", "债券") for nm in W._bounty_bonds(jobs)]


def _official_jobs(official: dict | None, tag: str) -> list[dict]:
    for sy in ((official or {}).get("SyndicateMissions") or []):
        if sy.get("Tag") == tag:
            return list(sy.get("Jobs") or [])
    return []


def _match_job(jobs: list[dict], lo, hi) -> dict:
    for j in jobs:
        if (j.get("minEnemyLevel"), j.get("maxEnemyLevel")) == (lo, hi):
            return j
    return {}


def _board_top(board: dict) -> list[dict]:
    """bountyBoard 最后 2 个非隔离库 job —— 与别人卡的展示顺序一致。

    RSC 里 ``isVault`` 为 ``True`` 才是隔离库；``$undefined`` 是普通任务。
    """
    jobs = [j for j in (board.get("jobs") or []) if isinstance(j, dict)]
    return [j for j in jobs if j.get("isVault") is not True][-2:]


def _clean(v) -> str:
    s = str(v or "").strip()
    return "" if s == "$undefined" else s


def _patch_worlds(worlds: list[dict], pl: dict, official: dict | None) -> None:
    boards = {b.get("tag"): b for b in (pl.get("bountyBoard") or [])
              if isinstance(b, dict)}
    for w in worlds:
        if not isinstance(w, dict):
            continue
        zh = str(w.get("name") or "")
        tag = _TAG_BY_WORLD.get(zh)
        if not tag:
            continue
        board = boards.get(tag) or {}
        if tag == "CetusSyndicate":
            items = _reward_names(board, "mods")
            if items:
                w["label"], w["items"] = "MOD奖励轮换", items
        elif tag == "SolarisSyndicate":
            items = _reward_names(board, "bonds")
            if items:
                w["label"], w["items"] = "债券轮换", items

        top = _board_top(board)
        if not top:
            log.warning("bounty_board_jobs_missing tag=%s", tag)
            continue
        off = _official_jobs(official, tag)
        icon, color = _WORLD_META[zh]
        jobs = []
        for bj in top:
            lo, hi = bj.get("minLevel"), bj.get("maxLevel")
            oj = _match_job(off, lo, hi)
            jt = str((oj or {}).get("jobType") or "")
            leaf = jt.rsplit("/", 1)[-1]
            narmer = "/Narmer/" in jt
            nm = job_name(leaf, narmer)
            if not nm:
                log.warning("bounty_wfa_name_missing tag=%s jobType=%s lvl=%s-%s",
                            tag, leaf or "(empty)", lo, hi)
                nm = _clean(bj.get("objective"))
            typ = mission_type(leaf) or _clean(bj.get("objective"))
            jobs.append({
                "lvl": "%s-%s" % (lo, hi), "type": typ, "name": nm,
                "narmer": narmer, "icon": icon, "color": color,
                "mission": "", "objective": "",
            })
        w["jobs"] = jobs


# ------------------------------------------------------------ 派系 ----
def _patch_factions(factions: list[dict], pl: dict) -> list[dict]:
    """用 groups 的实时 node/mission/challenge/ally 重建派系卡。

    第二行 = mission（类型词）+ 词库挑战名 [·ALLY的赏金]；
    第三行「目标：」= challenge 原文。全部来自 RSC + 词库，无硬编码清单。
    """
    groups = {g.get("tag"): g for g in (pl.get("groups") or [])
              if isinstance(g, dict)}
    old = {str(f.get("name") or ""): f for f in factions if isinstance(f, dict)}
    out: list[dict] = []
    for tag, (zh, take, meta) in _FACTION_BY_TAG.items():
        g = groups.get(tag)
        if not g or not g.get("jobs"):
            log.warning("bounty_groups_missing tag=%s", tag)
            out.append(old.get(zh, {"name": zh, "jobs": []}))
            continue
        prev = old.get(zh) or {}
        icon, color = prev.get("icon") or meta[0], prev.get("color") or meta[1]
        picked = [j for j in g["jobs"] if isinstance(j, dict)
                  and _clean(j.get("challenge"))][-take:]
        jobs = []
        for j in picked:
            ch = _clean(j.get("challenge"))
            ally = _clean(j.get("ally"))
            cname = challenge_name(ch)
            if not cname:
                log.warning("bounty_challenge_name_missing tag=%s challenge=%s",
                            tag, ch[:40])
            mission = cname
            if ally and cname:
                # 词库 Challenge_*_Desc 的首行模板即「|ALLY|的赏金任务」。
                mission = "%s·%s的赏金" % (cname, ally)
            jobs.append({
                "lvl": None, "name": _clean(j.get("node")),
                "type": _clean(j.get("mission")),
                "mission": mission, "target": ch, "objective": ch,
                "icon": icon, "color": color,
            })
        out.append({"name": zh, "icon": icon, "color": color, "jobs": jobs})
    return out


def _patch_deep(deep: dict) -> dict:
    """移除硬编码的深矿轮换名 / 最终任务（无可验证数据源，绝不编造）。"""
    d = dict(deep or {})
    d.pop("title", None)
    d["tag"] = "[钢铁之路]"
    d["detail"] = "等级 130-140 · 5阶段"
    return d


# -------------------------------------------------------------- 入口 ----
def enrich(data: dict | None, html: str | None = None,
           official: dict | None = None, payload: dict | None = None) -> dict:
    """返回补齐后的赏金卡数据。

    ``payload`` 是显式测试夹具入口（调用方传入即只读夹具，不联网）；
    生产路径不传，读 ``warframe`` 已抓好的两个进程缓存。无可用 RSC 时
    **显式告警并原样返回**，绝不编造奖励内容。
    """
    out = copy.deepcopy(data or {})
    if payload is None:
        if html is None or official is None:
            try:
                W = _wf()
            except Exception as e:  # noqa: BLE001 — 显式告警，不静默
                log.warning("bounty_live_warframe_import_failed err=%s:%s",
                            type(e).__name__, e)
                return out
            if html is None:
                html = (getattr(W, "_wfwiki_cache", {}) or {}).get("html")
            if official is None:
                official = (getattr(W, "_official_cache", {}) or {}).get("data")
        if not html:
            log.warning("bounty_live_no_html — 保留原数据，不编造奖励")
            return out
        try:
            payload = parse_payload(html)
        except Exception as e:  # noqa: BLE001 — RSC 解码失败必须响，不能静默出旧卡
            log.warning("bounty_live_decode_failed err=%s:%s",
                        type(e).__name__, e)
            return out
    if not (payload.get("bountyBoard") or payload.get("groups")):
        log.warning("bounty_live_no_payload — 保留原数据，不编造奖励")
        return out
    try:
        _patch_worlds(out.get("worlds") or [], payload, official)
        out["factions"] = _patch_factions(out.get("factions") or [], payload)
    except Exception as e:  # noqa: BLE001 — 补齐失败必须响，不能静默出旧卡
        log.warning("bounty_live_patch_failed err=%s:%s", type(e).__name__, e)
        return copy.deepcopy(data or {})
    if out.get("deep"):
        out["deep"] = _patch_deep(out["deep"])
    # rotation 的 rot/vault 奖励由 _bounty_card_data 从解析成功的 bounties
    # 直接生成且已带真实图标，这里绝不覆盖，避免把 image 打成空串。
    return out
