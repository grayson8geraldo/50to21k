"""
Market data fetcher using yfinance.
Provides real-time and historical OHLCV data for paper trading.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import pytz
import yfinance as yf

import config

logger = logging.getLogger(__name__)

EST = pytz.timezone("US/Eastern")


class DataFetcher:
    """Fetches real market data via yfinance for paper trading."""

    def __init__(self):
        self._cache_15m: dict[str, pd.DataFrame] = {}
        self._cache_1m: dict[str, pd.DataFrame] = {}
        self._last_refresh: Optional[datetime] = None
        self._consecutive_errors = 0

    def _fetch_with_retry(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        """Fetch data with retry and error handling for yfinance rate limits."""
        for attempt in range(3):
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period=period, interval=interval)
                self._consecutive_errors = 0
                return df
            except Exception as e:
                wait = 2 ** (attempt + 1)
                logger.warning("yfinance error for %s (%s): %s. Retry in %ds...",
                               symbol, interval, str(e)[:80], wait)
                time.sleep(wait)
        # All retries failed — return cached data if available
        logger.error("Failed to fetch %s %s after 3 retries", symbol, interval)
        self._consecutive_errors += 1
        return pd.DataFrame()

    def fetch_15m(self, symbol: str) -> pd.DataFrame:
        """Fetch 15-minute candles for S/R zone analysis."""
        df = self._fetch_with_retry(symbol, f"{config.DATA_HISTORY_DAYS_15M}d", "15m")
        if df.empty:
            cached = self._cache_15m.get(symbol, pd.DataFrame())
            if not cached.empty:
                logger.info("Using cached 15m data for %s (%d bars)", symbol, len(cached))
            return cached

        df = self._normalize(df)
        self._cache_15m[symbol] = df
        logger.info("Fetched %d 15m bars for %s", len(df), symbol)
        return df

    def fetch_1m(self, symbol: str) -> pd.DataFrame:
        """Fetch 1-minute candles for entry signal analysis."""
        df = self._fetch_with_retry(symbol, f"{config.DATA_HISTORY_DAYS_1M}d", "1m")
        if df.empty:
            cached = self._cache_1m.get(symbol, pd.DataFrame())
            if not cached.empty:
                logger.info("Using cached 1m data for %s (%d bars)", symbol, len(cached))
            return cached

        df = self._normalize(df)
        self._cache_1m[symbol] = df
        logger.info("Fetched %d 1m bars for %s", len(df), symbol)
        return df

    def get_cached_15m(self, symbol: str) -> pd.DataFrame:
        return self._cache_15m.get(symbol, pd.DataFrame())

    def get_cached_1m(self, symbol: str) -> pd.DataFrame:
        return self._cache_1m.get(symbol, pd.DataFrame())

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get latest price from cached 1m data."""
        df = self._cache_1m.get(symbol)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return None

    def get_day_open(self, symbol: str) -> Optional[float]:
        """Get today's opening price."""
        df = self._cache_1m.get(symbol)
        if df is None or df.empty:
            return None
        today = datetime.now(EST).date()
        today_bars = df[df.index.date == today]
        if today_bars.empty:
            return None
        return float(today_bars["open"].iloc[0])

    def refresh_all(self) -> dict[str, dict[str, pd.DataFrame]]:
        """Refresh data for all configured symbols."""
        # Back off if we've had many consecutive errors (rate limited)
        if self._consecutive_errors >= 5:
            backoff = min(300, 30 * self._consecutive_errors)
            logger.warning("Too many API errors (%d). Backing off %ds...",
                           self._consecutive_errors, backoff)
            time.sleep(backoff)

        result = {}
        for symbol in config.SYMBOLS:
            df_15m = self.fetch_15m(symbol)
            df_1m = self.fetch_1m(symbol)
            result[symbol] = {"15m": df_15m, "1m": df_1m}
            time.sleep(0.5)  # Small delay between symbols to avoid rate limit
        self._last_refresh = datetime.now(EST)
        return result

    def needs_refresh(self) -> bool:
        if self._last_refresh is None:
            return True
        elapsed = (datetime.now(EST) - self._last_refresh).total_seconds()
        return elapsed >= config.DATA_REFRESH_SECONDS

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names to lowercase."""
        df.columns = [c.lower() for c in df.columns]
        # Ensure timezone-aware index in EST
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(EST)
        else:
            df.index = df.index.tz_convert(EST)
        return df
