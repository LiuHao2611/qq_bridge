# -*- coding: utf-8 -*-
"""斗破门派系统 · 纯数据层（零副作用，不 import nonebot / 不碰现有玩法）。

用途
----
给「新手入道随机分配宗门」提供数据底座。粒度：
    门派 → 代表人物 → 普通斗技 → 特殊斗技 → 特殊物品 → 特殊武器/器物 → 传承异火

资料来源
--------
本文件的人物 / 斗技 / 器物 / 异火归属，来自公开权威整理资料与游戏反编译资源，
逐条标在 ``sources`` 里（编号对应 docs/斗破门派系统_权威资料与数据字典.md 第 0 节）：
    S1 百度百科斗技表   S2/S3 各方势力介绍(中)(下)   S4 中文百科势力结构+天鼎榜
    S5 搜狗百科异火(人-火对应)   S6 搜狗百科天鼎榜   S7 萌娘百科模板
    S8 陀舍古帝玉/萧族/陀舍古帝词条   S9 功法斗技介绍/各派压箱底宝物
    G1 本仓反编译资源 D:/AI/doupo/hy_text
    C1 张谦方《斗破设定整理·斗技整理》(B站 opus 959287705904087041)：斗技全表 + 人物/门派归属
    C2 月影ぜ假面《斗破苍穹 全本资料》(B站 opus 918447480316624896)：装备/物品/丹药/药材/异火/兽火/药鼎/功法/斗技/秘法/体质/阵法/魔兽/地点/人物
    C3 斗破苍穹功法大全(doupocangqiong.la/article/437.html)：功法门派归属（青木剑诀=云岚宗葛叶、狂狮怒罡=萧战、三兽蛮荒决=慕兰谷、血邪功=天冥宗易尘、火蝠功=林焱、暗影功=莫天行、血魂诀=万蝎门蝎毕岩、奔雷鉴=地魔老鬼）
标 ``[游戏原创]`` 的为游戏自造、原著无同名设定。
⚠️ 2026-11-15 原著核对：异火极稀有，flames 已从 19 派收敛到 11 派；
   8 朵自造镇派灵焰 + 毒宗「幽冥毒火/阴阳双炎」（正文 0 命中）全部移除；
   炎族「八荒破灭焱」正名为原著「荒破灭焱」；丹塔三千焱炎火按原著改为「无主之物」。
   每条归属的章节与原句见 _verify_flames.py 的门禁与 docs/门派异火归属_原著证据.md。

字段
----
    name/tier/pool/base   名称 / 分期 / 卡池 / 据点
    npcs                  代表人物（姓名（身份））
    skills                普通功法斗技（入道可学）
    special_skills        特殊斗技：[{name, rank, note, unlock_lv}]
    items                 特殊物品
    relics                特殊武器 / 器物 / 阵法 / 信物
    flames                传承异火（带榜位与持有人）——★ 只准写原著有据的归属，
                          每条必须附「第X章「标题」」；游戏自造 / 二手错名一律不进。
                          由 _verify_flames.py 门禁拦截（原著语料缺失会显式报错）。
    starter               {skill, special, item, relic} 入道发放
    weight                抽取权重
    unlock_lv             该宗门进入卡池的等级门槛
    sparse                True = 原著/游戏对该派斗技记载稀薄（系统里可只给物品线）

⚠️ 本模块只提供数据与纯函数，**不修改 player 之外的任何状态**；
   接入 core.create_player / commands.cmd_enter / card 的工作留给下一轮，
   因此现有 11 套已验收测试完全不受影响。
"""
from __future__ import annotations

import random

TIERS = ("early", "mid", "late")
POOLS = ("starter", "advanced", "hidden")

#: 每个池的等级门槛（建议值，可调）
POOL_UNLOCK = {"starter": 1, "advanced": 40, "hidden": 90}

#: 特殊斗技分阶解锁等级（建议值，正好复用现有「焚血散升 5 级」机制）
SPECIAL_UNLOCK_STEPS = (20, 40, 60, 80)


def _sp(name, rank, note, unlock_lv=20):
    return {"name": name, "rank": rank, "note": note, "unlock_lv": int(unlock_lv)}


