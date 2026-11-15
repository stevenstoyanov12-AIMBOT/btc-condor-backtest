import json
from datetime import datetime
from math import log, sqrt, exp, pi
from scipy.stats import norm

# Load data
btc_hourly = json.load(open("btc_hourly_full.json"))
btc_iv_daily = json.load(open("btc_dvol_daily.json"))

print(f"BTC hourly prices: {len(btc_hourly)} points")
print(f"BTC daily IV: {len(btc_iv_daily)} points")

# Build IV lookup
iv_lookup = {}
for d in btc_iv_daily:
    ts = d[0]
    iv = d[4] / 100
    day_start = (ts // (24*60*60*1000)) * (24*60*60*1000)
    for h in range(24):
        iv_lookup[day_start + h*60*60*1000] = iv

# Build price data
price_data = []
for p in btc_hourly:
    ts = p['ts']
    if ts in iv_lookup:
        price_data.append({'ts': ts, 'close': p['close'], 'iv': iv_lookup[ts]})

print(f"Aligned data: {len(price_data)} points")

# Greeks calculations
def bs_d1(S, K, T, r, sigma):
    return (log(S/K) + (r + sigma**2/2)*T) / (sigma*sqrt(T))

def bs_delta(S, K, T, sigma, opt_type):
    if T <= 0.001:
        if opt_type == 'call':
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0
    r = 0.05
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.cdf(d1) if opt_type == 'call' else norm.cdf(d1) - 1

def bs_gamma(S, K, T, sigma):
    """Gamma is same for calls and puts"""
    if T <= 0.001:
        return 0.0
    r = 0.05
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * sqrt(T))

def find_strike(spot, dte, iv, target_delta, opt_type):
    T = dte / 365
    if opt_type == 'put':
        low, high = spot * 0.5, spot * 0.98
        target = -target_delta
    else:
        low, high = spot * 1.02, spot * 2.0
        target = target_delta
    
    for _ in range(100):
        mid = (low + high) / 2
        delta = bs_delta(spot, mid, T, iv, opt_type)
        if abs(delta - target) < 0.001:
            return round(mid / 1000) * 1000
        if opt_type == 'put':
            if delta < target:
                high = mid
            else:
                low = mid
        else:
            if delta > target:
                low = mid
            else:
                high = mid
    return round(mid / 1000) * 1000

def estimate_credit(iv, dte):
    base = 260
    return base * (iv / 0.55) * sqrt(dte / 50) * 0.96

class Position:
    def __init__(self, entry_ts, spot, iv, dte, short_delta):
        self.entry_ts = entry_ts
        self.entry_spot = spot
        self.entry_iv = iv
        self.entry_dte = dte
        self.short_put = find_strike(spot, dte, iv, short_delta, 'put')
        self.short_call = find_strike(spot, dte, iv, short_delta, 'call')
        self.credit = estimate_credit(iv, dte)
        self.max_loss = 1000

def run_backtest(short_delta, exit_delta, gamma_threshold, cooldown_days):
    """
    Run backtest with given parameters.
    gamma_threshold: exit if position gamma > this value (in $ terms: gamma * spot^2)
    """
    results = []
    position = None
    last_loss_ts = 0
    cooldown_hours = cooldown_days * 24
    
    for d in price_data:
        ts = d['ts']
        spot = d['close']
        iv = d['iv']
        
        if position is None:
            hours_since_loss = (ts - last_loss_ts) / (1000 * 60 * 60)
            if hours_since_loss >= cooldown_hours:
                position = Position(ts, spot, iv, 50, short_delta)
                entry_ts = ts
        else:
            hours_held = (ts - position.entry_ts) / (1000 * 60 * 60)
            current_dte = 50 - (hours_held / 24)
            
            if current_dte <= 0.1:
                position = None
                continue
                
            T = current_dte / 365
            
            # Calculate Greeks
            put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
            call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
            put_gamma = bs_gamma(spot, position.short_put, T, iv)
            call_gamma = bs_gamma(spot, position.short_call, T, iv)
            
            # Position gamma in dollar terms (gamma * spot^2 / 100)
            # This represents $ P&L change per 1% move squared
            position_gamma = (put_gamma + call_gamma) * spot * spot / 10000
            
            # Estimate profit
            time_decay = (hours_held / 24) / 50
            delta_factor = max(put_delta, call_delta) / short_delta
            remaining = position.credit * (1 - time_decay * 0.8) * sqrt(delta_factor)
            profit_pct = (position.credit - remaining) / position.credit
            
            exit_type = None
            pnl = 0
            
            # Exit conditions
            if profit_pct >= 0.30:
                exit_type = 'profit'
                pnl = position.credit * 0.30
            elif gamma_threshold > 0 and position_gamma > gamma_threshold:
                exit_type = 'gamma_exit'
                pnl = -(remaining * 0.5)
                last_loss_ts = ts
            elif put_delta > exit_delta:
                exit_type = 'put_delta'
                intrinsic = max(position.short_put - spot, 0)
                pnl = -(remaining + intrinsic * 0.3)
                last_loss_ts = ts
            elif call_delta > exit_delta:
                exit_type = 'call_delta'
                intrinsic = max(spot - position.short_call, 0)
                pnl = -(remaining + intrinsic * 0.3)
                last_loss_ts = ts
            elif current_dte <= 21:
                if spot < position.short_put:
                    exit_type = 'put_itm'
                    pnl = -min(position.short_put - spot, 1000)
                    last_loss_ts = ts
                elif spot > position.short_call:
                    exit_type = 'call_itm'
                    pnl = -min(spot - position.short_call, 1000)
                    last_loss_ts = ts
                else:
                    exit_type = 'expire'
                    pnl = position.credit * 0.85
            
            if exit_type:
                results.append({
                    'pnl': pnl,
                    'exit_type': exit_type,
                    'days': hours_held / 24,
                    'gamma': position_gamma,
                    'put_delta': put_delta,
                    'call_delta': call_delta
                })
                position = None
    
    return results

