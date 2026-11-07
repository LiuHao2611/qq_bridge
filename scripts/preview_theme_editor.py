# -*- coding: utf-8 -*-
"""scripts/preview_theme_editor.py —— 本地起一个 FastAPI 跑主题编辑器，便于 IDE 内嵌浏览器预览。

运行：
    cd D:\\AI\\manju_agent\\qq_bridge
    .venv\\Scripts\\python.exe scripts\\preview_theme_editor.py

浏览器打开 http://127.0.0.1:8765/pic/theme-editor 即可。

**仅用于本地预览**，不依赖 QQ bridge / NapCat / NoneBot，临时 FastAPI 直接挂编辑器路由。
停止：在终端 Ctrl+C。
"""
import os
import sys

# 把 src 加进 path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import uvicorn
from fastapi import FastAPI

from src.plugins.qq_bridge.pic.editor import register as register_theme_editor

app = FastAPI(title="QQ 自定义主题编辑器·预览")
register_theme_editor(app)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")