FACTIONS = {
    # ------------------------------- 新手池 -------------------------------
    "xiaojia": {
        'name': "萧家",
        'tier': "early",
        'pool': "starter",
        'base': "乌坦城",
        'npcs': [
            "萧战（族长）",
            "萧鼎",
            "萧厉",
            "萧玉",
            "萧薰儿（寄居，古族）"
        ],
        'skills': [
            "狂狮怒罡（玄阶中，仅族长可修）[C3]",
            "炼火焚（黄阶高，萧家最顶尖火功法）",
            "碎石掌（黄阶低）/ 劈山掌（黄阶中）/ 铁山拳（黄阶高）/ 裂爪击（黄阶中，萧家斗技堂）[C1]",
            "狮山裂（玄阶低，配狂狮怒罡，萧战）[C1]",
            "六合游身尺（地阶中，萧厉）[C1]",
            "龙吟碎[游戏核心]",
            "天火三玄变[游戏核心]",
            "八极崩[游戏核心]",
            "五轮离火法[游戏核心]"
        ],
        'special_skills': [
            {"name": "萧族族纹", "rank": "—", "note": "九笔暗红族纹，危急时血脉暴涨 [S8]", "unlock_lv": 20},
            {"name": "天火三玄变", "rank": "秘法", "note": "萧玄所创，四步含族纹修法 [S8][S9]", "unlock_lv": 60}
        ],
        'items': [
            "三枚萧族印信 [G1]",
            "萧家密室 [G1]",
            "陀舍古帝玉·萧族碎片 [S8]"
        ],
        'relics': [
            "萧家后山 / 密室 [G1]",
            "萧界（与古界相接）[S8]"
        ],
        'flames': [
            "萧玄曾拥三种异火，天墓最后一战全数引爆；原著未载颜色、未称『萧族传承』（第一千三百四十二章「激活族纹」萧玄：『我当年也是拥有三种异火……三种异火，皆是被我彻底引爆开来』）[原著]"
        ],
        'starter': {
            "skill": "炼火焚",
            "special": "萧族族纹",
            "item": "萧族印信",
            "relic": "萧家密室"
        },
        'weight': 12,
        'unlock_lv': 1,
        'sources': [
            "S1",
            "S3",
            "S8",
            "S9",
            "G1",
            "C1",
            "C3"
        ],
        'sparse': False,
    },
    "yunlanzong": {
        'name': "云岚宗",
        'tier': "early",
        'pool': "starter",
        'base': "云岚山",
        'npcs': [
            "云破天（创始人）",
            "云山（上任宗主/斗宗）",
            "云韵（宗主/斗皇）",
            "纳兰嫣然（少宗主）",
            "云棱（大长老/斗王）",
            "云督",
            "云刹"
        ],
        'skills': [
            "青木剑诀（玄阶低，云岚宗葛叶所传）[C3]",
            "爆步（玄阶中，云韵所赠）",
            "玄岩盾（云棱）[C1]",
            "风火木壁（三长老合击）[C1]",
            "风灵分形剑（纳兰嫣然）[C1]",
            "飞絮（纳兰嫣然/云韵/加刑天）[C1]",
            "风之极·落日耀（云韵/纳兰嫣然）[C1]",
            "大悲撕风手（云山）[C1]",
            "风刹湮罡（云山）[C1]",
        ],
        'special_skills': [
            {"name": "天道神雷剑诀", "rank": "—", "note": "云棱绝学，[游戏原创] 原著无同名设定 [G1]", "unlock_lv": 40},
            {"name": "万剑归宗", "rank": "—", "note": "宗门合击，[游戏原创] [G1]", "unlock_lv": 20},
            {"name": "风之极·落日耀", "note": "云岚宗秘传风属性斗技，凝风成耀日之芒 [C1]", "rank": "地阶高级", "unlock_lv": 40}
        ],
        'items': [
            "三年之约 [G1]",
            "云岚宗门规 [G1]"
        ],
        'relics': [
            "天刑台 [G1]",
            "云烟覆日阵（护宗大阵）[C2]",
            "护宗大阵（阵眼「执法弟子」）[G1]",
            "云岚宗守护兽 [G1]"
        ],
        'flames': [],
        'starter': {
            "skill": "青木剑诀",
            "special": "万剑归宗",
            "item": "云岚宗门规",
            "relic": "天刑台"
        },
        'weight': 12,
        'unlock_lv': 1,
        'sources': [
            "S1",
            "S3",
            "G1",
            "C1",
            "C2",
            "C3"
        ],
        'sparse': False,
    },
    "lianyao_gonghui": {
        'name': "炼药师公会（加玛）",
        'tier': "early",
        'pool': "starter",
        'base': "黑岩城 / 帝都",
        'npcs': [
            "丹王古河（丹堂堂主）",
            "法犸"
        ],
        # 2026-11-15 梳理（用户「丹会都叫斗技？？？」）：
        # 炼药术/品阶考核/丹会/丹雷引是**宗门典故**，原著里不是可施展的斗技，
        # 不再放进 skills/special_skills（那会让兜底路径把它们当斗技发放）。
        # 斗技池唯一来源是 skill_book；本宗真斗技只有古河的「千焰剑罡」（第六百七十章「战古河」）。
        'skills': [
            "千焰剑罡（古河）[C1]",
        ],
        'lore': [
            "炼药术与丹方 [S4]",
            "炼药师品阶考核 [G1]",
            "丹会（中州丹塔主办、公会参与的炼药大比）[S2]",
            "丹雷引（高阶丹药出炉引动丹雷）[C2]",
        ],
        'special_skills': [],
        'items': [
            "丹方 [G1]",
            "紫云洞药方 [G1]",
            "筑基灵液 / 增气散 / 聚气散 [S7]",
            "丹药品阶表：回春散→三纹青灵丹→斗灵丹→破宗丹→阴阳玄龙丹→菩提大还丹（一品至九品）[C2]",
            "炼药师长袍 / 纳戒 [C2]"
        ],
        'relics': [
            "药鼎（天鼎榜：黑魔鼎8/青炎鼎/火山焰炉/八方明火药鼎/万兽鼎/山熔鼎）[S6][C2]"
        ],
        'flames': [],
        'starter': {
            # starter.skill 必须是真斗技；炼药术/丹会是典故，不能当入道发放的斗技
            "skill": "千焰剑罡",
            "special": "无",
            "item": "丹方",
            "relic": "药鼎"
        },
        'weight': 10,
        'unlock_lv': 1,
        'sources': [
            "S2",
            "S4",
            "S6",
            "S7",
            "G1",
            "C1",
            "C2"
        ],
        'sparse': False,
    },
    "sherenzu": {
        'name': "蛇人族",
        'tier': "early",
        'pool': "starter",
        'base': "塔戈尔大沙漠·蛇人禁地",
        'npcs': [
            "美杜莎/彩鳞（女王）",
            "青鳞（圣女，后入天蛇府）",
            "月媚",
            "墨巴斯",
            "炎刺"
        ],
        'skills': [
            "蛇人族法阵（半蛇人血统方可启动）[G1]",
            "魔蛇噬 / 百魂锁天蟒（蛇人族斗技）[C2]",
        ],
        'special_skills': [
            {"name": "蛇人封印术", "rank": "秘法", "note": "蛇人族女王美杜莎所施，曾封印冰皇海波东斗气 [S4][G1]", "unlock_lv": 40},
            {"name": "七彩吞天蟒进化", "rank": "—", "note": "借异火进化灵魂、脱壳凝新身 [G1]", "unlock_lv": 60}
        ],
        'items': [
            "七彩吞天蟒 [G1]",
            "七彩蛇鳞 [G1]",
            "青莲座 / 地火莲子 [S4]"
        ],
        'relics': [
            "蛇人禁地 [G1]",
            "封印术（曾封印冰皇）[G1]",
            "万蛇大阵 [C2]"
        ],
        'flames': [
            "青莲地心火(19)：美杜莎女王先取走，萧炎后自蛇人族神殿夺回（第二百章「青莲地心火」药老：『原来是有人捷足先登了啊』；第二百零九章「沙漠中心的城市」）[原著]"
        ],
        'starter': {
            "skill": "蛇人族法阵",
            "special": "蛇人封印术",
            "item": "七彩蛇鳞",
            "relic": "蛇人禁地"
        },
        'weight': 10,
        'unlock_lv': 1,
        'sources': [
            "S4",
            "S5",
            "S7",
            "G1",
            "C2"
        ],
        'sparse': False,
    },
    "motie": {
        'name': "漠铁佣兵团 / 石漠城",
        'tier': "early",
        'pool': "starter",
        'base': "石漠城",
        'npcs': [
            "萧鼎（创建者）",
            "萧厉（创建者）",
            "冰皇海波东",
            "青鳞（原侍女）"
        ],
        'skills': [
            "冰皇冰系斗技 [G1]",
            "万花冰镜 / 玄冰旋杀（海波东）[C1]",
            "雷弧三段舞（萧厉）[C1]",
        ],
        'special_skills': [
            {"name": "寒冰屏障", "rank": "—", "note": "[游戏原创] 冰皇拦云山所使 [G1]", "unlock_lv": 40},
            {"name": "地火印", "note": "墨家以地火淬炼机关与战技之印 [C1]", "rank": "玄阶", "unlock_lv": 30}
        ],
        'items': [
            "玄龙丹药方（六品，破封印用）[G1]",
            "素心丹丹方 [G1]",
            "塔戈尔大沙漠地图 / 地图残片 [G1]"
        ],
        'relics': [
            "试炼之塔（九层、守卫 500 年、顶层蛇人月媚、5000 金门票）[G1]"
        ],
        'flames': [],
        'starter': {
            "skill": "冰皇冰系斗技",
            "special": "寒冰屏障",
            "item": "塔戈尔大沙漠地图",
            "relic": "试炼之塔"
        },
        'weight': 10,
        'unlock_lv': 1,
        'sources': [
            "S3",
            "S4",
            "G1",
            "C1"
        ],
        'sparse': False,
    },
    "langtou": {
        'name': "狼头佣兵团 / 万药斋",
        'tier': "early",
        'pool': "starter",
        'base': "魔兽山脉·紫云洞",
        'npcs': [
            "穆蛇",
            "穆力"
        ],
        'skills': [
            "疾风刺 / 凤翔步（穆蛇）[C1]",
            "木之硬化 / 风刃刀舞 / 狂狮吟（穆力）[C1]",
        ],
        'special_skills': [
            {"name": "狼啸崩山", "note": "狼头佣兵团战阵斗技，啸声震碎岩 [游戏原创]", "rank": "黄阶高", "unlock_lv": 20},
            {"name": "狼噬劲", "note": "狼头佣兵团近身噬咬式劲力 [游戏原创]", "rank": "黄阶中", "unlock_lv": 20}
        ],
        'items': [
            "紫云洞药方（两派争夺物）[G1]",
            "狼头佣兵令（佣兵团身份凭证）[游戏原创]"
        ],
        'relics': [
            "紫云洞 [G1]"
        ],
        'flames': [],
        'starter': {
            "skill": "佣兵围猎",
            "special": "—",
            "item": "紫云洞药方",
            "relic": "紫云洞"
        },
        'weight': 6,
        'unlock_lv': 1,
        'sources': [
            "S7",
            "G1",
            "C1"
        ],
        'sparse': True,
    },
    "duzong": {
        'name': "毒宗（出云帝国）",
        'tier': "early",
        'pool': "starter",
        'base': "出云帝国",
        'npcs': [
            "天毒女/小医仙（宗主）",
            "五位斗皇长老"
        ],
        'skills': [
            "毒属性功法（毒素融入斗气）[S3]",
            "七彩毒经 [C2]",
            "毒丹之法 [C2]",
        ],
        'special_skills': [
            {"name": "厄难毒体", "rank": "体质", "note": "天生毒体，[S4]；冰河谷另研有后天厄难毒体 [S9]", "unlock_lv": 60},
            {"name": "蚀骨毒劲", "note": "毒宗秘传，劲力附蚀骨之毒 [S3]", "rank": "地阶", "unlock_lv": 40}
        ],
        'items': [
            "血莲精 [S4]",
            "毒宗毒丹（以毒淬体之丹）[S3]"
        ],
        'relics': [
            "万蝎门（敌对）[S3]"
        ],
        'flames': [],
        'starter': {
            "skill": "毒属性功法",
            "special": "厄难毒体",
            "item": "血莲精",
            "relic": "毒宗令"
        },
        'weight': 9,
        'unlock_lv': 1,
        'sources': [
            "S3",
            "S4",
            "S7",
            "S9",
            "C2"
        ],
        'sparse': False,
    },
    "mulangu": {
        'name': "慕兰谷（慕兰帝国）",
        'tier': "early",
        'pool': "starter",
        'base': "慕兰帝国",
        'npcs': [
            "虎头人（三长老）",
            "狮头人（三长老）",
            "熊头人（三长老）"
        ],
        'skills': [
            "凶熊碎地拳（熊头人）[C1]",
            "狮寸劲（狮头人）[C1]",
        ],
        'special_skills': [
            {"name": "三兽蛮荒决", "rank": "地阶中", "note": "镇派绝学，三长老合击可抗斗宗 [S9]", "unlock_lv": 40},
            {"name": "蛮荒三兽变", "note": "慕兰谷三兽蛮荒决化身之术 [C1]", "rank": "秘法", "unlock_lv": 40}
        ],
        'items': [
            "慕兰谷蛮荒地契（谷地产业凭证）[C1]"
        ],
        'relics': [
            "慕兰谷三长老阵 [S4]"
        ],
        'flames': [],
        'starter': {
            "skill": "三兽蛮荒决·基础",
            "special": "三兽蛮荒决",
            "item": "慕兰谷令",
            "relic": "三长老阵"
        },
        'weight': 7,
        'unlock_lv': 1,
        'sources': [
            "S4",
            "S9",
            "C1"
        ],
        'sparse': True,
    },

    # ------------------------------- 进阶池 -------------------------------
    "jianan": {
        'name': "迦南学院",
        'tier': "mid",
        'pool': "advanced",
        'base': "黑角域",
        'npcs': [
            "邙天尺（创建者/内院院长，雷族长老）",
            "琥乾（外院副院长）",
            "苏千（内院大长老）",
            "千百二老（守护者）",
            "若琳（外院导师）"
        ],
        'skills': [
            "狮虎碎金吟（玄阶高，藏书阁）[S7]",
            "风雷动 / 撼雷地弧爆（白山）[C1]",
            "血裂斩 / 影血闪（吴昊）[C1]",
            "青炫风杀 / 缠蛇手 / 青湮剑罡（林修崖）[C1]",
            "大裂劈棺爪 / 大裂岩 / 大裂山（柳擎）[C1]",
            "幽木毒蛇藤（琥嘉）[C1]",
            "玄土凝旋 / 血地八裂（白程）[C1]",
            "幽鲸水壁 / 湮幻刺（姚盛）[C1]",
            "三鲨刺（谰言）[C1]",
            "卸力水镜 / 水曼陀罗（若琳）[C1]",
        ],
        'special_skills': [
            {"name": "天焚炼气塔淬炼", "rank": "—", "note": "塔下陨落心炎持续淬炼斗气 [S4]", "unlock_lv": 40},
            {"name": "天焚炼气塔秘法", "note": "迦南学院借陨落心炎淬炼战气之秘法 [S9]", "rank": "秘法", "unlock_lv": 45}
        ],
        'items': [
            "地灵丹（六品，收服陨落心炎必备）[S9]",
            "陀舍古帝玉·洞府线索 [S8]"
        ],
        'relics': [
            "天焚炼气塔 [S4]",
            "古帝洞府（塔底）[S8]"
        ],
        'flames': [
            "陨落心炎(14)：迦南学院内院天焚炼气塔下封印，非门派私有（第四百六十三章「修炼加速器」药老：『这迦南院竟然把异火隐藏在这种地方』）[原著]"
        ],
        'starter': {
            "skill": "狮虎碎金吟",
            "special": "天焚炼气塔淬炼",
            "item": "地灵丹",
            "relic": "天焚炼气塔"
        },
        'weight': 7,
        'unlock_lv': 40,
        'sources': [
            "S3",
            "S4",
            "S7",
            "S8",
            "S9",
            "C1"
        ],
        'sparse': False,
    },
    "fengleige": {
        'name': "风雷阁",
        'tier': "mid",
        'pool': "advanced",
        'base': "中州·四方阁",
        'npcs': [
            "雷尊者（阁主，三星斗尊巅峰）",
            "沈云",
            "费天"
        ],
        'skills': [
            "三千雷动（地阶低，镇阁身法：雷闪→雷瞬→三千雷）[S1]",
            "风雷爪 / 千碎雷锤 / 灭雷锤（洪辰）[C1]",
            "风杀指 / 圣像妖凰钟（凤清儿）[C1]",
        ],
        'special_skills': [
            {"name": "三千雷幻身", "rank": "地阶高", "note": "镇阁之宝，本体不死分身不灭 [S1]", "unlock_lv": 60},
            {"name": "风雷动", "note": "风雷阁身法斗技，风雷相激速度暴涨 [C1]", "rank": "地阶", "unlock_lv": 45}
        ],
        'items': [
            "三千雷动残卷 [S3]",
            "净莲妖火残图（曾被血宗范凌拍得）[S3]"
        ],
        'relics': [
            "风雷阁雷坛 [S2]",
            "九天雷狱阵（护阁大阵）[C2]"
        ],
        'flames': [],
        'starter': {
            "skill": "三千雷动",
            "special": "三千雷幻身",
            "item": "三千雷动残卷",
            "relic": "风雷阁雷坛"
        },
        'weight': 7,
        'unlock_lv': 40,
        'sources': [
            "S1",
            "S2",
            "S3",
            "G1",
            "C1",
            "C2"
        ],
        'sparse': False,
    },
    "fenyangu": {
        'name': "焚炎谷",
        'tier': "mid",
        'pool': "advanced",
        'base': "中州·三谷",
        'npcs': [
            "焚炎老祖（创始人，远古五至强）",
            "唐震（谷主）",
            "火云老祖",
            "唐火儿",
            "吴辰",
            "赤火长老"
        ],
        'skills': [
            "青冥幽炎决（传承功法，可用九龙雷罡火）[S9]",
            "纯阳绵手 / 太乙火罗掌 / 天罡碎石步（三长老吴辰）[C1]",
        ],
        'special_skills': [
            {"name": "天火三玄变", "rank": "秘法", "note": "萧玄所创、传焚炎老祖，燃异火暴涨战力 [S3][S9]", "unlock_lv": 60},
            {"name": "红莲业火诀", "note": "焚炎谷借红莲业火锤炼之诀 [S8]", "rank": "地阶高级", "unlock_lv": 50}
        ],
        'items': [
            "火菩丹（七品，与萧炎合炼）[S6]",
            "焚炎谷红莲令（谷主亲传令符）[S8]"
        ],
        'relics': [
            "山熔鼎（天鼎榜 12，唐震所有）[S6]"
        ],
        'flames': [
            "九龙雷罡火(12)：焚炎谷传承之物（第一百五十四章「三千焱炎火」『九龙雷罡火是焚炎谷的传承之物，也算是有主之物』；第一百五十二章「异火消息」）[原著]"
        ],
        'starter': {
            "skill": "青冥幽炎决",
            "special": "天火三玄变",
            "item": "火菩丹",
            "relic": "山熔鼎"
        },
        'weight': 7,
        'unlock_lv': 40,
        'sources': [
            "S2",
            "S3",
            "S5",
            "S6",
            "S9",
            "C1"
        ],
        'sparse': False,
    },
    "binghegu": {
        'name': "冰河谷",
        'tier': "mid",
        'pool': "advanced",
        'base': "中州·三谷",
        'npcs': [
            "冰尊者冰河（谷主）",
            "天霜子（大长老）",
            "天蛇",
            "冰元",
            "冰鹤"
        ],
        'skills': [
            "冻天掌 [S2]",
            "冰蟒臂（冰鹤）[C1]",
        ],
        'special_skills': [
            {"name": "冰尊劲", "rank": "—", "note": "冻结对手经脉 [S9]", "unlock_lv": 40},
            {"name": "后天厄难毒体", "rank": "体质", "note": "谷主冰河豢养研究 [S9]", "unlock_lv": 60},
            {"name": "玄冰诀", "note": "冰河谷秘传冰属性功诀 [C1]", "rank": "地阶", "unlock_lv": 45}
        ],
        'items': [
            "冰河谷玄冰令（长老身份凭证）[C1]"
        ],
        'relics': [
            "冰河谷冰狱 [S2]",
            "冰河阵 [C2]"
        ],
        'flames': [],
        'starter': {
            "skill": "冻天掌",
            "special": "冰尊劲",
            "item": "冰河谷令",
            "relic": "冰河谷冰狱"
        },
        'weight': 6,
        'unlock_lv': 40,
        'sources': [
            "S2",
            "S9",
            "C1",
            "C2"
        ],
        'sparse': False,
    },
    "tianmingzong": {
        'name': "天冥宗",
        'tier': "mid",
        'pool': "advanced",
        'base': "中州·二宗",
        'npcs': [
            "天冥子（宗主）",
            "天冥老妖（太上长老）",
            "易尘（少主）",
            "辰闲",
            "辰天南"
        ],
        'skills': [
            "嗜血化骨手 / 血煞掌（易尘）[C1]",
            "大裂风 / 气化天地 / 邪风斩（妖花邪君）[C1]",
            "九幽冥手（森罗鬼尊）[C1]",
            "大冥龙拳（柳苍）[C1]",
            "天冥掌 / 天冥血掌（天冥老妖）[C1]",
        ],
        'special_skills': [
            {"name": "天冥修罗手", "rank": "地阶高", "note": "镇宗绝学，实际威力可比肩天阶 [S1]", "unlock_lv": 60},
            {"name": "九幽魂手", "note": "魂殿摘星老鬼秘手，凝漆黑鬼爪噬魂碎识 [C1]", "rank": "未知", "unlock_lv": 45}
        ],
        'items': [
            "天冥宗冥令（宗主亲传令符）[C1]"
        ],
        'relics': [
            "天冥宗杀伐阵 [S2]",
            "万魂噬灵阵 / 万魂吞灵阵 [C2]"
        ],
        'flames': [],
        'starter': {
            "skill": "天冥宗基础杀伐术",
            "special": "天冥修罗手",
            "item": "天冥宗令",
            "relic": "天冥宗杀伐阵"
        },
        'weight': 6,
        'unlock_lv': 40,
        'sources': [
            "S1",
            "S2",
            "C1",
            "C2"
        ],
        'sparse': True,
    },
    "huazong": {
        'name': "花宗",
        'tier': "mid",
        'pool': "advanced",
        'base': "中州·二宗",
        'npcs': [
            "云韵（宗主，八星斗尊）",
            "花玉（前任宗主）",
            "青仙子",
            "华仙子",
            "纳兰嫣然（少宗主）"
        ],
        'skills': [
            "花宗传承（只收女弟子）[S2]",
            "裂风炫舞 / 风之极·陨杀 / 风絮残影（宗主云韵）[C1]",
        ],
        'special_skills': [
            {"name": "花宗双太上长老阵", "rank": "—", "note": "中级/高级半圣坐镇 [S2]", "unlock_lv": 60},
            {"name": "花凰诀", "note": "花宗秘传，化花为凰困锁敌人 [C1]", "rank": "地阶", "unlock_lv": 45}
        ],
        'items': [
            "花宗花凰印（宗主信物）[C1]"
        ],
        'relics': [
            "花宗玉牌 [S2]"
        ],
        'flames': [],
        'starter': {
            "skill": "花宗传承",
            "special": "花宗双太上长老阵",
            "item": "花宗玉牌",
            "relic": "花宗玉牌"
        },
        'weight': 6,
        'unlock_lv': 40,
        'sources': [
            "S2",
            "S3",
            "C1"
        ],
        'sparse': True,
    },
    "danta": {
        'name': "丹塔",
        'tier': "mid",
        'pool': "advanced",
        'base': "圣丹城",
        'npcs': [
            "丹塔老祖（九品玄丹所化，六星斗圣）",
            "林老怪（小丹塔大长老）",
            "玄空子",
            "玄衣",
            "天雷子（三巨头）"
        ],
        # 2026-11-15 梳理：丹会/丹雷/大长老院表决/丹会炼术均为炼药界典故，
        # 不是斗技；丹塔本宗可施展的只有「土玄地火盾」（第一千一百四十三章「下杀手」）。
        'skills': [
            "土玄地火盾（宋清）[C1]",
        ],
        'lore': [
            "丹会 / 七色八品丹雷（进小丹塔门槛）[S2]",
            "小丹塔大长老院表决（十位长老投票决定丹塔大事）[S4]",
            "丹会炼术（丹塔丹会夺魁秘传炼药术）[C2]",
        ],
        'special_skills': [],
        'items': [
            "九品玄丹 [S2]",
            "天鼎榜药鼎 [S6]",
            "丹药品阶表（回春散→菩提大还丹，一品至九品）[C2]",
            "药鼎：万兽鼎 / 青炎鼎 / 火山焰炉 / 八方明火药鼎 [C2]"
        ],
        'relics': [
            "星域（独立空间，封印异火）[S4]",
            "小丹塔 [S2]"
        ],
        'flames': [
            "三千焱炎火(9)：丹塔封于星域，但原著明确为无主之物，丹会前十方有资格收取（第一百五十四章「三千焱炎火」『虽然与丹塔有关，可到现在却还是无主之物』）[原著]"
        ],
        'starter': {
            "skill": "土玄地火盾",
            "special": "无",
            "item": "九品玄丹",
            "relic": "星域"
        },
        'weight': 6,
        'unlock_lv': 40,
        'sources': [
            "S2",
            "S4",
            "S5",
            "S6",
            "C1",
            "C2"
        ],
        'sparse': False,
    },
    "xingyunge": {
        'name': "星陨阁",
        'tier': "mid",
        'pool': "advanced",
        'base': "中州·四方阁",
        'npcs': [
            "药圣药尘（阁主）",
            "风尊者风闲（副阁主）",
            "萧炎（少阁主）"
        ],
        'skills': [
            "药尘炼药术（八品巅峰炼药宗师）[S4]",
            "吸掌 / 八极崩 / 吹火掌 / 焰分噬浪尺（药尘）[C1]",
        ],
        'special_skills': [
            {"name": "星陨阁大阵", "rank": "—", "note": "四方阁护阁阵 [S4]", "unlock_lv": 60},
            {"name": "星陨诀", "note": "星陨阁传承功诀，引星力淬体 [C1]", "rank": "地阶", "unlock_lv": 45}
        ],
        'items': [
            "帝品丹方 [G1]",
            "真阳丹（帝品）[G1]"
        ],
        'relics': [
            "黑魔鼎（天鼎榜 8，药尘所有）[S6]",
            "纳戒 [G1]"
        ],
        'flames': [
            "骨灵冷火(11)：药尘本命异火（第六十三章「异火榜」药老：『我这火种，便是排名第十一位的骨灵冷火』）[原著]"
        ],
        'starter': {
            "skill": "药尘炼药术",
            "special": "星陨阁大阵",
            "item": "帝品丹方",
            "relic": "黑魔鼎"
        },
        'weight': 5,
        'unlock_lv': 40,
        'sources': [
            "S2",
            "S4",
            "S5",
            "S6",
            "G1",
            "C1"
        ],
        'sparse': False,
    },
    "hundian": {
        'name': "魂殿 / 魂族",
        'tier': "mid",
        'pool': "advanced",
        'base': "中州·一殿",
        'npcs': [
            "魂灭生（殿主）",
            "副殿主",
            "大天尊",
            "骨幽圣者",
            "血河尊者",
            "摘星老鬼",
            "慕骨老人",
            "鹜护法",
            "铁护法"
        ],
        'skills': [
            "摄魂 / 控魂 / 隐身 [G1]",
            "九幽魂手 / 森罗大星手 / 幻魂 / 碎星斩（摘星老鬼）[C1]",
            "血煞天魔掌 / 血魔袈裟（四天尊）[C1]",
            "黑色骷髅（骨幽圣者）/ 幽冥妖火臂（慕骨老人）/ 光耀印（韩枫）[C1]",
        ],
        'special_skills': [
            {"name": "灵魂体搜捕", "rank": "—", "note": "满大陆搜捕灵魂体、控制对手 [G1][S2]", "unlock_lv": 60},
            {"name": "摄魂冥手", "note": "魂殿摄魂秘手，拘敌魂魄 [C1]", "rank": "地阶", "unlock_lv": 45}
        ],
        'items': [
            "陀舍古帝玉（集齐八块）[S8]",
            "海心焰（韩枫→慕骨老人）[S5]"
        ],
        'relics': [
            "天罡殿/地煞殿/分殿 [S2]",
            "噬魂大殿 / 摄魂台 / 幽都 [G1]",
            "噬灵绝生阵 / 死亡之界大阵 / 斩帝阵 [C2]"
        ],
        'flames': [
            "虚无吞炎(2)【仅子火】：魂殿殿主所持本源分化之子火，本体在魂族（第一千五百一十四章「小伊显威」『这东西是虚无吞炎的子火不成』；第一千五百一十五章「分出胜负」）[原著]"
        ],
        'starter': {
            "skill": "摄魂术",
            "special": "灵魂体搜捕",
            "item": "魂殿令",
            "relic": "噬魂大殿"
        },
        'weight': 4,
        'unlock_lv': 40,
        'sources': [
            "S2",
            "S5",
            "S8",
            "G1",
            "C1",
            "C2"
        ],
        'sparse': False,
    },
    "moyangu": {
        'name': "魔炎谷（黑角域）",
        'tier': "mid",
        'pool': "advanced",
        'base': "黑角域",
        'npcs': [
            "地魔老鬼（创始人，七星斗宗）",
            "方言（大长老）",
            "鹰爪老人（四长老）"
        ],
        'skills': [
            "弄焰诀（地魔老鬼改良，三兽火融合化生火）[S3][S9]",
            "千幻玄冰刺 / 冰凝剑诀（地魔老鬼）[C1]",
            "真·弄焰诀 / 五轮离火法 / 惊蛰雷法 / 奔雷鉴（萧厉）[C1]",
        ],
        'special_skills': [
            {"name": "天妖傀炼制秘法", "rank": "秘法", "note": "可制造升级型战斗傀儡 [S3][S9]", "unlock_lv": 60},
            {"name": "魔炎变", "note": "魔炎谷以魔炎化体增幅之术 [S3]", "rank": "秘法", "unlock_lv": 45}
        ],
        'items': [
            "菩提化体涎（拍卖争夺）[S3]",
            "魔炎谷魔核（魔炎淬炼之核）[S3]"
        ],
        'relics': [
            "天妖傀（地妖傀→天妖傀→北王）[S9]"
        ],
        'flames': [
            "化生火：弄焰决融合出的假异火，非异火榜正火（第一百四十一章「收服」『他们想要使用这种假异火』；第一百四十章「化生火」）[原著]"
        ],
        'starter': {
            "skill": "弄焰诀",
            "special": "天妖傀炼制秘法",
            "item": "菩提化体涎",
            "relic": "天妖傀"
        },
        'weight': 5,
        'unlock_lv': 40,
        'sources': [
            "S3",
            "S7",
            "S9",
            "C1"
        ],
        'sparse': False,
    },
    "xuezong": {
        'name': "血宗（黑角域）",
        'tier': "mid",
        'pool': "advanced",
        'base': "黑角域",
        'npcs': [
            "范痨（宗主，黑榜第五）",
            "范凌（少宗主）"
        ],
        'skills': [
            "阴寒属性功法（被异火所制）[S4]",
            "嗜血印 / 大血菩噬（范痨）[C1]",
            "雷蝠天翼（范凌）[C1]",
        ],
        'special_skills': [
            {"name": "噬血大法", "note": "血宗以血淬力之秘法 [C1]", "rank": "地阶", "unlock_lv": 45},
            {"name": "血魔手", "note": "血宗范痨凝血成巨手之技，威能浩大 [C1]", "rank": "未知", "unlock_lv": 30}
        ],
        'items': [
            "阴阳玄龙丹（抢自天蛇府）[S3]",
            "三千雷动 [S3]",
            "净莲妖火残图 [S3]"
        ],
        'relics': [
            "血宗残破地图 [S4]"
        ],
        'flames': [],
        'starter': {
            "skill": "阴寒功法",
            "special": "—",
            "item": "阴阳玄龙丹",
            "relic": "血宗残破地图"
        },
        'weight': 4,
        'unlock_lv': 40,
        'sources': [
            "S3",
            "S4",
            "C1"
        ],
        'sparse': True,
    },

    # ------------------------------- 隐藏池 -------------------------------
    "guzu": {
        'name': "古族",
        'tier': "late",
        'pool': "hidden",
        'base': "古界",
        'npcs': [
            "古元（族长，九星斗圣巅峰）",
            "萧薰儿（古族大小姐）",
            "黑湮军"
        ],
        'skills': [
            "古圣宝鉴（天阶低，含吞天纳地）[S1]",
            "龙蛇步法 / 天煞碎气爪（黑湮军杨皓）[C1]",
            "万兽枪法（黑湮军古妖）[C1]",
            "燕返击 / 大寂灭术（萧薰儿）[C1]",
        ],
        'special_skills': [
            {"name": "帝印决", "rank": "地阶高", "note": "镇族绝学，开山/翻海/覆地/湮天/古帝五印 [S1]", "unlock_lv": 60},
            {"name": "大寂灭指", "rank": "天阶低", "note": "古族秘传，指劲所过空间崩裂 [S1]", "unlock_lv": 80}
        ],
        'items': [
            "陀舍古帝玉·古族碎片 [S8]",
            "古界 [S8]"
        ],
        'relics': [
            "护族大阵 [S9]",
            "古族大殿 [G1]",
            "古帝镜 / 古帝碎涅指 / 斩帝阵 / 黑湮军 [C1][C2]"
        ],
        'flames': [
            "金帝焚天炎(4)：古族传承之火，萧薰儿降服（第一千四百零六章「二天尊，骨幽圣者」骨幽：『金帝焚天炎，异火榜排名第四，古族传承之火』）[原著]"
        ],
        'starter': {
            "skill": "古圣宝鉴",
            "special": "帝印决",
            "item": "古族玉佩",
            "relic": "古界"
        },
        'weight': 2,
        'unlock_lv': 90,
        'sources': [
            "S1",
            "S4",
            "S5",
            "S8",
            "S9",
            "G1",
            "C1",
            "C2"
        ],
        'sparse': False,
    },
    "yanzu": {
        'name': "炎族",
        'tier': "late",
        'pool': "hidden",
        'base': "炎界",
        'npcs': [
            "炎烬（族长）",
            "火稚",
            "火炫",
            "火耀"
        ],
        'skills': [
            "五火传承 [S5]",
            "陨火玄指（炎烬）[C1]",
        ],
        'special_skills': [
            {"name": "护族大阵", "rank": "—", "note": "可召斗帝先祖残魂 [S9]", "unlock_lv": 90},
            {"name": "炎神降", "note": "炎族借火山石焰降世之术 [S8]", "rank": "地阶高级", "unlock_lv": 55}
        ],
        'items': [
            "陀舍古帝玉·炎族碎片 [S8]",
            "炎族火灵珠（火属本源珠）[S8]"
        ],
        'relics': [
            "炎界 [S4]"
        ],
        'flames': [
            "荒破灭焱(6)：炎族火炫降服；二手资料常误加前缀，原著正文只作『荒破灭焱』（第一千四百七十四章「荒破灭焱」火炫：『侥幸降服了族中的荒破灭焱』）[原著]",
            "九幽金祖火(7)、火山石焰(17)：炎族族长炎烬体内两种异火（第一千五百六十三章「雷动」薰儿：『在他的体内，拥有着两种异火……火山石焰……九幽金祖火』）[原著]",
            "红莲业火(8)：炎族火稚持有（第一千三百二十九章「收取报酬」『炎族的那位女子，名为火稚，拥有着……红莲业火』）[原著]",
            "火云水炎(16)：炎族火耀长老持有（第一千四百七十四章「荒破灭焱」火炫：『这位是我炎族的火耀长老……排名十六的火云水炎』）[原著]"
        ],
        'starter': {
            "skill": "五火传承",
            "special": "护族大阵",
            "item": "炎族火令",
            "relic": "炎界"
        },
        'weight': 2,
        'unlock_lv': 90,
        'sources': [
            "S4",
            "S5",
            "S8",
            "S9",
            "C1"
        ],
        'sparse': False,
    },
    "hunzu": {
        'name': "魂族",
        'tier': "late",
        'pool': "hidden",
        'base': "魂界",
        'npcs': [
            "魂天帝（族长，后成斗帝）",
            "魂虚子（首席炼药师）"
        ],
        'skills': [
            "吞噬血脉延续 [S4]",
            "裂风虎啸掌 / 黑极崩劲 / 吞天镜（魂风）[C1]",
            "灭魂掌（魂玉）[C1]",
            "枯死指（魂焱）/ 死神之指（魂生天）/ 死亡之碑（魂元天）[C1]",
            "碎魂冥掌（魂厉）[C1]",
        ],
        'special_skills': [
            {"name": "虚无吞炎", "rank": "异火", "note": "榜 2 本体，魂族吞族之战依仗 [S5]", "unlock_lv": 90},
            {"name": "化血大法", "note": "魂殿四天尊秘法，控血吸能，施展后一月内濒死 [C1]", "rank": "天阶", "unlock_lv": 60}
        ],
        'items': [
            "陀舍古帝玉（集齐八块）[S8]",
            "帝品雏丹 [S8]"
        ],
        'relics': [
            "魂殿（明面势力）[S4]",
            "葬天山脉 [S4]"
        ],
        'flames': [
            "虚无吞炎(2)【本体】：魂族所有（第一千五百一十章「传信」『排名第二的虚无吞炎恐怕落在魂族手中』；第一千五百四十四章「生灵之焱」神农老人：『没想到，此等异火，竟然落在了魂族手中』）[原著]"
        ],
        'starter': {
            "skill": "吞噬血脉术",
            "special": "虚无吞炎",
            "item": "陀舍古帝玉",
            "relic": "魂殿"
        },
        'weight': 1,
        'unlock_lv': 90,
        'sources': [
            "S4",
            "S5",
            "S8",
            "C1"
        ],
        'sparse': False,
    },
    "taixugulong": {
        'name': "太虚古龙族",
        'tier': "late",
        'pool': "hidden",
        'base': "古龙岛",
        'npcs': [
            "紫研（族长）",
            "烛坤（老族长）",
            "四大龙岛龙王"
        ],
        'skills': [
            "屠龙剑 / 龙皇钟（紫研）[C1]",
            "骨皇裂天（太虚古龙斗技）[C1]",
        ],
        'special_skills': [
            {"name": "龙凰古甲", "rank": "防身技", "note": "紫研注入龙凰古血，古龙专属 [S7]", "unlock_lv": 90},
            {"name": "龙凰古甲", "note": "太虚古龙族体术，凝龙凰之甲 [S8]", "rank": "地阶高级", "unlock_lv": 55}
        ],
        'items': [
            "龙凰本源果 [S9]",
            "古龙龙鳞（龙族护甲材料）[S8]"
        ],
        'relics': [
            "龙魂鼎（天鼎榜 4，龙族斗帝所造）[S6]",
            "古龙岛 [G1]"
        ],
        'flames': [],
        'starter': {
            "skill": "古龙肉身术",
            "special": "龙凰古甲",
            "item": "龙凰本源果",
            "relic": "古龙岛"
        },
        'weight': 2,
        'unlock_lv': 90,
        'sources': [
            "S4",
            "S6",
            "S7",
            "S9",
            "G1",
            "C1"
        ],
        'sparse': False,
    },
    "jiuyou": {
        'name': "九幽地冥蟒族",
        'tier': "late",
        'pool': "hidden",
        'base': "九幽地冥蟒族地",
        'npcs': [
            "妖暝（族长）",
            "黄泉妖圣"
        ],
        'skills': [
            "黄泉指（天阶低）[S1]",
            "枯叶大毒掌 / 幽鬼毒煞爪（大长老）[C1]",
            "黄泉大印（妖啸天）[C1]",
        ],
        'special_skills': [
            {"name": "黄泉掌", "rank": "天阶中", "note": "黄泉三绝之一 [S1]", "unlock_lv": 80},
            {"name": "黄泉天怒", "rank": "天阶高", "note": "黄泉妖圣最强杀招，灵魂攻击 [S1]", "unlock_lv": 90},
            {"name": "黄泉指", "note": "九幽地冥蟒一族黄泉指力 [S8]", "rank": "地阶高级", "unlock_lv": 55}
        ],
        'items': [
            "黄泉石碑 [S9]",
            "黄泉血晶 / 九阴黄泉丹 [C2]"
        ],
        'relics': [
            "黄泉妖圣传承地 [S9]"
        ],
        'flames': [],
        'starter': {
            "skill": "黄泉指",
            "special": "黄泉掌",
            "item": "黄泉石碑",
            "relic": "黄泉妖圣传承地"
        },
        'weight': 2,
        'unlock_lv': 90,
        'sources': [
            "S1",
            "S4",
            "S9",
            "C1",
            "C2"
        ],
        'sparse': False,
    },
    "tianfu": {
        'name': "天府联盟",
        'tier': "late",
        'pool': "hidden",
        'base': "中州",
        'npcs': [
            "药尘（盟主）",
            "萧炎（少盟主）",
            "丹塔老祖",
            "萧晨",
            "神农老人",
            "火云老祖",
            "青仙子",
            "小医仙",
            "青鳞"
        ],
        'skills': [
            "四派功法合流（星陨阁+丹塔+花宗+焚炎谷）[S3][S8]",
            "赤毒掌（神农老人）[C1]",
        ],
        'special_skills': [
            {"name": "联盟大阵", "rank": "—", "note": "为对抗魂殿而结盟 [S3]", "unlock_lv": 90},
            {"name": "天盟卫阵", "note": "天府联盟联军守护战阵（游戏自设）[游戏原创]", "rank": "秘法", "unlock_lv": 55}
        ],
        'items': [
            "天府联盟盟主令（联盟最高令符）[游戏原创]"
        ],
        'relics': [
            "天府联盟总坛 [S3]"
        ],
        'flames': [],
        'starter': {
            "skill": "四派合流功法",
            "special": "联盟大阵",
            "item": "天府盟令",
            "relic": "天府联盟总坛"
        },
        'weight': 1,
        'unlock_lv': 90,
        'sources': [
            "S3",
            "S8",
            "C1"
        ],
        'sparse': True,
    },
}


