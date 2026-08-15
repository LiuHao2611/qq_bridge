# P3 `@提及` 绑定 P1 资产 —— 验证结论（任务①）

## 结论
**成立**：分镜编辑器（P3）的 `@提及` 确实从 P1 资产中选择真实身份卡，并以
`asset_id` 持久化到分镜的 `shot.refs`，是 P1 → 生成环节的真正资产关联链路。
这与权威手册（P1 M3 `inject_consistency(intent, shot["asset_id"])` / P2 M1
`build_shot_video_prompt` 在生成时消费 `asset_id`）一致。

## 绑定链路（代码证据）
1. **候选来源 = P1 资产**
   `src/components/ProjectFragmentEditor.vue:257` `mentionOptions` 由
   `p.value.characters`（角色+阶段）与 `p.value.assets.scenes/images/props`
   构建 → 选项即 P1 资产名。
   `p.characters` 由 `src/composables/useProject.js:110` 从 `assets` 中
   `type==='role'` 映射而来，确认是 P1 身份卡。

2. **名称 → 真实 asset_id**
   `ProjectFragmentEditor.vue:173` `resolveRefByName(name)` 按名称解析为
   `{ asset_id, asset_type, stage_id, name }`：
   - 阶段名（如「周岩-青年」）→ 含 `stage_id` 的角色引用（阶段即最小引用单元）；
   - 场景/道具按资产名 → `asset_id`，`stage_id:null`；
   - 裸角色名（非阶段名）返回 `null` 并提示改用阶段名（F3 修复，避免错误引用）。

3. **UI 接线**
   `ProjectFragmentEditor.vue:1180 / :1183` 画外音、对白两个 `el-mention`
   的 `@update:modelValue` → `syncMentionRefs(s, { field, value })`，
   把 `@[资产名]` 标记同步进 `shot.refs`（F7 修复：超过上限不再静默截断）。

4. **持久化**
   `ProjectFragmentEditor.vue:648` 保存前统一把画外音/对白里的 `@提及`
   同步进 `refs`；`shot.refs` 作为 `p.value` 的一部分经 `useProject.save`
   落库。引用对象以 `{ asset_id, asset_type, stage_id, name }` 存储，
   即绑定的是 P1 真实资产主键，非自由文本。

## 消费侧（生成时）
`shot.refs[].asset_id` 在生成阶段经 P1 M3 `inject_consistency` /
P2 M1 `build_shot_video_prompt` 消费（注入 IPAdapterFaceID / 风格锚点），
构成「P1 资产 → P3 绑定 asset_id → 生成时一致性注入」的完整闭环。

## 验证方式与局限
- 静态代码审计（行号证据如上）确认绑定与持久化逻辑存在且正确。
- 运行时落库验证需启动后端 + 浏览器（本项目验收脚本
  `scripts/fragment_editor_acceptance.cjs` 新增检查 8 `mention-ui-bound`
  已断言 `el-mention` 在编辑器 DOM 中真实渲染并接线）。
- 本次改动未破坏任何现有行为（仅增量暴露引用入口）。
