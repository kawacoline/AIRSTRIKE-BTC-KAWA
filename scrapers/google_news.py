"""
scrapers/google_news.py
Google News RSS scraper — the fastest free breaking-news source.
No API key required.
"""
import asyncio
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List

from scrapers import RawHeadline
from scrapers.base import BaseScraper
from config import GOOGLE_NEWS_QUERIES


_BASE_URL = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=en-US&gl=US&ceid=US:en"
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class GoogleNewsScraper(BaseScraper):
    name = "Google News"
    interval = 3

    def __init__(self):
        self._queries = GOOGLE_NEWS_QUERIES

    async def fetch(self) -> List[RawHeadline]:
        results: List[RawHeadline] = []
        for query in self._queries:
            try:
                headlines = await asyncio.to_thread(self._fetch_query, query)
                results.extend(headlines)
            except Exception:
                pass
        return results

    def _fetch_query(self, query: str) -> List[RawHeadline]:
        url = _BASE_URL.format(query=urllib.parse.quote(query))
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
        root = ET.fromstring(data)
        headlines = []
        now = datetime.utcnow()
        for item in root.iter("item"):
            dt = now
            pub_date_el = item.find("pubDate")
            if pub_date_el is not None and pub_date_el.text:
                try:
                    parsed_dt = parsedate_to_datetime(pub_date_el.text)
                    if parsed_dt.tzinfo:
                        dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    else:
                        dt = parsed_dt.replace(tzinfo=None)
                    if (now - dt) > timedelta(hours=12):
                        continue  # Skip articles older than 12 hours
                except Exception:
                    pass

            title_el = item.find("title")
            link_el  = item.find("link")
            if title_el is not None and title_el.text:
                text = self._safe_text(title_el.text)
                headlines.append(RawHeadline(
                    text=text,
                    source=f"Google News ({query})",
                    url=link_el.text if link_el is not None else "",
                    timestamp=dt,
                ))
        return headlines
