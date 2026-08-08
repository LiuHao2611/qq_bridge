# DONE-01 · 七项自证（§10 交付前自检）

> 统一纪律基线：`AGENTS.md`（KB §08 Agent 开发纪律）。本文件是「交付前七项自证」的**原子准则**，各阶段报告/重做报告引用本文件而非重复罗列。
> 关联索引：见根目录 `PROJECT_MAP.md` → 文档索引 · 纪律/自证。

## 一、七项自证清单（每次交付 MUST 逐条回答）
1. **需求覆盖**：是否对照 KB 对应章节核验/增强？有无偏离？
2. **环境健康**：后端 7788 `/api/v1/health`=200；前端 8099=200；8099 反代 `/api/v1/*`=200。
3. **迁移落地**：新增表/列经 SQLite `PRAGMA table_info` 核验存在（`db._migrate` 幂等 `IF NOT EXISTS` + `ALTER TABLE ADD COLUMN`）。
4. **接口验证**：关键链路端点 200；写操作入库字段 DB 核验。
5. **能力验证**：核心能力有单元/集成测试证据（如真实 ffmpeg 拼接、WS 网关单测）。
6. **回归测试**：`tests/ui_flow.mjs` / `tests/pipeline_e2e.mjs` 全绿；构建成功（`frontend/dist` 含新 UI）。
7. **纪律自检**：CLOSE / REDUN / LOG / OPS 均有对应代码落点；无静默吞错（CLOSE-02 日志中间件 + ApiError/Exception handler 写 operation_logs）。

## 二、纪律优先级（MUST > SHOULD；资金类最高）
- **CLOSE-04（失败退款）优先级最高**：涉及积分/冻结金的操作失败必须退款，绝不误退/漏退。
- 状态机收敛（CLOSE-01）、幂等（CLOSE-02/03）、显式超时（CLOSE-05）、降级链（REDUN-01）、入参校验（REDUN-03）、结构化日志（LOG-01/03）、临时资源释放（OPS-01）、看门狗（OPS-04）。

## 三、具体实例（引用，不重复内容）
- 五阶段对齐：`docs/五阶段对齐报告.md` §四（已拆分为 `docs/phases/P1~P5.md`）。
- 第四阶段重做：`docs/第四阶段重做报告.md` §四（P4 专项七项）。
- §09 补全功能：`§09_DONE-01_七项自证.md`（独立批次交付自证）。

## 四、提交闸门
- 任一阶段/功能改动后，必须复跑对应测试并更新 `PROJECT_MAP.md`（新增/改动文件即更新索引）。
