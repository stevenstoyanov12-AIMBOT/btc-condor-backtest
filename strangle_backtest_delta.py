"""
BTC Short Strangle Backtest - Delta-Based Exits
Tests exits at different delta thresholds: 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60
"""
import json
import math
from datetime import datetime, timedelta
from scipy.stats import norm

# Black-Scholes functions
def d1(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))

def d2(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0
    return d1(S, K, T, r, sigma) - sigma * math.sqrt(T)

def call_price(S, K, T, r, sigma):
    if T <= 0:
        return max(S - K, 0)
    D1 = d1(S, K, T, r, sigma)
    D2 = d2(S, K, T, r, sigma)
    return S * norm.cdf(D1) - K * math.exp(-r * T) * norm.cdf(D2)

def put_price(S, K, T, r, sigma):
    if T <= 0:
        return max(K - S, 0)
    D1 = d1(S, K, T, r, sigma)
    D2 = d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * norm.cdf(-D2) - S * norm.cdf(-D1)

def call_delta(S, K, T, r, sigma):
    if T <= 0:
        return 1.0 if S > K else 0.0
    return norm.cdf(d1(S, K, T, r, sigma))

def put_delta(S, K, T, r, sigma):
    if T <= 0:
        return -1.0 if S < K else 0.0
    return norm.cdf(d1(S, K, T, r, sigma)) - 1

def call_theta(S, K, T, r, sigma):
    if T <= 0:
        return 0
    D1 = d1(S, K, T, r, sigma)
    D2 = d2(S, K, T, r, sigma)
    theta = (-S * norm.pdf(D1) * sigma / (2 * math.sqrt(T)) 
             - r * K * math.exp(-r * T) * norm.cdf(D2))
    return theta / 365

def put_theta(S, K, T, r, sigma):
    if T <= 0:
        return 0
    D1 = d1(S, K, T, r, sigma)
    D2 = d2(S, K, T, r, sigma)
    theta = (-S * norm.pdf(D1) * sigma / (2 * math.sqrt(T)) 
             + r * K * math.exp(-r * T) * norm.cdf(-D2))
    return theta / 365


class StrangleBacktest:
    def __init__(self, btc_data, dvol_data, exit_delta=0.45):
        self.btc_data = btc_data
        self.dvol_data = dvol_data
        self.r = 0.05
        
        # Strategy parameters
        self.strike_distance = 0.08  # 8% OTM
        self.exit_delta = exit_delta  # Exit when delta exceeds this
        self.min_dte = 20
        self.target_dte = 50
        self.fee_percent = 0.04  # 4% fee round trip (entry + exit)
        
        # Create DVOL lookup by date
        self.dvol_by_date = {}
        for row in dvol_data:
            ts = row[0]
            iv_close = row[4] / 100
            date = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
            self.dvol_by_date[date] = iv_close
    
    def get_iv_for_timestamp(self, ts):
        date = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
        for i in range(10):
            check_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=i)).strftime('%Y-%m-%d')
            if check_date in self.dvol_by_date:
                return self.dvol_by_date[check_date]
        return 0.50
    
    def run_backtest(self):
        results = {
            'trades': [],
            'total_pnl': 0,
            'wins': 0,
            'losses': 0,
            'exits_at_delta': 0,
            'exits_at_min_dte': 0,
            'total_theta_earned': 0,
            'total_hours_in_position': 0,
            'delta_at_exits': [],
            'total_fees': 0,
        }
        
        position = None
        
        for i, bar in enumerate(self.btc_data):
            ts = bar['ts']
            btc_price = bar['close']
            current_time = datetime.fromtimestamp(ts / 1000)
            iv = self.get_iv_for_timestamp(ts)
            
            if position is None:
                # Open new position
                put_strike = round(btc_price * (1 - self.strike_distance) / 1000) * 1000
                call_strike = round(btc_price * (1 + self.strike_distance) / 1000) * 1000
                
                T = self.target_dte / 365
                
                put_premium = put_price(btc_price, put_strike, T, self.r, iv)
                call_premium = call_price(btc_price, call_strike, T, self.r, iv)
                total_premium = put_premium + call_premium
                
                put_d = put_delta(btc_price, put_strike, T, self.r, iv)
                call_d = call_delta(btc_price, call_strike, T, self.r, iv)
                put_t = put_theta(btc_price, put_strike, T, self.r, iv)
                call_t = call_theta(btc_price, call_strike, T, self.r, iv)
                
                position = {
                    'entry_time': current_time,
                    'entry_price': btc_price,
                    'put_strike': put_strike,
                    'call_strike': call_strike,
                    'entry_premium': total_premium,
                    'entry_iv': iv,
                    'target_expiry': current_time + timedelta(days=self.target_dte),
                    'entry_put_delta': put_d,
                    'entry_call_delta': call_d,
                    'entry_theta': abs(put_t) + abs(call_t),
                    'hours_held': 0,
                    'theta_earned': 0,
                }
                
            else:
                position['hours_held'] += 1
                
                dte = (position['target_expiry'] - current_time).days
                if dte < 1:
                    dte = 1
                T = dte / 365
                
                put_premium = put_price(btc_price, position['put_strike'], T, self.r, iv)
                call_premium = call_price(btc_price, position['call_strike'], T, self.r, iv)
                current_premium = put_premium + call_premium
                
                put_d = put_delta(btc_price, position['put_strike'], T, self.r, iv)
                call_d = call_delta(btc_price, position['call_strike'], T, self.r, iv)
                put_t = put_theta(btc_price, position['put_strike'], T, self.r, iv)
                call_t = call_theta(btc_price, position['call_strike'], T, self.r, iv)
                current_theta = abs(put_t) + abs(call_t)
                
                position['theta_earned'] += current_theta / 24
                
                exit_reason = None
                exit_delta = None
                
                # Exit if delta exceeds threshold
                if abs(put_d) >= self.exit_delta:
                    exit_reason = 'put_delta'
                    exit_delta = abs(put_d)
                elif call_d >= self.exit_delta:
                    exit_reason = 'call_delta'
                    exit_delta = call_d
                elif dte <= self.min_dte:
                    exit_reason = 'min_dte'
                    exit_delta = max(abs(put_d), call_d)
                
                if exit_reason:
                    # Calculate fees (4% round trip on premium)
                    total_fees = (position['entry_premium'] + current_premium) * self.fee_percent / 2
                    
                    pnl_before_fees = position['entry_premium'] - current_premium
                    pnl = pnl_before_fees - total_fees
                    
                    trade = {
                        'entry_time': position['entry_time'],
                        'exit_time': current_time,
                        'entry_price': position['entry_price'],
                        'exit_price': btc_price,
                        'put_strike': position['put_strike'],
                        'call_strike': position['call_strike'],
                        'entry_premium': position['entry_premium'],
                        'exit_premium': current_premium,
                        'pnl': pnl,
                        'exit_reason': exit_reason,
                        'hours_held': position['hours_held'],
                        'theta_earned': position['theta_earned'],
                        'exit_put_delta': put_d,
                        'exit_call_delta': call_d,
                        'exit_delta': exit_delta,
                        'entry_iv': position['entry_iv'],
                        'exit_iv': iv,
                        'dte_at_exit': dte,
                    }
                    
                    results['trades'].append(trade)
                    results['total_pnl'] += pnl
                    results['total_fees'] += total_fees
                    results['total_theta_earned'] += position['theta_earned']
                    results['total_hours_in_position'] += position['hours_held']
                    results['delta_at_exits'].append(exit_delta)
                    
                    if pnl > 0:
                        results['wins'] += 1
                    else:
                        results['losses'] += 1
                    
                    if exit_reason in ['put_delta', 'call_delta']:
                        results['exits_at_delta'] += 1
                    else:
                        results['exits_at_min_dte'] += 1
                    
                    position = None
        
        return results


