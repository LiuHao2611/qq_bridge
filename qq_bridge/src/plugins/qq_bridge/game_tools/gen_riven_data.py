# -*- coding: utf-8 -*-
"""由 DE 官方 PublicExport 紫卡快照（koishi ``riven-public-export.json``）派生本项目的
紫卡分析三张数据表：

  * ``riven_disp.json``      —— 武器英文名 → {disp, type}（倾向倍率 + 武器类别）
  * ``riven_stat_base.json`` —— 类别 → {英文词条名: 基础值}（仅收录 koishi 校验过的词条）
  * ``riven_stat_zh.json``   —— 英文词条名 → {zh, aliases}（中文词条名 + 别名，供 OCR 匹配）

权威公式（与 koishi-plugin-warframe ``riven-export-adapter.ts`` 完全对齐）：

    baseValue = raw.value * 90 * (unit == 'percent' ? 100 : 1) * (reverseValueSymbol ? -1 : 1)
    unit      = FactionDamage → 'multiply'
                locTag 含 '%'  → 'percent'
                locTag 含 `\\|s` → 'seconds'
                否则           → ''
    verified  = tag != 'WeaponMeleeParryAngleMod' and (tag in splicedNames or tag in traditionalNames)

武器类别（rivenDefinitionTypes）映射到 7 类：
    LotusRifleRandomModRare      → Rifle
    LotusShotgunRandomModRare    → Shotgun
    LotusPistolRandomModRare     → Pistol
    LotusArchgunRandomModRare    → Archgun
    PlayerMeleeWeaponRandomModRare→ Melee
    LotusModularPistolRandomModRare → Kitgun
    LotusModularMeleeRandomModRare  → Zaw

武器→类别（7 类，三档优先级，见 ``_weapon_type``）：
  1. 快照的 ``weaponTypes``（schemaVersion 2，由 ``warframe-public-export-plus`` 的
     ``compatibleItems`` 算出）——**唯一**能正确区分 Rifle/Shotgun、Pistol/Kitgun、
     Melee/Zaw 的权威关系；官方精简导出本身不含该字段。
  2. 无权威映射但有 ``slot`` 字段（= 成品武器）→ 按 ``productCategory`` +
     npm warframe-items 的 ``type`` 兜底（覆盖 plus 包尚未收录的新武器）。
  3. 无 ``slot`` 且无权威映射 → 模块化**部件**（Zaw 握柄/连结、Amp 棱镜/支架/护腕、
     MOA 陀螺、同伴抗原/突变原），一律排除：它们挂在 Pistols 类目下、倾向多为 1.0，
     却不是紫卡适用对象（实测 180 条，会把倾向表从 623 污染到 761）。

倾向口径：官方 Hotfix 44.1.3 起**全部紫卡统一 1.2**，导出里的 ``omegaAttenuation``
已是 1.2；仍原样写入 ``disp``，以便对旧卡/异常卡做历史倾向回退。

数据源：``riven-public-export.json``（DE 官方 Public Export 的裁剪快照）。
由 ``wf_sync.py --now`` 每日从官方清单拉取并原子替换，随后调用本模块重派生三张表，
保证「最新官方数据」。
"""
from __future__ import annotations

import json
import re as _re
from pathlib import Path

# src/plugins/qq_bridge/game_tools/gen_riven_data.py -> parents[4] = 项目根
_ROOT = Path(__file__).resolve().parents[4]
_ITEMS_DIR = _ROOT / "data" / "warframe_items"
_EXPORT = _ITEMS_DIR / "riven-public-export.json"
_OUT_DISP = _ITEMS_DIR / "riven_disp.json"
_OUT_BASE = _ITEMS_DIR / "riven_stat_base.json"
_OUT_ZH = _ITEMS_DIR / "riven_stat_zh.json"

# ----------------------------------------------------------------------------
# koishi 权威映射表（与 riven-export-adapter.ts 逐字一致）
# ----------------------------------------------------------------------------
_RIVEN_DEFINITION_TYPES = {
    "LotusRifleRandomModRare": "Rifle",
    "LotusShotgunRandomModRare": "Shotgun",
    "LotusPistolRandomModRare": "Pistol",
    "LotusArchgunRandomModRare": "Archgun",
    "PlayerMeleeWeaponRandomModRare": "Melee",
    "LotusModularPistolRandomModRare": "Kitgun",
    "LotusModularMeleeRandomModRare": "Zaw",
}

