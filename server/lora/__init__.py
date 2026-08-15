# -*- coding: utf-8 -*-
"""LoRA 训练模块（手册 §1.3 / 设计思路 §0）。

子模块：
- preset：训练参数预设（6gb_fast / 6gb_quality / standard）。
- engine：kohya_ss 引擎探测 + TOML 配置生成 + 训练子进程 + progress 解析 + 取消。
- captioner：WD14 自动打标（角色 LoRA 强制 trigger_word 前缀）。
- dataset：数据集管理（从资产库导入 / 上传 / 校验）。
- register：训练产物注册 resource_catalog（闭环关键）。
- task_runner：训练任务执行器（worker 消费入口，CLOSE 纪律）。
"""
from . import preset
from . import engine
from . import captioner
from . import dataset
from . import register
from . import task_runner

__all__ = ["preset", "engine", "captioner", "dataset", "register", "task_runner"]
