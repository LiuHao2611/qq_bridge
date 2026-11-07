# -*- coding: utf-8 -*-
"""tools.scheduler —— 提醒/定时任务（M2 工具总线第二批能力，2026-10-20）。

契约来源（dev_plan §五，2026-10-20 实读 ai-groupmate README 验证）：
  - 定时任务跨度 10 秒 ~ 7 天；APScheduler 秒级扫库、原子领取、失败不自动重试、
    超时 5 分钟过期不补发。
本模块按同等契约用纯 asyncio 实现（不引 APScheduler：个人单机单进程，
asyncio tick 已够，且与 queue_consume_loop/_proactive_loop 同生命周期模式）。

三件套：
  ReminderStore     持久化（data/reminders.json 原子写）+ 状态机 + 原子领取
  ReminderParser    LLM 时间表达式解析（服务「/提醒」命令族；**禁正则兜底**）
  ReminderScheduler 1s tick 后台循环，到点经调用方注入的 on_fire 回调发送

两个入口（能力一致、解析路径不同）：
  - FC 工具 remind_create：时间由**主对话模型**算成绝对 Unix 时间戳传入
    （system 提示注入当前时间），工具本身是确定性函数；
  - /提醒 命令族：没有 FC 循环，用 ReminderParser 单独一次 LLM 调用解析。

禁止降级 / 禁止兜底：
  - 时间解析只信 LLM 输出（JSON Schema 校验不过 → 显式错误），**不用正则/
    dateparser 兜底**——兜底解析错时间比报错更糟；
  - 落盘失败显式抛错（CLOSE-02），不假装存上了；
  - 发送失败标记 failed + last_error，**不自动重试**（ai-groupmate 纪律：
    重试会让用户收到重复提醒；要用户自己改）。

本模块不 import nonebot / 不 import 主包：可裸单测；发送经 on_fire 注入。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from ..config import Config, get_logger, jlog

log = get_logger()

#: 到点后超过该秒数未触发 → 过期不补发（ai-groupmate：5 分钟）
EXPIRE_SECONDS = 300.0
#: 提醒跨度下限/上限（ai-groupmate：10s ~ 7 天）
MIN_DELAY = 10.0
MAX_DELAY = 7 * 86400.0
#: 每个 scope（群/私聊）最多同时持有多少条 pending 提醒（防刷屏）
MAX_PER_SCOPE = 20
#: 调度 tick 间隔（秒）
TICK_INTERVAL = 1.0

_REPEATS = ("none", "daily")

#: ReminderParser 要求的 LLM 输出协议（REDUN-03：按此校验）
PARSER_SYSTEM = (
    "你是提醒时间解析器。用户说了一句想创建/查看/修改/取消提醒的话，"
    "当前时间是 {now_human}（Unix 秒 {now_ts}，本地时区）。"
    "请只输出一个 JSON 对象，不要任何多余文字、不要 markdown 代码块。"
    "字段：action（create/list/modify/cancel 之一）、text（提醒内容，"
    "create 必填）、fire_at（Unix 秒绝对时间，create/modify 必填，"
    "必须在当前时间 +{min_delay:.0f} 秒到 +{max_delay_days:.0f} 天之间）、"
    "repeat（none 或 daily，默认 none）、id（cancel/modify 时填用户提到的"
    "序号或 ID，没有就输出空串）。"
    "拿不准时间就挑最合理的解释；用户没说日期就默认今天，说晚了就顺延到明天。"
)


class ReminderError(Exception):
    """显式化的提醒业务错误（message 即给用户的错误文本）。"""


def _now() -> float:
    return time.time()


def _gen_id() -> str:
    return uuid.uuid4().hex[:8]


def _cfg(name: str, default):
    """从 Config 读可配项（getattr 防御：配置未接线时用模块常量默认）。"""
    return getattr(Config, name, default)


class ReminderStore:
    """提醒存储：内存 + data/reminders.json 原子持久化（planstore 同款模式）。

    条目结构：
      id, scope, user_id, group_id, is_private, text,
      fire_at, repeat, status(pending/done/failed/cancelled/expired),
      claimed(bool), claimed_at, created_at, fired_at, last_error
    """

    def __init__(self, path: Path | None = None, expire: float | None = None,
                 max_per_scope: int | None = None) -> None:
        from ..queue import DATA_DIR  # 复用 data 目录约定（延迟导入防环）

        self.path = Path(path) if path else (DATA_DIR / "reminders.json")
        self.expire = float(expire if expire is not None
                            else _cfg("REMIND_EXPIRE_SECONDS", EXPIRE_SECONDS))
        self.max_per_scope = int(max_per_scope if max_per_scope is not None
                                 else _cfg("REMIND_MAX_PER_SCOPE", MAX_PER_SCOPE))
        self._items: list[dict] = []
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8")) or []
            else:
                data = []
            self._items = [self._norm(r) for r in data if isinstance(r, dict)]
        except Exception as e:  # noqa: BLE001 — 坏文件不能拖垮启动，但要留痕
            log.warning(jlog("reminders_load_failed", path=str(self.path),
                             error=type(e).__name__))
            self._items = []

    def _save(self) -> None:
        """原子写（tmp + replace）。失败显式抛错——CLOSE-02，不假装存上了。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            raise ReminderError("提醒落盘失败（%s）：%s" % (type(e).__name__, str(e)[:160]))

    @staticmethod
    def _norm(r: dict) -> dict:
        """补齐字段（老文件/缺键不能 KeyError）。"""
        r.setdefault("id", "")
        r.setdefault("scope", "")
        r.setdefault("user_id", 0)
        r.setdefault("group_id", 0)
        r.setdefault("is_private", False)
        r.setdefault("text", "")
        r.setdefault("fire_at", 0.0)
        r.setdefault("repeat", "none")
        r.setdefault("status", "pending")
        r.setdefault("claimed", False)
        r.setdefault("claimed_at", 0.0)
        r.setdefault("created_at", 0.0)
        r.setdefault("fired_at", 0.0)
        r.setdefault("last_error", "")
        r.setdefault("channel", "napcat")  # 异步回传用（双轨并行）
        return r

    # ---------------- 查询 ----------------
    def list(self, scope: str, active_only: bool = True) -> list[dict]:
        """某 scope 的提醒（默认只列 pending；按触发时间正序）。"""
        out = [r for r in self._items
               if r.get("scope") == scope and (not active_only or r.get("status") == "pending")]
        return sorted(out, key=lambda r: float(r.get("fire_at") or 0))

    def get(self, rid: str) -> dict | None:
        rid = (rid or "").strip()
        return next((r for r in self._items if r.get("id") == rid), None)

    def pending_count(self, scope: str) -> int:
        return len(self.list(scope))

    # ---------------- 创建 ----------------
    def create(self, text: str, fire_at: float, repeat: str, scope: str,
               user_id: int = 0, group_id: int = 0, is_private: bool = False,
               channel: str = "napcat", now: float | None = None) -> dict:
        """创建提醒。fire_at 为 Unix 秒绝对时间（调用方负责把自然语言算成时间）。

        校验（显式错误，不是静默修正）：
          - 文本非空；
          - fire_at 在 [now+10s, now+7d]；
          - repeat ∈ {none, daily}；
          - 该 scope pending 数未超上限。
        """
        text = (text or "").strip()
        if not text:
            raise ReminderError("提醒内容不能为空")
        if len(text) > 200:
            text = text[:200]
        rep = str(repeat or "none").strip().lower()
        if rep not in _REPEATS:
            raise ReminderError("重复方式只能是 none（一次）或 daily（每天）")
        now = _now() if now is None else float(now)
        try:
            at = float(fire_at)
        except (TypeError, ValueError):
            raise ReminderError("提醒时间格式不正确")
        if at < now + MIN_DELAY:
            raise ReminderError("提醒时间至少要是 %.0f 秒以后" % MIN_DELAY)
        if at > now + MAX_DELAY:
            raise ReminderError("提醒时间最晚只能设 %.0f 天以内" % (MAX_DELAY / 86400))
        if self.pending_count(scope) >= self.max_per_scope:
            raise ReminderError("这个会话的提醒已经满 %d 条了，先取消几条吧"
                                % self.max_per_scope)
        item = self._norm({
            "id": _gen_id(), "scope": scope, "user_id": int(user_id or 0),
            "group_id": int(group_id or 0), "is_private": bool(is_private),
            "channel": str(channel or "napcat"),
            "text": text, "fire_at": at, "repeat": rep,
            "status": "pending", "created_at": now,
        })
        self._items.append(item)
        self._save()
        log.info(jlog("reminder_created", rid=item["id"], scope=scope,
                      fire_at=at, repeat=rep))
        return item

    # ---------------- 修改 / 取消 ----------------
    def modify(self, rid: str, scope: str, text: "str | None" = None,
               fire_at: "float | None" = None, repeat: "str | None" = None,
               now: float | None = None) -> dict:
        r = self.get(rid)
        if not r or r.get("scope") != scope:
            raise ReminderError("提醒 %s 不存在（只能改本会话的）" % rid)
        if r.get("status") != "pending":
            raise ReminderError("提醒 %s 已经是 %s 状态，不能改" % (rid, r.get("status")))
        now = _now() if now is None else float(now)
        if text is not None:
            t = (text or "").strip()
            if not t:
                raise ReminderError("提醒内容不能为空")
            r["text"] = t[:200]
        if repeat is not None:
            rep = str(repeat or "none").strip().lower()
            if rep not in _REPEATS:
                raise ReminderError("重复方式只能是 none（一次）或 daily（每天）")
            r["repeat"] = rep
        if fire_at is not None:
            try:
                at = float(fire_at)
            except (TypeError, ValueError):
                raise ReminderError("提醒时间格式不正确")
            if at < now + MIN_DELAY:
                raise ReminderError("提醒时间至少要是 %.0f 秒以后" % MIN_DELAY)
            if at > now + MAX_DELAY:
                raise ReminderError("提醒时间最晚只能设 %.0f 天以内" % (MAX_DELAY / 86400))
            r["fire_at"] = at
            r["claimed"] = False
            r["claimed_at"] = 0.0
        self._save()
        log.info(jlog("reminder_modified", rid=rid, scope=scope,
                      fire_at=r["fire_at"], repeat=r["repeat"]))
        return r

    def cancel(self, rid: str, scope: str) -> dict:
        r = self.get(rid)
        if not r or r.get("scope") != scope:
            raise ReminderError("提醒 %s 不存在（只能取消本会话的）" % rid)
        if r.get("status") != "pending":
            raise ReminderError("提醒 %s 已经是 %s 状态了" % (rid, r.get("status")))
        r["status"] = "cancelled"
        self._save()
        log.info(jlog("reminder_cancelled", rid=rid, scope=scope))
        return r

    # ---------------- 领取 / 结果（调度器用） ----------------
    def claim_due(self, now: float | None = None) -> list[dict]:
        """原子「抢单」：返回本次 tick 应触发的 pending 提醒（已置 claimed）。

        与 planstore.claim_due 同款语义：**必须在真正发送之前调用**，
        否则每 tick 都会重复触发同一条。asyncio 单事件循环内同步临界区即原子
        （ai-groupmate 用 APScheduler 秒级扫库 + 原子领取，此处同契约不同实现）。

        过期规则：距 fire_at 超过 expire 秒（默认 5 分钟）→ 直接置 expired
        不补发（进程休眠/停机错过的不追——用户看到迟到几分钟的提醒更烦）。
        daily 的触发后推进到下一个未来的同一时刻（跳过已错过的周期）。
        """
        now = _now() if now is None else float(now)
        due: list[dict] = []
        for r in self._items:
            if r.get("status") != "pending" or r.get("claimed"):
                continue
            at = float(r.get("fire_at") or 0)
            if at <= 0:
                continue
            if now < at:
                continue
            if now - at > self.expire:
                r["status"] = "expired"
                r["claimed"] = True
                r["claimed_at"] = now
                log.info(jlog("reminder_expired", rid=r.get("id", ""),
                              scope=r.get("scope", ""), late_sec=int(now - at)))
                continue
            # 抢单：daily 推进 / 一次性关闭，都发生在发送前
            if str(r.get("repeat") or "none") == "daily":
                nxt = at
                while nxt <= now:
                    nxt += 86400.0
                r["fire_at"] = nxt
            else:
                r["status"] = "claimed"  # 一次性：发送成功后转 done
            r["claimed"] = True
            r["claimed_at"] = now
            due.append(r)
        if due or any(r.get("status") == "expired" for r in self._items):
            try:
                self._save()
            except ReminderError as e:
                # 落盘失败不能中断本次触发（发送照常），但错误要显式留下痕迹
                log.error(jlog("reminder_claim_save_failed", detail=str(e)[:160]))
        return due

    def mark_result(self, rid: str, ok: bool, err: str = "") -> None:
        """记录一次触发结果（失败不自动重试：ai-groupmate 纪律）。"""
        r = self.get(rid)
        if not r:
            return
        if ok:
            if r.get("status") == "claimed":
                r["status"] = "done"
            r["fired_at"] = _now()
            r["last_error"] = ""
        else:
            r["status"] = "failed"
            r["last_error"] = (err or "未知错误")[:200]
            # daily 的失败也保留 fire_at 原值不动（下个周期自然会到），
            # 不补发本次——避免用户收到重复/迟到的提醒
        try:
            self._save()
        except ReminderError as e:
            log.error(jlog("reminder_result_save_failed", rid=rid,
                           detail=str(e)[:160]))
        log.info(jlog("reminder_fired" if ok else "reminder_fire_failed",
                      rid=rid, scope=r.get("scope", ""), err=r.get("last_error", "")))


