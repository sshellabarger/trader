#!/usr/bin/env python3
"""
Diagnostic script to check why bot isn't trading
"""
import sys
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, 'src')

from trading_bot.broker_alpaca import AlpacaBroker
from trading_bot.state import StateStore
import json


def check_market_status():
    """Check if market is open"""
    print("\n" + "=" * 60)
    print("1. MARKET STATUS CHECK")
    print("=" * 60)

    broker = AlpacaBroker(
        key=os.getenv('ALPACA_API_KEY_ID'),
        secret=os.getenv('ALPACA_API_SECRET_KEY'),
        paper=True
    )

    clock = broker.get_clock()
    if clock:
        is_open = clock.get('is_open', False)
        next_open = clock.get('next_open', '')
        next_close = clock.get('next_close', '')

        print(f"Market Open: {is_open}")
        print(f"Next Open: {next_open}")
        print(f"Next Close: {next_close}")

        if not is_open:
            print("\n⚠️  MARKET IS CLOSED")
            print("   Stock trading only happens during market hours (9:30 AM - 4:00 PM ET)")
            return False
        else:
            print("\n✓ Market is open - stocks can trade")
            return True
    else:
        print("✗ Failed to get market clock")
        return None


def check_account():
    """Check account status"""
    print("\n" + "=" * 60)
    print("2. ACCOUNT STATUS CHECK")
    print("=" * 60)

    broker = AlpacaBroker(
        key=os.getenv('ALPACA_API_KEY_ID'),
        secret=os.getenv('ALPACA_API_SECRET_KEY'),
        paper=True
    )

    account = broker.get_account()
    if account:
        equity = float(account.get('equity', 0))
        buying_power = float(account.get('buying_power', 0))
        cash = float(account.get('cash', 0))

        print(f"Equity: ${equity:,.2f}")
        print(f"Buying Power: ${buying_power:,.2f}")
        print(f"Cash: ${cash:,.2f}")

        if buying_power < 100:
            print("\n⚠️  LOW BUYING POWER")
            print("   You may not have enough buying power to trade")
            return False
        else:
            print("\n✓ Sufficient buying power")
            return True
    else:
        print("✗ Failed to get account info")
        return None


def check_positions():
    """Check current positions"""
    print("\n" + "=" * 60)
    print("3. POSITIONS CHECK")
    print("=" * 60)

    broker = AlpacaBroker(
        key=os.getenv('ALPACA_API_KEY_ID'),
        secret=os.getenv('ALPACA_API_SECRET_KEY'),
        paper=True
    )

    positions = broker.list_positions()
    print(f"Current Positions: {len(positions)}")

    if positions:
        print("\nOpen Positions:")
        for pos in positions:
            symbol = pos.get('symbol')
            qty = pos.get('qty')
            avg_price = float(pos.get('avg_entry_price', 0))
            current_price = float(pos.get('current_price', 0))
            unrealized_pl = float(pos.get('unrealized_pl', 0))
            print(f"  {symbol}: {qty} shares @ ${avg_price:.2f} "
                  f"(current: ${current_price:.2f}, P/L: ${unrealized_pl:.2f})")

    return len(positions)


def check_settings():
    """Check bot settings"""
    print("\n" + "=" * 60)
    print("4. SETTINGS CHECK")
    print("=" * 60)

    state = StateStore('./data/trading_bot.db')

    # Check strategies
    strategies_str = state.get_kv('strategies')
    if strategies_str:
        strategies = json.loads(strategies_str)
        print("\nStrategies:")
        for key, val in strategies.items():
            status = "✓ ENABLED" if val else "✗ DISABLED"
            print(f"  {key}: {status}")
    else:
        print("\nNo strategies configured - using defaults")

    # Check thresholds
    thresholds_str = state.get_kv('thresholds')
    if thresholds_str:
        thresholds = json.loads(thresholds_str)
        print("\nThresholds:")
        for key, val in thresholds.items():
            print(f"  {key}: {val}")
    else:
        print("\nNo thresholds configured - using strategy-specific defaults")
        print("  (See strategy_configs.py for strategy-specific entry/exit thresholds)")

    # Check crypto
    crypto_str = state.get_kv('crypto')
    if crypto_str:
        crypto = json.loads(crypto_str)
        enabled = crypto.get('enabled', False)
        universe = crypto.get('universe', [])
        print(f"\nCrypto:")
        print(f"  Enabled: {enabled}")
        print(f"  Universe: {universe}")

        if enabled:
            print("\n✓ Crypto enabled - bot can trade 24/7")
            return True
    else:
        print("\nCrypto not configured")

    return False


def check_candidates():
    """Check if bot has identified any candidates"""
    print("\n" + "=" * 60)
    print("5. CANDIDATES CHECK")
    print("=" * 60)

    state = StateStore('./data/trading_bot.db')
    candidates_str = state.get_kv('candidates')

    if candidates_str:
        try:
            candidates = json.loads(candidates_str)
            print(f"\nFound {len(candidates)} candidates")

            if candidates:
                print("\nTop 5 Candidates:")
                for i, cand in enumerate(candidates[:5], 1):
                    symbol = cand.get('symbol', 'N/A')
                    score = cand.get('final_score', cand.get('score', 0))
                    print(f"  {i}. {symbol}: score={score:.3f}")

                # Check entry threshold (now strategy-specific)
                print("\n⚠️  Checking if scores meet entry threshold...")
                print(f"   NOTE: Entry thresholds are now strategy-specific (see strategy_configs.py)")
                print(f"   Default entry threshold for most strategies: ~0.62")

                top_score = candidates[0].get('final_score', candidates[0].get('score', 0))
                # Use a reasonable default for diagnostic purposes
                entry_threshold = 0.62

                if top_score < entry_threshold:
                    print(f"   ⚠️  Top score ({top_score:.3f}) < typical threshold ({entry_threshold})")
                    print("   Bot may not trade if candidates don't meet strategy-specific thresholds")
                    print("\n   TO FIX: Adjust strategy-specific thresholds in strategy_configs.py")
                    return False
                else:
                    print(f"   ✓ Top score ({top_score:.3f}) >= typical threshold ({entry_threshold})")
                    return True
            else:
                print("\n⚠️  No candidates found")
                return False
        except Exception as e:
            print(f"Error parsing candidates: {e}")
            return None
    else:
        print("\n⚠️  No candidates data - bot may still be initializing")
        print("   Wait 1-2 minutes and check the logs")
        return None


