# -*- coding: utf-8 -*-
"""Warframe 官方武器图标缓存（真实 DE 图鉴图，替代早期几何兜底图）。

图源按可用性依次回退（2026-11-04 本机实测）：
  1. **DE 官方 Public Export contentHash CDN（全量、权威、首选）**
       `https://content.warframe.com/PublicExport/<textureLocation>`
       与游戏客户端同源。经 `index_en.txt.lzma` → `Manifest/ExportManifest.json`
       取得 `{uniqueName: textureLocation}` 映射（20154 条，武器类 1290 条）。
       实测本地 609 把武器（Primary 184 / Secondary 144 / Melee 257 /
       SentinelWeapons 24）**609/609 全部命中**，包括 WFCD 仓库漏收的
       Acceltra / Akarius 这类「无后缀本体」。本机沙箱实测 index + manifest + 图
       三者皆 200。这是「几百把武器全都有图」的真相所在。
  2. https://cdn.warframestat.us/img/<imageName>
       warframestat.us 官方 CDN。文件名就是本地 `data/warframe_items/*.json`
       的 `imageName`（kebab + 内容哈希，如 `glaive-prime-170ccb6e9a.png`）。
       **但国内多数网络被 Cloudflare 风控 403**，实测本机 403，仅作兜底。
  3. {镜像}https://raw.githubusercontent.com/WFCD/warframe-items/master/data/img/<CamelCase>.png
       WFCD 社区镜像里的**官方 DE 资产**。镜像前缀默认
       `https://ghfast.top/` 与 `https://gh-proxy.com/`（国内可直连，
       2026-09-30 沙箱实测 200 / image/png）。文件名是 CamelCase：
       英文名去掉空格与符号；「&」在仓库里写法不统一（`AckAndBrunt.png` 带 And，
       `CobraCranePrime.png` 不带），故两个变体都试。
       实测覆盖 389/576（67.5%）——缺的几乎都是「无后缀本体」
       （仓库只收了 Prime / 皮肤版，如无 `Acceltra.png` 但有 `AcceltraPrime.png`）。
  4. 同 3 的 development 分支。

设计要点：
  * 渲染线程（guide.py 的 `@disp`）只做**同步缓存命中**检查（`icon_cache_path`），绝不外呼。
  * 文本生成（warframe.riven_disposition_text）在异步上下文里 `await ensure_icon` 下载落盘；
    首拉成功后续渲染直接命中缓存，零外呼。
  * **任何**网络/解析/落盘失败都静默返回 None，由 guide.py 回落几何图标，
    卡片永远能出图，不因图源抖动而崩。
  * 命中的图源前缀会记到 `<缓存目录>/_source.txt`，下次**优先**用它，省掉反复试错。

命令行自检（在你自己的机器上跑，能直接看出哪个图源通）：
    .venv\\Scripts\\python.exe src\\plugins\\qq_bridge\\pic\\weapon_icons.py --probe
    .venv\\Scripts\\python.exe src\\plugins\\qq_bridge\\pic\\weapon_icons.py --prefetch
"""
from __future__ import annotations

from PIL import Image, ImageDraw

import asyncio
import json
import lzma
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# src/plugins/qq_bridge/pic/weapon_icons.py -> parents[4] = 项目根
_CACHE_DIR = Path(__file__).resolve().parents[4] / "data" / "warframe_icons"
_SOURCE_HINT = _CACHE_DIR / "_source.txt"
_NEG_CACHE = _CACHE_DIR / "_absent.json"

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_MIN_BYTES = 600  # 低于此大小的“图标”视为下载残片/错误页，丢弃
#: 「取不到」的负缓存 TTL（秒）。全源都失败要跑满两三个外呼（约 4s），
# 而本地 576 把武器里有约 1/3（无后缀本体）仓库没收图 —— 不记下来的话
# 每次查这些武器都要白等 4s。
# 分两档，**关键**：只有「全源都明确回 404」才是与网络无关的确定性缺失（图确实不存在），
# 可以长记；若失败里含 403/超时/连接失败，那是**当时网络环境**的问题（比如某地
# GFW 挡了 CDN），换个网络就好了 —— 只能短记，否则会把「这台机器暂时连不上」
# 误当成「这张图不存在」长期钉死，换网络后也拿不到图。
_NEG_TTL = 24 * 3600      # 确定性缺失（全源 404）
_NEG_TTL_BLOCKED = 600    # 含网络性失败 → 10 分钟后再试
#: 负缓存读-改-写锁：prefetch 并发（asyncio.gather）下，多个任务同时
# _neg_mark 会互相覆盖彼此的条目，导致「取不到」的武器只记下一两条。
# 加锁保证并发写入不丢条目，用户机器上对「无后缀本体」类武器能正确短记，
# 避免每次查询都白跑满两三个外呼（约 4s）。
_NEG_LOCK = threading.Lock()

