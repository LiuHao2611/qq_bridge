# M1–M9 开发计划（含逐项 GitHub 引用）

> 版本：v2.1 配套开发计划 ｜ 日期：2026-10-20 ｜ 配套路线图：`ai_butler_roadmap.md`
> 链接核实：全部 GitHub 地址于 2026-10-20 经 Web 检索核实，使用 canonical `github.com` 地址。
> 状态口径：M1/M2/M6/M7/M9 已交付（M6：88/88 PASS，commit `ce8503b`+`8f46bcf`；M2：73 断言，commit `6029324`；M7：128 断言组合，commit `a5d1248`；M9：173 断言组合，commit `ef1a74d`；M1：206 断言组合，2026-10-20）；M3/M4/M5/M8 未开工（M8 因本机无 HA 实例按 §10.4 跳过，待用户具备 HA）。

---

## 〇、全局约束（落地任何一项都必须遵守）

1. **红线 — 禁止降级 / 禁止兜底**（用户最高声明）：选中方法失败如实报错（`raise`/显式错误上抛），绝不回退到次品路径。截图已落实：默认 `dxgi`，失败/全黑/空数据直接报错，无 `mss` 兜底。
2. **安全总则（所有域共用）**：
   - 高危操作（关机/重启/kill/删文件/执行 shell/禁言）**仅主人**且需回复「确认」二次确认；
   - 每工具独立开关 + 每群每日调用预算，超限降级（指限流，非功能降级）；
   - 审计日志（谁/何时/何工具/参数摘要），LOG-04 脱敏；
   - 失败显式上抛，禁止静默（IMA 铁律）。
3. **依赖拓扑**：M1–M5 相互依赖；M6–M9 依赖 M2 工具总线，但 M6 已用「命令 + 自然语言直通车」独立交付。建议先落地 **M2 工具总线** 再推 M7–M9。
4. **范围边界**：代码改动限定 `D:/AI/manju_agent/qq_bridge`；文档编辑在 WorkBuddy 工作树；提交前 `version_guard` 备份。

---

## 一、人格面（M1–M5，v1.0 已覆盖「此略」，以下为引用来源）

| 里程碑 | 内容 | 预估 | 参考项目（角色 / 搬什么） | GitHub（已核实） | 状态 |
|---|---|---|---|---|---|
| **M1 记忆系统** | 明确记忆三态 + 画像 + 群档案 | 2–3 天 | **MaiBot** — A-Memorix 记忆引擎（向量检索 / 图谱关系 / 人物画像 / 记忆演化）<br>**ai-groupmate** — 明确记忆 ORM（按会话/用户/Bot/群隔离）<br>**personification** — 用户画像 / 关系图 / 结构化画像更正 | https://github.com/Mai-with-u/MaiBot<br>https://github.com/yaowan233/nonebot-plugin-ai-groupmate<br>https://github.com/luojisama/nonebot-plugin-personification | ✅ 已交付（2026-10-20）：memory 三件套 + /记忆 命令族 + 对话 recall/ingest；33 断言 + 组合 206/206 全绿；向量检索二期（详见 `ai_butler_roadmap.md` §10.9） |
| **M2 Function Calling 总线** | 工具注册表 + 定时任务/提醒 | 2–3 天 | **ai-groupmate** — langchain agent 决策 + 自定义 Agent Tools + APScheduler 定时任务（自然语言创建/改/取消，10s–7天） | https://github.com/yaowan233/nonebot-plugin-ai-groupmate | ✅ 已交付（2026-10-20）：tools 五件套（registry/llm/fc/scheduler/builtins）+ 首批 5 工具 + `/提醒` 命令族，73 断言全绿；契约逐条对齐 ai-groupmate（详见 `ai_butler_roadmap.md` §10.6） |
| **M3 情感 + 日程 + 关系** | 二维情感 / 日程 / 社交关系 | 2 天 | **personification** — 好感度 / inner state / 关系图 / 动态态度<br>**MaiBot** — 二维情感表达系统 | https://github.com/luojisama/nonebot-plugin-personification<br>https://github.com/Mai-with-u/MaiBot | ⬜ 未开工 |
| **M4 多模态 + 语音** | 图理解 / 历史图召回 + 主人私聊语音 | 2 天 | **personification** — 图片输入 / GIF 采样理解 / 视觉 fallback / 可选视频理解 / 语音 | https://github.com/luojisama/nonebot-plugin-personification | ⬜ 未开工 |
| **M5 心流 + 群风格 + 预算** | HeartFlow 升级 / 群风格学习 / 预算 | 2 天 | **MaiBot** — HeartFlow 心流（持续认知循环 / 发言时机）<br>**personification** — 群风格学习 / 预算 shadow·adaptive / 拟人发送 | https://github.com/Mai-with-u/MaiBot<br>https://github.com/luojisama/nonebot-plugin-personification | ⬜ 未开工 |

