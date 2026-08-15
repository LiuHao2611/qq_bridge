# P5 怪盗风主题 · 设计契约与运维手册

> 第三套可选前端主题（与既有的 `t-gray` / `t-green` / `t-blue` / `t-gradient` / `t-splice` 并存，可一键切回）。
>
> 依据：`D:/AI/md/万能短剧AI创作平台_P5风格UI重构开发手册.md` v1.0

---

## 1. 设计铁律（三色 / 零圆角 / 硬影 / 斜切）

| 维度 | 规则 | 实现 |
|---|---|---|
| 主色板 | 绯红 `#E4002B` · 墨黑 `#0B0B0B` · 纸白 `#F5F1E8` + 警示黄 `#FFE100`（<5%） | `--p5-red / --p5-ink / --p5-paper / --p5-alert` |
| 圆角 | **零圆角**，全部直角 | `--radius: 0`，`--el-border-radius-*: 0` |
| 阴影 | **硬位移印刷影**（无 blur） | `4px 4px 0 0 rgba(0,0,0,.9)`；hover 位移 `-2px,-2px` |
| 构图 | **斜切**（块斜字回正），旋转约 -6° / -8° | 应用到 `.page-head h1`、激活侧栏项、`.p5-*` 工具类 |
| 文字 | 小号文字（≤12px）禁绯红前景；强调用 `--sub` 灰阶或 `--p5-white` | 所有 `.muted` 文字走 `--sub` |

---

## 2. 令牌矩阵（`html.t-p5` 作用域内）

### 2.1 色彩
| Token | 值 | 用途 |
|---|---|---|
| `--p5-red` | `#E4002B` | 主强调 / 主按钮 / 进度条 |
| `--p5-red-ink` | `#B00020` | 纸白底文字（强对比） |
| `--p5-red-deep` | `#8A0018` | 厚阴影色 |
| `--p5-ink` | `#0B0B0B` | 主背景 |
| `--p5-ink-soft` | `#191919` | 面板 / 卡片 |
| `--p5-ink-2` | `#141414` | 二级背景 / 表面 |
| `--p5-paper` | `#F5F1E8` | 纸白（备用浅底） |
| `--p5-paper-dim` | `#E8E2D4` | 纸白灰 |
| `--p5-white` | `#FFFFFF` | 前景文字 |
| `--p5-alert` | `#FFE100` | 警示黄（<5%） |
| `--line-dark` | `#2A2A2A` | 描边 / 分隔线（P5 块内自持定义，原 app 无此变量） |
| `--fg` | `var(--p5-white)` | 前景文字（卡片标题/表格正文） |
| `--surface` | `#141414` | `.p5-card` 表面 |
| `--fg-invert` | `var(--p5-ink)` | 纸白底上的反色文字 |

### 2.2 字体（纯系统栈 · RED-04）
| Token | 用途 | 字体栈 |
|---|---|---|
| `--font-display` | 斜切大标题 / 按钮 | `'Arial Black', 'Helvetica Neue', 'Impact', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif` |
| `--font-cond` | 条件 / 标签 / 章节名 | `'Arial Narrow', 'PingFang SC', 'Microsoft YaHei', sans-serif` |
| `--font-body` | 正文 | `'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', sans-serif` |
| `--font-num` | 数字 / 进度 | `'DIN Alternate', 'Roboto Mono', 'Consolas', monospace` |

### 2.3 排版级距
| Token | 值 |
|---|---|
| `--text-display` | `clamp(32px, 5vw, 56px)` |
| `--text-h1` | `clamp(24px, 3vw, 36px)` |
| `--text-h2` | `clamp(18px, 2vw, 24px)` |
| `--text-body` | `15px` |
| `--text-caption` | `12px` |
| `--text-num` | `clamp(20px, 3vw, 40px)` |

### 2.4 阴影 / 边框
| Token | 值 |
|---|---|
| `--shadow-hard-sm` | `2px 2px 0 0 rgba(0,0,0,.9)` |
| `--shadow-hard` | `4px 4px 0 0 rgba(0,0,0,.9)` |
| `--shadow-hard-lg` | `6px 6px 0 0 rgba(0,0,0,.9)` |
| `--shadow-red` | `4px 4px 0 0 var(--p5-red-deep)` |
| `--shadow-paper` | `4px 4px 0 0 var(--p5-paper)` |
| `--border-1` | `1px solid var(--line-dark)` |
| `--border-2` | `2px solid var(--p5-white)` |
| `--border-red` | `2px solid var(--p5-red)` |

