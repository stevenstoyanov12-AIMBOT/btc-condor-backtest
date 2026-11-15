"""
Delta Damage Analysis
Shows exactly how much you lose at each delta level using Black-Scholes
"""
import math
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

def gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0
    D1 = d1(S, K, T, r, sigma)
    return norm.pdf(D1) / (S * sigma * math.sqrt(T))


def analyze_delta_damage():
    """
    Analyze how much damage occurs at each delta level
    """
    # Parameters
    btc_price = 68000  # Current BTC price
    iv = 0.50  # 50% IV
    r = 0.05  # Risk-free rate
    dte = 50  # Days to expiry
    T = dte / 365
    
    # Strikes at 8% OTM
    put_strike = round(btc_price * 0.92 / 1000) * 1000  # 62000
    call_strike = round(btc_price * 1.08 / 1000) * 1000  # 74000
    
    print("="*100)
    print("DELTA DAMAGE ANALYSIS - SHORT STRANGLE")
    print("="*100)
    print(f"\nSetup:")
    print(f"  BTC Price: ${btc_price:,}")
    print(f"  Put Strike: ${put_strike:,} ({(put_strike/btc_price-1)*100:.1f}% OTM)")
    print(f"  Call Strike: ${call_strike:,} ({(call_strike/btc_price-1)*100:.1f}% OTM)")
    print(f"  IV: {iv*100:.0f}%")
    print(f"  DTE: {dte}")
    
    # Entry premium
    entry_put_premium = put_price(btc_price, put_strike, T, r, iv)
    entry_call_premium = call_price(btc_price, call_strike, T, r, iv)
    entry_total = entry_put_premium + entry_call_premium
    
    entry_put_delta = abs(put_delta(btc_price, put_strike, T, r, iv))
    entry_call_delta = call_delta(btc_price, call_strike, T, r, iv)
    entry_put_theta = abs(put_theta(btc_price, put_strike, T, r, iv))
    entry_call_theta = abs(call_theta(btc_price, call_strike, T, r, iv))
    entry_total_theta = entry_put_theta + entry_call_theta
    
    print(f"\nAt Entry (BTC ${btc_price:,}):")
    print(f"  Total Premium Received: ${entry_total:,.2f}")
    print(f"  Put Delta: {entry_put_delta:.3f}")
    print(f"  Call Delta: {entry_call_delta:.3f}")
    print(f"  Daily Theta: ${entry_total_theta:,.2f}")
    
    # Analyze PUT side (BTC dropping)
    print("\n" + "="*100)
    print("PUT SIDE - BTC DROPPING")
    print("="*100)
    print(f"\n{'BTC Price':<12} {'Move':<10} {'Put Delta':<12} {'Put Premium':<14} {'Loss':<12} {'% of Entry':<12} {'Days Theta':<12}")
    print("-"*100)
    
    # Test prices from entry down to strike
    test_prices_down = list(range(btc_price, put_strike - 1000, -1000))
    
    for price in test_prices_down:
        move = price - btc_price
        
        current_put_premium = put_price(price, put_strike, T, r, iv)
        current_call_premium = call_price(price, call_strike, T, r, iv)
        current_total = current_put_premium + current_call_premium
        
        current_put_delta = abs(put_delta(price, put_strike, T, r, iv))
        
        # Loss = what we have to pay to close - what we received
        loss = current_total - entry_total
        loss_percent = (loss / entry_total) * 100
        days_theta = loss / entry_total_theta if entry_total_theta > 0 else 0
        
        print(f"${price:<11,} ${move:<9,} {current_put_delta:<12.3f} ${current_put_premium:<13,.2f} ${loss:<11,.2f} {loss_percent:<11.1f}% {days_theta:<12.1f}")
    
    # Analyze CALL side (BTC rising)
    print("\n" + "="*100)
    print("CALL SIDE - BTC RISING")
    print("="*100)
    print(f"\n{'BTC Price':<12} {'Move':<10} {'Call Delta':<12} {'Call Premium':<14} {'Loss':<12} {'% of Entry':<12} {'Days Theta':<12}")
    print("-"*100)
    
    test_prices_up = list(range(btc_price, call_strike + 1000, 1000))
    
    for price in test_prices_up:
        move = price - btc_price
        
        current_put_premium = put_price(price, put_strike, T, r, iv)
        current_call_premium = call_price(price, call_strike, T, r, iv)
        current_total = current_put_premium + current_call_premium
        
        current_call_delta = call_delta(price, call_strike, T, r, iv)
        
        loss = current_total - entry_total
        loss_percent = (loss / entry_total) * 100
        days_theta = loss / entry_total_theta if entry_total_theta > 0 else 0
        
        print(f"${price:<11,} +${move:<8,} {current_call_delta:<12.3f} ${current_call_premium:<13,.2f} ${loss:<11,.2f} {loss_percent:<11.1f}% {days_theta:<12.1f}")
    
    # Summary by delta level
    print("\n" + "="*100)
    print("SUMMARY: LOSS AT EACH DELTA LEVEL")
    print("="*100)
    print(f"\n{'Delta':<10} {'BTC Price':<14} {'Move from Entry':<16} {'Loss':<14} {'% Premium':<12} {'Days Theta':<12}")
    print("-"*100)
    
    # Find prices where delta = 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60
    target_deltas = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    
    print("\nPUT SIDE (BTC dropping):")
    for target_d in target_deltas:
        # Binary search for price where put delta = target
        low, high = put_strike - 5000, btc_price
        while high - low > 10:
            mid = (low + high) / 2
            d = abs(put_delta(mid, put_strike, T, r, iv))
            if d < target_d:
                high = mid
            else:
                low = mid
        
        price = (low + high) / 2
        move = price - btc_price
        
        current_put = put_price(price, put_strike, T, r, iv)
        current_call = call_price(price, call_strike, T, r, iv)
        current_total = current_put + current_call
        
        loss = current_total - entry_total
        loss_pct = (loss / entry_total) * 100
        days = loss / entry_total_theta
        
        print(f"{target_d:<10.2f} ${price:<13,.0f} ${move:<15,.0f} ${loss:<13,.2f} {loss_pct:<11.1f}% {days:<12.1f}")
    
    print("\nCALL SIDE (BTC rising):")
    for target_d in target_deltas:
        # Binary search for price where call delta = target
        low, high = btc_price, call_strike + 5000
        while high - low > 10:
            mid = (low + high) / 2
            d = call_delta(mid, call_strike, T, r, iv)
            if d < target_d:
                low = mid
            else:
                high = mid
        
        price = (low + high) / 2
        move = price - btc_price
        
        current_put = put_price(price, put_strike, T, r, iv)
        current_call = call_price(price, call_strike, T, r, iv)
        current_total = current_put + current_call
        
        loss = current_total - entry_total
        loss_pct = (loss / entry_total) * 100
        days = loss / entry_total_theta
        
        print(f"{target_d:<10.2f} ${price:<13,.0f} +${move:<14,.0f} ${loss:<13,.2f} {loss_pct:<11.1f}% {days:<12.1f}")
    
    # Final recommendation
    print("\n" + "="*100)
    print("ANALYSIS")
    print("="*100)
    print(f"""
Key Findings:
- Entry premium: ${entry_total:,.2f}
- Daily theta: ${entry_total_theta:,.2f}
- Entry put delta: {entry_put_delta:.3f}
- Entry call delta: {entry_call_delta:.3f}

The 'Days Theta' column shows how many days of theta you need to recover the loss.

If you exit at delta 0.40 and loss = 5 days theta:
  - You need to hold 5 days just to break even
  - Plus 4% fees on close/reopen
  
If you exit at delta 0.50 and loss = 10 days theta:
  - More loss, but fewer trades = fewer fees
  - Risk: delta can accelerate fast past 0.50

RECOMMENDATION: Find the delta where (loss + fees) < (theta earned if held)
""")


if __name__ == "__main__":
    analyze_delta_damage()
