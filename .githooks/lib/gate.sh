#!/usr/bin/env bash
# .githooks/lib/gate.sh —— pre-commit / pre-push 共用的门禁判定逻辑。
#
# 两道门，一个来源：
#   1) 数据库 / 用户数据文件（*.db* / *.sqlite*）一律拒绝 —— 合并自 2026-08-08
#      的旧 .git/hooks/pre-commit；.gitignore 已排除，这里防的是 -f 强加。
#   2) 代码文件 >20KB 一律拒绝 —— 保证代码文件方便阅读浏览（用户 2026-11-08
#      明确要求「必须生效」）。
#
# 为什么抽成共享库：pre-commit 可被 `git commit --no-verify` 绕过，
# 所以 pre-push 必须复用**完全相同**的判定，不能各写一份（会漂移）。
#
# 本文件不执行任何动作，只提供函数与常量，由调用方 source。

GATE_MAX_BYTES=20480  # 20KB

# 永远跳过：lockfile + 自动生成的 / 打包产物。
# 用 read -ra 拆成数组而不是 `for x in $VAR` —— 后者会做 pathname
# expansion，仓库里恰好有 *.min.js 之类文件名时会被当前目录内容污染。
read -r -a GATE_SKIP_PATTERNS <<'EOF'
package-lock\.json$ yarn\.lock$ pnpm-lock\.yaml$ npm-shrinkwrap\.json$ poetry\.lock$ Pipfile\.lock$ composer\.lock$ Gemfile\.lock$ Cargo\.lock$ go\.sum$ bun\.lockb$ \.min\.(js|css)$ -min\.js$ \.bundle\.(js|css)$ \.generated\.[^/]+$ \.gen\.[^/]+$ \.pb\.(go|rb|py|java|cc|h)$ \.d\.ts$ \.map$ (^|/)vendor/ (^|/)node_modules/ (^|/)dist/ (^|/)build/ __pycache__/ \.pyc$
EOF

read -r -a GATE_CODE_EXTS <<'EOF'
py js jsx ts tsx vue go rs java c cpp cc cxx h hpp cs rb php swift kt scala sh bash sql html htm css scss less yaml yml toml xml ini cfg lua r pl ex exs
EOF

# 数据库 / 会话 / 用户数据：与旧 .git/hooks/pre-commit 完全同口径
read -r -a GATE_DB_SUFFIXES <<'EOF'
.db .db-wal .db-shm .sqlite .sqlite3
EOF

gate_is_skip() {
    local p="$1" pat
    for pat in "${GATE_SKIP_PATTERNS[@]}"; do
        if printf '%s' "$p" | grep -Eq "$pat"; then return 0; fi
    done
    return 1
}

gate_has_code_ext() {
    local ext="${1##*.}" e
    ext=$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')
    for e in "${GATE_CODE_EXTS[@]}"; do
        [ "$ext" = "$e" ] && return 0
    done
    return 1
}

gate_is_db() {
    local p="$1" sfx
    for sfx in "${GATE_DB_SUFFIXES[@]}"; do
        case "$p" in
            *"$sfx") return 0 ;;
        esac
    done
    return 1
}

gate_blob_size() {
    # $1 = 任意 git 可解析的 <rev>:<path> 或 :<path>（index）
    git cat-file -s "$1" 2>/dev/null || echo 0
}

# 过渡开关：默认**关闭**，即 20KB 上限对任何 staged 代码文件严格生效。
# 显式开启后，只对「HEAD 里本来就 >20KB 的存量文件」放宽为
# 「本次不得比 HEAD 更大」（棘轮），新文件仍然严格。
# 备选仅以用户显式配置存在，绝不自动兜底（项目「禁止降级/兜底」红线）。
gate_ratchet_on() {
    [ "${GITHOOKS_LEGACY_RATCHET:-0}" = "1" ] && return 0
    [ "$(git config --bool hooks.legacyRatchet 2>/dev/null || echo false)" = "true" ] && return 0
    return 1
}

# 判定单个文件。
#   $1 = 路径
#   $2 = 本次要提交的 blob 大小（字节）
#   $3 = 用于比较的基准 ref；空字符串 = 不比较（pre-push 用远端 sha）
# 返回 0 = 放行（可能打印 LEGACY 提醒），1 = 拦截（打印 BLOCKED 行）。
gate_verdict() {
    local path="$1" size="$2" base="${3:-}"
    local kb=$(( size / 1024 ))

    if gate_is_db "$path"; then
        echo "  BLOCKED [用户数据]: ${path} —— 数据库/会话文件不得入库（含 -f 强加）"
        return 1
    fi
    if gate_is_skip "$path" || ! gate_has_code_ext "$path"; then
        return 0
    fi
    if [ "$size" -le "$GATE_MAX_BYTES" ]; then
        return 0
    fi

    if gate_ratchet_on && [ -n "$base" ]; then
        local old
        old=$(gate_blob_size "${base}:${path}")
        if [ "$old" -gt "$GATE_MAX_BYTES" ] && [ "$size" -le "$old" ]; then
            echo "  LEGACY [棘轮]: ${path} ${kb}KB —— 存量超大文件，本次未变大，放行；请按功能拆分"
            return 0
        fi
    fi

    echo "  BLOCKED [超 20KB]: ${path} (${kb}KB) —— 代码文件上限 ${GATE_MAX_BYTES}B"
    return 1
}

gate_fail_message() {
    echo ""
    echo "提交/推送被拦截：检测到数据库文件或超过 20KB 的代码文件。"
    echo "目的：保证代码文件方便阅读浏览。请按功能拆分（split by feature）："
    echo "  - 把大文件拆成多个小模块，每个 < 20KB；"
    echo "  - 纯数据 / 配置 / 生成产物放到单独文件，跳过规则会自动放行；"
    echo "  - lockfile 与构建产物（vendor/ dist/ build/ *.min.js）已自动跳过。"
    echo ""
    echo "存量超大文件需要继续修改时，可显式开启棘轮（只禁止继续变大，新文件仍严格）："
    echo "  git config hooks.legacyRatchet true     # 或 GITHOOKS_LEGACY_RATCHET=1"
    echo ""
    echo "⚠️ --no-verify 只能跳过 pre-commit，无法跳过 pre-push（推送前会再拦一次）。"
}
