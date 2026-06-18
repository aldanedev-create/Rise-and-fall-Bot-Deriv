from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from flask import Flask
from flask_socketio import SocketIO

from app.extensions import db
from app.models import Signal
from app.services.deriv_client import DerivWebSocketClient
from app.services.gmail_alerts import GmailAlertService
from app.services.signal_engine import Candle, SignalDecision, SignalEngine


DEFAULT_SYMBOL_NAMES = {
    "R_10": "Volatility 10 Index",
    "R_25": "Volatility 25 Index",
    "R_50": "Volatility 50 Index",
    "R_75": "Volatility 75 Index",
    "R_100": "Volatility 100 Index",
    "1HZ10V": "Volatility 10 (1s) Index",
    "1HZ25V": "Volatility 25 (1s) Index",
    "1HZ50V": "Volatility 50 (1s) Index",
    "1HZ75V": "Volatility 75 (1s) Index",
    "1HZ100V": "Volatility 100 (1s) Index",
    "JD10": "Jump 10 Index",
    "JD25": "Jump 25 Index",
    "JD50": "Jump 50 Index",
    "JD75": "Jump 75 Index",
    "JD100": "Jump 100 Index",
}

TIMEFRAME_LABELS = {
    SignalEngine.TREND_GRANULARITY: "1H",
    SignalEngine.ENTRY_GRANULARITY: "15M",
}


