"""
Configuration for the 50-to-21K Reversal Trading Bot.
"""

# ── Paper Trading ──────────────────────────────────────────────
INITIAL_BALANCE = 200.0          # Starting virtual balance in USD
MAX_RISK_PER_TRADE_PCT = 2.0     # Max risk per trade as % of balance
MAX_OPEN_POSITIONS = 2           # Max simultaneous positions

# ── Instruments ────────────────────────────────────────────────
# Using ETF proxies for free real-time-ish data via yfinance
# SPY = S&P500, QQQ = NASDAQ-100, GLD = Gold
SYMBOLS = {
    "SPY": {"name": "S&P 500 (SPY)", "tick_size": 0.01, "contract_size": 1},
    "QQQ": {"name": "NASDAQ-100 (QQQ)", "tick_size": 0.01, "contract_size": 1},
    "GLD": {"name": "Gold (GLD)", "tick_size": 0.01, "contract_size": 1},
}

# ── Timeframes ─────────────────────────────────────────────────
TF_STRUCTURE = "15m"   # For support/resistance zones
TF_ENTRY = "1m"        # For entry signals

# ── Strategy Parameters ───────────────────────────────────────
# Support/Resistance detection
SR_LOOKBACK_BARS = 50            # Bars to look back for swing highs/lows on 15m
SR_SWING_WINDOW = 3              # Window for local extrema detection
SR_ZONE_TOLERANCE_PCT = 0.15     # % tolerance for S/R zone proximity

# Unhealthy move detection
UNHEALTHY_MIN_CANDLES = 3        # Min consecutive same-direction candles
UNHEALTHY_BODY_RATIO = 0.6      # Min body-to-range ratio per candle
UNHEALTHY_AVG_BODY_MULTIPLIER = 1.5  # Body must be N× average body size

# Trend break detection
TRENDLINE_MIN_TOUCHES = 2       # Min touches to form a trendline
TRENDLINE_LOOKBACK = 20         # Bars to look back for trendline

# Reversal pattern detection
PATTERN_LOOKBACK = 15            # Bars to look back for reversal patterns
HIGHER_LOW_TOLERANCE_PCT = 0.05  # % tolerance for higher-low confirmation

# ── Timing ─────────────────────────────────────────────────────
# US market open is 9:30 EST; key reversal times
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
REVERSAL_WINDOWS_MINUTES = [15, 30]  # Minutes after open (→ 9:45, 10:00 EST)
REVERSAL_WINDOW_TOLERANCE = 5        # ±minutes around target time

# ── Risk Management ───────────────────────────────────────────
RISK_REWARD_MIN = 2.0            # Minimum R:R ratio to take a trade
BREAKEVEN_TRIGGER_R = 1.0        # Move stop to BE after price moves this × risk
TRAILING_STOP_ENABLED = True     # Trail stop under each new higher low

# ── Data ───────────────────────────────────────────────────────
DATA_HISTORY_DAYS_15M = 5        # Days of 15m data to load
DATA_HISTORY_DAYS_1M = 2         # Days of 1m data to load
DATA_REFRESH_SECONDS = 60        # How often to refresh market data

# ── Logging ────────────────────────────────────────────────────
DB_PATH = "data/trades.db"
LOG_LEVEL = "INFO"