def check_recent_trades():
    """Check for recent trades"""
    print("\n" + "=" * 60)
    print("6. RECENT TRADES CHECK")
    print("=" * 60)

    state = StateStore('./data/trading_bot.db')

    try:
        # Query recent trades
        import sqlite3
        conn = sqlite3.connect('./data/trading_bot.db')
        cursor = conn.execute("""
            SELECT timestamp, symbol, side, qty, price 
            FROM trades 
            ORDER BY timestamp DESC 
            LIMIT 10
        """)

        trades = cursor.fetchall()
        conn.close()

        if trades:
            print(f"\nFound {len(trades)} recent trades:")
            for trade in trades:
                timestamp, symbol, side, qty, price = trade
                print(f"  {timestamp}: {side.upper()} {qty} {symbol} @ ${price:.2f}")
            return True
        else:
            print("\n⚠️  No trades recorded yet")
            return False
    except Exception as e:
        print(f"Error checking trades: {e}")
        return None


def check_logs():
    """Check recent log entries"""
    print("\n" + "=" * 60)
    print("7. RECENT LOG ENTRIES")
    print("=" * 60)

    try:
        with open('data/app.log', 'r') as f:
            lines = f.readlines()
            recent = lines[-20:]  # Last 20 lines

            print("\nLast 20 log entries:")
            for line in recent:
                print(line.rstrip())
    except FileNotFoundError:
        print("Log file not found at data/app.log")
    except Exception as e:
        print(f"Error reading logs: {e}")


def check_risk_limits():
    """Check risk management limits"""
    print("\n" + "=" * 60)
    print("8. RISK LIMITS CHECK")
    print("=" * 60)

    state = StateStore('./data/trading_bot.db')

    # Check risk settings
    risk_str = state.get_kv('risk')
    if risk_str:
        risk = json.loads(risk_str)
        print("\nRisk Settings:")
        for key, val in risk.items():
            print(f"  {key}: {val}")

        max_positions = risk.get('max_positions', 10)
        print(f"\n  Max positions allowed: {max_positions}")

        broker = AlpacaBroker(
            key=os.getenv('ALPACA_API_KEY_ID'),
            secret=os.getenv('ALPACA_API_SECRET_KEY'),
            paper=True
        )
        positions = broker.list_positions()

        if len(positions) >= max_positions:
            print(f"  ⚠️  At max positions ({len(positions)}/{max_positions})")
            print("  Bot won't open new positions until some close")
            return False
    else:
        print("No risk settings configured - using defaults")

    return True


def main():
    print("=" * 60)
    print("TRADING BOT DIAGNOSTICS")
    print("=" * 60)
    print(f"Time: {datetime.now()}")

    results = {}

    results['market'] = check_market_status()
    results['account'] = check_account()
    results['positions'] = check_positions()
    results['crypto'] = check_settings()
    results['candidates'] = check_candidates()
    results['trades'] = check_recent_trades()
    results['risk'] = check_risk_limits()

    check_logs()

    # Summary
    print("\n" + "=" * 60)
    print("DIAGNOSIS SUMMARY")
    print("=" * 60)

    issues = []

    if results['market'] == False and results['crypto'] == False:
        issues.append("❌ Market is CLOSED and crypto is DISABLED")
        issues.append("   → Bot can only trade stocks during market hours (9:30 AM - 4:00 PM ET)")
        issues.append("   → OR enable crypto to trade 24/7")

    if results['candidates'] == False:
        issues.append("❌ No candidates meet entry threshold")
        issues.append("   → Lower entry threshold or wait for better opportunities")

    if results['candidates'] is None:
        issues.append("⚠️  Bot is still gathering data")
        issues.append("   → Wait 2-5 minutes after startup")

    if results['account'] == False:
        issues.append("❌ Insufficient buying power")
        issues.append("   → Check account balance")

    if results['risk'] == False:
        issues.append("❌ At maximum position limit")
        issues.append("   → Close some positions or increase max_positions")

    if issues:
        print("\n🔍 ISSUES FOUND:\n")
        for issue in issues:
            print(issue)

        print("\n" + "=" * 60)
        print("QUICK FIXES:")
        print("=" * 60)
        print("\n1. To adjust entry thresholds (make bot trade more):")
        print("   Edit strategy_configs.py and adjust 'entry_threshold' for specific strategies")

        print("\n2. To enable crypto trading 24/7:")
        print("   curl -X POST http://localhost:8000/api/settings -H 'Content-Type: application/json' \\")
        print(
            "     -d '{\"strategies\":{\"crypto\":true},\"crypto\":{\"enabled\":true,\"universe\":[\"BTC/USD\",\"ETH/USD\"]}}'")

        print("\n3. To increase max positions:")
        print("   curl -X POST http://localhost:8000/api/settings -H 'Content-Type: application/json' \\")
        print("     -d '{\"risk\":{\"max_positions\":20}}'")
    else:
        print("\n✓ Everything looks good!")
        print("  Bot should be trading soon if opportunities arise.")

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()