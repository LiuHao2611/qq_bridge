# -*- coding: utf-8 -*-
"""ai_mode —— AI 陪聊模式（2026-10-19 新增）。

需求：给 QQ 机器人加「AI 模式」开关——开启后**群里所有消息**（不再需要
@机器人 / 关键词）都由本机大模型回复；关闭后恢复原有关键词门禁行为。

设计（与项目既有铁律对齐）
------------------------
* **只调用在线大模型**，协议 = OpenAI 兼容（POST {AI_API_BASE}/chat/completions，
  Bearer 鉴权）。base_url + api_key + model 三件套是 2026 年事实标准
  （调研：Yixi233-mo/qqbot、makenwjz/qq-roleplay-bot、CharlieNALAN/QQ-AI-Chatbot、
  SanJerry007/nonebot-plugin-chatgpt-api、wannaqueen66-create/qqbot 均采用），
  任何兼容服务商（OpenAI / DeepSeek / Moonshot / 阿里百炼 / 智谱）改三值即接入；
* 只经 HTTP 单向调在线 API，不 import 任何推理模块，与「bridge 与主项目
  物理隔离」红线一致；
* CLOSE-05：httpx 显式超时（`Config.AI_TIMEOUT`，默认 60s）；
* CLOSE-02：未配 Key / 401 / 429 / 超时 / 返回异常 → **显式错误文本**上抛，
  绝不静默不回；
* REDUN-02：仅 ConnectError / 429 / 5xx 指数退避重试（1s/2s + 抖动，总尝试
  封顶 3 次）；4xx 与超时不重试（后者会突破 REPLY_TIMEOUT 60s 预算）；
* OPS-02：对话历史 TTL（`Config.AI_HISTORY_TTL`）+ 惰性清理；
* 防刷屏：同一用户 cooldown（`Config.AI_COOLDOWN`）；
* 开关持久化：`data/ai_mode.json` 原子写（tmp + replace，同 planstore 模式），
  bridge 重启后恢复；
* LOG-04：API Key 只从 env / 配置页 secret 字段读，**严禁落日志**。

scope 约定（与 dispatcher.QQEvent.scope_key 一致）：
  群 = `g:<group_id>`；私聊 = `u:<user_id>`。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path

import httpx

from .config import Config, get_logger, jlog
from .queue import DATA_DIR

log = get_logger()

AI_MODE_PATH = DATA_DIR / "ai_mode.json"


def event_scope(event) -> str:
    """nonebot 事件 → AI 模式 scope key（与 dispatcher.QQEvent.scope_key 同构）。

    群 = `g:<group_id>`；私聊/其它 = `u:<user_id>`。
    供 game_tools / video_parse 等**独立 matcher** 判断「本会话是否开着 AI 模式」
    ——它们与主流程并行收消息，不走 _route，必须自己问一遍（2026-10-19）。
    """
    try:
        if str(getattr(event, "message_type", "") or "") == "group":
            return "g:%d" % int(getattr(event, "group_id", 0) or 0)
        return "u:%d" % int(getattr(event, "user_id", 0) or 0)
    except Exception:
        return "u:0"


#: AI 模式文字游戏登记表（2026-10-19 新增：游戏对话倒计时 + 超时 AI 特殊回复）。
#: 设计参考（2026 GitHub 权威项目调研，禁闭门造车，搬运思路本地兼容）：
#:   - kenan131/qqBot 成语接龙 / shenxgan/qqbot：游戏状态机 + 认输/挑衅收局文案；
#:   - luojisama/nonebot-plugin-personification：状态驱动的主动表达（空闲催更）、
#:     过期提交拦截（发送前复查会话有效性）、按生成耗时补足打字延迟；
#:   - Chaonaoo/QQ_bot：拟人延迟公式 min(6, 0.8+len/30+rand(0.3,1.5))；
#:   - gsskk/nonebot-plugin-dify：流式逐段回复降低感知延迟。
#: timeout = 每次对话倒计时（秒）：到点 AI 催更（不耐烦人设），
#: 再拖 2×timeout AI 收局（阴阳怪气收官），全程 LLM 生成、拒绝死板文案。
AI_GAMES: dict[str, dict] = {
    "成语接龙": {
        "aliases": ("接龙",),
        "timeout": 90,
        "intro": "来，成语接龙！你出一个本喵接一个，同音就算接上。接不上就乖乖认输喵～",
        "system": (
            "你在和群友玩【成语接龙】。规则：群友出一个成语，你接一个以上个成语"
            "最后一个字（同音即可）开头的新成语，轮流进行。每轮只回一个成语，"
            "可附一句20字以内的短评；禁止重复已出现过的成语、禁止长篇解释、"
            "禁止接不上还硬编。真的接不上就认输并放句狠话。"
        ),
    },
    "数字炸弹": {
        "aliases": ("炸弹",),
        "timeout": 60,
        "intro": "数字炸弹来了！本喵心里默想一个 1-100 的整数，你猜，本喵只告诉你大了小了。炸了算你输喵～",
        "system": (
            "你在和群友玩【数字炸弹】。你在心里默想一个1-100的整数（绝不说出来），"
            "群友每猜一个数，你只回「大了」或「小了」（可附一句短评），直到被猜中——"
            "被猜中就宣布「炸了」并调侃一句。禁止泄露答案、禁止替群友猜。"
        ),
    },
    "二十四点": {
        "aliases": ("24点",),
        "timeout": 120,
        "intro": "二十四点！本喵出四个数字，你用加减乘除凑出 24。算不出来可以求本喵给提示～",
        "system": (
            "你在和群友玩【二十四点】。你随机出4个1-10的整数，群友给出算式凑24。"
            "群友发来算式后你验证：对就夸一句并出下一组；错就指出错哪。"
            "群友说「提示」给一步提示，说「答案」才公布解法。禁止自己把答案全算了。"
        ),
    },
    "海龟汤": {
        "aliases": (),
        "timeout": 120,
        "intro": "海龟汤开局！本喵出题面，你提问，本喵只答是/不是/无关。揭开真相算你赢～",
        "system": (
            "你在和群友玩【海龟汤】（情境猜谜）。你出一个简短的谜题汤面（一两句话），"
            "群友通过提问还原真相，你只回答「是」「不是」「无关」或「是也不是」，"
            "不主动透露。群友猜中真相就公布完整汤底并夸奖；问满15问还没中就给提示。"
        ),
    },
    "谁是卧底": {
        "aliases": ("卧底",),
        "timeout": 90,
        "intro": "谁是卧底！本喵给你发一个词，别人拿一个相近的词。描述但不能说出词本身，找出卧底！",
        "system": (
            "你在和群友玩【谁是卧底】文字版。你分配两个相近的词（如「牛奶」「豆浆」），"
            "你自己是平民持其中一个词，群友依次描述自己的词（不能说出词本身），"
            "你点评每轮描述并引导投票找出卧底。保持悬念，不要提前泄底。"
        ),
    },
    "二十个问题": {
        "aliases": ("二十问",),
        "timeout": 60,
        "intro": "二十个问题！你心里想一个东西，本喵来问，最多二十个是/否问题，本喵猜它是什么～",
        "system": (
            "你在和群友玩【二十个问题】。群友心里想一个任意事物，你每次只问一个"
            "可以用「是/否」回答的问题，根据回答逐步缩小范围，最后给出猜测。"
            "每次只问一个问题，禁止一次问多个、禁止长篇分析。"
        ),
    },
}


class GameSession:
    """AI 模式游戏会话（per-scope 一份，内存态，不落盘——重启即解散，可接受）。"""

    __slots__ = ("game", "timeout", "nudges", "turns", "started_at")

    def __init__(self, game: str, timeout: float):
        self.game = game
        self.timeout = float(timeout)
        self.nudges = 0        # 已催更次数（>0 说明用户超时过，下条回复带迟到吐槽）
        self.turns = 0         # 已完成回合数
        self.started_at = time.time()


class GameSessionStore:
    """per-scope 游戏会话管理（内存 + 惰性 TTL 清理，OPS-02）。"""

    #: 会话最长存活（秒）：超龄自动解散，防僵尸局
    MAX_AGE = 3600

    def __init__(self) -> None:
        self._data: dict[str, GameSession] = {}

    def get(self, scope: str) -> GameSession | None:
        return self._data.get(scope)

    def start(self, scope: str, game: str, timeout: float) -> GameSession:
        s = GameSession(game, timeout)
        self._data[scope] = s
        log.info(jlog("ai_game_start", scope=scope, game=game, timeout=timeout))
        return s

    def end(self, scope: str) -> bool:
        s = self._data.pop(scope, None)
        if s is not None:
            log.info(jlog("ai_game_end", scope=scope, game=s.game, turns=s.turns,
                          nudges=s.nudges))
        return s is not None

    def ttl_cleanup(self) -> int:
        now = time.time()
        dead = [k for k, s in self._data.items() if now - s.started_at > self.MAX_AGE]
        for k in dead:
            self._data.pop(k, None)
        if dead:
            log.info(jlog("ai_game_ttl_cleanup", cleaned=len(dead)))
        return len(dead)


game_sessions = GameSessionStore()


class AIModeStore:
    """per-scope AI 模式开关（内存 set + JSON 持久化，原子写）。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else AI_MODE_PATH
        self._on: set[str] = set()
        self._load()

    # ---------------- 内部 ----------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    scopes = data.get("on") or []
                elif isinstance(data, list):  # 兼容裸数组旧格式
                    scopes = data
                else:
                    scopes = []
                self._on = {str(s).strip() for s in scopes if str(s).strip()}
        except Exception as e:  # noqa: BLE001 — 坏文件不能拖垮启动
            log.warning(jlog("ai_mode_load_failed", error=type(e).__name__,
                             detail=str(e)[:160]))
            self._on = set()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"on": sorted(self._on)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001 — 落盘失败只记日志（CLOSE-02）
            log.warning(jlog("ai_mode_save_failed", error=type(e).__name__,
                             detail=str(e)[:160]))

    # ---------------- 查询 / 变更 ----------------
    def is_on(self, scope_key: str) -> bool:
        """该 scope 是否开启 AI 模式。总开关 Config.AI_MODE_ENABLE 关闭时恒 False。"""
        if not Config.AI_MODE_ENABLE:
            return False
        return str(scope_key or "").strip() in self._on

    def set(self, scope_key: str, on: bool) -> None:
        k = str(scope_key or "").strip()
        if not k:
            return
        if on:
            self._on.add(k)
        else:
            self._on.discard(k)
        self._save()
        log.info(jlog("ai_mode_set", scope=k, on=bool(on)))

    def on_scopes(self) -> list[str]:
        return sorted(self._on)