_SPLICED_NAMES = {
    "WeaponWeakpointDamage": "弱点伤害",
    "WeaponWeakpointCriticalChance": "弱点暴击几率",
    "WeaponAmmoEfficiency": "弹药效率",
    "WeaponMagazineReloadHolstered": "收枪装填",
    "WeaponStatusDamage": "异常状态伤害",
    "WeaponMeleeHeavyAttackDamageMod": "重击伤害",
    "WeaponMeleeHeavyAttackChargeMod": "重击蓄力速度",
    "WeaponMeleeParryAngleMod": "格挡角度",
    "WeaponMeleeSlamDamageMod": "震地伤害",
    "WeaponBlastDamageMod": "爆炸",
    "WeaponCorrosiveDamageMod": "腐蚀",
    "WeaponGasDamageMod": "毒气",
    "WeaponMagneticDamageMod": "磁力",
    "WeaponRadiationDamageMod": "辐射",
    "WeaponViralDamageMod": "病毒",
    "WeaponFactionDamageOrokin": "对奥罗金伤害",
    "WeaponFactionDamageTechrot": "对科腐者伤害",
    "WeaponFactionDamageScaldra": "对炽蛇军伤害",
}

# 英文词条名规范化：官方导出 locTag 的元素/穿刺词条是裸名（Heat/Cold/Puncture…），
# 本项目 riven.py / riven_card.py 的权重表（_STAT_WEIGHT / STAT_MARKET / _short_stat）
# 统一用 "X Damage" 形式，故在此把裸名归一到规范名，避免权重表 KeyError/默认值。
_EN_NAME_OVERRIDE = {
    "Heat": "Heat Damage",
    "Cold": "Cold Damage",
    "Electricity": "Electricity Damage",
    "Toxin": "Toxin Damage",
    "Impact": "Impact Damage",
    "Slash": "Slash Damage",
    "Puncture": "Puncture Damage",
    "Corrosive": "Corrosive Damage",
    "Gas": "Gas Damage",
    "Viral": "Viral Damage",
    "Magnetic": "Magnetic Damage",
    "Radiation": "Radiation Damage",
    "Blast": "Blast Damage",
}

_TRADITIONAL_NAMES = {
    "ComboDurationMod": "连击持续时间",
    "SlideAttackCritChanceMod": "滑行攻击暴击几率",
    "WeaponAmmoMaxMod": "弹药上限",
    "WeaponArmorPiercingDamageMod": "穿刺",
    "WeaponClipMaxMod": "弹匣容量",
    "WeaponCritChanceMod": "暴击几率",
    "WeaponCritDamageMod": "暴击伤害",
    "WeaponDamageAmountMod": "基础伤害",
    "WeaponElectricityDamageMod": "电击",
    "WeaponFactionDamageCorpus": "对Corpus伤害",
    "WeaponFactionDamageGrineer": "对Grineer伤害",
    "WeaponFactionDamageInfested": "对Infested伤害",
    "WeaponFireDamageMod": "火焰",
    "WeaponFireIterationsMod": "多重射击",
    "WeaponFireRateMod": "射速/攻击速度",
    "WeaponFreezeDamageMod": "冰冻",
    "WeaponImpactDamageMod": "冲击",
    "WeaponMeleeComboBonusOnHitMod": "额外连击次数几率",
    "WeaponMeleeComboEfficiencyMod": "重击效率",
    "WeaponMeleeComboInitialBonusMod": "初始连击",
    "WeaponMeleeComboPointsOnHitMod": "获得连击次数的几率",
    "WeaponMeleeDamageMod": "近战伤害",
    "WeaponMeleeFactionDamageCorpus": "对Corpus伤害",
    "WeaponMeleeFactionDamageGrineer": "对Grineer伤害",
    "WeaponMeleeFactionDamageInfested": "对Infested伤害",
    "WeaponMeleeFinisherDamageMod": "处决伤害",
    "WeaponMeleeRangeIncMod": "攻击范围",
    "WeaponProcTimeMod": "触发持续时间",
    "WeaponProjectileSpeedMod": "投射物飞行速度",
    "WeaponPunctureDepthMod": "穿透",
    "WeaponRecoilReductionMod": "武器后坐力",
    "WeaponReloadSpeedMod": "装填速度",
    "WeaponSlashDamageMod": "切割",
    "WeaponStunChanceMod": "触发几率",
    "WeaponToxinDamageMod": "毒素",
    "WeaponZoomFovMod": "变焦",
}


