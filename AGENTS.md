# AGENTS.md — 万能短剧 AI 创作平台（个人本地版）

> 本文件对应 IMA 知识库《万能短剧AI创作平台-开发手册》第 08 章「Agent 开发纪律与提示词指南」。
> 第 8 章明确要求：规范内容可直接注入 Agent 系统提示词或项目根目录 AGENTS.md，与本仓库同等约束力。
> 规则冲突时 **MUST > SHOULD**；**资金类规则（CLOSE-04）优先级最高**。

## 0. 栈实情（person_local_only）

- 前端：Vue3 + Vite + Element Plus SPA（`src/`，由 `web_app.py` 托管于 `localhost:8099`）。
- 后端：FastAPI（`server/`，`127.0.0.1:7788`，纯 API，信封 `{code,data,message,request_id,timestamp}`）。
- 数据：本地 SQLite（`server/app.db`），单用户 `SINGLE_USER_ID`，积分 mock 默认 1999995162。
- AI：本地 Ollama(qwen3.5:9b) + 秋叶 ComfyUI(Realistic_Vision SD1.5) + FFmpeg。云端模型（Seedance/GPT Image 2）在本地被等价替代并诚实标注。
- 与 KB 云端版（Next.js16+PostgreSQL）属**事实偏差**，本文件约束本地版，不因偏差而违反下列纪律。

## 1. CLOSE 硬纪律（开发任何功能前先自检）

### CLOSE-01 闭环（Closed-loop）
- 任何写操作必须**有始有终**：提交 → 处理 → 成功/失败都必须回到终态，不能遗留中间态。
- 生成任务状态机 `queued→running→succeeded/failed`，`failed` 可 `retrying`。
- Worker 启动时 `recover()` 复位崩溃遗留的 `running→queued`；看门狗将超过 30 分钟无进展的 `running` 判 `failed`。
- 回忆录任务（`memoir_tasks`）由每请求独立线程执行，`_safe_run` 兜底：任一层意外异常都标记 `failed` 并退冻结金，绝不遗留永久 `queued`。
- 禁止「提交后石沉大海」：调用方必须能轮询/收到终态（本项目用轮询 `/tasks/:id` 与 `/memoirs/.../task`，前端不放 WebSocket）。

### CLOSE-02 幂等（Idempotency）
- 积分预授权 `points/pre-auth` 以 `idempotency_key` 幂等：相同 key 返回同一 `pre_auth_id`，不重复冻结；已结算/已退还的 key 拒绝复用（UNIQUE 约束）。
- 生成提交 `generate_segment`：同一 `pre_auth_id` 已有活跃任务则**直接返回该任务**，不新建。
- 所有「重试」必须由调用方带**相同幂等键**；不要用 `Date.now()+random()` 作为幂等键（那等于每次都新键，失去幂等意义）。
- 删除/更新类操作应可重复执行且结果一致。

### CLOSE-03 日志（Logging）· 失败重试 · 原子性
- 每个 HTTP 请求经 `request_logging_middleware`：记录 `method / path / status / 耗时 / x-request-id`，响应头带回 `x-request-id`。
- 已知 `ApiError` 与未捕获异常**必须**写入 `operation_logs(status='failed')` 并打印结构化日志；**禁止静默吞错**。
- 生成任务每个关键阶段写入 `generation_logs`（info/error）。
- 审计可追溯：凡资金变动必经 `log_operation`（points_settle / points_refund）。
- **出站网络调用必须指数退避重试且有上限**：`ai_local._http_post_json` 对 Ollama / ComfyUI 的 POST 按 `2^n` 秒退避，最多 3 次，全部失败才抛出（由上层降级或标记失败）。
- **生成任务失败自动退避重试**：Worker 内 `_serve_one` 失败且 `retry_count < 2` 时，以 `30*2^n` 秒指数退避重新入队（保留冻结金供重试），超过上限才最终 `failed` 并退还冻结金；`_loop` 只在 `next_attempt_at` 到期后才重新拾取。
- **原子性**：多语句写操作在 `try` 内统一 `commit()`，异常经 `finally` 关闭连接（SQLite 隐式回滚未提交事务）；资金路径额外经 `points_engine` 的 `pre_auth/settle/refund` 保证不重扣、不泄漏。

### CLOSE-04 资金安全（最高优先级）
- **提交前必须 pre-auth（BR-004）**：`generate_segment` / `regenerate_segment` / 回忆录 `storyboard` / `video` / `synthesize` 均要求真实 `pre_auth_id`，**禁止字面量占位**（旧代码 `pre_auth_id="synthesis"` 已废除）。
- **冻结即真实扣减（hold）**：`pre_auth` 扣减并冻结余额；`settle` 仅翻转状态（绝不二次扣减）；`refund` 复原冻结金。杜绝并发超额提交。
- **任务失败必须退还（BR-005）**：Worker/`_safe_run` 在失败路径调用 `refund`。
- **提交流程若未真正落库即失败，必须退还冻结金**：`generate_segment`/`regenerate_segment`/`synthesize`/回忆录两接口均在异常分支 `refund(pre_auth_id)`，防泄漏。
- 启动 `reconcile_points()` 对账：冻结改造前的历史 `estimated` 行（余额从未扣减）翻为 `deducted`，避免后续误退导致余额膨胀。
- 余额不足（`INSUFFICIENT_POINTS`）一律拒绝，不得在余额不足时继续扣减。

