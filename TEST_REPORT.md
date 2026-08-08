# 项目中心 · 五阶段流水线 · 端到端测试报告

> 生成时间：2026-08-07
> 目标：证明「项目中心」五阶段（资产库 → 分场拆解 → 分镜编排 → 异步生成 → 合成全集）作为一条影视工业流水线，前后端串联、无故障跑通。

## 结论

- **后端 E2E（`tests/pipeline_e2e.mjs`）：8/8 通过**，覆盖 Phase 0 ~ Phase 5，含真实 ComfyUI 生成与合成。
- **前端 UI（`tests/ui_flow.mjs`，Playwright）：5/5 通过**，覆盖真实上传缩略图回流、五步流程导航、@提及→引用资产 chip、生成/合成按钮接线、无 JS 运行时错误。

两条流水线全部绿灯，五阶段可端到端无故障运行。

---

## 一、后端 E2E 结果（8/8）

运行：`node tests/pipeline_e2e.mjs`（后端 7788 + ComfyUI 8188 + Ollama 11434 在线）

| 阶段 | 用例 | 结果 | 耗时 |
|------|------|------|------|
| Phase 0 | 健康检查 | PASS | 65ms |
| 准备 | 创建测试项目 | PASS | 71ms |
| Phase 1 | 全局资产上传 + 封面回流 | PASS | 145ms |
| Phase 1 | 本集（项目）资产上传 + 列表隔离 | PASS | 241ms |
| Phase 2 | LLM 分场拆解（Qwen，source=llm） | PASS | 76.4s |
| Phase 3 | 片段创建 + 分镜编排（@提及→refs 落库） | PASS | 347ms |
| Phase 4 | 预授权 + 异步生成 + 轮询 | PASS | 426.3s（真实 ComfyUI） |
| Phase 5 | 合成全集 + 轮询 | PASS | 5.4s |
| **合计** | | **8 通过 / 0 失败** | ~8.5min |

关键证据：
- Phase 1 封面：`GET /assets/{id}/cover` 返回 HTTP 200，磁盘缩略图存在。
- Phase 1 隔离：本集资产 `is_global=0`，出现在 `GET /projects/{id}/assets`；`GET /assets` 虽返回全部（全局页前端按 `is_global!==0` 过滤），但该资产被正确标记为 `is_global=0`，全局视图不会展示——隔离契约成立。
- Phase 2：`split_story` 走 LLM（Ollama qwen2.5:7b）拆出 ≥1 场次。
- Phase 3：分镜 `asset_refs` 含场景资产 id，画外音 `@客厅_E2E` 原文落库。
- Phase 4：积分预估 → `pre-auth`（BR-004）→ 提交生成 → 轮询至 `succeeded`，产出 `result_url` 视频。
- Phase 5：积分预估 → `pre-auth` → 提交 `synthesize` → 轮询至 `succeeded`，产出合成视频。

## 二、前端 UI 结果（5/5）

运行：`node tests/ui_flow.mjs`（前端 8099 + 后端 7788）

| 用例 | 结果 |
|------|------|
| Phase 1 全局资产上传 → 缩略图回流（`.thumb-img` 出现） | PASS |
| 流程导航含 5 阶段入口（含「合成全集」） | PASS |
| Phase 3 @提及 → 引用资产 chip（`.shot-refs .asset-tag`，数量 1） | PASS |
| Phase 4/5 「重新生成」「合成全集」按钮存在 | PASS |
| 页面无 JS 运行时错误 | PASS |

## 三、本阶段修复清单

1. **`server/ai_local.py`**：`process_asset_upload` 误用未导入的 `uid()`，改为 `uuid.uuid4().hex`。（Phase 1 上传 500 根因，已在上一会话修复。）
2. **`tests/pipeline_e2e.mjs`**：修正「本集资产泄漏到全局库」错误断言。`GET /assets` 按设计返回全部资产，正确隔离契约是校验该资产 `is_global===0`（全局视图前端过滤），而非断言其不出现在 `/assets`。
3. **`src/components/ProjectFragmentEditor.vue`**（Phase 3 @提及→refs）：
   - 原仅在 `el-mention` 的 `@change`（**失焦**才触发）同步 refs，导致输入时不实时、测试 `.fill()` 不触发失焦而失败。
   - 新增 `@update:modelValue` 实时触发 `syncMentionRefs`，并重构为接收 `{field, value}` 覆盖量（v-model emit 时 shot 字段未必已回写），实现边输入边联动引用 chip；`@change` 与 `onSave` 预同步保留。
   - 重新 `npm run build` 生效。

## 四、复跑命令

```bash
# 后端五阶段 E2E（需 7788 / 8188 / 11434 在线）
node tests/pipeline_e2e.mjs

# 前端 UI 流程（需 8099 / 7788 在线，已安装 playwright）
node tests/ui_flow.mjs
```

## 五、环境依赖

- 后端：FastAPI 7788（venv `C:\Users\L\.workbuddy\binaries\python\envs\default`）
- ComfyUI：8188（v0.28.0，含 IPAdapter+CLIP）
- Ollama：11434（qwen2.5:7b，用于 Phase 2 分场拆解）
- FFmpeg：`D:\AI\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe`（Phase 5 合成）
- 前端：8099（`web_app.py` 服务 `frontend/dist`）
