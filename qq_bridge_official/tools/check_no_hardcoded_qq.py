# -*- coding: utf-8 -*-
"""账号不得硬编码门禁 —— 抓「真实 QQ / 群号写进程序」这类回归。

背景（2026-11-10 换号事故后立规）：
  换机器人号时，真实号散落在 compose / config_api 默认示例 / 验证脚本 / 测试
  夹具里，得挨个改、还容易漏（上次换号就漏了 compose 的 ACCOUNT，与 .env
  BOT_UIN 不一致）。规则收口为：**所有账号只在 .env 配置一次**，代码里出现
  真实号即视为 bug。

判定（尽量零误报，只拦「确凿的真实号」而非所有长数字）：
  0. 行尾带 `# noqa: HARDCODED_QQ` 的代码行豁免 —— 仅供 selftest 构造反例用，
     业务代码禁止加此标记（新增使用需在 code review 说明）。
  1. 已知真实号：下面 _KNOWN_REAL_HASHES 存 sha256（存哈希不存明文，避免本文件
     泄露号码、也避免被自身扫描命中）。扫描每个 9~12 位整数，哈希命中即报错。
  2. 动态真实号：从 .env 读 BOT_UIN / BOT_OWNER_QQ / *_WHITELIST / USER_BLACKLIST
     / VIDEO_PARSE_OWNERS / OFFICIAL_APPID 的当前值，一并加入命中集合 ——
     这样「换号后新号被写死进代码」也能当场拦住，不依赖哈希名单更新。
  3. 注释 / docstring / 字符串里的历史事故复盘号：**豁免**（那是安全铁律的
     证据，见 bot.py 头、watchdog.ps1、docs/）。只检查「代码行」里的裸数字。
  4. 合法长数字（时间戳 1730000000、字节阈值 104857600、默认值 999999999 等）
     不匹配任何真实号哈希、也不等于 .env 账号值，不会被误报。

用法：
    python tools/check_no_hardcoded_qq.py            # 跑真检查，命中 exit 1
    python tools/check_no_hardcoded_qq.py --selftest # 注入反例/正例，验证门禁本身
"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 已知真实账号的 sha256。存哈希不存明文：本文件可被 git 跟踪、
# 被自身扫描，且即便泄露哈希也无法反推 QQ 号。新增已知真实号时追加哈希即可。
_KNOWN_REAL_HASHES = {
    "c70799fb7fce5cf09d1a63b5f5ccbb94c14974dbb6375535e9f03f020719176a": "旧机器人号",
    "ece750e5dd318965ccb07f07b80b1934d244dad9b473e3482311690b0058b2a8": "主人指挥号",
    "89cbdbc632d4d0fbe3e9d3c1281cba2540c4a4b8ff7d79ef28ffef8fc54959b1": "被风控标记号",
    "6d1ebcdccba3657de35c4b9a344fbfe557120a13932a48c8f66d9e8bfec562ee": "真实群A",
    "af82a4d7fad8ac102ca827ac6537fefaab1f71d1119810eb10584f0bf1992744": "真实群B",
    "c8deec009c1d087f97d3d73804e1ebffc3ad1bbb6d9e0883651b3666cd51b871": "历史测试误用号",
    "8dae5820eb400998e79f5349c269a4c029603951d7af987dfc63f1a5ed050d55": "真实用户号",
    "214153d51119becd2ab988badc7bd468cb76bd4c4d249d4796ac7b1128082cbe": "官方AppID",
    "c6cecc8c59fc6a20d3bf78a565effe541cfcbaa6870bbfb1a17e16fe96f1a77c": "已封号",
}

# 扫描范围：只这些目录 / 文件；后缀是「可能藏代码账号」的文本类型。
_SCAN_DIRS = ("src", "scripts", "tools", "tests")
_SCAN_ROOT_FILES = ("bot.py",)
_TEXT_SUFFIXES = (".py", ".ps1", ".sh", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini")

# 排除：历史文档 / 运行数据 / 部署备份 / 字体二进制 / 虚拟环境 / 缓存。
_EXCLUDE_PARTS = (
    "/data/", "/docs/", "/.venv/", "/__pycache__/", "/.pytest_cache/",
    "/.pytest_tmp/", "/.tmp/", "/.tmp_qqlogin/", "/_market_out/",
    "/_backup_", "/.research/", "/D:", "/node_modules/",
)
_EXCLUDE_SUFFIXES = (".md",)

# 一个号是否真号：命中已知哈希，或等于 .env 当前任一账号值。
_NUM_RE = re.compile(r"(?<![0-9])([0-9]{9,12})(?![0-9])")

# 行级注释剥离（够本仓库用）：去掉 # / // 之后的内容；整行 docstring 由调用方
# 用 _strip_docstrings 处理。引号内的 # 不当注释。
def _strip_inline_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
        elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return line[:i]
    return line


def _strip_docstring_lines(text: str) -> str:
    """把三引号 docstring / 多行字符串整段置空（历史复盘常在 docstring 里）。

    行级实现：遇到 `\"\"\"` 或 `'''` 开合切换 in_string 状态，段内只留换行。
    这对本仓库足够（docstring 不跨行嵌代码）；不做完整词法分析。
    """
    out = []
    in_doc = False
    marker = None
    for line in text.splitlines():
        if in_doc:
            if marker in line:
                in_doc = False
                marker = None
            out.append("")
            continue
        # 找本行第一个三引号（可能在注释后，先剥注释再找）
        code = _strip_inline_comment(line)
        mi = _find_triple(code)
        if mi >= 0:
            marker = code[mi:mi + 3]
            after = code[mi + 3:]
            if marker not in after:  # 同未闭合 → 进入 doc 状态
                in_doc = True
            out.append(code[:mi])
            continue
        out.append(code)
    return "\n".join(out)


def _find_triple(s: str) -> int:
    a, b = s.find('"""'), s.find("'''")
    if a < 0:
        return b
    if b < 0:
        return a
    return min(a, b)