class DerivSignalScanner:
    def __init__(self, app: Flask, socketio: SocketIO) -> None:
        self.app = app
        self.socketio = socketio
        self.client = DerivWebSocketClient(app.config["DERIV_WS_URL"])
        self.engine = SignalEngine(
            min_confidence=app.config["SIGNAL_MIN_CONFIDENCE"],
            cooldown_seconds=app.config["SIGNAL_COOLDOWN_SECONDS"],
            duration_minutes=app.config["SIGNAL_DURATION_MINUTES"],
            retest_candles=app.config["SIGNAL_RETEST_CANDLES"],
        )
        self.alerts = GmailAlertService(app.config)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._req_id = 1
        self._request_meta: dict[int, tuple[str, int]] = {}
        self._current_open_time: dict[tuple[str, int], int] = {}
        self._candles: dict[str, dict[int, deque[Candle]]] = defaultdict(
            lambda: {
                SignalEngine.TREND_GRANULARITY: deque(maxlen=self.app.config["CANDLE_1H_COUNT"] + 4),
                SignalEngine.ENTRY_GRANULARITY: deque(maxlen=self.app.config["CANDLE_15M_COUNT"] + 4),
            }
        )

        self.symbols = list(app.config["DERIV_SYMBOLS"])
        self.display_names = DEFAULT_SYMBOL_NAMES.copy()
        self.connected = False
        self.running = False
        self.status_text = "idle"
        self.last_error: str | None = None
        self.candles_received = 0
        self.signals_generated = 0
        self.started_at: datetime | None = None
        self.latest_candles: dict[str, dict[str, Any]] = {}

    def start(self, symbols: list[str] | None = None) -> dict[str, Any]:
        cleaned_symbols = self._clean_symbols(symbols) or list(self.app.config["DERIV_SYMBOLS"])
        with self._lock:
            if self.running:
                return self.status()

            self.symbols = cleaned_symbols
            self._stop_event.clear()
            self.running = True
            self.connected = False
            self.status_text = "starting"
            self.last_error = None
            self.candles_received = 0
            self.signals_generated = 0
            self.started_at = datetime.now(timezone.utc)
            self._request_meta.clear()
            self._current_open_time.clear()
            self._candles.clear()
            self.latest_candles.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deriv-signal-scanner",
                daemon=True,
            )
            self._thread.start()

        self._emit_status()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self.running:
                self.status_text = "idle"
                return self.status()
            self.status_text = "stopping"
            self._stop_event.set()

        self._emit_status()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "connected": self.connected,
                "status": self.status_text,
                "symbols": self.symbols,
                "last_error": self.last_error,
                "candles_received": self.candles_received,
                "signals_generated": self.signals_generated,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "latest_candles": self.latest_candles,
                "strategy": "1H trend direction + 15M market-state/BOS/confirmation",
                "duration_minutes": self.app.config["SIGNAL_DURATION_MINUTES"],
                "gmail_alerts_configured": self.alerts.configured(),
            }

    def _run(self) -> None:
        try:
            asyncio.run(self._scan_forever())
        finally:
            with self._lock:
                self.running = False
                self.connected = False
                if self.status_text != "error":
                    self.status_text = "idle"
            self._emit_status()

    async def _scan_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_scan()
            except Exception as exc:
                with self._lock:
                    self.connected = False
                    self.last_error = str(exc)
                    self.status_text = "reconnecting" if not self._stop_event.is_set() else "idle"
                self._emit_status()
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(3)

    async def _connect_and_scan(self) -> None:
        with self._lock:
            self.status_text = "connecting"
            self.last_error = None
        self._emit_status()

        websocket = await self.client.connect()
        async with websocket:
            with self._lock:
                self.connected = True
                self.status_text = "loading symbols"
            self._emit_status()

            active_symbols_payload = {
                "active_symbols": "brief",
                "req_id": self._next_req_id(),
            }
            product_type = self.app.config["DERIV_PRODUCT_TYPE"]
            if product_type:
                active_symbols_payload["product_type"] = product_type
            await self._send(websocket, active_symbols_payload)

            for symbol in self.symbols:
                await self._subscribe_candles(websocket, symbol, SignalEngine.TREND_GRANULARITY)
                await self._subscribe_candles(websocket, symbol, SignalEngine.ENTRY_GRANULARITY)

            with self._lock:
                self.status_text = "scanning"
            self._emit_status()

            try:
                while not self._stop_event.is_set():
                    try:
                        raw_message = await asyncio.wait_for(websocket.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        await self._send(websocket, {"ping": 1, "req_id": self._next_req_id()})
                        continue
                    self._handle_message(json.loads(raw_message))
            finally:
                try:
                    await self._send(websocket, {"forget_all": "candles", "req_id": self._next_req_id()})
                except Exception:
                    pass

    async def _subscribe_candles(self, websocket: Any, symbol: str, granularity: int) -> None:
        req_id = self._next_req_id()
        self._request_meta[req_id] = (symbol, granularity)
        count = (
            self.app.config["CANDLE_1H_COUNT"]
            if granularity == SignalEngine.TREND_GRANULARITY
            else self.app.config["CANDLE_15M_COUNT"]
        )
        await self._send(
            websocket,
            {
                "ticks_history": symbol,
                "style": "candles",
                "granularity": granularity,
                "count": count,
                "end": "latest",
                "subscribe": 1,
                "req_id": req_id,
            },
        )

    async def _send(self, websocket: Any, payload: dict[str, Any]) -> None:
        await self.client.send(websocket, payload)

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("msg_type")

        if "error" in message:
            error = message["error"]
            text = error.get("message", "Unknown Deriv API error")
            with self._lock:
                self.last_error = text
            self.socketio.emit("scanner_log", {"level": "error", "message": text})
            self._emit_status()
            return

        if msg_type == "active_symbols":
            self._update_display_names(message.get("active_symbols", []))
            self.socketio.emit("symbol_catalog", self._symbol_catalog())
            return

        if msg_type == "candles":
            self._handle_candle_history(message)
            return

        if msg_type == "ohlc":
            self._handle_ohlc(message)

    def _handle_candle_history(self, message: dict[str, Any]) -> None:
        symbol, granularity = self._metadata_from_message(message)
        if not symbol or not granularity:
            return

        candles = [
            self._normalise_candle(symbol, granularity, item)
            for item in message.get("candles", [])
        ]
        candles = [candle for candle in candles if candle is not None]
        if not candles:
            return

        with self._lock:
            history = self._candles[symbol][granularity]
            history.clear()
            for candle in sorted(candles, key=lambda item: item.open_time):
                history.append(candle)
            self._current_open_time[(symbol, granularity)] = history[-1].open_time
            self.candles_received += len(candles)

        self._emit_market_update(symbol)
        self._emit_status()

    def _handle_ohlc(self, message: dict[str, Any]) -> None:
        ohlc = message.get("ohlc", {})
        symbol = ohlc.get("symbol") or (message.get("echo_req") or {}).get("ticks_history")
        granularity = self._int_value(ohlc.get("granularity") or (message.get("echo_req") or {}).get("granularity"))
        candle = self._normalise_candle(symbol, granularity, ohlc)
        if candle is None:
            return

        key = (candle.symbol, candle.granularity)
        should_evaluate = False
        with self._lock:
            previous_open_time = self._current_open_time.get(key)
            if previous_open_time is not None and candle.open_time != previous_open_time:
                should_evaluate = candle.granularity == SignalEngine.ENTRY_GRANULARITY

            self._upsert_candle(candle)
            self._current_open_time[key] = candle.open_time
            self.candles_received += 1

        self._emit_market_update(candle.symbol)

        if should_evaluate:
            candles_1h = self._closed_candles(candle.symbol, SignalEngine.TREND_GRANULARITY)
            candles_15m = self._closed_candles(candle.symbol, SignalEngine.ENTRY_GRANULARITY)
            decision = self.engine.evaluate(candle.symbol, candles_1h, candles_15m)
            if decision:
                display_name = self.display_names.get(candle.symbol, candle.symbol)
                self._persist_and_emit_signal(decision, display_name)

        if self.candles_received % 10 == 0:
            self._emit_status()

    def _emit_market_update(self, symbol: str) -> None:
        candles_1h = self._closed_candles(symbol, SignalEngine.TREND_GRANULARITY)
        candles_15m = self._closed_candles(symbol, SignalEngine.ENTRY_GRANULARITY)
        latest_15m = candles_15m[-1] if candles_15m else self._latest_candle(symbol, SignalEngine.ENTRY_GRANULARITY)
        if latest_15m is None:
            return

        context = self.engine.market_context(candles_1h, candles_15m, symbol)
        payload = {
            "symbol": symbol,
            "display_name": self.display_names.get(symbol, symbol),
            "timeframe": "15M",
            "price": latest_15m.close,
            "epoch": latest_15m.open_time,
            "open_time": latest_15m.open_time,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "latest_15m": latest_15m.to_dict(),
            "latest_1h": candles_1h[-1].to_dict() if candles_1h else None,
            "history": [candle.close for candle in candles_15m[-60:]],
            "context": context,
        }

        with self._lock:
            self.latest_candles[symbol] = payload

        self.socketio.emit("candle_update", payload)

    def _persist_and_emit_signal(self, decision: SignalDecision, display_name: str) -> None:
        with self.app.app_context():
            signal = Signal(
                symbol=decision.symbol,
                display_name=display_name,
                direction=decision.direction,
                confidence=decision.confidence,
                price=decision.price,
                reason=decision.reason,
                indicators=decision.indicators,
            )
            db.session.add(signal)
            db.session.commit()
            payload = signal.to_dict()

        with self._lock:
            self.signals_generated += 1

        alert_queued = self.alerts.send_signal(payload)
        self.socketio.emit("signal_generated", payload)
        if alert_queued:
            self.socketio.emit(
                "scanner_log",
                {"level": "info", "message": f"Gmail alert queued for {decision.symbol}"},
            )
        self._emit_status()

    def _closed_candles(self, symbol: str, granularity: int) -> list[Candle]:
        now = int(time.time())
        with self._lock:
            return [
                candle
                for candle in self._candles[symbol][granularity]
                if candle.open_time + granularity <= now
            ]

    def _latest_candle(self, symbol: str, granularity: int) -> Candle | None:
        with self._lock:
            history = self._candles[symbol][granularity]
            return history[-1] if history else None

    def _upsert_candle(self, candle: Candle) -> None:
        history = self._candles[candle.symbol][candle.granularity]
        for index, existing in enumerate(history):
            if existing.open_time == candle.open_time:
                history[index] = candle
                return
        history.append(candle)
        ordered = sorted(history, key=lambda item: item.open_time)
        history.clear()
        history.extend(ordered)

    def _metadata_from_message(self, message: dict[str, Any]) -> tuple[str | None, int | None]:
        req_id = self._int_value(message.get("req_id"))
        if req_id and req_id in self._request_meta:
            return self._request_meta[req_id]

        echo_req = message.get("echo_req") or {}
        symbol = echo_req.get("ticks_history")
        granularity = self._int_value(echo_req.get("granularity"))
        return symbol, granularity

    def _normalise_candle(self, symbol: str | None, granularity: int | None, raw: dict[str, Any]) -> Candle | None:
        if not symbol or not granularity:
            return None

        open_time = self._int_value(raw.get("open_time") or raw.get("epoch"))
        if open_time is None:
            return None

        try:
            return Candle(
                symbol=symbol,
                granularity=granularity,
                open_time=open_time,
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _update_display_names(self, symbols: list[dict[str, Any]]) -> None:
        for item in symbols:
            symbol = item.get("symbol")
            display_name = item.get("display_name")
            if symbol and display_name:
                self.display_names[symbol] = display_name

    def _symbol_catalog(self) -> list[dict[str, str]]:
        return [
            {"symbol": symbol, "display_name": self.display_names.get(symbol, symbol)}
            for symbol in self.symbols
        ]

    def _emit_status(self) -> None:
        self.socketio.emit("scanner_status", self.status())

    def _next_req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    @staticmethod
    def _int_value(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_symbols(symbols: list[str] | None) -> list[str]:
        if not symbols:
            return []
        seen: set[str] = set()
        cleaned: list[str] = []
        for symbol in symbols:
            value = str(symbol).strip().upper()
            if value and value not in seen:
                seen.add(value)
                cleaned.append(value)
        return cleaned
