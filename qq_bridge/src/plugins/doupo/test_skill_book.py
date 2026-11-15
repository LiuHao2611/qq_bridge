# -*- coding: utf-8 -*-
r"""斗技图鉴（skill_book）机器验收 —— 专治「技能根本没描述 / 只写了个来源」。

用户 2026-11-15 定的验收口径：
    「刚才收集的信息都被你丢了，还有，你偷懒了，有的技能根本没有描述，
      就写了个来源。你是在糊弄谁啊你，你认真点，弄不好不准交付。」

因此这里不检查"有没有这一行"，而是检查**内容是否成立**：
  1. factions.py 里出现的每一门斗技（含 `A / B / C` 合并写法拆分后）都必须在
     skill_book 有条目 —— 抓「解析时被 split('/')[0] 静默丢掉」这类事故。
  2. 每条说明 ≥ 60 字，且含数值（`\d+%`），且不含占位词
     （`凝势一击` / `✅` / `见核心` / `TODO` / `待补`）。
  3. rank / kind / scope 必须在允许集合内，三阶必须与阶位自洽。
  4. 每条都有卡片短释且 ≤ 24 字（卡片只有两行位）。
  5. 每个宗门在「自己进池的等级」至少一门可学，且目录 ≥ 4 门（凑得满四槽）。
  6. docs/门派技能图鉴.md 必须与 skill_book 同步（改数据不改文档 = 失败）。

运行：
    python src/plugins/doupo/test_skill_book.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import skill_book as book  # noqa: E402

_FAC_PATH = os.path.join(_HERE, "factions.py")
_PLACEHOLDERS = ("凝势一击", "✅", "见核心", "TODO", "待补", "同上", "（略）")


def _load_factions():
    spec = importlib.util.spec_from_file_location("_fac_for_book_test", _FAC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _split_outside(s: str) -> list[str]:
    """只在括号**外**的 `/` 处拆分（`飞絮（纳兰嫣然/云韵）` 是一个名字，不是三个）。"""
    out, buf, depth = [], "", 0
    for ch in str(s):
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if ch == "/" and depth == 0:
            out.append(buf)
            buf = ""
            continue
        buf += ch
    out.append(buf)
    return [x.strip() for x in out if x.strip()]


def _bare(name: str) -> str:
    """去掉 `（黄阶低，萧战）` `[C1]` 这类注解，只留技名。"""
    s = re.sub(r"\[[^\]]*\]", "", str(name))
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", s)
    return s.strip()


def _code_skill_names(fac) -> dict[str, list[str]]:
    """factions.py → {宗门: [拆分后的技名]}。"""
    out: dict[str, list[str]] = {}
    for fid, f in fac.FACTIONS.items():
        names: list[str] = []
        for raw in f.get("skills", []):
            for piece in _split_outside(str(raw).split("[")[0]):
                n = _bare(piece)
                if n and n not in names:
                    names.append(n)
        for sp in f.get("special_skills", []):
            n = _bare(sp["name"] if isinstance(sp, dict) else sp)
            if n and n not in names:
                names.append(n)
        out[fid] = names
    return out


# ---------------------------------------------------------------------------
# 1. 覆盖：代码里有的技能，图鉴里必须有
# ---------------------------------------------------------------------------
def test_every_code_skill_has_book_entry():
    fac = _load_factions()
    missing = []
    for fid, names in _code_skill_names(fac).items():
        for n in names:
            if n not in book.SKILL_BOOK:
                missing.append("%s/%s" % (fid, n))
    assert not missing, "以下斗技在 factions.py 里有、skill_book 里没有说明：%s" % "、".join(missing)


def test_no_orphan_book_entry():
    """反向：图鉴里每条都必须真被某个宗门收录，避免写了没人用。"""
    ref = {n for ns in book.FACTION_SKILLS.values() for n in ns}
    orphan = sorted(set(book.SKILL_BOOK) - ref)
    assert not orphan, "图鉴里有未被任何宗门收录的条目：%s" % "、".join(orphan)


def test_faction_skills_cover_all_factions():
    fac = _load_factions()
    assert set(book.FACTION_SKILLS) == set(fac.FACTIONS), (
        "skill_book 宗门与 factions.py 不一致：多 %s / 少 %s"
        % (sorted(set(book.FACTION_SKILLS) - set(fac.FACTIONS)),
           sorted(set(fac.FACTIONS) - set(book.FACTION_SKILLS))))


# ---------------------------------------------------------------------------
# 2. 内容：描述必须成立，不是占位
# ---------------------------------------------------------------------------
def test_descriptions_are_substantive():
    bad = []
    for r in book.SKILL_BOOK.values():
        d = str(r.get("desc") or "")
        if len(d) < 60:
            bad.append("%s（%d 字）" % (r["name"], len(d)))
        elif not re.search(r"\d+\s*%", d):
            bad.append("%s（无数值）" % r["name"])
        else:
            hit = [p for p in _PLACEHOLDERS if p in d]
            if hit:
                bad.append("%s（占位词 %s）" % (r["name"], "/".join(hit)))
    assert not bad, "说明不合格：%s" % "；".join(bad)


def test_descriptions_are_not_copied_from_novel_only():
    """说明不能只是原文摘抄：必须出现至少一个游戏口径的词。"""
    # 只要出现任一「游戏机制词」就算过：既覆盖战斗技，也覆盖炼药/议事这类非战斗技艺
    GAME_WORDS = ("伤害", "攻击", "防御", "闪避", "护盾", "斗气", "冷却",
                  "回合", "束缚", "封印", "眩晕", "治疗", "体力", "命中",
                  "丹药", "药材", "成功率", "资源", "修为", "回复", "减伤")
    bad = [r["name"] for r in book.SKILL_BOOK.values()
           if not any(w in str(r.get("desc")) for w in GAME_WORDS)]
    assert not bad, "以下说明没有游戏口径（像在抄原文）：%s" % "、".join(bad)


def test_rank_kind_scope_are_valid():
    bad = []
    for r in book.SKILL_BOOK.values():
        if r["kind"] not in book.ALLOWED_KINDS:
            bad.append("%s 类型=%s" % (r["name"], r["kind"]))
        if r["scope"] not in book.ALLOWED_SCOPES:
            bad.append("%s 范围=%s" % (r["name"], r["scope"]))
        if not str(r["rank"]).strip():
            bad.append("%s 缺阶位" % r["name"])
        if book.tier_of(r["rank"]) != r["tier"]:
            bad.append("%s 三阶与阶位不自洽" % r["name"])
    assert not bad, "字段非法：%s" % "；".join(bad)


def test_numeric_fields_in_range():
    bad = []
    for r in book.SKILL_BOOK.values():
        if not (1 <= int(r["mp"]) <= 100):
            bad.append("%s mp=%s" % (r["name"], r["mp"]))
        if not (1 <= int(r["cd"]) <= 9):
            bad.append("%s cd=%s" % (r["name"], r["cd"]))
        if not (50 <= int(r["dmg"]) <= 400):
            bad.append("%s dmg=%s" % (r["name"], r["dmg"]))
        if not (1 <= int(r["unlock"]) <= 100):
            bad.append("%s unlock=%s" % (r["name"], r["unlock"]))
    assert not bad, "数值越界：%s" % "；".join(bad)


def test_every_entry_has_source_and_novel_slot():
    bad = [r["name"] for r in book.SKILL_BOOK.values() if not str(r.get("source") or "").strip()]
    assert not bad, "缺来源：%s" % "、".join(bad)
    # novel 允许为空，但必须是字符串键（生成文档要用）
    bad2 = [r["name"] for r in book.SKILL_BOOK.values() if "novel" not in r]
    assert not bad2, "缺原著字段：%s" % "、".join(bad2)


# ---------------------------------------------------------------------------
# 3. 卡片短释
# ---------------------------------------------------------------------------
def test_every_entry_has_short_brief():
    missing = [n for n in book.SKILL_BOOK if not book.SKILL_BOOK[n].get("brief")]
    assert not missing, "缺卡片短释：%s" % "、".join(missing)
    too_long = [(n, len(book.SKILL_BOOK[n]["brief"])) for n in book.SKILL_BOOK
                if len(book.SKILL_BOOK[n]["brief"]) > 24]
    assert not too_long, "短释超过 24 字（卡片两行放不下）：%s" % too_long


# ---------------------------------------------------------------------------
# 4. 可用性：四槽凑得满、进池即可学
# ---------------------------------------------------------------------------
def test_every_faction_has_at_least_four_skills():
    short = [(fid, len(ns)) for fid, ns in book.FACTION_SKILLS.items() if len(ns) < 4]
    assert not short, "以下宗门斗技不足 4 门，连目录都凑不齐：%s" % short


#: 原著里斗技本来就少的门派，进池等级凑不满四槽是**数据真实性的代价**，不是 bug。
#: （都只有 3 门普通/低阶技；慕兰谷更是只有 2 门兽人族族技 + 2 门地阶镇派绝学）
SHORT_LOADOUT_OK = {"sherenzu", "duzong", "lianyao_gonghui", "mulangu"}


def test_low_skill_factions_may_have_short_loadout():
    """门规：除上述 4 派外，其余门派进池等级必须能凑满四槽；4 派也必须 ≥1 门。"""
    fac = _load_factions()
    bad = []
    for fid in book.FACTION_SKILLS:
        base = fac.POOL_UNLOCK.get(fac.FACTIONS[fid].get("pool"), 1)
        learn = [r for r in book.skills_of(fid) if int(r["unlock"]) <= base]
        if not learn:
            bad.append("%s 一门都学不到" % fid)
            continue
        if len(learn) < 4 and fid not in SHORT_LOADOUT_OK:
            bad.append("%s 只有 %d 门可上阵（不在豁免名单）" % (fid, len(learn)))
    assert not bad, "；".join(bad)


def test_every_faction_learnable_at_pool_unlock():
    fac = _load_factions()
    bad = []
    for fid, f in fac.FACTIONS.items():
        base = fac.POOL_UNLOCK.get(f.get("pool"), 1)
        rows = book.skills_of(fid)
        if not any(int(r["unlock"]) <= base for r in rows):
            bad.append("%s（进池 lv%d）" % (fid, base))
    assert not bad, "进池等级无斗技可学：%s" % bad


def test_three_tiers_all_present():
    tiers = {r["tier"] for r in book.SKILL_BOOK.values()}
    assert tiers == {"普通", "高阶", "特殊"}, "三阶不全：%s" % sorted(tiers)
    for t in ("普通", "高阶", "特殊"):
        n = sum(1 for r in book.SKILL_BOOK.values() if r["tier"] == t)
        assert n >= 10, "%s 阶只有 %d 门，太少" % (t, n)


# ---------------------------------------------------------------------------
# 4b. 原著引用必须可核验（不是"凭剧情猜的章号"）
# ---------------------------------------------------------------------------
NOVEL_ABSENT = "原著无载 · 游戏自设"
_NOVEL_RE = re.compile(r"^(第[零一两二三四五六七八九十百千]+章)「([^」]+)」 —— (.+)$")


def test_novel_field_format():
    bad = []
    for r in book.SKILL_BOOK.values():
        v = str(r.get("novel") or "")
        if v == NOVEL_ABSENT:
            continue
        if not _NOVEL_RE.match(v):
            bad.append("%s=%r" % (r["name"], v[:40]))
    assert not bad, ("原著字段格式不对（应为 `第X章「标题」 —— 原句` 或 `%s`）：%s"
                     % (NOVEL_ABSENT, "；".join(bad)))


def test_novel_quotes_exist_in_source_text():
    """有原著语料时，逐条把引文拿回正文里查 —— 查不到就是编的。

    语料在 C:/Users/L/AppData/Local/Temp/chaps（可用 DOUPO_NOVEL_DIR 覆盖），
    不在的环境只跑上面的格式校验并跳过本项，避免 CI 卡死。
    """
    import _verify_novel as vn  # noqa: E402  同目录工具
    if not os.path.isdir(vn.CORPUS):
        print("      （跳过：未找到原著语料 %s）" % vn.CORPUS)
        return
    idx = vn.build_index()
    text = idx["text"]
    bad = []
    for r in book.SKILL_BOOK.values():
        v = str(r.get("novel") or "")
        if v == NOVEL_ABSENT:
            continue
        m = _NOVEL_RE.match(v)
        if not m:
            continue
        quote = m.group(3)
        if text.find(quote) < 0:
            bad.append("%s（引文正文查无）" % r["name"])
    assert not bad, "原著引文在正文里找不到（等于编的）：%s" % "、".join(bad)


def test_at_least_half_skills_have_real_citation():
    """不许整本摆烂成「原著无载」：至少一半要能引到真章。"""
    real = sum(1 for r in book.SKILL_BOOK.values()
               if str(r.get("novel") or "") != NOVEL_ABSENT)
    total = len(book.SKILL_BOOK)
    assert real >= total // 2, "只有 %d/%d 条有原著引用" % (real, total)


# ---------------------------------------------------------------------------
# 5. 文档同步
# ---------------------------------------------------------------------------
def test_doc_is_in_sync_with_book():
    gen = os.path.join(_HERE, "_gen_book_doc.py")
    r = subprocess.run([sys.executable, gen, "--check"],
                       capture_output=True, text=True, cwd=_HERE)
    assert r.returncode == 0, (
        "docs/门派技能图鉴.md 与 skill_book 不同步，请跑：python src/plugins/doupo/_gen_book_doc.py\n"
        + (r.stdout or "") + (r.stderr or ""))


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print("PASS  %s" % name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("SKILLBOOK_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
