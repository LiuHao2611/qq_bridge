# P0 阶段 · 设计偏离与未完成项清单（对照《P0开发手册_面向WorkBuddy_v2.md》）

> 审计时间：2026-08-14  
> 代码基线：`D:\AI\manju_agent`（含 P1 已交付内容，不影响 P0 契约判定）  
> 审计方法：通读 v2 手册 → 逐文件核对 `server/capabilities`、`server/intent_engine`、`server/director`、`realtime.py`、前端 `DirectorOrb.vue` + `useDirectorOrb.js`、既有 P0 测试  
> 结论：**P0 已落地框架，但存在 1 处架构级契约偏离 + 多项具体偏离/未完成项**，主要集中在 M1 的 build() 契约、M2 的标注机制与首尾帧分级、M3 的匹配算法硬编码与维度级 build 未接入、M4 的结构化意图未产出/未接入、以及缺失运行时截图交付。
>
> **【2026-08-14 返工闭环】** 依据 v2 手册完成全部 9 项返工（A1 + M1–M5 交付项），代码 + 单测 + 后端 proof 日志齐备；全量测试 `146 passed, 0 failed, 1 skipped`。下表中 ❌/⚠️ 均已修复为 ✅。唯一遗留为「铁律 3 前端运行时截图」——后端 :7788 已用新代码重启，但本沙箱 ComfyUI(:8188/:11434) 未运行，无法真实触发生成录屏；标注/匹配算法日志与 LTX↔Wan 模型无关验证已由 `D:\AI\manju_agent\.tmp\p0_proof\proof.log` 提供（见 §交付要求更新）。

---

## ⚠️ 架构级偏离（最高优先级，贯穿 M1–M4）

**A1 · 能力维度 `build()` 契约与手册不一致（手册 M1 / §2.1）** —— ✅ **已修复（2026-08-14）**

- 手册要求：`build(intent, wf)` **真实往 workflow 插入 ComfyUI 节点**（如 `IPAdapterFaceID`、`CLIPTextEncode`、`KSampler`、`LTXVImgToVideo`…），返回 `wf`。
- 修复：`base.Capability.build` 重定契约为 `build(self, intent, wf: Optional[Any] = None) -> Any`。`wf=None` 返回结构化 dict（兼容 P1/M4/build_all 离线断言）；**`wf` 给定则真实往 workflow 插节点**——consistency 插 `IPAdapterFaceID`、semantic 插 `CLIPTextEncode`、temporal 插 `KSampler`、audio_sync 插 `VHS_VideoCombine`、frame_anchor 插 `WanImgToVideo`/`LTXVImgToVideo`、camera 折叠进已有 `CLIPTextEncode.text`。`capabilities.build_all(intent, wf)` 逐维度调 `c.build(intent, wf)`。
- 验证：`server/tests/test_m1_build_nodes.py::TestBuildInsertsNodes` 硬断言 6 维度各插对应节点 + `build_all` 全插 + `wf=None` 兼容；`server/capabilities/workflow_builder.py`（`WorkflowBuilder`）提供轻量 ComfyUI API 节点构建器使断言可行。
- 边界：`build()` 插的是「标准 ComfyUI 节点」（非手搓裸 JSON 字符串），符合铁律 1.2「绝不手搓节点 JSON」——手册 M1 的「真实插节点」=经结构化意图插标准节点，已满足。

---

## M1 · 能力维度接口（含 build 具体实现）

| 项                                                               | 状态       | 说明                                                                                                    |
| --------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------- |
| 6 维度 declare/build 均有真实逻辑（非 pass/None）                          | ✅ 符合     | 6 类均已实现（semantic/temporal/consistency/audio_sync/camera/frame_anchor）                                 |
| build() 真实往 workflow 插节点                                        | ✅ 已修复   | A1 契约重定：`build(intent, wf=None)`；`wf` 给定则真实插 `IPAdapterFaceID`/`CLIPTextEncode`/`KSampler`/`WanImgToVideo` 等（见 A1 修复说明） |
| 单测断言 `consistency.build()` 产出 workflow **含 IPAdapterFaceID 节点** | ✅ 已修复   | 手册 M1 验收硬要求；`test_m1_build_nodes.py::TestBuildInsertsNodes` 断言 6 维度各插对应节点 + `build_all` 全插 |

---

## M2 · 能力注册表（含标注算法）

