# -*- coding: utf-8 -*-
"""tools/check_skill_catalog.py —— 斗技图鉴「什么才算斗技」交付闸门。

用户 2026-11-15：「我当前宗门这个很明显大部分都不是斗技，丹会都叫斗技？？？
什么乱七八糟的，你重新梳理一遍」。截图里炼药师公会的四槽是
「炼药术与丹方 / 炼药师品阶考核 / 千焰剑罡 / 丹会」—— 3 个是炼药事务，
却被写成「普通阶 斗气 20%」「特殊阶 斗气 50%」还能打伤害。

本闸门把「重新梳理」变成机器可拦的不变量：
  1. 非斗技（炼药/考核/大会/表决/制度/事件/阵法泛称/体质/典籍）绝不进斗技池；
  2. 三处清单（skill_book.NON_SKILL_NAMES / FACTION_LORE / factions.LORE_NAMES）完全对齐；
  3. 每个宗门的运行时斗技池 >= 4 条（四槽），且全部来自 SKILL_BOOK；
  4. 兜底路径（factions.skills / special_skills）也不得再冒出典故；
  5. 每条斗技要么有「第X章「标题」」原著章句，要么显式标「原著无载 · 游戏自设」，
     不许拿编造章号冒充原著；
  6. 真实存档四槽自动补满、且不含任何非斗技。

用法：
    python tools/check_skill_catalog.py             # 报告，不中断
    python tools/check_skill_catalog.py --check     # 违规 exit 1（交付前必跑）
    python tools/check_skill_catalog.py --selftest  # 反例注入：旧错必须被拦
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# doupo/__init__.py 里有 `from src.game_mode import ...`，项目根必须在 sys.path 里
for _p in (str(ROOT), str(ROOT / "src" / "plugins"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# doupo/__init__.py 会注册 matcher，这里只需纯计算模块 → 最小 nonebot 桩
_nb = types.ModuleType("nonebot")
_nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
_nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)
_nb.on_regex = lambda *a, **k: types.SimpleNamespace(
    handle=lambda *a, **k: (lambda f: f), finish=lambda *a, **k: None)
sys.modules["nonebot"] = _nb
for _m in ("nonebot.adapters", "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
    sys.modules[_m] = types.ModuleType(_m)
for _a in ("Message", "MessageEvent", "MessageSegment"):
    setattr(sys.modules["nonebot.adapters.onebot.v11"], _a, object)

from doupo import factions as fac  # noqa: E402
from doupo import skill_book as book  # noqa: E402
from doupo import skills as sk  # noqa: E402

#: 斗技名里出现这些词 = 不是可施展的斗技（事务/制度/典籍/泛称）。
#: LORE 里允许出现，SKILL_BOOK / FACTION_SKILLS / COMMON_SKILLS 里一律禁止。
FORBIDDEN_WORDS = (
    "丹会", "考核", "表决", "炼药", "丹方", "品阶", "炼术", "炼制", "丹雷",
    "传承", "进化", "合流", "延续", "搜捕", "淬炼", "大阵", "族纹", "体质",
    "毒经", "属性功法", "冰系斗技", "屏障", "隐身", "法阵", "封印术",
)
_CITE = re.compile(r"第[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十百千\d]+章「[^」]+」")


def _bad_name(n: str) -> str | None:
    for w in FORBIDDEN_WORDS:
        if w in n:
            return w
    return None


def catalog_problems() -> list[str]:
    out: list[str] = []
    bad = book.NON_SKILL_NAMES

    # 1) 权威表本身
    for n in book.SKILL_BOOK:
        w = _bad_name(n)
        if w:
            out.append(f"[图鉴] 斗技名含非斗技词「{w}」：{n}")
        if n in bad:
            out.append(f"[图鉴] 已剥离典故仍在 SKILL_BOOK：{n}")

    # 2) 三处清单对齐
    lore_all = {n for v in book.FACTION_LORE.values() for n in v}
    if lore_all != set(bad):
        out.append(f"[归档] FACTION_LORE 与 NON_SKILL_NAMES 不一致："
                   f"多={sorted(lore_all - set(bad))} 少={sorted(set(bad) - lore_all)}")
    if set(fac.LORE_NAMES) != set(bad):
        out.append("[归档] factions.LORE_NAMES 与 skill_book.NON_SKILL_NAMES 不一致")

    # 3) 通用池
    for n in book.COMMON_SKILLS:
        if n not in book.SKILL_BOOK:
            out.append(f"[通用] COMMON_SKILLS 里的 {n} 不在 SKILL_BOOK")
        w = _bad_name(n)
        if w:
            out.append(f"[通用] 通用斗技名含非斗技词「{w}」：{n}")

    # 4) 宗门表字面量本身（不能等 catalog 静默跳过缺失项）
    for fid, listed in book.FACTION_SKILLS.items():
        for n in listed:
            if n in bad:
                out.append(f"[宗门表] {fid} 仍收录典故：{n}")
            if n not in book.SKILL_BOOK:
                out.append(f"[宗门表] {fid} 收录了不在 SKILL_BOOK 的名字：{n}")

    # 5) 每个宗门运行时池
    for fid in book.FACTION_SKILLS:
        names = [e["name"] for e in sk.catalog(fid)]
        if len(names) < 4:
            out.append(f"[宗门] {fid} 只有 {len(names)} 门斗技，四槽填不满")
        for n in names:
            if n in bad:
                out.append(f"[宗门] {fid} 斗技池混入典故：{n}")
            w = _bad_name(n)
            if w:
                out.append(f"[宗门] {fid} 斗技名含非斗技词「{w}」：{n}")
            if n not in book.SKILL_BOOK:
                out.append(f"[宗门] {fid} 的 {n} 不在 SKILL_BOOK")
        if len(names) != len(set(names)):
            out.append(f"[宗门] {fid} 斗技池有重复名")

    # 6) 兜底路径不得再冒出典故
    for fid, f in fac.FACTIONS.items():
        for s in f["skills"] + [str(x.get("name", "")) for x in f["special_skills"]]:
            for n in bad:
                if n in str(s):
                    out.append(f"[兜底] {fid} 的 skills/special_skills 仍有典故：{n}")
                    break

    # 7) 原著章句 / 自设标注
    for n, rec in book.SKILL_BOOK.items():
        novel = str(rec.get("novel") or "")
        selfmade = "原著无载" in novel or "游戏自设" in str(rec.get("source") or "")
        if not _CITE.search(novel) and not selfmade:
            out.append(f"[出处] {n} 既无「第X章「标题」」章句，也没标游戏自设")
        if selfmade and "原著无载" not in novel:
            out.append(f"[出处] {n} 是自设技但 novel 未写「原著无载」，会被当原著斗技")

    # 8) all_names / get 不得漏出典故
    if any(n in bad for n in book.all_names()):
        out.append("[接口] all_names() 仍返回典故")
    if any(book.get(n) is not None for n in bad):
        out.append("[接口] get() 仍能取到典故")

    return out


def loadout_problems() -> list[str]:
    """真实存档：旧 loadout 里的典故必须被清掉，四槽必须补满。"""
    import json
    path = ROOT / "data" / "doupo.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[str] = []
    for qq, p in (data.get("players") or {}).items():
        before = list(p.get("loadout") or [])
        after = sk.ensure_loadout(p)
        for n in book.NON_SKILL_NAMES:
            if n in after:
                out.append(f"[存档] {qq} 四槽仍有典故：{n}")
        if len(after) < 4 and len(sk.learnable(p)) >= 4:
            out.append(f"[存档] {qq} 四槽未补满：{before} -> {after}")
        for n in after:
            if n not in {e["name"] for e in sk.entries_for(p)}:
                out.append(f"[存档] {qq} 四槽里的 {n} 不在斗技池")
    return out


def selftest() -> list[tuple[str, bool]]:
    """把这次真实犯过的错放回去，检查必须报错。"""
    res: list[tuple[str, bool]] = []

    saved = dict(book.SKILL_BOOK)
    book.SKILL_BOOK["丹会"] = {"name": "丹会", "novel": "第百五十四章「三千焱炎火」",
                             "source": "", "tier": "特殊"}
    res.append(("把「丹会」放回斗技图鉴", bool(catalog_problems())))
    book.SKILL_BOOK.clear()
    book.SKILL_BOOK.update(saved)

    saved_lore = dict(book.FACTION_LORE)
    book.FACTION_LORE.pop("lianyao_gonghui", None)
    res.append(("删掉炼药师公会的典故归档", bool(catalog_problems())))
    book.FACTION_LORE.clear()
    book.FACTION_LORE.update(saved_lore)

    saved_lo = set(fac.LORE_NAMES)
    fac.LORE_NAMES = frozenset(set(fac.LORE_NAMES) - {"丹会"})
    res.append(("factions.LORE_NAMES 漏掉「丹会」", bool(catalog_problems())))
    fac.LORE_NAMES = frozenset(saved_lo)

    saved_fs = dict(book.FACTION_SKILLS)
    book.FACTION_SKILLS["lianyao_gonghui"] = list(saved_fs["lianyao_gonghui"]) + ["丹会"]
    res.append(("宗门斗技表重新收录「丹会」", bool(catalog_problems())))
    book.FACTION_SKILLS.clear()
    book.FACTION_SKILLS.update(saved_fs)

    saved_fs = dict(book.FACTION_SKILLS)
    book.FACTION_SKILLS["lianyao_gonghui"] = ["千焰剑罡"]
    saved_common = book.COMMON_SKILLS
    book.COMMON_SKILLS = ()
    res.append(("撤掉通用斗技池导致四槽填不满", bool(catalog_problems())))
    book.COMMON_SKILLS = saved_common
    book.FACTION_SKILLS.clear()
    book.FACTION_SKILLS.update(saved_fs)

    saved_cite = book.SKILL_BOOK["千焰剑罡"]["novel"]
    book.SKILL_BOOK["千焰剑罡"]["novel"] = "据说是古河用的剑法"
    res.append(("斗技出处既不写章句也不标自设", bool(catalog_problems())))
    book.SKILL_BOOK["千焰剑罡"]["novel"] = saved_cite

    return res


def main() -> int:
    problems = catalog_problems() + loadout_problems()
    st = selftest()
    for name, caught in st:
        if not caught:
            problems.append(f"[selftest] 反例没被拦住: {name}")
    total = len(book.SKILL_BOOK)
    selfmade = sum(1 for r in book.SKILL_BOOK.values()
                   if "原著无载" in str(r.get("novel") or ""))
    print("斗技图鉴梳理：", "PASS" if not problems else f"FAIL（{len(problems)} 项）")
    print(f"图鉴 {total} 门（原著有章句 {total - selfmade} / 显式标注游戏自设 {selfmade}）"
          f" ｜ 剥离典故 {len(book.NON_SKILL_NAMES)} 条 ｜ 通用斗技 {len(book.COMMON_SKILLS)} 门")
    print(f"25 派运行池最小 {min(len(sk.catalog(f)) for f in book.FACTION_SKILLS)} 门"
          "（≥4 才能填满四槽）")
    print("反例注入：%d/%d 被拦" % (sum(1 for _, ok in st if ok), len(st)))
    for name, ok in st:
        print("  %s %s" % ("OK  " if ok else "MISS", name))
    for p in problems:
        print("  " + p)
    if "--selftest" in sys.argv:
        return 0 if all(ok for _, ok in st) else 1
    if "--check" in sys.argv and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