## 1.5 §9 提示词纪律（提示词注入模板）

- 所有调用本地大模型（Ollama / ComfyUI）的提示词**必须**经 `ai_local.build_prompt(role, objective, constraints, invariants)` 生成，禁止在业务函数里散写裸字符串。
- 模板骨架固定为四段：**角色 / 任务 / 约束 / 不可变**。
  - `role`：模型扮演的角色定位；
  - `objective`：本次要完成的具体任务；
  - `constraints`：软约束列表（应遵守但不致命）；
  - `invariants`：**硬不变量**，绝不违反（如保留 `<ref>` 引用标签、不新增未出现角色、以用户参考照为唯一视觉依据、不虚构未提供事实）。
- 已落地的调用点：`enhance_with_ollama`（编剧润色）、`plan_memoir`（回忆录策划）均走 `build_prompt`。

## 2. 业务规则（BR）速查（部分）
- BR-003 分镜时长之和 == 片段时长（`put_shots` 校验）。
- BR-004 生成前必须 pre-auth（见 CLOSE-04）。
- BR-005 失败必退（见 CLOSE-04）。
- BR-009 资产引用最多 9 个（`put_asset_refs` 校验）。
- BR-015 参考照 ≤10MB、至少 1 张方可生成回忆录视频。
- BR-016 回忆录分镜时长之和 == 章节时长（规划期 `_redistribute` 校正）。
- BR-019 章节数不超过故事板数量上限。
- BR-021 审片预检：策划/指导板/视频三项齐备方可导出。

## 3. 不可变不变量（改代码前必读）
1. 信封永远是 `{code,data,message,request_id,timestamp}`，`code=0` 成功；错误用 `ApiError(name)`。
2. 列表接口返回 `{items, pagination:{page,page_size,total,total_pages,has_next,has_prev}}`。
3. 分镜英文枚举（单一来源 `src/mock.js`）：`shot_type`∈{extreme_close_up,close_up,medium_close,medium,medium_full,full,long,extreme_long}；`camera_movement`∈{static,pan,tilt,dolly,zoom,handheld,aerial,follow}；`camera_angle`∈{eye_level,low_angle,high_angle,dutch,overhead,shoulder,pov}；`focal_length`∈{24mm,35mm,50mm,85mm,135mm}。后端字段名 `camera_movement`（非 `camera_move`）。
4. 非编辑文本区域全局 `user-select:none`，仅 `input/textarea/[contenteditable]/el-mention/el-input__inner/el-textarea__inner/el-select__tags-text` 可选。
5. 前端 mock 优先（`VITE_USE_MOCK`），后端不可用时页面不崩。

## 4. 本地运行
- 后端：`cd server && .venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 7788`
- 前端：`npm run build` → 产物 `frontend/dist`；`web_app.py` 托管 8099 并反代 `/api/v1`。
- 健康检查：`GET /api/v1/health` 返回含 `request_id`；错误响应同样带 `request_id`，用于日志关联。

## 5. 第 8 章自检清单（逐项比对，迭代时复核）

> 来源：KB 第 8 章「Agent 开发纪律与提示词指南」v2.0.0 自检清单（☐ 项）。逐条对照实现状态。

| # | 检查项（来自第 8 章） | 对应 | 状态 |
|---|---|---|---|
| 1 | 异步任务状态机必须收敛终态（无孤儿 running/queued） | CLOSE-01 | ✅ 启动 recover 复位 running→queued；看门狗 30min 回收卡死；_serve_one 外包异常收尾 |
| 2 | 幂等键去重；重复投递直接返回 | CLOSE-02 | ✅ pre_auth 按 idempotency_key 幂等；generate_segment 同 pre_auth_id 活跃任务直接返回 |
| 3 | 结构化日志（x-request-id）+ 失败重试指数退避 + 原子性/事务 + 写操作记 operation_logs | CLOSE-03 | ✅ 请求中间件带 x-request-id；出站调用指数退避重试(≤3)；失败自动退避重试(≤2)；写操作记 operation_logs；隐式事务回滚 |
| 4 | 失败路径已退积分（资金安全，最高优先级） | CLOSE-04 | ✅ 冻结即扣减；失败/超上限最终 failed 退冻结金；提交未落库即失败退还；启动 reconcile 防误退膨胀 |
| 5 | 提示词注入模板（角色 / 约束 / 不可变） | §9 | ✅ build_prompt 统一骨架；enhance_with_ollama / plan_memoir 已接入 |

冲突裁决：**MUST > SHOULD**；资金类（CLOSE-04）优先级最高。任何新功能合并前需逐行勾选本表。