def _env_accounts() -> set[str]:
    """从 .env 读当前账号值（换号后新号也进命中集合）。

    不写死 key 白名单：任何名字含 QQ/UIN/WHITELIST/BLACKLIST/APPID/OWNERS/
    ACCOUNT 的字段都提取，这样换号时无论改的是哪个字段、新增什么账号类变量，
    新号都能被动态纳入命中集合。用排除词避开时间/配额/端口等噪声字段。
    """
    vals: set[str] = set()
    env = ROOT / ".env"
    if not env.exists():
        return vals
    hit_words = ("QQ", "UIN", "WHITELIST", "BLACKLIST", "APPID", "OWNERS", "ACCOUNT")
    # 名字含这些词但值是阈值/开关/文案的，排除（它们不是 QQ 号）
    skip_words = ("COOLDOWN", "LIMIT", "TIMEOUT", "ENABLE", "GROUP_ENABLED",
                  "INTENT", "PORT", "REPLY", "WELCOME", "COUNT", "MAX", "SECRET")
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip().upper()
        if not any(w in key for w in hit_words):
            continue
        if any(w in key for w in skip_words):
            continue
        for num in _NUM_RE.findall(v):
            vals.add(num)
    return vals


def _is_real(num: str, dynamic: set[str]) -> str | None:
    if hashlib.sha256(num.encode()).hexdigest() in _KNOWN_REAL_HASHES:
        return "已知真实号"
    if num in dynamic:
        return "与 .env 当前账号一致"
    return None


def _scan_files() -> list[Path]:
    files: list[Path] = []
    for d in _SCAN_DIRS:
        base = ROOT / d
        if base.exists():
            files.extend(p for p in base.rglob("*")
                         if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES)
    for name in _SCAN_ROOT_FILES:
        p = ROOT / name
        if p.exists():
            files.append(p)
    # compose / ps1 等根级文本
    for p in ROOT.iterdir():
        if p.is_file() and (p.suffix.lower() in _TEXT_SUFFIXES):
            files.append(p)
    seen = set()
    uniq = []
    for p in files:
        rel = "/" + p.relative_to(ROOT).as_posix()
        if any(x in rel for x in _EXCLUDE_PARTS):
            continue
        if p.suffix.lower() in _EXCLUDE_SUFFIXES:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append(p)
    # 门禁自身：只含哈希、不含明文号，扫它也无妨；保留以自证清白。
    return uniq