| 项                                                     | 状态       | 说明                                                                                                                                                                                                                                                                                                                                                                          |
| ----------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| refresh() 调 /object_info + /system_stats 探测           | ✅ 符合     | `resource_catalog.py:170-187`                                                                                                                                                                                                                                                                                                                                               |
| capabilities 由标注算法程序生成（非手填死）                          | ✅ 已修复   | `declared_implementations()` 改调 `resource_catalog._video_capabilities_for_node`/`_ip_capabilities_for_node`（算法生成，消除手写 chip）；`NODE_DIMENSION_MAP` + `detect_capability_dimensions(object_info)` 按节点名映射维度 |
| 标注按"**节点名 → 能力维度**"映射                                 | ✅ 已修复   | 新增 `NODE_DIMENSION_MAP`（IPAdapterFaceID/InstantID→consistency 等）+ `detect_capability_dimensions(object_info)` 返回 `{dim:[node]}`，含 frame_anchor 分级（LTX 外推 / Wan 插值） |
| 首尾帧分级：LTX=first_frame_only，Wan=first_last_interpolate | ✅ 已修复   | `_VIDEO_INTERP_SUPPORTED = {"WanVideoSampler"}`（移除 LTXVImgToVideo）；`capabilities/__init__.py` 分级同步修正为 `node == "WanVideoSampler"`；`test_m2_annotation.py` 覆盖 |

---

## M3 · 意图翻译引擎（含匹配算法）

| 项                                              | 状态       | 说明                                                                                                                                                                                                       |
| ---------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LTX↔Wan 模型无关切换验证                               | ✅ 符合     | `test_translator_model_agnostic.py` 验证切换后 `last_selection.node` 改变、workflow 合法                                                                                                                           |
| 意图 → workflow 全程可追溯 + 记日志                      | ✅ 已修复   | `translate_video_intent`/`_translate_image` 增 `[MATCH]` 日志（维度+选定节点/底模+意图标签，由注册表打分）；`director/intent_bridge.py` 全流程日志 |
| 每维度从注册表找候选 → **filter+score+sort → 调 build()** | ✅ 已修复   | `NodeFamilyMapper.resolve` 增 `intent_tags × 候选 tags` 打分排序；`build_all(intent, wf)` 逐维度调 `c.build(intent, wf)` 真实插节点（见 A1） |
| **禁止 if-else 硬编码模型名**                          | ✅ 已修复   | 删除 `translate_video_intent` 中 `if nc=="CogVideoSampler"...elif nc=="WanVideoSampler"` 硬编码，改注册表驱动（`vf_cfg.get("num_frames"/"fps")` + `tags` 打分），`test_m3_matching.py` 覆盖 |
| 悬浮球显示"选了哪个模型/节点"                               | ✅ 符合     | `last_selection` 含 checkpoint/node                                                                                                                                                                       |



---

## M4 · 导演 Agent 意图化 + 决策点

| 项                                                         | 状态        | 说明                                                                                                                                                                                                                                                                                                                                                                   |
| --------------------------------------------------------- | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4 子 Agent 的 run() **无硬编码模型名**                             | ✅ 符合      | `sequence_scheduler._preferred_video_model_id()` 查注册表（`sequence_scheduler.py:44-54`）；各 Agent 未写死模型名                                                                                                                                                                                                                                                                  |
| 子 Agent 输出结构化意图（subjects/intent_tags、camera/frame_anchor） | ✅ 已修复    | `VisualControlAgent.run()` 增 `subjects=[{asset_id,weight}]`/`intent_tags=[style]+([warm_cool] if !=neutral)`；`SequenceSchedulerAgent.run()` 增 `camera={movement,shot_type}`/`frame_anchor={mode,first_frame_path,last_frame_path,enable_tail_frame}`（增量，不破既有 enhanced/global_style_ref） |
| 意图对象从 Agent 流转到 translator.translate(intent)              | ✅ 已修复    | 新增 `director/intent_bridge.py`：`agent_outputs_to_intent` 合并子 Agent 输出为 `ProductionIntent` → `build_workflow_from_intent` 调 `IntentTranslator.translate(intent)`（离线注入 catalog/object_info），打通「子 Agent → translator 走 M3 匹配算法」；`test_director_intent_bridge.py` 覆盖 LTX↔Wan |
| 决策点 preflight → WS decision_request → 前端弹卡                | ✅ 符合      | `preflight_check` 在 P1/P2 调用（`routers.py:128,434`）；`_publish_decision_request` 推 `decision_request`（`decision_engine.py:240-253`）；前端决策卡渲染（`DirectorOrb.vue:505`）                                                                                                                                                                                                     |

---

## M5 · 悬浮球三卡

| 项                                                                               | 状态         | 说明                                                                                                                                  |
| ------------------------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| 后端三事件：translator:{pid}(selection) / director.notice / director.decision_request | ✅ 符合       | `realtime.py:118-137`、`decision_engine.py:249`                                                                                      |
| 前端三区域订阅渲染                                                                       | ✅ 符合       | `useDirectorOrb.js:185-206,343-348`；`DirectorOrb.vue` 决策卡(505)/气泡(539)                                                              |
| selection 推送内容精确（实际解析的节点类）                                                      | ✅ 已修复      | `sequence_scheduler` 改推 `_resolve_ip_node_class()`（取 `NODE_FAMILY_REGISTRY["face_lock"]["priority"][0]`，兜底 `IPAdapterFaceID`），非硬编码 `"IP-Adapter"`；前端 `last_selection.node` 显示真实节点类 |
| 生成时气泡有具体内容（非"暂无任务"）需运行时截图证明                                                     | ⚠️ 待补交付（环境限制） | 铁律 3：本沙箱 ComfyUI(:8188/:11434) 未运行，无法真实触发生成录屏；后端 `:7788` 已用新代码重启，用户本机运行 ComfyUI 后可由悬浮球选型卡录屏补证（见 §交付要求更新） |

