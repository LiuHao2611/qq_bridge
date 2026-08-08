# P4 · 任务分发与异步 AI 推理（生产阶段）

> **纠正**：第四阶段**不是「任务中心」**，而是「任务分发与异步 AI 推理（生产阶段）」。原 `/tasks` 页面已降格为跨切面诊断视图（改名「推理监控」）。
> KB 依据：《万能短剧AI创作平台-开发手册》§05-v2 模块五（AI 视频生成）+ §18（WebSocket）+ §06（Webhook 回调）。
> 关联索引：见根目录 `PROJECT_MAP.md` → 文档索引 · 阶段原子模块。**变更记录见 `docs/第四阶段重做报告.md`**。

## 一、概念对齐（不可动摇）
- 流水线：资产库 → 分镜编辑 → **AI 视频生成（§05-v2 模块五）→ 视频预览 → 合成导出**。
- 实时链路：把生成任务分发给 AI 推理引擎（ComfyUI / Ollama），以 **§18 WebSocket** 实时回传进度/日志/完成事件；外部异步推理引擎通过 **§06 Webhook** 回调落地结果。
- KB 路由树**无独立"任务中心"页**：任务为跨切面概念，内嵌于编辑器 / 生产视图（`segments[].latest_task`）。

## 二、后端实现
- **`server/realtime.py`（新增）** — §18 WS 实时网关：`publish(channel, event, data)` / `handle_ws(websocket)` / `connection_count()`；token 鉴权、通道 `task:{id}` / `video:{genId}` / `notifications` / `credits`、事件 `status_update`/`progress`/`log`/`completed`/`failed`、心跳 ping 30s / pong 60s。
- **`server/routers.py`（新增端点）**：
  - `POST /callbacks/inference`（§06 Webhook）：REDUN-03 入参校验 → CLOSE-01 外部状态映射（`success/completed→succeeded`、`pending→queued`、`processing→running`）→ 段同步 → CLOSE-04 成功结算 / 失败退款 → WS 发布。
  - `GET /inference/health`（§06 引擎契约）：`comfyui` / `ollama` 可达性、`queue_depth`、`ws_connections`、`mode`、`async_callback`。
  - `GET /projects/{id}/production`：项目 + 各 `segments[]` 含 `latest_task`（**不含** `task_center`）。
- **`server/db.py`** — 迁移补齐 `generation_tasks.progress` / `generation_tasks.external_id`；新增 `MANJU_DB_PATH` 环境变量覆盖（指向工作区外可写副本，破解只读锁）。
- **`server/common.py`** — 新增 `MANJU_LOG_DIR` 环境变量覆盖。
- **`server/main.py`** — 注册 `@app.websocket("/ws")`；startup 顺序 `init_db → reconcile_points → worker_start`。

## 三、前端实现
- **`src/ws.js`（新增）** — 原生 WS 客户端（§18）：`connect()`、30s ping / 60s pong 超时重连、断线指数退避（1s→15s）、`subscribe(channel, fn)`（重连后 CLOSE-03 幂等重订阅）、`onGlobal`、`subscribeTask(taskId, onEvent)`。
- **`src/components/ProductionStage.vue`（新增）** — 「生成生产」阶段页：段网格、引擎可达性芯片、队列深度 + WS 连接数、**逐段实时监看（WS 优先，轮询兜底 REDUN-01）**、分发/重生成/取消/重试、合成全集（带实时进度）。
- **`src/router.js`** — 新增 `/projects/:id/production`。
- **`src/components/ProjectFlowNav.vue`** — step4 由「任务中心」改为「生成生产」（🚀，路径 `/projects/{id}/production`）。
- **`src/components/ProjectFragmentEditor.vue`** — `onSyncAll` 跳转由 `/tasks` 改为 `/projects/{id}/production`。
- **`src/components/SideBar.vue` / `TopBar.vue` / `TaskCenter.vue`** — 原「任务中心」改名为「推理监控」，明确其为跨切面诊断视图（非流程阶段页）。

## 四、§08 纪律落点
| 规则 | 落点 |
|---|---|
| CLOSE-01 状态机收敛 | Webhook 状态映射；worker `recover()`（`running→queued`）；段状态同步 |
| CLOSE-03 幂等写 | WS 重订阅幂等；Webhook 结算幂等 |
| CLOSE-04 失败退款 | Webhook `failed` 分支 `refund()` |
| REDUN-01 降级链 | `ProductionStage` 中 `!wsConnected()` 时回退轮询 |
| REDUN-03 入参校验 | `POST /callbacks/inference` 拒绝非法 `status` / 缺失 `task_id`（实测 400） |
| §18 实时网关 | `realtime.py` + `ws.js` 全量实现 |
| §06 异步回传 | `POST /callbacks/inference` + `GET /inference/health` |

## 五、验证（全绿）
- `realtime_test.py` **8/8 PASS**（connected / subscribed / ping→pong / publish 投递 / 未订阅不投递 / connection_count 增 / 断线归零）。
- `phase4_test.py` 全 PASS（Webhook CLOSE-01 收敛 `succeeded` + `progress=100` + 段同步 + `video_url`；REDUN-03 校验）。
- 实机：7788 `/api/v1/health`=200、`/inference/health` §06 契约、`/production` 含 `latest_task` 无 `task_center`、Webhook 非法入参 400；8099 服务 `dist` 含「生成生产」标记。

## 六、运行备注
- 本机 IDE 宿主对**工作区内 `.db` 文件**只读锁；后端以 `MANJU_DB_PATH` / `MANJU_LOG_DIR` 指向工作区外可写副本启动（见 `PROJECT_MAP.md` → 启动命令）。
- `curl` 直连 `/ws` 会 500（curl 非 WS 客户端，非缺陷）；WS 逻辑已由单测覆盖。

## 七、关联文档
- `docs/第四阶段重做报告.md`（纠正说明 + DONE-01 七项自证）
- `docs/phases/DONE-01_七项自证.md`
