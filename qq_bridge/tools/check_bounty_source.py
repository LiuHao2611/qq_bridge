#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赏金卡数据源闸门：wf.wiki RSC payload 必须解析出 bountyBoard / groups。

背景（2026-11-08 用户纠正）：「奖励内容呢，而且任务内容和别人机器人的也不一样，
你先搞清楚别人的数据来源再开始动手」。实证结论：别人（Warframe Rabbit）与本项目
**同源**，都是 worldstate.wf.wiki 的 Next.js RSC payload：
  * ``bountyBoard`` → 三大开放世界 jobs[].rewards（MOD / 债券 / 部件蓝图）
                    + objective / minLevel / maxLevel / stages / isVault
  * ``groups``      → 扎里曼 / 圣所 / 六人组的 node / mission / challenge / ally
  * ``bounties``    → 合一众轮换 rot / rotRewards / vaultRot / vaultRewards
中文任务名走 WFA_Lexicon 同源 ``dict.zh.json``，不是手写词库。

本项目的真实事故：``warframe._wfwiki_payload`` 用
``"".join(blocks).replace('\\"', '"')`` 反解 RSC 字符串，会把合法转义破坏成
非法 JSON，``raw_decode`` 抛错后被 ``except: pass`` **静默吞掉** →
``bountyBoard`` 永远为空 → MOD/债券奖励整段消失；同时派系任务改用了硬编码
``_BOUNTY_GROUPS`` 旧快照，所以任务名/目标行也和别人对不上。

用法：
  python tools/check_bounty_source.py --html tmp/probe_wfwiki_live.html
  python tools/check_bounty_source.py --selftest
退出码：0=数据源完整；1=缺字段（或注入未被拦住）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_BLOCK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
_KEYS = ("bountyBoard", "groups", "bounties")


def decode_rsc(html: str) -> str:
    """RSC block 是 **JS 字符串字面量**，按 JSON 字符串解码才是原始 payload。

    禁止用 ``.replace('\\"', '"')``：那会把 ``"name":"\\"秩序\\"残留物"`` 之类
    合法转义变成裸引号，整块 JSON 立刻非法。
    """
    blocks = _BLOCK_RE.findall(html)
    return "".join(json.loads('"' + b + '"') for b in blocks)


def decode_legacy(html: str) -> str:
    """旧错误写法（仅供 --selftest 注入，生产禁止使用）。"""
    return "".join(_BLOCK_RE.findall(html)).replace('\\"', '"')


def parse(html: str, decoder=decode_rsc) -> dict:
    text = decoder(html)
    out: dict = {}
    dec = json.JSONDecoder()
    for key in _KEYS:
        i = text.find('"%s":' % key)
        if i < 0:
            continue
        try:
            body = text[i + len('"%s"' % key):].lstrip()
            if body.startswith(":"):
                body = body[1:].lstrip()
            v, _ = dec.raw_decode(body)
            out[key] = v
        except Exception:
            # 不能静默吞：交给 check() 判为缺字段
            pass
    return out


def reward_names(board: dict) -> list[str]:
    names: list[str] = []
    for j in board.get("jobs") or []:
        for r in j.get("rewards") or []:
            nm = r.get("name")
            if nm and nm not in names:
                names.append(nm)
    return names


def check(pl: dict) -> list[str]:
    problems: list[str] = []
    bb = pl.get("bountyBoard")
    if not isinstance(bb, list) or not bb:
        problems.append("bountyBoard 缺失/为空 —— MOD、债券、部件蓝图奖励会整段消失")
        bb = []
    tags = {b.get("tag") for b in bb if isinstance(b, dict)}
    for tag in ("CetusSyndicate", "SolarisSyndicate", "EntratiSyndicate"):
        if tag not in tags:
            problems.append("bountyBoard 缺少 %s" % tag)
    cetus = next((b for b in bb if isinstance(b, dict) and b.get("tag") == "CetusSyndicate"), {})
    solaris = next((b for b in bb if isinstance(b, dict) and b.get("tag") == "SolarisSyndicate"), {})
    cn = reward_names(cetus)
    if not any("Cards/Images" in str(r.get("icon") or "")
               for j in cetus.get("jobs") or [] for r in j.get("rewards") or []):
        problems.append("地球赏金 rewards 没有 MOD 卡图（MOD奖励轮换会消失）")
    if len(cn) < 10:
        problems.append("地球赏金 reward 名称过少(%d)，疑似解析不完整" % len(cn))
    sn = reward_names(solaris)
    bonds = [n for n in sn if "债券" in n]
    if len(bonds) < 5:
        problems.append("金星赏金债券不足 5 种(%d)：%s" % (len(bonds), bonds))

    groups = pl.get("groups")
    if not isinstance(groups, list) or len(groups) < 3:
        problems.append("groups 缺失/不足 3 组 —— 派系任务与目标行会退回硬编码旧快照")
        groups = []
    gtags = {g.get("tag") for g in groups if isinstance(g, dict)}
    for tag in ("ZarimanSyndicate", "EntratiLabSyndicate", "HexSyndicate"):
        if tag not in gtags:
            problems.append("groups 缺少 %s" % tag)
    for g in groups:
        if not isinstance(g, dict):
            continue
        for f in ("node", "mission", "challenge"):
            if not any(j.get(f) for j in g.get("jobs") or []):
                problems.append("groups[%s] 的 jobs 缺 %s 字段" % (g.get("tag"), f))

    bounties = pl.get("bounties")
    if not isinstance(bounties, dict) or not bounties.get("rotRewards"):
        problems.append("bounties.rotRewards 缺失 —— 合一众轮换奖励会消失")
    return problems


