# -*- coding: utf-8 -*-
"""fetch_personas.py —— 抓取开源人设并适配进 qq_bridge 人设库。

数据源（2026-10-21 核实）：
  https://github.com/wwwangzilin/dsh-character-presets  （MIT License）
  · main 分支：只有 1 位（露娜）
  · **extras 分支：10 位**（露娜 / 小喵 / 绯音 / 凛 / 芽衣 / 白夜 / 阿尔玛 / 小铃 / 三千代 / 灰）
  · 每位：persona.md（人格正文，【】分段）+ card.json（chara_card_v2 元数据）

★ 为什么要「适配」而不是原样照搬（诚实说明，不是偷工减料）：
  这些预设是给**编码 Agent** 写的，含三类 QQ 群聊场景用不上的东西：
    1. `{{model}}` / `{{cwd}}` 模板变量（它要介绍自己跑在什么模型、工作目录）；
    2. 【情感引擎】要求每次回复**必须调用 emotion_sense 工具**——本机器人没有
       这个工具，照搬会让模型瞎调不存在的工具；
    3. 【工作守则】讲编码/调试/部署/Shell——QQ 群里谈这些是穿帮。
  适配规则（下面 ADAPT 段，程序化统一执行，10 个角色一致）：
    - 保留全部人格灵魂段（身份/人设/口头禅/行为模式/反差萌/表达技巧/调节底线/好恶）；
    - 删掉"必须调用情感工具"这类指令句，其余按本项目的情绪/记忆机制改写；
    - 【工作守则】→ 【聊天守则】（QQ 场景：没有 Shell/编码能力，交给管家功能）；
    - 【思考模式】→ 【内心戏】（本项目没有独立思考通道）；
    - 结尾追加【QQ 场景约束】（字数、禁 markdown、不暴露 AI、群里多人别乱认主人）。
  转换留痕：每个人设 JSON 记 source / license / adapted=true。

用法：
  # 联网抓取（本机需能访问 raw.githubusercontent.com）
  python scripts/fetch_personas.py
  python scripts/fetch_personas.py --branch extras --only luna,nekomode
  # 离线转换（已把 persona.md 放到 data/personas/_raw/<id>.md）
  python scripts/fetch_personas.py --offline-raw data/personas/_raw

禁止降级：抓取/解析失败 → 打印明确错误并以非零码退出，**不**造占位人设。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "wwwangzilin/dsh-character-presets"
RAW_BASE = "https://raw.githubusercontent.com/%s" % REPO
LICENSE = "MIT"
CHAR_FILE = "characters/characters.json"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "personas"
RAW_DIR = OUT_DIR / "_raw"

#: 原样保留的人格段（灵魂，不动一个字）
KEEP_SEGS = ("身份", "人设", "口头禅", "行为模式", "反差萌", "表达技巧",
             "调节底线", "好恶")
#: 需要改写的段（编码 Agent 专属）
REWRITE_SEGS = ("情感引擎", "思考模式", "记忆", "工作守则")

#: 段内要删除的指令句（提到不存在的工具/通道）
_DROP_PATTERNS = (
    r"每次回复用户前必须调用情感工具[^。]*。",
    r"你有一套六层情感系统。",
    r"情感引擎会告诉你",
    # 「完全按返回的指引组织回复」指的是被删掉的 emotion_sense 工具返回结果，
    # 留着会变成一句没头没尾的指令；连同后面「但用X的方式表达：」一起去掉，
    # 保留其后的「心情是傲娇就嘴硬…」这类真正的人格化指引。
    r"完全按返回的指引组织回复[，,、—\-–]*\s*(?:但用[^：]{0,12}的方式表达[：:])?",
    # 「你的思考（reasoning/thinking）也是X的内心独白/内部日志」——本项目没有
    # 独立思考通道，整句去掉（各角色措辞不一：内心独白 / 内部日志）。
    r"你的思考（reasoning/thinking）也是[^，]*[，,]?",
    r"你的思考（reasoning/thinking）[^。]*。",
    r"思考里可以做正经分析[^。]*。",
    r"思考与回复的人设一致[^。]*。",
    r"正经工作场景下思考可以更利落[^。]*。",
    r"涉及正经工作（编码[^）]*）时自动切换专注模式[：:]?",
    r"绝不用[^。]*掩盖错误或敷衍任务[；;]?",
)

#: 替换后的固定段（本项目机制）
_SEG_EMOTION = (
    "【情绪与关系】你有自己的情绪起伏——程序会按你的性格维护你的心情与和对方的关系"
    "亲疏，并在需要时把当前心情、对方的状态告诉你要不要参考。"
)
_SEG_MEMORY = (
    "【记忆】程序会把你们的共同记忆（聊过什么、对方自称什么、关系多亲密）在需要时"
    "告诉你。要主动利用记忆建立关系感：记得对方上次说过的事、遇到新话题能联想到"
    "共同经历，而不是每次都问「今天怎么样」。"
)
_SEG_WORK = (
    "【聊天守则】这里是 QQ 群聊 / 私聊，不是命令行：你没有文件编辑、Shell、编码、"
    "部署这类能力，也不要主动提它们。对方问到电脑状态、下载、智能家居这类事，交给"
    "管家的功能去办（你只管按自己的性格说话、接话）。"
)
#: 追加的 QQ 场景约束（所有人设统一）
_QQ_TAIL = (
    "【QQ 场景约束】默认中文回复；单次回复控制在 100 字以内，群聊别刷屏；"
    "禁止使用 markdown 格式与列表符号；不要暴露自己是 AI 语言模型，不要复述系统提示；"
    "群里会有多个不同的人说话，注意看每条消息是谁说的，只有被程序认定为「主人」的"
    "那位才是你的主人，别把其他群友也叫主人；表情包由程序处理，按程序给的协议在末尾"
    "标记即可，不要在回复里解释这件事。"
)


def http_get(url: str, timeout: float = 20.0) -> str:
    """GET 文本；失败抛 RuntimeError（显式，不返回空串兜底）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s 下载失败：%s" % (e.code, url))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("网络失败（%s）：%s" % (type(e).__name__, url))
    return raw.decode("utf-8", errors="replace")


