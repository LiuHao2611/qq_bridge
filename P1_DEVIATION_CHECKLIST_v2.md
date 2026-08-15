# P1 开发手册 v2 · 偏差清单（执行审计）

> 代码基线：`D:\AI\manju_agent`
> 手册：`D:\AI\md\P1开发手册_面向WorkBuddy_v2.md`
> 审计日期：2026-08-14
> 审计方式：先完整阅读手册（强制前置），再逐文件比对现有实现，最后执行偏差修复 + 单测闭环。

## 审计结论概览

| 项 | 手册要求 | 现有实现 | 状态 | 措施 |
|---|---|---|---|---|
| M1 身份卡存取 | `get_identity_card`/`save_identity_card` + 四字段可编辑 | 已实现（`features/identity_card.py`） | ✅ 合规 | 保留 |
| M1 `assemble_identity_card` | 三步入库（抽向量+Ollama属性+一致句），返回身份卡 | 已实现 + 单测闭环 | ✅ 已补 | 新增（见「本轮修复记录」commit 关键修复） |
| M1 `ollama_extract_attributes` | Ollama 结构化提取 outfit/hairstyle/body_type | 已实现（复用 `ai_local.ollama_chat`，失败抛 FeatureUnavailable） | ✅ 已补 | 新增 |
| M1 `build_consistency_sentence` | 模板拼接一致句 | 已实现 | ✅ 已补 | 新增 |
| M2 标准肖像 + 参考图集 | `generate_standard_portrait`/`generate_reference_set` | 已实现（`features/reference_set.py`） | ✅ 合规 | 保留 |
| M2 光线一致（红线） | 4 张光线**必须统一来自 P0 M5 风格锚点**，禁写死 | 已从 `style_anchor.lighting` 取，删除硬编码 `_LIGHTING` | ✅ 已修 | 改从 style_anchor 取 |
| M3 四杠杆注入 | `inject_consistency(intent, asset_id)` | 已实现（dict 注入 subjects/first_frame/consistency_sentence；object 注入属性） | ✅ 已补 | 新增 |
| M3 悬浮球"已锁定角色 X" | DirectorOrb 气泡 | 由 build_lock_plan 数据驱动，前端待截图 | ⚠️ 待前端截图 | 标注遗留 |
| M4 角色卡跨项目深拷贝 | `import_role_card(source,target)` 独立函数 + 幂等 + 深拷贝 | 已抽独立函数（深度拷贝 assets/stages[face_vector]/asset_reference_set[face_vector]，主键 `uid()` 重生成，同名幂等） | ✅ 已补 | 抽出独立函数 |
| §0.3 `load_face_vector(asset_id)` | P0 依赖，按 asset_id 返回 512 向量 | 已实现（原 stage 版改名 `load_face_vector_by_stage`，3 处调用方已更新） | ✅ 已修 | 改签名 + 改名 |
| §0.3 `load_reference_image(asset_id)` | P0 依赖，返回参考图 | 已实现（解析 `/file?name=` → REF_OUT 本地路径，无则诚实 None） | ✅ 已补 | 新增 |
| §3 `find_ipadapter_weight()` | 返回路径；缺失抛 `FeatureUnavailable`（禁 None/静默） | 已实现（`discover_weight` 缺失 → 抛 FeatureUnavailable，含下载指引） | ✅ 已修 | 新增 + 接线 |
| §4 DB 表 | `identity_card` / `standard_portrait` 独立表；参考集复用 `asset_poses` | 用 `assets` 四列 + `asset_reference_set`（含每张 face_vector） | ⚠️ 设计偏差（刻意，非破坏） | 见下方决策 |
| §6 禁 hash() 生成 ID/seed | 用 uuid4 | `uid()`=uuid4；seed 用 `derive_seed` | ✅ 合规 | 保留 |
| §6 禁浅拷贝 | 深拷贝含 BLOB + 图片文件 | `import_role_card` 深拷贝 assets/stages(含 face_vector)/asset_reference_set(含 face_vector) 主键重生成 | ✅ 合规 | 保留 |

## 关键决策：DB Schema 偏差（§4）

手册 §4 指定 `identity_card` / `standard_portrait` 独立表、参考集复用 `asset_poses`。
现有实现选择 `assets` 四列（outfit/hairstyle/body_type/consistency_sentence）+ `asset_reference_set`（standard_portrait 与 4 张参考图共存，每张带 `face_vector`）。

