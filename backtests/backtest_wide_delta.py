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

def estimate_credit(iv, dte, short_delta):
    # Credit scales with delta - higher delta = more credit
    base = 260 * (short_delta / 0.17)
    return base * (iv / 0.55) * sqrt(dte / 50) * 0.96

class Position:
    def __init__(self, entry_ts, spot, iv, dte, short_delta):
        self.entry_ts = entry_ts
        self.entry_spot = spot
        self.entry_iv = iv
        self.entry_dte = dte
        self.short_delta = short_delta
        self.short_put = find_strike(spot, dte, iv, short_delta, 'put')
        self.short_call = find_strike(spot, dte, iv, short_delta, 'call')
        self.credit = estimate_credit(iv, dte, short_delta)
        self.max_loss = 1000

def run_backtest(short_delta, exit_delta, profit_target, cooldown_days):
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
        else:
            hours_held = (ts - position.entry_ts) / (1000 * 60 * 60)
            current_dte = 50 - (hours_held / 24)
            
            if current_dte <= 0.1:
                position = None
                continue
                
            T = current_dte / 365
            
            put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
            call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
            
            # Profit estimate
            time_decay = (hours_held / 24) / 50
            delta_factor = max(put_delta, call_delta) / position.short_delta
            remaining = position.credit * (1 - time_decay * 0.8) * sqrt(delta_factor)
            profit_pct = (position.credit - remaining) / position.credit
            
            exit_type = None
            pnl = 0
            
            if profit_pct >= profit_target:
                exit_type = 'profit'
                pnl = position.credit * profit_target
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
                    'put_delta': put_delta,
                    'call_delta': call_delta
                })
                position = None
    
    return results

print("\n" + "="*90)
print("WIDE EXIT DELTA TEST - Finding BTC's sweet spot")
print("="*90)

results_table = []

# Test wider range of exit deltas (0.40 to 0.65)
# And different entry deltas and profit targets
for short_delta in [0.10, 0.12, 0.15, 0.17, 0.20]:
    for exit_delta in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]:
        for profit_target in [0.25, 0.30, 0.40, 0.50]:
            for cooldown in [7, 14]:
                results = run_backtest(short_delta, exit_delta, profit_target, cooldown)
                
                if len(results) < 10:
                    continue
                
                total_pnl = sum(r['pnl'] for r in results)
                wins = len([r for r in results if r['pnl'] > 0])
                losses = len([r for r in results if r['pnl'] <= 0])
                win_rate = wins / len(results) * 100
                
                delta_exits = len([r for r in results if 'delta' in r['exit_type']])
                
                if wins > 0 and losses > 0:
                    avg_win = sum(r['pnl'] for r in results if r['pnl'] > 0) / wins
                    avg_loss = abs(sum(r['pnl'] for r in results if r['pnl'] < 0) / losses)
                    # Required win rate to break even
                    req_wr = avg_loss / (avg_win + avg_loss) * 100
                    edge = win_rate - req_wr
                else:
                    avg_win = avg_loss = req_wr = edge = 0
                
                results_table.append({
                    'short_delta': short_delta,
                    'exit_delta': exit_delta,
                    'profit_target': profit_target,
                    'cooldown': cooldown,
                    'trades': len(results),
                    'win_rate': win_rate,
                    'delta_exits': delta_exits,
                    'total_pnl': total_pnl,
                    'avg_win': avg_win,
                    'avg_loss': avg_loss,
                    'req_wr': req_wr,
                    'edge': edge
                })

# Sort by P&L
results_table.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\nTop 40 combinations (sorted by P&L):")
print(f"{'EntryΔ':>7} {'ExitΔ':>7} {'Profit%':>8} {'Cool':>5} {'N':>4} {'WR%':>7} {'ΔExits':>7} {'AvgWin':>8} {'AvgLoss':>8} {'ReqWR%':>8} {'Edge%':>7} {'P&L':>10}")
print("-" * 110)

for r in results_table[:40]:
    print(f"{r['short_delta']:>7.2f} {r['exit_delta']:>7.2f} {r['profit_target']*100:>7.0f}% {r['cooldown']:>4}d {r['trades']:>4} {r['win_rate']:>6.1f}% {r['delta_exits']:>7} ${r['avg_win']:>7.0f} ${r['avg_loss']:>7.0f} {r['req_wr']:>7.1f}% {r['edge']:>+6.1f}% ${r['total_pnl']:>+9,.0f}")

# Show positive edge combinations
positive_edge = [r for r in results_table if r['edge'] > 0]
print(f"\n{'='*90}")
print(f"POSITIVE EDGE COMBINATIONS: {len(positive_edge)}")
print(f"{'='*90}")

if positive_edge:
    positive_edge.sort(key=lambda x: x['edge'], reverse=True)
    for r in positive_edge[:15]:
        print(f"  EntryΔ={r['short_delta']}, ExitΔ={r['exit_delta']}, Profit={r['profit_target']*100:.0f}%, Cool={r['cooldown']}d")
        print(f"    WR={r['win_rate']:.1f}% vs Required={r['req_wr']:.1f}% -> Edge={r['edge']:+.1f}% | P&L=${r['total_pnl']:+,.0f}")
else:
    print("  None found")

# Profitable combinations
profitable = [r for r in results_table if r['total_pnl'] > 0]
print(f"\n{'='*90}")
print(f"PROFITABLE COMBINATIONS: {len(profitable)}")
print(f"{'='*90}")

if profitable:
    for r in profitable:
        print(f"  EntryΔ={r['short_delta']}, ExitΔ={r['exit_delta']}, Profit={r['profit_target']*100:.0f}%, Cool={r['cooldown']}d -> P&L=${r['total_pnl']:+,.0f} (WR={r['win_rate']:.1f}%)")
else:
    print("  None found - strategy is fundamentally unprofitable on BTC over this period")

# Analyze what exit delta reduces delta exits most
print(f"\n{'='*90}")
print("DELTA EXIT ANALYSIS BY EXIT THRESHOLD")
print(f"{'='*90}")

for exit_d in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]:
    subset = [r for r in results_table if r['exit_delta'] == exit_d and r['short_delta'] == 0.15 and r['profit_target'] == 0.30]
    if subset:
        avg_delta_exits = sum(r['delta_exits'] for r in subset) / len(subset)
        avg_wr = sum(r['win_rate'] for r in subset) / len(subset)
        avg_pnl = sum(r['total_pnl'] for r in subset) / len(subset)
        print(f"  Exit Δ={exit_d}: Avg delta exits={avg_delta_exits:.1f}, WR={avg_wr:.1f}%, P&L=${avg_pnl:+,.0f}")