def main():
    # Load data
    print("Loading data...")
    
    with open('btc_hourly_full.json', 'r') as f:
        btc_data = json.load(f)
    
    with open('btc_dvol_daily.json', 'r') as f:
        dvol_data = json.load(f)
    
    print(f"Loaded {len(btc_data)} hourly BTC bars")
    print(f"Loaded {len(dvol_data)} daily DVOL bars")
    
    start_date = datetime.fromtimestamp(btc_data[0]['ts'] / 1000)
    end_date = datetime.fromtimestamp(btc_data[-1]['ts'] / 1000)
    print(f"Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    
    # Test different delta exit levels
    delta_levels = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    
    print("\n" + "="*90)
    print("DELTA-BASED EXIT BACKTEST RESULTS")
    print("="*90)
    print(f"\n{'Exit Delta':<12} {'Trades':<8} {'Win%':<8} {'Total PnL':<14} {'Total Fees':<14} {'Avg PnL':<12} {'Delta Exits':<12} {'DTE Exits':<10} {'Avg Days':<10}")
    print("-"*110)
    
    all_results = {}
    
    for delta_level in delta_levels:
        backtest = StrangleBacktest(btc_data, dvol_data, exit_delta=delta_level)
        results = backtest.run_backtest()
        all_results[delta_level] = results
        
        total_trades = len(results['trades'])
        if total_trades > 0:
            win_rate = results['wins'] / total_trades * 100
            avg_pnl = results['total_pnl'] / total_trades
            avg_days = results['total_hours_in_position'] / total_trades / 24
            
            print(f"{delta_level:<12.2f} {total_trades:<8} {win_rate:<8.1f} ${results['total_pnl']:<13,.2f} ${results['total_fees']:<13,.2f} ${avg_pnl:<11,.2f} {results['exits_at_delta']:<12} {results['exits_at_min_dte']:<10} {avg_days:<10.1f}")
    
    print("-"*110)
    
    # Detailed analysis for each delta level
    print("\n" + "="*90)
    print("DETAILED ANALYSIS PER DELTA LEVEL")
    print("="*90)
    
    for delta_level in delta_levels:
        results = all_results[delta_level]
        total_trades = len(results['trades'])
        if total_trades == 0:
            continue
            
        print(f"\n--- EXIT DELTA: {delta_level} ---")
        
        # Separate delta exits by put vs call
        put_exits = [t for t in results['trades'] if t['exit_reason'] == 'put_delta']
        call_exits = [t for t in results['trades'] if t['exit_reason'] == 'call_delta']
        dte_exits = [t for t in results['trades'] if t['exit_reason'] == 'min_dte']
        
        print(f"Put Delta Exits: {len(put_exits)} | Avg PnL: ${sum(t['pnl'] for t in put_exits)/len(put_exits) if put_exits else 0:,.2f}")
        print(f"Call Delta Exits: {len(call_exits)} | Avg PnL: ${sum(t['pnl'] for t in call_exits)/len(call_exits) if call_exits else 0:,.2f}")
        print(f"Min DTE Exits: {len(dte_exits)} | Avg PnL: ${sum(t['pnl'] for t in dte_exits)/len(dte_exits) if dte_exits else 0:,.2f}")
        
        # Worst and best trades
        sorted_trades = sorted(results['trades'], key=lambda x: x['pnl'])
        worst = sorted_trades[0]
        best = sorted_trades[-1]
        
        print(f"Worst Trade: ${worst['pnl']:,.2f} ({worst['exit_reason']}) | BTC ${worst['entry_price']:,.0f}→${worst['exit_price']:,.0f} | {worst['hours_held']/24:.1f} days")
        print(f"Best Trade: ${best['pnl']:,.2f} ({best['exit_reason']}) | BTC ${best['entry_price']:,.0f}→${best['exit_price']:,.0f} | {best['hours_held']/24:.1f} days")
        
        # Average delta at exit for delta-triggered exits
        delta_exits = [t for t in results['trades'] if t['exit_reason'] in ['put_delta', 'call_delta']]
        if delta_exits:
            avg_exit_delta = sum(t['exit_delta'] for t in delta_exits) / len(delta_exits)
            print(f"Avg Delta at Exit: {avg_exit_delta:.3f}")
    
    print("\n" + "="*90)
    print("RECOMMENDATION")
    print("="*90)
    
    # Find best delta level by total PnL
    best_delta = max(all_results.keys(), key=lambda d: all_results[d]['total_pnl'])
    best_result = all_results[best_delta]
    
    print(f"\nBest Exit Delta: {best_delta}")
    print(f"Total PnL: ${best_result['total_pnl']:,.2f}")
    print(f"Win Rate: {best_result['wins']/len(best_result['trades'])*100:.1f}%")
    print(f"Avg PnL per Trade: ${best_result['total_pnl']/len(best_result['trades']):,.2f}")


if __name__ == "__main__":
    main()