#: 官方中文模板与**游戏卡面文案**不一致时的补充别名（纯数据修正，不针对任何武器）。
#: 卡面实拍：伯斯顿紫卡显示「+192.2% 伤害」，而官方 locTag/传统名给的是「基础伤害」；
#: 若不做这一步，OCR 片段「伤害」会被模糊匹配抢到「暴击伤害」上（实测 2026-11-02）。
_ZH_ALIAS_EXTRA = {
    "Damage": ["伤害"],
}


def _zh_display(english: str, name: str) -> str:
    """卡面展示名：官方 locTag 对伤害类词条只给元素名（火焰/电击/毒素），
    游戏内卡面一律是「火焰伤害」这种完整写法，展示层统一补「伤害」二字。"""
    if name and english.endswith("Damage") and not name.endswith("伤害"):
        return name + "伤害"
    return name


def _zh_aliases(english: str, tmpl: str, name: str) -> list[str]:
    """构造中文匹配别名表（供 riven.py 的 OCR 词条识别做精确/模糊匹配）。

    补两类**规则化**别名，解决「官方文案 ≠ 卡面文案」：
      1. 伤害类：locTag 只给元素名（火焰 / 电击 / 毒素），卡面是「火焰伤害」
         → 补 ``name + "伤害"``；
      2. 斜杠并列：官方把「射速/攻击速度」合写，卡面按武器类别只显示其中一侧
         → 拆成两条独立别名。
    纯文案差异（基础伤害 vs 伤害）走 ``_ZH_ALIAS_EXTRA`` 数据表。
    """
    out = [english, tmpl, name]
    if name and english.endswith("Damage") and not name.endswith("伤害"):
        out.append(name + "伤害")
    if "/" in name:
        out.extend(part.strip() for part in name.split("/") if part.strip())
    out.extend(_ZH_ALIAS_EXTRA.get(english, []))
    return list(dict.fromkeys(x for x in out if x))  # 去重保序


def _clean_riven_template(loc: str):
    """对齐 koishi cleanRivenTemplate：去掉 <DT_..._COLOR>、|...|[%s]?、(...) 后 trim。"""
    text = _re.sub(r"<DT_[A-Z_]+_COLOR>", "", loc or "")
    if "<" in text or ">" in text:
        return None
    text = _re.sub(r"\|[^|]+\|[%s]?", "", text)
    text = _re.sub(r"\([^)]*\)", "", text)
    return text.strip()


def _unit_of(tag: str, loc: str) -> str:
    if "FactionDamage" in tag:
        return "multiply"
    if "%" in (loc or ""):
        return "percent"
    if _re.search(r"\|s\b", loc or ""):
        return "seconds"
    return ""


def _suffix(unique_name: str) -> str:
    return unique_name.split("/")[-1]


def _npm_type_lut() -> dict:
    """uniqueName → npm warframe-items 的 type 字段（用于区分 Shotgun/Kitgun/Zaw）。"""
    lut: dict = {}
    for fn in ("Primary.json", "Secondary.json", "Melee.json"):
        p = _ITEMS_DIR / fn
        if not p.exists():
            continue
        try:
            for w in json.loads(p.read_text(encoding="utf-8")):
                if w.get("uniqueName"):
                    lut[w["uniqueName"]] = w.get("type")
        except Exception:  # noqa: BLE001
            continue
    return lut


def _weapon_type(weapon: dict, npm_type: str | None, compat_type: str | None = None) -> str | None:
    """确定武器的紫卡类别（7 类）。

    优先级：
      1. ``compat_type``——快照里由 warframe-public-export-plus 的 ``compatibleItems``
         算出的**权威**映射（唯一能正确区分 Rifle/Shotgun、Kitgun/Zaw 的来源）；
      2. 无 ``compat_type`` 但**有 slot 字段**（= 成品武器）→ 按 productCategory +
         npm ``type`` 兜底（覆盖 plus 包尚未收录的新武器）；
      3. 无 slot 且无 compat_type → **模块化部件**（Zaw 握柄/连结、Amp 棱镜/支架/护腕、
         MOA 陀螺、同伴抗原/突变原…），它们都挂在 ``Pistols`` 类目下且倾向多为 1.0，
         但根本不是紫卡适用对象，必须排除（否则会污染倾向表、干扰 OCR 武器名匹配）。
    """
    if compat_type:
        return compat_type
    if "slot" not in weapon:
        return None
    pc = weapon.get("productCategory")
    if pc == "Melee":
        return "Zaw" if npm_type == "Zaw" else "Melee"
    if pc == "Pistols":
        return "Kitgun" if npm_type == "Kitgun" else "Pistol"
    if pc == "SpaceGuns":
        return "Archgun"
    if pc == "LongGuns":
        return "Shotgun" if npm_type == "Shotgun" else "Rifle"
    return None  # SentinelWeapons / OperatorAmps / SpecialItems / SpaceMelee：非紫卡武器


