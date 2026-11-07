# -*- coding: utf-8 -*-
"""safety（内容安全，设计文档 §3.5 / §7.2）。

职责：入站 prompt 审核 + 出站媒体审核。

关键接口：check_prompt(text) -> (ok, reason)；check_media(ref) -> (ok, reason)
验收（§3.5）：违规 prompt 不提交并回传"内容不合规"；违规结果不回传并告警。

现状与诚实声明：
  本模块目前是**本地敏感词表 + 规则**实现，能拦住明显违规请求，
  但**不具备图像 NSFW 判别能力**。check_media 目前只做
  「URL 协议白名单 + 扩展名白名单」的准入校验，真正的人体/违规图像识别
  需要接入审核 API（腾讯云/阿里云内容安全），属于后续扩展点（见 TODO）。
  此处不假装已实现 —— 文档 §7.2 明确要求"对外必做"，上线前需补齐。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..config import Config, get_logger, jlog

log = get_logger()

# 内置敏感词（最小可用集；生产应换成专业词库或审核 API）
_BUILTIN_WORDS = (
    "裸", "裸体", "色情", "淫秽", "做爱", "性交", "乱伦", "强奸",
    "毒品", "海洛因", "冰毒", "制毒", "贩毒",
    "枪支", "弹药", "炸药", "制枪", "爆炸物",
    "赌博", "赌场", "洗钱", "诈骗教程",
    "自杀", "自残", "教唆自杀",
)

_ALLOWED_MEDIA_SCHEMES = ("http", "https", "file")
_ALLOWED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_ALLOWED_VIDEO_EXT = (".mp4", ".mov", ".webm", ".avi", ".mkv")


def _word_list() -> tuple:
    extra = [w.strip() for w in (Config.SAFETY_EXTRA_WORDS or "").replace("，", ",").split(",") if w.strip()]
    if not extra:
        return _BUILTIN_WORDS
    return tuple(list(_BUILTIN_WORDS) + extra)


class Safety:
    def __init__(self, enable: bool | None = None) -> None:
        self.enable = Config.SAFETY_ENABLE if enable is None else enable

    def check_prompt(self, text: str) -> tuple[bool, str]:
        """入站审核。返回 (ok, reason)。ok=False 时 reason 供回传用户（不含命中词本身）。"""
        if not self.enable:
            return True, ""
        content = (text or "").strip()
        if not content:
            return True, ""
        lowered = content.lower()
        for w in _word_list():
            if w.lower() in lowered:
                # LOG-04：只记是否命中与长度，不记原始文本与命中词
                log.warning(jlog("safety_prompt_blocked", text_len=len(content)))
                return False, "内容不合规"
        return True, ""

    def check_media(self, ref: str, kind: str = "image") -> tuple[bool, str]:
        """出站媒体准入校验（URL/路径）。

        kind: image | video。
        注意：这是"准入"校验（协议/扩展名），**不是** NSFW 判别。
        """
        if not self.enable:
            return True, ""
        s = (ref or "").strip()
        if not s:
            return False, "空媒体地址"
        if s.startswith("base64://"):
            return True, ""
        # Windows 盘符路径（d:/out/a.png、D:\out\a.png）：
        # urlparse 会把单字母盘符误判成 scheme（scheme='d'），必须先于 urlparse 处理
        if re.match(r"^[A-Za-z]:[\\/]", s):
            low = s.lower()
            exts = _ALLOWED_IMAGE_EXT if kind == "image" else _ALLOWED_VIDEO_EXT
            if not low.endswith(exts):
                return False, "不支持的媒体类型"
            return True, ""
        try:
            p = urlparse(s)
        except Exception:
            return False, "非法媒体地址"
        # 本地路径（urlparse 无 scheme 且像路径）
        if not p.scheme:
            low = s.lower()
            exts = _ALLOWED_IMAGE_EXT if kind == "image" else _ALLOWED_VIDEO_EXT
            if not low.endswith(exts):
                return False, "不支持的媒体类型"
            return True, ""
        if p.scheme not in _ALLOWED_MEDIA_SCHEMES:
            log.warning(jlog("safety_media_blocked", reason="scheme", scheme=p.scheme))
            return False, "不支持的媒体协议"
        return True, ""


safety = Safety()
