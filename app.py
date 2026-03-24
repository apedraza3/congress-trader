"""Congress Trader — automated stock trading based on congressional disclosures."""

import functools
import logging
import os

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import config
from services.db import get_db, init_db
from services import db
from services import disclosure_service
from services import scoring_service
from services import market_service
from services import alpaca_service
from services import fred_service
from services import backtest_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


# ── Auth ──────────────────────────────────────────────────────────────

def login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == config.AUTH_PASSWORD:
            session["authenticated"] = True
            session.permanent = True
            return redirect(request.args.get("next", "/"))
        error = "Invalid password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── Pages ─────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("feed.html")


@app.route("/trades")
@login_required
def trades_page():
    return render_template("trades.html")


@app.route("/politicians")
@login_required
def politicians_page():
    return render_template("politicians.html")


@app.route("/analytics")
@login_required
def analytics_page():
    return render_template("analytics.html")


@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html")


@app.route("/backtest")
@login_required
def backtest_page():
    return render_template("backtest.html")


# ── API: Disclosures ─────────────────────────────────────────────────

@app.route("/api/disclosures")
@login_required
def api_disclosures():
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    tx_type = request.args.get("tx_type")
    min_score = request.args.get("min_score", 0, type=int)
    rows = db.get_disclosures(limit=limit, offset=offset, tx_type=tx_type, min_score=min_score)
    return jsonify(rows)


