import json
from math import log, sqrt, exp
from scipy.stats import norm

btc_hourly = json.load(open("btc_hourly_full.json"))
btc_iv_daily = json.load(open("btc_dvol_daily.json"))

iv_lookup = {}
for d in btc_iv_daily:
    ts = d[0]
    iv = d[4] / 100
    day_start = (ts // (24*60*60*1000)) * (24*60*60*1000)
    for h in range(24):
        iv_lookup[day_start + h*60*60*1000] = iv

price_data = [{'ts': p['ts'], 'close': p['close'], 'iv': iv_lookup[p['ts']]} 
              for p in btc_hourly if p['ts'] in iv_lookup]

R = 0.05
SPREAD_WIDTH = 1000

def bs_d1_d2(S, K, T, r, sigma):
    d1 = (log(S/K) + (r + sigma**2/2)*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)
    return d1, d2

def bs_call_price(S, K, T, r, sigma):
    if T <= 0.001: return max(S - K, 0)
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    return S * norm.cdf(d1) - K * exp(-r*T) * norm.cdf(d2)

def bs_put_price(S, K, T, r, sigma):
    if T <= 0.001: return max(K - S, 0)
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    return K * exp(-r*T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def bs_delta(S, K, T, sigma, opt_type):
    if T <= 0.001:
        return (1.0 if S > K else 0.0) if opt_type == 'call' else (-1.0 if S < K else 0.0)
    d1, _ = bs_d1_d2(S, K, T, R, sigma)
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

def calc_condor_value(spot, iv, dte, short_put, short_call):
    T = dte / 365
    long_put = short_put - SPREAD_WIDTH
    long_call = short_call + SPREAD_WIDTH
    put_spread = bs_put_price(spot, short_put, T, R, iv) - bs_put_price(spot, long_put, T, R, iv)
    call_spread = bs_call_price(spot, short_call, T, R, iv) - bs_call_price(spot, long_call, T, R, iv)
    return put_spread + call_spread

class Position:
    def __init__(self, ts, spot, iv, dte, short_delta):
        self.entry_ts = ts
        self.entry_spot = spot
        self.entry_iv = iv
        self.entry_dte = dte
        self.short_delta = short_delta
        self.short_put = find_strike(spot, dte, iv, short_delta, 'put')
        self.short_call = find_strike(spot, dte, iv, short_delta, 'call')
        self.entry_credit = calc_condor_value(spot, iv, dte, self.short_put, self.short_call) * 0.96

def run_backtest(short_delta, exit_delta, profit_target, cooldown_days, use_delta_exit=True):
    results, position, last_loss_ts = [], None, 0
    cooldown_hours = cooldown_days * 24
    all_losses = []
    
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
            put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
            call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
            
            current_value = calc_condor_value(spot, iv, current_dte, position.short_put, position.short_call)
            unrealized_pnl = position.entry_credit - current_value
            profit_pct = unrealized_pnl / position.entry_credit
            
            exit_type, pnl = None, 0
            
            if profit_pct >= profit_target:
                exit_type = 'profit'
                pnl = unrealized_pnl * 0.96
            elif use_delta_exit and put_delta > exit_delta:
                exit_type = 'put_delta'
                if unrealized_pnl > 0:
                    pnl = unrealized_pnl * 0.96
                else:
                    pnl = unrealized_pnl * 1.04
                last_loss_ts = ts
                all_losses.append(pnl)
            elif use_delta_exit and call_delta > exit_delta:
                exit_type = 'call_delta'
                if unrealized_pnl > 0:
                    pnl = unrealized_pnl * 0.96
                else:
                    pnl = unrealized_pnl * 1.04
                last_loss_ts = ts
                all_losses.append(pnl)
            elif current_dte <= 21:
                exit_type = 'dte_exit'
                if unrealized_pnl > 0:
                    pnl = unrealized_pnl * 0.96
                else:
                    pnl = unrealized_pnl * 1.04
                if pnl < 0:
                    last_loss_ts = ts
                    all_losses.append(pnl)
            
            if exit_type:
                results.append({'pnl': pnl, 'exit_type': exit_type, 'days_held': hours_held / 24})
                position = None
    
    return results, all_losses

print("="*120)
print("TESTING WITH AND WITHOUT DELTA EXIT")
print("="*120)

results_table = []

# WITH DELTA EXIT (0.40 to 0.70)
for exit_delta in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
    for profit_target in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
        for cooldown in [0, 7, 14]:
            results, losses = run_backtest(0.12, exit_delta, profit_target, cooldown, use_delta_exit=True)
            
            if len(results) < 10:
                continue
            
            wins = [r for r in results if r['pnl'] > 0]
            wr = len(wins) / len(results) * 100
            total_pnl = sum(r['pnl'] for r in results)
            
            avg_win = sum(r['pnl'] for r in wins) / len(wins) if wins else 0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 0
            max_loss = abs(min(losses)) if losses else 0
            
            ev = (wr/100 * avg_win) - ((100-wr)/100 * avg_loss)
            
            results_table.append({
                'delta_exit': f'{exit_delta}',
                'exit_delta': exit_delta,
                'profit_target': profit_target,
                'cooldown': cooldown,
                'trades': len(results),
                'win_rate': wr,
                'total_pnl': total_pnl,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'max_loss': max_loss,
                'num_losses': len(losses),
                'ev': ev
            })

# WITHOUT DELTA EXIT (NO delta exit, only profit target and DTE exit)
for profit_target in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]:
    for cooldown in [0, 7, 14]:
        results, losses = run_backtest(0.12, 999, profit_target, cooldown, use_delta_exit=False)
        
        if len(results) < 10:
            continue
        
        wins = [r for r in results if r['pnl'] > 0]
        wr = len(wins) / len(results) * 100
        total_pnl = sum(r['pnl'] for r in results)
        
        avg_win = sum(r['pnl'] for r in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        max_loss = abs(min(losses)) if losses else 0
        
        ev = (wr/100 * avg_win) - ((100-wr)/100 * avg_loss)
        
        results_table.append({
            'delta_exit': 'NONE',
            'exit_delta': 999,
            'profit_target': profit_target,
            'cooldown': cooldown,
            'trades': len(results),
            'win_rate': wr,
            'total_pnl': total_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_loss': max_loss,
            'num_losses': len(losses),
            'ev': ev
        })

# Sort by EV
results_table.sort(key=lambda x: x['ev'], reverse=True)

print(f"\nTOP 25 BY EXPECTED VALUE (INCLUDING NO DELTA EXIT):")
print(f"{'ExΔ':>6} {'TP%':>4} {'Cool':>5} {'N':>4} {'WR%':>7} {'#Loss':>6} {'AvgWin':>8} {'AvgLoss':>9} {'MaxLoss':>9} {'EV':>8} {'P&L':>10}")
print("-" * 105)

for r in results_table[:25]:
    cool = f"{r['cooldown']}d" if r['cooldown'] > 0 else "NO"
    print(f"{r['delta_exit']:>6} {r['profit_target']*100:>3.0f}% {cool:>5} {r['trades']:>4} {r['win_rate']:>6.1f}% {r['num_losses']:>6} ${r['avg_win']:>7.0f} ${r['avg_loss']:>8.0f} ${r['max_loss']:>8.0f} ${r['ev']:>+7.0f} ${r['total_pnl']:>+9,.0f}")

# NO DELTA EXIT ONLY
no_delta = [r for r in results_table if r['delta_exit'] == 'NONE']
print(f"\n{'='*120}")
print(f"NO DELTA EXIT CONFIGS (sorted by EV):")
print(f"{'='*120}")
print(f"{'TP%':>5} {'Cool':>5} {'N':>4} {'WR%':>7} {'#Loss':>6} {'AvgWin':>8} {'AvgLoss':>9} {'MaxLoss':>9} {'EV':>8} {'P&L':>10}")
print("-" * 90)
for r in no_delta[:15]:
    cool = f"{r['cooldown']}d" if r['cooldown'] > 0 else "NO"
    print(f"{r['profit_target']*100:>4.0f}% {cool:>5} {r['trades']:>4} {r['win_rate']:>6.1f}% {r['num_losses']:>6} ${r['avg_win']:>7.0f} ${r['avg_loss']:>8.0f} ${r['max_loss']:>8.0f} ${r['ev']:>+7.0f} ${r['total_pnl']:>+9,.0f}")

# BEST OVERALL
print(f"\n{'='*120}")
print("BEST CONFIG (Highest Expected Value):")
print(f"{'='*120}")
best = results_table[0]
cool = f"{best['cooldown']}d" if best['cooldown'] > 0 else "NONE"
print(f"  Delta Exit: {best['delta_exit']}")
print(f"  Profit Target: {best['profit_target']*100:.0f}%")
print(f"  Cooldown: {cool}")
print(f"")
print(f"  Trades: {best['trades']} (over 2.7 years)")
print(f"  Win Rate: {best['win_rate']:.1f}%")
print(f"  Losses: {best['num_losses']}")
print(f"")
print(f"  Avg Win: ${best['avg_win']:.0f}")
print(f"  Avg Loss: ${best['avg_loss']:.0f}")
print(f"  Max Loss: ${best['max_loss']:.0f}")
print(f"  Expected Value: ${best['ev']:+.0f} per trade")
print(f"")
print(f"  Total P&L (1 BTC): ${best['total_pnl']:+,.0f}")
print(f"  Total P&L (6.25 BTC): ${best['total_pnl'] * 6.25:+,.0f}")

# BEST NO DELTA EXIT
if no_delta:
    print(f"\n{'='*120}")
    print("BEST NO DELTA EXIT CONFIG:")
    print(f"{'='*120}")
    best_no = no_delta[0]
    cool = f"{best_no['cooldown']}d" if best_no['cooldown'] > 0 else "NONE"
    print(f"  Profit Target: {best_no['profit_target']*100:.0f}%")
    print(f"  Cooldown: {cool}")
    print(f"")
    print(f"  Trades: {best_no['trades']}")
    print(f"  Win Rate: {best_no['win_rate']:.1f}%")
    print(f"  Losses: {best_no['num_losses']}")
    print(f"")
    print(f"  Avg Win: ${best_no['avg_win']:.0f}")
    print(f"  Avg Loss: ${best_no['avg_loss']:.0f}")
    print(f"  Max Loss: ${best_no['max_loss']:.0f}")
    print(f"  Expected Value: ${best_no['ev']:+.0f} per trade")
    print(f"")
    print(f"  Total P&L (1 BTC): ${best_no['total_pnl']:+,.0f}")
    print(f"  Total P&L (6.25 BTC): ${best_no['total_pnl'] * 6.25:+,.0f}")