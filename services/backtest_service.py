"""Backtesting engine — replay historical disclosures with the scoring engine."""

import logging
import math
from datetime import datetime, timedelta

import config
from services import db

logger = logging.getLogger(__name__)


def _batch_download(tickers, start_date, end_date):
    """Download historical prices for multiple tickers at once. Returns {ticker: DataFrame}."""
    import yfinance as yf

    cache = {}
    for ticker in tickers:
        try:
            data = yf.download(ticker, start=start_date, end=end_date, progress=False)
            if not data.empty:
                cache[ticker] = data
        except Exception as e:
            logger.warning("Failed to download %s: %s", ticker, e)
    return cache


def _get_price_from_cache(cache, ticker, date_str):
    """Look up closing price from cached DataFrame."""
    if ticker not in cache:
        return 0
    df = cache[ticker]
    target = datetime.strptime(date_str, "%Y-%m-%d")
    # Find closest date on or before target
    before = df[df.index <= target]
    if before.empty:
        return 0
    close = before.iloc[-1]["Close"]
    return float(close.iloc[0]) if hasattr(close, "iloc") else float(close)


def _check_stop_loss_hit(cache, ticker, entry_date_str, exit_date_str, stop_price):
    """Check if price dropped below stop-loss during the hold period."""
    if ticker not in cache:
        return False, None
    df = cache[ticker]
    start = datetime.strptime(entry_date_str, "%Y-%m-%d")
    end = datetime.strptime(exit_date_str, "%Y-%m-%d")
    window = df[(df.index >= start) & (df.index <= end)]
    if window.empty:
        return False, None
    low_col = window["Low"]
    if hasattr(low_col, "columns"):
        low_col = low_col.squeeze()
    for idx, low_val in low_col.items():
        val = float(low_val.iloc[0]) if hasattr(low_val, "iloc") else float(low_val)
        if val <= stop_price:
            return True, idx.strftime("%Y-%m-%d")
    return False, None