class AIChatHistory:
    """per-scope 对话历史（内存 + TTL 惰性清理，OPS-02）。

    只存最近 `turns*2` 条（user/assistant 各算一条），超限从头淘汰。
    """

    def __init__(self, turns: int | None = None, ttl: int | None = None):
        self._turns = turns if turns is not None else Config.AI_HISTORY_TURNS
        self._ttl = ttl if ttl is not None else Config.AI_HISTORY_TTL
        self._data: dict[str, dict] = {}

    def ttl_cleanup(self) -> int:
        now = time.time()
        dead = [k for k, v in self._data.items() if now - v.get("ts", 0) > self._ttl]
        for k in dead:
            self._data.pop(k, None)
        if dead:
            log.info(jlog("ai_history_ttl_cleanup", cleaned=len(dead),
                          remain=len(self._data)))
        return len(dead)

    def _touch(self, scope: str) -> list:
        v = self._data.get(scope)
        if v is None:
            v = {"msgs": [], "ts": time.time()}
            self._data[scope] = v
        v["ts"] = time.time()
        return v["msgs"]

    def append(self, scope: str, role: str, content: str) -> None:
        msgs = self._touch(scope)
        msgs.append({"role": str(role), "content": str(content or "")})
        cap = max(2, self._turns * 2)
        if len(msgs) > cap:
            del msgs[: len(msgs) - cap]

    def messages(self, scope: str) -> list[dict]:
        v = self._data.get(scope)
        return list(v["msgs"]) if v else []

    def clear(self, scope: str) -> None:
        self._data.pop(scope, None)