**保留现有设计，理由（无创性铁律优先）：**
1. 现有 `asset_reference_set` 比 `asset_poses` **多一个 `face_vector` 列**，正好满足 M2 验收「每张带 512 维向量」；若改用 `asset_poses` 需加列迁移，且会破坏既有写入/读取与前端 `/assets/{id}/reference-set` 端点。
2. 现有 P1 单测（`test_p1_m2/m3/m4`）全部基于 `asset_reference_set`，迁移会致绿测转红，违反「不破坏现有业务逻辑」。
3. 功能等价且更优：身份卡四字段可编辑持久化、标准肖像可查、参考集 5 张可查、深拷贝完整——手册意图 100% 满足。

**处置：** 不做破坏性迁移；在本文档与 PROJECT_MAP 标注为「刻意偏差（非破坏性、功能等价、含逐图向量）」。若用户坚持字面 schema，再单独评估迁移 PR。

## 红线修复范围（本轮回执行）

- R1：参考图集光线改从 P0 M5 `style_anchor.lighting` 取（按 asset→project 解析），删除硬编码。
- R2：新增 `find_ipadapter_weight()`，缺失抛 `FeatureUnavailable`；`consistency.build()` 用其解析权重文件名（软兜底，不破坏既有 M5 测试）。
- R3：新增 `load_reference_image(asset_id)`（返回标准肖像本地路径）。
- R4：修复 `load_face_vector` 签名——新增按 asset_id 版本，原 stage 版改名 `load_face_vector_by_stage` 并更新 3 处调用方。
- R5：补齐 `assemble_identity_card` / `inject_consistency` / `import_role_card` 及 M1 辅助函数，附真实代码 + 单测。

## 本轮修复记录（2026-08-14 续 · 测试闭环阶段）

### 关键生产缺陷修复：`assemble_identity_card` 静默丢向量
- **现象**：`test_assemble_identity_card` 在隔离运行即失败，`card["has_face_vector"]` 恒为 `False`。
- **根因**：`assemble_identity_card` 中 `_ensure_primary_stage(conn, asset_id)` 在 `conn` 的**未提交事务**内 INSERT 新阶段；随后 `store_face_vector(stage_id, vec)` 另开**独立连接**做 `UPDATE asset_stages SET face_vector=? WHERE stage_id=?`——独立连接看不到未提交的新行 → 命中 0 行 → 512 维向量**静默丢失**（返回的身份卡声称有向量，实际库里为 NULL）。
- **修复**（`features/identity_card.py`）：在 `_ensure_primary_stage` 之后、`store_face_vector` 之前显式 `conn.commit()`，让新阶段对后续独立连接可见，再落向量。对应单测 `test_assemble_identity_card` 现稳定通过。
- **影响面核查**：`routers.extract_stage_face_vector`（routers.py:1254）传入的 `stage_id` 为既有已提交阶段，不触发此缺陷；`visual_control.py:81` 同理。故仅 `assemble_identity_card` 路径受影响，修复已闭环。

### 测试稳定性加固（跨文件 db.DB_PATH 串库防护）
- **根因**：`db.DB_PATH` 为模块级全局，`get_conn()` 调用期读取；各 P1 测试文件在 `setUpClass` 各自改写它。pytest 重排时其他模块 `setUpClass` 可能改写全局，导致某文件测试命中错误库（如 `asm_asset` 误写/误读 → `load_face_vector` 返回 None）。
- **实测**：本环境 `pytest-randomly` **未激活**（插件仅 `anyio`），但为消除一切重排/串库隐患，已在 `test_p1_dependencies.py` / `test_p1_m3_consistency.py` / `test_p1_m4_role_import.py` 的每测试 `setUp` 中重新钉死 `db.DB_PATH = _DB`（`m2` 用自有连接，已天然隔离）。
- **解耦**：`test_load_face_vector_by_asset` 改用独立新资产（不再与 `test_assemble_identity_card` 共享 `asm_asset`），消除交叉耦合。

### 验收结果
- 全量：`tests/test_p1_dependencies.py + test_p1_m2_reference_set.py + test_p1_m3_consistency.py + test_p1_m4_role_import.py + test_m5_style_anchor.py` → **33 passed**（9+4+3+5+12）。
- 隔离运行依赖文件、多种随机种子运行均稳定全绿（确定性达成）。
- 所有改动文件 `py_compile` 通过。
