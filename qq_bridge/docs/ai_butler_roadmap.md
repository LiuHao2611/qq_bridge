# AI 模式「贾维斯管家」升级路线图 v2.0

> 版本：v2.1（疏漏审查补全版）｜ 日期：2026-10-20 ｜ 状态：M6 第一批 + 第二批 + 文件互传 + **WGC 黑屏修复（DXGI Desktop Duplication）** 已完成（监控/进程/端口/温度/截图/kill确认流/窗口/剪贴板/应用/电源5s可取消/键鼠·识屏/文件双向传输 + 安全层 + GPU全屏非黑屏截图）
> v2.0 经疏漏审查，补入「§十、疏漏补全记录」；M6 第一批（单测 31/31）、第二批（单测 22/22）、文件互传（单测 24/24）、WGC黑屏修复（单测 11/11）均按纪律交付，真实链路抽样全 OK，四批合计 **88/88 PASS**。

---

## 〇、一句话定义

> 在 QQ 里住着一个「本喵管家」：**记得你、懂群里的梗、有作息有情绪**（人格面），
> 同时**看得见屏幕、管得了进程文件、下得了片、控得了全屋家电**（管家面）。
> 所有能力经自然语言触发，高危操作主人授权 + 二次确认，零命令记忆负担。

---

## 一、调研结论（2026-10 权威来源，全部仍活跃）

### 1. 群友人格面（v1.0 已覆盖，此略）
MaiBot 0.7（HeartFlow 心流 / A-Memorix 记忆 / 二维情感 / 日程 / 社交学习）、
ai-groupmate v2.5.15（Tool Calling / 明确记忆 / 定时任务）、personification（好感度/画像）。

### 2. 宿主机管家面（v2.0 新增）

| 来源 | 搬什么 |
|---|---|
| **Desktop Commander MCP**（19miha99，2026-05） | **23 工具分类学（本域设计的骨架）**：系统监控 10（概览/进程列表/进程详情/TopN/网络/分核CPU/kill/电池/端口占用/温度）、窗口管理 5（列举/聚焦/排列/状态/关闭）、桌面控制 5（截图/剪贴板读写/桌面通知/显示器信息）、应用管理 3（启动/搜索已装/开机启动项） |
| **computer-control-mcp**（ab498） | PyAutoGUI 键鼠控制 + RapidOCR 屏内容识别 + **WGC（Windows Graphics Capture）解决 GPU 窗口截图黑屏**——游戏/视频/Electron 应用截屏的关键 Windows 细节 |
| **dsh-qq-remote**（ASAKAFENG） | 已投产的「QQ→宿主机」先例：QQ→NapCat→插件→`/exec` shell / `/screenshot` / Agent 会话 / AI 聊天，阶段汇报回传 QQ。验证了整条链路可行 |
| **NapCatQQ Desktop**（NapNeko 官方） | 官方桌面端的能力清单参考：内嵌终端（本机/远端/Bot 实例）、配置备份 ZIP、运行监控（CPU/内存/磁盘 + Bot 指标 + 自动重启 + 掉线通知） |

### 3. 下载媒体面（v2.0 新增）

| 来源 | 搬什么 |
|---|---|
| **DownloadBot**（iaman1998） | aria2/qBittorrent JSON-RPC 远程控制范式：聊天发链接→宿主机下载、BT 智能选文件（只下最大的/按体积筛）、下载完成回传文件到 QQ、文件树输出（复杂结构转图片）、断点续传 |
| **StreamVault**（Lugic1993） | yt-dlp 多平台视频下载 + **下载完成 Webhook 通知**（可直推 QQ 群）——完成事件驱动的范式 |

### 4. 智能家居面（v2.0 新增）

| 来源 | 搬什么 |
|---|---|
| **HAQBot**（RicterZ） | **与本需求完全同构的先例**：QQ 自然语言控制 Home Assistant（LLM 意图→HA 服务调用）、**Webhook 反向推送**（HA 传感器/安防告警→QQ 群）、摄像头 stream→视频消息、腾讯云 ASR 语音转文字、设备白名单权限、猫娘管家 system prompt（「指令明确无条件执行/设备未响应如实告知/禁止返回原始 JSON」） |

---

