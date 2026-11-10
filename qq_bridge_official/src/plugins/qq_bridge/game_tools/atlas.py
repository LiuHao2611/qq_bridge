# Atlas 成品攻略图（Nwflower/Atlas 系列 = Yunzai 生态最主流的图鉴方案）。
#
# 为什么换这个方案（2026-09-14，用户两次拍桌之后认真调研的结论）：
#   成熟机器人（Yunzai/miao-plugin/haro 等）的角色攻略**不是自己抓文本画的**，
#   而是直接发米游社攻略组（听语惊花 等）维护的**成品攻略图**。
#
# 数据源（gitee 镜像实测可达；github raw 超时、jsDelivr 大文件握手超时）：
#   * 原神  Nwflower/Genshin-Atlas    material for role/{角色名}.png
#   * 星铁  Nwflower/star-rail-atlas  guide for role/{id}.png（映射在 othername.json）
#   * 绝区零 Nwflower/zzz-atlas       角色攻略/{角色名}.png
#   * 鸣潮/异环：该系列暂无库 → 调用方回落 wiki 卡片
#
# 行为（2026-09-14 二次修正）：
#   * **跨游戏探测**：用户说「艾莲」不带游戏名时，依次在 原→星→绝 三库探测
#     URL 是否真实存在（原神里的"艾莲"只是个 NPC，不在角色图库里，自然落到 zzz）；
#     探测结果缓存（名字→游戏），避免每条消息都发探测请求。
#   * gitee **raw** 下载图片可用（UA+Referer）；othername.json 走 raw 会 451，
#     必须走 **gitee API**（base64 content）——别改回 raw。
import base64
import json
import time
from urllib.parse import quote

import httpx
from nonebot.log import logger

from . import data

_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://gitee.com"}
_RAW = "https://gitee.com/Nwflower/{repo}/raw/master/{path}"
_API = "https://gitee.com/api/v5/repos/Nwflower/{repo}/contents/{path}"

#: 名字直命名的库：game → (repo, 目录)
NAME_DIR_REPOS = {
    "ys": ("Genshin-Atlas", "material for role"),
    "zzz": ("zzz-atlas", "角色攻略"),
}
#: 跨游戏探测顺序
_ORDER = ["ys", "sr", "zzz"]
_SR_REPO = "star-rail-atlas"

_sr_map: dict[str, str] = {}
_sr_ts = 0.0
_SR_TTL = 3600.0
#: 探测结果缓存：名字 → 命中的 game（24h；探测要发网络请求，能省则省）
_hit_cache: dict[str, str] = {}
_hit_ts: dict[str, float] = {}
_HIT_TTL = 86400.0


def _raw_url(repo: str, path: str) -> str:
    return _RAW.format(repo=repo, path=quote(path))


async def _api_json(repo: str, path: str):
    url = _API.format(repo=repo, path=quote(path))
    async with httpx.AsyncClient(headers=_HDR, timeout=15.0, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        d = r.json()
    return json.loads(base64.b64decode(d.get("content") or "").decode("utf-8", "ignore"))


async def _sr_id_map() -> dict[str, str]:
    """星铁 名字/别名 → 角色图 id（懒加载，1h 缓存）。"""
    global _sr_map, _sr_ts
    if _sr_map and time.time() - _sr_ts < _SR_TTL:
        return _sr_map
    try:
        obj = await _api_json(_SR_REPO, "othername.json")
        m: dict[str, str] = {}
        for cid, names in (obj.get("role") or {}).items():
            for n in names or []:
                m[str(n).strip()] = str(cid)
        if m:
            _sr_map = m
            _sr_ts = time.time()
    except Exception as e:  # noqa: BLE001 — 旁路数据源，失败回落 wiki
        logger.warning(f"atlas_sr_map_failed: {type(e).__name__}: {e}")
    return _sr_map


async def _exists(url: str) -> bool:
    """URL 是否真实存在（HEAD 不行就用 Range GET；gitee 均支持）。"""
    try:
        async with httpx.AsyncClient(headers=_HDR, timeout=8.0, follow_redirects=True) as c:
            r = await c.head(url)
            if r.status_code == 200:
                return True
            r = await c.get(url, headers={"Range": "bytes=0-15"})
            return r.status_code in (200, 206)
    except Exception:  # noqa: BLE001
        return False


def _url_for(game: str, nm: str, raw_name: str) -> str | None:
    if game == "sr":
        return None  # 星铁要 id，走异步映射，见 _url_for_sr
    repo_dir = NAME_DIR_REPOS.get(game)
    if not repo_dir:
        return None
    repo, d = repo_dir
    return _raw_url(repo, f"{d}/{nm}.png")


async def _url_for_sr(nm: str, raw_name: str) -> str | None:
    m = await _sr_id_map()
    cid = m.get(nm) or m.get(raw_name)
    if cid:
        return _raw_url(_SR_REPO, f"guide for role/{cid}.png")
    return None


async def guide_image_url(game: str, name: str) -> str:
    """查角色成品攻略图 URL。

    game 指定 → 只查该库；game 为空 → 跨 原→星→绝 探测（首个真实存在的命中）。
    未命中返回 ''（调用方回落 wiki 卡片）。
    """
    raw_name = (name or "").strip()
    nm = data.norm_name(raw_name)
    if not nm:
        return ""
    now = time.time()
    if nm in _hit_cache and now - _hit_ts.get(nm, 0) < _HIT_TTL:
        g = _hit_cache[nm]
        if g == "sr":
            u = await _url_for_sr(nm, raw_name)
            return u or ""
        return _url_for(g, nm, raw_name) or ""
    games = [game] if game in ("ys", "sr", "zzz") else _ORDER
    for g in games:
        if g == "sr":
            u = await _url_for_sr(nm, raw_name)
        else:
            u = _url_for(g, nm, raw_name)
        if u and await _exists(u):
            _hit_cache[nm] = g
            _hit_ts[nm] = now
            return u
    return ""
