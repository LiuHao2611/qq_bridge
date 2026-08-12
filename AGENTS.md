# AGENTS.md · 万能短剧 AI 创作平台（最高行为约束 · 合并版）

> 你正在开发「万能短剧AI创作平台」。以下规范具有强制约束力，违反 MUST / FORBIDDEN 规则的输出将被拒绝。
> 规则冲突时优先级：**资金类（CLOSE-04）> 其余 MUST > SHOULD**。本文件可直接整体注入 Agent 系统提示词。

---

## 0. 维护约定（每次对话必读 · 最高优先级）

- **定位**：本文件是项目根目录的 **最高 AI 行为约束（合并版）**，整合「版本管理器最高约束」+「业务纪律」+「功能/业务定义」+「Bug 修复笔记」。
- **开始前**：每次开发/修复任务前必须阅读 `PROJECT_MAP.md`（唯一索引）与本文件（FS-04）。
- **实时维护**：每次对话结束时，若发生 (a) 功能/业务定义变更，或 (b) 修复了 bug，必须同步更新对应章节并改「最后更新」日期。
- **Bug 笔记**：新增条目用 §3.1 统一表格（问题 / 根因 / 解决方案 / 状态 / 日期），**禁止覆盖历史记录**；已修复项标记 ✅，待修复标记 ❌。
- **索引同步**：任何文件新增/重命名/删除后，同步更新 `PROJECT_MAP.md`（§2.1 / §3.4），未更新地图的提交视为不合格。

---

## 1. 项目功能与业务定义

### 1.1 速览
- **名称**：万能短剧 AI 创作平台（个人本地版 `personal_local_only`）
- **定位**：短剧 / AI 视频生成工具，五阶段流水线（资产库 → 分场拆解 → 分镜编排 → 异步生成 → 合成全集）
- **技术栈**：前端 Vue3 + Vite5 + Element Plus；后端 FastAPI + SQLite + 本地文件；**不依赖任何云端 Key**
- **端口**：
  - 后端 `127.0.0.1:7788`（纯 API）
  - 前端服务 `web_app.py`（`0.0.0.0:8099`，托管 `MANJU_FRONTEND_DIR`，默认 `frontend/dist`，反代 `/api/v1`）
  - 外部依赖：ComfyUI `:8188`、Ollama `:11434`（模型 `qwen2.5:7b`）
- **知识库基准**：IMA《万能短剧AI创作平台-开发手册》kb_id `7491002965175219`（§08 纪律、§09 补全）
- **构建**：临时 `vite.build.tmp.mjs` → `MANJU_FRONTEND_DIR`；managed Node `22.22.2` 经 `node_modules/vite/bin/vite.js` 构建。

### 1.2 五阶段流水线（业务主流程）
| 阶段 | 名称 | 职责 |
|---|---|---|
| P1 | 资产初始化 | 角色/场景/道具冷启动；角色→阶段/姿势；全集↔本集隔离 |
| P2 | 策划与分场拆解 | LLM 拆解剧本、角色识别、阶段判定 |
| P3 | 分镜编辑与资产绑定 | 三栏编辑器、`@` 引用资产、反向提示词/镜头 |
| P4 | 任务分发与异步 AI 推理 | **非任务中心**：WS + Webhook + 生产视图（纠正旧「任务中心」误读） |
| P5 | 合成导出 | TTS/BGM/字幕/FFmpeg 拼接、降级与超时 |

