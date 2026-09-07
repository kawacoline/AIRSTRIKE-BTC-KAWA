"""
scrapers/generic_news.py
Generic HTML headline scraper for major news outlets.
"""
import asyncio
import urllib.request
import re
from typing import List

from scrapers import RawHeadline
from scrapers.base import BaseScraper

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_TAG_RE  = re.compile(r"<[^>]+>")
_H_RE    = re.compile(r"<h[234][^>]*>(.*?)</h[234]>", re.DOTALL | re.IGNORECASE)
_ENTITY_MAP = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " "}

_TARGETS = [
    {"name": "CNBC World",  "url": "https://www.cnbc.com/world/"},
    {"name": "Sky News",    "url": "https://news.sky.com/world"},
    {"name": "ABC News",    "url": "https://abcnews.go.com/International"},
]


def _clean(raw: str) -> str:
    text = _TAG_RE.sub("", raw)
    for ent, char in _ENTITY_MAP.items():
        text = text.replace(ent, char)
    return " ".join(text.split()).strip()


class GenericNewsScraper(BaseScraper):
    name = "Generic News"
    interval = 15

    async def fetch(self) -> List[RawHeadline]:
        tasks = [asyncio.to_thread(self._fetch_site, t) for t in _TARGETS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_headlines: List[RawHeadline] = []
        for res in results:
            if isinstance(res, list):
                all_headlines.extend(res)
        return all_headlines

    def _fetch_site(self, target: dict) -> List[RawHeadline]:
        try:
            req = urllib.request.Request(target["url"], headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=9) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            headlines = []
            for match in _H_RE.findall(html)[:20]:
                text = _clean(match)
                if text and 10 < len(text) < 300:
                    headlines.append(RawHeadline(
                        text=text,
                        source=target["name"],
                        url=target["url"],
                    ))
            return headlines
        except Exception:
            return []
