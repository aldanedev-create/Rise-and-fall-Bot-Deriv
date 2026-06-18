from __future__ import annotations

from flask import current_app, request
from flask_socketio import emit

from app.models import Signal


def register_socket_events(socketio):
    @socketio.on("connect")
    def handle_connect():
        emit("scanner_status", current_app.scanner.status())
        emit("connection_ack", {"sid": request.sid})

        recent_signals = (
            Signal.query.order_by(Signal.created_at.desc())
            .limit(25)
            .all()
        )
        emit("signal_history", [signal.to_dict() for signal in recent_signals])

    @socketio.on("start_scan")
    def handle_start_scan(payload=None):
        payload = payload or {}
        symbols = payload.get("symbols") or current_app.config["DERIV_SYMBOLS"]
        status = current_app.scanner.start(symbols)
        emit("scanner_status", status, broadcast=True)

    @socketio.on("stop_scan")
    def handle_stop_scan():
        status = current_app.scanner.stop()
        emit("scanner_status", status, broadcast=True)
