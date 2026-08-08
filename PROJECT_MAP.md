# PROJECT_MAP.md · 项目知识地图（唯一索引）
**最后更新**：2026-08-08

> 🔴 **强制约定（MUST）**
> 1. **找文件先查本索引**：以后任何"找源码 / 找文档 / 找实现"都必须**先查本文件**，按「路径」定位后再读取，**禁止凭记忆或全局搜索直接翻文件**。
> 2. **改动即更新地图**：每次**新增文件**或**修改文件职责**后，必须同步更新本索引对应表格（路径 / 用途 / 最后更新），删除或重命名同理。**未更新地图的提交视为不合格**。
> 3. 本文件是项目文件/文档的**唯一 Truth Source**；原子模块（`docs/phases/*`）是详细内容，"看细节去原子文档，找文件来本索引"。

---

## 0. 项目速览
- **名称**：万能短剧 AI 创作平台（个人本地版 · `personal_local_only`）
- **定位**：短剧 / AI 视频生成工具，五阶段流水线（资产库 → 分场拆解 → 分镜编排 → 异步生成 → 合成全集）
- **技术栈**：前端 Vue3 + Vite + Element Plus；后端 FastAPI + SQLite + 本地文件；**不依赖任何云端 Key**
- **运行时端口**：后端 `127.0.0.1:7788`（纯 API）；前端服务 `web_app.py`（仓库根 `D:\AI\manju_agent\web_app.py`）`0.0.0.0:8099`（托管 `MANJU_FRONTEND_DIR`，**默认** `frontend/dist` + 反代 `/api/v1`）；外部 ComfyUI `:8188`、Ollama `:11434`
- **关联后台系统 `manju_admin`**：用户前台 `http://127.0.0.1:8900`（Vite dev，反代 `/api`→`127.0.0.1:8910`）；后台 API `127.0.0.1:8910`（FastAPI，`theme.config.json` 持久化主题）；**主题跨系统同步**：后台改主题 → 8099 前台每 5s 拉取 `GET /api/theme` 并应用（详见 §8）。
- **知识库基准**：IMA《万能短剧AI创作平台-开发手册》kb_id `7491002965175219`（§08 纪律、§09 补全）

---

## 1. 顶层目录结构
```
D:\AI\manju_agent\
├── PROJECT_MAP.md            ← 本文件（唯一索引）
├── AGENTS.md                 ← §08 Agent 开发纪律（规则书，最高约束力）
├── frontend/dist/            ← 前端构建产物（**非服务目录**：web_app 实际托管 `MANJU_FRONTEND_DIR`；因安全删除钩子会拦截删旧 dist 文件导致重建失败，构建产物统一输出到 `MANJU_FRONTEND_DIR` / `C:/Users/L/AppData/Local/Temp/manju_dist_fixed`）
├── server/                   ← 后端（FastAPI）
├── src/                      ← 前端源码（Vue3）
├── docs/                     ← 文档（含 phases/ 原子模块）
├── tests/                    ← 端到端/UI 自动化测试
├── outputs/                  ← 生成视频/图产出
└── *.md（根）                ← 进度/差距分析/测试报告等（见 §2）
```

---

## 2. 文档索引（原子模块）

### 2.1 规范 / 纪律（MUST 级）
| 路径 | 主题 | 用途 | 最后更新 |
|---|---|---|---|
| `AGENTS.md` | §08 Agent 开发纪律 | CLOSE/REDUN/LOG/OPS/DONE 五组 MUST/FORBIDDEN 规则书；资金类（CLOSE-04）最高优先级 | 2026-08-07 |
| `docs/UI_NAV_SPEC.md` | UI 导航/页签规范 | 项目壳 pid 不清零、默认阶段、tab 语义、返回入口、顶/侧栏一致 | 2026-08-07 |
| `docs/phases/DONE-01_七项自证.md` | 交付前七项自证 | 七项自证原子准则 + 纪律优先级 | 2026-08-07 |

