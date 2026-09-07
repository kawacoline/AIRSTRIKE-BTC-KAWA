import asyncio
import json
import logging
from datetime import datetime
import websockets
from scrapers import RawHeadline

logger = logging.getLogger("TreeNewsWS")

class TreeNewsWebsocketScraper:
    def __init__(self):
        self.name = "tree_news_ws"
        self.ws_url = "wss://news.treeofalpha.com/ws"
        self.interval = 0  # Not used for streaming

    async def listen(self, queue: asyncio.Queue, processed_ids: set, status_dict: dict):
        status_dict[self.name] = {
            "last_poll": datetime.utcnow().timestamp(),
            "headlines_session": 0,
            "status": "Connecting...",
        }
        
        while True:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    status_dict[self.name]["status"] = "Connected"
                    logger.info(f"[{self.name}] Connected to Tree News WebSocket.")
                    
                    while True:
                        msg = await ws.recv()
                        status_dict[self.name]["last_poll"] = datetime.utcnow().timestamp()
                        
                        try:
                            data = json.loads(msg)
                            # Tree News typically sends JSON objects
                            title = data.get("title", "")
                            body = data.get("body", "")
                            source = data.get("source", "TreeNews")
                            url = data.get("url", "")
                            
                            if not title and not body:
                                continue
                                
                            text = f"{title} - {body}" if title and body else title or body
                            
                            # Clean up HTML tags or newlines if any
                            text = text.replace("\n", " ").strip()
                            
                            headline = RawHeadline(text=text, source=f"TreeNews/{source}", url=url)
                            
                            fingerprint = headline.text[:60]
                            if fingerprint not in processed_ids:
                                processed_ids.add(fingerprint)
                                if not queue.full():
                                    await queue.put(headline)
                                    status_dict[self.name]["headlines_session"] += 1
                                    
                        except json.JSONDecodeError:
                            # Might be a ping or raw text string
                            pass
                            
            except Exception as e:
                status_dict[self.name]["status"] = f"Error: {str(e)[:30]}"
                logger.error(f"[{self.name}] WebSocket Error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
