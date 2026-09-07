import asyncio
import json
import urllib.request
import os
from dotenv import load_dotenv

import ai_filter
import mt5_engine

load_dotenv()

class NewsListener:
    def __init__(self, log_callback=None, trade_callback=None):
        self.log_callback = log_callback
        self.trade_callback = trade_callback
        self.ai_model = ai_filter.initialize_ai()
        if not self.ai_model:
            self.log("[Error] AI Filter failed to initialize", is_error=True)
        self.api_key = os.getenv("FINNHUB_API_KEY")
        if self.api_key and "your_" not in self.api_key:
            self.rest_url = f"https://finnhub.io/api/v1/news?category=general&token={self.api_key}"
        else:
            self.rest_url = None
        self.processed_ids = set()

    def log(self, msg, is_error=False, is_trade=False):
        if self.log_callback and not is_trade:
            self.log_callback(msg, is_error)
        if self.trade_callback and is_trade:
             self.trade_callback(msg)

    async def listen(self):
        if not self.rest_url:
            self.log("[Error] API key missing. Configure .env 'FINNHUB_API_KEY'", is_error=True)
            return
        self.log("Starting REST Polling for Finnhub News (Every 5 seconds)..")
        while True:
            try:
                news_data = await asyncio.to_thread(self._fetch_news)
                if news_data:
                    is_initial_run = len(self.processed_ids) == 0
                    if is_initial_run:
                        self.log(f"Initial boot: Silently caching {len(news_data)} past headlines to avoid Gemini rate limits...")
                    for item in news_data:
                        news_id = item.get("id")
                        if news_id:
                            self.processed_ids.add(news_id)
                    if is_initial_run and len(news_data) > 0:
                        most_recent_headline = news_data[0].get("headline", "")
                        if most_recent_headline:
                            self.log(f"--- INITIAL BOOT TEST ---", is_trade=False)
                            await self.process_headline(most_recent_headline)
                            self.log("Caching complete. Now silently listening for breaking news...", is_trade=False)
                    elif not is_initial_run:
                        for item in reversed(news_data):
                            news_id = item.get("id")
                            headline = item.get("headline", "")
                            if news_id and news_id not in self.processed_ids:
                                self.processed_ids.add(news_id)
                                await self.process_headline(headline)
            except Exception as e:
                self.log(f"Polling Error: {e}", is_error=True)
            await asyncio.sleep(5)

    def _fetch_news(self):
        try:
            req = urllib.request.Request(self.rest_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = response.read().decode('utf-8')
                    return json.loads(data)
        except Exception as e:
             raise e
        return []

    async def process_headline(self, headline: str):
        self.log(f"NEW HEADLINE DETECTED: {headline}")
        if not self.ai_model:
            return
        impact = ai_filter.evaluate_headline(self.ai_model, headline)
        self.log(f"AI Evaluation -> Trade Event: {impact.is_trade_event}, Current: {impact.is_current_event}, Fake/Retraction: {impact.is_fake_news_retraction}")
        if impact.is_fake_news_retraction:
            self.log("WARNING: Fake News Retraction detected! Executing Emergency Close.", is_error=True, is_trade=True)
            trade_symbol = os.getenv("TRADE_SYMBOL", "BTCUSD")
            mt5_engine.emergency_close_all(symbol=trade_symbol)
        elif impact.is_trade_event and impact.trade_direction in ['BUY', 'SELL'] and impact.is_current_event:
            self.log("ALERT: Breaking Trade Event detected! Executing Sniper.", is_error=False, is_trade=True)
            trade_symbol = os.getenv("TRADE_SYMBOL", "BTCUSD")
            success = mt5_engine.execute_sniper_sell(symbol=trade_symbol, lots=0.1)
            if success:
                 self.log(f"Sniper Sell Executed Successfully on {trade_symbol}.", is_error=False, is_trade=True)
            else:
                 self.log(f"Sniper Sell Failed on {trade_symbol}. Verify symbol name in Market Watch.", is_error=True, is_trade=True)

if __name__ == "__main__":
    def print_log(msg, error=False):
        print(f"LOG: {msg}")
    listener = NewsListener(log_callback=print_log)
    asyncio.run(listener.listen())
