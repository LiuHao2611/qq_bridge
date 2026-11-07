# -*- coding: utf-8 -*-
"""配卡自动抓取（warframe.today）——让「配卡 <任意武器>」不再依赖手工白名单。

## 为什么需要这个模块

`builds.json` 原本是**手工维护的白名单**，只有被人工收录过的武器能出图，其余
一律回落文本「已收录本地配卡：…」。用户要查第三把武器就得再加一条，等于让
用户拿几百把武器逐个试——不可接受。

## 数据从哪来（只搬运，不编造）

warframe.today 的武器页（`https://warframe.today/weapons/<slug>/`）固定给出
**8 张社区主流 MOD**，形如：

    <div class="wf-fp-popmod pm-card" aria-label="Hornet Strike, rank 10 of 10">

`aria-label` 里同时带**卡名与等级**，这正是渲染横版卡需要的两个字段。

## 为什么敢用（三道校验，任一不过就放弃）

1. **卡名必须命中本地 `Mods.json`**（官方权威数据）——命中不了说明页面给了
   本地不认识的卡（自定义/已改版），整条记录放弃，绝不硬凑一张半真半假的卡；
2. **等级不得超过 `fusionLimit`**——防止页面把满级卡写成超上限；
3. **中文名/极性/耗能一律取本地**（`mod_full.json` 官方译名 +
   `Mods.json` 的 `baseDrain + rank`），与手写条目同源，不从网页抄。

校验通过后写回 `data/warframe_items/builds.json` 做缓存：下次秒回，且来源可审计
（`source.site / source.url`）。

## 失败怎么办

网络失败、页面 404（warframe.today 没收录该武器，如 Stug）、解析不足 6 张卡 →
返回 `None`，由调用方回落**文本配卡建议**。绝不静默、绝不编造。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import urllib.parse

from ..config import get_logger
from . import warframe as wf

logger = get_logger()

#: 数据源（页面固定 8 张热门卡；`/weapon/<slug>` 会 301 到 `/weapons/<slug>/`）
SITE = "warframe.today"
BASE = "https://warframe.today/weapons/%s/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

#: 卡片标签：`aria-label="Hornet Strike, rank 10 of 10"`
_CARD_RE = re.compile(r'aria-label="([^"]+?), rank (\d+) of (\d+)"')
_ENTITIES = (("&#039;", "'"), ("&#39;", "'"), ("&quot;", '"'),
             ("&amp;", "&"), ("&nbsp;", " "), ("&#8217;", "'"))

#: 少于这个张数视为「数据不足」，宁可回落文本
_MIN_MODS = 6

#: 有 Exilus 武器槽的类别（主/副/近战都是 8 主槽 + 1 Exilus = 9 格）
#: 注意：warframe.today 的 `wf-fp-popmods` **只列 8 张主槽卡**，Exilus 那张
#: 一般写在正文里（"Lethal Momentum in the Exilus slot"），多数页干脆不写。
_EXILUS_CATS = ("主武器", "副武器", "近战武器")

#: 正文里点名 Exilus 卡的两种写法
_EXILUS_PATTERNS = (
    re.compile(r"([A-Z][A-Za-z'’\-]*(?: [A-Z][A-Za-z'’\-]*){0,3})\s+in the Exilus slot"),
    re.compile(r"Exilus slot[^.]{0,60}?\b([A-Z][A-Za-z'’\-]*(?: [A-Z][A-Za-z'’\-]*){0,3})\b"),
)

#: 极性英文 → 卡面符号（与 build_card._draw_polarity 同一套）
#: 注意 `mod_full.json` 里存的**已经是符号**（"V"/"D"/"-"），而 `Mods.json`
#: 存的是全名（"madurai"…），两种都要认，否则极性会全落空（踩过：
#: 摘要里写成「极性 通用」，而卡面右上角却有极性符号，自相矛盾）。
_POL = {"madurai": "V", "naramon": "-", "vazarin": "D", "umbra": "U", "any": ""}
_POL_SYMBOLS = ("V", "D", "U", "-")


def _pol_symbol(v: object) -> str:
    """极性字段（符号或全名）→ 卡面符号；未知返回空串。"""
    s = str(v or "").strip()
    if s in _POL_SYMBOLS:
        return s
    if len(s) == 1 and s.upper() in ("V", "D", "U"):
        return s.upper()
    return _POL.get(s.lower(), "")

#: 单进程内防重复抓取（同一把武器被连发两次时不打两次网络）
_INFLIGHT: set[str] = set()
_ICON_INFLIGHT: set[str] = set()

#: builds.json 读-改-写锁（同一进程内多线程写盘会互相覆盖）
_LIB_LOCK = threading.Lock()

#: Mods.json + mod_full.json 合成索引（懒加载，进程级）
_MODIDX: dict | None = None


# ---------------------------------------------------------------------------
# 本地权威数据索引
# ---------------------------------------------------------------------------
def _mod_index() -> dict:
    """`{卡名小写: {en, zh, max, base, polarity}}`，来自 Mods.json + mod_full.json。"""
    global _MODIDX
    if _MODIDX is not None:
        return _MODIDX
    data_dir = wf._DATA_DIR
    idx: dict = {}
    try:
        mods = json.loads((data_dir / "Mods.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 数据缺失时下游会整体放弃抓取
        logger.warning(f"wf_buildfetch_mods_load_failed: {type(e).__name__}: {e}")
        _MODIDX = {}
        return _MODIDX
    for m in mods:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        idx[name.lower()] = {
            "en": name,
            "zh": "",
            "max": m.get("fusionLimit"),
            "base": m.get("baseDrain"),
            "polarity": m.get("polarity"),
        }
    # 官方中文名/极性以 mod_full.json 为准（Mods.json 里没有中文）
    try:
        mf = json.loads((data_dir / "mod_full.json").read_text(encoding="utf-8")).get("map") or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_buildfetch_modfull_load_failed: {type(e).__name__}: {e}")
        mf = {}
    for key, rec in idx.items():
        full = mf.get(key) or {}
        if full.get("zh"):
            rec["zh"] = full["zh"]
        if full.get("polarity"):
            rec["polarity"] = full["polarity"]
        if full.get("drain") is not None:
            rec["base"] = full["drain"]
        # ⚠️ mod_full.json 的 `max` 是**属性文本列表**（["+220% 伤害"]），
        # 不是等级上限！等级上限只在 Mods.json 的 fusionLimit 里。
        # 旧写法直接 `rec["max"] = full["max"]` 会让 `isinstance(limit,int)` 恒假，
        # 于是「rank ≤ 上限」这条校验被静默跳过（2026-11-03 实测发现）。
        if isinstance(full.get("max"), int):
            rec["max"] = full["max"]
    _MODIDX = idx
    return idx


def reset_mod_index() -> None:
    """wf_sync 更新 Mods.json 后调用。"""
    global _MODIDX
    _MODIDX = None


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _unescape(s: str) -> str:
    for a, b in _ENTITIES:
        s = s.replace(a, b)
    return s.strip()


def slug_candidates(en: str) -> list[str]:
    """英文武器名 → warframe.today slug 候选（去重保序）。"""
    base = (en or "").strip().lower()
    if not base:
        return []
    out: list[str] = []
    for sep in ("-", "_"):
        s = re.sub(r"[’'`]", "", base).replace(" ", sep)
        s = re.sub(r"[^a-z0-9%s]" % re.escape(sep), "", s)
        if s and s not in out:
            out.append(s)
    return [urllib.parse.quote(s) for s in out]


def parse_cards(html: str) -> list[tuple[str, int, int]]:
    """从页面里取「卡名, 等级/上限」列表（去重，保持页面顺序）。"""
    out: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for name, rank, mx in _CARD_RE.findall(html or ""):
        nm = _unescape(name)
        key = nm.lower()
        if not nm or key in seen:
            continue
        seen.add(key)
        try:
            out.append((nm, int(rank), int(mx)))
        except ValueError:
            continue
    return out


def build_mods(rows: list[tuple[str, int, int]]) -> list[dict] | None:
    """把抓到的卡逐条对齐本地权威数据；**任何一条对不上就整体放弃**。"""
    idx = _mod_index()
    if not idx:
        return None
    mods: list[dict] = []
    for name, rank, mx in rows:
        rec = idx.get(name.lower())
        if not rec:
            logger.warning(f"wf_buildfetch_unknown_mod name={name!r}")
            return None
        limit = rec.get("max")
        if isinstance(limit, int) and rank > limit:
            logger.warning(f"wf_buildfetch_rank_over name={name!r} rank={rank} max={limit}")
            return None
        base = rec.get("base") or 0
        try:
            drain = int(base) + int(rank)
        except (TypeError, ValueError):
            drain = int(rank)
        pol = _pol_symbol(rec.get("polarity"))
        mods.append({
            "name": rec.get("zh") or rec.get("en") or name,
            "en": (rec.get("en") or name).lower(),
            "rank": rank,
            "max": limit if isinstance(limit, int) else mx,
            "polarity": pol,
            "note": "耗能 %d" % drain,
            "slot": "main",
            "_drain": drain,
        })
    if len(mods) < _MIN_MODS:
        logger.warning(f"wf_buildfetch_too_few got={len(mods)} need>={_MIN_MODS}")
        return None
    return mods


def extract_exilus(html: str) -> dict | None:
    """从正文里找 Exilus 槽那张卡（找不到返回 None，**绝不默认塞一张**）。

    只有同时满足「正文点了名」+「本地 Mods.json 能命中」才认，否则当没有。
    """
    idx = _mod_index()
    if not idx or not html:
        return None
    for pat in _EXILUS_PATTERNS:
        for m in pat.finditer(html):
            name = _unescape(m.group(1))
            rec = idx.get(name.lower())
            if not rec:
                continue
            limit = rec.get("max")
            try:
                rank = int(limit) if isinstance(limit, int) else 0
            except (TypeError, ValueError):
                rank = 0
            drain = int(rec.get("base") or 0) + rank
            logger.info(f"wf_buildfetch_exilus name={rec.get('en')} rank={rank} drain={drain}")
            return {
                "name": rec.get("zh") or rec.get("en") or name,
                "en": (rec.get("en") or name).lower(),
                "rank": rank,
                "max": limit if isinstance(limit, int) else rank,
                "polarity": _pol_symbol(rec.get("polarity")),
                "note": "耗能 %d · Exilus" % drain,
                "slot": "exilus",
                "_drain": drain,
            }
    return None


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
async def _fetch_html(client, url: str, retries: int = 2) -> str | None:
    """GET 页面；404 直接返回 None（该武器无页面），网络抖动退避重试。"""
    for attempt in range(retries + 1):
        try:
            r = await client.get(url, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                logger.info(f"wf_buildfetch_404 url={url}")
                return None
        except Exception as e:  # noqa: BLE001 — 网络问题重试，最后放弃
            logger.info(f"wf_buildfetch_retry url={url} attempt={attempt} err={type(e).__name__}")
        if attempt < retries:
            await asyncio.sleep(1.0 * (attempt + 1))
    return None


async def _icon_rel(en: str, image_name: str | None) -> str | None:
    """武器图落到 `data/warframe_items/weapons/`，返回相对 builds.json 的路径。

    复用 `pic.weapon_icons`（多镜像 + 负缓存）；取不到就返回 None，
    卡片会留空框，不影响出图。
    """
    if not image_name:
        return None
    try:
        from ..pic import weapon_icons as wi
    except Exception:  # noqa: BLE001
        return None
    try:
        got = wi.icon_cache_path(image_name)
        if got is None:
            if image_name in _ICON_INFLIGHT:
                return None
            _ICON_INFLIGHT.add(image_name)
            try:
                got_name = await asyncio.wait_for(wi.ensure_icon(image_name, en), timeout=10.0)
                got = wi.icon_cache_path(got_name or image_name)
            finally:
                _ICON_INFLIGHT.discard(image_name)
        if got is None:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]", "", en.replace(" ", "")) or "weapon"
        rel = "weapons/%s.png" % safe
        dst = wf._DATA_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(got.read_bytes())
        return rel
    except Exception as e:  # noqa: BLE001 — 图只是装饰
        logger.warning(f"wf_buildfetch_icon_failed en={en}: {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# 武器名解析（支持「野马 腐蚀」这类带标签的写法）
# ---------------------------------------------------------------------------
def resolve_weapon_prefix(word: str, max_tokens: int = 4) -> dict | None:
    """按前缀最长优先解析武器，自动剥离后面的标签词。"""
    toks = [t for t in re.split(r"\s+", (word or "").strip()) if t]
    if not toks:
        return None
    for n in range(min(max_tokens, len(toks)), 0, -1):
        info = wf._resolve_weapon(" ".join(toks[:n]))
        if info:
            return info
    return None


# ---------------------------------------------------------------------------
# 写缓存
# ---------------------------------------------------------------------------
def _append_to_lib(key: str, entry: dict) -> None:
    """把条目合并进 builds.json（原子替换，避免写一半把库写坏）。"""
    fp = wf._DATA_DIR / "builds.json"
    with _LIB_LOCK:
        try:
            lib = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 库缺失/损坏时重建，不让抓取结果丢
            lib = {"weapons": {}}
        weapons = lib.setdefault("weapons", {})
        weapons[key] = entry
        tmp = fp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(lib, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, fp)


def _make_entry(info: dict, url: str, mods: list[dict], icon: str | None) -> dict:
    en = info.get("en") or ""
    zh = wf._weapon_zh(en) or en
    cat = info.get("cat") or ""
    tier = (wf._CAT_SHORT or {}).get(cat, cat)
    real = [m for m in mods if not m.get("placeholder")]
    exi = [m for m in mods if m.get("slot") == "exilus"]
    exi_filled = bool(exi) and not exi[0].get("placeholder")
    total = sum(m.pop("_drain", 0) for m in mods)
    pol_cnt = {"V": 0, "-": 0, "D": 0, "U": 0}
    for m in real:
        if m.get("polarity") in pol_cnt:
            pol_cnt[m["polarity"]] += 1
    pol_txt = " · ".join("%s×%d" % (k, v) for k, v in pol_cnt.items() if v) or "通用"
    if not exi:
        exi_txt = ""
    elif exi_filled:
        exi_txt = " + Exilus"
    else:
        exi_txt = "（Exilus 槽该来源未给出）"
    aliases = [en.lower()]
    if zh and zh.lower() not in aliases:
        aliases.append(zh)
    return {
        "zh": zh,
        "en": en,
        "aliases": aliases,
        "icon": icon,
        "sets": [{
            "index": 1,
            "tag": "社区主流",
            "tier": tier,
            "version": "",
            "status": "自动抓取",
            "author": SITE,
            "link": url,
            "source": {
                "site": SITE,
                "url": url,
                "note": "%d 主槽已逐条对照本地官方数据校验" % len(real),
            },
            "mods": mods,
            "arcane": {},
            "summary": ("来自 %s 的社区主流配卡：%d 主槽%s，满级合计耗能 %d，极性 %s。"
                        "MOD 名称 / 耗能均按本地官方数据核验，未做主观增删。"
                        % (SITE, len(real), exi_txt, total, pol_txt)),
            "howto": "",
        }],
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
async def ensure_build(word: str) -> str | None:
    """确保该武器在 builds.json 里有条目；命中/抓取成功返回 key，否则 None。

    命中缓存时**不发任何网络请求**（只 stat 一次 builds.json）。
    """
    info = resolve_weapon_prefix(word)
    if not info:
        return None
    en = info.get("en") or ""
    key = en.lower()
    if not key:
        return None
    if key in ((wf._load_build_lib().get("weapons")) or {}):
        return key
    if key in _INFLIGHT:
        return None
    _INFLIGHT.add(key)
    try:
        try:
            import httpx
        except ImportError:
            logger.warning("wf_buildfetch_no_httpx")
            return None
        async with httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=8.0), follow_redirects=True) as client:
            html = None
            used_url = ""
            for slug in slug_candidates(en):
                url = BASE % slug
                html = await _fetch_html(client, url)
                if html and _CARD_RE.search(html):
                    used_url = url
                    break
                html = None
            if not html:
                logger.info(f"wf_buildfetch_no_page en={en}")
                return None
            rows = parse_cards(html)
            mods = build_mods(rows)
            if not mods:
                logger.warning(f"wf_buildfetch_validate_failed en={en} rows={len(rows)}")
                return None
            # 第 9 格（Exilus）：正文点名且本地能命中就填真卡；否则画明确的空槽，
            # 让卡片保持「8 主槽 + 1 Exilus」的真实格数，同时不编造卡名。
            if info.get("cat") in _EXILUS_CATS:
                exi = extract_exilus(html)
                if exi:
                    mods.append(exi)
                else:
                    mods.append({
                        "name": "Exilus 槽", "en": "", "rank": 0, "max": 0,
                        "polarity": "", "note": "该来源未给出",
                        "slot": "exilus", "placeholder": True,
                    })
            icon = await _icon_rel(en, info.get("imageName"))
        entry = _make_entry(info, used_url, mods, icon)
        _append_to_lib(key, entry)
        logger.info(f"wf_buildfetch_ok en={en} zh={entry['zh']} mods={len(mods)} url={used_url}")
        return key
    except Exception as e:  # noqa: BLE001 — 抓取失败一律回落文本
        logger.warning(f"wf_buildfetch_failed en={en}: {type(e).__name__}: {e}")
        return None
    finally:
        _INFLIGHT.discard(key)