#: 模块级单例（插件入口直接用；单测各自 new 隔离）
reminder_store = ReminderStore()


class ReminderParser:
    """LLM 时间表达式解析（服务 /提醒 命令族，2026-10-20）。

    禁正则兜底：用户说「半小时后」「明早八点」「下周一」——正则只能覆盖
    一小半句式且错了很难看，dateparser 又是个黑盒。本项目的选择是**只信
    LLM**（prompt 注入当前时间，输出受 PARSER_SYSTEM 约束的 JSON），
    解析失败显式告知用户换个说法，绝不拿正则结果偷偷顶上（禁止兜底）。
    """

    def __init__(self, client=None) -> None:
        if client is None:
            from .llm import LLMClient
            client = LLMClient()
        self.client = client

    @staticmethod
    def _system_prompt() -> str:
        now = _now()
        return PARSER_SYSTEM.format(
            now_human=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            now_ts="%.0f" % now,
            min_delay=MIN_DELAY,
            max_delay_days=int(MAX_DELAY / 86400),
        )

    @staticmethod
    def _validate(plan: dict, now: float) -> dict:
        """REDUN-03：校验 LLM 输出的 JSON 计划，不过则显式错误。"""
        if not isinstance(plan, dict):
            raise ReminderError("提醒解析失败：模型没按约定返回 JSON")
        action = str(plan.get("action") or "").strip().lower()
        if action not in ("create", "list", "modify", "cancel"):
            raise ReminderError("提醒解析失败：无法识别你想做什么（create/list/"
                                "modify/cancel），换个说法试试")
        out = {"action": action,
               "text": str(plan.get("text") or "").strip()[:200],
               "repeat": str(plan.get("repeat") or "none").strip().lower(),
               "id": str(plan.get("id") or "").strip(),
               "fire_at": 0.0}
        if out["repeat"] not in _REPEATS:
            out["repeat"] = "none"
        if action in ("create", "modify"):
            raw = plan.get("fire_at")
            try:
                at = float(raw)
            except (TypeError, ValueError):
                raise ReminderError("提醒解析失败：模型给的时间不是数字，"
                                    "换个明确的说法试试（如「20 分钟后」）")
            if not (now + MIN_DELAY <= at <= now + MAX_DELAY):
                raise ReminderError("提醒时间得在 %.0f 秒到 %.0f 天之间，"
                                    "换个说法试试" % (MIN_DELAY, MAX_DELAY / 86400))
            out["fire_at"] = at
        if action == "create" and not out["text"]:
            raise ReminderError("提醒解析失败：没听清提醒内容，再说一遍要提醒啥")
        return out

    async def parse(self, user_text: str, now: float | None = None) -> dict:
        """解析一句自然语言 → 计划 dict（action/text/fire_at/repeat/id）。

        失败抛 ReminderError / LLMError（调用方显式告知用户）。
        """
        text = (user_text or "").strip()
        if not text:
            raise ReminderError("空的提醒指令")
        now = _now() if now is None else float(now)
        try:
            msg = await self.client.chat([
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": text},
            ], temperature=0.2)
        except Exception as e:  # noqa: BLE001 — LLMError 等，显式上抛（禁止兜底）
            from .llm import LLMError
            detail = str(e) if isinstance(e, LLMError) else "%s" % type(e).__name__
            raise ReminderError("提醒解析调用大模型失败：%s" % detail)
        content = str(msg.get("content") or "").strip()
        # 宽容剥掉模型爱加的 markdown 代码块围栏（只剥壳，不做内容兜底）
        if content.startswith("```"):
            content = content.strip("`").strip()
            if content.lower().startswith("json"):
                content = content[4:].strip()
        try:
            plan = json.loads(content)
        except Exception:
            log.warning(jlog("reminder_parse_bad_json", detail=content[:160]))
            raise ReminderError("提醒解析失败：模型返回的不是 JSON，换个说法试试")
        return self._validate(plan, now)


