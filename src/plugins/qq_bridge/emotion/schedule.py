# -*- coding: utf-8 -*-
"""emotion.schedule —— 日程提取与到期关心（M3，2026-10-20）。

需求（dev_plan M3 / roadmap）：日程 —— 机器人自己记住用户在对话里提到
的安排（面试/考试/出差/约会…），到点主动关心一句「你今天不是面试吗？」。

与 M2 提醒的分工（不重复造轮子）：
  提醒 = 用户主动下指令设的，到点回传原话（reminders.json）；
  日程 = 机器人**自己从对话里听到并记下**的，到点主动关心（本模块）。

设计：
  - 提取：LLM 从一轮对话里抽「未来安排」（标题 + 到期时间戳），
    协议 {items: [{title, due_ts, iso}]}，REDUN-03 校验（时间戳必须
    是未来、标题非空、条数上限）；
  - 关心文案：由 LLM 用人设语气的转述（复用 M9 通知中枢的 rephrase 思路）
    或本模块给模板句——**这里用 LLM 转述**（personification 原则：语气
    交给模型，代码只给事实）；
  - 到期判定：due_schedules(scope, now, window) 取「未通知过 + 未来
    window 秒内到期」的日程；关心后 mark_notified（幂等去重，防重复打扰）。

纪律：CLOSE-05 显式超时；失败显式 ScheduleError；不 import nonebot /
不 import 主包：可裸单测（LLM 客户端可注入）。
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..config import Config, get_logger, jlog

log = get_logger()

EXTRACT_SYSTEM = (
    "你是日程提取器。读一句话，只输出 JSON：{\"items\":[{\"title\":\"简短安排名\","
    "\"due_ts\":Unix秒级时间戳}]}。\n"
    "规则：只抽**未来**的安排（面试/考试/出差/约会/会议/旅行等），过去的不要；\n"
    "拿不准时间就别抽（空数组比错日程好）；最多 3 条；title 不超过 20 字。\n"
    "不要输出多余文字、不要 markdown 代码块。"
)


class ScheduleError(Exception):
    """显式化的日程提取/处理失败。"""


class ScheduleExtractor:
    """对话 → 日程条目（LLM 抽取 + 校验）。"""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _resolve_client(self):
        if self._client is not None:
            return self._client
        from ..tools.llm import LLMClient
        self._client = LLMClient()
        return self._client

    @staticmethod
    def validate(plan: Any, now: float | None = None,
                 max_items: int = 3) -> list[dict]:
        """REDUN-03 校验：结构、未来时间、条数、标题长度。坏条目剔除。"""
        now = time.time() if now is None else float(now)
        if not isinstance(plan, dict):
            raise ScheduleError("抽取结果不是 JSON object")
        raw = plan.get("items") or []
        if not isinstance(raw, list):
            raise ScheduleError("items 必须是数组")
        out = []
        for it in raw[:max_items]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()[:20]
            try:
                due = float(it.get("due_ts"))
            except (TypeError, ValueError):
                continue
            if not title:
                continue
            if due <= now:
                continue  # 过去的安排不记（提醒另说）
            # 远期上限：1 年内（防模型算错成 100 年后）
            if due > now + 365 * 86400:
                continue
            out.append({"title": title, "due_ts": due})
        return out

    async def extract(self, text: str, now: float | None = None) -> list[dict]:
        """抽一轮对话里的未来安排。返回条目列表（可为空——没安排很正常）。"""
        t = (text or "").strip()
        if not t:
            raise ScheduleError("对话内容为空")
        now = time.time() if now is None else float(now)
        client = self._resolve_client()
        hint = "（现在时间：%s）\n%s" % (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), t[:500])
        try:
            msg = await client.chat([
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": hint},
            ], temperature=0.2)
        except Exception as e:  # noqa: BLE001
            from ..tools.llm import LLMError
            detail = str(e) if isinstance(e, LLMError) else type(e).__name__
            raise ScheduleError("日程抽取调用大模型失败：%s" % detail)
        content = str(msg.get("content") or "").strip()
        if content.startswith("```"):  # 只剥围栏，不兜底内容
            content = content.strip("`").strip()
            if content.lower().startswith("json"):
                content = content[4:].strip()
        try:
            plan = json.loads(content)
        except Exception:
            log.warning(jlog("schedule_extract_bad_json", detail=content[:160]))
            raise ScheduleError("日程抽取结果不是 JSON")
        return self.validate(plan, now=now)


def format_due_line(item: dict, now: float | None = None) -> str:
    """日程 → 人话一行（/情感 日程 列表用）。"""
    now = time.time() if now is None else float(now)
    due = float(item.get("due_at") or 0)
    delta = due - now
    if delta <= 0:
        when = "（已到期）"
    elif delta < 3600:
        when = "（%d 分钟后）" % max(1, int(delta // 60))
    elif delta < 86400:
        when = "（%d 小时后）" % int(delta // 3600)
    else:
        when = "（%s）" % time.strftime("%m-%d %H:%M", time.localtime(due))
    return "· %s %s%s" % (item.get("title") or "?", when,
                          " ✅" if item.get("done") else "")


#: 模块级单例（单测各自 new 隔离）
schedule_extractor = ScheduleExtractor()
