# -*- coding: utf-8 -*-
"""原著核对工具：把 skill_book 里每条「原著：第N章「…」」拿去全文核一遍。

为什么需要
----------
skill_book 初版的原著章回有相当一部分是**推着写的**（按剧情大概位置填章号），
这在「必须参考原著」这条红线下是不可接受的：章号错了比不写更糟，因为读者一翻
就发现对不上。所以要有机器手段把每条引用验成三态：

    VERIFIED   技名在正文出现，且现有章号 == 真实命中章号（可用）
    WRONG_REF  技名在正文出现，但现有章号不在命中里（章号错，需改）
    NOT_IN_NOVEL 技名全文未出现（游戏自设，应写「原著无载」）

正文语料
--------
默认读 ``C:/Users/L/AppData/Local/Temp/chaps``（2133 个章节 html，上一轮抓的）。
文件编号与章号**不同序**（0143.html 是第一百二十二章），故必须从 title 取章号。

用法
----
    python src/plugins/doupo/_verify_novel.py            # 报告三态统计
    python src/plugins/doupo/_verify_novel.py --json out.json
    python src/plugins/doupo/_verify_novel.py --pick 狂狮怒罡   # 单条看全部命中
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import skill_book as book  # noqa: E402

CORPUS = os.environ.get("DOUPO_NOVEL_DIR", "C:/Users/L/AppData/Local/Temp/chaps")
CACHE = os.path.join(tempfile.gettempdir(), "doupo_novel_index.json")

#: 中文数字字符表 —— 必须用映射，不能用 str.index：
#: 一旦把「两」插进「二」后面，"三" 的 index 就会整体错位（实测把 1623 算成 1724）。
_CN_MAP = {
    "零": 0, "〇": 0,
    "一": 1, "壹": 1,
    "二": 2, "两": 2, "贰": 2,
    "三": 3, "叁": 3,
    "四": 4, "肆": 4,
    "五": 5, "伍": 5,
    "六": 6, "陆": 6,
    "七": 7, "柒": 7,
    "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_CN = "".join(_CN_MAP)
_NUM = r"[零一两二三四五六七八九十百千\d]+"


def _parse_num(s: str) -> int:
    """中文章节数字 → int。「一百二十二」/「两百」/「1623」都能吃。

    走「逐位累加」：普通数字先攒进 num，遇到 十/百/千 就把 num 乘上单位加进 section。
    """
    s = str(s).strip().replace("第", "").replace("章", "")
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    section = num = 0
    for ch in s:
        if ch in _CN_MAP:
            num = _CN_MAP[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        elif ch == "千":
            section += (num or 1) * 1000
            num = 0
        else:
            return 0
    return section + num


def build_index(force: bool = False) -> dict:
    """把 2133 章拼成一个可搜索的索引：{text, chapters:[{no,title,start}]}。"""
    if not force and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    if not os.path.isdir(CORPUS):
        raise SystemExit("原著语料不存在：%s（设 DOUPO_NOVEL_DIR 指到章节目录）" % CORPUS)

    files = sorted(f for f in os.listdir(CORPUS) if f.endswith(".html"))
    pieces, chapters, pos = [], [], 0
    seen_title: set[str] = set()
    seq = 0
    skipped = 0
    for fn in files:
        raw = open(os.path.join(CORPUS, fn), encoding="utf-8", errors="replace").read()
        m = re.search(r"<title>(.*?)</title>", raw, re.S)
        title = re.sub(r"\s+", "", m.group(1)) if m else ""
        title = title.replace("斗破苍穹", "").strip()
        body = re.sub(r"<script.*?</script>", "", raw, flags=re.S)
        body = re.sub(r"<[^>]+>", "", body)
        body = re.sub(r"\s+", "", body)
        # 该站每页开头会重复两三遍「章节标题 + 斗破苍穹 + 必读推荐 + 上一页:本书目录:下一页」，
        # 不切掉的话搜出来的"原句"全是导航串（实测 28 条标题命中全中招）。
        # 该站用『X』把单字包起来做反爬（"炼『药』师""白『色』"），
        # 不改会打断技名匹配（"狂狮『吟』"就搜不到"狂狮吟"）。原文本来没有『』，
        # 所以这里还原成原字，引用时也就不用带这层壳。
        body = re.sub(r"『(.)』", r"\1", body)
        body = body.replace("斗破苍穹", "").replace("必读推荐", "")
        body = re.sub(r"(?:上一页|第一页|下一页|本书目录)[:：]?", "", body)
        body = re.sub(r"关于" + re.escape(title) + r"[！!？?。]?", "", body)
        # 开头/结尾残留的重复标题一并剥掉（可能就是标题本身，也可能是标题+导航）
        while body.startswith(title):
            body = body[len(title):]
        while body.endswith(title):
            body = body[:-len(title)]
        # ⚠️ 该站点是「多卷制」：章号在每卷内重置（第十章出现 4 次），不能当键。
        # 也不能直接拿文件序号当章号（有重复抓取页）。故：按文件顺序去重后连续编号。
        # 目录里有「空壳页」：标题与正文页只差一个字（第五十六章迦南学院 / 第五十六章迦南院），
        # 按标题去重去不掉，得按正文长度判定（清洗后不足 200 字的一律是导航壳）。
        if len(body) < 200:
            skipped += 1
            continue
        if title in seen_title:
            continue
        seen_title.add(title)
        seq += 1
        mnum = re.search(r"第(" + _NUM + r")章", title)
        chapters.append({
            "no": seq,                                     # 去重后的连续章号（引用用这个）
            "vol_no": _parse_num(mnum.group(1)) if mnum else 0,  # 原文卷内章号（仅供核对）
            "title": title, "start": pos, "file": fn,
        })
        pieces.append(body)
        pos += len(body)
    data = {"text": "".join(pieces), "chapters": chapters, "skipped_empty": skipped}
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return data


def split_title(title: str) -> tuple[str, str]:
    """'第一百零三章假期' → ('第一百零三章', '假期')。"""
    m = re.match(r"^(第" + _NUM + r"章)(.*)$", str(title))
    return (m.group(1), m.group(2)) if m else ("", str(title))


def int2cn(n: int) -> str:
    """int → 规范中文数字（1→一 / 10→十 / 120→一百二十 / 1623→一千六百二十三）。

    为什么需要：语料里不少 title 的章号**缺字**（"第百二十章""第百四十章"），
    照抄进引用读者搜不到。好在缺的只是「一」这类前缀，数值解析是对的，
    所以用 vol_no 重新生成规范写法即可。
    """
    n = int(n)
    if n <= 0:
        return str(n)
    d = "零一二三四五六七八九"
    u = ["", "十", "百", "千"]
    raw = str(n)
    out, zero = "", False
    for i, ch in enumerate(raw):
        dig = int(ch)
        pos = len(raw) - i - 1
        if dig == 0:
            zero = True
            continue
        if zero and out:
            out += "零"
        zero = False
        if pos == 1 and dig == 1 and not out:
            out += "十"          # 10~19 省略「一」
        else:
            out += d[dig] + u[pos]
    return out


def cite(title: str, vol_no: int = 0) -> str:
    """标准引用串：`第一百二十章「以物换物」`。

    为什么不写「全书第 N 章」：该站点是多卷制，章号每卷重置；通行版全书章号
    又与本站不同，硬写一个数字必然是猜的。用**原著自己的章节标题**最稳——
    读者照标题能直接搜到，也经得起翻书核对。
    """
    head, tail = split_title(title)
    n = vol_no or _parse_num(head)
    if not n:
        return str(title)
    return "第%s章「%s」" % (int2cn(n), tail)


def chapter_at(idx: dict, offset: int) -> dict:
    """某个字符偏移落在哪一章。"""
    lo, hi = 0, len(idx["chapters"]) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if idx["chapters"][mid]["start"] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return idx["chapters"][lo]


def variants(name: str) -> list[str]:
    """技名的可搜变体：原名 / 去标点后的最长汉字段。"""
    out = [name]
    if "·" in name or "：" in name or ":" in name:
        segs = [s for s in re.split(r"[·：:]", name) if len(s) >= 2]
        for s in segs:
            if s not in out:
                out.append(s)
    return out


def hits(idx: dict, name: str, limit: int = 12) -> list[dict]:
    """全文搜技名，返回命中（章号 + 标题 + 上下文）。"""
    text = idx["text"]
    found, seen = [], set()
    for v in variants(name):
        start = 0
        while True:
            p = text.find(v, start)
            if p < 0:
                break
            start = p + 1
            ch = chapter_at(idx, p)
            key = (ch["no"], p - ch["start"])
            if ch["no"] in seen:
                continue
            seen.add(ch["no"])
            ctx = text[max(0, p - 40):p + len(v) + 60]
            found.append({"no": ch["no"], "title": ch["title"],
                          "cite": cite(ch["title"], ch.get("vol_no", 0)),
                          "ctx": ctx, "hit": v})
            if len(found) >= limit:
                return found
    return found


def ref_title(novel: str) -> str:
    """从 novel 字段里取现有章节标题（引号内那一段）。"""
    m = re.search(r"「(.+?)」", str(novel))
    return m.group(1).strip() if m else ""


def ref_no(novel: str) -> int:
    """从 novel 字段里取现有章号（仅作展示，不作为判定依据）。"""
    m = re.search(r"第\s*(" + _NUM + r")\s*章", str(novel))
    return _parse_num(m.group(1)) if m else 0


def _norm_title(s: str) -> str:
    """去掉站方屏蔽符后的标题正文，用于比对。"""
    return str(s).replace("**", "").replace("『", "").replace("』", "")


def _look_title(tail: str, by_tail: dict) -> list[dict]:
    """按标题查章，容忍站方用 `**` 吞字（「**游身尺」↔「六合游身尺」）。"""
    k = _norm_title(tail)
    if k in by_tail:
        return by_tail[k]
    for kk, v in by_tail.items():
        if kk and (kk in k or k in kk) and abs(len(kk) - len(k)) <= 4:
            return v
    return []


_NOVEL_RE = re.compile(r"^(第" + _NUM + r"章)「([^」]+)」 —— (.+)$")
ABSENT = "原著无载 · 游戏自设"


def audit(idx: dict) -> list[dict]:
    """逐条核 novel 字段本身（不是核技名搜不搜得到）。

    三态：
        VERIFIED  标题能在语料里找到，且引文连续存在于该章
        BROKEN    格式不对 / 标题不存在 / 引文查无 / 引文跑到别的章
        ABSENT    明写「原著无载 · 游戏自设」
    """
    text = idx["text"]
    by_tail: dict[str, list[dict]] = {}
    for c in idx["chapters"]:
        _, tail = split_title(c["title"])
        by_tail.setdefault(_norm_title(tail), []).append(c)

    rows = []
    for r in book.SKILL_BOOK.values():
        nv = str(r.get("novel") or "")
        row = {"name": r["name"], "novel": nv}
        if nv == ABSENT:
            rows.append(dict(row, state="ABSENT", why=""))
            continue
        m = _NOVEL_RE.match(nv)
        if not m:
            rows.append(dict(row, state="BROKEN", why="格式不对"))
            continue
        head, tail, quote = m.groups()
        cs = _look_title(tail, by_tail)
        if not cs:
            rows.append(dict(row, state="BROKEN", why="章节标题不存在"))
            continue
        p = text.find(quote)
        if p < 0:
            rows.append(dict(row, state="BROKEN", why="引文正文查无"))
            continue
        if chapter_at(idx, p)["no"] not in {c["no"] for c in cs}:
            rows.append(dict(row, state="BROKEN", why="引文不在所引章内"))
            continue
        rows.append(dict(row, state="VERIFIED", why=""))
    return rows


def main(argv) -> int:
    idx = build_index("--rebuild" in argv)
    print("语料：%d 章（去空壳后连续编号，丢弃空壳 %d 页）/ %d 字"
          % (len(idx["chapters"]), idx.get("skipped_empty", 0), len(idx["text"])))

    if "--pick" in argv:
        name = argv[argv.index("--pick") + 1]
        rec = book.SKILL_BOOK.get(name)
        print("现有原著：", (rec or {}).get("novel"))
        for h in hits(idx, name, limit=40):
            print("  第%d章 %s ｜ …%s…" % (h["no"], h["title"][:26], h["ctx"]))
        return 0

    rows = audit(idx)
    cnt = {}
    for r in rows:
        cnt[r["state"]] = cnt.get(r["state"], 0) + 1
    print("三态统计：", cnt)

    broken = [r for r in rows if r["state"] == "BROKEN"]
    print("\n不合格 %d 条：" % len(broken))
    for r in broken:
        print("  %-14s %s ｜ %s" % (r["name"], r["why"], r["novel"][:60]))

    print("\n原著无载（游戏自设）%d 条：" % cnt.get("ABSENT", 0))
    absent = [r["name"] for r in rows if r["state"] == "ABSENT"]
    for i in range(0, len(absent), 6):
        print("  " + "、".join(absent[i:i + 6]))

    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("\n已写", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
