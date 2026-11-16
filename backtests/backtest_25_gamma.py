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

R = 0.05
SPREAD_WIDTH = 1000

def bs_d1_d2(S, K, T, r, sigma):
    d1 = (log(S/K) + (r + sigma**2/2)*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)
    return d1, d2

def bs_call_price(S, K, T, r, sigma):
    if T <= 0.001:
        return max(S - K, 0)
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    return S * norm.cdf(d1) - K * exp(-r*T) * norm.cdf(d2)

def bs_put_price(S, K, T, r, sigma):
    if T <= 0.001:
        return max(K - S, 0)
    d1, d2 = bs_d1_d2(S, K, T, r, sigma)
    return K * exp(-r*T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def bs_delta(S, K, T, sigma, opt_type):
    if T <= 0.001:
        return (1.0 if S > K else 0.0) if opt_type == 'call' else (-1.0 if S < K else 0.0)
    d1, _ = bs_d1_d2(S, K, T, R, sigma)
    return norm.cdf(d1) if opt_type == 'call' else norm.cdf(d1) - 1

def bs_gamma(S, K, T, sigma):
    if T <= 0.001:
        return 0.0
    d1, _ = bs_d1_d2(S, K, T, R, sigma)
    return norm.pdf(d1) / (S * sigma * sqrt(T))

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

def run_backtest(short_delta, exit_delta, profit_target, gamma_high, gamma_mid, cooldown_days):
    """
    gamma_high: threshold when DTE > 35
    gamma_mid: threshold when 25 < DTE <= 35
    Set to 0 to disable gamma exit
    """
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
            
            # Greeks
            put_delta = abs(bs_delta(spot, position.short_put, T, iv, 'put'))
            call_delta = abs(bs_delta(spot, position.short_call, T, iv, 'call'))
            put_gamma = bs_gamma(spot, position.short_put, T, iv)
            call_gamma = bs_gamma(spot, position.short_call, T, iv)
            max_gamma = max(put_gamma, call_gamma)
            
            # Dynamic gamma threshold based on DTE
            gamma_threshold = None
            if gamma_high > 0 and current_dte > 35:
                gamma_threshold = gamma_high
            elif gamma_mid > 0 and current_dte > 25:
                gamma_threshold = gamma_mid
            
            # Current value and P&L
            current_value = calc_condor_value(spot, iv, current_dte, position.short_put, position.short_call)
            unrealized_pnl = position.entry_credit - current_value
            profit_pct = unrealized_pnl / position.entry_credit
            
            exit_type, pnl = None, 0
            
            # Exit conditions (gamma first for early exit)
            if gamma_threshold and max_gamma > gamma_threshold:
                exit_type = 'gamma'
                pnl = unrealized_pnl * 0.96
                last_loss_ts = ts
            elif profit_pct >= profit_target:
                exit_type = 'profit'
                pnl = unrealized_pnl * 0.96
            elif put_delta > exit_delta:
                exit_type = 'put_delta'
                pnl = unrealized_pnl * 0.96
                last_loss_ts = ts
            elif call_delta > exit_delta:
                exit_type = 'call_delta'
                pnl = unrealized_pnl * 0.96
                last_loss_ts = ts
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
                    'max_gamma': max_gamma,
                    'max_delta': max(put_delta, call_delta)
                })
                position = None
    
    return results

print("\n" + "="*100)
print("25% PROFIT TARGET WITH GAMMA EXIT OPTIMIZATION")
print("="*100)

# First establish baseline without gamma
print("\nBaseline (no gamma exit):")
baseline = run_backtest(0.12, 0.45, 0.25, 0, 0, 7)
wins = len([r for r in baseline if r['pnl'] > 0])
total_pnl = sum(r['pnl'] for r in baseline)
print(f"  0.12Δ, 0.45 exit, 25% TP, 7d cool: {len(baseline)} trades, {wins/len(baseline)*100:.1f}% WR, ${total_pnl:+,.0f}")

# Find typical gamma values at delta exit
delta_exits = [r for r in baseline if 'delta' in r['exit_type']]
if delta_exits:
    avg_gamma_at_exit = sum(r['max_gamma'] for r in delta_exits) / len(delta_exits)
    print(f"  Delta exits: {len(delta_exits)}, avg gamma at exit: {avg_gamma_at_exit:.8f}")

print("\n" + "-"*100)
print("Testing gamma thresholds (scaled for BTC):")
print("-"*100)

results_table = []

# BTC-scaled gamma thresholds (SOL 0.016-0.020 × 130/85000)
gamma_highs = [0, 0.000020, 0.000024, 0.000028, 0.000032]
gamma_mids = [0, 0.000025, 0.000030, 0.000035, 0.000040]

