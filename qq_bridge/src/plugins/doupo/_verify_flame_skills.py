# -*- coding: utf-8 -*-
"""异火技能门禁：每个异火的技能描述必须能在原著正文里站住脚。

为什么需要（2026-11-15）
------------------------
旧 ``config.FLAMES`` 的五条描述里有「不生不死」「无色无相」「天地源气」
「净化世间万物」——在 1608 章原著正文里全文 **0 命中**，是游戏实机文案 /
二手资料，不是原著。用户明确要求「技能描述要符合原著」，故：

  1. 5 种养成异火每种都必须有 **主动技 + 被动**，且主动技名互不重复；
  2. 每条 ``novel`` 的「第X章「标题」」必须能在原著章节表逐字找到；
  3. 描述里禁止出现那 4 个 0 命中词（``config.FLAMES[*].desc`` 同样受检）；
  4. ``config.FLAMES[*].desc`` 也必须带一个合法原著引用。

正文语料沿用 ``_verify_novel.CORPUS``；语料缺失**显式报错**，不静默通过。

用法
----
    python src/plugins/doupo/_verify_flame_skills.py
    python src/plugins/doupo/_verify_flame_skills.py --selftest
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config as cfg  # noqa: E402
import flame_skills as fs  # noqa: E402
import _verify_novel as vn  # noqa: E402

_CITE_RE = re.compile(r"第[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十百千\d]+章「[^」]+」")

#: 原著正文 0 命中的二手/实机词 —— 这次真实犯过的错，禁止再写进技能描述
_BANNED = ("不生不死", "无色无相", "天地源气", "净化世间万物")


def valid_cites(idx: dict) -> set[str]:
    return {vn.cite(ch["title"], ch.get("vol_no", 0)) for ch in idx["chapters"]}


def problems(idx: dict | None = None) -> list[str]:
    idx = idx if idx is not None else vn.build_index()
    cites = valid_cites(idx)
    bad: list[str] = []

    if set(fs.FLAME_SKILLS) != set(cfg.FLAME_NAMES):
        bad.append("异火技能表与 config.FLAME_NAMES 不一致：%s" %
                   (set(cfg.FLAME_NAMES) ^ set(fs.FLAME_SKILLS)))

    seen_active: set[str] = set()
    for name, rec in fs.FLAME_SKILLS.items():
        a, pv = rec.get("active"), rec.get("passive")
        if not a:
            bad.append(f"{name} 缺主动技")
        if not pv:
            bad.append(f"{name} 缺被动")
        for label, part in (("主动技", a), ("被动", pv)):
            if not part:
                continue
            blob = " ".join(str(part.get(k, "")) for k in ("desc", "name", "effect", "novel", "source"))
            refs = _CITE_RE.findall(str(part.get("novel", "")))
            if not refs:
                bad.append(f"{name} {label} 缺原著章节引用")
            for r in refs:
                if r not in cites:
                    bad.append(f"{name} {label} 引用在原著语料中不存在: {r}")
            for w in _BANNED:
                if w in blob:
                    bad.append(f"{name} {label} 出现原著 0 命中词: {w}")
        if a:
            if a["name"] in seen_active:
                bad.append(f"主动技名重复（不够独特）: {a['name']}")
            seen_active.add(a["name"])

    for f in cfg.FLAMES:
        d = str(f.get("desc", ""))
        for w in _BANNED:
            if w in d:
                bad.append(f"config.FLAMES {f['name']} desc 出现 0 命中词: {w}")
        if not _CITE_RE.search(d):
            bad.append(f"config.FLAMES {f['name']} desc 缺原著章节引用")
        for r in _CITE_RE.findall(d):
            if r not in cites:
                bad.append(f"config.FLAMES {f['name']} desc 引用不存在: {r}")
    return bad


def selftest() -> int:
    """拿这次真实犯过的错去试：门禁必须报错，改回来必须通过。"""
    idx = vn.build_index()
    real = problems(idx)
    if real:
        print("SELFTEST FAIL: 真实数据本身违规")
        for p in real[:6]:
            print("   ", p)
        return 1

    import copy
    orig_skills = copy.deepcopy(fs.FLAME_SKILLS)
    orig_flames = copy.deepcopy(cfg.FLAMES)
    try:
        # 反例 1：把旧实机文案「不生不死」写回技能描述
        fs.FLAME_SKILLS["三千焱炎火"]["active"]["desc"] += "，无色无相，不生不死"
        ok1 = any("0 命中词" in x for x in problems(idx))
        print(("  PASS" if ok1 else "  FAIL"), "反例1 二手词『不生不死』 → 必须报错")

        # 反例 2：编造章号
        fs.FLAME_SKILLS["净莲妖火"]["active"]["novel"] = "第九千九百九十九章「不存在的章」 —— 编的"
        ok2 = any("不存在" in x for x in problems(idx))
        print(("  PASS" if ok2 else "  FAIL"), "反例2 编造章号 → 必须报错")

        # 反例 3：漏配被动
        fs.FLAME_SKILLS["帝炎"]["passive"] = None
        ok3 = any("缺被动" in x for x in problems(idx))
        print(("  PASS" if ok3 else "  FAIL"), "反例3 漏配被动 → 必须报错")

        # 反例 4：config 描述改回无引用的旧文案
        cfg.FLAMES[0]["desc"] = "以青莲之力增加斗气槽上限，每级 +25%"
        ok4 = any("缺原著章节引用" in x for x in problems(idx))
        print(("  PASS" if ok4 else "  FAIL"), "反例4 config 描述无引用 → 必须报错")

        # 还原后必须重新通过
        fs.FLAME_SKILLS.clear(); fs.FLAME_SKILLS.update(orig_skills)
        cfg.FLAMES[:] = orig_flames
        ok5 = not problems(idx)
        print(("  PASS" if ok5 else "  FAIL"), "还原后 → 必须通过")

        ok = ok1 and ok2 and ok3 and ok4 and ok5
        print("\nSELFTEST:", "PASS (5/5)" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        fs.FLAME_SKILLS.clear(); fs.FLAME_SKILLS.update(orig_skills)
        cfg.FLAMES[:] = orig_flames


def main() -> int:
    if not os.path.isdir(vn.CORPUS):
        print("原著语料不存在：%s" % vn.CORPUS)
        print("设 DOUPO_NOVEL_DIR 指到章节目录；没有原著不允许验收通过。")
        return 1
    if "--selftest" in sys.argv:
        return selftest()
    idx = vn.build_index()
    print("原著正文: %d 章 / %d 字" % (len(idx["chapters"]), len(idx["text"])))
    print("养成异火 %d 种，主动技 %d 个，被动 %d 个" % (
        len(fs.FLAME_SKILLS),
        sum(1 for r in fs.FLAME_SKILLS.values() if r.get("active")),
        sum(1 for r in fs.FLAME_SKILLS.values() if r.get("passive"))))
    bad = problems(idx)
    if bad:
        print("\nFLAME_SKILL_GATE FAIL (%d):" % len(bad))
        for p in bad:
            print("  -", p)
        return 1
    print("\nFLAME_SKILL_GATE PASS: 每个异火的主动/被动都有可核验的原著章节证据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
