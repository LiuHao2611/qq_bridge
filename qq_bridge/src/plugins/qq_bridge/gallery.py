# -*- coding: utf-8 -*-
"""QQ 侧「历史画廊」（2026-09-11 新增）。

需求：在群里对话就能翻主项目的**文生图 / 图生图历史**，先看缩略图九宫格，
再回复编号查看原图（不用开网页、不用记任务号）。

设计要点
--------
1. **只经 HTTP 单向调主项目**（铁律 memory 86812403）：
   `GET {MANJU_API_BASE}/t2i/tasks?status=succeeded&page=&size=` → `data.items`。
   绝不 import 主项目任何模块。
2. **缩略图 = 真实图片缩略后拼接**，不是自绘文字卡：用户要的是"看图"，
   所以下载原图 → 居中裁剪成正方形 → 拼 3×3 → 每格左上角编号。
3. **单张下载失败画占位格**（灰底 + ？），绝不因一张坏图让整页发不出来
   （CLOSE-01 收敛终态 / 禁止静默吞：`download_thumbs` 记日志并返回 None）。
4. **编号 → 原图**靠内存缓存 `remember/pick`，带 TTL（OPS-02 持久数据声明
   存活时长），过期自动丢弃，避免"昨天点过号今天误发别人的图"。
5. 拼图输出 **JPEG**（9 张缩略图 PNG 会到几 MB，JPEG q85 约几百 KB，
   NapCat 更好发）。

不变量：本模块不做任何落盘（拼图是内存产物，直发 base64）。
"""
from __future__ import annotations

import asyncio
import io
import time

import httpx
from PIL import Image, ImageDraw

from .config import Config, get_logger, jlog

log = get_logger()

#: 一页张数（3×3 九宫格，QQ 里一屏看得全）
PAGE_SIZE = 9
#: 网格列数
COLS = 3
#: 每格边长（px）
CELL = 320
#: 格间距 / 外边距
GAP = 10
PAD = 16
#: 编号选择有效期（秒）
VIEW_TTL = 1800.0
#: 单张缩略图下载超时（秒）
THUMB_TIMEOUT = 15.0

#: 画廊翻页口令（2026-09-13）：**词本身即参数**。
#: 单一口径来源——`intent.MAID_ALIASES` 据此生成暗号、`dispatcher` 据此免 @ 放行、
#: `_cmd_gallery` 据此算页码增量，三处不再各写一份词表（此前只写在 _cmd_gallery 的
#: 正则里，导致"口令识别得到、但消息根本到不了那一层"）。
NAV_NEXT_WORDS = ("下一页", "下页", "翻页", "翻下页", "next")
NAV_PREV_WORDS = ("上一页", "上页", "翻上页", "prev", "previous")
NAV_WORDS = NAV_NEXT_WORDS + NAV_PREV_WORDS

#: user_id -> {"items": [...], "ts": float}
_VIEWS: dict[int, dict] = {}


# ------------------------------------------------------------------ 数据解析
def parse_items(payload: dict, drop_deleted: bool = False) -> list[dict]:
    """主项目 `/t2i/tasks` 响应 → 归一化条目。

    兼容两种返回形态（实测都存在）：
      * `item.images[{url}]`（文生图 / 图生图结果）
      * `item.result.images[{url}]` 或 `item.result.url`（部分老任务）

    过滤掉没有图片地址的任务（失败/进行中/被删），调用方拿到即可直接下载。

    :param drop_deleted: True 时**剔除回收站图片**（`deleted_at` 非空 = 主项目
        软删标记）。默认 False —— 保持 QQ 画廊命令既有行为不变（无创原则）；
        主题编辑器「选择项目图片」显式传 True（用户要求过滤回收站）。
    """
    data = (payload or {}).get("data") or {}
    raw = data.get("items") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        if drop_deleted and str(it.get("deleted_at") or "").strip():
            continue  # 回收站（软删）图片不上墙
        imgs = it.get("images") or []
        if not imgs:
            res = it.get("result") or {}
            imgs = res.get("images") or []
            if not imgs and res.get("url"):
                imgs = [{"url": res.get("url")}]
        url = ""
        if imgs and isinstance(imgs[0], dict):
            url = str(imgs[0].get("url") or "")
        if not url:
            continue  # 无图任务不上墙（避免编号点开是空的）
        t = str(it.get("type") or "")
        out.append({
            "task_id": str(it.get("task_id") or ""),
            "prompt": str(it.get("prompt") or ""),
            "image_url": url,
            "thumb_url": abs_url(url),   # 绝对地址：前端 <img src> 直接用
            "created_at": str(it.get("created_at") or ""),
            "kind": "改图" if t.startswith("i2i") else "生图",
            "type": t,
        })
    return out


