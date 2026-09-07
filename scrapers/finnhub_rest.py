import asyncio
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import List

from scrapers import RawHeadline
from scrapers.base import BaseScraper

_HEADERS = {"User-Agent": "Mozilla/5.0"}


class FinnhubRestScraper(BaseScraper):
    name = "Finnhub REST"
    interval = 5

    def __init__(self):
        self._api_key = os.getenv("FINNHUB_API_KEY", "")
        self._url = (
            f"https://finnhub.io/api/v1/news?category=general&token={self._api_key}"
        )
        self._enabled = bool(self._api_key and "your_" not in self._api_key)

    async def fetch(self) -> List[RawHeadline]:
        if not self._enabled:
            return []
        try:
            data = await asyncio.to_thread(self._fetch)
            results = []
            now = datetime.utcnow()
            for item in data:
                if not item.get("headline"):
                    continue
                created_ts = item.get("datetime")
                dt = now
                if created_ts:
                    try:
                        dt = datetime.fromtimestamp(created_ts, tz=timezone.utc).replace(tzinfo=None)
                    except Exception:
                        pass
                results.append(
                    RawHeadline(
                        text=self._safe_text(item.get("headline", "")),
                        source="Finnhub",
                        url=item.get("url", ""),
                        timestamp=dt,
                    )
                )
            return results
        except Exception:
            return []

    def _fetch(self) -> list:
        req = urllib.request.Request(self._url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
