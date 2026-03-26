"""
Paper trading engine.
Simulates order execution with virtual balance using real market data.
"""

import logging
from datetime import datetime
from typing import Optional

import pytz

import config
from bot.analysis import Direction
from bot.risk_manager import Position, RiskManager
from bot.strategy import TradeSignal
from bot.trade_logger import TradeLogger

logger = logging.getLogger(__name__)
EST = pytz.timezone("US/Eastern")


class PaperTrader:
    """Paper trading engine with virtual balance."""

    def __init__(self, initial_balance: float = None):
        self.initial_balance = initial_balance or config.INITIAL_BALANCE
        self.balance = self.initial_balance
        self.positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self.risk_manager = RiskManager(self.balance)
        self.trade_logger = TradeLogger()
        self.pending_orders: list[dict] = []  # Buy/sell stop orders

    def place_stop_order(self, signal: TradeSignal) -> Optional[dict]:
        """
        Place a buy-stop or sell-stop order (not a market order).
        Order triggers when price reaches entry_price.
        """
        if len(self.positions) >= config.MAX_OPEN_POSITIONS:
            logger.warning("Max open positions (%d) reached", config.MAX_OPEN_POSITIONS)
            return None

        # Check if we already have a position in this symbol
        for pos in self.positions:
            if pos.symbol == signal.symbol and pos.status == "open":
                logger.warning("Already have open position in %s", signal.symbol)
                return None

        quantity = self.risk_manager.calculate_position_size(
            signal.entry_price, signal.stop_loss
        )
        if quantity <= 0:
            logger.warning("Position size is 0 — insufficient balance")
            return None

        order = {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "quantity": quantity,
            "risk_per_share": signal.risk_amount,
            "pattern": signal.pattern,
            "confidence": signal.confidence,
            "placed_at": datetime.now(EST),
            "status": "pending",
        }
        self.pending_orders.append(order)

        logger.info(
            "📋 STOP ORDER placed: %s %s %d shares @ %.2f | SL: %.2f | TP: %.2f",
            signal.direction.value.upper(), signal.symbol, quantity,
            signal.entry_price, signal.stop_loss, signal.take_profit,
        )
        return order

    def check_pending_orders(self, symbol: str, current_price: float, current_high: float, current_low: float):
        """Check if any pending stop orders should be triggered."""
        triggered = []
        for order in self.pending_orders:
            if order["symbol"] != symbol or order["status"] != "pending":
                continue

            # Buy stop: triggers when price goes above entry
            if order["direction"] == Direction.LONG and current_high >= order["entry_price"]:
                self._execute_order(order)
                triggered.append(order)

            # Sell stop: triggers when price goes below entry
            elif order["direction"] == Direction.SHORT and current_low <= order["entry_price"]:
                self._execute_order(order)
                triggered.append(order)

        # Clean up triggered orders
        for order in triggered:
            order["status"] = "filled"

        # Cancel stale orders (older than 30 minutes)
        now = datetime.now(EST)
        for order in self.pending_orders:
            if order["status"] == "pending":
                age = (now - order["placed_at"]).total_seconds()
                if age > 1800:  # 30 minutes
                    order["status"] = "cancelled"
                    logger.info("📋 Order cancelled (stale): %s %s",
                                order["direction"].value, order["symbol"])

        self.pending_orders = [o for o in self.pending_orders if o["status"] == "pending"]

    def _execute_order(self, order: dict):
        """Execute a triggered stop order."""
        position = Position(
            symbol=order["symbol"],
            direction=order["direction"],
            entry_price=order["entry_price"],
            quantity=order["quantity"],
            stop_loss=order["stop_loss"],
            take_profit=order["take_profit"],
            initial_risk=order["risk_per_share"],
            current_stop=order["stop_loss"],
            highest_since_entry=order["entry_price"],
            lowest_since_entry=order["entry_price"],
        )
        self.positions.append(position)

        cost = position.entry_price * position.quantity
        self.balance -= cost

        self.trade_logger.log_entry(position, order["pattern"], order["confidence"])

        logger.info(
            "🟢 POSITION OPENED: %s %s %d @ %.2f | Risk: $%.2f | Balance: $%.2f",
            position.direction.value.upper(), position.symbol,
            position.quantity, position.entry_price,
            position.risk_dollars, self.balance,
        )

    def update_positions(self, symbol: str, current_price: float):
        """Update all open positions with current price."""
        for position in self.positions:
            if position.symbol != symbol or position.status != "open":
                continue

            old_status = position.status
            self.risk_manager.update_position(position, current_price)

            if position.status != "open" and old_status == "open":
                # Position was closed
                self.balance += position.entry_price * position.quantity + position.pnl
                self.risk_manager.balance = self.balance
                self.closed_positions.append(position)
                self.trade_logger.log_exit(position)

                emoji = "🟢" if position.pnl >= 0 else "🔴"
                logger.info(
                    "%s POSITION CLOSED (%s): %s %s | P&L: $%.2f | Balance: $%.2f",
                    emoji, position.status, position.direction.value.upper(),
                    position.symbol, position.pnl, self.balance,
                )

        # Remove closed positions from active list
        self.positions = [p for p in self.positions if p.status == "open"]

    def get_stats(self) -> dict:
        """Get current trading statistics."""
        total_trades = len(self.closed_positions)
        if total_trades == 0:
            return {
                "balance": self.balance,
                "initial_balance": self.initial_balance,
                "total_return_pct": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "open_positions": len(self.positions),
                "pending_orders": len(self.pending_orders),
            }

        wins = [p for p in self.closed_positions if p.pnl > 0]
        losses = [p for p in self.closed_positions if p.pnl <= 0]
        total_pnl = sum(p.pnl for p in self.closed_positions)
        unrealized = sum(p.pnl for p in self.positions)

        return {
            "balance": self.balance,
            "initial_balance": self.initial_balance,
            "total_return_pct": (self.balance - self.initial_balance) / self.initial_balance * 100,
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / total_trades * 100 if total_trades > 0 else 0,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / total_trades,
            "best_trade": max(p.pnl for p in self.closed_positions) if self.closed_positions else 0,
            "worst_trade": min(p.pnl for p in self.closed_positions) if self.closed_positions else 0,
            "open_positions": len(self.positions),
            "pending_orders": len(self.pending_orders),
            "unrealized_pnl": unrealized,
        }
