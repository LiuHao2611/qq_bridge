# -*- coding: utf-8 -*-
"""斗破苍穹 · 口令业务层（纯函数 + 存储交互，零 nonebot 依赖，可直接单测）。

每个 cmd_* 输入「store + 玩家/群标识 + 参数」，返回要回群的文字。
__init__.py 的 matcher 只负责取参数 → 调 cmd_* → finish(文字)。

⚠️ 命名约定：除「斗破菜单」外，全部口令带 dp 前缀。
   原因：修仙插件已占用 商店 / 坊市 / 突破 / 排行榜 / 任务 / 炼丹，
   同优先级 matcher 会互相抢答，故统一前缀彻底避开。
"""

from __future__ import annotations

import time

from . import config as cfg
from . import core

NOT_ENTERED = "你尚未踏入斗气大陆，发送「dp入道」开启征程。"


async def _player(store, qq):
    return await store.get_player(str(qq))


def _bar(cur: int, mx: int, width: int = 10) -> str:
    if mx <= 0:
        return ""
    n = max(0, min(width, int(cur * width / mx)))
    return "█" * n + "░" * (width - n)


def _fmt(n) -> str:
    n = int(n)
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


# ---------------------------------------------------------------------------
# 菜单（口令总览）
# ---------------------------------------------------------------------------
#: 菜单 = 唯一源，纯文本渲染直接从这里取
MENU_GROUPS = (
    ("入门 · 建立角色", (
        ("斗破菜单 / dp菜单", "就是这条，随时回来看全部口令"),
        ("dp入道", "创建角色：一星斗者，赠 10000 金币 + 玄重尺 + 回灵丹×5"),
        ("dp人物", "人物面板：境界星级 / 炼药师品阶 / 六维属性 / 装备"),
        ("dp帮助", "玩法细则：伤害、强化、炼药、异火、斗帝，全部数值"),
    )),
    ("修炼 · 涨修为升境界", (
        ("dp修炼", "结算离线修为（每分钟自动 +6，陨落心炎每级 +50%）"),
        ("dp突破", "修为满时晋升一星；28 级冲斗帝看机缘"),
        ("dp战斗", "随机遭遇一只同阶妖兽，回合制结算"),
    )),
    ("养成 · 变强", (
        ("dp强化", "强化玄重尺：十级蜕变为异火恒古尺（2000/2000/1000/1200）"),
        ("dp斗技", "看 4 门斗技，快捷键 1/3/7/9"),
        ("dp升级 龙吟碎", "用焚血散升斗技，每级伤害 +20%"),
        ("dp异火", "看 5 种异火与进度"),
        ("dp养火 青莲地心火", "用药渣喂养异火（炼药失败会出药渣）"),
    )),
    ("炼药 · 宝库", (
        ("dp宝库", "随身商店：药材 + 炎星石，看价"),
        ("dp买 血灵草 2", "买 2 份血灵草"),
        ("dp炼药", "看丹方：帝品 / 五品 / 四品"),
        ("dp炼制 回灵丹", "开炉炼丹，成功率看炼药师品阶，失败得药渣"),
        ("dp服丹 回灵丹", "服用丹药"),
        ("dp背包", "包裹（24 格）"),
    )),
    ("任务 · 江湖", (
        ("dp任务", "看主线进度与当前支线"),
        ("dp接任务", "随机接一条支线"),
        ("dp交任务", "支线达标后结算"),
        ("dp领主线", "主线达标后结算，推进章节"),
        ("dp排行", "本群战力榜前 10"),
        ("dp抢 @某人", "掠夺对方 10% 金币（输了自己掉）"),
        ("dp送 100 @某人", "赠金币"),
    )),
)


def menu_total() -> int:
    return sum(len(rows) for _, rows in MENU_GROUPS)