class ReminderScheduler:
    """1s tick 后台调度器：到点经 on_fire 回调发送（回调由调用方注入）。

    on_fire: ``async (item: dict) -> None``，发送失败请 raise（本类记 failed
    并继续，不自动重试）。单例见 reminder_scheduler。
    """

    def __init__(self, store: ReminderStore | None = None,
                 on_fire=None, tick: float | None = None) -> None:
        self.store = store or reminder_store
        self.on_fire = on_fire
        self.tick = max(0.2, float(tick if tick is not None
                                   else _cfg("REMIND_TICK", TICK_INTERVAL)))
        self._task: "asyncio.Task | None" = None
        self._stopped = True

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopped = False
        self._task = asyncio.ensure_future(self._loop())
        log.info(jlog("reminder_scheduler_started", tick=self.tick,
                      pending=self.store.pending_count("")))

    async def stop(self) -> None:
        self._stopped = True
        t = self._task
        self._task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        log.info(jlog("reminder_scheduler_stopped"))

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 单 tick 失败不杀循环（CLOSE-02 记而不吞）
                log.warning(jlog("reminder_tick_failed", error=type(e).__name__,
                                 detail=str(e)[:160]))
            await asyncio.sleep(self.tick)

    async def _tick(self) -> None:
        due = self.store.claim_due()
        for item in due:
            rid = item.get("id", "")
            try:
                if self.on_fire is None:
                    raise ReminderError("调度器未配置发送回调（on_fire）")
                await self.on_fire(item)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 发送失败：记 failed，不重试
                log.warning(jlog("reminder_fire_error", rid=rid,
                                 error=type(e).__name__, detail=str(e)[:160]))
                self.store.mark_result(rid, False, "%s: %s" % (type(e).__name__, e))
            else:
                self.store.mark_result(rid, True)


