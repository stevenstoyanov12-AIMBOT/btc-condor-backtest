import json
from datetime import datetime
from math import log, sqrt, exp
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
COOLDOWN_HOURS = 24 * 7  # Wait 7 days after a loss before re-entering

# BS delta - CORRECTED
def bs_delta(S, K, T, sigma, opt_type):
    """Calculate Black-Scholes delta"""
    if T <= 0.001:
        if opt_type == 'call':
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0
    
    r = 0.05
    d1 = (log(S/K) + (r + sigma**2/2)*T) / (sigma*sqrt(T))
    
    if opt_type == 'call':
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1  # Negative for puts

def find_strike(spot, dte, iv, target_delta, opt_type):
    """Find strike price for target delta"""
    T = dte / 365
    
    if opt_type == 'put':
        # Put delta is negative, target is like -0.17
        low, high = spot * 0.5, spot * 0.98
        target = -target_delta  # Convert to negative
    else:
        # Call delta is positive
        low, high = spot * 1.02, spot * 2.0
        target = target_delta
    
    for _ in range(100):
        mid = (low + high) / 2
        delta = bs_delta(spot, mid, T, iv, opt_type)
        
        if abs(delta - target) < 0.001:
            return round(mid / 1000) * 1000
        
        if opt_type == 'put':
            # Put delta becomes more negative as strike increases
            if delta < target:  # e.g., -0.20 < -0.17
                high = mid
            else:
                low = mid
        else:
            # Call delta decreases as strike increases
            if delta > target:
                low = mid
            else:
                high = mid
    
    return round(mid / 1000) * 1000

def estimate_credit(iv, dte):
    """Estimate credit received based on IV and DTE"""
    # Based on current market: ~$260 at 55% IV, 50 DTE for a $1000 spread
    base = 260
    iv_factor = iv / 0.55
    dte_factor = sqrt(dte / 50)  # Theta decays with sqrt of time
    return base * iv_factor * dte_factor * (1 - SLIP)

def estimate_position_value(position, spot, iv, current_dte):
    """
    Estimate current position value based on option pricing.
    For iron condor: value decreases as theta decays (good for seller)
    Value increases if spot moves toward strikes (bad for seller)
    """
    T = max(current_dte / 365, 0.001)
    
    # Calculate current deltas
    put_delta = bs_delta(spot, position.short_put, T, iv, 'put')
    call_delta = bs_delta(spot, position.short_call, T, iv, 'call')
    
    # Estimate remaining premium based on delta and time
    # Higher delta = closer to ITM = more premium left (bad)
    # Lower time = less extrinsic value = less premium left (good)
    
    put_premium_factor = abs(put_delta) / SHORT_DELTA  # Ratio vs entry delta
    call_premium_factor = abs(call_delta) / SHORT_DELTA
    
    time_factor = sqrt(current_dte / position.entry_dte)  # Theta decay
    
    # Remaining value is weighted by both delta proximity and time
    put_remaining = position.credit * 0.5 * put_premium_factor * time_factor
    call_remaining = position.credit * 0.5 * call_premium_factor * time_factor
    
    remaining_value = put_remaining + call_remaining
    return remaining_value, abs(put_delta), abs(call_delta)

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

# Verify strike finding is working
test_spot = 85000
test_iv = 0.55
test_dte = 50
print(f"\nStrike verification at spot=${test_spot:,}, IV={test_iv*100}%, DTE={test_dte}:")
put_strike = find_strike(test_spot, test_dte, test_iv, SHORT_DELTA, 'put')
call_strike = find_strike(test_spot, test_dte, test_iv, SHORT_DELTA, 'call')
put_d = bs_delta(test_spot, put_strike, test_dte/365, test_iv, 'put')
call_d = bs_delta(test_spot, call_strike, test_dte/365, test_iv, 'call')
print(f"  Put strike: ${put_strike:,} (delta: {put_d:.3f})")
print(f"  Call strike: ${call_strike:,} (delta: {call_d:.3f})")
print(f"  Width: {(call_strike - put_strike) / test_spot * 100:.1f}% of spot")

# Run backtest
results = []
position = None
last_loss_ts = 0
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
        # Check cooldown after loss
        hours_since_loss = (ts - last_loss_ts) / (1000 * 60 * 60)
        if hours_since_loss >= COOLDOWN_HOURS:
            position = Position(ts, spot, iv, ENTRY_DTE)
            entry_date = date_str
    else:
        hours_held = (ts - position.entry_ts) / (1000 * 60 * 60)
        current_dte = position.entry_dte - (hours_held / 24)
        
        remaining_value, put_delta, call_delta = estimate_position_value(
            position, spot, iv, current_dte
        )
        
        profit = position.credit - remaining_value
        profit_pct = profit / position.credit
        
        exit_type = None
        pnl = 0
        
        # Exit conditions (in priority order)
        if profit_pct >= EXIT_PROFIT_PCT:
            exit_type = 'profit_30pct'
            pnl = position.credit * EXIT_PROFIT_PCT
        elif put_delta > EXIT_DELTA:
            exit_type = 'put_delta_exit'
            # Loss = remaining value we have to buy back + any intrinsic
            intrinsic = max(position.short_put - spot, 0)
            pnl = -(remaining_value + intrinsic * 0.3)
            last_loss_ts = ts
        elif call_delta > EXIT_DELTA:
            exit_type = 'call_delta_exit'
            intrinsic = max(spot - position.short_call, 0)
            pnl = -(remaining_value + intrinsic * 0.3)
            last_loss_ts = ts
        elif current_dte <= MIN_DTE:
            # Close at 21 DTE regardless
            if spot < position.short_put:
                exit_type = 'put_itm_expiry'
                pnl = -min(position.short_put - spot, SPREAD_WIDTH)
                last_loss_ts = ts
            elif spot > position.short_call:
                exit_type = 'call_itm_expiry'
                pnl = -min(spot - position.short_call, SPREAD_WIDTH)
                last_loss_ts = ts
            else:
                exit_type = 'expire_otm'
                pnl = position.credit * 0.85  # Most of credit kept
        
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
                'exit_call_delta': call_delta,
                'profit_pct_at_exit': profit_pct
            })
            position = None
    
    idx += 1

# Summary
print(f"\n{'='*70}")
print("BACKTEST RESULTS - BTC Iron Condor (Fixed)")
print(f"Params: {SHORT_DELTA} delta, {EXIT_DELTA} exit delta, {COOLDOWN_HOURS/24:.0f}d cooldown")
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
        if delta_exits:
            avg_delta_loss = sum(r['pnl'] for r in delta_exits) / len(delta_exits)
            avg_days_to_exit = sum(r['days'] for r in delta_exits) / len(delta_exits)
            print(f"  Avg loss on delta exit: ${avg_delta_loss:,.0f}")
            print(f"  Avg days to delta exit: {avg_days_to_exit:.1f}")
    
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
    
    # Show all trades with detail
    print(f"\nAll {total} trades:")
    for r in results:
        move = (r['exit_spot'] - r['entry_spot']) / r['entry_spot'] * 100
        print(f"  {r['entry'][:10]} -> {r['exit'][:10]} ({r['days']:5.1f}d): {r['exit_type']:18s} | ${r['pnl']:+8,.0f} | spot ${r['entry_spot']:,.0f}->${r['exit_spot']:,.0f} ({move:+6.1f}%) | put_d={r['exit_put_delta']:.2f} call_d={r['exit_call_delta']:.2f}")
