import json
from math import log, sqrt, exp
from scipy.stats import norm

# Load data
btc_hourly = json.load(open("btc_hourly_full.json"))
btc_iv_daily = json.load(open("btc_dvol_daily.json"))

print(f"BTC hourly: {len(btc_hourly)} | IV daily: {len(btc_iv_daily)}")

# Build IV lookup
iv_lookup = {}
for d in btc_iv_daily:
    ts = d[0]
    iv = d[4] / 100
    day_start = (ts // (24*60*60*1000)) * (24*60*60*1000)
    for h in range(24):
        iv_lookup[day_start + h*60*60*1000] = iv

price_data = [{'ts': p['ts'], 'close': p['close'], 'iv': iv_lookup[p['ts']]} 
              for p in btc_hourly if p['ts'] in iv_lookup]
print(f"Aligned: {len(price_data)}")

# Black-Scholes functions
def bs_d1_d2(S, K, T, r, sigma):
    d1 = (log(S/K) + (r + sigma**2/2)*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)
    return d1, d2

def bs_call_price(S, K, T, r, sigma):
    """Black-Scholes call option price"""
    if T <= 0.001:
        return max(S - K, 0)
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    return S * norm.cdf(d1) - K * exp(-r*T) * norm.cdf(d2)

def bs_put_price(S, K, T, r, sigma):
    """Black-Scholes put option price"""
    if T <= 0.001:
        return max(K - S, 0)
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    return K * exp(-r*T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def bs_delta(S, K, T, sigma, opt_type):
    if T <= 0.001:
        return (1.0 if S > K else 0.0) if opt_type == 'call' else (-1.0 if S < K else 0.0)
    d1, _ = bs_d1_d2(S, K, T, 0.05, sigma)
    return norm.cdf(d1) if opt_type == 'call' else norm.cdf(d1) - 1

def find_strike(spot, dte, iv, target_delta, opt_type):
    T = dte / 365
    low, high = (spot * 0.5, spot * 0.98) if opt_type == 'put' else (spot * 1.02, spot * 2.0)
    target = -target_delta if opt_type == 'put' else target_delta
    for _ in range(100):
        mid = (low + high) / 2
        delta = bs_delta(spot, mid, T, iv, opt_type)
        if abs(delta - target) < 0.001:
            return round(mid / 1000) * 1000
        if opt_type == 'put':
            high, low = (mid, low) if delta < target else (high, mid)
        else:
            low, high = (mid, high) if delta > target else (low, mid)
    return round(mid / 1000) * 1000

SPREAD_WIDTH = 1000
R = 0.05  # Risk-free rate

def calc_condor_value(spot, iv, dte, short_put, short_call):
    """Calculate iron condor value using Black-Scholes"""
    T = dte / 365
    long_put = short_put - SPREAD_WIDTH
    long_call = short_call + SPREAD_WIDTH
    
    # Put spread value (short - long)
    put_spread = bs_put_price(spot, short_put, T, R, iv) - bs_put_price(spot, long_put, T, R, iv)
    
    # Call spread value (short - long)
    call_spread = bs_call_price(spot, short_call, T, R, iv) - bs_call_price(spot, long_call, T, R, iv)
    
    return put_spread + call_spread

class Position:
    def __init__(self, ts, spot, iv, dte, short_delta):
        self.entry_ts = ts
        self.entry_spot = spot
        self.entry_iv = iv
        self.entry_dte = dte
        self.short_delta = short_delta
        
        # Find strikes
        self.short_put = find_strike(spot, dte, iv, short_delta, 'put')
        self.short_call = find_strike(spot, dte, iv, short_delta, 'call')
        
        # Calculate entry credit using BS
        self.entry_credit = calc_condor_value(spot, iv, dte, self.short_put, self.short_call)
        
        # Apply slippage (4%)
        self.entry_credit *= 0.96

def run_backtest(short_delta, exit_delta, profit_target, cooldown_days):
    results, position, last_loss_ts = [], None, 0
    cooldown_hours = cooldown_days * 24
    
    for d in price_data:
        ts, spot, iv = d['ts'], d['close'], d['iv']
        
        if position is None:
            if (ts - last_loss_ts) / 3600000 >= cooldown_hours:
                position = Position(ts, spot, iv, 50, short_delta)
        else:
            hours_held = (ts - position.entry_ts) / 3600000
            current_dte = 50 - hours_held / 24
            
            if current_dte <= 0.1:
                position = None
                continue
            
            T = current_dte / 365
            
            # Calculate current greeks
            put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
            call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
            
            # Calculate current condor value using BS
            current_value = calc_condor_value(spot, iv, current_dte, position.short_put, position.short_call)
            
            # P&L = what we received - what it costs to close
            # We received entry_credit, it costs current_value to close
            unrealized_pnl = position.entry_credit - current_value
            profit_pct = unrealized_pnl / position.entry_credit
            
            exit_type, pnl = None, 0
            
            # Profit target
            if profit_pct >= profit_target:
                exit_type = 'profit'
                pnl = unrealized_pnl * 0.96  # Slippage on exit
            
            # Delta exit
            elif put_delta > exit_delta:
                exit_type = 'put_delta'
                pnl = unrealized_pnl * 0.96  # Could be negative
                last_loss_ts = ts
            elif call_delta > exit_delta:
                exit_type = 'call_delta'
                pnl = unrealized_pnl * 0.96
                last_loss_ts = ts
            
            # DTE exit at 21 days
            elif current_dte <= 21:
                exit_type = 'dte_exit'
                pnl = unrealized_pnl * 0.96
                if pnl < 0:
                    last_loss_ts = ts
            
            if exit_type:
                results.append({
                    'pnl': pnl,
                    'exit_type': exit_type,
                    'days_held': hours_held / 24,
                    'entry_credit': position.entry_credit,
                    'exit_value': current_value,
                    'profit_pct': profit_pct
                })
                position = None
    
    return results

# Verify BS pricing is working
print("\n" + "="*80)
print("BLACK-SCHOLES PRICING VERIFICATION")
print("="*80)

spot = 85000
iv = 0.55
dte = 50
T = dte / 365

short_put = find_strike(spot, dte, iv, 0.12, 'put')
short_call = find_strike(spot, dte, iv, 0.12, 'call')

print(f"\nSpot: ${spot:,} | IV: {iv*100:.0f}% | DTE: {dte}")
print(f"Short put: ${short_put:,} | Short call: ${short_call:,}")
print(f"Long put: ${short_put - SPREAD_WIDTH:,} | Long call: ${short_call + SPREAD_WIDTH:,}")

entry_credit = calc_condor_value(spot, iv, dte, short_put, short_call)
print(f"\nEntry credit (BS): ${entry_credit:,.2f}")

# Simulate theta decay (price stays same)
print(f"\nTheta decay simulation (spot stays at ${spot:,}):")
for days_passed in [0, 7, 14, 21, 29]:
    remaining_dte = dte - days_passed
    value = calc_condor_value(spot, iv, remaining_dte, short_put, short_call)
    pnl = entry_credit - value
    pct = pnl / entry_credit * 100
    print(f"  Day {days_passed:2d} (DTE {remaining_dte:2d}): Value=${value:,.2f} | P&L=${pnl:+,.2f} ({pct:+.1f}%)")

# Simulate price move (call side stress)
print(f"\nPrice rally simulation (spot moves up, 30 DTE):")
for new_spot in [85000, 90000, 95000, 100000, 105000]:
    value = calc_condor_value(new_spot, iv, 30, short_put, short_call)
    pnl = entry_credit - value
    pct = pnl / entry_credit * 100
    call_d = abs(bs_delta(new_spot, short_call, 30/365, iv, 'call'))
    print(f"  Spot ${new_spot:,}: Value=${value:,.2f} | P&L=${pnl:+,.2f} ({pct:+.1f}%) | Call Δ={call_d:.3f}")

print("\n" + "="*80)
print("BACKTEST WITH PROPER BS PRICING")
print("="*80)

results_table = []

for short_delta in [0.10, 0.12, 0.15]:
    for exit_delta in [0.40, 0.45, 0.50]:
        for profit_target in [0.20, 0.25, 0.30, 0.35, 0.40, 0.50]:
            for cooldown in [0, 7]:
                results = run_backtest(short_delta, exit_delta, profit_target, cooldown)
                
                if len(results) < 10:
                    continue
                
                total_pnl = sum(r['pnl'] for r in results)
                wins = [r for r in results if r['pnl'] > 0]
                losses = [r for r in results if r['pnl'] <= 0]
                avg_days = sum(r['days_held'] for r in results) / len(results)
                
                if wins and losses:
                    avg_win = sum(r['pnl'] for r in wins) / len(wins)
                    avg_loss = abs(sum(r['pnl'] for r in losses) / len(losses))
                    req_wr = avg_loss / (avg_win + avg_loss) * 100
                    edge = len(wins) / len(results) * 100 - req_wr
                else:
                    avg_win, avg_loss, req_wr, edge = 0, 0, 0, 0
                
                results_table.append({
                    'short_delta': short_delta, 'exit_delta': exit_delta,
                    'profit_target': profit_target, 'cooldown': cooldown,
                    'trades': len(results), 
                    'win_rate': len(wins) / len(results) * 100,
                    'avg_days': avg_days,
                    'total_pnl': total_pnl, 
                    'avg_win': avg_win, 
                    'avg_loss': avg_loss,
                    'edge': edge
                })

results_table.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\nTop 30 combinations:")
print(f"{'EntryΔ':>7} {'ExitΔ':>7} {'TP%':>5} {'Cool':>5} {'N':>5} {'WR%':>7} {'Days':>6} {'AvgWin':>9} {'AvgLoss':>9} {'Edge%':>7} {'P&L':>12}")
print("-" * 105)

for r in results_table[:30]:
    cool_str = f"{r['cooldown']}d" if r['cooldown'] > 0 else "NONE"
    print(f"{r['short_delta']:>7.2f} {r['exit_delta']:>7.2f} {r['profit_target']*100:>4.0f}% {cool_str:>5} {r['trades']:>5} {r['win_rate']:>6.1f}% {r['avg_days']:>5.1f}d ${r['avg_win']:>8,.0f} ${r['avg_loss']:>8,.0f} {r['edge']:>+6.1f}% ${r['total_pnl']:>+10,.0f}")

# Profitable
profitable = [r for r in results_table if r['total_pnl'] > 0]
print(f"\n{'='*80}")
print(f"PROFITABLE: {len(profitable)} / {len(results_table)}")
print(f"{'='*80}")

for r in profitable[:10]:
    cool_str = f"{r['cooldown']}d" if r['cooldown'] > 0 else "NONE"
    print(f"  Δ={r['short_delta']}, ExitΔ={r['exit_delta']}, TP={r['profit_target']*100:.0f}%, Cool={cool_str}")
    print(f"    {r['trades']} trades, {r['avg_days']:.1f}d avg, WR={r['win_rate']:.1f}%, Edge={r['edge']:+.1f}%, P&L=${r['total_pnl']:+,.0f}")
