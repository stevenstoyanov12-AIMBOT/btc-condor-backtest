import json
from datetime import datetime
from math import log, sqrt
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
    """Raw gamma - same as Deribit returns"""
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

# First, let's see what raw gamma values look like for BTC
print("\n" + "="*70)
print("RAW GAMMA VALUES FOR BTC (matching Deribit API format)")
print("="*70)

spot = 85000
iv = 0.55

for dte in [50, 35, 25, 14]:
    T = dte / 365
    # 0.17 delta put/call strikes
    put_strike = find_strike(spot, dte, iv, 0.17, 'put')
    call_strike = find_strike(spot, dte, iv, 0.17, 'call')
    
    put_gamma = bs_gamma(spot, put_strike, T, iv)
    call_gamma = bs_gamma(spot, call_strike, T, iv)
    max_gamma = max(put_gamma, call_gamma)
    
    print(f"  {dte} DTE: put_gamma={put_gamma:.8f}, call_gamma={call_gamma:.8f}, max={max_gamma:.8f}")

# Compare to SOL equivalent
print(f"\nSOL thresholds: 0.016 (high DTE), 0.020 (mid DTE)")
print(f"BTC equivalent = SOL_threshold × (SOL_price / BTC_price)")
print(f"  High DTE: 0.016 × (130 / 85000) = {0.016 * 130 / 85000:.8f}")
print(f"  Mid DTE:  0.020 × (130 / 85000) = {0.020 * 130 / 85000:.8f}")

# So BTC thresholds should be around 0.000024 to 0.00003
btc_gamma_high = 0.000024
btc_gamma_mid = 0.00003

print(f"\nProposed BTC thresholds: {btc_gamma_high} (high DTE), {btc_gamma_mid} (mid DTE)")

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