#: WFCD 仓库里武器图的固定目录（master / development 两个分支都试）
_REPO_PATH = "/WFCD/warframe-items/{branch}/data/img/{name}.png"
_REPO_BRANCHES = ("master", "development")
#: raw.githubusercontent.com 的国内可达镜像前缀（按实测可用性排序）
_MIRRORS = ("https://ghfast.top/", "https://gh-proxy.com/")
#: Warframe 官方图 CDN（warframestat）。注意正确路径是 `/img/<imageName>`（带 /img/ 前缀），
#: 少写这级会导致 404。该 CDN 是本机网络（西安电信）被 Cloudflare 风控 403，但能通
#: CDN 的机器上它是**全量源**（覆盖全部武器），故保留作兜底。
_CDN = "https://cdn.warframestat.us/img/"

#: **DE 官方 contentHash CDN —— 真正的全量源**。``content.warframe.com/PublicExport/``
#: 是 DE 与游戏客户端同源的公开导出，含**每一把**武器的 store 图标（含 Acceltra 这类
#: WFCD 仓库没收的本体武器）。``textureLocation`` 形如
#: ``Lotus/Interface/Icons/StoreIcons/Weapons/.../<Name>.png!<hash>``，URL 即
#: ``PublicExport/<textureLocation>``。本项目 ``tools/gen_mod_cards.py`` 已用它取 mod 立绘
#: （注：「容器内 200 可达」）。**本沙箱实测 index + manifest 均 200**，故它是首要图源。
_DE_BASE = "https://content.warframe.com/PublicExport/"
_DE_MANIFEST_CACHE = _CACHE_DIR / "_de_weapon_map.json"  # {uniqueName: textureLocation(去/前缀)}
#: 内存缓存（避免每次渲染都读盘）
_DE_MAP: dict | None = None          # uniqueName -> textureLocation
_DE_NAME2UN: dict | None = None      # 武器名(lower) -> uniqueName（来自本地 warframe_items）
_DE_BASE_IDX: dict | None = None     # 路径末段归一化名 -> uniqueName（本地库未收录时的兜底匹配）
#: manifest 只拉一次：无论成功失败，同一进程内不再重复下载（失败时靠 10 分钟负缓存重试）
_DE_LOCK = threading.Lock()
_DE_TRIED = False
#: 本地武器库索引惰性构建：(imageName -> uniqueName, name.lower() -> uniqueName)
_LOCAL_IDX: tuple[dict, dict] | None = None

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _mirrors() -> tuple[str, ...]:
    """镜像前缀，可用 .env `WF_ICON_MIRRORS` 覆盖（逗号分隔）。"""
    raw = (os.environ.get("WF_ICON_MIRRORS") or "").strip()
    if raw:
        return tuple(m.strip().rstrip("/") + "/" for m in raw.split(",") if m.strip())
    return _MIRRORS


def camel_variants(en_name: str) -> list[str]:
    """英文武器名 → WFCD 仓库里的 CamelCase 文件名候选（去重、保持顺序）。"""
    if not en_name:
        return []
    out: list[str] = []
    for cand in (re.sub(r"[^A-Za-z0-9]", "", en_name),                      # Cobra & Crane -> CobraCrane
                 re.sub(r"[^A-Za-z0-9]", "", en_name.replace("&", "And"))):  # Ack & Brunt   -> AckAndBrunt
        if cand and cand not in out:
            out.append(cand)
    return out