# ---------------------------------------------------------------------------
# 宗门资料净化（2026-11-15「丹会都叫斗技？？？重新梳理一遍」）
# ---------------------------------------------------------------------------
#: 与 skill_book.NON_SKILL_NAMES 完全一致的两份清单由
#: ``tools/check_skill_catalog.py`` 门禁校验对齐，防止一边改一边漂移。
LORE_NAMES = frozenset({
    "炼药术与丹方", "炼药师品阶考核", "丹会", "丹雷引", "毒丹之法",
    "七彩毒经", "毒属性功法", "七色八品丹雷", "小丹塔大长老院表决",
    "丹会炼术", "药尘炼药术", "天妖傀炼制秘法",
    "花宗传承", "花宗双太上长老阵", "五火传承", "四派功法合流",
    "吞噬血脉延续", "七彩吞天蟒进化", "灵魂体搜捕",
    "天焚炼气塔淬炼", "天焚炼气塔秘法",
    "萧族族纹", "厄难毒体", "后天厄难毒体", "蛇人族法阵", "蛇人封印术",
    "星陨阁大阵", "护族大阵", "联盟大阵", "天盟卫阵", "虚无吞炎",
    "冰皇冰系斗技", "寒冰屏障", "阴寒属性功法", "隐身",
})


def _split_faction_lore() -> None:
    """把「典故」从 skills / special_skills 挪进 lore，运行时视图里不再有假斗技。

    资料**不删**：原文（含来源标注 [S2]/[C1]）整条进 ``lore``，宗门卡 / 文档仍可引用。
    这样不用手改 25 派的字面量，也不会出现「某派漏改又把丹会放回斗技池」。
    """
    for f in FACTIONS.values():
        lore = list(f.get("lore", ()))
        skills, special = [], []
        for s in f.get("skills", ()):
            (lore if any(n in str(s) for n in LORE_NAMES) else skills).append(s)
        for sp in f.get("special_skills", ()):
            nm = str(sp.get("name") or "")
            if any(n in nm for n in LORE_NAMES):
                lore.append("%s：%s" % (nm, sp.get("note", "")))
            else:
                special.append(sp)
        f["skills"] = skills
        f["special_skills"] = special
        f["lore"] = lore


