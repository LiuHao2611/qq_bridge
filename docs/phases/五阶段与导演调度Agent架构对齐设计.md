# 五阶段 ↔ 导演调度 Agent 架构对齐设计文档

- 关联手册：`AI 短剧导演调度 Agent 系统开发执行手册.md`（Layer0–Layer3 / DAG N01–N09 / §6 错误码 / §5 资产注入）
- 关联代码：`D:\AI\manju_agent\server\director\`（Director Agent Core 骨架）
- 关联基建：`D:\AI\manju_agent\server\infra\`（Layer0 抽象）
- 适用环境：`personal_local_only`（无 GPU 集群 / 无 Celery / 无 Redis / 无对象存储；仅 Ollama + ComfyUI + FFmpeg + 单进程 worker）
- 文档状态：**骨架阶段**（2026-08-08 落地 Director Agent Core 契约 + 编排；填充阶段待续）

---

## 1. 背景与目标

用户要求「对项目中心的五个阶段底层进行调整」，参考执行手册的导演调度 Agent 思路重构底层。
经 AskUserQuestion 确认方向：**落地 Director Agent Core 架构 + 严格按手册建 Layer0 模块结构 + 先搭骨架再填充**。

目标不是推翻现有可运行系统，而是在不改动 `worker.py` / `routers.py` 的前提下，新增一层
**导演调度编排（Layer2 Director Agent Core）**，把现有 P2/P3/P4/P5 逻辑以最小改动委托进标准子 Agent，
使其满足手册的「四层架构 + DAG 编排 + §6 错误重试 + 统一接口」约束，并为后续分布式/并行化留出扩展点。

---

## 2. 四层架构 ↔ 现有五阶段映射

| 手册层 | 职责 | 现有项目对应 | 本仓库落点 |
|---|---|---|---|
| Layer0 基础设施 | 算力池 / 模型引擎池 / 存储服务 | Ollama + ComfyUI + 本地 outputs 目录 | `server/infra/`（compute_pool / model_pool / storage 抽象） |
| Layer1 资产管理 | 资产库 + 特征注入 | `assets` / `asset_stages` / `segment_asset_refs` 表 | 现有 DB，由 VisualControl 注入 |
| Layer2 Director Agent Core | 全局调度器 + 4 子 Agent | P2–P5 既有逻辑 | `server/director/`（scheduler + agents/） |
| Layer3 前端 | 项目中心 | Vue 项目中心（P1–P5 页面） | 现有前端，暂未接线（填充阶段加端点） |

### 五阶段 ↔ DAG 节点 ↔ 子 Agent 责任边界

| 阶段 | 业务 | DAG 节点 | 负责子 Agent | 委托的现有实现 |
|---|---|---|---|---|
| P1 | 资产初始化（冷启动） | — | （资产库自身） | `extract_assets_from_story` 等 |
| P2 | 项目策划与分场拆解 | N02 Script Parse | `ScriptParserAgent` | `ai_local.split_story_with_llm` |
| P3 | 多模态分镜编排与资产绑定 | N03 Visual Enhancement | `VisualControlAgent` | `ai_local.enhance_with_ollama` / `build_segment_prompt` |
| P4 | 任务分发与异步 AI 推理 | N04 Task Dispatch + N05 Shot Generate | `SequenceSchedulerAgent` | `ai_local.generate_segment_video`（与 `worker._serve_one` 同核心） |
| P5 | 多片段音视频合成 | N06 QC + N07 Audio + N08 Compose | `PostProductionAgent` | `ai_local.synthesize_project_video` |
| — | 触发接收 / 成片输出 | N01 Receive + N09 Final | `GlobalScheduler` 自身 | 参数校验 / 路径装配 |

---

## 3. Director Agent Core 契约（已落地）

- **统一接口**（`agent_interface.py`）：`AgentInterface.init / run / rollback / get_status`；
  `AgentContext`（trace_id 贯穿）/ `AgentResult`（status: success|partial_fail|failed）/ `AgentStatus`。
- **错误码体系**（`errors.py`，手册 §6）：`ErrorCategory` 1000–5999；`RETRY_POLICIES`
  （参数/资产 0 重试、模型 2 次常量 5s→降级模型、调度 3 次指数退避→降级队列、系统 1 次→告警）；
  `AgentError` 携带 category/code/policy；`retry_after(err, attempt)` 指数退避 + ±20% 抖动。
- **DAG**（`dag.py`）：`DAG_NODES` N01–N09（含 agent 映射）、`BRANCH_RULES`（§4.2 三条）、
  `topological_order()`、`PHASE_MAP`。
- **结构化日志**（`logutil.py`，LOG-01/02/03/04）：trace_id 贯穿、级别规范、event snake_case、敏感信息剔除。

### 子 Agent 骨架行为（含查漏补缺后的增强）

| Agent | run() 行为 | 失败处理 |
|---|---|---|
| `ScriptParserAgent` | 取 `ctx.params.story` → `split_story_with_llm`；**为分镜单元补 segment_id**；**资产自动绑定**：扫描场次文本按关键词匹配资产库（相似度≥0.85 绑定，低于标「待人工确认」），产出 `bound_assets`；缺 story 抛 `MISSING_SCRIPT` | `SCRIPT_PARSE_FAILED`（模型类，重试 2 次→降级模型） |
| `VisualControlAgent` | 读 upstream.segments + bound_assets，逐镜头 `build_segment_prompt` + 场景 `enhance_with_ollama`；**全局参数下发**（aspect_ratio / seed_strategy=inherit 跨镜微扰）；产出 `global_style_ref` / `asset_inject_config`（weight 0.85） | `VISUAL_ENHANCE_FAILED`（模型类重试） |
| `SequenceSchedulerAgent` | 逐片段调 `generate_segment_video`，落地 `segment.video_url`；**首尾帧锚定**（提取上片段尾帧作下片段首帧参考，经 `first_frame` 透传）；**断点续跑**（已有成功片段跳过）；`priority_level` 透传 | 单片段 fail 记 `partial_fail`；异常 `SHOT_GENERATE_FAILED` |
| `PostProductionAgent` | **N06 结构化品控**（文件存在 + 时长一致性 Σ镜头≈片段时长误差≤0.5s + 人脸相似度占位）→ `synthesize_project_video`；产出 `quality_report` / `failed_shot_list` | `COMPOSITION_FAILED`（系统类，重试 1 次→告警） |

### 查漏补缺对照（手册 §3 + 设计逻辑「导演型调度 Agent 系统设计逻辑」）

> 手册 §4 DAG（N01–N09）、§5 资产注入协议、§6 错误码/重试 经核对与骨架**完全对齐**（DAG 节点、RETRY_POLICIES、TAIL_FRAME_ANCHOR_FAILED 4002、ASSET_* 错误均已落地）。以下为手册 §3 子 Agent 规范 + 设计逻辑相对首版骨架的**补齐项**。

| 手册/设计逻辑要求 | 骨架状态 | 落地方式 |
|---|---|---|
| §3.1.4 资产自动匹配与绑定（关键词→资产 ID） | ✅ 已补齐 | `ScriptParser._auto_bind`：名称精确/分词包含作相似度代理，≥0.85 绑定，低于标待人工确认 |
| §3.1.4 粒度切分（max_shot_duration）/ 镜头参数补全 | ⚠️ 部分 | 切分由 `split_story_with_llm` 承担（既有）；镜头参数补全在 LLM 提示词层，骨架未额外做规则引擎二次校验 |
| §3.2.2 全局参数（aspect_ratio/seed_strategy）下发 | ✅ 已补齐 | `VisualControl` 消费 `global_params`，seed_strategy=inherit 跨镜微扰 |
| §3.2.4 全局风格参考图 / §5 真人脸特征注入 | ❌ 待 fill | 产出 `global_style_ref`/`asset_inject_config` 占位；实际注入需 ComfyUI IP-Adapter/ControlNet（full 版） |
| §3.3.4 首尾帧锚定 | ✅ 已补齐（编排层） | `SequenceScheduler` 提取尾帧经 `first_frame` 透传；引擎消费待 ai_local 接 img2video（full 版） |
| §3.3 分布式队列 / 并行 / 优先级队列 | ⚠️ 部分 | `priority_level` 透传记录；实际入队 worker + 并行由填充阶段接 `generation_tasks` |
| §3.3 断点续跑 | ✅ 已补齐 | `SequenceScheduler` 检测已有成功片段则跳过 |
| §3.4 自动化品控（人脸相似度≥0.8 / 场景匹配 / 时长） | ✅ 已补齐（结构） | `PostProduction._quality_check` 结构化报告 + `failed_shot_list`；人脸相似度占位（需比对算法，full 版） |
| §3.4 转场按镜头类型 / 全局后处理（人脸修复/降噪/锐化/帧率） | ❌ 待 fill | 由 ComfyUI 后处理节点承担（full 版） |
| 设计逻辑「空间站位预规划（blocking）」 | ❌ 待 fill | 多人场景站位蓝图，需场景理解，full 版 |
| §7.2 中间帧/尾帧参考图保留 7 天 | ✅ 已对齐 | `infra/storage` INTERMEDIATE_TTL_DAYS=7；尾帧落 outputs（本地中间存储） |

**设计三原则落地**：可控性优先（规则引擎资产绑定 + 参数下发，LLM 仅创意补全）✅；非破坏性编辑（分镜独立生成/重试/回滚，断点续跑）✅；资产沉淀复用（bound_assets 复用资产库）✅。

---

## 4. Layer0 抽象与本地落地的差异 / 裁剪原则

手册的 Layer0 面向分布式（算力池跨机、模型引擎池多卡、对象存储 S3）。本地 `personal_local_only` 做如下**等价裁剪**，不破坏抽象：

| Layer0 概念 | 手册（分布式） | 本地落地 | 裁剪原则 |
|---|---|---|---|
| 算力池 | 跨机 GPU 集群 + 队列 | `LocalComputePool`（ThreadPoolExecutor max_workers=2） | 抽象一致；`submit` 本地线程执行，注释标明分布式应入队 |
| 模型引擎池 | 多引擎 + 负载均衡 | `LocalModelPool`（映射 OLLAMA_URL / COMFY_URL） | 引擎分组一致（text2video/face_id/tts/enhance） |
| 存储服务 | 对象存储 + TTL 生命周期 | `LocalStorageService`（outputs + .meta sidecar） | `INTERMEDIATE_TTL_DAYS=7` / `FAILED_TTL_DAYS=7`，`cleanup_expired` 幂等分批 ≤500 带锁（OPS-02/03） |

**约束（AGENTS.md §08）平台特化参数**：视频生成超时 600s、图像 120s、普通 HTTP 30s、重试 3 次（1s/2s/4s+抖动）、
看门狗 >30min、清理批 ≤500。Director 调度器 `WATCHDOG_SECS=30*60` 已对齐。

---

## 5. 当前已落地文件清单（骨架）

```
server/director/
  __init__.py              包说明
  errors.py                §6 错误码 + 重试策略 + AgentError + retry_after
  agent_interface.py       AgentInterface / AgentContext / AgentResult / AgentStatus
  dag.py                   N01–N09 + BRANCH_RULES + topological_order + PHASE_MAP
  logutil.py               结构化日志工具
  scheduler.py             GlobalScheduler（N01/N09 + 编排/重试/降级/看门狗/回滚）
  agents/
    __init__.py            子 Agent 注册表
    base.py                BaseAgent（状态机 + 日志 + rollback 兜底）
    script_parser.py       N02
    visual_control.py      N03
    sequence_scheduler.py  N04/N05
    post_production.py     N06/N07/N08
