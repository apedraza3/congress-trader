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


def fetch_finnhub(symbol="", from_date=None, to_date=None):
    """Fetch congressional trading data from Finnhub API."""
    if not config.FINNHUB_API_KEY:
        logger.warning("No Finnhub API key configured")
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


def fetch_house_disclosures():
    """Fetch from House clerk XML feed as fallback."""
    try:
        year = datetime.utcnow().year
        url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/index.html"
        resp = requests.get(url, timeout=15)
        if not resp.ok:
            logger.warning("House clerk returned %s", resp.status_code)
            return []

        # Parse the HTML table of filings
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        results = []
        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 8:
                continue
            filing_date = cols[1].get_text(strip=True)
            name = cols[0].get_text(strip=True)
            # The clerk table doesn't have ticker detail — it links to the PDF
            # We store what we can and note that detailed parsing requires PDF
            results.append({
                "politician_name": name,
                "filing_date": filing_date,
                "source": "house_clerk",
            })
        return results
    except Exception as e:
        logger.error("House clerk fetch failed: %s", e)
        return []


def parse_finnhub_trade(trade):
    """Convert a Finnhub congressional trading record to our format."""
    tx_date = trade.get("transactionDate", "")
    filing_date = trade.get("filingDate", "")

    # Calculate reporting delay
    delay = 0
    try:
        td = datetime.strptime(tx_date, "%Y-%m-%d")
        fd = datetime.strptime(filing_date, "%Y-%m-%d")
        delay = (fd - td).days
    except (ValueError, TypeError):
        pass

    # Parse amount range
    amount_text = trade.get("amountText", trade.get("transactionAmount", ""))
    amount_min, amount_max = AMOUNT_RANGES.get(str(amount_text), (0, 0))
    if not amount_min:
        # Try numeric fields
        amount_min = trade.get("amountFrom", 0) or 0
        amount_max = trade.get("amountTo", 0) or amount_min

    # Determine transaction type
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


def ingest_new_disclosures():
    """Fetch and store new disclosures. Returns count of new records."""
    trades = fetch_finnhub()
    if not trades:
        logger.info("No new trades from Finnhub")
        return 0

    new_count = 0
    for raw in trades:
        parsed = parse_finnhub_trade(raw)
        if not parsed["ticker"] or not parsed["trade_date"]:
            continue

        disc_id = db.insert_disclosure(parsed)
        if disc_id:
            new_count += 1
            # Upsert politician
            db.upsert_politician(
                parsed["politician_name"],
                party=parsed.get("party", ""),
                state=parsed.get("state", ""),
                chamber=parsed.get("chamber", ""),
            )

    logger.info("Ingested %d new disclosures out of %d fetched", new_count, len(trades))
    return new_count
