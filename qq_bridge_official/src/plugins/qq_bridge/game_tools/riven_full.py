# -*- coding: utf-8 -*-
"""game_tools/riven_full.py —— 紫卡全面分析（Warframe Rabbit 完整分析卡）数据层。

版式与 ``pic/templates/riven_full.py`` 一一对应（655×1920 设计空间）。

数据纪律（与紫卡融合/趋势一致，绝不编数）：
  * **主路径＝紫卡截图 OCR**：截图 → ``riven._parse_ocr`` → 真实词条/等级/倾向，
    再走 ``riven_card._sim``（击杀效率）/ ``_scores``（评分）/ ``build_card_data``（区间与均值）。
  * **武器名路径**：只填真实可查的数据（WFM 挂牌中位、本地配卡库 MOD、武器倾向）；
    依赖截图的词条行/融合行/评分区**如实标「需紫卡截图」**，不编造数值。
  * ``reference_sample()`` 是**参考卡版式复刻夹具**（sample=True），只用于像素级对齐
    回归测试与 ``#紫卡全面 样例`` 显式预览；卡片脚注会明确标注「版式样例·非实时数据」，
    绝不冒充真实查询结果。
  * WFM 拍卖不暴露 fuse 字段；「词条价差预估」是模型估算，脚注恒标「模型预估 谨慎参考」。
  * 价格两盒由 ``riven_price`` 按真实匹配样本分层估价（2026-11-10 对齐 Rabbit 参考卡），
    不再把「暂无匹配样本」写死；「最近观测」用参考卡的相对时间口径。
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

logger = logging.getLogger("qq_bridge")

# market 未获取时的可见失败态（绝不回退静态表 / 绝不填「暂无」冒充成功）。
_EMPTY_MARKET = {"status": "error", "error": "market 未获取", "slug": "", "sample_n": 0}


__all__ = [
    "RivenFullError", "FULL_SOURCE", "reference_sample", "riven_full_data",
    "full_text", "MOD_ASSET_DIR", "_live_mod_image_by_en",
    "_build_mods_from_build", "_replaced_by_riven",
]


class RivenFullError(Exception):
    """紫卡全面分析数据层错误（武器未收录 / OCR 解析失败等）。"""


FULL_SOURCE = (
    "词条/区间：紫卡截图 OCR + 本仓库倾向表｜击杀效率：本仓库 DPS 模型（无/当前/最强紫卡，"
    "最强=该武器理论最优组合）｜市价：Warframe.Market 挂牌要价·非成交价（中位价，单位白金）"
    "｜词条价差为模型预估，谨慎参考｜WFM 不暴露 fuse，不编造融合等级市价"
    "｜融合属性清单来自 DE 官方导出，继承方式官方未公开，融合表仅列候选、数值沿用当前落点示意"
    "｜配卡：按本武器真实社区 build（warframe.today/本地配卡库），卡图为 DE 官方立绘+官方横版卡框合成"
    "（DE 不发布成品卡图，全平台同此），「紫卡可替」=词条同类 stat 候选标注、非精确计算"
)

MOD_ASSET_DIR = Path(__file__).resolve().parents[1] / "pic" / "templates" / "riven_full_assets"

#: 第 8 格「当前紫卡」固定用原始母版 `mod_1_3.png`（紫色星标 + 「当前紫卡」字样，
#: 2026-11-12 用户明确认可的效果）。**唯一**固定资产：紫卡是当前这张、不随武器类型变，
#: 无法由 MOD 数据表合成，故用母版。其余 MOD 卡一律**运行时实时生成 + 缓存**
#: （见 :func:`_live_mod_image`），禁止把卡图当静态资产硬编码进仓库——
#: 用户 2026-11-12 明确：「其他 mod 都是实时获取或者从缓存拿，禁止硬编码」。
_CURRENT_RIVEN_IMAGE = MOD_ASSET_DIR / "mod_1_3.png"

#: 数据源根（与 ``tools/gen_mod_cards.py`` 同款；宿主/容器 ``/app`` 通用）。
#: 容器由 ``./data:/app/data`` 挂载，DE 立绘缓存 2160 张 + Mods.json 运行期恒可用。
_DATA_DIR = Path(__file__).resolve().parents[4] / "data"
_MOD_FULL_JSON = _DATA_DIR / "warframe_items" / "mod_full.json"

#: 横版 MOD 卡**内存缓存**（同 ``build_card.py::_MOD_CARD_CACHE`` 范式）：
#: {中文卡名 → PNG bytes}。渲染线程内同一次/多次出卡复用，绝不重复合成、绝无磁盘静态资产。
_LIVE_MOD_CACHE: dict[str, bytes] = {}
#: zh→en 反查缓存（从 mod_full.json 事实源派生，零手工映射）。
_ZH_TO_EN: dict[str, str] | None = None
#: en→zh 反查缓存（同一事实源反向；供真实 build 缺图占位框显示官方中文名）。
_EN_TO_ZH: dict[str, str] | None = None


def _zh_to_en_from_mod_full() -> dict[str, str]:
    """从 ``mod_full.json`` 事实源反查「中文卡名 → 英文小写名」（零硬编码映射）。

    ``mod_full.json`` 结构 ``{en_lower: {zh, type, rarity, polarity, drain, max:[...]}}``。
    运行期只读盘一次并缓存；文件缺失/损坏返回空 dict，调用方诚实降级 None。
    """
    global _ZH_TO_EN
    if _ZH_TO_EN is not None:
        return _ZH_TO_EN
    out: dict[str, str] = {}
    try:
        mp = json.loads(_MOD_FULL_JSON.read_text(encoding="utf-8")).get("map", {})
        for en, v in mp.items():
            zh = str((v or {}).get("zh") or "").strip()
            if zh and zh not in out:
                out[zh] = en.lower()
    except Exception as e:  # noqa: BLE001 — 事实源缺失只降级，不崩、不编造
        logger.warning("riven_full_mod_full_load_failed: %s: %s", type(e).__name__, e)
    _ZH_TO_EN = out
    return out


def _en_to_zh_from_mod_full() -> dict[str, str]:
    """``mod_full.json`` 事实源反查 en(小写) → 官方中文卡名（零硬编码）。"""
    global _EN_TO_ZH
    if _EN_TO_ZH is not None:
        return _EN_TO_ZH
    out: dict[str, str] = {}
    try:
        mp = json.loads(_MOD_FULL_JSON.read_text(encoding="utf-8")).get("map", {})
        for en, v in mp.items():
            zh = str((v or {}).get("zh") or "").strip()
            if zh:
                out[en.lower()] = zh
    except Exception as e:  # noqa: BLE001
        logger.warning("riven_full_en_to_zh_load_failed: %s: %s", type(e).__name__, e)
    _EN_TO_ZH = out
    return out


def _live_mod_image_by_en(en: str) -> io.BytesIO | None:
    """英文小写卡名 → **运行时实时合成**的横版 MOD 真卡（io.BytesIO），内存缓存复用。

    🔴 这是配卡出图的**首选出口**（2026-11-12 用户推翻 c22/c23 后重构）：
    真实 build 链路（``builds.json`` / ``build_fetch.ensure_build``）每张卡**自带 en
    英文字段**，直接喂给 :func:`tools.gen_mod_cards.render_mod_card_wide`，**从根上
    绕开「中文卡名反查英文」这个唯一失败点**——c22 那 6 个深色占位块的根因就是
    固定类别表里 14/28 个幽灵译名在 mod_full.json 反查不到，提前 return None，
    render 根本没被调用（不是「自己画」，是降级占位框）。

    ``render_mod_card_wide`` 用**官方素材**合成、零自画：DE 官方立绘
    （``art/`` + ``de_manifest.json``）+ 2026-11-02 vendored 的 warframe.today
    横版官方卡框（``modFrames/wide/``），内部按 ``Mods.json`` 的 rarity 自动分档，
    带出中文名 / 满级耗能 / 极性 / 底部等级点。DE 官方不发布成品卡图（引擎内动态
    合成），WFCD mod-generator / warframe.today / Rabbit 全走同一条「官方素材合成」，
    这就是横版真卡的**唯一真实来源**，且一直在仓库里。

    * 缓存 key = en（真实 build 的 en 字段，事实源，零手工映射）；
    * 合成失败 / 数据源缺失 → None，模板诚实画占位框 + 真名，绝不编造；
    * 返回**新** BytesIO（缓存不可变 bytes），规避指针复位问题。
    """
    key = (en or "").strip().lower()
    if not key:
        return None
    if key not in _LIVE_MOD_CACHE:
        try:
            import sys as _sys
            _root = str(Path(__file__).resolve().parents[4])
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from tools.gen_mod_cards import render_mod_card_wide  # type: ignore
            raw = render_mod_card_wide(key)
            _LIVE_MOD_CACHE[key] = raw or b""
        except Exception as e:  # noqa: BLE001 — 单卡失败不拖垮整张，诚实降级
            logger.warning("riven_full_live_mod_en_failed: %s: %s", type(e).__name__, e)
            _LIVE_MOD_CACHE[key] = b""
    raw = _LIVE_MOD_CACHE.get(key) or b""
    return io.BytesIO(raw) if len(raw) > 500 else None


def _live_mod_image(name: str) -> io.BytesIO | None:
    """中文卡名兼容入口（仅 ``reference_sample`` 旧路径用；生产走 :func:`_live_mod_image_by_en`）。

    生产出卡（``_build_mods_from_build``）一律用 en 直喂，不再走 zh 反查——
    这里保留只为不破坏历史调用；反查失败仍诚实降级 None。
    """
    zh = (name or "").strip()
    if not zh:
        return None
    en = _zh_to_en_from_mod_full().get(zh)
    return _live_mod_image_by_en(en) if en else None

# 参考卡实测的 8 张 MOD 缩略图（前景内容，非背景花纹），由参考卡逐像素裁切
_MOD_NAMES = [
    "深层冷冻", "病原弹头", "致命洪流", "火焰装填 Prime",
    "黄蜂蛰刺", "弹头扩散", "持续火力 Prime", "当前紫卡",
]

# 紫卡最多 4 条词条（Warframe 规则：3正1负 / 2正1负 等）。旧实现写死 [:3]
# 把第 4 条（通常是诅咒负词条）整条丢掉——2026-11-11 用户图1/图2 对照根因之一。
# 模板 stats 区对 4 条走紧凑行距（见 pic/templates/riven_full.py），融合表本就 4 行。
_MAX_STATS = 4

_HONEST_HINT = "高低值以对应倾向的均值为基准，正常范围为±10%（卡面取整可能略有偏差）。"
_NEED_SHOT = "需紫卡截图"


def _mod_assets() -> list[dict]:
    """**仅参考卡版式样例**用：8 张 MOD 缩略卡（参考卡逐像素裁切的枪卡图）。

    ⚠️ 只服务 ``reference_sample()``（sample=True，脚注标「版式样例·非实时数据」）
    复刻参考卡版式。生产出卡（``riven_full_data``）**禁止**走这里——旧实现把所有
    武器都套这 8 张写死枪卡，近战武器因此显示「深层冷冻/病原弹头」（用户图1
    「配卡完全不符合这个武器」根因，2026-11-11）。生产统一走 ``_build_mods``。
    """
    out = []
    for r in range(2):
        for c in range(4):
            p = MOD_ASSET_DIR / ("mod_%d_%d.png" % (r, c))
            out.append({"name": _MOD_NAMES[r * 4 + c],
                        "image": str(p) if p.is_file() else None})
    return out


#: riven_disp.json 的 7 类 type → riven_card 唯一真配卡表键。
#: 配卡**名称**一律从 ``riven_card._BUILD`` / ``_MELEE_BUILD`` 派生，这里只做类型归类，
#: 绝不在此另抄一份 MOD 名（事实源单一，防手工表漂移）。
#: 归类依据：Zaw=近战、Kitgun=手枪、Archgun=枪械（按步枪通用 DPS 桶）。
_TYPE_TO_BUILD = {
    "Melee": "melee", "Zaw": "melee",
    "Pistol": "Pistol", "Kitgun": "Pistol",
    "Rifle": "Rifle", "Archgun": "Rifle",
    "Shotgun": "Shotgun",
}
_BUILD_LABEL = {
    "melee": "基于击杀效率配卡 · 近战通用",
    "Pistol": "基于击杀效率配卡 · 手枪通用",
    "Rifle": "基于击杀效率配卡 · 步枪通用",
    "Shotgun": "基于击杀效率配卡 · 霰弹通用",
}
_NEED_SHOT_BUILD = "基于击杀效率配卡（需紫卡截图确认武器类型）"


def _build_key(wtype: str | None) -> str:
    """riven_disp type → riven_card 配卡表键；未知/缺失返回空串（不套枪卡）。"""
    return str(_TYPE_TO_BUILD.get((wtype or "").strip(), "") or "")


def _build_mods(wtype: str | None) -> list[dict]:
    """按武器类型从 ``riven_card`` 唯一真配卡表派生 8 格配卡（生产出卡唯一出口）。

    纪律：
      * 卡名只来自 ``riven_card._BUILD`` / ``_MELEE_BUILD``，本模块零手工卡名；
      * ``image`` = :func:`_live_mod_image` **运行时实时合成 + 内存缓存**的横版
        MOD 卡（zh→en 从 mod_full.json 事实源反查 → render_mod_card_wide 用真 DE
        立绘 + 稀有度卡框实时出 370×185 深色满幅金属边框卡，零外呼、零静态资产）。
        **任何**武器类型的真表卡都能实时出图（不再只有近战 7 张有图）；合成失败
        才返回 None 走占位框 + 真名，**绝不张冠李戴**；
      * 第 8 格「当前紫卡」是**唯一**固定资产：用母版 ``mod_1_3.png``（紫色星标，
        用户认可）。它不随武器类型变、无法由 MOD 数据表合成——「当前紫卡」对该
        武器恒成立，故保留母卡；其余 MOD 禁静态硬编码（用户 2026-11-12 明确）；
      * 未知/缺失 type（如仅给武器名、无 OCR）返回 8 格「需紫卡截图」，
        **绝不默认套 Rifle 枪卡**——没真实类型就不假装给出针对该武器的配卡。
    """
    key = _build_key(wtype)
    if not key:
        return [{"name": _NEED_SHOT, "image": None} for _ in range(8)]
    from . import riven_card as rc
    names = list(rc._MELEE_BUILD) if key == "melee" else list(rc._BUILD[key])
    out = [{"name": n, "image": _live_mod_image(n)} for n in names]
    riven_img = str(_CURRENT_RIVEN_IMAGE) if _CURRENT_RIVEN_IMAGE.is_file() else None
    out.append({"name": "当前紫卡", "image": riven_img})
    return out[:8]


# ---------------------------------------------------------------------------
# 🔴 按「具体武器真实 build + 紫卡词条替换」装配配卡（2026-11-12 重构，推翻固定表）
# ---------------------------------------------------------------------------
#: 紫卡词条（中文名/效果行）→ 可替换的原卡类别关键词。事实源派生、零手工卡名。
#: 社区通法（Overframe / Fandom / Reddit 多源一致）：紫卡某词条覆盖的 stat，
#: 可由真实 build 里「提供同类 stat」的那张原卡让位。这里只做**诚实标注**
#: （在卡名后加「·紫卡可替」），不改卡图、不换槽、不编造数值——是否是真最优
#: 取决于玩家实际词条 roll 与极化，故只提示候选，绝不声称「一定替换」。
_STAT_CATEGORY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("暴击几率", ("暴击几率", "暴击率")),
    ("暴击伤害", ("暴击伤害",)),
    ("触发几率", ("触发几率", "触发率")),
    ("多重射击", ("多重射击",)),
    ("攻击速度", ("攻击速度", "攻速")),
    # 元素按**具体元素**分档，避免「紫卡带冰」把整元素卡全标可替：
    ("毒素", ("毒素伤害", "毒伤")),
    ("冰冻", ("冰冻伤害", "冰伤")),
    ("火焰", ("火焰伤害", "火伤")),
    ("电流", ("电流伤害", "电伤")),
)
#: 宽泛「伤害」类 purposely **不**进类别表：基伤/近战伤害/元素伤害同桶但不可精确
#: 判定谁让位，标了反而误导（几乎每张攻击卡都含「伤害」二字→全场「替」）。宁可不标。


def _stat_category(text: str) -> str:
    """中文词条名 / mod 效果行 → 精确归类（暴击/触发/多重/攻速/具体元素/空）。

    只认 mod_full.json 效果行与紫卡词条名里的**事实文本**，不维护任何卡名表。
    故意排除宽泛「伤害」——同类匹配要精确才诚实，模糊就宁可不标。
    """
    s = (text or "").replace(" ", "")
    for cat, kws in _STAT_CATEGORY:
        if any(k in s for k in kws):
            return cat
    return ""


def _replaced_by_riven(mod_en: str, parsed_stats: list[dict]) -> bool:
    """该真实 build 原卡是否被紫卡某词条覆盖（供卡名诚实标注「紫卡可替」）。

    * 从 ``mod_full.json`` 派生这张 mod 的满级效果行 → 归类；
    * 紫卡每条 stat 的 name → 归类；
    * 同类即视为「可被紫卡覆盖的候选」（玩家可考虑让位，非强制）。
    查不到效果行 / 归类为空 → False（不标注，绝不瞎猜）。
    """
    try:
        mf = json.loads(_MOD_FULL_JSON.read_text(encoding="utf-8")).get("map") or {}
        eff = mf.get((mod_en or "").lower()) or {}
        lines = eff.get("max") or []
        mod_cats = {_stat_category(str(x)) for x in lines if isinstance(x, str)}
        mod_cats.discard("")
        if not mod_cats:
            return False
        riven_cats = {_stat_category(str((s or {}).get("name") or ""))
                      for s in (parsed_stats or [])}
        riven_cats.discard("")
        return bool(mod_cats & riven_cats)
    except Exception as e:  # noqa: BLE001 — 事实源缺失只不标注，不出错
        logger.warning("riven_full_replace_cat_failed: %s: %s", type(e).__name__, e)
        return False


def _build_mods_from_build(build_mods: list[dict],
                           parsed_stats: list[dict] | None = None) -> list[dict]:
    """按**具体武器的真实 build**装配 8 格配卡（生产出卡唯一新出口）。

    :param build_mods: ``builds.json`` sets[0].mods / ``build_fetch`` 的真实 mod 列表，
        每项 **必须带 ``en``**（英文字段，事实源），可选 ``zh``/``rank``/``note``。
    :param parsed_stats: 紫卡 OCR 词条行（``riven_card.build_card_data`` 的 stats），
        用于「紫卡可替」诚实标注；None 则不标注。

    纪律（对齐用户 2026-11-12 定稿）：
      * 卡**名**、卡**图**都来自真实 build 的 en 字段 → ``_live_mod_image_by_en``
        官方素材合成横版真卡；**en 直喂，零 zh 反查**（杜绝 c22 幽灵译名深色块）；
      * **不固定类别表**：这套卡是这把武器的真实社区 build，随武器变；
      * 紫卡词条只做「·紫卡可替」候选标注（社区通法），不改卡图、不编数；
      * 第 8 格固定「当前紫卡」母卡；不足 8 格用诚实占位，多余截断；
      * 任一 mod 缺 en → 该格退化文字，**不用别的武器卡顶替**。
    """
    out: list[dict] = []
    for m in (build_mods or []):
        en = str((m or {}).get("en") or "").strip().lower()
        if not en or (m or {}).get("placeholder"):
            # Exilus 空槽等占位项：诚实文字，不画假卡
            out.append({"name": str((m or {}).get("name") or "Exilus 槽"),
                        "image": None, "note": "该来源未给出"})
            continue
        # 卡名（仅缺图占位框显示；真图路径下卡面中文名由生成器按 en 自动画）：
        # 优先 build 条目的中文 name/zh，缺失则用 mod_full.json 的 en→zh 事实源，
        # 保证联网抓取的英文条目（如 amalgam serration）也显示官方中文名。
        zh = (str((m or {}).get("name") or "").strip()
              or str((m or {}).get("zh") or "").strip()
              or _en_to_zh_from_mod_full().get(en) or en)
        replaced = _replaced_by_riven(en, parsed_stats or [])
        out.append({"name": zh + (" ·紫卡可替" if replaced else ""),
                    "name_plain": zh,       # 卡面/角标用清爽卡名（不含标注文字）
                    "replace": replaced,    # 模板据此画「紫卡可替」角标（真图也可见）
                    "image": _live_mod_image_by_en(en),
                    "note": str((m or {}).get("note") or "")})
    riven_img = str(_CURRENT_RIVEN_IMAGE) if _CURRENT_RIVEN_IMAGE.is_file() else None
    out.append({"name": "当前紫卡", "image": riven_img})
    return out[:8]


def _build_label_for_weapon(weapon_zh: str, source: str = "") -> str:
    """配卡区标题：标明是「按此武器真实 build」，来源可审计。"""
    w = (weapon_zh or "").strip() or "该武器"
    tag = (" · " + source) if source else ""
    return "基于 %s 真实配卡%s" % (w, tag)


def _build_label(wtype: str | None) -> str:
    """配卡区标题口径随真实武器类型变化；未知类型如实标「需截图」。"""
    return _BUILD_LABEL.get(_build_key(wtype), _NEED_SHOT_BUILD)


def _wtype_from_name(name: str) -> str:
    """武器名（中文/英文）→ riven_disp.json 的 type（唯一同源事实源）。

    只读复用 ``riven._load_riven_data()`` / ``riven._RIVEN['disp']``（与
    ``rank_eligibility`` 复用 ``warframe._wf_dict`` 同类做法，riven.py 零改）。
    容器真探针（2026-11-11）确认：绝路→Rifle、沙皇→Rifle、Coda Motovore→Melee、
    阿利乌双枪→Pistol 均精确命中；战甲名/外号查不到则落空串→诚实占位，绝不猜类型。
    ⚠️ 不可用 ``warframe._resolve_riven_weapon`` 的 slug 反查 disp 表——两套 key
    体系不同源（slug ``coda-motovore`` ≠ disp key ``Coda Motovore``）。
    """
    n = (name or "").strip().lower().replace(" ", "").replace("·", "")
    if not n:
        return ""
    try:
        from . import riven as _riven
        _riven._load_riven_data()
        disp = _riven._RIVEN.get("disp") or {}
    except Exception:  # noqa: BLE001 — 加载失败落空串，不编造类型
        return ""

    def norm(s: str) -> str:
        return (s or "").strip().lower().replace(" ", "").replace("·", "")

    # 1) 官方中文名精确（disp 表自带 zh，最可靠）
    for rec in disp.values():
        if norm(rec.get("zh")) == n:
            return str(rec.get("type") or "")
    # 2) 英文 key 精确
    for k, rec in disp.items():
        if norm(k) == n:
            return str(rec.get("type") or "")
    # 3) 包含匹配：仅当所有命中指向**同一 type** 才采信（防「绝」命中一堆武器乱套）
    hits = {str(rec.get("type") or "") for k, rec in disp.items()
            if n and (n in norm(k) or n in norm(rec.get("zh"))) and rec.get("type")}
    return next(iter(hits)) if len(hits) == 1 else ""


def _disp_filled(disp: float) -> int:
    """倾向点数（1.20 → 4 点，与 riven_card._dots 同口径）。"""
    try:
        v = float(disp)
    except (TypeError, ValueError):
        return 3
    if v >= 1.45:
        return 5
    if v >= 1.30:
        return 4
    if v >= 1.15:
        return 4
    if v >= 1.00:
        return 3
    if v >= 0.80:
        return 2
    return 1


def _f(v, nd: int = 1) -> str:
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v: float) -> str:
    try:
        return "%+.1f%%" % float(v)
    except (TypeError, ValueError):
        return "—"


def _kill_parts(base, cur, best) -> list[tuple[str, str]]:
    return [
        ("击杀效率：无紫卡 ", "dark"), (_f(base), "blue"),
        (" → 当前紫卡 ", "dark"), (_f(cur), "purple"),
        (" → 最强紫卡 ", "dark"), (_f(best), "green"),
    ]


def _gain_parts(gain: float, gain_max: float) -> list[tuple[str, str]]:
    return [
        ("紫卡增益：当前紫卡 ", "dark"), (_fmt_pct(gain * 100), "purple"),
        (" → 最强紫卡 ", "dark"), (_fmt_pct(gain_max * 100), "green"),
    ]


def _price_boxes(market: dict | None) -> tuple[dict, dict]:
    """价格两盒统一交给 ``riven_price``（真实匹配样本分层估价）。

    2026-11-10 之前这里把左盒 big/foot 写死「暂无匹配样本」、左右两盒填同一个
    ``med``，与 Rabbit 参考卡「匹配样本估价 / 零洗基准 / 词条价差模型」三套语义
    都不符。旧实现已删除，唯一实现见 ``riven_price.price_boxes``。
    """
    from . import riven_price
    return riven_price.price_boxes(market)


# ---------------------------------------------------------------------------
# 真实数据装配（OCR 驱动 / 武器名驱动）
# ---------------------------------------------------------------------------
def riven_full_data(weapon: str | None = None, parsed: dict | None = None,
                    mods: list[dict] | None = None,
                    card_data: dict | None = None,
                    market: dict | None = None,
                    build_mods: list[dict] | None = None,
                    build_source: str = "") -> dict:
    """组装「紫卡全面分析」卡数据。

    :param parsed: OCR 解析结果（``riven._parse_ocr`` 的返回）；有它才有真实词条行。
    :param card_data: ``riven_card.build_card_data(parsed, market)`` 的结构化结果（可选），
        含每条词条的 low/mean/high/dev/bar，避免重复计算。
    :param weapon: 武器名（中文/英文/外号），用于 WFM 挂牌与本地配卡库。
    :param market: ``riven_card.fetch_market_for_card(parsed)`` 的真实市场快照
        （**必填**：``build_card_data`` 2026-11-04 改为必填位置参数，漏传直接 TypeError）。
        缺省时退回可见失败态，不让卡面静默假装无数据。
    :param mods: 已装配好的配卡列表（[{name,image}]）；最高优先级，显式传值直接覆盖。
    :param build_mods: 🔴 **具体武器的真实 build mod 原始列表**（每项带 ``en`` 英文字段，
        来自 ``builds.json`` / ``build_fetch.ensure_build``；2026-11-12 重构）。给了它
        就走 :func:`_build_mods_from_build` 按 en 出**官方素材合成横版真卡** + 紫卡词条
        「可替」标注，**不再用固定类别表**；紫卡词条取自 ``parsed["stats"]``/card_data。
    :param build_source: 真实 build 来源标注（如 ``warframe.today`` / ``本地配卡库``），
        进配卡标题，来源可审计。
    """
    from . import riven_price
    observed = riven_price.observed_text((market or {}).get("ts"))
    # 模块 E：配卡/标题在下方武器名解析后按真实武器类型统一派生（见「配卡」块）。
    # base 里先放中性占位，避免任何路径漏判回落到写死枪卡。
    base: dict = {
        "status": "ok",
        "sample": False,
        "weapon_zh": "未指定武器",
        "riven_name": "",
        "level": None,
        "disp_value": "—",
        "disp_filled": 3,
        "stats": [],
        "market_note": "需紫卡截图（发紫卡截图 + #紫卡全面 获取真实词条）",
        "hint": _HONEST_HINT,
        "fusion_rows": [],
        "scores": [
            {"label": "伤害分", "value": "—", "label_color": "gray"},
            {"label": "市场分", "value": "—", "label_color": "gray"},
            {"label": "综合评分", "value": "—", "label_color": "red"},
        ],
        "observed": observed,
        # 中性占位：下方「模块 E 配卡」块按真实武器类型覆盖。绝不在这里默认 _mod_assets()
        # （那是参考卡版式样例专用的写死枪卡，套到生产出卡=近战显示枪卡，用户图1根因）。
        "build_title": _build_label(None),
        "mods": _build_mods(None),
        "kill_line": [],
        "gain_line": [],
        "cmd_groups": [],
        "footer": {"pc": "PC", "brand": "曼珠",
                   "tip": "*善用右键/长按提取文字"},
        "source": FULL_SOURCE,
    }
    left, right = _price_boxes(market)
    base["price_left"], base["price_right"] = left, right

    # ---- 武器名 ----
    w = (weapon or "").strip()
    if not w and parsed:
        w = str(parsed.get("weapon_zh") or parsed.get("weapon") or "")
    if w:
        base["weapon_zh"] = w

    # ---- 配卡装配（2026-11-12 重构：真实 build 优先，废除固定类别表主路径）----
    # 优先级：① 显式装配好的 mods（最高，调用方已处理）；
    #         ② build_mods=具体武器真实 build（带 en）→ 按 en 出官方素材合成横版真卡
    #            + 紫卡词条「可替」标注，标题标来源；**不再按武器类型套固定表**；
    #         ③ 都没有 → 旧「需紫卡截图」诚实占位（绝不默认套 Rifle 写死枪卡）。
    _wtype = str((parsed or {}).get("type") or "").strip() or _wtype_from_name(w)
    _riven_stats = list((card_data or {}).get("stats") or []) if card_data else []
    if mods is not None:
        base["mods"] = mods
        base["build_title"] = _build_label(_wtype)
    elif build_mods:
        # 紫卡词条来源：优先 card_data.stats（含 name），其次 parsed 直取。
        if not _riven_stats and parsed:
            # parsed["stats"] 每项 {en, zh, value, unit, sign}；中文词条名在 zh 键。
            _riven_stats = [{"name": str((s or {}).get("zh") or (s or {}).get("en") or "")}
                            for s in (parsed.get("stats") or [])]
        base["mods"] = _build_mods_from_build(build_mods, _riven_stats)
        base["build_title"] = _build_label_for_weapon(w, build_source)
        logger.info("riven_full_real_build weapon=%s source=%s mods=%d"
                    % (w, build_source or "?", len(build_mods)))
    else:
        base["mods"] = _build_mods(_wtype)
        base["build_title"] = _build_label(_wtype)

    if parsed:
        base["riven_name"] = str(parsed.get("riven_name") or "")
        base["level"] = parsed.get("rank")
        base["disp_value"] = _f(parsed.get("disp"), 2)
        base["disp_filled"] = _disp_filled(parsed.get("disp"))
    elif card_data:
        base["level"] = card_data.get("level")
        base["disp_value"] = str(card_data.get("disp_value") or "—")
        base["disp_filled"] = _disp_filled(card_data.get("disp"))
        base["riven_name"] = str(card_data.get("riven_name") or "")
        zh = str(card_data.get("weapon_zh") or "")
        if zh:
            base["weapon_zh"] = zh

    # ---- 指令区（真实口令，随武器名变化） ----
    wz = base["weapon_zh"]
    base["cmd_groups"] = [
        {"title": "[指令]查看紫卡其他数据",
         "rows": [["洗卡推荐 " + wz, "紫卡词条范围 " + wz],
                  ["词条价差 " + wz, "配卡指南 " + wz]],
         "note": '*使用"紫卡分析 完整 [截图]"可以获取更详细的分析数据.'},
        {"title": "[指令]查看类似紫卡价格",
         "rows": [["紫卡价格 " + wz],
                  ["wmr 触发几率 弹匣 带负 " + wz]],
         "note": ""},
    ]

    # ---- 真实词条行 / 评分 / 击杀效率（必须有 OCR parsed）----
    if parsed:
        try:
            from . import riven_card as rc
            cd = card_data or rc.build_card_data(
                parsed, market if market is not None else _EMPTY_MARKET)
            rows = []
            for s in (cd.get("stats") or [])[:_MAX_STATS]:
                dev_dir = s.get("dev_dir") or ""
                dev_mag = s.get("dev_mag") or ""
                dev = (dev_dir + dev_mag) if (dev_dir and dev_mag) else "—"
                rows.append({
                    "value": s.get("value") or "—",
                    "neg": bool(s.get("neg")),
                    "icon": "负" if s.get("neg") else "正",
                    "name": s.get("name") or "—",
                    "low": s.get("low") or "—",
                    "mean": s.get("mean") or "—",
                    "dev": dev,
                    "high": s.get("high") or "—",
                    "bar": float(s.get("bar") or 0.5),
                })
            base["stats"] = rows
            # 「市场负面参考」= 推荐负面词条的同分位价差，语义字段是 recs.neg
            # （成功态「滑暴 +91.1p/+105%…」/ 失败态「不可用（市场数据获取失败）」）。
            # 2026-11-11 之前误取 cd["market"]（市场整体状态串），失败态会把
            # 「获取失败（RivenMarketError: WFM 拍卖返回 NoneType 对象…）」整段
            # 原始异常画到卡面——用户图1「市场负面参考乱写」根因。
            # 严禁回退到 cd["market"]；recs 缺失也只给固定人话，不露异常。
            _recs = cd.get("recs") if isinstance(cd.get("recs"), dict) else {}
            _neg = str(_recs.get("neg") or "").strip()
            if not _neg or any(t in _neg for t in ("RivenMarketError", "NoneType",
                                                    "Traceback", "Exception")):
                _neg = "不可用（市场数据暂缺或已过期）"
            base["market_note"] = "*市场负面参考：" + _neg
            # 融合表诚实标注并入 hint（模板在融合表上方渲染、2 行上限）：
            # 保留原「±10% 区间」提示，补一句「融合仅列候选、继承方式未验证」。
            _h = str(cd.get("hint") or _HONEST_HINT)
            base["hint"] = _h + " 融合表仅列候选属性，官方未公开继承方式，谨慎参考。"

            wraw = rc._weapon_raw(str(parsed.get("weapon") or wz))
            if wraw:
                sim = rc._sim(parsed, wraw)
                sc = rc._scores(parsed, sim)
                base["kill_line"] = _kill_parts(
                    sim.get("ke_base"), sim.get("ke_cur"), sim.get("ke_best"))
                base["gain_line"] = _gain_parts(sc.get("gain", 0), sc.get("gain_max", 0))
                # 评级字母口径（2026-11-11）：sc 的三个值是 **0–100 分制**，必须用
                # ``_letter``（85→S/65→A/50→B…）。``_grade`` 是「卡面偏差百分比」口径
                # （p=(10+d)/20），把 0–100 分喂进去会把 11/62/81/100 **全变 S**——
                # 用户图1「伤害分/市场分/综合评分 全 S」根因。`_grade` 在 build_card_data
                # 里传 dev（偏差百分比）是正确用法，两者不可混用。
                base["scores"] = [
                    {"label": "伤害分", "value": "%.1f (%s)" % (
                        sc.get("dmg", 0), rc._letter(sc.get("dmg", 0))), "label_color": "gray"},
                    {"label": "市场分", "value": "%.1f (%s)" % (
                        sc.get("mkt", 0), rc._letter(sc.get("mkt", 0))), "label_color": "gray"},
                    {"label": "综合评分", "value": "%.1f (%s)" % (
                        sc.get("total", 0), rc._letter(sc.get("total", 0))), "label_color": "red"},
                ]
        except Exception as e:  # noqa: BLE001 — 模型失败如实标注，不编数
            logger.warning(f"riven_full_model_failed: {type(e).__name__}: {e}")
            base["market_note"] = "模型计算失败，词条原始值仍需核对截图"

    # ---- 融合词条分析：真实截图才有融合结论；否则如实标需截图 ----
    if parsed and base["stats"]:
        base["fusion_rows"] = _fusion_from_stats(base["stats"])
    else:
        base["fusion_rows"] = [{
            "state": "需截图", "pair": _NEED_SHOT, "pair_color": "gray",
            "result_parts": [(_NEED_SHOT, "gray")],
            "value": "—", "dev": "—", "bar": 0.0,
            "ticks": ["—", "—", "—"],
        }]

    return base


#: DE 官方 Public Export 的**紫卡融合（spliced）属性清单**（18 种，中文名）。
#: 事实源：``data/warframe_items/koishi_ref/riven-export-adapter.ts::splicedNames``
#: （koishi-plugin-warframe 随官方导出维护，Identity/translation only；数值仍来自官方）。
#: 🔴 边界：官方**只**给出「哪些属性属于融合属性」，**未公开任何融合数值继承算法**
#:   （koishi ``riven-data.md:76`` 亦明确「官方导出没有提供融合数值继承算法，不能将
#:   评分解释为已验证的客户端随机品级」）。因此本表只用于**列出候选属性名**，
#:   绝不声称某词条「融合后一定变成 X」、绝不编造融合溢价/市价/继承数值。
_FUSION_ATTRS = (
    "弱点伤害", "弱点暴击几率", "弹药效率", "异常状态伤害", "重击伤害",
    "重击蓄力速度", "震地伤害", "爆炸", "腐蚀", "毒气", "磁力", "辐射",
    "病毒", "对奥罗金伤害", "对科腐者伤害", "对炽蛇军伤害",
)
#: 卡面融合区统一脚注：把「官方无继承算法、仅列候选」写到用户能看到的地方。
_FUSION_REF_NOTE = ("融合属性清单来自 DE 官方导出；具体继承方式官方未公开，"
                    "本表仅列候选属性，数值沿用当前词条真实落点示意，谨慎参考。")


def _fusion_from_stats(stats: list[dict]) -> list[dict]:
    """由真实词条行生成「融合词条分析」表（2026-11-11 全量对齐图2 Rabbit 结构）。

    诚实性红线（优先于视觉对齐）：
      * 候选融合属性名来自官方 18 种真表 ``_FUSION_ATTRS``（轮换分配，**不宣称因果**）；
      * 数值 ``value/dev/bar/ticks`` 全部沿用**当前词条真实落点**，不编造继承值；
      * 每个候选结果拼 ``（参考）`` 标签，卡面 hint/脚注声明「继承方式未验证」。
    图2 Rabbit 的「连击持续时间=重击准备速度」是**社区流派建议**，非官方数据；
    本项目不写死这类未经验证的因果配方，只给「候选属性清单 + 真值示意」。
    """
    out = []
    for i, s in enumerate(stats[:_MAX_STATS]):
        cand = _FUSION_ATTRS[i % len(_FUSION_ATTRS)]
        out.append({
            "state": "可融合" if i == 0 else "可锁定",
            "pair": s["name"], "pair_color": "red" if s.get("neg") else "gray",
            "result_parts": [("候选 ", "orange"), (cand, "purple"), ("（参考）", "gray")],
            "value": "约" + str(s["value"]), "dev": s["dev"], "bar": s["bar"],
            "ticks": [s["low"], s["mean"], s["high"]],
        })
    return out


def full_text(data: dict | None = None) -> str:
    """出图失败时的纯文本降级。"""
    d = data or {}
    if d.get("status") != "ok":
        return d.get("msg") or "紫卡全面分析生成失败"
    tag = "【版式样例·非实时数据】" if d.get("sample") else ""
    lines = [tag + "【紫卡全面分析】" + str(d.get("weapon_zh") or "")]
    if d.get("riven_name"):
        lines.append("紫卡名：" + str(d["riven_name"]))
    lines.append("等级 Lv%s · 倾向 %s" % (d.get("level") or "—", d.get("disp_value") or "—"))
    for s in d.get("stats") or []:
        lines.append("  %s %s｜低 %s · 均 %s · %s · 高 %s"
                     % (s.get("value"), s.get("name"), s.get("low"), s.get("mean"),
                        s.get("dev"), s.get("high")))
    if not d.get("stats"):
        lines.append("  词条行：需紫卡截图")
    lines.append("市场：" + str((d.get("price_left") or {}).get("sub_val") or "暂无匹配样本"))
    lines.append("价差：" + str((d.get("price_right") or {}).get("big") or "暂无实盘样本")
                 + "（模型预估 谨慎参考）")
    if d.get("fusion_rows"):
        lines.append("融合：" + _FUSION_REF_NOTE)
    lines.append("数据源：" + str(d.get("source") or FULL_SOURCE))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 参考卡版式复刻夹具（仅用于像素级对齐回归 / 显式「样例」预览）
# ---------------------------------------------------------------------------
def reference_sample() -> dict:
    """严格照用户提供的参考卡逐字复刻的版式夹具（sample=True）。

    ⚠️ 数值来自参考卡截图，不是实时查询结果；卡片脚注会标注「版式样例·非实时数据」。
    用途：像素级对齐验证（与参考图并排核对），以及 ``#紫卡全面 样例`` 显式预览。
    """
    d = {
        "status": "ok", "sample": True,
        "weapon_zh": "阿利乌双枪", "riven_name": "ArmaDex", "level": 8,
        "disp_value": "1.20", "disp_filled": 4,
        "stats": [
            {"value": "+135.4%", "neg": False, "icon": "正", "name": "触发几率",
             "low": "120.3%", "mean": "133.7%", "dev": "高1.3%", "high": "147.0%", "bar": 0.53},
            {"value": "+79.1%", "neg": False, "icon": "正", "name": "弹匣容量",
             "low": "66.8%", "mean": "74.2%", "dev": "高6.5%", "high": "81.7%", "bar": 0.82},
            {"value": "-30.7%", "neg": True, "icon": "负", "name": "装填速度",
             "low": "26.7%", "mean": "29.7%", "dev": "高3.4%", "high": "32.7%", "bar": 0.72},
        ],
        "market_note": "*市场负面参考：[普通负]装填 -64.5p/-17%",
        "hint": _HONEST_HINT,
        "fusion_rows": [
            {"state": "可融合", "pair": "弹匣+装填", "pair_color": "red",
             "result_parts": [("弹药效率", "purple")],
             "value": "+71.2%", "dev": "高6.5%", "bar": 0.80,
             "ticks": ["60.1%", "66.8%", "73.5%"]},
            {"state": "可锁定", "pair": "弹匣", "pair_color": "gray",
             "result_parts": [("+弹药", "orange"), ("=收武器自动装填", "purple")],
             "value": "约142.4%", "dev": "高6.5%", "bar": 0.88,
             "ticks": ["120.3%", "133.7%", "147.0%"]},
            {"state": "可锁定", "pair": "装填", "pair_color": "gray",
             "result_parts": [("+弹药", "orange"), ("=收武器自动装填", "purple")],
             "value": "约138.2%", "dev": "高3.4%", "bar": 0.70,
             "ticks": ["120.3%", "133.7%", "147.0%"]},
            {"state": "可锁定", "pair": "触发几率", "pair_color": "gray",
             "result_parts": [("+基伤", "orange"), ("=异常", "purple")],
             "value": "约135.4%", "dev": "高1.3%", "bar": 0.53,
             "ticks": ["120.3%", "133.7%", "147.0%"]},
        ],
        "scores": [
            {"label": "伤害分", "value": "10.1 (F)", "label_color": "gray"},
            {"label": "市场分", "value": "0.0 (F)", "label_color": "gray"},
            {"label": "综合评分", "value": "10.1 (F)", "label_color": "red"},
        ],
        "price_left": {
            "title": "市场价格预计", "big": "暂无匹配样本",
            "sub": "零洗中位价：", "sub_val": "369P", "foot": "暂无匹配样本",
        },
        "price_right": {
            "title": "词条价差预估", "big": "386P",
            "sub": "来源：wm市场|置信度80%", "foot": "模型预估 谨慎参考",
        },
        "observed": "最近观测：13小时内",
        "build_title": "基于击杀效率配卡 - 需极化4次",
        "mods": _mod_assets(),
        "kill_line": _kill_parts(12.2, 14.2, 31.8),
        "gain_line": _gain_parts(0.163, 1.611),
        "cmd_groups": [
            {"title": "[指令]查看紫卡其他数据",
             "rows": [["洗卡推荐 阿利乌双枪", "紫卡词条范围 阿利乌双枪"],
                      ["词条价差 阿利乌双枪", "配卡指南 阿利乌双枪"]],
             "note": '*使用"紫卡分析 完整 [截图]"可以获取更详细的分析数据.'},
            {"title": "[指令]查看类似紫卡价格",
             "rows": [["紫卡价格 阿利乌双枪"],
                      ["wmr 触发几率 弹匣 带负 阿利乌双枪"]],
             "note": ""},
        ],
        "footer": {"pc": "PC", "brand": "曼珠",
                   "tip": "*善用右键/长按提取文字"},
        "source": "版式样例·非实时数据（复刻参考卡用于像素对齐）｜" + FULL_SOURCE,
    }
    return d