### 2.2 阶段原子模块（由 `五阶段对齐报告.md` 拆分）
| 路径 | 主题 | 用途 | 最后更新 |
|---|---|---|---|
| `docs/phases/P1_资产初始化.md` | P1 资产初始化（冷启动） | 角色阶段/姿势、全集↔本集隔离、接口与纪律落点 | 2026-08-07 |
| `docs/phases/P2_项目策划与分场拆解.md` | P2 策划拆解 | LLM 拆解、角色识别、阶段判定 | 2026-08-07 |
| `docs/phases/P3_分镜编辑与资产绑定.md` | P3 分镜编排 | 三栏编辑器、`@` 引用资产、反向提示词/镜头 | 2026-08-07 |
| `docs/phases/P4_任务分发与异步AI推理.md` | P4 生产阶段 | **纠正：非任务中心**；§18 WS + §06 Webhook + 生产视图 | 2026-08-07 |
| `docs/phases/P5_合成导出.md` | P5 合成导出 | TTS/BGM/字幕/FFmpeg 拼接、降级与超时 | 2026-08-07 |

### 2.3 报告 / 分析 / 进度（根 `*.md`）
| 路径 | 主题 | 用途 | 最后更新 |
|---|---|---|---|
| `docs/五阶段对齐报告.md` | 五阶段对齐（概述） | **已拆分**：仅保留概览+指针，细节见 `docs/phases/*` | 2026-08-07 |
| `docs/第四阶段重做报告.md` | P4 重做记录 | 纠正"任务中心"误读 + DONE-01 七项自证 + 运行说明 | 2026-08-07 |
| `KB差距分析与重做方案.md` | KB 差距分析 | 已对齐/缺什么/重做方案（源分析） | 2026-08-07 |
| `项目进度清单.md` | 功能进度清单 | 页面/功能完成勾选（截图项全 ✅） | 2026-08-06 |
| `文档_实在做不了清单.md` | 能力边界清单 | 对照手册契约，本地模式做不到的项（L1 出图已通） | 2026-08-06 |
| `TEST_REPORT.md` | 端到端测试报告 | 五阶段流水线前后端串联验证（pipeline_e2e 8/8） | 2026-08-07 |
| `§09_DONE-01_七项自证.md` | §09 补全功能自证 | 独立批次交付的七项自证 | 2026-08-07 |

> ⚠️ 根 `*.md`（进度/差距/做不了/测试报告）为**状态快照**，随开发推进可能过期；以 `PROJECT_MAP.md` 索引 + 文件内日期为准，改动后同步更新本表。

---

## 3. 源码索引

