"""
scrapers/__init__.py
Exports the RawHeadline dataclass used by all scraper implementations.
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawHeadline:
    """Normalized output from every scraper source."""
    text: str
    source: str
    url: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __repr__(self):
        return f"[{self.source}] {self.text[:80]}"
