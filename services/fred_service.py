"""FRED API recession guard — Sahm Rule + Unemployment Rate."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

# Module-level cache (FRED data updates monthly, no need to hit it every score call)
_cache = {"result": None, "timestamp": 0}
CACHE_TTL = 6 * 3600  # 6 hours


def _fetch_fred_csv(series_id, limit=12):
    """Fetch latest observations from FRED CSV endpoint (no API key needed)."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    try:
        resp = requests.get(url, params={"id": series_id}, timeout=15)
        if not resp.ok:
            logger.error("FRED CSV fetch failed for %s: %s", series_id, resp.status_code)
            return []
        lines = resp.text.strip().split("\n")
        rows = []
        for line in lines[1:]:  # skip header
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() != ".":
                try:
                    rows.append({"date": parts[0].strip(), "value": float(parts[1].strip())})
                except ValueError:
                    continue
        return rows[-limit:] if rows else []
    except Exception as e:
        logger.error("FRED fetch error for %s: %s", series_id, e)
        return []


def get_sahm_rule():
    """Get latest Sahm Rule indicator value. >0.5 = recession signal."""
    data = _fetch_fred_csv("SAHMREALTIME", limit=3)
    if not data:
        return {"value": None, "date": None, "recession_signal": False, "error": "No data"}
    latest = data[-1]
    return {
        "value": latest["value"],
        "date": latest["date"],
        "recession_signal": latest["value"] > 0.5,
        "error": None,
    }


def get_unemployment_rate():
    """Get latest unemployment rate (UNRATE)."""
    data = _fetch_fred_csv("UNRATE", limit=3)
    if not data:
        return {"value": None, "date": None, "error": "No data"}
    latest = data[-1]
    return {
        "value": latest["value"],
        "date": latest["date"],
        "error": None,
    }


def is_recession_active():
    """Check if recession guard should block trades.

    Returns (blocked: bool, reason: str, details: dict).
    Results are cached for 6 hours since FRED data updates monthly.
    """
    now = time.time()
    if _cache["result"] is not None and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["result"]

    sahm = get_sahm_rule()
    unemployment = get_unemployment_rate()
    details = {"sahm": sahm, "unemployment": unemployment}

    if sahm.get("recession_signal"):
        result = (True, f"Sahm Rule at {sahm['value']:.2f} (>0.50 threshold)", details)
    else:
        result = (False, "No recession signal", details)

    _cache["result"] = result
    _cache["timestamp"] = now
    return result
