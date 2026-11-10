# B站游戏 wiki 抓取（原神 / 星穹铁道 / 绝区零 / 鸣潮 / 异环）。
#
# 选型说明（实测 2026-09-14）：
#   * hakush.in 本机 DNS 解析失败、genshin.dev 已不返回 JSON，均不可用；
#   * wiki.biligame.com 实测可达，中文、覆盖五款游戏，作为数据源。
#
# 踩过的坑（别改回去）：
#   1) 鸣潮是 wutheringwaves —— 不是 mc（Minecraft Wiki）；异环是 yh；
#   2) 页面 URL 必须用 **index.php?title=** 形式（短链在鸣潮站 404）；
#   3) 角色页 HTML 前几十行是导航废话，必须去噪（_clean_lines）；
#   4) **副本/活动必须用 wikitext API**（action=parse&prop=wikitext）：
#      HTML 抓出来全是导航；wikitext 里才有结构化的当期数据
#      （原神渊月螺旋的 ==渊月祝福== / ===第X层=== / ◆buff / 讨伐列表，
#       星铁混沌回忆的 |记忆紊流= / |其一1队1波=）；
#   5) gitee raw 可取图片；othername.json 走 raw 会 451，必须走 gitee API。
import asyncio
import re
import time
from collections import OrderedDict
from urllib.parse import quote

import httpx
from nonebot.log import logger

WIKI_BASE = {
    "ys": "https://wiki.biligame.com/ys/",
    "sr": "https://wiki.biligame.com/sr/",
    "zzz": "https://wiki.biligame.com/zzz/",
    "ww": "https://wiki.biligame.com/wutheringwaves/",
    "yh": "https://wiki.biligame.com/yh/",
}

#: 当期副本页面（wikitext 页面名）
#: 原神 9-12 层（玩家真正关心的当期深渊）在「渊月螺旋」，1-8 层在「深境回廊」
ABYSS_PAGES = {
    "ys": "渊月螺旋",
    "sr": "混沌回忆",
    "zzz": "危局强袭",
    "ww": "逆境深塔",
    "yh": "副本",
}
#: 当期活动页面
EVENT_PAGES = {
    "ys": "活动一览",
    "sr": "活动一览",
    "zzz": "活动一览",
    "ww": "活动一览",
    "yh": "活动一览",
}

GAME_NAMES = {
    "ys": "原神", "sr": "星穹铁道", "zzz": "绝区零", "ww": "鸣潮", "yh": "异环",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 正文去噪用的词表（角色页 HTML 用）
_STRATEGY_RE = re.compile(r"武器|圣遗物|遗器|音擎|驱动盘|声骸|天赋|行迹|技能|命之座|星魂|"
                          r"配队|阵容|队友|词条|主词条|副词条|突破|材料|定位|元素|属性|"
                          r"伤害|输出|辅助|治疗|护盾|充能|暴击|暴伤|攻击|生命|防御|精通|"
                          r"手法|循环|毕业|养成|推荐|平民|光锥|共鸣链|谐律")
_NOISE_RE = re.compile(r"WIKI|wiki|编辑|筛选|导航|推荐给朋友|Ctrl|主观|取自|本页面|"
                       r"收藏|分享|关注|评论|点赞|广告|首页|总览|图鉴导航|新手指南|"
                       r"热门攻略|往期攻略|数据分析|工具箱|创作激励|手机端|B站|哔哩哔哩")

#: 缓存 10 分钟：数据没那么快变；请求太频繁会触发 B站 wiki 的 EdgeOne 防护
#: （2026-09-14 实测被限流，页面直接返回拦截页，整套查询瘫痪几十分钟）
_CACHE_TTL = 600.0
#: 全局请求节流：两次 wiki 请求最小间隔（防护对高频访问敏感）
_MIN_INTERVAL = 1.5
_last_req = 0.0
_HTML_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_TEXT_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_WT_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_CACHE_MAX = 30


def _cache_get(store: OrderedDict, key: str) -> str | None:
    item = store.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > _CACHE_TTL:
        store.pop(key, None)
        return None
    store.move_to_end(key)
    return val


def _cache_put(store: OrderedDict, key: str, val: str) -> None:
    store[key] = (time.time(), val)
    store.move_to_end(key)
    while len(store) > _CACHE_MAX:
        store.popitem(last=False)


def page_url(game: str, title: str) -> str:
    return WIKI_BASE.get(game, WIKI_BASE["ys"]) + "index.php?title=" + quote(title)


def home_url(game: str) -> str:
    return WIKI_BASE.get(game, WIKI_BASE["ys"])


def api_url(game: str, **params) -> str:
    base = WIKI_BASE.get(game, WIKI_BASE["ys"])
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"{base}api.php?{qs}&format=json&formatversion=2"


async def _get(url: str) -> str:
    global _last_req
    now = time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_req)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_req = time.monotonic()
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


