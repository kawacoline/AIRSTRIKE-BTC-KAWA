"""
scrapers/twitter_nitter.py
Twitter/X scraper via Nitter — open-source Twitter front-ends.
Falls back across multiple Nitter instances if one is down.
No API key required.
"""
import asyncio
import urllib.request
import re
from typing import List, Optional

from scrapers import RawHeadline
from scrapers.base import BaseScraper
from config import NITTER_INSTANCES, TWITTER_ACCOUNTS

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_TWEET_RE = re.compile(
    r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)
_TAG_CLEAN_RE = re.compile(r"<[^>]+>")


class TwitterNitterScraper(BaseScraper):
    name = "Twitter/Nitter"
    interval = 5

    def __init__(self):
        self._accounts  = TWITTER_ACCOUNTS
        self._instances = NITTER_INSTANCES

    async def fetch(self) -> List[RawHeadline]:
        tasks = [asyncio.to_thread(self._fetch_account, acc) for acc in self._accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_headlines: List[RawHeadline] = []
        for res in results:
            if isinstance(res, list):
                all_headlines.extend(res)
        return all_headlines

    def _fetch_account(self, account: str) -> List[RawHeadline]:
        for instance in self._instances:
            try:
                url = f"{instance}/{account}"
                req = urllib.request.Request(url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                matches = _TWEET_RE.findall(html)
                headlines = []
                for raw_html in matches[:5]:
                    text = _TAG_CLEAN_RE.sub("", raw_html)
                    text = self._safe_text(text)
                    if text and len(text) > 10:
                        headlines.append(RawHeadline(
                            text=text,
                            source=f"Twitter @{account}",
                            url=f"https://twitter.com/{account}",
                        ))
                return headlines
            except Exception:
                continue
        return []
