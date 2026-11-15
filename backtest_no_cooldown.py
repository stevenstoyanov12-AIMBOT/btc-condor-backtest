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
                results.append({'pnl': pnl, 'exit_type': exit_type})
                position = None
    
    return results

print("\n" + "="*100)
print("NO COOLDOWN TEST - Many Profit Targets")
print("="*100)

results_table = []

for short_delta in [0.10, 0.12, 0.15, 0.17]:
    for exit_delta in [0.40, 0.45, 0.50, 0.55]:
        for profit_target in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80]:
            for cooldown in [0, 3, 7]:
                results = run_backtest(short_delta, exit_delta, profit_target, cooldown)
                if len(results) < 10:
                    continue
                
                total_pnl = sum(r['pnl'] for r in results)
                wins = [r for r in results if r['pnl'] > 0]
                losses = [r for r in results if r['pnl'] <= 0]
                
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
                    'trades': len(results), 'win_rate': len(wins) / len(results) * 100,
                    'delta_exits': len([r for r in results if 'delta' in r['exit_type']]),
                    'total_pnl': total_pnl, 'avg_win': avg_win, 'avg_loss': avg_loss,
                    'req_wr': req_wr, 'edge': edge
                })

results_table.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\nTop 50 combinations:")
print(f"{'EntryΔ':>7} {'ExitΔ':>7} {'Profit%':>8} {'Cool':>5} {'N':>5} {'WR%':>7} {'ΔExits':>7} {'AvgWin':>8} {'AvgLoss':>8} {'Edge%':>7} {'P&L':>12}")
print("-" * 105)

for r in results_table[:50]:
    cool_str = f"{r['cooldown']}d" if r['cooldown'] > 0 else "NONE"
    print(f"{r['short_delta']:>7.2f} {r['exit_delta']:>7.2f} {r['profit_target']*100:>7.0f}% {cool_str:>5} {r['trades']:>5} {r['win_rate']:>6.1f}% {r['delta_exits']:>7} ${r['avg_win']:>7.0f} ${r['avg_loss']:>7.0f} {r['edge']:>+6.1f}% ${r['total_pnl']:>+10,.0f}")

# No cooldown only
no_cooldown = [r for r in results_table if r['cooldown'] == 0]
no_cooldown.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\n{'='*100}")
print(f"NO COOLDOWN ONLY - Top 20:")
print(f"{'='*100}")

for r in no_cooldown[:20]:
    print(f"  Δ={r['short_delta']}, ExitΔ={r['exit_delta']}, Profit={r['profit_target']*100:.0f}% -> {r['trades']} trades, WR={r['win_rate']:.1f}%, Edge={r['edge']:+.1f}%, P&L=${r['total_pnl']:+,.0f}")

# Profitable no-cooldown
profitable_nc = [r for r in no_cooldown if r['total_pnl'] > 0]
print(f"\n{'='*100}")
print(f"PROFITABLE NO-COOLDOWN: {len(profitable_nc)}")
print(f"{'='*100}")
for r in profitable_nc[:10]:
    print(f"  Δ={r['short_delta']}, ExitΔ={r['exit_delta']}, Profit={r['profit_target']*100:.0f}% -> P&L=${r['total_pnl']:+,.0f} (WR={r['win_rate']:.1f}%, {r['trades']} trades)")
