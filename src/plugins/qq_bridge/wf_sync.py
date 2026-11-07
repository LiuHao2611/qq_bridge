# -*- coding: utf-8 -*-
"""每日与官方同步 Warframe 静态数据（武器 / 战甲 / 赋能 + 紫卡倾向倍率）。

为什么需要：本地 ``data/warframe_items/*.json`` 与 ``data/wf_riven_weapons.json``
是静态快照。DE 出新武器 / 改倾向后，必须刷新它们，新武器才能出现在 ``#wf 倾向``
卡片里、图标才能拉到、倾向倍率才准。

数据源（按可达性 + 权威性排序，任何一环失败都优雅降级，绝不破坏现有数据）：
  1. **DE 官方 Public Export**（``content.warframe.com/PublicExport/``）——紫卡词条
     数值与武器倾向的**唯一权威源**，与游戏客户端同源：
       ``index_{en,zh}.txt.lzma``（官方清单，LZMA1）
         → 解压得 ``ExportUpgrades_{lang}.json!<hash>`` / ``ExportWeapons_{lang}.json!<hash>``
         → 拉 ``Manifest/<entry>`` 得官方导出原文
         → 裁剪成 ``riven-public-export.json``（只留 ``/Lotus/Upgrades/Mods/Randomized/``
           下的紫卡定义 + ``omegaAttenuation>0`` 的武器 + 中文名）
         → 调 ``gen_riven_data.generate()`` 重新派生 ``riven_disp.json`` /
           ``riven_stat_base.json`` / ``riven_stat_zh.json`` 三张表。
     官方清单只给「文件名!版本哈希」，不带 `.lzma` 的 EOS 标记，故解压走
     ``FORMAT_RAW`` + 头部声明长度（见 ``_unlzma_public_export``）。
  2. npm 包 ``warframe-items``（WFCD 官方物品库的编译产物，含全部武器/战甲/赋能）
     → 拉 tarball，解包 ``package/data/json/{Primary,Secondary,Melee,Warframes,
     Mods,Arcanes}.json`` 覆盖本地 ``data/warframe_items/*.json``。
     首选国内可达镜像 ``registry.npmmirror.com``，回退 ``registry.npmjs.org`` /
     ``registry.yarnpkg.com``。其中 ``Primary/Secondary/Melee.json`` 的 ``type``
     字段是区分霰弹枪 / Kitgun / Zaw 的必备元数据（派生紫卡表时用）。

历史：旧源的 ``api.warframestat.us/pc/riven/weapons`` 已于 2026 年实测 404 失效，
且其 schema 与官方导出不同口径（会导致区间整体偏移），已彻底移除。

设计要点：
  * 原子替换：新内容先写 ``.tmp``，JSON 校验通过后才 ``os.replace``；旧文件备份为 ``.bak``。
  * 失败静默降级：某源取不到就保留本地旧文件，不影响任何功能。
  * 同步后：失效 ``warframe.py`` 的静态数据内存缓存 + 触发 ``weapon_icons`` 预拉取
    新武器图标（仅当确有文件更新时）。
  * 频率：记录上次成功同步时间，24h 内不重复（除非 force）；每日循环 + 启动钩子双保险。
  * 环境开关：``WF_SYNC_DISABLE=1`` 完全跳过；``WF_ICON_DISABLE=1`` 只跳过图标预拉取。

命令行（在你自己的机器上跑，直接看同步结果）：
    .venv\\Scripts\\python.exe src\\plugins\\qq_bridge\\wf_sync.py --now
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import io
import json
import lzma
import os
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# src/plugins/qq_bridge/wf_sync.py -> parents[3] = 项目根（qq_bridge/）
# ⚠️ 2026-11-02 修正：原写 parents[4]，实际指向 manju_agent/（多跳一层），
# 导致同步把数据写到 D:\AI\manju_agent\data\warframe_items（另一个目录），
# 插件真正读的 qq_bridge/data/warframe_items 从未被更新（Primary.json 停在 9-16）。
# 同目录层的 game_tools/*.py 都在更深一层，故它们用 parents[4] 是对的。
_ROOT = Path(__file__).resolve().parents[3]
_ITEMS_DIR = _ROOT / "data" / "warframe_items"
_DATA_DIR = _ROOT / "data"
_STATE = _ITEMS_DIR / "_sync_state.json"

#: 要从 npm 包里解包并覆盖本地的 WFCD 物品文件（武器 + 战甲 + 赋能）。
#: ``SentinelWeapons.json``：DE 的 Rifle 紫卡 compatibleItems 收录了哨兵步枪
#: （如冰凇 / Verglas），缺它会导致这类卡 DPS 面板整块「面板数据缺失」。
_ITEM_FILES = ("Primary.json", "Secondary.json", "Melee.json",
               "SentinelWeapons.json", "Warframes.json", "Mods.json", "Arcanes.json")
_ITEM_MEMBER_TMPL = "package/data/json/{name}"  # tarball 内相对路径

_NPM_MIRRORS = ("https://registry.npmmirror.com/",
                "https://registry.npmjs.org/",
                "https://registry.yarnpkg.com/")
_NPM_PKG = "warframe-items"

#: 紫卡数据权威源：DE 官方 Public Export（与游戏客户端同源）。
_DE_BASE = "https://content.warframe.com/PublicExport/"
#: 官方只把该前缀下的 Mod 当作可洗紫卡词条定义。
_RIVEN_ROOT = "/Lotus/Upgrades/Mods/Randomized/"
#: 裁剪后的官方紫卡快照（gen_riven_data 的唯一输入）。
_RIVEN_EXPORT = "riven-public-export.json"
#: 快照内保留的武器字段（与 koishi makeSnapshot 一致，其余字段不参与计算）。
_RIVEN_WEAPON_FIELDS = ("uniqueName", "name", "productCategory",
                        "omegaAttenuation", "primeOmegaAttenuation", "slot")
_RIVEN_LANGS = ("en", "zh")
_RIVEN_KINDS = ("ExportUpgrades", "ExportWeapons")
#: 只比较内容差异（忽略 downloadedAt / sources），避免每天无意义改写文件。
_RIVEN_CONTENT_KEYS = ("upgrades", "upgradesZh", "weapons", "weaponNamesZh",
                       "weaponTypes")
#: 官方精简导出**不含**武器↔紫卡类别的兼容关系（compatibleItems），该元数据只在
#: ``warframe-public-export-plus`` 里；缺了它就无法区分 Shotgun/Rifle、Kitgun/Zaw，
#: 也无法把 Zaw 握柄、Amp 棱镜、同伴抗原这些「组件」排除在紫卡武器之外。
_EXPORT_PLUS_PKG = "warframe-public-export-plus"
_EXPORT_PLUS_MEMBER = "package/ExportUpgrades.json"
#: 紫卡定义后缀 → 本项目的 7 类武器名（与 gen_riven_data/_RIVEN_DEFINITION_TYPES 一致）。
_RIVEN_TYPE_BY_SUFFIX = {
    "LotusRifleRandomModRare": "Rifle",
    "LotusShotgunRandomModRare": "Shotgun",
    "LotusPistolRandomModRare": "Pistol",
    "LotusArchgunRandomModRare": "Archgun",
    "PlayerMeleeWeaponRandomModRare": "Melee",
    "LotusModularPistolRandomModRare": "Kitgun",
    "LotusModularMeleeRandomModRare": "Zaw",
}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_SYNC_INTERVAL = 24 * 3600  # 每日一次
_TARGBALL_TIMEOUT_FACTOR = 4  # tarball 比单文件大，给更长时间

_STATE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _urllib_get(url: str, timeout: float) -> bytes | None:
    """GET 一个 URL，返回字节或 None（任何失败都静默返回 None）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            if getattr(r, "status", 200) == 200:
                return r.read()
        return None
    except urllib.error.HTTPError:
        return None
    except Exception:  # noqa: BLE001 — 连接/超时等，交给下一个镜像/源
        return None


