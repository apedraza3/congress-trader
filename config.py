"""Configuration from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24).hex())
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")

# Alpaca
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# Data sources
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# App settings
DB_PATH = os.getenv("DB_PATH", "data/congress.db")
POLL_INTERVAL_HOURS = int(os.getenv("POLL_INTERVAL_HOURS", "2"))
FLASK_PORT = int(os.getenv("FLASK_PORT", "5051"))

# Risk filter defaults
MAX_REPORTING_DELAY_DAYS = int(os.getenv("MAX_REPORTING_DELAY_DAYS", "3"))
MIN_TRADE_AMOUNT = int(os.getenv("MIN_TRADE_AMOUNT", "15000"))
MAX_PRICE_CHANGE_PCT = float(os.getenv("MAX_PRICE_CHANGE_PCT", "5.0"))
MIN_POLITICIAN_WIN_RATE = float(os.getenv("MIN_POLITICIAN_WIN_RATE", "60.0"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "5.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "8.0"))
HOLD_DAYS = int(os.getenv("HOLD_DAYS", "45"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "15"))
