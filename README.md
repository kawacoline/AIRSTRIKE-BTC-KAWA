# ⚡ Airstrike BTC — Event-Driven Geopolitical Algorithmic Trading System

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python)](https://python.org)
[![MetaTrader 5](https://img.shields.io/badge/Platform-MetaTrader%205-green.svg)](https://www.metatrader5.com)
[![AI Engine](https://img.shields.io/badge/AI-Google%20Gemini%20Flash-orange.svg?logo=google)](https://ai.google.dev)
[![Web3 Market](https://img.shields.io/badge/DeFi-Polymarket%20CLOB-purple.svg)](https://polymarket.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An institutional-grade, fully autonomous **news-to-execution pipeline** designed to capture high-velocity market dislocations in Bitcoin during breaking geopolitical events. 

The engine continuously streams and aggregates raw headlines from 10+ global sources, classifies breaking military/macro events using LLM reasoning (Google Gemini), and executes ultra-low-latency short-sell orders on **MetaTrader 5** and **Polymarket BTC 5m binary markets** in sub-second timeframes.

---

## 🏛️ System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      REAL-TIME INGESTION LAYER                         │
│  7 Concurrent Asynchronous Scrapers (Google News RSS, Finnhub, Reddit,   │
│     LiveUAMap Conflict Tracker, Twitter/Nitter, Major Wire Outlets)     │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ Raw Headlines
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     NLP & CLASSIFICATION ENGINE                         │
│  • Cross-source Jaccard similarity deduplication (<3ms)                 │
│  • Google Gemini Flash reasoning pipeline:                              │
│      - `is_us_airstrike`   → Confirms official military event           │
│      - `is_current_event`  → Rejects historical/stale headlines         │
│      - `is_fake_news`      → Flags retractions / denials                │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ Validated Trade Signal
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      ORDER ROUTING & RISK ENGINE                        │
│  ┌─────────────────────────────────┐   ┌─────────────────────────────┐  │
│  │       MetaTrader 5 Terminal     │   │    Polymarket CLOB API      │  │
│  │ • Direct CTrade IPC execution   │   │ • EIP-712 Order Signing     │  │
│  │ • Dynamic SL (1%) & TP (4%)     │   │ • 5-minute binary put/down  │  │
│  │ • Emergency retraction kill-sw. │   │ • Automatic capital limits  │  │
│  └─────────────────────────────────┘   └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ Key Features

- **Sub-Second Event Ingestion**: Multi-threaded, asynchronous scraping workers polling and streaming from 10+ distinct geopolitical and financial feeds.
- **Cognitive NLP Filtering**: Eliminates false positives by evaluating context, grammar, and source credibility using Gemini LLMs rather than simple keyword matches.
- **Cross-Market Execution**: Simultaneously routes market orders to traditional CFD/forex brokers via MetaTrader 5 and Web3 prediction markets (Polymarket).
- **Automated Retraction Circuit Breaker**: If follow-up news reports a correction, denial, or false alarm, the system immediately closes all open market positions to protect capital.
- **Live Terminal Dashboard**: Rich TUI interface providing real-time feed telemetry, AI latency counters, deduplication rates, and order statuses.
- **Telegram Command & Control**: Real-time trade dispatch notifications, profit/loss telemetry, and remote kill-switch capability.

---

## 📊 Quantitative Mechanics & Risk Management

| Parameter | Configuration | Purpose |
|---|---|---|
| **Primary Symbol** | `BTCUSD` / `BTC 5m Down` | High-beta asset with immediate risk-off response |
| **Stop Loss (SL)** | Dynamic (1% from fill) | Strict capital preservation limit |
| **Take Profit (TP)** | Dynamic (4% from fill) | Captures institutional post-shock liquidity flush |
| **Deduplication** | Token-level Jaccard index | Blocks duplicate execution from syndicated news |
| **Cooldown Window** | 60s per signal / 3 per hr | Prevents over-trading during headline storms |

---

## 🛠️ Installation & Setup

### 1. Prerequisites
- **Operating System**: Windows 10/11 (required for MetaTrader 5 native IPC)
- **Python**: 3.10 or higher
- **MetaTrader 5**: Desktop terminal installed with algorithmic trading enabled

### 2. Repository Clone & Dependencies
```bash
git clone https://github.com/kawacoline/AIRSTRIKE-BTC-KAWA.git
cd AIRSTRIKE-BTC-KAWA
```

Run the automated setup script to provision the virtual environment:
```bash
setup.bat
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and configure your credentials:
```bash
copy .env.example .env
```

Key environment parameters:
```ini
# MetaTrader 5
MT5_LOGIN=your_account_number
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Server

# Gemini AI Engine
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash

# Telegram Notifications
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_USERS=your_user_id

# Risk & Order Sizing
TRADE_LOTS=0.01
SL_PIPS=700
TP_PIPS=200
```

### 4. Running the System
Start the terminal dashboard and execution loops:
```bash
start.bat
```

---

## 📁 Project Structure

```
├── main.py                 # Core TUI dashboard and master event loop
├── scraper_engine.py       # Concurrent multi-feed aggregator
├── ai_filter.py            # Gemini LLM cognitive classifier
├── dedup_engine.py         # Real-time Jaccard deduplication engine
├── mt5_engine.py           # MetaTrader 5 low-latency IPC execution
├── polymarket_engine.py    # Polymarket CLOB binary contract integration
├── telegram_notifier.py    # Asynchronous alerts & remote kill-switch
├── scrapers/               # Modular feed scrapers (RSS, API, LiveUAMap, etc.)
├── config.py               # Centralized runtime configuration
└── requirements.txt        # Production dependencies
```

---

## 👨‍💻 Author

**Hazael**  
*Full Stack Software Engineer & Algorithmic Trading Specialist*  
- **GitHub**: [@kawacoline](https://github.com/kawacoline)  
- **Email**: kawacoline@gmail.com  
- **Portfolio**: [hazael.dev](https://github.com/kawacoline)

---

## ⚖️ Disclaimer

*This software is published for research, educational, and portfolio demonstration purposes. Algorithmic trading involves financial risk. Always test in simulated or demo environments before committing capital.*