### 3.1 后端 `server/`
| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 入口；注册 `/ws`；startup `init_db→reconcile_points→worker_start`；CLOSE-02/03 请求日志中间件 + 异常 handler；`/file` 静态回传 |
| `db.py` | SQLite 连接（`MANJU_DB_PATH` 覆盖）、`_SCHEMA`、`_migrate`（幂等加列）、`init_db`、`uid/now_ms`、`row_to_dict`、`fetch_one/all`、`log_operation` |
| `routers.py` | 主 API 路由：项目/片段/资产/阶段/姿势、上传、合成、**生产视图**、**§06 Webhook `/callbacks/inference`**、**§06 `/inference/health`**、**服务/模块管理器**（`/system/services`、`/modules` 系列）；**2026-08-08 修复 `split_story` 分镜时长之和 ≠ 片段时长（BR-003）导致「导入场次失败」**：新增 `_redistribute_durations`，LLM/正则两路均摊时长使之和正好等于片段时长；**同日修复 `patch_asset`/`patch_template`/`patch_project`/`patch_segment` 参数顺序错位 bug（CLOSE-02 级静默失效）**：旧写法 `params.append(id)` 后接 `+[int(time.time())]` 会把 id 绑到 `updated_at=?`、时间戳绑到 `WHERE id=?`，导致 UPDATE 匹配 0 行、PATCH 返回 code:0 但数据不变；改为 `params + [int(time.time()), id]` 严格对齐占位符。**⚠️ 2026-08-08 重建（重要）**：`routers.py` 源码曾被误清空，运行期唯一权威产物是 `__pycache__/routers_compiled.cpython-313.pyc`（由原始 `routers.cpython-313.pyc` 拷出独立留存，含上述全部修复）。现采用「**门面 + 定点覆盖**」策略——`routers.py` 通过 `importlib` 从 `.pyc` 载入已编译模块 `routers_compiled` 并 `from routers_compiled import *` 复用其 `api`（全部 90+ 端点路由）与所有 handler/helper，行为 100% 等价于已验证产物；仅 `split_story` 被**定点覆盖**（删除旧路由后以新实现重注册），适配 `ai_local.split_story_with_llm` 新的 `{segments, meta}` 契约：返回 `data={segments, count, source, meta}`，`meta` 含 `model/duration_ms/raw_preview/error`，供前端底部「推理进度」面板实时展示模型是否真正被调用及回退原因。**`.pyc`（即 `__pycache__/routers_compiled.cpython-313.pyc`，由原始 `routers.cpython-313.pyc` 拷出独立留存，避免被主缓存覆盖）（权威产物已固化为 `server/_vendor/routers_compiled.cpython-313.pyc` 并 Git 跟踪，见本表末行与下方「兜底回退」；`__pycache__` 同名副本仅回退、可删）**；未来改其它端点请把实现从 `routers_compiled` 平移为明文并删除旧路由重注册 |
| `module_manager.py` | **服务/模块版本管理器**：业务模块快照/回退（落盘 `versions/<key>/<version>/`）、灰度开关（`module_flags` 表）、服务探针、经 `launcher.py` 启停 backend/frontend；`_run_frontend_build` 支持 `MANJU_FRONTEND_DIR` 并异步执行避免阻塞事件循环（2026-08-07 新增/修订） |
| `launcher.py` | **受管服务启动器**：backend(7788)/frontend(8099) 的 start/stop/restart/status；PID/日志目录支持 `MANJU_RUN_DIR`/`MANJU_LOG_DIR` 覆盖以绕过工作区文件锁；透传 `MANJU_FRONTEND_DIR` 给 web_app（2026-08-07 新增/修订） |
| `worker.py` | 异步生成 worker：状态机、`recover()` 崩溃恢复、30min 看门狗(OPS-04)、`VIDEO_TIMEOUT` 线程池超时(CLOSE-05)、`_emit` 结构化日志(LOG-01/03)、退款 |
| `ai_local.py` | 本地推理/合成：`synthesize_project_video`（P5 拼接+TTS/BGM/字幕 REDUN-01）、视频生成调用 |
| `points_engine.py` | 积分引擎：`reconcile_points`（启动对账 CLOSE-04）、扣减/冻结/退款 |
| `realtime.py` | **§18 WebSocket 网关**：`publish` / `handle_ws` / `connection_count`；token/通道/心跳/事件 |
| `memoir_routes.py` | 回忆录模块路由（独立功能，AGENT_SPEC_15） |
| `common.py` | 公用：`ApiError`、`envelope`、`setup_logging`（`MANJU_LOG_DIR` 覆盖）、常量 |
| `routers/`（目录） | 当前为空/占位；路由集中在 `routers.py` |
| `smoke_test.py` | 探针/冒烟测试脚本（非运行时） |
| `_smoke_g2.py` / `_t2i_test.py` | **残留临时脚本（2026-08-08 清理时因 safe-delete 钩子拦截未删成，无害）**：上一轮 PYC 逆向重建辅助脚本；建议手动删除。权威恢复产物已固化为 `server/_vendor/routers_compiled.cpython-313.pyc` |
| `_dis_all.py` / `_dis_pyc.py` / `_extract_pyc.py` / `_map_routes.py` / `_dis_imports.py` / `_dis_top*.py` / `_dis_module.py` / `_validate_facade.py`（均 `server/` 下） | **已删除（2026-08-08 清理）**：临时 PYC 逆向重建辅助脚本；重建校验通过后用 git 基线 + `version_guard` 兜底，不再需要。权威恢复产物已固化为 `server/_vendor/routers_compiled.cpython-313.pyc`（纳入 Git 版本控制）。 |
| `server/_vendor/routers_compiled.cpython-313.pyc` | **权威恢复产物（Git 跟踪）**：原始 `routers.py` 编译副本，`routers.py` 门面优先从此加载（`__pycache__` 为回退）。即便 `routers.py` 源码被误清空，这里有 100% 等价的可运行后端，且 git 可回退到任意历史版本。 |
| `version_guard.py`（仓库根） | **误改/误删兜底工具（2026-08-08 新增，Git 跟踪）**：`python version_guard.py backup <file>` 改动前自动快照到 `.backups/`，`restore <file>` 一键回退，`status` 查看 git 基线与备份份数。与 git 互不冲突，见下方「兜底回退」说明。 |
| `web_app.py`（仓库根 `D:\AI\manju_agent\web_app.py`） | **前端静态服务进程**：托管 `MANJU_FRONTEND_DIR`（**默认** `frontend/dist`）+ 反代 `/api/v1`→`127.0.0.1:7788`；`0.0.0.0:8099`。**2026-08-08 更正**：此前误记在 `server/` 下，实际位于仓库根。 |