#: 已知端点后缀——用户可能把「完整端点 URL」直接粘进 AI_API_BASE
#: （2026-10-19 实测：用户配方舟时粘的是 .../api/v3/responses，
#:  固定拼 /chat/completions 会得到 .../responses/chat/completions → 404）。
_ENDPOINT_SUFFIXES = ("/chat/completions", "/responses", "/completions", "/messages")


def _resolve_chat_url(base: str) -> str:
    """把 AI_API_BASE 归一化为 OpenAI 兼容 chat/completions 全端点 URL。

    兼容三种输入（大小写不敏感、容忍尾斜杠）：
      - 版本根：`https://ark.cn-beijing.volces.com/api/v3`
        → `.../api/v3/chat/completions`
      - 完整 chat 端点：`https://api.openai.com/v1/chat/completions`
        → 原样（幂等，不会拼成 /chat/completions/chat/completions）
      - 其它协议端点：`https://ark.cn-beijing.volces.com/api/v3/responses`
        → 剥掉 /responses 再拼 `/chat/completions`（方舟的 OpenAI 兼容端点）
    """
    u = (base or "").strip().rstrip("/")
    low = u.lower()
    for suf in _ENDPOINT_SUFFIXES:
        if low.endswith(suf):
            u = u[: len(u) - len(suf)]
            break
    return u.rstrip("/") + "/chat/completions"