def generate(export_path: Path | None = None) -> dict:
    """读取官方导出，生成三张表，返回诊断信息。"""
    export_path = export_path or _EXPORT
    exp = json.loads(export_path.read_text(encoding="utf-8"))
    upgrades = exp.get("upgrades") or []
    weapons = exp.get("weapons") or []
    names_zh = exp.get("weaponNamesZh") or {}

    # ---- 1) 词条基础值表 + 中文名表（按 7 类武器分别） ----
    base_by_type: dict[str, dict] = {}
    zh_union: dict[str, dict] = {}
    for up in upgrades:
        rtype = _RIVEN_DEFINITION_TYPES.get(_suffix(up.get("uniqueName", "")))
        if not rtype:
            continue
        base_by_type.setdefault(rtype, {})
        for entry in up.get("upgradeEntries", []):
            tag = entry.get("tag")
            raw = (entry.get("upgradeValues") or [{}])[0]
            loc = raw.get("locTag")
            if not loc or not isinstance(raw.get("value"), (int, float)):
                continue
            tmpl = _clean_riven_template(loc)
            if not tmpl:
                continue
            spliced = tag in _SPLICED_NAMES
            english = ("Damage" if tag == "WeaponMeleeDamageMod"
                       else "Fire Rate / Attack Speed" if tag == "WeaponFireRateMod"
                       else tmpl)
            english = _EN_NAME_OVERRIDE.get(english, english)
            name = (_SPLICED_NAMES.get(tag) or _TRADITIONAL_NAMES.get(tag) or english)
            unit = _unit_of(tag, loc)
            reverse = bool(raw.get("reverseValueSymbol"))
            base_value = raw["value"] * 90.0 * (100.0 if unit == "percent" else 1.0) * (-1.0 if reverse else 1.0)
            verified = (tag != "WeaponMeleeParryAngleMod") and (spliced or tag in _TRADITIONAL_NAMES)
            if not verified:
                continue
            base_by_type[rtype][english] = round(base_value, 6)
            if english not in zh_union:
                zh_union[english] = {"zh": _zh_display(english, name),
                                     "aliases": _zh_aliases(english, tmpl, name)}

    # ---- 2) 武器倾向表 ----
    npm_lut = _npm_type_lut()
    compat_lut = exp.get("weaponTypes") or {}
    disp: dict[str, dict] = {}
    skipped = 0
    for w in weapons:
        om = w.get("omegaAttenuation")
        if not isinstance(om, (int, float)) or om <= 0:
            continue
        rtype = _weapon_type(w, npm_lut.get(w.get("uniqueName")),
                             compat_lut.get(w.get("uniqueName")))
        if not rtype:
            skipped += 1
            continue
        name = w.get("name")
        if not name:
            continue
        zh = names_zh.get(w.get("uniqueName"), "")
        if rtype == "Kitgun":
            # koishi：Kitgun 同时生成 (Secondary)/(Primary) 两个倾向入口
            disp[f"{name} (Secondary)"] = {"disp": round(float(om), 4), "type": "Kitgun", "zh": (zh or "") + "（副武器）" if zh else ""}
            pomega = w.get("primeOmegaAttenuation")
            if isinstance(pomega, (int, float)) and pomega > 0:
                disp[f"{name} (Primary)"] = {"disp": round(float(pomega), 4), "type": "Kitgun", "zh": (zh or "") + "（主武器）" if zh else ""}
        else:
            disp[name] = {"disp": round(float(om), 4), "type": rtype, "zh": zh}

    # ---- 3) 落盘（原子替换） ----
    for path, data in ((_OUT_DISP, disp), (_OUT_BASE, base_by_type), (_OUT_ZH, zh_union)):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    diag = {
        "weapons_total": len(disp),
        "weapons_skipped_non_rivenable": skipped,
        "types": {k: len(v) for k, v in base_by_type.items()},
        "stat_keys_total": len(zh_union),
    }
    return diag


if __name__ == "__main__":
    info = generate()
    print("enerate riven data done:", json.dumps(info, ensure_ascii=False))
