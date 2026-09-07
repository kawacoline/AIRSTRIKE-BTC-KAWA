"""
scrapers/base.py
Abstract base class for all news scrapers.
"""
import asyncio
from abc import ABC, abstractmethod
from typing import List

from scrapers import RawHeadline


class BaseScraper(ABC):
    name: str = "BaseScraper"
    interval: int = 10

    @abstractmethod
    async def fetch(self) -> List[RawHeadline]:
        ...

    def _safe_text(self, raw: str) -> str:
        return " ".join(raw.split()).strip()
