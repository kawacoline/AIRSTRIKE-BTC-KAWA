import os
import re
import json
import urllib.request
import datetime
import time

LOG_FILE = "logs/bot_history.log"
SUCCESS_FILE = "logs/successful_trades.log"

def get_binance_klines(symbol, start_ts, end_ts):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&startTime={start_ts}&endTime={end_ts}&limit=100"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            time.sleep(1)
    return []

def main():
    accepted_headlines = set()
    if os.path.exists(SUCCESS_FILE):
        with open(SUCCESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "HEADLINE:" in line:
                    hl = line.split("HEADLINE:")[1].strip()
                    hl = re.sub(r'^\[.*?\]\s*', '', hl)
                    accepted_headlines.add(hl)

    candidates = {}
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\[(.*?)\] \[NEWS\].*Candidate:\s*(.*)', line)
            if m:
                ts_str, hl = m.groups()
                hl = hl.strip()
                if hl not in candidates:
                    candidates[hl] = ts_str
                    
    analyzed_events = []
    
    # We want to pick a balanced sample
    cat_counts = {"Accepted (Trade Event)": 0, "Rejected (Drug/Cartel)": 0, "Rejected (Counter-Terrorism)": 0}
    
    for hl, ts_str in candidates.items():
        try:
            dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except:
            continue
            
        dt = dt.replace(tzinfo=datetime.timezone.utc)
        ts_ms = int(dt.timestamp() * 1000)
        
        is_accepted = hl in accepted_headlines or any(hl in ah for ah in accepted_headlines) or any(ah in hl for ah in accepted_headlines)
        
        category = "Ignored/Other"
        if is_accepted:
            category = "Accepted (Trade Event)"
        elif "drug" in hl.lower() or "narco" in hl.lower() or "vessel" in hl.lower() or "boat" in hl.lower():
            category = "Rejected (Drug/Cartel)"
        elif "isis" in hl.lower() or "al qaeda" in hl.lower() or "terrorist" in hl.lower() or "boko haram" in hl.lower() or "militants" in hl.lower():
            category = "Rejected (Counter-Terrorism)"
            
        if category == "Ignored/Other":
            continue
            
        if cat_counts[category] >= 10:
            continue
            
        klines = get_binance_klines("BTCUSDT", ts_ms, ts_ms + 35*60*1000)
        if not klines or len(klines) < 30:
            continue
            
        t0_open = float(klines[0][1])
        t5_close = float(klines[5][4]) if len(klines) > 5 else t0_open
        t15_close = float(klines[15][4]) if len(klines) > 15 else t0_open
        t30_close = float(klines[30][4]) if len(klines) > 30 else t0_open
        
        pct_5 = (t5_close - t0_open) / t0_open * 100
        pct_15 = (t15_close - t0_open) / t0_open * 100
        pct_30 = (t30_close - t0_open) / t0_open * 100
        
        analyzed_events.append({
            "timestamp": ts_str,
            "headline": hl[:65] + "..." if len(hl) > 65 else hl,
            "category": category,
            "pct_5": pct_5,
            "pct_15": pct_15,
            "pct_30": pct_30
        })
        
        cat_counts[category] += 1
        time.sleep(0.1)

    md = "# Historical Market Impact Analysis\n\n"
    md += "This report analyzes a sample of different event types encountered by the bot to verify the market impact of accepted trades vs. rejected events.\n\n"
    md += "| Timestamp (UTC) | Category | Headline | 5m Impact | 15m Impact | 30m Impact |\n"
    md += "|-----------------|----------|----------|-----------|------------|------------|\n"
    
    analyzed_events.sort(key=lambda x: x["category"])
    
    for ev in analyzed_events:
        c5 = f"{ev['pct_5']:+.2f}%"
        c15 = f"{ev['pct_15']:+.2f}%"
        c30 = f"{ev['pct_30']:+.2f}%"
        md += f"| {ev['timestamp']} | **{ev['category']}** | {ev['headline']} | {c5} | {c15} | {c30} |\n"

    with open(r"C:\Users\kawa\.gemini\antigravity-ide\brain\8a84e3d8-c4a1-4338-9451-b40f8c030bd4\market_impact_analysis.md", "w", encoding="utf-8") as f:
        f.write(md)
        
    print("Generated market_impact_analysis.md")

if __name__ == "__main__":
    main()
