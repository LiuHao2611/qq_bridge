# 抖音解析：短链重定向 + 分享页 og:meta 提取，并尝试抓取网页版直链下载视频。
import base64
import json
import re

from nonebot.log import logger

from .meta import MetaParser, _extract_video_urls, _dedup
from .base import Platform, handle


class DouyinParser(MetaParser):
    platform = Platform(name="douyin", display_name="抖音")

    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("douyin", r"douyin\.com/[a-z]+/\d+")
    @handle("iesdouyin", r"iesdouyin\.com/share/[a-z]+/\d+")
    async def _parse(self, searched):
        url = "https://" + searched.group(0)
        real = await self.get_redirect_url(url)
        return await self.parse_page(real)

    async def _parse_meta(self, url, metas):
        title = metas.get("og:title") or metas.get("title")
        cover = metas.get("og:image")
        desc = metas.get("og:description") or metas.get("description")
        return self.result(title=title, text=desc, cover_url=cover, url=url)

    async def download_video(self, result):
        url = result.url
        if not url:
            return None
        try:
            real = await self.get_redirect_url(url)
        except Exception:  # noqa: BLE001
            real = url
        html = await self.fetch_text(real)
        candidates = list(_extract_video_urls(html))
        # 抖音直链常藏在 RENDER_DATA（base64 JSON）里
        m = re.search(r'<script id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                data = json.loads(base64.b64decode(m.group(1)).decode("utf-8", "ignore"))
                candidates += _extract_video_urls(data)
            except Exception as e:  # noqa: BLE001
                logger.warning("douyin_render_data_failed: %s", e)
        for u in _dedup(candidates):
            path = await self._save_video(u, referer="https://www.douyin.com/")
            if path:
                return path
        logger.warning("douyin_no_video_url url=%s", real)
        return None
