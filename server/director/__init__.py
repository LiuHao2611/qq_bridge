# -*- coding: utf-8 -*-
"""Director Agent Core：导演调度 Agent 核心包。

包结构（对照执行手册四层架构）：
  - layer0     → ../infra/（算力池/模型引擎池/存储服务抽象）
  - layer1     → 资产库（现有 assets 表，由 VisualControl 注入）
  - layer2     → 本包：GlobalScheduler + 4 个子 Agent
  - layer3     → 前端（现有 Vue 项目中心，暂未接线）

本包为「先搭骨架再填充」阶段的 Director Agent Core 契约 + 编排实现：
  - errors.py          错误码体系 1000-5999（手册 §6）
  - agent_interface.py 子 Agent 标准接口 Init/Run/Rollback/GetStatus
  - dag.py             DAG 节点 N01-N09 + 分支规则（手册 §4）
  - logutil.py         结构化日志（LOG-01/02/03/04）
  - scheduler.py       GlobalScheduler（N01/N09 + 全链路编排/重试/降级/看门狗）
  - agents/            四个子 Agent（script_parser/visual_control/sequence_scheduler/post_production）
"""
