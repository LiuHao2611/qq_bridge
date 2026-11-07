# -*- coding: utf-8 -*-
"""修仙游戏核心逻辑单测（零依赖、可 exit-code 验证）。

运行方式（二选一，均给出通过/失败 exit code）：
    python xiuxian/test_core.py        # 不依赖 pytest，自带 runner
    pytest xiuxian/test_core.py        # 标准 pytest

说明：本测试在导入 xiuxian 包前桩掉 nonebot，使插件模块可在无 QQ
连接的环境中安全加载（仅校验纯逻辑，不触碰网络/驱动）。
"""
import os
import sys
import types
import random
import asyncio
import tempfile

# ---- 让 xiuxian 包与 src.* 都可被导入 ----
# PLUGINS_DIR 供 `from xiuxian import ...`；REPO_ROOT 供插件 __init__.py 里的
# `from src.group_switch import ...` / `from src.game_mode import ...`。
# 裸跑脚本时 CWD 不保证是仓库根，两者缺一即 ModuleNotFoundError。
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---- 桩掉 nonebot，避免测试环境需安装/初始化 nonebot ----
def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        # nonebot: Matcher.handle() 是装饰器工厂，调用时不带参，返回装饰器
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

from xiuxian import core, config  # noqa: E402
from xiuxian.store import Store  # noqa: E402


def _fresh_player(qq="10001", group="20001", name="测试道友"):
    return core.create_player(qq, name, group, rng=lambda: 0.0)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_create_player_and_root():
    p = core.create_player("1", "甲", "g", rng=lambda: 0.0)
    assert p["qq"] == "1" and p["name"] == "甲" and p["group"] == "g"
    assert p["root"] in {r[0] for r in config.ROOT_POOL}
    assert p["realm"] == 0 and p["exp"] == 0
    assert p["ling_shi"] == config.START_LINGSHI
    assert p["secluding"] is False


def test_settle_only_while_secluding():
    """不闭关一点不涨；闭关才按分钟产出（开源 closing_exp 机制）。

    2026-10-26 改：旧实现按真实时间无条件涨修为，导致"突破完又满了、一直突破"，
    用户实测 12 分钟从筑基冲到金丹 3607/2000。现在改成只有闭关中才产出。
    """
    p = _fresh_player()
    p["last_settle"] = 0.0
    idle = core.settle(p, now_ts=3600.0)          # 干坐 1 小时但没闭关
    assert idle["gain"] == 0 and p["exp"] == 0

    p["secluding"] = True
    p["last_settle"] = 0.0
    res = core.settle(p, now_ts=600.0)            # 闭关 10 分钟
    assert res["gain"] == int(10 * config.SECLUDE_EXP_PER_MIN * p["root_mult"])
    assert p["exp"] == res["gain"]
    assert res["died"] is False                   # 无挂机死亡，杜绝打卡焦虑


def test_seclude_gain_and_cap():
    """出关结算按分钟给修为，且封顶在下境界需求 ×1.5（closing_exp_upper_limit）。"""
    p = _fresh_player()
    p["last_settle"] = 0.0
    core.start_seclude(p, now_ts=0.0)
    res = core.stop_seclude(p, now_ts=600.0)      # 闭关 10 分钟
    assert res["gain"] == int(10 * config.SECLUDE_EXP_PER_MIN * p["root_mult"])
    assert p["secluding"] is False

    # 挂很久也不会超过上限（否则一觉醒来能连破好几级）
    p2 = _fresh_player()
    p2["secluding"] = True
    p2["last_settle"] = 0.0
    core.settle(p2, now_ts=10 ** 8)
    assert p2["exp"] == core.seclude_cap(p2)
    assert p2["exp"] == int(config.REALMS[1]["exp_to_next"] * config.SECLUDE_CAP_RATIO)


