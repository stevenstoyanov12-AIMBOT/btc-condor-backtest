import json
from datetime import datetime
from math import log, sqrt
from scipy.stats import norm

# Load data
btc_hourly = json.load(open("btc_hourly_full.json"))
btc_iv_daily = json.load(open("btc_dvol_daily.json"))

print(f"BTC hourly prices: {len(btc_hourly)} points")
print(f"BTC daily IV: {len(btc_iv_daily)} points")

# Build IV lookup (daily IV applied to all hours of that day)
iv_lookup = {}
for d in btc_iv_daily:
    ts = d[0]
    iv = d[4] / 100  # DVOL to decimal
    day_start = (ts // (24*60*60*1000)) * (24*60*60*1000)
    for h in range(24):
        iv_lookup[day_start + h*60*60*1000] = iv

# Build price lookup
price_data = []
for p in btc_hourly:
    ts = p['ts']
    if ts in iv_lookup:
        price_data.append({'ts': ts, 'close': p['close'], 'iv': iv_lookup[ts]})

print(f"Aligned hourly data: {len(price_data)} points")
if price_data:
    print(f"Range: {datetime.utcfromtimestamp(price_data[0]['ts']/1000).strftime('%Y-%m-%d')} to {datetime.utcfromtimestamp(price_data[-1]['ts']/1000).strftime('%Y-%m-%d')}")

# Strategy params
ENTRY_DTE = 50
MIN_DTE = 21
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
            return round(mid / 1000) * 1000
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

def estimate_credit(iv, dte):
    base = 260  # $260 at 55% IV, 50 DTE
    return base * (iv / 0.55) * (dte / 50) * (1 - SLIP)

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
        self.credit = estimate_credit(iv, dte)
        self.max_loss = SPREAD_WIDTH

# Run backtest
results = []
position = None
idx = 0

print("\nRunning hourly backtest...")

while idx < len(price_data):
    d = price_data[idx]
    ts = d['ts']
    spot = d['close']
    iv = d['iv']
    dt = datetime.utcfromtimestamp(ts/1000)
    date_str = dt.strftime('%Y-%m-%d %H:%M')
    
    if position is None:
        position = Position(ts, spot, iv, ENTRY_DTE)
        entry_date = date_str
        entry_ts = ts
    else:
        hours_held = (ts - position.entry_ts) / (1000 * 60 * 60)
        current_dte = position.entry_dte - (hours_held / 24)
        T = max(current_dte / 365, 0.001)
        
        put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
        call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
        
        # Theta decay
        theta_pct = (hours_held / 24) / position.entry_dte
        remaining_value = position.credit * (1 - theta_pct * 0.8)
        profit_pct = (position.credit - remaining_value) / position.credit
        
        exit_type = None
        pnl = 0
        
        if profit_pct >= EXIT_PROFIT_PCT:
            exit_type = 'profit_30pct'
            pnl = position.credit * EXIT_PROFIT_PCT
        elif put_delta > EXIT_DELTA:
            exit_type = 'put_delta_exit'
            intrinsic = max(position.short_put - spot, 0)
            pnl = -(position.credit + intrinsic * 0.5)
        elif call_delta > EXIT_DELTA:
            exit_type = 'call_delta_exit'
            intrinsic = max(spot - position.short_call, 0)
            pnl = -(position.credit + intrinsic * 0.5)
        elif current_dte <= MIN_DTE:
            if spot < position.short_put:
                exit_type = 'put_itm'
                pnl = -min(position.short_put - spot, SPREAD_WIDTH)
            elif spot > position.short_call:
                exit_type = 'call_itm'
                pnl = -min(spot - position.short_call, SPREAD_WIDTH)
            else:
                exit_type = 'expire_profit'
                pnl = position.credit * 0.9
        
        if exit_type:
            results.append({
                'entry': entry_date,
                'exit': date_str,
                'hours': hours_held,
                'days': hours_held / 24,
                'exit_type': exit_type,
                'pnl': pnl,
                'entry_spot': position.entry_spot,
                'exit_spot': spot,
                'entry_iv': position.entry_iv,
                'exit_iv': iv,
                'short_put': position.short_put,
                'short_call': position.short_call,
                'exit_put_delta': put_delta,
                'exit_call_delta': call_delta
            })
            position = None
    
    idx += 1

# Summary
print(f"\n{'='*70}")
print("BACKTEST RESULTS - BTC Iron Condor (Hourly Granularity)")
print(f"{'='*70}")

if not results:
    print("No trades completed!")
else:
    total = len(results)
    wins = [r for r in results if r['pnl'] > 0]
    losses = [r for r in results if r['pnl'] <= 0]
    
    print(f"\nTotal trades: {total}")
    print(f"Period: {results[0]['entry'][:10]} to {results[-1]['exit'][:10]}")
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
    
    # Delta exit analysis
    delta_exits = [r for r in results if 'delta_exit' in r['exit_type']]
    if delta_exits:
        print(f"\nDelta exit analysis ({len(delta_exits)} trades):")
        put_exits = [r for r in delta_exits if 'put' in r['exit_type']]
        call_exits = [r for r in delta_exits if 'call' in r['exit_type']]
        print(f"  Put-side exits: {len(put_exits)}")
        print(f"  Call-side exits: {len(call_exits)}")
        avg_delta_loss = sum(r['pnl'] for r in delta_exits) / len(delta_exits)
        print(f"  Avg loss on delta exit: ${avg_delta_loss:,.0f}")
    
    # Monthly breakdown
    print(f"\nMonthly breakdown:")
    monthly = {}
    for r in results:
        month = r['entry'][:7]
        if month not in monthly:
            monthly[month] = {'trades': 0, 'pnl': 0, 'wins': 0}
        monthly[month]['trades'] += 1
        monthly[month]['pnl'] += r['pnl']
        if r['pnl'] > 0:
            monthly[month]['wins'] += 1
    
    for month in sorted(monthly.keys()):
        m = monthly[month]
        wr = m['wins'] / m['trades'] * 100 if m['trades'] > 0 else 0
        print(f"  {month}: {m['trades']:2d} trades | {wr:5.1f}% WR | ${m['pnl']:+,.0f}")
    
    # Show all trades with detail
    print(f"\nAll {total} trades:")
    for r in results:
        move = (r['exit_spot'] - r['entry_spot']) / r['entry_spot'] * 100
        print(f"  {r['entry'][:10]} -> {r['exit'][:10]} ({r['days']:4.1f}d): {r['exit_type']:18s} | ${r['pnl']:+7,.0f} | BTC ${r['entry_spot']:,.0f}->${r['exit_spot']:,.0f} ({move:+5.1f}%) | IV {r['entry_iv']*100:.0f}%->{r['exit_iv']*100:.0f}%")
