# -*- coding: utf-8 -*-
"""tools/check_skill_multidim.py —— 斗技「多维度设计」交付闸门。

用户 2026-11-15（附截图）：
    「全部斗技的展示做的太简陋了……技能描述也没展示出来，技能数值也没展示，
      比如一个技能有位移又有伤害，那就应该有闪避和少量伤害，懂吗，而不是一个
      单纯的数字。这么设计的话所有的斗技就只剩下一个数值对比了，就失去了斗技
      使用的多样性……让玩家有更好的技能搭配，而不是无脑选择高数值的斗技。」

根因不是「文案不够好」，而是**数据结构只有一维伤害**：``kind/scope`` 是单值枚举，
``_hit()`` 只按 ``dmg`` 倍率算伤害，desc 里的吸血/破防/DoT/控制全是死文本。
因此引入 ``role + effects[] + gate`` 三元组（见 ``src/plugins/doupo/skill_effects.py``），
并让核心战斗与卡片都消费它。

本闸门守的就是「不许退回去」：

1. **字段完整**：目标宗门（默认天冥宗）每门斗技必须有 role / effects / gate，
   且 role 在受控词表内、effect 类型在 EFFECT_LABELS 内、数值不越界。
2. **反无脑选最大值**：每门至少满足一条战术维度——
   ``effects`` 有实质效果，或 ``gate`` 写明代价，或 role 属于战术型（非纯输出）。
   低伤（倍率 <= 1.3）技能若既无效果又无门控 = 纯数字填充，直接拦。
3. **高威力必须付代价**：倍率 >= 1.8 的斗技必须有 gate，否则就是无脑最优解。
4. **描述与结构一致**：desc 里写到的百分比必须能在 effects 里找到对应数值，
   防止「文案说有 30% 破防、数据里却是 0」这种图文说两回事。
5. **卡片消费**：``card.build_skills_data`` / ``build_roster_data`` 必须真的把
   role/chips/gate 透出到卡片数据（否则修了数据层、玩家还是只看到一个数字）。
   2026-11-15 23:5x 起**全 25 派逐派跑** ``build_roster_data``，不再只查天冥宗。
6. **来源融入描述**（2026-11-15 用户纠正）：名录卡每行 desc 以
   「<传承来源>，<施展流程>…」开头，来源只取 ``skill_book.source``（谁所使 /
   怎么得到）；**严禁写「原著第X章「标题」载」这类小说注脚**（用户：「不要写
   原著多少章这种描述，你见过哪个游戏的技能描述这样写的？」）；有来源就加、
   没有就不加、不许写「出处：」标题、不许来源单独成句。
7. **状态徽章醒目**：右上角 14px 浅字 + 高对比实底 + 状态圆点、三态不同色；
   退回旧版 12px 淡底同色小胶囊即报错（``state_badge_problems``）。

用法：
    python tools/check_skill_multidim.py            # 报告，不中断
    python tools/check_skill_multidim.py --check    # 违规 exit 1（交付前必跑）
    python tools/check_skill_multidim.py --selftest # 反例注入：每个旧错都必须被拦
"""
from __future__ import annotations