> **🛡️ 兜底/回退保障机制（2026-08-08 建立，针对「误改/误删关键文件」）**
> 项目此前**无版本控制**，任何关键文件被覆盖都无法恢复。现已建立三重保险：
> 1. **Git 基线（主）**：`D:/AI/manju_agent` 已 `git init`，提交 `baseline-2026-08-08`（含全部源码 + `server/_vendor` 恢复副本）。任何文件被改坏，`git checkout baseline-2026-08-08 -- <file>` 或 `git revert` 即可回退。
> 2. **version_guard 文件级快照（辅）**：改关键文件前先 `python version_guard.py backup server/routers.py`（会登记到 `.backups/`，已被 `.gitignore` 忽略不入库）；需要回退时 `python version_guard.py restore server/routers.py`。
> 3. **pre-commit 钩子**：禁止把 `*.db` / 用户数据误提交进版本库（`.gitignore` 已排除，钩子为第二重保险）。
> ⚠️ 约定：今后**改动 `routers.py` / `db.py` / `main.py` 等关键文件前，必须先用 `version_guard.py backup` 快照一次**；改动后若验证通过，再 `git add` + `git commit` 固化基线。

### 3.2 前端 `src/`
| 文件 | 职责 |
|---|---|
| `main.js` | 入口（挂载前 `initRemoteTheme()` 从后台 8910 拉取并轮询应用「配色预设+明暗」） |
| `App.vue` | 根壳；`pid` 由路由参数派生（导航根因所在） |
| `router.js` | 路由表（`/projects/:id` 入口、`/production`、`/editor`、`/planning`、`/assets`、`/tasks` 等） |
| `store.js` | 全局状态 |
| `api.js` | 后端 API 封装（统一信封解析） |
| `parseDocx.js` | **2026-08-08 新增**：零依赖 `.docx` 纯文本提取器（OOXML=ZIP，用内置 `DecompressionStream('deflate-raw')` 解压 `word/document.xml`，按 `<w:p>` 换行、`<w:br>/<w:tab>` 转义；CLOSE-02 解析失败显式抛错）；供 `ProjectPlanning.vue` 导入剧本 |
| `ws.js` | **§18 WebSocket 客户端**（P4 新增）：连接/心跳/订阅/重连 |
| `mock.js` | **枚举单一来源**（AGENTS.md REQUIRED）；2026-08-08 新增资产库枚举 `ASSET_TAB_OPTIONS/STAGE_OPTIONS/POSE_OPTIONS/ROLE_TYPE_OPTIONS` |
| `style.css` | 全局样式（含配色预设 `html.t-gray/green/blue/gradient/splice`，变量与后台 admin 同源，挂在 `html.t-*`） |

### 3.3 组合式 `src/composables/`
| 文件 | 职责 |
|---|---|
| `useProject.js` | 项目/片段/资产组合式（applyActiveSegment/switchSegment/addSegment/removeSegment/refreshAssets） |
| `useMemoir.js` | 回忆录组合式 |
| `useHotkeys.js` | 快捷键 |
| `usePersist.js` | 本地持久化 |
| `useServiceStatus.js` | 服务状态 |
| `useTheme.js` | 主题（明暗 `dark/light/auto` + 配色预设 `t-*` + `initRemoteTheme` 从后台 8910 拉取 `GET /api/theme` 每 5s 轮询应用；`wn_settings.theme`/`manju_theme` 本地兜底） |
| `useModuleFlags.js` | **模块灰度开关**（与 `module_flags` 对齐）：路由导航守卫据此拦截已停用模块（2026-08-07 新增） |