## 二、总体架构：一个大脑 + 一双手

```
                        ┌─────────────────────────────┐
                        │   ai_mode（人格大脑·现有）    │
                        │  记忆/情感/日程/心流/表情包   │
                        └──────────────┬──────────────┘
                                       │ 统一 LLM 编排
                        ┌──────────────▼──────────────┐
                        │      ai_tools（工具总线）     │
                        │  注册表 + Function Calling   │
                        │  + 高危操作二次确认队列       │
                        └──┬───────┬───────┬───────┬───┘
              ┌───────────┘       │       │       └───────────┐
              ▼                   ▼       ▼                   ▼
    ┌──────────────────┐ ┌────────────┐ ┌──────────┐ ┌────────────────┐
    │ 群友人格工具集     │ │ 宿主机工具 │ │ 家居工具  │ │ 下载媒体工具    │
    │ 表情包/生图/游戏  │ │ 监控/进程  │ │ HA 桥接   │ │ aria2/yt-dlp   │
    │ 提醒/reaction    │ │ 窗口/截图  │ │ webhook   │ │ 文件回传       │
    │ 天气/搜索/禁言    │ │ 文件/剪贴 │ │ 摄像头    │ │ 订阅监控       │
    └──────────────────┘ └────────────┘ └──────────┘ └────────────────┘
```

**安全总则**（所有域共用）：
1. 高危操作（关机/重启/kill 进程/删文件/执行 shell/禁言）**仅主人**，且需回复「确认」二次确认；
2. 每工具独立开关 + 每群每日调用预算，超限降级；
3. 所有操作写审计日志（谁/何时/何工具/参数摘要），LOG-04 脱敏；
4. 失败显式上抛，禁止静默（IMA 铁律）。

---

## 三、里程碑总览

| 里程碑 | 域 | 内容 | 预估 |
|---|---|---|---|
| M1 | 人格 | 记忆系统（明确记忆三态+画像+群档案） | 2-3 天 |
| M2 | 人格 | Function Calling 总线 + 定时任务/提醒 | 2-3 天 | ✅ **已交付 2026-10-20**（见 §10.6） |
| M3 | 人格 | 情感 + 日程 + 关系 | 2 天 |
| M4 | 人格 | 多模态（图理解/历史图召回）+ 语音（主人私聊） | 2 天 |
| M5 | 人格 | 心流升级 + 群风格学习 + 预算 | 2 天 |
| **M6** | **管家** | **宿主机管家：监控/进程/窗口/截图/文件/剪贴板/应用/电源** | 3 天 |
| **M7** | **管家** | **下载媒体中心：aria2 远程下载/BT 智能选/yt-dlp/完成推送/文件回传** | 2 天 |
| **M8** | **管家** | **智能家居：HA 桥接/webhook 告警/摄像头/ASR 语音指令** | 2 天 |
| **M9** | **管家** | **运维面：git/CI/服务器状态/WoL/内网穿透状态 + 桌面通知中枢** | 1-2 天 |

M1-M5 相互有依赖；M6 起依赖 M2 的工具总线，但 M6 也可先以「命令 + 自然语言直通车」形式独立交付（不阻塞）。

---

## 四、功能域详设

### M6 宿主机管家（v2.0 核心新增）

**设计来源**：Desktop Commander 23 工具分类学 + WGC 截图 + dsh-qq-remote 链路先例。
**交互**：自然语言为主（「看看电脑咋了」「把 Chrome 关了」「截个图看看」），
保留 `/pc` 命令族作为直通通道（不依赖 LLM，断网也能用）。

#### 6.1 系统监控（10 项）
| 能力 | 触发示例 | 实现 |
|---|---|---|
| 系统概览（CPU/内存/磁盘/swap/uptime） | 「电脑咋样」 | psutil，复用现有 /hw 卡片模板 |
| 进程列表（按 CPU/内存排序） | 「谁在吃 CPU」 | psutil，渲染文本卡 |
| Top N 资源消耗 | 「最占内存的 5 个」 | psutil |
| 进程详情（连接/线程/IO） | 「chrome 开了多少线程」 | psutil |
| kill 进程 | 「杀掉 xxx」 | **高危**：主人+确认 |
| 端口占用查询 | 「8080 谁占着」 | psutil net_connections |
| 分核 CPU | 「每个核心忙不忙」 | psutil cpu_percent(percpu=True) |
| 硬件温度 | 「CPU 多少度」 | psutil sensors（Win 需 LibreHardwareMonitor 回退，不支持则明说） |
| 电池状态 | 「电量多少」 | psutil sensors_battery |
| 网络流量 | 「网速多少」 | psutil net_io_counters |

