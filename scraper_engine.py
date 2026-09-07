"""
scraper_engine.py
Master news scraper engine — replaces news_listener.py.

Architecture:
  - Instantiates ALL scraper sources
  - Each runs independently on its own asyncio loop at its configured interval
  - All headlines flow into a central asyncio.Queue
  - Consumer task pulls from queue → keyword pre-filter → dedup check → AI → MT5

The scrapers run AUTOMATICALLY and CONTINUOUSLY as long as the engine is running.
No manual intervention required.
"""
import asyncio
import time
from datetime import datetime
from typing import Callable, List, Optional

import ai_filter
import mt5_engine
import polymarket_engine
import technical_overlay
import dedup_engine
from scrapers import RawHeadline
import config
from config import (
    SCRAPER_ENABLED, SCRAPER_INTERVALS, KEYWORD_PREFILTER,
    KEYWORD_EXCLUSIONS,
    TRADE_SYMBOL, AI_COOLDOWN_SECONDS
)

import telegram_notifier
from scrapers.google_news   import GoogleNewsScraper
from scrapers.rss_feeds     import RssFeedsScraper
from scrapers.reddit        import RedditScraper
from scrapers.twitter_nitter import TwitterNitterScraper
from scrapers.liveuamap     import LiveUAMapScraper
from scrapers.finnhub_rest  import FinnhubRestScraper
from scrapers.generic_news  import GenericNewsScraper
from scrapers.tree_news_ws  import TreeNewsWebsocketScraper