async def _fetch_bytes(url: str, timeout: float) -> bytes | None:
    return await asyncio.to_thread(_urllib_get, url, timeout)


def _read_state() -> dict:
    try:
        d = json.loads(_STATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_state_unlocked(d: dict) -> None:
    """写状态文件的**无锁**核心；调用方负责持有 ``_STATE_LOCK``。

    ⚠️ 2026-09-30 事故：``sync_warframe_data`` 曾在 `with _STATE_LOCK:` 内直接调
    ``_write_state``（其内部又取同一把**不可重入**的 Lock）→ 同线程二次获取永久死锁，
    且发生在事件循环线程里，导致整个 bridge 冻结（CPU 0、HTTP 不响应、消息不处理）。
    故拆分：锁只在 ``_write_state``（对外入口）与显式 ``with _STATE_LOCK`` 块中存在。
    """
    try:
        _ITEMS_DIR.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _write_state(d: dict) -> None:
    """写状态文件（自带锁，供外部独立调用）。"""
    with _STATE_LOCK:
        _write_state_unlocked(d)


# ---------------------------------------------------------------------------
# 原子替换（带备份）
# ---------------------------------------------------------------------------
def _replace_json(local: Path, new_bytes: bytes) -> bool:
    """校验 JSON 并原子替换本地文件；内容未变返回 False（不写、不备份）。

    成功返回 True。任何异常（解析失败/磁盘）返回 False，保留旧文件。
    """
    try:
        json.loads(new_bytes.decode("utf-8"))
    except Exception:  # noqa: BLE001 — 不是合法 JSON → 绝不覆盖
        return False
    try:
        if local.exists() and local.read_bytes() == new_bytes:
            return False  # 内容一致，无需更新
        if local.exists():  # 备份旧文件
            local.with_suffix(local.suffix + ".bak").write_bytes(local.read_bytes())
        tmp = local.with_suffix(local.suffix + ".tmp")
        tmp.write_bytes(new_bytes)
        os.replace(tmp, local)
        return True
    except OSError:
        return False
    finally:
        try:
            local.with_suffix(local.suffix + ".tmp").unlink()
        except OSError:
            pass


def _extract_members(tar_bytes: bytes, names: set[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes))
        for m in tf.getmembers():
            if m.name in names:
                f = tf.extractfile(m)
                if f:
                    out[m.name] = f.read()
    except Exception:  # noqa: BLE001
        return out
    return out


def _unlzma_public_export(blob: bytes) -> bytes:
    """解压 DE PublicExport 的 ``*.txt.lzma``。

    ⚠️ 官方索引是 **LZMA1 alone 头部（1 字节 props + 4 字节 dict + 8 字节长度）
    但不写 EOS 结束标记** 的流。Python 的 ``FORMAT_ALONE`` 解码器要求 EOS，
    遇到该文件直接抛 ``LZMAError: Corrupt input data``；必须按头部参数自己
    组 ``FORMAT_RAW`` 过滤器，并用头部声明的解压长度做 ``max_length``。

    实测（2026-11）：``index_en.txt.lzma`` 496B → 798B，props=0x5d（lc3/lp0/pb2），
    dict=16MiB，usize=798。
    """
    if len(blob) < 13:
        raise ValueError("官方索引长度不足 13 字节，疑似下载被截断")
    props = blob[0]
    dict_size = int.from_bytes(blob[1:5], "little")
    usize = int.from_bytes(blob[5:13], "little")
    if not dict_size or not usize:
        raise ValueError(f"官方索引头部非法：dict={dict_size} usize={usize}")
    lc = props % 9
    rest = props // 9
    lp, pb = rest % 5, rest // 5
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                "lc": lc, "lp": lp, "pb": pb}]
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    out = dec.decompress(blob[13:], max_length=usize)
    if len(out) != usize:
        raise ValueError(f"官方索引解压不完整：{len(out)}/{usize} 字节")
    return out