def menu_text() -> str:
    out = [f"📖 斗破菜单 · 共 {menu_total()} 条口令（除菜单外全部 dp 前缀）"]
    for group, rows in MENU_GROUPS:
        out.append("")
        out.append(f"【{group}】")
        for cmd, desc in rows:
            out.append(f"{cmd} —— {desc}")
    out.append("")
    out.append("⚠️ 与「修仙」重名的 商店/突破/任务/排行榜/炼丹 已统一让出，斗破一律 dp 前缀。")
    return "\n".join(out)


def cmd_menu() -> str:
    return menu_text()


def cmd_help() -> str:
    lines = [
        "📘 斗破玩法细则",
        "",
        "【属性】力量=普攻，斗气=技能，敏捷→暴击+闪避，防御=减伤，体力=血量",
        "  成长：值 = 基础×10 + 阶²×基础×10 + 基础×等级，阶 =(等级-1)/9",
        "  暴击% = 10 + (敏捷+30)/100，闪避% = 5 + (敏捷+20)/200",
        "",
        "【伤害】我方 = 攻击 × 800/(800+敌防)，暴击 ×1.5",
        "  敌方 = 敌攻 × 800/(800+我方防御)",
        "",
        "【强化】玄重尺 L 级时属性 = 基础×(1+0.1·L(L+1)/2)",
        "  每次消耗炎星石 = L+1 块；L=9（界面 10 级）硬编码 2000/2000/1000/1200",
        "  十级即「异火恒古尺」",
        "",
        "【炼药】成功率 = 55% + 5%/品阶（最高 95%）；失败给药渣，药渣喂异火",
        "",
        "【异火】青莲地心火 斗气槽+25%/级；陨落心炎 经验+50%/级；",
        "  三千焱炎火 吸血+2%/级；净莲妖火 暴击闪避+10%/级；帝炎 斗帝专属",
        "",
        "【斗帝】28 级斗圣巅峰冲 29 级：成功率 = 30% + 机缘/2000，帝炎 100%",
        "",
        "【挂机】每分钟自动 +6 修为，不登录也涨，不强制打卡。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入道 / 人物
# ---------------------------------------------------------------------------
async def cmd_enter(store, qq, name, gid) -> str:
    p = await _player(store, qq)
    if p:
        return "你已踏入斗气大陆，发送「dp人物」查看面板。"
    p = core.create_player(qq, name, gid)
    await store.put_player(p)
    # 私聊用 pv:<user_id> 虚拟作用域，不登记进「启用群」列表，避免污染群态
    if not str(gid).startswith("pv:"):
        await store.enable_group(str(gid))
    return (
        "🔥 你自云岚山下睁眼，斗气绕体三周——\n"
        f"【{cfg.full_realm(1)}】\n"
        "手中握着那柄黑黝黝的巨尺「玄重尺」。\n\n"
        "发送「dp人物」看面板，「dp战斗」试刀，「dp帮助」看全部规则。"
    )


async def cmd_panel(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)
    await store.put_player(p)

    st = core.stats(p)
    lv = int(p["lv"])
    need = cfg.exp_need(lv)
    exp_line = f"{_fmt(p['exp'])}/{_fmt(need)}" if need else f"{_fmt(p['exp'])}（已至巅峰）"
    alch = cfg.ALCHEMIST[int(p.get("alch", 0))]
    alch_need = alch[1]
    w = int(p.get("weapon", 0))

    rows = [
        f"👤 【{p['name']}】",
        f"境界　{cfg.full_realm(lv)}",
        f"　　　{exp_line}　{_bar(int(p['exp']), need)}",
        f"炼药　{alch[0]}炼药师",
        f"　　　{_fmt(p.get('alch_exp', 0))}/{_fmt(alch_need) if alch_need else '—'}"
        f"　{_bar(int(p.get('alch_exp', 0)), alch_need)}",
        "",
        f"🗡️ {cfg.weapon_name(w)}　{cfg.weapon_display_level(w)} 级",
    ]
    wp = {}
    for k, base in cfg.WEAPON["stats"].items():
        wp[k] = cfg.weapon_stat(base, w) if w < 9 else cfg.WEAPON["max_stats"][k]
    rows.append(f"　力量 {wp['力量']}　敏捷 {wp['敏捷']}")
    rows.append(f"　斗气 {wp['斗气']}　防御 {wp['防御']}")
    rows += [
        "",
        f"　力量 {st['力量']}　敏捷 {st['敏捷']}　斗气 {st['斗气']}",
        f"　防御 {st['防御']}　机缘 {st['机缘']}　体力 {st['体力']}",
        "",
        f"❤️ 气血 {_bar(p['hp'], st['体力'])} {p['hp']}/{st['体力']}",
        f"💠 斗气 {_bar(p['mp'], core.mp_max(p))} {p['mp']}/{core.mp_max(p)}",
        f"💰 金币 {p['gold']}　💎 炎星石 {core.count_item(p, '炎星石')}",
        f"⚔️ 战力 {core.power(p)}（{p.get('wins', 0)}胜 {p.get('losses', 0)}负）",
    ]
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# 修炼 / 突破
# ---------------------------------------------------------------------------
async def cmd_cultivate(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.settle(p)
    await store.put_player(p)
    lv = int(p["lv"])
    need = cfg.exp_need(lv)
    tip = "，可发送「dp突破」晋升" if core.can_level(p) else ""
    head = f"🧘 静修 {res['minutes']} 分钟，得修为 +{res['gain']}。" if res["gain"] else "🧘 你刚静修过，气机未动。"
    return (f"{head}\n修为 {_fmt(p['exp'])}/{_fmt(need) if need else '—'}{tip}\n"
            f"当前：{cfg.full_realm(lv)}")


async def cmd_break(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)
    lv = int(p["lv"])
    if lv >= cfg.MAX_LV:
        await store.put_player(p)
        return "你已至斗帝之境，此界再无敌手。"
    if not core.can_level(p):
        need = cfg.exp_need(lv) - int(p["exp"])
        return f"修为不足，距下一星还差 {_fmt(need)}。去「dp战斗」或「dp修炼」。"
    res = core.level_up(p)
    await store.put_player(p)
    if res.get("tiandi"):
        return (
            "🌩️ 九霄雷动，天地齐鸣——\n"
            "🔥 你踏碎最后一层壁障，成就【斗帝】！\n"
            "帝炎自燃于体，全属性 ×1.2，机缘值全满。"
        )
    if res.get("reason") == "tiandi_fail":
        return (f"💥 冲击斗帝失败（成功率 {int(res['rate'] * 100)}%）！\n"
                "斗气倒灌，修为折损。多积机缘（支线任务 / 九幽散）再来。")
    return f"✨ 突破成功！晋入【{res['name']}】。气血斗气已满。"


# ---------------------------------------------------------------------------
# 战斗
# ---------------------------------------------------------------------------
async def cmd_fight(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)
    now = time.time()
    left = int(cfg.FIGHT_CD_SEC - (now - float(p.get("last_fight", 0))))
    if left > 0:
        await store.put_player(p)
        return f"气血未复，需调息 {left} 秒。"

    # 先抽"物种"，再按玩家当前属性实例化成同阶怪（难度恒定，不随阶跃断崖）
    mon = core.monster_scaled(p, core.pick_monster(p))
    res = core.fight(p, mon)
    p["last_fight"] = now
    if res["win"]:
        core.task_progress(p, "kill", 1)
        core.main_progress(p, "kill", 1)
    await store.put_player(p)

    # 战报：只取前 3 回合 + 末回合，避免刷屏
    seq = res["log"]
    show = seq if len(seq) <= 6 else seq[:3] + ["..."] + seq[-2:]
    lines = [f"⚔️ 你遭遇【{mon['name']}】（{mon['lv']} 级）"]
    for it in show:
        if it == "...":
            lines.append("　…")
        elif it.get("miss"):
            lines.append("　→ 你身形一晃，避开了兽击")
        elif it.get("taken"):
            lines.append(f"　← 兽击 {it['dmg']}")
        else:
            tag = f"{it['kind']}" + ("（暴击！）" if it["crit"] else "")
            lines.append(f"　→ {tag} {it['dmg']}")

    if res["win"]:
        lines.append(f"🏆 斩获！{res['rounds']} 回合，受伤 {res['taken']}")
        lines.append(f"　修为 +{_fmt(res['exp'])}　金币 +{res['gold']}")
        if res["drops"]:
            lines.append("　掉落：" + "、".join(f"{n}×{c}" for n, c in res["drops"]))
    else:
        lines.append(f"💀 不敌败退（{res['rounds']} 回合，受伤 {res['taken']}）")
        lines.append(f"　你拖着重伤退走，气血剩 {p['hp']}")
    lv = int(p["lv"])
    if core.can_level(p):
        lines.append(f"⬆️ 修为已满（{cfg.full_realm(lv)}），发送「dp突破」晋升")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 强化
# ---------------------------------------------------------------------------
async def cmd_enhance(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    pv = core.enhance_preview(p)
    if int(p.get("weapon", 0)) > 8:
        return f"🗡️ {cfg.WEAPON['name_max']} 已至十级巅峰，无法再强化。"

    head = [
        f"🗡️ {cfg.weapon_name(int(p['weapon']))}",
        f"　{pv['disp']} 级 ↗ {pv['next_disp']} 级",
        f"　力量 ↗{pv['next']['力量'] - pv['cur']['力量']}　"
        f"敏捷 ↗{pv['next']['敏捷'] - pv['cur']['敏捷']}",
        f"　斗气 ↗{pv['next']['斗气'] - pv['cur']['斗气']}　"
        f"防御 ↗{pv['next']['防御'] - pv['cur']['防御']}",
        f"本次强化需要 {pv['cost']} 块炎星石（现有 {core.count_item(p, '炎星石')}）",
    ]
    res = core.enhance(p)
    await store.put_player(p)
    if not res.get("ok"):
        head.append("❌ 炎星石不足，无法强化。")
        head.append("　（打怪掉落 / 「dp买 炎星石」）")
        return "\n".join(head)

    head.append("✅ 强化成功！")
    if res["evolved"]:
        head.append("🔥 玄重尺寸寸崩裂，黑铁尽褪——【异火恒古尺】现世！")
        head.append("　力量 2000　敏捷 2000　斗气 1000　防御 1200")
    else:
        head.append(f"　现为 {res['display']} 级")
    return "\n".join(head)


# ---------------------------------------------------------------------------
# 斗技 / 异火
# ---------------------------------------------------------------------------
async def cmd_skills(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    sk = p.get("skills") or {}
    out = ["📜 斗技（快捷键 1/3/7/9）"]
    for s in cfg.SKILLS:
        lv = int(sk.get(s["name"], 1))
        bar = "●" * lv + "○" * (s["max"] - lv)
        out.append(f"　[{s['key']}] {s['name']}　{bar} {lv}/{s['max']}")
    out.append("")
    out.append("　升一级伤害 +20%，消耗 " + cfg.SKILL_UP_ITEM)
    out.append(f"　现有 {cfg.SKILL_UP_ITEM} ×{core.count_item(p, cfg.SKILL_UP_ITEM)}")
    out.append("　用法：dp升级 龙吟碎")
    return "\n".join(out)


async def cmd_skill_up(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：dp升级 龙吟碎（可选：" + "、".join(cfg.SKILL_NAMES) + "）"
    hit = next((n for n in cfg.SKILL_NAMES if name in n or n in name), None)
    if not hit:
        return "无此斗技。可选：" + "、".join(cfg.SKILL_NAMES)
    res = core.skill_up(p, hit)
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "max":
            return f"【{hit}】已至 {res['lv']} 级圆满。"
        return (f"❌ {res['item']}不足：需要 {res['need']} 份，你有 {res['have']}。\n"
                f"　去「dp炼制 {cfg.SKILL_UP_ITEM}」自己炼。")
    return f"✅【{hit}】升至 {res['lv']}/{res['max']} 级，技能伤害再进一层。"


async def cmd_flames(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    fl = p.get("flames") or {}
    out = [f"🔥 异火　药渣 ×{core.count_item(p, '药渣')}"]
    for f in cfg.FLAMES:
        lv = int(fl.get(f["name"], 0))
        if f["name"] == cfg.FLAME_DIYAN:
            state = "已收服" if p.get("diyan") else "未得（成就斗帝自燃）"
            out.append(f"　{f['icon']} {f['name']}　{state}")
            continue
        if lv >= cfg.FLAME_MAX_LV:
            out.append(f"　{f['icon']} {f['name']}　{lv} 级（圆满）")
            continue
        need = int(f["need"]) * (lv + 1)
        out.append(f"　{f['icon']} {f['name']}　{lv}/{cfg.FLAME_MAX_LV} 级　"
                   f"下级需 {_fmt(need)} 药渣")
    out.append("")
    out.append("　用法：dp养火 青莲地心火（一次喂光现有药渣）")
    return "\n".join(out)


async def cmd_feed(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：dp养火 青莲地心火（可选：" + "、".join(cfg.FLAME_NAMES) + "）"
    hit = next((n for n in cfg.FLAME_NAMES if name in n or n in name), None)
    if not hit:
        return "无此异火。可选：" + "、".join(cfg.FLAME_NAMES)
    res = core.feed_flame(p, hit)
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "nodross":
            return "你没有药渣。炼药失败会产药渣——去「dp炼制」试试。"
        if res.get("reason") == "diyan":
            return "帝炎不靠喂养，只认斗帝。"
        if res.get("reason") == "max":
            return f"【{hit}】已至 {res['lv']} 级圆满，再多药渣也烧不动了。"
        return "无可喂养。"
    if res["done"]:
        return f"🔥 药渣尽数化入【{hit}】，晋为 {res['lv']} 级！"
    return (f"🔥 投入 {res['give']} 份药渣，{hit} 距下一级还差 "
            f"{_fmt(res['left'])}。")


# ---------------------------------------------------------------------------
# 宝库 / 炼药 / 背包
# ---------------------------------------------------------------------------
async def cmd_shop(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    out = [f"🏦 随身商店　💰 {p['gold']}"]
    for it in core.shop_list():
        out.append(f"　{it['name']}　{it['price']} 金")
        out.append(f"　　{it['desc']}")
    out.append("")
    out.append("　用法：dp买 血灵草 2")
    return "\n".join(out)


async def cmd_buy(store, qq, args) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    args = (args or "").strip()
    if not args:
        return "用法：dp买 血灵草 2（发「dp宝库」看货）"
    parts = args.split()
    name = parts[0]
    n = 1
    if len(parts) > 1 and parts[1].isdigit():
        n = max(1, min(99, int(parts[1])))
    else:
        # 支持「dp买 血灵草2」这种没空格的
        for it in core.shop_list():
            if args.startswith(it["name"]):
                name = it["name"]
                rest = args[len(it["name"]):].strip()
                if rest.isdigit():
                    n = max(1, min(99, int(rest)))
                break
    res = core.shop_buy(p, name, n)
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "poor":
            return f"💰 金币不足：需要 {res['need']}，你有 {res['have']}。"
        return f"没有【{name}】这样货。发「dp宝库」看全部。"
    return f"✅ 购入【{res['name']}】×{res['n']}，花费 {res['total']} 金，余 {p['gold']}。"


async def cmd_recipes(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    alch = cfg.ALCHEMIST[int(p.get("alch", 0))]
    out = [f"⚗️ 炼药　{alch[0]}炼药师（成功率 {int(cfg.craft_rate(int(p.get('alch', 0))) * 100)}%）"]
    for grade in cfg.GRADE_ORDER:
        rows = [r for r in cfg.RECIPES if r["grade"] == grade]
        if not rows:
            continue
        out.append(f"【{grade}丹药】")
        for r in rows:
            mats = "、".join(f"{k}×{v}" for k, v in r["need"].items())
            ok = all(core.count_item(p, k) >= v for k, v in r["need"].items())
            mark = "✅" if ok else "　"
            out.append(f"　{mark}{r['name']}　（{mats}）")
            out.append(f"　　{r['desc']}")
    out.append("")
    out.append("　用法：dp炼制 回灵丹")
    return "\n".join(out)


async def cmd_craft(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：dp炼制 回灵丹（发「dp炼药」看全部丹方）"
    hit = next((n for n in cfg.RECIPE_NAMES if name in n or n in name), None)
    if not hit:
        return "无此丹方。发「dp炼药」看全部。"
    res = core.craft(p, hit)
    if res.get("reason") == "cd":
        await store.put_player(p)
        return f"丹炉尚温，需再候 {res['left']} 秒。"
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "mat":
            return "❌ 材料不足：" + "、".join(res["missing"]) + "\n　去「dp宝库」买药。"
        if res.get("reason") in ("fail",):
            msg = (f"💨 炼制失败（成功率 {int(res['rate'] * 100)}%）——\n"
                   f"　药渣 +{res['dross']}，可拿去「dp养火」。")
            if res.get("alch_up"):
                msg += f"\n　炼药经验积累，晋为【{res['alch_up']}炼药师】！"
            return msg
        return "炼制失败。"
    msg = f"⚗️ 丹成！【{hit}】×1（成功率 {int(res['rate'] * 100)}%）"
    if res.get("alch_up"):
        msg += f"\n　炼药经验积累，晋为【{res['alch_up']}炼药师】！"
    return msg


async def cmd_bag(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    items = core.bag_slots(p)
    out = [f"🎒 包裹（{len(items)}/24 格）　💰 {p['gold']}"]
    known = {r["name"]: r["desc"] for r in cfg.RECIPES}
    known.update({h["name"]: h["desc"] for h in cfg.HERBS})
    if not items:
        out.append("　空空如也。")
    for i, (name, n) in enumerate(items[:24]):
        out.append(f"　[{i + 1}] {name} ×{n}")
        if name in known:
            out.append(f"　　　{known[name]}")
    out.append("")
    out.append("　用法：dp服丹 回灵丹")
    return "\n".join(out)


async def cmd_use(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：dp服丹 回灵丹"
    hit = next((n for n in cfg.RECIPE_NAMES if name in n or n in name), None)
    if not hit:
        return "无此丹药。"
    res = core.use_item(p, hit)
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "none":
            return f"你没有【{hit}】。去「dp炼制 {hit}」或「dp宝库」看看。"
        return res.get("msg") or "无法服用。"
    return f"💊 服下【{hit}】，{res['msg']}"


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
async def cmd_tasks(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    out = ["📜 任务"]
    m = core.current_main(p)
    if m:
        cur = int(p.get("main_progress", 0))
        out.append(f"【主线·{m['name']}】{m['desc']}")
        out.append(f"　进度 {cur}/{m['target']}　{_bar(cur, m['target'])}")
        rw = m["reward"]
        out.append(f"　奖励：{rw['gold']} 金 / {rw['exp']} 修为 / 机缘 +{rw['luck']}")
    else:
        out.append("【主线】已全部完成。")
    t = p.get("task")
    if t:
        cur = int(t.get("progress", 0))
        out.append(f"【支线·{t['name']}】{t['desc']}")
        out.append(f"　进度 {cur}/{t['target']}　{_bar(cur, t['target'])}")
    else:
        out.append("【支线】无。发送「dp接任务」接一条。")
    return "\n".join(out)


async def cmd_task_accept(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.task_accept(p)
    await store.put_player(p)
    if not res.get("ok"):
        t = res["task"]
        return f"你手上还有支线未交：{t['name']}（{t.get('progress', 0)}/{t['target']}）"
    t = res["task"]
    return f"📌 接取支线【{t['name']}】：{t['desc']}（0/{t['target']}）"


async def cmd_task_submit(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.task_submit(p)
    if not res.get("ok"):
        if res.get("reason") == "none":
            return "你没有在做的支线。发送「dp接任务」接一条。"
        t = res["task"]
        return f"尚未完成：{t['name']}（{t.get('progress', 0)}/{t['target']}）"
    rw = res["reward"]
    return (f"✅ 支线【{res['task']['name']}】完成！\n"
            f"　金币 +{rw['gold']}　修为 +{rw['exp']}　机缘 +{rw['luck']}")


async def cmd_main_submit(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.main_submit(p)
    if not res.get("ok"):
        if res.get("reason") == "done":
            return "主线已全部完成，你已是此界巅峰。"
        t = res["task"]
        return f"主线未完成：{t['name']}（{res.get('progress', 0)}/{t['target']}）"
    rw = res["reward"]
    msg = (f"🏅 主线【{res['task']['name']}】完成！\n"
           f"　金币 +{rw['gold']}　修为 +{rw['exp']}　机缘 +{rw['luck']}")
    if res.get("next"):
        msg += f"\n📖 下一章：{res['next']['name']} —— {res['next']['desc']}"
    else:
        msg += "\n🎉 主线全部走完。"
    return msg


# ---------------------------------------------------------------------------
# 排行 / 抢 / 送
# ---------------------------------------------------------------------------
async def cmd_rank(store, gid) -> str:
    ps = await store.all_players(str(gid))
    if not ps:
        return "本群尚无人踏入斗气大陆，发送「dp入道」！"
    ps = core.rank(ps)
    out = ["🏆 本群战力榜（前 10）"]
    for i, q in enumerate(ps[:10], 1):
        out.append(f"{i}. {q['name']}　{cfg.full_realm(int(q['lv']))}　战力 {core.power(q)}")
    return "\n".join(out)


async def cmd_rob(store, qq, target) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target:
        return "用法：dp抢 @某人"
    vic = await store.get_player(str(target))
    if not vic:
        return "对方尚未入道。"
    if int(vic.get("gold", 0)) <= 0:
        return "对方囊中空空。"
    pa, pb = core.power(p), core.power(vic)
    import random as _r
    win = pa >= pb * (0.75 + 0.5 * _r.random())
    if win:
        amt = max(1, int(int(vic["gold"]) * 0.10))
        vic["gold"] = int(vic["gold"]) - amt
        p["gold"] = int(p.get("gold", 0)) + amt
        msg = f"😈 你以战力 {pa} 压过 {pb}，夺得 {amt} 金币。"
    else:
        amt = max(1, int(int(p["gold"]) * 0.10))
        p["gold"] = int(p.get("gold", 0)) - amt
        vic["gold"] = int(vic["gold"]) + amt
        msg = f"💢 反被击退（战力 {pa} vs {pb}），赔了 {amt} 金币。"
    await store.put_player(p)
    await store.put_player(vic)
    return msg


async def cmd_give(store, qq, target, amount) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target or not amount:
        return "用法：dp送 100 @某人"
    amt = int(amount)
    if amt <= 0:
        return "数量无效。"
    if int(p.get("gold", 0)) < amt:
        return "金币不足。"
    to = await store.get_player(str(target))
    if not to:
        return "对方尚未入道。"
    p["gold"] = int(p["gold"]) - amt
    to["gold"] = int(to.get("gold", 0)) + amt
    await store.put_player(p)
    await store.put_player(to)
    return f"🎁 你赠予对方 {amt} 金币，善缘结下。"
