# 万能短剧 AI 创作平台 · 数据库层设计说明书

> 本文描述 `manju_agent`（8099 前台）后端 SQLite 数据层的完整设计，包括表结构、字段约束、索引、模块映射、CRUD 封装与扩展规范。
> 与 `server/db.py` 保持单一来源：表名常量 `TABLES`、`_SCHEMA`、`_migrate`、通用 helper 与 CRUD 封装均定义在 `server/db.py`。

## 1. 设计目标与约束

- **personal_local_only**：单用户本地运行，无云端依赖，默认用户 `SINGLE_USER_ID = 00000000-0000-0000-0000-000000000001`。
- **SQLite + WAL**：默认启用 WAL 模式与 busy timeout，兼顾读写并发与可靠性。
- **幂等初始化**：`init_db()` + `_migrate()` 可重复执行，新增列/表对旧库透明。
- **字段约束**：核心业务枚举字段使用 `CHECK` 约束；时间戳统一用 Unix 秒或毫秒整型，避免时区歧义。
- **可扩展性**：新表/新列通过 `_migrate()` 追加；通用 CRUD 封装减少重复 SQL。

## 2. 连接与初始化

| 函数 | 职责 |
|---|---|
| `_resolve_db_path()` | 优先 `MANJU_DB_PATH` 环境变量；否则使用 `C:/Users/L/AppData/Local/Temp/manju_app_snapshot.db` 快照副本；最后回退工作区 `server/app.db`。 |
| `get_conn()` | 创建连接，启用 `row_factory=sqlite3.Row` 与 `PRAGMA foreign_keys=ON`。 |
| `init_db()` | 执行 `_SCHEMA` + `_migrate()` + 单用户初始化。 |
| `_migrate()` | 幂次追加列/表/索引，兼容历史库。 |

## 3. 表结构总览

| 业务域 | 表名 | 说明 |
|---|---|---|
| 用户/积分 | `users`, `point_transactions` | 单用户、积分余额、积分流水（含幂等键）。 |
| 项目/片段/分镜 | `projects`, `segments`, `shots` | 五阶段核心数据模型。 |
| 资产/阶段/姿势 | `assets`, `asset_stages`, `asset_poses`, `segment_asset_refs` | 角色/场景/道具、年龄阶段、多姿势、分镜引用关系。 |
| 生成任务 | `generation_tasks`, `generation_logs`, `image_tasks`, `memoir_tasks` | 视频生成、文生图、回忆录独立任务队列。 |
| 消息/操作审计 | `messages`, `operation_logs` | 站内消息与全量操作日志。 |
| 配置/模板 | `settings`, `templates`, `module_versions`, `module_flags` | KV 设置、模板市场、模块版本与灰度开关。 |
| 回忆录 | `memoirs`, `memoir_chapters`, `memoir_fact_anchors` | 回忆录项目、章节、事实锚点。 |
| 反馈 | `feedbacks` | 用户反馈工单。 |

## 4. 核心表字段与约束

### 4.1 users
| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| user_id | TEXT | PK | 默认单用户 UUID。 |
| nickname | VARCHAR(64) | NOT NULL DEFAULT '小志' | 昵称。 |
| avatar_url | TEXT | — | 头像 URL。 |
| membership_tier | TEXT | CHECK personal/vip/ultimate_vip/ultimate_svip | 会员档位。 |
| points_balance | BIGINT | NOT NULL DEFAULT 1999995162 | 当前积分余额。 |
| created_at/updated_at | TIMESTAMP | DEFAULT strftime('%s','now') | 创建/更新时间。 |