def _unlzma_public_export(blob: bytes) -> bytes:
    """解压 DE PublicExport 的 ``*.txt.lzma``（LZMA1 alone 头 + 无 EOS 标记）。

    与 ``wf_sync._unlzma_public_export`` / ``tools/gen_mod_cards.py`` 同款实现：
    头 13 字节 = 1 字节 props + 4 字节 dict_size + 8 字节 usize，必须是 FORMAT_RAW。
    """
    if len(blob) < 13:
        raise ValueError("官方索引长度不足 13 字节，疑似下载被截断")
    props = blob[0]
    dict_size = int.from_bytes(blob[1:5], "little")
    usize = int.from_bytes(blob[5:13], "little")
    if not dict_size or not usize:
        raise ValueError("官方索引头部非法：dict=%d usize=%d" % (dict_size, usize))
    lc = props % 9
    rest = props // 9
    lp, pb = rest % 5, rest // 5
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
        {"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}])
    out = dec.decompress(blob[13:], max_length=usize)
    if len(out) != usize:
        raise ValueError("官方索引解压不完整：%d/%d 字节" % (len(out), usize))
    return out


def _de_manifest(allow_download: bool = True) -> dict:
    """``{uniqueName: textureLocation}``（全量 20154 条，含所有武器 store 图标）。

    三级取数：内存 → 本地缓存 ``_de_weapon_map.json`` → DE 官方 Public Export。
    下载路径：``index_en.txt.lzma`` → ``Manifest/ExportManifest.json!<hash>``。
    任何失败都返回 ``{}``（调用方回落到 CDN / 镜像），绝不抛。
    """
    global _DE_MAP, _DE_TRIED
    if _DE_MAP is not None:
        return _DE_MAP
    with _DE_LOCK:
        if _DE_MAP is not None:  # 双检：并发首个请求只解一次盘
            return _DE_MAP
        try:
            data = json.loads(_DE_MANIFEST_CACHE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                _DE_MAP = data
                return data
        except Exception:  # noqa: BLE001 — 缓存缺失/损坏 → 重新拉
            pass
        if not allow_download or _DE_TRIED:
            return {}
        _DE_TRIED = True
        data = {}
        try:
            idx_raw, _ = _urllib_get(_DE_BASE + "index_en.txt.lzma", 30.0)
            if not idx_raw:
                raise RuntimeError("index_en.txt.lzma 取不到")
            idx_txt = _unlzma_public_export(idx_raw).decode("utf-8")
            entry = next((ln.strip() for ln in idx_txt.splitlines()
                          if ln.strip().startswith("ExportManifest.json!")), None)
            if not entry:
                raise RuntimeError("index 里找不到 ExportManifest.json")
            man_raw, _ = _urllib_get(_DE_BASE + "Manifest/" + entry, 90.0)
            if not man_raw:
                raise RuntimeError("ExportManifest.json 取不到")
            for e in json.loads(man_raw)["Manifest"]:
                un, tl = e.get("uniqueName"), e.get("textureLocation")
                if un and tl:
                    data[un] = tl.lstrip("/")
            if data:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _DE_MANIFEST_CACHE.write_text(json.dumps(data, ensure_ascii=False),
                                              encoding="utf-8")
        except Exception:  # noqa: BLE001 — 网络/格式问题一律降级，不拖垮渲染
            data = {}
        _DE_MAP = data
        return data


def _de_texture(unique_name: str | None) -> str | None:
    """uniqueName → textureLocation（只读已加载/已落盘的映射，不联网）。"""
    if not unique_name:
        return None
    return _de_manifest(allow_download=False).get(unique_name)


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _local_weapon_index() -> tuple[dict, dict]:
    """本地武器索引：``(imageName → uniqueName, name.lower() → uniqueName)``。

    两个来源合并：
      * ``riven-public-export.json`` 的 ``weapons``（**841 把**，DE 官方紫卡武器全表，
        含 Archgun/空战枪这类不在四件套里的武器，如葛拉特勒 ``ArchGRNAAGun``）；
      * ``data/warframe_items`` 下的 Primary / Secondary / Melee / SentinelWeapons
        （609 把，带 ``imageName``，用于 imageName → uniqueName 的精确反查）。
    这是把「渲染层拿到的英文名/图名」翻译成「DE manifest 的 uniqueName」的桥。
    """
    global _LOCAL_IDX
    if _LOCAL_IDX is not None:
        return _LOCAL_IDX
    by_img: dict[str, str] = {}
    by_name: dict[str, str] = {}
    base = _CACHE_DIR.parent / "warframe_items"
    # 1) 紫卡武器全表（841 把，权威 uniqueName）
    try:
        riven = json.loads((base / "riven-public-export.json").read_text(encoding="utf-8"))
        for it in riven.get("weapons") or []:
            un, nm = it.get("uniqueName"), it.get("name")
            if un and nm:
                by_name.setdefault(nm.strip().lower(), un)
    except Exception:  # noqa: BLE001
        pass
    # 2) 四件套（带 imageName，可精确反查）
    for fname in ("Primary.json", "Secondary.json", "Melee.json", "SentinelWeapons.json"):
        try:
            items = json.loads((base / fname).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for it in items:
            un, img, nm = it.get("uniqueName"), it.get("imageName"), it.get("name")
            if not un:
                continue
            if img:
                by_img.setdefault(img, un)
            if nm:
                by_name.setdefault(nm.strip().lower(), un)
    _LOCAL_IDX = (by_img, by_name)
    return _LOCAL_IDX


def _de_base_index() -> dict:
    """``路径末段归一化名 → uniqueName``，用于本地库未收录武器的保守匹配。

    注意：manifest 尚未加载时**不写入缓存**，否则会把空索引钉死在进程里，
    导致之后 manifest 到位了也匹配不上。
    """
    global _DE_BASE_IDX
    if _DE_BASE_IDX is not None:
        return _DE_BASE_IDX
    idx: dict[str, str] = {}
    for un in _de_manifest(allow_download=False):
        base = _norm_name(un.rsplit("/", 1)[-1])
        if base:
            idx.setdefault(base, un)
    if idx:
        _DE_BASE_IDX = idx
    return idx


def weapon_unique_name(image_name: str | None, en_name: str | None = None) -> str | None:
    """把武器（图名/英文名）翻译成 DE ``uniqueName``，取不到返回 None。

    优先本地武器库（609 把，实测 100% 有 DE 图标）；库外新武器退化为
    「manifest 路径末段归一化名 == 英文名 或 英文名+Weapon」的保守匹配。
    """
    by_img, by_name = _local_weapon_index()
    if image_name and image_name in by_img:
        return by_img[image_name]
    if en_name:
        un = by_name.get(en_name.strip().lower())
        if un:
            return un
        key = _norm_name(en_name)
        if key:
            idx = _de_base_index()
            return idx.get(key) or idx.get(key + "weapon")
    return None


def de_icon_url(unique_name: str | None) -> str | None:
    """uniqueName → DE 官方图标完整 URL（映射未加载时返回 None，不联网）。"""
    tl = _de_texture(unique_name)
    return (_DE_BASE + tl) if tl else None


def source_urls(image_name: str | None, en_name: str | None = None,
                unique_name: str | None = None) -> list[str]:
    """给出该武器所有候选下载地址。

    顺序：**DE 官方全量 CDN → warframestat CDN → WFCD 仓库镜像**。
    DE 官方 ``content.warframe.com/PublicExport/`` 与游戏客户端同源，本地 609 把武器
    实测 609/609 命中（含 WFCD 漏收的 Acceltra 等无后缀本体），是真正的全量源；
    warframestat CDN 在全量上等价但本机被 Cloudflare 403；WFCD 仓库镜像只覆盖约 2/3，
    仅作最后兜底。命中前缀记到 ``_source.txt``，下次同机优先重排。
    """
    de_urls: list[str] = []
    cdn_urls: list[str] = []
    mirror_urls: list[str] = []
    de = de_icon_url(unique_name)
    if de:
        de_urls.append(de)
    if image_name:
        cdn_urls.append(_CDN + urllib.parse.quote(image_name, safe=""))
    for camel in camel_variants(en_name or ""):
        for branch in _REPO_BRANCHES:
            raw = "https://raw.githubusercontent.com" + _REPO_PATH.format(branch=branch, name=camel)
            for m in _mirrors():
                mirror_urls.append(m + raw)
    urls = de_urls + cdn_urls + mirror_urls
    # 命中的图源前缀优先重排（省掉反复试错）；DE 全量源始终压在最前
    hint = _read_hint()
    if hint:
        head = [u for u in urls if u.startswith(hint)]
        if head:
            urls = head + [u for u in urls if not u.startswith(hint)]
    if de_urls:
        urls = ([u for u in urls if u.startswith(_DE_BASE)]
                + [u for u in urls if not u.startswith(_DE_BASE)])
    return urls


def _read_hint() -> str:
    try:
        return _SOURCE_HINT.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_hint(url: str) -> None:
    """记住这次成功的图源前缀（如 `https://ghfast.top/`），下次优先。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _SOURCE_HINT.write_text(url, encoding="utf-8")
    except OSError:
        pass


def _neg_load() -> dict:
    """读负缓存 `{image_name: [失败时间戳, 该条 TTL]}`。"""
    try:
        d = json.loads(_NEG_CACHE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — 负缓存坏了就当没有，重试即可
        return {}


def _neg_live(v: object, now: float) -> bool:
    if not (isinstance(v, list) and len(v) == 2):
        return False
    ts, ttl = v
    if not (isinstance(ts, (int, float)) and isinstance(ttl, (int, float))):
        return False
    return now - ts < ttl


def _neg_fresh(image_name: str) -> bool:
    return _neg_live(_neg_load().get(image_name), time.time())


def _neg_mark(image_name: str, ttl: float = _NEG_TTL) -> None:
    now = time.time()
    with _NEG_LOCK:
        d = {k: v for k, v in _neg_load().items() if _neg_live(v, now)}
        d[image_name] = [now, ttl]
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _NEG_CACHE.write_text(json.dumps(d), encoding="utf-8")
        except OSError:
            pass


def _cache_name(image_name: str | None, en_name: str | None = None) -> str | None:
    """统一的缓存文件名：优先用官方 ``imageName``；没有则退化为「英文名 CamelCase + .png」。

    这一层专门解决两类历史坑：
      * ``ensure_icon(image_name=None, en_name=...)`` 之前会因缓存文件名是 ``None``
        而在 ``_CACHE_DIR / None`` 处抛 TypeError（被调用方吞掉 → 永远取不到图）；
      * 本地 ``warframe_items`` 武器库没收的武器（如 Gratlter）即便英文名合法、
        WFCD 仓库有图，也因拿不到 ``imageName`` 而直接放弃。
    现在两者都能正确落盘与命中缓存，图标可用性不再被本地库覆盖度绑架。
    """
    if image_name:
        return image_name
    cv = camel_variants(en_name or "")
    return (cv[0] + ".png") if cv else None


def icon_cache_path(image_name: str | None, en_name: str | None = None) -> Path | None:
    """同步：仅当图标已缓存（文件存在且像样）时返回路径，否则 None。

    供渲染线程使用——**绝不**发起网络请求。``image_name`` 缺省时按 ``en_name`` 推导。
    """
    name = _cache_name(image_name, en_name)
    if not name:
        return None
    p = _CACHE_DIR / name
    try:
        if p.is_file() and p.stat().st_size >= _MIN_BYTES:
            return p
    except OSError:
        pass
    return None


def _urllib_get(url: str, timeout: float) -> tuple[bytes | None, bool]:
    """GET 一个 URL，返回 (字节, 是否 404)。404 是「该路径确定不存在」，与网络故障区分开。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "image/*,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 白名单固定域名
            if getattr(r, "status", 200) == 200:
                return r.read(), False
        return None, False
    except urllib.error.HTTPError as e:
        return None, getattr(e, "code", None) == 404
    except Exception:  # noqa: BLE001 — 连接/超时等，交给下一个镜像
        return None, False


async def _fetch(client, url: str, timeout: float) -> tuple[bytes | None, bool]:
    """取一个 URL 的字节；单源失败返回 (None, 是否404)（不抛）。"""
    try:
        if client is not None:
            r = await client.get(url, headers={"User-Agent": _UA, "Accept": "image/*,*/*;q=0.8"})
            if r.status_code == 200 and r.content:
                return r.content, False
            return None, r.status_code == 404
        return await asyncio.to_thread(_urllib_get, url, timeout)
    except Exception as e:  # noqa: BLE001 — 单源失败就试下一个
        return None, "404" in str(e)


def _path_key(url: str) -> str:
    """去掉镜像前缀后的「同内容路径」。

    多个镜像指向同一个仓库版本，同一路径在 A 镜像 404、在 B 镜像也必然 404——
    据此跳过重复请求，失败用例的请求数直接减半。
    """
    for m in _mirrors():
        if url.startswith(m):
            return url[len(m):]
    return url


async def ensure_icon(image_name: str | None, en_name: str | None = None,
                      timeout: float = 8.0) -> str | None:
    """异步下载官方武器图标到缓存；成功返回缓存文件名，失败返回 None。

    幂等：已缓存则直接返回，不重复下载。``image_name`` 缺省时按 ``en_name`` 推导缓存名，
    从而支持「本地武器库没收、但 DE/WFCD 有图」的武器。

    取图策略（2026-11-04 起）：**DE 官方全量 CDN 优先**。manifest 若已落盘则直接
    读盘（~30ms）拿到本武器的 ``textureLocation``；若尚未落盘，先走旧的 CDN/镜像，
    仍失败才拉一次 manifest 再补一刀 —— 既不拖慢首个卡片，也不让任何一把武器漏图。
    """
    name = _cache_name(image_name, en_name)
    if not name:
        return None
    if icon_cache_path(image_name, en_name) is not None:
        return name
    if (os.environ.get("WF_ICON_DISABLE") or "").strip() not in ("", "0", "false", "False"):
        return None  # 显式关闭外呼（测试/离线场景）：只吃缓存，不回源
    un = weapon_unique_name(image_name, en_name)
    # manifest 已在盘上 → 只读盘（快），让 DE 图源排在第一位
    if un and _DE_MAP is None and _DE_MANIFEST_CACHE.exists():
        await asyncio.to_thread(_de_manifest, False)
    de_url = de_icon_url(un)
    # 有 DE 全量图源时不理会历史负缓存（旧版本只试过镜像，记的「缺失」已过期）
    if not de_url and _neg_fresh(name):
        return None  # 近期已确认取不到 → 不再白等（删 _absent.json 可强制重试）
    try:
        import httpx
    except ImportError:
        client = None
    else:
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout),
                                   follow_redirects=True)
    try:
        status = [False]  # status[0]=True 表示出现过 403/超时等网络性失败
        data = await _try_sources(client, source_urls(image_name, en_name, un), timeout, status)
        if data is None and _DE_MAP is None:
            # 旧源全灭且 manifest 还没拉过 → 拉一次，用 DE 全量源补最后一刀
            await asyncio.to_thread(_de_manifest, True)
            de_url = de_icon_url(un)
            if de_url:
                data = await _try_sources(client, [de_url], timeout, status)
        if data is None:
            # 全源都没拿到 → 记负缓存（含网络性失败只短记），免得后续每次查询都白等
            _neg_mark(name, _NEG_TTL_BLOCKED if status[0] else _NEG_TTL)
            return None
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (_CACHE_DIR / name).write_bytes(data[1])
        except OSError:
            return None
        _write_hint(data[0])
        return name
    finally:
        if client is not None:
            await client.aclose()


async def _try_sources(client, urls: list[str], timeout: float,
                       blocked: list[bool]) -> tuple[str, bytes] | None:
    """按顺序试一组 URL，返回 (命中前缀, 字节)；全失败返回 None。"""
    absent: set[str] = set()  # 已确定 404 的同内容路径（跳过其余镜像，省一半请求）
    for url in urls:
        key = _path_key(url)
        if key in absent:
            continue
        raw, not_found = await _fetch(client, url, timeout)
        if not_found:
            absent.add(key)
            continue
        if not raw:
            blocked[0] = True  # 403/超时等「网络性」失败，非确定性 404
            continue
        if len(raw) < _MIN_BYTES:
            continue
        if not (raw[:8] == _PNG_SIG or raw[:3] == b"\xff\xd8\xff"):  # PNG 或 JPEG
            continue
        return _prefix_of(url), raw
    return None


def _prefix_of(url: str) -> str:
    """把命中的 URL 归约成「图源前缀」，用于下次优先重排。"""
    if url.startswith(_DE_BASE):
        return _DE_BASE
    if url.startswith(_CDN):
        return _CDN
    u = urllib.parse.urlparse(url)
    for m in _mirrors():
        p = urllib.parse.urlparse(m)
        if u.netloc == p.netloc:
            return m
    return u.scheme + "://" + u.netloc + "/"


# ---------------------------------------------------------------------------
# 批量预取
# ---------------------------------------------------------------------------
def _weapon_pairs() -> list[tuple[str | None, str]]:
    """本地全部「有紫卡」武器的 (imageName|None, 英文名)，去重保序。

    来源合并：
      * ``riven-public-export.json`` 的 weapons —— **841 把**紫卡武器全表，
        覆盖空战枪（葛拉特勒）、Archgun 等四件套之外的类型；
      * 四件套 609 把，带 ``imageName``，能直接命中既有缓存文件名。
    守护武器没有 ``imageName``，按英文名推缓存名即可。
    """
    base = _CACHE_DIR.parent / "warframe_items"
    out: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    # 先四件套（有 imageName，能直接命中既有缓存文件名，避免重复下载）
    for fname in ("Primary.json", "Secondary.json", "Melee.json", "SentinelWeapons.json"):
        try:
            items = json.loads((base / fname).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for it in items:
            img = it.get("imageName")
            en = it.get("name")
            if not en:
                continue
            key = en.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((img, en))
    # 再补紫卡全表里四件套之外的类型（空战枪等）
    try:
        riven = json.loads((base / "riven-public-export.json").read_text(encoding="utf-8"))
        for it in riven.get("weapons") or []:
            nm = it.get("name")
            if not nm:
                continue
            key = nm.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((None, nm))
    except Exception:  # noqa: BLE001
        pass
    return out


async def prefetch_known_icons(timeout: float = 8.0, concurrency: int = 4,
                               limit: int | None = None, clear_absent: bool = False,
                               progress=None) -> tuple[int, int]:
    """批量预拉取本地武器库全部图标，铺满 `data/warframe_icons/`。

    先确保 DE 官方 manifest 就位（一次下载，之后全走官方全量源），再并发拉图。
    ``clear_absent=True`` 会先清空负缓存——旧版本只用过镜像，记的「缺失」对新图源无效。
    返回 (成功数, 失败数)。失败不抛异常——只计数。
    """
    if clear_absent:
        try:
            _NEG_CACHE.unlink()
        except OSError:
            pass
    await asyncio.to_thread(_de_manifest, True)  # 失败也无妨，会退回 CDN/镜像
    pairs = _weapon_pairs()
    if limit:
        pairs = pairs[:limit]
    sem = asyncio.Semaphore(max(1, concurrency))
    counter = {"ok": 0, "fail": 0, "done": 0}

    async def one(img: str | None, en: str) -> None:
        async with sem:
            if icon_cache_path(img, en) is not None:
                counter["ok"] += 1
            elif await ensure_icon(img, en, timeout) is not None:
                counter["ok"] += 1
            else:
                counter["fail"] += 1
            counter["done"] += 1
            if progress and counter["done"] % 20 == 0:
                progress(counter["done"], len(pairs), counter["ok"], counter["fail"])

    await asyncio.gather(*(one(i, e) for i, e in pairs))
    return counter["ok"], counter["fail"]


# ---------------------------------------------------------------------------
# 类别剪影（无官方图标时的兜底「武器图片」）
# ---------------------------------------------------------------------------
def build_silhouette(category: str, w: int, h: int, color: str = "#735787") -> "Image.Image":
    """按武器类别画一张透明背景的武器剪影（无官方图标时的兜底『武器图片』）。

    ``category`` 取 riven 的 type（Rifle/Pistol/Shotgun/Kitgun/Melee/Zaw/Archgun/…）。
    返回 RGBA，尺寸 = (w, h)，剪影居中、留 ~12% 边距。枪械类画侧面枪剪影，
    近战类画剑剪影——保证右上角永远是「一张武器图」，而不是空白或纯文字框。
    """
    try:
        _rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:  # noqa: BLE001 — 颜色解析失败兜底品牌紫
        _rgb = (0x73, 0x57, 0x87)
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0,0, 0))
    d = ImageDraw.Draw(img)
    pad = max(2, int(min(w, h) * 0.12))
    x0, y0, x1, y1 = pad, pad, w - pad, h - pad
    cat = (category or "").strip().lower()
    if cat in ("melee", "zaw"):
        _draw_blade(d, x0, y0, x1, y1, _rgb)
    else:
        _draw_gun(d, x0, y0, x1, y1, _rgb)
    return img


