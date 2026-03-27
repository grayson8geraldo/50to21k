#!/usr/bin/env python3
"""
50-to-21K Reversal Trading Bot — Paper Trading Mode

A reversal strategy bot that trades SPY, QQQ, and GLD using:
- 15m S/R zones for context
- 1m entries with trend break + reversal pattern confirmation
- Timing focus on 9:45/10:00 EST windows
- Full risk management: stop loss, break-even, trailing stops

Usage:
    python main.py              # Start paper trading bot
    python main.py --status     # Show current status
    python main.py --history    # Show trade history
    python main.py --backtest   # Run on recent historical data
"""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime

import pytz

import config

os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)
from bot.data_fetcher import DataFetcher
from bot.paper_trader import PaperTrader
from bot.strategy import StrategyEngine

EST = pytz.timezone("US/Eastern")

# ── Logging setup ──────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", mode="a"),
    ],
)
logger = logging.getLogger("main")


class TradingBot:
    """Main bot orchestrator."""

    def __init__(self):
        self.data_fetcher = DataFetcher()
        self.strategy = StrategyEngine()
        self.trader = PaperTrader(config.INITIAL_BALANCE)
        self.running = False

    def run(self):
        """Main trading loop."""
        self.running = True
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        logger.info("=" * 60)
        logger.info("  50-to-21K Reversal Bot — PAPER TRADING MODE")
        logger.info("  Starting balance: $%.2f", config.INITIAL_BALANCE)
        logger.info("  Symbols: %s", ", ".join(config.SYMBOLS.keys()))
        logger.info("  Strategy: Reversal at S/R zones")
        logger.info("=" * 60)

        self.trader.trade_logger.log_balance(config.INITIAL_BALANCE, "start")

        # Initial data load
        logger.info("Loading market data...")
        self.data_fetcher.refresh_all()

        cycle = 0
        market_closed_logged = False
        while self.running:
            try:
                now = datetime.now(EST)

                # Check if market is open (weekday, 9:30-16:00 EST)
                if not self._is_market_hours(now):
                    if not market_closed_logged:
                        next_open = self._next_market_open(now)
                        logger.info("Market closed. Next open: %s EST", next_open.strftime("%a %b %d %H:%M"))
                        self._print_status()
                        market_closed_logged = True
                    time.sleep(300)  # Check every 5 min when market is closed
                    continue

                # Market is open
                if market_closed_logged:
                    logger.info("Market is OPEN. Starting to scan for signals...")
                    market_closed_logged = False

                # Refresh data periodically
                if self.data_fetcher.needs_refresh():
                    self.data_fetcher.refresh_all()

                # Process each symbol
                for symbol in config.SYMBOLS:
                    self._process_symbol(symbol)

                # Log periodic status
                cycle += 1
                if cycle % 5 == 0:
                    self._print_status()
                    self.trader.trade_logger.log_balance(self.trader.balance, "periodic")

                time.sleep(config.DATA_REFRESH_SECONDS)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Error in main loop: %s", e, exc_info=True)
                time.sleep(10)

        self._shutdown()

    def _process_symbol(self, symbol: str):
        """Process one symbol: check orders, update positions, evaluate strategy."""
        df_1m = self.data_fetcher.get_cached_1m(symbol)
        df_15m = self.data_fetcher.get_cached_15m(symbol)

        if df_1m.empty:
            return

        current_price = float(df_1m["close"].iloc[-1])
        current_high = float(df_1m["high"].iloc[-1])
        current_low = float(df_1m["low"].iloc[-1])

        # 1. Check pending stop orders
        self.trader.check_pending_orders(symbol, current_price, current_high, current_low)

        # 2. Update open positions
        self.trader.update_positions(symbol, current_price)

        # 3. Evaluate strategy for new signals
        day_open = self.data_fetcher.get_day_open(symbol)
        signal = self.strategy.evaluate(symbol, df_15m, df_1m, day_open)

        if signal:
            logger.info("=" * 40)
            logger.info("SIGNAL: %s %s @ %.2f", signal.direction.value.upper(),
                        symbol, signal.entry_price)
            logger.info("  Pattern: %s | Confidence: %.0f%%",
                        signal.pattern, signal.confidence * 100)
            logger.info("  SL: %.2f | TP: %.2f | R:R: %.1f",
                        signal.stop_loss, signal.take_profit,
                        abs(signal.take_profit - signal.entry_price) / signal.risk_amount)
            logger.info("=" * 40)
            self.trader.place_stop_order(signal)

    def _print_status(self):
        """Print current bot status."""
        stats = self.trader.get_stats()
        logger.info("-" * 50)
        logger.info("  PORTFOLIO STATUS")
        logger.info("  Balance:     $%.2f (%.1f%%)",
                     stats["balance"], stats["total_return_pct"])
        logger.info("  Total P&L:   $%.2f", stats["total_pnl"])
        logger.info("  Trades:      %d (W: %d / L: %d | WR: %.0f%%)",
                     stats["total_trades"], stats["winning_trades"],
                     stats["losing_trades"], stats["win_rate"])
        logger.info("  Open:        %d positions, %d pending orders",
                     stats["open_positions"], stats["pending_orders"])
        if stats.get("unrealized_pnl", 0) != 0:
            logger.info("  Unrealized:  $%.2f", stats["unrealized_pnl"])
        logger.info("-" * 50)

    def backtest(self):
        """Run strategy on historical data to validate signals."""
        logger.info("=" * 60)
        logger.info("  BACKTEST MODE — Running on historical data")
        logger.info("=" * 60)

        self.data_fetcher.refresh_all()

        for symbol in config.SYMBOLS:
            logger.info("\n--- Backtesting %s ---", symbol)
            df_1m = self.data_fetcher.get_cached_1m(symbol)
            df_15m = self.data_fetcher.get_cached_15m(symbol)

            if df_1m.empty or df_15m.empty:
                logger.warning("No data for %s, skipping", symbol)
                continue

            day_open = float(df_1m["open"].iloc[0]) if not df_1m.empty else None

            # Simulate bar-by-bar
            window = 60  # Need at least 60 bars of history
            signals_found = 0

            for i in range(window, len(df_1m)):
                slice_1m = df_1m.iloc[:i + 1]
                current_price = float(slice_1m["close"].iloc[-1])
                current_high = float(slice_1m["high"].iloc[-1])
                current_low = float(slice_1m["low"].iloc[-1])

                # Check pending orders
                self.trader.check_pending_orders(symbol, current_price, current_high, current_low)

                # Update positions
                self.trader.update_positions(symbol, current_price)

                # Evaluate strategy
                sig = self.strategy.evaluate(symbol, df_15m, slice_1m, day_open)
                if sig:
                    signals_found += 1
                    self.trader.place_stop_order(sig)

            logger.info("[%s] Found %d signals in %d bars", symbol, signals_found, len(df_1m))

        self._print_status()
        logger.info("\nBacktest complete.")

    @staticmethod
    def _is_market_hours(now: datetime) -> bool:
        """Check if within US market hours."""
        if now.weekday() >= 5:  # Saturday/Sunday
            return False
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now <= market_close

    @staticmethod
    def _next_market_open(now: datetime) -> datetime:
        """Calculate when the market next opens."""
        from datetime import timedelta
        next_day = now
        while True:
            if next_day.date() == now.date() and now.hour < 9:
                # Today before open
                return next_day.replace(hour=9, minute=30, second=0, microsecond=0)
            next_day = (next_day + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
            if next_day.weekday() < 5:  # Mon-Fri
                return next_day

    def _shutdown(self, *args):
        """Graceful shutdown."""
        self.running = False
        logger.info("\nShutting down...")
        self._print_status()

        # Show trade history
        history = self.trader.trade_logger.get_trade_history(10)
        if history:
            logger.info("\nRecent trades:")
            for t in history:
                pnl_str = f"${t['pnl']:.2f}" if t['pnl'] else "open"
                logger.info("  %s %s %s @ %.2f → %s (%s)",
                            t['direction'].upper(), t['symbol'],
                            t['pattern'] or "", t['entry_price'],
                            pnl_str, t['status'])

        self.trader.trade_logger.log_balance(self.trader.balance, "shutdown")
        logger.info("Bot stopped. Final balance: $%.2f", self.trader.balance)


def show_status():
    """Show current bot status from database."""
    from bot.trade_logger import TradeLogger
    tl = TradeLogger()
    history = tl.get_trade_history(20)
    balances = tl.get_balance_history(5)

    print("\n--- Trade History ---")
    if not history:
        print("No trades yet.")
    for t in history:
        pnl_str = f"${t['pnl']:.2f}" if t['pnl'] else "open"
        print(f"  {t['direction'].upper()} {t['symbol']} | "
              f"{t['pattern']} | Entry: {t['entry_price']:.2f} | "
              f"P&L: {pnl_str} | {t['status']}")

    print("\n--- Balance History ---")
    for b in balances:
        print(f"  {b['timestamp']} | ${b['balance']:.2f} | {b['event']}")


def main():
    parser = argparse.ArgumentParser(
        description="50-to-21K Reversal Trading Bot — Paper Trading Mode"
    )
    parser.add_argument("--status", action="store_true",
                        help="Show current trading status")
    parser.add_argument("--history", action="store_true",
                        help="Show trade history")
    parser.add_argument("--backtest", action="store_true",
                        help="Run on recent historical data")
    args = parser.parse_args()

    if args.status or args.history:
        show_status()
        return

    bot = TradingBot()

    if args.backtest:
        bot.backtest()
    else:
        bot.run()


if __name__ == "__main__":
    main()
