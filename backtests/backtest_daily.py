import json
from datetime import datetime
from math import log, sqrt
from scipy.stats import norm

# Load data
btc_price = json.load(open("btc_price_daily.json"))
btc_iv = json.load(open("btc_dvol_daily.json"))

print(f"BTC daily prices: {len(btc_price)} points")
print(f"BTC daily IV: {len(btc_iv)} points")
print(f"Price range: {datetime.utcfromtimestamp(btc_price[0][0]/1000).strftime('%Y-%m-%d')} to {datetime.utcfromtimestamp(btc_price[-1][0]/1000).strftime('%Y-%m-%d')}")

# Build lookups
price_lookup = {p[0]: p[4] for p in btc_price}  # ts -> close
iv_lookup = {d[0]: d[4] / 100 for d in btc_iv}  # ts -> IV decimal

# Get aligned timestamps
price_times = set(price_lookup.keys())
iv_times = set(iv_lookup.keys())
common_times = sorted(price_times & iv_times)
print(f"Common data points: {len(common_times)}")

# Strategy params
ENTRY_DTE = 50
MIN_DTE = 21  # Force close by this DTE
EXIT_PROFIT_PCT = 0.30
EXIT_DELTA = 0.40
SHORT_DELTA = 0.17
SPREAD_WIDTH = 1000
SLIP = 0.04

# BS delta
def bs_delta(S, K, T, sigma, opt_type):
    if T <= 0:
        if opt_type == 'put':
            return -1.0 if S < K else 0.0
        return 1.0 if S > K else 0.0
    r = 0.05
    d1 = (log(S/K) + (r + sigma**2/2)*T) / (sigma*sqrt(T))
    return norm.cdf(d1) if opt_type == 'call' else norm.cdf(d1) - 1

# Find strike at target delta
def find_strike(spot, dte, iv, target_delta, opt_type):
    T = dte / 365
    if opt_type == 'put':
        low, high = spot * 0.5, spot * 0.98
    else:
        low, high = spot * 1.02, spot * 1.5
    
    for _ in range(50):
        mid = (low + high) / 2
        delta = abs(bs_delta(spot, mid, T, iv, opt_type))
        if abs(delta - target_delta) < 0.001:
            return round(mid / 1000) * 1000  # Round to nearest 1000
        if opt_type == 'put':
            if delta < target_delta:
                high = mid
            else:
                low = mid
        else:
            if delta < target_delta:
                low = mid
            else:
                high = mid
    return round(mid / 1000) * 1000

# Estimate credit from IV and DTE
def estimate_credit(iv, dte, spread_width):
    # Higher IV = higher credit, longer DTE = higher credit
    # Based on current data: ~$260 at 55% IV, 50 DTE
    base = 260
    iv_factor = iv / 0.55
    dte_factor = dte / 50
    return base * iv_factor * dte_factor * (1 - SLIP)

# Position tracker
class Position:
    def __init__(self, entry_ts, spot, iv, dte):
        self.entry_ts = entry_ts
        self.entry_spot = spot
        self.entry_iv = iv
        self.entry_dte = dte
        self.short_put = find_strike(spot, dte, iv, SHORT_DELTA, 'put')
        self.short_call = find_strike(spot, dte, iv, SHORT_DELTA, 'call')
        self.long_put = self.short_put - SPREAD_WIDTH
        self.long_call = self.short_call + SPREAD_WIDTH
        self.credit = estimate_credit(iv, dte, SPREAD_WIDTH)
        self.max_loss = SPREAD_WIDTH

# Run backtest
results = []
position = None
idx = 0

print("\nRunning daily backtest...")

