# -*- coding: utf-8 -*-
"""pytest 公共配置：将 server/ 加入 sys.path，使 `import director...` 可用。

所有 Director 单测均通过此路径隔离运行，不依赖业务服务进程（:7788）或外部模型。
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SERVER = os.path.join(_REPO, "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)