#### 6.2 窗口管理（5 项）
列举窗口（标题/PID/位置/状态）｜聚焦窗口（「把 VSCode 拉前面」）｜
排列窗口（「左右分屏」= 精确坐标）｜最小化/最大化/还原｜关闭窗口。
实现：pygetwindow（Win）。

#### 6.3 桌面控制（5 项）
| 能力 | 说明 |
|---|---|
| 截屏 | **DXGI Desktop Duplication**（GPU 全屏内容真修复：捕获桌面合成器输出，含游戏/视频/Electron 全屏；WGC 在本机 Win10 21H2 边框 API 不支持故不可用）；**不做任何降级/回退**：选中方法失败如实报错（默认即 DXGI，无 mss 兜底）；支持「截当前窗口/全屏/区域」；截图送 vision 模型可「描述屏幕内容」 |
| 剪贴板读 | 「剪贴板里啥」→ 回传（脱敏：超 500 字截断） |
| 剪贴板写 | 「把 xxx 复制到剪贴板」→ 主人确认（防恶意注入） |
| 桌面通知 | 宿主机 toast 通知（「提醒我喝水」的本地通道） |
| 显示器信息 | 分辨率/DPI/多屏布局 |

#### 6.4 文件管理（8 项）
目录列举（文件树，复杂结构转图片，DownloadBot 范式）｜全局搜文件｜
发文件给机器人→存入宿主机指定目录（QQ 传输上行）｜
机器人发文件→QQ 回传（下载/文档）｜移动/复制/重命名｜
删除（高危：主人+确认+软删除到回收站）｜磁盘占用分析（「啥占了 C 盘」树形图）。

#### 6.5 应用管理（3 项）
启动应用/打开文件/打开 URL（「开个 B 站」）｜搜索已安装软件（注册表）｜
开机启动项列举（「开机自动运行了啥」）。

#### 6.6 键鼠自动化（高级，默认关）
PyAutoGUI：输入文本/快捷键/点击/滚动；屏内容 OCR（RapidOCR）→
「屏幕上现在显示啥」；危险操作（输入密码类）由 safety 层拦截。

#### 6.7 电源控制（4 项，全部高危）
锁屏｜休眠｜重启｜关机——**主人 + 二次确认 + 5 秒可取消倒计时**。

### M7 下载媒体中心（v2.0 新增）
- **aria2 RPC 桥**：聊天发 HTTP/BT/磁力链接 → 加入宿主机 aria2 队列；
  「下载列表」/「暂停 N」/「删 N」；下载完成自动回传文件（<100MB 直发，超大发链接）
- **BT 智能选文件**：磁力/种子只选最大文件 / 按大小筛（DownloadBot 范式）
- **yt-dlp 视频解析**：B站/抖音/YouTube 链接 → 解析下载 → 回传（复用现有
  视频解析链路做元数据，下载走后端）
- **订阅监控**：B站 UP 主/收藏夹更新 → 自动下载 → 群通知（StreamVault 范式）
- **文件中转站**：「把 D:\电影\xxx.mkv 发我」→ QQ 文件消息；「存到 D:\下载」

### M8 智能家居中枢（v2.0 新增，HAQBot 同构）
- **HA 桥接**：Home Assistant REST + Conversation Agent（本地 Ollama 驱动）；
  自然语言控灯/空调/开关/执行场景脚本（「洗澡模式」「睡眠模式」本地配置化）
- **设备白名单 + 场景脚本映射**：高敏设备（门锁/摄像头）仅主人
- **Webhook 反向告警**：HA 自动化（温度超标/漏水/门磁/人在）→ 推送 QQ 群，
  摄像头事件附截图/视频片段
