# -*- coding: utf-8 -*-
"""intent（意图路由，设计文档 §3.3 + §6 指令集）。

输入：标准化 QQEvent + 会话态 Session
输出：Route（要么指向平台 API，要么是直接回复的文本）

验收（§3.3）：
  纯文本→文生图；图片+文本→图生图；/video→视频；未知指令→/help。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config, get_logger, jlog
from ..dispatcher import QQEvent
from ..session import Session
from .. import gallery

log = get_logger()

# 女仆暗号：自然语言口令，替代难输入的斜杠命令（2026-09-04 用户决策）。
# mode="exact" 仅整句相等触发（避免误吞正常对话）；mode="prefix" 暗号后接描述。
MAID_ALIASES = [
    ("你能干啥", "/help", "exact"),
    ("你会什么", "/help", "exact"),
    ("帮帮忙", "/help", "exact"),
    ("能干嘛", "/help", "exact"),
    ("你咋样", "/status", "exact"),
    ("在忙啥", "/status", "exact"),
    ("任务进度", "/status", "exact"),
    ("你还好吗", "/status", "exact"),
    ("资源", "/hw", "exact"),
    ("硬件", "/hw", "exact"),
    ("占用", "/hw", "exact"),
    ("硬件占用", "/hw", "exact"),
    ("看看资源", "/hw", "exact"),
    ("剩几次", "/quota", "exact"),
    ("还有几次", "/quota", "exact"),
    ("今天额度", "/quota", "exact"),
    ("切视频", "/mode video", "exact"),
    ("换视频模式", "/mode video", "exact"),
    ("切生图", "/mode image", "exact"),
    ("换图片模式", "/mode image", "exact"),
    ("在吗", "/ping", "exact"),
    ("在不在", "/ping", "exact"),
    ("你在吗", "/ping", "exact"),
    ("醒醒", "/ping", "exact"),
    # 自绘图片主题切换（2026-09-07：深色霓虹 / 极简白底 / 按时段自动，热生效）
    ("换深色", "/theme neon", "exact"),
    ("深色模式", "/theme neon", "exact"),
    ("夜间模式", "/theme neon", "exact"),
    ("换浅色", "/theme light", "exact"),
    ("浅色模式", "/theme light", "exact"),
    ("白天模式", "/theme light", "exact"),
    ("自动主题", "/theme auto", "exact"),
    # ---- 任务中心（2026-09-08：监控 / 下发 / 查看 / 排队 / 预览）----
    # 注意顺序：**exact 必须排在 prefix 前面**，否则「任务进度」会被「任务」prefix 吞掉。
    ("我的任务", "/task", "exact"),
    ("我投的图", "/task", "exact"),
    ("最近任务", "/recent", "exact"),
    ("大家的任务", "/recent", "exact"),
    ("看看队列", "/queue", "exact"),
    ("排队情况", "/queue", "exact"),
    ("队列状态", "/queue", "exact"),
    ("清空队列", "/clear", "exact"),
    ("监控打开", "/monitor on", "exact"),
    ("打开监控", "/monitor on", "exact"),
    ("监控关闭", "/monitor off", "exact"),
    ("关闭监控", "/monitor off", "exact"),
    # ---- 监听主项目生图任务（2026-09-13：从推理监控页 /tasks 获取）----
    ("主项目任务", "/mtasks", "exact"),
    ("后台任务", "/mtasks", "exact"),
    ("后台在跑啥", "/mtasks", "exact"),
    ("在跑啥", "/mtasks", "exact"),
    ("在干嘛", "/mtasks", "exact"),
    ("主项目在画啥", "/mtasks", "exact"),
    # ---- 视频 / 扩图 / 放大 ----
    ("给我拍个视频", "/video", "prefix"),
    ("生成视频", "/video", "prefix"),
    ("高清放大", "/upscale", "prefix"),
    ("放大这张", "/upscale", "prefix"),
    ("往左扩", "/outpaint 左", "prefix"),
    ("往右扩", "/outpaint 右", "prefix"),
    ("往上扩", "/outpaint 上", "prefix"),
    ("往下扩", "/outpaint 下", "prefix"),
    # ---- 任务中心 prefix 型（必须放在所有 exact 之后）----
    ("任务", "/task", "prefix"),
    ("队列", "/queue", "prefix"),
    ("取消", "/cancel", "prefix"),
    ("插队", "/promote", "prefix"),
    ("再看", "/again", "prefix"),
    ("重发", "/again", "prefix"),
    ("监控", "/monitor", "prefix"),
    # ---- 监听主项目生图任务（2026-09-13）----
    ("生图监听", "/mlisten", "prefix"),
    ("监听生图", "/mlisten", "prefix"),
    # ---- 历史画廊（2026-09-11）----
    ("历史", "/gallery", "prefix"),
    ("图库", "/gallery", "prefix"),
    ("相册", "/gallery", "prefix"),
    ("我的图", "/gallery", "prefix"),
    # ---- 画廊翻页口令（2026-09-13 修复）----
    # 为什么 cmd 里要显式带上这个词：_translate_maid 命中 prefix/exact 暗号后会把
    # 暗号本身"吃掉"，若只映射成 "/gallery"，参数会变成空串 → _cmd_gallery 拿不到
    # 导航词 → 永远停在「第 1 页」（这正是"发下一页没反应/没翻页"的第三层原因）。
    # 词表统一取自 gallery.NAV_WORDS（与 dispatcher 免 @ 放行、_cmd_gallery 增量同源）。
    # 必须 exact：翻页是短口令，prefix 会误吞"下一页要画猫"这类正常句子。
    # ---- 签到模块（2026-10-30）：自然语言入口 ----
    ("签到", "/签到", "exact"),
    ("签到榜", "/签到 榜", "exact"),
    ("签到排行榜", "/签到 榜", "exact"),
    ("补签", "/签到 补", "exact"),
    ("补个签", "/签到 补", "exact"),
    ("签到规则", "/签到 规则", "exact"),
] + [(w, "/gallery " + w, "exact") for w in gallery.NAV_WORDS]


def _translate_maid(text: str) -> str | None:
    """把女仆暗号翻译为标准斜杠命令；非暗号返回 None。

    精确匹配（去尾标点/空格）优先，其次前缀匹配（暗号后接描述，如视频/扩图）。
    """
    t = (text or "").strip().rstrip("？?。.!！~～，, ").strip().lower()
    for alias, cmd, mode in effective_maid_aliases():
        a = alias.lower()
        if mode == "exact":
            if t == a:
                return cmd
        elif t.startswith(a):
            return cmd + t[len(a):]
    # 2026-09-16：自定义口令（管理页 CUSTOM_MAID_ALIASES，热生效，不覆盖内置）
    for alias, cmd, mode in parse_custom_aliases(
            getattr(Config, "CUSTOM_MAID_ALIASES", "") or ""):
        a = alias.lower()
        if mode == "exact":
            if t == a:
                return cmd
        elif t.startswith(a):
            return cmd + t[len(a):]
    return None


def parse_custom_aliases(raw: str) -> list:
    """解析管理页自定义口令：每行 `口令 => /命令`（exact）；口令以 ! 结尾 → prefix。

    非法行静默跳过（记日志），返回 [(alias, cmd, mode)]。
    """
    out = []
    for line in (raw or "").replace("\r", "").split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=>" not in s:
            log.warning(jlog("custom_alias_skip", line=s[:60]))
            continue
        phrase, cmd = (p.strip() for p in s.split("=>", 1))
        if not phrase or not cmd.startswith("/"):
            log.warning(jlog("custom_alias_skip", line=s[:60]))
            continue
        mode = "prefix" if phrase.endswith("!") else "exact"
        if mode == "prefix":
            phrase = phrase[:-1].strip()
        if phrase:
            out.append((phrase, cmd, mode))
    return out


_EFF_MAID = ["", None]  # [raw, parsed] 缓存


def effective_maid_aliases() -> list:
    """内置女仆暗号实际生效集：.env 配置了 MAID_ALIASES（管理页编辑写回）则用配置，
    否则用代码内置默认。配置文本未变不重复解析。"""
    raw = getattr(Config, "MAID_ALIASES", "") or ""
    if _EFF_MAID[0] == raw and _EFF_MAID[1] is not None:
        return _EFF_MAID[1]
    parsed = parse_custom_aliases(raw) if raw.strip() else list(MAID_ALIASES)
    _EFF_MAID[0] = raw
    _EFF_MAID[1] = parsed
    return parsed


def all_intents() -> dict:
    """全部触发口令清单（管理页 /api/qq/intents 展示用，只读）。"""
    groups = []

    def _push(name, items):
        if items:
            groups.append({"name": name, "items": items})

    _push("女仆暗号 · 整句触发", [
        {"phrase": a, "command": cmd, "mode": m}
        for a, cmd, m in effective_maid_aliases() if m == "exact"])
    _push("女仆暗号 · 前缀触发", [
        {"phrase": a, "command": cmd, "mode": m}
        for a, cmd, m in effective_maid_aliases() if m == "prefix"])
    kw = []
    for key, label in (("DRAW_KEYWORDS", "生图触发词"),
                        ("TEST_KEYWORDS", "测试对话词")):
        for w in (getattr(Config, key, ()) or ()):
            kw.append({"phrase": w, "command":
                       ("<提示词>" if key == "DRAW_KEYWORDS" else "固定回复"),
                       "config_key": key, "label": label})
    _push("可配置关键词（管理页「关键词与话术」可改）", kw)
    # 游戏攻略口令（game_tools 自然语言口令：前缀触发「口令+参数」，按命令聚合展示）
    try:
        from ..game_tools import GUIDE_TITLES as _GT, NATURAL_CMDS as _NCMDS
        _gt_items = []
        for _p in _NCMDS:
            _t = _GT.get(_p)
            if _t and _t != "游戏工具箱":
                _gt_items.append({"phrase": _p, "command": _t, "mode": "prefix"})
        _push("游戏攻略口令（前缀触发：口令+参数）", _gt_items)
    except Exception:  # game_tools 未加载时跳过，口令清单不受影响
        pass

    _push("自定义口令（管理页「自定义触发口令」可改）", [
        {"phrase": a, "command": cmd, "mode": m}
        for a, cmd, m in parse_custom_aliases(
            getattr(Config, "CUSTOM_MAID_ALIASES", "") or "")])
    return {"groups": groups,
            "total": sum(len(g["items"]) for g in groups),
            "custom_raw": getattr(Config, "CUSTOM_MAID_ALIASES", "") or "",
            "maid_raw": getattr(Config, "MAID_ALIASES", "") or ""}


HELP_TEXT = """哟，本喵能干的活儿，直接说就行（别老打 / 了，本喵听得懂人话）：
· 直接发文字 → 文生图（别指望我陪聊）
· 发图片 + 文字 → 图生图（改你的图）
· 高清放大 / 放大这张 [+图] → 高清放大
· 往左扩 / 往右扩 / 往上扩 / 往下扩 [+图] → 扩图
· 给我拍个视频 描述 → 生成视频
· 切视频 / 切生图 → 切换默认模式
· /ai on|off → AI 陪聊模式（仅主人；开启后这个群里所有消息都由在线大模型纯文字回复；说「玩成语接龙」「来个海龟汤」开局文字游戏，每轮倒计时超时会催你；冷场了本喵还会自己起话头；/ai game 看游戏列表；/ai sticker 看表情库；/ai proactive 主动发言开关）
· 你咋样 / 任务进度 → 看当前任务进度（发进度条图）
· 资源 / 硬件占用 → 看本地 CPU/内存/磁盘/GPU 实时占用
· 剩几次 / 今天额度 → 看今日还剩几次（发配额图）
· 换深色 / 换浅色 / 自动主题 → 切换出图配色（深色霓虹 / 极简白底）
· 你能干啥 → 把这份破清单再发一遍
· 在吗 → 测活（看本喵死没死）

