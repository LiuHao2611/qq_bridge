#!/usr/bin/env bash
# .githooks/lib/gate.sh —— pre-commit / pre-push 共用的门禁判定逻辑。
#
# 两道门，一个来源：
#   1) 数据库 / 用户数据文件（*.db* / *.sqlite*）一律拒绝 —— 合并自 2026-08-08
#      的旧 .git/hooks/pre-commit；.gitignore 已排除，这里防的是 -f 强加。
#   2) 代码文件 >200KB 一律拒绝 —— 保证代码文件方便阅读浏览。上限由用户
#      2026-11-08 定的 20KB，按用户 2026-11-11「必须符合 200KB 文件」放宽到
#      200KB（204800B），其余纪律不变。
#
# 同源复制豁免（2026-11-11）：官方机器人是 NapCat 的**完整独立副本**
#   （qq_bridge_official/ 复刻 qq_bridge/）。副本里有 2 个从原件逐字节复制的
#   业务大文件（__init__.py / warframe.py），原件在 origin/master 本就 >200KB
#   且已入库。它们不是新写的大文件，而是「复制」这一最高要求本身的产物，
#   拆分会破坏复制一致性。故对 ``qq_bridge_official/<rel>``：当 base 中存在
#   ``qq_bridge/<rel>`` 且副本大小 ≤ 原件 + GATE_LEGACY_COPY_DELTA 时，按
#   LEGACY 放行并打印原件/副本字节证据。真正新增的大文件、或借复制之名
#   膨胀超阈值的文件，一律照拦（不接受「任意 new 大文件」开后门）。
#
# 为什么抽成共享库：pre-commit 可被 `git commit --no-verify` 绕过，
# 所以 pre-push 必须复用**完全相同**的判定，不能各写一份（会漂移）。
#
# 本文件不执行任何动作，只提供函数与常量，由调用方 source。

GATE_MAX_BYTES=204800       # 200KB：受管代码文件上限（用户 2026-11-11 明示）
GATE_LEGACY_COPY_DELTA=16384  # 同源复制件允许的「官方接线」增量上限（16KB）

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
    local p="$1"
    # 一次性把全部 pattern 传给单个 grep（-e 逐个声明，避免 `-min\.js$`
    # 被当 max-count 选项）。原先逐 pattern fork 一次 grep：425 文件 ×
    # ~30 pattern ≈ 上万次进程，Windows Git Bash 下 pre-push 会直接超时
    # （2026-11-11 实测 SIGTERM）。合并后每文件只 fork 一次，语义不变。
    printf '%s' "$p" | grep -Eq "${_GREP_SKIP_ARGS[@]}"
}

# 预构造 gate_is_skip 的 grep -e 参数数组（source 时一次性，避免每次重建）。
_GREP_SKIP_ARGS=()
for _pat in "${GATE_SKIP_PATTERNS[@]}"; do
    _GREP_SKIP_ARGS+=(-e "$_pat")
done
unset _pat

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

# 过渡开关：默认**关闭**，即 200KB 上限对任何 staged 代码文件严格生效。
# 显式开启后，只对「HEAD 里本来就 >200KB 的存量文件」放宽为
# 「本次不得比 HEAD 更大」（棘轮），新文件仍然严格。
# 备选仅以用户显式配置存在，绝不自动兜底（项目「禁止降级/兜底」红线）。
gate_ratchet_on() {
    [ "${GITHOOKS_LEGACY_RATCHET:-0}" = "1" ] && return 0
    [ "$(git config --bool hooks.legacyRatchet 2>/dev/null || echo false)" = "true" ] && return 0
    return 1
}

# 同源复制豁免： qq_bridge_official/<rel> 是 qq_bridge/<rel> 的独立副本。
# 当 base 里存在原件、且副本大小 ≤ 原件 + GATE_LEGACY_COPY_DELTA 时，视为
# 「复制而非新写」的存量大文件，按 LEGACY 放行。
#   $1 = 副本路径  $2 = 副本大小  $3 = base ref
# 返回 0 = 是合法同源复制（放行）；1 = 否（继续走严格判定）。
gate_legacy_copy_original() {
    local path="$1" size="$2" base="${3:-}" rel original old
    [ -n "$base" ] || return 1
    case "$path" in
        qq_bridge_official/*) rel="${path#qq_bridge_official/}" ;;
        *) return 1 ;;
    esac
    original="qq_bridge/${rel}"
    old=$(gate_blob_size "${base}:${original}")
    [ "${old:-0}" -gt "$GATE_MAX_BYTES" ] || return 1   # 原件本身就是存量超大文件
    [ "$size" -le $(( old + GATE_LEGACY_COPY_DELTA )) ] || return 1  # 副本没借复制之名膨胀
    echo "  LEGACY [同源复制]: ${path} ${size}B ← 原件 ${original} ${old}B（副本独立、未膨胀，按用户「复制机器人」要求放行）"
    return 0
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

    # 同源复制豁免（官方副本复刻自原件的存量大文件）：先于严格拦截判定。
    if gate_legacy_copy_original "$path" "$size" "$base"; then
        return 0
    fi

    echo "  BLOCKED [超 200KB]: ${path} (${kb}KB) —— 代码文件上限 ${GATE_MAX_BYTES}B"
    return 1
}

gate_fail_message() {
    echo ""
    echo "提交/推送被拦截：检测到数据库文件或超过 200KB 的代码文件。"
    echo "目的：保证代码文件方便阅读浏览。请按功能拆分（split by feature）："
    echo "  - 把大文件拆成多个小模块，每个 < 200KB；"
    echo "  - 纯数据 / 配置 / 生成产物放到单独文件，跳过规则会自动放行；"
    echo "  - lockfile 与构建产物（vendor/ dist/ build/ *.min.js）已自动跳过。"
    echo ""
    echo "两类合法宽例（都会打印 LEGACY 行，不静默）："
    echo "  1) 存量超大文件继续修改且不变大：git config hooks.legacyRatchet true（或 GITHOOKS_LEGACY_RATCHET=1）；"
    echo "  2) qq_bridge_official/<rel> 独立副本复刻 qq_bridge/<rel>、且未比原件膨胀 >16KB：自动按同源复制放行。"
    echo ""
    echo "⚠️ --no-verify 只能跳过 pre-commit，无法跳过 pre-push（推送前会再拦一次）。"
}
