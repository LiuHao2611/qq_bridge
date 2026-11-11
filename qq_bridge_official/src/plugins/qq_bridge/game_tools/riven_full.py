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

import logging
from pathlib import Path

logger = logging.getLogger("qq_bridge")

# market 未获取时的可见失败态（绝不回退静态表 / 绝不填「暂无」冒充成功）。
_EMPTY_MARKET = {"status": "error", "error": "market 未获取", "slug": "", "sample_n": 0}


__all__ = [
    "RivenFullError", "FULL_SOURCE", "reference_sample", "riven_full_data",
    "full_text", "MOD_ASSET_DIR",
]


class RivenFullError(Exception):
    """紫卡全面分析数据层错误（武器未收录 / OCR 解析失败等）。"""


FULL_SOURCE = (
    "词条/区间：紫卡截图 OCR + 本仓库倾向表｜击杀效率：本仓库 DPS 模型（无/当前/最强紫卡）"
    "｜市价：Warframe.Market 挂牌要价·非成交价（中位价，单位白金）"
    "｜词条价差为模型预估，谨慎参考｜WFM 不暴露 fuse，不编造融合等级市价"
)

MOD_ASSET_DIR = Path(__file__).resolve().parents[1] / "pic" / "templates" / "riven_full_assets"

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
      * ``image`` 一律 ``None``——资产目录只有参考卡那 8 张**枪卡**像素图，给近战/
        正确类型卡贴枪卡图等于张冠李戴（制造新错配）；模板对 ``image=None``
        画占位卡框 + 真名，诚实且可读；
      * 未知/缺失 type（如仅给武器名、无 OCR）返回 8 格「需紫卡截图」，
        **绝不默认套 Rifle 枪卡**——没真实类型就不假装给出针对该武器的配卡。
    """
    key = _build_key(wtype)
    if not key:
        return [{"name": _NEED_SHOT, "image": None} for _ in range(8)]
    from . import riven_card as rc
    names = list(rc._MELEE_BUILD) if key == "melee" else list(rc._BUILD[key])
    out = [{"name": n, "image": None} for n in names]
    out.append({"name": "当前紫卡", "image": None})
    return out[:8]


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
                    market: dict | None = None) -> dict:
    """组装「紫卡全面分析」卡数据。

    :param parsed: OCR 解析结果（``riven._parse_ocr`` 的返回）；有它才有真实词条行。
    :param card_data: ``riven_card.build_card_data(parsed, market)`` 的结构化结果（可选），
        含每条词条的 low/mean/high/dev/bar，避免重复计算。
    :param weapon: 武器名（中文/英文/外号），用于 WFM 挂牌与本地配卡库。
    :param market: ``riven_card.fetch_market_for_card(parsed)`` 的真实市场快照
        （**必填**：``build_card_data`` 2026-11-04 改为必填位置参数，漏传直接 TypeError）。
        缺省时退回可见失败态，不让卡面静默假装无数据。
    :param mods: 真实配卡 MOD 列表（[{name,image}]）；缺省时按 ``parsed["type"]``
        从 ``riven_card`` 唯一真配卡表派生（2026-11-11 模块 E）；只有显式传值才覆盖。
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

    # ---- 模块 E：按真实武器类型派生配卡（2026-11-11「配卡完全不符合武器」根因）----
    # 判定优先级：显式 parsed['type']（OCR 真值） > 武器名从 riven_disp.json 反查 > 未知。
    # 未知/战甲名/外号 → _build_mods/_build_label 返回「需紫卡截图」占位，绝不套写死枪卡。
    # 调用方显式传 mods 时尊重覆盖（事实源单一：内部只在 None 时派生，绝不手工拼卡名）。
    _wtype = str((parsed or {}).get("type") or "").strip() or _wtype_from_name(w)
    base["mods"] = _build_mods(_wtype) if mods is None else mods
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
            base["hint"] = str(cd.get("hint") or _HONEST_HINT)

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


def _fusion_from_stats(stats: list[dict]) -> list[dict]:
    """由真实词条行推导「融合词条分析」的候选组合（只做组合枚举，不编造融合数值）。

    WFM 无 fuse 字段、也没有「融合后词条」实盘样本，因此融合结果只给**可能组合**
    与**该词条真实区间**，价格/收益一律不编造。
    """
    out = []
    for i, s in enumerate(stats[:_MAX_STATS]):
        out.append({
            "state": "可融合" if i == 0 else "可锁定",
            "pair": s["name"], "pair_color": "red" if s.get("neg") else "gray",
            "result_parts": [(s["name"] + "（组合待洗）", "purple")],
            "value": s["value"], "dev": s["dev"], "bar": s["bar"],
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
