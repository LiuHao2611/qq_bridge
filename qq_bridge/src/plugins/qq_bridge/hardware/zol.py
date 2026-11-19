# -*- coding: utf-8 -*-
"""hardware.zol —— ZOL 中关村在线「公开报价」采集（每日行情源）。

为什么是它（2026-11-19 调研结论）：
  * 京东 `p.3.cn` 价格接口在本机/容器均不可达（No route / 502），京东搜索未登录只回
    2.7KB 拦截页、需 Playwright + 登录态（账号风控 + 封号风险，本项目红线不碰）；
  * 拼多多 / 闲鱼同样要登录态与浏览器；
  * GitHub 上**没有**带许可的「中国 DIY 每日行情」数据仓（frocloud 是月度；
    diy-price-report 无 LICENSE 且依赖登录浏览器；PriceDive 是通用商品追踪，MIT）；
  * ZOL 分类/系列页是**公开 HTML、无需登录**，一页 48 个商品带「参考价」，是唯一
    能在机器人容器里稳定每日抓的 CNY 源。

口径（必须写进卡面，不许含糊）：
  * 价格 = ZOL 页面上的「参考报价」（元），不是某家店的实时成交价；
  * 一个型号取**当日样本最低价**（同型号不同品牌/店铺取最低），并记录样本数；
  * 每日快照由本工具生成，涨跌 = 今日快照 vs 上一份快照（不是 ZOL 的历史曲线）。

覆盖边界（不伪造）：
  * 显卡按 ZOL 系列页采（当前 10 个系列）；ZOL 没有系列页的新卡（9060XT/7650GRE/
    Arc B580 等）**采不到就不编**；
  * CPU 按 ZOL 家族页采（Ryzen 5/7/9、i5/i7/i9、Ultra 5/7/9），再按型号名精确匹配
    frocloud 的型号；匹配不到就记入 `missing`，卡面写「今日无报价」。
"""
from __future__ import annotations

import html as _html
import http.cookiejar
import re
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
BASE = "https://detail.zol.com.cn"
TIMEOUT = 20.0
RETRIES = 2
DELAY = 1.0
MAX_PAGES = 4

_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

#: 显卡：ZOL 系列页（sid 来自分类页侧栏实测，2026-11-19）
#: (bucket, 展示名, sid, include 正则, exclude 正则)
GPU_CATALOG = (
    ("gpu", "RTX 5050", "11095", "", ""),
    ("gpu", "RTX 5060", "11072", "", ""),
    ("gpu", "RTX 5060 Ti 16G", "11070", r"(?<!\d)16G", ""),
    ("gpu", "RTX 5060 Ti 8G", "11070", r"(?<!\d)8G", ""),
    ("gpu", "RTX 5070", "11071", "", ""),
    ("gpu", "RTX 5070 Ti", "11069", "", ""),
    ("gpu", "RTX 5080", "11094", "", ""),
    ("gpu", "RTX 5090 D", "11096", "", r"(?i)v2"),
    ("gpu", "RTX 5090 D v2", "11096", r"(?i)v2", ""),
    ("gpu", "RX 9070", "11097", "", ""),
    ("gpu", "RX 9070 XT", "11098", "", ""),
    ("gpu", "RX 9070 GRE", "11099", "", ""),
)

#: CPU 家族页（sid 来自分类页侧栏实测）
CPU_FAMILIES = {
    "R5": "7275", "R7": "7274", "R9": "8259",
    "I5": "1079", "I7": "1584", "I9": "7313",
    "U5": "11028", "U7": "11029", "U9": "11027",
}

#: 商品名里出现这些词就不是「全新在售整卡」，整条剔除（宁可少采，不混二手/整机）
JUNK = ("二手", "租赁", "整机", "拆机", "矿卡", "服务器", "专业版", "涡轮服务器")

_CARD_RE = re.compile(r'<li data-follow-id="p\d+"[^>]*>(.*?)</li>', re.S)
_NAME_RE = re.compile(r'<h3><a[^>]*title="([^"]+)"')
_PRICE_RE = re.compile(r'price-type">(\d+)</b>')
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_RE = re.compile(r"\b(\d{3,5}[A-Z]{0,4}\d?)\b")


class ZolError(Exception):
    """抓取/解析失败（显式上抛；调用方决定是否记入 errors，绝不静默）. """


def _get(url: str, referer: str = "") -> str:
    """带 CookieJar + 浏览器头的 GET。

    实测（2026-11-19）：家族页第 2 页裸请求必 502，带第 1 页种下的 Cookie 与
    Referer 才 200 —— 所以必须复用同一个 opener，不能每次新建连接。
    """
    last = None
    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer
    for i in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with _opener.open(req, timeout=TIMEOUT) as r:  # noqa: S310 — 固定 https
                raw = r.read()
            return raw.decode("gb18030", "ignore")
        except Exception as e:  # noqa: BLE001 — 重试后仍失败则上抛
            last = e
            if i < RETRIES:
                time.sleep(1.5 * (i + 1))
    raise ZolError("%s: %s" % (url, type(last).__name__))


def fetch_family(sid: str) -> list[tuple[str, int]]:
    """抓一个 CPU 家族的全部分页（最多 MAX_PAGES 页），去重后返回。"""
    url = "%s/cpu/s%s/" % (BASE, sid)
    page = _get(url)
    out = parse_cards(page)
    m = re.search(r"<b>\d+</b>/(\d+)", page)
    total = min(int(m.group(1)), MAX_PAGES) if m else 1
    for n in range(2, total + 1):
        time.sleep(DELAY)
        out += parse_cards(_get("%s/%d.html" % (url.rstrip("/"), n), referer=url))
    seen, uniq = set(), []
    for name, price in out:
        k = (name, price)
        if k not in seen:
            seen.add(k)
            uniq.append((name, price))
    return uniq