class AIChatter:
    """在线大模型对话客户端（httpx，OpenAI 兼容）+ 历史管理 + per-user cooldown。"""

    def __init__(self) -> None:
        self.history = AIChatHistory()
        self._last_at: dict[str, float] = {}
        #: 最近一次 chat() 剥离出的 [表情:分类] marker（per-scope，调用方读取后即用）
        self.last_mood: dict[str, str | None] = {}

    # ---------------- cooldown ----------------
    @staticmethod
    def _ckey(scope: str, user_id) -> str:
        return "%s::%s" % (scope, user_id)

    def cooldown_left(self, scope: str, user_id) -> float:
        """距离该用户下次可发起 AI 对话的剩余秒数（0 = 可发）。"""
        left = Config.AI_COOLDOWN - (time.time() - self._last_at.get(self._ckey(scope, user_id), 0.0))
        return max(0.0, left)

    def _mark(self, scope: str, user_id) -> None:
        self._last_at[self._ckey(scope, user_id)] = time.time()
        # 惰性清理，防字典无限增长（保留近 10 倍 cooldown 内的记录）
        if len(self._last_at) > 500:
            cutoff = time.time() - max(Config.AI_COOLDOWN * 10, 60.0)
            self._last_at = {k: v for k, v in self._last_at.items() if v > cutoff}

    # ---------------- 对话 ----------------
    async def _post(self, messages: list) -> tuple[str, str]:
        """OpenAI 兼容 /chat/completions 调用（含 REDUN-02 重试）。返回 (content, error)。

        重试（REDUN-02）：仅 ConnectError / HTTP 429 / 5xx 重试，指数退避
        1s/2s + 抖动，总尝试 = 1 + AI_MAX_RETRIES（封顶 3 次）；4xx 与超时
        不重试（确定性失败 / 突破 REPLY_TIMEOUT 60s 消息处理预算）。
        """
        payload = {
            "model": Config.AI_MODEL,
            "messages": messages,
            "temperature": Config.AI_TEMPERATURE,
            "max_tokens": Config.AI_MAX_TOKENS,
            "stream": False,
        }
        headers = {
            "Authorization": "Bearer %s" % Config.AI_API_KEY,
            "Content-Type": "application/json",
        }
        url = _resolve_chat_url(Config.AI_API_BASE)
        retries = max(0, min(int(Config.AI_MAX_RETRIES), 2))  # 封顶总尝试 3 次
        last_err = ""
        for attempt in range(retries + 1):
            try:
                # trust_env=False：在线 API 直连，绕开本机 HTTP_PROXY 等代理 env
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(Config.AI_TIMEOUT), trust_env=False
                ) as cli:
                    resp = await cli.post(url, json=payload, headers=headers)
            except httpx.ConnectError:
                last_err = "连不上大模型服务（%s）" % Config.AI_API_BASE
            except httpx.TimeoutException:
                # 超时不重试：直接显式失败，保 60s 消息处理预算
                return "", "大模型响应超时（%ss）" % Config.AI_TIMEOUT
            except Exception as e:  # noqa: BLE001 — CLOSE-02：显式失败，绝不静默
                return "", "AI 调用失败（%s）" % type(e).__name__
            else:
                sc = resp.status_code
                if sc < 400:
                    try:
                        j = resp.json()
                    except Exception as e:  # noqa: BLE001
                        return "", "大模型返回不是 JSON（%s）" % type(e).__name__
                    try:
                        content = str(
                            ((j or {}).get("choices") or [{}])[0]
                            .get("message", {}).get("content") or ""
                        ).strip()
                    except Exception:  # noqa: BLE001 — 结构异常按空内容处理
                        content = ""
                    if not content:
                        return "", "大模型回了空内容"
                    return content, ""
                if sc in (401, 403):
                    return "", ("API Key 无效或无权访问（HTTP %d），"
                                "去「机器人配置 → 常规·AI 模式」检查" % sc)
                if sc == 429:
                    last_err = "大模型限流（HTTP 429，请求太频繁或额度不足）"
                elif sc >= 500:
                    last_err = "大模型服务端故障（HTTP %d）" % sc
                else:
                    # 其它 4xx：确定性失败，不重试
                    return "", "大模型接口报错 HTTP %d" % sc
            # 可重试错误：指数退避 1s/2s + 抖动（REDUN-02）
            if attempt < retries:
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.5))
        return "", last_err or "大模型调用失败"

    @staticmethod
    def _sys_content(extra_system: str | None) -> str:
        """系统提示词 = 人设 + （游戏规则等）追加注入。"""
        base = Config.AI_SYSTEM_PROMPT
        return (base + "\n" + extra_system) if extra_system else base

    def _record(self, scope: str, content: str, user_id=0) -> str:
        """记历史 + 冷却打点 + 超长截断，返回对外发送文本。"""
        self.history.append(scope, "assistant", content)
        self._mark(scope, user_id)
        if len(content) > Config.AI_MAX_REPLY_LEN:
            content = content[: Config.AI_MAX_REPLY_LEN] + "……（太长，本喵省略）"
        return content

    async def chat(self, scope: str, user_text: str, user_id=0,
                   extra_system: str | None = None, note: str | None = None) -> tuple[str, str]:
        """发起一轮 AI 对话（OpenAI 兼容 POST {AI_API_BASE}/chat/completions）。

        参数：
          extra_system: 追加进系统提示的上下文（游戏规则等，不落历史）；
          note:         仅送给模型的旁注（如「用户超时才回」），**不落历史**，
                        历史只记用户干净原文，保证上下文保真。

        返回 (reply, error)：
          * 成功 → (回复文本, "")；
          * error 为 "empty" / "cooldown" / "no_key" → 调用方静默跳过或转
            配置指引（no_key 提示去配置页填 Key）；
          * 其它 error → 调用方应把错误**显式**告知用户（CLOSE-02）。
        """
        text = (user_text or "").strip()
        if not text:
            return "", "empty"
        if self.cooldown_left(scope, user_id) > 0:
            return "", "cooldown"
        if not Config.AI_API_KEY:
            return "", "no_key"

        self.history.ttl_cleanup()
        user_content = text + (("\n" + note) if note else "")
        messages = [
            {"role": "system", "content": self._sys_content(extra_system)},
            *self.history.messages(scope),
            {"role": "user", "content": user_content},
        ]
        content, err = await self._post(messages)
        if err:
            return "", err
        # 剥离 [表情:分类] marker：历史与对外发送都不带它（协议噪音不进上下文）
        clean, mood = parse_sticker_marker(content)
        self.last_mood[scope] = mood
        # 历史记干净原文（note/extra_system 不落历史，保持上下文保真）
        self.history.append(scope, "user", text)
        return self._record(scope, clean, user_id), ""

    async def say(self, scope: str, instruction: str, user_id=0,
                  extra_system: str | None = None) -> tuple[str, str]:
        """AI **主动开口**（游戏催更/收局/告别/主动发言）：instruction 以 user
        消息身份送模型，但**不记入历史**（避免「系统事件」污染上下文），
        AI 回复记入历史保持连续性。

        与 chat() 同款剥离 [表情:分类] marker（主动发言也能自主决定配图，
        见 last_mood）；复用全部纪律：cooldown / no_key / REDUN-02 重试 /
        CLOSE-02 显式错误。
        """
        text = (instruction or "").strip()
        if not text:
            return "", "empty"
        if self.cooldown_left(scope, user_id) > 0:
            return "", "cooldown"
        if not Config.AI_API_KEY:
            return "", "no_key"

        self.history.ttl_cleanup()
        messages = [
            {"role": "system", "content": self._sys_content(extra_system)},
            *self.history.messages(scope),
            {"role": "user", "content": text},
        ]
        content, err = await self._post(messages)
        if err:
            return "", err
        clean, mood = parse_sticker_marker(content)
        self.last_mood[scope] = mood
        return self._record(scope, clean, user_id), ""

    async def chat_tools(self, scope: str, user_text: str, user_id=0,
                         extra_system: str | None = None, note: str | None = None,
                         converse=None, tool_ctx=None) -> tuple[str, str]:
        """带工具总线的 AI 对话（2026-10-20 M2 Function Calling）。

        与 chat() 的差别只在**执行体**：消息表组织完全一致（system + 历史 +
        user），但 POST 前后交给 converse 回调（tools.fc.FCRunner 注入的
        多回合循环）执行；历史纪律不变——只记用户干净原话与**最终**回复，
        中间工具轮次不落历史（保真，同 note 取舍）。

        参数：
          converse:   ``async (messages, tool_ctx) -> (reply, error)``；
                      None 或返回 "tool_bus_empty"（没注册任何工具）→ 显式
                      错误上抛，**不在本层偷偷回落纯对话**（那是路由层的事，
                      见 _handle_ai_chat：开关开且无工具 = 配置态，直接走 chat）。
          tool_ctx:   tools.registry.ToolContext（权限/预算/trace_id 载体）。

        返回 (reply, error)：错误分类同 chat()（empty/cooldown/no_key + 显式
        错误文本）；成功时回复同样剥 [表情:分类] marker。
        """
        text = (user_text or "").strip()
        if not text:
            return "", "empty"
        if self.cooldown_left(scope, user_id) > 0:
            return "", "cooldown"
        if not Config.AI_API_KEY:
            return "", "no_key"
        if converse is None:
            return "", "工具总线未接线（converse 回调缺失）"

        self.history.ttl_cleanup()
        user_content = text + (("\n" + note) if note else "")
        messages = [
            {"role": "system", "content": self._sys_content(extra_system)},
            *self.history.messages(scope),
            {"role": "user", "content": user_content},
        ]
        content, err = await converse(messages, tool_ctx)
        if err == "tool_bus_empty":
            return "", ("工具总线开着但一个工具都没注册——到「机器人配置」检查"
                        "「AI 工具总线」开关，或关掉它改用纯对话")
        if err:
            log.warning(jlog("ai_chat_tools_failed", scope=scope,
                             user_id=user_id, err=err[:160]))
            return "", err
        clean, mood = parse_sticker_marker(content)
        self.last_mood[scope] = mood
        # 历史记干净原文（note/extra_system 不落历史，保持上下文保真）
        self.history.append(scope, "user", text)
        return self._record(scope, clean, user_id), ""