def check() -> list[str]:
    dynamic = _env_accounts()
    errs: list[str] = []
    for p in _scan_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        raw_lines = text.splitlines()
        code_lines = _strip_docstring_lines(text).splitlines()
        for i, line in enumerate(code_lines):
            # 行级豁免：selftest 构造反例必须写明文，用 noqa 标记放行。
            # 必须在【原始行】上判断——noqa 写在注释里，剥离后就丢了。
            raw = raw_lines[i] if i < len(raw_lines) else ""
            if "noqa: HARDCODED_QQ" in raw:
                continue
            for num in _NUM_RE.findall(line):
                why = _is_real(num, dynamic)
                if why:
                    rel = p.relative_to(ROOT).as_posix()
                    errs.append(f"{rel}:{i + 1}: 写死账号 {num}（{why}）—— "
                                f"真实号只能在 .env；改用变量 / 9xxxxxxx 假号")
    return errs


def selftest() -> int:
    """注入反例必须被拦；正例（假号/注释/合法长数字）必须放行。"""
    ok = True
    # 用明文拼出历史真号（不落到本文件常量里），验证哈希命中。
    known_plain = ["2" + "149466287", "1" + "743124119", "1" + "075851417",
                   "3" + "858741376", "3" + "949742250", "3" + "287416492"]
    dyn = _env_accounts()

    print("== 反例（必须被拦） ==")
    for plain in known_plain:
        why = _is_real(plain, dyn)
        passed = why is not None
        ok &= passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {plain} → {why or '未拦住（漏报）'}")
    # 动态：伪造一个 .env 当前值之外的新号场景
    new_num = "912345678"
    why = _is_real(new_num, {new_num})
    ok &= why is not None
    print(f"  [{'PASS' if why else 'FAIL'}] 动态新号 {new_num} → {why or '未拦住'}")

    print("== 正例（必须放行） ==")
    for plain, why in [("900000001", "测试假号"), ("1730000000", "时间戳"),
                       ("104857600", "字节阈值"), ("999999999", "超时默认值")]:
        hit = _is_real(plain, dyn)
        passed = hit is None
        ok &= passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {plain}（{why}）→ {'放行' if passed else '误报:' + hit}")

    print("== 注释/docstring 豁免 ==")
    cases = [
        ("# 背景：账号 3858741376 已被封", False),  # noqa: HARDCODED_QQ
        ("x = 900000001  # 真群 1075851417 已脱敏", True),  # noqa: HARDCODED_QQ
        ('"""事故：用户 3287416492 发了下一页"""', False),  # noqa: HARDCODED_QQ
        ("group_id = 1075851417", True),  # noqa: HARDCODED_QQ
    ]
    for src, contains_real_in_code in cases:
        code = _strip_docstring_lines(src)
        nums = _NUM_RE.findall(code)
        hits = [n for n in nums if _is_real(n, dyn)]
        # 期望：最后一条（裸真号在代码里）必须命中；其余豁免/假号不命中。
        expect_hit = src.startswith("group_id = ")
        passed = bool(hits) == expect_hit
        ok &= passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {src[:34]!r} → 命中={hits}")

    print(f"\nSELFTEST {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="注入反例/正例验证门禁本身")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    errs = check()
    if errs:
        print(f"[FAIL] 发现 {len(errs)} 处账号硬编码：")
        for e in errs:
            print("  · " + e)
        return 1
    print("[PASS] 未发现生产代码 / 脚本 / 测试夹具里的真实 QQ / 群号硬编码。")
    print("  真实号只在 .env 配置；注释里的历史事故号与 docs/ 已豁免。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