- **摄像头 stream→视频消息**：「看看客厅」→ 拉流转短视频发送
- **ASR 语音指令**（可选）：腾讯云 ASR 转写语音消息 → 控设备
- HA 管家 prompt 规范（搬 HAQBot）：指令明确无条件执行 / 未响应如实告知 /
  禁止回传原始 JSON / 「天冷了开空调」类意图推理

### M9 运维面与通知中枢（v2.0 新增）
- **开发机状态**：「git status」/「CI 过没过」→ 群推送（本机 manju_agent 仓库）
- **远程主机**：SSH 命令白名单执行 + 结果回传（dsh-qq-remote /exec 范式）
- **Wake-on-LAN**：「开机 客厅那台」→ 魔术包唤醒
- **内网穿透状态**：frp/nps 隧道在线状态查询
- **通知中枢**：宿主机所有事件（下载完成/渲染完成/温度告警/CI 失败）统一走
  AI 人格化转述推送到指定群（「你的视频下好了喵」而非干巴日志）

---

## 五、配置面扩展（全部 env 可覆盖 + 配置页热生效）

| 配置组 | 新增关键项 |
|---|---|
| 宿主机管家 | `PC_ENABLE` / `PC_HIGH_RISK_CONFIRM` / `PC_SCREENSHOT_METHOD`(wgc/mss) / `PC_FILE_ROOTS`(可操作目录白名单) / `PC_SHELL_WHITELIST` |
| 下载媒体 | `DL_ENABLE` / `DL_ARIA2_RPC` / `DL_AUTO_UPLOAD_MAX_MB` / `DL_CONCURRENCY` / `DL_SAVE_DIR` |
| 智能家居 | `HA_ENABLE` / `HA_URL` / `HA_TOKEN` / `HA_AGENT_ID` / `HA_WEBHOOK_PORT` / `HA_ALLOWED_SENDERS` |
| 运维通知 | `OPS_ENABLE` / `OPS_SSH_HOSTS`(白名单) / `NOTIFY_GROUPS` |

---

## 六、安全模型（管家面的生命线）

1. **权限分层**：群友（查询类：监控/天气/搜索）< 主人（文件/窗口/应用）< 主人+确认（电源/kill/删除/shell/剪贴板写）
2. **高危二次确认**：指令 → 机器人回「确认？回复『确认』执行，30 秒有效」→ 执行/超时取消
3. **目录/域名白名单**：文件操作限 `PC_FILE_ROOTS` 内；shell 走白名单命令模板，禁裸 exec
4. **审计日志**：`pc_action` 事件全量落库（操作者/工具/参数摘要/结果）
5. **限额**：每群每日工具调用预算；高危操作每小时上限

---

## 七、排期与依赖

```
M1 记忆 ──┬─→ M2 工具总线 ──┬─→ M3 情感日程
          │                 ├─→ M4 多模态语音
          │                 └─→ M5 心流成长
          └─→ M6 宿主机管家 ──→ M7 下载媒体 ──→ M8 智能家居 ──→ M9 运维通知
```
- M2 是总开关：工具总线不落地，M6-M9 全部退回「命令直通」模式（功能不阻塞，体验降级）
- 建议节奏：人格面（M1-M5）与管家面（M6）并行推进，M6 优先（用户刚需）

---

## 八、验收总标准

**人格面**：称呼记忆/提醒天气看图/凌晨不扰/「你在干嘛」答得出/群热会接话/说话带群梗
**管家面**：「电脑咋样」出体检报告 /「杀掉卡死的进程」确认后执行 /「截个图」秒回
（含 GPU 窗口）/「下这个链接」下载完成自动回传 /「看看客厅」出视频 /「关机」30 秒内
可取消 / 所有高危操作非主人调用被拒并留痕

---

## 九、风险与对策

| 风险 | 对策 |
|---|---|
| LLM 误触高危操作 | 权限分层 + 二次确认 + 命令白名单 + safety 层关键词拦截 |
| 截图/文件泄露 | 目录白名单；剪贴板回传脱敏；文件回传限大小 |
| Token 费用 | 每群预算 + 判断类走便宜模型（AI_JUDGE_MODEL）+ 管家查询类**不走 LLM**（psutil 直接算） |
| DXGI/WGC 黑屏/依赖缺失 | DXGI 失败如实报错、不做 mss 回退（本机 WGC 不可用，已切 DXGI 为唯一真修复；mss 仅作显式备选，非兜底） |
| HA 不在线 | 连接探测 + 显式降级提示 |
| 误关机 | 5 秒取消窗口 + 确认机制 |

