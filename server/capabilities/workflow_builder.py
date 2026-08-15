# -*- coding: utf-8 -*-
"""轻量 ComfyUI API 工作流构建器（手册 M1 §2.1 / 铁律 1.2）。

能力维度的 build(intent, wf) 在给定 wf 时**真实往 workflow 插入节点**，
而非 return None / pass。本类提供与 ComfyUI API 格式一致的最小构建器：

    wf = WorkflowBuilder()
    nid = wf.add_node("IPAdapterFaceID", {"weight": 0.85, ...})
    wf.nodes  # {"1": {"class_type": "IPAdapterFaceID", "inputs": {...}}}

它刻意不依赖真实 ComfyUI 实例——节点插入是「结构化意图 → 节点拓扑」的纯函数式
构造，最终交给 ComfyUI 执行。因此 build() 可被单测直接断言「产出的 workflow 含 X 节点」
（手册 M1 验收硬要求）。
"""
from typing import Any, Dict, Optional


class WorkflowBuilder:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    def add_node(self, class_type: str, inputs: Optional[Dict[str, Any]] = None,
                 title: Optional[str] = None) -> str:
        """插入一个节点，返回节点 id（字符串）。"""
        self._counter += 1
        nid = str(self._counter)
        node = {"class_type": class_type, "inputs": dict(inputs or {})}
        if title:
            node["_meta"] = {"title": title}
        self.nodes[nid] = node
        return nid

    def find_node(self, class_type: str):
        """返回 (node_id, node) 或 (None, None)。"""
        for nid, n in self.nodes.items():
            if n.get("class_type") == class_type:
                return nid, n
        return None, None

    def has_node(self, class_type: str) -> bool:
        _, n = self.find_node(class_type)
        return n is not None

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        return self.nodes