def _draw_gun(d, x0: float, y0: float, x1: float, y1: float, rgb: tuple) -> None:
    """侧面枪械剪影（步枪/手枪/霰弹通吃）：长枪身 + 右侧枪管 + 前侧下垂握把 + 扳机护圈。"""
    W, H = x1 - x0, y1 - y0
    cy = y0 + H * 0.42
    a = rgb + (255,)
    # 枪身（横条）
    d.polygon([(x0 + W * 0.06, cy - H * 0.22), (x1 - W * 0.04, cy - H * 0.22),
               (x1 - W * 0.04, cy + H * 0.02), (x0 + W * 0.06, cy + H * 0.02)], fill=a)
    # 枪管 / 准星（右侧凸起）
    d.rectangle([x1 - W * 0.15, cy - H * 0.34, x1 - W * 0.02, cy - H * 0.20], fill=a)
    # 握把（前侧下垂）
    d.polygon([(x0 + W * 0.34, cy + H * 0.02), (x0 + W * 0.50, cy + H * 0.02),
               (x0 + W * 0.44, y1), (x0 + W * 0.30, y1)], fill=a)
    # 扳机护圈
    tw = max(1, int(H * 0.05))
    d.ellipse([x0 + W * 0.18, cy + H * 0.04, x0 + W * 0.36, cy + H * 0.34],
              outline=a, width=tw)