def test_breakthrough_need_cd_and_fail():
    """突破门槛取下一境界修为；成功后 60 分钟 CD 内不可再破；失败扣 1~10% 且成功率累积。"""
    p = _fresh_player()
    need = core.breakthrough_need(p)
    assert need == config.REALMS[1]["exp_to_next"]     # 下一境界（练气境初期）的修为

    p["exp"] = need + 10
    ok = core.do_breakthrough(p, rng=lambda: 0.0)      # 强制成功
    assert ok["ok"] and p["realm"] == 1 and p["exp"] == 10
    assert core.bt_cd_left(p) > 0                      # 破镜 CD = 60 分钟

    # CD 内修为够了也不能再破（旧实现没有 CD，这是"连破"的根源）
    p["exp"] = core.breakthrough_need(p) + 1
    blocked = core.do_breakthrough(p, rng=lambda: 0.0)
    assert (not blocked["ok"]) and blocked["reason"] == "cd"

    # 失败：扣 1%~10% 修为 + 损寿 1 年 + 成功率 ×1.3 累积
    p2 = _fresh_player()
    p2["exp"] = core.breakthrough_need(p2)
    rate0 = core.bt_rate(p2)
    fail = core.do_breakthrough(p2, rng=lambda: 1.0)   # 强制失败
    assert (not fail["ok"]) and fail["reason"] == "fail"
    assert p2["exp"] < core.breakthrough_need(p2)
    assert core.bt_rate(p2) > rate0


def test_power_increases_with_realm():
    p = _fresh_player()
    low = core.power(p)
    p["exp"] = 999999
    p["realm"] = config.MAX_REALM
    high = core.power(p)
    assert high > low


def test_pvp_transfer_lingshi():
    att = _fresh_player("A")
    vic = _fresh_player("B")
    att["ling_shi"] = 1000
    vic["ling_shi"] = 1000
    res = core.pvp(att, vic, rng=lambda: 0.0)  # A 必胜
    assert res["win"] and vic["ling_shi"] < 1000 and att["ling_shi"] > 1000
    assert att["wins"] == 1 and vic["losses"] == 1


def test_crit_affects_fight():
    """暴击必须真的影响胜负（历史 bug：crit 只写不读，是条假成长线）。"""
    a, b = _fresh_player("A"), _fresh_player("B")
    # 双方完全对等 → 胜率恰好 0.5
    _, p_even = core._fight(a, b, rng=lambda: 0.5)
    assert abs(p_even - 0.5) < 1e-6, p_even
    # 期望倍率本身：5% × (1.5-1) → 1.025
    assert abs(core.crit_mult({"crit": 0.05, "crit_dmg": 1.5}) - 1.025) < 1e-9
    assert core.crit_mult({"crit": 0.0}) == 1.0            # 无暴击 = 无加成
    assert core.crit_mult({"crit": 0.5}) == core.crit_mult({"crit": 0.5, "crit_dmg": 1.5})
    assert core.crit_mult({"crit": 5.0, "crit_dmg": 1.5}) == 1.5   # 暴击率封顶 100%
    # 只有 A 堆暴击 → A 的胜率必须上升，且 B 相应下降
    a["crit"] = 0.5
    a_win, p_crit = core._fight(a, b, rng=lambda: 0.5)
    assert p_crit > p_even, (p_even, p_crit)
    b_win, p_mirror = core._fight(b, a, rng=lambda: 0.5)
    assert abs(p_crit + p_mirror - 1.0) < 1e-6
    # 高暴击方在"胜率掷骰刚好落在中间"时必须赢
    assert a_win is True and b_win is False


def test_rob_zero_target_safe():
    att = _fresh_player("A")
    vic = _fresh_player("B")
    vic["ling_shi"] = 0
    res = core.rob(att, vic, rng=lambda: 0.0)
    assert res["win"] is True  # 赢了但对方没钱
    assert res["amt"] == 0 and att["ling_shi"] == config.START_LINGSHI


def test_claim_cooldown_and_amount():
    p = _fresh_player()
    p["last_claim"] = 0
    random.seed(42)
    res = core.claim(p, now_ts=10 ** 9)
    assert res["ok"] and config.CLAIM_MIN <= res["amt"] <= config.CLAIM_MAX * (1 + config.CLAIM_STREAK_CAP)
    # 冷却内不可再次领取
    res2 = core.claim(p, now_ts=10 ** 9 + 1)
    assert not res2["ok"] and res2["reason"] == "cooldown"


