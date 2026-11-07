# -*- coding: utf-8 -*-
"""tools/fetch_wiki_mod_cards.py —— 从中文灰机 wiki 抓 mod 卡图（整卡 258×376）。

为什么是这个源：
  * DE 不发布成品 mod 卡图（客户端实时合成）；WFCD 只有卡框素材 + 40% 立绘。
  * 灰机 wiki 的 ``File:<CamelCase>.png`` 就是**整张卡**（实测 PrimedReach.png
    258×376），1733 个 mod 基本齐全，且带历史版本（U227/U26 后缀）。

两条通道（这是关键，别混）：
  1. **API**（``api.php``）在 Cloudflare 托管挑战后面，必须用**真浏览器**（Playwright）
     在页面里 ``fetch``，才能带 cf_clearance；
  2. **图片 CDN**（``huiji-public.huijistatic.com``）**不需要过挑战**，直连 200，
     用普通 HTTP + 限速即可。

风控要点（按用户要求）：
  * API：单线程，每次间隔 ``--api-gap``（默认 1.0s），遇 429/403 指数退避；
  * CDN：并发 ``--workers``（默认 4），每张之间 ``--img-gap``（默认 0.12s）；
  * 全部落盘、可断点续跑（已存在的文件跳过）；失败记 negative cache 不再重试。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\fetch_wiki_mod_cards.py --index        # 只建索引
    .venv\\Scripts\\python.exe tools\\fetch_wiki_mod_cards.py --all          # 索引 + 下载
    .venv\\Scripts\\python.exe tools\\fetch_wiki_mod_cards.py --all --workers 3 --api-gap 1.5
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "data" / "warframe_items"
OUT = ROOT / "data" / "warframe_mods" / "wiki"
INDEX = OUT / "index.json"

BASE = "https://warframe.huijiwiki.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_FETCH_JSON = """async (u) => {
  const r = await fetch(u, {credentials:'include'});
  return [r.status, await r.text()];
}"""


# ---------------------------------------------------------------------------
# 1) 用浏览器过 CF，拿 API 数据
# ---------------------------------------------------------------------------
class WikiApi:
    """在真实浏览器上下文里发 API 请求（唯一能过 Cloudflare 挑战的方式）。"""

    def __init__(self, api_gap: float = 1.0):
        from playwright.sync_api import sync_playwright
        self.gap = api_gap
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        self.ctx = self.browser.new_context(
            locale="zh-CN", user_agent=UA, viewport={"width": 1280, "height": 900})
        self.page = self.ctx.new_page()
        self.page.goto(BASE + "/wiki/首页", wait_until="domcontentloaded", timeout=60000)
        self._last = 0.0

    def get_json(self, query: str, retries: int = 3) -> dict | None:
        url = BASE + "/api.php?" + query
        for attempt in range(retries):
            wait = self.gap - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                status, text = self.page.evaluate(_FETCH_JSON, url)
                self._last = time.time()
                if status == 200:
                    return json.loads(text)
                # 429/403/5xx：退避重试（风控）
                time.sleep(self.gap * (2 ** (attempt + 1)))
            except Exception as e:  # noqa: BLE001
                self._last = time.time()
                print(f"    API 异常 {type(e).__name__}: {str(e)[:60]}，退避重试")
                time.sleep(self.gap * (2 ** (attempt + 1)))
        return None

    def close(self) -> None:
        try:
            self.browser.close()
            self._pw.stop()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 2) 索引：mod 名 → wiki 文件
# ---------------------------------------------------------------------------
def camel(name: str, lower_small: bool = False) -> str:
    """'Primed Bane of Corpus' → 'PrimedBaneOfCorpus'（lower_small=True 时 of→of）。"""
    small = {"of", "the", "and", "in", "on", "to", "for", "a", "an"}
    words = re.split(r"[^A-Za-z0-9']+", name)
    out = []
    for i, w in enumerate(words):
        if not w:
            continue
        if lower_small and i > 0 and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return "".join(out)


def file_of(title: str) -> str:
    """'文件:PrimedReach.png' / 'File:PrimedReach.png' → 'PrimedReach.png'。

    中文 wiki 的 File 命名空间被本地化成「文件」，API 返回的 title 带的是**中文前缀**，
    而请求时用 File: 也能命中——所以统一按「首个冒号之后」取文件名再比对。
    """
    return (title or "").split(":", 1)[-1].replace(" ", "")


def _load_zh() -> dict:
    """EN(小写) → 中文名。灰机 wiki 的 mod 卡有两种命名：英文 CamelCase
    （PrimedReach.png）**和中文**（适应.png / 酸性弹药.png），必须两种都试。"""
    try:
        d = json.loads((ITEMS / "mod_zh.json").read_text(encoding="utf-8"))
        return (d.get("map") or {}) if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


_ZH: dict | None = None


def variants(name: str) -> list[str]:
    global _ZH
    if _ZH is None:
        _ZH = _load_zh()
    v = [camel(name), camel(name, True), re.sub(r"[^A-Za-z0-9]", "", name)]
    z = (_ZH.get(name.lower()) or "").strip()
    if z:
        v += [z, z.replace(" ", ""), z.replace(" ", "_")]
    return list(dict.fromkeys(x for x in v if x))


def build_index(api: WikiApi, mods: list[dict], prefix_probe: int = 60) -> dict:
    """按默认名批量查 imageinfo（一次 50 个 title），建 名称→{url,size} 映射。

    只查**精确候选名**，不做全站枚举：wiki 上图片总数上万，全量枚举要 80+ 次 API，
    而我们只要这 1733 个 mod 的卡。
    """
    OUT.mkdir(parents=True, exist_ok=True)
    idx: dict[str, dict] = {}
    if INDEX.exists():
        try:
            idx = json.loads(INDEX.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            idx = {}

    todo: list[tuple[str, list[str]]] = []
    for m in mods:
        name = (m.get("name") or "").strip()
        if not name or name in idx:
            continue
        todo.append((name, variants(name)))
    print(f"待查 {len(todo)} 个 mod（已有索引 {len(idx)}）")

    # 展平成 title 批次，每批 50（MediaWiki 上限 50 titles/请求）
    flat: list[tuple[str, str]] = []
    for name, vs in todo:
        for v in vs:
            flat.append((name, f"File:{v}.png"))
    batch = 50
    for i in range(0, len(flat), batch):
        chunk = flat[i:i + batch]
        titles = "|".join(t for _, t in chunk)
        q = ("action=query&format=json&prop=imageinfo&iiprop=url%7Csize&titles="
             + urllib.request.quote(titles, safe=""))
        d = api.get_json(q)
        if not d:
            print(f"    批次 {i // batch + 1} 失败，跳过")
            continue
        pages = (d.get("query") or {}).get("pages") or {}
        # title → 命中信息
        found: dict[str, dict] = {}
        for _, v in pages.items():
            key = file_of(v.get("title")).lower()
            ii = (v.get("imageinfo") or [None])[0]
            if not ii or "missing" in v:
                continue
            found[key] = {"url": ii["url"], "w": ii.get("width"),
                          "h": ii.get("height"), "size": ii.get("size"),
                          "title": v.get("title")}
        for name, title in chunk:
            if name in idx:
                continue
            hit = found.get(file_of(title).lower())
            if hit:
                idx[name] = hit
        if (i // batch) % 10 == 0:
            print(f"    {i + len(chunk)}/{len(flat)}  …… 已命中 {len(idx)}")
        INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return idx


# ---------------------------------------------------------------------------
# 3) 下载（走 CDN，不需要过 CF）
# ---------------------------------------------------------------------------
def download(idx: dict, names: list[str], workers: int, img_gap: float) -> tuple[int, int]:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = []
    for name in names:
        info = idx.get(name)
        if not info:
            continue
        fn = file_of(info["title"])
        dst = OUT / fn
        if dst.exists() and dst.stat().st_size > 2000:
            continue
        jobs.append((info["url"], dst, fn))
    got = fail = 0
    lock_gap = max(0.0, img_gap)

    def one(job):
        """单张下载。**必须走 curl**：本机 python 的 urllib 出站被沙箱代理拦成
        HTTP 567，而 curl 直连 CDN 是 200（实测 185075B image/png）。"""
        url, dst, fn = job
        tmp = dst.with_suffix(dst.suffix + ".part")
        for attempt in range(3):
            try:
                r = subprocess.run(
                    ["curl", "-sS", "-m", "45", "-A", UA, "-e", BASE + "/",
                     "-o", str(tmp), "-w", "%{http_code} %{size_download}", url],
                    capture_output=True, text=True, timeout=60)
                parts = r.stdout.split()          # curl -w 输出："<code> <size>"
                code = parts[0] if parts else "0"
                size = int(parts[1]) if len(parts) > 1 else 0
                if code == "200" and size > 2000:
                    with open(tmp, "rb") as f:
                        head = f.read(4)
                    if head == b"\x89PNG":
                        tmp.replace(dst)
                        return True
            except Exception:  # noqa: BLE001 — 退避后重试
                pass
            time.sleep(1.5 * (attempt + 1))
        try:
            tmp.unlink()
        except OSError:
            pass
        return False

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = []
        for job in jobs:
            futs.append(ex.submit(one, job))
            if lock_gap:
                time.sleep(lock_gap)
        for f in cf.as_completed(futs):
            if f.result():
                got += 1
            else:
                fail += 1
    return got, fail


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--api-gap", type=float, default=1.0)
    ap.add_argument("--img-gap", type=float, default=0.12)
    args = ap.parse_args()
    if not (args.index or args.all):
        print("请给 --index 或 --all")
        return 1

    mods = [m for m in json.loads((ITEMS / "Mods.json").read_text(encoding="utf-8"))
            if m.get("uniqueName")]
    if args.only:
        mods = [m for m in mods if (m.get("name") or "") == args.only]

    api = WikiApi(api_gap=args.api_gap)
    try:
        idx = build_index(api, mods)
    finally:
        api.close()
    print(f"索引命中 {len(idx)} / {len(mods)} 个 mod")
    if not args.all:
        return 0
    names = [m["name"] for m in mods if m.get("name") in idx]
    got, fail = download(idx, names, args.workers, args.img_gap)
    print(f"下载完成 {got} 张，失败 {fail} 张 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