# Test different gamma thresholds
print("\n" + "="*80)
print("GAMMA OPTIMIZATION")
print("="*80)

# First, let's see what gamma values look like
print("\nSample gamma values at different scenarios:")
for spot in [85000]:
    for strike_pct in [0.85, 0.90, 0.95]:  # % of spot
        strike = spot * strike_pct
        for dte in [50, 30, 14, 7]:
            T = dte / 365
            gamma = bs_gamma(spot, strike, T, 0.55)
            dollar_gamma = gamma * spot * spot / 10000
            print(f"  Spot ${spot:,}, Strike ${strike:,.0f} ({strike_pct*100:.0f}%), {dte} DTE: gamma={gamma:.8f}, $gamma={dollar_gamma:.1f}")

print("\n" + "-"*80)
print("Testing parameter combinations...")
print("-"*80)

best_pnl = float('-inf')
best_params = None
results_table = []

for short_delta in [0.15, 0.17, 0.20]:
    for exit_delta in [0.35, 0.40, 0.45]:
        for gamma_thresh in [0, 50, 100, 150, 200, 300]:  # 0 = no gamma exit
            for cooldown in [3, 7, 14]:
                results = run_backtest(short_delta, exit_delta, gamma_thresh, cooldown)
                
                if len(results) < 10:
                    continue
                
                total_pnl = sum(r['pnl'] for r in results)
                wins = len([r for r in results if r['pnl'] > 0])
                win_rate = wins / len(results) * 100
                avg_pnl = total_pnl / len(results)
                
                results_table.append({
                    'short_delta': short_delta,
                    'exit_delta': exit_delta,
                    'gamma_thresh': gamma_thresh,
                    'cooldown': cooldown,
                    'trades': len(results),
                    'win_rate': win_rate,
                    'total_pnl': total_pnl,
                    'avg_pnl': avg_pnl
                })
                
                if total_pnl > best_pnl:
                    best_pnl = total_pnl
                    best_params = (short_delta, exit_delta, gamma_thresh, cooldown)
                    best_results = results

# Sort by total P&L and show top 20
results_table.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\nTop 20 parameter combinations:")
print(f"{'Short Δ':>8} {'Exit Δ':>8} {'Gamma':>8} {'Cool':>6} {'Trades':>8} {'WR%':>8} {'Total P&L':>12} {'Avg P&L':>10}")
print("-" * 80)

for r in results_table[:20]:
    print(f"{r['short_delta']:>8.2f} {r['exit_delta']:>8.2f} {r['gamma_thresh']:>8} {r['cooldown']:>6}d {r['trades']:>8} {r['win_rate']:>7.1f}% ${r['total_pnl']:>+10,.0f} ${r['avg_pnl']:>+9,.0f}")

print("\n" + "="*80)
print(f"BEST PARAMETERS: short_delta={best_params[0]}, exit_delta={best_params[1]}, gamma={best_params[2]}, cooldown={best_params[3]}d")
print(f"Total P&L: ${best_pnl:,.0f}")
print("="*80)

# Run detailed backtest with best params
print("\nDetailed results with best parameters:")
results = run_backtest(*best_params)

total = len(results)
wins = [r for r in results if r['pnl'] > 0]
losses = [r for r in results if r['pnl'] <= 0]

print(f"Total trades: {total}")
print(f"Wins: {len(wins)} ({len(wins)/total*100:.1f}%)")
print(f"Losses: {len(losses)} ({len(losses)/total*100:.1f}%)")
print(f"Total P&L: ${sum(r['pnl'] for r in results):,.0f}")
print(f"Avg win: ${sum(r['pnl'] for r in wins)/len(wins):,.0f}")
print(f"Avg loss: ${sum(r['pnl'] for r in losses)/len(losses):,.0f}")
print(f"Avg days held: {sum(r['days'] for r in results)/total:.1f}")

# Exit breakdown
print(f"\nExit breakdown:")
exit_types = {}
for r in results:
    t = r['exit_type']
    if t not in exit_types:
        exit_types[t] = {'count': 0, 'pnl': 0}
    exit_types[t]['count'] += 1
    exit_types[t]['pnl'] += r['pnl']

for t, data in sorted(exit_types.items()):
    print(f"  {t:15s}: {data['count']:3d} trades | ${data['pnl']:+,.0f}")

# Gamma exit analysis
gamma_exits = [r for r in results if r['exit_type'] == 'gamma_exit']
if gamma_exits:
    print(f"\nGamma exit analysis ({len(gamma_exits)} trades):")
    print(f"  Avg gamma at exit: {sum(r['gamma'] for r in gamma_exits)/len(gamma_exits):.1f}")
    print(f"  Avg loss: ${sum(r['pnl'] for r in gamma_exits)/len(gamma_exits):,.0f}")
