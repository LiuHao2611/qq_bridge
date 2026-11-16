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
    """反向：图鉴里每条都必须真被某个宗门收录，避免写了没人用。

    例外是归属审查后从错误宗门移除的 4 门可考个人技：必须在
    ``UNATTACHED_SKILLS`` 显式登记理由，且确实不属于任何宗门。
    没登记的无归属条目一律 FAIL（不许悄悄把斗技丢成孤儿）。
    """
    ref = {n for ns in book.FACTION_SKILLS.values() for n in ns}
    orphan = sorted(set(book.SKILL_BOOK) - ref)
    unknown = [n for n in orphan if n not in book.UNATTACHED_SKILLS]
    assert not unknown, "图鉴里有未登记的无归属条目：%s" % "、".join(unknown)
    for n in book.UNATTACHED_SKILLS:
        assert n in book.SKILL_BOOK, "%s 登记了却不在图鉴里" % n
        assert n not in ref, "%s 登记为无归属，却又挂了宗门" % n
        assert str(book.UNATTACHED_REASONS.get(n) or "").strip(), (
            "%s 没写不归属理由" % n)


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
    """2026-11-16 新口径：source 写「怎么来的」，查不到就留空。

    用户：「你全都写谁用的，这并不符合斗技来源的说法」——
    旧断言要求每门非空，逼着填「某某所使」；改成：空合法，但写了就必须是
    真正的来历（创/传/取得/购/夺/锤炼），不许写成使用记录。
    """
    # 本 runner 不 stub nonebot，不能 import skill_effects（它会触发 nonebot）；
    # 判据就地写一份，与 skill_effects 的两条正则保持一致。
    usage_re = re.compile(r"(所使|所用|所修|亦修|施展者|擅长使用|惯用|所使用的?|最擅长)")
    origin_re = re.compile(
        r"(所创|创出|自创|所传|传授|所授|授与|取得|到手|所得|购|买|拍得|"
        r"拍卖|抢|夺|获|得自|锤炼|磨出|修成|习得|藏|捡|发现|遗留|继承)")
    # 2026-11-16 用户「先把迦南学院的斗技都改好再说」：本轮只锁已逐门查证过的
    # 迦南学院本宗 19 门；其余宗门同类问题由 usage_only_source_stats() 统计排期。
    bad = [n for n in book.FACTION_SKILLS["jianan"]
           if usage_re.search(str(book.SKILL_BOOK[n].get("source") or ""))
           and not origin_re.search(str(book.SKILL_BOOK[n].get("source") or ""))]
    assert not bad, "迦南学院来源只写「谁用过」而非来历：%s" % "、".join(bad[:12])
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
def _runtime_pool(fid: str) -> list[str]:
    """运行时斗技目录 = 本宗专属 + 通用基础斗技（与 skills.catalog 同源，去重）。

    2026-11-15 梳理后 ``FACTION_SKILLS`` 只放**本宗专属**，炼药师公会这类
    原著里只有 1 门真斗技的宗门靠 COMMON_SKILLS 补齐四槽，不再塞炼药事务凑数。
    """
    own = list(book.FACTION_SKILLS.get(fid, ()))
    return own + [n for n in book.COMMON_SKILLS if n not in own]


def test_every_faction_has_at_least_four_skills():
    short = [(fid, len(_runtime_pool(fid))) for fid in book.FACTION_SKILLS
             if len(_runtime_pool(fid)) < 4]
    assert not short, "以下宗门运行时斗技不足 4 门，四槽凑不满：%s" % short