@app.route("/api/disclosures/<int:disclosure_id>")
@login_required
def api_disclosure_detail(disclosure_id):
    d = db.get_disclosure(disclosure_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    return jsonify(d)


@app.route("/api/disclosures/refresh", methods=["POST"])
@login_required
def api_refresh_disclosures():
    """Manually trigger disclosure fetch + scoring."""
    new_count = disclosure_service.ingest_new_disclosures()
    passing = scoring_service.score_unprocessed()
    return jsonify({
        "new_disclosures": new_count,
        "passing_trades": len(passing),
        "passing": [{"ticker": p["ticker"], "politician": p["politician_name"], "score": p["score"]} for p in passing],
    })


@app.route("/api/disclosures/<int:disclosure_id>/score", methods=["POST"])
@login_required
def api_rescore(disclosure_id):
    d = db.get_disclosure(disclosure_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    result = scoring_service.score_disclosure(d)
    db.update_disclosure_score(
        disclosure_id, result["score"],
        price_at_trade=result["price_at_trade"],
        price_at_filing=result["price_now"],
        price_change_pct=result["price_change_pct"],
    )
    return jsonify(result)


# ── API: Trades ───────────────────────────────────────────────────────

@app.route("/api/trades")
@login_required
def api_trades():
    status = request.args.get("status")
    limit = request.args.get("limit", 50, type=int)
    rows = db.get_trades(status=status, limit=limit)
    return jsonify(rows)


@app.route("/api/trades/execute", methods=["POST"])
@login_required
def api_execute_trade():
    """Manually execute a trade for a specific disclosure."""
    data = request.get_json()
    disclosure_id = data.get("disclosure_id")
    if not disclosure_id:
        return jsonify({"error": "disclosure_id required"}), 400

    d = db.get_disclosure(disclosure_id)
    if not d:
        return jsonify({"error": "Disclosure not found"}), 404

    trade_id = alpaca_service.submit_buy_order(
        d["ticker"], disclosure_id, d["politician_name"]
    )
    if trade_id:
        return jsonify({"trade_id": trade_id, "status": "submitted"})
    return jsonify({"error": "Order submission failed"}), 500


@app.route("/api/trades/<int:trade_id>/close", methods=["POST"])
@login_required
def api_close_trade(trade_id):
    """Manually close a trade."""
    trades = db.get_trades()
    trade = next((t for t in trades if t["id"] == trade_id), None)
    if not trade:
        return jsonify({"error": "Trade not found"}), 404

    current_price = market_service.get_current_price(trade["ticker"])
    if not current_price:
        return jsonify({"error": "Cannot get current price"}), 500

    from datetime import datetime
    entry = trade["entry_price"]
    pnl_dollars = round((current_price - entry) * trade["quantity"], 2)
    pnl_pct = round(((current_price - entry) / entry) * 100, 2) if entry else 0

    db.update_trade(trade_id,
                    exit_price=current_price,
                    exit_date=datetime.utcnow().strftime("%Y-%m-%d"),
                    status="closed",
                    pnl_dollars=pnl_dollars,
                    pnl_pct=pnl_pct)

    if trade["politician_name"]:
        db.update_politician_stats(trade["politician_name"])

    return jsonify({"status": "closed", "pnl_dollars": pnl_dollars, "pnl_pct": pnl_pct})


# ── API: Politicians ──────────────────────────────────────────────────

@app.route("/api/politicians")
@login_required
def api_politicians():
    limit = request.args.get("limit", 50, type=int)
    rows = db.get_politicians(limit=limit)
    return jsonify(rows)


# ── API: Portfolio / Analytics ────────────────────────────────────────

@app.route("/api/portfolio")
@login_required
def api_portfolio():
    account = alpaca_service.get_account_info()
    open_trades = db.get_trades(status="open")
    closed_trades = db.get_trades(status="closed")
    stopped_trades = db.get_trades(status="stopped")

    total_pnl = sum(t.get("pnl_dollars", 0) for t in closed_trades + stopped_trades)
    win_count = sum(1 for t in closed_trades if t.get("pnl_pct", 0) > 0)
    total_closed = len(closed_trades) + len(stopped_trades)
    win_rate = round((win_count / total_closed) * 100, 1) if total_closed else 0

    return jsonify({
        "account": account,
        "open_positions": len(open_trades),
        "total_closed": total_closed,
        "total_pnl": round(total_pnl, 2),
        "win_rate": win_rate,
        "open_trades": open_trades,
    })


@app.route("/api/analytics")
@login_required
def api_analytics():
    snapshots = db.get_snapshots(days=90)
    closed = db.get_trades(status="closed")
    stopped = db.get_trades(status="stopped")
    all_done = closed + stopped

    # Top performers
    by_ticker = {}
    for t in all_done:
        tk = t["ticker"]
        if tk not in by_ticker:
            by_ticker[tk] = {"ticker": tk, "total_pnl": 0, "trades": 0}
        by_ticker[tk]["total_pnl"] += t.get("pnl_dollars", 0)
        by_ticker[tk]["trades"] += 1

    top = sorted(by_ticker.values(), key=lambda x: x["total_pnl"], reverse=True)[:10]
    bottom = sorted(by_ticker.values(), key=lambda x: x["total_pnl"])[:10]

    return jsonify({
        "snapshots": snapshots,
        "top_performers": top,
        "worst_performers": bottom,
        "total_trades": len(all_done),
        "avg_pnl_pct": round(sum(t.get("pnl_pct", 0) for t in all_done) / len(all_done), 2) if all_done else 0,
    })


# ── API: Recession ────────────────────────────────────────────────────

@app.route("/api/recession-status")
@login_required
def api_recession_status():
    try:
        blocked, reason, details = fred_service.is_recession_active()
        return jsonify({
            "blocked": blocked,
            "reason": reason,
            "sahm": details.get("sahm", {}),
            "unemployment": details.get("unemployment", {}),
            "enabled": db.get_setting("enable_recession_guard", "true") == "true",
        })
    except Exception as e:
        return jsonify({"blocked": False, "reason": str(e), "error": True})


# ── API: Backtest ────────────────────────────────────────────────────

@app.route("/api/backtest", methods=["POST"])
@login_required
def api_run_backtest():
    data = request.get_json() or {}
    result = backtest_service.run_backtest(
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
        min_score=data.get("min_score", 70),
        initial_capital=data.get("initial_capital", 100000),
    )
    return jsonify(result)


# ── API: Settings ─────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    return jsonify({
        "max_reporting_delay_days": int(db.get_setting("max_reporting_delay_days", config.MAX_REPORTING_DELAY_DAYS)),
        "min_trade_amount": int(db.get_setting("min_trade_amount", config.MIN_TRADE_AMOUNT)),
        "max_price_change_pct": float(db.get_setting("max_price_change_pct", config.MAX_PRICE_CHANGE_PCT)),
        "min_politician_win_rate": float(db.get_setting("min_politician_win_rate", config.MIN_POLITICIAN_WIN_RATE)),
        "max_position_pct": float(db.get_setting("max_position_pct", config.MAX_POSITION_PCT)),
        "stop_loss_pct": float(db.get_setting("stop_loss_pct", config.STOP_LOSS_PCT)),
        "hold_days": int(db.get_setting("hold_days", config.HOLD_DAYS)),
        "max_open_positions": int(db.get_setting("max_open_positions", config.MAX_OPEN_POSITIONS)),
        "auto_trade": db.get_setting("auto_trade", "false") == "true",
        "require_technical_confirmation": db.get_setting("require_technical_confirmation", str(config.REQUIRE_TECHNICAL_CONFIRMATION).lower()) == "true",
        "enable_recession_guard": db.get_setting("enable_recession_guard", str(config.ENABLE_RECESSION_GUARD).lower()) == "true",
        "alpaca_connected": alpaca_service.is_connected(),
    })


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings_save():
    data = request.get_json()
    allowed = [
        "max_reporting_delay_days", "min_trade_amount", "max_price_change_pct",
        "min_politician_win_rate", "max_position_pct", "stop_loss_pct",
        "hold_days", "max_open_positions", "auto_trade",
        "require_technical_confirmation", "enable_recession_guard",
    ]
    bool_keys = {"auto_trade", "require_technical_confirmation", "enable_recession_guard"}
    for key in allowed:
        if key in data:
            val = str(data[key]).lower() if key in bool_keys else str(data[key])
            db.set_setting(key, val)
    return jsonify({"status": "saved"})


# ── API: Status ───────────────────────────────────────────────────────

@app.route("/api/status")
@login_required
def api_status():
    conn = get_db()
    disc_count = conn.execute("SELECT COUNT(*) as c FROM disclosures").fetchone()["c"]
    trade_count = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()["c"]
    conn.close()
    recession_blocked = False
    try:
        blocked, _, _ = fred_service.is_recession_active()
        recession_blocked = blocked
    except Exception:
        pass

    return jsonify({
        "disclosures": disc_count,
        "trades": trade_count,
        "alpaca_connected": alpaca_service.is_connected(),
        "finnhub_configured": bool(config.FINNHUB_API_KEY),
        "recession_blocked": recession_blocked,
    })


# ── Init ──────────────────────────────────────────────────────────────

init_db()
os.makedirs("data", exist_ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=False)