def run_backtest(start_date=None, end_date=None, min_score=70, initial_capital=100000):
    """Replay historical disclosures and simulate trades.

    Args:
        start_date: YYYY-MM-DD start (default: earliest disclosure)
        end_date: YYYY-MM-DD end (default: today)
        min_score: Minimum risk score to enter a trade
        initial_capital: Starting portfolio value

    Returns dict with trades, metrics, and equity_curve.
    """
    conn = db.get_db()

    query = "SELECT * FROM disclosures WHERE processed=1 AND tx_type='purchase'"
    params = []
    if start_date:
        query += " AND trade_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND trade_date <= ?"
        params.append(end_date)
    query += " ORDER BY trade_date ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    disclosures = [dict(r) for r in rows]

    if not disclosures:
        return {"trades": [], "metrics": _empty_metrics(initial_capital), "equity_curve": []}

    # Filter by minimum score
    disclosures = [d for d in disclosures if d.get("risk_score", 0) >= min_score]
    if not disclosures:
        return {"trades": [], "metrics": _empty_metrics(initial_capital), "equity_curve": []}

    # Load settings
    hold_days = int(db.get_setting("hold_days", config.HOLD_DAYS))
    stop_loss_pct = float(db.get_setting("stop_loss_pct", config.STOP_LOSS_PCT))
    max_position_pct = float(db.get_setting("max_position_pct", config.MAX_POSITION_PCT))
    max_positions = int(db.get_setting("max_open_positions", config.MAX_OPEN_POSITIONS))

    # Determine date range for price downloads
    earliest = disclosures[0]["trade_date"]
    latest = disclosures[-1]["trade_date"]
    dl_start = (datetime.strptime(earliest, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    dl_end = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=hold_days + 10)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    if dl_end > today:
        dl_end = today

    # Batch download all tickers + SPY
    unique_tickers = list(set(d["ticker"] for d in disclosures))
    logger.info("Backtesting %d disclosures across %d tickers", len(disclosures), len(unique_tickers))
    price_cache = _batch_download(unique_tickers + ["SPY"], dl_start, dl_end)

    # Simulate trades
    cash = initial_capital
    open_positions = []
    closed_trades = []
    equity_points = []

    # Build a day-by-day timeline
    all_dates = set()
    for d in disclosures:
        dt = datetime.strptime(d["trade_date"], "%Y-%m-%d")
        for i in range(hold_days + 1):
            all_dates.add((dt + timedelta(days=i)).strftime("%Y-%m-%d"))
    all_dates = sorted(all_dates)

    # SPY baseline for comparison
    spy_start_price = _get_price_from_cache(price_cache, "SPY", dl_start)

    for date_str in all_dates:
        # Check open positions for exit
        still_open = []
        for pos in open_positions:
            exit_date = pos["target_exit_date"]
            stop_price = pos["stop_loss_price"]

            # Check stop-loss
            stopped, stop_date = _check_stop_loss_hit(
                price_cache, pos["ticker"], pos["entry_date"], date_str, stop_price
            )
            if stopped and stop_date and stop_date <= date_str:
                exit_price = stop_price
                pnl_dollars = (exit_price - pos["entry_price"]) * pos["quantity"]
                pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
                cash += exit_price * pos["quantity"]
                closed_trades.append({
                    **pos,
                    "exit_price": round(exit_price, 2),
                    "exit_date": stop_date,
                    "pnl_dollars": round(pnl_dollars, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": "stop_loss",
                })
                continue

            # Check hold period expiry
            if date_str >= exit_date:
                exit_price = _get_price_from_cache(price_cache, pos["ticker"], exit_date)
                if not exit_price:
                    exit_price = _get_price_from_cache(price_cache, pos["ticker"], date_str)
                if exit_price:
                    pnl_dollars = (exit_price - pos["entry_price"]) * pos["quantity"]
                    pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
                    cash += exit_price * pos["quantity"]
                    closed_trades.append({
                        **pos,
                        "exit_price": round(exit_price, 2),
                        "exit_date": exit_date,
                        "pnl_dollars": round(pnl_dollars, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "exit_reason": "hold_expired",
                    })
                    continue
            still_open.append(pos)
        open_positions = still_open

        # Enter new positions from disclosures on this date
        for d in disclosures:
            if d["trade_date"] != date_str:
                continue
            if len(open_positions) >= max_positions:
                continue

            entry_price = d.get("price_at_trade", 0)
            if not entry_price:
                entry_price = _get_price_from_cache(price_cache, d["ticker"], date_str)
            if not entry_price or entry_price <= 0:
                continue

            # Position sizing
            portfolio_value = cash + sum(
                _get_price_from_cache(price_cache, p["ticker"], date_str) * p["quantity"]
                for p in open_positions
            )
            position_value = portfolio_value * (max_position_pct / 100)
            quantity = int(position_value / entry_price)
            if quantity <= 0:
                continue
            cost = entry_price * quantity
            if cost > cash:
                continue

            cash -= cost
            target_exit = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=hold_days)).strftime("%Y-%m-%d")
            stop_loss_price = entry_price * (1 - stop_loss_pct / 100)

            open_positions.append({
                "ticker": d["ticker"],
                "politician_name": d.get("politician_name", ""),
                "entry_price": round(entry_price, 2),
                "entry_date": date_str,
                "quantity": quantity,
                "cost_basis": round(cost, 2),
                "stop_loss_price": round(stop_loss_price, 2),
                "target_exit_date": target_exit,
                "risk_score": d.get("risk_score", 0),
            })

        # Record equity point (weekly to keep data manageable)
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.weekday() == 4 or date_str == all_dates[-1]:  # Fridays + last date
            invested = sum(
                _get_price_from_cache(price_cache, p["ticker"], date_str) * p["quantity"]
                for p in open_positions
            )
            portfolio_value = cash + invested
            spy_price = _get_price_from_cache(price_cache, "SPY", date_str)
            spy_normalized = (spy_price / spy_start_price * initial_capital) if spy_start_price else initial_capital
            equity_points.append({
                "date": date_str,
                "portfolio": round(portfolio_value, 2),
                "sp500": round(spy_normalized, 2),
            })

    # Force-close any still-open positions at latest available price
    for pos in open_positions:
        exit_price = _get_price_from_cache(price_cache, pos["ticker"], all_dates[-1] if all_dates else today)
        if exit_price:
            pnl_dollars = (exit_price - pos["entry_price"]) * pos["quantity"]
            pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
            closed_trades.append({
                **pos,
                "exit_price": round(exit_price, 2),
                "exit_date": all_dates[-1] if all_dates else today,
                "pnl_dollars": round(pnl_dollars, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": "backtest_end",
            })

    # Calculate metrics
    metrics = _calculate_metrics(closed_trades, equity_points, initial_capital, price_cache, dl_start, dl_end)

    return {
        "trades": closed_trades,
        "metrics": metrics,
        "equity_curve": equity_points,
    }


def _calculate_metrics(trades, equity_curve, initial_capital, price_cache, start_date, end_date):
    """Calculate performance metrics from backtest results."""
    if not trades:
        return _empty_metrics(initial_capital)

    total_trades = len(trades)
    winners = [t for t in trades if t["pnl_pct"] > 0]
    win_rate = (len(winners) / total_trades) * 100 if total_trades else 0

    final_value = equity_curve[-1]["portfolio"] if equity_curve else initial_capital
    total_return_pct = ((final_value - initial_capital) / initial_capital) * 100

    # Max drawdown from equity curve
    max_drawdown = 0
    peak = initial_capital
    for point in equity_curve:
        if point["portfolio"] > peak:
            peak = point["portfolio"]
        drawdown = ((peak - point["portfolio"]) / peak) * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    # Sharpe ratio (annualized from weekly returns)
    sharpe = 0
    if len(equity_curve) >= 2:
        returns = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]["portfolio"]
            curr = equity_curve[i]["portfolio"]
            if prev > 0:
                returns.append((curr - prev) / prev)
        if returns:
            avg_ret = sum(returns) / len(returns)
            std_ret = (sum((r - avg_ret) ** 2 for r in returns) / len(returns)) ** 0.5
            risk_free_weekly = 0.05 / 52
            if std_ret > 0:
                sharpe = ((avg_ret - risk_free_weekly) / std_ret) * math.sqrt(52)

    # S&P 500 return for same period
    sp500_return = 0
    if equity_curve and len(equity_curve) >= 2:
        sp_start = equity_curve[0].get("sp500", initial_capital)
        sp_end = equity_curve[-1].get("sp500", initial_capital)
        if sp_start > 0:
            sp500_return = ((sp_end - sp_start) / sp_start) * 100

    avg_pnl = sum(t["pnl_pct"] for t in trades) / total_trades if total_trades else 0
    avg_winner = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
    losers = [t for t in trades if t["pnl_pct"] <= 0]
    avg_loser = sum(t["pnl_pct"] for t in losers) / len(losers) if losers else 0

    return {
        "total_return_pct": round(total_return_pct, 2),
        "win_rate": round(win_rate, 1),
        "max_drawdown_pct": round(max_drawdown, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sp500_return_pct": round(sp500_return, 2),
        "alpha_pct": round(total_return_pct - sp500_return, 2),
        "total_trades": total_trades,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "avg_return_pct": round(avg_pnl, 2),
        "avg_winner_pct": round(avg_winner, 2),
        "avg_loser_pct": round(avg_loser, 2),
        "initial_capital": initial_capital,
        "final_value": round(equity_curve[-1]["portfolio"], 2) if equity_curve else initial_capital,
    }


def _empty_metrics(initial_capital):
    return {
        "total_return_pct": 0, "win_rate": 0, "max_drawdown_pct": 0,
        "sharpe_ratio": 0, "sp500_return_pct": 0, "alpha_pct": 0,
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "avg_return_pct": 0, "avg_winner_pct": 0, "avg_loser_pct": 0,
        "initial_capital": initial_capital, "final_value": initial_capital,
    }