# 模块级单例（插件入口直接用；单测各自 new 隔离）
ai_mode_store = AIModeStore()
ai_chatter = AIChatter()


#: AI 自主发表情包的 marker 协议：回复末尾单独一行 `[表情:分类]`。
#: 设计参考 Funny1Potato/nonebot-plugin-aigf-master（AI 自主决定发表情包）与
#: ko-RT-nya/nonebot-plugin-kirya-ai-chat（提示词分隔符拆分）的本地融合：
#: 让模型自己决定「此刻想不想配图、配什么情绪」，程序只做执行与兜底。
_STICKER_MARKER_RE = re.compile(r"\n*\s*\[表情:([\u4e00-\u9fa5]{1,4})\]\s*$")


def parse_sticker_marker(text: str) -> tuple[str, str | None]:
    """剥离回复末尾的 [表情:分类] marker。

    返回 (干净文本, 分类或 None)。marker 分类不在分类表里也接受
    （调用方会经人设映射兜底）——但绝不把 marker 文本留给用户看。
    """
    t = text or ""
    m = _STICKER_MARKER_RE.search(t)
    if not m:
        return t, None
    return t[:m.start()].rstrip(), m.group(1)


# ===========================================================================
# 拟人连发三件套（2026-10-19 忠实搬运自 MaiMBot，算法逐行不变）
#   来源：github.com/Ruins996/MaiMBot → src/plugins/chat/utils.py 的
#   calculate_typing_time / split_into_sentences_w_remove_punctuation，
#   以及 utils.process_llm_response 的编排（错别字→拆条→数量守卫）。
#   仅人设文案按本喵适配（守卫降级文案），参数默认值对齐其线上值。
# ===========================================================================