#: 模块级单例（插件入口注入 on_fire 后 start；单测各自 new 隔离）
reminder_scheduler = ReminderScheduler()


# ---------------- FC 工具 handler 工厂（供 tools/builtins.py 注册） ----------------
def make_fc_handlers(store: ReminderStore | None = None) -> dict:
    """生成 remind_create / remind_list / remind_cancel 三个 FC 工具 handler。

    时间由主对话模型算成绝对 Unix 时间戳（fire_at）传入——工具是确定性
    函数，不做二次自然语言解析（那是 /提醒 命令族 ReminderParser 的职责）。
    """

    st = store or reminder_store

    async def remind_create(args: dict, ctx) -> str:
        item = st.create(text=args.get("text", ""), fire_at=args.get("fire_at", 0),
                         repeat=args.get("repeat", "none"), scope=ctx.scope,
                         user_id=ctx.user_id, group_id=ctx.group_id,
                         is_private=ctx.is_private,
                         channel=getattr(ctx, "channel", "napcat"))
        return ("已设好提醒：%s\n时间：%s\n编号 %s%s"
                % (item["text"],
                   time.strftime("%m-%d %H:%M:%S", time.localtime(item["fire_at"])),
                   item["id"],
                   "（每天重复）" if item["repeat"] == "daily" else ""))

    async def remind_list(args: dict, ctx) -> str:
        items = st.list(ctx.scope)
        if not items:
            return "当前会话没有待触发的提醒"
        lines = ["待触发的提醒："]
        for i, r in enumerate(items, 1):
            lines.append("%d. [%s] %s（%s%s）" % (
                i, r["id"],
                time.strftime("%m-%d %H:%M", time.localtime(r["fire_at"])),
                r["text"][:40],
                "，每天" if r.get("repeat") == "daily" else ""))
        return "\n".join(lines)

    async def remind_cancel(args: dict, ctx) -> str:
        rid = str(args.get("id") or "").strip()
        if not rid:
            # 模型没给 ID：按「取消第几条」的序号兜一层（用户口语说「第 2 个」）
            idx = args.get("index")
            try:
                items = st.list(ctx.scope)
                rid = items[int(idx) - 1]["id"]
            except Exception:
                raise ReminderError("要取消哪条提醒？把编号或序号告诉我")
        item = st.cancel(rid, ctx.scope)
        return "已取消提醒：%s（编号 %s）" % (item["text"], rid)

    return {"remind_create": remind_create,
            "remind_list": remind_list,
            "remind_cancel": remind_cancel}