---

*v2.0 待批准。建议先批 M1+M6（人格地基 + 管家刚需），其余按排期推进。*

---

## 十、疏漏补全记录（v2.0 → v2.1 审查结论）

审查方式：逐域重读 + 对每个「需要外部能力/平台假设」的点做实测或调研。结果：

### 10.1 已补的安全硬规则（v2.0 最大疏漏）
| # | 疏漏 | 补全 |
|---|---|---|
| 1 | **宿主机信息泄露**：v2.0 未限定 PC 管家的会话边界——群里任何人发「电脑咋样」= 泄露主人硬件配置 | **PC 管家全部能力仅限「私聊 + 主人」**；群聊/非主人调用一律拒绝并留痕（配置 `PC_OWNER_ONLY=true` 可放宽到白名单 QQ） |
| 2 | 截图泄露屏幕内容 | 同 1（仅私聊主人）；截图加**串行锁**（防并发截屏打满磁盘） |
| 3 | 高危操作无取消机制 | kill/删除/电源：主人 + 回复「确认」+ 30s 有效；电源类额外 5s 可取消 |

### 10.2 平台能力实测结论（Windows/psutil 7.2.2）
| 能力 | v2.0 设想 | 实测/调研结论（v2.1） |
|---|---|---|
| CPU/主板温度 | psutil sensors | **psutil 7.2.2 Windows 无 `sensors_temperatures`（AttributeError）** → 三级方案：GPU 温度走 **pynvml**（本机实测 GTX 1660 Ti 37°C ✓）；CPU 温度探测 **LibreHardwareMonitor HTTP :8085**（未运行则诚实告知「装 LHM 才能看 CPU 温度」），不静默返回假数据 |
| GPU 监控 | 未规划 | **新增**：pynvml 提供 GPU 温度/负载/显存——与用户 GTX 1660 Ti + ComfyUI 场景强相关，并入 `/pc` 概览与 `/pc temp` |
| 电池 | psutil battery | 台式机无电池 → 探测不到时输出「台式机」而非报错 |
| 文件回传大小 | <100MB 直发 | NapCat WS 单帧 16MB → base64 实传 ≤10MB → **≤50MB base64 直发，超过走 bridge 本地 HTTP 链接**（`PC_FILE_LINK_MAX_MB`） |
| 删除文件 | 软删除回收站 | `send2trash`（已装）→ 进回收站，禁 `rm -rf` |
| 截图 | mss/DXGI | 第一批用 **mss**（纯 Python 零依赖，GPU 全屏内容黑屏）；**DXGI Desktop Duplication 为 GPU 全屏黑屏真修复**（WGC 在本机 Win10 21H2 不可用），DXGI 失败如实报错、不回退 mss；mss 仅作显式备选（GPU 全屏会黑屏）；mss 失败亦显式报错 |
| 运行环境假设 | 未写明 | **宿主机管家必须跑在交互式用户会话**（截图/键鼠需要桌面）；Windows 服务化部署时这些功能自动禁用并提示（探测 `os.name` + 会话隔离 flag） |

### 10.3 依赖清单（已安装并锁定）
`psutil 7.2.2` / `mss 10.2.0` / `nvidia-ml-py 13.615.71`（替代 deprecated `pynvml` 旧包，已卸载）/ `send2trash 2.1.0`。M7-M9 依赖：`aria2p`(aria2 RPC) / `yt-dlp` / `paramiko` 按里程碑安装。

