# -*- coding: utf-8 -*-
"""导演调度 DAG 节点定义（执行手册 §4 · Core Execution Flow & DAG Orchestration）。

全链路 DAG 节点 N01→N09，每个节点映射到 Director Agent Core 的某个子 Agent。
分支规则（§4.2）在此集中声明，由 GlobalScheduler 在编排时执行。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    node_id: str
    name: str
    agent: str                                   # 负责该节点的子 Agent 标识
    predecessors: List[str] = field(default_factory=list)
    description: str = ""


# 全链路 DAG（§4.1 表格）
DAG_NODES: List[DAGNode] = [
    DAGNode("N01", "Task Receive", "scheduler",
            [], "接收前端生成触发指令，初始化任务上下文"),
    DAGNode("N02", "Script Parse", "script_parser",
            ["N01"], "ScriptParserAgent 执行脚本结构化拆解，输出 shot_list"),
    DAGNode("N03", "Visual Enhancement", "visual_control",
            ["N02"], "VisualControlAgent 执行全局视觉参数/资产特征注入"),
    DAGNode("N04", "Task Dispatch", "sequence_scheduler",
            ["N03"], "SequenceSchedulerAgent 构建 DAG 并分发并行/串行任务"),
    DAGNode("N05", "Shot Generate", "sequence_scheduler",
            ["N04"], "分镜级并行/串行生成视频片段"),
    DAGNode("N06", "Quality Check", "post_production",
            ["N05"], "单分镜画质与一致性校验"),
    DAGNode("N07", "Audio Synthesis", "post_production",
            ["N06"], "同步生成音频并对齐时间轴"),
    DAGNode("N08", "Video Composition", "post_production",
            ["N07"], "多片段拼接、转场、全局后处理"),
    DAGNode("N09", "Final Output", "scheduler",
            ["N08"], "成片编码、存储、返回结果"),
]

NODE_BY_ID = {n.node_id: n for n in DAG_NODES}

# 分支规则（§4.2）
BRANCH_RULES = [
    "N06 校验不通过：触发单分镜重试，重试 2 次仍失败则标记为失败，进入 N08 时跳过并告警",
    "N05 部分分镜无依赖：提前进入 N06，不等待全部分镜生成完成",
    "用户主动终止：触发全链路回滚，清理临时资源，更新任务状态",
]


def topological_order() -> List[str]:
    """返回 N01→N09 的拓扑顺序（手册 DAG 为线性链，直接按定义顺序）。"""
    return [n.node_id for n in DAG_NODES]


# 与项目五阶段的映射（供前端/文档参考）
PHASE_MAP = {
    "P1": "资产初始化（冷启动） → 资产库（Layer1 Asset Management）",
    "P2": "项目策划与分场拆解 → N02 ScriptParserAgent",
    "P3": "多模态分镜编排与资产绑定 → N03 VisualControlAgent + N04 入参",
    "P4": "任务分发与异步 AI 推理 → N04 SequenceSchedulerAgent + N05 Shot Generate",
    "P5": "多片段音视频合成 → N06/N07/N08 PostProductionAgent + N09 Final Output",
}
