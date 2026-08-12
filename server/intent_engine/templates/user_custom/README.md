# 用户自定义模板（user_custom）

本目录用于**零代码扩展**意图翻译引擎的模板（手册 §2.4.3 / §9）。

- 放置与本目录上级同名的 `*.json` 模板（如 `character_genesis.json`），将**覆盖**系统模板；
- 新增全新 `task_type` 的模板也放这里，引擎 `discover_templates()` 自动发现；
- 模板格式见上级 `character_genesis.json`：`semantic_nodes`（语义别名节点）+ `placeholder_schema`（占位符声明）。

> 放入模型 → 刷资源目录 → （可选）放模板到这里。无需改任何 Python 代码。
