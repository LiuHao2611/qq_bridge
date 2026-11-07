# QQ 机器人「自绘图片」能力规划（菜单图 / 进度条图 / 卡片图）

日期：2026-09-07　状态：**P0~P4 已完成（69 条断言全过），待真实 QQ 收发验收**
目标：让 qq_bridge 机器人自己**合成排版好的图片**（帮助菜单、任务进度条、配额条、结果卡片）发出去，替代/补充纯文字。

**已确认的三项决策（用户 2026-09-07）**
1. 进度条 = **按需绘制**（用户发 `/status` 时才画），不做定时刷新
2. 视觉风格 = **深色霓虹 + 极简白底两套主题，支持热切换**
3. 渲染库 = **选更被主流认可的那个** → 结论：**Pillow**（见第二节数据）

---

## 一、需求与现状

| 场景 | 现在 | 期望 |
|---|---|---|
| `/help`、你能干啥 | 长文本 `HELP_TEXT` | **菜单卡片图**（分区 + 图标 + 命令高亮） |
| `/status`、你咋样 | 「还在画呢（已等 42 秒）」 | **进度条图**（百分比 + 耗时 + 状态色） |
| `/quota`、今天额度 | 「今天你已经造了 12 / 50」 | **配额条图**（已用占比 + 剩余） |
| 生图完成 | 纯文字 + 图片 | 结果卡片（P3，可选） |

约束：宽度 720~900px、体积 < 2MB、渲染 < 300ms、不阻塞事件循环、失败回落纯文本。

---

## 二、渲染库选型：**Pillow**（数据对比）

| 指标 | **Pillow** | nonebot-plugin-htmlkit |
|---|---|---|
| PyPI 累计下载 | **88.1 亿次** | 极少（检索到的第三方采用仅 1 个插件） |
| 近 30 天下载 | **5.14 亿次**（月约 1 亿） | — |
| 最新版本 / 时间 | **12.2.0 / 2026-04-01** | `0.1.0rc5` / 2026-01-17（**8 个月未更新**） |
| 发布状态 | **稳定正式版** | rc1~rc5 **全部预发布，从未有正式版**；classifier = `Development Status :: 4 - Beta` |
| GitHub | **14k stars** / 500+ 贡献者 | nonebot 组织维护，但生态采用罕见 |
| 生态评价 | Python 图像领域 **dominant library by a wide margin**，被列为 Python community **key ecosystem package** | 官方 2026-08 有最佳实践文档（官方在推），但社区落地少 |
| 体积 / 依赖 | wheel ~2.5MB，**纯 Python 单包** | Windows wheel **7.27MB** + 额外拖 6 个依赖（jinja2 / markdown / pygments / pymdown-extensions / aiofiles / nonebot2） |
| 渲染能力 | 手算坐标（本需求是固定布局，够用） | litehtml CSS 子集、**无 JS**、dpi/height 受限 |
| 主题热切换 | **天然支持**（主题 = Python 常量字典，渲染时读取） | 需切 CSS 文件 / 双模板 |
| 风险 | 无 | prerelease + Windows fontconfig 字体查找 + emoji 大概率黑白/豆腐 |

**结论：Pillow 以约 4 个数量级的采用度胜出，且本项目只需 4 套固定布局，不需要 CSS 引擎。**
→ **采用 Pillow 自绘，不引入 nonebot-plugin-htmlkit。**

> 说明：QQ 机器人圈"精美图片"的**历史主流**是浏览器渲染（htmlrender + Playwright），但那需要 ~170MB Chromium；在我们的 4 套固定卡片场景下，Pillow 更轻、更可控、零风险，且是 Python 生态公认主流。

### 借鉴来源（思路可抄，代码不抄）
- **zhenxun_bot `BuildImage`**（`utils/_build_image.py`）：圆角卡片 `circle_corner`、圆形头像 `circle`、网格排版 `auto_paste`、居中文本、`pic2bytes()`、`@run_sync` 防阻塞
  ⚠️ **AGPL-3.0 —— 禁止复制源码，只按思路重写**
- **nonebot-plugin-cardimg**（2026-02）：`render('help', title, items={...})` / `render('table', columns, data)` 的 **API 形态** 与 help/table 模板结构（License 未声明 → 只抄形态不抄文件）
- **Yunzai help-plugin**：**图标集中管理 + 按参数 hash 缓存 + 主题切换** 三件事

---

## 三、目标架构（新代码全在 `qq_bridge/`，符合隔离铁律）

