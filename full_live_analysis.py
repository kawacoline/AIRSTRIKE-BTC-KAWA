import os
import re
import json
import sqlite3
import datetime
import urllib.request
import time

LOG_FILE = "logs/bot_history.log"
SUCCESS_FILE = "logs/successful_trades.log"

def get_binance_klines(symbol, start_ts, end_ts):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&startTime={start_ts}&endTime={end_ts}&limit=100"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            time.sleep(1)
    return []

def main():
    events = []
    
    # 1. Accepted Trades (from successful_trades.log)
    if os.path.exists(SUCCESS_FILE):
        with open(SUCCESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "HEADLINE:" in line and "RETRACTION" not in line:
                    # e.g. [2026-05-26 09:31:32 Local] SOURCE: Google News ...
                    m = re.match(r'^\[(.*?) Local\].*HEADLINE:\s*(.*)', line)
                    if not m:
                        m = re.match(r'^\[(.*?)\] SOURCE:.*HEADLINE:\s*(.*)', line)
                    
                    if m:
                        ts_str, hl = m.groups()
                        # Ignore the initial boot burst (01:37 - 01:50) if it somehow got in
                        if "2026-05-26 01:3" in ts_str or "2026-05-26 01:4" in ts_str or "2026-05-26 01:5" in ts_str:
                            continue
                        events.append({
                            "time": ts_str.strip(),
                            "cat": "Accepted (Sovereign)",
                            "hl": hl.strip()[:65]
                        })

    # 2. Rejected Trades (from bot_history.log live polling)
    # We look for [AI] Target=... lines that occurred AFTER the initial boot sequence
    last_candidate_time = None
    last_candidate_hl = None
    
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if "2026-05-26 01:3" in line or "2026-05-26 01:4" in line or "2026-05-26 01:5" in line:
                continue # Skip boot sequence
                
            m_cand = re.match(r'^\[(.*?)\] \[NEWS\].*Candidate[^:]*:\s*(.*)', line)
            if m_cand:
                last_candidate_time, last_candidate_hl = m_cand.groups()
                continue
                
            if "[AI]" in line and "Target=" in line and "Trade Event=False" in line:
                if last_candidate_time and last_candidate_hl:
                    cat = "Rejected (Other)"
                    hl_lower = last_candidate_hl.lower()
                    if "isis" in hl_lower or "al qaeda" in hl_lower or "terrorist" in hl_lower:
                        cat = "Rejected (Counter-Terrorism)"
                    elif "drug" in hl_lower or "boat" in hl_lower or "vessel" in hl_lower:
                        cat = "Rejected (Drug/Cartel)"
                    elif "threat" in hl_lower or "warn" in hl_lower:
                        cat = "Rejected (Political Threat)"
                        
                    events.append({
                        "time": last_candidate_time,
                        "cat": cat,
                        "hl": last_candidate_hl[:65]
                    })
                    last_candidate_time = None
                    last_candidate_hl = None

    # Deduplicate by time to avoid spamming the same minute
    dedup_events = {}
    for ev in events:
        try:
            dt = datetime.datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
        except:
            continue
        ts_minute = dt.strftime("%Y-%m-%d %H:%M")
        if ts_minute not in dedup_events:
            dedup_events[ts_minute] = ev

    final_events = list(dedup_events.values())
    
    md = "# Full Live Market Impact Analysis\n\n"
    md += "This report analyzes all live-polled events (Accepted and Rejected) to evaluate the AI's current filtering logic.\n\n"
    md += "| Timestamp (UTC) | Category | Headline | 5m Impact | 15m Impact | 30m Impact |\n"
    md += "|-----------------|----------|----------|-----------|------------|------------|\n"

    # Sort by category then time
    final_events.sort(key=lambda x: (x["cat"], x["time"]))
    
    count = 0
    for ev in final_events:
        dt = datetime.datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=datetime.timezone.utc)
        ts_ms = int(dt.timestamp() * 1000)
        
        klines = get_binance_klines("BTCUSDT", ts_ms, ts_ms + 35*60*1000)
        if not klines or len(klines) < 30:
            continue
            
        t0_open = float(klines[0][1])
        t5_close = float(klines[5][4]) if len(klines)>5 else t0_open
        t15_close = float(klines[15][4]) if len(klines)>15 else t0_open
        t30_close = float(klines[30][4]) if len(klines)>30 else t0_open
        
        pct_5 = (t5_close - t0_open) / t0_open * 100
        pct_15 = (t15_close - t0_open) / t0_open * 100
        pct_30 = (t30_close - t0_open) / t0_open * 100
        
        c5 = f"{pct_5:+.2f}%"
        c15 = f"{pct_15:+.2f}%"
        c30 = f"{pct_30:+.2f}%"
        
        md += f"| {ev['time']} | **{ev['cat']}** | {ev['hl']} | {c5} | {c15} | {c30} |\n"
        
        count += 1
        if count % 10 == 0:
            print(f"Processed {count} / {len(final_events)} events...")
        time.sleep(0.05)

    with open(r"C:\Users\kawa\.gemini\antigravity-ide\brain\8a84e3d8-c4a1-4338-9451-b40f8c030bd4\market_impact_analysis.md", "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Generated true impact analysis for {count} events.")

if __name__ == "__main__":
    main()
