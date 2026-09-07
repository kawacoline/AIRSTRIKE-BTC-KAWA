"""
scrapers/rss_feeds.py
Multi-RSS aggregator — polls 8 major news outlets simultaneously.
No API key required.
"""
import asyncio
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List

from scrapers import RawHeadline
from scrapers.base import BaseScraper
from config import RSS_FEEDS

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class RssFeedsScraper(BaseScraper):
    name = "RSS Feeds"
    interval = 10

    def __init__(self):
        self._feeds = RSS_FEEDS

    async def fetch(self) -> List[RawHeadline]:
        tasks = [asyncio.to_thread(self._fetch_feed, feed) for feed in self._feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_headlines: List[RawHeadline] = []
        for res in results:
            if isinstance(res, list):
                all_headlines.extend(res)
        return all_headlines

    def _fetch_feed(self, feed: dict) -> List[RawHeadline]:
        try:
            req = urllib.request.Request(feed["url"], headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
            root = ET.fromstring(data)
            tag_iter = "item"
            if "feed" in root.tag.lower():
                tag_iter = "{http://www.w3.org/2005/Atom}entry"
            headlines = []
            now = datetime.utcnow()
            for item in root.iter(tag_iter):
                dt = now
                pub_date_el = item.find("pubDate")
                if pub_date_el is None:
                    pub_date_el = item.find("{http://www.w3.org/2005/Atom}updated")
                if pub_date_el is not None and pub_date_el.text:
                    try:
                        parsed_dt = parsedate_to_datetime(pub_date_el.text)
                        if parsed_dt.tzinfo:
                            dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
                        else:
                            dt = parsed_dt.replace(tzinfo=None)
                        if (now - dt) > timedelta(hours=12):
                            continue  # Skip old articles
                    except Exception:
                        pass

                title_el = item.find("title")
                if title_el is None:
                    title_el = item.find("{http://www.w3.org/2005/Atom}title")
                link_el = item.find("link")
                link = ""
                if link_el is not None:
                    link = link_el.text or link_el.get("href", "")
                if title_el is not None and title_el.text:
                    headlines.append(RawHeadline(
                        text=self._safe_text(title_el.text),
                        source=feed["name"],
                        url=link,
                        timestamp=dt,
                    ))
            return headlines
        except Exception:
            return []