def _manifest_entry(index_text: str, kind: str, lang: str) -> str | None:
    """从官方清单里取唯一的 ``<kind>_<lang>.json!<hash>`` 条目。"""
    prefix = f"{kind}_{lang}.json!"
    hits = [ln.strip() for ln in index_text.replace("\r", "").split("\n")
            if ln.strip().startswith(prefix)]
    return hits[0] if len(hits) == 1 else None


def _riven_definitions(doc: dict) -> list[dict]:
    """筛出官方导出里所有紫卡词条定义（只保留 uniqueName + upgradeEntries）。"""
    out: list[dict] = []
    for d in doc.get("ExportUpgrades") or []:
        un = d.get("uniqueName") or ""
        entries = d.get("upgradeEntries") or []
        if un.startswith(_RIVEN_ROOT) and entries:
            out.append({"uniqueName": un, "upgradeEntries": entries})
    return out


def _riven_weapon_types(upgrades: dict) -> dict[str, str]:
    """由 ``warframe-public-export-plus`` 的 ExportUpgrades 建 武器uniqueName→紫卡类别。

    该文件是 ``{路径: {uniqueName, compatibleItems: [...], ...}}``，7 个
    ``*RandomModRare`` 定义了「哪把武器能洗哪类紫卡」——这是官方精简导出里
    唯一缺失的权威关系。实测（v0.6.12）覆盖 Rifle 104 / Shotgun 21 /
    Pistol 93 / Archgun 15 / Melee 167 / Kitgun 6 / Zaw 11。
    """
    out: dict[str, str] = {}
    for key, value in (upgrades or {}).items():
        if not isinstance(value, dict):
            continue
        un = value.get("uniqueName") or key
        if not isinstance(un, str):
            continue
        rtype = _RIVEN_TYPE_BY_SUFFIX.get(un.rsplit("/", 1)[-1])
        if not rtype:
            continue
        for item in value.get("compatibleItems") or []:
            if isinstance(item, str) and item:
                out[item] = rtype
    return out


