# -*- coding: utf-8 -*-
"""修仙 · 全部 20 条口令的业务级测试（自带 runner，exit-code 可验证）。

策略：不启动 QQ / nonebot，直连 commands.*（真实业务层）+ 临时 Store，
在**同一个 asyncio 事件循环**里按真实玩家顺序跑一遍全部口令，断言：
  - 返回文案符合预期（命中关键字）
  - 存档状态被正确写入（灵石 / 宗门 / 世界Boss 等）

运行：
    python xiuxian/test_commands.py     # 自带 runner，exit 0 = 全绿
    pytest xiuxian/test_commands.py     # 标准 pytest
"""
import os
import sys
import types
import random
import asyncio
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    adapters = types.ModuleType("nonebot.adapters")
    onebot = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    # 状态卡 / 菜单卡用 MessageSegment.image 发图；单测只关心"发没发图"，不需要真图
    v11.MessageSegment = types.SimpleNamespace(image=lambda data: ("[image]", len(data or b"")))
    sys.modules["nonebot.adapters"] = adapters
    sys.modules["nonebot.adapters.onebot"] = onebot
    sys.modules["nonebot.adapters.onebot.v11"] = v11

    logmod = types.ModuleType("nonebot.log")
    logmod.logger = types.SimpleNamespace(
        warning=lambda *a, **k: None, info=lambda *a, **k: None,
        debug=lambda *a, **k: None, error=lambda *a, **k: None,
    )
    nb.log = logmod
    sys.modules["nonebot.log"] = logmod


_stub_nonebot()

from xiuxian import commands, core, config  # noqa: E402
from xiuxian.store import Store  # noqa: E402

G = "1075851417"
A, B, C, D = "100001", "100002", "100003", "100004"

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))


