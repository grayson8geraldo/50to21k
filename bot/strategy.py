"""
Strategy engine — implements the reversal strategy entry checklist.
Combines all analysis signals to generate trade signals.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
import pytz

import config
from bot.analysis import (
    Direction,
    ReversalSignal,
    SRZone,
    detect_reversal_pattern,
    detect_sr_zones,
    detect_trend_break,
    detect_unhealthy_move,
    price_in_sr_zone,
)

logger = logging.getLogger(__name__)
EST = pytz.timezone("US/Eastern")


@dataclass
class TradeSignal:
    """Complete trade signal ready for execution."""
    symbol: str
    direction: Direction
    entry_price: float       # Buy/sell stop price
    stop_loss: float
    take_profit: float
    risk_amount: float       # Dollar risk per share/contract
    pattern: str
    confidence: float
    sr_zone: Optional[SRZone]
    timestamp: datetime


class StrategyEngine:
    """
    Evaluates the full entry checklist for the reversal strategy:
    1. Price in S/R zone (15m)
    2. Unhealthy move detected (1m)
    3. Trend break (1m)
    4. Reversal pattern (1m)
    5. Timing window (9:45 / 10:00 EST)
    """

    def evaluate(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        df_1m: pd.DataFrame,
        day_open: Optional[float] = None,
    ) -> Optional[TradeSignal]:
        """
        Run the full checklist and return a TradeSignal if all conditions met.
        """
        if df_15m.empty or df_1m.empty:
            return None

        current_price = float(df_1m["close"].iloc[-1])
        now = datetime.now(EST)

        # ── 1. Detect S/R zones on 15m ──
        zones = detect_sr_zones(df_15m)
        if not zones:
            logger.debug("[%s] No S/R zones found", symbol)
            return None

        # Check if price is near a support zone (for longs) or resistance (for shorts)
        support_zone = price_in_sr_zone(current_price, zones, "support")
        resistance_zone = price_in_sr_zone(current_price, zones, "resistance")

        if not support_zone and not resistance_zone:
            logger.debug("[%s] Price %.2f not in any S/R zone", symbol, current_price)
            return None

        # Determine trade direction based on which zone price is in
        if support_zone:
            trade_dir = Direction.LONG
            active_zone = support_zone
            logger.info("[%s] ✓ Price in SUPPORT zone [%.2f - %.2f]",
                        symbol, active_zone.lower, active_zone.upper)
        else:
            trade_dir = Direction.SHORT
            active_zone = resistance_zone
            logger.info("[%s] ✓ Price in RESISTANCE zone [%.2f - %.2f]",
                        symbol, active_zone.lower, active_zone.upper)

        # ── 2. Detect unhealthy move ──
        unhealthy = detect_unhealthy_move(df_1m)
        if unhealthy is None:
            logger.debug("[%s] No unhealthy move detected", symbol)
            return None

        # Unhealthy move should be opposite to our trade direction
        if trade_dir == Direction.LONG and unhealthy.direction != Direction.SHORT:
            logger.debug("[%s] Unhealthy move is bullish, need bearish for long entry", symbol)
            return None
        if trade_dir == Direction.SHORT and unhealthy.direction != Direction.LONG:
            logger.debug("[%s] Unhealthy move is bearish, need bullish for short entry", symbol)
            return None

        logger.info("[%s] ✓ Unhealthy %s move: %d candles, %.2f%%",
                    symbol, unhealthy.direction.value,
                    unhealthy.candle_count, unhealthy.magnitude_pct)

        # ── 3. Check trend break ──
        trend_broken = detect_trend_break(df_1m, trade_dir)
        if not trend_broken:
            logger.debug("[%s] Trend not broken yet", symbol)
            return None

        logger.info("[%s] ✓ Trend break confirmed for %s", symbol, trade_dir.value)

        # ── 4. Detect reversal pattern ──
        pattern_result = detect_reversal_pattern(df_1m, trade_dir)
        if pattern_result is None:
            logger.debug("[%s] No reversal pattern found", symbol)
            return None

        pattern_name, entry_price, stop_loss = pattern_result
        logger.info("[%s] ✓ Pattern: %s | Entry: %.2f | Stop: %.2f",
                    symbol, pattern_name, entry_price, stop_loss)

        # ── 5. Check timing ──
        timing_ok = self._check_timing(now)
        confidence = 0.8 if timing_ok else 0.5
        if timing_ok:
            logger.info("[%s] ✓ Timing window active (%.0f min after open)",
                        symbol, (now.hour * 60 + now.minute) - (9 * 60 + 30))
        else:
            logger.info("[%s] ⚠ Outside ideal timing window (lower confidence)", symbol)

        # ── Calculate risk/reward ──
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share == 0:
            return None

        # Take profit: day open or full retracement of unhealthy move
        if trade_dir == Direction.LONG:
            tp_retracement = unhealthy.start_price
            tp_day_open = day_open if day_open and day_open > entry_price else None
            take_profit = tp_day_open if tp_day_open else tp_retracement
        else:
            tp_retracement = unhealthy.start_price
            tp_day_open = day_open if day_open and day_open < entry_price else None
            take_profit = tp_day_open if tp_day_open else tp_retracement

        reward = abs(take_profit - entry_price)
        rr_ratio = reward / risk_per_share if risk_per_share > 0 else 0

        if rr_ratio < config.RISK_REWARD_MIN:
            logger.info("[%s] ✗ R:R ratio %.1f < minimum %.1f",
                        symbol, rr_ratio, config.RISK_REWARD_MIN)
            return None

        logger.info("[%s] ✓ R:R ratio: %.1f (target: %.2f)", symbol, rr_ratio, take_profit)

        return TradeSignal(
            symbol=symbol,
            direction=trade_dir,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=risk_per_share,
            pattern=pattern_name,
            confidence=confidence,
            sr_zone=active_zone,
            timestamp=now,
        )

    @staticmethod
    def _check_timing(now: datetime) -> bool:
        """Check if current time is within a reversal timing window."""
        market_open_minutes = config.MARKET_OPEN_HOUR * 60 + config.MARKET_OPEN_MINUTE
        current_minutes = now.hour * 60 + now.minute
        minutes_after_open = current_minutes - market_open_minutes

        if minutes_after_open < 0:
            return False

        tolerance = config.REVERSAL_WINDOW_TOLERANCE
        for target in config.REVERSAL_WINDOWS_MINUTES:
            if abs(minutes_after_open - target) <= tolerance:
                return True

        return False
