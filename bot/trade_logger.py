"""
Trade logger — stores all trades in SQLite for analysis.
"""

import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

import pytz

import config
from bot.risk_manager import Position

logger = logging.getLogger(__name__)
EST = pytz.timezone("US/Eastern")


class TradeLogger:
    """Logs trades to SQLite database."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
        os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    pnl REAL DEFAULT 0,
                    status TEXT DEFAULT 'open',
                    pattern TEXT,
                    confidence REAL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    risk_dollars REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    balance REAL NOT NULL,
                    event TEXT
                )
            """)

    def log_entry(self, position: Position, pattern: str, confidence: float):
        now = datetime.now(EST).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO trades
                   (symbol, direction, entry_price, stop_loss, take_profit,
                    quantity, status, pattern, confidence, entry_time, risk_dollars)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
                (
                    position.symbol,
                    position.direction.value,
                    position.entry_price,
                    position.stop_loss,
                    position.take_profit,
                    position.quantity,
                    pattern,
                    confidence,
                    now,
                    position.risk_dollars,
                ),
            )
        logger.debug("Trade entry logged: %s %s", position.direction.value, position.symbol)

    def log_exit(self, position: Position):
        now = datetime.now(EST).isoformat()
        exit_price = position.current_stop if "sl" in position.status else position.take_profit

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE trades SET
                   exit_price = ?, pnl = ?, status = ?, exit_time = ?
                   WHERE symbol = ? AND status = 'open'
                   ORDER BY id DESC LIMIT 1""",
                (exit_price, position.pnl, position.status, now, position.symbol),
            )
        logger.debug("Trade exit logged: %s %s P&L=%.2f",
                      position.direction.value, position.symbol, position.pnl)

    def log_balance(self, balance: float, event: str = "update"):
        now = datetime.now(EST).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO balance_history (timestamp, balance, event) VALUES (?, ?, ?)",
                (now, balance, event),
            )

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_balance_history(self, limit: int = 100) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM balance_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