· 我的任务 / 任务 <号> → 查任务进度与结果（顺带显示主项目后台在跑啥；「再看 <号>」重发结果图）
· 历史 / 图库 → 翻出图历史（九宫格缩略图，回复编号看原图；「历史 文生图」「历史 图生图」分类，「历史 2」或「下一页」翻页）
· 队列 → 看排队情况（本地显卡独占，一次只跑一张）
· 取消 <号> → 撤掉你投的任务（插队 / 清空队列只有主人能）
· 监控 开 / 关 → 任务失败或超时时私聊提醒（仅主人，默认关）
· 主项目任务 / 后台 → 看主项目后台正在跑/排队/刚生成的生图任务（含结果图）
· 生图监听 开 / 关 → 主项目开始生图、生图完成时私聊提醒你（仅主人，默认关）

· 角色 <名> / 配队 <角色> / 养成 <角色> → 二游攻略（原神/星铁/绝区零/鸣潮/异环：图鉴、配队、毕业路线，回复发图片）
· 副本 [游戏] → 当期深境螺旋 / 混沌回忆 / 危局强袭 / 逆境深塔
· 抽签 / 猜数字 / 24点 / 答题 / 接龙 → 摸鱼小游戏（加 # 前缀也认，如 #配队 胡桃）

生图还能带参数：描述后面加 --尺寸 1024 --比例 16:9 --数量 4 --步数 20
（本地显卡 6GB，数量最多 8 张，多了本喵要炸）

