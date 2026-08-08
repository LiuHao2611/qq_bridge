# UI 导航 / 页签 规范文档（项目中心）

> 适用范围：`src/` 前端（Vue3 + Vite + Element Plus）、`server/`（FastAPI）。
> 强制级别：**MUST / FORBIDDEN / SHOULD**。违反 MUST/FORBIDDEN 的提交视为不合格。
> 本规范针对本次复盘发现的「点资产跳出项目中心」「创建/打开不落默认阶段」等低级 UI 设计问题，建立可审计的硬性规则。

---

## 0. 核心原则

项目中心是一个**带项目上下文（pid）的壳（shell）**。一切「项目内流程步骤」都必须留在壳内；只有明确的「全局功能」才允许脱离项目。

根因回顾：`App.vue` 的 `pid` 由路由参数派生（如 `/projects/:id`）。一旦跳到顶层全局路由（如 `/global-assets`、`/tasks`），`pid` 被清空 → 侧栏「项目内」分组消失、顶栏切换为全局标题，**用户感觉离开了项目中心**。因此：

> **任何会让 `pid` 清零的导航，都不得作为「项目内流程步骤」的落点。**

---

## 1. 项目内流程导航规则（MUST）

文件：`src/components/ProjectFlowNav.vue`

- 五阶段步骤的路由必须由 `pathPrefix: '/projects/'` + `suffix` 拼接而成，**禁止硬编码顶层全局路由**。
  - ✅ `{ id: 1, label: '资产初始化', pathPrefix: '/projects/', suffix: '/assets' }`
  - ❌ `{ id: 1, label: '全局资产', path: '/global-assets' }`  ← 会导致跳出项目
- `onStep` 已对带 `pathPrefix` 的步骤自动拼接 `/projects/:id<美化>`；新增步骤务必沿用该机制，**不要**新增 `path:` 顶层写法。
- 步骤文案必须使用「动词+名词」的阶段语义（资产初始化 / 分场拆解 / 分镜编排 / 异步生成 / 合成全集），**禁止**使用「全局资产」等会让用户误以为是脱离项目的词。
- 任务中心（步骤 4）属全局页（`/tasks`）——经用户确认保留全局，但**从项目内进入时必须提供「返回项目」面包屑**（见 §4）。

### 审计清单（逐项）
- [x] 步骤 1 已改为项目内路由 `/projects/:id/assets`（原名「全局资产」已改为「资产初始化」）
- [x] 步骤 2/3 使用 `/projects/:id/planning`、`/projects/:id/editor`（原本正确）
- [ ] 步骤 4 `/tasks` 从项目内进入时，补「← 返回项目」入口（待办，用户确认保留全局页）

---

## 2. 默认阶段规则（MUST）

文件：`src/components/ProjectEntry.vue`、`src/router.js`、`ProjectsList.vue`、`Home.vue`

- 项目统一入口路由：`/projects/:id` → `ProjectEntry.vue`，**按后端 `current_phase` 自动跳转**，禁止直接硬编码落到某一阶段。
- 创建项目 → 落点统一为 `/projects/:id`（入口），由 `current_phase` 决定实际阶段（新项目无资产 → `current_phase=1` → 资产初始化）。
- 打开已有项目 → 落点统一为 `/projects/:id`（入口），由 `current_phase` 决定实际阶段（进行中阶段）。
- **禁止** `router.push('/projects/' + id + '/editor')` 这类写死第 3 阶段的落点（旧代码已清除）。
- `current_phase` 由后端 `server/routers.py · get_project` 派生（无需新字段）：
  | 阶段 | 完成判定 |
  |---|---|
  | 1 资产初始化 | 本集(is_global=0)资产数 > 0 |
  | 2 分场拆解 | 存在 `scene_description` 非空的片段 |
  | 3 分镜编排 | 存在状态 ≠ `draft` 的片段 |
  | 4 异步生成 | 存在 `succeeded` 片段 且 无待生成片段 |
  | 5 合成全集 | 存在 `model_id='synthesize'` 且 `status='succeeded'` 的任务 |
  - 全部完成取 5；任一未完成 → 取第一个未完成阶段。

