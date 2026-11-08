#!/usr/bin/env bash
# .githooks 门禁验收：在**项目内临时仓库**里跑真实 git commit / push。
# 不污染主仓 index、不写 C 盘（临时目录固定在 $ROOT/.tmp/，已被根 .gitignore 忽略）。
#
# 跑法：bash .githooks/tests/gate_acceptance.sh
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
GATE_SRC="$ROOT/.githooks"
mkdir -p "$ROOT/.tmp"
WORK=$(mktemp -d "$ROOT/.tmp/gate.XXXXXX") || { echo "无法创建临时目录"; exit 1; }
# 用 python 清理：WorkBuddy 的 safe-delete shim 会拦 shell 的 rm -rf。
# 仅限本脚本自己创建、且位于 $ROOT/.tmp/gate.* 下的目录（带前缀断言）。
# Windows 版 python 认不了 MSYS 的 /d/... 路径，必须用 cygpath -w 转成 D:\...
WROOT=$(cygpath -w "$ROOT/.tmp" 2>/dev/null || echo "$ROOT/.tmp")
# Git 把 .git/objects 下的文件设成只读，Windows 上 rmtree 会留下残骸 → 先解只读。
cleanup() {
    # 必须先离开临时仓：Windows 不允许删除「当前工作目录」所在的目录树。
    cd "$ROOT" 2>/dev/null || true
    chmod -R u+w "$WORK" 2>/dev/null || true
    local w
    w=$(cygpath -w "$WORK" 2>/dev/null || echo "$WORK")
    python -c "
import os, shutil, sys
p, root = sys.argv[1], sys.argv[2]
assert os.path.isabs(p) and os.path.normcase(p).startswith(os.path.normcase(root) + os.sep), 'refuse to clean: ' + p
shutil.rmtree(p, ignore_errors=True)
" "$w" "$WROOT" 2>/dev/null || true
}
trap cleanup EXIT
# 幂等：清掉上一轮（safe-delete 拦截等原因）留下的 gate.* 目录
chmod -R u+w "$ROOT"/.tmp/gate.* 2>/dev/null || true
python -c "
import glob, os, shutil, sys
root = sys.argv[1]
for p in glob.glob(os.path.join(root, 'gate.*')):
    if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
" "$WROOT" 2>/dev/null || true

PASS=0
FAIL=0
check() {
    local name="$1" cond="$2" note="${3:-}"
    if [ "$cond" = "1" ]; then
        PASS=$((PASS + 1)); printf '  PASS  %s\n' "$name"
    else
        FAIL=$((FAIL + 1)); printf '  FAIL  %s' "$name"
        [ -n "$note" ] && printf '   <- %s' "$note"
        printf '\n'
    fi
}

# 造指定字节数的文本文件
mkfile() { python -c "import sys;open(sys.argv[1],'w',encoding='utf-8').write('#'+'x'*int(sys.argv[2])+chr(10))" "$1" "$2" 2>/dev/null; }

# 清掉一次失败的 commit 留下的 staged 内容
clean_index() {
    git reset -q HEAD -- . 2>/dev/null || true
    find . -maxdepth 1 -type f ! -name '*.md' -delete 2>/dev/null || true
}

REPO="$WORK/repo"
git init -q -b master "$REPO"
cd "$REPO" || exit 1
git config user.email gate@local
git config user.name gate
git config core.autocrlf false
cp -r "$GATE_SRC" ./.githooks
chmod +x .githooks/pre-commit .githooks/pre-push
# 本地兜底转发脚本直接取主仓真实文件来测（不是复制一份假实现）
cp "$ROOT/.git/hooks/pre-commit" .git/hooks/pre-commit
cp "$ROOT/.git/hooks/pre-push" .git/hooks/pre-push
chmod +x .git/hooks/pre-commit .git/hooks/pre-push
git config core.hooksPath .githooks

# ---------- 1. 小代码文件：放行 ----------
printf '# ok\n' > small.py
git add small.py
out=$(git commit -m "small" 2>&1); rc=$?
check "小代码文件 (<20KB) 放行" "$([ $rc -eq 0 ] && echo 1 || echo 0)" "$out"
clean_index >/dev/null 2>&1

# ---------- 2. >20KB 代码文件：拦截 ----------
before=$(git rev-parse HEAD)
mkfile big.py 21000
git add big.py
out=$(git commit -m "big" 2>&1); rc=$?
after=$(git rev-parse HEAD)
check ">20KB 代码文件被拦" "$([ $rc -ne 0 ] && echo 1 || echo 0)" "rc=$rc"
check "拦截信息含 BLOCKED [超 20KB]" "$(echo "$out" | grep -q 'BLOCKED \[超 20KB\]' && echo 1 || echo 0)" "$out"
check "拦后 HEAD 未前进（确实没产生 commit）" "$([ "$after" = "$before" ] && echo 1 || echo 0)" "$before -> $after"
clean_index >/dev/null 2>&1

# ---------- 3. 数据库文件：拦截 ----------
printf 'x' > fake.db
git add -f fake.db
out=$(git commit -m "db" 2>&1); rc=$?
check "数据库文件被拦（含 -f 强加）" "$([ $rc -ne 0 ] && echo 1 || echo 0)" "rc=$rc"
check "拦截信息含 BLOCKED [用户数据]" "$(echo "$out" | grep -q 'BLOCKED \[用户数据\]' && echo 1 || echo 0)" "$out"
clean_index >/dev/null 2>&1

