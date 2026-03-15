"""Stock price lookups via yfinance."""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_price_at_date(ticker, date_str):
    """Get closing price for a ticker on a specific date."""
    try:
        import yfinance as yf
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start = dt - timedelta(days=5)  # buffer for weekends
        end = dt + timedelta(days=3)
        data = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                           end=end.strftime("%Y-%m-%d"), progress=False)
        if data.empty:
            return 0
        # Find closest date
        target = dt.strftime("%Y-%m-%d")
        if target in data.index.strftime("%Y-%m-%d"):
            return float(data.loc[target]["Close"].iloc[0]) if hasattr(data.loc[target]["Close"], "iloc") else float(data.loc[target]["Close"])
        # Return last available before target
        before = data[data.index <= dt]
        if not before.empty:
            close = before.iloc[-1]["Close"]
            return float(close.iloc[0]) if hasattr(close, "iloc") else float(close)
        return 0
    except Exception as e:
        logger.error("yfinance price lookup failed for %s@%s: %s", ticker, date_str, e)
        return 0


def get_current_price(ticker):
    """Get current/latest price for a ticker."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = getattr(info, "last_price", 0) or 0
        if not price:
            hist = t.history(period="1d")
            if not hist.empty:
                close = hist.iloc[-1]["Close"]
                price = float(close.iloc[0]) if hasattr(close, "iloc") else float(close)
        return float(price)
    except Exception as e:
        logger.error("yfinance current price failed for %s: %s", ticker, e)
        return 0


def get_price_change_pct(ticker, from_date_str):
    """Calculate price change % from a date to now."""
    price_then = get_price_at_date(ticker, from_date_str)
    price_now = get_current_price(ticker)
    if not price_then or not price_now:
        return 0, price_then, price_now
    pct = ((price_now - price_then) / price_then) * 100
    return round(pct, 2), price_then, price_now


def get_sp500_price():
    """Get current S&P 500 price for benchmark."""
    return get_current_price("SPY")