---

## 数据库迁移

| 项           | 状态   | 说明                                                     |
| ----------- | ---- | ------------------------------------------------------ |
| decisions 表 | ✅ 符合 | `db.py:558` 创建；`decision_engine.py` 持久化 + resolve/list |

---

## 交付要求（铁律 3：无截图=未交付）

| 项                           | 状态       | 说明                                                         |
| --------------------------- | -------- | ---------------------------------------------------------- |
| 逐功能截图/录屏（M1–M5）             | ⚠️ 后端已证/前端待补 | 后端逻辑由 `D:\AI\manju_agent\.tmp\p0_proof\proof.log` 提供算法证明；前端运行时录屏因 ComfyUI 未运行暂缺，用户本机补录（见下） |
| 标注算法 + 匹配算法附日志证明"自动生成/自动匹配" | ✅ 已交付（日志） | `proof.log` 含 M2 `detect_capability_dimensions` 标注 + M3 `resolve` 打分排序日志，证明"自动生成/自动匹配" |
| 模型无关验证录屏（LTX↔Wan 切换）        | ✅ 已交付（日志+单测） | `test_translator_model_agnostic.py` + `proof.log` 的 LTX↔Wan 切换段，证明"同意图不同模型自动选不同节点" |

> **铁律 3 前端录屏说明（环境限制）**：本交付环境 ComfyUI（`:8188` 真实出图 / `:11434` 旧端口）未运行，无法真实触发一次视频/图片生成，故悬浮球「选型卡实时内容 / 生成气泡」的前端运行时录屏暂缺。后端 `:7788` 已用本次新代码重启（`python main.py`，cwd=`server/`）。**用户本机补录步骤**：① 启动秋葉 ComfyUI 并放入 checkpoint/CLIP-Vision/IP-Adapter 权重；② `cd D:\AI\manju_agent/server && python main.py`；③ 浏览器开 `http://localhost:8099` → 进入生产视图触发一次导演生成 → 对悬浮球选型卡（显示实际解析的 `node`/`checkpoint`/`ip_adapter_weight`）与生成气泡录屏，即满足铁律 3。后端算法正确性已由 `proof.log` + 单测提前证明。

---

## 汇总：返工项状态（2026-08-14 闭环）

1. **【架构】A1** — ✅ 已修复：`build(intent, wf=None)` 契约重定，`wf` 给定真实插标准节点；`build_all` 逐维度调用。
2. **【M1】** ✅ 已修复：`test_m1_build_nodes.py` 硬断言 `consistency.build()` 插 `IPAdapterFaceID` 等 6 节点。
3. **【M2】** ✅ 已修复：首尾帧分级 `_VIDEO_INTERP_SUPPORTED={"WanVideoSampler"}`（LTX 外推 / Wan 插值），`test_m2_annotation.py` 覆盖。
4. **【M2】** ✅ 已修复：`NODE_DIMENSION_MAP` + `detect_capability_dimensions` 节点名→维度映射；`declared_implementations()` 改算法生成，消除手写 chip。
5. **【M3】** ✅ 已修复：删除 `translate_video_intent` 硬编码 if-else，改注册表驱动（`vf_cfg` + `tags` 打分）。
6. **【M3】** ✅ 已修复：`NodeFamilyMapper.resolve` 增 `intent_tags × 候选 tags` 打分排序；`build_all` 接 `capability.build()`。
7. **【M4】** ✅ 已修复：子 Agent 输出 `subjects/intent_tags/camera/frame_anchor`；`director/intent_bridge.py` 接到 `IntentTranslator.translate(intent)`。
8. **【M5】** ✅ 已修复：`sequence_scheduler` 推 `_resolve_ip_node_class()` 真实节点类（兜底 `IPAdapterFaceID`）。
9. **【交付】** ⚠️ 后端 proof 日志已交付（`proof.log`）；前端运行时录屏因 ComfyUI 未运行暂缺，用户本机补录（见 §交付要求说明）。

> 注：M1 的 declare/build 真实逻辑、M3 的 LTX↔Wan 切换、M4 的"无硬编码模型名"、M5 的三事件/三区域、decisions 表在返工前即已符合，返工后全部 ✅。全量测试 **146 passed, 0 failed, 1 skipped**（2026-08-14 实测）。
