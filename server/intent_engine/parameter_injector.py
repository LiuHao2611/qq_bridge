# -*- coding: utf-8 -*-
"""参数注入器（手册 §2.5 / 设计思路 §4.6）。

职责：按 placeholder_schema 把意图参数填充到模板占位符。
- 注入完成后校验：所有占位符已替换，无残留 {{...}}，否则抛 InjectError。
- derive_seed：从「角色名 + 姿势」用 SHA256 截断确定性派生（禁 Python hash()/random）。
"""
import hashlib
import logging

from .errors import InjectError

LOG = logging.getLogger("intent_engine")


def derive_seed(character_name: str, pose: str) -> int:
    """确定性 seed 派生（手册 §2.5.2）。

    同一角色同一姿势 → 永远一致（角色卡可复现、不随机）。
    禁 Python 内置 hash()（跨进程随机化）/ random（不稳定）。必须用 SHA256。
    """
    raw = ("%s:%s" % (character_name, pose)).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16) % (2 ** 31)


class ParameterInjector:
    def __init__(self, schema: dict):
        self.schema = schema or {}
        self._values = {}

    @staticmethod
    def _norm(name: str) -> str:
        n = name.strip()
        if n.startswith("{{") and n.endswith("}}"):
            n = n[2:-2].strip()
        return n

    def set(self, placeholder, value):
        """校验类型与 range（越界报错），存入待注入值（已转为 schema 声明类型）。"""
        name = self._norm(placeholder)
        spec = self.schema.get(name)
        if spec is None:
            # 未在 schema 声明的占位符：允许透传（宽松），但记录警告
            LOG.warning("占位符 %s 未在 placeholder_schema 声明，按原类型透传", name)
            self._values[name] = value
            return self
        t = spec.get("type", "string")
        try:
            if t == "int":
                v = int(value)
            elif t == "float":
                v = float(value)
            else:  # string / base64
                v = str(value)
        except (ValueError, TypeError):
            raise InjectError("占位符 %s 类型不符（期望 %s，实得 %r）" % (name, t, value))
        rng = spec.get("range")
        if rng and len(rng) == 2:
            lo, hi = rng
            if v < lo or v > hi:
                raise InjectError(
                    "占位符 %s 越界（%s 不在 [%s, %s]）" % (name, v, lo, hi))
        self._values[name] = v
        return self

    def set_defaults(self):
        """对 schema 中带 default 且未显式 set 的占位符填入默认值。"""
        for name, spec in self.schema.items():
            if name not in self._values and "default" in spec:
                self.set(name, spec["default"])
        return self

    def inject(self, workflow: dict) -> dict:
        """替换所有 {{name}}（无残留则合法），返回最终 workflow。"""
        final = self._replace(workflow)
        self._assert_no_residual(final)
        return final

    def _replace(self, node):
        if isinstance(node, dict):
            return {k: self._replace(v) for k, v in node.items()}
        if isinstance(node, list):
            # [int, slot] 已解析引用保留；[str,...] 非引用则递归
            if (len(node) == 2 and isinstance(node[0], int)
                    and isinstance(node[1], int)):
                return node
            return [self._replace(x) for x in node]
        if isinstance(node, str):
            s = node
            for name, val in self._values.items():
                token = "{{%s}}" % name
                if token in s:
                    # 整串等于占位符且 schema 为数值 → 替换为数值类型（非字符串）
                    spec = self.schema.get(name, {})
                    if s == token and spec.get("type") in ("int", "float"):
                        return val
                    s = s.replace(token, str(val))
            return s
        return node

    @staticmethod
    def _assert_no_residual(node):
        if isinstance(node, dict):
            for v in node.values():
                ParameterInjector._assert_no_residual(v)
        elif isinstance(node, list):
            for v in node:
                ParameterInjector._assert_no_residual(v)
        elif isinstance(node, str) and "{{" in node:
            raise InjectError("注入后仍存在未替换占位符：%s" % node)