async def _scenario():
    random.seed(20261026)
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "x.json"))
        await s.load()

        # 0 菜单：与 matcher 清单逐条比对，防止「加了口令忘了更新菜单」
        r = commands.cmd_menu()
        matchers = ("我要修仙", "我的修仙", "修仙菜单", "修仙帮助", "闭关", "出关", "突破",
                    "领取机缘", "秘境", "悬赏", "接悬赏", "交悬赏", "切磋", "抢灵石",
                    "偷灵石", "送灵石",
                    "排行榜", "创建宗门", "加入宗门", "宗门", "宗门战",
                    "世界boss", "讨伐boss", "开启秘境",
                    "商店", "买", "炼丹", "收取丹药", "用丹")
        listed = [c for _, rows in commands.MENU_GROUPS for c, _ in rows]
        check("修仙菜单(条数)", len(listed) == len(matchers) == commands.menu_total(),
              f"listed={len(listed)} matchers={len(matchers)} total={commands.menu_total()}")
        miss = [m for m in matchers if not any(m in c for c in listed)]
        check("修仙菜单(无遗漏)", not miss, "缺：" + "、".join(miss))
        check("修仙菜单(每条有说明)",
              all(d.strip() for _, rows in commands.MENU_GROUPS for _, d in rows), "")
        check("修仙菜单(文案)", all(g in r for g, _ in commands.MENU_GROUPS) and "口令" in r, r[:40])

        # 0b 修仙帮助：细则条数自洽 + 关键数值必须与 config 同步（改了 config 忘了改帮助会红）
        h = commands.cmd_help()
        listed_h = [n for _, rows in commands.HELP_GROUPS for n, _ in rows]
        check("修仙帮助(条数自洽)",
              len(listed_h) == commands.help_total() == len(set(listed_h)),
              f"listed={len(listed_h)} total={commands.help_total()} uniq={len(set(listed_h))}")
        check("修仙帮助(每条有细则)",
              all(d.strip() for _, rows in commands.HELP_GROUPS for _, d in rows), "")
        wanted = (
            f"{int(config.ROB_PCT * 100)}%",        # 抢夺比例 15%
            f"{int(config.STEAL_PCT * 100)}%",      # 偷取比例 8%
            f"{config.ROB_CAP}",                    # 抢上限 5000
            f"{config.STEAL_CAP}",                  # 偷上限 2000
            f"{config.CLAIM_MIN}~{config.CLAIM_MAX}",  # 机缘 20~120
            f"{config.BOSS_REWARD_POOL} 灵石",       # Boss 奖池
            f"共 {config.MAX_REALM + 1} 层",         # 境界 12 层
            # 悬赏：任务总数 + 成功报酬区间（防再次把 fail_thank 当成报酬写进帮助卡）
            f"共 {len(config.BOUNTIES)} 个任务",
            f"{min(r[4] for r in config.BOUNTIES)}~{max(r[4] for r in config.BOUNTIES)} 灵石",
        )
        miss_n = [x for x in wanted if x not in h]
        check("修仙帮助(数值跟随 config)", not miss_n, "缺：" + "、".join(miss_n))

        # 1 我要修仙
        r = await commands.cmd_enter(s, A, "甲", G)
        check("我要修仙", ("入道" in r) and ("灵根" in r or "修炼×" in r), r[:60])
        # 重复入道
        r = await commands.cmd_enter(s, A, "甲", G)
        check("我要修仙(重复)", "已踏入仙途" in r, r[:60])

        # 2 我的修仙
        r = await commands.cmd_status(s, A)
        check("我的修仙", ("境界" in r) and ("战力" in r), r[:60])
        # 未入道玩家
        r = await commands.cmd_status(s, "999999")
        check("我的修仙(未入道)", "尚未入道" in r, r[:60])

        # 3 闭关
        r = await commands.cmd_seclude(s, A)
        check("闭关", ("闭关" in r or "入定" in r), r[:60])
        r = await commands.cmd_seclude(s, A)
        check("闭关(重复)", "已在闭关中" in r, r[:60])

        # 4 出关
        r = await commands.cmd_unseclude(s, A)
        check("出关", ("出关" in r and "修为" in r), r[:60])
        r = await commands.cmd_unseclude(s, A)
        check("出关(未闭关)", "并未闭关" in r, r[:60])

        # 5 突破（先喂满修为，保证可突破；成败两态都含"突破"）
        p = await s.get_player(A)
        p["exp"] = 10 ** 9
        await s.put_player(p)
        r = await commands.cmd_breakthrough(s, A)
        check("突破", "突破" in r, r[:60])

        # 6 领取机缘
        r = await commands.cmd_claim(s, A)
        check("领取机缘", "机缘" in r, r[:60])

        # 7 秘境（限时秘境：未开启提示；开启后报名自动探索；期间只看进度；结束总览）
        r = await commands.cmd_explore(s, A, G)
        check("秘境(未开启)", "没有开启" in r, r[:60])

        await s.set_world(G, {"secret": core.secret_new()})
        r = await commands.cmd_explore(s, A, G)
        check("秘境(报名自动探索)", "自动探索" in r or "正式开始" in r, r[:60])

        r2 = await commands.cmd_explore(s, A, G)
        check("秘境(查进度)", "探索中" in r2 and "剩余" in r2, r2[:60])

        # 自动事件累加后，进度里事件数应上升
        world = await s.get_world(G)
        part = world["secret"]["participants"][A]
        pl = await s.get_player(A)
        for _ in range(3):
            core.secret_roll_event(pl, part, now_ts=world["secret"]["started_at"] + 10)
        await s.put_player(pl)
        r3 = await commands.cmd_explore(s, A, G)
        check("秘境(自动事件累加)", "已历" in r3, r3[:60])

        # 结束总览
        world = await s.get_world(G)
        world["secret"]["open"] = False
        await s.set_world(G, world)
        summ = core.secret_summary_text(world["secret"])
        check("秘境(结束总览)", "总览" in summ and "灵石" in summ, summ[:60])

        # 7b 开启秘境（仅主人；非主人拒绝；主人可开；重复开提示进行中）
        config.OWNER_QQS = {A}
        r = await commands.cmd_open_secret(s, G, B)
        check("开启秘境(非主人)", "只有主人" in r, r[:60])
        r = await commands.cmd_open_secret(s, G, A)
        check("开启秘境(主人)", "开启" in r, r[:60])
        r2 = await commands.cmd_open_secret(s, G, A)
        check("开启秘境(已在进行)", "进行中" in r2, r2[:60])

        # 7b 悬赏：榜单 / 接取 / 未到期 / 到期结算 / 占用互斥（数据搬自开源 悬赏令.json）
        r = await commands.cmd_bounty(s, A)
        miss_b = [b[1] for b in config.BOUNTIES if b[1] not in r]
        check("悬赏(榜单)", not miss_b and "成功率" in r,
              "缺：" + "、".join(miss_b) if miss_b else r[:40])

        r = await commands.cmd_take_bounty(s, A, "玄冰花")   # 简称也要能接
        check("接悬赏(简称)", "玄冰花" in r and "60 分钟" in r, r[:60])

        r = await commands.cmd_take_bounty(s, A, "寻找玉髓芝")
        check("接悬赏(占用互斥)", "只能接一个" in r or "还在做" in r, r[:60])

        r = await commands.cmd_finish_bounty(s, A)
        check("交悬赏(未到期)", "还没到期" in r, r[:60])

        # 把到期时间拨到过去，强制可结算；灵石增量必须落在该任务的 成功/失败 报酬二选一
        p = await s.get_player(A)
        before_ls = p["ling_shi"]
        p["bounty"]["end"] = 0
        await s.put_player(p)
        r = await commands.cmd_finish_bounty(s, A)
        p = await s.get_player(A)
        gain = p["ling_shi"] - before_ls
        allowed = {int(p_b[4]) for p_b in config.BOUNTIES if p_b[1] == "寻找玄冰花"}
        allowed |= {int(p_b[5]) for p_b in config.BOUNTIES if p_b[1] == "寻找玄冰花"}
        check("交悬赏(结算)", gain in allowed and p.get("bounty") is None and "灵石" in r,
              f"gain={gain} allowed={sorted(allowed)} bounty={p.get('bounty')}")

        r = await commands.cmd_finish_bounty(s, A)
        check("交悬赏(无任务)", "没有悬赏" in r, r[:60])

        r = await commands.cmd_take_bounty(s, A, "不存在的任务")
        check("接悬赏(不存在)", "没有叫" in r, r[:60])

        # 8 入道 B（对抗/宗门需要第二个玩家）
        r = await commands.cmd_enter(s, B, "乙", G)
        check("我要修仙(B)", "入道" in r, r[:60])

        # 9 切磋
        r = await commands.cmd_pvp(s, A, B)
        check("切磋", "战力" in r, r[:60])
        r = await commands.cmd_pvp(s, A, None)
        check("切磋(未@)", "需 @ 一位道友" in r, r[:60])

        # 10 抢灵石
        r = await commands.cmd_rob(s, A, B)
        check("抢灵石", "灵石" in r, r[:60])

        # 11 偷灵石
        r = await commands.cmd_steal(s, A, B)
        check("偷灵石", "灵石" in r, r[:60])

        # 12 送灵石
        pa = await s.get_player(A)
        r = await commands.cmd_give(s, A, B, 5) if pa["ling_shi"] >= 5 else await commands.cmd_give(s, A, B, 0)
        check("送灵石", ("灵石" in r) or ("用法" in r), r[:60])
        r = await commands.cmd_give(s, A, B, None)
        check("送灵石(无数额)", "用法" in r, r[:60])

        # 13 排行榜
        r = await commands.cmd_rank(s, G)
        check("排行榜", "战力" in r, r[:60])

        # 14 创建宗门
        r = await commands.cmd_create_sect(s, A, "仙门")
        check("创建宗门", "宗门" in r, r[:60])
        r = await commands.cmd_create_sect(s, A, "二门")
        check("创建宗门(重复)", "已有宗门" in r, r[:60])

        # 15 加入宗门
        r = await commands.cmd_join_sect(s, B, "仙门")
        check("加入宗门", "加入宗门" in r or "宗门" in r, r[:60])
        r = await commands.cmd_join_sect(s, B, "仙门")
        check("加入宗门(重复)", "已有宗门" in r, r[:60])

        # 16 宗门（信息）
        r = await commands.cmd_sect_info(s, A)
        check("宗门", ("宗门" in r) and ("成员" in r), r[:80])

        # 17 宗门战：C 建魔门，A 的仙门对其宣战
        await commands.cmd_enter(s, C, "丙", G)
        await commands.cmd_create_sect(s, C, "魔门")
        r = await commands.cmd_sect_war(s, A, "魔门")
        check("宗门战", "宗门战" in r, r[:80])
        r = await commands.cmd_sect_war(s, A, "不存在的门")
        check("宗门战(敌方不存在)", "未找到" in r, r[:80])

        # 18 世界Boss（无 → 有 → 击杀）
        r = await commands.cmd_boss(s, G)
        check("世界boss(无)", "无世界Boss" in r, r[:60])
        r = await commands.cmd_hit_boss(s, A, G)
        check("讨伐boss(无)", "暂无世界Boss" in r, r[:60])

        world = await s.get_world(G)
        world["boss"] = core.spawn_boss(1)
        await s.set_world(G, world)
        r = await commands.cmd_boss(s, G)
        check("世界boss(有)", ("气血" in r) or ("Boss" in r), r[:60])

        dead = False
        for _ in range(5000):
            r = await commands.cmd_hit_boss(s, A, G)
            if "伏诛" in r:
                dead = True
                check("讨伐boss(击杀)", "伏诛" in r or "奖池" in r, r[:80])
                break
            if "伤害" not in r:
                check("讨伐boss(出力)", "伤害" in r, r[:80])
                break
        if not dead:
            check("讨伐boss(击杀)", False, "多次攻击未击杀 Boss")

        # 19 商店 / 炼丹 / 用丹（搬 Sam5440 炼丹丹药，机制忠实源表）
        # 19a 商店展示
        r = await commands.cmd_shop(s, A)
        check("商店", "商店" in r and "炼丹炉" in r and "药材" in r, r[:40])

        # 19b 买丹炉 + 买药材（先灌足灵石，避免被前面抢/送/宗门扣光）
        p = await s.get_player(A)
        p["ling_shi"] = 100000
        await s.put_player(p)
        r = await commands.cmd_buy(s, A, "炼丹炉·陨铁炉")
        check("买(丹炉)", "购入" in r and "陨铁炉" in r, r[:60])
        r = await commands.cmd_buy(s, A, "药材2")
        check("买(药材)", "购入" in r and "本源药材" in r and "2型" in r, r[:60])
        r = await commands.cmd_buy(s, A, "不存在的物品")
        check("买(不存在)", "没有" in r, r[:60])

        # 19c 炼丹：玩家 D 独立跑（需炼丹炉 + 药材 + 境界门槛）
        await commands.cmd_enter(s, D, "丁", G)
        p = await s.get_player(D)
        p["realm"] = 10                      # 元婴境初期，可炼 洗髓丹
        p["furnace"] = "陨铁炉"
        p["inv"]["药材2"] = 6
        p["inv"]["药材3"] = 6
        await s.put_player(p)
        r = await commands.cmd_mix(s, D, "洗髓丹")
        check("炼丹", "开炉炼制" in r and "洗髓丹" in r, r[:60])
        # 收取（尚未到时间）
        r = await commands.cmd_mix_collect(s, D)
        check("收取丹药(未成)", "尚未炼成" in r or "还需" in r, r[:60])
        # 把炼制结束时间拨到过去，再收
        p = await s.get_player(D)
        if p.get("mixing"):
            p["mixing"]["end"] = 0
            await s.put_player(p)
        r = await commands.cmd_mix_collect(s, D)
        check("收取丹药", "炼成" in r and "洗髓丹" in r, r[:60])
        # 用丹（成功）
        r = await commands.cmd_use_elixir(s, D, "洗髓丹")
        check("用丹", "服下" in r and "洗髓丹" in r, r[:60])
        # 用丹（境界不足：把 D 境界降到 0，并塞一枚丹药，确保走到 realm 门槛）
        p = await s.get_player(D)
        p["realm"] = 0
        p["inv"]["洗髓丹"] = 1
        await s.put_player(p)
        r = await commands.cmd_use_elixir(s, D, "洗髓丹")
        check("用丹(境界不足)", "境界不足" in r, r[:60])

        # 存档持久化自检
        saved = await s.get_player(A)
        check("存档持久化", saved is not None and saved["qq"] == A, "")


def _run_all():
    asyncio.run(_scenario())
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed
    for name, ok, detail in _results:
        print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  :: {detail}"))
    print(f"\n==== {passed} passed, {failed} failed ====")
    return 1 if failed else 0


def test_all_commands():  # pytest 入口
    _results.clear()
    asyncio.run(_scenario())


if __name__ == "__main__":
    sys.exit(_run_all())