### 4.2 projects
| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| project_id | TEXT | PK | 项目 UUID。 |
| user_id | TEXT | NOT NULL FK → users | 项目所有者。 |
| name | VARCHAR(128) | NOT NULL | 项目名称。 |
| description | TEXT | — | 项目描述。 |
| project_type | TEXT | CHECK short_drama/memoir | 项目类型。 |
| thumbnail_url | TEXT | — | 封面图 URL。 |
| ratio | TEXT | CHECK 16:9/9:16/1:1/4:3/21:9 | 画幅。 |
| resolution | TEXT | CHECK 480p/720p/1080p/2k/4k | 分辨率。 |
| preset_id | TEXT | CHECK pro/flash-short/flash-long/max | 套餐。 |
| is_favorite | INTEGER | DEFAULT 0 | 是否收藏。 |
| is_draft | INTEGER | DEFAULT 0 | 是否草稿。 |
| progress | INTEGER | DEFAULT 0 | 总进度 0–100。 |
| tags | TEXT | — | JSON 标签数组。 |
| deleted_at | TIMESTAMP | — | 软删时间戳。 |
| created_at/updated_at/last_opened_at | TIMESTAMP | — | 时间。 |
| first_frame_mode | TEXT | CHECK no_output/output | 首帧是否输出图片。 |

### 4.3 segments
| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| segment_id | TEXT | PK | 片段 UUID。 |
| project_id | TEXT | NOT NULL FK → projects | 所属项目。 |
| index | INTEGER | NOT NULL | 片段序号（保留字加引号）。 |
| title | VARCHAR(128) | DEFAULT '片段' | 标题。 |
| duration | INTEGER | CHECK 5/8/10/12/15 | 片段时长（秒）。 |
| shot_count | INTEGER | DEFAULT 3 | 分镜数量。 |
| generation_task_id | TEXT | — | 关联生成任务。 |
| video_url/thumbnail_url | TEXT | — | 结果视频/缩略图。 |
| status | TEXT | CHECK draft/saved/generating/succeeded/failed/cancelled | 状态。 |
| scene_description | TEXT | — | 场景描述。 |
| audio_enabled | INTEGER | DEFAULT 1 | 是否启用音频。 |
| asset_humanize | INTEGER | DEFAULT 1 | 真人资产注入。 |
| return_last_frame | INTEGER | DEFAULT 0 | 尾帧锚定。 |
| seed/resolution/ratio | — | — | 生成参数。 |
| created_at/updated_at | TIMESTAMP | — | 时间。 |

### 4.4 shots
| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| shot_id | TEXT | PK | 分镜 UUID。 |
| segment_id | TEXT | NOT NULL FK → segments | 所属片段。 |
| index | INTEGER | NOT NULL | 分镜序号。 |
| duration | INTEGER | CHECK > 0 | 分镜时长。 |
| shot_type/camera_movement/camera_angle/focal_length | TEXT | — | 镜头参数。 |
| description/voiceover/dialogue | TEXT | — | 画面描述/画外音/对白。 |
| image_prompt/negative_prompt/setting | TEXT | — | 正向/反向提示词、镜头设定。 |
| asset_refs | TEXT | — | 历史兼容 JSON。 |
| stage_blueprint | TEXT | — | 空间站位预规划 JSON。 |
| created_at/updated_at | TIMESTAMP | — | 时间。 |

### 4.5 assets / asset_stages / asset_poses / segment_asset_refs
| 表 | 核心字段 | 说明 |
|---|---|---|
| assets | asset_id PK, user_id FK, name, type(role/scene/prop/voice), role_type, description, cover_url, tags, is_global, project_id, asset_path, cover_path | 全局/项目资产。 |
| asset_stages | stage_id PK, asset_id FK, stage_name, age, reference_image_url, face_vector BLOB, selected | 资产年龄阶段；`stage_name` 已解除枚举，支持层级命名如「周岩-青年」。 |
| asset_poses | pose_id PK, stage_id FK, asset_id FK, pose_name(front/side/back/full_body/custom), reference_image_url, selected | 阶段下的多姿势参考图；级联删除。 |
| segment_asset_refs | ref_id PK, segment_id FK, asset_id FK, asset_type, stage_id, used_in, created_at | 片段对资产/阶段的引用关系；支撑 BR-009 唯一引用统计。 |