def _extract_export_plus_types(tar_bytes: bytes) -> dict[str, str]:
    """从 warframe-public-export-plus tarball 里抽 ExportUpgrades 并建类别表。"""
    extracted = _extract_members(tar_bytes, {_EXPORT_PLUS_MEMBER})
    raw = extracted.get(_EXPORT_PLUS_MEMBER)
    if not raw:
        raise ValueError("包内缺少 ExportUpgrades.json")
    return _riven_weapon_types(json.loads(raw.decode("utf-8")))


def _build_riven_snapshot(docs: dict[str, dict], sources: dict[str, dict],
                          weapon_types: dict[str, str]) -> dict:
    """按 koishi ``makeSnapshot`` 的同一规则裁剪并校验官方导出。

    校验逐条对齐（数值必须有限、定义与武器都不能为空），任何一条不满足直接
    抛异常 → 上层保留旧快照，绝不写入半成品。
    注意：DE 2026-10-22 热修 44.1.3 起紫卡倾向全武器统一 1.2，下面对
    omegaAttenuation 的范围检查只用于**历史快照完整性**，不再代表现行规则，
    现行值以 warframe.RIVEN_DISPOSITION 为唯一来源。
    """
    upgrades = _riven_definitions(docs["ExportUpgrades_en"])
    upgrades_zh = _riven_definitions(docs["ExportUpgrades_zh"])
    weapons: list[dict] = []
    for w in docs["ExportWeapons_en"].get("ExportWeapons") or []:
        om = w.get("omegaAttenuation")
        if not isinstance(om, (int, float)) or om <= 0:
            continue  # 倾向 0/缺失 = 不可洗紫卡（任务物品、Archwing 等）
        weapons.append({k: w[k] for k in _RIVEN_WEAPON_FIELDS if k in w})
    if not upgrades or not weapons:
        raise ValueError("官方导出缺少紫卡定义或武器（导出结构可能已变更）")
    for d in upgrades:
        for e in d["upgradeEntries"]:
            if not e.get("tag") or not e.get("upgradeValues"):
                raise ValueError(f"紫卡定义条目非法：{d['uniqueName']}")
            for v in e["upgradeValues"]:
                if not isinstance(v.get("value"), (int, float)):
                    raise ValueError(f"紫卡词条数值非法：{d['uniqueName']}")
    for w in weapons:
        for key in ("omegaAttenuation", "primeOmegaAttenuation"):
            if key not in w:
                continue
            v = w[key]
            # 44.1.3 后官方值恒为 1.2；这里仅拦截明显损坏的历史字段
            if not isinstance(v, (int, float)) or not (0 < v <= 1.550001):
                raise ValueError(f"历史倾向字段异常：{w.get('uniqueName')} {key}={v}")
    names_zh = {w.get("uniqueName"): w.get("name")
                for w in docs["ExportWeapons_zh"].get("ExportWeapons") or []}
    return {
        "schemaVersion": 2,
        "downloadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": sources,
        "upgrades": upgrades,
        "upgradesZh": upgrades_zh,
        "weapons": weapons,
        "weaponNamesZh": names_zh,
        # schemaVersion 2 新增：武器 → 紫卡类别（来自 warframe-public-export-plus
        # 的 compatibleItems）。为空表示该源不可达，派生时回退 npm type + productCategory。
        "weaponTypes": weapon_types,
    }


