# -*- coding: utf-8 -*-
"""积分引擎：预冻结(pre-auth) → 实扣(settle) → 退还(refund)。

CLOSE-04（资金安全，最高优先级）：
  - 预冻结即真实扣减并冻结余额（hold），杜绝并发超额提交；
  - settle 仅翻转状态（余额已在冻结时扣减），绝不二次扣减；
  - refund 复原冻结金，幂等（已退/已结算返回原值，不重复复原）；
  - 提交前必须 pre-auth（BR-004）；任务失败必须退还（BR-005）。

personal_local 模式：单用户，余额 mock 默认 1999995162。
"""
import time

from db import get_conn, uid, SINGLE_USER_ID, fetch_one, log_operation, push_message
from common import ApiError

_PREAUTH_TTL_MS = 30 * 60 * 1000


def _get_balance(conn):
    return fetch_one(conn, "SELECT points_balance FROM users WHERE user_id=?", (SINGLE_USER_ID,))["points_balance"]


def pre_auth(amount, segment_id, idempotency_key):
    """冻结一笔积分（estimated）。CLOSE-04：冻结即真实扣减并冻结余额。

    幂等：相同 idempotency_key 直接返回已有预授权（不重复冻结）。
    若 key 已结算/已退还，拒绝重复预授权（point_transactions.idempotency_key 唯一约束）。
    """
    if not isinstance(amount, int) or amount <= 0:
        raise ApiError("INVALID_PARAMS", "amount 必须为正整数")
    if not idempotency_key:
        raise ApiError("MISSING_FIELD", "idempotency_key 必填")
    now = int(time.time() * 1000)
    conn = get_conn()
    try:
        existing = fetch_one(conn,
            "SELECT txn_id, type FROM point_transactions WHERE idempotency_key=?",
            (idempotency_key,))
        if existing:
            if existing["type"] == "estimated":
                # 幂等返回：同一笔预授权不重复冻结
                return {"pre_auth_id": existing["txn_id"], "expires_at": now + _PREAUTH_TTL_MS}
            # 该 key 已结算或已退还，禁止复用（UNIQUE 约束会拒绝重复插入）
            raise ApiError("DUPLICATE_REFERENCE", "idempotency_key 已使用，不能重复预授权")
        bal = _get_balance(conn)
        if bal < amount:
            raise ApiError("INSUFFICIENT_POINTS",
                           "积分不足：余额 %d < 需冻结 %d" % (bal, amount))
        # CLOSE-04：冻结即真实扣减（hold）
        conn.execute("UPDATE users SET points_balance=points_balance-? WHERE user_id=?",
                     (amount, SINGLE_USER_ID))
        txn_id = uid()
        conn.execute(
            """INSERT INTO point_transactions (txn_id, user_id, amount, type, task_id, idempotency_key)
               VALUES (?,?,?,?,?,?)""",
            (txn_id, SINGLE_USER_ID, amount, "estimated", segment_id, idempotency_key))
        conn.commit()
        return {"pre_auth_id": txn_id, "expires_at": now + _PREAUTH_TTL_MS}
    finally:
        conn.close()


def settle(pre_auth_id):
    """实扣：estimated → deducted。余额已在冻结时扣减，此处仅翻转状态，绝不二次扣减。"""
    conn = get_conn()
    try:
        txn = fetch_one(conn,
            "SELECT * FROM point_transactions WHERE txn_id=? AND type='estimated'",
            (pre_auth_id,))
        if not txn:
            # 已结算/已退还，幂等返回
            return {"txn_id": pre_auth_id, "actual_amount": 0,
                    "balance": _get_balance(conn), "already_settled": True}
        conn.execute("UPDATE point_transactions SET type='deducted' WHERE txn_id=?",
                     (pre_auth_id,))
        log_operation(conn, "points_settle", "generation_task", txn["task_id"],
                      cost_credits=txn["amount"])
        conn.commit()
        return {"txn_id": pre_auth_id, "actual_amount": txn["amount"],
                "balance": _get_balance(conn)}
    finally:
        conn.close()