### 1.3 角色卡业务规则（2026-08-09 重构 · Turn-8）
- **R1 角色卡不可拖入提示词**：角色卡本身不作为提示词素材（已移除旧 `text/asset` 拖拽，见 Bug F2）。
- **R2 阶段卡是提示词引用单元**：拖入提示词引用的是「阶段卡」，必须携带 `stage_id`。
- **R3 角色必须 ≥1 阶段才可引用**：0 阶段角色在引用列表/卡片中禁用（`disabled`）。
- **R4 资产库精简 + 缩略图完整**：左侧资产库精简；阶段/角色缩略图 `object-fit:contain`（不裁切）。
- **R5 引用对象 schema**：拖拽/引用载荷统一为 `{ asset_id, asset_type, stage_id, name }`。
- **R6 引用唯一键**：`refKeyOf(r) = (r.asset_id) + '::' + (r.stage_id || '')`（⚠️ 见 F8：`||` 应改 `??`）。
- **R7 引用上限 BR-009**：单个分镜（shot）引用数 ≤ `MAX_REFS = 9`，按唯一 `refKey` 计数（跨 active segment）。
- **R8 后端已支持**：`segment_asset_refs` 已有 `stage_id` 列 → ref 升级无需后端表改动（但删资产级联见 F1）。
- **R9 拖拽 MIME**：`DRAG_MIME = 'application/x-manju-asset'`（统一载荷介质）。

### 1.4 关键不变量 / 导航硬规则
- **NAV-1**：项目内流程步骤必须用 `/projects/:id/...` 子路由，**禁止**指向顶层全局路由（否则 `App.vue` 的 `pid` 清零、跳出项目中心）。创建/打开项目落点统一 `/projects/:id`（按后端 `current_phase` 自动跳进行中阶段）。详见 `docs/UI_NAV_SPEC.md`。
- **INV-1**：删资产/阶段必须级联清理引用（见 F1）。
- **INV-2**：阶段命名三处须一致（见 F4）。

---

## 2. 开发纪律速查

### 2.1 版本管理器最高约束（最高优先级 · 凌驾其余纪律）
- AI 对业务模块 **只读**；仅产出 **修复包**，不直接写业务代码。
- `module_manager`（VM）是模块快照/回退/灰度开关的 **唯一写入口**（落盘 `versions/<key>/<version>/`、`module_flags` 表）。
- 详见知识库《版本管理器最高约束规则 V1.0》§1–§23。

### 2.2 规则总览表（MUST / FORBIDDEN / SHOULD）
| 规则 | 内容 | 级别 | 适用 |
|---|---|---|---|
| CLOSE-01 | 异步任务状态机必须收敛终态（pending→processing→success/failed/canceled） | MUST | 所有队列任务/状态机 |
| CLOSE-02 | 禁止静默吞异常（空 catch / 仅 pass / 仅 DEBUG） | FORBIDDEN | 全部代码 |
| CLOSE-03 | 写操作必须幂等（业务唯一键判重） | MUST | 队列消费/回调/重试入口 |
| CLOSE-04 | 失败路径必须退还积分/资金（最高优先级） | MUST | 涉及积分扣减的所有流程 |
| CLOSE-05 | 外部调用必须显式超时 | MUST | 模型 API/S3/支付/HTTP |
| REDUN-01 | 关键依赖必须有降级链 | MUST | AI 模型调用 |
| REDUN-02 | 网络重试必须指数退避且有上限（1s/2s/4s+抖动，≤3次） | MUST | 所有 HTTP/RPC 调用 |
| REDUN-03 | 输入必须先 schema 校验 | MUST | API 入口/队列任务体/Webhook |
| REDUN-04 | 并发写必须防竞态（事务/乐观锁） | MUST | 积分/名额/库存类操作 |
| REDUN-05 | 自动流程必须有手动补偿入口 | SHOULD | 所有长链路业务 |
| LOG-01 | 日志结构化且必带上下文字段（trace_id/event/module/user_id/task_id） | MUST | 全部代码 |
| LOG-02 | 日志级别按约定使用 | MUST | 全部代码 |
| LOG-03 | 关键位置强制打点（入口/出口/catch/状态迁移） | MUST | 全部代码 |
| LOG-04 | 敏感信息禁止落日志 | FORBIDDEN | 全部代码 |
| LOG-05 | 事件名 snake_case 全局唯一 | SHOULD | 全部代码 |
| OPS-01 | 临时资源必须请求内释放（try/finally） | MUST | 临时文件/锁/连接/FFmpeg 目录 |
| OPS-02 | 持久数据必须声明 TTL 与清理者 | MUST | 所有新增表/S3前缀/Redis key |
| OPS-03 | 清理任务必须幂等、分批（≤500）、带锁 | MUST | 所有定时清理任务 |
| OPS-04 | 长耗时任务必须配看门狗 | MUST | processing 可能 >10min 的任务 |
| OPS-05 | Redis 锁必须带 TTL | MUST | 所有分布式锁 |
| DONE-01 | 交付前必须完成收尾自查（七项自证） | MUST | 每次交付 |
| FS-01 | 禁止操作 C 盘任何文件（含回收站） | FORBIDDEN | 全部文件操作 |
| FS-02 | C 盘核心文件必须迁移至 D:\AI | MUST | C 盘核心文件 |
| FS-03 | 文件操作仅限 D:\AI 目录 | MUST | 删除/修改/增加操作 |
| FS-04 | 开始前必读 PROJECT_MAP.md 与开发规则 | MUST | 每次开发任务 |