def _riven_payload_changed(local: Path, snapshot: dict) -> bool:
    """只比较内容（忽略 downloadedAt / sources），无实质变化则不重写文件。"""
    try:
        old = json.loads(local.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 不存在/损坏都算「需要写」
        return True
    return any(old.get(k) != snapshot.get(k) for k in _RIVEN_CONTENT_KEYS)


def _write_riven_mult_cache(snapshot: dict) -> int:
    """由官方快照派生 ``data/wf_riven_weapons.json``（``warframe._riven_mults`` 的口径）。

    该文件原先由 warframestat.us 直接提供（现已 404），格式为
    ``[{disposition, i18n:{en:{name}}, slug}, ...]``。这里用官方
    ``omegaAttenuation`` 按同一 schema 重建，``#wf 倾向`` 卡片才不会停在旧值。
    返回写入的武器数。
    """
    rows = []
    for w in snapshot.get("weapons") or []:
        name = (w.get("name") or "").strip()
        om = w.get("omegaAttenuation")
        if not name or not isinstance(om, (int, float)):
            continue
        rows.append({"disposition": float(om),
                     "i18n": {"en": {"name": name}},
                     "slug": name.lower().replace(" ", "-")})
    if not rows:
        return 0
    rows.sort(key=lambda r: r["i18n"]["en"]["name"])
    payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    _replace_json(_DATA_DIR / "wf_riven_weapons.json", payload)
    return len(rows)


def _refresh_wiki_modcards(timeout: float = 1800.0) -> str:
    """每周一次：本地离线合成 WFCD 风格 mod 卡（替代已失效的 wiki 真卡抓取）。

    背景：原 ``tools/fetch_wiki_mod_cards.py`` 用 Playwright 过 Cloudflare 抓 wiki
    真卡，现已被墙失效。改为本地合成（``tools/gen_mod_cards.py``）——卡框/极性/字体
    是仓库 vendored 的 WFCD genesis-assets（零外网），真实 DE 立绘优先走
    **DE 官方 Public Export contentHash CDN**（``content.warframe.com``，容器内可达、
    mod 更新即同步），browse.wf 仅作宿主兜底。

    用独立子进程：合成 1733 张 + 立绘拉取较重，子进程跑完即退，崩了也带不走消息循环。
    增量模式（--missing）：只补本地缺失的卡图，新 mod 才外呼 DE 官方 CDN。
    ``WF_WIKI_MODCARDS_DISABLE=1`` 可整段关闭。
    """
    import subprocess
    import sys as _sys
    tool = _ROOT / "tools" / "gen_mod_cards.py"
    if not tool.is_file():
        return "skip:no-tool"
    try:
        r = subprocess.run([_sys.executable, str(tool), "--prefetch-art", "--missing"],
                           cwd=str(_ROOT), capture_output=True, text=True,
                           timeout=timeout)
        tail = (r.stdout or "").strip().splitlines()[-2:]
        return "ok:" + " | ".join(tail) if r.returncode == 0 else f"err:{r.returncode}"
    except Exception as e:  # noqa: BLE001
        return f"fail:{type(e).__name__}"


def _refresh_mod_frames() -> int:
    """校验 mod 卡图所需的 vendored 卡框/极性/字体素材是否齐备（仓库已内置，无需下载）。

    背景：mod 卡图不是现成图片——DE 不发布成品卡，社区（WFCD/genesis-assets）只给
    卡框模板，成品要本地合成（见 ``tools/gen_mod_cards.py``）。卡框素材已随镜像
    COPY 进容器（src/plugins/qq_bridge/game_tools/modcard/assets/），此处只做存在性
    清点，失败绝不影响数据同步。
    """
    try:
        from pathlib import Path as _P
        assets = _ROOT / "src" / "plugins" / "qq_bridge" / "game_tools" / "modcard" / "assets"
        frames = assets / "modFrames"
        pol = assets / "polarities"
        font = assets / "msyh.ttc"
        n = 0
        if frames.is_dir():
            n += len(list(frames.glob("*.png")))
        if pol.is_dir():
            n += len(list(pol.glob("*.png")))
        if font.is_file():
            n += 1
        if n == 0:
            logger.warning("wf_mod_frames_missing: vendored assets not found at %s", assets)
        return n
    except Exception as e:  # noqa: BLE001 — 素材校验失败绝不影响数据同步
        logger.warning(f"wf_mod_frames_refresh_failed: {type(e).__name__}: {e}")
        return 0


def _regen_riven_tables(export_path: Path) -> dict:
    """由快照重新派生 riven_disp / riven_stat_base / riven_stat_zh 三张表。"""
    from .game_tools.gen_riven_data import generate
    return generate(export_path)


# ---------------------------------------------------------------------------
# 各数据源同步
# ---------------------------------------------------------------------------
async def _sync_item_files(timeout: float) -> tuple[int, str | None]:
    """从 npm 包拉 tarball 并解包覆盖本地 WFCD 物品 JSON。返回 (更新数, 版本号)。"""
    # 1) 解析 tarball 地址（依次试镜像）
    tarball_url: str | None = None
    version: str | None = None
    for mirror in _NPM_MIRRORS:
        data = await _fetch_bytes(mirror + _NPM_PKG + "/latest", timeout)
        if not data:
            continue
        try:
            meta = json.loads(data)
            tarball_url = (meta.get("dist") or {}).get("tarball")
            version = meta.get("version")
            if tarball_url:
                break
        except Exception:  # noqa: BLE001
            continue
    if not tarball_url:
        return 0, version
    # 2) 下载 tarball（更大，给足超时）
    tb = await _fetch_bytes(tarball_url, timeout * _TARGBALL_TIMEOUT_FACTOR)
    if not tb:
        return 0, version
    # 3) 解包需要的成员并原子替换（重活放线程，绝不阻塞事件循环）
    member_to_name = {_ITEM_MEMBER_TMPL.format(name=n): n for n in _ITEM_FILES}
    extracted = await asyncio.to_thread(_extract_members, tb, set(member_to_name))
    updated = 0
    for member, name in member_to_name.items():
        nb = extracted.get(member)
        if not nb:
            continue
        if await asyncio.to_thread(_replace_json, _ITEMS_DIR / name, nb):
            updated += 1
    return updated, version


async def _sync_riven_export(timeout: float) -> tuple[bool, str]:
    """同步 DE 官方紫卡快照并重派生本地表。返回 (是否有实质更新, 说明)。

    流程：清单 → 四个官方导出 → 裁剪校验 → 原子替换快照 → 重派生三张表。
    任一步失败返回 ``(False, 原因)`` 并**保留旧数据**（绝不半覆盖）。
    表每次都重派生（快照无变化时是幂等重写），保证「表 ⊆ 快照」始终成立，
    也让首次迁移 / 表被误删后能自愈。
    """
    docs: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for lang in _RIVEN_LANGS:
        name = f"index_{lang}"
        url = f"{_DE_BASE}{name}.txt.lzma"
        blob = await _fetch_bytes(url, timeout)
        if not blob:
            return False, f"{name} 下载失败"
        try:
            text = (await asyncio.to_thread(_unlzma_public_export, blob)).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            return False, f"{name} 解压失败：{type(exc).__name__} {exc}"
        sources[name] = {"url": url, "sha256": hashlib.sha256(blob).hexdigest(),
                         "bytes": len(blob)}
        for kind in _RIVEN_KINDS:
            entry = _manifest_entry(text, kind, lang)
            if not entry:
                return False, f"{name} 缺少或重复 {kind} 清单条目"
            man_url = f"{_DE_BASE}Manifest/{entry}"
            raw = await _fetch_bytes(man_url, timeout * 2)
            if not raw:
                return False, f"{kind}_{lang} 下载失败"
            try:
                docs[f"{kind}_{lang}"] = json.loads(raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                return False, f"{kind}_{lang} 解析失败：{type(exc).__name__}"
            sources[f"{kind}_{lang}"] = {"url": man_url,
                                         "sha256": hashlib.sha256(raw).hexdigest(),
                                         "bytes": len(raw)}
    # compatibleItems（武器↔紫卡类别）只在 warframe-public-export-plus 里，属可选增强：
    # 取不到就沿用旧快照里的类别表（绝不清空），仍可退回 npm type + productCategory。
    local = _ITEMS_DIR / _RIVEN_EXPORT
    weapon_types: dict[str, str] = {}
    tb_url: str | None = None
    for mirror in _NPM_MIRRORS:
        meta_raw = await _fetch_bytes(mirror + _EXPORT_PLUS_PKG + "/latest", timeout)
        if not meta_raw:
            continue
        try:
            tb_url = (json.loads(meta_raw).get("dist") or {}).get("tarball")
        except Exception:  # noqa: BLE001
            tb_url = None
        if tb_url:
            break
    if tb_url:
        tb = await _fetch_bytes(tb_url, timeout * _TARGBALL_TIMEOUT_FACTOR * 3)
        if tb:
            sources["export_plus"] = {"url": tb_url,
                                      "sha256": hashlib.sha256(tb).hexdigest(),
                                      "bytes": len(tb)}
            try:
                weapon_types = await asyncio.to_thread(_extract_export_plus_types, tb)
            except Exception:  # noqa: BLE001
                weapon_types = {}
    if not weapon_types:
        try:
            weapon_types = json.loads(local.read_text(encoding="utf-8")).get("weaponTypes") or {}
        except Exception:  # noqa: BLE001
            weapon_types = {}
    if weapon_types:
        sources.setdefault("export_plus", {})["types"] = len(weapon_types)

    try:
        snapshot = await asyncio.to_thread(_build_riven_snapshot, docs, sources, weapon_types)
    except Exception as exc:  # noqa: BLE001
        return False, f"快照校验失败：{exc}"
    changed = await asyncio.to_thread(_riven_payload_changed, local, snapshot)
    if changed:
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
        if not await asyncio.to_thread(_replace_json, local, payload):
            return False, "快照落盘失败"
    try:
        diag = await asyncio.to_thread(_regen_riven_tables, local)
    except Exception as exc:  # noqa: BLE001
        return changed, f"表派生失败：{type(exc).__name__} {exc}"
    mult = await asyncio.to_thread(_write_riven_mult_cache, snapshot)
    return changed, (f"快照{'更新' if changed else '无变化'}，"
                     f"武器{diag.get('weapons_total')} 词条{diag.get('stat_keys_total')} "
                     f"倾向{mult}")


# ---------------------------------------------------------------------------
# 缓存失效 + 图标预拉取
# ---------------------------------------------------------------------------
def _invalidate_caches() -> None:
    """数据更新后失效 warframe.py 的静态数据内存缓存（下次查询重新从磁盘加载）。

    ⚠️ 旧代码写的是 ``from ..game_tools.warframe``——本模块以 ``qq_bridge.wf_sync``
    导入，``..game_tools`` 指向不存在的 ``plugins.game_tools``，异常被 except 静默
    吞掉，导致「同步了但缓存不失效」。这里按正确相对路径优先，并保留旧路径兜底。
    """
    for mod in (".game_tools.warframe", "..game_tools.warframe"):
        try:
            module = importlib.import_module(mod, __package__)
            module.reset_warframe_caches()
            return
        except Exception:  # noqa: BLE001
            continue


async def _prefetch_icons(timeout: float) -> tuple[int, int]:
    try:
        from ..pic.weapon_icons import prefetch_known_icons
        return await prefetch_known_icons(timeout=timeout)
    except Exception:  # noqa: BLE001
        return 0, 0


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
async def sync_warframe_data(force: bool = False, timeout: float = 15.0) -> dict:
    """执行一次同步（武器/战甲/赋能 + 紫卡倾向）。

    返回摘要 dict。``WF_SYNC_DISABLE=1`` 时整体跳过；24h 内且非 force 时跳过。
    """
    if (os.environ.get("WF_SYNC_DISABLE") or "").strip() in ("1", "true", "True"):
        return {"ok": False, "skipped": "disabled"}
    st = _read_state()
    last = float(st.get("last", 0) or 0)
    if not force and (time.time() - last) < _SYNC_INTERVAL:
        return {"ok": False, "skipped": "recent", "last": last}

    riven, riven_note = await _sync_riven_export(timeout)
    items, version = await _sync_item_files(timeout)
    frames = 0
    wiki_note = ""
    _off = (os.environ.get("WF_MODCARDS_DISABLE") or "").strip() in ("1", "true", "True")
    if not _off:
        frames = await asyncio.to_thread(_refresh_mod_frames)
        # 真卡每周刷一次（首次没有时间戳就当需要）
        _st = _read_state()
        if time.time() - float(_st.get("last_wiki_modcards", 0) or 0) > 7 * 86400:
            if (os.environ.get("WF_WIKI_MODCARDS_DISABLE") or "").strip() not in ("1", "true", "True"):
                wiki_note = await asyncio.to_thread(_refresh_wiki_modcards)
                with _STATE_LOCK:
                    _s2 = _read_state()
                    _s2["last_wiki_modcards"] = time.time()
                    _s2["last_wiki_modcards_note"] = wiki_note
                    _write_state_unlocked(_s2)
    changed = items > 0 or riven

    icons = (0, 0)
    if changed:
        _invalidate_caches()
        if (os.environ.get("WF_ICON_DISABLE") or "").strip() not in ("1", "true", "True"):
            icons = await _prefetch_icons(timeout)

    with _STATE_LOCK:
        ns = _read_state()
        ns["last"] = time.time()
        if version:
            ns["version"] = version
        ns["last_items_updated"] = items
        ns["last_riven_updated"] = bool(riven)
        ns["last_riven_note"] = riven_note
        ns["last_icons"] = {"ok": icons[0], "fail": icons[1]}
        _write_state_unlocked(ns)  # 已持锁，必须用无锁版，否则自锁死（见其 docstring）

    return {"ok": changed, "items_updated": items, "riven_updated": bool(riven),
            "riven_note": riven_note, "version": version, "mod_frames": frames,
            "wiki_modcards": wiki_note,
            "icons": {"ok": icons[0], "fail": icons[1]}}


# ---------------------------------------------------------------------------
# 每日循环 + 启动/关闭钩子
# ---------------------------------------------------------------------------
_daily_task: "asyncio.Task | None" = None


async def _daily_loop() -> None:
    """每 24h 跑一次同步；单次失败只记日志，绝不退出循环。

    ⚠️ 加 `asyncio.wait_for` 硬超时：即便某源彻底无响应、或代码再出阻塞 bug，
    也只拖住「这一次同步」，绝不永久占死事件循环（2026-09-30 冻结事故的兜底）。
    """
    while True:
        try:
            await asyncio.wait_for(sync_warframe_data(force=False), timeout=600.0)
        except asyncio.TimeoutError:
            pass
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(_SYNC_INTERVAL)


def start_sync_scheduler() -> None:
    """拉起每日同步循环（幂等：已在跑则不重复创建）。"""
    global _daily_task
    if _daily_task is not None and not _daily_task.done():
        return
    try:
        _daily_task = asyncio.get_running_loop().create_task(_daily_loop())
    except RuntimeError:
        pass


def stop_sync_scheduler() -> None:
    """取消每日同步循环。"""
    global _daily_task
    if _daily_task is not None:
        _daily_task.cancel()
        _daily_task = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main() -> int:
    import sys
    # 以脚本方式直跑时 __package__ 为空，模块内的相对导入（.game_tools.*）会失败。
    # 这里补一个最小 qq_bridge 包壳，让 `python wf_sync.py --now` 与插件内运行等价。
    if not __package__:
        import types
        pkg_name = "qq_bridge"
        # ⚠️ 直跑时 sys.path[0] = 本文件所在目录（qq_bridge/），其中的 queue/ 包会
        # **遮蔽标准库 queue**，导致 concurrent.futures / asyncio.to_thread 全崩
        # （ModuleNotFoundError: httpx，因为 queue/__init__.py 自己 import 了 httpx）。
        # 包壳已通过 __path__ 提供子模块查找，故把脚本目录移出 sys.path 最安全。
        here = str(Path(__file__).resolve().parent)
        sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
        if pkg_name not in sys.modules:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = [here]
            pkg.__package__ = pkg_name
            sys.modules[pkg_name] = pkg
        globals()["__package__"] = pkg_name
    if "--now" in sys.argv:
        res = asyncio.run(sync_warframe_data(force=True, timeout=30.0))
        print("同步结果：", json.dumps(res, ensure_ascii=False))
        return 0
    print(__doc__)
    print("用法：--now 立即同步一次")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