def _draw_blade(d, x0: float, y0: float, x1: float, y1: float, rgb: tuple) -> None:
    """近战剪影：细长剑身（尖朝上）＋ 横护手 ＋ 握柄 ＋ 柄头，整体明显像一把剑。"""
    W, H = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2.0
    a = rgb + (255,)
    bw = W * 0.05                       # 剑身半宽（细长，避免被看成箭头）
    # 剑身：尖顶 → 略收的剑脊 → 底部，长占上半部
    d.polygon([(cx, y0 + H * 0.03),
               (cx + bw, y0 + H * 0.20),
               (cx + bw, y0 + H * 0.46),
               (cx - bw, y0 + H * 0.46),
               (cx - bw, y0 + H * 0.20)], fill=a)
    # 护手（横杆，明显宽于剑身）
    d.rectangle([cx - W * 0.24, y0 + H * 0.46, cx + W * 0.24, y0 + H * 0.53], fill=a)
    # 握柄（细长竖条）
    d.rectangle([cx - W * 0.045, y0 + H * 0.53, cx + W * 0.045, y1 - H * 0.06], fill=a)
    # 柄头（底部小方块）
    d.rectangle([cx - W * 0.10, y1 - H * 0.08, cx + W * 0.10, y1 - H * 0.02], fill=a)


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------
def _main() -> int:
    import sys

    args = sys.argv[1:]
    if "--prefetch" in args:
        def _p(done, total, ok, fail):
            print("  ... %d/%d  成功 %d 失败 %d" % (done, total, ok, fail), flush=True)
        ok, fail = asyncio.run(prefetch_known_icons(clear_absent=True, progress=_p))
        print("预取完成：成功 %d，失败 %d，缓存目录 %s" % (ok, fail, _CACHE_DIR))
        return 0
    if "--probe" in args:
        sample = [("glaive-prime-170ccb6e9a.png", "Glaive Prime"),
                  ("acceltra-f87797699a.png", "Acceltra"),
                  ("ack-&-brunt-2e80c10c0d.png", "Ack & Brunt")]
        _de_manifest(True)  # 顺带验证 DE manifest 是否可达
        print("DE manifest：%d 条，缓存 %s"
              % (len(_DE_MAP or {}), _DE_MANIFEST_CACHE))
        for img, en in sample:
            print("== %s (%s)" % (en, img))
            un = weapon_unique_name(img, en)
            print("   uniqueName    %s" % (un or "(未匹配)"))
            for u in source_urls(img, en, un):
                data, not_found = _urllib_get(u, 8.0)
                if data and (data[:8] == _PNG_SIG or data[:3] == b"\xff\xd8\xff"):
                    state = "OK  %d bytes" % len(data)
                elif not_found:
                    state = "404 不存在"
                else:
                    state = "FAIL 网络/风控"
                print("   %-14s %s" % (state, u[:110]))
        # 已缓存统计
        cached = len(list(_CACHE_DIR.glob("*.png"))) if _CACHE_DIR.is_dir() else 0
        print("缓存目录：%s（已缓存 %d 张）" % (_CACHE_DIR, cached))
        print("记下的优选图源：%s" % (_read_hint() or "(无)"))
        return 0
    print(__doc__)
    print("用法：--probe 探测图源 / --prefetch 批量拉取全部武器图标")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
