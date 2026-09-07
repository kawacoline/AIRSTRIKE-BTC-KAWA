"""
Centralized configuration for Airstrike BTC Kawa.
All tunable parameters live here — adjust scraper intervals, trade sizing, and keyword lists.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Trade Settings ─────────────────────────────────────────────────────────────────
BOT_ACTIVE       = os.getenv("BOT_ACTIVE", "True").lower() == "true"
TRADE_SYMBOL     = os.getenv("TRADE_SYMBOL", "BTCUSD")
TRADE_LOTS       = float(os.getenv("TRADE_LOTS", "0.05"))

# --- Burst Execution Mode ---
BURST_EXECUTION = bool(os.getenv("BURST_EXECUTION", "False").lower() == "true")
_tps_env = os.getenv("BURST_TPS_PIPS", "10000,15000,30000")
BURST_TPS_PIPS = [float(x.strip()) for x in _tps_env.split(",") if x.strip()]
SHIFT_SL_AFTER_TP = int(os.getenv("SHIFT_SL_AFTER_TP", "2"))
BREAKEVEN_PROFIT_PIPS = float(os.getenv("BREAKEVEN_PROFIT_PIPS", "2000.0"))

POLY_BET_SIZE    = float(os.getenv("POLY_BET_SIZE", "2.50")) # Polymarket bet size in pUSD ($)
SL_PIPS          = float(os.getenv("SL_PIPS", "50000.0"))     # Stop Loss in pips ($1.00 USD per pip on BTCUSD)
TP_PIPS          = float(os.getenv("TP_PIPS", "15000.0"))     # Take Profit in pips ($1.00 USD per pip on BTCUSD)
TRADE_COOLDOWN_SECONDS = int(os.getenv("TRADE_COOLDOWN_SECONDS", "60"))  # 1 minute cooldown between executions
MAX_TRADES_PER_HOUR    = int(os.getenv("MAX_TRADES_PER_HOUR", "3"))      # Max trades allowed per rolling 1 hour window
MAX_NEWS_AGE_SECONDS   = int(os.getenv("MAX_NEWS_AGE_SECONDS", "180"))     # 3 minutes maximum news age to prevent old news executions

MT5_LOGIN = os.getenv("MT5_LOGIN", "unknown_mt5")
POLY_FUNDER_ADDRESS = os.getenv("POLY_FUNDER_ADDRESS", "unknown_poly")

import json

# ─── AI Filter ─────────────────────────────────────────────────────────────────────
AI_COOLDOWN_SECONDS  = int(os.getenv("AI_COOLDOWN_SECONDS", "10"))
REQUIRE_GEOPOLITICAL = bool(os.getenv("REQUIRE_GEOPOLITICAL", "True").lower() == "true")
GEOPOLITICAL_PROMPT  = os.getenv("GEOPOLITICAL_PROMPT", "True if the event involves an ACTUAL kinetic military airstrike between sovereign nations (e.g., US bombing a country or vice versa). False for strikes against terrorist organizations (like ISIS/Al-Qaeda), purely verbal threats, drug cartels, domestic crime, or purely non-military news.")

_KEYWORDS_FILE = "keywords.json"

_DEFAULT_PREFILTER = [
    "airstrike", "air strike", "airstrikes", "air strikes",
    "bombing", "bombed", "bombs",
    "missile", "missiles",
    "military strike", "military attack",
    "us military", "u.s. military",
    "pentagon", "us forces", "u.s. forces", "american forces",
    "us troops", "u.s. troops",
    "us launches", "u.s. launches",
    "nato strike", "nato attack",
    "drone strike", "drone attack",
    "warplane", "warplanes", "fighter jet",
    "us bombs", "us hit", "us attacked",
    "operation", "surgical strike",
    "retaliation", "retaliatory strike",
    "iran bombed", "syria bombed", "iraq bombed", "yemen bombed",
    "middle east attack", "houthi", "houthis",
]

_DEFAULT_EXCLUSIONS = [
    "drug boat", "drug vessel", "drug trafficking", "drug smuggling",
    "alleged drug", "narco", "narco-terrorist", "narco-trafficking",
    "suspected drug", "smuggling drugs",
    "eastern pacific", "caribbean sea",
    "boat strike",
    "boat in pacific", "boat in the pacific",
    "boat bombing",
    "killing three in pacific", "killing two in pacific",
    "killing 3 in pacific", "killing 2 in pacific",
    "death toll", "boat in caribbean",
]

def load_keywords():
    if os.path.exists(_KEYWORDS_FILE):
        try:
            with open(_KEYWORDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("prefilter", _DEFAULT_PREFILTER), data.get("exclusions", _DEFAULT_EXCLUSIONS)
        except Exception:
            return _DEFAULT_PREFILTER, _DEFAULT_EXCLUSIONS
    else:
        # Create it
        data = {"prefilter": _DEFAULT_PREFILTER, "exclusions": _DEFAULT_EXCLUSIONS}
        try:
            with open(_KEYWORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass
        return _DEFAULT_PREFILTER, _DEFAULT_EXCLUSIONS

KEYWORD_PREFILTER, KEYWORD_EXCLUSIONS = load_keywords()


# Headlines matching ANY of these get boosted priority — these are the
# geopolitical escalation events that actually move crypto.
GEOPOLITICAL_PRIORITY_KEYWORDS = [
    "iran", "strait of hormuz", "hormuz", "gulf of oman",
    "blockade", "embargo", "naval blockade",
    "syria", "iraq", "yemen", "houthi", "hezbollah",
    "nuclear", "centcom", "tehran", "isfahan", "bandar abbas",
    "qeshm", "kharg island", "persian gulf",
]

DEDUP_TTL_SECONDS   = 1800   # 30 minutes

SCRAPER_INTERVALS = {
    "google_news":   3,
    "rss_feeds":    10,
    "reddit":       15,
    "twitter":       5,
    "liveuamap":    10,
    "finnhub":       5,
    "generic_news": 15,
}

SCRAPER_ENABLED = {
    "google_news":  True,
    "rss_feeds":    True,
    "reddit":       True,
    "twitter":      True,
    "liveuamap":    True,
    "finnhub":      bool(os.getenv("FINNHUB_API_KEY") and "your_" not in os.getenv("FINNHUB_API_KEY", "")),
    "generic_news": True,
}

REDDIT_SUBREDDITS = [
    "worldnews",
    "geopolitics",
    "BreakingNews",
    "news",
]

RSS_FEEDS = [
    {"name": "Reuters World",    "url": "https://feeds.reuters.com/Reuters/worldNews"},
    {"name": "AP Top News",      "url": "https://rsshub.app/apnews/topics/apf-topnews"},
    {"name": "BBC World",        "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Al Jazeera",       "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "NPR World",        "url": "https://feeds.npr.org/1004/rss.xml"},
    {"name": "The Guardian",     "url": "https://www.theguardian.com/world/rss"},
    {"name": "CNN World",        "url": "http://rss.cnn.com/rss/edition_world.rss"},
    {"name": "Fox News World",   "url": "https://feeds.foxnews.com/foxnews/world"},
]

GOOGLE_NEWS_QUERIES = [
    "US airstrike",
    "US military strike",
    "US bombing",
    "Pentagon strike",
    "US Iran Strait Hormuz",
    "US blockade Iran Gulf Oman",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

TWITTER_ACCOUNTS = [
    "ABORINT",
    "BNONews",
    "IntelCrab",
    "sentdefender",
    "Liveuamap",
    "PostalAzul",
]