def test_bounty_start_finish_and_mutex():
    """悬赏：接取占用互斥、未到期不可结算、到点按 rate 判定、成败都给灵石。"""
    p = _fresh_player()
    t0 = 10 ** 9

    # 任务不存在
    assert core.start_bounty(p, "不存在的任务", t0)["reason"] == "noent"

    # 接取（简称也能命中「寻找玄冰花」）
    res = core.start_bounty(p, "玄冰花", t0)
    assert res["ok"] and res["minutes"] == 60 and res["thank"] == 1200

    # 一次只能接一个
    assert core.start_bounty(p, "寻找玉髓芝", t0)["reason"] == "busy"

    # 未到期不可结算
    wait = core.finish_bounty(p, now_ts=t0 + 60)
    assert not wait["ok"] and wait["reason"] == "wait"

    # 到期且掷骰必中（rng 返回 0 < rate）→ 拿全额报酬
    ls0 = p["ling_shi"]
    ok = core.finish_bounty(p, now_ts=t0 + 60 * 60, rng=lambda: 0.0)
    assert ok["ok"] and ok["win"] and ok["amt"] == 1200
    assert p["ling_shi"] == ls0 + 1200 and p.get("bounty") is None

    # 高成功率任务掷骰必失（rng 返回 0.99 > rate/100）→ 只拿辛苦钱
    core.start_bounty(p, "寻找玉髓芝", t0 + 10 ** 5)
    ls1 = p["ling_shi"]
    bad = core.finish_bounty(p, now_ts=t0 + 10 ** 5 + 60 * 60, rng=lambda: 0.99)
    assert bad["ok"] and not bad["win"] and bad["amt"] == 100
    assert p["ling_shi"] == ls1 + 100

    # 没接任务时结算要明确拒绝
    assert core.finish_bounty(p, now_ts=t0 + 10 ** 6)["reason"] == "none"


def test_bounty_table_matches_source():
    """悬赏表完整性：16 条、三类、rate∈(0,100]、报酬与耗时均为正、成功率与报酬反相关。"""
    rows = config.BOUNTIES
    assert len(rows) == 16
    assert {r[0] for r in rows} == {"采药", "捉妖", "暗杀"}
    assert all(0 < r[2] <= 100 and r[3] > 0 and r[4] > 0 and r[5] > 0 for r in rows)
    # 成功率越高 → 报酬越低（开源表的设计取向，防止高成功率任务变成无脑印钞）
    by_rate = sorted(rows, key=lambda r: r[2])
    assert by_rate[0][4] > by_rate[-1][4]


def test_explore_structural():
    random.seed(7)
    seen_nodes = set()
    for _ in range(200):
        p = _fresh_player()
        ls0, exp0 = p["ling_shi"], p["exp"]
        r = core.explore(p)
        seen_nodes.add(r["node"])
        assert r["node"] in config.SECRET_NODES
        if r["node"] == "灵石矿":
            assert p["ling_shi"] > ls0
        elif r["node"] in ("灵药", "妖兽") and r.get("win"):
            assert p["exp"] > exp0
    # 多跑几次应覆盖多种节点
    assert len(seen_nodes) >= 3


def test_secret_lifecycle():
    """限时秘境自动探索：开启判定 / 报名去重 / 事件累加 / 进度 / 总览。"""
    BASE = 2_000_000_000
    p = _fresh_player()
    sec = core.secret_new(now_ts=BASE)
    assert core.secret_is_open(sec, now_ts=BASE) is True
    assert core.secret_is_open(sec, now_ts=BASE + config.SECRET_DURATION_SEC) is False
    assert core.secret_is_open(None) is False

    part = core.secret_join(sec, p, now_ts=BASE)
    assert str(p["qq"]) in sec["participants"] and part["event_count"] == 0
    again = core.secret_join(sec, p, now_ts=BASE + 1)   # 重复报名不重复计数
    assert again is part and len(sec["participants"]) == 1

    for i in range(5):
        core.secret_roll_event(p, part, now_ts=BASE + 10 * i)
    assert part["event_count"] == 5
    assert len(part["log"]) == 5
    assert part["next_event_at"] > BASE

    txt = core.secret_progress_text(sec, p, now_ts=BASE + 60)
    assert txt and "探索中" in txt and "剩余" in txt

    summ = core.secret_summary_text(sec)
    assert "总览" in summ and p["name"] in summ and "灵石" in summ
    assert "无人入内" in core.secret_summary_text(core.secret_new(now_ts=BASE))


def test_sect_create_join_war():
    leader = _fresh_player("L")
    member = _fresh_player("M")
    res = core.create_sect(leader, "仙门")
    assert res["ok"] and leader["sect"] == res["sect"]["id"]
    sect = res["sect"]
    jr = core.join_sect(member, sect)
    assert jr["ok"] and member["sect"] == sect["id"]
    assert core.sect_power(sect, [leader, member]) > 0
    # 宗门战：以弱胜强扰动存在
    enemy = core.create_sect(_fresh_player("E"), "魔门")["sect"]
    core.join_sect(_fresh_player("E2"), enemy)
    war = core.sect_war(sect, [leader, member], enemy,
                        [core.create_player("E2", "e", "g")], rng=lambda: 0.5)
    assert "a_wins" in war and "pa" in war and "pb" in war