def calculate_typing_time(input_string: str, chinese_time: float = 0.2,
                          english_time: float = 0.1) -> float:
    """打字延迟 = 中文字数 × chinese_time + 其它字符 × english_time。

    MaiMBot 原版：中文 0.2s/字、英文 0.1s/字（发送前 sleep，封顶 10s）——
    「按生成耗时补足打字延迟」的真相是**按回复长度**，不是固定随机值。
    """
    total_time = 0.0
    for char in input_string:
        if '\u4e00' <= char <= '\u9fff':  # 中文字符
            total_time += chinese_time
        else:
            total_time += english_time
    return total_time


def split_into_sentences_w_remove_punctuation(text: str) -> list[str]:
    """概率拆条 + 标点随机清理（MaiMBot 原版算法逐行搬运）。

    与「标点必断」的直觉完全相反，真机制是：
      1. 按文本长度分「拆条强度」：<12 字 0.3 / <32 字 0.7 / 更长 0.9
         （长文才值得多拆，短文拆了显得神经）；
      2. `!！?？` 按强度概率**删除**（强度越高越删——长文里感叹号不拆条）；
      3. `。…` 按 (1-强度) 概率删除（强度越高越保留句号）；
      4. **真正的断句只在 `，` 和空格**，各自按强度概率断；
      5. 段内标点二连击：强度×0.5 概率把逗号删光、强度概率把逗号换空格。
    句号永远不拆条——真人打字也是一逗一句喘口气，不是一句一条。
    """
    len_text = len(text)
    if len_text < 5:
        if random.random() < 0.01:
            return list(text)  # 极短文本 1% 概率按字符分割
        return [text]
    if len_text < 12:
        split_strength = 0.3
    elif len_text < 32:
        split_strength = 0.7
    else:
        split_strength = 0.9

    # 统一英文逗号为中文逗号；换行当空格
    text = text.replace(',', '，')
    text = text.replace('\n', ' ')

    text_no_1 = ''
    for letter in text:
        if letter in ['!', '！', '?', '？']:
            if random.random() < split_strength:
                letter = ''
        if letter in ['。', '…']:
            if random.random() < 1 - split_strength:
                letter = ''
        text_no_1 += letter

    # 对每个逗号单独判断是否分割
    sentences = [text_no_1]
    new_sentences = []
    for sentence in sentences:
        parts = sentence.split('，')
        current_sentence = parts[0]
        for part in parts[1:]:
            if random.random() < split_strength:
                new_sentences.append(current_sentence.strip())
                current_sentence = part
            else:
                current_sentence += '，' + part
        # 处理空格分割
        space_parts = current_sentence.split(' ')
        current_sentence = space_parts[0]
        for part in space_parts[1:]:
            if random.random() < split_strength:
                new_sentences.append(current_sentence.strip())
                current_sentence = part
            else:
                current_sentence += ' ' + part
        new_sentences.append(current_sentence.strip())
    sentences = [s for s in new_sentences if s]

    # 段内逗号二连击：删光 / 换空格 / 保留
    sentences_done = []
    for sentence in sentences:
        sentence = sentence.rstrip('，,')
        if random.random() < split_strength * 0.5:
            sentence = sentence.replace('，', '').replace(',', '')
        elif random.random() < split_strength:
            sentence = sentence.replace('，', ' ').replace(',', ' ')
        sentences_done.append(sentence)
    return sentences_done