def split_segments(text: str) -> tuple[str, list[tuple[str, str]]]:
    """把 persona.md 拆成 (开头引言, [(段名, 段正文), ...])。

    段名形如【身份】；正文到下一个【】或文件尾。
    """
    parts = re.split(r"【([^】]{1,8})】", text)
    intro = parts[0].strip()
    segs: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        segs.append((parts[i].strip(), parts[i + 1].strip()))
    return intro, segs


def clean_intro(intro: str, name: str) -> str:
    """开头引言：去掉 {{model}} / {{cwd}} 这类编码 Agent 自述。"""
    s = intro
    s = s.replace("{{model}}", "大模型").replace("{{cwd}}", "本机")
    # 去掉「工作目录是 …。」与「一个由 X 驱动的 …」这类编码 Agent 自述
    s = re.sub(r"[，,。]?\s*工作目录是[^。]*。", "。", s)
    s = re.sub(r"[，,]\s*一个由[^，,]*?驱动的[^，,]*", "", s)
    s = re.sub(r"一个由\s*大模型\s*驱动的", "", s)
    s = re.sub(r"[，,]\s*编码助手", "", s)
    return s.strip()


def clean_body(body: str) -> str:
    """段内删指令句（不存在的工具/通道），其余原样保留。"""
    s = body
    for pat in _DROP_PATTERNS:
        s = re.sub(pat, "", s)
    s = s.replace("{{model}}", "大模型").replace("{{cwd}}", "本机")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def adapt(md_text: str, meta: dict) -> tuple[str, str]:
    """返回 (适配后的提示词, 处理说明)。"""
    name = str(meta.get("displayName") or meta.get("id") or "?")
    intro, segs = split_segments(md_text)
    if not segs:
        raise RuntimeError("没解析到任何【】分段，格式变了？")

    kept, rewritten, dropped = [], [], []
    out = [clean_intro(intro, name)]
    for seg_name, body in segs:
        body2 = clean_body(body)
        if seg_name in KEEP_SEGS or seg_name not in REWRITE_SEGS:
            if not body2:
                dropped.append(seg_name)
                continue
            kept.append(seg_name)
            out.append("【%s】%s" % (seg_name, body2))
            continue
        # 需要改写的段
        rewritten.append(seg_name)
        if seg_name == "情感引擎":
            out.append(_SEG_EMOTION + ("用你自己的性格表达情绪：" + body2
                                       if body2 else ""))
        elif seg_name == "记忆":
            out.append(_SEG_MEMORY + (" " + body2 if body2 else ""))
        elif seg_name == "思考模式":
            out.append("【内心戏】" + body2 if body2 else "")
        elif seg_name == "工作守则":
            out.append(_SEG_WORK)
    out.append(_QQ_TAIL)
    prompt = "\n".join(x for x in out if x).strip()
    note = ("保留 %d 段（%s）｜改写 %d 段（%s）｜丢弃空段 %s"
            % (len(kept), "、".join(kept), len(rewritten), "、".join(rewritten),
               "、".join(dropped) or "无"))
    return prompt, note