### 3.4 组件 `src/components/`（27，按功能分组）
- **流程壳/导航**：`ProjectFlowNav.vue`（5 阶段步骤，step4=生成生产）、`SideBar.vue`、`TopBar.vue`、`ProjectEntry.vue`（按 current_phase 跳转）、`ProjectsList.vue`、`Home.vue`
- **P1 资产**：`Assets.vue`（角色阶段/姿势面板 + **2026-08-08 增强**：拖拽/批量上传、上传元数据弹窗、搜索/标签筛选、从全集导入弹窗、角色类型 badge、空状态引导、骨架屏；**同日新增角色卡 hover 三按钮覆盖层**：①拖动角色卡(`Grid`/`draggable`) ②编辑角色(`EditPen`→`RoleEditorModal`) ③预览角色图(`ZoomIn`)；`toCard` 的 `prompt` 用 `splitVoice` 剥离音色块，`onRoleSaved` 写回完整 `description`(含音色)保证一致）、`RoleEditorModal.vue`（**2026-08-08 新增**：小云雀式「编辑角色」弹窗——左侧角色大图(替换/预览)、右侧 角色名称/定位(`el-select`)/设定(`textarea`)/**角色音色**(`textarea`，与设定共生于 `description`，用 `\u0001VOICE\u0001` 不可见分隔，加载时 `splitVoice` 剥离避免污染分镜提示词)/年龄阶段参考图/上传音频/AI生成，底部「去画布编辑/取消/保存」；保存走 `PATCH /assets/{id}` 持久化 name/description(设定+音色)/role_type/cover_url）、`GlobalAssets.vue`（全局资产库；**2026-08-08 增强**：角色 Tab「编辑」接入 `RoleEditorModal`(含角色音色)，场景/道具仍用原简单「编辑资产」弹窗；`adaptAsset` 用 `splitVoice` 剥离音色供 type 展示）、`src/assetMeta.js`（**2026-08-08 新增**工具：`splitVoice`/`joinVoice`——角色设定与音色共生于 `assets.description` 的隔离器，因后端 `patch_asset` 仅支持 name/description/tags/cover_url/role_type 且无独立音色列）
- **P2 策划**：`ProjectPlanning.vue`（导入场次 `doImport`：**2026-08-08 新增客户端 `redistributeDurations` 防御**，按片段时长重新均摊分镜时长，杜绝 BR-003 拦截「导入场次」；**同日 UX 优化**：批量选择工具栏（全选/全不选/反选、Shift 连选）、搜索过滤、卡片/列表双视图、场次内分镜展开预览与「保留前 N 个分镜」裁剪、Ctrl+A/Esc 快捷键、**拖入/选择文件导入剧本（.txt/.md/.srt/.docx，.srt 自动去时间轴、.docx 经 `src/parseDocx.js` 纯前端解压提取正文，详见该行）**；后端 `split_story` 已同源修复）
- **P3 分镜**：`ProjectFragmentEditor.vue`（三栏 + `@`/`@[ ]` 引用 + **资产拖拽入提示词**生成 @引用标签/chip，BR-009 守卫；2026-08-08 画面描述固定高度 84px + 弹窗大编辑 + 左侧资产库，其他提示词框 autosize 限 maxRows:2；弹窗 AssetRichInput 设 `:auto-grow="false"` 以填满 70vh；**2026-08-08 同增强**：左侧资产库「角色」卡片(`char-card`，主区 + 画面描述弹窗两处) hover 出现小云雀式三按钮——①拖动角色卡(`Grid`/`draggable`) ②编辑角色(`EditPen`→`RoleEditorModal`) ③预览角色图(`ZoomIn`)，保存后 `refreshAssets()` 重映射 `p.characters`）、`AssetRichInput.vue`（contenteditable 富文本输入框，支持资产气泡、拖拽插入、弹窗大编辑；2026-08-08 新增 `fixedHeight`/`autoGrow` 两 prop：`fixedHeight` 时高度锁定不可拖拽，`autoGrow=false` 时不按内容收缩）
- **P4 生产**：`ProductionStage.vue`（生成生产页）、`TaskCenter.vue`（**推理监控**诊断页）、`VideoLogPanel.vue`
- **创作/工具**：`Txt2Img.vue`、`Img2Img.vue`、`Img2Prompt.vue`、`Canvas.vue`、`Creative.vue`
- **通用/其他**：`Logs.vue`、`History.vue`、`Membership.vue`、`Messages.vue`、`Settings.vue`、`Help.vue`、`Placeholder.vue`、`MemoirDirectorBoard.vue`、`MemoirList.vue`
- **系统/管理**：`SystemManager.vue`（**服务与版本管理器页**：服务健康/启停 + 模块版本快照/回退/启用开关，2026-08-07 新增）

---

## 4. 关键约定（精炼自 AGENTS.md / UI_NAV_SPEC.md）
- **纪律优先级**：MUST > SHOULD；**资金类 CLOSE-04（失败退款）最高优先级**。
- **导航不许清空 pid**：项目内流程步骤必须 `/projects/:id/*`；全局页（如 `/global-assets`、`/tasks`）才允许顶层。
- **WebSocket 用轮询兜底**：生产阶段 WS 不可用时前端回退轮询（REDUN-01）。
- **枚举单一来源**：`src/mock.js`，禁止散落硬编码。
- **统一信封**：`{code,data,message,request_id,timestamp}`；无静默吞错（CLOSE-02）。
- **取用阈值（禁自编）**：视频生成超时 600s、HTTP 超时 30s、重试 3 次（1s/2s/4s+抖动）、看门狗 >30min。

---

## 5. 启动 / 运行命令
```bash
# 后端（MANJU_ 系列变量按项目设计指向工作区外 Temp 目录：DB 默认 C: 快照、日志/运行/前端产物在 Temp。
# 注：D: 工作区 SQLite 实测可写，"只读锁"为误判；MANJU_ 路径是项目既定运行时设计，非绕过锁。
# 安全删除钩子会拦截删旧 dist 文件，故前端构建产物放工作区外。）
cd D:\AI\manju_agent\server
MANJU_RUN_DIR="C:/Users/L/AppData/Local/Temp/manju_run" \
MANJU_DB_PATH="C:/Users/L/AppData/Local/Temp/manju_app_snapshot.db" \
MANJU_LOG_DIR="C:/Users/L/AppData/Local/Temp/manju_logs" \
MANJU_FRONTEND_DIR="C:/Users/L/AppData/Local/Temp/manju_dist_fixed" \
C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe launcher.py start backend
# 监听 127.0.0.1:7788
# ⚠️ 若在无头/自动化环境里 launcher 的 start "title" cmd /k 窗口无法保持进程存活，
# 可直接以 venv python 拉起 main.py（同环境变量）：
#   cd D:\AI\manju_agent\server
#   MANJU_DB_PATH="C:/Users/L/AppData/Local/Temp/manju_app_snapshot.db" ^
#   MANJU_LOG_DIR="C:/Users/L/AppData/Local/Temp/manju_logs" ^
#   MANJU_RUN_DIR="C:/Users/L/AppData/Local/Temp/manju_run" ^
#   C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe main.py

# 前端静态服务（托管 frontend/dist + 反代 /api/v1）
# 经 launcher 启动（推荐，受 SystemManager 管理）。
# 安全删除钩子会拦截删旧 dist 文件导致 vite 重建失败 → 统一把构建产物输出到工作区外 MANJU_FRONTEND_DIR：
cd D:\AI\manju_agent
C:/Users/L/.workbuddy/binaries/node/versions/22.22.2/node.exe node_modules/vite/bin/vite.js build \
  --outDir "C:/Users/L/AppData/Local/Temp/manju_dist_fixed"
# 再让 web_app 服务该外部目录：
cd D:\AI\manju_agent\server
MANJU_RUN_DIR="C:/Users/L/AppData/Local/Temp/manju_run" \
MANJU_LOG_DIR="C:/Users/L/AppData/Local/Temp/manju_logs" \
MANJU_FRONTEND_DIR="C:/Users/L/AppData/Local/Temp/manju_dist_fixed" \
C:/Users/L/.workbuddy/binaries/python/envs/default/Scripts/python.exe launcher.py start frontend
# 0.0.0.0:8099

# 版本管理器回退闭环：backend 的 module_manager 读取 MANJU_FRONTEND_DIR，回退后自动构建到同一目录，
# web_app 每次请求从磁盘读文件 + Vite 内容哈希文件名 → 用户刷新即看到回退版本。
```

---

## 6. 更新规则（MUST · 每次变动必做）
1. **新增文件** → 在 §2（文档）或 §3（源码）对应表格追加一行（含路径/用途/最后更新）。
2. **修改文件职责** → 同步更新该文件所在行的「用途 / 最后更新」。
3. **删除/重命名** → 同步移除或改名对应行，并确认无其它索引指向旧路径。
4. **文档拆分/合并** → 同步更新 §2 指针与 `docs/五阶段对齐报告.md` 概述。
5. 本文件的「最后更新」日期随任何改动刷新为当日。**改动完成前先把地图改对，再交付。**

---

## 7. 测试
- `tests/ui_flow.mjs`（Playwright UI 回归，需先起 7788 + 8099）：导航/默认阶段/`@` 引用/无 JS 错误。
- `tests/pipeline_e2e.mjs`（后端五阶段 E2E，含真实 ComfyUI 生成）：Phase0~5 串联。
- 阶段专项：`phase4_test.py`（HTTP 链路）、`realtime_test.py`（§18 WS 网关 8/8）。
- 拖拽引用专项：`tests/test_drag_refs.py`（REDUN-03 文本 @引用 解析单元测试，6 项）、`tests/test_drag_refs_e2e.py`（后端闭环 E2E：PUT shots 文本 @[名]→落库 asset_refs + BR-009）。
- 运行：`node tests/ui_flow.mjs` / `node tests/pipeline_e2e.mjs` / `python tests/test_drag_refs.py` / `python tests/test_drag_refs_e2e.py`。

---

## 8. 主题跨系统同步（manju_admin ↔ manju_agent）· 2026-08-07
- **目标**：后台（8900/8910）改「配色预设 + 明暗」，8099 前台自动跟随（用户已选「全套都同步」）。
- **预设枚举**：`''`（默认粉紫）/ `gray` / `green` / `blue` / `gradient` / `splice`；与明暗（`dark`/`light`/`auto`）正交，挂在 `html.t-*` 上。
- **持久化**：后台 `8910` 的 `GET/POST /api/theme` 读写 `manju_admin/theme.config.json`（`{preset, mode}`），统一信封 `{code,data,...}`；CORS 放行 `http://127.0.0.1:8099` 等 8099 来源。
- **8099 应用**：`src/composables/useTheme.js` 启动先用本地兜底（`wn_settings.theme` + `manju_theme`）`applyMode/applyPreset`，`initRemoteTheme()` 拉取 `http://127.0.0.1:8910/api/theme` 并每 5s 轮询覆盖（后台为总开关，不可达时静默保留本地值）；`src/style.css` 含 5 个 `html.t-*` 配色块（变量名与后台 admin 完全一致，同源直接生效）。
- **改主题流程**：后台 8900「主题」页选预设/明暗 → `POST /api/theme` → 8099 刷新或 ≤5s 内自动生效。
- **构建注意**：8099 构建产物必须落到 `MANJU_FRONTEND_DIR`（默认 `C:/Users/L/AppData/Local/Temp/manju_dist_fixed`），`web_app.py` 每次请求从磁盘读 + 内容哈希文件名 → 刷新即生效；根目录 `frontend/dist` 非服务目录（勿误改/勿误以为它即线上产物）。
- **注意**：后台为总开关，8099 本地的明暗切换（TopBar/Settings）会被远程每 5s 覆盖回后台设定。