while idx < len(common_times):
    ts = common_times[idx]
    spot = price_lookup[ts]
    iv = iv_lookup[ts]
    date = datetime.utcfromtimestamp(ts/1000).strftime('%Y-%m-%d')
    
    if position is None:
        # Enter new position
        position = Position(ts, spot, iv, ENTRY_DTE)
        entry_date = date
        days_held = 0
    else:
        days_held += 1
        current_dte = position.entry_dte - days_held
        T = max(current_dte / 365, 0.001)
        
        # Calculate current deltas
        put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
        call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
        
        # Theta decay estimate (credit erodes as time passes)
        theta_pct = days_held / position.entry_dte
        remaining_value = position.credit * (1 - theta_pct * 0.8)
        profit = position.credit - remaining_value
        profit_pct = profit / position.credit
        
        exit_type = None
        pnl = 0
        
        # Exit conditions
        if profit_pct >= EXIT_PROFIT_PCT:
            exit_type = 'profit_30pct'
            pnl = position.credit * EXIT_PROFIT_PCT
        elif put_delta > EXIT_DELTA:
            exit_type = 'put_delta_exit'
            # Loss depends on how close to breach
            intrinsic = max(position.short_put - spot, 0)
            pnl = -(position.credit + intrinsic * 0.5)  # Partial intrinsic loss
        elif call_delta > EXIT_DELTA:
            exit_type = 'call_delta_exit'
            intrinsic = max(spot - position.short_call, 0)
            pnl = -(position.credit + intrinsic * 0.5)
        elif current_dte <= MIN_DTE:
            # Check if ITM at expiry approach
            if spot < position.short_put:
                exit_type = 'put_itm'
                pnl = -min(position.short_put - spot, SPREAD_WIDTH)
            elif spot > position.short_call:
                exit_type = 'call_itm'
                pnl = -min(spot - position.short_call, SPREAD_WIDTH)
            else:
                exit_type = 'expire_profit'
                pnl = position.credit * 0.9  # Most of credit captured
        
        if exit_type:
            results.append({
                'entry': entry_date,
                'exit': date,
                'days': days_held,
                'exit_type': exit_type,
                'pnl': pnl,
                'entry_spot': position.entry_spot,
                'exit_spot': spot,
                'entry_iv': position.entry_iv,
                'short_put': position.short_put,
                'short_call': position.short_call
            })
            position = None
    
    idx += 1

# Summary
print(f"\n{'='*70}")
print("BACKTEST RESULTS - BTC Iron Condor (Daily, 2.7 Years)")
print(f"{'='*70}")

if not results:
    print("No trades completed!")
else:
    total = len(results)
    wins = [r for r in results if r['pnl'] > 0]
    losses = [r for r in results if r['pnl'] <= 0]
    
    print(f"\nTotal trades: {total}")
    print(f"Period: {results[0]['entry']} to {results[-1]['exit']}")
    print(f"\nWins: {len(wins)} ({len(wins)/total*100:.1f}%)")
    print(f"Losses: {len(losses)} ({len(losses)/total*100:.1f}%)")
    
    total_pnl = sum(r['pnl'] for r in results)
    avg_pnl = total_pnl / total
    print(f"\nTotal P&L: ${total_pnl:,.0f}")
    print(f"Avg P&L/trade: ${avg_pnl:,.0f}")
    
    if wins:
        avg_win = sum(r['pnl'] for r in wins) / len(wins)
        print(f"Avg win: ${avg_win:,.0f}")
    if losses:
        avg_loss = sum(r['pnl'] for r in losses) / len(losses)
        print(f"Avg loss: ${avg_loss:,.0f}")
    
    avg_days = sum(r['days'] for r in results) / total
    print(f"\nAvg days held: {avg_days:.1f}")
    
    # Exit breakdown
    print(f"\nExit type breakdown:")
    exit_counts = {}
    exit_pnl = {}
    for r in results:
        t = r['exit_type']
        exit_counts[t] = exit_counts.get(t, 0) + 1
        exit_pnl[t] = exit_pnl.get(t, 0) + r['pnl']
    
    for t in sorted(exit_counts.keys()):
        print(f"  {t:20s}: {exit_counts[t]:3d} trades ({exit_counts[t]/total*100:5.1f}%) | P&L: ${exit_pnl[t]:+,.0f}")
    
    # Yearly breakdown
    print(f"\nYearly breakdown:")
    yearly = {}
    for r in results:
        year = r['entry'][:4]
        if year not in yearly:
            yearly[year] = {'trades': 0, 'pnl': 0, 'wins': 0}
        yearly[year]['trades'] += 1
        yearly[year]['pnl'] += r['pnl']
        if r['pnl'] > 0:
            yearly[year]['wins'] += 1
    
    for year in sorted(yearly.keys()):
        y = yearly[year]
        wr = y['wins'] / y['trades'] * 100 if y['trades'] > 0 else 0
        print(f"  {year}: {y['trades']:2d} trades | {wr:5.1f}% WR | ${y['pnl']:+,.0f}")
    
    # Show all trades
    print(f"\nAll {total} trades:")
    for r in results:
        move = (r['exit_spot'] - r['entry_spot']) / r['entry_spot'] * 100
        print(f"  {r['entry']} -> {r['exit']} ({r['days']:2d}d): {r['exit_type']:18s} | ${r['pnl']:+7,.0f} | BTC ${r['entry_spot']:,.0f}->${r['exit_spot']:,.0f} ({move:+.1f}%)")