def _created_at_key(it: dict) -> float:
    """统一把 created_at 解析成 epoch 秒（浮点），供降序排序。

    主项目 created_at 有两种形态混存：ISO 字符串 ``"2026-09-06 17:42:07"``
    与 unix 时间戳（整/浮点数字）。二者直接按字符串比较会错序（'1'<'2' 导致
    时间戳排到 ISO 日期之后），这里归一为数值再比，确保「最新在前」。
    """
    import datetime
    c = str(it.get("created_at") or "").strip()
    if not c:
        return 0.0
    # 1) unix 时间戳（秒，可能浮点）
    if c.lstrip("-").replace(".", "", 1).isdigit():
        try:
            return float(c)
        except ValueError:
            pass
    # 2) ISO 字符串
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(c, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def parse_gallery_arg(arg: str) -> tuple[str, int]:
    """解析 `历史 [分类] [页码]` 命令参数。

    返回 ``(cls, page)``：
      - ``cls``：``"i2i"``（图生图） / ``"t2i_local"``（文生图） / ``""``（全部）
      - ``page``：页码（≥1）

    分类词：图生图=图生图/改图/垫图/i2i/img2img；文生图=文生图/文图/生图/t2i/txt2img。
    先判图生图整词，避免「图生图」被「生图」误判成文生图。
    """
    import re
    s = (arg or "").strip().lower()
    cls = ""
    _i2i = ("图生图", "图生", "改图", "垫图", "i2i", "img2img", "图图")
    _t2i = ("文生图", "文生", "文图", "生图", "t2i", "txt2img")
    for k in _i2i:
        if k in s:
            cls = "i2i"
            break
    if not cls:
        for k in _t2i:
            if k in s:
                cls = "t2i_local"
                break
    # 移除已识别的分类词，避免其内含数字（如 i2i 的 2）被误当页码
    for k in _i2i + _t2i:
        s = s.replace(k, " ")
    m = re.search(r"(\d+)", s)
    page = int(m.group(1)) if m else 1
    return cls, max(1, page)


def abs_url(url: str) -> str:
    """`/file?name=xxx` → `http://127.0.0.1:7788/file?name=xxx`（NapCat 要绝对地址）。"""
    s = (url or "").strip()
    if not s or s.startswith(("http://", "https://", "base64://", "file:///")):
        return s
    if s.startswith("/"):
        return Config.MANJU_FILE_BASE.rstrip("/") + s
    return s


async def fetch_recent(page: int = 1, size: int = PAGE_SIZE, type_filter: str = "",
                       transport=None) -> tuple[list[dict], str, int]:
    """拉主项目最近成功出图的任务。返回 `(items, error, total)`。

    :param type_filter: 分类筛选——``"i2i"`` 只看图生图（type_prefix=i2i），
        ``"t2i_local"`` 只看文生图（type=t2i_local），空串=全部。
    error 为空串即成功；total 为符合筛选的总张数（用于提示「共 N 页」）。
    """
    base = Config.MANJU_API_BASE.rstrip("/")
    p = max(1, int(page or 1))
    s = max(1, int(size or PAGE_SIZE))
    url = "%s/t2i/tasks?status=succeeded&page=%d&size=%d" % (base, p, s)
    if type_filter == "i2i":
        url += "&type_prefix=i2i"
    elif type_filter:
        url += "&type=" + type_filter
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as cli:
            resp = await cli.get(url)
        if resp.status_code >= 400:
            return [], "HTTP %d" % resp.status_code, 0
        payload = resp.json()
        items = parse_items(payload)
        # 本地兜底排序：主项目已 ORDER BY created_at DESC，但脏数据（NULL/空
        # created_at）可能错排，这里按 ISO 字符串再稳一次（CLOSE-02 防御）。
        items.sort(key=_created_at_key, reverse=True)
        total = 0
        try:
            total = int((payload.get("data") or {})
                        .get("pagination", {}).get("total") or 0)
        except (TypeError, ValueError):
            total = len(items)
        return items, "", total
    except Exception as e:  # noqa: BLE001 — 记日志后交调用方降级（CLOSE-02）
        log.warning(jlog("gallery_fetch_failed", error=type(e).__name__,
                         detail=str(e)[:160]))
        return [], type(e).__name__, 0


# ------------------------------------------------- 主题编辑器「选择项目图片」
#: 一次扫描最多翻多少页（800 张）：主项目成图会一直累积，全量扫不现实；
#: 超出部分在响应里用 capped 标出，前端如实提示「仅覆盖最近 N 张」。
SCAN_MAX_PAGES = 8
#: 单次扫描每页张数（与 SCAN_MAX_PAGES 相乘 = 覆盖上限）
SCAN_PAGE_SIZE = 100


def is_project_url(url: str) -> bool:
    """是否指向**主项目自己的**文件服务（导入图片前的 SSRF 闸门）。

    主项目图片地址形如 `/file?name=xxx`（相对），补齐后是 `http://127.0.0.1:7788/...`。
    这里只放行 MANJU_FILE_BASE / MANJU_API_BASE 的 host，杜绝被诱导去拉内网任意地址。
    """
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).netloc or "").strip().lower()
    except Exception:
        return False
    if not host:
        return False
    allowed = set()
    for b in (getattr(Config, "MANJU_FILE_BASE", "") or "",
              getattr(Config, "MANJU_API_BASE", "") or ""):
        try:
            allowed.add((urlparse(b).netloc or "").strip().lower())
        except Exception:
            pass
    allowed.discard("")
    return host in allowed