_split_faction_lore()


# ---------------------------------------------------------------------------
# 纯函数（不读写文件、不 import 运行时）
# ---------------------------------------------------------------------------
def all_factions() -> dict[str, dict]:
    return FACTIONS


def get(fid: str) -> dict | None:
    return FACTIONS.get(fid)


def by_tier(tier: str) -> dict[str, dict]:
    return {k: v for k, v in FACTIONS.items() if v["tier"] == tier}


def by_pool(pool: str, lv: int | None = None) -> dict[str, dict]:
    out = {k: v for k, v in FACTIONS.items() if v["pool"] == pool}
    if lv is not None:
        out = {k: v for k, v in out.items() if int(v["unlock_lv"]) <= int(lv)}
    return out


def random_faction(pool: str = "starter", lv: int | None = None, rng=None) -> str | None:
    """按 weight 抽一个宗门 id；rng 可注入（测试用 lambda: 0.0 / 1.0）。"""
    r = rng or random.random
    pool_map = by_pool(pool, lv if lv is not None else POOL_UNLOCK.get(pool, 1))
    if not pool_map:
        return None
    ids = list(pool_map)
    weights = [max(0, int(pool_map[i]["weight"])) for i in ids]
    total = sum(weights)
    if total <= 0:
        return None
    pick = r() * total
    acc = 0
    for fid, w in zip(ids, weights):
        acc += w
        if pick < acc:
            return fid
    return ids[-1]


