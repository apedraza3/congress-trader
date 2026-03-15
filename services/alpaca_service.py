"""Alpaca trading integration for paper and live trading."""

import logging
from datetime import datetime, timedelta

import config
from services import db

logger = logging.getLogger(__name__)

_api = None


def get_api():
    """Get or create Alpaca API client."""
    global _api
    if _api:
        return _api
    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        logger.warning("Alpaca API keys not configured")
        return None
    try:
        from alpaca_trade_api import REST
        _api = REST(
            config.ALPACA_API_KEY,
            config.ALPACA_SECRET_KEY,
            config.ALPACA_BASE_URL,
        )
        return _api
    except ImportError:
        logger.error("alpaca-trade-api not installed")
        return None
    except Exception as e:
        logger.error("Alpaca connection failed: %s", e)
        return None


def is_connected():
    """Check if Alpaca API is reachable."""
    api = get_api()
    if not api:
        return False
    try:
        api.get_account()
        return True
    except Exception:
        return False


def get_account_info():
    """Get account balance and status."""
    api = get_api()
    if not api:
        return None
    try:
        acct = api.get_account()
        return {
            "cash": float(acct.cash),
            "portfolio_value": float(acct.portfolio_value),
            "buying_power": float(acct.buying_power),
            "equity": float(acct.equity),
            "status": acct.status,
            "trading_blocked": acct.trading_blocked,
            "paper": "paper" in config.ALPACA_BASE_URL,
        }
    except Exception as e:
        logger.error("Alpaca account fetch failed: %s", e)
        return None


def submit_buy_order(ticker, disclosure_id, politician_name=""):
    """Submit a market buy order with position sizing and stop-loss."""
    api = get_api()
    if not api:
        return None

    try:
        # Get account for position sizing
        acct = api.get_account()
        portfolio_value = float(acct.portfolio_value)
        max_pct = float(db.get_setting("max_position_pct", config.MAX_POSITION_PCT))
        position_value = portfolio_value * (max_pct / 100)

        # Check max open positions
        max_positions = int(db.get_setting("max_open_positions", config.MAX_OPEN_POSITIONS))
        open_trades = db.get_trades(status="open")
        if len(open_trades) >= max_positions:
            logger.warning("Max open positions (%d) reached, skipping %s", max_positions, ticker)
            return None

        # Get current price to calculate quantity
        from services.market_service import get_current_price
        price = get_current_price(ticker)
        if not price:
            logger.error("Cannot get price for %s, skipping order", ticker)
            return None

        quantity = int(position_value / price)
        if quantity < 1:
            logger.warning("Position too small for %s (price=$%.2f, budget=$%.2f)", ticker, price, position_value)
            return None

        # Calculate stop-loss price
        stop_pct = float(db.get_setting("stop_loss_pct", config.STOP_LOSS_PCT))
        stop_price = round(price * (1 - stop_pct / 100), 2)

        # Calculate target exit date
        hold_days = int(db.get_setting("hold_days", config.HOLD_DAYS))
        target_exit = (datetime.utcnow() + timedelta(days=hold_days)).strftime("%Y-%m-%d")

        # Submit market order
        order = api.submit_order(
            symbol=ticker,
            qty=quantity,
            side="buy",
            type="market",
            time_in_force="day",
        )

        # Record in our DB
        trade_id = db.insert_trade({
            "disclosure_id": disclosure_id,
            "ticker": ticker,
            "politician_name": politician_name,
            "entry_price": price,
            "entry_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "quantity": quantity,
            "cost_basis": round(price * quantity, 2),
            "stop_loss_price": stop_price,
            "target_exit_date": target_exit,
            "status": "open",
            "paper_or_live": "paper" if "paper" in config.ALPACA_BASE_URL else "live",
            "alpaca_order_id": order.id,
        })

        logger.info("Order submitted: BUY %d %s @ $%.2f (stop=$%.2f, trade_id=%d)",
                     quantity, ticker, price, stop_price, trade_id)
        return trade_id

    except Exception as e:
        logger.error("Order submission failed for %s: %s", ticker, e)
        return None


def check_positions():
    """Check open positions for stop-losses and hold period exits."""
    api = get_api()
    if not api:
        return

    open_trades = db.get_trades(status="open")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    for trade in open_trades:
        ticker = trade["ticker"]
        try:
            from services.market_service import get_current_price
            current_price = get_current_price(ticker)
            if not current_price:
                continue

            should_sell = False
            reason = ""

            # Check stop-loss
            if trade["stop_loss_price"] and current_price <= trade["stop_loss_price"]:
                should_sell = True
                reason = "stop_loss"

            # Check hold period expiry
            if trade["target_exit_date"] and today >= trade["target_exit_date"]:
                should_sell = True
                reason = "hold_expired"

            if should_sell:
                # Submit sell order
                try:
                    api.submit_order(
                        symbol=ticker,
                        qty=int(trade["quantity"]),
                        side="sell",
                        type="market",
                        time_in_force="day",
                    )
                except Exception as e:
                    logger.error("Sell order failed for %s: %s", ticker, e)
                    continue

                # Calculate P&L
                entry = trade["entry_price"]
                pnl_dollars = round((current_price - entry) * trade["quantity"], 2)
                pnl_pct = round(((current_price - entry) / entry) * 100, 2) if entry else 0

                status = "stopped" if reason == "stop_loss" else "closed"
                db.update_trade(trade["id"],
                                exit_price=current_price,
                                exit_date=today,
                                status=status,
                                pnl_dollars=pnl_dollars,
                                pnl_pct=pnl_pct)

                # Update politician stats
                if trade["politician_name"]:
                    db.update_politician_stats(trade["politician_name"])

                logger.info("SOLD %s: %s (P&L: $%.2f / %.1f%%)",
                            ticker, reason, pnl_dollars, pnl_pct)

        except Exception as e:
            logger.error("Position check failed for %s: %s", ticker, e)