async def fetch_images(page: int = 1, size: int = 24, type_filter: str = "",
                       keyword: str = "", sort: str = "desc",
                       transport=None) -> dict:
    """拉主项目「文生图 / 图生图」成图，供主题编辑器「选择项目图片」直接取用。

    与 `fetch_recent`（QQ 画廊九宫格）的差异，本函数专为主题编辑器设计：
      * **过滤回收站**：`deleted_at` 非空一律剔除（用户明确要求）；
      * **关键词筛选**：按 prompt 模糊匹配（主项目接口无 q 参数，本地筛）；
      * **真排序**：最新优先 / 最早优先（主项目只给 DESC，升序靠本地排）；
      * **真分页**：先扫描（并发翻页，上限 SCAN_MAX_PAGES×SCAN_PAGE_SIZE 张），
        再在本地做过滤 → 排序 → 分页，所以页码/总数对得上，不会翻页翻出空页。

    分类筛选（文生图 / 图生图）交给主项目做，这样 total 才是该分类的真实总数。

    :return: ``{ok, items, total, page, page_size, total_pages, scanned, capped, error}``
             error 非空即失败（主项目不可达 / HTTP 非 2xx），items 为 []。
    """
    base = Config.MANJU_API_BASE.rstrip("/")
    p = max(1, int(page or 1))
    s = max(1, min(60, int(size or 24)))
    kw = (keyword or "").strip().lower()

    def _url(pg: int, sz: int) -> str:
        u = "%s/t2i/tasks?status=succeeded&page=%d&size=%d" % (base, pg, sz)
        if type_filter == "i2i":
            u += "&type_prefix=i2i"
        elif type_filter:
            u += "&type=" + type_filter
        return u

    async def _get(pg: int, sz: int) -> dict:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as cli:
            resp = await cli.get(_url(pg, sz))
        if resp.status_code >= 400:
            raise RuntimeError("HTTP %d" % resp.status_code)
        return resp.json()

    try:
        first = await _get(1, SCAN_PAGE_SIZE)
        pg0 = (first.get("data") or {}).get("pagination") or {}
        try:
            total_pages = max(1, int(pg0.get("total_pages") or 1))
        except (TypeError, ValueError):
            total_pages = 1
        pages = min(total_pages, SCAN_MAX_PAGES)
        capped = total_pages > pages
        payloads = [first]
        if pages > 1:
            payloads += list(await asyncio.gather(
                *[_get(i, SCAN_PAGE_SIZE) for i in range(2, pages + 1)],
                return_exceptions=True))
        items: list[dict] = []
        scanned = 0
        for pl in payloads:
            if isinstance(pl, BaseException) or not isinstance(pl, dict):
                continue  # 单页失败跳过，不让整次查询挂掉（CLOSE-02 降级）
            got = parse_items(pl, drop_deleted=True)
            scanned += len(got)
            items += got
        if kw:
            items = [it for it in items if kw in str(it.get("prompt") or "").lower()]
        # 去重：并发翻页边界可能重复命中同一 task
        seen, uniq = set(), []
        for it in items:
            tid = it.get("task_id") or it.get("image_url")
            if tid in seen:
                continue
            seen.add(tid)
            uniq.append(it)
        items = uniq
        items.sort(key=_created_at_key, reverse=(str(sort or "desc").lower() != "asc"))
        total = len(items)
        total_pages_out = max(1, (total + s - 1) // s)
        start = (p - 1) * s
        return {
            "ok": True,
            "items": items[start:start + s],
            "total": total,
            "page": p,
            "page_size": s,
            "total_pages": total_pages_out,
            "scanned": scanned,
            "capped": capped,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001 — 记日志后交调用方降级（CLOSE-02）
        log.warning(jlog("gallery_fetch_images_failed", error=type(e).__name__,
                         detail=str(e)[:160]))
        return {"ok": False, "items": [], "total": 0, "page": p, "page_size": s,
                "total_pages": 0, "scanned": 0, "capped": False,
                "error": type(e).__name__}


# ------------------------------------------------------------------ 缩略图拼接
async def download_thumbs(urls: list[str], transport=None) -> list:
    """并发下载缩略图；单张失败返回 None（**不抛**，保证整页能出）。"""
    async def _one(u: str):
        if not u:
            return None
        try:
            async with httpx.AsyncClient(timeout=THUMB_TIMEOUT,
                                         transport=transport) as cli:
                r = await cli.get(u)
            if r.status_code == 200 and r.content:
                return r.content
            log.warning(jlog("gallery_thumb_http", status=r.status_code))
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("gallery_thumb_failed", error=type(e).__name__))
        return None

    return list(await asyncio.gather(*[_one(u) for u in urls]))


def _square(data: bytes, cell: int = CELL) -> Image.Image:
    """原图 bytes → 居中裁剪的正方形缩略图。"""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    s = min(w, h)
    if s <= 0:
        raise ValueError("空图片")
    left, top = (w - s) // 2, (h - s) // 2
    return img.crop((left, top, left + s, top + s)).resize((cell, cell), Image.LANCZOS)


def _placeholder(cell: int = CELL) -> Image.Image:
    """下载失败时的占位格（浅灰 + 问号）。"""
    img = Image.new("RGB", (cell, cell), (58, 58, 66))
    d = ImageDraw.Draw(img)
    d.text((cell // 2 - 4, cell // 2 - 8), "?", fill=(160, 160, 170))
    return img


def _badge(canvas: Image.Image, x: int, y: int, n: int) -> None:
    """格子左上角画圆形编号徽标。"""
    d = ImageDraw.Draw(canvas)
    r = 18
    d.ellipse([x + 6, y + 6, x + 6 + r * 2, y + 6 + r * 2], fill=(0, 0, 0))
    d.text((x + 6 + r - 3, y + 6 + r - 6), str(n), fill=(255, 255, 255))


def build_grid(images: list, cols: int = COLS, cell: int = CELL) -> bytes:
    """把缩略图（bytes 或 None）拼成带编号的网格图，返回 JPEG bytes。

    :param images: 每项为图片 bytes；`None` 表示那张下载失败 → 画占位格。
    :raises ValueError: 空列表（调用方应先判空）
    """
    if not images:
        raise ValueError("没有可拼接的图片")
    cols = max(1, int(cols or COLS))
    rows = (len(images) + cols - 1) // cols
    w = PAD * 2 + cols * cell + (cols - 1) * GAP
    h = PAD * 2 + rows * cell + (rows - 1) * GAP
    canvas = Image.new("RGB", (w, h), (32, 32, 38))
    for i, data in enumerate(images):
        try:
            tile = _square(data, cell) if data else _placeholder(cell)
        except Exception:  # noqa: BLE001 — 坏图退化成占位，不让整页挂掉
            tile = _placeholder(cell)
        c, r = i % cols, i // cols
        x = PAD + c * (cell + GAP)
        y = PAD + r * (cell + GAP)
        canvas.paste(tile, (x, y))
        _badge(canvas, x, y, i + 1)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


async def build_page(items: list[dict], cols: int = COLS) -> bytes:
    """一步到位：条目列表 → 九宫格 JPEG bytes（内部并发下载）。"""
    urls = [abs_url(it.get("image_url") or "") for it in items]
    thumbs = await download_thumbs(urls)
    return build_grid(thumbs, cols=cols)


# ------------------------------------------------------------------ 编号选原图
def remember(user_id, items: list[dict], cls: str = "", page: int = 1) -> None:
    """记住某用户最近一次看到的画廊（供回复编号选原图 + 上下文翻页）。"""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return
    _VIEWS[uid] = {"items": list(items or []), "ts": time.time(),
                   "cls": cls, "page": int(page or 1)}


def peek(user_id):
    """取最近一次画廊视图的 (cls, page)；过期/不存在返回 None。

    供画廊上下文「下一页 / 上页」沿用上次分类，避免用户重复打字。
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    rec = _VIEWS.get(uid)
    if not rec:
        return None
    if time.time() - rec.get("ts", 0.0) > VIEW_TTL:
        _VIEWS.pop(uid, None)
        return None
    return rec.get("cls", ""), rec.get("page", 1)


def pick(user_id, n) -> dict | None:
    """取第 n 张（**从 1 开始**）；越界 / 过期 / 没看过 → None。"""
    try:
        uid, idx = int(user_id), int(n)
    except (TypeError, ValueError):
        return None
    rec = _VIEWS.get(uid)
    if not rec:
        return None
    if time.time() - rec.get("ts", 0.0) > VIEW_TTL:
        _VIEWS.pop(uid, None)  # 过期清理（OPS-02）
        return None
    items = rec.get("items") or []
    if idx < 1 or idx > len(items):
        return None
    return items[idx - 1]


def active_count(user_id) -> int:
    """该用户当前可点编号的张数（0=没有活跃画廊）。"""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return 0
    rec = _VIEWS.get(uid)
    if not rec:
        return 0
    if time.time() - rec.get("ts", 0.0) > VIEW_TTL:
        _VIEWS.pop(uid, None)
        return 0
    return len(rec.get("items") or [])


def nav_delta(text) -> int:
    """画廊翻页口令 → 页码增量（``+1`` 下一页 / ``-1`` 上一页 / ``0`` 不是翻页口令）。

    严格的**整句相等**判定（去尾标点、忽略大小写）：翻页口令是短词，用宽松匹配
    极易误吞正常对话（如"下面给你看看"）。
    """
    t = str(text or "").strip().lower().rstrip("？?。.!！~～，, ").strip()
    if t in NAV_PREV_WORDS:
        return -1
    if t in NAV_NEXT_WORDS:
        return 1
    return 0


def is_nav(text) -> bool:
    """是否画廊翻页口令（下一页 / 上一页 / 翻页）——群 @ 门禁免 @ 放行用。"""
    return nav_delta(text) != 0


def reset() -> None:
    """清空所有视图缓存（测试用）。"""
    _VIEWS.clear()