def _html_to_text(html: str) -> str:
    s = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S | re.I)
    m = re.search(r'<div[^>]+class="[^"]*mw-parser-output[^"]*"[^>]*>(.*)', s, re.S | re.I)
    if m:
        s = m.group(1)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = re.sub(r"&nbsp;?", " ", s)
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _extract_og_image(html: str) -> str:
    m = re.search(
        r'(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']'
        r'|content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image["\']',
        html, re.I,
    )
    return ((m.group(1) or m.group(2) or "").strip()) if m else ""


def _clean_lines(text: str, limit: int = 60) -> list[str]:
    out: list[str] = []
    for raw in text.split("\n"):
        ln = raw.strip()
        if len(ln) < 2 or len(ln) > 120:
            continue
        if _STRATEGY_RE.search(ln):
            out.append(ln)
            continue
        if _NOISE_RE.search(ln):
            continue
        out.append(ln)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- wikitext
def clean_wikitext(w: str) -> str:
    """wikitext → 可读中文（副本/活动的当期数据都靠这个提取）。

    处理：注释 → 模板（图标/参数值）→ 链接 → 标题 → 标记符号 → 空白。
    注释里常是历史期或编辑提示，先去掉；去掉后内容过少则保留注释再清一遍。
    """
    def _one(s: str) -> str:
        # **不删除注释内容**：星铁「混沌回忆」的当期阵容就藏在注释里的模板中，
        # 直接删注释会把当期数据一起丢掉（2026-09-14 实测踩到），只去掉标记。
        s = s.replace("<!--", "").replace("-->", "")
        # {{图标|名称|n}} → 名称；{{模板|key=value|...}} → 保留非空值
        def tpl(m: re.Match) -> str:
            body = m.group(1)
            parts = [p.strip() for p in body.split("|")]
            head = parts[0].strip().lower()
            vals: list[str] = []
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    v = v.strip()
                    # 纯数字/空/过短的值没有信息量（多为排版参数）
                    if v and len(v) > 1 and not v.isdigit():
                        vals.append(f"{k.strip()}：{v}")
                elif p and len(p) > 1 and not p.isdigit() and head in ("图标", "图片", "icon"):
                    vals.append(p)
            return "; ".join(vals) if vals else ""
        for _ in range(3):  # 嵌套模板最多处理三轮
            s = re.sub(r"\{\{([^{}]*)\}\}", tpl, s)
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
        # 链接 [[A|B]] → B ；[[A]] → A
        s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
        s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
        # 外部链接 [http.. 名称] → 名称
        s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
        # 标题层级 → 【】分区
        s = re.sub(r"^=====\s*(.+?)\s*=====\s*$", r"【\1】", s, flags=re.M)
        s = re.sub(r"^====\s*(.+?)\s*====\s*$", r"【\1】", s, flags=re.M)
        s = re.sub(r"^===\s*(.+?)\s*===\s*$", r"【\1】", s, flags=re.M)
        s = re.sub(r"^==\s*(.+?)\s*==\s*$", r"【\1】", s, flags=re.M)
        # 标记与标签
        s = s.replace("'''", "").replace("''", "")
        s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
        s = re.sub(r"<[^>]+>", "", s)
        s = re.sub(r"&nbsp;?", " ", s)
        # wikitable 语法残留在纯文本里没意义（{| class=... / |} / |- / ! 表头）
        s = re.sub(r"^\{\|[^\n]*$", "", s, flags=re.M)
        s = re.sub(r"^\|\}.*$", "", s, flags=re.M)
        s = re.sub(r"^\|-.*$", "", s, flags=re.M)
        s = re.sub(r"^!\s?", "", s, flags=re.M)
        # 行首列表符保留（* 是讨伐列表内容）
        s = re.sub(r"^\*+", "·", s, flags=re.M)
        s = re.sub(r"[ \t]+", " ", s)
        s = re.sub(r"\n{2,}", "\n", s)
        return s.strip()

    out = _one(w)
    if len(out) < 300 and w:
        # 注释里可能才有当期数据（星铁混沌回忆就是）→ 保留注释再清
        out2 = _one(re.sub(r"<!--(?!.*?-->)", "", w, flags=re.S))
        if len(out2) > len(out):
            out = out2
    return out


