# QQ 机器人 · 任务中心设计方案（以「文生图」为首个落地场景）

> 版本：v1 → **已实施（2026-09-08）**
> 日期：2026-09-08
> 状态：P1~P5 全部落地并验收通过
> 目标：把 QQ 机器人从「生图发图工具」升级为**主项目的远程控制台**——
> 监控 / 任务下发 / 任务查看 / 任务排队 / 结果预览。
> 首个落地场景：`http://127.0.0.1:8099/txt2img`（文生图）。

---

## 0.1 实施状态（2026-09-08）

**用户拍板值**（已落配置，前台「常规·任务队列」可见 + 热生效）：
- `QUEUE_MAX = 20`（有界队列，满了明确拒绝并告知排队位）
- 队首遇 `IMG_BUSY(409)` → 退避 `300s`(5min)，重试 `3` 次；3 次用完仍忙 → 通知并弹出队
- `--编排 dify` **不做**（避免 LLM 延迟不可控）
- P5 Web 面板「📋 任务中心」tab **全做**

**交付清单**：

| 层 | 文件 | 说明 |
|---|---|---|
| P1a 配置 | `src/plugins/qq_bridge/config.py` + `config_api.py` | `QUEUE_*` / `MONITOR_*` 参数 + SCHEMA「常规·任务队列」分组（前台可见、热生效） |
| P1b 命令 | `src/plugins/qq_bridge/intent/__init__.py` | 生图全参解析（与 /txt2img 对齐）+ 任务/最近/队列/取消/插队/再看/监控 暗号路由 |
| P2 队列 | `src/plugins/qq_bridge/queue/__init__.py` | 持久有界队列（落 `data/queue.json`）+ 单消费者 + 409 退避 3×5min + 幂等去重 |
| P3 预览 | `__init__.py` `_cmd_again` + `send_images` | 「再看 <id>」重发结果图（不重新生成、不占队列） |
| P4 监控 | `src/plugins/qq_bridge/monitor/__init__.py` | 轮询告警（默认关，仅主人，幂等去重：失败/超时/持续繁忙） |
| P5 面板 | `src/components/QQTaskCenter.vue` + `api.js` | 8099 新增「📋 任务中心」tab：监控卡 / 下发 / 查看 / 排队 / 预览 |
| 接入 | `__init__.py` | 生命周期（on_startup/on_shutdown）+ REST 端点 `/api/qq/queue*` |

**验收（机器判定 + 非零退出）**：
- `tests/test_queue.py` —— **15/15**（含「3 次退避后通知并出队」）
- `tests/test_intent_task_cmds.py` —— **27/27**（生图全参 / 命令路由 / 在线通道零回归）
- `tests/test_monitor.py` —— **8/8**（幂等 / 超时 / 持续繁忙 / 默认关闭 / TTL 清理）
- `tests/verify_qq_task_center.mjs` —— **QQ_TASK_CENTER_OK**（监控卡 6 张 / 下发 / 详情 / 排队 / 加载更多范式 / 无 JS 错误）
- `tests/verify_qq_bridge_ui_v2.mjs` —— **14/14**（修复陈旧测试：默认 tab 改为先切「机器人配置」）
- `tests/verify_qq_row_focus_highlight.mjs` —— **13/13**（编辑高亮，承接上轮修复）

**操作复现**（真实界面）：
- QQ 私聊/群：`生图 一只猫 --比例 16:9 --数量 4` → 入队，完成后主动发图
- `队列` 看排队；`我的任务` 看进度；`再看 <号>` 重发结果图；`监控 开` 开告警（仅主人）
- 8099 `/qq-bridge` → 「📋 任务中心」：下发生图、看队列、点任务看详情与结果预览

**已知遗留（非本次引入，预存在）**：`qq_bridge/tests/` 全量跑有 24 个用例因**测试间隔离污染**（非代码缺陷，单跑均过）报失败，属测试套件卫生问题，不在本次任务范围；本任务新增 51 个单测全部通过。

---

## 0. 一句话结论

**主项目后端零改动、QQ 侧自建持久队列**——P1~P4 全部在 `qq_bridge/` 内完成，
只在 P5（Web 面板）需要主项目前端加一个 tab。排队放在 bridge 侧而非主项目侧，
理由是红线「QQ 服务崩溃绝不影响主项目 7788/8099」。

