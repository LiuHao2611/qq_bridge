# .githooks —— 仓库门禁

**目的**：保证代码文件方便阅读浏览（用户 2026-11-08 定 20KB 上限、2026-11-11 按「必须符合 200KB 文件」放宽到 200KB，纪律本身仍「必须生效」）。

## 启用（每个 clone 都要跑一次）

`core.hooksPath` 存在 `.git/config`，**不会随仓库传播**，所以新 clone / 新工作树必须手动执行一次：

**本机（有 22 个 worktree）必须用绝对路径**：

```bash
git config core.hooksPath "D:/AI/manju_agent/.githooks"
```

原因：`core.hooksPath` 是**所有 worktree 共享**的配置，而相对 `.githooks` 会按
**各自 worktree 的根**解析。指向旧 commit 的 worktree（例如当前
`master-e6caec37` → `06ec597`）里根本没有 `.githooks/` 目录，
Git 找不到 hook 会**静默跳过**，门禁在那里等于不存在。
绝对路径让全部 worktree 共用主仓 master 上的这一份，判定仍然是各 worktree
自己的 index（Git 运行 hook 时会设好 `GIT_DIR`/`GIT_WORK_TREE`），不会串味。

**其它机器 / 单一工作树的 clone** 用相对路径即可：

```bash
git config core.hooksPath .githooks
```

确认：

```bash
git config --get core.hooksPath
```

`D:/AI/manju_agent` 主仓已于 2026-11-08 配成绝对路径。本机 `.git/hooks/` 另放了
转发脚本作兜底：即使 `core.hooksPath` 被清掉，pre-commit / pre-push 仍会转到这里，
且对旧 commit 的 worktree 会回落到主仓绝对路径，**不会静默失效**。
其它机器若没有这份兜底，未执行上面的 `git config` 就等于没有门禁。

## 两道门（判定逻辑单一来源：`lib/gate.sh`）

| 门 | 规则 | 来源 |
|---|---|---|
| 用户数据 | `*.db` / `*.db-wal` / `*.db-shm` / `*.sqlite` / `*.sqlite3` 一律拒绝，含 `git add -f` 强加 | 合并自 2026-08-08 的旧 `.git/hooks/pre-commit` |
| 代码体积 | 受管代码文件 **> 204800B（200KB）** 一律拒绝 | 2026-11-08 强化，2026-11-11 放宽到 200KB |

适用扩展名：`py js jsx ts tsx vue go rs java c cpp cc cxx h hpp cs rb php swift kt scala sh bash sql html htm css scss less yaml yml toml xml ini cfg lua r pl ex exs`。

自动跳过（lockfile / 生成物 / 打包产物，不是人读的源码）：
`package-lock.json` `yarn.lock` `pnpm-lock.yaml` `npm-shrinkwrap.json` `poetry.lock` `Pipfile.lock`
`composer.lock` `Gemfile.lock` `Cargo.lock` `go.sum` `bun.lockb`、`*.min.js/css` `*-min.js`
`*.bundle.*` `*.generated.*` `*.gen.*` `*.pb.*` `*.d.ts` `*.map`、
`vendor/` `node_modules/` `dist/` `build/` `__pycache__/` `*.pyc`。

## 为什么有 pre-push

`git commit --no-verify` 能跳过 pre-commit。只靠 pre-commit，「必须生效」就是空话。
`pre-push` 对**本次推送引入的变更文件**用同一份 `lib/gate.sh` 再判一次，
所以绕过 commit 门也推不上去。两处共用同一份判定，不会漂移。

## 只拦「本次变更」，不拦历史存量

门禁只看 `git diff --cached`（pre-commit）与 `<remote-sha>..<local-sha>`（pre-push）里
**本次新增/修改**的文件。仓库里已存在的超大文件不会因为「存在」就被拒，
但**一旦有人改动并暂存它们，就会被拦**——这是刻意的：逼着按功能拆分。

2026-11-08 实测：受版本管理且 >20KB 的代码文件共 **84 个**，例如
`server/ai_local.py`(468KB)、`qq_bridge/src/plugins/qq_bridge/__init__.py`(382KB)、
`game_tools/warframe.py`(280KB)、`src/components/Assets.vue`(135KB)。
（`frontend/static/vendor/*.min.js` 等已由跳过规则放行，不在这 84 个的「需拆分」范围内。）
200KB 上限下其中多数已自动合规；仅少数真·巨型文件（如 `server/ai_local.py` 468KB）
仍需拆分或走下面两类 LEGACY 宽例。

### 两类合法宽例（均打印 `LEGACY` 行，绝不静默）

**1) 同源复制豁免（自动，2026-11-11）** —— 为「复制一个官方机器人」的最高要求服务：
`qq_bridge_official/<rel>` 是 `qq_bridge/<rel>` 的**完整独立副本**。副本里 2 个从原件
逐字节复制的业务大文件（`__init__.py` 378KB、`warframe.py` 280KB），原件在
`origin/master` 本就 >200KB 且已入库。它们不是新写的大文件，而是「复制」本身的产物，
拆分会破坏复制一致性。故当 base 里存在 `qq_bridge/<rel>` 原件、且副本大小
≤ 原件 + `GATE_LEGACY_COPY_DELTA`(16KB，容纳必要官方接线增量) 时，按 LEGACY 放行
并打印原件/副本字节证据。**借复制之名膨胀 >16KB、或没有同源原件的新大文件，一律照拦**
（自检第 11 节用真实 commit 注入验证，防开后门）。

**2) 棘轮（默认关闭，需显式开启）** —— 若某次任务必须先改动存量巨型文件、又来不及拆分：
**存量文件允许提交，但不得比 HEAD 版本更大；新文件仍然严格 ≤200KB。**

```bash
git config hooks.legacyRatchet true      # 或单次：GITHOOKS_LEGACY_RATCHET=1 git commit ...
git config --unset hooks.legacyRatchet   # 用完关掉
```

这是显式配置，**不是自动兜底**（项目「禁止降级/禁止兜底」红线）。
长期目标仍是把存量巨型文件按功能拆分到 200KB 以内。

### 性能注（2026-11-11）

`gate_is_skip` 原逐个 pattern fork 一次 `grep`，425 文件的副本分支会触发上万次进程、
Windows Git Bash 下 pre-push 直接超时 SIGTERM。已合并为**单次 `grep -E`**（`-e` 逐个声明
pattern，保留 `-min\.js$` 防选项坑），每文件只 fork 一次，语义不变（自检 29 项全过）。

## 自检

```bash
bash .githooks/tests/gate_acceptance.sh
```

脚本在**项目内的临时仓库**（不写 C 盘、不污染主仓）里造 >200KB / ≤200KB 代码文件 /
`.db` / lockfile / 小文件、以及同源复制件（同尺寸 / +8KB 接线 / 注入膨胀 >16KB /
无原件新大文件），逐一断言「该拦的拦、该放的放」，并验证 pre-push 能挡住
`--no-verify`、兜底转发在 config 被清后仍然生效。共 **29 项**，2026-11-11 全过。