class ScraperEngine:
    def __init__(
        self,
        log_callback:   Optional[Callable] = None,
        trade_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
    ):
        self.log_callback    = log_callback
        self.trade_callback  = trade_callback
        self.status_callback = status_callback
        self._queue: asyncio.Queue[RawHeadline] = asyncio.Queue(maxsize=500)
        self._ai_model = ai_filter.initialize_ai()
        if not self._ai_model:
            self._log("[Error] Gemini AI Filter failed to initialize.", is_error=True)
        self._last_ai_call: float = 0.0
        self._last_trade_time: float = 0.0
        self._trade_history_timestamps: list = []
        self._processed_ids: set = set()
        self.scraper_status: dict = {}
        self._scrapers = []
        self._register_scrapers()

    def _register_scrapers(self):
        _all = [
            ("google_news",  GoogleNewsScraper),
            ("rss_feeds",    RssFeedsScraper),
            ("reddit",       RedditScraper),
            ("twitter",      TwitterNitterScraper),
            ("liveuamap",    LiveUAMapScraper),
            ("finnhub",      FinnhubRestScraper),
            ("generic_news", GenericNewsScraper),
            ("tree_news_ws", TreeNewsWebsocketScraper),
        ]
        for key, cls in _all:
            if SCRAPER_ENABLED.get(key, True):
                instance = cls()
                instance.interval = SCRAPER_INTERVALS.get(key, instance.interval)
                self._scrapers.append(instance)
                self.scraper_status[instance.name] = {
                    "last_poll": None,
                    "headlines_session": 0,
                    "status": "Starting...",
                }

    def _log(self, msg: str, is_error: bool = False):
        if self.log_callback:
            self.log_callback(msg, is_error)

    def _trade_log(self, msg: str):
        if self.trade_callback:
            self.trade_callback(msg)

    def _keyword_match(self, text: str):
        import re
        lower = text.lower()
        # Use regex word boundaries to prevent 'exodUS LAUNCHES' from triggering 'us launches'
        for kw in KEYWORD_PREFILTER:
            if re.search(r'\b' + re.escape(kw) + r'\b', lower):
                return kw
        return None

    def _keyword_exclusion_match(self, text: str) -> bool:
        """Returns True if headline matches exclusion keywords (e.g. narco boats)."""
        import re
        lower = text.lower()
        for kw in KEYWORD_EXCLUSIONS:
            if re.search(r'\b' + re.escape(kw) + r'\b', lower):
                return True
        return False

    async def run(self):
        tasks = [self._scraper_loop(scraper) for scraper in self._scrapers]
        tasks.append(self._consumer_loop())
        await asyncio.gather(*tasks)

    async def inject_headline(self, text: str, source: str = "Manual Trigger"):
        headline = RawHeadline(text=text, source=source)
        await self._queue.put(headline)
        self._log(f"[Injected] {text[:80]}")

    async def _scraper_loop(self, scraper):
        name = scraper.name
        
        # Fast path for continuous WebSocket streaming
        if hasattr(scraper, 'listen'):
            await scraper.listen(self._queue, self._processed_ids, self.scraper_status)
            return

        while True:
            try:
                headlines = await scraper.fetch()
                now = time.time()
                self.scraper_status[name]["last_poll"] = now
                self.scraper_status[name]["status"] = "OK"
                new_count = 0
                for h in headlines:
                    fingerprint = h.text[:60]
                    if fingerprint not in self._processed_ids:
                        self._processed_ids.add(fingerprint)
                        if not self._queue.full():
                            await self._queue.put(h)
                            new_count += 1
                if new_count:
                    self.scraper_status[name]["headlines_session"] += new_count
            except Exception as e:
                self.scraper_status[name]["status"] = f"Error: {str(e)[:30]}"
            await asyncio.sleep(scraper.interval)

    async def _consumer_loop(self):
        while True:
            headline: RawHeadline = await self._queue.get()
            try:
                text = headline.text
                source = headline.source
                matched_kw = self._keyword_match(text)
                if not matched_kw:
                    continue
                self._log(f"[{source}] Candidate (regex: {matched_kw}): {text[:100]}")

                # 0. Exclusion Filter — reject narco/drug boat headlines immediately
                if self._keyword_exclusion_match(text):
                    self._log(f"[Exclusion Filter] Narco/drug-boat headline rejected — not geopolitical.")
                    continue

                # 1. News Age Filter
                news_age = (datetime.utcnow() - headline.timestamp).total_seconds()
                if news_age > config.MAX_NEWS_AGE_SECONDS:
                    self._log(f"[Age Filter] News too old ({news_age:.1f}s ago, max: {config.MAX_NEWS_AGE_SECONDS}s) — skipping.")
                    continue

                if source != "Telegram /test_trigger" and dedup_engine.is_duplicate(text):
                    self._log(f"[Dedup] Duplicate detected — skipping AI call.")
                    continue
                now = time.time()
                elapsed = now - self._last_ai_call
                if elapsed < AI_COOLDOWN_SECONDS:
                    wait = AI_COOLDOWN_SECONDS - elapsed
                    self._log(f"[AI Cooldown] Waiting {wait:.1f}s to avoid rate limit...")
                    await asyncio.sleep(wait)
                if not self._ai_model:
                    continue
                import db_engine
                import news_extractor
                recent_trades = await asyncio.to_thread(db_engine.get_recent_trades, 10, 0)
                recent_headlines = [t['headline'] for t in recent_trades if 'headline' in t]

                news_context, scrape_status = await news_extractor.fetch_news_context(headline.url)

                self._last_ai_call = time.time()
                impact = await asyncio.to_thread(
                    ai_filter.evaluate_headline, self._ai_model, text, recent_headlines, news_context, scrape_status
                )
                self._log(
                    f"[AI] Target={impact.target_classification} | "
                    f"Trade Event={impact.is_trade_event} ({impact.trade_direction}) | "
                    f"Current={impact.is_current_event} | "
                    f"Retraction={impact.is_fake_news_retraction} | "
                    f"GeoPolitical={impact.is_geopolitically_significant} | "
                    f"Duplicate={impact.is_duplicate} | "
                    f"Scrape={scrape_status}"
                )
                if impact.is_fake_news_retraction:
                    self._log("Retraction detected — Emergency closing all positions.", is_error=True)
                    self._trade_log("RETRACTION -> Emergency Close")
                    await asyncio.to_thread(mt5_engine.emergency_close_all, TRADE_SYMBOL)
                    try:
                        import telegram_notifier
                        telegram_notifier.log_successful_trade(
                            source, headline, TRADE_SYMBOL, config.TRADE_LOTS, is_retraction=True,
                            ai_result={
                                "Target": impact.extracted_target,
                                "Classification": impact.target_classification,
                                "Geopolitical Reasoning": impact.geopolitical_reasoning,
                                "Timeline Reasoning": impact.timeline_reasoning,
                                "Scrape Status": impact.scrape_status,
                                "Trade Event": impact.is_trade_event, 
                                "Current Event": impact.is_current_event, 
                                "Geopolitical": impact.is_geopolitically_significant,
                                "Duplicate": impact.is_duplicate
                            }
                        )
                    except Exception as e:
                        self._log(f"[Telegram] Failed to notify: {e}")
                        
                elif impact.is_duplicate:
                    self._log(f"[AI Dedup] Ignored duplicate news reporting same event: {text[:80]}")

                elif impact.is_trade_event and impact.trade_direction in ['BUY', 'SELL'] and impact.is_current_event and (not getattr(config, 'REQUIRE_GEOPOLITICAL', True) or impact.is_geopolitically_significant) and not impact.is_duplicate:
                    # Notify Telegram IMMEDIATELY — even if trade gets blocked by cooldown/funds
                    try:
                        import telegram_notifier
                        telegram_notifier.log_trade_event_detected(source, headline, trade_direction=impact.trade_direction)
                    except Exception as e:
                        self._log(f"[Telegram] Failed to notify detection: {e}")

                    # 2. Trade Cooldown Filter
                    now_time = time.time()
                    elapsed_since_trade = now_time - self._last_trade_time
                    if elapsed_since_trade < config.TRADE_COOLDOWN_SECONDS:
                        wait_rem = config.TRADE_COOLDOWN_SECONDS - elapsed_since_trade
                        self._log(f"[Trade Cooldown] Active — skipping Trade Event (remaining: {wait_rem:.1f}s)")
                        continue

                    # 3. Hourly Rate Limit Check
                    if getattr(config, 'MAX_TRADES_PER_HOUR', 0) > 0:
                        self._trade_history_timestamps = [t for t in self._trade_history_timestamps if now_time - t <= 3600]
                        if len(self._trade_history_timestamps) >= config.MAX_TRADES_PER_HOUR:
                            self._log(f"[Hourly Limit] {len(self._trade_history_timestamps)} trades fired in last hour. Max is {config.MAX_TRADES_PER_HOUR}. Skipping.")
                            self._trade_log(f"SKIPPED (HOURLY LIMIT) -> Max {config.MAX_TRADES_PER_HOUR}/hr reached.")
                            continue

                    if not config.BOT_ACTIVE:
                        self._log(f"[Skipped] BOT_ACTIVE is False. Would have executed SELL for: {text[:80]}")
                        self._trade_log(f"SKIPPED (BOT OFF) -> {impact.trade_direction} {config.TRADE_LOTS} lots {TRADE_SYMBOL}")
                        continue

                    self._log(f"BREAKING EVENT! Executing Sniper {impact.trade_direction} on {TRADE_SYMBOL}...", is_error=False)
                    self._trade_log(f"TRADE EVENT -> {impact.trade_direction} {config.TRADE_LOTS} lots {TRADE_SYMBOL} | [{source}]")
                    self._last_trade_time = now_time
                    self._trade_history_timestamps.append(now_time)
                    
                    # 1. MT5 Execution
                    result = await asyncio.to_thread(
                        mt5_engine.execute_sniper_trade, TRADE_SYMBOL, config.TRADE_LOTS, impact.trade_direction
                    )
                    # Unpack (success, error_detail) tuple
                    if isinstance(result, tuple):
                        success, error_detail_or_ticket = result
                    else:
                        success, error_detail_or_ticket = bool(result), ""
                        
                    # 2. Polymarket Execution
                    try:
                        markets = await asyncio.to_thread(polymarket_engine.get_active_btc_markets)
                        if markets:
                            successful_poly_markets = []
                            # Fire on the shortest-term 5m market
                            for best_market in markets[:1]:
                                poly_success, poly_id = await asyncio.to_thread(
                                    polymarket_engine.execute_polymarket_bet, best_market, config.POLY_BET_SIZE
                                )
                                if poly_success:
                                    successful_poly_markets.append(best_market)
                                    # Log Polymarket Trade to Database
                                    try:
                                        import db_engine
                                        db_engine.record_poly_trade(
                                            order_id=poly_id,
                                            market_question=best_market['question'],
                                            bet_size=config.POLY_BET_SIZE,
                                            headline=headline,
                                            url=url,
                                            source=source,
                                            account_id=str(config.POLY_FUNDER_ADDRESS)
                                        )
                                    except Exception as e:
                                        self._log(f"[DB Error] Failed to log Poly trade: {e}")
                                    
                                    pm_msg = f"✅ <b>POLYMARKET EXECUTED:</b> ${config.POLY_BET_SIZE} on '{best_market['question']}'"
                                    self._trade_log(pm_msg)
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, pm_msg)
                                else:
                                    pm_err = f"❌ <b>POLYMARKET FAILED</b> on '{best_market['question']}': {poly_id}"
                                    self._trade_log(pm_err)
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, pm_err)
                                    
                            if successful_poly_markets:
                                await asyncio.to_thread(technical_overlay.activate_scale_in_mode, successful_poly_markets)
                        else:
                            pm_skip = "⚠️ <b>POLYMARKET SKIPPED:</b> No active short-term BTC markets found."
                            self._trade_log(pm_skip)
                            await asyncio.to_thread(telegram_notifier.send_telegram_message, pm_skip)
                    except Exception as e:
                        pm_crash = f"❌ <b>POLYMARKET CRASH:</b> {e}"
                        self._trade_log(pm_crash)
                        await asyncio.to_thread(telegram_notifier.send_telegram_message, pm_crash)
                        
                    
                    if success:
                        self._last_trade_time = time.time()  # Record execution time for cooldown
                        self._trade_log(f"Sniper {impact.trade_direction} executed on {TRADE_SYMBOL} (Tickets: {error_detail_or_ticket})")
                        
                        # Log to DB
                        try:
                            import db_engine
                            import MetaTrader5 as mt5
                            tick = mt5.symbol_info_tick(TRADE_SYMBOL)
                            price = tick.bid if impact.trade_direction == "SELL" else tick.ask
                            if not price: price = 0.0
                            url = getattr(headline, "url", "")
                            
                            tickets = [t.strip() for t in str(error_detail_or_ticket).split(",")]
                            for t in tickets:
                                if t:
                                    ticket_id = int(t)
                                    db_engine.record_new_trade(
                                        ticket_id, TRADE_SYMBOL, config.TRADE_LOTS / len(tickets), impact.trade_direction, 
                                        price, text, url, source, str(config.MT5_LOGIN)
                                    )
                        except Exception as e:
                            self._log(f"[DB Error] Could not record trade: {e}", is_error=True)
                        try:
                            import telegram_notifier
                            telegram_notifier.log_successful_trade(
                                source, headline, TRADE_SYMBOL, config.TRADE_LOTS, 
                                is_retraction=False, trade_direction=impact.trade_direction,
                                ai_result={
                                    "Target": impact.extracted_target,
                                    "Classification": impact.target_classification,
                                    "Geopolitical Reasoning": impact.geopolitical_reasoning,
                                    "Timeline Reasoning": impact.timeline_reasoning,
                                    "Scrape Status": impact.scrape_status,
                                    "Trade Event": impact.is_trade_event, 
                                    "Current Event": impact.is_current_event, 
                                    "Geopolitical": impact.is_geopolitically_significant,
                                    "Duplicate": impact.is_duplicate
                                }
                            )
                        except Exception as e:
                            self._log(f"[Telegram] Failed to notify: {e}")
                    else:
                        error_detail = str(error_detail_or_ticket)
                        self._trade_log(f"Sniper {impact.trade_direction} FAILED — {error_detail}")
                        self._log(f"[MT5 ERROR] {error_detail}", is_error=True)
                        try:
                            import telegram_notifier
                            telegram_notifier.log_failed_trade(source, headline, TRADE_SYMBOL, config.TRADE_LOTS, error_detail=error_detail, trade_direction=impact.trade_direction)
                        except Exception as e:
                            self._log(f"[Telegram] Failed to notify failure: {e}")

                else:
                    self._log(f"[AI Rejected] Passed regex but failed AI criteria.")
                    try:
                        import telegram_notifier
                        telegram_notifier.log_ai_rejection(
                            source, headline, 
                            ai_result={
                                "Target": impact.extracted_target,
                                "Classification": impact.target_classification,
                                "Geopolitical Reasoning": impact.geopolitical_reasoning,
                                "Timeline Reasoning": impact.timeline_reasoning,
                                "Scrape Status": impact.scrape_status,
                                "Regex Trigger": f'"{matched_kw}"',
                                "Trade Event": impact.is_trade_event, 
                                "Current Event": impact.is_current_event, 
                                "Geopolitical": impact.is_geopolitically_significant,
                                "Duplicate": impact.is_duplicate
                            }
                        )
                    except Exception as e:
                        self._log(f"[Telegram] Failed to notify AI rejection: {e}")

            except Exception as e:
                self._log(f"[Consumer Error] {e}", is_error=True)
            finally:
                self._queue.task_done()
