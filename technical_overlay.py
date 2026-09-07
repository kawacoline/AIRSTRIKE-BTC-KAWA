import time
import asyncio
import MetaTrader5 as mt5

import config
import polymarket_engine

# Global flag to track if we are in an active "Airstrike Mode" where we should scale in.
# This will be activated by scraper_engine when a primary strike hits.
_active_airstrike_markets = []
_airstrike_activation_time = 0
_last_5m_engulfing_time = 0
_last_15m_engulfing_time = 0

def activate_scale_in_mode(markets):
    """
    Called by scraper_engine right after the primary Polymarket bet.
    Saves the market object so the background daemon can buy more contracts.
    """
    global _active_airstrike_markets, _airstrike_activation_time
    _active_airstrike_markets = markets if isinstance(markets, list) else [markets]
    _airstrike_activation_time = time.time()
    print(f"[Technical Overlay] Activated scale-in mode for {len(_active_airstrike_markets)} markets.")

def is_bearish_engulfing(rates):
    """
    Takes 2 candles: rates[0] is previous, rates[1] is current/recent completed.
    Returns True if rates[1] bearishly engulfs rates[0].
    """
    if len(rates) < 2:
        return False
        
    prev = rates[0]
    curr = rates[1]
    
    # Previous must be bullish
    prev_bullish = prev['close'] > prev['open']
    if not prev_bullish:
        return False
        
    # Current must be bearish
    curr_bearish = curr['close'] < curr['open']
    if not curr_bearish:
        return False
        
    # Engulfing condition: curr open >= prev close AND curr close <= prev open
    if curr['open'] >= prev['close'] and curr['close'] <= prev['open']:
        # Strict engulfing requires body to completely cover previous body
        body_prev = abs(prev['close'] - prev['open'])
        body_curr = abs(curr['close'] - curr['open'])
        if body_curr > body_prev:
            return True
            
    return False

def check_timeframe_engulfing(timeframe, timeframe_name):
    """
    Fetches the last 2 completed candles from MT5 and checks for engulfing.
    Returns the timestamp of the current engulfing candle if True, else 0.
    """
    # Fetch 3 candles: [0]=prev completed, [1]=last completed, [2]=current active
    # We want to check if [1] engulfed [0].
    rates = mt5.copy_rates_from_pos(config.TRADE_SYMBOL, timeframe, 1, 2)
    if rates is None or len(rates) < 2:
        return 0
        
    if is_bearish_engulfing(rates):
        return rates[1]['time']
    return 0

def poll_technical_scale_in():
    """
    Background daemon loop that checks for bearish engulfing patterns.
    """
    global _active_airstrike_markets, _last_5m_engulfing_time, _last_15m_engulfing_time
    
    print("[Technical Overlay] Scale-in daemon started.")
    
    while True:
        try:
            if _active_airstrike_markets:
                # If market expired or it's been more than 4 hours, deactivate
                now = time.time()
                if now - _airstrike_activation_time > 4 * 3600:
                    print("[Technical Overlay] Airstrike scale-in mode expired (4 hours).")
                    _active_airstrike_markets = []
                    continue
                
                if mt5.terminal_info() is None:
                    # MT5 disconnected
                    time.sleep(5)
                    continue
                    
                # Check 5m
                t_5m = check_timeframe_engulfing(mt5.TIMEFRAME_M5, "5m")
                if t_5m > 0 and t_5m > _last_5m_engulfing_time:
                    _last_5m_engulfing_time = t_5m
                    for market in _active_airstrike_markets:
                        print(f"[Technical Overlay] 5M Bearish Engulfing Detected! Scaling into Polymarket at ${config.POLY_BET_SIZE}...")
                        polymarket_engine.execute_polymarket_bet(market, bet_size_usd=config.POLY_BET_SIZE)
                    
        except Exception as e:
            print(f"[Technical Overlay] Error in poll loop: {e}")
            
        time.sleep(10) # Poll every 10 seconds
