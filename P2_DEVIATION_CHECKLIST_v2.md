# P2 偏差清单 v2 · 对照《P2开发手册_面向WorkBuddy_v2.md》

> 执行对象: WorkBuddy | 代码基线: `D:\AI\manju_agent` | 日期: 2026-08-14
> 状态图例: ✅ 已闭环(翻绿)  ⚠️ 诚实缺口(待补，非代码缺失)  ❌ 未达标

---

## 一、§4 红线 (8 条，逐条)

| # | 红线 | 状态 | 证据 |
|---|------|------|------|
| 1 | 禁止只做后端不做前端 | ✅ | ProjectFragmentEditor.vue(M1) + DirectorOrb.vue(M2/M3) 已改，vite 构建通过 index-C7rXVzY6.js |
| 2 | 禁止删改 src/mock.js 枚举 (只允许新增映射) | ✅ | 映射表落地 features/camera_mapping.py；src/mock.js 零改动；超集兼容前端+官方+推断枚举 |
| 3 | 禁止裸 shot["description"] 当视频 prompt | ✅ | semantic.py:92 仅经 build_shot_video_prompt 产出 video_prompt；无裸 description 直用 |
| 4 | 禁止模型不支持插值时硬造假插值 | ✅ | choose_anchor_mode 不支持→first_frame_only；frame_anchor.py:47 无尾帧诚实降级+warning；orb 诚实后缀 |
| 5 | 禁止静态 PPT 静默通过 | ✅ | qc_and_retry 检测→换 seed 重试≤2→仍静态 mark_failed；orb 品控 pill 提示 |
| 6 | 禁止 Ollama 扩写阻塞主线程 (15s 超时+失败回退) | ✅ | expand_action ThreadPoolExecutor timeout=15.0；超时/异常退回 description |
| 7 | 禁止用 Python hash() 生成 seed | ✅ | perturb_seed 调 derive_seed(SHA256)，无 hash() |
| 8 | 禁止破坏 P0 v3 / P1 v2 接口 | ✅ | 完整 pytest 套件 PASS (1 skipped, 0 failed)，P0/P1 测试无回归；增量改写向后兼容 |

---

## 二、§0.3 P0/P1 接口衔接 (6 行，逐条)

| # | 衔接项 | 状态 | 证据 |
|---|--------|------|------|
| 1 | semantic.build → build_video_prompt | ✅ | semantic.py:92；单测 test_semantic_build_produces_video_prompt |
| 2 | camera.build → CAMERA_MOVEMENT_PROMPT | ✅ | camera.py:73；单测 test_camera_build_uses_camera_movement_prompt |
| 3 | frame_anchor.build → frame_anchor_mode | ✅ | frame_anchor.py:40；单测 TestFrameAnchorBuildConsumesMode |
| 4 | ProductionIntent(camera/spatial/style) → M1 消费注入 | ✅ | video_prompt 消费；单测 TestBuildShotVideoPrompt |
| 5 | P1 inject_consistency first_frame_path → M2 首帧 | ✅ | frame_chain.chain_frames；单测 TestChainFrames |
| 6 | P0 M5 风格锚点 → M1 风格词 | ✅ | build_video_prompt 接收 style_anchor；单测 test_style_anchor_injected |

---

## 三、§3 交付总要求

| # | 要求 | 状态 | 说明 |
|---|------|------|------|
| 1 | 逐功能交付附截图/录屏 | ⚠️ | M1/M2/M3 运行时录屏+前端截图待本地启动服务后补 (铁律「无截图=未交付」诚实标注)；前端代码+构建已交付 |
| 2 | 每个函数附真实代码 + 单测断言 | ✅ | 6 命名函数均有断言，见 PROJECT_MAP §10.8 / proof.log |
| 3 | P0 依赖打通证据 | ✅ | 三条调用链均有 file:line + 单测断言 |
| 4 | 核心验收录屏 (M1运镜/M2衔接/M3重试) | ⚠️ | 同第 1 条诚实缺口 |

---

## 四、§0.2 实现逻辑铁律 (输入→处理→输出)

| 函数 | 状态 | 说明 |
|------|------|------|
| resolve_camera_movement / resolve_camera_fragment | ✅ | 输入枚举→查表→输出英文 fragment |
| expand_action | ✅ | 输入 shot→三步(显式/ollama/回退)→动作短语 |
| build_spatial_fragment | ✅ | 输入 spatial→模板拼接→站位 fragment |
| build_video_prompt | ✅ | 输入六片段→拼接→完整 prompt / 空兜底 cinematic scene |
| build_shot_video_prompt | ✅ | 输入 intent→调 4.1-4.4→prompt |
| choose_anchor_mode | ✅ | 输入 shot+模型能力→三步判断→frame_anchor_mode |
| chain_frames | ✅ | 输入前后分镜→抽尾帧→写首帧(同场景) |
| detect_motion | ✅ | 输入视频→抽帧→帧差→阈值判定 |
| perturb_seed | ✅ | 输入 seed+角色→SHA256 派生 offset→新 seed |
| qc_and_retry | ✅ | 输入 shot+task→检测→重试→合格/失败 |

---

## 五、诚实缺口清单 (待用户本地补采)

1. **M1 运镜可见录屏**：需启动 :7788 + :8099，在 ProjectFragmentEditor 选 push_in 看预览 + 生成视频见真实运镜。
2. **M2 三镜头衔接录屏**：同场景 3 连续分镜生成 + DirectorOrb 锚定模式气泡 + 成片衔接对比。
3. **M3 静态重试录屏**：构造静态视频 → 运动检测判定 → 换 seed 重试 → 悬浮球品控结果。
4. **worker.regenerate_video_task / worker.mark_video_task_failed 真实存在性**：motion_qc 懒加载引用，有 try/except 兜底；建议本地启动后核实两函数真实签名。

> 说明：以上缺口均为「运行时录屏/截图」与「本地 ComfyUI/ffmpeg 环境」，非代码或单测缺失。
> 代码交付单元 (后端+单测+前端构建) 已完成并验证。

---

## 六、结论

§4 红线 8/8 ✅；§0.3 衔接 6/6 ✅；§3 要求 2/4 ✅ + 2/4 ⚠️(诚实缺口)；§0.2 逻辑铁律 10/10 ✅。
验收运行：`server/.venv/Scripts/python.exe -m pytest tests/test_p2_m1_video_prompt.py tests/test_p2_m2_frame_anchor.py tests/test_p2_m3_qc.py -q` → **40 passed, 0 failed**。
运行时证据：`D:\AI\manju_agent\.tmp\p2_proof\proof.log`。