def test_world_boss_hit_and_reward():
    p = _fresh_player()
    boss = core.spawn_boss(1)
    assert boss["hp"] == boss["max_hp"] > 0
    # 反复击打直至死亡
    dead = None
    for _ in range(10000):
        r = core.boss_hit(p, boss)
        if r["dead"]:
            dead = r
            break
    assert dead and sum(dead["rewards"].values()) <= config.BOSS_REWARD_POOL
    # 贡献者分奖合理
    assert str(p["qq"]) in dead["rewards"]


def test_roll_world_event_domain():
    random.seed(1)
    for _ in range(50):
        assert core.roll_world_event() in ("boss", "secret", "qiyu")


def test_store_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "x.json"))
        asyncio.run(s.load())
        p = _fresh_player()
        asyncio.run(s.put_player(p))
        got = asyncio.run(s.get_player(p["qq"]))
        assert got and got["qq"] == p["qq"]
        # 群启用 + 列表
        asyncio.run(s.enable_group("g1"))
        assert "g1" in asyncio.run(s.enabled_groups())
        # 宗门存取
        sect = core.create_sect(_fresh_player("X"), "宗")["sect"]
        asyncio.run(s.put_sect(sect))
        assert asyncio.run(s.get_sect(sect["id"]))["name"] == "宗"
        # 世界状态
        asyncio.run(s.set_world("g1", {"boss": {"hp": 1}}))
        assert asyncio.run(s.get_world("g1"))["boss"]["hp"] == 1


# ---------------------------------------------------------------------------
# 商店 / 炼丹 / 用丹（搬 Sam5440 炼丹丹药.json，机制忠实源表）
# 境界索引：练气境圆满=3，元婴境初期=10（见 config_open.OPEN_REALMS）
# ---------------------------------------------------------------------------
def test_buy_shop_furnace_and_herb():
    """商店：扣灵石买丹炉（入 furnace）/ 本源药材（入 inv·药材t）；灵石不足拒绝。"""
    p = _fresh_player()
    p["ling_shi"] = 5000
    # 买最便宜的陨铁炉（3000）
    r = core.buy_shop_item(p, "furnace:4003")
    assert r["ok"] and p["furnace"] == "陨铁炉" and p["ling_shi"] == 2000
    # 买 2 型本源药材（30/株）
    r2 = core.buy_shop_item(p, "herb:2")
    assert r2["ok"] and p["inv"].get("药材2") == 1 and p["ling_shi"] == 1970
    # 灵石不足：寒铁铸心炉 18000
    p["ling_shi"] = 100
    poor = core.buy_shop_item(p, "furnace:4001")
    assert not poor["ok"] and poor["reason"] == "poor"
    # 不存在的条目
    assert not core.buy_shop_item(p, "nope:0")["ok"]


def test_mix_elixir_requires_furnace_herb_realm():
    """炼丹前置：无炉/无药材/境界不够都拦下；齐了才消耗药材并开炉。"""
    pill = config.find_elixir("洗髓丹")  # exp_up，配方 {"2":6,"3":6}，需 元婴境初期(10)
    # 无炉（境界够，只缺炉 → nofurnace；注意 realm 门槛在 furnace 之前检查）
    p0 = _fresh_player(); p0["realm"] = 10
    assert core.mix_elixir_start(p0, "洗髓丹")["reason"] == "nofurnace"
    # 有炉无药材
    p1 = _fresh_player(); p1["realm"] = 10; p1["furnace"] = "陨铁炉"
    assert core.mix_elixir_start(p1, "洗髓丹")["reason"] == "herb"
    # 药材够但境界不够（用 练气境圆满=3 的玩家炼 元婴境初期 丹药）
    p2 = _fresh_player(); p2["realm"] = 3; p2["furnace"] = "陨铁炉"
    p2["inv"]["药材2"] = 6; p2["inv"]["药材3"] = 6
    assert core.mix_elixir_start(p2, "洗髓丹")["reason"] == "realm"
    # 齐活：消耗药材、开炉、记录丹炉 buff（陨铁炉 buff=0）
    p3 = _fresh_player(); p3["realm"] = 10; p3["furnace"] = "陨铁炉"
    p3["inv"]["药材2"] = 6; p3["inv"]["药材3"] = 6
    r = core.mix_elixir_start(p3, "洗髓丹")
    assert r["ok"] and r["name"] == "洗髓丹" and r["buff"] == 0
    assert p3["inv"].get("药材2", 0) == 0 and p3["inv"].get("药材3", 0) == 0
    assert p3["mixing"]["name"] == "洗髓丹"
    # 炼制中不可再开新炉
    assert core.mix_elixir_start(p3, "洗髓丹")["reason"] == "busy"


