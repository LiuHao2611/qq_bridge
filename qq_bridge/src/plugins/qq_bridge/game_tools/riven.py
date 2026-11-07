# -*- coding: utf-8 -*-
"""game_tools/riven.py —— 紫卡截图分析：OCR 解析 + Warframe Rabbit 风格评估。

数据全部来自 **DE 官方 Public Export**（``content.warframe.com/PublicExport/``），
经 ``wf_sync._sync_riven_export`` 裁剪成 ``riven-public-export.json``，再由
``gen_riven_data.generate()`` 派生三张表：
  * riven_disp.json      —— 武器名 → {disp, type, zh}（623 把可洗紫卡武器）
  * riven_stat_base.json —— 7 类武器 × 49 词条基础值（``raw.value × 90 × 100``）
  * riven_stat_zh.json   —— 词条中文名 + 别名（官方模板与卡面文案差异已归一）

公式（koishi ``services/riven/analysis.ts`` 同源）：
    standard = base × disposition × factor × (rank+1)/9
    range    = standard × [0.9, 1.1]
    factor   = 2正 0.99 / 3正 0.75 / 2正1负 1.2375·−0.495 / 3正1负 0.9375·−0.75
    倾向     = 官方 Hotfix 44.1.3 后统一 1.2（导出 omegaAttenuation 仅历史兼容）

OCR 用 Windows 自带 OCR（winrt），纯本地零外呼；zh-CN + en-US 双引擎合并
（同一行会被两个引擎各读一次 → 按包围盒聚成「逻辑行」再择优取值，见 _group_rows），
兼容中文客户端（词条名去空格/错字模糊匹配、全角符号归一、x 乘数与括号 x2 防误吃）。

评估输出对齐社区常用「Warframe Rabbit」样式：
  词条行（数值 + 理论范围 + roll 偏离）、综合评分（字母档）、
  分类评分（伤害/市场）、推荐洗卡（T0-T3）、推荐负面/致命负词。
伤害模拟与最佳配装为 Rabbit 付费闭源能力，本工具不做假数据，如实标注未接入。
"""
from __future__ import annotations

import asyncio
import io
import json
import re as _re

from pathlib import Path
from PIL import Image
from nonebot.log import logger

from . import ocr as ocr_mod
from . import warframe

_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "warframe_items"

#: 词条组合修正系数（koishi rivenStatFixFactor）：buff 乘正词条范围，curse 乘负词条
_FIX = {
    "2": {"buff": 0.99, "curse": 0.0},
    "21": {"buff": 1.2375, "curse": -0.495},
    "3": {"buff": 0.75, "curse": 0.0},
    "31": {"buff": 0.9375, "curse": -0.75},
}

#: 词条价值权重（社区经验，按武器类型微调见 _weight）
_STAT_WEIGHT = {
    "Critical Chance": 40, "Critical Damage": 40, "Damage": 40, "Multishot": 40,
    "Status Chance": 24,
    "Cold Damage": 24, "Electricity Damage": 24, "Heat Damage": 24, "Toxin Damage": 24,
    "Fire Rate / Attack Speed": 24, "Fire Rate": 24, "Attack Speed": 24, "Reload Speed": 20,
    "Magazine Capacity": 14, "Punch Through": 14, "Projectile Speed": 12,
    "Status Duration": 12, "Combo Duration": 12, "Range": 16, "Initial Combo": 16,
    "Heavy Attack Efficiency": 12, "Heavy Attack Damage": 12,
    "Slide Attack Critical Chance": 10, "Slide Attack Critical Damage": 10,
    "Impact Damage": 8, "Puncture Damage": 8, "Slash Damage": 8,
    "Zoom": 6, "Ammo Maximum": 6, "Weapon Recoil": 10, "Finisher Damage": 6,
    "Channeling Efficiency": 6, "Channeling Damage": 6, "Block Resistance": 8,
    "Damage to Grineer": 8, "Damage to Corpus": 8, "Damage to Infested": 8,
    "Damage vs Grineer": 8, "Damage vs Corpus": 8, "Damage vs Infested": 8,
    "Combo Count Chance": 10, "Slide Attack": 8,
}

#: 负词条伤度（数字越大越伤；洗卡推荐 = 别洗出高伤度词条）
_NEG_DAMAGE = {
    "Zoom": 5, "Ammo Maximum": 5,
    "Damage to Grineer": 5, "Damage to Corpus": 5, "Damage to Infested": 5,
    "Damage vs Grineer": 5, "Damage vs Corpus": 5, "Damage vs Infested": 5,
    "Projectile Speed": 12, "Weapon Recoil": 12, "Magazine Capacity": 12,
    "Status Duration": 12,
    "Fire Rate": 18, "Attack Speed": 18, "Fire Rate / Attack Speed": 18,
    "Reload Speed": 18,
    "Status Chance": 22, "Impact Damage": 22, "Puncture Damage": 22, "Slash Damage": 22,
    "Critical Chance": 30, "Critical Damage": 30, "Damage": 30, "Multishot": 30,
}

#: 派系伤害词条（市场维度提档：派系卡贵）
_FACTION = {"Damage to Grineer", "Damage vs Grineer",
            "Damage to Corpus", "Damage vs Corpus",
            "Damage to Infested", "Damage vs Infested"}

_RIVEN: dict = {"disp": None, "base": None, "zh": None, "en_keys": None, "zh_flat": []}