### 2.3 平台特化参数（禁止自行编造）
| 参数 | 值 | 关联规则 |
|---|---|---|
| 视频生成超时 | 600s | CLOSE-05 |
| 图像生成超时 | 120s | CLOSE-05 |
| 普通 HTTP 调用超时 | 30s | CLOSE-05 |
| 重试次数 / 退避 | 3 次，1s/2s/4s + 抖动 | REDUN-02 |
| 视频生成看门狗阈值 | processing > 30min | OPS-04 |
| 清理任务批大小 | ≤ 500 条/批 | OPS-03 |
| 看门狗轮询间隔 | 5min | OPS-04 |
| S3 预签名 URL 有效期 | 15min | §07 |

### 2.4 交付自查 DONE-01（七项自证 · 必输出）
| # | 维度 | 需回答 |
|---|---|---|
| 1 | 闭环 | 列出所有异常路径及其终态 |
| 2 | 幂等 | 说明重复调用/重复投递的行为 |
| 3 | 日志 | 列出新增日志事件名，确认 trace_id 贯穿 |
| 4 | 清理 | 列出新增临时资源/数据及其删除路径（代码行） |
| 5 | 边界 | 说明空输入、超长输入、并发重复提交的表现 |
| 6 | 验证 | 粘贴实际运行/测试的关键输出 |
| 7 | 回归 | 列出检查过的受影响调用方 |

### 2.5 验收 Checklist（DONE-01 汇总 · 18 项）
CLOSE-01~05、REDUN-01~04、LOG-01/02/04、OPS-01~05、DONE-01 —— 逐项打勾，任何一项无法回答视为任务未完成，禁止交付。

---

## 3. Bug 修复笔记

### 3.1 维护模板
| 编号 | 日期 | 模块/文件 | 问题现象 | 根因 | 解决方案 | 状态 | 关联纪律 |
|---|---|---|---|---|---|---|---|
| Fx | YYYY-MM-DD | path:line | 现象描述 | 根本原因 | 修复做法（含提交/代码行） | ❌待修复 / ✅已修复 / 🔍待评估 | 规则ID |

