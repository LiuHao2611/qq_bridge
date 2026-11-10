# -*- coding: utf-8 -*-
"""星际战甲（Warframe）口令对照表 + 战甲外号对照表（管理页展示用）。

数据源与一致性：
  * 口令对照表：curated 文档，覆盖 game_tools.__init__ 的 dispatch() 与
    _wf_dispatch() 实际注册的星际战甲口令（顶部 # 口令 + #wf 子口令 + 市集别名
    + 自然语言口令）。新增/改名口令时务必同步本表。
  * 外号对照表：直接解析 data/warframe_items/战甲别名全表.md（该 md 由 aliases.json
    社区黑话整理而来，唯一权威源），零硬编码，永不与源文件漂移。

两个函数均为纯离线计算，不触发任何外部 HTTP 请求（管理页随时可看，不依赖社区源）。
"""
from __future__ import annotations

import re

from . import warframe

#: 数据目录（与 warframe._DATA_DIR 同源：qq_bridge/data/warframe_items）
_DATA_DIR = warframe._DATA_DIR


# ===========================================================================
# 口令对照表（curated 文档，与 game_tools 实际 dispatch 保持一致）
# 每个条目：
#   group  分组（用于前端分组展示）
#   cmd    规范口令写法（示例）
#   aliases 其余可接受触发词（中文别名 / 顶部 # 口令 / #wf 子口令）
#   desc   一句话功能说明
#   example 示例用法
# ===========================================================================
def command_reference() -> list[dict]:
    """星际战甲全部口令对照表（管理页「口令对照表」展示）。"""
    return [
        # ---- 世界状态（实时数据：Tenno.Tools / 官方 worldState）----
        {"group": "世界状态·实时", "cmd": "#wf 突击", "aliases": "突击",
         "desc": "今日三连突击：阵营 / Boss / 奖励池 / 剩余时间", "example": "#wf 突击"},
        {"group": "世界状态·实时", "cmd": "#wf 奸商", "aliases": "虚空商人",
         "desc": "Baro Ki'Teer 来访状态 / 地点 / 剩余（每两周周五~周日）", "example": "#wf 奸商"},
        {"group": "世界状态·实时", "cmd": "#wf 特惠", "aliases": "每日特惠",
         "desc": "Darvo 每日折扣商品（-x%）", "example": "#wf 特惠"},
        {"group": "世界状态·实时", "cmd": "#wf 裂隙", "aliases": "普通裂隙 / 钢铁裂隙",
         "desc": "虚空裂隙按纪元分组（普通 / 钢铁 / 速览）", "example": "#wf 裂隙"},
        {"group": "世界状态·实时", "cmd": "#wf 入侵", "aliases": "入侵",
         "desc": "阵营入侵战进度（选奖励好的一边打）", "example": "#wf 入侵"},
        {"group": "世界状态·实时", "cmd": "#wf 仲裁", "aliases": "仲裁",
         "desc": "当前仲裁任务（每 1 小时轮换）", "example": "#wf 仲裁"},
        {"group": "世界状态·实时", "cmd": "#wf 电波", "aliases": "电波",
         "desc": "午夜电波赛季与本周 / 每日挑战", "example": "#wf 电波"},
        {"group": "世界状态·实时", "cmd": "#wf 平原", "aliases": "平原",
         "desc": "夜灵平原 / 奥布山谷 / 地球野外昼夜与剩余", "example": "#wf 平原"},
        {"group": "世界状态·实时", "cmd": "#wf 九重天", "aliases": "九重天",
         "desc": "九重天虚空风暴（含倒计时）", "example": "#wf 九重天"},
        {"group": "世界状态·实时", "cmd": "#wf 周本", "aliases": "周本",
         "desc": "周期任务一览（执政官狩猎 / 深层档案馆 / 电波周等）", "example": "#wf 周本"},
        {"group": "世界状态·实时", "cmd": "#wf 轮换", "aliases": "轮换",
         "desc": "信条 / 终幕 / 赤毒 / 灵化武器轮换", "example": "#wf 轮换"},
        {"group": "世界状态·实时", "cmd": "#wf 信条", "aliases": "信条 / tenet（无参数）",
         "desc": "佩兰数列信条武器轮换卡：5 把武器 + 元素加成 + 刷新倒计时（单列粉紫版式）；"
                 "与 #wf 终幕 是两个口令、两套版式。带武器名（#wf 信条 弧电）则走市价查询",
         "example": "#wf 信条"},
        {"group": "世界状态·实时", "cmd": "#wf 终幕", "aliases": "终幕 / coda",
         "desc": "终幕(Coda)信条武器轮换卡：埃莉诺售卖清单 + 元素加成 + 刷新倒计时（2 列网格）", "example": "#wf 终幕"},
        {"group": "世界状态·实时", "cmd": "#wf 赏金", "aliases": "赏金 / 钢铁赏金 / 钢路",
         "desc": "赏金任务一览：希图斯 / 索拉里斯 / 殁世幽都 + 深矿 + 扎里曼 / 圣所 / 六人组",
         "example": "#wf 赏金"},
        {"group": "世界状态·实时", "cmd": "#wf 科研", "aliases": "科研 / netracell",
         "desc": "本周深层 / 时光实验室科研任务（钢铁难度词缀）", "example": "#wf 科研"},

        # ---- 市场交易（Warframe.Market）----
        {"group": "市场交易", "cmd": "#wf 价格 <物品>", "aliases": "市价 / wm / 市场",
         "desc": "查任意物品市价：最低卖价 / 最高买价 / 挂单数；「价格 分类」看全部分类",
         "example": "#wf 价格 充沛"},
        {"group": "市场交易", "cmd": "#wf 赤毒 <武器>", "aliases": "赤毒 / kuva",
         "desc": "赤毒武器市价（如 #wf 赤毒 绝路）", "example": "#wf 赤毒 沙皇"},
        {"group": "市场交易", "cmd": "#wf 信条 <武器>", "aliases": "信条 / tenet（带参数）",
         "desc": "信条武器市价（如 #wf 信条 弧电）；不带参数出信条武器轮换卡，见世界状态·实时",
         "example": "#wf 信条 弧电"},
        {"group": "市场交易", "cmd": "#wf 紫卡 <武器>", "aliases": "紫卡 / riven / zk",
         "desc": "紫卡市价（如 #wf 紫卡 绝路）", "example": "#wf 紫卡 绝路"},
        {"group": "市场交易", "cmd": "#wf 紫卡分析 <武器>", "aliases": "紫卡分析 / 分析 / 评估",
         "desc": "紫卡属性评估（极性 / 正负面 / 数值范围）", "example": "#wf 紫卡分析 绝路"},
        {"group": "市场交易", "cmd": "#wf 倾向 <武器>", "aliases": "倾向",
         "desc": "现行紫卡倍率统一 1.20（2026-10-22 起）+ 改动前对照；不再有星级强弱",
         "example": "#wf 倾向 绝路"},
        {"group": "市场交易", "cmd": "#wf 词条推荐 <武器>", "aliases": "词条推荐 / 洗卡推荐 / 紫卡推荐",
         "desc": "紫卡洗卡词条推荐卡（击杀效率 + 市场挂牌分层 双维度整卡）", "example": "#wf 词条推荐 凤殁"},
        {"group": "市场交易", "cmd": "#wf 紫卡趋势 <武器>", "aliases": "紫卡趋势 / riven趋势",
         "desc": "紫卡挂牌中位价本地采样走势（非成交价；首次只有 1 个点，如实标注）",
         "example": "#紫卡趋势 绝路"},
        {"group": "市场交易", "cmd": "#wf 词条价差 <武器>", "aliases": "词条价差 / 紫卡价差 / 紫卡词条价差",
         "desc": "正面词条相对基准池的同分位溢价排行（同等级 rank8 + 中位数）",
         "example": "#词条价差 绝路"},
        {"group": "市场交易", "cmd": "#wf 紫卡融合 [武器]", "aliases": "紫卡融合 / riven融合",
         "desc": "紫卡融合机制 + 融合费用表（Wiki 公式：紫卡按 Rare 稀有度 EBC=30/CrBC=1449）+ 真实 rank8 中位价；WFM 无 fuse 字段，不编造按融合等级的市价",
         "example": "#紫卡融合 绝路"},
        {"group": "市场交易", "cmd": "#wf 紫卡全面 [武器]", "aliases": "紫卡全面 / 紫卡全面分析 / 全面分析",
         "desc": "655×1920 紫卡全面分析卡（像素级复刻 Rabbit）：数值分析 / 融合词条 / 综合评分 / 市场价格预计 / 词条价差预估 / 击杀效率配卡 / 指令区。词条依赖紫卡截图 OCR，武器名路径如实标「需截图」；WFM 不暴露 fuse，词条价差为模型预估 谨慎参考。「样例」为版式复刻夹具并明确标注非实时数据",
         "example": "#紫卡全面 绝路"},
        {"group": "市场交易", "cmd": "#wf 金垃圾", "aliases": "金垃圾 / 垃圾 / 杜卡德",
         "desc": "杜卡德金币价值参考（哪些物品值得换）", "example": "#wf 金垃圾"},

        # ---- 资料查询（离线 warframe-items + 官方 i18n）----
        {"group": "资料查询", "cmd": "#wf 战甲 <名>", "aliases": "战甲 / frame / warframe",
         "desc": "战甲信息：被动 / 技能 / 集团卡（如 #wf 战甲 毒妈）", "example": "#wf 战甲 牛"},
        {"group": "资料查询", "cmd": "#wf mod <名>", "aliases": "mod / 卡 / 卡片",
         "desc": "MOD 信息（满级效果 / 极性，如 #wf mod 压迫点）", "example": "#wf mod 膛线"},
        {"group": "资料查询", "cmd": "#wf 赋能 <名>", "aliases": "赋能 / arcane",
         "desc": "赋能信息（如 #wf 赋能 充沛）", "example": "#wf 赋能 充沛"},
        {"group": "资料查询", "cmd": "#wf 掉落 <名>", "aliases": "掉落 / drop",
         "desc": "物品掉落来源（裂隙 / BOSS / 警报等）", "example": "#wf 掉落 神经网络"},
        {"group": "资料查询", "cmd": "#wf 遗物 <名>", "aliases": "遗物 / 核桃 / relic",
         "desc": "遗物蕴含物一览", "example": "#wf 遗物 Axi"},
        {"group": "资料查询", "cmd": "#wf 配卡 <名>", "aliases": "配卡 / build / 配置",
         "desc": "配卡推荐（如 #wf 配卡 牛）", "example": "#wf 配卡 电男"},

        # ---- 紫卡娱乐 & 小工具 ----
        {"group": "娱乐·小工具", "cmd": "#wf 模拟开卡", "aliases": "模拟开卡 / 开卡",
         "desc": "模拟开一张紫卡（纯娱乐）", "example": "#wf 模拟开卡"},
        {"group": "娱乐·小工具", "cmd": "#wf 仓鼠运势", "aliases": "仓鼠运势 / 运势",
         "desc": "仓鼠今日运势（每天固定）", "example": "#wf 仓鼠运势"},
        {"group": "娱乐·小工具", "cmd": "#wf 黄历", "aliases": "黄历 / 黄道吉日 / 仓鼠黄历",
         "desc": "仓鼠老黄历（宜忌 + 紫卡 + 任务）", "example": "#wf 黄历"},
        {"group": "娱乐·小工具", "cmd": "#wf 结合目标", "aliases": "结合目标 / 大黄脸 / 扫描 / synthesis",
         "desc": "结合目标（Cephalon 扫描）信息", "example": "#wf 结合目标"},
        {"group": "娱乐·小工具", "cmd": "#wf 翻译 <词>", "aliases": "翻译 / translate",
         "desc": "常用中英对照翻译", "example": "#wf 翻译 沙皇"},
        {"group": "娱乐·小工具", "cmd": "#wf wiki <词>", "aliases": "wiki / 维基",
         "desc": "灰机 Wiki 搜索（如 #wf wiki 牛）", "example": "#wf wiki 裂隙"},

        # ---- 自然语言口令（免 # ，直接说也能查）----
        {"group": "自然语言口令", "cmd": "直接说中文口令", "aliases": "突击 / 奸商 / 裂隙 / 价格 / 紫卡 …",
         "desc": "免 # 直接说口令（如「突击」「裂隙」）即触发对应查询（参数 >30 字放行）",
         "example": "直接发：裂隙"},
        {"group": "自然语言口令", "cmd": "直接说 战甲/武器/赋能 中文名或外号",
         "aliases": "毒妈 / 咖喱棒 / 充沛 / 牛 …",
         "desc": "直接说战甲 / 武器 / 赋能的中文名或外号（≥2 字）也能查（精确优先再包含）",
         "example": "直接发：毒妈"},

        # ---- 帮助 ----
        {"group": "帮助", "cmd": "#wf", "aliases": "星际战甲 / 战甲",
         "desc": "星际战甲帮助总览（无参数时）", "example": "#wf"},
        {"group": "帮助", "cmd": "#wf 帮助", "aliases": "帮助 / help / 菜单",
         "desc": "看完整用法说明", "example": "#wf 帮助"},
    ]


