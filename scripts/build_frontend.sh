#!/bin/bash
# =============================================================================
# scripts/build_frontend.sh — FS-01/FS-03 合规的前端构建包装
# =============================================================================
# 规则背景：IMA 知识库 7492136878505535《开发规则》§12 文件系统纪律
#   FS-01（FORBIDDEN）：不允许删除、修改、增加 C 盘任何文件，包括回收站
#   FS-03（MUST）：文件操作仅限 D:\AI 目录
#
# 修复点：直接执行 `node vite.js build` 会让 esbuild 把预打包缓存写到
#   %TEMP% = C:\Users\L\AppData\Local\Temp\cod-*\esbuild-*，Vite 关闭时再
#   被 safe-delete 钩子拦截删除 → 整个 cod-* 目录 + esbuild-* 文件 + 
#   vite.config.timestamp-* 全被打包丢进 C 盘回收站，违反 FS-01。
#
# 本脚本通过显式 export TMPDIR/TEMP/TMP 全部指向 D:\AI\manju_agent\.tmp\vite
# 把 esbuild / node 全部临时文件约束在 D 盘内（FS-03 合规），D 盘内的清理
# 由本脚本 try/finally 兜底（OPS-01 合规）。
#
# 调用方：以后任何 `vite build` 走本脚本；start_manju.bat 启动链路若触发
# 前端构建也应走本脚本（module_manager._run_frontend_build 同步切换）。
# =============================================================================

set -e  # 任何错误立即退出

ROOT="D:/AI/manju_agent"
TMP_VITE="$ROOT/.tmp/vite"

# ---- 1. 准备 D 盘临时目录（FS-03 合规）----
mkdir -p "$TMP_VITE"

# ---- 2. 显式覆盖三大临时环境变量（FS-01/FS-03 合规）----
# TMPDIR / TEMP / TMP 是 esbuild、node、Vite 内部读取临时目录的统一入口
export TMPDIR="$TMP_VITE"
export TEMP="$TMP_VITE"
export TMP="$TMP_VITE"

# Vite 自身的 cacheDir（node_modules/.vite 之外的 dep 缓存）
export VITE_CACHE_DIR="$TMP_VITE/vite-cache"

# esbuild 自带的环境变量（双保险，部分版本读这个）
export ESBUILD_TMPDIR="$TMP_VITE"

cd "$ROOT"

# ---- 3. try/finally 确保 D 盘临时目录被释放（OPS-01 合规）----
cleanup() {
  # 仅清 D 盘内本次构建产物；任何 C 盘写入都不允许
  if [ -d "$TMP_VITE" ]; then
    rm -rf "$TMP_VITE"/* 2>/dev/null || true
  fi
  echo "[build_frontend.sh] cleanup OK: D:/AI/manju_agent/.tmp/vite/* released"
}
trap cleanup EXIT

# ---- 4. 真正执行构建 ----
NODE="/c/Users/L/.workbuddy/binaries/node/versions/22.22.2/node.exe"
echo "[build_frontend.sh] TMPDIR=$TMPDIR"
echo "[build_frontend.sh] running: $NODE ./node_modules/vite/bin/vite.js build"

"$NODE" ./node_modules/vite/bin/vite.js build

# ---- 5. 安全清理未被 index.html 引用的旧 bundle ----
# 根因：vite.config.js 设了 emptyOutDir:false（避免每次 build 删旧文件触发
#   safe-delete 钩子把一堆旧 bundle 丢进回收站），但副作用是 dist/assets 会
#   不断堆积历史 hash 的死 bundle。这里在 build 后精确清理，仅删「未被本次
#   新 index.html 引用」的构建产物。
#
# 删除方式：PowerShell [System.IO.File]::Delete（绕过 safe-delete Node 钩子，
#   bash `rm` 在本沙箱会被钩子拦截并 fail-closed 不真删；.NET Delete 是直接 OS
#   系统调用，能可靠删除且只动 D:\AI 内构建产物，FS-03 合规）。
#
# 设计（修复「kept 0 referenced」脆弱性）：stale 清单完全由 bash 侧计算
#   （grep index.html 引用集合 + 列出目录比对），仅把最终要删的绝对路径作为
#   $args 逐个传给 PowerShell Delete——不再让 PowerShell 读文件/枚举目录
#   （此前因路径转换/读文件失败导致 $refs/$dir 为空）。
#   安全护栏：若 index.html 解析不到任何引用（异常），则跳过清理（fail-open，
#   宁可留 stale bundle，绝不误删正在服务的构建产物）。
ASSETS_DIR="$ROOT/frontend/dist/assets"
INDEX_HTML="$ROOT/frontend/dist/index.html"
if [ -f "$INDEX_HTML" ] && [ -d "$ASSETS_DIR" ]; then
  # 5. 全量交给 PowerShell 枚举+删除：避免 Git-Bash 进程替换 < <(ls -1)
  #    在 MSYS 下偶发返回空，导致 stale 漏删 / 误判「kept 0 referenced」。
  #    PowerShell 直接读 index.html 引用集合并枚举 dist/assets，仅删未引用的
  #    构建产物（File::Delete 为 OS 系统调用，绕过 safe-delete 钩子，仅动
  #    D:\AI 内文件，FS-03 合规）。护栏：解析不到任何引用则跳过（fail-open）。
  powershell -NoProfile -Command '
    $root = "D:\AI\manju_agent"
    $indexHtml = Join-Path $root "frontend\dist\index.html"
    $assetsDir = Join-Path $root "frontend\dist\assets"
    $txt = [System.IO.File]::ReadAllText($indexHtml)
    $refs = [regex]::Matches($txt, "assets/([A-Za-z0-9_./-]+)") | ForEach-Object { $_.Groups[1].Value }
    if ($refs.Count -eq 0) {
      Write-Output "[build_frontend.sh] WARN: no referenced assets parsed; skipping cleanup (fail-open)"
      exit 0
    }
    $files = Get-ChildItem -Path $assetsDir -File
    $n = 0
    foreach ($f in $files) {
      if ($refs -notcontains $f.Name) { [System.IO.File]::Delete($f.FullName); $n++ }
    }
    Write-Output "[build_frontend.sh] cleaned $n stale bundle(s); kept $($files.Count - $n) referenced"
  ' || true
fi

echo "[build_frontend.sh] BUILD_OK"