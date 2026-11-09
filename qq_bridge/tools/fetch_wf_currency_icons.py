# -*- coding: utf-8 -*-
"""补齐 / 校验 Warframe 市价卡三种货币的**真实游戏内图标**。

用法（在 qq_bridge 根目录）：
    python tools/fetch_wf_currency_icons.py           # 联网补齐三枚
    python tools/fetch_wf_currency_icons.py --check  # 只校验本地资产，缺图 exit 1

数据源（按权威顺序）：
  1. **灰机 WARFRAME 中文维基 Text Icons**（``warframe.huijiwiki.com`` MediaWiki API →
     ``huiji-public.huijistatic.com``）：``Platinum64.png`` / ``Credits64.png`` /
     ``PrimeBucks.png``（实际重定向为 ``OrokinDucats.png``）。这三枚是游戏内货币图标的
     官方转录，64×64 RGBA，**三枚风格统一**，且白金图标齐全。
     官方 wiki.warframe.com 与 fandom 在本机全通道 Cloudflare 403，故用同源中文维基镜像。
  2. **DE 官方 Public Export**（``index_en.txt.lzma`` → ``ExportManifest.json!<hash>`` →
     ``textureLocation``）：与游戏客户端同源，提供 ``Ducat.png`` /
     ``CorpusCreditCardLow.png``，仅作灰机失败时的降级与交叉验证；DE 导出里
     **没有独立白金货币图标**（``platinum`` 命中的全是 Relic Projection / Specter 道具）。

本机 Python HTTP 栈访问灰机 CDN 会被 567 拦截（curl / 浏览器正常，TLS 指纹差异），
因此 ``_fetch_bytes`` 在拿到非预期响应时**显式打印并改用 curl 直连**，不静默吞错。

产物裁掉透明边后**等比 contain** 进 128×128 RGBA（不拉伸变形），写入
``pic/templates/assets/wf_currency/``。任一枚缺失/损坏即 exit 1，可直接当交付闸门。
"""
from __future__ import annotations

import argparse
import json
import lzma
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates" / "assets" / "wf_currency"
KINDS = ("platinum", "credits", "ducats")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SIZE = 128

DE_BASE = "https://content.warframe.com/PublicExport/"
HUIJI_API = "https://warframe.huijiwiki.com/api.php"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8"}

# API 查得的直链（API 不可达时用）；路径 hash 见 assets/wf_currency/README.md
HUIJI_FILES = {"platinum": "Platinum64.png", "credits": "Credits64.png", "ducats": "PrimeBucks.png"}
HUIJI_URLS = {
    "platinum": "https://huiji-public.huijistatic.com/warframe/uploads/1/15/Platinum64.png",
    "credits": "https://huiji-public.huijistatic.com/warframe/uploads/b/b1/Credits64.png",
    "ducats": "https://huiji-public.huijistatic.com/warframe/uploads/d/d5/OrokinDucats.png",
}

DE_LEAF = {"Ducat.png": "ducats", "CorpusCreditCardLow.png": "credits"}
DE_FALLBACK = {
    "ducats": "Lotus/Interface/Icons/StoreIcons/Currency/Ducat.png!00_-49OhiZUiif4NhKrOS8EpA",
    "credits": "Lotus/Interface/Icons/StoreIcons/Currency/CorpusCreditCardLow.png!00_swGu2M5VfbKis+r+odexwA",
}


def _curl(url: str) -> bytes:
    """Python HTTP 栈被拦时改走 curl（Windows curl.exe / Linux curl 均可）。"""
    r = subprocess.run(
        ["curl", "-L", "-sS", "--max-time", "30", "-A", UA["User-Agent"], url],
        capture_output=True, check=False)
    if r.returncode != 0:
        raise RuntimeError("curl rc=%d %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[:200]))
    return r.stdout


def _fetch_bytes(cli: httpx.Client, url: str, want_png: bool = True,
                 want_json: bool = False) -> bytes:
    """httpx 优先；被拦 / 返回非预期内容（如 403 HTML）时显式改道 curl，不静默降级。"""
    def ok(data: bytes) -> bool:
        if not data:
            return False
        if want_png:
            return data[:8] == PNG_MAGIC
        if want_json:
            return data.lstrip()[:1] == b"{"
        return True

    try:
        r = cli.get(url, timeout=30.0)
        data = r.content or b""
        if ok(data):
            return data
        why = "HTTP %s %s（非预期内容）" % (r.status_code, r.headers.get("content-type"))
    except Exception as e:  # noqa: BLE001 — 显式记录原因后改道
        why = "%s: %s" % (type(e).__name__, e)
    print("    [curl 改道] httpx 拿不到预期响应（%s）" % why)
    data = _curl(url)
    if not ok(data):
        raise RuntimeError("curl 也未返回预期内容（%d bytes）" % len(data))
    return data


def _huiji_urls(cli: httpx.Client) -> dict[str, str]:
    """从灰机 MediaWiki API 动态解析三枚图标直链；失败回落已知直链。"""
    out: dict[str, str] = {}
    for kind, fn in HUIJI_FILES.items():
        u = HUIJI_API + "?action=query&titles=File%3A" + fn + "&prop=imageinfo&iiprop=url&format=json"
        try:
            data = json.loads(_fetch_bytes(cli, u, want_png=False, want_json=True))
            pages = data["query"]["pages"]
            out[kind] = next(iter(pages.values()))["imageinfo"][0]["url"]
        except Exception as e:  # noqa: BLE001 — 显式降级到 API 查得的固定直链
            print("  [WARN] %-8s API 解析失败（%s: %s），用已知直链" % (kind, type(e).__name__, e))
    return {**HUIJI_URLS, **out}


def _unlzma(blob: bytes) -> bytes:
    props = blob[0]
    ds = int.from_bytes(blob[1:5], "little")
    us = int.from_bytes(blob[5:13], "little")
    lc, rest = props % 9, props // 9
    lp, pb = rest % 5, rest // 5
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
        {"id": lzma.FILTER_LZMA1, "dict_size": ds, "lc": lc, "lp": lp, "pb": pb}])
    return dec.decompress(blob[13:], max_length=us)


