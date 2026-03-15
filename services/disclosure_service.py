"""Fetch congressional trade disclosures from public sources."""

import json
import logging
from datetime import datetime, timedelta

import requests

import config
from services import db

logger = logging.getLogger(__name__)

# Amount range mapping from disclosure text
AMOUNT_RANGES = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, 100000000),
}


# ── Primary: Financial Modeling Prep ──────────────────────────────────

def fetch_fmp_senate(page=0):
    """Fetch Senate trading disclosures from FMP."""
    if not config.FMP_API_KEY:
        logger.warning("No FMP API key configured")
        return []

    url = "https://financialmodelingprep.com/stable/senate-trading"
    params = {"apikey": config.FMP_API_KEY, "page": page}

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.ok:
            return resp.json() if isinstance(resp.json(), list) else []
        logger.error("FMP Senate API error %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("FMP Senate fetch failed: %s", e)
    return []


def fetch_fmp_house(page=0):
    """Fetch House trading disclosures from FMP."""
    if not config.FMP_API_KEY:
        logger.warning("No FMP API key configured")
        return []

    url = "https://financialmodelingprep.com/stable/house-trading"
    params = {"apikey": config.FMP_API_KEY, "page": page}

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.ok:
            return resp.json() if isinstance(resp.json(), list) else []
        logger.error("FMP House API error %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("FMP House fetch failed: %s", e)
    return []


def parse_fmp_trade(trade, chamber):
    """Convert an FMP congressional trading record to our format."""
    tx_date = trade.get("transactionDate", trade.get("transaction_date", ""))
    filing_date = trade.get("disclosureDate", trade.get("disclosure_date", ""))

    # Calculate reporting delay
    delay = 0
    try:
        td = datetime.strptime(tx_date[:10], "%Y-%m-%d")
        fd = datetime.strptime(filing_date[:10], "%Y-%m-%d")
        delay = (fd - td).days
    except (ValueError, TypeError):
        pass

    # Parse amount range
    amount_text = trade.get("amount", trade.get("transactionAmount", ""))
    amount_min, amount_max = AMOUNT_RANGES.get(str(amount_text), (0, 0))
    if not amount_min:
        amount_min = trade.get("amountFrom", 0) or 0
        amount_max = trade.get("amountTo", 0) or amount_min

    # Determine transaction type
    tx_type_raw = (trade.get("type", trade.get("transactionType", "")) or "").lower()
    if "purchase" in tx_type_raw or "buy" in tx_type_raw:
        tx_type = "purchase"
    elif "sale" in tx_type_raw and "full" in tx_type_raw:
        tx_type = "sale_full"
    elif "sale" in tx_type_raw:
        tx_type = "sale_partial"
    elif "exchange" in tx_type_raw:
        tx_type = "exchange"
    else:
        tx_type = tx_type_raw or "unknown"

    # FMP uses different field names for Senate vs House
    name = (trade.get("representative", "") or
            trade.get("senator", "") or
            trade.get("name", "") or
            trade.get("firstName", "") + " " + trade.get("lastName", "")).strip()

    ticker = trade.get("ticker", trade.get("symbol", trade.get("asset", "")))

    return {
        "politician_name": name,
        "ticker": ticker,
        "trade_date": tx_date[:10] if tx_date else "",
        "filing_date": filing_date[:10] if filing_date else "",
        "amount_min": amount_min,
        "amount_max": amount_max,
        "tx_type": tx_type,
        "chamber": chamber,
        "party": trade.get("party", ""),
        "state": trade.get("state", trade.get("district", "")),
        "reporting_delay_days": delay,
        "raw_json": json.dumps(trade),
    }


# ── Fallback: Finnhub ────────────────────────────────────────────────

def fetch_finnhub(symbol="", from_date=None, to_date=None):
    """Fetch congressional trading data from Finnhub API (fallback)."""
    if not config.FINNHUB_API_KEY:
        return []

    if not from_date:
        from_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not to_date:
        to_date = datetime.utcnow().strftime("%Y-%m-%d")

    url = "https://finnhub.io/api/v1/stock/congressional-trading"
    params = {
        "from": from_date,
        "to": to_date,
        "token": config.FINNHUB_API_KEY,
    }
    if symbol:
        params["symbol"] = symbol

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.ok:
            data = resp.json()
            return data.get("data", []) if isinstance(data, dict) else data
        logger.error("Finnhub API error %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("Finnhub fetch failed: %s", e)
    return []


def parse_finnhub_trade(trade):
    """Convert a Finnhub congressional trading record to our format."""
    tx_date = trade.get("transactionDate", "")
    filing_date = trade.get("filingDate", "")

    delay = 0
    try:
        td = datetime.strptime(tx_date, "%Y-%m-%d")
        fd = datetime.strptime(filing_date, "%Y-%m-%d")
        delay = (fd - td).days
    except (ValueError, TypeError):
        pass

    amount_text = trade.get("amountText", trade.get("transactionAmount", ""))
    amount_min, amount_max = AMOUNT_RANGES.get(str(amount_text), (0, 0))
    if not amount_min:
        amount_min = trade.get("amountFrom", 0) or 0
        amount_max = trade.get("amountTo", 0) or amount_min

    tx_type_raw = (trade.get("transactionType", "") or "").lower()
    if "purchase" in tx_type_raw or "buy" in tx_type_raw:
        tx_type = "purchase"
    elif "sale" in tx_type_raw and "full" in tx_type_raw:
        tx_type = "sale_full"
    elif "sale" in tx_type_raw:
        tx_type = "sale_partial"
    elif "exchange" in tx_type_raw:
        tx_type = "exchange"
    else:
        tx_type = tx_type_raw or "unknown"

    return {
        "politician_name": trade.get("name", trade.get("representative", "")),
        "ticker": trade.get("symbol", ""),
        "trade_date": tx_date,
        "filing_date": filing_date,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "tx_type": tx_type,
        "chamber": trade.get("chamber", ""),
        "party": trade.get("party", ""),
        "state": trade.get("state", ""),
        "reporting_delay_days": delay,
        "raw_json": json.dumps(trade),
    }


# ── Fallback: House Clerk ────────────────────────────────────────────

def fetch_house_disclosures():
    """Fetch from House clerk HTML table as last-resort fallback."""
    try:
        year = datetime.utcnow().year
        url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/index.html"
        resp = requests.get(url, timeout=15)
        if not resp.ok:
            logger.warning("House clerk returned %s", resp.status_code)
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        results = []
        rows = table.find_all("tr")[1:]
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 8:
                continue
            filing_date = cols[1].get_text(strip=True)
            name = cols[0].get_text(strip=True)
            results.append({
                "politician_name": name,
                "filing_date": filing_date,
                "source": "house_clerk",
            })
        return results
    except Exception as e:
        logger.error("House clerk fetch failed: %s", e)
        return []


# ── Ingestion pipeline ───────────────────────────────────────────────

def _ingest_trades(raw_trades, parser, source_name):
    """Common ingestion logic for parsed trades."""
    new_count = 0
    for raw in raw_trades:
        parsed = parser(raw) if not isinstance(raw, tuple) else parser(*raw)
        if not parsed["ticker"] or not parsed["trade_date"]:
            continue

        disc_id = db.insert_disclosure(parsed)
        if disc_id:
            new_count += 1
            db.upsert_politician(
                parsed["politician_name"],
                party=parsed.get("party", ""),
                state=parsed.get("state", ""),
                chamber=parsed.get("chamber", ""),
            )

    logger.info("[%s] Ingested %d new disclosures out of %d fetched",
                source_name, new_count, len(raw_trades))
    return new_count


def ingest_new_disclosures():
    """Fetch and store new disclosures. Tries FMP first, falls back to Finnhub."""
    total_new = 0

    # Try FMP first (free, both chambers)
    if config.FMP_API_KEY:
        senate = fetch_fmp_senate()
        house = fetch_fmp_house()
        if senate or house:
            total_new += _ingest_trades(
                senate, lambda t: parse_fmp_trade(t, "Senate"), "FMP-Senate")
            total_new += _ingest_trades(
                house, lambda t: parse_fmp_trade(t, "House"), "FMP-House")
            logger.info("FMP total: %d new disclosures", total_new)
            return total_new

    # Fallback to Finnhub
    if config.FINNHUB_API_KEY:
        trades = fetch_finnhub()
        if trades:
            total_new = _ingest_trades(trades, parse_finnhub_trade, "Finnhub")
            return total_new

    logger.warning("No data source returned results")
    return 0
