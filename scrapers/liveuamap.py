"""
scrapers/liveuamap.py
LiveUAMap scraper — the premier free real-time military conflict tracker.
No API key required.
"""
import asyncio
import urllib.request
import re
from typing import List

from scrapers import RawHeadline
from scrapers.base import BaseScraper

_URL = "https://liveuamap.com/en"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

_EVENT_RE = re.compile(
    r'<div[^>]*class="[^"]*event-title[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_MAP = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}


def _clean_html(raw: str) -> str:
    text = _TAG_RE.sub("", raw)
    for ent, char in _ENTITY_MAP.items():
        text = text.replace(ent, char)
    return " ".join(text.split()).strip()


class LiveUAMapScraper(BaseScraper):
    name = "LiveUAMap"
    interval = 10

    async def fetch(self) -> List[RawHeadline]:
        try:
            html = await asyncio.to_thread(self._fetch)
            return self._parse(html)
        except Exception:
            return []

    def _fetch(self) -> str:
        req = urllib.request.Request(_URL, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    def _parse(self, html: str) -> List[RawHeadline]:
        matches = _EVENT_RE.findall(html)
        headlines = []
        for raw in matches[:10]:
            text = _clean_html(raw)
            if text and len(text) > 10:
                headlines.append(RawHeadline(
                    text=text,
                    source="LiveUAMap",
                    url=_URL,
                ))
        return headlines