### 4.6 generation_tasks / generation_logs / image_tasks / memoir_tasks
| 表 | 用途 | 关键字段 |
|---|---|---|
| generation_tasks | 视频生成任务 | task_id, segment_id FK, model_id, params JSON, status, progress, cost_credits, result_url, pre_auth_id, trace_id, external_id, last_frame_path, started_at/finished_at |
| generation_logs | 任务日志 | log_id FK, level, message, trace_id |
| image_tasks | 文生图任务 | task_id, prompt, negative_prompt, params, status, progress, result JSON, pre_auth_id |
| memoir_tasks | 回忆录章节任务 | task_id, memoir_id, chapter_id, kind, status, result_url, pre_auth_id |

### 4.7 point_transactions
| 字段 | 说明 |
|---|---|
| txn_id PK, user_id FK, amount, type(estimated/deducted/refunded/recharged/rewarded/expired), task_id, idempotency_key UNIQUE | 积分流水；幂等键防止重复记账。 |

### 4.8 messages / operation_logs
| 表 | 说明 |
|---|---|
| messages | 站内消息：type(system/task/credits/activity), title, content, action_url, is_read, is_pinned, expires_at, deleted_at（软删）。 |
| operation_logs | 操作审计：action, object_type, object_id, object_name, diff JSON, status, cost_credits。 |

### 4.9 settings / templates / module_versions / module_flags
| 表 | 说明 |
|---|---|
| settings | key PK, value TEXT(JSON), updated_at。 |
| templates | 模板市场/本地模板：category(drama/ad/holiday/business/vlog/knowledge), preset, duration_s, ratio, resolution, use_count, rating, owner_id, is_public。 |
| module_versions | 模块版本快照：module_key, version, snapshot_path, is_current, created_at。 |
| module_flags | 模块灰度开关：module_key PK, enabled, updated_at。 |

### 4.10 memoirs / memoir_chapters / memoir_fact_anchors / feedbacks
| 表 | 说明 |
|---|---|
| memoirs | project_id FK, subject_name, background_text, storyboard_count, target_ratio/resolution, enhance_emotion, exclude_content, reference_photos, timeline_events, character_anchors, character_anchors_locked, model_config, person_perspective。 |
| memoir_chapters | memoir_id FK, chapter_index, title, era, emotion, storyboard_image_url, storyboard_prompt, shot_data, audio_notes, color_palette, mood_keywords, duration, video_url, status, last_task_id, shot_table_camera_paths, lighting_scheme, emotion_palette, updated_at。 |
| memoir_fact_anchors | memoir_id FK, anchor_type(event/relation/date/location/other), content, is_locked, deleted_at（软删）。 |
| feedbacks | user_id FK, type, title, content, attachments JSON, contact_email, status(open/triaged/in_progress/resolved/closed), deleted_at（软删）。 |

## 5. 索引

```sql
-- asset_poses
CREATE INDEX idx_poses_stage ON asset_poses(stage_id);
CREATE INDEX idx_poses_asset ON asset_poses(asset_id);

-- memoir fact anchors
CREATE INDEX idx_mfa_memoir ON memoir_fact_anchors(memoir_id);

-- feedbacks
CREATE INDEX idx_feedbacks_user ON feedbacks(user_id);

-- module versions
CREATE INDEX idx_mv_key ON module_versions(module_key);
```

> 扩展规范：高频列表/过滤字段（如 `projects.user_id`、`assets.type`、`generation_tasks.status`）如成为性能瓶颈，可在 `_migrate()` 中追加索引，保持向后兼容。

## 6. CRUD 封装

全部定义在 `server/db.py`：

