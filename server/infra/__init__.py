# -*- coding: utf-8 -*-
"""Layer 0 · Infrastructure Layer（执行手册 §2.1）。

本层定义计算资源池、模型引擎池、存储服务三个抽象，向下屏蔽本地/分布式的差异。
当前 personal_local_only 环境下均为「本地实现」（单进程 worker + SQLite + 本地
Ollama/ComfyUI/FFmpeg），接口与手册 Layer0 完全一致，后续可替换为真实 GPU 集群 /
Celery/Redis/对象存储而不动上层 Director Agent Core（依赖倒置）。
"""