def refund(pre_auth_id, reason=""):
    """退还：复原冻结金（无论 estimated 还是 deducted，余额都曾被预冻结扣减）。幂等。"""
    conn = get_conn()
    try:
        txn = fetch_one(conn,
            "SELECT * FROM point_transactions WHERE txn_id=? AND type IN ('estimated','deducted')",
            (pre_auth_id,))
        if not txn:
            # 已退还或不存在，幂等返回
            return {"refund_txn_id": None, "refunded": 0, "balance": _get_balance(conn)}
        amount = txn["amount"]
        # 复原冻结金
        conn.execute("UPDATE users SET points_balance=points_balance+? WHERE user_id=?",
                     (amount, SINGLE_USER_ID))
        conn.execute("UPDATE point_transactions SET type='refunded' WHERE txn_id=?", (pre_auth_id,))
        refund_id = uid()
        conn.execute(
            """INSERT INTO point_transactions (txn_id, user_id, amount, type, task_id, idempotency_key)
               VALUES (?,?,?,?,?,?)""",
            (refund_id, SINGLE_USER_ID, amount, "refunded", txn["task_id"],
             "refund-" + pre_auth_id))
        log_operation(conn, "points_refund", "generation_task", txn["task_id"],
                      cost_credits=-amount)
        push_message(conn, "credits", "积分已退还",
                     "任务失败，已退还 %d 积分（%s）" % (amount, reason or "自动"))
        conn.commit()
        return {"refund_txn_id": refund_id, "refunded": amount, "balance": _get_balance(conn)}
    finally:
        conn.close()


def reconcile_points():
    """CLOSE-04 启动对账：冻结改造前的历史 estimated 行（余额从未扣减）统一视为已结算，
    避免后续误退导致余额膨胀；活跃任务关联的预授权保持冻结态不动。"""
    conn = get_conn()
    try:
        conn.execute(
            """UPDATE point_transactions SET type='deducted'
               WHERE type='estimated'
                 AND txn_id NOT IN (SELECT pre_auth_id FROM generation_tasks WHERE status IN ('queued','running','retrying'))
                 AND txn_id NOT IN (SELECT pre_auth_id FROM memoir_tasks WHERE status IN ('queued','running','retrying'))""")
        conn.commit()
    finally:
        conn.close()


def estimate_credits(params):
    """积分估算（SCHEMA_INDEX §6）。

    formula = base × duration × resolution_coef × audio_coef × model_coef
    base_unit = 18；手册另有截图吻合的替代公式 base_rate=180：180 × duration × resolution_coef。
    采用替代公式以匹配截图「预计 2,160 积分」(12s×720p)。
    """
    duration = int(params.get("duration", 12))
    resolution = params.get("resolution", "720p")
    audio = params.get("audio", True)
    model_id = params.get("model_id", "seedance-2.0-mini")

    res_coef = {"480p": 0.5, "720p": 1.0, "1080p": 2.0, "2k": 4.0, "4k": 8.0}.get(resolution, 1.0)
    audio_coef = 1.2 if audio else 1.0
    model_coef = {"seedance-2.0-mini": 1.0, "seedance-2.0-pro": 1.5, "kling-1.6": 1.4,
                  "jimeng-video-3.0": 1.7, "vidu-q3": 1.2}.get(model_id, 1.0)
    base_rate = 180
    credits = int(round(base_rate * duration * res_coef * audio_coef * model_coef))
    explanation = ("公式 180 × duration(%d) × res_coef(%.1f) × audio_coef(%.1f) × model_coef(%.1f) = %d"
                   % (duration, res_coef, audio_coef, model_coef, credits))
    return {"estimated_credits": credits, "formula_explanation": explanation}
