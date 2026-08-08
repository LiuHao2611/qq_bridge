# KB 差距分析：哪些做错了 / 缺什么（知识库 v1.3.0 · 7491002965175219）

> 分析对象：本地已运行的工作台 `D:\AI\manju_agent`（Vue3 + Vite + Element Plus 前端 / FastAPI + SQLite 后端，localhost:8099 ↔ 127.0.0.1:7788）。
> 对照基准：IMA 知识库《万能短剧AI创作平台-开发手册》v1.3.0（8 章：总览/技术栈/DB/API/前端/业务模块/AI模型/部署）。

---

## 一、已经对齐的（做得对，保留）

1. **导航覆盖面**：本地 16+ 路由已覆盖 KB 全部 16 个导航项（首页/项目中心/任务中心/文生图/图生图/创意工坊/人生回忆录/全局资产库/图片反推/历史记录/会员中心/消息中心/帮助/操作日志/设置）。
2. **API 信封**：本地 `src/api.js` 已用 `/api/v1` 前缀 + `{code,data,message,timestamp}` 解包，与 KB Base URL `/v1` + 信封一致（仅缺 `request_id` 字段）。
3. **模型/预设枚举**：`MODEL_OPTIONS`（Seedance 2.0 Mini/Pro、可灵、即梦、Vidu）与 `PRESET_OPTIONS`（创作旗舰 Pro / Flash·短 / Flash·长 / Max）与 KB `CreationParams` 定义高度一致。
4. **业务规则**：`BR-003`（分镜时长之和 = 片段时长）、`BR-009`（片段资产引用 ≤ 9）已在 `ProjectFragmentEditor.vue` 实现。
5. **积分引擎**：`server/points_engine.py` 已存在，信用点估算公式（base 180 × 时长 × 分辨率系数 × 音频）与 KB 思路一致。
6. **回忆录导演板**：`MemoirDirectorBoard.vue` 已实现 8 段（角色/环境/摄影机/灯光情绪/情绪关键词/镜头表/色彩/声音旁白），与 KB 8 维接近。

---

## 二、“做错了”的（必须修正的契约偏差）

### 1. 路由结构不符（高危）
| KB 规范 | 本地现状 | 问题 |
|---|---|---|
| `/projects/:id/editor`（项目编辑页核心） | `/project/:id`（单数，无 `/editor`） | 单数 + 缺 `/editor` 后缀 |
| `/memoir`（回忆录列表）+ `/memoir/:id`（回忆录编辑页） | `/memoir` 直接 302 跳到 `/project/demo-memoir/memoir`；回忆录挂在 `/project/:id/memoir` | 无独立回忆录列表页，路由嵌套层级错误 |
| SideBar 标签「导演工作台 / 导演创意板」 | KB 命名为「项目编辑页 / 人生回忆录编辑页」 | 命名不统一 |

### 2. 数据模型术语冲突（高危，易混淆）
KB 的 ER：`projects → segments(片段) → shots(分镜)`，资产 `characters/scenes/props` 独立。
本地 `ProjectFragmentEditor` 复用了冲突命名：
- 本地 `chapters` ≈ KB 的 **segments（片段）**
- 本地 `scenes`（含 `shot_type/camera_angle` 的分镜卡片）≈ KB 的 **shots（分镜）**
- 本地 `assets.scenes` ≈ KB 的 **scenes（场景资产）**
→ 同一个词 "scene" 在本地既指「分镜」又指「场景资产」，与 KB 严重冲突，API/字段对接会混乱。

### 3. 分镜枚举值不规范
- 本地用中文值：`shot_type:'近景'`、`camera_angle:'平视机位'`、`camera_move:'固定机位'`。
- KB 规范英文枚举：`shot_type ∈ {extreme_close_up,close_up,medium,...,long,extreme_long}`；`camera_movement ∈ {static,pan,tilt,dolly,zoom,handheld,aerial,follow}`；`camera_angle ∈ {eye_level,low_angle,high_angle,dutch,overhead,shoulder,pov}`；`focal_length ∈ {24mm,35mm,50mm,85mm,135mm}`。
→ 应改为「英文枚举值 + 中文显示标签」，与 KB 模型/接口字段对齐。

### 4. 回忆录编辑器结构不符（高危）
- KB `MemoirEditorPage` = **6 个标签页工作流**：资料 → 策划 → 指导板 → 审片 → 视频 → 合成。
- 本地 = 单页 + 「章节切换(ch-tab)」+ 折叠面板，**没有 6 标签工作流**，也没有「审片」「合成」「导出交付包」独立阶段页。