def test_exclusive_skills_never_leak_into_other_factions():
    """用户 2026-11-15：「焰分噬浪尺为什么分到天冥宗了？？？？？？」

    通用池会被 ``skills.catalog`` 全量灌给**每个**宗门，所以有明确授受对象的
    专属技绝不能进 ``COMMON_SKILLS``；撤出后仍须留在自己的宗门（资料不丢）。
    """
    exclusive = {
        "焰分噬浪尺": "xingyunge", "吸掌": "xingyunge", "吹火掌": "xingyunge",
        "爆步": "yunlanzong", "雷蝠天翼": "xuezong", "燕返击": "guzu",
        "卸力水镜": "jianan", "水曼陀罗": "jianan",
    }
    leaked = [n for n in exclusive if n in book.COMMON_SKILLS]
    assert not leaked, "专属技混进通用池，会灌给所有宗门：%s" % leaked
    tianming = set(book.FACTION_SKILLS["tianmingzong"]) | set(book.COMMON_SKILLS)
    assert not (set(exclusive) & tianming), "天冥宗学到了别人的专属斗技"
    for name, fid in exclusive.items():
        assert name in book.FACTION_SKILLS[fid], "%s 的原宗门归属丢了" % name

    # 反例注入：把焰分噬浪尺放回通用池，天冥宗立刻「学会」→ 证明断言不是空跑
    saved = book.COMMON_SKILLS
    book.COMMON_SKILLS = tuple(saved) + ("焰分噬浪尺",)
    try:
        leaked_pool = (set(book.FACTION_SKILLS["tianmingzong"])
                       | set(book.COMMON_SKILLS))
        assert "焰分噬浪尺" in leaked_pool, "反例失效：注入后仍没灌进天冥宗"
    finally:
        book.COMMON_SKILLS = saved


def _learnable_at(fid: str, lv: int) -> list[dict]:
    """进池等级可学斗技 = 本宗 + 通用（与 skills.learnable 同口径）。"""
    rows = [book.SKILL_BOOK[n] for n in _runtime_pool(fid) if n in book.SKILL_BOOK]
    return [r for r in rows if int(r["unlock"]) <= lv]


def test_low_skill_factions_may_have_short_loadout():
    """2026-11-15 改为：所有宗门进池等级都能凑满四槽，且四槽不该再缩水。

    旧断言允许 4 派「凑不满四槽」，结果逼着当初往宗门里塞炼药/考核/丹会
    这类非斗技凑数（用户截图里那张四槽卡就是这么来的）。现在由
    ``skills.ensure_loadout`` 自动补满，本测试改为锁住这个新契约。
    """
    fac = _load_factions()
    bad = []
    for fid in book.FACTION_SKILLS:
        base = fac.POOL_UNLOCK.get(fac.FACTIONS[fid].get("pool"), 1)
        learn = _learnable_at(fid, base)
        if len(learn) < 4:
            bad.append("%s 进池 lv%d 只有 %d 门可学" % (fid, base, len(learn)))
    assert not bad, "；".join(bad)


def test_every_faction_learnable_at_pool_unlock():
    fac = _load_factions()
    bad = []
    for fid, f in fac.FACTIONS.items():
        base = fac.POOL_UNLOCK.get(f.get("pool"), 1)
        if not _learnable_at(fid, base):
            bad.append("%s（进池 lv%d）" % (fid, base))
    assert not bad, "进池等级无斗技可学：%s" % bad


def test_three_tiers_all_present():
    tiers = {r["tier"] for r in book.SKILL_BOOK.values()}
    assert tiers == {"普通", "高阶", "特殊"}, "三阶不全：%s" % sorted(tiers)
    # 2026-11-15：剥离的 35 条典故里大半是「秘法/特殊阶」（丹会、表决、护族大阵、
    # 吞噬血脉延续…），特殊阶从 ~30 门降到 7 门是梳理的正确结果，不是数据缩水。
    # 原著里特殊阶本就稀少，阈值从 10 调到 5；同时锁住「三阶齐全」不放松。
    for t in ("普通", "高阶", "特殊"):
        n = sum(1 for r in book.SKILL_BOOK.values() if r["tier"] == t)
        assert n >= 5, "%s 阶只有 %d 门，太少" % (t, n)


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