### 2.5 动效
| Token | 值 |
|---|---|
| `--dur-fast` / `--dur-base` / `--dur-slow` / `--dur-epic` | `120ms` / `240ms` / `420ms` / `700ms` |
| `--ease-out` / `--ease-pop` / `--ease-steps` | `cubic-bezier(.22,.61,.36,1)` / `cubic-bezier(.34,1.56,.64,1)` / `steps(4,end)` |
| `--skew` / `--skew-title` / `--skew-sm` / `--skew-hard` | `-6deg` / `-8deg` / `-4deg` / `-12deg` |

### 2.6 clip-path（块状几何）
- `--clip-slant`：斜边矩形
- `--clip-para`：双斜边梯形
- `--clip-zig`：锯齿下边
- `--clip-tear`：撕裂边缘
- `--clip-star`：八角星（用于 `.p5-badge-star` / `.p5-starburst`）

---

## 3. 组件映射表（零模板改动 · RED-01/RED-02 合规）

> 通过 `html.t-p5 <class> { ... }` 选择器作用域覆盖现有全局组件类，**33 个 `.vue` 组件零改动即 P5 化**。

### 3.1 骨架 / 导航
| 目标 | 选择器 | P5 样式 |
|---|---|---|
| 页头底部红条 | `.page-head::after` | 绯红渐变线 4px |
| 侧栏 | `.sidebar` | 墨黑底 + 绯红左边框 |
| 侧栏 logo | `.sidebar .logo` | display 字体 + 斜切 |
| 侧栏激活项 | `.sidebar .nav-item.active` | 绯红底 + 斜切 + 硬影 |
| 顶栏 | `.topbar` | 墨黑底 + 3px 绯红底边 |
| 页头大标题 | `.page-head h1` | display 字体 900 + 斜切 -8° + 双色文字阴影（红+墨） |
| 折叠占位符 | `.muted` | 灰阶 `--sub` + 条件字 |

### 3.2 原生按钮族
`.btn` / `.btn.ghost` / `.btn.blue` / `.btn.green` / `.btn.pink` / `.btn.del` / `.btn.sm` 全部零圆角 + 硬影 + 绯红 hover 平移

### 3.3 项目 / 资产 / 章节卡
`.proj-card` / `.asset-card` / `.chapter-tabs .ch` 零圆角 + 硬影 + hover 平移 + 旋转 -1°

### 3.4 标签组 / 筛选
`.asset-tabs .t` / `.tabs .tab` / `.asset-tag` 零圆角 + 条件字体 + 激活态绯红 + 斜切

### 3.5 分镜表（关键 · 序号斜切方块）
`.shot-table .idx` = 绯红斜切方块（`transform: skewX(-6deg)`，内文字反向 skew 回正）
`.shot-table select/input/textarea` = 零圆角 + 墨黑底 + 聚焦红边

### 3.6 卡片 / 面板 / 参数
`.director-card` / `.param-group` / `.preview-box` / `.check-item` / `.toggle` / `.color-swatch` 全部零圆角 + 硬影

### 3.7 进度条
`.progress .bar i` = `linear-gradient(115deg, var(--p5-red) 0 90%, transparent 90% 100%)` 斜切段

### 3.8 模态 / Toast / 提示
`.modal` / `.toast` / `.toast.ok` / `.toast.warn` 全部零圆角 + 硬影 + 左侧绯红描边

### 3.9 Element Plus 覆盖
`.el-button` / `.el-menu-item` / `.el-card` / `.el-dialog` / `.el-drawer` / `.el-table` / `.el-tag` / `.el-input__wrapper` / `.el-textarea__inner` / `.el-input-number` / `.el-select-dropdown` / `.el-progress-bar` / `.el-tabs__active-bar` / `.el-switch.is-checked` / `.el-radio__inner` / `.el-checkbox__inner` / `.el-slider__bar` / `.el-step` / `.el-pager` / `.el-message` / `.el-tooltip__popper` / `.el-popper` / `.el-loading-mask`

> 实现要点：所有 EP 覆盖只引用 P5 令牌（`--p5-red` / `--p5-ink` / `--p5-white` / `--line-dark`），不引入任何新色值，确保色板纯粹。

---

## 4. 路由切换 wipe（`<transition name="p5-page" mode="out-in">`）

仅在 `html.t-p5` 作用域生效 → 其他主题保持原 transition 行为
`mode="out-in"`：先 leave 后 enter（避免新旧组件同时占用导致闪烁）
**未加 `:key`**：保持现有 `<keep-alive :include="cachedViews" :max="5">` 缓存语义不变
`@media (prefers-reduced-motion: reduce)` 下所有动效退化为 `0.01ms`（RED-04 §7.9）