# ===========================================================================
# 战甲外号对照表（解析 data/warframe_items/战甲别名全表.md）
# 返回 [{"section": 分组标题, "rows": [{"name": 官方英文名, "aliases": [中文外号...]}]}]
# ===========================================================================
def alias_reference() -> list[dict]:
    """解析 战甲别名全表.md → 结构化 战甲外号对照表（唯一权威源，零硬编码）。"""
    fp = _DATA_DIR / "战甲别名全表.md"
    out: list[dict] = []
    cur: dict | None = None
    try:
        text = fp.read_text(encoding="utf-8")
    except Exception:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("## "):
            cur = {"section": s[3:].strip(), "rows": []}
            out.append(cur)
            continue
        if not s.startswith("|"):
            continue
        # 去掉首尾的 |，再按 | 切分单元格
        inner = s.strip("|")
        cells = [c.strip() for c in inner.split("|")]
        if len(cells) < 2:
            continue
        # 跳过表头行（战甲 | 别名）与分隔行（|---|---|）
        if cells[0] == "战甲" and cells[1] == "别名":
            continue
        if _is_separator(cells):
            continue
        name = cells[0]
        raw = cells[1] if len(cells) > 1 else ""
        aliases = [a.strip() for a in re.split(r"[、,，/]", raw) if a.strip()]
        if cur is None:
            cur = {"section": "战甲", "rows": []}
            out.append(cur)
        cur["rows"].append({"name": name, "aliases": aliases})
    # 给每个分组补 count，方便前端展示总数
    for sec in out:
        sec["count"] = len(sec.get("rows") or [])
    return out


def _is_separator(cells: list[str]) -> bool:
    """判断是否为 markdown 分隔行（单元格仅由 - : 空格组成）。"""
    for c in cells:
        if c and not set(c) <= set("-: "):
            return False
    return True
