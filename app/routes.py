from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template

from app.models import Signal
from app.services.scanner import DEFAULT_SYMBOL_NAMES


bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    configured_symbols = current_app.config["DERIV_SYMBOLS"]
    symbols = [
        {
            "symbol": symbol,
            "display_name": DEFAULT_SYMBOL_NAMES.get(symbol, symbol),
        }
        for symbol in configured_symbols
    ]
    recent_signals = (
        Signal.query.order_by(Signal.created_at.desc())
        .limit(25)
        .all()
    )

    return render_template(
        "index.html",
        symbols=symbols,
        recent_signals=[signal.to_dict() for signal in recent_signals],
    )


@bp.get("/api/signals")
def signals():
    recent_signals = (
        Signal.query.order_by(Signal.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify([signal.to_dict() for signal in recent_signals])


@bp.get("/api/status")
def status():
    return jsonify(current_app.scanner.status())
