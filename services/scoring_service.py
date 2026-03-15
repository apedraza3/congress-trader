"""Risk scoring engine for congressional trade disclosures."""

import logging

import config
from services import db
from services.market_service import get_price_change_pct

logger = logging.getLogger(__name__)


def score_disclosure(disclosure):
    """Score a disclosure based on risk filters.

    Returns dict with: passed (bool), score (0-100), reasons, fail_reasons
    """
    reasons = []
    fail_reasons = []
    score = 0

    # 1. Reporting delay (max 40 points)
    delay = disclosure.get("reporting_delay_days", 99)
    max_delay = int(db.get_setting("max_reporting_delay_days", config.MAX_REPORTING_DELAY_DAYS))
    if delay <= max_delay:
        if delay <= 1:
            score += 40
            reasons.append(f"Filed within {delay} day — excellent")
        elif delay <= 2:
            score += 30
            reasons.append(f"Filed within {delay} days — good")
        else:
            score += 20
            reasons.append(f"Filed within {delay} days — acceptable")
    else:
        fail_reasons.append(f"Reporting delay {delay} days exceeds max {max_delay}")

    # 2. Buy trades only (pass/fail)
    tx_type = disclosure.get("tx_type", "")
    if tx_type == "purchase":
        score += 10
        reasons.append("Purchase transaction")
    else:
        fail_reasons.append(f"Transaction type '{tx_type}' — only purchases allowed")

    # 3. Trade size (max 20 points)
    amount_min = disclosure.get("amount_min", 0)
    min_amount = int(db.get_setting("min_trade_amount", config.MIN_TRADE_AMOUNT))
    if amount_min >= min_amount:
        if amount_min >= 100000:
            score += 20
            reasons.append(f"Large trade (${amount_min:,.0f}+)")
        elif amount_min >= 50000:
            score += 15
            reasons.append(f"Significant trade (${amount_min:,.0f}+)")
        else:
            score += 10
            reasons.append(f"Trade meets minimum (${amount_min:,.0f}+)")
    else:
        fail_reasons.append(f"Trade amount ${amount_min:,.0f} below minimum ${min_amount:,.0f}")

    # 4. Price change since trade (max 15 points)
    ticker = disclosure.get("ticker", "")
    trade_date = disclosure.get("trade_date", "")
    max_change = float(db.get_setting("max_price_change_pct", config.MAX_PRICE_CHANGE_PCT))

    price_change = 0
    price_at_trade = 0
    price_now = 0
    if ticker and trade_date:
        price_change, price_at_trade, price_now = get_price_change_pct(ticker, trade_date)

    if abs(price_change) <= max_change:
        score += 15
        reasons.append(f"Price change {price_change:+.1f}% since trade — opportunity still open")
    else:
        fail_reasons.append(f"Price already moved {price_change:+.1f}% — opportunity may have passed")

    # 5. Politician track record (max 15 points)
    politician_name = disclosure.get("politician_name", "")
    min_win_rate = float(db.get_setting("min_politician_win_rate", config.MIN_POLITICIAN_WIN_RATE))

    politician = None
    if politician_name:
        conn = db.get_db()
        row = conn.execute("SELECT * FROM politicians WHERE name=?", (politician_name,)).fetchone()
        conn.close()
        if row:
            politician = dict(row)

    if politician and politician.get("total_trades", 0) >= 5:
        win_rate = 0
        if politician["total_trades"] > 0:
            win_rate = (politician.get("winning_trades", 0) / politician["total_trades"]) * 100
        if win_rate >= min_win_rate:
            score += 15
            reasons.append(f"{politician_name} win rate {win_rate:.0f}%")
        else:
            fail_reasons.append(f"{politician_name} win rate {win_rate:.0f}% below {min_win_rate:.0f}%")
    else:
        # New politician or not enough history — neutral, don't fail
        score += 5
        reasons.append(f"{politician_name} — insufficient history, neutral score")

    # Determine pass/fail
    passed = len(fail_reasons) == 0

    return {
        "passed": passed,
        "score": min(score, 100),
        "reasons": reasons,
        "fail_reasons": fail_reasons,
        "price_at_trade": price_at_trade,
        "price_now": price_now,
        "price_change_pct": price_change,
    }


def score_unprocessed():
    """Score all unprocessed disclosures. Returns list of passing disclosures."""
    conn = db.get_db()
    rows = conn.execute("SELECT * FROM disclosures WHERE processed=0").fetchall()
    conn.close()

    passing = []
    for row in rows:
        d = dict(row)
        result = score_disclosure(d)
        db.update_disclosure_score(
            d["id"],
            result["score"],
            price_at_trade=result["price_at_trade"],
            price_at_filing=result["price_now"],
            price_change_pct=result["price_change_pct"],
        )
        if result["passed"]:
            passing.append({**d, **result})
            logger.info("PASS: %s %s %s (score=%d)", d["politician_name"], d["tx_type"], d["ticker"], result["score"])
        else:
            logger.debug("FAIL: %s %s %s — %s", d["politician_name"], d["tx_type"], d["ticker"], result["fail_reasons"])

    return passing
