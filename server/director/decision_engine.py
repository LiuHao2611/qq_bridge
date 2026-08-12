# -*- coding: utf-8 -*-
"""导演 Agent 管家统筹 · 决策中枢（M2）。

设计依据：导演Agent能力增强_开发手册.md §3 + 设计思路文档（终版）§5/§8。
职责：
  - 全局快照 get_project_snapshot：让导演「看得见」五阶段全流程。
  - 决策点上报 + preflight：A 类（必须上报）攒批一次性问，B 类（自主决策）记日志。
  - 决策点持久化到 decisions 表（M2 db 迁移新增），支持 resolve / list。

红线（手册 §9 / §11）：decision_id 用 uuid4，禁 hash()/random（跨进程不稳定）；
禁静默吞错——所有 DB/探测失败显式抛错 + 记日志；WS 推送失败仅告警不阻断落库。
"""
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional

logger = logging.getLogger("manju.director.decision")

# director/ 位于 server/director，db/realtime 在 server 根；统一把 server 根加入 sys.path。
_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

from realtime import publish  # noqa: E402

PHASES = ("P1", "P2", "P3", "P4", "P5")

# A 类决策点默认清单（手册 §3.2.2）：影响全局 / 不可逆 / 高度主观，必须上报。
# 每个条目带适用阶段；preflight 按阶段收集，攒批一次性推送（禁逐个打断）。
A_CLASS_DEFAULTS = [
    {
        "phase": "P1",
        "title": "整体视觉风格定调",
        "description": "确定全片视觉风格基调（写实/动漫/电影感/水彩绘本等），影响底模与 LoRA 选型。",
        "options": ["写实", "动漫", "电影感", "水彩绘本"],
        "default": "写实",
    },
    {
        "phase": "P1",
        "title": "角色是否使用真人参考",
        "description": "角色卡是否接入真人参考图（IP-Adapter 人脸锁）。否=纯 AI 生成角色。",
        "options": ["使用真人参考", "纯 AI 生成"],
        "default": "纯 AI 生成",
    },
    {
        "phase": "P2",
        "title": "单片段时长上限选多少？",
        "description": "按套餐/节奏约束单场次最大时长；超出自动切分为多分镜（手册 §2 粒度切分）。",
        "options": ["3s", "6s", "9s", "12s"],
        "default": "9s",
    },
    {
        "phase": "P3",
        "title": "seed 策略",
        "description": "分镜出图 seed 策略：固定 seed 保证同角色同镜可复现；每镜随机增强多样性。",
        "options": ["固定seed（可复现）", "每镜随机"],
        "default": "固定seed（可复现）",
    },
    {
        "phase": "P3",
        "title": "真人资产开关",
        "description": "是否开启真人资产注入（N03 将 512 维身份向量注入扩散模型交叉注意力层）。关闭=纯风格化生成。",
        "options": ["开启", "关闭"],
        "default": "开启",
    },
    {
        "phase": "P3",
        "title": "画幅比例",
        "description": "分镜成片画幅（竖屏短剧 9:16 / 横屏 16:9 等）。",
        "options": ["9:16", "16:9", "3:4", "1:1"],
        "default": "9:16",
    },
    {
        "phase": "P4",
        "title": "视频模型选择（受显存限制）",
        "description": "本地 6GB 显存下可选视频模型受限；优先 Wan/CogVideoX，超出显存则降级文生图序列。",
        "options": ["Wan", "CogVideoX", "文生图序列兜底"],
        "default": "文生图序列兜底",
    },
]


