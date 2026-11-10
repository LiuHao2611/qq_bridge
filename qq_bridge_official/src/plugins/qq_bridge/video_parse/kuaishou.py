# 快手解析：短链重定向 + 分享页提取，并尝试抓取网页版直链下载视频。
import json
import re

from nonebot.log import logger

from .meta import MetaParser, _extract_video_urls, _dedup
from .base import Platform, handle


class KuaishouParser(MetaParser):
    platform = Platform(name="kuaishou", display_name="快手")

    @handle("v.kuaishou", r"v\.kuaishou\.com/[a-zA-Z0-9_\-]+")
    @handle("kuaishou", r"kuaishou\.com/short-video/[a-zA-Z0-9]+")
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
        m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', html, re.S)
        if m:
            try:
                candidates += _extract_video_urls(json.loads(m.group(1)))
            except Exception as e:  # noqa: BLE001
                logger.warning("kuaishou_state_failed: %s", e)
        for u in _dedup(candidates):
            path = await self._save_video(u, referer="https://www.kuaishou.com/")
            if path:
                return path
        logger.warning("kuaishou_no_video_url url=%s", real)
        return None