```
src/plugins/qq_bridge/pic/
├── __init__.py         统一入口 render(template, theme=None, **kw) -> bytes
├── canvas.py           Canvas 画布类（新建/圆角/渐变/描边/导出 bytes）
├── theme.py            主题定义 + 解析（neon / light / auto）+ 当前主题取值
├── widgets.py          可复用控件：卡片、标题、菜单行、进度条、标签、页脚、图标
├── icons.py            图标（Unicode 符号 + 可选自制 PNG），集中管理
├── templates/
│   ├── menu.py         帮助菜单
│   ├── progress.py     任务进度条（按需绘）
│   ├── quota.py        今日配额条
│   └── result.py       生图结果卡片（P3）
├── fonts.py            中文字体探测与加载（msyh / msyhbd / simhei 回退链）
└── cache.py            按 (template, theme, params) hash 缓存，TTL + LRU 上限
```

**统一入口**
```python
data: bytes = await pic.render("progress", theme="neon", percent=42, elapsed=25, kind="t2i")
```
- `render` 内部：`解析主题 → 渲染（asyncio.to_thread）→ 缓存 → 返回 PNG bytes`
- **主题不传则读 `Config.PIC_THEME`**（类属性）→ 改配置**立即热生效**，无需重启（与项目现有 `_hot_apply` 机制一致）

---

## 四、双主题设计（热切换）

### 4.1 色板
| token | **neon（深色霓虹）** | **light（极简白底）** |
|---|---|---|
| bg | `#0B0E17` | `#FFFFFF` |
| card | `#141A2B` | `#F5F7FA` |
| card_border | `#232C45` | `#E3E8EF` |
| text | `#EAF0FF` | `#1A1A1A` |
| text_sub | `#7C8AA5` | `#6B7280` |
| accent | `#00E5FF`（青） | `#2563EB`（蓝） |
| accent2 | `#FF4FD8`（洋红，渐变尾） | `#60A5FA` |
| progress_track | `#1E2740` | `#E5E7EB` |
| progress_fill | `#00E5FF → #FF4FD8` 渐变 | `#2563EB` 纯色 |
| ok / warn / err | `#3DDC97` / `#FFB020` / `#FF5C7A` | `#16A34A` / `#D97706` / `#DC2626` |

### 4.2 热切换三层
1. **全局默认**：`Config.PIC_THEME = neon | light | auto`（`auto` = 19:00–07:00 用 neon，其余 light）
2. **配置页热改**：`config_api.SCHEMA` 追加「常规·图片渲染」分组，`core=false` → `setattr(Config, ...)` 立即生效
3. **用户级临时切换**（可选，女仆暗号）：「换深色」/「换浅色」→ 存 `Session.pic_theme`，渲染时会话优先于全局

### 4.3 图标方案（Pillow 不渲染彩色 emoji 的应对）
1. **首选**：Unicode 几何符号 `★ ● ◆ ▶ ■ ▲ ✦ ✔ ⏳`，**微软雅黑/黑体原生覆盖**，零依赖
2. **备选**：`assets/icons/*.png` 自制 32×32 霓虹描边小图标（完全可控，纯 Pillow 画）
3. **不采用** `pilmoji`：仅为 emoji 再引一个依赖，性价比低；若后续确实要彩色 emoji 再评估

---

## 五、与现有代码的接入点（改动很小）

| 文件 | 改动 |
|---|---|
| `src/plugins/qq_bridge/media/__init__.py` | 新增 `send_pic(ctx, data: bytes)` → `base64://...` → `MessageSegment.image()`；复用已有的 URL→base64 回退逻辑 |
| `src/plugins/qq_bridge/__init__.py` `_handle_reply` | `__STATUS__` → 有进行中任务时发 `progress` 图；`__QUOTA__` → 发 `quota` 图 |
| `src/plugins/qq_bridge/__init__.py` `/help` | 先尝试 `menu` 图，失败回落 `HELP_TEXT` |
| `src/plugins/qq_bridge/config.py` | `PIC_ENABLE / PIC_THEME / PIC_MAX_WIDTH / PIC_CACHE_TTL / PIC_CACHE_MAX` |
| `src/plugins/qq_bridge/config_api.py` | SCHEMA 追加「常规·图片渲染」分组（全 `core=false`） |
| `requirements.txt` | 新增 `Pillow>=10.0`（当前 venv **未安装**，wheel ~2.5MB） |
| `tests/` | `test_pic_render.py`（断言见第七节） |