def run_backtest(short_delta, exit_delta, gamma_high_dte, gamma_mid_dte, cooldown_days):
    """
    Run backtest with DTE-based gamma thresholds like SOL bot.
    gamma_high_dte: threshold when DTE > 35
    gamma_mid_dte: threshold when 25 < DTE <= 35
    No gamma exit when DTE <= 25
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
            
            # Calculate Greeks (raw gamma like Deribit)
            put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
            call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
            put_gamma = bs_gamma(spot, position.short_put, T, iv)
            call_gamma = bs_gamma(spot, position.short_call, T, iv)
            max_gamma = max(put_gamma, call_gamma)
            
            # Dynamic DTE-based gamma threshold (like SOL bot)
            gamma_threshold = None
            if current_dte > 35:
                gamma_threshold = gamma_high_dte
            elif current_dte > 25:
                gamma_threshold = gamma_mid_dte
            # else: no gamma exit below 25 DTE
            
            # Estimate profit
            time_decay = (hours_held / 24) / 50
            delta_factor = max(put_delta, call_delta) / short_delta
            remaining = position.credit * (1 - time_decay * 0.8) * sqrt(delta_factor)
            profit_pct = (position.credit - remaining) / position.credit
            
            exit_type = None
            pnl = 0
            
            # Exit conditions (gamma first, then delta, like SOL bot)
            if gamma_threshold and max_gamma > gamma_threshold:
                exit_type = 'gamma_exit'
                pnl = -(remaining * 0.3)
                last_loss_ts = ts
            elif profit_pct >= 0.30:
                exit_type = 'profit'
                pnl = position.credit * 0.30
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
                    'dte_at_exit': current_dte,
                    'gamma': max_gamma,
                    'put_delta': put_delta,
                    'call_delta': call_delta
                })
                position = None
    
    return results

# Test combinations with RAW gamma thresholds
print("\n" + "="*70)
print("OPTIMIZATION WITH RAW GAMMA (BTC-scaled)")
print("="*70)

best_pnl = float('-inf')
best_params = None
results_table = []

# BTC-scaled gamma thresholds
gamma_highs = [0.000020, 0.000024, 0.000028, 0.000032]
gamma_mids = [0.000025, 0.000030, 0.000035, 0.000040]

for short_delta in [0.15, 0.17, 0.20]:
    for exit_delta in [0.35, 0.40, 0.45]:
        for gamma_high in gamma_highs:
            for gamma_mid in gamma_mids:
                if gamma_mid <= gamma_high:
                    continue  # Mid should be higher than high
                for cooldown in [3, 7, 14]:
                    results = run_backtest(short_delta, exit_delta, gamma_high, gamma_mid, cooldown)
                    
                    if len(results) < 10:
                        continue
                    
                    total_pnl = sum(r['pnl'] for r in results)
                    wins = len([r for r in results if r['pnl'] > 0])
                    win_rate = wins / len(results) * 100
                    
                    gamma_exits = len([r for r in results if r['exit_type'] == 'gamma_exit'])
                    
                    results_table.append({
                        'short_delta': short_delta,
                        'exit_delta': exit_delta,
                        'gamma_high': gamma_high,
                        'gamma_mid': gamma_mid,
                        'cooldown': cooldown,
                        'trades': len(results),
                        'win_rate': win_rate,
                        'total_pnl': total_pnl,
                        'gamma_exits': gamma_exits
                    })
                    
                    if total_pnl > best_pnl:
                        best_pnl = total_pnl
                        best_params = (short_delta, exit_delta, gamma_high, gamma_mid, cooldown)
                        best_results = results

# Also test with NO gamma threshold
for short_delta in [0.15, 0.17, 0.20]:
    for exit_delta in [0.35, 0.40, 0.45]:
        for cooldown in [3, 7, 14]:
            results = run_backtest(short_delta, exit_delta, 0, 0, cooldown)
            
            if len(results) < 10:
                continue
            
            total_pnl = sum(r['pnl'] for r in results)
            wins = len([r for r in results if r['pnl'] > 0])
            win_rate = wins / len(results) * 100
            
            results_table.append({
                'short_delta': short_delta,
                'exit_delta': exit_delta,
                'gamma_high': 0,
                'gamma_mid': 0,
                'cooldown': cooldown,
                'trades': len(results),
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'gamma_exits': 0
            })
            
            if total_pnl > best_pnl:
                best_pnl = total_pnl
                best_params = (short_delta, exit_delta, 0, 0, cooldown)
                best_results = results

# Sort by P&L
results_table.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\nTop 30 combinations:")
print(f"{'Δ':>6} {'ExΔ':>6} {'γ_high':>12} {'γ_mid':>12} {'Cool':>6} {'N':>5} {'WR%':>7} {'γ_exits':>8} {'P&L':>12}")
print("-" * 90)

for r in results_table[:30]:
    gh = f"{r['gamma_high']:.6f}" if r['gamma_high'] > 0 else "OFF"
    gm = f"{r['gamma_mid']:.6f}" if r['gamma_mid'] > 0 else "OFF"
    print(f"{r['short_delta']:>6.2f} {r['exit_delta']:>6.2f} {gh:>12} {gm:>12} {r['cooldown']:>5}d {r['trades']:>5} {r['win_rate']:>6.1f}% {r['gamma_exits']:>8} ${r['total_pnl']:>+10,.0f}")

# Profitable?
positive = [r for r in results_table if r['total_pnl'] > 0]
print(f"\n{'='*70}")
if positive:
    print(f"PROFITABLE COMBINATIONS: {len(positive)}")
    for r in positive[:10]:
        print(f"  Δ={r['short_delta']}, exit_Δ={r['exit_delta']}, γ_hi={r['gamma_high']}, γ_mid={r['gamma_mid']}, cool={r['cooldown']}d -> ${r['total_pnl']:+,.0f}")
else:
    print("NO PROFITABLE COMBINATIONS FOUND")
print(f"{'='*70}")

# Best detailed
print(f"\nBEST: Δ={best_params[0]}, exit_Δ={best_params[1]}, γ_hi={best_params[2]}, γ_mid={best_params[3]}, cool={best_params[4]}d")
print(f"P&L: ${best_pnl:,.0f}")

results = best_results
total = len(results)
wins = [r for r in results if r['pnl'] > 0]
losses = [r for r in results if r['pnl'] <= 0]

print(f"\nTrades: {total} | WR: {len(wins)/total*100:.1f}%")
print(f"Avg win: ${sum(r['pnl'] for r in wins)/len(wins):,.0f}")
if losses:
    print(f"Avg loss: ${sum(r['pnl'] for r in losses)/len(losses):,.0f}")

# Exit breakdown
exit_types = {}
for r in results:
    t = r['exit_type']
    if t not in exit_types:
        exit_types[t] = {'count': 0, 'pnl': 0}
    exit_types[t]['count'] += 1
    exit_types[t]['pnl'] += r['pnl']

print(f"\nExit breakdown:")
for t, data in sorted(exit_types.items(), key=lambda x: -x[1]['count']):
    print(f"  {t:15s}: {data['count']:3d} | ${data['pnl']:+,.0f}")