def _sample_html(*, break_escapes: bool = False, drop_groups: bool = False,
                 drop_rewards: bool = False) -> str:
    """构造最小 RSC 样本；break_escapes 复现旧写法会炸的转义引号。"""
    cetus_jobs = [{
        "objective": "捕获", "minLevel": 5, "maxLevel": 15, "stages": 3,
        "rewards": [{"name": "压迫点", "icon": "https://x/Cards/Images/A.jpg", "count": 1},
                    {"name": "收割螺旋", "icon": "https://x/Cards/Images/B.jpg", "count": 1},
                    {"name": "预言 探求", "icon": "https://x/Cards/Images/C.jpg", "count": 1},
                    {"name": "角斗士 威猛", "icon": "https://x/Cards/Images/D.jpg", "count": 1},
                    {"name": "私法 军备", "icon": "https://x/Cards/Images/E.jpg", "count": 1},
                    {"name": "预言 神密", "icon": "https://x/Cards/Images/F.jpg", "count": 1},
                    {"name": "角斗士 决心", "icon": "https://x/Cards/Images/G.jpg", "count": 1},
                    {"name": "私法 活力", "icon": "https://x/Cards/Images/H.jpg", "count": 1},
                    {"name": "猎鹰俯击", "icon": "https://x/Cards/Images/I.jpg", "count": 1},
                    {"name": "现金", "icon": "$undefined", "count": 1500}],
    }]
    if drop_rewards:
        cetus_jobs[0]["rewards"] = []
    solaris_jobs = [{"objective": "搜救", "minLevel": 5, "maxLevel": 15, "stages": 3,
                     "rewards": [{"name": "%s债务债券" % n, "count": 2}
                                 for n in "培训 庇护 医疗 预支 家族".split()]}]
    board = [
        {"tag": "CetusSyndicate", "name": "Ostron", "jobs": cetus_jobs},
        {"tag": "SolarisSyndicate", "name": "索拉里斯", "jobs": solaris_jobs},
        {"tag": "EntratiSyndicate", "name": "英择谛",
         "jobs": [{"objective": "刺杀", "rewards": [{"name": "内融核心"}]}]},
    ]
    groups = [] if drop_groups else [
        {"tag": "ZarimanSyndicate", "name": "坚守者",
         "jobs": [{"node": "哈拉科防线", "mission": "歼灭", "challenge": "唤醒一个休眠的天使"}]},
        {"tag": "EntratiLabSyndicate", "name": "科维兽",
         "jobs": [{"node": "恶涌", "mission": "刺杀", "challenge": "摧毁 60 个装饰物"}]},
        {"tag": "HexSyndicate", "name": "六人组",
         "jobs": [{"node": "至日广场", "mission": "防御", "challenge": "从上方击杀 50 名敌人"}]},
    ]
    payload = {
        "bountyBoard": board,
        "groups": groups,
        "bounties": {"rot": "B", "rotRewards": [{"name": "Caliban 机体"}],
                     "vaultRot": "A", "vaultRewards": [{"name": "孢丝感染枪 枪管"}]},
    }
    if break_escapes:
        # 真实 payload 里出现过 `"name":"\"秩序\"残留物"` 这类带转义引号的中文名
        board[2]["jobs"][0]["rewards"][0]["name"] = '"秩序"残留物'
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    block = json.dumps(text, ensure_ascii=False)[1:-1]  # JS 字符串字面量内容
    return 'self.__next_f.push([1,"%s"])' % block


def selftest() -> int:
    good = _sample_html()
    p = check(parse(good))
    if p:
        print("SELFTEST FAIL：正常 payload 被误判：%s" % p[:2])
        return 1
    print("SELFTEST OK：正常 payload → 数据源完整")
    cases = [
        ("旧写法 replace 转义（本次真实事故）",
         dict(html=_sample_html(break_escapes=True), decoder=decode_legacy), "bountyBoard"),
        ("groups 丢失", dict(html=_sample_html(drop_groups=True)), "groups"),
        ("地球 rewards 丢失", dict(html=_sample_html(drop_rewards=True)), "MOD 卡图"),
    ]
    for name, kw, expect in cases:
        html = kw.pop("html")
        decoder = kw.pop("decoder", decode_rsc)
        p = check(parse(html, decoder=decoder))
        if not any(expect in x for x in p):
            print("SELFTEST FAIL：注入「%s」未被拦住，problems=%s" % (name, p[:3]))
            return 1
        print("SELFTEST OK：注入「%s」→ %s" % (name, next(x for x in p if expect in x)[:80]))
    print("SELFTEST PASS (3/3 注入全部被拦下)")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    path = None
    if "--html" in argv:
        path = Path(argv[argv.index("--html") + 1])
    if path is None:
        path = ROOT / "tmp" / "probe_wfwiki_live.html"
    if not path.is_file():
        print("ERROR：找不到 wf.wiki 快照 %s（先跑 tmp/probe_bounty_sources.py 或 --html 指定）" % path)
        return 2
    pl = parse(path.read_text(encoding="utf-8"))
    print("payload keys:", {k: len(v) if isinstance(v, (list, dict)) else "-" for k, v in pl.items()})
    p = check(pl)
    if p:
        print("FAIL (%d)：" % len(p))
        for x in p:
            print("  - " + x)
        return 1
    print("PASS：bountyBoard / groups / bounties 齐全，MOD 与债券奖励可还原")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
