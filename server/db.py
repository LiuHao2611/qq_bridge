# -*- coding: utf-8 -*-
"""
本地 SQLite 数据层 —— 严格按知识库 SCHEMA_INDEX §2 落地。
personal_local_only 模式：单用户、积分 mock、无云端依赖。
所有表名 snake_case，PK 为 UUID（TEXT），枚举以 TEXT + CHECK 约束。
"""
import os
import sqlite3
import uuid
import time
import json
from contextlib import contextmanager

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_db_path():
    """解析运行时数据库路径。

    优先使用 MANJU_DB_PATH 覆盖（PROJECT_MAP §3 文档化的可写快照副本）。
    未设置时：若该快照副本已存在则优先使用它——它是用户实际工作的可写库（含最新数据）；
    仅当快照不存在时回退到工作区 server/app.db（历史种子库）。

    这样无论后端被谁拉起（WorkBuddy 看门狗 / service_console 手动 / launcher），
    只要未显式设置 MANJU_DB_PATH，都会落到正确的可写库，避免：
      - 工作区 app.db 在沙箱/安全删除钩子下为只读 → “readonly database”；
      - 或落到陈旧的种子库而丢失用户在快照中的最新数据。
    """
    override = os.environ.get("MANJU_DB_PATH")
    if override:
        return override
    snapshot = "C:/Users/L/AppData/Local/Temp/manju_app_snapshot.db"
    if os.path.exists(snapshot):
        return snapshot
    return os.path.join(BASE, "server", "app.db")


DB_PATH = _resolve_db_path()

