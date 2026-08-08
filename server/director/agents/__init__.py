# -*- coding: utf-8 -*-
"""导演调度子 Agent 注册表。"""
from .script_parser import ScriptParserAgent
from .visual_control import VisualControlAgent
from .sequence_scheduler import SequenceSchedulerAgent
from .post_production import PostProductionAgent

AGENT_REGISTRY = {
    "script_parser": ScriptParserAgent,
    "visual_control": VisualControlAgent,
    "sequence_scheduler": SequenceSchedulerAgent,
    "post_production": PostProductionAgent,
}

__all__ = [
    "ScriptParserAgent",
    "VisualControlAgent",
    "SequenceSchedulerAgent",
    "PostProductionAgent",
    "AGENT_REGISTRY",
]
