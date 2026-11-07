"""机器验收：自动重试登录是否真的被禁掉了。

查的是「运行时真实生效的那份 napcat.mjs」，不是仓库里的文件：
NapCat 的 napcat.mjs 是 entrypoint 从 NapCat.Shell.zip 解出来的，每次重建容器都会重新生成，
所以补丁必须打在容器里、且必须验证容器里。

六项不变量：
  1. 容器内有 /app/qqbridge_no_autologin.py（新镜像才带）
  2. napcat.mjs 里「二维码过期」分支已改（含新串）
  3. napcat.mjs 里「登录服务重连」分支已改（含新串）
  4. napcat.mjs 里两处旧串（会自动取码的原文）已不存在
  5. entrypoint.sh 里有闸口调用（否则重建容器后补丁不会重打）
  6. 容器 restart 策略是 no（否则 QQ 一退出，Docker 拉起容器=又登录一次）

用法：
    .venv\\Scripts\\python.exe scripts\\verify_no_autologin.py
    .venv\\Scripts\\python.exe scripts\\verify_no_autologin.py --container 其它容器名
退出码：0 = 全部 PASS；1 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "docker"))

from qqbridge_no_autologin import NEW_1, NEW_2, OLD_1, OLD_2  # noqa: E402

INSPECT_PY = r"""
import json, os
p = "/app/napcat/napcat.mjs"
try:
    src = open(p, encoding="utf-8").read()
    exists = True
except FileNotFoundError:
    src, exists = "", False
new1, new2, old1, old2 = json.loads(os.environ["QBGATE"])
print(json.dumps({
    "mjs_exists": exists,
    "helper_exists": os.path.isfile("/app/qqbridge_no_autologin.py"),
    "new1": new1 in src,
    "new2": new2 in src,
    "old1": old1 in src,
    "old2": old2 in src,
    "calls": src.count("getQRCodePicture()"),
}))
"""


def _docker(*args: str, stdin: str | None = None) -> str:
    r = subprocess.run(
        ["docker", *args], capture_output=True, text=True, input=stdin, timeout=120
    )
    if r.returncode != 0:
        raise RuntimeError("docker %s 失败：%s" % (" ".join(args), r.stderr.strip()[-500:]))
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default="qq-bridge-napcat")
    args = ap.parse_args()
    name = args.container

    env = json.dumps([NEW_1, NEW_2, OLD_1, OLD_2], ensure_ascii=False)
    out = _docker(
        "exec", "-i", "-e", "QBGATE=" + env, name, "python3", "-", stdin=INSPECT_PY
    )
    info = json.loads(out.strip().splitlines()[-1])

    entry = _docker("exec", name, "sh", "-c", "grep -c qqbridge_no_autologin.py /app/entrypoint.sh || true")
    entry_count = int(entry.strip() or 0)
    policy = _docker("inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}", name).strip()

    rows = [
        ("容器内有自动重登闸脚本", info["helper_exists"], True),
        ("napcat.mjs 已存在", info["mjs_exists"], True),
        ("二维码过期分支已改（不再自动换码）", info["new1"], True),
        ("重连分支已改（不再自动取码）", info["new2"], True),
        ("旧串【二维码过期自动取码】已消失", info["old1"], False),
        ("旧串【重连自动取码】已消失", info["old2"], False),
        ("entrypoint 含闸口调用", entry_count >= 1, True),
        ("restart 策略为 no", policy, "no"),
    ]

    fail = 0
    print("=" * 72)
    for label, actual, expect in rows:
        ok = actual == expect
        fail += 0 if ok else 1
        print("  %-5s %-38s actual=%-6r expect=%r"
              % ("PASS" if ok else "FAIL", label, actual, expect))
    print("-" * 72)
    print("  剩余 getQRCodePicture() 调用 %d 处（应全部属于「首次启动」或「人工刷新」路径）"
          % info["calls"])
    print("=" * 72)
    print("%d PASS / %d FAIL" % (len(rows) - fail, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
