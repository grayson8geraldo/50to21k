"""
Risk management module.
Handles position sizing, stop loss management, trailing stops,
and break-even logic.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import config
from bot.analysis import Direction

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """An open paper trading position."""
    symbol: str
    direction: Direction
    entry_price: float
    quantity: int               # Number of shares/contracts
    stop_loss: float
    take_profit: float
    initial_risk: float         # Risk per share at entry
    current_stop: float         # Current (possibly trailed) stop
    breakeven_hit: bool = False
    highest_since_entry: float = 0.0   # For trailing (long)
    lowest_since_entry: float = 999999.0  # For trailing (short)
    pnl: float = 0.0
    status: str = "open"        # open, closed_tp, closed_sl, closed_manual

    @property
    def risk_dollars(self) -> float:
        return self.initial_risk * self.quantity


class RiskManager:
    """Manages position sizing and active position risk."""

    def __init__(self, balance: float):
        self.balance = balance

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
    ) -> int:
        """
        Calculate position size based on max risk per trade.
        Risk = (entry - stop) × quantity ≤ max_risk_pct × balance
        """
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share == 0:
            return 0

        max_risk_dollars = self.balance * (config.MAX_RISK_PER_TRADE_PCT / 100.0)
        quantity = int(max_risk_dollars / risk_per_share)

        # Ensure we can afford the position
        position_cost = entry_price * quantity
        if position_cost > self.balance:
            quantity = int(self.balance / entry_price)

        return max(quantity, 0)

    def update_position(self, position: Position, current_price: float) -> Position:
        """
        Update position with current price:
        - Check stop loss hit
        - Check take profit hit
        - Move to break-even if triggered
        - Trail stop loss
        """
        if position.status != "open":
            return position

        # Update tracking prices
        if position.direction == Direction.LONG:
            position.highest_since_entry = max(position.highest_since_entry, current_price)
        else:
            position.lowest_since_entry = min(position.lowest_since_entry, current_price)

        # ── Check stop loss ──
        if position.direction == Direction.LONG and current_price <= position.current_stop:
            position.status = "closed_sl"
            position.pnl = (position.current_stop - position.entry_price) * position.quantity
            logger.info("[%s] STOP LOSS hit at %.2f | P&L: $%.2f",
                        position.symbol, position.current_stop, position.pnl)
            return position

        if position.direction == Direction.SHORT and current_price >= position.current_stop:
            position.status = "closed_sl"
            position.pnl = (position.entry_price - position.current_stop) * position.quantity
            logger.info("[%s] STOP LOSS hit at %.2f | P&L: $%.2f",
                        position.symbol, position.current_stop, position.pnl)
            return position

        # ── Check take profit ──
        if position.direction == Direction.LONG and current_price >= position.take_profit:
            position.status = "closed_tp"
            position.pnl = (position.take_profit - position.entry_price) * position.quantity
            logger.info("[%s] TAKE PROFIT hit at %.2f | P&L: $%.2f",
                        position.symbol, position.take_profit, position.pnl)
            return position

        if position.direction == Direction.SHORT and current_price <= position.take_profit:
            position.status = "closed_tp"
            position.pnl = (position.entry_price - position.take_profit) * position.quantity
            logger.info("[%s] TAKE PROFIT hit at %.2f | P&L: $%.2f",
                        position.symbol, position.take_profit, position.pnl)
            return position

        # ── Break-even logic ──
        if not position.breakeven_hit:
            price_moved = (
                current_price - position.entry_price
                if position.direction == Direction.LONG
                else position.entry_price - current_price
            )
            if price_moved >= position.initial_risk * config.BREAKEVEN_TRIGGER_R:
                position.current_stop = position.entry_price
                position.breakeven_hit = True
                logger.info("[%s] Moved stop to BREAK-EVEN at %.2f",
                            position.symbol, position.entry_price)

        # ── Trailing stop ──
        if config.TRAILING_STOP_ENABLED and position.breakeven_hit:
            if position.direction == Direction.LONG:
                # Trail under the highest price minus initial risk
                new_stop = position.highest_since_entry - position.initial_risk
                if new_stop > position.current_stop:
                    position.current_stop = new_stop
                    logger.debug("[%s] Trailing stop raised to %.2f",
                                 position.symbol, new_stop)
            else:
                new_stop = position.lowest_since_entry + position.initial_risk
                if new_stop < position.current_stop:
                    position.current_stop = new_stop
                    logger.debug("[%s] Trailing stop lowered to %.2f",
                                 position.symbol, new_stop)

        # Update unrealized P&L
        if position.direction == Direction.LONG:
            position.pnl = (current_price - position.entry_price) * position.quantity
        else:
            position.pnl = (position.entry_price - current_price) * position.quantity

        return position