---

## 1. 检索到的行业案例（2025–2026）与对比

按项目铁律，动手前先检索。关键词：`telegram bot AI image task queue 2026`、
`chatops 命令设计 权限 幂等 任务编排 2026`、`QQ 机器人 任务下发 2026`。

| 案例 | 来源 | 核心做法 | 可借鉴点 | License / 可搬运性 |
|---|---|---|---|---|
| **stable-diffusion-telegram-bot** | [nonoo](https://github.com/nonoo/stable-diffusion-telegram-bot) | 请求进队列，**一次只处理一个**；用「回复原消息」展示进度，不刷屏 | ★★★★★ 与本项目「本地 ComfyUI 独占」**完全同构**，交互范式直接照搬 | 未声明 License → **只借鉴思路，不复制源码** |
| **FLUX.1-Kontext Telegram Bot** | [Duxon](https://github.com/Duxon/FLUX.1-Kontext-Image-Editor-Telegram-Bot) | `robust job queue` + **自动服务生命周期管理**（空闲自动停 ComfyUI） | ★★★★ 队列持久化 + 空闲回收显存，对 6GB 显卡有价值 | 未声明 → 借鉴思路 |
| **botatobot** | [scristobal](https://github.com/scristobal/botatobot) | **bounded worker queue**（有界队列，满了明确拒绝而非无限堆积） | ★★★★ 「有界」很关键——防内存打爆 | 未声明 → 借鉴思路 |
| **ChatOps 中文实战（2026-06）** | [知乎 / smidigital](https://zhuanlan.zhihu.com/p/2052377031053252163) | 三步走：高频操作封装为命令 → 意图解析 → **参数校验**；强调不要把 ChatOps 降维成"群机器人" | ★★★★ 命令分层 + 参数前置校验 | 文章，借鉴范式 |
| **Atlassian ChatOps 事件管理** | [atlassian.com](https://www.atlassian.com/zh/incident-management/devops/chatops) | 事件驱动告警入群 + 可回写操作 | ★★★ 告警通道设计 | 文章，借鉴范式 |
| **QQ 官方机器人文档（2026-08 更新）** | [bot.q.qq.com](https://bot.q.qq.com/wiki/) | 官方支持 Webhook + Agent 接入 | ★★ 本项目走 NapCat 个人号，官方方案不适用，仅作对照 | 官方文档 |

### 跨案例的四条共识（本方案全部采纳）

1. **排队在 bot 侧**：后端只管"一次跑一个"，队列由 bot 持有，不阻塞聊天线程。
2. **进度用「一条消息反复更新」而非刷屏**：Telegram 用 edit_message。
3. **高危命令做权限分级**（取消/清空/改配置限主人）。
4. **有界队列**：满了明确拒绝并告知排队位置，不无限堆积。

### QQ 平台的能力边界（必须诚实说明，交互要相应降级）

| 能力 | Telegram | QQ（NapCat + OneBot V11） | 本方案降级方案 |
|---|---|---|---|
| 编辑已发消息 | ✅ edit_message | ❌ 不支持（只有 delete_msg） | 提交时**先发一条"排队中 #N"占位**，完成后**另发结果**；占位消息不删（避免刷屏删除的观感） |
| Inline Keyboard 按钮 | ✅ callback | ❌ 不支持 | **文本菜单 + 数字选择**（发 `1` / `2` / `3`），配合现有「女仆暗号」自然语言 |
| 消息长度限制 | 4096 | ~2000 字安全 | 长列表**分页**，超出折叠为"还有 N 条，回复 `继续` 查看" |

---

## 2. 现状盘点（已有什么，别重复造轮子）

### 2.1 主项目后端（7788）——**已具备，全部可复用**

| 端点 | 现状 | 复用方式 |
|---|---|---|
| `POST /qq/image-generate` | **已存在的 QQ 专用端点**：入队 `image_tasks`，返回 `task_id`；忙时 **409 `IMG_BUSY`**；参数缺失用 `settings.qq.imageDefaults` 补齐；**必须指定有效模板**（拒绝退化 SD1.5） | ★ 任务下发直接用，`P1` 零后端改动 |
| `GET /t2i/tasks/{task_id}` | 任务详情（含 params/result 解析） | ★ 任务查看 / 结果预览 |
| `GET /t2i/tasks` | 列表，支持 `page/size/status/source/type/type_prefix/count_only` | ★ Web 面板列表 + QQ `最近任务` |
| `GET /t2i/active-task` | 当前活跃任务，无则 `{"task": null}` | ★ 监控轮询（最轻量） |
| `POST /t2i/tasks/{task_id}/stop` | 停止任务 + 中断 ComfyUI + 卸载模型（释放显存） | ★ `取消` 命令 |
| `GET /t2i/templates` | 模板列表 | ★ 下发时选模板 |
| WS `/ws` + `notifications` 通道 | `server/realtime.py` 已有 `publish(channel, event, data)` | ☆ 监控告警**可选**通道（P4 评估） |

**关键既有设计决策（正确，不可推翻）**：
`/t2i/generate`（页面端点）入队时会**把别人正在跑的任务置 cancelled 并中断 ComfyUI**。
所以 **QQ 绝不能打 `/t2i/generate`** —— 会把用户在页面上跑了一半的图打断。
`/qq/image-generate` 正是为此而生的正交端点，本方案继续沿用它。

### 2.2 QQ bridge（8080）——已有骨架

| 模块 | 现状 | 用于 |
|---|---|---|
| `intent/` | 命令路由 + **女仆暗号**（自然语言口令，如「任务进度」→`/status`） | 新增命令在此注册 |
| `taskmgr/` | 任务编排：HTTP 调 7788、重试退避、**全局任务簿 `_current_tasks`**、本地通道串行锁 | 队列消费者复用它的 `_request` / `_poll_task` |
| `pic/` | **自绘图片引擎**（进度图 / 配额图 / 菜单图 / 主题） | ★ 结果预览、队列看板直接复用自绘卡片，不用拼长文本 |
| `dispatcher/` | 事件标准化 + 白名单/限流 | 权限分级已有 `BOT_OWNER_QQ` / `STRICT_WHITELIST` |
| `config_api.py` | 配置热生效（昨天刚修好） | 新增 `QUEUE_*` 参数走同样机制，热生效 |
| `history/` | SQLite 存会话/群/成员 | 任务审计落 `qq_bridge/data/` |

### 2.3 Web 管理页（8099 `/qq-bridge`）——**只有 2 个 tab**

```
💬 对话  |  ⚙️ 机器人配置
```
→ 需要新增 `📋 任务中心`（P5）。

---

## 3. 核心架构决策：排队放哪一层？

这是本方案**唯一需要拍板**的架构问题。三个选项：

| 选项 | 做法 | 优点 | 缺点 | 判定 |
|---|---|---|---|---|
| **A. 主项目后端排队** | 改 `image_tasks` + `worker.py` 为真 FIFO 队列 | 全平台统一；状态机单一；Web 端天然可见排队 | 要动主项目核心（worker / 表结构），**风险高**；违反「QQ 不拖垮主项目」；页面与 QQ 抢占语义要重设计 | ❌ 否决 |
| **B. bridge 内存队列** | bridge 里 `asyncio.Queue` + 单消费者 | 主项目零改动 | **bridge 重启队列全丢**；Web 端看不到排队中任务 | ⚠️ 不够 |
| **C. bridge 持久队列（有界）** | `qq_bridge/data/queue.json` + 单消费者 + 重启恢复 | 主项目零改动；重启不丢；**符合红线** | 排队中的任务在 Web 端 `image_tasks` 里看不到（因为还没提交） | ✅ **选定** |

### 选 C 的理由

1. **红线优先**：QQ 服务崩/重启，主项目 7788/8099 完全无感，排队数据只在
   `qq_bridge/data/` 内，损失可控。
2. **与行业案例一致**：三个 Telegram 案例全部是「bot 侧持队列」。
3. **有界**：`QUEUE_MAX`（默认 20）满了明确拒绝并告知"队列已满，请先取消一些"，
   避免内存/文件无限膨胀（借鉴 botatobot）。
4. **Web 端可见性**用「合并视图」补：QQ 的 `/queue` 与 Web 面板都读
   bridge 的 `GET /api/qq/queue`（排队中）+ 主项目 `GET /t2i/tasks`（已入平台的），
   前端合并渲染，标注来源。

### 队列状态机（bridge 侧，与 `image_tasks` 状态机正交）

```
                   ┌──────────────────────────────────────┐
  用户下发 ──▶ queued ──▶ submitting ──▶ platform_task ──▶ done
                  │           │                │
                  └──▶ canceled   └──▶ failed  └──▶ failed
                       (用户/主人)     (409 IMG_BUSY 会
                                        自动退避重投,
                                        不入队尾,保持队首)
```

**关键细节**：消费者拿到队首 → 调 `/qq/image-generate` → 若返回 **409 `IMG_BUSY`**
（说明平台正忙，可能是用户在页面上跑图）→ **不弹出队**，退避 5s 重试（最多
`QUEUE_BUSY_RETRY` 次，默认 60 次 ≈ 5 分钟），超时才标记 failed 并通知。
这样保证：**页面任务优先，QQ 任务排队等，互不打断**（延续 §2.1 的正交设计）。

---

## 4. 功能设计（5 大能力）

### 4.1 任务下发（P1）

**命令**（沿用女仆暗号风格，`/斜杠` 与自然语言双通道）：

```
生图 <提示词> [--尺寸 1024] [--比例 16:9] [--步数 20] [--数量 4]
     [--种子 -1] [--模板 <名/id>] [--负向 <负面词>] [--风格 电影感]

# 自然语言等价
帮我画一只在雨中撑伞的猫
画 4 张 赛博朋克城市 --16:9
```

**参数契约**（与 `/txt2img` 页面**严格对齐**，避免"QQ 与页面参数漂移"）：

| 页面字段（`useT2i.state`） | QQ 参数 | `/qq/image-generate` 字段 | 默认来源 |
|---|---|---|---|
| `prompt` | 位置参数 | `prompt` | 必填 |
| `negative` | `--负向` | `negative` | `qq.imageDefaults` |
| `resolution` | `--尺寸` | `resolution` | `qq.imageDefaults` |
| `ratio` | `--比例` | `ratio` | `qq.imageDefaults` |
| `steps` | `--步数` | `steps` | 模板值（蒸馏模板自带 8） |
| `count` | `--数量` | `count` | `qq.imageDefaults` |
| `seed` | `--种子` | `seed` | `-1` 随机 |
| `style` | `--风格` | `style` | `qq.imageDefaults` |
| `templateId` | `--模板` | `template_id` | `qq.imageDefaults`（**必填，否则后端拒绝**） |

> 页面里 `model / enhance / orchestrator` 三项**不暴露给 QQ**：
> `model` 在 QQ 通道等价于模板（选了模板即选了模型）；`enhance`/`orchestrator`
> 走 `qq.imageDefaults` 固定值，避免聊天里调 LLM 编排导致不可控延迟。
> （若后续要支持，再开 `--编排 dify` 开关，默认关。）

**反馈**（不刷屏）：
1. 立即回一条**自绘卡片**（复用 `pic/`）：`已入队 · 队列第 N 位 · 提示词摘要 · 参数摘要`
2. 开始执行时**再回一条**：`开始生成（task_id 前 8 位）`
3. 完成时**发结果图 + 一条完成卡片**（耗时 / 尺寸 / 种子 / task_id）

### 4.2 任务查看（P1）

```
任务             → 我最近 5 条任务（自绘卡片，含状态/耗时/缩略图）
任务 <id前8位>   → 单条详情（状态 / 进度 / 参数 / 结果图）
最近             → 全局最近 10 条（含页面上发起的，标注来源）
```

数据来源：`GET /t2i/tasks`（`source=local`、`type_prefix=t2i`）→ 过滤
`params.channel == "qq"` 标记 QQ 来源（`/qq/image-generate` **已写入**
`"channel": "qq"`，见 `routers.py:2446` —— 已具备，零改动）。

### 4.3 任务排队（P2）

```
队列      → 自绘队列看板：排队中 N · 运行中 1 · 平台忙等待 M
插队 <id> → 仅主人：把某任务提到队首
```

`GET /api/qq/queue`（bridge 新增）返回：
```json
{ "ok": true,
  "queue": [ {"seq":1,"user_id":123,"prompt":"...","enqueued_at":1699,"state":"queued"} ],
  "running": { "seq": 0, "task_id": "ab12cd34", "platform_task_id": "..." },
  "platform_busy": false,
  "max": 20 }
```

### 4.4 结果预览（P3）

| 场景 | 交互 | 实现 |
|---|---|---|
| **QQ 内看图** | 完成时直接发原图（现有能力已支持） | 复用 `taskmgr._abs_url` 拼绝对地址 |
| **QQ 内看网格** | `任务 <id>` 用 `pic/` 自绘**多图拼接触点图**（4 张拼 2×2） | 复用 `pic/canvas.py` |
| **Web 面板预览** | 缩略图网格 + 点击放大 + 原图对比滑块 | 复用现成 `src/components/ImageCompare.vue`（记忆 86998232） |
| **再看一遍** | `再看 <id>` 重发结果图（不清队列、不重新生成） | 纯查库 |

**为什么结果预览要复用 `ImageCompare.vue`**：该项目组件已两次修过「两图不在同一框」
的 bug，有验收测试 `tests/verify_image_compare_same_frame.mjs`。**重复造轮子违规**
（铁律第 9 条），Web 面板直接复用。

### 4.5 监控（P4）

**被动查询**（`你咋样` / `状态`）：
```
平台: 在线 ✅   ComfyUI: 空闲/生成中(63%)
队列: 排队 2 · 运行中 1
今日: 成功 18 · 失败 2 · 平均 71s
最近失败: IMG_BUSY ×2（本地通道忙）
```
→ 用 `pic/` 自绘一张状态卡（比纯文本清晰，且已有进度图/配额图先例）。

**主动告警**（`监控 开/关`，仅主人）：
- bridge 后台 `asyncio.Task` 每 `MONITOR_INTERVAL`（默认 30s）轮询
  `GET /t2i/active-task` + `GET /t2i/tasks?status=failed`
- 触发条件（可在 `.env` 配，全部默认关，避免打扰）：
  - 任务失败 → 推主人（含 task_id + 错误摘要）
  - 任务超时（> `MONITOR_TASK_TIMEOUT`，默认 600s）→ 推主人
  - 连续 N 次（默认 3）`IMG_BUSY` → 推"平台持续繁忙"
- **幂等**：同一 task_id 同一事件只推一次（内存 `set` + TTL），防重复告警刷屏

> 告警走 **轮询**而非主项目 WS：bridge 是独立进程，接 WS 会引入长连接耦合，
> 一旦主项目重启 bridge 要处理重连风暴。**轮询 + 退避**更符合「单向依赖、可独立崩溃」。

---

## 5. Web 管理页（P5）：新增 `📋 任务中心` tab

```
💬 对话  |  ⚙️ 机器人配置  |  📋 任务中心
```

布局（沿用现有 `.qq-panel` 左侧导航 + 右侧行型的成熟版式）：

```
┌─────────────────────────────────────────────────────────┐
│ [监控卡 ×4] 平台在线 │ ComfyUI 空闲 │ 队列 2 │ 今日 18 ✅ 2 ❌ │
├──────────────┬──────────────────────────────────────────┤
│ 左：队列视图  │  右：任务详情 + 结果预览                   │
│  ├ 排队中 (2)│  ┌ 提示词 / 参数 / 状态 / 耗时 ┐           │
│  ├ 运行中 (1)│  └ 缩略图 2×2 网格（点击放大）┘           │
│  ├ 已完成    │  [重新生成] [取消] [下载]                  │
│  └ 失败      │                                          │
├──────────────┴──────────────────────────────────────────┤
│ 下发区：提示词 + 尺寸/比例/步数/数量 chops + [开始生成]    │
└─────────────────────────────────────────────────────────┘
```

**预览交互**：沿用已沉淀的「加载更多 + 进度指示」（记忆 18700683）——
底部「已展示 N / 共 M」+ 按钮，加载完显示「— 已展示全部 M —」，
**不用传统数字分页**。

**编辑高亮**：沿用昨天刚加的 `.qq-row:focus-within`（已在 `QQBridgePanel.vue`）。

---

## 6. 权限与红线（不可违背）

| 项 | 规则 |
|---|---|
| **单向依赖** | QQ 只经 HTTP 调 7788 `/api/v1`，**绝不 import `server/`**（铁律 4） |
| **目录隔离** | 队列数据 `qq_bridge/data/queue.json`；日志 `qq_bridge/logs/`；**不碰主项目 `logs/`、`data/`**（铁律 1/5） |
| **端口独立** | bridge 8080（配置+队列 API）不变；不新开端口 |
| **崩溃隔离** | bridge 挂了 → 主项目 7788/8099 照常；queue.json 留待重启恢复 |
| **权限分级** | 普通白名单用户：下发 / 查看自己 / 取消自己；**仅主人（`BOT_OWNER_QQ`）**：插队 / 清空队列 / 看全局 / 开监控告警 |
| **不打断页面** | 消费者遇 409 `IMG_BUSY` 退避重试，**绝不**打 `/t2i/generate`（会 cancel 别人的任务） |
| **幂等** | 同一 `(user_id, prompt, 参数hash)` 在 `IDEMPOTENT_TTL`（默认 60s）内重复下发 → 返回原任务，不入第二条 |

---

## 7. 分阶段落地计划

| 阶段 | 内容 | 主项目改动 | 验收方式 |
|---|---|---|---|
| **P1 命令层** | `生图` 全参解析 + `任务`/`最近` 查询 + `取消`；接 `/qq/image-generate` | **0** | `qq_bridge/tests/` 单测 + 真机 QQ 冒烟 |
| **P2 排队层** | 持久有界队列 + 单消费者 + 409 退避 + `GET /api/qq/queue` | **0** | 单测（MockTransport 免起后端）+ 重启后队列恢复 |
| **P3 预览层** | 多图拼接自绘卡片 + `再看 <id>` | **0** | 自绘图快照对比 |
| **P4 监控层** | 状态自绘卡 + 轮询告警（默认关）+ 幂等去重 | **0** | 单测 + 模拟失败任务触发告警 |
| **P5 Web 面板** | 8099 新增 `📋 任务中心` tab | 前端 `QQBridgePanel.vue` + 新组件 | Playwright 验收脚本（复用 `tests/verify_qq_*.mjs` 风格） |

**建议先做 P1+P2**（价值最大、改动最小、主项目零风险），上线跑几天再决定 P3~P5。

---

## 8. 待你拍板的 4 个问题

1. **排队上限**：`QUEUE_MAX` 默认 20 是否合适？（6GB 显卡，单张 60~80s，20 张 ≈ 25 分钟）
2. **平台忙时**：QQ 任务在队首遇到 `IMG_BUSY` 时，退避上限 5 分钟（60 次 × 5s）是否合理？
   还是直接告诉用户"平台忙，稍后再投"并弹出队？
3. **下发参数暴露粒度**：是否要开放 `--编排 dify`（会调 LLM，延迟不可控）？
   方案里默认**不开放**。
4. **P5 Web 面板**是否本期就做，还是先只做 QQ 侧（P1~P4）？

---

## 9. 附：本方案复用的既有资产清单（避免重复造轮子）

| 资产 | 位置 | 用于 |
|---|---|---|
| `/qq/image-generate` 端点 | `server/routers.py:2365` | 任务下发（**已支持全参 + 忙闲判定 + channel=qq 标记**） |
| `GET /t2i/tasks` / `{task_id}` / `active-task` | `server/routers.py:1523/1658/1705` | 查看 / 监控 |
| `POST /t2i/tasks/{id}/stop` | `server/routers.py:2462` | 取消 |
| 自绘图片引擎 | `qq_bridge/src/plugins/qq_bridge/pic/` | 队列看板 / 状态卡 / 多图拼接 |
| 女仆暗号机制 | `qq_bridge/.../intent/__init__.py:22` | 自然语言命令 |
| 全局任务簿 | `qq_bridge/.../taskmgr/__init__.py:95` | 运行中任务跟踪 |
| 配置热生效 | `qq_bridge/.../config_api.py` | 新增 `QUEUE_*` 参数热生效 |
| `ImageCompare.vue` | `src/components/ImageCompare.vue` | Web 端结果对比（有验收测试） |
| 加载更多分页模式 | 记忆 18700683 | Web 面板历史列表 |
