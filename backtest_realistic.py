import json
from math import log, sqrt
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

def bs_delta(S, K, T, sigma, opt_type):
    if T <= 0.001:
        return (1.0 if S > K else 0.0) if opt_type == 'call' else (-1.0 if S < K else 0.0)
    d1 = (log(S/K) + (0.05 + sigma**2/2)*T) / (sigma*sqrt(T))
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

class Position:
    def __init__(self, ts, spot, iv, dte, short_delta):
        self.entry_ts, self.entry_spot, self.entry_iv, self.entry_dte = ts, spot, iv, dte
        self.short_delta = short_delta
        self.short_put = find_strike(spot, dte, iv, short_delta, 'put')
        self.short_call = find_strike(spot, dte, iv, short_delta, 'call')
        self.credit = 260 * (short_delta / 0.17) * (iv / 0.55) * sqrt(dte / 50) * 0.96

def run_backtest(short_delta, exit_delta, profit_target, cooldown_days):
    results, position, last_loss_ts = [], None, 0
    cooldown_hours = cooldown_days * 24
    total_days_held = 0
    
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
            
            time_decay = hours_held / 24 / 50
            remaining = position.credit * (1 - time_decay * 0.8) * sqrt(max(put_delta, call_delta) / position.short_delta)
            profit_pct = (position.credit - remaining) / position.credit
            
            exit_type, pnl = None, 0
            
            if profit_pct >= profit_target:
                exit_type, pnl = 'profit', position.credit * profit_target
            elif put_delta > exit_delta:
                exit_type, pnl = 'put_delta', -(remaining + max(position.short_put - spot, 0) * 0.3)
                last_loss_ts = ts
            elif call_delta > exit_delta:
                exit_type, pnl = 'call_delta', -(remaining + max(spot - position.short_call, 0) * 0.3)
                last_loss_ts = ts
            elif current_dte <= 21:
                if spot < position.short_put:
                    exit_type, pnl = 'put_itm', -min(position.short_put - spot, 1000)
                    last_loss_ts = ts
                elif spot > position.short_call:
                    exit_type, pnl = 'call_itm', -min(spot - position.short_call, 1000)
                    last_loss_ts = ts
                else:
                    exit_type, pnl = 'expire', position.credit * 0.85
            
            if exit_type:
                results.append({
                    'pnl': pnl, 
                    'exit_type': exit_type,
                    'days_held': hours_held / 24
                })
                total_days_held += hours_held / 24
                position = None
    
    return results, total_days_held

print("\n" + "="*110)
print("REALISTIC PROFIT TARGETS - No Cooldown Focus")
print("="*110)

results_table = []

# Focus on realistic profit targets (20-50%) with no cooldown
for short_delta in [0.10, 0.12, 0.15]:
    for exit_delta in [0.40, 0.45, 0.50]:
        for profit_target in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
            results, total_days = run_backtest(short_delta, exit_delta, profit_target, 0)  # 0 = no cooldown
            if len(results) < 10:
                continue
            
            total_pnl = sum(r['pnl'] for r in results)
            wins = [r for r in results if r['pnl'] > 0]
            losses = [r for r in results if r['pnl'] <= 0]
            delta_exits = [r for r in results if 'delta' in r['exit_type']]
            
            avg_days = total_days / len(results) if results else 0
            
            if wins and losses:
                avg_win = sum(r['pnl'] for r in wins) / len(wins)
                avg_loss = abs(sum(r['pnl'] for r in losses) / len(losses))
                req_wr = avg_loss / (avg_win + avg_loss) * 100
                edge = len(wins) / len(results) * 100 - req_wr
            else:
                avg_win, avg_loss, req_wr, edge = 0, 0, 0, 0
            
            results_table.append({
                'short_delta': short_delta, 'exit_delta': exit_delta,
                'profit_target': profit_target,
                'trades': len(results), 'win_rate': len(wins) / len(results) * 100,
                'delta_exits': len(delta_exits),
                'avg_days': avg_days,
                'total_pnl': total_pnl, 'avg_win': avg_win, 'avg_loss': avg_loss,
                'edge': edge
            })

results_table.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\nAll combinations (sorted by P&L):")
print(f"{'EntryΔ':>7} {'ExitΔ':>7} {'TP%':>5} {'N':>5} {'WR%':>7} {'ΔExits':>7} {'AvgDays':>8} {'AvgWin':>8} {'AvgLoss':>8} {'Edge%':>7} {'P&L':>12}")
print("-" * 105)

for r in results_table:
    print(f"{r['short_delta']:>7.2f} {r['exit_delta']:>7.2f} {r['profit_target']*100:>4.0f}% {r['trades']:>5} {r['win_rate']:>6.1f}% {r['delta_exits']:>7} {r['avg_days']:>7.1f}d ${r['avg_win']:>7.0f} ${r['avg_loss']:>7.0f} {r['edge']:>+6.1f}% ${r['total_pnl']:>+10,.0f}")

# Show profitable only
profitable = [r for r in results_table if r['total_pnl'] > 0]
print(f"\n{'='*110}")
print(f"PROFITABLE COMBINATIONS: {len(profitable)}")
print(f"{'='*110}")

# Group by profit target
print("\nBest per profit target:")
for pt in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
    subset = [r for r in results_table if r['profit_target'] == pt]
    if subset:
        best = max(subset, key=lambda x: x['total_pnl'])
        status = "✓" if best['total_pnl'] > 0 else "✗"
        print(f"  {status} {pt*100:>3.0f}%: Δ={best['short_delta']}, ExitΔ={best['exit_delta']} -> {best['trades']} trades, {best['avg_days']:.1f}d avg, WR={best['win_rate']:.1f}%, P&L=${best['total_pnl']:+,.0f}")

# Efficiency analysis - P&L per day held
print(f"\n{'='*110}")
print("EFFICIENCY ANALYSIS - P&L per day held (time in market)")
print(f"{'='*110}")

for r in results_table:
    r['pnl_per_day'] = r['total_pnl'] / (r['trades'] * r['avg_days']) if r['avg_days'] > 0 else 0

results_table.sort(key=lambda x: x['pnl_per_day'], reverse=True)

print(f"{'EntryΔ':>7} {'ExitΔ':>7} {'TP%':>5} {'N':>5} {'AvgDays':>8} {'P&L':>12} {'P&L/Day':>10}")
print("-" * 70)

for r in results_table[:15]:
    print(f"{r['short_delta']:>7.2f} {r['exit_delta']:>7.2f} {r['profit_target']*100:>4.0f}% {r['trades']:>5} {r['avg_days']:>7.1f}d ${r['total_pnl']:>+10,.0f} ${r['pnl_per_day']:>+9.2f}")