async def fetch_wikitext(game: str, page: str) -> tuple[str, str]:
    """抓页面 wikitext 并清洗，返回 (纯文本, 页面链接)；失败返回 ('', url)。

    教训（2026-09-14 线上事故）：wiki 偶发风控会返回 **200 + error JSON**（无 parse 字段），
    旧代码把它当空数据静默吞掉**还缓存了 120 秒**，把一次瞬时失败放大成两分钟全失败。
    现在：空结果绝不缓存、打出真实 error、自动重试一次（1.5s 退避）。
    """
    url = api_url(game, action="parse", page=page, prop="wikitext")
    wt = _cache_get(_WT_CACHE, url)
    if wt is None:
        import json as _json
        for attempt in range(2):
            try:
                data = _json.loads(await _get(url))
                parse = data.get("parse") or {}
                wt = parse.get("wikitext") or ""
                if not wt:
                    err = data.get("error") or {}
                    logger.warning(
                        f"wiki_wikitext_empty page={page} attempt={attempt} "
                        f"error={str(err.get('code') or list(data.keys()))[:120]}"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"wiki_wikitext_failed page={page} attempt={attempt} "
                    f"error={type(e).__name__}: {str(e)[:150]}"
                )
            if wt:
                _cache_put(_WT_CACHE, url, wt)
                return clean_wikitext(wt), page_url(game, page)
            if attempt == 0:
                await asyncio.sleep(1.5)
        # 两次都失败 → 页面名可能不同，opensearch 纠正后再试
        for cand in await _opensearch(game, page):
            u2 = api_url(game, action="parse", page=cand, prop="wikitext")
            try:
                wt = (_json.loads(await _get(u2)).get("parse") or {}).get("wikitext") or ""
            except Exception:  # noqa: BLE001
                continue
            if wt:
                _cache_put(_WT_CACHE, u2, wt)
                return clean_wikitext(wt), page_url(game, cand)
        return "", page_url(game, page)
    return clean_wikitext(wt), page_url(game, page)


async def _opensearch(game: str, keyword: str) -> list[str]:
    import json as _json
    try:
        data = _json.loads(await _get(api_url(game, action="opensearch", search=keyword, limit=6)))
        if isinstance(data, list) and len(data) > 1:
            return [str(x) for x in data[1] if x]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wiki_opensearch_failed game={game} kw={keyword}: {type(e).__name__}: {e}")
    return []


# ---------------------------------------------------------------- 对外接口
async def fetch_page(game: str, title: str) -> tuple[str, str, str]:
    """HTML 页面 → (正文, og:image, URL)（角色图鉴用，需要头像）。"""
    url = page_url(game, title)
    html = _cache_get(_HTML_CACHE, url)
    if html is None:
        try:
            html = await _get(url)
            _cache_put(_HTML_CACHE, url, html)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wiki_fetch_failed url={url} error={type(e).__name__}: {e}")
            for cand in await _opensearch(game, title):
                u2 = page_url(game, cand)
                try:
                    html = await _get(u2)
                    _cache_put(_HTML_CACHE, u2, html)
                    url = u2
                    break
                except Exception:  # noqa: BLE001
                    continue
    if not html:
        return "", "", url
    text = _cache_get(_TEXT_CACHE, url)
    if text is None:
        text = _html_to_text(html)
        _cache_put(_TEXT_CACHE, url, text)
    return text, _extract_og_image(html), url


