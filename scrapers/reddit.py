"""
scrapers/reddit.py
Reddit JSON API scraper — no authentication required.
"""
import asyncio
import json
import urllib.request
from datetime import datetime, timezone
from typing import List

from scrapers import RawHeadline
from scrapers.base import BaseScraper
from config import REDDIT_SUBREDDITS

_BASE_URL = "https://www.reddit.com/r/{sub}/new.json?limit=10&sort=new"
_HEADERS  = {
    "User-Agent": "AirstrikeBot/1.0 (autonomous news monitor)",
    "Accept": "application/json",
}


class RedditScraper(BaseScraper):
    name = "Reddit"
    interval = 15

    def __init__(self):
        self._subs = REDDIT_SUBREDDITS

    async def fetch(self) -> List[RawHeadline]:
        tasks = [asyncio.to_thread(self._fetch_subreddit, sub) for sub in self._subs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_headlines: List[RawHeadline] = []
        for res in results:
            if isinstance(res, list):
                all_headlines.extend(res)
        return all_headlines

    def _fetch_subreddit(self, subreddit: str) -> List[RawHeadline]:
        try:
            url = _BASE_URL.format(sub=subreddit)
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            posts = data.get("data", {}).get("children", [])
            headlines = []
            now = datetime.utcnow()
            for post in posts:
                pd = post.get("data", {})
                title = pd.get("title", "").strip()
                url   = "https://reddit.com" + pd.get("permalink", "")
                created_utc = pd.get("created_utc")
                dt = now
                if created_utc:
                    try:
                        dt = datetime.fromtimestamp(created_utc, tz=timezone.utc).replace(tzinfo=None)
                    except Exception:
                        pass
                if title:
                    headlines.append(RawHeadline(
                        text=self._safe_text(title),
                        source=f"Reddit r/{subreddit}",
                        url=url,
                        timestamp=dt,
                    ))
            return headlines
        except Exception:
            return []
