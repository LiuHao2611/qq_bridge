# -*- coding: utf-8 -*-
"""能力维度抽象层 · 统一接口（手册 M1 / §2.1）。

核心抽象：视频生成 = 能力维度组合；模型只是能力的实现，可插拔、可替换。

本模块定义 6 个能力维度的统一基类 ``Capability``，以及每个维度必须实现的
两个方法：

- ``declare()``：声明本实现的能力参数（供资源注册表 / Settings 展示「当前实现」）；
- ``build(intent, wf=None)``：从结构化 ``ProductionIntent`` 构建本维度的生成条件。
  当 ``wf`` 为 ``None`` 时返回结构化 dict（兼容 P1 锁脸计划 / M4 意图聚合 /
  ``build_all``）；当传入 ``wf``（``WorkflowBuilder`` 或 ComfyUI API dict 包装）
  时**真实往 workflow 插入本维度对应的 ComfyUI 节点**并返回 wf（手册 M1 硬要求：
  每个维度必须真实插节点，禁止 return None / pass）。

铁律 1.2 的边界澄清：build() 在 wf 模式下插入的是「经能力维度语义选定的
标准节点」（IPAdapterFaceID / CLIPTextEncode / KSampler / VHS_VideoCombine /
LTXVImgToVideo 等），等价于「结构化意图 → 节点拓扑」的映射，而非散写裸节点 JSON。

红线（手册 §5）：禁止「空接口 + pass」。每个维度的 declare / build 必须有真实实现。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class Capability(ABC):
    """能力维度基类。

    子类须设置类属性 ``key``（维度标识，与资源注册表 capabilities 键严格对齐）、
    ``name``（中文名）、``implementer``（当前实现名，供 Settings 展示）、
    ``description``（维度描述），并实现 ``declare`` / ``build``。
    """

    key: str = ""
    name: str = ""
    implementer: str = ""
    description: str = ""

    @abstractmethod
    def declare(self) -> Dict[str, Any]:
        """声明本实现的能力参数（手册 M2：资源注册表维度声明结构）。"""
        raise NotImplementedError

    @abstractmethod
    def build(self, intent, wf: Optional[Any] = None) -> Any:
        """从 ProductionIntent 构建本维度的生成条件。

        - ``wf is None``：返回结构化 dict（兼容聚合 / 锁脸计划）；
        - ``wf`` 给定：往 wf 插入本维度对应节点并返回 wf（手册 M1 硬要求）。
        """
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        """导出维度元信息 + 声明，供 Settings / 资源注册表展示。"""
        return {
            "key": self.key,
            "name": self.name,
            "implementer": self.implementer,
            "description": self.description,
            "declare": self.declare(),
        }

    def __repr__(self):  # pragma: no cover - 调试用
        return "<%s key=%s implementer=%s>" % (
            self.__class__.__name__, self.key, self.implementer)