> 人格面三项参考均为 **NoneBot2 生态 / 群聊陪伴 Agent** 先例，与本项目 `qq_bridge` 同栈（NoneBot2 + NapCat + OneBot v11），可直读源码对齐契约。

---

## 二、宿主机管家（M6 — 已交付）

| 里程碑 | 内容 | 参考项目（角色 / 搬什么） | GitHub（已核实） | 状态 |
|---|---|---|---|---|
| **M6 宿主机管家** | 监控/进程/窗口/截图(DXGI)/文件/剪贴板/应用/电源/键鼠·识屏/文件互传 | **Desktop Commander MCP**（19miha99）— 23 工具分类学骨架（系统监控10/窗口5/桌面控制5/应用3）<br>**computer-control-mcp**（AB498）— PyAutoGUI 键鼠 + RapidOCR 屏内容识别<br>**dsh-qq-remote**（ASAKAFENG）— QQ→宿主机 链路先例（/exec /screenshot / 阶段汇报）<br>**NapCatQQ Desktop**（NapNeko）— 官方桌面端能力清单（内嵌终端/运行监控/掉线通知） | https://github.com/19miha99/system-monitor-mcp<br>https://github.com/AB498/computer-control-mcp<br>https://github.com/ASAKAFENG/dsh-qq-remote<br>https://github.com/NapNeko/NapCatQQ-Desktop | ✅ **100% 交付**（四批 88/88 PASS） |

> 备注：路线图将 WGC 归在 `computer-control-mcp` 名下；实际 WGC/Desktop Duplication 的 Windows 捕获最佳参考是 `wonderwhy-er/DesktopCommanderMCP`（9.3k★，同样 23 工具分类学）与 `windows-capture`（Rust+Python，本项目 M6 实际采用其 **DXGI Desktop Duplication** 路径，非 WGC）。补充参考：
> - https://github.com/wonderwhy-er/DesktopCommanderMCP （流行版 Desktop Commander，概念同源）
> - DXGI 实现依赖 `windows-capture` PyPI（Rust+Python, MIT），本项目已装入 venv。

---

## 三、管家面其余（M7–M9，已详设未开工）

| 里程碑 | 内容 | 预估 | 参考项目（角色 / 搬什么） | GitHub（已核实） | 状态 |
|---|---|---|---|---|---|
| **M7 下载媒体中心** | aria2 RPC 桥 / BT 智能选文件 / yt-dlp 解析 / 订阅监控 / 文件中转站 | 2 天 | **DownloadBot**（iaman1998）— aria2/qBittorrent JSON-RPC 远程控制范式（聊天发链接→下载、BT 只选最大文件、完成回传 QQ、文件树转图）<br>**StreamVault**（Lugic1993）— yt-dlp 多平台解析 + **下载完成 Webhook 通知**（可直推 QQ 群） | https://github.com/iaman1998/DownloadBot<br>https://github.com/Lugic1993/StreamVault | ✅ 已交付（2026-10-20）：download 五件套 + /dl 命令族 + FC 工具 3 个，55 断言 + 与 M2/config 组合 128/128 全绿，真实 aria2 链路抽样 OK（详见 `ai_butler_roadmap.md` §10.7） |
| **M8 智能家居中枢** | HA 桥接 / 设备白名单 + 场景脚本 / Webhook 反向告警 / 摄像头 stream→视频 / ASR 语音指令 | 2 天 | **HAQBot**（RicterZ）— 与需求**完全同构**的先例：QQ 自然语言控 HA（LLM 意图→HA 服务）、Webhook 反向推送（传感器/安防→QQ 群）、摄像头 stream→视频、腾讯云 ASR、设备白名单、猫娘管家 system prompt（指令明确无条件执行/未响应如实告知/禁回原始 JSON） | https://github.com/RicterZ/HAQBot | ⬜ 未开工（已详设 §M8） |
| **M9 运维面 + 通知中枢** | 开发机状态(git/CI) / 远程主机 SSH 白名单 / WoL / 内网穿透状态 / 通知中枢 | 1–2 天 | **dsh-qq-remote**（ASAKAFENG）— `/exec` SSH 范式（复用；其 /exec 自认可执行任意命令，本项目安全模型更严：主机+命令双白名单+审计+仅主人+二次确认）<br>**frp**（fatedier）— 内网穿透隧道在线状态查询参考<br>通知中枢为**内部设计**（人格化转述推送，无专属外部仓库） | https://github.com/ASAKAFENG/dsh-qq-remote<br>https://github.com/fatedier/frp | ✅ 已交付（2026-10-20）：ops 六件套 + /ops 命令族 + 通知中枢，45 断言 + 全相关组合 173/173 全绿（详见 `ai_butler_roadmap.md` §10.8） |

