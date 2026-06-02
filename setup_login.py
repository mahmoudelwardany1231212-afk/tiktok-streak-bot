"""
TikTok Streak Bot - Optional Diagnostic Tool
=============================================
This is an OPTIONAL tool to test TikTok login on your local machine.
It's NOT needed for setup - you can enter credentials directly
through the Web Dashboard at /setup.

Usage:
  python setup_login.py            (test login with a single account)
  python setup_login.py --full     (test both accounts from saved creds)

This tool helps you:
  - Verify your TikTok credentials work before deploying
  - Test that Playwright is installed correctly
  - Debug login issues locally
"""

import os
import sys
import getpass
import asyncio
from pathlib import Path

from streak_bot import TikTokStreakBot


async def test_login(bot, username, password, label):
    print(f"\n  [~] Testing login for {label} ({username})...")
    try:
        await bot._init_browser()
        await bot.login(username, password)
        print(f"  [✓] Login SUCCESSFUL for {label}! TikTok session works.")
        await bot.close()
        return True
    except Exception as e:
        print(f"  [✗] Login FAILED for {label}: {e}")
        return False


async def test_from_saved(account_key, enc_password, label):
    print(f"\n  [~] Testing {label} from saved credentials ({account_key})...")
    bot = TikTokStreakBot()
    try:
        creds = bot.load_credentials(account_key, enc_password)
        print(f"  [i] Loaded credentials for: {creds['username']}")
        return await test_login(bot, creds["username"], creds["password"], label)
    except FileNotFoundError:
        print(f"  [!] No saved credentials found for {account_key}")
        print(f"  [!] Set up credentials via the Dashboard first (/setup)")
        return False
    except Exception as e:
        print(f"  [✗] Error: {e}")
        return False


async def main():
    print("=" * 60)
    print("  TikTok Streak Bot - Login Diagnostic Tool")
    print("  =========================================")
    print()
    print("  This tool tests TikTok login locally.")
    print("  It's OPTIONAL - you can skip it and use the Dashboard.")
    print()

    if "--full" in sys.argv:
        enc_password = os.environ.get("ENCRYPTION_PASSWORD")
        if not enc_password:
            enc_password = getpass.getpass("  Enter ENCRYPTION_PASSWORD: ").strip()
            os.environ["ENCRYPTION_PASSWORD"] = enc_password

        await test_from_saved("user_a", enc_password, "Account A")
        await test_from_saved("user_b", enc_password, "Account B")
    else:
        print("  Quick test - enter credentials to test:")
        username = input("  TikTok Username: ").strip()
        password = getpass.getpass("  TikTok Password: ").strip()

        if not username or not password:
            print("  [!] No credentials provided.")
            sys.exit(1)

        bot = TikTokStreakBot()
        try:
            await test_login(bot, username, password, "Test Account")
        finally:
            await bot.close()

    print()
    print("=" * 60)
    print("  Done!")

    if "--full" not in sys.argv:
        print()
        print("  Tip: Run with --full to test both accounts from saved credentials")
        print("       python setup_login.py --full")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"\n  Error: {e}")
        sys.exit(1)
