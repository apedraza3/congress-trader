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


def get_technical_signals(ticker):
    """Compute MACD (12/26/9) and 200-day EMA for a ticker.

    Returns dict with macd_bullish, above_200ema, and raw values.
    """
    try:
        import yfinance as yf
        import pandas as pd

        data = yf.download(ticker, period="2y", progress=False)
        if data.empty or len(data) < 200:
            return {"macd_bullish": False, "above_200ema": False, "error": "Insufficient data"}

        close = data["Close"].squeeze()

        # MACD: 12-day EMA - 26-day EMA, signal: 9-day EMA of MACD
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line

        # 200-day EMA
        ema_200 = close.ewm(span=200, adjust=False).mean()

        latest_price = float(close.iloc[-1])
        latest_hist = float(histogram.iloc[-1])
        prev_hist = float(histogram.iloc[-2])
        latest_ema200 = float(ema_200.iloc[-1])

        # Bullish: histogram positive (or just crossed from negative to positive)
        macd_bullish = latest_hist > 0 or (latest_hist > prev_hist and prev_hist < 0)
        above_200ema = latest_price > latest_ema200

        return {
            "macd_bullish": macd_bullish,
            "above_200ema": above_200ema,
            "macd_histogram": round(latest_hist, 4),
            "macd_prev_histogram": round(prev_hist, 4),
            "price": round(latest_price, 2),
            "ema_200": round(latest_ema200, 2),
            "error": None,
        }
    except Exception as e:
        logger.error("Technical signal computation failed for %s: %s", ticker, e)
        return {"macd_bullish": False, "above_200ema": False, "error": str(e)}


def get_historical_technical_signals(ticker, date_str):
    """Compute MACD + 200 EMA as of a historical date (for backtesting)."""
    try:
        import yfinance as yf
        import pandas as pd

        end_dt = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
        start_dt = end_dt - timedelta(days=600)  # ~2 years of trading days
        data = yf.download(ticker, start=start_dt.strftime("%Y-%m-%d"),
                           end=end_dt.strftime("%Y-%m-%d"), progress=False)
        if data.empty or len(data) < 200:
            return {"macd_bullish": False, "above_200ema": False, "error": "Insufficient data"}

        close = data["Close"].squeeze()

        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line
        ema_200 = close.ewm(span=200, adjust=False).mean()

        latest_price = float(close.iloc[-1])
        latest_hist = float(histogram.iloc[-1])
        prev_hist = float(histogram.iloc[-2])
        latest_ema200 = float(ema_200.iloc[-1])

        macd_bullish = latest_hist > 0 or (latest_hist > prev_hist and prev_hist < 0)
        above_200ema = latest_price > latest_ema200

        return {
            "macd_bullish": macd_bullish,
            "above_200ema": above_200ema,
            "macd_histogram": round(latest_hist, 4),
            "price": round(latest_price, 2),
            "ema_200": round(latest_ema200, 2),
            "error": None,
        }
    except Exception as e:
        logger.error("Historical technical signals failed for %s@%s: %s", ticker, date_str, e)
        return {"macd_bullish": False, "above_200ema": False, "error": str(e)}