server/infra/
  __init__.py / compute_pool.py / model_pool.py / storage.py   （Layer0 抽象，见 Task #61）
```

**验证（骨架自测）**：`GlobalScheduler.run(project_id=..., params={})` 在无 story 时正确经
N01→N02 失败收敛（CLOSE-01），错误码 1002（`MISSING_SCRIPT`）；无 project_id/story 时 N01
触发 1001（`INVALID_PARAMETER`）。结构化日志带 trace_id / event / level。

---

## 6. 填充阶段待办（full 版，不在本骨架范围）

> **骨架阶段已补齐（2026-08-08 查漏补缺，对照设计逻辑）**：以下行为已在骨架层落地，无需等 full 版：
> - ✅ **ScriptParser 资产自动匹配与绑定**：关键词→资产 ID 匹配（精确命中 conf=1.0 / 分词重叠 conf=0.6，相似度 ≥0.85 自动写入 `bound_assets` 并标 weight，低于阈值标「待人工确认」warning）；缺 segment_id 自动补 ID；粒度切分复用 `ai_local.split_story_with_llm`。
> - ✅ **VisualControl 全局参数下发**：`global_params`（aspect_ratio / seed_strategy / enable_real_asset）；`seed_strategy=inherit` 跨镜微扰（`running_seed=base+i`）；`asset_inject_config`（base_weight=0.85 / lora_weight_range=[0.3,0.6]）；输出 `global_style_ref` 占位。
> - ✅ **SequenceScheduler 首尾帧锚定 + 断点续跑 + 优先级**：`enable_tail_frame=True`（尾帧作下分镜首帧，`_extract_last_frame` 用 ffmpeg 抽尾帧）；`resume=True`（命中已有 video 则 skip）；`priority_level` 透传。
> - ✅ **PostProduction 结构化品控**：逐片段 `quality_report`（file_ok / duration_ok 误差≤0.5s / face_sim 占位 / pass），`FACE_SIM_THRESHOLD=0.8`；产出 `failed_shot_list` 供重生成。
>
> 剩余待 full 版落地的项（标注「骨架占位」= 已留接口/埋点，等引擎接入）：

1. **端点接线（✅ 已落地 2026-08-08）**：`POST /projects/{id}/director-run`（后台线程跑 `GlobalScheduler.run`，返回 run_id）+ `GET /director-run`（列表）+ `GET /director-run/{run_id}`（查询）；
   `routers.py` 门面新增（已 version_guard 备份），剧本缺省从项目场次聚合（`_build_project_story_text`）；`on_progress` 经 WS 频道 `director:<pid>` 推送进度（§6 进度透出已落地）。
   前端「导演一站式生成」入口已加在生成生产页（ProductionStage.vue），WS 优先、轮询兜底（REDUN-01），进度横幅 + 成片链接。
2. **P4 改入队（骨架占位）**：`SequenceSchedulerAgent` 现「直接调 `generate_segment_video`」；full 版改为向 `generation_tasks`
   入队，由 `worker` 消费；调度器按 REDUN-01（WebSocket 不可用则轮询）跟踪状态，支持并行度与失败重试。
3. **N03 真人资产注入（待引擎）**：接入 ComfyUI IP-Adapter / ControlNet，落地手册 §5 的 512 维人脸特征 +
   身份权重 0.8–0.9（骨架仅注入资产名不变量 + 分层权重配置，引擎消费侧待 full 版）。
4. **重试/降级落地（骨架埋点）**：`retry_after` + `_degrade` 已在 scheduler 接入；需补「降级模型 / 降级队列」的实际切换动作
   （`_degrade` 当前仅记录类别，未真切换）。
5. **进度透出（骨架占位）**：AgentResult 进度经 WebSocket / 轮询推前端（复用现有 `ws_publish`，骨架未接）。
6. **看门狗联动（骨架埋点）**：长耗时整链 >30min 触发 `watchdog_timeout` 已埋点，需接退款 + 告警（CLOSE-04 / OPS-04）。
7. **转场 / 全局后处理（待引擎）**：按镜头类型的转场、人脸修复 / 降噪 / 锐化 / 帧率统一后处理（骨架 `synthesize_project_video` 委托层未含，等 full 版）。
8. **自动化品控重生成闭环（骨架占位）**：`face_sim` 当前占位 None，`failed_shot_list` 产出但未自动触发重生成；等真人脸比对引擎接入后接回 SequenceScheduler 重投。
9. **规则引擎二次校验（待 full 版）**：设计逻辑「非破坏性编辑 / 资产沉淀复用」原则对应的规则引擎二次校验节点，骨架未含。

---

## 7. 纪律合规对照（DONE-01 自证预备）

- **CLOSE-01 状态机收敛**：`NodeStatus` PENDING→RUNNING→DONE/FAILED/SKIPPED；全链路终态单一，失败即 break 收敛。
- **CLOSE-02 不吞异常**：Agent 异常统一转 `AgentError` 上抛；`partial_fail` 不静默吞（warning 记失败片段）。
- **CLOSE-04 失败退积分**：积分相关逻辑仍在 `worker`（`_mark_failed` 退冻结金）；Director 生成路径复用 worker 时不重复扣退。
- **REDUN-01 降级链**：`ai_local` 内部 L1/L2/L3 三级降级；Director 层 §6 重试 + 降级类别。
- **REDUN-02 重试退避**：`retry_after` 指数退避 + 抖动，尊重 §6.2。
- **LOG-01/02/03/04**：`blog()` 全链路结构化 JSON，trace_id 贯穿，敏感字段剔除。
- **OPS-01 临时资源**：DB 连接 `try/finally` 释放；`generate_segment_video` 内部 temp 收尾。
- **OPS-04 看门狗**：`WATCHDOG_SECS=30*60` 整体超时保护。
