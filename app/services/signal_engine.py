from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean


@dataclass(frozen=True)
class Candle:
    symbol: str
    granularity: int
    open_time: int
    open: float
    high: float
    low: float
    close: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "granularity": self.granularity,
            "open_time": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }


@dataclass(frozen=True)
class PriceZone:
    kind: str
    low: float
    high: float
    center: float
    touches: int
    caused_move: bool

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "low": round(self.low, 5),
            "high": round(self.high, 5),
            "center": round(self.center, 5),
            "touches": self.touches,
            "caused_move": self.caused_move,
        }


@dataclass(frozen=True)
class TrendResult:
    direction: str
    reason: str
    support: PriceZone | None
    resistance: PriceZone | None


@dataclass(frozen=True)
class MarketState:
    state: str
    reason: str


@dataclass(frozen=True)
class PendingBos:
    direction: str
    zone: PriceZone
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class SignalDecision:
    symbol: str
    direction: str
    confidence: float
    price: float
    reason: str
    indicators: dict


class SignalEngine:
    TREND_GRANULARITY = 3600
    ENTRY_GRANULARITY = 900

    def __init__(
        self,
        min_confidence: float = 80,
        cooldown_seconds: int = 600,
        duration_minutes: int = 5,
        retest_candles: int = 6,
    ) -> None:
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds
        self.duration_minutes = duration_minutes
        self.retest_candles = retest_candles
        self._last_signal_at: dict[str, datetime] = {}
        self._last_signal_candle: dict[str, int] = {}
        self._pending_bos: dict[str, PendingBos] = {}

    def evaluate(
        self,
        symbol: str,
        candles_1h: list[Candle],
        candles_15m: list[Candle],
    ) -> SignalDecision | None:
        if len(candles_1h) < 18 or len(candles_15m) < 24:
            return None

        current = candles_15m[-1]
        previous = candles_15m[-2]
        if self._is_on_cooldown(symbol, current.open_time):
            return None

        trend = self.detect_trend(candles_1h)
        if trend.direction not in {"uptrend", "downtrend"}:
            self._pending_bos.pop(symbol, None)
            return None

        market_state = self.detect_market_state(candles_15m)
        if market_state.state == "sideways":
            self._pending_bos.pop(symbol, None)
            return None

        support = self.detect_zone(candles_15m[:-1], "support")
        resistance = self.detect_zone(candles_15m[:-1], "resistance")
        if support is None or resistance is None:
            return None

        if trend.direction == "uptrend":
            decision = self._evaluate_rise(symbol, current, previous, trend, resistance)
        else:
            decision = self._evaluate_fall(symbol, current, previous, trend, support)

        if decision and decision.confidence >= self.min_confidence:
            now = datetime.now(timezone.utc)
            self._last_signal_at[symbol] = now
            self._last_signal_candle[symbol] = current.open_time
            self._pending_bos.pop(symbol, None)
            return decision

        return None

    def market_context(
        self,
        candles_1h: list[Candle],
        candles_15m: list[Candle],
        symbol: str | None = None,
    ) -> dict:
        trend = self.detect_trend(candles_1h)
        market_state = self.detect_market_state(candles_15m)
        support = self.detect_zone(candles_15m, "support")
        resistance = self.detect_zone(candles_15m, "resistance")
        pending = self._pending_bos.get(symbol or "") if symbol else None

        return {
            "trend": trend.direction,
            "trend_reason": trend.reason,
            "market_state": market_state.state,
            "market_state_reason": market_state.reason,
            "major_support": trend.support.to_dict() if trend.support else None,
            "major_resistance": trend.resistance.to_dict() if trend.resistance else None,
            "entry_support": support.to_dict() if support else None,
            "entry_resistance": resistance.to_dict() if resistance else None,
            "pending_bos": {
                "direction": pending.direction,
                "zone": pending.zone.to_dict(),
                "created_at": pending.created_at,
                "expires_at": pending.expires_at,
            }
            if pending
            else None,
            "strategy": "1H trend direction + 15M market-state/BOS/confirmation",
            "duration_minutes": self.duration_minutes,
        }

    def detect_trend(self, candles: list[Candle]) -> TrendResult:
        if len(candles) < 18:
            if len(candles) >= 2 and candles[-1].close >= candles[0].close:
                return TrendResult("uptrend", "1H direction resolved upward from available candles", None, None)
            if len(candles) >= 2:
                return TrendResult("downtrend", "1H direction resolved downward from available candles", None, None)
            return TrendResult("waiting", "waiting for 1H candles", None, None)

        recent = candles[-50:]
        atr = self._atr(recent)
        last_close = recent[-1].close
        threshold = max(atr * 0.12, last_close * 0.00002)
        highs = self._swing_points(recent, "high")
        lows = self._swing_points(recent, "low")
        support = self.detect_zone(recent, "support")
        resistance = self.detect_zone(recent, "resistance")

        last_highs = highs[-3:]
        last_lows = lows[-3:]
        higher_highs = self._strictly_rising([point[1] for point in last_highs], threshold)
        higher_lows = self._strictly_rising([point[1] for point in last_lows], threshold)
        lower_highs = self._strictly_falling([point[1] for point in last_highs], threshold)
        lower_lows = self._strictly_falling([point[1] for point in last_lows], threshold)
        slope = self._linear_slope([candle.close for candle in recent[-20:]])

        if higher_highs and higher_lows and slope > 0:
            return TrendResult("uptrend", "1H higher highs and higher lows", support, resistance)

        if lower_highs and lower_lows and slope < 0:
            return TrendResult("downtrend", "1H lower highs and lower lows", support, resistance)

        direction, reason = self._resolve_1h_direction(recent, highs, lows, threshold)
        return TrendResult(direction, reason, support, resistance)

    def detect_market_state(self, candles: list[Candle]) -> MarketState:
        if len(candles) < 24:
            return MarketState("sideways", "not enough 15M candles for market-state filter")

        recent = candles[-48:]
        atr = self._atr(recent)
        support = self.detect_zone(recent, "support")
        resistance = self.detect_zone(recent, "resistance")
        price_range = max(candle.high for candle in recent[-24:]) - min(candle.low for candle in recent[-24:])
        closes = [candle.close for candle in recent[-18:]]
        slope = abs(self._linear_slope(closes))
        avg_close = mean(closes)
        compression = price_range <= max(atr * 3.0, avg_close * 0.00035)
        flat_closes = slope <= max(atr * 0.06, avg_close * 0.000015)

        if support and resistance:
            zone_width = resistance.high - support.low
            closes_inside = sum(1 for close in closes if support.low <= close <= resistance.high)
            mostly_inside_zones = closes_inside >= int(len(closes) * 0.72)
            repeated_rejections = support.touches >= 2 and resistance.touches >= 2
            if mostly_inside_zones and repeated_rejections and (compression or flat_closes or zone_width <= atr * 4.0):
                return MarketState("sideways", "15M price is ranging between support and resistance")

        if compression and flat_closes:
            return MarketState("sideways", "15M candles are compressed with flat closes")

        return MarketState("trending", "15M market is not sideways")

    def detect_zone(self, candles: list[Candle], kind: str) -> PriceZone | None:
        if len(candles) < 8:
            return None

        recent = candles[-60:]
        atr = self._atr(recent)
        pivots = self._swing_points(recent, "high" if kind == "resistance" else "low")
        if pivots:
            center = max(point[1] for point in pivots) if kind == "resistance" else min(point[1] for point in pivots)
        else:
            center = max(candle.high for candle in recent) if kind == "resistance" else min(candle.low for candle in recent)

        width = max(atr * 0.32, center * 0.00008)
        zone_low = center - width
        zone_high = center + width
        touches = 0
        caused_move = False

        for index, candle in enumerate(recent):
            if kind == "resistance":
                touched = zone_low <= candle.high <= zone_high and candle.close < zone_high
                rejected = touched and candle.close < candle.open
                future_move = self._future_move_from_zone(recent, index, center, "down")
            else:
                touched = zone_low <= candle.low <= zone_high and candle.close > zone_low
                rejected = touched and candle.close > candle.open
                future_move = self._future_move_from_zone(recent, index, center, "up")

            if touched or rejected:
                touches += 1
            if future_move >= atr * 1.2:
                caused_move = True

        if touches < 1 and not caused_move:
            return None

        return PriceZone(
            kind=kind,
            low=zone_low,
            high=zone_high,
            center=center,
            touches=touches,
            caused_move=caused_move,
        )

    def _evaluate_rise(
        self,
        symbol: str,
        current: Candle,
        previous: Candle,
        trend: TrendResult,
        resistance: PriceZone,
    ) -> SignalDecision | None:
        pattern, pattern_score = self._bullish_confirmation(previous, current)
        bos = previous.close <= resistance.high and current.close > resistance.high
        pending = self._pending_bos.get(symbol)

        if bos:
            self._pending_bos[symbol] = PendingBos(
                direction="RISE",
                zone=resistance,
                created_at=current.open_time,
                expires_at=current.open_time + (self.ENTRY_GRANULARITY * self.retest_candles),
            )
            if pattern:
                return self._build_decision(
                    symbol=symbol,
                    direction="RISE",
                    current=current,
                    trend=trend,
                    zone=resistance,
                    confirmation=pattern,
                    pattern_score=pattern_score,
                    bos_mode="15M close broke resistance",
                    retest=False,
                )
            return None

        if pending and pending.direction == "RISE":
            if current.open_time > pending.expires_at or current.close < pending.zone.low:
                self._pending_bos.pop(symbol, None)
                return None

            retested = current.low <= pending.zone.high and current.close > pending.zone.high
            if retested and pattern:
                return self._build_decision(
                    symbol=symbol,
                    direction="RISE",
                    current=current,
                    trend=trend,
                    zone=pending.zone,
                    confirmation=pattern,
                    pattern_score=pattern_score,
                    bos_mode="15M resistance retest after BOS",
                    retest=True,
                )

        return None

    def _evaluate_fall(
        self,
        symbol: str,
        current: Candle,
        previous: Candle,
        trend: TrendResult,
        support: PriceZone,
    ) -> SignalDecision | None:
        pattern, pattern_score = self._bearish_confirmation(previous, current)
        bos = previous.close >= support.low and current.close < support.low
        pending = self._pending_bos.get(symbol)

        if bos:
            self._pending_bos[symbol] = PendingBos(
                direction="FALL",
                zone=support,
                created_at=current.open_time,
                expires_at=current.open_time + (self.ENTRY_GRANULARITY * self.retest_candles),
            )
            if pattern:
                return self._build_decision(
                    symbol=symbol,
                    direction="FALL",
                    current=current,
                    trend=trend,
                    zone=support,
                    confirmation=pattern,
                    pattern_score=pattern_score,
                    bos_mode="15M close broke support",
                    retest=False,
                )
            return None

        if pending and pending.direction == "FALL":
            if current.open_time > pending.expires_at or current.close > pending.zone.high:
                self._pending_bos.pop(symbol, None)
                return None

            retested = current.high >= pending.zone.low and current.close < pending.zone.low
            if retested and pattern:
                return self._build_decision(
                    symbol=symbol,
                    direction="FALL",
                    current=current,
                    trend=trend,
                    zone=pending.zone,
                    confirmation=pattern,
                    pattern_score=pattern_score,
                    bos_mode="15M support retest after BOS",
                    retest=True,
                )

        return None

    def _build_decision(
        self,
        symbol: str,
        direction: str,
        current: Candle,
        trend: TrendResult,
        zone: PriceZone,
        confirmation: str,
        pattern_score: float,
        bos_mode: str,
        retest: bool,
    ) -> SignalDecision:
        confidence = min(100.0, 40.0 + 30.0 + pattern_score)
        setup = "BOS retest" if retest else "BOS candle"
        reason = (
            f"{trend.reason}; {bos_mode}; {confirmation}; "
            f"{setup}; duration {self.duration_minutes} minutes"
        )
        indicators = {
            "duration_minutes": self.duration_minutes,
            "timeframes": {"trend": "1H", "entry": "15M"},
            "trend": trend.direction,
            "trend_reason": trend.reason,
            "market_state": "trending",
            "market_state_reason": "15M market is not sideways",
            "bos": bos_mode,
            "confirmation": confirmation,
            "entry_setup": setup,
            "zone": zone.to_dict(),
            "major_support": trend.support.to_dict() if trend.support else None,
            "major_resistance": trend.resistance.to_dict() if trend.resistance else None,
            "signal_candle": current.to_dict(),
            "confidence_weights": {"1h_trend": 40, "15m_bos": 30, "15m_confirmation": pattern_score},
        }
        return SignalDecision(
            symbol=symbol,
            direction=direction,
            confidence=round(confidence, 1),
            price=current.close,
            reason=reason,
            indicators=indicators,
        )

    def _bullish_confirmation(self, previous: Candle, current: Candle) -> tuple[str | None, float]:
        if self._bullish_engulfing(previous, current):
            return "Bullish Engulfing", 30.0
        if self._bullish_pin_bar(current):
            return "Bullish Pin Bar", 30.0
        if self._strong_bullish_candle(current):
            return "Strong bullish candle closing above resistance", 25.0
        return None, 0.0

    def _bearish_confirmation(self, previous: Candle, current: Candle) -> tuple[str | None, float]:
        if self._bearish_engulfing(previous, current):
            return "Bearish Engulfing", 30.0
        if self._bearish_pin_bar(current):
            return "Bearish Pin Bar", 30.0
        if self._strong_bearish_candle(current):
            return "Strong bearish candle closing below support", 25.0
        return None, 0.0

    @staticmethod
    def _bullish_engulfing(previous: Candle, current: Candle) -> bool:
        return (
            previous.close < previous.open
            and current.close > current.open
            and current.open <= previous.close
            and current.close >= previous.open
        )

    @staticmethod
    def _bearish_engulfing(previous: Candle, current: Candle) -> bool:
        return (
            previous.close > previous.open
            and current.close < current.open
            and current.open >= previous.close
            and current.close <= previous.open
        )

    @staticmethod
    def _bullish_pin_bar(candle: Candle) -> bool:
        body = abs(candle.close - candle.open)
        full_range = max(candle.high - candle.low, 1e-9)
        lower_wick = min(candle.open, candle.close) - candle.low
        upper_wick = candle.high - max(candle.open, candle.close)
        return (
            lower_wick >= max(body * 2.0, full_range * 0.45)
            and upper_wick <= full_range * 0.28
            and candle.close > candle.open
        )

    @staticmethod
    def _bearish_pin_bar(candle: Candle) -> bool:
        body = abs(candle.close - candle.open)
        full_range = max(candle.high - candle.low, 1e-9)
        lower_wick = min(candle.open, candle.close) - candle.low
        upper_wick = candle.high - max(candle.open, candle.close)
        return (
            upper_wick >= max(body * 2.0, full_range * 0.45)
            and lower_wick <= full_range * 0.28
            and candle.close < candle.open
        )

    @staticmethod
    def _strong_bullish_candle(candle: Candle) -> bool:
        full_range = max(candle.high - candle.low, 1e-9)
        body = candle.close - candle.open
        close_position = (candle.close - candle.low) / full_range
        return body > 0 and body >= full_range * 0.62 and close_position >= 0.75

    @staticmethod
    def _strong_bearish_candle(candle: Candle) -> bool:
        full_range = max(candle.high - candle.low, 1e-9)
        body = candle.open - candle.close
        close_position = (candle.close - candle.low) / full_range
        return body > 0 and body >= full_range * 0.62 and close_position <= 0.25

    def _is_on_cooldown(self, symbol: str, candle_open_time: int) -> bool:
        if self._last_signal_candle.get(symbol) == candle_open_time:
            return True

        last_signal_at = self._last_signal_at.get(symbol)
        if not last_signal_at:
            return False

        return (datetime.now(timezone.utc) - last_signal_at).total_seconds() < self.cooldown_seconds

    @staticmethod
    def _atr(candles: list[Candle], period: int = 14) -> float:
        if len(candles) < 2:
            return 0.0

        recent = candles[-(period + 1) :]
        ranges = []
        for index in range(1, len(recent)):
            current = recent[index]
            previous = recent[index - 1]
            ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous.close),
                    abs(current.low - previous.close),
                )
            )
        return mean(ranges) if ranges else 0.0

    @staticmethod
    def _swing_points(candles: list[Candle], field: str) -> list[tuple[int, float]]:
        if len(candles) < 5:
            return []

        points: list[tuple[int, float]] = []
        for index in range(2, len(candles) - 2):
            value = getattr(candles[index], field)
            left = candles[index - 2 : index]
            right = candles[index + 1 : index + 3]
            neighbors = left + right
            if field == "high" and value >= max(candle.high for candle in neighbors):
                points.append((index, value))
            elif field == "low" and value <= min(candle.low for candle in neighbors):
                points.append((index, value))
        return points

    @staticmethod
    def _strictly_rising(values: list[float], threshold: float) -> bool:
        if len(values) < 2:
            return False
        return all(right > left + threshold for left, right in zip(values, values[1:]))

    @staticmethod
    def _strictly_falling(values: list[float], threshold: float) -> bool:
        if len(values) < 2:
            return False
        return all(right < left - threshold for left, right in zip(values, values[1:]))

    @staticmethod
    def _linear_slope(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        x_values = list(range(len(values)))
        x_mean = mean(x_values)
        y_mean = mean(values)
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, values))
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        return numerator / denominator if denominator else 0.0

    def _resolve_1h_direction(
        self,
        candles: list[Candle],
        highs: list[tuple[int, float]],
        lows: list[tuple[int, float]],
        threshold: float,
    ) -> tuple[str, str]:
        closes = [candle.close for candle in candles]
        score = 0.0
        signals: list[str] = []

        slope_20 = self._linear_slope(closes[-20:])
        if slope_20 > 0:
            score += 2.0
            signals.append("close slope rising")
        elif slope_20 < 0:
            score -= 2.0
            signals.append("close slope falling")

        fast_average = mean(closes[-6:])
        slow_average = mean(closes[-18:])
        if fast_average > slow_average:
            score += 1.6
            signals.append("recent closes above 1H average")
        elif fast_average < slow_average:
            score -= 1.6
            signals.append("recent closes below 1H average")

        if closes[-1] > closes[-10] + threshold:
            score += 1.4
            signals.append("10-hour momentum up")
        elif closes[-1] < closes[-10] - threshold:
            score -= 1.4
            signals.append("10-hour momentum down")

        recent_pressure = sum(candle.close - candle.open for candle in candles[-8:])
        if recent_pressure > 0:
            score += 1.0
            signals.append("recent candle pressure bullish")
        elif recent_pressure < 0:
            score -= 1.0
            signals.append("recent candle pressure bearish")

        if len(highs) >= 2:
            if highs[-1][1] > highs[-2][1] + threshold:
                score += 1.0
                signals.append("latest swing high is higher")
            elif highs[-1][1] < highs[-2][1] - threshold:
                score -= 1.0
                signals.append("latest swing high is lower")

        if len(lows) >= 2:
            if lows[-1][1] > lows[-2][1] + threshold:
                score += 1.0
                signals.append("latest swing low is higher")
            elif lows[-1][1] < lows[-2][1] - threshold:
                score -= 1.0
                signals.append("latest swing low is lower")

        direction = "uptrend" if score >= 0 else "downtrend"
        direction_word = "uptrend" if direction == "uptrend" else "downtrend"
        reason_bits = signals[:3] or ["latest 1H close comparison"]
        return direction, f"1H direction resolved as {direction_word}: {', '.join(reason_bits)}"

    @staticmethod
    def _future_move_from_zone(candles: list[Candle], index: int, center: float, direction: str) -> float:
        future = candles[index + 1 : index + 4]
        if not future:
            return 0.0
        if direction == "down":
            return center - min(candle.low for candle in future)
        return max(candle.high for candle in future) - center