---

## 5. `.p5-*` 工具类（供组件精修）

| 工具类 | 用途 |
|---|---|
| `.p5-title` | 巨型斜切标题（含双影） |
| `.p5-tag` / `.p5-tag.on-paper/.ink/.alert/.ok` | 斜切小标签（`.on-paper` 走纸白底） |
| `.p5-card` | 标准 P5 卡片（零圆角 + 硬影 + hover 平移） |
| `.p5-btn` / `.p5-btn--primary/--dark/--paper/--ghost` | 斜切按钮（自带 skew 校正） |
| `.p5-progress` | 绯红斜切段进度 |
| `.p5-notice` | 斜切告示条（pop 入场） |
| `.p5-badge-star` | 八角星徽章 |
| `.p5-duotone` | 双色调图片（红 + 灰度滤镜叠加） |
| `.p5-halftone` | 半色调网点装饰 |
| `.p5-diag-split` | 对角分割（黑/红/黑） |
| `.p5-starburst` | 八角星横幅 |
| `.p5-zigzag` | 锯齿底边 |
| `.p5-tear` / `.p5-tear.paper` | 撕裂边缘卡片 |
| `.p5-divider` / `.p5-divider-red` | 分隔线（3px 红） |
| `.p5-guide-line` | 引导线（垂直 1px 半透白） |
| `.p5-num` / `.p5-label` | 等宽数字 / 条件标签 |

---

## 6. 接入与回退

### 6.1 启用 P5
- **首次访问**：默认启用（`readPreset()` 返回 `'p5'` → `applyPreset` 加 `t-p5`）
- **手动切换**：设置页 → 外观 → 主题色 → 选「#E4002B 绯红」圆点；写入 `localStorage.manju_theme = 'p5'`
- **非破坏性**：原主题 `''/gray/green/blue/gradient/splice` 全部保留可一键切回

### 6.2 回退（恢复原主题）
```js
// 浏览器控制台一行：
localStorage.setItem('manju_theme', '');  // 或 'gray' / 'green' / 'blue' / 'gradient' / 'splice'
location.reload();
```

### 6.3 紧急回滚源码
```bash
# 备份在 .tmp/p5_backup/
cp .tmp/p5_backup/style.css.bak     src/style.css
cp .tmp/p5_backup/useTheme.js.bak   src/composables/useTheme.js
cp .tmp/p5_backup/App.vue.bak       src/App.vue
cp .tmp/p5_backup/Settings.vue.bak  src/components/Settings.vue
bash scripts/build_frontend.sh
```

---

## 7. 克制清单（MUST 维护）

| 项 | 约束 |
|---|---|
| ❌ 引入新字体文件 | 仅使用系统字体栈 |
| ❌ 添加新 npm 依赖 | 零运行时新增（纯 CSS） |
| ❌ 复制官方素材 | 全部自绘（八角星/锯齿/撕裂） |
| ❌ 在 <12px 文字用绯红前景 | 强调只能走 `--sub` 灰阶或 `--p5-white` |
| ❌ 给已交付组件加新 `<button class="...">` 触发业务变更 | 仅样式重映射，**不增加/删除任何功能入口** |
| ❌ 把 P5 视觉强加到非 P5 主题 | 所有 P5 规则严格作用域 `html.t-p5` 下 |

---

## 8. 验证（Playwright · 2026-08-14）

`D:/AI/manju_agent/.tmp/p5_verify.mjs` 12/12 PASS：

1. ✅ html 含 `t-p5` 预设类（默认启用）
2. ✅ `--p5-red` 注入 = `#E4002B`
3. ✅ `--p5-ink` 注入 = `#0B0B0B`
4. ✅ `--radius` 归零 = `0`
5. ✅ body 背景 = `rgb(11, 11, 11)`
6. ✅ el-button--primary 圆角 = `0px`
7. ✅ el-button--primary 背景 = `rgb(228, 0, 43)`
8. ✅ 原生 .btn 圆角 = `0px`
9. ✅ .page-head h1 斜切 transform
10. ✅ .page-head h1 颜色 = 纸白
11. ✅ 路由切换无新增运行时错误
12. ✅ 无 pageerror / console error

构建：`bash scripts/build_frontend.sh` 成功（1688 modules，CSS hash `index-CdEWsbvh.css` 615.36 kB / gzip 89.74 kB）。
截图归档：`D:/AI/manju_agent/.tmp/p5_home.png` / `p5_settings.png`。