def parse_cards(page: str) -> list[tuple[str, int]]:
    """从分类/系列页 HTML 抽 [(商品名, 参考价)]。没有价格的卡片跳过（不补 0）。"""
    out: list[tuple[str, int]] = []
    for m in _CARD_RE.finditer(page):
        block = m.group(1)
        nm = _NAME_RE.search(block)
        pr = _PRICE_RE.search(block)
        if not nm or not pr:
            continue
        name = _html.unescape(nm.group(1)).strip()
        price = int(pr.group(1))
        if not name or price <= 0:
            continue
        out.append((name, price))
    return out


def is_junk(name: str) -> bool:
    return any(w in name for w in JUNK)


def _pick(products, include: str, exclude: str):
    inc = re.compile(include) if include else None
    exc = re.compile(exclude) if exclude else None
    hit = []
    for name, price in products:
        if is_junk(name):
            continue
        if inc and not inc.search(name):
            continue
        if exc and exc.search(name):
            continue
        hit.append((name, price))
    return hit


def cpu_token(name: str) -> str:
    """商品名 → 型号 token：'AMD Ryzen 7 9800X3D' → '9800X3D'；'酷睿 Ultra 9 285' → '285'。

    实测坑：
      * 中文品牌词在型号**前面**（'Intel 酷睿 Ultra 9 285台…'），不能从第一个汉字处截断；
      * 卖点会直接粘在型号后面（'3700X8核心/16线程…'）：粘在汉字前的单个尾数字要丢掉，
        但纯数字型号（'285台…'）必须保留。
    """
    s = str(name or "").upper()
    s = re.sub(r"\b(AMD|INTEL)\b", " ", s)
    s = re.sub(r"(RYZEN|酷睿|ULTRA|CORE)", " ", s)
    # 型号后面紧贴汉字的尾数字（3700X8核…）：有字母在前才丢
    m = re.search(r"([0-9][A-Z0-9]*)([\u4e00-\u9fff])", s)
    if m:
        tok = m.group(1)
        if tok[-1].isdigit() and any(c.isalpha() for c in tok[:-1]):
            s = s[:m.start(1)] + tok[:-1] + s[m.end(1):]
    s = re.sub(r"[\u4e00-\u9fff]+", " ", s)      # 其余中文（描述）变空格
    s = re.sub(r"^\s*[579]\s*", " ", s)          # Ryzen 7 / Ultra 9 的档位
    s = re.sub(r"^\s*I[3579]\s*", " ", s)         # 酷睿i5
    s = re.sub(r"[^A-Z0-9]+", " ", s).strip()
    tok = s.split(" ")[0] if s else ""
    return tok


def model_token(label: str) -> str:
    """frocloud 型号 → token：'R79800X3D' → '9800X3D'；'i514600K' → '14600K'；'U9285K' → '285K'。"""
    s = str(label or "").upper().replace(" ", "").replace("-", "")
    for pre in ("R9", "R7", "R5", "I9", "I7", "I5", "U9", "U7", "U5"):
        if s.startswith(pre):
            return s[len(pre):]
    return s


def family_of(label: str) -> str:
    s = str(label or "").upper().replace(" ", "")
    for pre in ("R9", "R7", "R5", "I9", "I7", "I5", "U9", "U7", "U5"):
        if s.startswith(pre):
            return pre
    return ""


def collect(cpu_models: list[str]) -> dict:
    """抓 GPU 系列 + CPU 家族页，返回 {items, errors, fetched_at}。

    :param cpu_models: 要跟踪的 CPU 型号（来自 frocloud 当前在售集合）
    """
    items: dict[str, dict] = {}
    errors: list[str] = []
    family_cache: dict[str, list[tuple[str, int]]] = {}

    for _b, name, sid, inc, exc in GPU_CATALOG:
        try:
            page = _get("%s/vga/s%s/" % (BASE, sid))
            hit = _pick(parse_cards(page), inc, exc)
            if hit:
                best = min(hit, key=lambda x: x[1])
                items["gpu:" + name] = {"price": best[1], "samples": len(hit),
                                        "sample_name": best[0][:60], "sid": sid}
            else:
                errors.append("gpu:%s 无有效样本" % name)
        except ZolError as e:
            errors.append(str(e))
        time.sleep(DELAY)

    for label in cpu_models:
        fam = family_of(label)
        sid = CPU_FAMILIES.get(fam)
        if not sid:
            errors.append("cpu:%s 无家族映射" % label)
            continue
        if fam not in family_cache:
            try:
                family_cache[fam] = fetch_family(sid)
            except ZolError as e:
                errors.append(str(e))
                family_cache[fam] = []
            time.sleep(DELAY)
        token = model_token(label)
        hit = [(n, p) for n, p in family_cache[fam]
               if not is_junk(n) and cpu_token(n) == token]
        if hit:
            best = min(hit, key=lambda x: x[1])
            items["cpu:" + label] = {"price": best[1], "samples": len(hit),
                                     "sample_name": best[0][:60], "sid": sid}
        # 无样本不进 items（卡面写「今日无报价」），不编造

    return {"items": items, "errors": errors, "sources": len(GPU_CATALOG) + len(family_cache)}
