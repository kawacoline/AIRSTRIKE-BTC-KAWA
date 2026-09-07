import urllib.request
import json
import datetime
import time

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

events = [
    {"time": "2026-05-26 09:31:00", "cat": "Accepted (Sovereign)", "hl": "U.S. military conducted self-defense strikes in southern Iran"},
    {"time": "2026-05-26 08:18:00", "cat": "Accepted (Sovereign)", "hl": "US launches new strikes targeting Iranian naval base"},
    {"time": "2026-05-28 08:22:00", "cat": "Accepted (Sovereign)", "hl": "Oil Shoots Up After Iran And US Exchange Air Strikes"},
    {"time": "2026-05-27 08:35:00", "cat": "Accepted (Sovereign)", "hl": "Israel hits Lebanon with huge bombing raid"},
    {"time": "2026-06-24 13:08:00", "cat": "Rejected (Counter-Terrorism)", "hl": "Senior ISIS leader killed in US airstrike in northwest Syria"},
    {"time": "2026-06-22 14:13:00", "cat": "Rejected (Empty Threat)", "hl": "Progress Cited in U.S.-Iran Talks Despite Trump's Threat to Resume Bombing"}
]

md = "# TRUE Historical Market Impact Analysis\n\n"
md += "This report analyzes the ACTUAL real-time breaking moments of the news to prove the market impact of Sovereign vs Counter-Terrorism strikes.\n\n"
md += "| Timestamp (UTC) | Category | Headline | 5m Impact | 15m Impact | 30m Impact |\n"
md += "|-----------------|----------|----------|-----------|------------|------------|\n"

for ev in events:
    dt = datetime.datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(tzinfo=datetime.timezone.utc)
    ts_ms = int(dt.timestamp() * 1000)
    
    klines = get_binance_klines("BTCUSDT", ts_ms, ts_ms + 35*60*1000)
    if not klines or len(klines) < 30:
        md += f"| {ev['time']} | **{ev['cat']}** | {ev['hl']} | N/A | N/A | N/A |\n"
        continue
        
    t0_open = float(klines[0][1])
    t5_close = float(klines[5][4])
    t15_close = float(klines[15][4])
    t30_close = float(klines[30][4])
    
    pct_5 = (t5_close - t0_open) / t0_open * 100
    pct_15 = (t15_close - t0_open) / t0_open * 100
    pct_30 = (t30_close - t0_open) / t0_open * 100
    
    c5 = f"{pct_5:+.2f}%"
    c15 = f"{pct_15:+.2f}%"
    c30 = f"{pct_30:+.2f}%"
    
    md += f"| {ev['time']} | **{ev['cat']}** | {ev['hl']} | {c5} | {c15} | {c30} |\n"

with open(r"C:\Users\kawa\.gemini\antigravity-ide\brain\8a84e3d8-c4a1-4338-9451-b40f8c030bd4\market_impact_analysis.md", "w", encoding="utf-8") as f:
    f.write(md)

print("Generated true impact analysis")