---

## 四、建议落地顺序（依赖驱动）

1. **M2 工具总线**（优先）：M6–M9 的总开关；复用 `ai-groupmate` 的 Tool Registry + APScheduler 范式。
2. **M7 下载媒体中心**：依赖 M2；aria2/yt-dlp 均为本地进程，链路最短、风险最低。
3. **M9 运维面 + 通知中枢**：依赖 M2 + 复用 dsh `/exec`；通知中枢可反向服务 M6–M8 的所有事件。
4. **M8 智能家居中枢**：依赖 M2 + HA 环境（需用户具备 Home Assistant 实例）。
5. **M1 / M3 / M4 / M5 人格面**：内部相互依赖，建议 M2 之后统一排期；M1 记忆先行（M3/M5 依赖画像与关系数据）。

> M6 已完成，可作为「命令 + 自然语言直通车」样板供 M7–M9 复用（权限注入经参数、纯模块可单测、`pc.py` 不 import nonebot）。

---

## 五、验收与纪律

- 每里程碑交付标准沿用 M6 纪律：**单测全绿 + 真实链路抽样 OK + 提交前 `version_guard` 备份 + 禁止静默失败**。
- 每个子项落地时，先在对应 GitHub 仓库 README/源码确认契约（trust but verify），再写代码，禁止凭记忆/假设。
- 高危功能（M6 电源、M7 文件删除、M8 高敏设备、M9 远程 shell）严格走「仅主人 + 二次确认 + 审计」。
- 文档同步：新里程碑完成后在 `AGENTS.md` 追加 F 条目、`PROJECT_MAP.md` 扩行、`ai_butler_roadmap.md` 状态行改「已完成」，并分别提交到各自分支（源码→master，文档→工作树）。

---

## 六、引用清单（去重，便于克隆/对照）

| # | 项目 | 角色 | URL |
|---|---|---|---|
| 1 | MaiBot (Mai-with-u) | 记忆/情感/心流先例 | https://github.com/Mai-with-u/MaiBot |
| 2 | nonebot-plugin-ai-groupmate (yaowan233) | Tool Calling/明确记忆/定时任务 | https://github.com/yaowan233/nonebot-plugin-ai-groupmate |
| 3 | nonebot-plugin-personification (luojisama) | 画像/好感度/群风格/多模态/语音 | https://github.com/luojisama/nonebot-plugin-personification |
| 4 | system-monitor-mcp (19miha99) | 23 工具分类学骨架 | https://github.com/19miha99/system-monitor-mcp |
| 5 | computer-control-mcp (AB498) | 键鼠 + RapidOCR 屏识别 | https://github.com/AB498/computer-control-mcp |
| 6 | DesktopCommanderMCP (wonderwhy-er) | 流行版 Desktop Commander（概念同源，WGC 参考） | https://github.com/wonderwhy-er/DesktopCommanderMCP |
| 7 | dsh-qq-remote (ASAKAFENG) | QQ→宿主机 链路 / /exec SSH 范式 | https://github.com/ASAKAFENG/dsh-qq-remote |
| 8 | NapCatQQ-Desktop (NapNeko) | 官方桌面端能力清单 | https://github.com/NapNeko/NapCatQQ-Desktop |
| 9 | DownloadBot (iaman1998) | aria2/qBittorrent JSON-RPC 范式 | https://github.com/iaman1998/DownloadBot |
| 10 | StreamVault (Lugic1993) | yt-dlp + 下载完成 Webhook | https://github.com/Lugic1993/StreamVault |
| 11 | HAQBot (RicterZ) | QQ 控 HA 同构先例 | https://github.com/RicterZ/HAQBot |
| 12 | frp (fatedier) | 内网穿透状态查询参考 | https://github.com/fatedier/frp |