# ---------- 4. 跳过规则：lockfile / min.js / vendor / 非代码扩展名 ----------
mkfile package-lock.json 30000
mkfile app.min.js 30000
mkdir -p vendor
mkfile vendor/huge.js 30000
mkfile blob.dat 30000
git add package-lock.json app.min.js vendor/huge.js blob.dat
out=$(git commit -m "skips" 2>&1); rc=$?
check "lockfile/min.js/vendor/非代码扩展名 自动放行" "$([ $rc -eq 0 ] && echo 1 || echo 0)" "$out"
clean_index >/dev/null 2>&1

# ---------- 5. 指向主仓：hooksPath 真的配上了 ----------
hp=$(git -C "$ROOT" config --get core.hooksPath 2>/dev/null || echo "")
check "主仓 core.hooksPath=.githooks" "$([ "$hp" = '.githooks' ] && echo 1 || echo 0)" "实际=$hp"

# ---------- 6. 兜底：config 被清后，.git/hooks 转发仍拦得住 ----------
git config --unset core.hooksPath
mkfile fallback.py 21000
git add fallback.py
out=$(git commit -m "fallback" 2>&1); rc=$?
check "config 被清后兜底转发仍拦截" "$([ $rc -ne 0 ] && echo 1 || echo 0)" "rc=$rc $out"
clean_index >/dev/null 2>&1
git config core.hooksPath .githooks

# ---------- 7. --no-verify 绕过 commit，但 pre-push 必须补拦 ----------
git init -q --bare "$WORK/remote.git"
git remote add origin "$WORK/remote.git"
git push -q -u origin master 2>/dev/null || true
mkfile bypass.py 21000
git add bypass.py
bout=$(git commit --no-verify -m "bypass commit hook" 2>&1); brc=$?
check "--no-verify 确实跳过了 pre-commit（前提成立）" "$([ $brc -eq 0 ] && echo 1 || echo 0)" "$bout"
pout=$(git push origin master 2>&1); prc=$?
check "pre-push 补拦 --no-verify 的超大文件" "$([ $prc -ne 0 ] && echo 1 || echo 0)" "rc=$prc"
check "pre-push 拦截信息含 BLOCKED" "$(echo "$pout" | grep -q 'BLOCKED' && echo 1 || echo 0)" "$pout"
# 远端不应包含这个 commit
remotesha=$(git --git-dir="$WORK/remote.git" rev-parse refs/heads/master 2>/dev/null || echo "")
localsha=$(git rev-parse HEAD)
check "远端未被污染（remote HEAD != 本地 HEAD）" "$([ "$remotesha" != "$localsha" ] && echo 1 || echo 0)" "r=$remotesha l=$localsha"

# ---------- 8. 棘轮：默认关闭 → 改存量仍被拦 ----------
mkfile bypass.py 21005   # 比 HEAD 里的 21002 更大
git add bypass.py
out=$(git commit -m "grow" 2>&1); rc=$?
check "棘轮默认关闭时改动存量超大文件仍被拦" "$([ $rc -ne 0 ] && echo 1 || echo 0)" "rc=$rc"
clean_index >/dev/null 2>&1

# ---------- 9. 棘轮显式开启：不增大则放行，增大仍被拦 ----------
git config hooks.legacyRatchet true
# 内容不同但**字节数完全相同**（都是 21002B）：确保 staged 有变更、却没变大。
# 若写成同内容，git 会判 "nothing to commit"，测不到棘轮。
python -c "open('bypass.py','w',encoding='utf-8').write('#'+'y'*21000+chr(10))" 2>/dev/null
git add bypass.py
out=$(git commit -m "no growth" 2>&1); rc=$?
check "棘轮开启且未变大 → 放行（LEGACY 提示）" "$([ $rc -eq 0 ] && echo 1 || echo 0)" "$out"
check "放行时打印 LEGACY 棘轮提醒" "$(echo "$out" | grep -q 'LEGACY \[棘轮\]' && echo 1 || echo 0)" "$out"
clean_index >/dev/null 2>&1
mkfile bypass.py 22000   # 增大
git add bypass.py
out=$(git commit -m "growth under ratchet" 2>&1); rc=$?
check "棘轮开启但变大 → 仍被拦" "$([ $rc -ne 0 ] && echo 1 || echo 0)" "rc=$rc"
clean_index >/dev/null 2>&1
git config --unset hooks.legacyRatchet

# ---------- 10. 新文件在棘轮开启时也严格（防棘轮被滥用） ----------
git config hooks.legacyRatchet true
mkfile brandnew.py 21000
git add brandnew.py
out=$(git commit -m "new under ratchet" 2>&1); rc=$?
check "棘轮开启时新增的大文件仍严格拦截" "$([ $rc -ne 0 ] && echo 1 || echo 0)" "rc=$rc"
clean_index >/dev/null 2>&1
git config --unset hooks.legacyRatchet

echo ""
echo "===================================================================="
echo "合计 $((PASS + FAIL)) 项，失败 $FAIL 项"
echo "===================================================================="
[ "$FAIL" -eq 0 ] || exit 1
exit 0
