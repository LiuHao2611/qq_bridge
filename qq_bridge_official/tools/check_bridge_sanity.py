# -*- coding: utf-8 -*-
"""qq_bridge 部署形态门禁 —— 抓「两条路线同时跑」这类静默故障。

三项检查，每一项都对得上一次真实踩过的坑：

  P1 端口单一所有者
     容器 qq-bridge 在跑时，8080 上不能再有宿主 python 监听。
     2026-11-05 事故：开机 VBS 拉起宿主 bot.py（13:15），Docker 稍后起容器
     抢走 8080（16:36）。Windows 的 SO_REUSEADDR 让两边都绑成功，宿主实例
     从此永久 ws_down 却一直占着端口，日志里还夹着它的 HTTP 400 噪音。

  P2 aria2 凭据可用
     .env 必须有非空 DL_ARIA2_SECRET，且真调一次 RPC 要 200。
     事故：secret 为空时 aria2 返 HTTP 400 Unauthorized，每 7 秒刷一条
     download_tick_failed，把真错误埋掉。

  P3 测试日志隔离
     pytest 必须由 tests/conftest.py 统一把 QQ_LOG_DIR 指向临时目录；
     独立脚本 src/plugins/*/test_e2e.py 自己改道，且 GID 不能在
     GROUP_WHITELIST 里（必须是 9xxxxxxx 假号，AGENTS.md §51）。
     事故：历史上有测试伪造真群号群事件，pytest 一跑就把假消息写进生产
     logs/qq_bridge.log，排查时误判成群聊总闸失效。

用法：
    python tools/check_bridge_sanity.py             # 跑真检查，失败 exit 1
    python tools/check_bridge_sanity.py --selftest  # 注入三类历史错误，必须全被拦下
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RPC = "http://127.0.0.1:6800/jsonrpc"


# ---------------------------------------------------------------- 纯检查函数
# 下面三个函数只吃数据、不碰系统。--selftest 靠它们验证「坏数据必须报错」。

def check_single_owner(listeners: list[tuple[str, int]],
                       proc_names: dict[int, str],
                       container_running: bool,
                       port: int = 8080) -> list[str]:
    """listeners: [(本地地址, pid)]，只关心通配绑定 0.0.0.0/[::]。"""
    errs: list[str] = []
    wildcard = sorted({pid for addr, pid in listeners
                       if addr.endswith(":%d" % port)
                       and (addr.startswith("0.0.0.0") or addr.startswith("[::]"))})
    native = [p for p in wildcard
              if "python" in proc_names.get(p, "").lower()]

    if len(native) >= 2:
        errs.append("8080 上有 %d 个 python 实例在监听（PID %s）——"
                    "重复启动，先 stop_bridge.ps1 再起"
                    % (len(native), ", ".join(map(str, native))))
    if container_running and native:
        errs.append("容器 qq-bridge 正在运行，但宿主 python（PID %s）也占着 8080。"
                    "两条路线互斥：要么 docker compose -f docker-compose.bridge.yml down "
                    "后走原生，要么 stop_bridge.ps1 后只留容器"
                    % ", ".join(map(str, native)))
    return errs


def check_aria2(secret: str, rpc: str, timeout: float = 8.0) -> list[str]:
    """真调一次 aria2.getGlobalStat，按响应判定凭据是否可用。"""
    if not (secret or "").strip():
        return ["DL_ARIA2_SECRET 为空 —— aria2 会返 HTTP 400 Unauthorized，"
                "每 7 秒刷一条 download_tick_failed。写进 .env"]
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "sanity",
        "method": "aria2.getGlobalStat",
        "params": ["token:%s" % secret.strip()],
    }).encode()
    req = urllib.request.Request(
        rpc, data=payload, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return ["aria2 RPC 返 HTTP %d（%s）—— 多半是 secret 不对" % (e.code, rpc)]
    except Exception as e:  # noqa: BLE001 — 连不上要如实说明
        return ["aria2 RPC 连不上（%s）：%s: %s"
                % (rpc, type(e).__name__, e)]
    if isinstance(body, dict) and body.get("error"):
        return ["aria2 拒绝请求：%s" % str(body["error"])[:160]]
    if not isinstance(body, dict) or "result" not in body:
        return ["aria2 返回缺少 result 字段：%s" % str(body)[:160]]
    return []


def check_test_isolation(cases: list[dict], whitelist: list[int]) -> list[str]:
    """cases: [{'path', 'kind': 'pytest'|'script', 'log_redirect': bool, 'gids': [int]}]"""
    errs: list[str] = []
    for c in cases:
        rel = Path(c["path"]).as_posix().replace(str(ROOT.as_posix()) + "/", "")
        if not c.get("log_redirect"):
            errs.append("%s 没有把 QQ_LOG_DIR 指向临时目录 —— "
                        "它伪造的事件会写进生产 logs/qq_bridge.log" % rel)
        if c.get("kind") == "script":
            gids = c.get("gids") or []
            if not gids:
                errs.append("%s 里找不到 `GID = <数字>`，无法确认它不是真群号" % rel)
            for gid in gids:
                if gid in whitelist:
                    errs.append("%s 的 GID=%d 出现在 GROUP_WHITELIST 里 —— "
                                "假事件和真群消息无法区分，改用 9xxxxxxx 假号" % (rel, gid))
    return errs


# ---------------------------------------------------------------- 取数
def _read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _listeners() -> list[tuple[str, int]]:
    out = subprocess.run(["netstat", "-ano"], capture_output=True,
                         text=True, errors="replace").stdout
    rows: list[tuple[str, int]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        try:
            rows.append((parts[1], int(parts[4])))
        except ValueError:
            continue
    return rows


def _proc_name(pid: int) -> str:
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid,
                            "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, errors="replace")
        m = re.match(r'"([^"]+)"', r.stdout.strip())
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001 — 查不到就当未知，不影响其余判定
        return ""


def _container_running(name: str = "qq-bridge") -> bool:
    try:
        r = subprocess.run(["docker", "ps", "--filter", "name=^%s$" % name,
                            "--filter", "status=running", "--format", "{{.Names}}"],
                           capture_output=True, text=True, errors="replace")
        return name in r.stdout.split()
    except Exception:  # noqa: BLE001
        return False


def _strip_comments(text: str) -> str:
    """去掉 Python 注释（整行 + 引号外的行尾），只留真正会执行的代码。

    为什么需要它：P3 原来用 `"QQ_LOG_DIR" in text` 做子串判断，结果把
    「`# os.environ.setdefault("QQ_LOG_DIR", ...)` 注释掉」也判成已隔离 ——
    2026-11-05 用真实文件做过 A/B：注释掉隔离行后门禁仍打印 PASS / exit 0。
    假阳性比没有门禁更糟：它让人以为测试不会污染生产日志。

    只做行级处理，不写完整词法分析：本仓库的改道语句都独占一行。
    """
    out: list[str] = []
    for line in text.splitlines():
        quote: str | None = None
        cut = len(line)
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


#: 「真的把 QQ_LOG_DIR 设成某个值」的两种写法；单纯读取（os.environ.get）不算。
_REDIRECT_RE = re.compile(
    r"""os\.environ(?:\[\s*['"]QQ_LOG_DIR['"]\s*\]\s*=
                   |\.setdefault\(\s*['"]QQ_LOG_DIR['"])""",
    re.VERBOSE,
)


def _redirects_log(text: str) -> bool:
    """判断文件里是否有**实际生效**的 QQ_LOG_DIR 赋值（注释掉的不算）。"""
    return bool(_REDIRECT_RE.search(_strip_comments(text)))


def _test_cases() -> list[dict]:
    cases: list[dict] = []
    # pytest 的日志隔离只有一处负责：tests/conftest.py（它在任何测试模块 import
    # 插件之前执行，config.LOG_DIR 才来得及改道）。各用例里写什么号都无所谓。
    conf = ROOT / "tests" / "conftest.py"
    if conf.exists():
        cases.append({
            "path": str(conf), "kind": "pytest", "gids": [],
            "log_redirect": _redirects_log(conf.read_text(encoding="utf-8",
                                                          errors="replace")),
        })
    # 独立 E2E 脚本不吃 conftest，必须自己改道，且群号不能是真群。
    for p in sorted(ROOT.glob("src/plugins/*/test_e2e.py")):
        text = p.read_text(encoding="utf-8", errors="replace")
        cases.append({
            "path": str(p), "kind": "script",
            "gids": [int(g) for g in re.findall(r"^GID\s*=\s*(\d+)", text, re.M)],
            "log_redirect": _redirects_log(text),
        })
    return cases


# ---------------------------------------------------------------- 跑
def run() -> int:
    env = _read_env(ROOT / ".env")
    port = int(env.get("PORT") or 8080)
    results: list[tuple[bool, str, list[str]]] = []

    listeners = _listeners()
    pids = {pid for _, pid in listeners}
    names = {pid: _proc_name(pid) for pid in pids}
    running = _container_running()
    results.append((True, "P1 端口单一所有者（container_running=%s）" % running,
                    check_single_owner(listeners, names, running, port)))

    rpc = env.get("DL_ARIA2_RPC") or DEFAULT_RPC
    results.append((True, "P2 aria2 凭据可用（%s）" % rpc,
                    check_aria2(env.get("DL_ARIA2_SECRET", ""), rpc)))

    wl = [int(x) for x in re.findall(
        r"\d+", env.get("GROUP_WHITELIST", ""))]
    results.append((True, "P3 测试日志隔离（白名单 %s）" % wl,
                    check_test_isolation(_test_cases(), wl)))

    failed = 0
    for _, title, errs in results:
        if errs:
            failed += 1
            print("[FAIL] %s" % title)
            for e in errs:
                print("       · %s" % e)
        else:
            print("[PASS] %s" % title)
    print("\n%d 项检查，%d 项失败" % (len(results), failed))
    return 1 if failed else 0


def selftest() -> int:
    """把三类历史错误原样注进去，必须全部被拦下；再用正确值确认放行。"""
    bad: list[tuple[str, list[str]]] = []

    # P1：容器在跑，宿主 python 也绑着 8080（2026-11-05 原样）
    bad.append(("P1 双绑", check_single_owner(
        [("0.0.0.0:8080", 14852), ("0.0.0.0:8080", 14432), ("127.0.0.1:8080", 16824)],
        {14852: "python.exe", 14432: "com.docker.backend.exe", 16824: "wslrelay.exe"},
        container_running=True)))
    # P1：两个原生实例
    bad.append(("P1 重复实例", check_single_owner(
        [("0.0.0.0:8080", 111), ("0.0.0.0:8080", 222)],
        {111: "python.exe", 222: "python.exe"}, container_running=False)))

    # P2：空 secret 必须被拦下（不联网就能判）
    bad.append(("P2 secret 为空", check_aria2("", "http://127.0.0.1:1/jsonrpc")))

    # P3：独立脚本借真群号 + 没改道日志（原样）；pytest 的 conftest 没隔离。
    # 白名单用假号即可，检测器只判「gids 是否落在白名单」，与具体号无关。
    _FAKE_WL = [900000001, 900000003]
    bad.append(("P3 脚本借真群号", check_test_isolation(
        [{"path": "src/plugins/doupo/test_e2e.py", "kind": "script",
          "gids": [_FAKE_WL[0]], "log_redirect": False}],
        _FAKE_WL)))
    bad.append(("P3 conftest 未隔离日志", check_test_isolation(
        [{"path": "tests/conftest.py", "kind": "pytest",
          "gids": [], "log_redirect": False}],
        _FAKE_WL)))

    ok = True
    for name, errs in bad:
        if errs:
            print("[PASS] 反例被拦下：%s → %s" % (name, errs[0][:70]))
        else:
            ok = False
            print("[FAIL] 反例没被拦下：%s" % name)

    # P3 检测器本身：必须能认出「注释掉的改道」。
    # 2026-11-05 真实 A/B 漏判过 —— 把 conftest 那行 setdefault 注释掉后门禁仍
    # 打印 PASS / exit 0，因为旧实现只查子串 "QQ_LOG_DIR" in text。
    for label, src, expect in [
        ("注释掉的 setdefault",
         '# os.environ.setdefault("QQ_LOG_DIR", "/tmp/x")\n', False),
        ("注释掉的 os.environ[] 赋值",
         '# os.environ["QQ_LOG_DIR"] = "/tmp/x"\n', False),
        ("只读取不改道（config.py 的写法）",
         'd = Path(os.environ.get("QQ_LOG_DIR") or ROOT / "logs")\n', False),
        ("真 setdefault",
         'os.environ.setdefault("QQ_LOG_DIR", tempfile.mkdtemp())\n', True),
        ("真 os.environ[] 赋值",
         'os.environ["QQ_LOG_DIR"] = os.path.join(TMP, "logs")\n', True),
    ]:
        got = _redirects_log(src)
        if got == expect:
            print("[PASS] 检测器判定正确：%s → log_redirect=%s" % (label, got))
        else:
            ok = False
            print("[FAIL] 检测器误判：%s → 期望 %s，实际 %s" % (label, expect, got))

    # 正确值必须放行，否则门禁会天天误报
    good = [
        ("P1 正常（只有容器）", check_single_owner(
            [("0.0.0.0:8080", 14432), ("127.0.0.1:8080", 16824)],
            {14432: "com.docker.backend.exe", 16824: "wslrelay.exe"},
            container_running=True)),
        ("P1 正常（只有原生）", check_single_owner(
            [("0.0.0.0:8080", 14852)], {14852: "python.exe"},
            container_running=False)),
        ("P3 正常（假群号 + 改道）", check_test_isolation(
            [{"path": "src/plugins/doupo/test_e2e.py", "kind": "script",
              "gids": [900000001], "log_redirect": True},
             {"path": "tests/conftest.py", "kind": "pytest",
              "gids": [], "log_redirect": True}],
            # 白名单是真实群的假想值，绝不该和测试 GID 重叠；这里特意用别的假号
            [900000003, 900000004])),
    ]
    for name, errs in good:
        if errs:
            ok = False
            print("[FAIL] 误报：%s → %s" % (name, errs[0][:70]))
        else:
            print("[PASS] 正常值放行：%s" % name)

    print("\nSELFTEST %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true",
                    help="用三类历史错误验证门禁本身有效")
    args = ap.parse_args()
    return selftest() if args.selftest else run()


if __name__ == "__main__":
    sys.exit(main())
