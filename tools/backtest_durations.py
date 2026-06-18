from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.signal_engine import Candle, SignalEngine  # noqa: E402
from config import Config  # noqa: E402


PAGE_LIMIT = 1000


def _parse_csv(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _make_candle(symbol: str, granularity: int, raw: dict) -> Candle:
    return Candle(
        symbol=symbol,
        granularity=granularity,
        open_time=int(raw["epoch"]),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
    )


async def _request(websocket, payload: dict) -> dict:
    await websocket.send(json.dumps(payload))
    while True:
        raw = await asyncio.wait_for(websocket.recv(), timeout=20)
        data = json.loads(raw)
        if data.get("error") and "req_id" not in data:
            raise RuntimeError(data["error"].get("message", "Deriv API error"))
        if data.get("req_id") == payload["req_id"]:
            if data.get("error"):
                raise RuntimeError(data["error"].get("message", "Deriv API error"))
            return data


async def _fetch_granularity(
    websocket,
    symbol: str,
    granularity: int,
    start_epoch: int,
    end_epoch: int,
    req_id: list[int],
) -> list[Candle]:
    candles_by_epoch: dict[int, Candle] = {}
    page_end = end_epoch

    while True:
        req_id[0] += 1
        payload = {
            "ticks_history": symbol,
            "style": "candles",
            "granularity": granularity,
            "end": page_end,
            "count": PAGE_LIMIT,
            "req_id": req_id[0],
        }
        data = await _request(websocket, payload)
        page = sorted(data.get("candles", []), key=lambda candle: int(candle["epoch"]))
        if not page:
            break

        for raw in page:
            epoch = int(raw["epoch"])
            if start_epoch <= epoch <= end_epoch:
                candles_by_epoch[epoch] = _make_candle(symbol, granularity, raw)

        first_epoch = int(page[0]["epoch"])
        if first_epoch <= start_epoch or len(page) < PAGE_LIMIT:
            break

        page_end = first_epoch - granularity
        await asyncio.sleep(0.02)

    return sorted(candles_by_epoch.values(), key=lambda candle: candle.open_time)


async def _fetch_symbol(symbol: str, start_epoch: int, end_epoch: int) -> dict[int, list[Candle]]:
    req_id = [0]
    async with websockets.connect(
        Config.DERIV_WS_URL,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
        max_queue=4096,
    ) as websocket:
        return {
            granularity: await _fetch_granularity(
                websocket,
                symbol,
                granularity,
                start_epoch,
                end_epoch,
                req_id,
            )
            for granularity in (
                SignalEngine.TREND_GRANULARITY,
                SignalEngine.ENTRY_GRANULARITY,
                300,
            )
        }


def _signed_percent(direction: str, entry_price: float, exit_price: float) -> float:
    raw = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price else 0.0
    return raw if direction == "RISE" else -raw


def _summarise(results: dict[int, dict]) -> list[dict]:
    summary = []
    for duration, result in sorted(results.items()):
        total = result["total"]
        values = sorted(result["values"])
        median = 0.0
        if total and total % 2:
            median = values[total // 2]
        elif total:
            median = (values[(total // 2) - 1] + values[total // 2]) / 2

        summary.append(
            {
                "duration_minutes": duration,
                "trades": total,
                "wins": result["wins"],
                "losses": result["losses"],
                "ties": result["ties"],
                "win_rate_pct": round((result["wins"] / total) * 100.0, 2) if total else 0.0,
                "avg_signed_pct": round(result["sum"] / total, 5) if total else 0.0,
                "median_signed_pct": round(median, 5),
            }
        )
    return summary


async def run_backtest(symbols: list[str], days: int, durations: list[int]) -> dict:
    now = int(time.time())
    max_closed_5m_close = (now // 300) * 300
    end_epoch = max_closed_5m_close
    start_epoch = end_epoch - (days * 24 * 3600)

    results = {
        duration: {"wins": 0, "losses": 0, "ties": 0, "total": 0, "sum": 0.0, "values": []}
        for duration in durations
    }
    signals_by_symbol: defaultdict[str, int] = defaultdict(int)
    per_symbol_duration: defaultdict[str, dict[int, dict[str, int]]] = defaultdict(
        lambda: {duration: {"wins": 0, "total": 0} for duration in durations}
    )
    sample_trades = []
    errors = []

    for symbol in symbols:
        try:
            series = await _fetch_symbol(symbol, start_epoch, end_epoch)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            continue

        candles_1h = series.get(SignalEngine.TREND_GRANULARITY, [])
        candles_15m = series.get(SignalEngine.ENTRY_GRANULARITY, [])
        candles_5m = series.get(300, [])
        if not candles_1h or not candles_15m or not candles_5m:
            errors.append({"symbol": symbol, "error": "missing candle history"})
            continue

        close_5m = {
            candle.open_time + 300: candle.close
            for candle in candles_5m
            if candle.open_time + 300 <= max_closed_5m_close
        }
        engine = SignalEngine(
            min_confidence=Config.SIGNAL_MIN_CONFIDENCE,
            cooldown_seconds=0,
            duration_minutes=durations[0],
            retest_candles=Config.SIGNAL_RETEST_CANDLES,
        )
        one_hour_index = 0

        for index, candle in enumerate(candles_15m):
            entry_time = candle.open_time + SignalEngine.ENTRY_GRANULARITY
            if entry_time + (max(durations) * 60) > max_closed_5m_close:
                continue
            if entry_time < start_epoch + (50 * 3600):
                continue

            while (
                one_hour_index < len(candles_1h)
                and candles_1h[one_hour_index].open_time + SignalEngine.TREND_GRANULARITY
                <= entry_time
            ):
                one_hour_index += 1

            decision = engine.evaluate(
                symbol,
                candles_1h[:one_hour_index],
                candles_15m[: index + 1],
            )
            if not decision:
                continue

            exits = {}
            for duration in durations:
                target = entry_time + (duration * 60)
                if target not in close_5m:
                    exits = {}
                    break
                exits[duration] = close_5m[target]
            if not exits:
                continue

            signals_by_symbol[symbol] += 1
            trade_outcomes = {}
            for duration, exit_price in exits.items():
                signed = _signed_percent(decision.direction, decision.price, exit_price)
                won = signed > 0
                tie = abs(signed) < 1e-12
                result = results[duration]
                result["total"] += 1
                result["sum"] += signed
                result["values"].append(signed)
                if won:
                    result["wins"] += 1
                    per_symbol_duration[symbol][duration]["wins"] += 1
                elif tie:
                    result["ties"] += 1
                else:
                    result["losses"] += 1
                per_symbol_duration[symbol][duration]["total"] += 1
                trade_outcomes[str(duration)] = round(signed, 5)

            if len(sample_trades) < 10:
                sample_trades.append(
                    {
                        "symbol": symbol,
                        "direction": decision.direction,
                        "entry_time": entry_time,
                        "entry_price": decision.price,
                        "confidence": decision.confidence,
                        "outcomes_signed_pct": trade_outcomes,
                    }
                )

    summary = _summarise(results)
    best = None
    if summary:
        best = sorted(
            summary,
            key=lambda item: (
                item["win_rate_pct"],
                item["avg_signed_pct"],
                -item["duration_minutes"],
            ),
            reverse=True,
        )[0]

    return {
        "test": "current_strategy_replay",
        "days": days,
        "start_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(start_epoch)),
        "end_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(end_epoch)),
        "symbols": symbols,
        "signal_count": sum(signals_by_symbol.values()),
        "summary": summary,
        "best": best,
        "signals_by_symbol": dict(sorted(signals_by_symbol.items())),
        "per_symbol_duration_win_rates": {
            symbol: {
                str(duration): (
                    round((data[duration]["wins"] / data[duration]["total"]) * 100.0, 2)
                    if data[duration]["total"]
                    else None
                )
                for duration in durations
            }
            for symbol, data in sorted(per_symbol_duration.items())
        },
        "errors": errors,
        "sample_trades": sample_trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest signal expiry durations.")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--symbols", default=",".join(Config.DERIV_SYMBOLS))
    parser.add_argument("--durations", default="5,10,15,30")
    args = parser.parse_args()

    symbols = _parse_csv(args.symbols)
    durations = [int(value) for value in _parse_csv(args.durations)]
    result = asyncio.run(run_backtest(symbols, args.days, durations))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