私聊冷却 10s，群聊 5s，提示词限 300 字。超了别怪本喵不理你哈。"""

# 扩图方向 → /t2i/edit 的 left/top 参数偏移（文档 §6 未规定数值，按常规取 128）
OUTPAINT_OFFSETS = {
    "左": {"left": 128, "top": 0},
    "右": {"left": -128, "top": 0},
    "上": {"left": 0, "top": 128},
    "下": {"left": 0, "top": -128},
}


# ===========================================================================
# 生图全参解析（2026-09-08：QQ 任务中心 P1）
# ---------------------------------------------------------------------------
# 目标：让 QQ 下发的参数与 /txt2img 页面**严格对齐**，避免「QQ 与页面参数漂移」。
# 形态：`生图 <提示词> --尺寸 1024 --比例 16:9 --数量 4`
# 解析：按 **已知 key** 从文本中抽取 `--key value` 并移除，剩下的即提示词。
#   只认已知 key + 要求 value 非空 → 提示词里出现 `--` 不会被误吞。
# ===========================================================================
#: 中文名 / 英文名 → 提交给 /qq/image-generate 的字段名
DRAW_ARG_KEYS = {
    "尺寸": "resolution", "size": "resolution", "分辨率": "resolution",
    "比例": "ratio", "ratio": "ratio",
    "步数": "steps", "steps": "steps",
    "数量": "count", "count": "count", "张数": "count",
    "种子": "seed", "seed": "seed",
    "模板": "template_id", "tpl": "template_id", "template": "template_id",
    "负向": "negative", "neg": "negative", "负面": "negative",
    "风格": "style", "style": "style",
}
#: 需要转成 int 的字段
_INT_ARGS = ("steps", "count", "seed")
#: 参数中文名（报错文案用）
_ARG_LABELS = {
    "resolution": "尺寸", "ratio": "比例", "steps": "步数", "count": "数量",
    "seed": "种子", "template_id": "模板", "negative": "负向", "style": "风格",
}
#: 合法比例（与 /txt2img 页面的 ratios 保持一致）
VALID_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")


def parse_draw_args(text: str) -> tuple[str, dict, str]:
    """把 `提示词 --尺寸 1024 --数量 4` 拆成 (prompt, params, error)。

    - 未知 `--xxx` 一律**留在提示词里**（不报错、不吞字）——提示词可能含任意字符；
    - 已知 key 但缺 value → 报错并给出用法（不能静默忽略，否则用户以为改了尺寸）；
    - `--数量` 限制 1~8（本地 6GB 显卡，太多会 OOM）；`--步数` 1~150。

    返回 (prompt, params, error)；error 非空时调用方应直接回复错误文案。
    """
    import re
    raw = (text or "").strip()
    if not raw:
        return "", {}, "光说「生图」不给词，本喵画什么？"

    params: dict = {}
    # 形如 --尺寸 1024 / --比例 16:9 / --种子 -1
    # value 两个分支（**顺序敏感**：数字分支必须带 `(?=\s|$)` 边界）：
    #   1) `-?数字` —— 允许负数（种子 -1 是常用值）；必须后接空白或结尾，
    #      否则 `--比例 16:9` 会被截成 "16"（真踩过：比例校验报「不支持 16」）。
    #   2) `[^\s-]开头` —— 普通值（含 16:9 / tpl_abc / 中文），但**首字符不能是 -**
    #      （否则 `--尺寸 --比例 16:9` 会把 `--比例` 当成尺寸的值，静默吞掉提示词）。
    pat = re.compile(r"--([^\s\-‐-―]+)\s+(-?\d+(?:\.\d+)?(?=\s|$)|[^\s\-][^\s]*)")

    def _take(m) -> str:
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        field = DRAW_ARG_KEYS.get(key) or DRAW_ARG_KEYS.get(key.lower())
        if not field:
            return m.group(0)  # 未知参数：原样留在提示词里
        params[field] = val
        return ""

    prompt = pat.sub(_take, raw)
    # 清理多余空白（参数被移除后可能留下连续空格）
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ，,。.、" )

    if not prompt:
        return "", {}, "提示词是空的（参数我收到了，但描述呢？）"

    # ---- 校验 + 类型转换 ----
    for field in list(params.keys()):
        label = _ARG_LABELS.get(field, field)
        v = str(params[field]).strip()
        if field in _INT_ARGS:
            try:
                iv = int(v)
            except ValueError:
                return prompt, {}, "--%s 得是数字，你给的 %r 本喵看不懂" % (label, v)
            if field == "count" and not (1 <= iv <= 8):
                return prompt, {}, "--%s 限 1~8（本地显卡 6GB，太多要炸），你给了 %d" % (label, iv)
            if field == "steps" and not (1 <= iv <= 150):
                return prompt, {}, "--%s 限 1~150，你给了 %d" % (label, iv)
            params[field] = iv
        elif field == "ratio":
            v = v.replace("：", ":")
            if v not in VALID_RATIOS:
                return prompt, {}, "--%s 只支持 %s，你给了 %r" % (label, "/".join(VALID_RATIOS), v)
            params[field] = v

    # 缺 value 的形态：`--尺寸` 后面没东西（正则要求 value 非空，故会留在 prompt 里）
    # 这里再兜一次：提示词末尾出现孤立的 `--已知key` → 明确报错
    dangling = re.search(r"--([^\s]+)\s*$", prompt)
    if dangling:
        key = dangling.group(1).strip().lower()
        if key in DRAW_ARG_KEYS or key.lower() in DRAW_ARG_KEYS:
            return prompt, {}, "--%s 后面得跟个值（例：--%s 1024）" % (
                _ARG_LABELS.get(DRAW_ARG_KEYS.get(key) or DRAW_ARG_KEYS.get(key.lower()), key), key)

    return prompt, params, ""


@dataclass
class Route:
    """路由结果。kind 决定 taskmgr 如何执行。"""

    kind: str                      # t2i | i2i | video | reply
    api: str = ""                  # 平台端点相对路径，如 /t2i/online/generate
    payload: dict = field(default_factory=dict)
    text: str = ""                 # kind='reply' 时的回复文本
    consume_image: bool = False    # 是否消耗会话里的待编辑源图


def _strip_cmd(raw: str, cmd: str) -> str:
    """去掉 '/cmd' 前缀，返回剩余描述文本。"""
    s = (raw or "").strip()
    low = s.lower()
    if low.startswith("/" + cmd):
        return s[len(cmd) + 1:].strip()
    if low.startswith(cmd):
        return s[len(cmd):].strip()
    return s


def match_keyword(raw_text: str) -> tuple[str, str]:
    """关键词门禁（Config.KEYWORD_GATE）。

    返回 (kind, payload)：
      "test" → 测试对话，payload 为空；
      "draw" → 生图，payload 为剥离触发词后的提示词；
      "cmd"  → 斜杠指令（/help 等零成本自检命令），payload 为原文；
      ""     → 未命中任何关键词，调用方应静默丢弃（不回任何包）。

    命中优先级：斜杠指令 > 测试词（整句相等）> 生图词（前缀）。
    """
    text = (raw_text or "").strip()
    if not text:
        return "", ""
    low = text.lower()

    # 女仆暗号（自然语言指令）与斜杠指令同等放行
    if _translate_maid(text) is not None:
        return "cmd", text

    # 斜杠指令始终放行：零成本自检命令，不消耗配额
    if low.startswith("/"):
        return "cmd", text

    # 测试对话：整句等于关键词之一
    if low in [str(w).lower() for w in Config.TEST_KEYWORDS]:
        return "test", ""

    # 生图：以触发词开头，其后内容作为提示词
    for w in Config.DRAW_KEYWORDS:
        w = str(w)
        if low.startswith(w.lower()):
            return "draw", text[len(w):].strip()

    return "", ""


def route(ev: QQEvent, sess: Session) -> Route:
    """把标准化事件映射为平台 API 调用（文档 §6 规则表）。"""
    text = (ev.raw_text or "").strip()
    low = text.lower()

    # 女仆暗号翻译为内部斜杠命令，复用下方 startswith 匹配
    maid = _translate_maid(text)
    if maid:
        text = maid
        low = text.lower()

    # ---- 指令优先 ----
    if low.startswith(("/help", "/帮助", "help")):
        return Route(kind="reply", text=HELP_TEXT)

    if low.startswith("/quota"):
        return Route(kind="reply", text="__QUOTA__")  # 由入口填充真实配额

    if low.startswith("/status"):
        return Route(kind="reply", text="__STATUS__")

    if low.startswith(("/hw", "/hardware", "/资源")):
        return Route(kind="reply", text="__HW__")

    # ---- 图片主题（自绘菜单/进度/配额图的配色，热生效）----
    if low.startswith("/theme"):
        arg = _strip_cmd(text, "theme").lower()
        if arg in ("neon", "dark", "深色", "霓虹"):
            return Route(kind="reply", text="__THEME_NEON__")
        if arg in ("light", "white", "浅色", "白底"):
            return Route(kind="reply", text="__THEME_LIGHT__")
        if arg in ("auto", "自动"):
            return Route(kind="reply", text="__THEME_AUTO__")
        return Route(kind="reply", text="用法：/theme neon|light|auto（或直接说「换深色」「换浅色」）")

    if low.startswith("/mode"):
        arg = _strip_cmd(text, "mode").lower()
        if arg in ("video", "视频"):
            return Route(kind="reply", text="__MODE_VIDEO__")
        if arg in ("image", "img", "图片", "生图"):
            return Route(kind="reply", text="__MODE_IMAGE__")
        return Route(kind="reply", text="用法：/mode image 或 /mode video（切个模式还要本喵教？）")

    # ---- 任务中心（2026-09-08：监控 / 查看 / 排队 / 取消 / 预览）----
    # 约定：`__CMD__@@<arg>` —— 占位符 + `@@` 分隔参数，由入口 _handle_reply 解析。
    # 用占位符而非直接查库：intent 层**不碰 I/O**（保持纯函数可单测），
    # 与既有 __QUOTA__ / __STATUS__ 完全同构。
    if low.startswith("/task"):
        return Route(kind="reply", text="__TASK__@@" + _strip_cmd(text, "task").strip())

    if low.startswith("/recent"):
        return Route(kind="reply", text="__RECENT__")

    # ---- 监听主项目生图任务（2026-09-13）：从推理监控页 /tasks 获取 ----
    # `主项目任务 [页码]` → 看主项目后台正在跑/排队/最近完成的生图任务（含结果图）。
    if low.startswith("/mtasks"):
        return Route(kind="reply", text="__MTASKS__@@" + _strip_cmd(text, "mtasks").strip())

    # ---- 历史画廊（2026-09-11）：看历史缩略图，回复编号看原图 ----
    # arg 支持页码：「历史 2」= 第 2 页；留空=第 1 页。
    if low.startswith("/gallery"):
        return Route(kind="reply", text="__GALLERY__@@" + _strip_cmd(text, "gallery").strip())

    # 纯数字（1~2 位）=「选第 N 张看原图」。intent 只判形态（保持纯函数），
    # 是否真有活跃画廊由入口 gallery.pick 判定——没翻过相册会回提示，
    # 不会误把"3"当成生图提示词。
    _d = text.strip()
    if _d.isdigit() and 1 <= len(_d) <= 2:
        return Route(kind="reply", text="__PICK__@@" + _d)

    if low.startswith("/queue"):
        return Route(kind="reply", text="__QUEUE__")

    if low.startswith("/cancel"):
        target = _strip_cmd(text, "cancel").strip()
        if not target:
            return Route(kind="reply", text="用法：取消 <任务号>（不记得号就说「队列」看看）")
        return Route(kind="reply", text="__CANCEL__@@" + target)

    if low.startswith("/promote"):
        target = _strip_cmd(text, "promote").strip()
        if not target:
            return Route(kind="reply", text="用法：插队 <任务号>（只主人能用，把你的活提到最前）")
        return Route(kind="reply", text="__PROMOTE__@@" + target)

    if low.startswith("/clear"):
        return Route(kind="reply", text="__CLEAR__")

    if low.startswith("/again"):
        target = _strip_cmd(text, "again").strip()
        if not target:
            return Route(kind="reply", text="用法：再看 <任务号>（把结果图再发一遍，不重新生成、不占队列）")
        return Route(kind="reply", text="__AGAIN__@@" + target)

    if low.startswith("/monitor"):
        arg = _strip_cmd(text, "monitor").strip().lower()
        if arg in ("on", "开", "1", "true", "enable", "打开"):
            return Route(kind="reply", text="__MONITOR_ON__")
        if arg in ("off", "关", "0", "false", "disable", "关闭"):
            return Route(kind="reply", text="__MONITOR_OFF__")
        return Route(kind="reply", text="用法：监控 开 / 监控 关（任务失败或超时时私聊提醒你，仅主人）")

    # ---- 监听主项目生图任务开关（仅主人）----
    if low.startswith("/mlisten"):
        arg = _strip_cmd(text, "mlisten").strip().lower()
        if arg in ("on", "开", "1", "true", "enable", "打开"):
            return Route(kind="reply", text="__MLISTEN_ON__")
        if arg in ("off", "关", "0", "false", "disable", "关闭"):
            return Route(kind="reply", text="__MLISTEN_OFF__")
        return Route(kind="reply", text="用法：生图监听 开 / 生图监听 关（主项目开始/完成生图时私聊提醒你，仅主人）")

    # ---- 视频 ----
    if low.startswith(("/video", "/视频")):
        prompt = _strip_cmd(text, "video")
        if not prompt:
            return Route(kind="reply", text="用法：/video 一段夕阳下的城市航拍（描述写清楚点，别让本喵猜）")
        return Route(kind="video", api="/qq/video-generate", payload={"prompt": prompt})

    # ---- 图生图类（需要源图）----
    if low.startswith("/upscale"):
        prompt = _strip_cmd(text, "upscale")
        return Route(
            kind="i2i",
            api="/t2i/edit",
            payload={"mode": "upscale", "prompt": prompt},
            consume_image=True,
        )

    if low.startswith("/outpaint"):
        rest = _strip_cmd(text, "outpaint")
        parts = rest.split()
        direction = parts[0] if parts else ""
        prompt = " ".join(parts[1:]) if len(parts) > 1 else ""
        if direction not in OUTPAINT_OFFSETS:
            return Route(kind="reply", text="用法：/outpaint 左|右|上|下 [描述]（先扔张图过来，懂？）")
        payload = {"mode": "outpaint", "prompt": prompt}
        payload.update(OUTPAINT_OFFSETS[direction])
        return Route(kind="i2i", api="/t2i/edit", payload=payload, consume_image=True)

    # ---- 图片 + 文本 → 图生图 ----
    if ev.images:
        return Route(
            kind="i2i",
            api="/t2i/online/edit",
            payload={"prompt": text or "优化画质", "mode": "img2img"},
            consume_image=True,
        )

    # ---- 有暂存源图 + 纯文本 → 继续改图（§3.8 验收场景）----
    if sess.pending_image and text:
        return Route(
            kind="i2i",
            api="/t2i/online/edit",
            payload={"prompt": text, "mode": "img2img"},
            consume_image=True,
        )

    # ---- 纯文本 → 按会话默认模式 ----
    if not text:
        return Route(kind="reply", text="发段描述本喵才画，光@我不给词算什么意思？/help 看你能使唤本喵干啥。")

    # 显式生图词优先于会话默认视频模式（P3：/mode video 后发「生图 x」仍走生图）
    if getattr(ev, "force_t2i", False):
        return _t2i_route(text)

    if sess.mode == "video":
        return Route(kind="video", api="/qq/video-generate", payload={"prompt": text})

    return _t2i_route(text)


def _strip_draw_keyword(text: str) -> str:
    """剥掉生图触发词前缀（「生图」「画图」…），别让它混进提示词。

    背景：开启 KEYWORD_GATE 后用户必须说「生图 一只猫」才能触发，但 route() 收
    到的是**原话**（`ev.raw_text`），触发词会被当成提示词的一部分提交 →
    生成的图里凭空多个「生图」语义，质量变差。这里统一剥掉。
    仅当剥离后仍有内容才剥（用户真想画「生图」这个主题时不会被吃掉）。
    """
    low = (text or "").lower()
    for w in Config.DRAW_KEYWORDS:
        w = str(w)
        if not w:
            continue
        if low.startswith(w.lower()):
            rest = text[len(w):].strip(" ，,。.、")
            if rest:
                return rest
    return text


def _t2i_route(text: str) -> Route:
    """纯文本 → 生图路由：**本地通道走 QQ 持久队列，在线通道保持直发**。

    为什么只有本地通道排队：
      * 本地 ComfyUI **独占**（同时只跑 1 个），且 /t2i/generate 入队会 cancel
        别人的任务 → 必须串行排队，否则互相打断；
      * 在线通道是云端**并发**，排队只会白白增加延迟，没有意义。
    """
    chan = str(getattr(Config, "IMAGE_CHANNEL", "online") or "online").strip().lower()
    if chan != "local":
        return Route(kind="t2i", api="/t2i/online/generate",
                     payload={"prompt": _strip_draw_keyword(text)})

    prompt, params, err = parse_draw_args(_strip_draw_keyword(text))
    if err:
        # 参数写错（如 --数量 99）→ 直接把用法错误回给用户，不占队列名额
        return Route(kind="reply", text=err)
    return Route(kind="queue_t2i", payload={"prompt": prompt, "params": params})


def validate_prompt(text: str) -> tuple[bool, str]:
    """§3.6 长度限制：prompt ≤300。"""
    if len(text or "") > Config.PROMPT_MAX_LEN:
        return False, "写小作文呢？描述控制在 %d 字以内，删删再发。" % Config.PROMPT_MAX_LEN
    return True, ""


def truncate_prompt(text: str) -> tuple[str, bool]:
    """§3.6 修正（2026-09-07）：超 PROMPT_MAX_LEN 时**按句末自动截断**而非直接拒。

    之前策略：超长 → validate_prompt 返 False → quota_refund + return → task_mgr.run
    不跑 → 全局任务簿空 → /status 永远回"闲着呢"（用户体感"看不到进度"）。
    新策略：截到 ≤PROMPT_MAX_LEN 后放行；返回 (truncated_text, was_truncated)。
    截断优先按句末标点（。！？.!?，,；;\n）找最靠后的位置，找不到则硬截。

    例："18岁黑色头发...（超长）" → 在最近的 "。" 切，保留完整句子。
    """
    text = (text or "").strip()
    if len(text) <= Config.PROMPT_MAX_LEN:
        return text, False
    truncated = text[:Config.PROMPT_MAX_LEN]
    cut_marks = ["。", "！", "?", "？", ".", "!", "，", ",", ";", "；", "\n"]
    best = -1
    for m in cut_marks:
        i = truncated.rfind(m)
        if i > best:
            best = i
    if best >= Config.PROMPT_MAX_LEN // 2:
        truncated = truncated[: best + 1]
    return truncated.strip(), True
