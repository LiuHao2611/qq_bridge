# -*- coding: utf-8 -*-
"""门派异火归属门禁：每条 ``flames`` 必须有可核验的原著章节证据。

为什么需要（2026-11-15）
------------------------
原著里异火极其稀有，全大陆二十三种、多半被少数远古种族/强者持有。
但 ``factions.py`` 初版给 25 个门派里的 19 个都挂了 ``flames``，其中
8 朵是「云岚宗·风灵青焰」这类自造镇派灵焰，另有「幽冥毒火」「阴阳双炎」
「八荒破灭焱」三个名字原著正文全文 0 命中，丹塔那条还把原著明确写的
「无主之物」误写成「丹塔所有」。这违背「必须参考原著」的红线。

所以三条硬规则（任何一条不过就 exit 1）：
  1. 每条 flames 至少带一个 ``第X章「标题」`` 引用，且该引用必须能在
     原著正文语料的章节表里逐字找到（章号 + 标题都对得上）；
  2. 禁止 ``[游戏原创]`` / ``游戏自设`` / ``镇派灵焰`` 等自造标记；
  3. 条目里出现的异火名必须在原著正文里真实出现（防二手资料错名）。

正文语料沿用 ``_verify_novel.CORPUS``（默认 2133 章 html，去重后 1608 章）。
语料缺失会**显式报错**，绝不静默通过 —— 没有原著就没法验收。

用法
----
    python src/plugins/doupo/_verify_flames.py              # 校验真实数据
    python src/plugins/doupo/_verify_flames.py --selftest   # 反例注入自测
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import factions  # noqa: E402
import _verify_novel as vn  # noqa: E402

#: 引用串：第X章「标题」
_CITE_RE = re.compile(r"第[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十百千\d]+章「[^」]+」")
#: 自造标记 —— 出现即视为把游戏设定冒充原著
_FAKE_MARKS = ("[游戏原创]", "游戏自设", "镇派灵焰", "游戏原创", "本源焰", "本源火")
#: 需要在正文里核实存在的异火名（含原著正确写法，不含二手错名）
_FLAME_WORDS = (
    "青莲地心火", "陨落心炎", "三千焱炎火", "骨灵冷火", "九龙雷罡火",
    "荒破灭焱", "九幽金祖火", "红莲业火", "火云水炎", "火山石焰",
    "金帝焚天炎", "虚无吞炎", "化生火", "净莲妖火", "生灵之焱",
    "九幽风炎", "海心焰", "龟灵地火", "帝炎",
)
#: 已知的二手错名 —— 正文 0 命中，出现即报错（这次真实犯过的错）
_WRONG_NAMES = ("八荒破灭焱", "幽冥毒火", "阴阳双炎", "风灵青焰", "丹火灵焰",
                "地火炉焰", "雷霄灵焰", "玄冰灵焰", "陨星焰", "虚空龙焰", "冥火")


def valid_cites(idx: dict) -> set[str]:
    """原著语料里所有合法的标准引用串。"""
    return {vn.cite(ch["title"], ch.get("vol_no", 0)) for ch in idx["chapters"]}


def problems(idx: dict | None = None) -> list[str]:
    """返回全部违规说明；空列表 = 通过。idx 可注入以便 selftest。"""
    idx = idx if idx is not None else vn.build_index()
    cites = valid_cites(idx)
    text = idx["text"]
    bad: list[str] = []
    for key, d in factions.FACTIONS.items():
        name = d.get("name", key)
        for e in (d.get("flames") or []):
            tag = f"{name}: {e[:38]}…"
            refs = _CITE_RE.findall(e)
            if not refs:
                bad.append(f"缺少原著章节引用 | {tag}")
            for r in refs:
                if r not in cites:
                    bad.append(f"引用章号/标题在原著语料中不存在: {r} | {tag}")
            for m in _FAKE_MARKS:
                if m in e:
                    bad.append(f"游戏自造设定混入 flames: {m} | {tag}")
            for w in _WRONG_NAMES:
                if w in e:
                    bad.append(f"原著正文 0 命中的错名: {w} | {tag}")
            for w in _FLAME_WORDS:
                if w in e and w not in text:
                    bad.append(f"异火名未在原著正文出现: {w} | {tag}")
    return bad


def selftest() -> int:
    """拿这次真实犯过的错去试：门禁必须报错，改回来必须通过。"""
    idx = vn.build_index()
    real = problems(idx)
    if real:
        print("SELFTEST FAIL: 真实数据本身就违规，先修数据")
        for p in real[:5]:
            print("   ", p)
        return 1

    orig = {k: list(v.get("flames") or []) for k, v in factions.FACTIONS.items()}
    try:
        # 反例 1：自造镇派灵焰重新混进 flames（这次真实的错）
        factions.FACTIONS["yunlanzong"]["flames"] = [
            "云岚宗·风灵青焰（游戏自设镇派灵焰）[游戏原创]"]
        p = problems(idx)
        ok1 = any("游戏自造设定混入" in x for x in p)
        print(("  PASS" if ok1 else "  FAIL"), "反例1 自造镇派灵焰 → 必须报错")

        # 反例 2：二手错名（原著 0 命中）
        factions.FACTIONS["duzong"]["flames"] = ["幽冥毒火(20) / 阴阳双炎(21) [S7]"]
        p = problems(idx)
        ok2 = any("缺少原著章节引用" in x or "错名" in x for x in p)
        print(("  PASS" if ok2 else "  FAIL"), "反例2 幽冥毒火/阴阳双炎 → 必须报错")

        # 反例 3：编造章号
        factions.FACTIONS["guzu"]["flames"] = ["金帝焚天炎(4)：古族传承（第九千九百九十九章「不存在的章」）[原著]"]
        p = problems(idx)
        ok3 = any("不存在" in x for x in p)
        print(("  PASS" if ok3 else "  FAIL"), "反例3 编造章号 → 必须报错")

        # 反例 4：把「丹塔所有」这个错误归属放回去（应因缺引用被拦）
        factions.FACTIONS["danta"]["flames"] = ["三千焱炎火(9)：星域禁锢，丹塔所有 [S4][S5]"]
        p = problems(idx)
        ok4 = any("缺少原著章节引用" in x for x in p)
        print(("  PASS" if ok4 else "  FAIL"), "反例4 无证据的『丹塔所有』 → 必须报错")

        ok = ok1 and ok2 and ok3 and ok4
        print("\nSELFTEST:", "PASS (4/4 反例全被拦)" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        for k, v in orig.items():
            factions.FACTIONS[k]["flames"] = v


def main() -> int:
    if not os.path.isdir(vn.CORPUS):
        print("原著语料不存在：%s" % vn.CORPUS)
        print("设 DOUPO_NOVEL_DIR 指到章节目录；没有原著不允许验收通过。")
        return 1
    if "--selftest" in sys.argv:
        return selftest()
    idx = vn.build_index()
    print("原著正文: %d 章 / %d 字" % (len(idx["chapters"]), len(idx["text"])))
    print("门派数: %d, 有异火归属: %d, 条目数: %d" % (
        len(factions.FACTIONS),
        sum(1 for d in factions.FACTIONS.values() if d.get("flames")),
        sum(len(d.get("flames") or []) for d in factions.FACTIONS.values())))
    bad = problems(idx)
    if bad:
        print("\nFLAMES_GATE FAIL (%d):" % len(bad))
        for p in bad:
            print("  -", p)
        return 1
    print("\nFLAMES_GATE PASS: 每条门派异火归属都有可核验的原著章节证据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