def process_llm_response(text: str, typo: bool = True) -> list[str]:
    """LLM 回复 → 拟人连发消息列表（MaiMBot process_llm_response 编排搬运）。

    流程：过长守卫 → 错别字注入 → 概率拆条 → 数量守卫。
    返回的每个元素 = 一个气泡；长度为 1 = 单条发送。
    守卫降级文案按本喵人设适配（原版是麦麦昵称）。
    """
    if not text:
        return []
    if len(text) > 300:
        return ['懒得说']  # 原版行为：超长直接摆烂（防刷屏）
    typoed_text = text
    if typo and Config.AI_TYPO_ENABLE:
        try:
            from .typo_generator import get_typo_generator
            gen = get_typo_generator()
            # 配置热生效：比率随配置页实时调整（MaiMBot 线上默认值见 config.py）
            gen.set_params(error_rate=Config.AI_TYPO_ERROR_RATE,
                           tone_error_rate=Config.AI_TYPO_TONE_RATE,
                           word_replace_rate=Config.AI_TYPO_WORD_RATE)
            typoed_text = gen.create_typo_sentence(text)[0]
        except Exception as e:  # noqa: BLE001 — 错别字失败不挡拆条
            log.warning(jlog("typo_inject_failed", error=type(e).__name__))
    sentences = split_into_sentences_w_remove_punctuation(typoed_text)
    if len(sentences) > max(2, int(Config.AI_BURST_MAX_PARTS)):
        return ['本喵不知道哦']  # 原版行为：拆太多放弃花哨（阈值可配）
    return sentences
