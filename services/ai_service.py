"""AI-powered analysis via local Ollama instance."""

import json
import logging
import time

import requests

import config
from services.news_service import get_ticker_news

logger = logging.getLogger(__name__)

# Cache sentiment results to avoid re-analyzing the same ticker
_sentiment_cache = {}
SENTIMENT_CACHE_TTL = 3600  # 1 hour


def is_available():
    """Check if Ollama is reachable."""
    try:
        resp = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5)
        return resp.ok
    except Exception:
        return False


def analyze_sentiment(ticker, politician_name="", trade_context=""):
    """Analyze news sentiment for a ticker using local LLM.

    Returns dict: {
        score: -100 to +100 (negative=bearish, positive=bullish),
        sentiment: "very_bearish"|"bearish"|"neutral"|"bullish"|"very_bullish",
        summary: str (1-2 sentence explanation),
        article_count: int,
        error: str|None,
    }
    """
    if not config.OLLAMA_URL:
        return _default_result("Ollama not configured")

    # Check cache
    now = time.time()
    cache_key = ticker
    if cache_key in _sentiment_cache and (now - _sentiment_cache[cache_key]["ts"]) < SENTIMENT_CACHE_TTL:
        return _sentiment_cache[cache_key]["data"]

    # Fetch news
    articles = get_ticker_news(ticker, days=3, limit=8)
    if not articles:
        result = _default_result(None, note="No recent news found")
        _sentiment_cache[cache_key] = {"data": result, "ts": now}
        return result

    # Build prompt
    news_text = ""
    for i, a in enumerate(articles[:8], 1):
        headline = a.get("headline", "").strip()
        summary = a.get("summary", "").strip()[:300]
        source = a.get("source", "")
        news_text += f"{i}. [{source}] {headline}\n"
        if summary and summary != headline:
            news_text += f"   {summary}\n"
        news_text += "\n"

    context = f"A US Congress member ({politician_name}) recently purchased shares of {ticker}." if politician_name else f"Analyzing stock {ticker}."
    if trade_context:
        context += f" {trade_context}"

    prompt = f"""{context}

Here are the most recent news articles about {ticker}:

{news_text}

Based on these news articles, analyze the short-term sentiment for {ticker} stock. Consider:
1. Is the overall news positive, negative, or neutral for the stock price?
2. Are there any red flags (lawsuits, investigations, earnings misses, downgrades)?
3. Are there positive catalysts (upgrades, partnerships, earnings beats, new products)?
4. Could the politician have insider knowledge based on what's in the news?

Respond with ONLY valid JSON in this exact format, no other text:
{{"score": <integer from -100 to 100>, "sentiment": "<very_bearish|bearish|neutral|bullish|very_bullish>", "summary": "<1-2 sentence explanation>"}}"""

    try:
        resp = requests.post(f"{config.OLLAMA_URL}/api/generate", json={
            "model": config.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200},
        }, timeout=120)

        if not resp.ok:
            logger.error("Ollama request failed: %s", resp.status_code)
            return _default_result(f"Ollama returned {resp.status_code}")

        response_text = resp.json().get("response", "").strip()
        result = _parse_sentiment_response(response_text, len(articles))

        _sentiment_cache[cache_key] = {"data": result, "ts": now}
        return result

    except requests.exceptions.Timeout:
        logger.warning("Ollama timeout for %s sentiment", ticker)
        return _default_result("Ollama request timed out")
    except Exception as e:
        logger.error("AI sentiment analysis failed for %s: %s", ticker, e)
        return _default_result(str(e))


def _parse_sentiment_response(text, article_count):
    """Parse the LLM's JSON response into a structured result."""
    try:
        # Try to extract JSON from the response (LLM might include extra text)
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            score = max(-100, min(100, int(data.get("score", 0))))
            sentiment = data.get("sentiment", "neutral")
            valid_sentiments = {"very_bearish", "bearish", "neutral", "bullish", "very_bullish"}
            if sentiment not in valid_sentiments:
                sentiment = _score_to_sentiment(score)
            return {
                "score": score,
                "sentiment": sentiment,
                "summary": data.get("summary", ""),
                "article_count": article_count,
                "error": None,
            }
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Failed to parse LLM sentiment response: %s", e)

    return _default_result("Failed to parse LLM response")


def _score_to_sentiment(score):
    if score <= -60:
        return "very_bearish"
    elif score <= -20:
        return "bearish"
    elif score <= 20:
        return "neutral"
    elif score <= 60:
        return "bullish"
    return "very_bullish"


def _default_result(error=None, note=None):
    return {
        "score": 0,
        "sentiment": "neutral",
        "summary": note or "",
        "article_count": 0,
        "error": error,
    }