def _de_paths(cli: httpx.Client) -> dict[str, str]:
    """DE 官方 manifest 动态解析（只作为灰机失败时的降级源）。"""
    blob = _fetch_bytes(cli, DE_BASE + "index_en.txt.lzma", want_png=False)
    txt = _unlzma(blob).decode("utf-8")
    entry = next(ln.strip() for ln in txt.splitlines()
                 if ln.strip().startswith("ExportManifest.json!"))
    man = json.loads(_fetch_bytes(cli, DE_BASE + "Manifest/" + entry,
                                  want_png=False, want_json=True))["Manifest"]
    out: dict[str, str] = {}
    for e in man:
        tl = (e.get("textureLocation") or "").lstrip("/")
        leaf = tl.split("/")[-1].split("!")[0]
        if leaf in DE_LEAF:
            out[DE_LEAF[leaf]] = tl
    return out


def _save(kind: str, data: bytes) -> bool:
    """裁透明边 → 等比 contain 进 128×128 RGBA（禁止拉伸变形）。"""
    if data[:8] != PNG_MAGIC:
        return False
    im = Image.open(BytesIO(data)).convert("RGBA")
    bb = im.getchannel("A").getbbox()
    if bb:
        im = im.crop(bb)
    w, h = im.size
    s = min(float(SIZE) / max(1, w), float(SIZE) / max(1, h))
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((SIZE - nw) // 2, (SIZE - nh) // 2))
    ASSETS.mkdir(parents=True, exist_ok=True)
    canvas.save(ASSETS / (kind + ".png"), format="PNG", optimize=True)
    return True


def _local_ok(kind: str) -> bool:
    try:
        return Image.open(ASSETS / (kind + ".png")).size == (SIZE, SIZE)
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="只校验本地三枚资产，缺图 exit 1")
    args = ap.parse_args(argv)

    if args.check:
        miss = [k for k in KINDS if not _local_ok(k)]
        for k in KINDS:
            print("  [%s] %-8s %s" % ("OK" if k not in miss else "MISS", k, ASSETS / (k + ".png")))
        print("货币图标闸门：%s" % ("PASS 3/3" if not miss else "FAIL 缺 " + ", ".join(miss)))
        return 1 if miss else 0

    ok: dict[str, str] = {}
    with httpx.Client(headers=UA, follow_redirects=True, timeout=60.0) as cli:
        print("① 灰机 Wiki Text Icons（游戏内货币图标同源）")
        for kind, url in _huiji_urls(cli).items():
            try:
                if _save(kind, _fetch_bytes(cli, url)):
                    ok[kind] = "灰机 " + url.rsplit("/", 1)[-1]
                    print("  [OK] %-8s <- %s" % (kind, url))
                else:
                    print("  [FAIL] %-8s 返回非 PNG" % kind)
            except Exception as e:  # noqa: BLE001 — 显式报出，继续其它货币
                print("  [FAIL] %-8s %s: %s" % (kind, type(e).__name__, e))

        pending = [k for k in KINDS if k not in ok]
        if pending:
            print("② DE 官方 Public Export 降级补齐（仅 credits / ducats）")
            try:
                paths = _de_paths(cli)
                print("   manifest 解析到 %d 枚货币图标" % len(paths))
            except Exception as e:  # noqa: BLE001
                print("   manifest 解析失败（%s: %s），用已知 hash 兜底" % (type(e).__name__, e))
                paths = {}
            for kind, tl in {**DE_FALLBACK, **paths}.items():
                if kind not in pending:
                    continue
                try:
                    if _save(kind, _fetch_bytes(cli, DE_BASE + tl)):
                        ok[kind] = "DE 官方 " + tl.split("/")[-1].split("!")[0]
                        print("  [OK] %-8s <- DE %s" % (kind, tl))
                except Exception as e:  # noqa: BLE001
                    print("  [FAIL] %-8s DE %s: %s" % (kind, type(e).__name__, e))

    miss = [k for k in KINDS if not _local_ok(k)]
    print("\n结果：", json.dumps(ok, ensure_ascii=False))
    if miss:
        print("闸门 FAIL：以下货币缺真实图标 -> %s" % ", ".join(miss))
        return 1
    print("闸门 PASS：3/3 真实游戏内图标齐备（platinum / credits / ducats）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
