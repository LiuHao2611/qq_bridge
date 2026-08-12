# -*- coding: utf-8 -*-
"""Layer 0：ComfyUI 基础设施服务（手册 SPEC-DIR-001 §2.1）。

职责：封装 ComfyUI 原生 HTTP 接口，供上层（意图翻译引擎产出工作流后）提交与取回，
不掺杂任何节点拼装逻辑（节点拼装归意图翻译引擎 + 模板，铁律 1.2）。

封装接口：
  - /prompt        提交工作流，返回 prompt_id
  - /upload/image  上传参考图 / 首帧锚点图，返回 ComfyUI 内部文件名
  - /history/<id>  轮询取回执行结果与输出图

与现有 ai_local._comfy_run 的关系：本服务是「原生端点封装」的单一归属；
ai_local 的历史轮询逻辑后续将逐步委托本服务（目前并存，不破坏已验证出图）。
"""
import os
import io
import time
import json
import urllib.request
import urllib.error

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
_HTTP_MAX_RETRIES = 3
_HTTP_BACKOFF_BASE = 1.0


def _resolve_url(base_url=None):
    if base_url:
        return base_url.rstrip("/")
    env = os.environ.get("MANJU_COMFY_URL")
    if env:
        return env.rstrip("/")
    return DEFAULT_COMFY_URL


def _http_post(url, data_bytes, headers, timeout):
    last = None
    for attempt in range(_HTTP_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:500]
            raise RuntimeError("ComfyUI HTTP %s @ %s: %s" % (e.code, url, detail))
        except Exception as e:  # 网络抖动 -> 指数退避重试（CLOSE-03 精神）
            last = e
            time.sleep(_HTTP_BACKOFF_BASE * (2 ** attempt))
    raise RuntimeError("ComfyUI 请求失败(%s): %s" % (url, last))


def _http_get(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError("ComfyUI HTTP %s @ %s: %s" % (e.code, url, detail))
    except Exception as e:
        raise RuntimeError("ComfyUI 请求失败(%s): %s" % (url, e))


class ComfyUIService:
    """ComfyUI 服务封装（单例语义，无状态）。"""

    def __init__(self, base_url=None):
        self.base_url = _resolve_url(base_url)

    # ---- §2.1 三个原生接口封装 ----

    def submit_prompt(self, wf, client_id="manju_local", timeout=120):
        """POST /prompt，提交工作流字典，返回 prompt_id。"""
        payload = json.dumps({"prompt": wf, "client_id": client_id}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        body = _http_post(self.base_url + "/prompt", payload, headers, timeout)
        pid = json.loads(body).get("prompt_id")
        if not pid:
            raise RuntimeError("ComfyUI 未返回 prompt_id")
        return pid

    def upload_image(self, image_path, name=None, subfolder="", timeout=60):
        """POST /upload/image，上传本地图，返回 ComfyUI 内部文件名（供 LoadImage 引用）。"""
        if not os.path.isfile(image_path):
            raise RuntimeError("待上传图片不存在: %s" % image_path)
        boundary = "----manju_boundary_%f" % time.time()
        buf = io.BytesIO()
        fname = name or os.path.basename(image_path)
        with open(image_path, "rb") as f:
            raw = f.read()
        buf.write(("--%s\r\n" % boundary).encode())
        buf.write(('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % fname).encode())
        buf.write(b"Content-Type: image/png\r\n\r\n")
        buf.write(raw)
        buf.write(b"\r\n")
        buf.write(("--%s\r\n" % boundary).encode())
        buf.write(b'Content-Disposition: form-data; name="subfolder"\r\n\r\n')
        buf.write(subfolder.encode())
        buf.write(b"\r\n")
        buf.write(("--%s--\r\n" % boundary).encode())
        headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}
        body = _http_post(self.base_url + "/upload/image", buf.getvalue(), headers, timeout)
        resp = json.loads(body)
        # ComfyUI 返回 {name, subfolder, type}，LoadImage 只需 name
        return resp.get("name") or fname

    def get_history(self, prompt_id, timeout=10):
        """GET /history/<prompt_id>，返回原始 history 节点字典。"""
        return json.loads(_http_get(self.base_url + "/history/" + prompt_id, timeout))

    # ---- 便捷：提交 + 轮询取回首张输出图（供 ai_local 迁移使用）----

    def wait_for_output(self, prompt_id, output_dir, timeout=240, poll=3):
        """轮询 history 直到完成，返回首张输出图绝对路径；失败/超时抛 RuntimeError。"""
        os.makedirs(output_dir, exist_ok=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                h = self.get_history(prompt_id)
            except Exception:
                time.sleep(poll)
                continue
            if prompt_id in h:
                node = h[prompt_id]
                st = node.get("status", {})
                if st.get("status_str") == "error" or st.get("completed") is False:
                    msgs = st.get("messages", [])
                    err_txt = "; ".join(str(m[1]) for m in msgs
                                        if isinstance(m, (list, tuple)) and len(m) > 1)[:600] or "未知执行错误"
                    raise RuntimeError("ComfyUI 执行错误: %s" % err_txt)
                for node_out in node.get("outputs", {}).values():
                    for im in (node_out.get("images") or []):
                        fn = im.get("filename")
                        if not fn:
                            continue
                        sub = im.get("subfolder")
                        p = os.path.join(output_dir, sub, fn) if sub else os.path.join(output_dir, fn)
                        if os.path.isfile(p):
                            return p
                raise RuntimeError("ComfyUI 执行完成但未产出图像文件")
            time.sleep(poll)
        raise RuntimeError("ComfyUI 轮询超时（%ds 内未取回图像）" % timeout)

    def generate(self, wf, output_dir, client_id="manju_local", timeout=240):
        """提交并取回：返回首张输出图绝对路径（端到端便捷封装）。"""
        pid = self.submit_prompt(wf, client_id=client_id)
        return self.wait_for_output(pid, output_dir, timeout=timeout)

    def ping(self, timeout=5):
        """探测 ComfyUI 是否在线（对应 comfy_available）。"""
        try:
            _http_get(self.base_url + "/", timeout)
            return True
        except Exception:
            return False


# 模块级便捷实例（与 ai_local 全局 COMFY_URL 约定一致）
_default_service = None


def get_service(base_url=None):
    global _default_service
    if base_url:
        return ComfyUIService(base_url)
    if _default_service is None:
        _default_service = ComfyUIService()
    return _default_service