### 10.4 功能优先级修正（基于用户刚需）
- **M6 提前为最高优先**（人格面 M1 并行）；
- M6 分批交付：**第一批**（已完成 2026-10-20）：系统概览 / Top 进程 / 端口占用 / GPU+温度 / 截图 / kill(确认流) + 安全层 + 审计日志；**第二批**（已完成 2026-10-20）：窗口管理 / 剪贴板 / 应用启动 / 开机自启 / 电源控制(锁屏/睡眠/关机/重启，5s 可取消倒计时) / 键鼠 / 屏幕 OCR；**文件互传**（已完成 2026-10-20）：PC→QQ `/pc get <路径>`（白名单+上限）/ QQ→宿主 私聊发文件自动落盘收件箱（防覆盖·超上限拒·文件名清洗）；**剩余**：WGC 黑屏修复（GPU 全屏窗口截图，mss 下黑屏，用 Windows.Graphics.Capture 替换）；
- M7 前置条件明确：需用户机器已装 aria2 并开 RPC，未检测到则显式提示安装方法（不内置进程，尊重用户机器主权）；
- M8 HA 为**可选域**：未配置 HA_URL/TOKEN 时功能关闭，不影响其它域。

### 10.5 仍未决（下一批调研）
1. 键鼠自动化（PyAutoGUI）的误触防护策略——拟引入「 dry-run 模式」：先回显将执行的动作，确认后才真做；
2. 多显示器/多用户的截图命名规范；
3. M7 aria2 的断点续传文件如何经 QQ 回传（分片 or 链接）。

### 10.6 M2 工具总线交付记录（2026-10-20 ✅）
契约来源：`yaowan233/nonebot-plugin-ai-groupmate v2.5.15`（2026-10-20 实读 README 验证：Tool 注册 + agent 循环、定时任务 10s–7 天、APScheduler 秒级扫库、原子领取、失败不自动重试、超时 5 分钟过期不补发、工具超时 30s、单轮 LLM 上限 8 次、工具结果截断 6000 字、有副作用工具串行）。本地化取舍：不引 langchain/APScheduler，OpenAI 兼容 tools 载荷 + 自有多回合循环 + asyncio tick。

**交付物**（`src/plugins/qq_bridge/tools/` 五件套，不 import 主包、可裸单测）：
| 文件 | 职责 |
|---|---|
| `registry.py` | 工具注册表 + 分发器：风险三级（safe/owner/confirm）、每 scope 每日预算（`AI_TOOL_DAILY_BUDGET`，0=不限）、REDUN-03 入参校验、CLOSE-05 30s 显式超时、结果 6000 字截断、全失败路径显式文本 |
| `llm.py` | OpenAI 兼容客户端（tools 载荷 + `tool_choice=auto`）；模型不支持 tools 的 4xx **如实上抛**并提示关总线/换模型（禁止剥 tools 重试） |
| `fc.py` | `FCRunner` 多回合循环：≤8 回合、串行分发、中间轮次不落用户历史、回合耗尽显式报错 |
| `scheduler.py` | 提醒三件套：`ReminderStore`（`data/reminders.json` 原子写、10s~7 天、每 scope 20 条、claim 原子领取、超 5 分钟过期不补发、失败不自动重试）、`ReminderParser`（LLM-only 时间解析，**禁正则兜底**）、`ReminderScheduler`（1s tick + on_fire 注入） |
| `builtins.py` | 首批 5 工具注册（deps 注入）：`remind_create/remind_list/remind_cancel`（safe）+ `queue_status`（safe）+ `pc_overview`（owner） |

**接线**：`config.py` 新增「工具总线」配置组 8 项（`AI_TOOLS_ENABLE`/`AI_FC_MAX_ROUNDS=8`/`AI_TOOL_TIMEOUT=30`/`AI_TOOL_RESULT_MAX_CHARS=6000`/`AI_TOOL_DAILY_BUDGET`/`REMIND_TICK=1`/`REMIND_EXPIRE_SECONDS=300`/`REMIND_MAX_PER_SCOPE=20`）；`config_api.py` 配置页同款 8 条；`ai_mode.py` 新增 `chat_tools()`；`__init__.py`：`register()` 注册首批工具、`_on_startup/_on_shutdown` 起停提醒调度器（`_remind_fire` 回传原会话）、`_route` 挂 `/提醒` 命令族（创建/列表/取消/修改）、`_handle_ai_chat` 按「开关开且已有工具注册」路由到 FC 路径（否则纯对话——路由选择非兜底）。

**confirm 级不在第一轮**：kill/电源/剪贴板写/删文件继续走 `/pc` 命令通道既有确认流；registry 遇 confirm 调用明确拒绝并指路。这是发布范围决策，两条通道能力全集不变。

