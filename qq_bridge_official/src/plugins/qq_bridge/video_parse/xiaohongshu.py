# 小红书解析：短链重定向 + 分享页提取，并尝试抓取直链下载视频。
# 注意：小红书视频直链需登录 cookie 签名，未配置时大概率拿不到，会降级为只发文字卡。
import re

from nonebot.log import logger

from .meta import MetaParser, _extract_video_urls, _dedup
from .base import Platform, handle


class XiaohongshuParser(MetaParser):
    platform = Platform(name="xiaohongshu", display_name="小红书")

    @handle("xhslink", r"xhslink\.com/[a-zA-Z0-9_\-/]+")
    @handle("xiaohongshu", r"xiaohongshu\.com/(?:discovery|explore|user/profile)/[a-zA-Z0-9]+")
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
        candidates = _dedup(_extract_video_urls(html))
        for u in candidates:
            path = await self._save_video(u, referer=real)
            if path:
                return path
        logger.warning("xhs_no_video_url url=%s (可能需要登录 cookie)", real)
        return None