import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "src" / "plugins"
for _p in (str(ROOT), str(PLUGINS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: 默认检查的宗门（用户当前宗门；其余宗门的斗技按同一规范逐步补齐）
TARGET_FACTION = "tianmingzong"
#: 低伤门槛：倍率 <= 此值且无任何战术维度 = 纯数字技能
LOW_DMG = 1.3
#: 高威力门槛：倍率 >= 此值必须写 gate（否则无脑最优）
HIGH_DMG = 1.8

#: 允许的战术型主定位（纯「输出」不算战术维度——那还是比数字）
TACTICAL_ROLES = ("续航", "控场", "异常", "防御", "位移", "功能")


def _stub_nonebot() -> None:
    """让 doupo 纯逻辑层可在无 QQ 环境导入（不加载主插件）。"""
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import skills, skill_book as book          # noqa: E402
from doupo import skill_effects as fx                 # noqa: E402


# ---------------------------------------------------------------------------
# 1. 结构校验
# ---------------------------------------------------------------------------
def _faction_entries(fid: str) -> list[dict]:
    return skills.catalog(fid)


def struct_problems(entries: list[dict], fid: str = TARGET_FACTION) -> list[str]:
    """字段完整性 / 词表 / 数值范围。"""
    out: list[str] = []
    if not entries:
        return ["[数据] %s 一门斗技都没读到（catalog 失效）" % fid]
    for e in entries:
        name = e["name"]
        rec = book.SKILL_BOOK.get(name, {})
        role = str(rec.get("role") or "")
        effects = rec.get("effects")
        gate = str(rec.get("gate") or "")
        if not role:
            out.append("[字段] %s 缺 role（主定位）" % name)
        elif role not in fx.ROLES:
            out.append("[词表] %s 的 role=%r 不在受控词表 %s" % (name, role, fx.ROLES))
        if effects is None or effects == []:
            # 7 门技原著/desc 里确实没有可合规量化的独立机制，已显式登记，
            # 不许硬编数值凑 chip；白名单之外一律报错。
            if name not in book.NO_EFFECT_SKILLS:
                out.append("[字段] %s 没有 effects（多维度为空，只剩一个伤害数字）" % name)
        for err in fx.validate_effects(effects or []):
            out.append("[效果] %s %s" % (name, err))
        if not gate:
            out.append("[字段] %s 缺 gate（没写门控/代价）" % name)
        if not str(rec.get("desc") or ""):
            out.append("[字段] %s 缺完整 desc" % name)
    return out


# ---------------------------------------------------------------------------
# 2. 反无脑：低伤必须有战术维度，高威力必须有代价
# ---------------------------------------------------------------------------
def _has_tactical(e: dict, rec: dict) -> bool:
    """有实质效果 / 有门控 / 主定位是战术型 —— 三者有其一即算有维度。"""
    effects = [x for x in (rec.get("effects") or []) if isinstance(x, dict)]
    if effects:
        return True
    if str(rec.get("gate") or "").strip():
        return True
    return str(rec.get("role") or "") in TACTICAL_ROLES


def balance_problems(entries: list[dict], fid: str = TARGET_FACTION) -> list[str]:
    out: list[str] = []
    for e in entries:
        name = e["name"]
        rec = book.SKILL_BOOK.get(name, {})
        mult = float(e.get("dmg") or 0)
        role = str(rec.get("role") or "")
        gate = str(rec.get("gate") or "").strip()
        if mult <= LOW_DMG and not _has_tactical(e, rec):
            out.append("[无脑] %s 倍率 %.2f 但无任何战术维度（纯数字填充）" % (name, mult))
        if mult >= HIGH_DMG and not gate:
            out.append("[无脑] %s 倍率 %.2f 却没有 gate，会成为唯一最优解" % (name, mult))
        if (role == "输出" and not [x for x in (rec.get("effects") or [])]
                and name not in book.NO_EFFECT_SKILLS):
            out.append("[无脑] %s 定位纯输出且无效果（还是只剩比数字）" % name)
    # 全局：不允许「全部技能同一个 role」这种伪多维
    roles = {str(book.SKILL_BOOK.get(e["name"], {}).get("role") or "") for e in entries}
    roles.discard("")
    if len(roles) < 3:
        out.append("[多样性] 本宗只有 %d 种主定位 %s，技能搭配空间不足" % (len(roles), sorted(roles)))
    return out


# ---------------------------------------------------------------------------
# 3. 描述与结构一致（文案说 X%，数据里必须有 X）
# ---------------------------------------------------------------------------
_PCT_RE = re.compile(r"(\d+)%")


def desc_consistency_problems(entries: list[dict]) -> list[str]:
    """desc 里出现的百分比，至少有一个能在 effects 的 v/p 里对上。

    只做「存在性」校验：完整比对会把「斗气 34%」「体力上限 8%」这类非效果数值
    误判，因此要求 effects 至少命中一个 desc 百分比即可。

    控制类效果（眩晕/定身/禁技/击退）本身不带百分比——这类技能 desc 里只有
    伤害倍率（如「造成 160% 伤害，并令目标 1 回合内无法施展斗技」），
    无数值可比，跳过（否则会误报纯控制技）。
    """
    out: list[str] = []
    for e in entries:
        name = e["name"]
        rec = book.SKILL_BOOK.get(name, {})
        desc = str(rec.get("desc") or "")
        effects = [x for x in (rec.get("effects") or []) if isinstance(x, dict)]
        if not desc or not effects:
            continue
        # effects 里的数值（v/p）与持续回合(d)都可能以百分比出现，全部纳入候选
        have: set[int] = set()
        for x in effects:
            for k in ("v", "p"):
                if x.get(k) is not None:
                    try:
                        have.add(int(round(float(x[k]) * 100)))
                    except (TypeError, ValueError):
                        pass
        if not have:
            continue          # 纯控制/纯位移效果：没有百分比可比，跳过
        want = {int(m) for m in _PCT_RE.findall(desc)}
        if not want:
            continue
        if not (want & have):
            out.append("[图文] %s 描述里的百分比 %s 在 effects 数值 %s 里找不到对应"
                       % (name, sorted(want), sorted(have)))
    return out


# ---------------------------------------------------------------------------
# 4. 卡片真的消费了多维字段
# ---------------------------------------------------------------------------
def _skills_shape_problems(d: dict) -> list[str]:
    """四槽卡（doupo_skills）数据结构校验（纯函数，便于反例注入）。"""
    out: list[str] = []
    rows = d.get("rows") or []
    if not any(r.get("role") for r in rows):
        out.append("[卡片] build_skills_data 没有 role（四槽卡看不到主定位）")
    if not any(r.get("chips") for r in rows):
        out.append("[卡片] build_skills_data 没有 chips（四槽卡看不到多维效果）")
    if not any(r.get("gate") for r in rows):
        out.append("[卡片] build_skills_data 没有 gate（四槽卡看不到代价）")
    if any(len(str(r.get("desc") or "")) <= 20 for r in rows):
        out.append("[卡片] build_skills_data 的 desc 过短，疑似退回 brief")
    for r in rows:
        if "限制：" in str(r.get("desc") or ""):
            out.append("[卡片] %s 的描述没剥离「限制：」段，会和 gate 行重复" % r.get("name"))
            break
    return out


def _roster_shape_problems(roster: dict) -> list[str]:
    """名录卡（doupo_roster）数据结构校验（纯函数，便于反例注入）。

    2026-11-15 第二次视觉重做后，名录卡按阶位分组 + 每门一个卡片块 +
    状态 pill + 效果 chip + 代价行。这里断言**结构化字段**，不再用
    「字符串里有没有括号」这种弱判断（弱判断下把 role 删掉、只留字面量
    括号也能过）。
    """
    out: list[str] = []
    rows = roster.get("rows") or []
    if not roster.get("groups"):
        out.append("[卡片] build_roster_data 没有按阶位分组（名录卡会退回平铺表格）")
    if not any(r.get("role") for r in rows):
        out.append("[卡片] build_roster_data 没有 role（看不到主定位胶囊）")
    if not any(r.get("state_kind") for r in rows):
        out.append("[卡片] build_roster_data 没有 state_kind（状态 pill 无法分色）")
    if not any(r.get("gate") for r in rows):
        out.append("[卡片] build_roster_data 没有 gate（看不到代价行）")
    if not any(len(r.get("chips") or []) >= 2 for r in rows):
        out.append("[卡片] build_roster_data 的 chips 不足 2 枚（看不到伤害+多维效果）")
    # 用户 2026-11-15：「技能描述加上，别偷懒」——名录卡必须有完整描述
    if not all(str(r.get("desc") or "") for r in rows):
        out.append("[卡片] 有斗技没有 desc（名录卡只剩 brief，属于偷懒）")
    if any(len(str(r.get("desc") or "")) < 40 for r in rows):
        out.append("[卡片] 有斗技描述过短，疑似退回 brief")
    for r in rows:
        if "限制：" in str(r.get("desc") or ""):
            out.append("[卡片] %s 的描述没剥离「限制：」段，会和代价行重复" % r.get("name"))
            break
    for r in rows:
        ch = r.get("chips") or []
        if ch and str(ch[0].get("label")) != "伤害":
            out.append("[卡片] %s 的第一个 chip 不是伤害倍率（玩家无法比数值）"
                       % r.get("name"))
            break
    for r in rows:
        m = str(r.get("meta") or "")
        if " ·  · " in m or m.startswith(" · "):
            out.append("[卡片] %s 的副行分隔符异常：%s" % (r.get("name"), m))
            break
        segs = [x.strip() for x in m.split("·")]
        dup = next((segs[i] for i in range(len(segs) - 1)
                    if segs[i] and segs[i] == segs[i + 1]), None)
        if dup:
            out.append("[卡片] %s 的副行重复拼词「%s」：%s" % (r.get("name"), dup, m))
            break
    return out


# ---------------------------------------------------------------------------
# 4.5 描述首句来源 + 右上角状态徽章（2026-11-15 用户第三轮优化）
# ---------------------------------------------------------------------------
def source_problems(roster: dict) -> list[str]:
    """名录卡每行：来源以状语并进**描述第一句**、无来源不编、不写章节号。

    用户 2026-11-15 纠正：「不要写原著多少章这种描述，你见过哪个游戏的
    技能描述这样写的？」——所以除了「有来源必加」，还必须拦文献引用写法
    与「来源单独成句」（那不是游戏技能说明）。

    纯函数，反例注入见 ``selftest``。字段 ``source_phrase`` / ``has_source``
    由 ``card.build_roster_data`` 结构化透出，避免靠字符串猜有没有加来源。
    """
    out: list[str] = []
    rows = roster.get("rows") or []
    if not rows:
        return ["[来源] 名录卡没有行"]
    if not any(r.get("has_source") for r in rows):
        out.append("[来源] 整张名录卡没有任何技能来源（card 层没接 source_phrase）")
    for r in rows:
        name = str(r.get("name") or r.get("title") or "?")
        phrase = str(r.get("source_phrase") or "")
        desc = str(r.get("desc") or "")
        has = bool(r.get("has_source"))
        if has != bool(phrase):
            out.append("[来源] %s 的 has_source 与 source_phrase 不一致" % name)
        if phrase and not desc.startswith(phrase + "，"):
            out.append("[来源] %s 的来源没并进描述第一句（应「<来源>，<施展流程>…」）：%s"
                       % (name, desc[:32]))
        if fx.CITATION_RE.search(desc):
            out.append("[来源] %s 的描述写成小说注脚了（出现原著章节引用）：%s"
                       % (name, desc[:32]))
        if not has and ("原著第" in desc or "出自第" in desc):
            out.append("[来源] %s 没有来源却编了原著章节" % name)
        for tag in ("出处：", "出处:", "来源：", "来源:", "原著："):
            if tag in phrase:
                out.append("[来源] %s 把来源写成了独立标题「%s」，应融进描述首句"
                           % (name, tag))
                break
        if "\n" in phrase or phrase.startswith("限制："):
            out.append("[来源] %s 的来源短语不是描述里的自然开头" % name)
        if "原著无载" in phrase or "游戏自设" in phrase:
            out.append("[来源] %s 把「游戏自设」当成了技能来源" % name)
    return out


def badge_problems() -> list[str]:
    """状态徽章醒目度：读模板常量，退回淡色小胶囊即报错。"""
    from doupo import card

    if card._load_pic() is None:
        return ["[状态徽章] pic 包不可用，无法读取模板常量"]
    import importlib
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo_roster")
    return list(tpl.state_badge_problems())


def card_problems() -> list[str]:
    """卡片数据层必须透出结构化多维字段，否则玩家看到的还是老样子。"""
    from doupo import card, core, factions as fac

    p = core.create_player("10001", "闸门", "20001")
    p["lv"] = 50
    p["faction"] = TARGET_FACTION
    p["faction_name"] = fac.FACTIONS[TARGET_FACTION]["name"]
    core.skill_pool(p)

    out = _skills_shape_problems(card.build_skills_data(p))
    roster = card.build_roster_data(p)
    out += _roster_shape_problems(roster)
    out += source_problems(roster)
    out += badge_problems()

    # 专用模板接线（源码契约，防止被顺手改回通用列表卡）
    import inspect
    if '"doupo_roster"' not in inspect.getsource(card.render_roster):
        out.append("[卡片] render_roster 没走专用模板 doupo_roster（会退回无分组表格）")
    return out


# ---------------------------------------------------------------------------
# 5. 反例注入（自举：闸门必须真的会报错）
# ---------------------------------------------------------------------------
def selftest() -> list[tuple[str, bool]]:
    """把这次真实犯过的错放回去，检查必须报错；全 True 才算闸门有效。"""
    entries = _faction_entries(TARGET_FACTION)
    if not entries:
        return [("读到天冥宗斗技", False)]
    name0 = entries[0]["name"]
    rec0 = dict(book.SKILL_BOOK.get(name0, {}))
    res: list[tuple[str, bool]] = []

    saved = book.SKILL_BOOK.get(name0)
    book.SKILL_BOOK[name0] = dict(rec0, role="", effects=[], gate="")
    res.append(("清空 role/effects/gate", bool(struct_problems(entries))))
    book.SKILL_BOOK[name0] = dict(rec0, effects=[{"t": "wat", "v": 0.5}])
    res.append(("塞入未知效果类型", bool(struct_problems(entries))))
    book.SKILL_BOOK[name0] = dict(rec0, effects=[{"t": "dot", "v": 9}])
    res.append(("效果数值越界", bool(struct_problems(entries))))

    # 低伤纯数字：构造一个倍率 1.0、无任何维度的假条目
    fake = {"name": name0, "dmg": 1.0, "tier": "普通", "rank": "黄阶低级"}
    book.SKILL_BOOK[name0] = {"role": "输出", "effects": [], "gate": "", "desc": "造成 100% 伤害。"}
    res.append(("低伤且无战术维度", bool(balance_problems([fake]))))
    # 高威力无 gate
    book.SKILL_BOOK[name0] = {"role": "爆发", "effects": [{"t": "pierce", "v": 0.3}],
                              "gate": "", "desc": "造成 200% 伤害，无视 30% 防御。"}
    res.append(("高威力却没有 gate", bool(balance_problems([dict(fake, dmg=2.0)]))))
    # 图文不一致：desc 说 30%，effects 是 5%
    book.SKILL_BOOK[name0] = {"role": "异常", "effects": [{"t": "dot", "v": 0.05}],
                              "gate": "某代价", "desc": "造成 140% 伤害，并附加 30% 减疗。"}
    res.append(("描述与结构数值不符", bool(desc_consistency_problems([fake]))))

    # ---- 名录卡结构反例（2026-11-15 第二次视觉重做后新增）----
    good_roster = {
        "groups": [{"tier": "普通", "count": 1, "rows": []}],
        "rows": [{"name": "示例", "role": "续航", "state_kind": "on",
                  "meta": "〔本宗〕黄阶低级 · 攻击 · 斗气 20%",
                  "desc": "斗气凝于掌心，一掌拍出，造成伤害并吸取对方气血回复自身；"
                          "掌风带起一阵血色涟漪，被扫中者伤口发黑，久不能愈。",
                  "chips": [{"label": "伤害", "value": "×1.00"},
                            {"label": "吸血", "value": "25%"}],
                  "gate": "某代价"}],
    }
    res.append(("名录卡结构完整时不应报错", not _roster_shape_problems(good_roster)))
    res.append(("名录卡退化成平铺表格（删 groups）",
                bool(_roster_shape_problems(dict(good_roster, groups=[])))))
    res.append(("名录卡首枚 chip 不是伤害倍率", bool(_roster_shape_problems(
        {"groups": good_roster["groups"],
         "rows": [dict(good_roster["rows"][0],
                       chips=[{"label": "吸血", "value": "25%"},
                              {"label": "破防", "value": "30%"}])]}))))
    res.append(("名录卡只剩一个数值（chips 不足 2 枚）", bool(_roster_shape_problems(
        {"groups": good_roster["groups"],
         "rows": [dict(good_roster["rows"][0],
                       chips=[{"label": "伤害", "value": "×1.00"}])]}))))
    res.append(("名录卡副行重复拼词", bool(_roster_shape_problems(
        {"groups": good_roster["groups"],
         "rows": [dict(good_roster["rows"][0],
                       meta="〔本宗〕 · 秘法 · 秘法 · 斗气 50%")]}))))
    res.append(("四槽卡 desc 退回 brief", bool(_skills_shape_problems(
        {"rows": [{"role": "输出", "chips": [{"label": "破防", "value": "30%"}],
                   "gate": "代价", "desc": "一句短话"}]}))))
    res.append(("名录卡没有描述（只剩 brief）", bool(_roster_shape_problems(
        {"groups": good_roster["groups"],
         "rows": [dict(good_roster["rows"][0], desc="")]}))))
    res.append(("描述没剥离「限制：」段（与代价行重复）", bool(_roster_shape_problems(
        {"groups": good_roster["groups"],
         "rows": [dict(good_roster["rows"][0],
                       desc="斗气凝血成掌，造成伤害并附加血毒。限制：气血虚时威力骤降，"
                            "对无血之物无效。")]}))))

    # ---- 描述首句来源（2026-11-15 用户纠正：不许写原著章节号）----
    phrase = "天冥宗易尘所使"
    body = "斗气凝血成掌，一掌拍出，血毒随掌力渗入，造成 140% 伤害。"
    good_src = {"rows": [
        {"name": "血煞掌", "has_source": True, "source_phrase": phrase,
         "desc": phrase + "，" + body},
        {"name": "无来源技", "has_source": False, "source_phrase": "",
         "desc": body},
    ]}
    res.append(("来源并进描述首句时不应报错", not source_problems(good_src)))
    res.append(("删掉描述首句的技能来源", bool(source_problems(
        {"rows": [dict(good_src["rows"][0], desc=body), good_src["rows"][1]]}))))
    res.append(("旧写法：原著第X章「标题」载（用户纠正的错）", bool(source_problems(
        {"rows": [dict(good_src["rows"][0],
                       desc="原著第一千一百九十章「承让」载，天冥宗易尘所使。" + body),
                  good_src["rows"][1]]}))))
    res.append(("来源单独成句、没并入施展流程", bool(source_problems(
        {"rows": [dict(good_src["rows"][0], desc=phrase + "。" + body),
                  good_src["rows"][1]]}))))
    res.append(("有来源却没标 has_source", bool(source_problems(
        {"rows": [dict(good_src["rows"][0], has_source=False), good_src["rows"][1]]}))))
    res.append(("给无来源技能编造原著章节", bool(source_problems(
        {"rows": [good_src["rows"][0],
                  dict(good_src["rows"][1], desc="原著第五百章「杜撰」载。" + body)]}))))
    res.append(("把来源写成独立标题「出处：」", bool(source_problems(
        {"rows": [dict(good_src["rows"][0], source_phrase="出处：" + body,
                       desc="出处：" + body), good_src["rows"][1]]}))))
    res.append(("把「游戏自设」当成技能来源", bool(source_problems(
        {"rows": [dict(good_src["rows"][0], source_phrase="原著无载 · 游戏自设。",
                       desc="原著无载 · 游戏自设。" + body), good_src["rows"][1]]}))))

    # ---- 右上角状态徽章（用户「更明显更显眼」）----
    from doupo import card as _card
    _card._load_pic()
    import importlib
    tpl = importlib.import_module(_card._PKG_NAME + ".templates.doupo_roster")
    res.append(("状态徽章正常时不应报错", not tpl.state_badge_problems()))
    res.append(("徽章退回 12px / 18px 小胶囊", bool(tpl.state_badge_problems(fs=12, h=18))))
    pale = {"on": ("#FBEEDD", "#EBC79A", "#9E1B32"),
            "learn": ("#F7F1E9", "#E6DCCB", "#8A7C6B"),
            "lock": ("#F7F1E9", "#E6DCCB", "#8A7C6B")}
    res.append(("徽章退回淡底且可学/未解锁同色",
                bool(tpl.state_badge_problems(style=pale))))

    # ---- 无机制白名单诚实性 ----
    saved_reg = book.NO_EFFECT_SKILLS
    book.NO_EFFECT_SKILLS = tuple(saved_reg) + ("碎石掌",)
    res.append(("把有效果原子的技塞进无机制白名单",
                bool(no_effect_registry_problems())))
    book.NO_EFFECT_SKILLS = saved_reg

    # ---- 全宗门覆盖（用户「优化剩余的所有宗门技能展示」）----
    other_fid = ("xiaojia" if "xiaojia" in book.FACTION_SKILLS
                 else tuple(book.FACTION_SKILLS)[0])
    other_name = _faction_entries(other_fid)[0]["name"]
    saved_other = book.SKILL_BOOK.get(other_name)
    book.SKILL_BOOK[other_name] = dict(saved_other, role="", effects=[], gate="")
    res.append(("清空非天冥宗某派斗技的多维字段",
                bool(struct_problems(_faction_entries(other_fid), other_fid))))
    if saved_other is not None:
        book.SKILL_BOOK[other_name] = saved_other

    if saved is not None:
        book.SKILL_BOOK[name0] = saved
    return res


# ---------------------------------------------------------------------------
def no_effect_registry_problems() -> list[str]:
    """白名单本身必须诚实：登记的技确实无 effects、有 role/gate、理由非空。

    防止有人把有机制却写坏的技塞进白名单来绕过「空 effects」检查。
    """
    out: list[str] = []
    if len(set(book.NO_EFFECT_SKILLS)) != len(book.NO_EFFECT_SKILLS):
        out.append("[白名单] NO_EFFECT_SKILLS 有重复技名")
    for n in book.NO_EFFECT_SKILLS:
        rec = book.SKILL_BOOK.get(n)
        if rec is None:
            out.append("[白名单] %s 不在 SKILL_BOOK" % n)
            continue
        if rec.get("effects"):
            out.append("[白名单] %s 明明有 effects，却登记成无独立机制" % n)
        if not rec.get("role") or not rec.get("gate"):
            out.append("[白名单] %s 缺 role/gate，不能用白名单掩盖" % n)
        if not str(book.NO_EFFECT_REASONS.get(n) or "").strip():
            out.append("[白名单] %s 没写理由" % n)
    return out


def all_faction_card_problems() -> list[str]:
    """按天冥宗同款展示，全 25 派名录卡都必须消费多维字段。

    用户 2026-11-15：「按照目前天冥宗的技能展示，优化剩余的所有宗门技能展示」。
    数据齐了模板自动生效，这里逐派跑 build_roster_data 确认没有一派退化成
    「只有伤害数字 + 一句 brief」。
    """
    from doupo import card, core, factions as fac

    out: list[str] = []
    for fid in book.FACTION_SKILLS:
        p = core.create_player("10001", "闸门", "20001")
        p["lv"] = 50
        p["faction"] = fid
        p["faction_name"] = fac.FACTIONS[fid]["name"]
        core.skill_pool(p)
        d = card.build_roster_data(p)
        for x in (_roster_shape_problems(d) + source_problems(d)):
            out.append("[%s] %s" % (fid, x))
    return out


def main() -> int:
    # 2026-11-15 23:5x：从「只查天冥宗」升级为「全 25 派」
    all_fids = tuple(book.FACTION_SKILLS)
    sp: list[str] = []
    bp: list[str] = []
    dp: list[str] = []
    total = 0
    for fid in all_fids:
        ents = _faction_entries(fid)
        total += len(ents)
        sp += struct_problems(ents, fid)
        bp += balance_problems(ents, fid)
        dp += desc_consistency_problems(ents)
    cp = card_problems() + all_faction_card_problems()
    rp = no_effect_registry_problems()
    st = selftest()
    problems = sp + bp + dp + cp + rp
    for name, caught in st:
        if not caught:
            problems.append("[selftest] 反例没被拦住: %s" % name)

    print("斗技多维度闸门 · 全 %d 派 / %d 门次（图鉴 %d 门）"
          % (len(all_fids), total, len(book.SKILL_BOOK)))
    print("  结构完整（role/effects/gate/desc）：%s" % ("PASS" if not sp else "FAIL %d 项" % len(sp)))
    print("  反无脑（低伤有维度/高威力有代价）：%s" % ("PASS" if not bp else "FAIL %d 项" % len(bp)))
    print("  图文一致（desc 百分比能在 effects 对上）：%s" % ("PASS" if not dp else "FAIL %d 项" % len(dp)))
    print("  卡片已消费多维字段：%s" % ("PASS" if not cp else "FAIL %d 项" % len(cp)))
    look = [p for p in cp if p.startswith(("[来源]", "[状态徽章]"))]
    print("  来源首句 / 状态徽章醒目：%s"
          % ("PASS" if not look else "FAIL %d 项" % len(look)))
    print("  无机制白名单诚实性（%d 门）：%s"
          % (len(book.NO_EFFECT_SKILLS),
             "PASS" if not rp else "FAIL %d 项" % len(rp)))
    print("  反例注入：%d/%d 被拦" % (sum(1 for _, ok in st if ok), len(st)))
    for name, ok in st:
        print("    %s %s" % ("OK  " if ok else "MISS", name))
    for p in problems:
        print("  " + p)
    if "--selftest" in sys.argv:
        return 0 if all(ok for _, ok in st) else 1
    if "--check" in sys.argv and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