async def character_summary(name: str, game: str = "ys") -> tuple[str, str]:
    """角色图鉴：返回 (文本, 头像URL)。"""
    text, avatar, url = await fetch_page(game, name)
    gname = GAME_NAMES.get(game, game)
    if len(text) < 80:
        return (f"没抓到【{gname}】「{name}」的资料：站上没有这个页面，或网络不通。\n"
                f"去站点翻翻：{home_url(game)}"), ""
    lines = _clean_lines(text, limit=46)
    return (f"【基本信息】\n{gname} · {name}\n【wiki 内容】\n"
            + "\n".join(lines)[:1100] + f"\n详情：{url}"), avatar


async def abyss_summary(game: str = "ys") -> tuple[str, str]:
    """**当期副本**（实时）：渊月螺旋 / 混沌回忆 / 危局强袭 / 逆境深塔。"""
    page = ABYSS_PAGES.get(game, ABYSS_PAGES["ys"])
    text, url = await fetch_wikitext(game, page)
    gname = GAME_NAMES.get(game, game)
    if len(text) < 60:
        return (f"没抓到【{gname}】当期「{page}」数据（该站页面名可能不同或暂未更新）。\n"
                f"直达：{url}"), ""
    lines: list[str] = []
    for ln in text.split("\n"):
        s = ln.strip().lstrip("|").strip()
        if len(s) < 2:
            continue
        # 编辑器注释残留 / 排版参数 / wiki 未填完的占位时间
        if re.match(r"^(ctrl\+f|buff|挑战内容|往期祝福|标题|宽度)", s, re.I):
            continue
        if re.search(r"\d{4}//|////", s):
            continue
        lines.append(s)
    body = "\n".join(lines[:46])
    return f"【{gname}·{page}】\n{body[:1200]}\n\n完整内容：{url}", ""


async def _fetch_table_rows(game: str, page: str) -> tuple[list[str], str]:
    """抓渲染后的 HTML 表格行（活动列表是 wiki 动态渲染的，wikitext 里只有表头）。

    返回 (行文本列表, 页面链接)；每个 tr 的单元格用 " / " 连接。
    """
    url = page_url(game, page)
    html = _cache_get(_HTML_CACHE, url)
    if html is None:
        try:
            html = await _get(url)
            _cache_put(_HTML_CACHE, url, html)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"wiki_table_fetch_failed page={page}: {type(e).__name__}: {e}")
            return [], url
    if not html:
        return [], url
    rows: list[str] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)
        vals: list[str] = []
        for c in cells:
            t = re.sub(r"<[^>]+>", " ", c)
            t = re.sub(r"&nbsp;?", " ", t)
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                vals.append(t)
        if len(vals) >= 2:
            line = " / ".join(vals[:4])
            if 8 <= len(line) <= 130 and line not in rows:
                rows.append(line)
        if len(rows) >= 18:
            break
    return rows, url


async def event_summary(game: str = "ys") -> tuple[str, str]:
    """**当期活动**（实时）：优先解析活动一览的渲染表格，失败退回 wikitext。"""
    page = EVENT_PAGES.get(game, "活动一览")
    gname = GAME_NAMES.get(game, game)
    rows, url = await _fetch_table_rows(game, page)
    if rows:
        # 活动一览的表格是 **JS 动态渲染** 的：静态 HTML 里只有筛选器（类别/TAG/地区），
        # 抓到这些说明没有真实活动数据 → 直接降级给链接，不拿筛选器糊弄人。
        junk = sum(1 for r in rows if re.search(r"^(类别|类型|TAG|地区|查看全部)", r))
        if junk >= 1 and junk >= len(rows) // 2:
            rows = []
    if rows:
        return f"【{gname}·当期活动】\n" + "\n".join(rows[:16])[:1200] + f"\n\n完整：{url}", ""
    return (f"【{gname}】活动列表是 wiki 的**动态渲染**内容（无 Cargo API、无静态数据），"
            f"当前抓不到结构化活动列表。\n"
            f"去站点看当期活动：{url}"), ""
