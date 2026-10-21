# -*- coding: utf-8 -*-
"""budget/manager —— AI 调用预算闸（2026-10-21）。

语义（对齐 personification 预算 shadow·adaptive，且遵守本项目「禁止降级/诚实」
纪律）：

  - 跟踪每 scope 与全局的每日 AI 调用次数（估算 token 仅做账目）。
  - 三级水位 level：ok（< warn_ratio）/ warn（≥ warn_ratio）/ exhausted（≥ 1.0）。
  - 闸口 check(scope, kind)：
      · kind="proactive"（主动发言等**非必需**调用）→ 硬闸门：全局或 per-scope
        任一 exhausted → 拒绝（ok=False）。这是预算的主要作用面（省最无谓的消耗）。
      · kind="reactive"（用户主动发消息要回）→ **永远允许**（用户明确要求，必须
        回应），但当全局 exhausted 时返回 level="exhausted" 让 caller 附一句诚实提示；
        绝不静默拒绝用户。
      · kind="multimodal"（看图/语音等较贵调用）→ 与 proactive 同款硬闸门。
  - shadow 模式（BUDGET_SHADOW=True）：只记账、不拦任何调用（dry-run，观察用量
    而不限制）—— 对应 personification 的 shadow（试跑不消费）。关闭 shadow 才真正限流。
  - 超预算时**显式告知**原因（reason），不静默、不伪造「额度充足」。

记录：每次 AI 调用后 record(scope)（无论是否被闸口放行，账目都不漏）。
"""
from __future__ import annotations

from ..config import Config, get_logger
from .store import BudgetError, BudgetStore, budget_store

log = get_logger()


class BudgetManager:
    """AI 调用预算闸。"""

    def __init__(self, store: BudgetStore | None = None) -> None:
        self.store = store if store is not None else budget_store

    # ---------------- 配置透传（热生效） ----------------
    @staticmethod
    def _enabled() -> bool:
        return bool(getattr(Config, "BUDGET_ENABLE", False))

    @staticmethod
    def _shadow() -> bool:
        return bool(getattr(Config, "BUDGET_SHADOW", False))

    @staticmethod
    def _global_cap() -> int:
        return max(0, int(getattr(Config, "BUDGET_GLOBAL_DAILY_CALLS", 300) or 0))

    @staticmethod
    def _scope_cap() -> int:
        return max(0, int(getattr(Config, "BUDGET_PER_SCOPE_DAILY_CALLS", 100) or 0))

    @staticmethod
    def _warn_ratio() -> float:
        r = float(getattr(Config, "BUDGET_WARN_RATIO", 0.8) or 0.8)
        return min(1.0, max(0.0, r))

    @staticmethod
    def _est_per_call() -> int:
        return max(0, int(getattr(Config, "BUDGET_EST_TOKEN_PER_CALL", 800) or 0))

    # ---------------- 账目 ----------------
    def record(self, scope: str, calls: int = 1) -> None:
        """记录一次 AI 调用（估算 token 按配置常量计入）。"""
        if not self._enabled():
            return  # 未启用预算 → 不记账（避免无谓 IO）
        try:
            self.store.record(scope, calls=calls,
                             est_tokens=calls * self._est_per_call())
        except BudgetError as e:  # noqa: BLE001 — 账目失败记日志不阻断业务
            log.warning("budget_record_failed", error=str(e)[:160])

    def snapshot(self, scope: str) -> dict:
        """当日用量快照（scope + 全局）。"""
        try:
            s = self.store.get_scope_day(scope)
        except BudgetError:
            s = {"scope_calls": 0, "scope_tokens": 0,
                 "global_calls": 0, "global_tokens": 0}
        g_cap = self._global_cap()
        s_cap = self._scope_cap()
        g_ratio = (s["global_calls"] / g_cap) if g_cap > 0 else 0.0
        s_ratio = (s["scope_calls"] / s_cap) if s_cap > 0 else 0.0
        return {
            **s,
            "global_cap": g_cap,
            "scope_cap": s_cap,
            "global_ratio": g_ratio,
            "scope_ratio": s_ratio,
            "global_level": self._level(g_ratio),
            "scope_level": self._level(s_ratio),
        }

    @staticmethod
    def _level(ratio: float) -> str:
        if ratio >= 1.0:
            return "exhausted"
        if ratio >= BudgetManager._warn_ratio_static():
            return "warn"
        return "ok"

    @classmethod
    def _warn_ratio_static(cls) -> float:
        r = float(getattr(Config, "BUDGET_WARN_RATIO", 0.8) or 0.8)
        return min(1.0, max(0.0, r))

    # ---------------- 闸口 ----------------
    def check(self, scope: str, kind: str = "proactive") -> dict:
        """判断某类调用此刻是否可放行。

        返回 {ok, reason, level, scope_level, remaining_global, remaining_scope}。
          - kind="proactive" / "multimodal"：硬闸门（预算耗尽 → ok=False）。
          - kind="reactive"：永远 ok=True（用户主动要求），但 exhausted 时 level
            标 "exhausted" 让 caller 附诚实提示。
          - shadow 模式：ok 恒 True（只记账不拦）。
        """
        if not self._enabled():
            return self._pass("disabled")
        if self._shadow():
            return self._pass("shadow")
        snap = self.snapshot(scope)
        g_exhausted = snap["global_cap"] > 0 and snap["global_calls"] >= snap["global_cap"]
        s_exhausted = snap["scope_cap"] > 0 and snap["scope_calls"] >= snap["scope_cap"]
        if kind in ("proactive", "multimodal"):
            if g_exhausted:
                return self._deny("global_exhausted", snap)
            if s_exhausted:
                return self._deny("scope_exhausted", snap)
            return self._pass(snap["scope_level"], snap)  # ok / warn
        # reactive：永远放行，但 exhausted 透传给 caller（附诚实提示）
        if g_exhausted:
            return self._pass("exhausted", snap)
        return self._pass(snap["scope_level"], snap)

    def _pass(self, level: str, snap: dict | None = None) -> dict:
        snap = snap or self.snapshot("u:0")
        return {
            "ok": True, "reason": level, "level": level,
            "scope_level": snap.get("scope_level", "ok"),
            "remaining_global": max(0, snap.get("global_cap", 0) - snap.get("global_calls", 0)),
            "remaining_scope": max(0, snap.get("scope_cap", 0) - snap.get("scope_calls", 0)),
        }

    def _deny(self, reason: str, snap: dict) -> dict:
        return {
            "ok": False, "reason": reason, "level": "exhausted",
            "scope_level": snap.get("scope_level", "ok"),
            "remaining_global": max(0, snap.get("global_cap", 0) - snap.get("global_calls", 0)),
            "remaining_scope": max(0, snap.get("scope_cap", 0) - snap.get("scope_calls", 0)),
        }

    # ---------------- 人话状态 ----------------
    def status_text(self, scope: str) -> str:
        if not self._enabled():
            return "预算未启用（AI 调用不计额度）。"
        if self._shadow():
            return "预算影子模式：只记账不限制（dry-run）。"
        snap = self.snapshot(scope)
        lines = ["【AI 调用预算·今日】"]
        lines.append("全局：%d / %d 次（%s）" % (
            snap["global_calls"], snap["global_cap"], snap["global_level"]))
        lines.append("本会话：%d / %d 次（%s）" % (
            snap["scope_calls"], snap["scope_cap"], snap["scope_level"]))
        lines.append("估算 token：全局 %d" % snap["global_tokens"])
        return "\n".join(lines)


#: 模块级单例
budget_manager = BudgetManager()
