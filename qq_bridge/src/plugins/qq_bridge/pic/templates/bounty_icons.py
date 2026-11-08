# -*- coding: utf-8 -*-
"""bounty_icons.py —— 赏金卡**真实物品图标**解析器（DE 官方 CDN，非几何占位）。

2026-11-08 用户纠正：「你又偷懒不去做物品的图标是吗，狍丝感染枪，预言 探求，
债券，战甲机体，武器蓝图，重做」。旧版给每个物品画一个几何小方块，等于把参考图
里最显眼的整层信息丢了。

数据源（实测 2026-11-08，worldstate.wf.wiki RSC payload）：
    {"name":"预言 探求","icon":"https://content.warframe.com/PublicExport/
     Lotus/Interface/Cards/Images/Sets/Augur/AugurSeeker.jpg!00_<hash>","count":1}
每一条 reward 都自带**游戏内官方图标 URL** 和中文名，共 226 对，参考图里的
MOD / 债券 / 战甲机体 / 武器蓝图 / 枪托 全部命中。

为什么不走 warframe._wfwiki_payload 的 JSON 解析：payload 里有
``"name":"\\"混乱\\"残留物"`` 这种转义引号，替换后变成非法 JSON，
``bountyBoard`` 会抛 ``JSONDecodeError: Expecting ',' delimiter``（项目既有
解析器同样失败，所以 MOD/债券列表本来就取不稳）。这里用**只读正则抽取**
``"name":"…","icon":"http…"`` 对，绕过非法 token，只取需要的两个字段。

架构（与项目 ``item_icons.py`` 同款：数据准备与渲染分离）：
  * 索引：进程内 TTL 120s → 已加载的 warframe 缓存 html（零外呼）→
    磁盘 ``data/warframe_icons/bounty_icon_index.json`` → 最多抓一次 wf.wiki；
  * 图标：命中本地缓存直接同步返回；缺失才同步下载并统一转 PNG 落盘，
    DE 官方 ``content.warframe.com/PublicExport/`` 与客户端同源；
  * 渲染线程（to_thread）只做本地 IO + 可能的首次短超时下载，绝不阻塞事件循环；
  * 下载失败记负缓存，不反复打源；任何失败返回 None，由渲染层**显式告警**。

运行时数据全部落在项目内 ``data/warframe_icons/``（``data/`` 已被 .gitignore 忽略）。
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

from .. import weapon_icons as _wi
from .. import icons as _icons

#: 任务类型 → 几何图标语义（真实物品图标之外的任务图标，仍是项目自绘 monoline）。
_TYPE_ICON = {
    "捕获": "window", "防御": "round_square", "歼灭": "cross",
    "资源": "cart", "净化": "check", "中断": "clipboard",
    "移动防御": "arrow_right", "虚空覆涌": "question", "刺杀": "sword",
    "生存": "clock", "救援": "check", "回收": "cart",
}


def type_icon(typ: str, fallback: str) -> str:
    """任务类型 → 图标名（未知类型回落到数据自带的 icon）。"""
    return _TYPE_ICON.get(typ) or (fallback if _icons.exists(fallback) else "circle")


WFWIKI = "https://worldstate.wf.wiki/"
TTL = 120.0
DISK_TTL = 24 * 3600.0
_MIN_BYTES = 200

# 只取「中文名 + 官方 icon URL」两个字段，绕开 payload 里的 $undefined、
# 转义引号等非法 JSON token（bountyBoard 用 json.JSONDecoder 会整体失败）。
_PAIR_RE = re.compile(r'"name":"([^"]+)","icon":"(https://[^"]+)"')
_BLOCK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)

_INDEX: dict[str, str] = {}
_INDEX_TS = 0.0
_NEG: set[str] = set()

INDEX_FILE: Path = _wi._CACHE_DIR / "bounty_icon_index.json"


def normalize(name: str) -> str:
    """「2x 培训债券」/「培训债务债券」→「培训债券」。

    参考图写「2x 培训债券」，wf.wiki payload 写「培训债务债券」；剥离数量前缀
    与「债务」二字后两者一致。其余物品名原样保留。
    """
    s = re.sub(r"^\d+\s*[xX]\s*", "", str(name or "").strip())
    s = s.replace("债务债券", "债券")
    return s.strip()


#: DE 官方通用部件图（本地已缓存）→ 历史轮换中文名。值写**缓存文件名**而非 URL：
#: ``ensure()`` 会先查本地缓存文件，命中即返回，不会拿它去联网下载。
#: 只登记用户参考图点名过、且 wf.wiki 当前轮换已不再列出的条目；
#: 真实 URL 一旦重新出现会优先于别名（见 ``load_index`` 的合并顺序）。
_LOCAL_ICON_ALIAS: dict[str, str] = {
    "孢丝感染枪 枪托": "GenericGunStock.png",
}


def index_from_html(html: str) -> dict[str, str]:
    """RSC payload → {归一化中文名: 官方图标 URL}（去重保留首次）。"""
    if not html:
        return {}
    full = "".join(_BLOCK_RE.findall(html)).replace('\\"', '"')
    out: dict[str, str] = {}
    for name, url in _PAIR_RE.findall(full):
        key = normalize(name)
        if key and key not in out:
            out[key] = url
    return out


def _warframe_html() -> str | None:
    """只读已加载的 warframe 模块缓存，**绝不主动 import**（避免 pic↔game_tools 循环）。

    ``bounties_card_data()`` 刚抓过 wf.wiki，此时 html 就在进程内；线上零额外外呼。
    """
    m = sys.modules.get("src.plugins.qq_bridge.game_tools.warframe")
    if m is None:
        return None
    cache = getattr(m, "_wfwiki_cache", None)
    if not isinstance(cache, dict):
        return None
    html = cache.get("html")
    return html if isinstance(html, str) and html else None


def _fetch_html(timeout: float = 5.0) -> str | None:
    try:
        req = urllib.request.Request(
            WFWIKI, headers={"User-Agent": "Mozilla/5.0", "Referer": WFWIKI})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 固定白名单域名
            return r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — 网络故障交给磁盘索引兜底
        return None


def _save_index(idx: dict[str, str]) -> None:
    try:
        _wi._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_FILE.write_text(
            json.dumps({"ts": time.time(), "map": idx}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass


def _disk_index() -> dict[str, str]:
    try:
        d = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if (isinstance(d, dict) and isinstance(d.get("map"), dict)
                and time.time() - float(d.get("ts") or 0) < DISK_TTL):
            return d["map"]
    except Exception:  # noqa: BLE001
        pass
    return {}


def load_index(force: bool = False) -> dict[str, str]:
    """返回图标索引；优先级：进程内 → warframe 缓存 html → 磁盘 → 抓一次 wf.wiki。

    **累积合并，不整体覆盖**：wf.wiki 每 2.5h 轮换只列当前奖励，上一版直接把
    新索引整个赋给 ``_INDEX``，会把旧轮换的「孢丝感染枪 枪托」抹掉，渲染随即
    回落几何方块。现在新键覆盖旧值、旧键保留，并并入本地已缓存的官方通用部件
    图别名。真实 URL 优先于别名，别名仅作历史轮换兜底。
    """
    global _INDEX, _INDEX_TS
    now = time.time()
    if not force and _INDEX and now - _INDEX_TS < TTL:
        return _INDEX

    def _merge(fresh: dict[str, str]) -> dict[str, str]:
        return {**_LOCAL_ICON_ALIAS, **_disk_index(), **_INDEX, **fresh}

    html = _warframe_html()
    if html:
        idx = index_from_html(html)
        if idx:
            _INDEX, _INDEX_TS = _merge(idx), now
            _save_index(_INDEX)
            return _INDEX
    idx = _disk_index()
    if idx:
        _INDEX, _INDEX_TS = _merge(idx), now
        return _INDEX
    html = _fetch_html()
    idx = index_from_html(html or "")
    _INDEX = _merge(idx)
    if idx:
        _save_index(_INDEX)
    _INDEX_TS = now
    return _INDEX


def cache_name(url: str) -> str:
    """URL → 本地缓存文件名：去掉 ``!00_<hash>`` 后缀，扩展名统一 .png。

    MOD 卡面是 .jpg，债券/部件是 .png；统一转存 PNG 让渲染层只处理一种格式。
    """
    base = url.split("!00_")[0].rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    return stem + ".png"


def ensure(name: str, timeout: float = 4.0) -> str | None:
    """中文物品名 → **真实图标本地绝对路径**；取不到返回 None（调用方显式告警）。"""
    key = normalize(name)
    if not key:
        return None
    url = load_index().get(key)
    if not url:
        return None
    fname = cache_name(url)
    p = _wi._CACHE_DIR / fname
    try:
        if p.is_file() and p.stat().st_size >= _MIN_BYTES:
            return str(p)
    except OSError:
        pass
    if fname in _NEG:
        return None
    # ``!00_<hash>`` 是 DE CDN **必需的路径戳**，不能去掉（去掉实测 404）。
    # 它只用于算本地缓存文件名（见 cache_name），下载必须用原始带戳 URL。
    # 项目 warframe._bounty_download_icon 也是同款：base 取名、icon_url 下载。
    data, _404 = _wi._urllib_get(url, timeout)
    if not data or len(data) < _MIN_BYTES:
        _NEG.add(fname)
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        _wi._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        im.save(p, "PNG")
        return str(p)
    except Exception:  # noqa: BLE001 — 错误页/截断图，记负缓存不重试
        _NEG.add(fname)
        return None


def prewarm(names: list[str], timeout: float = 4.0) -> dict[str, str | None]:
    """批量预取（测试/部署前用），返回 {原名: 路径或 None}。"""
    load_index()
    return {n: ensure(n, timeout) for n in names}


def stats() -> dict:
    """自检用：索引条数 / 已缓存图标数 / 负缓存数。"""
    idx = load_index()
    cached = sum(1 for u in idx.values()
                 if (_wi._CACHE_DIR / cache_name(u)).is_file())
    return {"index": len(idx), "cached": cached,
            "missing": len(idx) - cached, "negative": len(_NEG)}