SINGLE_USER_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_POINTS = 1999995162  # 手册 §5/§6/TERMINOLOGY 规定本地模拟默认值

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  user_id      TEXT PRIMARY KEY,
  nickname     VARCHAR(64) NOT NULL DEFAULT '小志',
  avatar_url   TEXT,
  membership_tier TEXT NOT NULL DEFAULT 'personal'
                CHECK (membership_tier IN ('personal','vip','ultimate_vip','ultimate_svip')),
  points_balance BIGINT NOT NULL DEFAULT 1999995162,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now')),
  updated_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS projects (
  project_id   TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  name         VARCHAR(128) NOT NULL,
  description  TEXT,
  project_type TEXT NOT NULL DEFAULT 'short_drama'
                CHECK (project_type IN ('short_drama','memoir')),
  thumbnail_url TEXT,
  ratio        TEXT NOT NULL DEFAULT '16:9'
                CHECK (ratio IN ('16:9','9:16','1:1','4:3','21:9')),
  resolution   TEXT NOT NULL DEFAULT '720p'
                CHECK (resolution IN ('480p','720p','1080p','2k','4k')),
  preset_id    TEXT NOT NULL DEFAULT 'flash-long'
                CHECK (preset_id IN ('pro','flash-short','flash-long','max')),
  is_favorite  INTEGER NOT NULL DEFAULT 0,
  is_draft     INTEGER NOT NULL DEFAULT 0,
  progress     INTEGER NOT NULL DEFAULT 0,
  tags         TEXT,
  deleted_at   TIMESTAMP,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now')),
  updated_at   TIMESTAMP DEFAULT (strftime('%s','now')),
  last_opened_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS segments (
  segment_id   TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES projects(project_id),
  "index"      INTEGER NOT NULL,
  title        VARCHAR(128) DEFAULT '片段',
  duration     INTEGER NOT NULL DEFAULT 12 CHECK (duration IN (5,8,10,12,15)),
  shot_count   INTEGER NOT NULL DEFAULT 3,
  generation_task_id TEXT,
  video_url    TEXT,
  thumbnail_url TEXT,
  status       TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','saved','generating','succeeded','failed','cancelled')),
  scene_description TEXT,
  audio_enabled INTEGER NOT NULL DEFAULT 1,
  asset_humanize INTEGER NOT NULL DEFAULT 1,
  return_last_frame INTEGER NOT NULL DEFAULT 0,
  seed         INTEGER NOT NULL DEFAULT -1,
  resolution   VARCHAR(8),
  ratio        VARCHAR(8),
  created_at   TIMESTAMP DEFAULT (strftime('%s','now')),
  updated_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS shots (
  shot_id      TEXT PRIMARY KEY,
  segment_id   TEXT NOT NULL REFERENCES segments(segment_id),
  "index"      INTEGER NOT NULL,
  duration     INTEGER NOT NULL DEFAULT 4 CHECK (duration > 0),
  shot_type    TEXT,
  camera_movement TEXT,
  camera_angle TEXT,
  focal_length VARCHAR(16),
  description  TEXT,
  voiceover    TEXT,
  dialogue     TEXT,
  asset_refs   TEXT,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now')),
  updated_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id     TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  name         VARCHAR(128) NOT NULL,
  type         TEXT NOT NULL CHECK (type IN ('role','scene','prop','voice')),
  role_type    TEXT CHECK (role_type IN ('protagonist','supporting','antagonist')),
  description  TEXT,
  cover_url    TEXT,
  tags         TEXT,
  is_global    INTEGER NOT NULL DEFAULT 1,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now')),
  updated_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS asset_stages (
  stage_id     TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(asset_id),
  stage_name   TEXT NOT NULL,
  age          INTEGER NOT NULL,
  reference_image_url TEXT NOT NULL DEFAULT '',
  face_vector  BLOB,
  selected     INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS segment_asset_refs (
  ref_id       TEXT PRIMARY KEY,
  segment_id   TEXT NOT NULL REFERENCES segments(segment_id),
  asset_id     TEXT NOT NULL REFERENCES assets(asset_id),
  asset_type   TEXT NOT NULL CHECK (asset_type IN ('role','scene','prop')),
  stage_id     TEXT,
  used_in      TEXT CHECK (used_in IN ('shot','scene_description','summary')),
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS generation_tasks (
  task_id      TEXT PRIMARY KEY,
  segment_id   TEXT NOT NULL REFERENCES segments(segment_id),
  runninghub_task_id VARCHAR(64),
  local_task_id VARCHAR(64) NOT NULL,
  model_id     VARCHAR(64) NOT NULL DEFAULT 'seedance-2.0-mini',
  params       TEXT,
  asset_refs_snapshot TEXT,
  asset_humanize INTEGER NOT NULL DEFAULT 0,
  return_last_frame INTEGER NOT NULL DEFAULT 0,
  seed         INTEGER NOT NULL DEFAULT -1,
  pre_auth_id  TEXT NOT NULL,
  cost_credits INTEGER,
  result_url   TEXT,
  thumbnail_url TEXT,
  status       TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','succeeded','failed','cancelled','retrying')),
  priority     INTEGER NOT NULL DEFAULT 0,
  retry_count  INTEGER NOT NULL DEFAULT 0,
  error_code   VARCHAR(32),
  error_message TEXT,
  started_at   TIMESTAMP,
  finished_at  TIMESTAMP,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS generation_logs (
  log_id       TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL REFERENCES generation_tasks(task_id),
  level        TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info','warning','error')),
  message      TEXT NOT NULL,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS point_transactions (
  txn_id       TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  amount       INTEGER NOT NULL,
  type         TEXT NOT NULL CHECK (type IN ('estimated','deducted','refunded','recharged','rewarded','expired')),
  task_id      TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS messages (
  message_id   TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  type         TEXT NOT NULL CHECK (type IN ('system','task','credits','activity')),
  title        VARCHAR(128) NOT NULL,
  content      TEXT NOT NULL,
  action_url   VARCHAR(255),
  is_read      INTEGER NOT NULL DEFAULT 0,
  is_pinned    INTEGER NOT NULL DEFAULT 0,
  expires_at   TIMESTAMP,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS operation_logs (
  log_id       TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(user_id),
  action       VARCHAR(32) NOT NULL,
  object_type  VARCHAR(32),
  object_id    TEXT,
  object_name  VARCHAR(128),
  diff         TEXT,
  status       TEXT NOT NULL DEFAULT 'success' CHECK (status IN ('success','failed')),
  cost_credits INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS settings (
  key          VARCHAR(64) PRIMARY KEY,
  value        TEXT NOT NULL,
  updated_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS templates (
  template_id  TEXT PRIMARY KEY,
  name         VARCHAR(128) NOT NULL,
  category     TEXT CHECK (category IN ('drama','ad','holiday','business','vlog','knowledge')),
  preset       TEXT NOT NULL,
  duration_s   INTEGER NOT NULL DEFAULT 60,
  ratio        VARCHAR(8) NOT NULL DEFAULT '16:9',
  resolution   VARCHAR(8) NOT NULL DEFAULT '720p',
  tags         TEXT,
  use_count    INTEGER NOT NULL DEFAULT 0,
  rating       REAL,
  owner_id     TEXT,
  is_public    INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS memoirs (
  memoir_id    TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES projects(project_id),
  subject_name VARCHAR(128) NOT NULL,
  background_text TEXT,
  storyboard_count INTEGER NOT NULL DEFAULT 4,
  target_ratio VARCHAR(8) NOT NULL DEFAULT '16:9',
  target_resolution VARCHAR(8) NOT NULL DEFAULT '720p',
  enhance_emotion VARCHAR(64),
  exclude_content TEXT,
  reference_photos TEXT,
  timeline_events TEXT,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS memoir_chapters (
  chapter_id   TEXT PRIMARY KEY,
  memoir_id    TEXT NOT NULL REFERENCES memoirs(memoir_id),
  chapter_index INTEGER NOT NULL,
  title        VARCHAR(128) NOT NULL,
  era          VARCHAR(64),
  emotion      VARCHAR(64),
  storyboard_image_url TEXT,
  storyboard_prompt TEXT,
  shot_data    TEXT,
  audio_notes  TEXT,
  color_palette TEXT,
  mood_keywords TEXT,
  created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn):
    """补齐 AGENT_SPEC_15 所需的 memoir 字段（兼容已存在的库文件）。"""
    alters = [
        ("memoirs", "person_perspective TEXT NOT NULL DEFAULT '第一人称'"),
        ("memoir_chapters", "duration INTEGER NOT NULL DEFAULT 15"),
        ("memoir_chapters", "video_url TEXT"),
        ("memoir_chapters", "status TEXT NOT NULL DEFAULT 'draft'"),
        ("memoir_chapters", "last_task_id TEXT"),
        ("generation_tasks", "next_attempt_at TIMESTAMP"),
        # §09 MEM-04 人物锚点 / MEM-15 四模型独立配置
        ("memoirs", "character_anchors TEXT NOT NULL DEFAULT '{}'"),
        ("memoirs", "character_anchors_locked INTEGER NOT NULL DEFAULT 0"),
        ("memoirs", "model_config TEXT NOT NULL DEFAULT '{}'"),
        # §09 MEM-06 摄影机路径 / MEM-07 灯光方案 / MEM-08 情绪色卡
        ("memoir_chapters", "shot_table_camera_paths TEXT NOT NULL DEFAULT '[]'"),
        ("memoir_chapters", "lighting_scheme TEXT NOT NULL DEFAULT '{}'"),
        ("memoir_chapters", "emotion_palette TEXT NOT NULL DEFAULT '[]'"),
        # §09 STR-03 首帧参数（不直出图）
        ("projects", "first_frame_mode TEXT NOT NULL DEFAULT 'no_output' CHECK (first_frame_mode IN ('no_output','output'))"),
        # 修正：memoir_chapters 缺 updated_at（reorder / patch_chapter 均依赖），补齐以符合表约定
        ("memoir_chapters", "updated_at INTEGER NOT NULL DEFAULT 0"),
        # 修正：memoirs 缺 updated_at（patch_memoir 依赖）；messages 缺 deleted_at（delete_notification 软删依赖）
        ("memoirs", "updated_at INTEGER NOT NULL DEFAULT 0"),
        ("messages", "deleted_at INTEGER"),
        # 修正：templates 缺 updated_at（patch_template 依赖）
        ("templates", "updated_at INTEGER NOT NULL DEFAULT 0"),
        # 修正：shots 缺 image_prompt 列，导致分镜 prompt 保存即丢失
        ("shots", "image_prompt TEXT"),
        # 修正：shots 缺 negative_prompt / setting 列，导致反向提示词与镜头设定保存即丢失（字段补全 #4）
        ("shots", "negative_prompt TEXT"),
        ("shots", "setting TEXT"),
        # Phase 1 全局资产初始化：项目级资产隔离 + 真实上传文件路径
        ("assets", "project_id TEXT"),
        ("assets", "asset_path TEXT"),
        ("assets", "cover_path TEXT"),
        # §08 LOG-01/03：生成任务结构化追踪 id（贯穿任务↔日志，便于链路排查）
        ("generation_tasks", "trace_id TEXT"),
        ("generation_logs", "trace_id TEXT"),
        # Phase 4 任务分发与异步AI推理：实时进度 + 外部异步引擎回传关联 id
        ("generation_tasks", "progress REAL DEFAULT 0"),
        ("generation_tasks", "external_id TEXT"),
        # N04/N05 首尾帧锚定（手册 §3.3.4/§3.3.5）：存储前序分镜提取的尾帧路径，供同场景连续分镜作首帧参考
        ("generation_tasks", "last_frame_path TEXT"),
        # 空间站位预规划蓝图（设计逻辑「空间站位预规划」/ §3.4.4）：多人分镜角色相对位置/朝向/视线，
        # 由 ScriptParserAgent(N02) 计算并随 shots 落库，前端站位条可视化；GET 端点 SELECT * 自动带回。
        ("shots", "stage_blueprint TEXT"),
        # DirectorOrb v2（悬浮球化身）所需可观测性字段：全部 ADD COLUMN，向后兼容，无创性。
        # 设计：悬浮球不新增独立检测，只聚合导演 Agent 已有数据，这些列用于承接既有品控/质量结果。
        ("assets", "quality_flags TEXT NOT NULL DEFAULT '{}'"),   # P1 单体质量标记（人脸/角度/姿势/描述）
        ("assets", "color_signature TEXT"),                        # P1 风格一致性（颜色直方图指纹）
        ("generation_tasks", "face_similarity REAL"),             # P4 品控：人脸相似度(≥0.8 通过)
        ("generation_tasks", "first_frame_match REAL"),           # P4 品控：首尾帧衔接匹配度
        ("generation_tasks", "degrade_reason TEXT"),              # P4 降级原因透出（诚实告知用户）
        ("shots", "recipe_override TEXT NOT NULL DEFAULT '{}'"),  # P3 用户手动配方覆盖(user > style_tags)
        # 导演Agent能力增强 M2（决策点 + 意图翻译产物落库）：generation_tasks 存翻译后的 ComfyUI workflow
        ("generation_tasks", "workflow_json TEXT"),
    ]
    for tbl, col_def in alters:
        col = col_def.split(" ", 1)[0]
        try:
            conn.execute("ALTER TABLE %s ADD COLUMN %s" % (tbl, col_def))
        except Exception:
            # 列已存在（SQLite 不支持 IF NOT EXISTS），忽略
            pass
    # 回忆录章节生成任务（与 segment 任务解耦，避免 generation_tasks.segment_id 外键约束）
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memoir_tasks (
             task_id     TEXT PRIMARY KEY,
             memoir_id   TEXT NOT NULL,
             chapter_id  TEXT NOT NULL,
             kind        TEXT NOT NULL,
             status      TEXT NOT NULL DEFAULT 'queued',
             result_url  TEXT,
             error_message TEXT,
             pre_auth_id TEXT,
             cost_credits INTEGER,
             created_at  TIMESTAMP DEFAULT (strftime('%s','now')),
             finished_at TIMESTAMP
           )"""
    )
    # 文生图独立任务队列（与视频 generation_tasks 解耦，无 segment 外键依赖）
    conn.execute(
        """CREATE TABLE IF NOT EXISTS image_tasks (
             task_id      TEXT PRIMARY KEY,
             prompt       TEXT NOT NULL,
             negative_prompt TEXT,
             params       TEXT,
             status       TEXT NOT NULL DEFAULT 'queued'
                           CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
             progress     INTEGER NOT NULL DEFAULT 0,
             result       TEXT,
             error_message TEXT,
             pre_auth_id  TEXT,
             cost_credits INTEGER,
             priority     INTEGER NOT NULL DEFAULT 0,
             retry_count  INTEGER NOT NULL DEFAULT 0,
             next_attempt_at TIMESTAMP,
             started_at   TIMESTAMP,
             finished_at  TIMESTAMP,
             created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
           )"""
    )
    # Phase 1 角色多姿势（KB §02 character_poses）：每个年龄阶段下的多角度参考照，
    # 供 IP-Adapter/ControlNet 三股流绑定使用。stage_id 级联删除由应用层保证。
    conn.execute(
        """CREATE TABLE IF NOT EXISTS asset_poses (
             pose_id      TEXT PRIMARY KEY,
             stage_id     TEXT NOT NULL REFERENCES asset_stages(stage_id) ON DELETE CASCADE,
             asset_id     TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
             pose_name    TEXT NOT NULL DEFAULT 'front' CHECK (pose_name IN ('front','side','back','full_body','custom')),
             reference_image_url TEXT NOT NULL DEFAULT '',
             selected     INTEGER NOT NULL DEFAULT 0,
             created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_poses_stage ON asset_poses(stage_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_poses_asset ON asset_poses(asset_id)"
    )
    # §09 MEM-03 事实锚点（用户对 AI 的硬约束语句）
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memoir_fact_anchors (
             id           TEXT PRIMARY KEY,
             memoir_id    TEXT NOT NULL REFERENCES memoirs(memoir_id) ON DELETE CASCADE,
             anchor_type  TEXT NOT NULL
                           CHECK (anchor_type IN ('event','relation','date','location','other')),
             content      TEXT NOT NULL,
             is_locked    INTEGER NOT NULL DEFAULT 1,
             created_at   TIMESTAMP DEFAULT (strftime('%s','now')),
             updated_at   TIMESTAMP DEFAULT (strftime('%s','now')),
             deleted_at   TIMESTAMP
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mfa_memoir ON memoir_fact_anchors(memoir_id)"
    )
    # §09 API-06 用户反馈
    conn.execute(
        """CREATE TABLE IF NOT EXISTS feedbacks (
             id           TEXT PRIMARY KEY,
             user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
             type         TEXT NOT NULL
                           CHECK (type IN ('bug','feature','question','other')),
             title        VARCHAR(100) NOT NULL,
             content      TEXT NOT NULL,
             attachments  TEXT NOT NULL DEFAULT '[]',
             contact_email VARCHAR(255),
             status       TEXT NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open','triaged','in_progress','resolved','closed')),
             created_at   TIMESTAMP DEFAULT (strftime('%s','now')),
             updated_at   TIMESTAMP DEFAULT (strftime('%s','now')),
             deleted_at   TIMESTAMP
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedbacks_user ON feedbacks(user_id)"
    )
    # 业务模块版本管理器（服务/模块 灰度开关 + 版本快照/回退）
    conn.execute(
        """CREATE TABLE IF NOT EXISTS module_versions (
             id           TEXT PRIMARY KEY,
             module_key   TEXT NOT NULL,
             version      TEXT NOT NULL,
             note         TEXT,
             snapshot_path TEXT,
             is_current   INTEGER NOT NULL DEFAULT 0,
             created_at   INTEGER NOT NULL DEFAULT 0
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mv_key ON module_versions(module_key)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS module_flags (
             module_key   TEXT PRIMARY KEY,
             enabled      INTEGER NOT NULL DEFAULT 1,
             updated_at   INTEGER NOT NULL DEFAULT 0
           )"""
    )
    # DirectorOrb v2 灰度开关：默认关闭（enabled=0）。前端导航守卫 + orb 接口均依赖 get_flags()。
    # 设计 §4.5：悬浮球高频交互组件，先灰度验证建议质量与打扰程度后再默认开启。
    try:
        conn.execute(
            "INSERT OR IGNORE INTO module_flags (module_key, enabled, updated_at) "
            "VALUES ('director_orb_v2', 0, strftime('%s','now'))"
        )
    except Exception:
        pass

    # 导演Agent能力增强 M2（管家统筹 + 角色卡管线）：决策点表（A 类必须上报 / B 类自主决策）。
    # decision_id 用 uuid4（禁 hash()/random，§9/§11 红线）；category ∈ {A,B}；status ∈ {pending,resolved,skipped}。
    conn.execute(
        """CREATE TABLE IF NOT EXISTS decisions (
             decision_id TEXT PRIMARY KEY,
             project_id  TEXT NOT NULL,
             phase       TEXT NOT NULL,
             category    TEXT NOT NULL CHECK (category IN ('A','B')),
             title       TEXT NOT NULL,
             description TEXT,
             options     TEXT NOT NULL DEFAULT '[]',
             status      TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','resolved','skipped')),
             answer      TEXT,
             created_at  TIMESTAMP DEFAULT (strftime('%s','now')),
             updated_at  TIMESTAMP DEFAULT (strftime('%s','now'))
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_project_status ON decisions(project_id, status)"
    )

    # 2026-08-09 修正：asset_stages.stage_name 原枚举 CHECK 限制自由/层级命名（如 周岩-青年），迁移移除。
    _migrate_asset_stages_free_stage_name(conn)


def _migrate_asset_stages_free_stage_name(conn):
    """如果现有 asset_stages 仍带旧枚举 CHECK，则重建表以解除命名限制。"""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='asset_stages'"
        ).fetchone()
        if not row or not row[0] or "childhood" not in row[0]:
            return  # 已无旧枚举约束（或表不存在）
    except Exception:
        return
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        conn.execute(
            """CREATE TABLE asset_stages_new (
                 stage_id     TEXT PRIMARY KEY,
                 asset_id     TEXT NOT NULL REFERENCES assets(asset_id),
                 stage_name   TEXT NOT NULL,
                 age          INTEGER NOT NULL,
                 reference_image_url TEXT NOT NULL DEFAULT '',
                 face_vector  BLOB,
                 selected     INTEGER NOT NULL DEFAULT 0,
                 created_at   TIMESTAMP DEFAULT (strftime('%s','now'))
               )"""
        )
        conn.execute("INSERT INTO asset_stages_new SELECT * FROM asset_stages")
        conn.execute("DROP TABLE asset_stages")
        conn.execute("ALTER TABLE asset_stages_new RENAME TO asset_stages")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass


def init_db():
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        # 单用户初始化（personal_local_only）
        cur = conn.execute("SELECT 1 FROM users WHERE user_id=?", (SINGLE_USER_ID,))
        if not cur.fetchone():
            conn.execute(
                "INSERT INTO users (user_id, nickname, membership_tier, points_balance) VALUES (?,?,?,?)",
                (SINGLE_USER_ID, "小志", "personal", DEFAULT_POINTS),
            )
        conn.commit()
    finally:
        conn.close()


def uid():
    return str(uuid.uuid4())


def now_ms():
    return int(time.time() * 1000)


def row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# ----------------------------- 通用查询助手 -----------------------------
def fetch_one(conn, sql, params=()):
    cur = conn.execute(sql, params)
    return row_to_dict(cur.fetchone())


def fetch_all(conn, sql, params=()):
    cur = conn.execute(sql, params)
    return [row_to_dict(r) for r in cur.fetchall()]


# ----------------------------- 表名常量（单一来源，避免拼写散落） -----------------------------
TABLES = {
    "users": "users",
    "projects": "projects",
    "segments": "segments",
    "shots": "shots",
    "assets": "assets",
    "asset_stages": "asset_stages",
    "asset_poses": "asset_poses",
    "segment_asset_refs": "segment_asset_refs",
    "generation_tasks": "generation_tasks",
    "generation_logs": "generation_logs",
    "image_tasks": "image_tasks",
    "memoir_tasks": "memoir_tasks",
    "point_transactions": "point_transactions",
    "messages": "messages",
    "operation_logs": "operation_logs",
    "settings": "settings",
    "templates": "templates",
    "memoirs": "memoirs",
    "memoir_chapters": "memoir_chapters",
    "memoir_fact_anchors": "memoir_fact_anchors",
    "feedbacks": "feedbacks",
    "module_versions": "module_versions",
    "module_flags": "module_flags",
}


# ----------------------------- 事务与 CRUD 封装 -----------------------------
# 安全提示：以下 helper 的 table / where / order_by 参数直接拼入 SQL，调用方必须
# 保证这些标识符来自内部常量（TABLES）或硬编码，不得直接拼接用户输入。


@contextmanager
def transaction(conn):
    """自动提交/回滚的事务上下文。

    用法：
        with transaction(conn) as c:
            insert_row(c, ...)
            update_row(c, ...)
    """
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _build_insert(table, data):
    cols = list(data.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), placeholders)
    return sql, tuple(data.values())


def _build_update(table, data, pk):
    data = {k: v for k, v in data.items() if k != pk}
    if not data:
        raise ValueError("update data is empty")
    cols = ["%s=?" % k for k in data.keys()]
    sql = "UPDATE %s SET %s WHERE %s=?" % (table, ",".join(cols), pk)
    return sql, tuple(data.values())


def insert_row(conn, table, data, returning=None):
    """插入一行并返回指定列（SQLite 3.35+ 支持 RETURNING）。

    :param returning: None 或单列名，如 'id'
    :return: returning 指定时返回该列值，否则 None
    """
    sql, params = _build_insert(table, data)
    if returning:
        cur = conn.execute(sql + " RETURNING %s" % returning, params)
        row = cur.fetchone()
        return row[0] if row else None
    conn.execute(sql, params)
    return None


def update_row(conn, table, row_id, data, pk="id"):
    """按主键更新。data 中即使含 pk 也会被忽略。返回影响行数。"""
    sql, params = _build_update(table, data, pk)
    cur = conn.execute(sql, params + (row_id,))
    return cur.rowcount


def delete_row(conn, table, row_id, pk="id"):
    """按主键删除。返回影响行数。"""
    cur = conn.execute("DELETE FROM %s WHERE %s=?" % (table, pk), (row_id,))
    return cur.rowcount


def get_by_id(conn, table, row_id, pk="id"):
    """按主键查询单行。"""
    return fetch_one(conn, "SELECT * FROM %s WHERE %s=?" % (table, pk), (row_id,))


def exists(conn, table, where, params=()):
    """条件是否存在记录（where 语句直接拼入 SQL，调用方保证安全）。"""
    sql = "SELECT 1 FROM %s WHERE %s LIMIT 1" % (table, where)
    return conn.execute(sql, params).fetchone() is not None


def count_rows(conn, table, where=None, params=()):
    """计数。"""
    sql = "SELECT COUNT(*) FROM %s" % table
    if where:
        sql += " WHERE %s" % where
    return conn.execute(sql, params).fetchone()[0]


def list_rows(conn, table, where=None, params=(), order_by=None, limit=None, offset=None):
    """列表查询（where / order_by 直接拼入 SQL，调用方保证安全）。"""
    sql = "SELECT * FROM %s" % table
    if where:
        sql += " WHERE %s" % where
    if order_by:
        sql += " ORDER BY %s" % order_by
    if limit is not None:
        sql += " LIMIT %d" % int(limit)
    if offset is not None:
        sql += " OFFSET %d" % int(offset)
    return fetch_all(conn, sql, params)


def upsert_row(conn, table, data, conflict_columns, update_columns=None):
    """SQLite upsert（INSERT ... ON CONFLICT DO UPDATE）。

    :param conflict_columns: 冲突判定列名列表，如 ['user_id', 'key']
    :param update_columns: 需要更新的列名列表；None 表示更新除冲突列外的所有列
    """
    cols = list(data.keys())
    placeholders = ",".join(["?"] * len(cols))
    conflict = ",".join(conflict_columns)
    if update_columns is None:
        update_columns = [c for c in cols if c not in conflict_columns]
    updates = ["%s=excluded.%s" % (c, c) for c in update_columns]
    if not updates:
        raise ValueError("upsert has no columns to update")
    sql = "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(%s) DO UPDATE SET %s" % (
        table, ",".join(cols), placeholders, conflict, ",".join(updates)
    )
    conn.execute(sql, tuple(data.values()))


# ----------------------------- 业务封装（与现有模块衔接） -----------------------------
def ensure_user(conn):
    """确保单用户存在。"""
    if not exists(conn, TABLES["users"], "user_id=?", (SINGLE_USER_ID,)):
        insert_row(conn, TABLES["users"], {
            "user_id": SINGLE_USER_ID,
            "nickname": "小志",
            "membership_tier": "personal",
            "points_balance": DEFAULT_POINTS,
        })


def get_user_points(conn):
    """获取当前单用户积分余额。"""
    row = get_by_id(conn, TABLES["users"], SINGLE_USER_ID, pk="user_id")
    return row["points_balance"] if row else DEFAULT_POINTS


def set_user_points(conn, balance):
    """设置当前单用户积分余额。"""
    update_row(conn, TABLES["users"], SINGLE_USER_ID,
               {"points_balance": balance, "updated_at": now_ms()}, pk="user_id")


def insert_point_transaction(conn, amount, txn_type, task_id=None, idempotency_key=None):
    """写入积分流水。"""
    if idempotency_key is None:
        idempotency_key = uid()
    insert_row(conn, TABLES["point_transactions"], {
        "txn_id": uid(),
        "user_id": SINGLE_USER_ID,
        "amount": amount,
        "type": txn_type,
        "task_id": task_id,
        "idempotency_key": idempotency_key,
    })


def list_projects_for_user(conn, limit=None, offset=None, order_by="updated_at DESC"):
    """我的项目查询（服务于 MyData.vue / 项目中心）。"""
    return list_rows(
        conn, TABLES["projects"],
        where="user_id=? AND deleted_at IS NULL",
        params=(SINGLE_USER_ID,),
        order_by=order_by, limit=limit, offset=offset,
    )


def list_assets_for_user(conn, asset_type=None, limit=None, offset=None, order_by="updated_at DESC"):
    """我的资产查询（服务于 MyData.vue / 资产库）。"""
    where = "user_id=?"
    params = [SINGLE_USER_ID]
    if asset_type:
        where += " AND type=?"
        params.append(asset_type)
    return list_rows(conn, TABLES["assets"], where=where, params=tuple(params),
                     order_by=order_by, limit=limit, offset=offset)


def list_image_tasks_for_user(conn, status=None, limit=None, offset=None, order_by="created_at DESC"):
    """我的成片查询（服务于 MyData.vue / 文生图历史）。"""
    where = "1=1"
    params = []
    if status:
        where += " AND status=?"
        params.append(status)
    return list_rows(conn, TABLES["image_tasks"], where=where, params=tuple(params),
                     order_by=order_by, limit=limit, offset=offset)


def log_operation(conn, action, object_type=None, object_id=None, object_name=None,
                  diff=None, status="success", cost_credits=0):
    conn.execute(
        """INSERT INTO operation_logs
           (log_id, user_id, action, object_type, object_id, object_name, diff, status, cost_credits)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (uid(), SINGLE_USER_ID, action, object_type, object_id, object_name,
         diff and json.dumps(diff, ensure_ascii=False), status, cost_credits),
    )


def push_message(conn, mtype, title, content, action_url=None):
    conn.execute(
        """INSERT INTO messages (message_id, user_id, type, title, content, action_url)
           VALUES (?,?,?,?,?,?)""",
        (uid(), SINGLE_USER_ID, mtype, title, content, action_url),
    )
    # §5.10.4 / §18：消息实时推送（WebSocket 优先；REDUN-01 前端轮询兜底）
    try:
        from realtime import publish as _ws_publish
        _ws_publish("notifications", "new",
                    {"type": mtype, "title": title, "content": content, "action_url": action_url})
    except Exception:
        pass


if __name__ == "__main__":
    import json
    init_db()
    print("DB initialized at", DB_PATH)