**发送链路**：`pic.render()` → bytes → `media.send_pic()` → `_send()` 已有 1~3s 随机延迟 + 落 `history` 旁路 → QQ
**失败降级**：渲染异常 → 记 `jlog("pic_render_failed", ...)` → 回落现有纯文本，绝不静默（CLOSE-02）

---

## 六、分阶段计划

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P0 地基（半天）** | `pip install Pillow`；`fonts.py` 探测 `msyh.ttc / msyhbd.ttc / simhei.ttf`；画一张「中文 + 圆角 + 进度条」验证图 | 1 张验证 PNG，确认无豆腐、圆角正常 |
| **P1 内核** | `canvas.py`（圆角/渐变/描边/导出）+ `theme.py`（双主题 + auto + 取值）+ `fonts.py` + `cache.py` | `tests/test_pic_theme.py` 通过 |
| ~~**P1 实际已交付**~~ | + `icons.py`（16 种几何图标自绘，4× 超采样抗锯齿）+ `widgets.py`（卡片/标题/进度条/菜单行/键值行/分割线/页脚/标签/面板/数值块） | `tests/test_pic_widgets.py` **31/31 通过**（含图标全 kind 参数化、进度条占比 ±3%、菜单行不越界、缓存命中/TTL 过期/LRU 淘汰/索引损坏恢复） |
| **P1 顺带踩到的坑** | ① light 主题 accent_soft 原 #E8F0FE 与卡片色差仅 24（徽章几乎看不见）→ 改 #D7E4FC，差值更大；② 菜单行图标 22px 偏小（占容器 55%，不达标）→ 提到 26px（65%），符合主流；③ `_non_bg_count` 弃用 getdata，改 getcolors(Pillow 14 兼容) | 已写注释 + 调整断言语义（数图标色像素而非底色差） |
| **P2 模板** | `menu` / `progress` / `quota` 三套（design 调研确认**任务进度用线性条**+**配额用环形**）+ 统一 `render()` 入口（to_thread + 缓存联动）+ Config 新增 PIC_* 5 项 | `tests/test_pic_templates.py` **13/13 通过**（含异步/性能<300ms/体积<200KB/缓存命中/未知模板抛错/危险色生效）。`render()` 同模板+同主题+同参数复用产物；`Config.PIC_THEME` 改值即热生效。 |
| **P2 顺带踩到的坑** | ① progress 模板高度 296 不够装下"2 行 wrap 提示词+ footer" → 提到 360；② test_dispatcher::test_group_message_with_command_prefix_is_accepted 等 7 个**既有测试失败与 pic 无关**（MANJU_FILE_BASE 派生时机 + 2026-09-04 收紧@策略后旧测试未更新）→ 临时移开 pic 包重跑同样失败 = pre-existing | 已修；既有失败未擅自触碰（诚实声明） |
| **P2 控件** | `widgets.py`：卡片、标题、菜单行、进度条（含渐变填充 + 百分比）、标签、页脚；`icons.py` | `tests/test_pic_widgets.py` 通过 |
| **P3 模板** | `menu` / `progress` / `quota` 三套（result 视需要） | 6 张示例图（2 主题 × 3 模板）人工确认 |
| **P4 接入** | `media.send_pic`（base64:// 直发）+ `_try_send_pic`（失败返回 False → 文本兜底）+ `/help`→menu、`/status`→progress、`/quota`→quota + Config/SCHEMA 五配置（全 core=false 热生效）+ 女仆暗号「换深色/换浅色/自动主题」会话级切换 | `tests/test_pic_integration.py` **13/13 通过**（含渲染失败回落、PIC_ENABLE 关闭止血、主题切换真的传入 render） |
| **P5 验收** | 全量回归：`pytest tests -q` → **200 passed / 7 failed**；7 个失败经「临时移开 pic 包重跑」确证为 **pre-existing**（`MANJU_FILE_BASE` 在 import 时派生、测试改 `MANJU_API_BASE` 不生效；2026-09-04 收紧群消息必须真 @ 后旧测试未更新），**与本次功能无关，未擅自修改** | 交付说明 + 复现步骤见 README「自绘图片」章节 |
| **P6 用户新需求（2026-09-07）** | (1)「把 @机器人返回都改成图片」→ 新增 `text` 短文本卡模板（自适应高度 + 主色左边条 + 图标 + 标题/正文），`_reply` 统一出口替换所有机器人主动回复的 `_say`（模式切换/主题切换/help 空@/pong/test 词/HELP_TEXT 兜底）；顺手修掉 `sess.set` 死代码（删除！）（2）「加自定义主题页面」→ `pic/themes_store.py` JSON 持久化（强校验 17 字段 + 原子写）+ `theme.resolve()` 支持自定义名（`PIC_CUSTOM_THEME` 优先于 `PIC_THEME`）+ `pic/editor.py` FastAPI REST（列表/详情/保存/删除/激活/取消/**实时预览**）+ `pic/editor_page.html` 玻璃拟态 dashboard 编辑器（17 拾色器 + WCAG 实时算 + 预设 + 导出 JSON）+ `pic.render_raw()` 绕过解析直接渲染临时 Theme | `tests/test_pic_integration.py` **16/16 通过**（含模式/主题切换发 text 卡、_reply 失败回落文本、`_reply` helper）；`tests/test_pic_custom_theme.py` **18/18 通过**（themes_store CRUD + 校验错误 + resolve 命中自定义 + 激活热生效 + 编辑器全 REST 端点 + preview PNG + 页面 HTML）。全量 **221 passed / 7 failed**（7 个仍为 pre-existing，与本次功能完全无关） |
| **P6 顺带踩到的坑** | ① `_reply` 渲染失败 → `test_status_idle_says_text` 测试失效（断言"空状态发文本"，现已发 text 图），更新为「发 text 图」语义；② editor.py 模块级 `from . import pic` 触发循环（pic 包内无名为 pic 的子模块），改用 `sys.modules[__package__]` 拿 pic 包对象；③ editor.py FastAPI 端点 `req` 参数必须有类型注解（否则 422），加 `: Request`；⑤ validate_colors 强制 6 位但内置 `LIGHT.card_shadow = "#0F172A0D"` 是 8 位带 alpha，放宽为接受 6/8 位 | 全部已修 + 加注释，避免后续踩 |

---

## 七、验收标准（机器判定，禁止人工读图自判）

`tests/test_pic_render.py` 断言项：
1. `render()` 返回非空 `bytes`，且以 PNG magic `\x89PNG` 开头
2. 宽度 == `PIC_MAX_WIDTH`（默认 800），高度 > 200
3. 主题生效：`neon` 背景像素 ≈ `#0B0E17`；`light` ≈ `#FFFFFF`（取左上角像素比对，容差 ≤2）
4. 进度条：`percent=42` 时，填充色像素在进度条 Y 中线上的**占比 ≈ 42% ± 3%**
5. 双主题同参数产物**不相同**（hash 不同）
6. 缓存：同参数二次调用命中缓存（返回相同 hash，耗时显著下降）
7. 热切换：`setattr(Config, "PIC_THEME", "light")` 后立即 `render()` 产物变为浅色
8. 降级：强制渲染抛异常时，`_handle_reply` 仍发出文本（`send_text` 被调用）

---

## 八、风险与红线

1. **License**：`zhenxun_bot` = AGPL-3.0 **禁止复制源码**（只借鉴思路重写）；`cardimg` License 未声明，只抄 API 形态不抄文件；Pillow = HPND，**可商用**
2. **字体**：Windows 通用回退链 `msyh.ttc → msyhbd.ttc → simhei.ttf → 系统默认`；找不到中文则记 WARNING 并用默认字体（不崩溃）
3. **不阻塞事件循环**：所有绘制在 `asyncio.to_thread` 内执行（与现有 `history` 模块一致）
4. **不刷屏**：进度图**只在 `/status` 时按需绘制**（已确认），定时刷新开关默认关闭
5. **缓存有界**：`data/pic_cache/`，TTL + LRU 上限，防磁盘无限增长（OPS-02/03）
6. **隔离铁律**：`pic/` 全部代码、字体、图标、缓存均在 `qq_bridge/` 内；不 import 主项目任何模块；不装任何依赖进主项目 venv
7. **诚实交付**：做不到/没做完必须明说，禁止假装完成

---

## 九、开工前最后确认

- [x] 进度条按需绘
- [x] 双主题 + 热切换
- [x] 渲染库 = Pillow
- [x] **P0 地基完成**（2026-09-07）—— `Pillow 12.3.0` 已装、`fonts.py` 系统字体探测通过（`msyh/msyhbd/simhei` 齐全）、`canvas.py` 圆角 + 渐变 + 文字 + 导出 OK；`tests/test_pic_canvas.py` **12/12 通过**；预览图 `data/pic_preview/progress_{neon,light}.png` 已生成（中文字形清晰、渐变平滑、圆角干净）。下一步 P1。
