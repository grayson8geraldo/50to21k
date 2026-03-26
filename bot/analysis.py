"""
Technical analysis module.
Detects support/resistance zones, trend breaks, unhealthy moves,
and reversal candlestick patterns.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


class Direction(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class SRZone:
    """Support or Resistance zone."""
    price: float
    zone_type: str   # "support" or "resistance"
    strength: int    # Number of touches
    upper: float
    lower: float


@dataclass
class UnhealthyMove:
    """Detected aggressive/unhealthy price move."""
    direction: Direction
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    candle_count: int
    magnitude_pct: float


@dataclass
class ReversalSignal:
    """A detected reversal setup."""
    direction: Direction       # Direction of the TRADE (not the prior move)
    entry_price: float         # Buy-stop / sell-stop price
    stop_loss: float           # Initial stop loss
    pattern_name: str          # e.g. "higher_low", "double_bottom"
    confidence: float          # 0-1 score
    sr_zone: Optional[SRZone] = None
    unhealthy_move: Optional[UnhealthyMove] = None


def detect_sr_zones(df_15m: pd.DataFrame) -> list[SRZone]:
    """
    Detect support and resistance zones from 15-minute swing highs/lows.
    """
    if len(df_15m) < config.SR_LOOKBACK_BARS:
        return []

    df = df_15m.tail(config.SR_LOOKBACK_BARS).copy()
    highs = df["high"].values
    lows = df["low"].values
    w = config.SR_SWING_WINDOW

    swing_highs = []
    swing_lows = []

    for i in range(w, len(df) - w):
        # Swing high: higher than w bars on each side
        if all(highs[i] >= highs[i - j] for j in range(1, w + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, w + 1)):
            swing_highs.append(highs[i])

        # Swing low: lower than w bars on each side
        if all(lows[i] <= lows[i - j] for j in range(1, w + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, w + 1)):
            swing_lows.append(lows[i])

    zones = []
    current_price = float(df["close"].iloc[-1])
    tol = config.SR_ZONE_TOLERANCE_PCT / 100.0

    # Cluster nearby levels
    all_levels = [(p, "resistance") for p in swing_highs] + \
                 [(p, "support") for p in swing_lows]

    used = set()
    for i, (price, ztype) in enumerate(all_levels):
        if i in used:
            continue
        cluster = [price]
        for j, (p2, _) in enumerate(all_levels):
            if j != i and j not in used and abs(p2 - price) / price < tol:
                cluster.append(p2)
                used.add(j)
        used.add(i)

        avg_price = np.mean(cluster)
        zone_range = avg_price * tol
        # Classify based on position relative to current price
        if avg_price < current_price:
            z_type = "support"
        else:
            z_type = "resistance"

        zones.append(SRZone(
            price=avg_price,
            zone_type=z_type,
            strength=len(cluster),
            upper=avg_price + zone_range,
            lower=avg_price - zone_range,
        ))

    zones.sort(key=lambda z: abs(z.price - current_price))
    logger.debug("Detected %d S/R zones", len(zones))
    return zones


def detect_unhealthy_move(df_1m: pd.DataFrame) -> Optional[UnhealthyMove]:
    """
    Detect aggressive/unhealthy moves: 3+ large same-direction candles
    without meaningful pullback.
    """
    if len(df_1m) < config.UNHEALTHY_MIN_CANDLES + 5:
        return None

    closes = df_1m["close"].values
    opens = df_1m["open"].values
    highs = df_1m["high"].values
    lows = df_1m["low"].values

    # Calculate average body size over recent history
    bodies = np.abs(closes - opens)
    avg_body = np.mean(bodies[-30:]) if len(bodies) >= 30 else np.mean(bodies)
    if avg_body == 0:
        return None

    min_body = avg_body * config.UNHEALTHY_AVG_BODY_MULTIPLIER
    min_candles = config.UNHEALTHY_MIN_CANDLES

    # Check last N candles for consecutive aggressive moves
    lookback = min(20, len(df_1m))
    for start in range(len(df_1m) - lookback, len(df_1m) - min_candles + 1):
        # Check bearish sequence
        bearish_count = 0
        for i in range(start, min(start + 8, len(df_1m))):
            body = closes[i] - opens[i]
            candle_range = highs[i] - lows[i]
            if candle_range == 0:
                break
            body_ratio = abs(body) / candle_range
            if body < 0 and abs(body) >= min_body and body_ratio >= config.UNHEALTHY_BODY_RATIO:
                bearish_count += 1
            else:
                break

        if bearish_count >= min_candles:
            end_i = start + bearish_count - 1
            mag = abs(closes[end_i] - opens[start]) / opens[start] * 100
            return UnhealthyMove(
                direction=Direction.SHORT,  # Move was bearish
                start_idx=start,
                end_idx=end_i,
                start_price=float(opens[start]),
                end_price=float(closes[end_i]),
                candle_count=bearish_count,
                magnitude_pct=mag,
            )

        # Check bullish sequence
        bullish_count = 0
        for i in range(start, min(start + 8, len(df_1m))):
            body = closes[i] - opens[i]
            candle_range = highs[i] - lows[i]
            if candle_range == 0:
                break
            body_ratio = abs(body) / candle_range
            if body > 0 and abs(body) >= min_body and body_ratio >= config.UNHEALTHY_BODY_RATIO:
                bullish_count += 1
            else:
                break

        if bullish_count >= min_candles:
            end_i = start + bullish_count - 1
            mag = abs(closes[end_i] - opens[start]) / opens[start] * 100
            return UnhealthyMove(
                direction=Direction.LONG,  # Move was bullish
                start_idx=start,
                end_idx=end_i,
                start_price=float(opens[start]),
                end_price=float(closes[end_i]),
                candle_count=bullish_count,
                magnitude_pct=mag,
            )

    return None


def detect_trend_break(df_1m: pd.DataFrame, direction: Direction) -> bool:
    """
    Detect if the current local trend has been broken.
    For a long setup: checks if a downtrend line is broken (price closes above).
    For a short setup: checks if an uptrend line is broken (price closes below).
    """
    lookback = min(config.TRENDLINE_LOOKBACK, len(df_1m))
    if lookback < 5:
        return False

    df = df_1m.tail(lookback)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    if direction == Direction.LONG:
        # Looking for downtrend break: find descending swing highs
        swing_high_indices = []
        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                swing_high_indices.append(i)

        if len(swing_high_indices) < config.TRENDLINE_MIN_TOUCHES:
            return False

        # Check if swing highs are descending (downtrend)
        recent_swings = swing_high_indices[-3:]
        swing_values = [highs[i] for i in recent_swings]
        if not all(swing_values[i] >= swing_values[i + 1] for i in range(len(swing_values) - 1)):
            return False  # Not a clear downtrend

        # Check if latest close breaks above the last swing high
        last_swing_high = highs[recent_swings[-1]]
        if closes[-1] > last_swing_high:
            logger.info("Downtrend break detected: close %.2f > swing high %.2f",
                        closes[-1], last_swing_high)
            return True

    elif direction == Direction.SHORT:
        # Looking for uptrend break: find ascending swing lows
        swing_low_indices = []
        for i in range(1, len(lows) - 1):
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                swing_low_indices.append(i)

        if len(swing_low_indices) < config.TRENDLINE_MIN_TOUCHES:
            return False

        recent_swings = swing_low_indices[-3:]
        swing_values = [lows[i] for i in recent_swings]
        if not all(swing_values[i] <= swing_values[i + 1] for i in range(len(swing_values) - 1)):
            return False

        last_swing_low = lows[recent_swings[-1]]
        if closes[-1] < last_swing_low:
            logger.info("Uptrend break detected: close %.2f < swing low %.2f",
                        closes[-1], last_swing_low)
            return True

    return False


def detect_reversal_pattern(
    df_1m: pd.DataFrame,
    trade_direction: Direction,
) -> Optional[tuple[str, float, float]]:
    """
    Detect reversal candlestick patterns on 1m chart.
    Returns (pattern_name, entry_price, stop_loss) or None.

    For LONG: looks for double bottom / higher low after a dip.
    For SHORT: looks for double top / lower high after a spike.
    """
    lookback = min(config.PATTERN_LOOKBACK, len(df_1m))
    if lookback < 8:
        return None

    df = df_1m.tail(lookback)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values

    if trade_direction == Direction.LONG:
        # Find the lowest low in the lookback
        min_idx = np.argmin(lows)
        min_low = lows[min_idx]

        # Need at least a few bars after the low
        if min_idx >= len(lows) - 3:
            return None

        # Look for higher low after the minimum
        tol = min_low * (config.HIGHER_LOW_TOLERANCE_PCT / 100.0)
        post_lows = lows[min_idx + 1:]
        higher_low_found = False
        higher_low_val = None

        for i, low_val in enumerate(post_lows):
            # Check for a dip that stays above the original low (higher low)
            actual_idx = min_idx + 1 + i
            if actual_idx < len(lows) - 1:  # Not the last bar
                if low_val > min_low and low_val < lows[min_idx] + (highs[min_idx] - lows[min_idx]):
                    # Check if next bar bounces up
                    if actual_idx + 1 < len(closes) and closes[actual_idx + 1] > closes[actual_idx]:
                        higher_low_found = True
                        higher_low_val = low_val
                        break

        if not higher_low_found:
            # Check for double bottom
            for i, low_val in enumerate(post_lows):
                if abs(low_val - min_low) <= tol * 3:
                    actual_idx = min_idx + 1 + i
                    if actual_idx + 1 < len(closes) and closes[actual_idx + 1] > opens[actual_idx + 1]:
                        # Bullish candle after double bottom
                        entry = float(max(highs[actual_idx], highs[actual_idx + 1]))
                        stop = float(min_low - tol)
                        return ("double_bottom", entry, stop)

            return None

        # Higher low pattern found
        hl_idx = min_idx + 1 + list(post_lows).index(higher_low_val)
        # Entry: above the high of the reversal candle
        reversal_high = max(highs[hl_idx:hl_idx + 2]) if hl_idx + 1 < len(highs) else highs[hl_idx]
        entry = float(reversal_high)
        stop = float(min(min_low, higher_low_val) - tol)
        return ("higher_low", entry, stop)

    elif trade_direction == Direction.SHORT:
        # Find the highest high in the lookback
        max_idx = np.argmax(highs)
        max_high = highs[max_idx]

        if max_idx >= len(highs) - 3:
            return None

        tol = max_high * (config.HIGHER_LOW_TOLERANCE_PCT / 100.0)
        post_highs = highs[max_idx + 1:]

        lower_high_found = False
        lower_high_val = None

        for i, high_val in enumerate(post_highs):
            actual_idx = max_idx + 1 + i
            if actual_idx < len(highs) - 1:
                if high_val < max_high and high_val > lows[max_idx]:
                    if actual_idx + 1 < len(closes) and closes[actual_idx + 1] < closes[actual_idx]:
                        lower_high_found = True
                        lower_high_val = high_val
                        break

        if not lower_high_found:
            # Check for double top
            for i, high_val in enumerate(post_highs):
                if abs(high_val - max_high) <= tol * 3:
                    actual_idx = max_idx + 1 + i
                    if actual_idx + 1 < len(closes) and closes[actual_idx + 1] < opens[actual_idx + 1]:
                        entry = float(min(lows[actual_idx], lows[actual_idx + 1]))
                        stop = float(max_high + tol)
                        return ("double_top", entry, stop)
            return None

        lh_idx = max_idx + 1 + list(post_highs).index(lower_high_val)
        reversal_low = min(lows[lh_idx:lh_idx + 2]) if lh_idx + 1 < len(lows) else lows[lh_idx]
        entry = float(reversal_low)
        stop = float(max(max_high, lower_high_val) + tol)
        return ("lower_high", entry, stop)

    return None


def price_in_sr_zone(price: float, zones: list[SRZone], zone_type: str) -> Optional[SRZone]:
    """Check if price is within a support or resistance zone."""
    for zone in zones:
        if zone.zone_type == zone_type and zone.lower <= price <= zone.upper:
            return zone
    return None
