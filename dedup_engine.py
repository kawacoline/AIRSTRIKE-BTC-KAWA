"""
dedup_engine.py
Cross-source headline deduplication engine.

When Google News, Reuters, Reddit and Twitter all report the same airstrike
within minutes, we only process the headline ONCE — preventing Gemini quota burn
and duplicate MT5 orders.

Strategy:
  1. Normalize the headline text (lowercase, strip punctuation, collapse whitespace)
  2. Build a simple word-set fingerprint
  3. Compare against recent fingerprints using Jaccard similarity
  4. Fingerprints expire after DEDUP_TTL_SECONDS (default 30 min)
"""

import os
import json
import time
import re
import string
from config import DEDUP_TTL_SECONDS

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "be", "been", "has", "have",
    "had", "will", "would", "could", "should", "may", "might", "this",
    "that", "it", "its", "by", "from", "as", "up", "about", "into",
    "then", "than", "so", "if", "no", "not", "also", "after", "before",
    "during", "says", "say", "said", "over", "new", "s", "us",
}

_store: dict[frozenset, float] = {}
_store_loaded = False
_STORE_FILE = "logs/dedup_store.json"


def _load_store():
    global _store, _store_loaded
    _store_loaded = True
    if not os.path.exists(_STORE_FILE):
        return
    try:
        with open(_STORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            words = frozenset(item["words"])
            _store[words] = item["timestamp"]
    except Exception as e:
        print(f"[Dedup] Error loading store: {e}")


def _save_store():
    try:
        os.makedirs(os.path.dirname(_STORE_FILE), exist_ok=True)
        data = []
        for words, ts in _store.items():
            data.append({
                "words": list(words),
                "timestamp": ts
            })
        with open(_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Dedup] Error saving store: {e}")


def _normalize(text: str) -> frozenset:
    text = text.lower()
    text = re.sub(r"https?://\S+", "", text)
    text = text.translate(str.maketrans("", "", string.punctuation))
    words = {w for w in text.split() if w not in _STOP_WORDS and len(w) > 2}
    return frozenset(words)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _purge_expired():
    now = time.time()
    expired = [k for k, ts in _store.items() if now - ts > DEDUP_TTL_SECONDS]
    if expired:
        for k in expired:
            del _store[k]
        _save_store()


def is_duplicate(headline: str, similarity_threshold: float = 0.60) -> bool:
    global _store_loaded
    if not _store_loaded:
        _load_store()
    _purge_expired()
    fingerprint = _normalize(headline)
    if not fingerprint:
        return True
    for stored_fp in _store:
        score = _jaccard(fingerprint, stored_fp)
        if score >= similarity_threshold:
            return True
    _store[fingerprint] = time.time()
    _save_store()
    return False


def reset():
    _store.clear()
    if os.path.exists(_STORE_FILE):
        try:
            os.remove(_STORE_FILE)
        except Exception:
            pass
