#!/usr/bin/env python3
"""Code-size gate: reject code files larger than --max-kb (default 20KB).
Skips lockfiles and auto-generated / bundled artifacts.
Reads a list of changed file paths (one per line) from --files.
Exit 1 if any code file exceeds the limit.
"""
import sys, os, argparse, re

DEFAULT_MAX_KB = 20

# Always skipped: lockfiles + auto-generated / bundled output.
SKIP = [
    r'package-lock\.json$', r'yarn\.lock$', r'pnpm-lock\.yaml$', r'npm-shrinkwrap\.json$',
    r'poetry\.lock$', r'Pipfile\.lock$', r'composer\.lock$', r'Gemfile\.lock$',
    r'Cargo\.lock$', r'go\.sum$', r'bun\.lockb$',
    r'\.min\.(js|css)$', r'min\.js$', r'\.bundle\.(js|css)$',
    r'\.generated\.[^/]+$', r'\.gen\.[^/]+$', r'\.pb\.(go|rb|py|java|cc|h)$',
    r'\.d\.ts$', r'\.map$',
    r'(^|/)vendor/', r'(^|/)node_modules/', r'(^|/)dist/', r'(^|/)build/',
    r'__pycache__/', r'\.pyc$',
]
# Source-code extensions subject to the size gate.
CODE_EXTS = {
    'py','js','jsx','ts','tsx','vue','go','rs','java','c','cpp','cc','cxx','h','hpp',
    'cs','rb','php','swift','kt','scala','sh','bash','sql','html','htm','css','scss',
    'less','yaml','yml','toml','xml','ini','cfg','lua','r','pl','ex','exs',
}
SKIP_RE = [re.compile(p) for p in SKIP]


def is_skip(path):
    return any(rx.search(path) for rx in SKIP_RE)


def has_code_ext(path):
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    return ext in CODE_EXTS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', required=True, help='file with one path per line')
    ap.add_argument('--max-kb', type=int, default=DEFAULT_MAX_KB)
    args = ap.parse_args()
    max_bytes = args.max_kb * 1024

    violations = []
    with open(args.files, encoding='utf-8') as f:
        for line in f:
            p = line.strip()
            if not p:
                continue
            if is_skip(p):
                continue
            if not has_code_ext(p):
                continue
            if not os.path.exists(p):
                continue
            sz = os.path.getsize(p)
            if sz > max_bytes:
                violations.append((p, sz))

    if violations:
        print("CODE-SIZE-GATE: FAILED — code files over %dKB:" % args.max_kb)
        for p, sz in violations:
            print("  BLOCKED %s (%d KB)" % (p, sz // 1024))
        print("\nFix: split by feature (按功能拆分).")
        print("Bypass: git commit --no-verify (local) or skip CI (not recommended).")
        sys.exit(1)
    print("CODE-SIZE-GATE: PASSED")
    sys.exit(0)


if __name__ == '__main__':
    main()