**验收**：`tests/test_tools_registry.py` + `test_tools_fc.py` + `test_reminders.py` 三件套 47 断言 + 与 `test_config_api`/`test_config_hot_reload` 组合 **73/73 全绿**。既有缺陷记录：全量 `pytest tests/` 时 hot_reload 必红 12 条（旧测试用 `src.plugins.qq_bridge.*` 限定名导致两份 Config 类，`config_api._find_config_class` 命中错的那份）——单文件/相关组合全绿，本轮只记录不修（见 AGENTS.md F12）。

### 10.7 M7 下载媒体中心交付记录（2026-10-20 ✅）
契约来源（2026-10-20 实读验证）：**DownloadBot**（iaman1998）Aria2.go——aria2 JSON-RPC 方法名（addUri/addTorrent/getFiles/changeOption/tellStatus/tellStopped/pause/unpause/remove/getGlobalStat）、BT「只选最大文件/按大小筛」范式、磁力 paused 添加等元数据再选文件时序；**StreamVault**（Lugic1993）README——yt-dlp 通用平台下载 + 下载完成 Webhook 通知 + 收藏夹/作品监控范式。

**交付物**（`src/plugins/qq_bridge/download/` 五件套，不 import 主包，可裸单测）：
| 文件 | 职责 |
|---|---|
| `aria2.py` | aria2 JSON-RPC over HTTP 客户端（httpx 裸协议，不引 aria2p）：token:secret 鉴权、addUri/addTorrent(base64)/tellStatus/getFiles/changeOption/tell*/pause/unpause/remove/getGlobalStat/getVersion/purge；CLOSE-05 15s 超时、REDUN-02 仅 ConnectError 退避重试封顶 3 次、连不上显式给出开启命令（**不内置拉起进程**——尊重机器主权） |
| `btselect.py` | BT 智能选文件纯函数：largest（只选最大）/smart（≥平均×0.8）/all（显式全选）；元数据零字节时显式空让调用方告知，不静默全选 |
| `ytdlp.py` | yt-dlp 封装：解析（元数据）/下载（默认 bv*+ba/b 合并）；同步 API 经 asyncio.to_thread；标题清洗防路径穿越；失败显式 YtDlpError |
| `manager.py` | 下载编排：链接四分类（http/magnet/torrent/video）；磁力 paused 添加→元数据轮询（DL_BT_META_TIMEOUT 180s 超时显式失败）→选文件→unpause；tellStopped 轮询（DL_POLL_INTERVAL 5s）检测完成→on_complete 回调；bridge 侧登记 `data/downloads.json`（CLOSE-01 状态机、原子写、终态滚动清理 100 条） |
| `subscribe.py` | 订阅监控（StreamVault 范式）：yt-dlp flat playlist 统一抽象（不为每平台写 API）→ 新条目判定（id 不在 last_ids）→ 自动下载 + on_new 通知；DL_SUB_INTERVAL 默认 1h、每 scope 上限 5 |

**接线**：`config.py` 「下载媒体」配置组 15 项 + `config_api.py` 配置页同款；`tools/builtins.py` 注册 3 个 FC 工具（`download_add`/`download_list`/`download_cancel`——下载占主人资源，add/cancel 定 owner 级）；`__init__.py`：`_route` 挂 `/dl` 命令族（下载/列表/暂停/继续/取消/发我/订阅/退订/状态；下载与订阅仅主人，群聊静默同 /pc 口径）、`_on_startup` 起 download_manager（on_complete=_dl_notify：小文件 ≤PC_FILE_MAX_MB 直发、大文件报路径+取件命令）与 subscription_monitor（submit/on_new 注入）、`_on_shutdown` 停两者。

**文件中转站**：`/dl 发我 <序号|编号|绝对路径>` 复用 M6 `_pc_send_file`（PC_DISK_ROOTS 白名单 + PC_FILE_MAX_MB 上限 + send_file）；删文件不在 M7 范围（高危留 M6 确认流）。

