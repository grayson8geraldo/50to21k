# 50-to-21K Reversal Trading Bot

Paper trading bot implementing a reversal strategy on US market ETFs (SPY, QQQ, GLD) with a $200 virtual starting balance.

## Strategy

Reversal strategy based on:
1. **S/R Zones** — 15-minute swing highs/lows for key support/resistance levels
2. **Unhealthy Moves** — Detects aggressive 3+ candle moves without pullback
3. **Trend Break** — Confirms local trend line has been broken
4. **Reversal Patterns** — Higher lows, double bottoms/tops on 1-minute chart
5. **Timing** — Focus on 9:45 and 10:00 EST reversal windows
6. **Risk Management** — Stop loss, 1:2+ R:R, break-even at 1R, trailing stops

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Start paper trading (real-time during market hours)
python main.py

# Run backtest on recent historical data
python main.py --backtest

# View trade history and status
python main.py --status
```

## Architecture

```
main.py              — Bot runner and CLI
config.py            — All configurable parameters
bot/
  data_fetcher.py    — Real market data via yfinance
  analysis.py        — S/R zones, trend breaks, patterns
  strategy.py        — Entry checklist engine
  risk_manager.py    — Position sizing, stops, trailing
  paper_trader.py    — Virtual balance order execution
  trade_logger.py    — SQLite trade history
```

## Configuration

Edit `config.py` to adjust:
- Starting balance, risk per trade, max positions
- S/R detection sensitivity
- Unhealthy move thresholds
- Timing windows
- Risk/reward requirements
