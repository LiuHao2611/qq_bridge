# .githooks —— 仓库门禁

**目的**：保证代码文件方便阅读浏览（用户 2026-11-08 要求「20KB 上限必须生效」）。

## 启用（每个 clone 都要跑一次）

`core.hooksPath` 存在 `.git/config`，**不会随仓库传播**，所以新 clone / 新工作树必须手动执行一次：

```bash
git config core.hooksPath .githooks
```

确认：

```bash
git config --get core.hooksPath    # 应输出 .githooks
```

`D:/AI/manju_agent` 主仓已于 2026-11-08 配置。本机 `.git/hooks/` 另放了转发脚本作兜底：
即使 `core.hooksPath` 被清掉，pre-commit / pre-push 仍会转到这里，**不会静默失效**。
其它机器若没有这份兜底，未执行上面的 `git config` 就等于没有门禁。

## 两道门（判定逻辑单一来源：`lib/gate.sh`）

| 门 | 规则 | 来源 |
|---|---|---|
| 用户数据 | `*.db` / `*.db-wal` / `*.db-shm` / `*.sqlite` / `*.sqlite3` 一律拒绝，含 `git add -f` 强加 | 合并自 2026-08-08 的旧 `.git/hooks/pre-commit` |
| 代码体积 | 受管代码文件 **> 20480B（20KB）** 一律拒绝 | 2026-11-08 强化 |

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

### 过渡开关（默认关闭，需显式开启）

若某次任务必须先改动存量巨型文件、又来不及拆分，可开棘轮：
**存量文件允许提交，但不得比 HEAD 版本更大；新文件仍然严格 ≤20KB。**

```bash
git config hooks.legacyRatchet true      # 或单次：GITHOOKS_LEGACY_RATCHET=1 git commit ...
git config --unset hooks.legacyRatchet   # 用完关掉
```

这是显式配置，**不是自动兜底**（项目「禁止降级/禁止兜底」红线）。
长期目标仍是把 84 个存量文件按功能拆分到 20KB 以内。

## 自检

```bash
bash .githooks/tests/gate_acceptance.sh
```

脚本在**项目内的临时仓库**（不写 C 盘、不污染主仓）里造 >20KB 代码文件 /
`.db` / lockfile / 小文件，逐一断言「该拦的拦、该放的放」，并验证
pre-push 能挡住 `--no-verify`、兜底转发在 config 被清后仍然生效。
