"""Risk scoring engine for congressional trade disclosures."""

import logging

import config
from services import db
from services.market_service import get_price_change_pct, get_technical_signals

logger = logging.getLogger(__name__)


def score_disclosure(disclosure):
    """Score a disclosure based on risk filters.

    7-factor scoring system (0-100):
      1. Reporting delay        (max 30 pts)
      2. Transaction type        (10 pts, pass/fail)
      3. Trade size              (max 15 pts)
      4. Price change            (max 10 pts)
      5. Politician track record (max 10 pts)
      6. Technical alignment     (max 10 pts)
      7. AI news sentiment       (max 15 pts)

    Returns dict with: passed (bool), score (0-100), reasons, fail_reasons
    """
    reasons = []
    fail_reasons = []
    score = 0

    # ── Pre-check: Recession Guard ──
    enable_recession = db.get_setting("enable_recession_guard", "true") == "true"
    if enable_recession:
        try:
            from services.fred_service import is_recession_active
            blocked, reason, recession_details = is_recession_active()
            if blocked:
                return {
                    "passed": False,
                    "score": 0,
                    "reasons": [],
                    "fail_reasons": [f"RECESSION GUARD: {reason}"],
                    "price_at_trade": 0,
                    "price_now": 0,
                    "price_change_pct": 0,
                    "technical_signals": {},
                    "ai_sentiment": {},
                    "recession_blocked": True,
                    "recession_details": recession_details,
                }
        except Exception as e:
            logger.warning("Recession guard check failed: %s", e)

    # 1. Reporting delay (max 30 points)
    delay = disclosure.get("reporting_delay_days", 99)
    max_delay = int(db.get_setting("max_reporting_delay_days", config.MAX_REPORTING_DELAY_DAYS))
    if delay <= max_delay:
        if delay <= 1:
            score += 30
            reasons.append(f"Filed within {delay} day — excellent")
        elif delay <= 2:
            score += 22
            reasons.append(f"Filed within {delay} days — good")
        else:
            score += 14
            reasons.append(f"Filed within {delay} days — acceptable")
    else:
        fail_reasons.append(f"Reporting delay {delay} days exceeds max {max_delay}")

    # 2. Buy trades only (pass/fail, 10 points)
    tx_type = disclosure.get("tx_type", "")
    if tx_type == "purchase":
        score += 10
        reasons.append("Purchase transaction")
    else:
        fail_reasons.append(f"Transaction type '{tx_type}' — only purchases allowed")

    # 3. Trade size (max 15 points)
    amount_min = disclosure.get("amount_min", 0)
    min_amount = int(db.get_setting("min_trade_amount", config.MIN_TRADE_AMOUNT))
    if amount_min >= min_amount:
        if amount_min >= 100000:
            score += 15
            reasons.append(f"Large trade (${amount_min:,.0f}+)")
        elif amount_min >= 50000:
            score += 10
            reasons.append(f"Significant trade (${amount_min:,.0f}+)")
        else:
            score += 7
            reasons.append(f"Trade meets minimum (${amount_min:,.0f}+)")
    else:
        fail_reasons.append(f"Trade amount ${amount_min:,.0f} below minimum ${min_amount:,.0f}")

    # 4. Price change since trade (max 10 points)
    ticker = disclosure.get("ticker", "")
    trade_date = disclosure.get("trade_date", "")
    max_change = float(db.get_setting("max_price_change_pct", config.MAX_PRICE_CHANGE_PCT))

    price_change = 0
    price_at_trade = 0
    price_now = 0
    if ticker and trade_date:
        price_change, price_at_trade, price_now = get_price_change_pct(ticker, trade_date)

    if abs(price_change) <= max_change:
        score += 10
        reasons.append(f"Price change {price_change:+.1f}% since trade — opportunity still open")
    else:
        fail_reasons.append(f"Price already moved {price_change:+.1f}% — opportunity may have passed")

    # 5. Politician track record (max 10 points)
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
            score += 10
            reasons.append(f"{politician_name} win rate {win_rate:.0f}%")
        else:
            fail_reasons.append(f"{politician_name} win rate {win_rate:.0f}% below {min_win_rate:.0f}%")
    else:
        score += 4
        reasons.append(f"{politician_name} — insufficient history, neutral score")

    # 6. Technical alignment — MACD + 200 EMA (max 10 points)
    require_tech = db.get_setting("require_technical_confirmation",
                                  str(config.REQUIRE_TECHNICAL_CONFIRMATION).lower()) == "true"
    tech = {}
    if ticker:
        tech = get_technical_signals(ticker)

    if tech.get("error"):
        if require_tech:
            fail_reasons.append(f"Technical data unavailable: {tech['error']}")
        else:
            score += 3
            reasons.append("Technical data unavailable — neutral")
    elif tech.get("macd_bullish") and tech.get("above_200ema"):
        score += 10
        reasons.append(f"MACD bullish + above 200 EMA (${tech.get('price', 0):.0f} > ${tech.get('ema_200', 0):.0f})")
    elif tech.get("macd_bullish") or tech.get("above_200ema"):
        score += 5
        which = "MACD bullish" if tech.get("macd_bullish") else "Above 200 EMA"
        reasons.append(f"Partial technical alignment ({which})")
    else:
        if require_tech:
            fail_reasons.append("No technical confirmation — MACD bearish and below 200 EMA")
        else:
            reasons.append("Technical indicators bearish — 0 points")

    # 7. AI news sentiment (max 15 points)
    ai_sentiment = {}
    enable_ai = db.get_setting("enable_ai_sentiment",
                               str(config.ENABLE_AI_SENTIMENT).lower()) == "true"
    if enable_ai and ticker:
        try:
            from services.ai_service import analyze_sentiment
            ai_sentiment = analyze_sentiment(
                ticker,
                politician_name=politician_name,
                trade_context=f"Trade size: ${amount_min:,.0f}+, filed {delay} days after trade.",
            )

            if ai_sentiment.get("error"):
                # AI unavailable — neutral, don't penalize
                score += 5
                reasons.append(f"AI sentiment unavailable — neutral ({ai_sentiment['error']})")
            else:
                sent_score = ai_sentiment.get("score", 0)  # -100 to +100
                sentiment = ai_sentiment.get("sentiment", "neutral")
                summary = ai_sentiment.get("summary", "")

                if sent_score >= 40:
                    score += 15
                    reasons.append(f"AI sentiment: {sentiment} ({sent_score:+d}) — {summary}")
                elif sent_score >= 10:
                    score += 10
                    reasons.append(f"AI sentiment: {sentiment} ({sent_score:+d}) — {summary}")
                elif sent_score >= -20:
                    score += 5
                    reasons.append(f"AI sentiment: {sentiment} ({sent_score:+d}) — {summary}")
                elif sent_score >= -50:
                    # Bearish — no points but don't fail
                    reasons.append(f"AI sentiment: {sentiment} ({sent_score:+d}) — {summary}")
                else:
                    # Very bearish — fail the trade
                    fail_reasons.append(f"AI BEARISH ({sent_score:+d}): {summary}")

        except Exception as e:
            logger.warning("AI sentiment scoring failed: %s", e)
            score += 5
            reasons.append("AI sentiment analysis error — neutral")
    elif not enable_ai:
        score += 5
        reasons.append("AI sentiment disabled — neutral")

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
        "technical_signals": tech,
        "ai_sentiment": ai_sentiment,
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