def test_mix_elixir_collect_timing_and_buff():
    """炼丹领取：到点才给丹；丹炉 buff 让产出 +0/+1/+2 枚；领完清空 mixing。"""
    p = _fresh_player(); p["realm"] = 10; p["furnace"] = "寒铁铸心炉"  # buff=2
    p["inv"]["药材2"] = 6; p["inv"]["药材3"] = 6
    now = 1000.0
    core.mix_elixir_start(p, "洗髓丹", now_ts=now)  # 耗时 60 分钟 → end=4600
    # 10 分钟后还没好
    early = core.mix_elixir_collect(p, now_ts=now + 10 * 60)
    assert not early["ok"] and early["reason"] == "wait" and early["remain"] > 0
    # 到点领取：1 + 丹炉 buff(2) = 3 枚
    ok = core.mix_elixir_collect(p, now_ts=now + 61 * 60)
    assert ok["ok"] and ok["count"] == 3 and p["inv"].get("洗髓丹") == 3
    assert p["mixing"] is None


def test_use_elixir_effects_and_limits():
    """用丹：exp_up/atk_buff/level_up_big 三类效果分别生效；境界门槛/无丹/每日上限拦截。"""
    # exp_up：洗髓丹 +8000 修为
    pe = _fresh_player(); pe["realm"] = 10; pe["inv"]["洗髓丹"] = 1
    re = core.use_elixir(pe, "洗髓丹")
    assert re["ok"] and re["bt"] == "exp_up" and pe["exp"] == 8000 and pe["inv"].get("洗髓丹", 0) == 0
    # atk_buff：摄魂鬼丸 +720 攻击
    pa = _fresh_player(); pa["realm"] = 10; pa["inv"]["摄魂鬼丸"] = 1
    ra = core.use_elixir(pa, "摄魂鬼丸")
    assert ra["ok"] and ra["bt"] == "atk_buff" and pa["atk"] == 10 + 720
    # level_up_big：筑基丹 +30% 突破率（累加到 bt_big，下次突破吃掉）
    pl = _fresh_player(); pl["realm"] = 3; pl["inv"]["筑基丹"] = 1
    rl = core.use_elixir(pl, "筑基丹")
    assert rl["ok"] and rl["bt"] == "level_up_big" and pl["bt_big"] == 30.0
    # 境界不够
    pr = _fresh_player(); pr["realm"] = 0; pr["inv"]["洗髓丹"] = 1
    assert core.use_elixir(pr, "洗髓丹")["reason"] == "realm"
    # 背包里没有
    pn = _fresh_player(); pn["realm"] = 10
    assert core.use_elixir(pn, "洗髓丹")["reason"] == "none"
    # 每日/累计上限：筑基丹 day_num=1 all_num=1，用一次后第二次被拦
    pl["inv"]["筑基丹"] = 1
    core.use_elixir(pl, "筑基丹")  # 已用过一次（上面），这里第二次
    again = core.use_elixir(pl, "筑基丹")
    assert not again["ok"] and again["reason"] in ("daylimit", "alllimit")


def test_bt_big_consumed_on_breakthrough():
    """level_up_big 的突破率加成在突破时并入 rate，且无论成败都清零。"""
    p = _fresh_player(); p["realm"] = 3  # 练气境圆满，下一境界 筑基境初期 need_exp=6000
    p["exp"] = 6000; p["bt_big"] = 30.0
    rate0 = core.bt_rate(p)
    ok = core.do_breakthrough(p, rng=lambda: 0.0)  # 强制成功
    assert ok["ok"] and p["realm"] == 4 and ok["rate"] == min(100.0, rate0 + 30.0)
    assert p["bt_big"] == 0.0  # 已消耗


# ---------------------------------------------------------------------------
# 自带 runner（无需 pytest）
# ---------------------------------------------------------------------------
def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {fn.__name__}: {e!r}")
            failed += 1
    print(f"\n==== {passed} passed, {failed} failed ====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