**验收**：`tests/test_download_aria2.py`（MockTransport 打假服务测真实协议路径）+ `test_download_btselect.py` + `test_download_manager.py` + `test_download_subscribe.py` 共 55 断言，与 M2/config 组合 **128/128 全绿**；**真实链路抽样 OK**（aria2c 1.37.0 RPC 实测 getVersion→addUri→下载 22B 文件→complete 通知→listTasks，抽样后进程已关）。顺带修复 falsy 配置吞噬 bug（`getattr or default` 把合法 0 值吃掉，详见 AGENTS.md F13）。

### 10.8 M9 运维面 + 通知中枢交付记录（2026-10-20 ✅）
契约来源（2026-10-20 实读验证）：**dsh-qq-remote**（ASAKAFENG）README——QQ→SSH `/exec` 远程执行范式（其自认「可执行任意命令」；本项目安全模型**更严**：主机白名单 + 命令白名单 + 危险模式双保险 + 审计 + 仅主人 + 二次确认）；**frp**（fatedier）——frpc admin API（`/api/status`）作隧道状态查询协议。

**交付物**（`src/plugins/qq_bridge/ops/` 六件套，不 import 主包，可裸单测）：
| 文件 | 职责 |
|---|---|
| `gitinfo.py` | 开发机 git 仓库状态（只读子命令白名单 status/diff/log/branch/rev-parse/show/remote/stash-list，写操作一律拒绝）；subprocess 显式超时 + 参数数组化（不经 shell 防注入） |
| `ssh.py` | SSH 命令白名单执行（paramiko）：`别名:user@host[:port]` 主机白名单 + 命令前缀白名单 + 危险模式（rm/poweroff/git push/管道 sh）双保险；显式超时不重试（远程执行重试=重复副作用）；审计 jlog（谁/何时/主机/命令）；key 鉴权（密码禁落配置 LOG-04） |
| `wol.py` | Wake-on-LAN 魔术包（标准 6×0xFF+16×MAC，UDP 广播）；主机白名单 `别名:MAC[@广播/端口]`；发送失败显式报错 + BIOS/网卡/路由器三处排查指引 |
| `frp.py` | frpc admin API `/api/status` 查询（httpx 显式超时）；未配/不通诚实告知缺什么，不返回假在线状态 |
| `notify.py` | **通知中枢**：机器事件统一入口 `hub.push(NotifyEvent)` → 级别过滤 → 目标路由（OPS_NOTIFY_TARGET）→ LLM 人格化转述（rephrase 回调）→ 发送（send 回调）；同类事件冷却期合并计数到点补发（防刷屏+LLM 成本）；转述失败**原文照发+显式附注**（不冒充转述成功）；最近 50 条内存环供查询 |
| `watchdog.py` | 开发机仓库巡检循环：定时 git status（脏改动/未推送**从无到有才推**——没变化静默，防刷屏）+ 可选自检命令（OPS_WATCH_CHECKS，退出码非零→error 级事件） |

**接线**：`config.py`「运维面」配置组 17 项 + `config_api.py` 配置页同款；`__init__.py`：`_route` 挂 `/ops` 命令族（git/gitlog/ssh/wol/frp/通知/通知历史；**全部仅主人私聊**，群聊静默同 /pc 口径；SSH 走「确认」二次确认 30s 有效 + 审计）；`_on_startup` 起 repo_watchdog（on_event=hub.push）+ 注入 hub.rephrase（借 `ai_chatter.say` 人设）/hub.send（`_ops_notify_send` 发往 OPS_NOTIFY_TARGET）；`_on_shutdown` 停巡检；FC 工具 2 个（ops_git_status/ops_frp_status，owner 级；**SSH 不注册 FC**——高危只走 /ops 确认流）。

**通知中枢接入策略（分期，如实记录）**：一期服务 M9 自有事件（仓库巡检脏改动/未推送/自检失败）；M6 告警/M7 下载完成保持既有直发链路（它们本就是人话，无需转述），二期按「机器事件才转述」原则逐个收编——hub 的 push API 即统一契约，收编是接入问题不是重构。

**验收**：`tests/test_ops_gitinfo.py`（含真实仓库实测）+ `test_ops_ssh.py` + `test_ops_wol_frp.py`（MockTransport/FakeSock）+ `test_ops_notify.py` 共 45 断言，与 M2/M7/config 组合 **173/173 全绿**。顺带修复 SSH/WoL 配置解析缺陷与 socket import 位置（见 AGENTS.md F14）。
