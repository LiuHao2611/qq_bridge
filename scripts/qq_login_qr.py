"""扫码登录辅助：从 NapCat 容器取二维码，加静区放大成可直接扫的图片。

用法：
    python scripts/qq_login_qr.py            # 刷新二维码并导出图片
    python scripts/qq_login_qr.py --status   # 只看登录状态，不刷新
    python scripts/qq_login_qr.py --no-refresh   # 取当前已有二维码，不刷新

为什么需要它：NapCat 被 QQ 服务端踢下线（KickedOffLine）后，本地 session 作废，
`-q` 快速登录会失败，密码回退又要短信验证码。此时唯一自助路径是扫码，
而二维码只落在容器内 /app/napcat/cache/qrcode.png，且没有静区、尺寸偏小，
直接拷出来在部分手机上识别困难。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

CONTAINER = "qq-bridge-napcat"
WEBUI = "http://127.0.0.1:6099"
OUT_DIR = Path(__file__).resolve().parent.parent / ".tmp_qqlogin"
QR_IN_CONTAINER = "/app/napcat/cache/qrcode.png"


def _docker(*args: str) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} 失败：{r.stderr.strip()}")
    return r.stdout


def _post(path: str, body: dict, credential: str = "") -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(WEBUI + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if credential:
        req.add_header("Authorization", "Bearer " + credential)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def webui_credential() -> str:
    cfg = json.loads(_docker("exec", CONTAINER, "cat", "/app/napcat/config/webui.json"))
    token = cfg["token"]
    # NapCat 的登录 hash 算法：sha256(token + ".napcat")
    h = hashlib.sha256((token + ".napcat").encode()).hexdigest()
    r = _post("/api/auth/login", {"hash": h})
    if r.get("code") != 0:
        raise RuntimeError(f"WebUI 登录失败：{r.get('message')}")
    return r["data"]["Credential"]


def status(cred: str) -> dict:
    r = _post("/api/QQLogin/CheckLoginStatus", {}, cred)
    if r.get("code") != 0:
        raise RuntimeError(f"取登录状态失败：{r.get('message')}")
    return r["data"]


def refresh_qr(cred: str) -> str:
    r = _post("/api/QQLogin/RefreshQRcode", {}, cred)
    if r.get("code") != 0:
        raise RuntimeError(f"刷新二维码失败：{r.get('message')}")
    return r["data"].get("qrcodeurl") or ""


def export_qr() -> Path:
    """把容器内 qrcode.png 拽出来，加 4 模块静区后放大 5 倍。"""
    from PIL import Image

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = OUT_DIR / "napcat_qrcode.png"
    _docker("cp", f"{CONTAINER}:{QR_IN_CONTAINER}", str(raw))

    im = Image.open(raw).convert("L")
    pad = 28  # 原图 147px / 21~29 模块，28px 约等于 4 个模块的静区
    canvas = Image.new("L", (im.width + pad * 2, im.height + pad * 2), 255)
    canvas.paste(im, (pad, pad))
    out = OUT_DIR / "qq_login_qr.png"
    canvas.resize((canvas.width * 5, canvas.height * 5), Image.NEAREST).save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只打印登录状态")
    ap.add_argument("--no-refresh", action="store_true", help="不刷新，取当前二维码")
    args = ap.parse_args()

    cred = webui_credential()
    st = status(cred)

    if args.status:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    if st.get("isLogin"):
        print("已登录，无需扫码")
        return 0

    url = "" if args.no_refresh else refresh_qr(cred)
    if not url:
        url = st.get("qrcodeurl") or ""
        if not url:
            print("当前没有可用二维码，加 --refresh 重新生成")
            return 1

    path = export_qr()
    print(f"二维码链接: {url}")
    print(f"二维码图片: {path}")
    print(f"登录阶段  : {st.get('loginPhase')}   错误: {st.get('loginError') or '无'}")
    print("二维码有效期约 2~3 分钟，过期后重跑本脚本即可。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 直接暴露原因，不吞异常
        print(f"失败：{e}", file=sys.stderr)
        sys.exit(2)
