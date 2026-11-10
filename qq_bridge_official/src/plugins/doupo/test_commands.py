# -*- coding: utf-8 -*-
"""斗破苍穹 · 全部口令的业务级测试（自带 runner，exit-code 可验证）。

策略：不启动 QQ / nonebot，直连 commands.*（真实业务层）+ 临时 Store，
在**同一个 asyncio 事件循环**里按真实玩家顺序走一遍全流程，断言：
  - 未入道时每条口令都回 NOT_ENTERED（防"没角色也能改数据"）
  - 返回文案符合预期（命中关键字）
  - 存档状态被正确写入（金币 / 修为 / 武器 / 背包 / 异火）

运行：
    python src/plugins/doupo/test_commands.py     # exit 0 = 全绿
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

# PLUGINS_DIR 供 `from doupo import ...`；REPO_ROOT 供插件 __init__.py 里的
# `from src.game_mode import ...`。裸跑脚本时 CWD 不保证是仓库根，两者缺一即 ModuleNotFoundError。
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


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
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    sys.modules["nonebot.adapters.onebot.v11"].MessageEvent = object
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = \
        types.SimpleNamespace(image=lambda data: ("[image]", len(data or b"")))
    sys.modules["nonebot.adapters.onebot.v11"].Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg  # noqa: E402
from doupo.store import Store  # noqa: E402

G = "900000001"
A, B = "100001", "100002"

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))


async def main():
    tmp = tempfile.mkdtemp(prefix="doupo_cmd_")
    store = Store(os.path.join(tmp, "doupo.json"))
    await store.load()

    # ------------------------------------------------------------------
    # 0. 未入道：所有需要角色的口令都必须挡回去
    # ------------------------------------------------------------------
    guarded = [
        ("人物", commands.cmd_panel(store, A)),
        ("修炼", commands.cmd_cultivate(store, A)),
        ("突破", commands.cmd_break(store, A)),
        ("战斗", commands.cmd_fight(store, A)),
        ("强化", commands.cmd_enhance(store, A)),
        ("斗技", commands.cmd_skills(store, A)),
        ("异火", commands.cmd_flames(store, A)),
        ("宝库", commands.cmd_shop(store, A)),
        ("炼药", commands.cmd_recipes(store, A)),
        ("背包", commands.cmd_bag(store, A)),
        ("任务", commands.cmd_tasks(store, A)),
    ]
    for label, coro in guarded:
        txt = await coro
        check(f"未入道 · {label} 被拦截", txt == commands.NOT_ENTERED, txt[:40])

    # ------------------------------------------------------------------
    # 1. 菜单 / 帮助
    # ------------------------------------------------------------------
    menu = commands.cmd_menu()
    check("菜单含 斗破菜单 口令", "斗破菜单" in menu)
    # 关键：菜单条数必须等于 __init__.py 里注册的 matcher 数（e2e 里再做静态断言）
    check("菜单条数 = menu_total()", commands.menu_total() == 25,
          f"menu_total={commands.menu_total()}")
    # 新设计：菜单是唯一切换口（斗破菜单），其余口令全部纯中文、不再带前缀
    lines = [l.split(" —— ")[0].strip() for l in menu.splitlines() if " —— " in l]
    bad = [l for l in lines if "\u0064\u0070" in l]
    check("菜单内不再含任何前缀残留", not bad, f"违例={bad}")
    # 卡片 tip / 游戏内错误提示同样用户可见，只查菜单文本会漏（2026-11-07 实测漏过 8 处）。
    # 用 ast 扫全部生产模块的字符串常量，docstring 也算，防止旧写法留在说明里被照抄。
    # 测试文件不扫：它们用转义写法做自举断言。
    import ast
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    offenders = []
    for fn in ("__init__.py", "commands.py", "card.py", "core.py",
               "config.py", "store.py", "avatar.py", "_throttle.py"):
        src = (here / fn).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "\u0064\u0070" in node.value):
                offenders.append(f"{fn}:{node.lineno}")
    check("全模块用户可见文案无前缀残留", not offenders, f"违例={offenders[:6]}")
    check("帮助含暴击公式", "暴击% = 10" in commands.cmd_help())

    # ------------------------------------------------------------------
    # 2. 入道 → 人物
    # ------------------------------------------------------------------
    r = await commands.cmd_enter(store, A, "萧炎", G)
    check("入道 成功", "玄重尺" in r and "一星斗者" in r, r[:50])
    p = await store.get_player(A)
    check("入道写入存档", p is not None and p["name"] == "萧炎")
    check("入道赠金币", p["gold"] == cfg.START_GOLD, f"gold={p['gold']}")
    check("入道赠回灵丹×5", core.count_item(p, "回灵丹") == cfg.START_HP_POTION,
          f"回灵丹={core.count_item(p, '回灵丹')}")
    check("群被登记", str(G) in await store.groups())
    r2 = await commands.cmd_enter(store, A, "萧炎", G)
    check("重复入道被挡", "已踏入" in r2, r2[:30])

    panel = await commands.cmd_panel(store, A)
    check("人物 含境界", "一星斗者" in panel)
    check("人物 含玄重尺", "玄重尺" in panel)
    check("人物 含战力", "战力" in panel)

    # ------------------------------------------------------------------
    # 3. 修炼 / 突破
    # ------------------------------------------------------------------
    r = await commands.cmd_cultivate(store, A)
    check("修炼 有回包", "修为" in r, r[:40])
    r = await commands.cmd_break(store, A)
    check("突破 修为不足被挡", "修为不足" in r, r[:40])
    p = await store.get_player(A)
    p["exp"] = cfg.exp_need(1) + 10
    await store.put_player(p)
    r = await commands.cmd_break(store, A)
    check("突破 成功", "突破成功" in r and "二星斗者" in r, r[:60])
    p = await store.get_player(A)
    check("突破后等级 =2", int(p["lv"]) == 2, f"lv={p['lv']}")
    check("突破后回满血", p["hp"] == core.stats(p)["体力"])

    # ------------------------------------------------------------------
    # 4. 战斗（含 CD）
    # ------------------------------------------------------------------
    r = await commands.cmd_fight(store, A)
    check("战斗 遭遇妖兽", "遭遇" in r, r[:60])
    p = await store.get_player(A)
    check("战斗写入 last_fight", float(p.get("last_fight", 0)) > 0)
    r = await commands.cmd_fight(store, A)
    check("战斗 CD 生效", "调息" in r, r[:40])
    # 清 CD 后必须仍能打（斗气每战回满，不能第二场就只能普攻）
    p = await store.get_player(A)
    p["last_fight"] = 0
    await store.put_player(p)
    r = await commands.cmd_fight(store, A)
    check("战斗 第二场正常开打", "遭遇" in r, r[:40])

    # ------------------------------------------------------------------
    # 5. 强化
    # ------------------------------------------------------------------
    p = await store.get_player(A)
    core.take_item(p, "炎星石", core.count_item(p, "炎星石"))   # 清空（take 数量不足时不扣）
    await store.put_player(p)
    r = await commands.cmd_enhance(store, A)
    check("强化 缺石被挡", "炎星石不足" in r, r[:60])
    core.add_item(p, "炎星石", 30)
    await store.put_player(p)
    r = await commands.cmd_enhance(store, A)
    check("强化 成功", "强化成功" in r, r[:60])
    p = await store.get_player(A)
    check("强化后武器 =1", int(p["weapon"]) == 1, f"weapon={p['weapon']}")
    # 一路强到十级：必须蜕变且封顶
    core.add_item(p, "炎星石", 200)
    await store.put_player(p)
    evolved = ""
    for _ in range(12):
        t = await commands.cmd_enhance(store, A)
        if "异火恒古尺" in t:
            evolved = t
        if "巅峰" in t:
            break
    check("强化 十级蜕变", "异火恒古尺" in evolved, evolved[:60])
    p = await store.get_player(A)
    check("武器封顶 internal=9", int(p["weapon"]) == 9, f"weapon={p['weapon']}")
    r = await commands.cmd_enhance(store, A)
    check("强化 满级后挡回", "无法再强化" in r, r[:40])
    # 还原成 0 级，别影响后面战力断言
    p["weapon"] = 0
    await store.put_player(p)

    # ------------------------------------------------------------------
    # 6. 斗技 / 升级
    # ------------------------------------------------------------------
    r = await commands.cmd_skills(store, A)
    check("斗技 列出 4 门", all(s["name"] in r for s in cfg.SKILLS))
    r = await commands.cmd_skill_up(store, A, "龙吟碎")
    check("升级 缺焚血散被挡", "不足" in r, r[:60])
    p = await store.get_player(A)
    core.add_item(p, cfg.SKILL_UP_ITEM, 10)
    await store.put_player(p)
    r = await commands.cmd_skill_up(store, A, "龙吟碎")
    check("升级 成功", "升至" in r, r[:60])
    p = await store.get_player(A)
    check("斗技等级 =2", int(p["skills"]["龙吟碎"]) == 2)
    r = await commands.cmd_skill_up(store, A, "不存在")
    check("升级 未知斗技被挡", "无此斗技" in r, r[:40])

    # ------------------------------------------------------------------
    # 7. 异火 / 养火
    # ------------------------------------------------------------------
    r = await commands.cmd_flames(store, A)
    check("异火 列出 5 种", all(f["name"] in r for f in cfg.FLAMES))
    r = await commands.cmd_feed(store, A, "青莲地心火")
    check("养火 无药渣被挡", "药渣" in r, r[:40])
    p = await store.get_player(A)
    core.add_item(p, "药渣", 500)
    await store.put_player(p)
    r = await commands.cmd_feed(store, A, "青莲地心火")
    check("养火 喂养生效", "药渣" in r and "级" in r, r[:60])
    p = await store.get_player(A)
    check("异火等级上升", int(p["flames"].get("青莲地心火", 0)) >= 1,
          f"lv={p['flames'].get('青莲地心火')}")
    # 喂到圆满后必须封顶，不能无限涨
    core.add_item(p, "药渣", 10000)
    await store.put_player(p)
    for _ in range(10):
        await commands.cmd_feed(store, A, "青莲地心火")
    p = await store.get_player(A)
    check("异火封顶于 FLAME_MAX_LV",
          int(p["flames"].get("青莲地心火", 0)) == cfg.FLAME_MAX_LV,
          f"lv={p['flames'].get('青莲地心火')} max={cfg.FLAME_MAX_LV}")
    r = await commands.cmd_feed(store, A, "青莲地心火")
    check("养火 满级后挡回", "圆满" in r, r[:40])
    r = await commands.cmd_feed(store, A, "帝炎")
    check("养火 帝炎拒绝喂养", "只认斗帝" in r, r[:40])

    # ------------------------------------------------------------------
    # 8. 宝库 / 买
    # ------------------------------------------------------------------
    r = await commands.cmd_shop(store, A)
    check("宝库 有货", "炎星石" in r)
    r = await commands.cmd_buy(store, A, "血灵草 2")
    check("买 成功", "购入" in r and "血灵草" in r, r[:60])
    p = await store.get_player(A)
    check("背包收到血灵草", core.count_item(p, "血灵草") >= 2,
          f"血灵草={core.count_item(p, '血灵草')}")
    r = await commands.cmd_buy(store, A, "不存在的东西")
    check("买 未知货物被挡", "没有" in r, r[:40])
    p = await store.get_player(A)
    p["gold"] = 1
    await store.put_player(p)
    r = await commands.cmd_buy(store, A, "炎星石 5")
    check("买 金币不足被挡", "金币不足" in r, r[:40])
    p["gold"] = cfg.START_GOLD
    await store.put_player(p)

    # ------------------------------------------------------------------
    # 9. 炼药 / 炼制 / 服丹 / 背包
    # ------------------------------------------------------------------
    r = await commands.cmd_recipes(store, A)
    check("炼药 列出丹方", "回灵丹" in r and "灭神丹" in r)
    p = await store.get_player(A)
    p["hp"] = 1
    core.add_item(p, "回灵丹", 3)
    await store.put_player(p)
    r = await commands.cmd_craft(store, A, "回灵丹")
    check("炼制 材料不足被挡", "材料不足" in r, r[:60])
    # 备齐材料（血灵草×2 + 紫叶兰×1）后必须炼得出来
    for _ in range(3):
        for k, v in cfg.RECIPE_BY_NAME["回灵丹"]["need"].items():
            core.add_item(p, k, v)
        await store.put_player(p)
        r = await commands.cmd_craft(store, A, "回灵丹")
        if "丹成" in r or "失败" in r:
            break
        p["last_craft"] = 0          # 清冷却继续试
    check("炼制 有回包（丹成/失败）", ("丹成" in r) or ("失败" in r), r[:60])
    r = await commands.cmd_craft(store, A, "不存在的丹")
    check("炼制 未知丹方被挡", "无此丹方" in r, r[:40])
    r = await commands.cmd_use(store, A, "回灵丹")
    check("服丹 回血", "气血" in r or "服下" in r, r[:60])
    p = await store.get_player(A)
    check("服丹后回血", int(p["hp"]) > 1, f"hp={p['hp']}")
    bag = await commands.cmd_bag(store, A)
    check("背包 有内容", "包裹" in bag and "×" in bag)

    # ------------------------------------------------------------------
    # 10. 任务
    # ------------------------------------------------------------------
    r = await commands.cmd_tasks(store, A)
    check("任务 含主线", "主线" in r, r[:60])
    r = await commands.cmd_task_accept(store, A)
    check("接任务 成功", "接取支线" in r, r[:60])
    r = await commands.cmd_task_accept(store, A)
    check("接任务 重复接被挡", "未交" in r, r[:60])
    r = await commands.cmd_task_submit(store, A)
    check("交任务 未达标被挡", "尚未完成" in r, r[:60])
    r = await commands.cmd_main_submit(store, A)
    check("领主线 未达标被挡", "未完成" in r or "完成" in r, r[:60])

    # ------------------------------------------------------------------
    # 11. 排行 / 抢 / 送
    # ------------------------------------------------------------------
    await commands.cmd_enter(store, B, "药老", G)
    r = await commands.cmd_rank(store, G)
    check("排行 列出两人", "萧炎" in r and "药老" in r, r[:80])
    r = await commands.cmd_rank(store, "999999")
    check("排行 空群有提示", "尚无人" in r, r[:40])

    pa = await store.get_player(A)
    pb = await store.get_player(B)
    gold_a, gold_b = int(pa["gold"]), int(pb["gold"])
    r = await commands.cmd_give(store, A, B, 1000)
    check("送 成功", "赠予" in r, r[:40])
    pa = await store.get_player(A)
    pb = await store.get_player(B)
    check("送 金额正确", int(pb["gold"]) == gold_b + 1000
          and int(pa["gold"]) == gold_a - 1000,
          f"A {gold_a}->{pa['gold']}  B {gold_b}->{pb['gold']}")
    r = await commands.cmd_give(store, A, B, -5)
    check("送 负数被挡", "无效" in r, r[:40])
    r = await commands.cmd_give(store, A, "999999", 100)
    check("送 对方未入道被挡", "尚未入道" in r, r[:40])

    r = await commands.cmd_rob(store, A, B)
    check("抢 有结果", "金币" in r, r[:60])
    r = await commands.cmd_rob(store, A, None)
    check("抢 缺目标被挡", "用法" in r, r[:40])

    # ------------------------------------------------------------------
    # 12. 存档落盘可回读
    # ------------------------------------------------------------------
    store2 = Store(os.path.join(tmp, "doupo.json"))
    await store2.load()
    back = await store2.get_player(A)
    check("存档可回读", back is not None and back["name"] == "萧炎")
    check("回读等级一致", int(back["lv"]) == int(pa["lv"]),
          f"{back.get('lv')} vs {pa.get('lv')}")


def _run():
    asyncio.run(main())
    ok = 0
    for name, passed, detail in _results:
        print(("PASS  " if passed else "FAIL  ") + name + ("" if passed else f"  -> {detail}"))
        ok += 1 if passed else 0
    print("=" * 70)
    print(f"CMD_TOTAL={len(_results)} PASSED={ok} FAILED={len(_results) - ok}")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    sys.exit(_run())