### 审计清单
- [x] `ProjectEntry.vue` 新增，按 `current_phase` 重定向
- [x] `ProjectsList.vue` 创建/打开落点改为 `/projects/:id`
- [x] `Home.vue` 创建/打开落点改为 `/projects/:id`
- [x] `router.js` 新增 `/projects/:id` 入口；`/project/:id` 重定向改为入口

---

## 3. 页签（tab）语义规则（MUST）

- **页内 tab（如资产库的角色/场景/道具、作用域的本集/全集）只改变本地状态，绝不触发路由跳转。** 它们只是同一页面内的视图筛选。
- 只有以下两类才允许路由导航：
  1. **流程步骤**（ProjectFlowNav 的 5 步）——项目内子路由。
  2. **侧栏条目**（Sidebar）——区分全局功能与项目功能。
- 禁止把「页内 tab」做成 `<router-link>` 或 `router.push`，否则会破坏「当前页」的连续性并制造返回歧义。

---

## 4. 返回项目入口（MUST）

- 每个项目内页面（`/projects/:id/*`）**必须**提供清晰的「← 返回项目中心」入口（顶栏或面包屑），落点为 `/projects`。
- 编辑器 `ProjectFragmentEditor` 已有返回入口；本次为 `Assets.vue`（资产初始化页）补上了 `ProjectFlowNav`（步骤 1 高亮），天然提供阶段间切换与返回。
- 从全局页（如 `/tasks`）反向进入项目时，全局页需提供「返回项目 #id」面包屑（**待办**）。

---

## 5. 顶栏 / 侧栏 一致性（MUST）

- 顶栏标题、侧栏高亮、实际路由三者**必须一致**。
- `TopBar.vue` 的标题映射须覆盖所有项目内路由：
  - `/projects/:id/assets` → 「素材库 / 资产初始化」
  - `/projects/:id/planning` → 「分场拆解」
  - `/projects/:id/editor` → 「分镜编排」
  - `/projects/:id` → 按当前阶段显示
- 侧栏「项目内」分组仅在 `pid` 存在时显示；任何项目内路由都必须让 `pid` 保持有值（呼应 §1 根因）。

---

## 6. 全局导航 vs 项目流程导航（SHOULD）

| 类别 | 示例 | 是否可顶层 |
|---|---|---|
| 全局功能（Sidebar 级） | 全局资产库 `/global-assets`、任务中心 `/tasks`、文生图、设置… | ✅ 可顶层 |
| 项目流程步骤（FlowNav 级） | 资产初始化 / 分场拆解 / 分镜编排 / 异步生成 / 合成全集 | ❌ 必须 `/projects/:id/*` |

- 全局资产库（`/global-assets`）是**全局页**，与项目内「资产初始化」页（`/projects/:id/assets`）是两回事：前者跨项目共享，后者是本项目资产库 + 一键复用入口。两者都合法，但**流程步骤不得指向全局页**。

---

## 7. 提交前自检（DONE-01 本地化）

每次涉及导航/页签的改动，交付前必须回答：
1. 该改动是否会让 `pid` 清零？（是 → 不合格）
2. 新建项目是否会落到第一阶段？
3. 打开旧项目是否会落到进行中阶段？
4. 页内 tab 是否只改本地状态、未路由？
5. 项目内页面是否有返回入口？
6. 顶栏/侧栏/路由三者是否一致？

---

## 8. 自动化回归

`tests/ui_flow.mjs` 已覆盖以下断言（违反即失败）：
- 新建项目 → 默认进入 `/projects/:id/assets`
- 点「资产初始化」步骤 → 路由仍含 `/projects/`，且不以 `/global-assets` 结尾
- 打开已完成资产初始化的旧项目 → 默认进入 `/projects/:id/planning`
- 全局资产上传 → 缩略图回流
- `@` 提及 → 引用资产 chip
- 生成/合成按钮存在、无 JS 运行时错误

运行：`node tests/ui_flow.mjs`（需先启动后端 7788 与 web_app 8099）。
后端五阶段无故障跑通见 `tests/pipeline_e2e.mjs` 与 `TEST_REPORT.md`。
