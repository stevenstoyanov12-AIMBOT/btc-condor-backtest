import json
from math import log, sqrt, exp, pi
from scipy.stats import norm

# Load data
btc_hourly = json.load(open("btc_hourly_full.json"))
btc_iv_daily = json.load(open("btc_dvol_daily.json"))

# Greeks calculations
def bs_d1(S, K, T, r, sigma):
    return (log(S/K) + (r + sigma**2/2)*T) / (sigma*sqrt(T))

def bs_delta(S, K, T, sigma, opt_type):
    if T <= 0.001:
        return 0.0
    r = 0.05
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.cdf(d1) if opt_type == 'call' else norm.cdf(d1) - 1

def bs_gamma(S, K, T, sigma):
    if T <= 0.001:
        return 0.0
    r = 0.05
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * sqrt(T))

# Test with real numbers
print("="*70)
print("GAMMA VALUE ANALYSIS")
print("="*70)

# Scenario 1: Entry position
spot = 85000
iv = 0.55
dte = 50
T = dte / 365

# 0.17 delta strikes
put_strike = 72000   # Approximate 0.17 delta put
call_strike = 102000 # Approximate 0.17 delta call

put_delta = abs(bs_delta(spot, put_strike, T, iv, 'put'))
call_delta = bs_delta(spot, call_strike, T, iv, 'call')
put_gamma = bs_gamma(spot, put_strike, T, iv)
call_gamma = bs_gamma(spot, call_strike, T, iv)

print(f"\nENTRY POSITION (Spot ${spot:,}, 50 DTE, 55% IV):")
print(f"  Put strike: ${put_strike:,}")
print(f"    Delta: {put_delta:.4f}")
print(f"    Gamma: {put_gamma:.10f}")
print(f"    Gamma * S: {put_gamma * spot:.6f}")
print(f"    Gamma * S^2 / 10000: {put_gamma * spot * spot / 10000:.2f}")
print(f"  Call strike: ${call_strike:,}")
print(f"    Delta: {call_delta:.4f}")
print(f"    Gamma: {call_gamma:.10f}")
print(f"    Gamma * S: {call_gamma * spot:.6f}")
print(f"    Gamma * S^2 / 10000: {call_gamma * spot * spot / 10000:.2f}")
print(f"  Position gamma (sum): {(put_gamma + call_gamma) * spot * spot / 10000:.2f}")

# Scenario 2: Price moved toward call strike
print(f"\n" + "-"*70)
spot2 = 95000  # Price rallied
dte2 = 40
T2 = dte2 / 365

put_delta2 = abs(bs_delta(spot2, put_strike, T2, iv, 'put'))
call_delta2 = bs_delta(spot2, call_strike, T2, iv, 'call')
put_gamma2 = bs_gamma(spot2, put_strike, T2, iv)
call_gamma2 = bs_gamma(spot2, call_strike, T2, iv)

print(f"\nAFTER RALLY (Spot ${spot2:,}, 40 DTE):")
print(f"  Put strike: ${put_strike:,}")
print(f"    Delta: {put_delta2:.4f}")
print(f"    Gamma: {put_gamma2:.10f}")
print(f"    Gamma * S^2 / 10000: {put_gamma2 * spot2 * spot2 / 10000:.2f}")
print(f"  Call strike: ${call_strike:,}")
print(f"    Delta: {call_delta2:.4f}")
print(f"    Gamma: {call_gamma2:.10f}")
print(f"    Gamma * S^2 / 10000: {call_gamma2 * spot2 * spot2 / 10000:.2f}")
print(f"  Position gamma (sum): {(put_gamma2 + call_gamma2) * spot2 * spot2 / 10000:.2f}")

# Scenario 3: Very close to expiry, price at strike
print(f"\n" + "-"*70)
spot3 = 100000  # Price very close to call strike
dte3 = 7
T3 = dte3 / 365

put_delta3 = abs(bs_delta(spot3, put_strike, T3, iv, 'put'))
call_delta3 = bs_delta(spot3, call_strike, T3, iv, 'call')
put_gamma3 = bs_gamma(spot3, put_strike, T3, iv)
call_gamma3 = bs_gamma(spot3, call_strike, T3, iv)

print(f"\nNEAR EXPIRY + NEAR STRIKE (Spot ${spot3:,}, 7 DTE):")
print(f"  Put strike: ${put_strike:,}")
print(f"    Delta: {put_delta3:.4f}")
print(f"    Gamma: {put_gamma3:.10f}")
print(f"    Gamma * S^2 / 10000: {put_gamma3 * spot3 * spot3 / 10000:.2f}")
print(f"  Call strike: ${call_strike:,}")
print(f"    Delta: {call_delta3:.4f}")
print(f"    Gamma: {call_gamma3:.10f}")
print(f"    Gamma * S^2 / 10000: {call_gamma3 * spot3 * spot3 / 10000:.2f}")
print(f"  Position gamma (sum): {(put_gamma3 + call_gamma3) * spot3 * spot3 / 10000:.2f}")

# What gamma values would trigger at different thresholds
print(f"\n" + "="*70)
print("THRESHOLD ANALYSIS")
print("="*70)
print("\nThe backtest tested thresholds: 0, 50, 100, 150, 200, 300")
print(f"\nActual gamma values seen:")
print(f"  Entry (far OTM, 50 DTE):     ~{(put_gamma + call_gamma) * spot * spot / 10000:.1f}")
print(f"  After rally (still OTM):     ~{(put_gamma2 + call_gamma2) * spot2 * spot2 / 10000:.1f}")
print(f"  Near expiry, near strike:    ~{(put_gamma3 + call_gamma3) * spot3 * spot3 / 10000:.1f}")

print(f"\nCONCLUSION:")
print(f"  Gamma values at 0.17 delta, 50 DTE are ~1-5")
print(f"  They need to get to ~{put_gamma3 * spot3 * spot3 / 10000:.0f}+ to trigger threshold")
print(f"  But by then, DELTA has already exceeded 0.40 and triggered first!")

# Let's find what delta corresponds to what gamma
print(f"\n" + "="*70)
print("DELTA vs GAMMA RELATIONSHIP")
print("="*70)

spot = 85000
strike = 102000  # Call strike
iv = 0.55

for dte in [50, 40, 30, 21, 14, 7]:
    T = dte / 365
    delta = bs_delta(spot, strike, T, iv, 'call')
    gamma = bs_gamma(spot, strike, T, iv)
    dollar_gamma = gamma * spot * spot / 10000
    print(f"  {dte:2d} DTE: delta={delta:.3f}, gamma={dollar_gamma:6.2f}")

print(f"\nAs price moves toward strike (spot -> $95k -> $100k):")
for spot_test in [85000, 90000, 95000, 100000]:
    T = 30 / 365
    delta = bs_delta(spot_test, strike, T, iv, 'call')
    gamma = bs_gamma(spot_test, strike, T, iv)
    dollar_gamma = gamma * spot_test * spot_test / 10000
    print(f"  Spot ${spot_test:,}: delta={delta:.3f}, gamma={dollar_gamma:6.2f}")