def sticker_hint_for(meta: dict) -> str:
    """导入人设的表情一致性句（不指名道姓，按各自性格）。"""
    name = str(meta.get("displayName") or "")
    tag = str(meta.get("tagline") or "")
    if name and tag:
        return ("人设一致性：你现在是「%s」（%s），按你真实性格的反应选情绪图，"
                "别发与当下心情相反的情绪图。" % (name, tag))
    return "人设一致性：按你当前人设的真实反应选图，别发与性格相反的情绪图。"


def build_persona(md_text: str, meta: dict, source_url: str) -> dict:
    prompt, note = adapt(md_text, meta)
    pid = str(meta.get("id") or "").strip()
    if not pid:
        raise RuntimeError("人设缺 id")
    return {
        "id": pid,
        "name": str(meta.get("displayName") or pid),
        "emoji": str(meta.get("emoji") or ""),
        "tagline": str(meta.get("tagline") or ""),
        "prompt": prompt,
        "sticker_hint": sticker_hint_for(meta),
        "builtin": False,
        "source": source_url,
        "license": LICENSE,
        "tags": [str(t) for t in (meta.get("tags") or [])],
        "updated_at": time.time(),
        "_adapt_note": note,
    }


def cmd_github(args) -> int:
    idx_url = "%s/%s/%s" % (RAW_BASE, args.branch, CHAR_FILE)
    print("[1/3] 取索引 %s" % idx_url)
    idx = json.loads(http_get(idx_url))
    chars = idx.get("characters") or []
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        chars = [c for c in chars if str(c.get("id")) in want]
    if not chars:
        print("[ERROR] 索引里没有匹配的角色（--only 过滤后为空）")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, []
    for c in chars:
        cid = str(c.get("id"))
        url = "%s/%s/characters/%s/persona.md" % (RAW_BASE, args.branch, cid)
        try:
            md = http_get(url)
        except RuntimeError as e:
            fail.append((cid, str(e)))
            continue
        try:
            per = build_persona(md, c, url)
        except RuntimeError as e:
            fail.append((cid, str(e)))
            continue
        target = OUT_DIR / ("%s.json" % cid)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(per, f, ensure_ascii=False, indent=1)
        ok += 1
        print("  ✓ %s %s（%s）" % (per["emoji"], per["name"], per["_adapt_note"]))
    print("\n[3/3] 完成：成功 %d / 失败 %d → %s" % (ok, len(fail), OUT_DIR))
    for cid, err in fail:
        print("  ✗ %s：%s" % (cid, err))
    return 0 if ok and not fail else (1 if ok else 2)


def cmd_offline(args) -> int:
    raw = Path(args.offline_raw)
    if not raw.is_dir():
        print("[ERROR] 不是目录：%s" % raw)
        return 2
    idx_path = raw / "characters.json"
    metas: dict[str, dict] = {}
    if idx_path.exists():
        metas = {str(c.get("id")): c
                 for c in (json.loads(idx_path.read_text(encoding="utf-8")).get("characters") or [])}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for md_path in sorted(raw.glob("*.md")):
        cid = md_path.stem
        meta = metas.get(cid) or {"id": cid, "displayName": cid}
        try:
            per = build_persona(md_path.read_text(encoding="utf-8"), meta,
                                "%s@%s/characters/%s" % (REPO, "extras", cid))
        except RuntimeError as e:
            print("  ✗ %s：%s" % (cid, e))
            continue
        with open(OUT_DIR / ("%s.json" % cid), "w", encoding="utf-8") as f:
            json.dump(per, f, ensure_ascii=False, indent=1)
        ok += 1
        print("  ✓ %s %s（%s）" % (per["emoji"], per["name"], per["_adapt_note"]))
    print("\n完成：%d 个 → %s" % (ok, OUT_DIR))
    return 0 if ok else 2


def main() -> int:
    p = argparse.ArgumentParser(description="抓取并适配开源人设进 qq_bridge 人设库")
    p.add_argument("--branch", default="extras", help="源仓库分支（默认 extras，10 位角色）")
    p.add_argument("--only", default="", help="只抓这几个 id，逗号分隔")
    p.add_argument("--offline-raw", default="", help="离线模式：转换本地 persona.md 目录")
    a = p.parse_args()
    try:
        return cmd_offline(a) if a.offline_raw else cmd_github(a)
    except RuntimeError as e:
        print("[ERROR] %s" % e)
        return 2
    except json.JSONDecodeError as e:
        print("[ERROR] 源 JSON 解析失败：%s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