| 函数 | 说明 |
|---|---|
| `transaction(conn)` | 上下文管理器，自动 commit/rollback。 |
| `insert_row(conn, table, data, returning=None)` | 插入一行，可选返回指定列。 |
| `update_row(conn, table, row_id, data, pk='id')` | 按主键更新，返回影响行数。 |
| `delete_row(conn, table, row_id, pk='id')` | 按主键删除，返回影响行数。 |
| `upsert_row(conn, table, data, conflict_columns, update_columns=None)` | `INSERT ... ON CONFLICT DO UPDATE`。 |
| `get_by_id(conn, table, row_id, pk='id')` | 按主键查询单行。 |
| `exists(conn, table, where, params=())` | 条件存在性查询。 |
| `count_rows(conn, table, where=None, params=())` | 计数。 |
| `list_rows(conn, table, where=None, params=(), order_by=None, limit=None, offset=None)` | 列表分页。 |
| `fetch_one(conn, sql, params=())` / `fetch_all(conn, sql, params=())` | 原始 SQL 查询。 |
| `row_to_dict(row)` | sqlite3.Row → dict。 |
| `uid()` / `now_ms()` | UUID 与毫秒时间戳。 |
| `log_operation(conn, ...)` / `push_message(conn, ...)` | 审计日志/消息通知。 |

### 6.1 安全约定
`table`、`where`、`order_by` 直接拼入 SQL，调用方必须：
- 使用 `TABLES` 常量或硬编码表名，禁止直接拼接用户输入。
- `where`/`order_by` 中的标识符由后端代码生成；参数值使用 `params` 占位符传递。

## 7. 与功能模块映射

| 功能模块 | 核心表 | 说明 |
|---|---|---|
| 项目中心 / MyData 我的项目 | `projects` | 按 `user_id` + `deleted_at IS NULL` 列表。 |
| 资产库 / MyData 我的资产 | `assets`, `asset_stages`, `asset_poses` | 支持全局/项目级资产、阶段、姿势。 |
| 分镜编辑器 | `segments`, `shots`, `segment_asset_refs` | `shots.asset_refs` 旧字段已由 `segment_asset_refs` 取代，保留兼容。 |
| P4 生产 / 推理监控 | `generation_tasks`, `generation_logs` | 异步 worker 消费，状态机 `queued→running→succeeded/failed/cancelled`。 |
| 文生图 / MyData 我的成片 | `image_tasks` | 独立队列，与视频 `generation_tasks` 解耦。 |
| 回忆录 | `memoirs`, `memoir_chapters`, `memoir_tasks`, `memoir_fact_anchors` | AGENT_SPEC_15。 |
| 会员中心 / 积分 | `users.points_balance`, `point_transactions` | 本地 mock 大积分，`point_transactions` 记录 estimated/deducted/refunded。 |
| 消息中心 | `messages` | WebSocket 实时推送 + 轮询兜底。 |
| 设置/主题 | `settings` | KV 存储，value 为 JSON。 |
| 服务与版本管理 | `module_versions`, `module_flags` | SystemManager 页面数据。 |
| 操作日志 | `operation_logs` | 请求中间件与 ApiError handler 自动写入。 |

## 8. 扩展指南

新增业务表/列的标准流程：

1. 在 `_SCHEMA` 的 `CREATE TABLE IF NOT EXISTS` 区域添加完整表定义（含约束、默认值、索引）。
2. 若需兼容旧库，在 `_migrate()` 的 `alters` 列表追加 `("表名", "列定义")`。
3. 将新表名加入 `TABLES` 常量字典。
4. 在 `docs/db_schema.md` 第 3/4/7 节补充说明。
5. 需要通用 CRUD 直接调用 `insert_row/update_row/delete_row/list_rows`；复杂业务查询仍用 `fetch_one/fetch_all` 手写 SQL。
6. 改动后运行 `py_compile server/db.py` 并启动后端验证 `init_db()` 不报错。

## 9. 运行验证

```bash
cd D:/AI/manju_agent/server
# 语法检查
python -m py_compile db.py

# 使用临时库初始化测试
set MANJU_DB_PATH=D:/AI/manju_agent/.tmp/test.db
python db.py
# 预期输出：DB initialized at D:/AI/manju_agent/.tmp/test.db
```
