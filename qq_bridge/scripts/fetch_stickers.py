# -*- coding: utf-8 -*-
"""从 ChineseBQB 开源数据集拉取「抽象/搞笑」表情包到本地库。

数据来源：https://github.com/zhaoolee/ChineseBQB （CC 开源，图片直链）
通过 jsDelivr CDN 下载（国内可达）。筛选抽象/搞笑/沙雕/猫娘相关分类，
随机取样 N 张写入 <项目根>/data/stickers/。

用法：
    python scripts/fetch_stickers.py [数量] [refresh]
    refresh : 重新拉取索引 JSON（默认复用已缓存的 _idx.json）
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STICKER_DIR = os.path.join(ROOT, "data", "stickers")
os.makedirs(STICKER_DIR, exist_ok=True)

# 抽象 / 搞笑 / 沙雕 / 猫娘相关 分类关键词
KW = [
    "Funny", "沙雕", "斗图", "土嗨", "HappyDuck", "Penguin", "Chicken",
    "开心鸭", "滑稽", "兄贵", "Cat_是喵", "Dog_狗", "TuHi",
    "WhiteVillain", "BeiFang", "TomAndJerry", "Golden_Curator", "KeNan",
]

N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
REFRESH = len(sys.argv) > 2 and sys.argv[2] == "refresh"

IDX = os.path.join(STICKER_DIR, "_idx.json")
if REFRESH or not os.path.exists(IDX) or os.path.getsize(IDX) < 50:
    print("下载索引…")
    urllib.request.urlretrieve(
        "https://cdn.jsdelivr.net/gh/zhaoolee/ChineseBQB@master/chinesebqb_github.json", IDX)

d = json.load(open(IDX, encoding="utf-8"))
arr = d.get("data") or []
sel = [x for x in arr if any(k in (x.get("category") or "") for k in KW)]
random.seed()
random.shuffle(sel)
chosen = sel[:N]
print("候选 %d 张，本批下载 %d 张" % (len(sel), len(chosen)))

ok = 0
for it in chosen:
    base = "https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/"
    candidates = [
        "https://raw.gitmirror.com/zhaoolee/ChineseBQB/master/",
        "https://ghproxy.net/https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/",
        "https://gitee.com/dsym/ChineseBQB/raw/master/",
    ]
    raw = it.get("url") or ""
    if not raw.startswith(base):
        continue
    rel = raw[len(base):]
    fn = urllib.parse.unquote(rel).rstrip("/").split("/")[-1]
    dst = os.path.join(STICKER_DIR, fn)
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        ok += 1
        continue
    saved = False
    for cand in candidates:
        url = cand + urllib.parse.quote(rel, safe=":/")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) < 50:
                continue
            open(dst, "wb").write(data)
            ok += 1
            saved = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not saved:
        print("失败", fn)
print("完成：本地就绪 %d 张 -> %s" % (ok, STICKER_DIR))
