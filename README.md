# Congress Trader

An automated stock trading system that analyzes congressional STOCK Act filings and executes trades based on a multi-factor risk scoring engine. The system ingests real-time disclosure data from multiple sources, evaluates each filing through configurable risk filters, and manages positions via the Alpaca brokerage API.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask)
![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Alpaca](https://img.shields.io/badge/Alpaca-Trading_API-FFDC00)

## How It Works

```
Congressional Disclosure → Risk Scoring → Trade Execution → Position Monitoring → Analytics
```

1. **Ingest** — Polls congressional trading disclosures from Financial Modeling Prep (Senate + House), with fallback to Finnhub and House Clerk HTML scraping
2. **Score** — Each disclosure is evaluated through a 5-factor risk model (0–100 points)
3. **Execute** — Passing disclosures trigger market buy orders via Alpaca with calculated position sizing and stop-losses
4. **Monitor** — Background jobs track open positions, enforce stop-losses, and auto-close positions after the hold period expires
5. **Analyze** — Portfolio snapshots, P&L tracking, politician leaderboards, and performance vs. S&P 500

## Risk Scoring Engine

Every congressional disclosure is scored across five weighted factors:

| Factor | Max Points | Criteria |
|--------|-----------|----------|
| **Reporting Delay** | 40 | Days between trade date and filing date — faster filings signal stronger conviction |
| **Transaction Type** | 10 | Purchases only — sales are excluded as potential exit signals |
| **Trade Size** | 20 | Minimum $15K threshold filters out noise; larger trades score higher |
| **Price Momentum** | 15 | Rejects tickers that have already moved >5% since the trade date |
| **Politician Track Record** | 15 | Win rate ≥60% required for politicians with 5+ historical trades |

A disclosure **passes** only if all five factors are met (zero fail reasons). All thresholds are configurable via the settings page.

## Position Management

| Control | Default | Purpose |
|---------|---------|---------|
| Max Position Size | 5% of portfolio | Diversification |
| Stop-Loss | 8% below entry | Downside protection |
| Hold Period | 45 days | Time-boxed thesis |
| Max Open Positions | 15 | Portfolio concentration limit |

**Exit triggers:**
- Stop-loss hit → automatic sell, marked as "stopped"
- Hold period elapsed → automatic sell, marked as "closed"
- Manual close via UI → calculate final P&L

## Features

- **Disclosure Feed** — Real-time stream of congressional trades with risk scores, filterable by type and score threshold
- **Trade Management** — View open/closed positions, P&L tracking, manual close capability
- **Politician Leaderboard** — Ranked by win rate, average return, and reporting speed with party/chamber filters
- **Analytics Dashboard** — Cumulative P&L chart, sector breakdown, top winners/losers, S&P 500 comparison
- **Configurable Settings** — All risk filters, position sizing, and API connections tunable without restart
- **Multi-Source Ingestion** — Graceful fallback chain (FMP → Finnhub → House Clerk HTML scraper)
- **Paper Trading** — Alpaca paper mode by default for risk-free testing

## Architecture

```
congress-trader/
├── app.py                        # Flask routes & API endpoints
├── config.py                     # Environment configuration
├── services/
│   ├── db.py                     # SQLite schema, CRUD, WAL mode
│   ├── disclosure_service.py     # Multi-source disclosure ingestion
│   ├── scoring_service.py        # 5-factor risk scoring engine
│   ├── alpaca_service.py         # Brokerage integration & position mgmt
│   └── market_service.py         # Price lookups via yfinance
├── templates/
│   ├── feed.html                 # Disclosure stream
│   ├── trades.html               # Position tracking
│   ├── politicians.html          # Leaderboard
│   ├── analytics.html            # Charts & performance
│   └── settings.html             # Configuration
└── static/
    ├── css/app.css               # Dark theme UI
    └── js/app.js                 # API utilities & formatters
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.13, Flask 3.1, Gunicorn |
| **Database** | SQLite with WAL mode for concurrent access |
| **Trading** | Alpaca Trade API (paper or live) |
| **Market Data** | yfinance (free, no API key required) |
| **Data Sources** | Financial Modeling Prep, Finnhub, House Clerk (HTML scraping) |
| **Scraping** | BeautifulSoup4, lxml |
| **Scheduling** | APScheduler (background polling & position checks) |
| **Deployment** | Docker + Docker Compose |

## Setup

### Prerequisites
- Docker and Docker Compose
- API keys: [Alpaca](https://alpaca.markets/) (free paper trading), [Financial Modeling Prep](https://financialmodelingprep.com/) (free tier), [Finnhub](https://finnhub.io/) (free tier)

### Quick Start

```bash
git clone https://github.com/apedraza3/congress-trader.git
cd congress-trader
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
```

The app runs at `http://localhost:5051`.

### Environment Variables

```env
# Security
SECRET_KEY=<random-secret>
AUTH_PASSWORD=<login-password>

# Alpaca (paper trading by default)
ALPACA_API_KEY=<key>
ALPACA_SECRET_KEY=<secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Data Sources
FMP_API_KEY=<key>
FINNHUB_API_KEY=<key>

# Risk Filters (all tunable via UI)
MAX_REPORTING_DELAY_DAYS=3
MIN_TRADE_AMOUNT=15000
MAX_PRICE_CHANGE_PCT=5.0
MIN_POLITICIAN_WIN_RATE=60.0

# Position Sizing
MAX_POSITION_PCT=5.0
STOP_LOSS_PCT=8.0
HOLD_DAYS=45
MAX_OPEN_POSITIONS=15
```

## Database Schema

Five core tables with proper indexing and unique constraints:

- **disclosures** — Raw congressional filings with risk scores and pass/fail status
- **politicians** — Aggregated stats: win rate, average return, trade count
- **trades** — Open/closed positions with entry/exit prices, P&L, stop-loss levels
- **portfolio_snapshots** — Daily portfolio value, cash, invested, and S&P 500 benchmark
- **settings** — Persisted configuration (survives restarts)

Duplicate prevention via `UNIQUE(politician_name, ticker, trade_date, tx_type)`.

## Disclaimer

This project is for **educational and research purposes only**. Congressional trading data is publicly available under the STOCK Act. The default configuration uses Alpaca's paper trading mode. Use at your own risk if switching to live trading.
