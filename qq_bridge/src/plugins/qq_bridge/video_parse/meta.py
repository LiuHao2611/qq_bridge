# 通用分享页 <meta og:*> 提取：抖音/快手/小红书等短视频平台。
# 视频直链：抓网页版 HTML，解析内嵌 JSON（抖音 RENDER_DATA / 快手 __INITIAL_STATE__）
# 或直接正则搜 mp4/play 直链；能拿到就下载发送，拿不到则降级为只发文字卡片。
import asyncio
import os
import re
import tempfile

import httpx
from nonebot.log import logger

from .base import BaseParser, ParseException, handle


# QQ 群视频文件大小上限（保守取 45MB：大文件 NapCat 发送易超时失败）
VIDEO_MAX_BYTES = 45 * 1024 * 1024

# 明确后缀的视频直链
_VIDEO_RE = re.compile(
    r"https?://[^\s\"'\\<>{}\[\]]+?\.(?:mp4|m3u8|flv)(?:\?[^\"'\s\\<>{}\[\]]*)?"
)
# 路径/域名含播放特征的直链（部分平台直链不带 .mp4 后缀）
_PLAY_RE = re.compile(
    r"https?://[^\s\"'\\<>{}\[\]]*?(?:douyinvod|snssdk\.com|kuaishou|kscrash|v\d+-ex|"
    r"xhscdn|sns-video)[^\s\"'\\<>{}\[\]]*"
)


def _extract_video_urls(obj) -> list[str]:
    """递归从 dict/list/str 收集候选视频直链。"""
    out: list[str] = []
    if isinstance(obj, str):
        out += _VIDEO_RE.findall(obj)
        out += _PLAY_RE.findall(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _extract_video_urls(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += _extract_video_urls(v)
    return out


def _dedup(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    uniq: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]*?content=["\']([^"\']*)["\']'
    r'|<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def extract_metas(html: str) -> dict[str, str]:
    """返回 {property/name: content} 小写键便于查找。"""
    out: dict[str, str] = {}
    for m in _META_RE.finditer(html):
        if m.group(1) and m.group(2):
            out[m.group(1).lower()] = m.group(2)
        elif m.group(3) and m.group(4):
            out[m.group(3).lower()] = m.group(4)
    return out


class MetaParser(BaseParser):
    """抖音/快手/小红书共用的 og:meta 解析基类。

    子类只需：
      - 定义 platform 与 @handle 注册的 URL 形态；
      - 实现 _parse_meta(self, url, metas) -> VideoParseResult；
      - 可选 override download_video(self, result) 下载视频文件。
    """

    async def _parse_meta(self, url: str, metas: dict[str, str]) -> "VideoParseResult":  # noqa: F821
        raise NotImplementedError

    async def parse_page(self, url: str) -> "VideoParseResult":  # noqa: F821
        html = await self.fetch_text(url, self.headers)
        metas = extract_metas(html)
        return await self._parse_meta(url, metas)

    async def _save_video(self, video_url: str, referer: str | None = None) -> str | None:
        """下载单个直链为 mp4（flv/m3u8 用 ffmpeg 转封装），返回本地路径；失败返回 None。"""
        tmp = tempfile.mkdtemp(prefix="vv_")
        if ".m3u8" in video_url:
            raw_path = os.path.join(tmp, "v.m3u8")
        elif ".flv" in video_url:
            raw_path = os.path.join(tmp, "v.flv")
        else:
            raw_path = os.path.join(tmp, "v.mp4")
        hdrs = dict(self.headers)
        if referer:
            hdrs["Referer"] = referer
        try:
            async with httpx.AsyncClient(headers=hdrs, follow_redirects=True, timeout=60.0) as client:
                async with client.stream("GET", video_url) as r:
                    r.raise_for_status()
                    length = int(r.headers.get("content-length", 0) or 0)
                    if length and length > VIDEO_MAX_BYTES:
                        logger.warning("video_too_large skip bytes=%s", length)
                        return None
                    with open(raw_path, "wb") as f:
                        async for chunk in r.aiter_bytes(65536):
                            f.write(chunk)
        except Exception as e:  # noqa: BLE001
            logger.warning("video_dl_failed: %s", e)
            return None
        if raw_path.endswith(".mp4"):
            return raw_path
        out_path = os.path.join(tmp, "v.mp4")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", raw_path, "-c", "copy", out_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=120.0)
        except Exception as e:  # noqa: BLE001
            logger.warning("video_remux_failed: %s", e)
            return None
        if proc.returncode != 0 or not os.path.exists(out_path):
            return None
        return out_path