@dataclass
class DecisionPoint:
    """决策点（手册 §3.2.1）。decision_id 用 uuid4（禁 hash()/random）。"""

    decision_id: str
    project_id: str
    phase: str
    category: str                      # "A"=必须上报 | "B"=自主决策
    title: str
    description: Optional[str] = None
    options: List[str] = field(default_factory=list)
    default: Optional[str] = None      # UI 默认选中提示（decisions 表未持久化该列，属内存提示）
    status: str = "pending"            # pending|resolved|skipped
    answer: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_row(cls, row):
        return cls(
            decision_id=row["decision_id"],
            project_id=row["project_id"],
            phase=row["phase"],
            category=row["category"],
            title=row["title"],
            description=row["description"],
            options=json.loads(row["options"] or "[]"),
            default=row.get("default"),     # decisions 表无此列 → None
            status=row["status"],
            answer=row["answer"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _infer_phase(conn, pid, roles, shots):
    """由 DB 状态推断当前阶段（启发式，仅供快照展示）。

    - 无分镜：有角色资产→P2（剧本已拆），否则 P1（资产初始化中）；
    - 有分镜无成功生成→P3（分镜编排中）；
    - 部分成功→P4（任务分发中）；全部成功→P5（后期合成）。
    """
    if not shots:
        return "P2" if roles else "P1"
    succeeded = sum(1 for s in shots if s.get("status") == "succeeded")
    if succeeded == 0:
        return "P3"
    if succeeded < len(shots):
        return "P4"
    return "P5"


def get_project_snapshot(pid):
    """全局快照（手册 §3.1）：聚合项目五阶段状态，供导演/悬浮球消费。

    【必须】返回 {phase, assets:{roles,scenes,props}, shots:[{id,status,quality_score}], pending_decisions}。
    数据来源于现有 db（projects/segments/shots/assets/generation_tasks/decisions）。
    """
    import db

    conn = db.get_conn()
    try:
        proj = db.fetch_one(conn, "SELECT * FROM projects WHERE project_id=?", (pid,))
        if not proj:
            raise ValueError("项目不存在: %s" % pid)

        roles = db.fetch_all(
            conn,
            "SELECT asset_id,name,type,role_type,tags,cover_url FROM assets "
            "WHERE project_id=? AND type='role'",
            (pid,),
        )
        scenes = db.fetch_all(
            conn,
            "SELECT asset_id,name,type,tags,cover_url FROM assets "
            "WHERE project_id=? AND type='scene'",
            (pid,),
        )
        props = db.fetch_all(
            conn,
            "SELECT asset_id,name,type,tags,cover_url FROM assets "
            "WHERE project_id=? AND type='prop'",
            (pid,),
        )

        seg_rows = db.fetch_all(
            conn, "SELECT segment_id FROM segments WHERE project_id=?", (pid,)
        )
        seg_ids = [r["segment_id"] for r in seg_rows]
        shots = []
        if seg_ids:
            placeholders = ",".join("?" * len(seg_ids))
            shot_rows = db.fetch_all(
                conn,
                'SELECT shot_id, segment_id, "index" FROM shots '
                "WHERE segment_id IN (%s)" % placeholders,
                seg_ids,
            )
            task_rows = db.fetch_all(
                conn,
                "SELECT segment_id, status, face_similarity FROM generation_tasks "
                "WHERE segment_id IN (%s)" % placeholders,
                seg_ids,
            )
            latest = {}
            for t in task_rows:
                latest[t["segment_id"]] = t  # 同 segment 多任务取其一即可（状态快照）
            for s in shot_rows:
                t = latest.get(s["segment_id"]) or {}
                shots.append(
                    {
                        "id": s["shot_id"],
                        "status": t.get("status") or "pending",
                        "quality_score": t.get("face_similarity"),
                    }
                )

        pend = db.fetch_all(
            conn,
            "SELECT decision_id,phase,category,title,status FROM decisions "
            "WHERE project_id=? AND status='pending'",
            (pid,),
        )
        pending_decisions = [
            {
                "decision_id": p["decision_id"],
                "phase": p["phase"],
                "category": p["category"],
                "title": p["title"],
            }
            for p in pend
        ]

        phase = _infer_phase(conn, pid, roles, shots)
        return {
            "project_id": pid,
            "phase": phase,
            "assets": {
                "roles": [dict(r) for r in roles],
                "scenes": [dict(s) for s in scenes],
                "props": [dict(p) for p in props],
            },
            "shots": shots,
            "pending_decisions": pending_decisions,
        }
    finally:
        conn.close()


def _publish_decision_request(pid, phase, decisions):
    """一次性批量推送 A 类决策清单（手册 §3.2.3 禁止逐个推送）。

    事件名 decision_request（M3 §4.1 定义的导演事件之一，M2 先行接入）；
    复用现有 WS 频道 director:{pid} + realtime.publish（线程安全，无订阅者亦安全）。
    """
    try:
        publish(
            "director:%s" % pid,
            "decision_request",
            {"phase": phase, "decisions": [d.to_dict() for d in decisions]},
        )
    except Exception as e:  # WS 不可用不应阻断决策落库
        logger.warning("[decision] WS 批量推送 decision_request 失败（不影响落库）: %s" % e)


def preflight_check(pid, phase):
    """决策预检（手册 §3.2.3）：收集该阶段所有 A 类决策点，落库并批量推送。

    幂等：同项目+阶段+标题已 pending 则复用，已 resolved 则跳过（不重复问）。
    返回本次需用户确认的 DecisionPoint 列表（可能为已存在的 pending 项）。
    """
    import db

    if phase not in PHASES:
        raise ValueError("未知阶段: %s（应为 %s 之一）" % (phase, "/".join(PHASES)))

    conn = db.get_conn()
    now = int(time.time())
    created = []
    try:
        for tmpl in A_CLASS_DEFAULTS:
            if tmpl["phase"] != phase:
                continue
            # 已 pending → 复用；已 resolved → 跳过
            existing = db.fetch_one(
                conn,
                "SELECT * FROM decisions WHERE project_id=? AND phase=? AND title=? AND status='pending'",
                (pid, phase, tmpl["title"]),
            )
            if existing:
                created.append(DecisionPoint.from_row(existing))
                continue
            resolved = db.fetch_one(
                conn,
                "SELECT * FROM decisions WHERE project_id=? AND phase=? AND title=? AND status='resolved'",
                (pid, phase, tmpl["title"]),
            )
            if resolved:
                continue
            dp = DecisionPoint(
                decision_id=str(uuid.uuid4()),
                project_id=pid,
                phase=phase,
                category="A",
                title=tmpl["title"],
                description=tmpl["description"],
                options=list(tmpl["options"]),
                default=tmpl["default"],
                status="pending",
                created_at=now,
                updated_at=now,
            )
            conn.execute(
                "INSERT INTO decisions "
                "(decision_id, project_id, phase, category, title, description, options, status, answer, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    dp.decision_id,
                    dp.project_id,
                    dp.phase,
                    dp.category,
                    dp.title,
                    dp.description,
                    json.dumps(dp.options, ensure_ascii=False),
                    dp.status,
                    dp.answer,
                    dp.created_at,
                    dp.updated_at,
                ),
            )
            created.append(dp)
        conn.commit()
    finally:
        conn.close()

    if created:
        _publish_decision_request(pid, phase, created)
    return created


def resolve_decision(pid, decision_id, answer):
    """提交用户答案（手册 §3.2.4）：更新 decisions 行 status=resolved + answer。"""
    import db

    conn = db.get_conn()
    now = int(time.time())
    try:
        row = db.fetch_one(
            conn,
            "SELECT * FROM decisions WHERE decision_id=? AND project_id=?",
            (decision_id, pid),
        )
        if not row:
            raise ValueError("决策点不存在: %s" % decision_id)
        conn.execute(
            "UPDATE decisions SET status='resolved', answer=?, updated_at=? WHERE decision_id=?",
            (answer, now, decision_id),
        )
        conn.commit()
        row = db.fetch_one(conn, "SELECT * FROM decisions WHERE decision_id=?", (decision_id,))
        return DecisionPoint.from_row(row)
    finally:
        conn.close()


def list_decisions(pid, status=None):
    """列出项目决策点（手册 §3.2.4 GET /decisions）；status 可选过滤。"""
    import db

    conn = db.get_conn()
    try:
        if status:
            rows = db.fetch_all(
                conn,
                "SELECT * FROM decisions WHERE project_id=? AND status=? ORDER BY created_at ASC",
                (pid, status),
            )
        else:
            rows = db.fetch_all(
                conn,
                "SELECT * FROM decisions WHERE project_id=? ORDER BY created_at ASC",
                (pid,),
            )
        return [DecisionPoint.from_row(r) for r in rows]
    finally:
        conn.close()


def log_b_decision(pid, title, choice, note=None):
    """B 类自主决策（手册 §3.2.2）：引擎自主决定，记入 operation_logs，不阻断用户。"""
    import db

    conn = db.get_conn()
    try:
        diff = {"project_id": pid, "choice": choice, "note": note}
        db.log_operation(
            conn, "director_auto_decision", "decision", None, title, diff=diff
        )
        conn.commit()
    finally:
        conn.close()