def _load_riven_data() -> None:
    """懒加载紫卡数据（幂等）。"""
    if _RIVEN["disp"] is not None:
        return
    _RIVEN["disp"] = _RIVEN["base"] = _RIVEN["zh"] = {}
    try:
        _RIVEN["disp"] = json.loads((_DATA_DIR / "riven_disp.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_riven_disp_load_failed: {type(e).__name__}: {e}")
    try:
        _RIVEN["base"] = json.loads((_DATA_DIR / "riven_stat_base.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_riven_base_load_failed: {type(e).__name__}: {e}")
    try:
        _RIVEN["zh"] = json.loads((_DATA_DIR / "riven_stat_zh.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_riven_zh_load_failed: {type(e).__name__}: {e}")
    keys = set()
    for attrs in (_RIVEN["base"] or {}).values():
        keys.update(attrs.keys())
    _RIVEN["en_keys"] = sorted(keys, key=len, reverse=True)  # 最长优先匹配
    # 中文别名扁平表（去空格，按长度降序：最具体的先匹配）
    zh_flat = []
    for k, info in (_RIVEN["zh"] or {}).items():
        names = [info.get("zh", "")] + (info.get("aliases") or [])
        for n in names:
            if n:
                zh_flat.append((_re.sub(r"\s+", "", n).lower(), k))
    zh_flat.sort(key=lambda x: len(x[0]), reverse=True)
    _RIVEN["zh_flat"] = zh_flat


def _stat_zh(en: str) -> str:
    info = (_RIVEN["zh"] or {}).get(en) or {}
    return info.get("zh") or en


#: OCR 错字 → 正字（紫卡字体细笔画中文在小图里常误读；按需扩充）。
#: 现在允许多对一映射（一个错字可能对应多个正字，由模糊匹配上下文决定）。
_CN_CONFUSE = {
    "苦": {"伤"}, "伤": {"苦"},     # 暴击苦 → 暴击伤害
    "病": {"毒"}, "毒": {"病"},     # 病《伤 → 毒素伤害 / 病毒伤害
    "《": {"素", "毒"}, "素": {"《"}, # 素→《；病毒/毒素的「毒」也常被读成《
}

#: 派系词条：卡面写「对 Infested 伤害」，而 OCR 极常把末尾「伤害」读成 C / f' / 乱码，
#: 只剩「对 + 派系名」。这时靠中文字符串匹配一定失败（中文片段只剩一个「对」），
#: 必须用派系英文名反查。列表覆盖游戏内全部带紫卡派系词条的目标。
_FACTION_STATS = (
    ("infested", "Damage to Infested"),
    ("grineer", "Damage to Grineer"),
    ("corpus", "Damage to Corpus"),
    ("orokin", "Damage to Orokin"),
    ("scaldra", "Damage to Scaldra"),
    ("techrot", "Damage to Techrot"),
    ("corrupted", "Damage to Corrupted"),
    ("murmur", "Damage to Murmur"),
    ("narmer", "Damage to Narmer"),
)


def _faction_stat(line: str) -> str | None:
    """「对 <派系> 伤害」→ 对应词条键。容忍 OCR 的 l↔I / 1↔i / 0↔o 混淆。"""
    if "对" not in line and "to" not in line.lower():
        return None
    t = line.lower()
    for token, key in _FACTION_STATS:
        for variant in (token, token.replace("i", "l"), token.replace("i", "1"),
                        token.replace("o", "0")):
            if variant in t:
                return key
    return None


#: 实测卡面写「暴击率 / 弹药最大值」，官方中文映射写「暴击几率 / 弹药上限」——
#: 子序列模糊匹配救不了（弹药最大值 vs 弹药上限 只共享前两字），必须同义归一。
_CN_SYNONYM = (("最大值", "上限"), ("几率", "率"))


def _cn_syn(text: str) -> str:
    for a, b in _CN_SYNONYM:
        text = text.replace(a, b)
    return text


#: 允许进入中文片段的字符（CJK 主体 + 被误读成标点的中文部件如 《）。
_CORRUPT_ALLOW = set("《〈〉【】")


def _cn_fragment(line: str) -> str:
    """从一行里抽取中文（含被误读成标点的中文部件），丢弃数字/拉丁/百分号/空格。"""
    out = []
    for c in line:
        o = ord(c)
        if 0x4E00 <= o <= 0x9FFF or c in _CORRUPT_ALLOW:
            out.append(c)
    return "".join(out)


def _cn_close(a: str, b: str) -> bool:
    """两字符是否可视为同一（精确相等或互为已知 OCR 错字；允许多对一）。"""
    if a == b:
        return True
    return b in _CN_CONFUSE.get(a, set()) or a in _CN_CONFUSE.get(b, set())


def _fuzzy_stat(frag: str, cname: str):
    """frag（OCR 中文片段，可能丢字/错字）作为 cname（标准词条名）的模糊匹配。

    返回 (coverage, anchored, exact)：
      * coverage = 子序列覆盖率 [0,1]：frag 字符按序匹配 cname（cname 中可跳过
        被 OCR 漏读的字；frag 中也可跳过被误插的乱字），逐字允许错字（_cn_close）。
        OCR 丢字（多击→多重射击）与错字（暴击苦→暴击伤害）都能命中。
      * anchored = frag 首尾字符（错字容错）是否恰好对齐 cname 首尾。
        用于兜底「首尾清晰、中间糊成一团」的片段（如 病过伤 → 毒素伤害），
        避免中间乱字把覆盖率压到阈值以下却仍能唯一锁定词条。
      * exact = frag 与 cname 中精确相等（不用错字映射）的字符数，用于多义
        错字（如 病《伤 同时像 毒素/病毒）时优先选精确匹配更多的那个。
    """
    m, n = len(frag), len(cname)
    if m == 0 or n == 0:
        return 0.0, False, 0
    prev = [0] * (n + 1)
    prev_ex = [0] * (n + 1)
    for i in range(1, m + 1):
        cur = [0] * (n + 1)
        cur_ex = [0] * (n + 1)
        fi = frag[i - 1]
        for j in range(1, n + 1):
            # 跳过 cname 字符（OCR 漏读）
            best, best_ex = prev[j], prev_ex[j]
            # 跳过 frag 字符（容错）
            if cur[j - 1] > best or (cur[j - 1] == best and cur_ex[j - 1] > best_ex):
                best, best_ex = cur[j - 1], cur_ex[j - 1]
            if _cn_close(fi, cname[j - 1]):
                sc, sc_ex = prev[j - 1] + 1, prev_ex[j - 1]
                if fi == cname[j - 1]:
                    sc_ex += 1
                if sc > best or (sc == best and sc_ex > best_ex):
                    best, best_ex = sc, sc_ex
            cur[j], cur_ex[j] = best, best_ex
        prev, prev_ex = cur, cur_ex
    coverage = prev[n] / m
    # 反向覆盖率：cname 有多少字符被 frag 覆盖。只算单向（frag→cname）时，
    # 任意短片段都能「满分」匹配任意长词条——实测「伤害」曾以 score=1.00 被抢成
    # 「暴击伤害」（2026-11-02，伯斯顿实拍），必须用双向覆盖率区分。
    cov_out = prev[n] / n
    anchored = (_cn_close(frag[0], cname[0])
                and _cn_close(frag[-1], cname[-1]))
    exact = prev_ex[n]
    return coverage, cov_out, anchored, exact


def _lev(a: str, b: str) -> int:
    """编辑距离（Levenshtein）。空间优化 DP，O(n·m)。

    用途：OCR 单字替换/漏读/插字容错——这是游戏字体最常见的识别错误
    （实测「三宝聚」被读成「二宝聚」，三/二字形极像，单字替换让 LCS 覆盖率
    从 1.0 掉到 2/3=0.667，刚好卡在 0.7 阈值下导致整卡认不出武器）。
    LCS 覆盖率对「替换」零容忍，编辑距离则天然把 1 次替换算作 1 步，
    故在 _fuzzy_weapon 里作为对短武器名的兜底。
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def _find_stat(line: str) -> str | None:
    """行内找词条英文键（中英文均支持；中文支持 OCR 错字/漏字与卡面同义描述）。

    匹配优先级：英文键 → 派系反查 → 中文精确 → 中文模糊，但**更具体的词条名优先**：
    卡面常写比词条名更长的描述（「在重击时的近战伤害」包含「重击伤害」全部字符），
    精确匹配只能命中最短的那个（「伤害」），会把整条词条判成基础伤害（实测漏判）。
    """
    compact = _re.sub(r"\s+", "", line.lower())
    # 英文键匹配（先去空格）
    for k in _RIVEN["en_keys"]:
        if k.lower().replace(" ", "") in compact:
            return k
    # 派系词条优先：「对 Infested 伤害」的「伤害」常被读成乱码，只剩「对+派系名」，
    # 若不先判派系，会被中文精确匹配里的「伤害」抢走（实测 riven_005 判成 Damage）。
    fac = _faction_stat(compact)
    if fac:
        return fac
    syn = _cn_syn(compact)
    exact_hit = None  # (词条名长度, key)
    for cname, k in _RIVEN["zh_flat"]:  # 已按长度降序，首个命中即最长
        if _cn_syn(cname) in syn:
            exact_hit = (len(cname), k)
            break
    frag = _cn_fragment(syn)
    fuzzy_hit = None  # (score, exact, 词条名长度, key, cov_out)
    if len(frag) >= 2:
        best = None
        for cname, k in _RIVEN["zh_flat"]:
            cov, cov_out, anchored, exact = _fuzzy_stat(frag, _cn_syn(cname))
            # 三种通过条件：
            #   cov≥0.8        片段基本被词条名覆盖（OCR 漏字）
            #   cov≥0.6 + 锚定  中间糊、首尾清晰
            #   cov_out≥0.9     卡面用了**更长的同义描述**
            if not (cov >= 0.8 or (cov >= 0.6 and anchored) or cov_out >= 0.9):
                continue
            score = (cov + cov_out) / 2.0 + (0.05 if anchored else 0.0)
            key = (score, exact, len(cname))
            if best is None or key > best[:3]:
                best = (score, exact, len(cname), k, cov_out)
        fuzzy_hit = best
    if exact_hit and fuzzy_hit and fuzzy_hit[2] > exact_hit[0] and fuzzy_hit[4] >= 0.9:
        logger.info(f"wf_riven_stat_specific: frag={frag!r} -> {fuzzy_hit[3]} "
                    f"(比精确命中的 {exact_hit[1]} 更具体)")
        return fuzzy_hit[3]
    if exact_hit:
        return exact_hit[1]
    if fuzzy_hit:
        logger.info(f"wf_riven_fuzzy_stat: frag={frag!r} -> {fuzzy_hit[3]} "
                    f"score={fuzzy_hit[0]:.2f} cov_out={fuzzy_hit[4]:.2f}")
        return fuzzy_hit[3]
    return None


def _extract_value(line: str) -> dict | None:
    """提取数值：%百分比 / x乘数 / 纯数字 + 正负号（兼容全角、OCR l↔1 混淆）。

    顺序：先 % 再 x 再纯数字——「+222.6% 暴击几率（重击时x2）」必须取 %，
    x 乘数要求带小数点（x1.59 / xl，59），避免误吃括号里的 x2。
    OCR 变体：全角＋－％．、小数点被读成逗号（104，5）、负号读成一横线
    变体（¯–——）、「/」当 7（第二引擎）。
    """
    t = _re.sub(r"\s+", "", line)
    t = (t.replace("．", ".").replace("％", "%").replace("＋", "+").replace("－", "-")
          .replace("¯", "-").replace("–", "-").replace("—", "-").replace("‐", "-")
          .replace("彐", "-").replace("コ", "-"))
    # 字母↔数字混淆：夹在数字之间的 l/L/I/| 几乎必然是 1，O/o 必然是 0。
    # 实测「101.4」被读成「10L4」（2026-11-02 冰凇卡），补这一步后可还原为 1014
    # → 再由下方「>400 除以 10」的漏小数点规则还原成 101.4。
    t = _re.sub(r"(?<=\d)[lLI|](?=[\d.,，%])", "1", t)
    t = _re.sub(r"(?<=[1-9])[Oo](?=\d)", "0", t)
    # 正负号被 OCR 插到数字中间：紫卡数值内部不会有 +/-，直接把数字间的符号删掉。
    # 实测「+65.3%」被读成「6 + 5．3」——字符全在，只是加号错位（2026-11-02 玻之武杖卡）。
    t = _re.sub(r"(?<=\d)\s*[+\-－¯–—]\s*(?=\d)", "", t)
    # 小数点被 OCR 成逗号：数字间逗号 → 点（104，5 → 104.5；千位分隔极罕见，忽略）
    t = _re.sub(r"(?<=\d)[，,](?=\d)", ".", t)
    m = _re.search(r"([+\-]?)(\d+(?:\.\d+)?)\s*%", t)
    if m:
        val = float(m.group(2))
        # OCR 漏小数点：110.4% 被读成 1104%（>400 对紫卡 % 词条不可能），
        # 除以 10 还原（最多两轮，覆盖万位级拼接）。1104→110.4，17.4 不会误伤。
        if abs(val) > 400:
            val /= 10.0
        if abs(val) > 400:
            val /= 10.0
        return {"value": val, "unit": "%", "sign": m.group(1) or "+"}
    # x 倍率：x1.59 / x0.63 / xl，59 都要认。整数位必须取**读到的那个数字**，
    # 旧实现固定成 "1"/"0" 二选一，导致「x0.63」（派系负词条）解析不出单位。
    m = _re.search(r"[xX]\s*([0-9lI])?\s*[.,，]\s*(\d+)", t)
    if m:
        lead = (m.group(1) or "").lower()
        whole = "1" if lead in ("", "l", "i") else lead
        return {"value": float(whole + "." + m.group(2)), "unit": "x", "sign": "+"}
    m = _re.search(r"([+\-]?)(\d+(?:\.\d+)?)", t)
    if m:
        return {"value": float(m.group(2)), "unit": "", "sign": m.group(1) or "+"}
    return None


def _subseq_cov(a: str, b: str) -> float:
    """a 的字符在 b 中按序出现的覆盖率（0~1）。用于武器名 OCR 残读兜底。

    例：a='努寇微波枪'(5) 在 b='努寇微波№ni一' 中命中 努/寇/微/波(枪缺失)=4/5=0.8。
    """
    if not a:
        return 0.0
    i = 0
    for ch in b:
        if i < len(a) and ch == a[i]:
            i += 1
    return i / len(a)


def _fuzzy_weapon(lines, disp_map: dict, zh_map: dict) -> str | None:
    """武器名 OCR 残读兜底：中文武器名在紧凑 OCR 行里做子序列覆盖匹配。

    紫卡标题行武器名常被读残（'枪'→'№ni一'），精确子串匹配失败，
    故用覆盖率≥0.7 且字符数≥3 的子序列匹配找回正确武器键。

    中文名优先取**倾向表自带的官方中文名**（riven_disp.json 的 ``zh``），
    其次才回落到 ``warframe._zh_map``——后者是旧版 zh_names.json，缺新武器。

    编辑距离兜底（tier 1）：LCS 覆盖率对「单字替换」零容忍——实测「三宝聚」
    被读成「二宝聚」（三/二字形极像），覆盖率掉到 2/3=0.667 < 0.7 阈值，
    整卡认不出武器。对短武器名（≤4 字）额外做编辑距离判定，只差 ≤1 个字符
    （替换/漏读/插字）即视为命中。该兜底仅对短名生效，避免长名被短片段偶然
    低距命中造成误判。tier 2（LCS 命中）始终优先于 tier 1（编辑距命中）。
    """
    best = None  # (score_tier, cov_out, name_len, weapon_key)
    for ln in lines:
        compact = _re.sub(r"\s+", "", ln)
        cfrag = _cn_fragment(compact)  # 仅取中文片段，避免拉丁紫卡名污染编辑距离
        if len(cfrag) < 2:
            continue
        for wk in disp_map:
            zhn = ((disp_map.get(wk) or {}).get("zh") or "").strip()
            if not zhn:
                info = (zh_map or {}).get(wk) or {}
                zhn = (info.get("zh") or "") if isinstance(info, dict) else ""
            if len(zhn) < 2:
                continue
            # ⚠️ 必须用 **LCS 覆盖率（cname 被覆盖的比例）**，不能用贪心子序列：
            # 贪心遇到中间一个字读错就整段中断——实测「奥堤克光子枪」被读成
            # 「奥堤克光上枪」时贪心只走到第 4 字（0.67 < 阈值）→ 整卡认不出武器。
            # LCS 允许跳过错字：5/6 = 0.83 ✓。冰凇 / 宝拉 这类 2 字名要求整名命中。
            _cov, cov_out, _anch, _ex = _fuzzy_stat(compact, zhn)
            need = 1.0 if len(zhn) < 3 else 0.7
            if cov_out >= need:
                score = (2, cov_out, len(zhn))
            elif (len(zhn) <= 4 and abs(len(cfrag) - len(zhn)) <= 1
                  and _lev(cfrag, zhn) <= 1):
                # 单字 OCR 错误兜底：差 1 步编辑距离即视为命中；评分略低于 LCS 命中，
                # 但高于任何弱部分匹配，确保正确武器胜出（同距时按覆盖率、字数定胜负）。
                _d = _lev(cfrag, zhn)
                score = (1, 1.0 - _d / max(len(cfrag), len(zhn)), len(zhn))
            else:
                continue
            if best is None or score > best[:3]:
                best = (score[0], score[1], score[2], wk)
    return best[3] if best else None


def _sample_red(img: Image.Image, box: tuple, focus_left: float = 0.45) -> float:
    """采样某行包围盒内像素，返回"红主导"像素占比（0~1）。

    Warframe 卡面中诅咒（负）词条的"数值/符号"永远渲染为红色；正词条为白/青色；
    词条名本身常为白色。因此只采样包围盒左侧（数值所在区域，默认 45%），
    避免白色词条名把红像素比例稀释。
    """
    x, y, w, h = box
    W, H = img.size
    w = w * focus_left
    x0 = max(0, int(x)); y0 = max(0, int(y))
    x1 = min(W, int(x + w)); y1 = min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    crop = img.crop((x0, y0, x1, y1)).convert("RGB")
    n = red = 0
    for (r, g, b) in crop.getdata():
        n += 1
        # 红主导：R 明显大于 G/B，且整体亮度不低（暗区可能是阴影）
        if r > 70 and r > 1.3 * g and r > 1.3 * b and (r + g + b) > 140:
            red += 1
    return (red / n) if n else 0.0


_COMPANION_RE = _re.compile(
    r"^([+\-¯彐コ]?)\s*(\d+)(?:\s*[ ,]\s*(\d{1,2}))?\s*/\s*\d+\s*$"
)


def _extract_companions(boxes: list) -> list:
    """从包围盒里捞"压缩数字伴侣行"（如 `-17,40/0` / `+110 40/0` / `+38, 30/0`）。

    Warframe 卡面会把词条数值另行渲染成紧凑数字 + 末尾 `/<MR>` 残留，
    例如 `-17.4` 渲染成 `-17,40/0`（值×100 + 逗号/空格分隔 + `/0` 噪声）。
    当主词条行被 OCR 漏读首位数字（17.4→7.4）时，用伴侣行补全真实值。
    返回 [(box_y, sign, value), ...]，value = 整数 + 小数/100（同号）。
    """
    out = []
    for (_t, _x, _y, _w, _h) in boxes or []:
        m = _COMPANION_RE.match((_t or "").strip())
        if not m:
            continue
        _sgn = "-" if m.group(1) in ("-", "¯", "彐", "コ") else "+"
        _val = int(m.group(2)) + (int(m.group(3)) / 100.0 if m.group(3) else 0.0)
        out.append((_y, _sgn, _val))
    return out


def _group_rows(boxes: list) -> list[dict]:
    """把多引擎的行包围盒按竖直重叠聚成「逻辑行」。

    为什么必须聚类：``ocr_image`` 会把中/英两个引擎的**全部**行拼在一起返回，
    同一行常有两种读法——
        中文引擎：``+ 19 ． 2 ％ 伤 害``   ← 词条名正确，数值漏读一位
        英文引擎：``+192.2%``            ← 数值正确，没有词条名
    逐行独立解析时，正确的数值因为「没有词条名」被丢弃，而错值被采纳
    （实测 2026-11-02 伯斯顿卡：192.2% 被算成 19.2%）。按 y 聚类后，
    同一逻辑行能同时看到两种读法，词条名任取其一、数值按规则择优。
    """
    items: list[dict] = []
    for b in boxes or []:
        if len(b) < 5:
            continue
        t = (b[0] or "").strip()
        if not t:
            continue
        items.append({"t": t, "x": b[1], "y": b[2], "h": b[4] or 12})
    items.sort(key=lambda d: (d["y"], d["x"]))
    rows: list[dict] = []
    for it in items:
        for row in rows:
            # 与行内首项的竖直中心距 < 半行高 → 同一逻辑行（同行两引擎 y 常差 1~2px）
            if abs(it["y"] - row["y"]) <= max(3.0, min(it["h"], row["h"]) * 0.65):
                row["items"].append(it)
                row["y"] = min(row["y"], it["y"])
                break
        else:
            rows.append({"y": it["y"], "h": it["h"], "items": [it]})
    out: list[dict] = []
    for row in rows:
        its = row["items"]
        x0 = min(i["x"] for i in its)
        x1 = max(i["x"] + max(1, len(i["t"]) * 6) for i in its)
        out.append({
            "y": row["y"],
            "text": " ".join(i["t"] for i in its),
            "texts": [i["t"] for i in its],
            "box": (x0, row["y"], max(1, x1 - x0), max(i["h"] for i in its)),
        })
    # 词条名换行：长词条名会被卡面折成两行（实测「在重击时的近战伤害」 →
    # 第一行「+147.9% 在重击时的近战」、第二行只剩「伤害」）。若不合并，
    # 第一行找不到词条（名字不完整）、第二行又被误配成「伤害」，
    # 整条 147.9% 词条直接丢失。判据：无数字 + 纯汉字 ≤4 字 + 紧贴上一行。
    merged: list[dict] = []
    for row in out:
        prev = merged[-1] if merged else None
        if (prev is not None
                and not _re.search(r"\d", row["text"])
                and 1 <= len(_cn_fragment(row["text"])) <= 4
                and 0 < row["y"] - prev["y"] <= max(26.0, prev["box"][3] * 2.4)):
            prev["text"] = prev["text"] + " " + row["text"]
            prev["texts"] = prev["texts"] + row["texts"]
            continue
        merged.append(row)
    return merged


def _row_values(texts: list[str]) -> list[dict]:
    """同一逻辑行内**所有**引擎/放大档的数值读数（去重，保留原样）。

    多档预处理会给出互斥读数（实测「+65.3%」被读成 6.3 与 65.3 两种），
    不能在这里就用「位数多者优先」定死——那会把把图标数字粘进来的 6106.5
    选成最优。正确做法是把候选全带上，最后用**理论区间**裁决（见 _refine_values）。
    """
    out: list[dict] = []
    seen: set[tuple[float, str, str]] = set()
    for t in texts:
        v = _extract_value(t)
        if not v:
            continue
        key = (v["value"], v["unit"], v["sign"])
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _pick_row_value(texts: list[str]) -> dict | None:
    """单值兜底选择（没有理论区间可裁决时用）：位数多者优先、同位数优先带负号。

    仅作为 `_extract_value` 的初值；有 base 表时会被 ``_refine_values`` 覆盖。
    """
    cands = _row_values(texts)
    if not cands:
        return None

    def rank(v: dict) -> tuple[int, int]:
        digits = sum(ch.isdigit() for ch in f"{v['value']:g}")
        return digits, 1 if v["sign"] == "-" else 0

    return max(cands, key=rank)


def _parse_ocr(text: str, boxes: list | None = None, img_data: bytes | None = None) -> dict | None:
    """OCR 文本 → {weapon, weapon_zh, riven_name, rank, disp, type,
    stats:[{en,zh,value,unit,sign}]}。

    boxes/img_data 可选：传入后做"布局感知"——对每条词行的包围盒做红色像素采样，
    直接判定诅咒（负）词条（游戏内诅咒永远红字且排最后），覆盖脆弱的负号 OCR。
    """
    _load_riven_data()
    warframe._load_translate_data()
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    # 有包围盒时按「逻辑行」解析（多引擎同一行的两种读法归并，见 _group_rows）；
    # 没有包围盒就退回纯文本行（降级路径，行为与旧版一致）。
    units = _group_rows(boxes) if boxes else [
        {"text": ln, "texts": [ln], "box": None, "y": None} for ln in lines]
    stats = []
    #: 标题行里出现过的纯拉丁词（紫卡名候选）；「单词式紫卡名」（无连字符，如 Hexado）
    #: 与武器名同行，无法在行内区分，故延迟到武器解析完成后再定（见循环后）。
    _name_tokens: list[str] = []
    weapon_key = None
    riven_name = None
    pending_rn = None
    rank = None
    mr = None
    disp_map = _RIVEN["disp"] or {}
    known_weapons_low = set()

    def _is_riven_name(ln: str) -> bool:
        """紫卡名（如 Sati-vexitis / Acri-SatiTio）：纯拉丁含连字符，
        且不是已知武器英文名（MK1-Braton 这类带连字符的武器名要排除）。
        末尾带连字符的是被断行的前缀（如 Cari-），不算完整名。"""
        s = ln.strip()
        if not _re.fullmatch(r"[A-Za-z][A-Za-z\-']{3,24}", s):
            return False
        if s.endswith("-"):
            return False
        if "-" not in s:
            return False
        return s.lower() not in known_weapons_low

    # 裸数字行只有出现在卡面上部才可能是等级徽章（右上角固定位置）；
    # 底部裸数字多为「段位 N」（精通门槛）——用包围盒 y 位置过滤（通用布局规则）
    _bare_digit_ok: dict[str, bool] = {}
    if boxes:
        try:
            _ymax = max((b[2] + b[3]) for b in boxes if len(b) >= 4) or 0
            for b in boxes:
                _t = (b[0] or "").strip()
                if len(b) >= 4 and _re.fullmatch(r"\d{1,2}", _t):
                    _bare_digit_ok[_t] = (b[2] < _ymax * 0.6)
        except Exception:  # noqa: BLE001 — 盒子异常时不做位置过滤
            _bare_digit_ok = {}

    for _u in units:
        ln = _u["text"]
        sk = _find_stat(ln)
        if sk:
            v = _pick_row_value(_u["texts"])
            if v:
                stats.append({"en": sk, "zh": _stat_zh(sk),
                              "value": v["value"], "unit": v["unit"], "sign": v["sign"],
                              "_line": ln, "_box": _u.get("box"),
                              "_cands": _row_values(_u["texts"])})
                continue
        # 紫卡等级：裸数字行（0~8，允许带 ≤2 个杂符如「18 一」「18 v」「18-」）。
        # ⚠️ 紫卡 Fusion 上限是 Rank 8；徽章右侧那个数字常被 OCR 读成 18/10 之类
        # （实拍 2026-11-02 伯斯顿卡：徽章读出「18 一」，实际按满级 8 计算）。
        # 段位=精通门槛、循环数(3位数)都不是等级；位于卡面底部的裸数字=段位行，不算等级。
        if rank is None:
            _nm = _re.match(r"^\s*(\d{1,2})\s*[^\d\s]{0,2}\s*$", ln)
            if _nm:
                n = int(_nm.group(1))
                _core = _re.sub(r"[^\d]", "", ln).strip()
                _bottom_only = (_bare_digit_ok
                                and _core in _bare_digit_ok
                                and not _bare_digit_ok[_core])
                if not _bottom_only and 0 <= n <= 8:
                    rank = n
                    continue
        # 等级显式标注（等级:8 / Rank 8）——「段位」是精通门槛，不算紫卡等级
        rm = _re.search(r"(?:等\s*级|rank)\s*[:：]?\s*(\d+)", ln, _re.I)
        if rm and rank is None:
            rank = int(rm.group(1))
            continue
        # 紫卡自身段位要求（卡面底部「段位 16」）—— 不是等级，单独记录给分析卡「MR N」用
        if mr is None:
            _mm = _re.search(r"段\s*位\s*[:：]?\s*(\d{1,2})", ln)
            if _mm:
                mr = int(_mm.group(1))
                continue
        # 紫卡名（拉丁含连字符、非武器名）；游戏内标题行混排且可能断行：
        # 「视使之触 Sati-」+「vexitis」→ 提取行尾拉丁尾巴，下一行纯拉丁则拼接
        if riven_name is None and not sk:
            _name_tokens.extend(
                t for t in _re.split(r"\s+", ln) if _re.fullmatch(r"[A-Za-z]{4,20}", t))
        if riven_name is None:
            # 续行：本行含纯拉丁词。多引擎会把同一行拆成多个片段
            # （实拍 "i niata igniata"：一个引擎读残 "i niata"、另一个读出 "igniata"），
            # 取**最长**片段拼接，残读片段自然被淘汰。
            _toks = [t for t in _re.split(r"\s+", ln)
                     if _re.fullmatch(r"[A-Za-z]{3,}", t)]
            if pending_rn and _toks:
                riven_name = pending_rn + max(_toks, key=len)
                pending_rn = None
                continue
            if _is_riven_name(ln):
                riven_name = ln.strip()
                continue
            if pending_rn is None and not sk:
                _tm = _re.search(r"([A-Za-z]{2,}-)\s*$", ln)
                if _tm:
                    # 只记尾巴，不 continue——同一行可能还带武器名（「视使之触 Sati-」）
                    pending_rn = _tm.group(1)
        # 非词条行 → 武器名候选
        # ⚠️ 只要有数字/百分号就不可能是武器名行。旧实现是 `[%x]|\d`——把含字母 x 的
        # **紫卡名/武器名**（Hexado / Vex 类）当成「x 乘数」整行丢弃，导致
        # 「冰凇 Hexado」这种卡完全匹配不上武器（实测 2026-11-02）。
        if _re.search(r"\d", ln) or _re.search(r"[%％]", ln):
            continue
        if not _re.search(r"[A-Za-z\u4e00-\u9fa5]", ln):
            continue
        if not weapon_key:
            low = _re.sub(r"\s+", "", ln).lower()
            best = None
            for wk in sorted(disp_map, key=len, reverse=True):
                # ⚠️ 大小写必须对齐：low 已 lower()，若拿原文比，英文界面截图永远命中不了
                # （旧实现即此 bug，2026-11-02 修）。
                if wk.replace(" ", "").lower() in low:
                    best = wk
                    break
            # 中文武器名反查（国际服客户端可为中文界面）
            if best is None and warframe._zh_lookup:
                lown = _re.sub(r"\s+", "", ln).lower()
                disp_flat = {k.replace(" ", "").lower(): k for k in disp_map}
                for zk in sorted(warframe._zh_lookup, key=len, reverse=True):
                    if zk.replace(" ", "").lower() in lown:
                        for en in warframe._zh_lookup[zk]:
                            if en.replace(" ", "").lower() in disp_flat:
                                best = disp_flat[en.replace(" ", "").lower()]
                                break
                        if best:
                            break
            # 紫卡倾向表自带官方中文名（riven_disp.json 的 zh 字段，比 zh_names.json 新）
            if best is None:
                for wk in sorted(disp_map, key=len, reverse=True):
                    zhn = (disp_map[wk].get("zh") or "").replace(" ", "").lower()
                    if len(zhn) >= 2 and zhn in low:
                        best = wk
                        break
            if best:
                weapon_key = best
                known_weapons_low.add(best.lower())

    # 武器名 OCR 残读兜底（必须在 None 判定之前）：精确子串匹配失败时，
    # 用子序列覆盖匹配找回（例：'努寇微波枪' 被读成 '努寇微波№ni一'，'枪' 缺失）。
    # 仅作兜底，不覆盖已命中的精确武器名。
    if weapon_key is None:
        weapon_key = _fuzzy_weapon(lines, disp_map, warframe._zh_map)

    # 单词式紫卡名兜底（如「冰凇 Hexado」）：取第一个不是武器名的拉丁词。
    # 必须在 weapon_key 定稿之后做，否则英文界面下会把武器名误当紫卡名。
    if riven_name is None and weapon_key:
        _wk = weapon_key.lower().replace(" ", "")
        for _t in _name_tokens:
            _tl = _t.lower()
            if _tl == _wk or _tl in _wk or _wk in _tl:
                continue
            riven_name = _t
            break

    if not weapon_key or not stats:
        return None
    # 双引擎合并会产生重复词条行：按 (词条, 符号) 去重，保留首次读数
    seen = set()
    dedup = []
    for s in stats:
        key = (s["en"], s["sign"])
        if key not in seen:
            seen.add(key)
            dedup.append(s)
    stats = dedup

    # ---- 布局感知：诅咒（负）词条判定 ----
    # 游戏内确定事实：诅咒永远渲染为红色、且排在所有词条最后一行。
    # 优先用"红色像素采样"直接判定（比脆弱的负号 OCR 可靠）；
    # 仅在无颜色信号（小图/压缩洗掉红色）时回退到"最后一条判负"的版式约定。
    color_curse = None
    if boxes and img_data:
        try:
            _img = Image.open(io.BytesIO(img_data)).convert("RGB")
        except Exception:  # noqa: BLE001
            _img = None
        if _img is not None:
            # 建立 去空白行文本 → 包围盒 的查找表
            _boxmap = {}
            for (_t, _x, _y, _w, _h) in boxes:
                _k = _re.sub(r"\s+", "", _t or "")
                if _k and _k not in _boxmap:
                    _boxmap[_k] = (_x, _y, _w, _h)
            for _s in stats:
                _ln = _re.sub(r"\s+", "", _s.get("_line", ""))
                # 优先用逻辑行自带的合并包围盒（_group_rows 产出）；纯文本降级路径
                # 仍按去空白行文本回查 _boxmap。
                _box = _s.get("_box") or _boxmap.get(_ln)
                _s["_red"] = _sample_red(_img, _box) if _box else 0.0
                _s["_box_y"] = _box[1] if _box else None
            # 红色占比最高且过阈值的那条 = 诅咒（全卡仅一条）
            _best_i = max(range(len(stats)), key=lambda i: stats[i].get("_red", 0.0))
            _best_red = stats[_best_i].get("_red", 0.0)
            if _best_red >= 0.08:
                color_curse = _best_i
                if stats[_best_i]["sign"] != "-":
                    stats[_best_i]["sign"] = "-"
                    stats[_best_i]["sus_neg"] = True
                    logger.info(f"wf_riven_color_curse: weapon={weapon_key} "
                                f"stat={stats[_best_i]['en']} red={_best_red:.2f}")
            # 伴侣数值行兜底（主信号）：卡面会把数值另行渲染成"压缩数字 + /MR残留"
            # （如 -17,40/0 / +38, 30/0）。负伴侣行（- 开头）是诅咒的强信号，且它
            # 通常完整保留被主词条行漏读的首位数字（17.4 不被读成 7.4），故按 y 临近
            # 匹配到主词条，补全真实值并锁定为负词条（比脆弱的红字判定可靠）。
            _companions = _extract_companions(boxes)
            for (_cyy, _csign, _cval) in _companions:
                if _csign != "-":
                    continue
                _best_s = None
                for _i, _s in enumerate(stats):
                    _sy = _s.get("_box_y")
                    if _sy is None:
                        continue
                    if _best_s is None or abs(_sy - _cyy) < abs(stats[_best_s]["_box_y"] - _cyy):
                        _best_s = _i
                if _best_s is not None and abs(stats[_best_s]["_box_y"] - _cyy) <= 18:
                    _was = stats[_best_s]["sign"]
                    stats[_best_s]["value"] = _cval
                    stats[_best_s]["sign"] = "-"
                    stats[_best_s]["sus_neg"] = True
                    color_curse = _best_s
                    logger.info(f"wf_riven_companion_neg: weapon={weapon_key} "
                                f"stat={stats[_best_s]['en']} sign={_was}->- val={_cval}")

    # 按卡面视觉 y 坐标排序（OCR 文本行顺序不一定从上往下），保证"最后一条"
    # 诅咒回退基于真实排版顺序；排序在布局块之后，因此 _box_y 已可用。
    stats.sort(key=lambda s: s.get("_box_y", 0))

    # 紫卡规则：3 正 + 1 负。若颜色信号未给出诅咒（小图/压缩无解），
    # 按版式约定把**视觉最后一条**判为负（游戏内负词条固定排最后），并留标记
    pos_cnt = sum(1 for s in stats if s["sign"] != "-")
    if color_curse is None and pos_cnt >= 4 and all(s["sign"] != "-" for s in stats):
        stats[-1]["sign"] = "-"
        stats[-1]["sus_neg"] = True
        logger.warning(f"wf_riven_ocr_sus_neg_applied: weapon={weapon_key} "
                       f"last_stat={stats[-1]['en']} {stats[-1]['value']}")
    d = disp_map[weapon_key]
    # 词条值用理论区间裁决（必须在 sign / 诅咒判定之后——区间系数依赖正负布局）
    try:
        _refine_values(stats, d["type"], float(d["disp"]))
    except Exception as e:  # noqa: BLE001 — 裁决失败保留原读数，不影响出卡
        logger.warning(f"wf_riven_value_refine_failed: {type(e).__name__}: {e}")
    wmap = (warframe._zh_map or {}).get(weapon_key) or {}
    # 中文名优先用倾向表自带的官方中文名（与最新官方导出同源），旧 zh_names.json 兜底
    weapon_zh = (d.get("zh") or "") or ((wmap.get("zh") if isinstance(wmap, dict) else "") or "")
    # 截图默认按**满级 Rank 8** 分析（与 koishi 文档同口径）：卡面徽章数字不可靠
    # （实测读出 18），猜错等级会把区间整体放大 (rank+1)/9 倍。截图只做满级结论，
    # 明确写出假设，避免"半张卡算出两套数"。
    return {"weapon": weapon_key, "weapon_zh": weapon_zh, "riven_name": riven_name,
            "rank": rank if rank is not None else 8,
            "rank_assumed": rank is None,
            "mr": mr,
            "disp": float(d["disp"]), "type": d["type"], "stats": stats}


def _weight(en: str, wtype: str) -> int:
    """词条权重（近战特化：攻速/范围/连击/重击上调）。"""
    w = _STAT_WEIGHT.get(en, 10)
    if wtype == "Melee":
        if en in ("Range", "Initial Combo", "Combo Duration", "Heavy Attack Damage",
                  "Heavy Attack Efficiency", "Combo Count Chance", "Attack Speed"):
            w += 12
    return w


def _disp_dots(disp: float) -> str:
    """[已废弃] 旧倾向星级点阵。DE 44.1.3 移除倾向差异后不再对外输出。"""
    return ""  # 旧分档实现已删除，任何调用点都拿不到星级点阵


def _letter(score: float) -> str:
    """字母档：S≥90 / A≥80 / B≥70 / C≥60 / D≥50 / E<50。"""
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "E"


def _refine_values(stats: list, rtype: str, disp: float) -> None:
    """用**卡内共识尺度**裁决同一条词条的多个 OCR 读数。

    为什么不能用「理论区间（倾向 1.2 + 满级）」裁决：紫卡截图里有大量**未满级卡**
    和**旧倾向卡**（第三方语料 2026-10 的 12 张里就有 6 张），它们的真实数值天然
    远低于 1.2/满级区间。按区间"修正"会把正确读数糟蹋掉（实测 +11.1% 被改成 111%）。

    改成只看**这一张卡内部是否自洽**：每条词条给出尺度
    ``k_i = value_i / (base_i × 布局系数)``（等价于 disp×(rank+1)/9）。
    同一张卡的 k_i 必然彼此接近（无论满级/未满级/新旧倾向），
    只有 OCR 读错的那一条会离群。于是：

      1. 先取当前读数的 k 中位数作为共识；
      2. 每条词条从**全部原始候选**里挑 k 最接近共识的那个（不造数，只选）；
      3. 全部候选都离群（>3×）时，才做 ×10 / 补首位数字 的派生修复。
    """
    base_tbl = (_RIVEN["base"] or {}).get(rtype) or {}
    if not base_tbl or len(stats) < 2:
        return
    pos = sum(1 for s in stats if s["sign"] != "-")
    neg = len(stats) - pos
    fkey = str(min(pos, 3)) + ("1" if neg else "")
    fix = _FIX.get(fkey, _FIX["3"])

    def _base_of(en: str):
        b = base_tbl.get(en)
        if b is None:
            b = base_tbl.get(en.replace(" vs ", " to "))
        if b is None:
            b = base_tbl.get(en.replace(" to ", " vs "))
        return abs(b) if b else 0.0

    def _factor(s) -> float:
        return abs(fix["curse"]) if s["sign"] == "-" else fix["buff"]

    def _k(v: float, s) -> float:
        b = _base_of(s["en"])
        return (v / (b * _factor(s))) if b else 0.0

    # 1) 共识（用当前已选值的中位数，抗单条离群）
    ks = sorted(_k(s["value"], s) for s in stats if _base_of(s["en"]))
    if not ks:
        return
    med = ks[len(ks) // 2]
    if med <= 0:
        return

    for s in stats:
        base = _base_of(s["en"])
        if not base:
            continue
        # 补单位：卡面整行丢 % 时，百分比量级词条（base≥20）按 % 处理
        if s["unit"] == "" and base >= 20.0:
            s["unit"] = "%"
        # 倍率型词条（派系伤害）<1 就是减伤 = 诅咒。卡面不会有负号，靠这条判。
        if s["unit"] == "x" and float(s["value"]) < 1.0:
            s["sign"] = "-"
        cands = {round(c["value"], 4) for c in (s.get("_cands") or [])}
        cands.add(round(float(s["value"]), 4))
        # 2) 只在原始候选里挑最贴近共识的（不造数）
        near = [(abs(_k(v, s) / med - 1.0), v) for v in cands if v > 0]
        if near:
            best = min(near)
            if best[0] <= 0.15:  # 与共识差 ≤15% 视为同一读数族
                if abs(best[1] - s["value"]) > 1e-9:
                    logger.info(f"wf_riven_value_pick: {s['en']} {s['value']:g} -> "
                                f"{best[1]:g} (共识 k={med:.3f})")
                s["value"] = best[1]
                continue
        # 3) 候选全离群 → 派生修复（漏读一位 / 小数点丢失）
        pool: list[float] = []
        for v in cands:
            pool.extend([v * 10.0, v * 100.0])
            vs = f"{v:g}"
            for d in "123456789":
                try:
                    pool.append(float(d + vs))
                except ValueError:
                    continue
        good = [(abs(_k(v, s) / med - 1.0), v) for v in pool if v > 0]
        if good:
            sc, v = min(good)
            if sc <= 0.15 and abs(v - s["value"]) > 1e-9:
                logger.warning(f"wf_riven_value_fix: {s['en']} {s['value']:g} -> {v:g} "
                               f"(共识 k={med:.3f})")
                s["value"] = v


def _stat_range(s: dict, parsed: dict, fix: dict) -> tuple[float, float] | None:
    """词条理论范围 [lo, hi]（base × disp × factor × [0.9, 1.1]）。

    base 是加成量：百分比词条直接显示；派系伤害（x 乘数）显示 1+加成量。
    """
    base_tbl = (_RIVEN["base"] or {}).get(parsed["type"], {})
    base = base_tbl.get(s["en"])
    # koishi 表只存 "to" 键，OCR 可能命中 "vs" 变体
    if base is None and " vs " in s["en"]:
        base = base_tbl.get(s["en"].replace(" vs ", " to "))
    if base is None and " to " in s["en"]:
        base = base_tbl.get(s["en"].replace(" to ", " vs "))
    if not base:
        return None
    # ⚠️ 正负词条用**各自的布局系数**：3正1负的 buff=0.9375 / curse=−0.75，
    # 旧实现两条都用 buff（区间宽 1.25 倍）。同时把符号归一：诅咒区间返回负值区间，
    # 与 _fmt_stat_value 里 signed=−value 的比较空间一致（否则 roll 恒为 0 → "低 100%"）。
    f = fix["buff"] if s["sign"] != "-" else fix["curse"]
    mid = abs(base) * parsed["disp"] * abs(f)
    sign = -1.0 if s["sign"] == "-" else 1.0
    lo, hi = sorted((mid * 0.9 * sign, mid * 1.1 * sign))
    return lo, hi


def _fmt_stat_value(s: dict, lo: float | None, hi: float | None, wtype: str = "") -> str:
    """词条行：+202.8% 近战伤害（187.4%-229.3%）低 36.8%

    负词条（诅咒）的数值以「带符号量」参与 roll/偏差计算：s["value"] 存的是
    正的绝对值、sign="-"，这里用 signed = -value 还原成负量，再与负区间
    [lo,hi]（均为负）比较，否则会把 +85.8 错对齐到负区间算成 低204%（已修）。
    """
    v = s["value"]
    sign = "+" if s["sign"] != "-" else "-"
    signed = -v if s["sign"] == "-" else v   # 负词条还原成带符号量
    unit = s["unit"]
    # 持续类词条游戏中为秒（OCR 纯数字），补单位
    if unit == "" and s["en"] in ("Combo Duration", "Status Duration"):
        unit = "秒"
    # 近战语境下 Damage 显示「近战伤害」（社区叫法）
    zh = "近战伤害" if (wtype == "Melee" and s["en"] == "Damage") else s["zh"]
    # 乘数型显示 x1.59
    if unit == "x":
        val_str = f"x{v:g}"
    else:
        val_str = f"{sign}{v:g}{unit}"
    head = f"  · {val_str} {zh}"
    if lo is None or hi is None:
        return head + "（无范围表）"
    neg = lo < 0 and hi < 0
    if unit == "%":
        rng = (f"（-{abs(lo):.1f}%~-{abs(hi):.1f}%）" if neg
               else f"（{lo:.1f}%-{hi:.1f}%）")
    elif unit == "x":
        # base 是加成量，x 乘数 = 1 + 加成量（x1.51-x1.63）
        rng = f"（x{1 + lo:.2f}-x{1 + hi:.2f}）"
    elif unit == "秒":
        rng = f"（{lo:.1f}秒-{hi:.1f}秒）"
    else:
        rng = f"（{lo:.1f}-{hi:.1f}）"
    roll_v = signed - 1.0 if unit == "x" else signed  # x 乘数需换算成加成量再算 roll
    roll = 1.0 if hi <= lo else min(1.0, max(0.0, (roll_v - lo) / (hi - lo)))
    # 偏离度 = 相对范围中值的百分比（正=高于中值），与社区「低3.68%」同尺度
    mid = (lo + hi) / 2
    dev = (roll_v - mid) / mid * 100.0 if mid else 0.0
    side = "高" if dev >= 0 else "低"
    pct = abs(dev)
    out = head + f" {rng} {side}{pct:.1f}%"
    # OCR 漏读保护：正词条数值远低于范围下界（如 222.6% 被读成 22.6%）
    if s["sign"] != "-" and roll <= 0.05 and roll_v < lo * 0.85:
        out += "（疑似漏读）"
    return out


def _eval(parsed: dict) -> str:
    """⛔ 旧版评估文本（2026-11-04 22:25 起**已无任何调用方**）。

    旧代码在 ``build_card_async`` 失败时会回退到这里，属于用户明令禁止的
    「失败后降级到次品路径」。现在 ``riven_img_analyze_full`` 失败时直接返回
    错误原因文本，不再调用本函数。**禁止把这里重新接成任何兜底路径**；
    保留仅为兼容历史数据核对，后续可整体删除。
    """
    stats = parsed["stats"]
    pos = [s for s in stats if s["sign"] != "-"]
    neg = [s for s in stats if s["sign"] == "-"]
    # 紫卡规则：最多 3正+1负；OCR 丢负号会把 4 条全判成正 → 按 3正1负 系数并提示
    sus_neg = len(pos) >= 4 and not neg
    key = str(min(len(pos), 3)) + ("1" if (neg or sus_neg) else "")
    fix = _FIX.get(key, _FIX["3"])
    disp = parsed["disp"]
    wtype = parsed["type"]

    lines = []
    for s in stats:
        rng = _stat_range(s, parsed, fix)
        lines.append(_fmt_stat_value(s, rng[0] if rng else None, rng[1] if rng else None, wtype))
    lines.append("* 高低值上下限为 ±10%（理论范围 = 基础值×倾向×组合系数），越接近 0/100 越极端")
    if sus_neg:
        lines.append("* 检测到 4 个正词条（紫卡最多 3 正），疑似 OCR 漏读负号，按 3正1负 计算")

    # ---- 综合评分（0-100，字母档） ----
    total = 0.0
    top_count = 0
    core_count = 0
    CORE = {"Critical Chance", "Critical Damage", "Damage", "Multishot"}
    dmg_w, dmg_v = 0.0, 0.0
    for s in pos:
        rng = _stat_range(s, parsed, fix)
        roll_v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
        roll = 1.0 if not rng or rng[1] <= rng[0] else min(1.0, max(0.0, (roll_v - rng[0]) / (rng[1] - rng[0])))
        w = _weight(s["en"], wtype)
        total += w * (0.5 + 0.5 * roll)
        dmg_w += w
        dmg_v += w * roll
        if roll >= 0.95:
            top_count += 1
        if s["en"] in CORE:
            core_count += 1
    for s in neg:
        total -= _NEG_DAMAGE.get(s["en"], 10)
    if not neg:
        total += 10
    if core_count >= 2:
        total += 10
    if len(pos) >= 3:
        total += 5
    total += top_count * 3
    total = min(100.0, max(0.0, round(total)))
    # 分类评分：伤害 = 词条 roll 加权；市场 = 词条本身值钱度（不看 roll）
    dmg_score = round(100 * dmg_v / dmg_w, 1) if dmg_w else 0.0
    max_w = sum(_weight(c, wtype) for c in CORE) if len(pos) >= 1 else 1
    mkt_score = round(100 * min(1.0, sum(_weight(s["en"], wtype) for s in pos) / max(2, max_w)), 1)
    card_type = "市场卡" if not neg or all(_NEG_DAMAGE.get(s["en"], 10) <= 5 for s in neg) else "自用卡"

    head = f"【紫卡分析】{parsed['weapon'].upper()}"
    if parsed.get("weapon_zh"):
        head += f"（{parsed['weapon_zh']}）"
    rank_txt = f"等级:{parsed['rank']}" if parsed.get("rank") else "等级:未知"
    head += f" {rank_txt} 倍率:{disp:g}(统一) 词条:{len(pos)}正{len(neg)}负"

    lines.append(f"紫卡综合评分 - {_overall_grade(parsed, total)} - {card_type}")
    lines.append(f"分类评分：大佬 暂无｜伤害 {dmg_score}｜市场 {mkt_score}"
                 + (" ✓" if mkt_score >= 50 else ""))

    # ---- 推荐洗卡（伤害维度 T0-T3；市场维度 T0-T2） ----
    wmap_e = {e: _weight(e, wtype) for e in _RIVEN["en_keys"]}
    def tiers(pred=None):
        t0, t1, t2, t3 = [], [], [], []
        for e, w in sorted(wmap_e.items(), key=lambda x: -x[1]):
            if pred and not pred(e):
                continue
            zh = _stat_zh(e)
            if w >= 36:
                t0.append(zh)
            elif w >= 28:
                t1.append(zh)
            elif w >= 20:
                t2.append(zh)
            elif w >= 12:
                t3.append(zh)
        return t0, t1, t2, t3
    d0, d1, d2, d3 = tiers()
    lines.append("推荐洗卡（伤害维度 T0-T3）："
                 + "｜".join(["T0:" + "/".join(d0) if d0 else "T0:暂无",
                              "T1:" + "/".join(d1) if d1 else "T1:暂无",
                              "T2:" + "/".join(d2[:4]) if d2 else "T2:暂无",
                              "T3:" + "/".join(d3[:4]) if d3 else "T3:暂无"]))
    m0, m1, m2, _m3 = tiers(lambda e: e in _FACTION)
    lines.append("推荐洗卡（市场维度 T0-T2，派系卡溢价）："
                 + "｜".join(["T0:" + "/".join(m0) if m0 else "T0:暂无",
                              "T1:" + "/".join(m1) if m1 else "T1:暂无",
                              "T2:" + "/".join(m2) if m2 else "T2:暂无"]))

    # ---- 推荐负面 / 致命负词（to/vs 键去重） ----
    light = sorted((e for e in _NEG_DAMAGE if _NEG_DAMAGE[e] <= 5),
                   key=lambda x: _NEG_DAMAGE[x])
    light = [e for e in light if e.replace(" vs ", " to ") not in
             {x.replace(" vs ", " to ") for x in light if x != e} or " vs " not in e]
    heavy = sorted((e for e in _NEG_DAMAGE if _NEG_DAMAGE[e] >= 22),
                   key=lambda x: -_NEG_DAMAGE[x])
    heavy = [e for e in heavy if e.replace(" vs ", " to ") not in
             {x.replace(" vs ", " to ") for x in heavy if x != e} or " vs " not in e]
    lines.append("推荐负面词条（轻伤，市场更友好）：" + ("/".join(_stat_zh(e) for e in light[:6]) or "暂无"))
    lines.append("致命负词条（千万别洗出）：" + ("/".join(_stat_zh(e) for e in heavy[:4]) or "暂无"))

    # ---- 指令与说明 ----
    lines.append(f"指令：#wf 紫卡 {parsed['weapon']}（市价）｜#wf 紫卡分析 {parsed['weapon']}（面板+倾向）")
    return "\n".join([head] + lines)


def _overall_grade(parsed: dict, fallback_total: float) -> str:
    """「紫卡综合评分」文本——与紫卡分析卡**同一口径**（各词条偏差绝对值均值 + 字母档）。

    旧兜底路径用的是 0~100 的启发式加权分（_letter(total)），与主路径数值不可比：
    同一张卡在主路径是 3.910(A-)、兜底却可能是 100.0(S)（实测 2026-11-02）。
    评分只应有一套口径，故此处复用 riven_card 的 _fix_of / _mid / _grade；
    复用失败（构造异常）才退回旧启发式分数。
    """
    try:
        from .riven_card import _fix_of, _grade, _mid
        fix = _fix_of(parsed)
        devs: list[float] = []
        for s in parsed["stats"]:
            m = _mid(s, parsed, fix)
            if not m:
                continue
            v = (s["value"] - 1.0) if s["unit"] == "x" else s["value"]
            devs.append((abs(v) / m - 1.0) * 100.0)
        if not devs:
            raise ValueError("no ranged stat")
        overall = sum(devs) / len(devs)
        return f"{overall:.3f}({_grade(overall)})"
    except Exception:  # noqa: BLE001 — 兜底失败就用旧启发式分，绝不因此报错
        return f"{fallback_total} ({_letter(fallback_total)})"


def help_line() -> str:
    return "  #wf 紫卡图（发紫卡截图+@机器人：OCR 识别词条并评级）"


def _ocr_diagnose(text: str, boxes: list | None) -> str:
    """解析失败时的**可读诊断**（替代整段 OCR 原文倾倒）。

    分别回答三件事：读到了哪些词条？疑似标题行是什么？下一步能怎么办。
    旧实现直接回贴 OCR 原文（`+ 10L4 ％ * 冰 冻 伤 害` 这种），用户看不懂也不知道下一步
    （实测 2026-11-02 用户反馈「识别结果完全没法看」）。
    """
    _load_riven_data()  # 幂等；独立调用（非 _parse_ocr 之后）时也必须可用
    units = _group_rows(boxes) if boxes else [
        {"text": ln, "texts": [ln], "box": None, "y": None}
        for ln in (text or "").splitlines() if ln.strip()]
    stats: list[str] = []
    titles: list[str] = []
    for u in units:
        ln = u["text"].strip()
        if not ln:
            continue
        sk = _find_stat(ln)
        if sk:
            v = _pick_row_value(u["texts"])
            if v:
                unit = v["unit"]
                stats.append(f"{v['sign']}{v['value']:g}{unit} {_stat_zh(sk)}")
            continue
        # 标题行候选：含中文或拉丁、不含数字/百分号
        if not _re.search(r"\d", ln) and not _re.search(r"[%％]", ln) \
                and _re.search(r"[A-Za-z\u4e00-\u9fa5]", ln):
            titles.append(_re.sub(r"\s+", " ", ln))
    lines = []
    if stats:
        lines.append("读到词条：" + " / ".join(stats[:4]))
    else:
        lines.append("没读完整词条（图可能太小、被裁切或词条行被遮挡）。")
    if titles:
        lines.append("标题行读到：" + " / ".join(titles[:2]))
    if stats and not titles:
        lines.append("但没找到武器名——把卡面标题那一行完整截进来即可。")
    lines.append("也可直接发「#wf 紫卡分析 <武器名>」先看面板+倾向。")
    return "\n".join(lines)


async def _weapon_render_path(weapon_en: str) -> str | None:
    """取武器立绘（WFCD data/img 的透明 PNG）本地缓存路径；没有就短超时补拉一次。

    分析卡右上角要贴武器图（参考卡如此）。只用**已缓存**的图会漏新武器，
    所以缓存缺失时给 4s 上限补拉一次；仍失败就返回 None（模板会留空，不报错、不占位）。
    """
    try:
        from ..pic import weapon_icons as wi
        from . import riven_card
        raw = riven_card._weapon_raw(weapon_en) or {}
        img_name = raw.get("imageName")
        # 即便本地武器库没有该武器的 imageName（如 Gratlter），也继续用英文名
        # CamelCase 直接回源 WFCD 仓库取图——不再因本地库覆盖度而直接放弃。
        p = wi.icon_cache_path(img_name, weapon_en)
        if p is None:
            try:
                got = await asyncio.wait_for(wi.ensure_icon(img_name, weapon_en), timeout=4.0)
            except Exception:  # noqa: BLE001 — 补拉失败不阻塞出卡
                got = None
            # 普通版图标上游（WFCD data/img）缺失时（如 Akarius 只有 AkariusPrime.png），
            # 用同武器 Prime 版图标兜底，右上角不再留空。
            if got is None and not raw.get("isPrime"):
                prime_raw = riven_card._weapon_raw(f"{weapon_en} Prime") or {}
                prime_img = prime_raw.get("imageName")
                if prime_img:
                    try:
                        got = await asyncio.wait_for(
                            wi.ensure_icon(prime_img, f"{weapon_en} Prime"), timeout=4.0)
                    except Exception:  # noqa: BLE001
                        got = None
            p = wi.icon_cache_path(got or img_name, weapon_en)
        return str(p) if p else None
    except Exception as e:  # noqa: BLE001 — 立绘只是装饰，任何异常都不影响分析
        logger.warning(f"wf_riven_weapon_img_failed: {type(e).__name__}: {e}")
        return None


async def riven_img_analyze_full(data: bytes) -> tuple[str, dict | None]:
    """紫卡截图 → (文字卡 markdown, 图片卡数据)。

    图片卡数据用于 ``pic`` 的 ``riven_analysis`` 模板（逐像素复刻参考卡）。
    2026-11-04 22:25：市场数据由 ``riven_card.fetch_market_for_card`` 只抓一次，
    成功态与失败态都必须显式进卡面；失败时错误原因同时写进日志与文字卡，
    不回退旧评估、不写「暂无」冒充成功。
    """
    if not ocr_mod.available():
        return ("本机 OCR 引擎不可用（需 Windows 10/11 自带 OCR），\n"
                "可先发「#wf 紫卡分析 <武器>」看面板+倾向+洗卡建议。", None)
    text = await ocr_mod.ocr_image(data)
    if not text:
        return "没识别出内容，换张清晰点的紫卡截图试试～", None
    # 等级：全图 OCR 常漏读右上角等级徽章 → 单独裁右上角识别（通用布局规则）。
    # 徽章结果为权威值，解析后直接覆盖——全图里散落的裸数字可能是「段位 N」
    # 等无关数字，不能让它抢先占用 rank（实测 Nukor 卡误读段位 10 为等级）。
    _rk = ""
    try:
        _rk = await ocr_mod.ocr_rank(data)
    except Exception as e:  # noqa: BLE001 — 等级补读失败不影响主流程
        logger.warning(f"wf_riven_rank_ocr_failed: {type(e).__name__}: {e}")
    logger.info(f"wf_riven_ocr_raw: len={len(text)} text={text[:1200]!r}")
    # 布局感知：拿行级包围盒，供 _parse_ocr 做"红字诅咒"颜色判定
    boxes = []
    try:
        boxes = await ocr_mod.ocr_image_boxes(data)
    except Exception as e:  # noqa: BLE001 — 盒子失败不影响纯文本解析
        logger.warning(f"wf_riven_boxes_failed: {type(e).__name__}: {e}")
    parsed = _parse_ocr(text, boxes=boxes, img_data=data)
    if parsed and _rk:
        try:
            _rv = int(_rk)  # 徽章裁切识别结果覆盖全图散数字
        except ValueError:
            _rv = -1
        # 徽章只接受合法紫卡等级 0~8；读出 18/10 之类一律视为误读，保留满级假设
        if 0 <= _rv <= 8:
            parsed["rank"] = _rv
            parsed["rank_assumed"] = False
        else:
            logger.info(f"wf_riven_rank_badge_ignored: raw={_rk!r}")
    if not parsed:
        return ("识别到了内容，但没凑齐「武器名 + 词条」：\n"
                + _ocr_diagnose(text, boxes)), None
    logger.info(f"wf_riven_parsed: weapon={parsed['weapon']!r} zh={parsed.get('weapon_zh')!r} "
                f"rname={parsed.get('riven_name')!r} rank={parsed.get('rank')} "
                f"mr={parsed.get('mr')} disp={parsed.get('disp')} type={parsed.get('type')} "
                f"stats={[(s['en'], s['value'], s['unit'], s['sign']) for s in parsed['stats']]}")
    # 2026-11-04 22:25：市场数据**只抓一次**，同一份快照同时给文字卡和图片卡。
    # 旧版图片卡 build_card_data(parsed, weapon_image=img) 完全不传市场数据，
    # 于是 PNG 卡底部永远「WM 拍卖样本不足，暂无」，且整块推荐词条缺失
    # （用户截图：冷冻光束步枪 / GLAXION）。
    from . import riven_card  # 懒导入防循环（riven_card → warframe）
    market = await riven_card.fetch_market_for_card(parsed)
    logger.info(f"wf_riven_market: status={market.get('status')} "
                f"slug={market.get('slug')} n={market.get('sample_n')} "
                f"err={market.get('error')}")
    md = ""
    try:
        md = await riven_card.build_card_async(parsed, market=market)
        logger.info(f"wf_riven_card_md(len={len(md)}): {md[:1500]!r}")
    except Exception as e:  # noqa: BLE001
        # 用户纪律「禁止降级/兜底」：不再回退 _eval(parsed) 旧评估冒充成功。
        logger.error(f"riven_card_build_failed: {type(e).__name__}: {e}")
        md = (f"紫卡分析卡构建失败：{type(e).__name__}: {e}\n"
              "（已记录日志；未启用旧版评估兜底）")
    payload = None
    try:
        img = await _weapon_render_path(parsed["weapon"])
        payload = riven_card.build_card_data(parsed, market, weapon_image=img)
    except Exception as e:  # noqa: BLE001 — 失败原因必须可见，不静默
        logger.error(f"riven_card_data_failed: {type(e).__name__}: {e}")
        md += f"\n[图片卡生成失败：{type(e).__name__}: {e}]"
        payload = None
    return md, payload



async def riven_img_parse(data: bytes) -> dict | None:
    """紫卡截图 → OCR → **原始 parsed**（供「紫卡全面分析」卡做真实词条/评分/击杀效率）。

    与 :func:`riven_img_analyze_full` 同源同口径（全图 OCR + 等级徽章补读 + 行级
    boxes 颜色判定），只把中间的 parsed 直接返回；失败返回 None，绝不编造词条。
    """
    if not ocr_mod.available():
        return None
    text = await ocr_mod.ocr_image(data)
    if not text:
        return None
    _rk = ""
    try:
        _rk = await ocr_mod.ocr_rank(data)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_riven_full_rank_ocr_failed: {type(e).__name__}: {e}")
    boxes = []
    try:
        boxes = await ocr_mod.ocr_image_boxes(data)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"wf_riven_full_boxes_failed: {type(e).__name__}: {e}")
    parsed = _parse_ocr(text, boxes=boxes, img_data=data)
    if parsed and _rk:
        try:
            _rv = int(_rk)
        except ValueError:
            _rv = -1
        if 0 <= _rv <= 8:
            parsed["rank"] = _rv
            parsed["rank_assumed"] = False
    return parsed


async def riven_img_analyze(data: bytes) -> str:
    """紫卡截图 → OCR → Rabbit 版「紫卡分析」卡（guide 标记，_send_guide 渲染）。
    新卡构建失败时降级旧版评估文本（绝不静默吞错，CLOSE-02）。"""
    text, _ = await riven_img_analyze_full(data)
    return text
