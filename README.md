# BTC Iron Condor Backtest

Backtests of a short iron condor on BTC options, run on real hourly BTC prices from 2023-06-27 to 2026-03-22. The goal was to find out whether simple exit rules (a profit target, a delta-based stop, a cooldown after losses) make the strategy worth trading.

An iron condor is an options trading strategy. It sells a put below the price and a call above it, and buys one further out on each side as protection. It collects a credit up front and keeps it if the price stays between the two sold strikes.

## Demo

**Live demo:** https://stevenstoyanov12-aimbot.github.io/btc-condor-backtest/demo/

`demo/index.html` is an interactive version of the main backtest. Open it in a browser, change the exit rules and it reruns the whole backtest on the same data, with the price chart, every trade and the cumulative P&L.

## How the backtest works

- **Entry:** a 50-day condor at the 12-delta strikes with $1,000 wings, opened whenever no position is on.
- **Pricing:** Black-Scholes, repriced every hour. I had no historical implied volatility data for the options, so implied volatility is calculated from the daily DVOL index.
- **Costs:** a 4% haircut on entry and exit.
- **Exits:** a profit target (share of the credit), a delta exit when a sold strike's delta passes a threshold, or a time exit at 21 days to expiry.
- **Cooldown:** an optional wait after a losing trade.

## What I found

The win rate is high, but the losses are several times larger than the wins, so most rule sets end near flat or negative. On the 207 rule sets in the main grid:

- The best one (no delta exit, 90% profit target, 14-day cooldown) made about $870 per 1 BTC of size over 31 trades, with an 80.6% win rate.
- A typical middle setting (0.50 delta exit, 25% profit target, 7-day cooldown) lost about $244 over 70 trades.

The best rule set was chosen on the same data it was tested on, so it is a ceiling, not a forecast. Prices also come from a model with one volatility number per day, not from real option quotes.

## Files

| File | What it does |
|---|---|
| `backtest_90wr.py` | Main grid search: delta exit, profit target and cooldown, with and without the delta exit |
| `backtest_gamma.py`, `backtest_25_gamma.py`, `backtest_raw_gamma.py`, `gamma_debug.py` | Gamma-based exit experiments |
| `backtest_wide_delta.py`, `backtest_fixed.py`, `backtest_realistic.py`, `backtest_no_cooldown.py`, `backtest_bs.py`, `backtest_hourly.py` | Earlier variants of the entry and exit rules |
| `backtest_daily.py` | Daily-bar version. Needs `btc_price_daily.json`, which is not included |
| `strangle_backtest_delta.py` | Short strangle with delta-based exits |
| `delta_damage_analysis.py` | How much a position loses at each delta level |
| `btc_hourly_full.json` | Hourly BTC prices |
| `btc_dvol_daily.json` | Daily DVOL index |

## Run it

```bash
pip install -r requirements.txt
python backtest_90wr.py
```

The scripts read the two JSON files from the current folder. The full grid uses SciPy's normal distribution on every hour and can take a long time to finish.
