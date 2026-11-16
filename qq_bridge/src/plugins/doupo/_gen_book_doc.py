# -*- coding: utf-8 -*-
"""从 skill_book 生成 docs/门派技能图鉴.md（唯一数据源 → 文档，杜绝两边不一致）。

用法：
    python src/plugins/doupo/_gen_book_doc.py            # 生成
    python src/plugins/doupo/_gen_book_doc.py --check    # 只校验文档是否与代码同步（exit 1 = 过期）
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import skill_book as book  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))),
                   "docs", "门派技能图鉴.md")

POOL_CN = {"starter": "新手池", "advanced": "进阶池", "hidden": "隐世池"}
POOL_ORDER = ("starter", "advanced", "hidden")

#: 宗门展示名 + 卡池（与 factions.py 对齐；此处只做展示，不参与逻辑）
FACTION_META = [
    ("xiaojia", "萧家", "starter"),
    ("yunlanzong", "云岚宗", "starter"),
    ("lianyao_gonghui", "炼药师公会", "starter"),
    ("sherenzu", "蛇人族", "starter"),
    ("motie", "漠铁佣兵团", "starter"),
    ("langtou", "狼头佣兵团", "starter"),
    ("duzong", "毒宗", "starter"),
    ("mulangu", "慕兰谷", "starter"),
    ("jianan", "迦南学院", "advanced"),
    ("fengleige", "风雷阁", "advanced"),
    ("fenyangu", "焚炎谷", "advanced"),
    ("binghegu", "冰河谷", "advanced"),
    ("tianmingzong", "天冥宗", "advanced"),
    ("huazong", "花宗", "advanced"),
    ("danta", "丹塔", "advanced"),
    ("xingyunge", "星陨阁", "advanced"),
    ("hundian", "魂殿", "advanced"),
    ("moyangu", "魔炎谷", "advanced"),
    ("xuezong", "血宗", "advanced"),
    ("guzu", "古族", "hidden"),
    ("yanzu", "炎族", "hidden"),
    ("hunzu", "魂族", "hidden"),
    ("taixugulong", "太虚古龙族", "hidden"),
    ("jiuyou", "九幽地冥蟒族", "hidden"),
    ("tianfu", "天府联盟", "hidden"),
]


def _stat(fid: str) -> dict:
    rows = book.skills_of(fid)
    out = {"普通": 0, "高阶": 0, "特殊": 0}
    for r in rows:
        out[r["tier"]] += 1
    out["合计"] = len(rows)
    return out


def build() -> str:
    L = [
        "# 斗破 · 门派斗技图鉴（游戏内技能说明）",
        "",
        "> **本文件由 `src/plugins/doupo/skill_book.py` 生成，请勿直接手改**；"
        "改数据请改 skill_book，再跑 `python src/plugins/doupo/_gen_book_doc.py`。",
        ">",
        "> 数值口径：伤害 100% = 攻击力 100%（未计目标防御与暴击）；斗气按 `mp_max` 百分比扣。",
        "> 每条「说明」按施展流程写：怎么起手 → 斗气怎么走 → 打出什么 → 增益/副作用 → 限制。",
        "",
    ]

    # ---------------- 总览 ----------------
    L += ["## 总览", "",
          "| 卡池 | 宗门 | 普通 | 高阶 | 特殊 | 合计 |",
          "|---|---|---:|---:|---:|---:|"]
    tot = {"普通": 0, "高阶": 0, "特殊": 0, "合计": 0}
    for pool in POOL_ORDER:
        for fid, cn, p in FACTION_META:
            if p != pool:
                continue
            s = _stat(fid)
            for k in tot:
                tot[k] += s[k]
            L.append("| %s | %s `%s` | %d | %d | %d | %d |"
                     % (POOL_CN[pool], cn, fid, s["普通"], s["高阶"], s["特殊"], s["合计"]))
    L.append("| **合计** | 25 派 | **%d** | **%d** | **%d** | **%d** |"
             % (tot["普通"], tot["高阶"], tot["特殊"], tot["合计"]))
    L.append("")

    # ---------------- 分池分派 ----------------
    for pool in POOL_ORDER:
        L += ["## " + POOL_CN[pool], ""]
        for fid, cn, p in FACTION_META:
            if p != pool:
                continue
            rows = book.skills_of(fid)
            s = _stat(fid)
            L += ["### %s `%s`" % (cn, fid), "",
                  "普通 %d ｜ 高阶 %d ｜ 特殊 %d ｜ 合计 %d" % (s["普通"], s["高阶"], s["特殊"], s["合计"]),
                  ""]
            for r in rows:
                L.append("**%s** ｜ %s · %s · %s ｜ %s"
                         % (r["name"], r["rank"], r["kind"], r["scope"], r["tier"]))
                L.append("")
                L.append("- 说明：%s" % r["desc"])
                L.append("- 数值：斗气 %d%%·mp_max ｜ 冷却 %d 回合 ｜ 基础伤害 %d%% ｜ 解锁 lv%d"
                         % (r["mp"], r["cd"], r["dmg"], r["unlock"]))
                L.append("- 出处：%s" % r["source"])
                nv = r["novel"] or ""
                L.append("- 原著：%s" % ("无载（游戏自设）" if nv.startswith("原著无载") else nv))
                L.append("")

    # ---------------- 无宗门归属的可考斗技 ----------------
    L += ["## 无宗门归属的可考斗技（全宗门归属审查后单列）", "",
          "原著里属于个人 / 世家、项目 25 派中没有对应宗门的斗技。"
          "保留在权威表中，**不冒充任何宗门传承**，也不进任何宗门的斗技池。", ""]
    for n in book.UNATTACHED_SKILLS:
        r = book.SKILL_BOOK.get(n)
        if r is None:
            continue
        L += ["**%s** ｜ %s · %s · %s ｜ %s"
              % (r["name"], r["rank"], r["kind"], r["scope"], r["tier"]), "",
              "- 说明：%s" % r["desc"],
              "- 出处：%s" % r["source"],
              "- 原著：%s" % (r["novel"] or "无载（游戏自设）"),
              "- 不归属理由：%s" % book.UNATTACHED_REASONS.get(n, ""),
              ""]

    # ---------------- 自检 ----------------
    L += ["## 数据自检", "",
          "| 项 | 值 |", "|---|---|",
          "| 斗技总数 | %d |" % len(book.SKILL_BOOK),
          "| 宗门数 | %d |" % len(book.FACTION_SKILLS),
          "| 说明最短字数 | %d |" % min(len(r["desc"]) for r in book.SKILL_BOOK.values()),
          "| 说明平均字数 | %d |" % (sum(len(r["desc"]) for r in book.SKILL_BOOK.values())
                                  // len(book.SKILL_BOOK)),
          "| 卡片短释最长字数 | %d |" % max(len(b) for b in book.BRIEFS.values()),
          "| 原著可核验（章+原句） | %d 条 |" % sum(
              1 for r in book.SKILL_BOOK.values()
              if not str(r.get("novel") or "").startswith("原著无载")),
          "| 原著无载（游戏自设） | %d 条 |" % sum(
              1 for r in book.SKILL_BOOK.values()
              if str(r.get("novel") or "").startswith("原著无载")),
          ""]
    return "\n".join(L) + "\n"


def main(argv) -> int:
    text = build()
    if "--check" in argv:
        cur = ""
        if os.path.exists(OUT):
            cur = open(OUT, encoding="utf-8").read()
        if cur != text:
            print("图鉴文档与 skill_book 不同步，请重跑 _gen_book_doc.py")
            return 1
        print("图鉴文档已与 skill_book 同步（%d 条斗技）" % len(book.SKILL_BOOK))
        return 0
    open(OUT, "w", encoding="utf-8").write(text)
    print("已生成 %s（%d 条斗技 / %d 派 / %d 字）"
          % (OUT, len(book.SKILL_BOOK), len(book.FACTION_SKILLS), len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