for short_delta in [0.12, 0.15]:
    for exit_delta in [0.45, 0.50]:
        for profit_target in [0.20, 0.25, 0.30]:
            for gamma_high in gamma_highs:
                for gamma_mid in gamma_mids:
                    if gamma_high > 0 and gamma_mid > 0 and gamma_mid <= gamma_high:
                        continue
                    for cooldown in [0, 7]:
                        results = run_backtest(short_delta, exit_delta, profit_target, gamma_high, gamma_mid, cooldown)
                        
                        if len(results) < 10:
                            continue
                        
                        total_pnl = sum(r['pnl'] for r in results)
                        wins = [r for r in results if r['pnl'] > 0]
                        losses = [r for r in results if r['pnl'] <= 0]
                        gamma_exits = [r for r in results if r['exit_type'] == 'gamma']
                        delta_exits = [r for r in results if 'delta' in r['exit_type']]
                        avg_days = sum(r['days_held'] for r in results) / len(results)
                        
                        if wins and losses:
                            avg_win = sum(r['pnl'] for r in wins) / len(wins)
                            avg_loss = abs(sum(r['pnl'] for r in losses) / len(losses))
                        else:
                            avg_win, avg_loss = 0, 0
                        
                        results_table.append({
                            'short_delta': short_delta, 
                            'exit_delta': exit_delta,
                            'profit_target': profit_target, 
                            'gamma_high': gamma_high,
                            'gamma_mid': gamma_mid,
                            'cooldown': cooldown,
                            'trades': len(results), 
                            'win_rate': len(wins) / len(results) * 100,
                            'gamma_exits': len(gamma_exits),
                            'delta_exits': len(delta_exits),
                            'avg_days': avg_days,
                            'total_pnl': total_pnl, 
                            'avg_win': avg_win, 
                            'avg_loss': avg_loss
                        })

results_table.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\nTop 30 (sorted by P&L):")
print(f"{'Δ':>5} {'ExΔ':>5} {'TP%':>4} {'γHi':>10} {'γMid':>10} {'Cool':>5} {'N':>4} {'WR%':>6} {'γEx':>4} {'ΔEx':>4} {'Days':>5} {'P&L':>10}")
print("-" * 95)

for r in results_table[:30]:
    gh = f"{r['gamma_high']:.6f}" if r['gamma_high'] > 0 else "OFF"
    gm = f"{r['gamma_mid']:.6f}" if r['gamma_mid'] > 0 else "OFF"
    cool = f"{r['cooldown']}d" if r['cooldown'] > 0 else "NO"
    print(f"{r['short_delta']:>5.2f} {r['exit_delta']:>5.2f} {r['profit_target']*100:>3.0f}% {gh:>10} {gm:>10} {cool:>5} {r['trades']:>4} {r['win_rate']:>5.1f}% {r['gamma_exits']:>4} {r['delta_exits']:>4} {r['avg_days']:>4.1f}d ${r['total_pnl']:>+9,.0f}")

# Best with 90%+ WR
high_wr = [r for r in results_table if r['win_rate'] >= 90]
if high_wr:
    print(f"\n{'='*100}")
    print("90%+ WIN RATE COMBINATIONS:")
    print(f"{'='*100}")
    high_wr.sort(key=lambda x: x['total_pnl'], reverse=True)
    for r in high_wr[:10]:
        gh = f"{r['gamma_high']:.6f}" if r['gamma_high'] > 0 else "OFF"
        gm = f"{r['gamma_mid']:.6f}" if r['gamma_mid'] > 0 else "OFF"
        cool = f"{r['cooldown']}d" if r['cooldown'] > 0 else "NO"
        print(f"  Δ={r['short_delta']}, ExΔ={r['exit_delta']}, TP={r['profit_target']*100:.0f}%, γHi={gh}, γMid={gm}, Cool={cool}")
        print(f"    {r['trades']} trades, WR={r['win_rate']:.1f}%, γExits={r['gamma_exits']}, ΔExits={r['delta_exits']}, P&L=${r['total_pnl']:+,.0f}")

# Best overall
best = results_table[0]
print(f"\n{'='*100}")
print("BEST OVERALL:")
print(f"{'='*100}")
gh = f"{best['gamma_high']:.6f}" if best['gamma_high'] > 0 else "OFF"
gm = f"{best['gamma_mid']:.6f}" if best['gamma_mid'] > 0 else "OFF"
cool = f"{best['cooldown']}d" if best['cooldown'] > 0 else "NONE"
print(f"  Entry Δ: {best['short_delta']}")
print(f"  Exit Δ: {best['exit_delta']}")
print(f"  Profit Target: {best['profit_target']*100:.0f}%")
print(f"  Gamma High DTE: {gh}")
print(f"  Gamma Mid DTE: {gm}")
print(f"  Cooldown: {cool}")
print(f"  ---")
print(f"  Trades: {best['trades']}")
print(f"  Win Rate: {best['win_rate']:.1f}%")
print(f"  Gamma Exits: {best['gamma_exits']}")
print(f"  Delta Exits: {best['delta_exits']}")
print(f"  Avg Days: {best['avg_days']:.1f}")
print(f"  Total P&L: ${best['total_pnl']:+,.0f} (per contract)")
print(f"  P&L with 2.4 contracts: ${best['total_pnl'] * 2.4:+,.0f}")
