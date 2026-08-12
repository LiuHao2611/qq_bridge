# -*- coding: utf-8 -*-
"""意图翻译引擎统一异常（手册 §2.2.2 / §2.3.2 / §2.4 / §2.5 / §2.6）。

设计原则（CLOSE-03）：所有探测失败 / 匹配失败 / 注入失败必须显式抛错 + 记日志，
绝不静默返回空或兜底错误结果。
"""
import logging

LOG = logging.getLogger("intent_engine")

# 能力缺失（模型/节点/硬件不满足），调用方据此诚实阻断或走降级。
class FeatureUnavailable(Exception):
    """请求的能力在本地 ComfyUI 当前不可用（缺模型/缺节点/超显存等）。"""

    def __init__(self, capability, reason="", installed=None):
        self.capability = capability
        self.reason = reason
        self.installed = installed or []
        msg = "能力不可用[%s]" % capability
        if reason:
            msg += "：%s" % reason
        if self.installed:
            msg += "；已安装：%s" % (", ".join(self.installed) if isinstance(self.installed, (list, tuple)) else self.installed)
        super().__init__(msg)


# 模板 / 占位符 / 注入相关
class TemplateError(Exception):
    """语义模板加载或解析失败（残留语义名、缺字段等）。"""


class InjectError(Exception):
    """参数注入失败（占位符缺失 / 类型越界 / 残留 {{}}）。"""


# 翻译主流程
class TranslateError(Exception):
    """意图翻译失败（选模板/选模型/选节点族任一环节失败）。"""