### 3.2 已知问题清单（种子 · 2026-08-09 角色卡模块审计 F1–F9；2026-08-11 补充 F10）
| 编号 | 日期 | 模块/文件 | 问题现象 | 根因 | 解决方案 | 状态 | 关联纪律 |
|---|---|---|---|---|---|---|---|
| F1 | 2026-08-09 | `useProject.js` / 后端 `del_stage`·`delete_asset` | 删除阶段/角色后，分镜中仍残留悬空 `@引用`，渲染空引用或报错 | 删除资产/阶段无级联清理：`useProject` 无 `pruneRefs`；后端 `.pyc` 级联未知 | **2026-08-11 已修复**：① 后端新增附加端点 `POST /projects/{id}/prune-refs`，按 `asset_id`（可选 `stage_id`）全局清理 `segment_asset_refs` 与 `shots.asset_refs`（JSON 文本列逐镜头剔除），零改动编译模块中的 `del_stage`/`delete_asset`；② 前端 `useProject` 改为按 `(id,kind)` 单例并新增 `pruneRefs()`，删除成功后调用——既触发后端级联（覆盖所有片段），又清理 keep-alive 编辑器内存态悬空引用（避免保存时写回）；③ 删除点接入：`Assets.vue`(del/removeStage)、`RoleEditorModal.vue`(removeStage,新增`projectId` prop)、`AssetStageManager.vue`(removeStage,`useRoute` 取 pid)、`GlobalAssets.vue`(remove 全局清理)。已重建前端 + 重启后端验证（端点注册+执行通过） | ✅已修复 | CLOSE-02 / OPS-02 |
| F2 | 2026-08-09 | `Assets.vue:1047` | 角色卡仍可拖拽，写入非正式 MIME `text/asset`，全局无消费者 | 角色卡 `draggable="true"` + `@dragstart` 写 `text/asset`，与 R1「角色卡不作提示词素材」相反 | **2026-08-11 已修复**：角色卡 `:draggable="a.type !== 'role'"`（仅 scene/prop 可拖拽），scene/prop 改用规范 `application/x-manju-asset` MIME；角色卡 overlay 按钮 tooltip 改为提示「按 R1 请拖拽阶段卡」。前端已重建部署 | ✅已修复 | R1 / INV-1 |
| F3 | 2026-08-11 | `ProjectFragmentEditor.vue` `resolveRefByName` | 手动输入 `@角色名` 仍生成 `stage_id:null` 角色级引用，绕过「阶段为引用单元 / ≥1阶段」规则 | `resolveRefByName` 角色回退分支允许 `stage_id:null` | `@` 解析仅匹配阶段名（含 `stage_id`）与场景/道具资产名；角色名（非阶段名）返回 null，不生成 `stage_id:null` 引用，由 UI 提示改用具体阶段名。前端已重建部署 | ✅已修复 | R2 / R3 |
| F4 | 2026-08-09 | `RoleEditorModal.vue:336` vs `AssetStageManager.vue:122`/`Assets.vue` | 阶段命名规则不对称：仅 RoleEditorModal 强制层级名（如「周岩-青年」），其余用裸 `stage_name` | 三处 `submitStage` 命名逻辑不一致 | **2026-08-11 已修复**：新增共享 `stageNameFor(assetName,type,suffix)`（`src/assetMeta.js`）——`role` 用层级名 `roleName-suffix`，`scene/prop` 用裸名；三处 `submitStage`（RoleEditorModal / AssetStageManager / Assets.vue）统一调用，`ProjectFragmentEditor.vue` 给 role 型 `AssetStageManager` 传 `:asset-name`。已重建前端部署 | ✅已修复 | INV-2 |
| F5 | 2026-08-09 | `ProjectFragmentEditor.vue` `resolveRefByName` | 跨角色同名阶段，`@` 解析仅按 name 匹配会命中错误角色 | 缺少父角色作用域消歧 | **2026-08-11 评估结论（已由 F4 解决）**：F4 层级命名（`roleName-suffix`）使 `stage_name` 全局唯一（角色名唯一前提下），`resolveRefByName` 按 `stage_name` 精确匹配（`===`）即无跨角色歧义，`@周岩-青年` 不存在误命中；残留边界（两角色同名）由角色名唯一约束兜底，无需额外 `@角色名/阶段名` 消歧代码 | ✅已修复 | R2 |
| F6 | 2026-08-09 | `RoleEditorModal.vue` 静态 `stage_name` | 重命名角色后 `stage_name` 静态字符串不更新，文本引用 `@[旧名]` 与阶段展示脱节 | 引用存 name 文本而非按 id 解析 | 2026-08-11 已确认 schema：`segment_asset_refs` 无 name 字段（仅 stage_id 引用），故级联只需更新 `asset_stages.stage_name`（前缀 `oldName-`→`newName-` 替换）+ `shots.asset_refs` JSON 内 `name` 字段；设计为新增附加端点（同 F1 prune-refs 模式）+ `RoleEditorModal.persist` 调用 + `useProject` 内存态同步。列为下一轮实现 | 🔍待评估（设计就绪，计划下次） | INV-2 |
| F7 | 2026-08-09 | `ProjectFragmentEditor.vue` `syncMentionRefs:498` | BR-009 超 9 直接 `slice(0,9)` 静默截断，用户无感知 | 截断无提示/置灰 | **2026-08-11 已修复**：`syncMentionRefs` 超 9 时不再静默 `slice(0,9)` 丢弃，改为显式 `toast('片段资产引用最多 9 个（BR-009），部分提及未纳入引用')` 后截断；`tryMergeRef` 已有达上限置灰+拦截守卫。前端已重建部署（index-Dqxb0YFx.js） | ✅已修复 | R7 |
| F8 | 2026-08-11 | `ProjectFragmentEditor.vue:164` `refKeyOf` | `stage_id` 为 `0` 时 `0 || ''` 误判为空，导致 key 冲突/计数错误 | 用 `||` 而非 `??` | `refKeyOf` 改用 `??` 仅对 `null/undefined` 兜底，`stage_id=0` 不再误判为空 | ✅已修复（低风险） | R6 |
| F9 | 2026-08-09 | `ProjectFragmentEditor.vue:978/1139` | BR-009 展示口径歧义：段落级（唯一 refKey 计数）与逐镜头（per-shot）共用「/9」标签但指标不同 | 两类计数混用同一标签 | **2026-08-11 已修复**：`tryMergeRef` 守卫原仅检查 `others.size`（排除当前分镜），使「当前分镜已有引用 + 其他分镜也有引用」时仍可加入，片段级唯一引用总数可突破 9；改为以片段级总量 `segRefCount` 为准（仅当待加入 key 为全新引用且片段已达上限才拦截）。展示标签早已统一用 `segRefCount`（片段级口径），与守卫一致，无口径歧义 | ✅已修复（低风险） | R7 |
| F10 | 2026-08-11 | 空间清理脚本 / `web_app.py:49` | 清理时把 `frontend/dist`、`.uploads`、`server/outputs` 标记为 `[待删除]`，用户删除后 `127.0.0.1:8099` 报错「index.html 不存在」且上传/文件兜底路径失效 | `web_app.py` 默认 `FRONTEND_DIR = frontend/dist`；`UPLOADS_DIR = .uploads`；文件 fallback 含 `server/outputs` | 从 `MANJU_FRONTEND_DIR` 临时构建目录复制恢复 `frontend/dist`；重建空 `.uploads` 与 `server/outputs`；清理白名单必须排除这三项 | ✅已修复 | FS-04 / OPS-02 |
| F11 | 2026-08-11 | `server/director/agents/script_parser.py:164` `_auto_bind` | 资产子分词匹配 `toks=[t for t in name if len(t)>=2]` 对字符串按字符迭代，CJK/任意字符串字符长度均=1 → `toks` 恒为空，低置信（`0.6`）兜底分支对字符串永远不可达，仅精确 `name in text` 命中 | 迭代单位错误（应取≥2字子串而非单字符） | **2026-08-11 已修复**：`toks` 改为相邻二元组（bigram）切分 `name[i:i+2]`，修复对字符串按字符迭代导致 `toks` 恒为空、低置信(0.6) `needs_review` 分支不可达的死代码；现部分重叠（如资产名「周岩」出现在「周岩青年」文本）可正确触发弱匹配→待人工确认路径。需回归 `_auto_bind` 单测（见 Task #5） | ✅已修复 | R6 |

### 3.3 本次审计验证通过项（无需修 · 2026-08-09）
- ✅ `AssetRichInput.vue:204` `@drop` `stopPropagation()` 防止与外层描述字段双重插入。
- ✅ 0 阶段角色禁用 + `refAssetOptions` 排除 0 阶段角色（满足 R3）。
- ✅ `useProject.js:336` `save` 正确序列化并携带 `stage_id`（满足 R8）。
- ✅ 阶段/角色缩略图 `object-fit:contain` 已落地（满足 R4）。

---

**最后更新：2026-08-11**