def starter_kit(fid: str) -> dict:
    """入道发放的 4 项（普通斗技 / 特殊斗技 / 特殊物品 / 特殊器物）。"""
    f = FACTIONS.get(fid)
    if not f:
        return {}
    kit = dict(f["starter"])
    kit["faction"] = fid
    kit["faction_name"] = f["name"]
    return kit


def assign_faction(p: dict, rng=None, pool: str = "starter", lv: int | None = None) -> dict:
    """把宗门写进 player dict（**只增字段，不改既有字段、不动 schema**）。"""
    level = int(lv if lv is not None else p.get("lv", 1))
    fid = random_faction(pool=pool, lv=level, rng=rng)
    if not fid:
        return {}
    kit = starter_kit(fid)
    p.update(kit)
    return kit


def faction_of(p: dict) -> dict | None:
    """读玩家当前宗门；未分配返回 None。"""
    return FACTIONS.get(str(p.get("faction") or ""))


def special_skills_at(fid: str, lv: int) -> list[dict]:
    """按等级返回已解锁的特殊斗技。"""
    f = FACTIONS.get(fid)
    if not f:
        return []
    return [s for s in f["special_skills"] if int(lv) >= int(s["unlock_lv"])]


def validate() -> list[str]:
    """数据自检：返回问题清单，空列表 = 数据完整（供 test_factions.py 断言）。"""
    problems: list[str] = []
    required = ("name", "tier", "pool", "base", "npcs", "skills", "special_skills",
                "items", "relics", "flames", "starter", "weight", "unlock_lv", "sources")
    for fid, f in FACTIONS.items():
        for k in required:
            if k not in f:
                problems.append("%s 缺字段 %s" % (fid, k))
        if f.get("tier") not in TIERS:
            problems.append("%s tier 非法" % fid)
        if f.get("pool") not in POOLS:
            problems.append("%s pool 非法" % fid)
        if not f.get("npcs"):
            problems.append("%s 无代表人物" % fid)
        if int(f.get("weight") or 0) <= 0:
            problems.append("%s 权重非正" % fid)
        if not f.get("sources"):
            problems.append("%s 无来源标注" % fid)
        kit = f.get("starter") or {}
        for k in ("skill", "special", "item", "relic"):
            if not kit.get(k):
                problems.append("%s starter.%s 为空" % (fid, k))
        for s in f.get("special_skills") or []:
            if not s.get("name") or "unlock_lv" not in s:
                problems.append("%s 特殊斗技字段不全" % fid)
    return problems
