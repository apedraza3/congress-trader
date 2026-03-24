"""News fetching for ticker sentiment analysis."""

import logging
import time
from datetime import datetime, timedelta

import requests

import config

logger = logging.getLogger(__name__)

# Simple cache to avoid refetching news for the same ticker within a scoring run
_news_cache = {}
NEWS_CACHE_TTL = 3600  # 1 hour


def get_ticker_news(ticker, days=3, limit=10):
    """Fetch recent news articles for a ticker.

    Tries Finnhub first (company news endpoint), falls back to FMP.
    Returns list of dicts: [{headline, summary, source, datetime, url}, ...]
    """
    now = time.time()
    cache_key = f"{ticker}:{days}"
    if cache_key in _news_cache and (now - _news_cache[cache_key]["ts"]) < NEWS_CACHE_TTL:
        return _news_cache[cache_key]["data"]

    articles = []

    # Try Finnhub company news
    if config.FINNHUB_API_KEY:
        articles = _fetch_finnhub_news(ticker, days, limit)

    # Fallback to FMP
    if not articles and config.FMP_API_KEY:
        articles = _fetch_fmp_news(ticker, limit)

    _news_cache[cache_key] = {"data": articles, "ts": now}
    return articles


def _fetch_finnhub_news(ticker, days=3, limit=10):
    """Fetch company news from Finnhub."""
    try:
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        resp = requests.get("https://finnhub.io/api/v1/company-news", params={
            "symbol": ticker,
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
            "token": config.FINNHUB_API_KEY,
        }, timeout=10)
        if not resp.ok:
            logger.warning("Finnhub news failed for %s: %s", ticker, resp.status_code)
            return []
        data = resp.json()
        articles = []
        for item in data[:limit]:
            articles.append({
                "headline": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "source": item.get("source", ""),
                "datetime": item.get("datetime", 0),
                "url": item.get("url", ""),
            })
        return articles
    except Exception as e:
        logger.error("Finnhub news error for %s: %s", ticker, e)
        return []


def _fetch_fmp_news(ticker, limit=10):
    """Fetch stock news from Financial Modeling Prep."""
    try:
        resp = requests.get(f"https://financialmodelingprep.com/api/v3/stock_news", params={
            "tickers": ticker,
            "limit": limit,
            "apikey": config.FMP_API_KEY,
        }, timeout=10)
        if not resp.ok:
            return []
        data = resp.json()
        articles = []
        for item in data[:limit]:
            articles.append({
                "headline": item.get("title", ""),
                "summary": item.get("text", "")[:500],
                "source": item.get("site", ""),
                "datetime": item.get("publishedDate", ""),
                "url": item.get("url", ""),
            })
        return articles
    except Exception as e:
        logger.error("FMP news error for %s: %s", ticker, e)
        return []