### 5. 导演板段落数偏差
- KB 指导板 = **9 段**（A 角色参考 / B 环境场景 / C 摄影机路径 / D 灯光情绪 / E 情绪关键词 / F 镜头表 / G 声音与旁白(波形) / H 旁白备注 / I 色彩参考）。
- 本地 = 8 段（把 G 声音 + H 旁白合并为「声音备注/旁白」一段）。应拆回 9 段，G 加音频波形图、H 独立旁白备注。

### 6. API 分页/响应形态不符
- KB：列表返回 `{items:[...], pagination:{page,page_size,total,total_pages,has_next,has_prev}}`。
- 本地：`api.js` 解包为 `.projects`、`.tasks`（平铺数组，无 pagination 对象）。后端 `routers.py` 需补分页信封。

### 7. 通知是 mock，非真实
- `TopBar.vue` 用 `mockNotifications` 假数据；KB 要求真实 `notifications` 表 + WebSocket 推送 + 铃铛红点。本地应至少接真实通知接口（实时可用轮询替代 WS）。

### 8. 鉴权缺失
- KB 要求 Bearer JWT + 全路由 AuthGuard。本地是演示账号「小志」，无登录态。本地工具可接受，但需标注「本地免登录」与 KB 差异。

---

## 三、缺什么（KB 有、本地没有或不到位）

1. **`/ai-create` 统一 AI 创作页**（KB 导航项）：本地拆成 文生图/图生图/创意工坊，缺顶层的「文生视频/图生视频/故事生视频/模板」聚合入口。
2. **真实「模板管理」**（`/api/templates`）：本地无模板 CRUD/应用。
3. **真实「全局资产库导入到项目」**：`GlobalAssets.vue` 存在，但跨项目导入/以图搜图（`search-by-image`）未接真实后端。
4. **WebSocket 实时**：本地用 `pollStatus` 轮询；KB 用 Socket.IO。可保留轮询（本地足够）。
5. **合成导出完整配置 UI**：KB 合成模块支持 format/quality/片头片尾/字幕样式；本地 `exportSegment` 仅基础导出，缺配置弹窗。
6. **`aspect_ratio` 需补 `4:3`**：KB 枚举 `16:9/9:16/4:3`（+1:1）；需核对本地 `OPTION_RATIOS` 是否含 4:3。
7. **`character_stages` 多阶段角色**：KB 角色支持童年/少年/青年多阶段参考图；本地 `Assets.vue` 是否支持待核对。

---

## 四、技术栈差异（根因，需你拍板）

| 维度 | KB 规范（云端 SaaS） | 本地现状（本机可跑） |
|---|---|---|
| 前端 | Next.js16 + React19 + TS + Tailwind + shadcn/ui + Zustand + TanStack Query | Vue3 + Vite + Element Plus + Pinia 风格 composables |
| 后端 | FastAPI + Celery + Redis + BullMQ + Socket.IO | FastAPI + SQLite（单文件）+ 本地 worker |
| 数据库 | PostgreSQL 16（UUID/软删/JSONB） | SQLite（整数 id） |
| AI | Seedance2.0 / GPT Image2 / Gemini3.5 / Kling / MiniMax（云端 API） | 本地 Ollama qwen3.5:9b + 秋叶 ComfyUI SD1.5 + FFmpeg |
| 部署 | Docker / K8s / Nginx | 本机 python + vite build + web_app.py |

> 结论：KB 的 Next.js/PostgreSQL/云端模型栈**无法在本机（GTX 1660Ti 6G、C 盘紧张、无云端 Key）直接运行**。本地栈是为其做的「可离线运行」适配版。真正的"重做"应是在**保留本地可跑栈**的前提下，把**页面路由 / 术语 / 枚举 / 回忆录 6 标签 / 导演板 9 段 / API 分页 / 真实通知**等契约对齐 KB。

---

## 五、建议的重做范围（待确认）

- **A（推荐）对齐契约**：保留 Vue3/FastAPI/SQLite，修正路由、术语、枚举、回忆录 6 标签、导演板 9 段、分页信封、真实通知；本机可跑、风险低。
- **B 全量重写**：按 KB 改为 Next.js/React/PostgreSQL/Celery/Redis/WebSocket + 云端模型——**本机跑不起来**，只能交付代码、无法验证。
- **C 只修高危项**：仅改路由/术语/枚举/回忆录 6 标签，其余后续。
