import os
import json
import base64
import random
import re
import secrets
import datetime
import time
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


PBKDF2_ITERATIONS = 1_000_000
SALT_BYTES = 16


class TikTokStreakBot:
    def __init__(self, accounts_dir="accounts", config_path="config.json", status_path="status.json"):
        self.accounts_dir = Path(accounts_dir)
        self.config_path = Path(config_path)
        self.status_path = Path(status_path)
        self.config = self._load_json(self.config_path)
        self.status = self._load_json(self.status_path)
        self.browser = None
        self.context = None
        self.page = None

    # ============================================================
    # FILE HELPERS
    # ============================================================
    def _load_json(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_json(self, data, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ============================================================
    # SECURE ENCRYPTION  (random salt per encryption, 1M PBKDF2 iterations)
    # ============================================================
    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def encrypt_credentials(self, data: dict, password: str) -> str:
        salt = secrets.token_bytes(SALT_BYTES)
        key = self._derive_key(password, salt)
        f = Fernet(key)
        ciphertext = f.encrypt(json.dumps(data).encode())
        return json.dumps({
            "salt": base64.b64encode(salt).decode(),
            "data": ciphertext.decode(),
            "iterations": PBKDF2_ITERATIONS,
        })

    def decrypt_credentials(self, encrypted_json: str, password: str) -> dict:
        try:
            # Handle double-encoded JSON (legacy bug fix)
            if isinstance(encrypted_json, dict):
                payload = encrypted_json
            else:
                payload = json.loads(encrypted_json)
                # If the result is still a string, it was double-encoded
                if isinstance(payload, str):
                    payload = json.loads(payload)
            salt = base64.b64decode(payload["salt"])
            data = payload["data"].encode()
            key = self._derive_key(password, salt)
            f = Fernet(key)
            decrypted = f.decrypt(data)
            return json.loads(decrypted.decode())
        except Exception as e:
            raise ValueError(f"Decryption failed — wrong password or corrupted data: {e}")

    # ============================================================
    # FILE-BASED CREDENTIALS  (local testing only)
    # ============================================================
    def save_encrypted_credentials(self, account_key, tiktok_username, tiktok_password, encryption_password):
        data = {"username": tiktok_username, "password": tiktok_password}
        encrypted = self.encrypt_credentials(data, encryption_password)
        self.accounts_dir.mkdir(exist_ok=True)
        filepath = self.accounts_dir / f"{account_key}.enc"
        with open(filepath, "w") as f:
            f.write(encrypted)
        return filepath

    def load_credentials(self, account_key, encryption_password):
        filepath = self.accounts_dir / f"{account_key}.enc"
        if not filepath.exists():
            raise FileNotFoundError(f"Credentials file not found: {filepath}")
        with open(filepath, "r") as f:
            encrypted = f.read().strip()
        return self.decrypt_credentials(encrypted, encryption_password)

    # ============================================================
    # ENV-BASED CREDENTIALS  (GitHub Actions — secure)
    # ============================================================
    def load_credentials_from_env(self, account_key) -> dict:
        env_var = f"{account_key.upper()}_CREDS"
        encrypted = os.environ.get(env_var)
        if not encrypted:
            return self.load_credentials(account_key, os.environ.get("ENCRYPTION_PASSWORD", ""))
        enc_password = os.environ.get("ENCRYPTION_PASSWORD")
        if not enc_password:
            raise RuntimeError("ENCRYPTION_PASSWORD environment variable not set")
        return self.decrypt_credentials(encrypted, enc_password)

    # ============================================================
    # PLAYWRIGHT BROWSER
    # ============================================================
    async def _init_browser(self):
        import shutil
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        chromium_path = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        launch_kwargs = dict(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--single-process",
            ]
        )
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
        self.browser = await self.playwright.chromium.launch(**launch_kwargs)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Africa/Cairo",
        )
        try:
            from playwright_stealth import stealth_async
            self.page = await self.context.new_page()
            await stealth_async(self.page)
        except ImportError:
            self.page = await self.context.new_page()
        return self.page

    # ============================================================
    # TIKTOK LOGIN
    # ============================================================
    async def login(self, username, password):
        if not self.page:
            await self._init_browser()
            
        # --- COOKIE LOGIN BYPASS (CAPTCHA SOLUTION) ---
        if password.startswith("sessionid="):
            print("  [~] Using session cookie bypass instead of password...")
            session_id = password.replace("sessionid=", "").strip()
            await self.context.add_cookies([{
                "name": "sessionid",
                "value": session_id,
                "domain": ".tiktok.com",
                "path": "/"
            }])
            # Go directly to messages, skipping login page
            await self.page.goto("https://www.tiktok.com/messages", timeout=60000)
            await self.page.wait_for_timeout(3000)
            print(f"  [+] Logged in via cookie successfully.")
            return True

        # --- NORMAL PASSWORD LOGIN ---
        # Navigate directly to the email/username login tab
        await self.page.goto("https://www.tiktok.com/login/phone-or-email/email", timeout=60000)
        await self.page.wait_for_timeout(4000)
        
        # Try to click the "Log in with phone / email / username" option if we are on the main login screen
        try:
            btn = self.page.locator("div:has-text('Use phone / email / username')").last
            if await btn.count() > 0:
                await btn.click()
                await self.page.wait_for_timeout(2000)
            
            # Switch to 'Log in with email or username' link if it's visible
            email_link = self.page.locator("a:has-text('Log in with email or username')").first
            if await email_link.count() > 0:
                await email_link.click()
                await self.page.wait_for_timeout(2000)
        except:
            pass

        try:
            # Robust selectors for username/email field
            user_input = self.page.locator("input[name='username'], input[placeholder*='Email'], input[placeholder*='username'], input[type='text']").first
            await user_input.fill(username)
            await self.page.wait_for_timeout(500)
            
            # Robust selector for password field
            pass_input = self.page.locator("input[type='password']").first
            await pass_input.fill(password)
            await self.page.wait_for_timeout(500)
            
            # Submit button
            submit_btn = self.page.locator("button[type='submit'], button:has-text('Log in')").first
            await submit_btn.click()
            await self.page.wait_for_timeout(8000)
        except Exception as e:
            raise Exception(f"Login failed to locate input fields: {e}")
            
        await self.page.wait_for_timeout(3000)
        current_url = self.page.url
        if "login" in current_url.lower() or "auth" in current_url.lower():
            raise Exception("Login failed — still on login page. CAPTCHA may be required.")
        print(f"  [+] Logged in successfully. URL: {current_url}")
        return True

    # ============================================================
    # CHECK & SEND MESSAGE
    # ============================================================
    async def check_and_send_message(self, sender_username, sender_password, friend_username, message,
                                     already_sent_in_status=False):
        """
        already_sent_in_status: if True, skip sending (user already sent manually today).
        """
        print(f"  [~] Logging in as {sender_username}...")
        await self.login(sender_username, sender_password)

        # --- Scrape streak count from profile/chat ---
        print(f"  [~] Navigating to messages...")
        await self.page.goto("https://www.tiktok.com/messages", timeout=30000)
        await self.page.wait_for_timeout(4000)

        print(f"  [~] Finding conversation with {friend_username}...")
        opened = await self._open_conversation(friend_username)

        if opened:
            # Scrape streak count from the conversation
            tiktok_streak = await self._scrape_streak_from_tiktok()
            if tiktok_streak and tiktok_streak > self.status.get("streak_count", 0):
                self.status["streak_count"] = tiktok_streak
                print(f"  [✓] Scraped real streak from TikTok: {tiktok_streak} days")

        # Check if already sent today (via status.json — reliable)
        if already_sent_in_status:
            print(f"  [✓] Already marked as sent today in status. Skipping.")
            return {"action": "skipped", "reason": "already_sent"}

        # Secondary check: look at the actual message timestamps in the chat
        print(f"  [~] Checking today's messages...")
        if opened:
            today_sent = await self._check_today_messages_in_chat(sender_username)
            if today_sent:
                print(f"  [✓] Message already sent today from {sender_username}. Skipping.")
                return {"action": "skipped", "reason": "already_sent"}

        print(f"  [~] Sending message: {message}")
        result = await self._send_message(message)
        return result

    async def _open_conversation(self, friend_username):
        """Try multiple strategies to open the DM conversation with friend_username."""
        try:
            # Wait for the message list to appear
            try:
                await self.page.wait_for_selector(
                    "[class*='conversation'], [class*='message-list'], [class*='inbox']",
                    timeout=10000
                )
            except Exception:
                pass

            # Strategy 1: link containing username
            conversation = self.page.locator(f"a[href*='{friend_username}']").first
            if await conversation.count() > 0:
                await conversation.click()
                await self.page.wait_for_timeout(3000)
                print(f"  [✓] Conversation opened via link.")
                return True

            # Strategy 2: any element containing the username text
            conversation = self.page.locator(f"[class*='conversation']:has-text('{friend_username}')").first
            if await conversation.count() == 0:
                conversation = self.page.locator(f"[class*='inbox']:has-text('{friend_username}')").first
            if await conversation.count() == 0:
                conversation = self.page.locator(f"li:has-text('{friend_username}')").first
            if await conversation.count() > 0:
                await conversation.click()
                await self.page.wait_for_timeout(3000)
                print(f"  [✓] Conversation opened via text match.")
                return True

            # Strategy 3: navigate directly to DM URL
            await self.page.goto(f"https://www.tiktok.com/messages?username={friend_username}", timeout=20000)
            await self.page.wait_for_timeout(3000)
            print(f"  [~] Navigated to DM URL for {friend_username}")
            return True

        except Exception as e:
            print(f"  [!] Error finding conversation: {e}")
            return False

    async def _send_message(self, message):
        """Send a message in the currently open conversation."""
        try:
            # Try contenteditable div first (TikTok's primary input)
            textarea = self.page.locator("div[contenteditable='true']").first
            if await textarea.count() == 0:
                textarea = self.page.locator("textarea").first
            if await textarea.count() == 0:
                textarea = self.page.locator("input[type='text']").first

            if await textarea.count() > 0:
                await textarea.click()
                await self.page.wait_for_timeout(500)
                # Use type instead of fill for contenteditable
                await textarea.type(message, delay=50)
                await self.page.wait_for_timeout(1000)
                await self.page.keyboard.press("Enter")
                await self.page.wait_for_timeout(2000)
                print(f"  [✓] Message sent successfully!")
                return {"action": "sent", "message": message}
            else:
                print(f"  [!] Could not find message input!")
                return {"action": "failed", "reason": "no_input_found"}
        except Exception as e:
            print(f"  [✗] Failed to send message: {e}")
            return {"action": "failed", "reason": str(e)}

    async def _scrape_streak_from_tiktok(self):
        """Scrape the streak count from the TikTok messages page."""
        try:
            page_text = await self.page.inner_text("body")
            patterns = [
                r'(\d+)\s*[- ]?day\s*streak',
                r'streak[:\s]*(\d+)',
                r'(\d+)\s*.\u064a\u0648\u0645',
                r'\u0627\u0633\u062a\u0631\u064a\u0643[:\s]*(\d+)',
            ]
            for p in patterns:
                match = re.search(p, page_text, re.IGNORECASE)
                if match:
                    num = int(match.group(1))
                    if 1 <= num <= 9999:
                        return num
            # Look for fire emoji + adjacent number (common TikTok streak display)
            fire_idx = page_text.find('\U0001f525')
            if fire_idx >= 0:
                chunk = page_text[max(0, fire_idx - 10):fire_idx + 30]
                nums = re.findall(r'\d+', chunk)
                if nums:
                    num = int(nums[0])
                    if 1 <= num <= 9999:
                        return num
        except Exception as e:
            print(f"    [!] Streak scrape error: {e}")
        return None

    async def _check_today_messages_in_chat(self, sender_username):
        """
        Check whether the sender already sent a message today by reading
        the actual message timestamps visible in the open chat window.
        Returns True if we're confident a message was already sent today.
        """
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        today_alt = datetime.datetime.now().strftime("%m/%d/%Y")
        today_short = datetime.datetime.now().strftime("%-m/%-d")  # e.g. 6/2
        now = datetime.datetime.now()

        try:
            # Look for timestamp elements in the chat
            timestamp_selectors = [
                "[class*='timestamp']",
                "[class*='time']",
                "[class*='date']",
                "time",
            ]
            for sel in timestamp_selectors:
                els = self.page.locator(sel)
                count = await els.count()
                for i in range(min(count, 20)):
                    try:
                        txt = await els.nth(i).inner_text()
                        txt = txt.strip()
                        # TikTok shows "Today", relative times like "2:30 PM", or dates
                        if "today" in txt.lower():
                            print(f"    [~] Found 'Today' timestamp in chat.")
                            return True
                        if today in txt or today_alt in txt:
                            print(f"    [~] Found today's date in chat timestamp.")
                            return True
                        # Match time-only strings (e.g. "2:30 PM") — only if within today
                        if re.match(r'^\d{1,2}:\d{2}\s*(AM|PM)?$', txt, re.IGNORECASE):
                            # A raw time like "2:30 PM" means it was sent today
                            print(f"    [~] Found today's time-only timestamp: {txt}")
                            return True
                    except Exception:
                        continue

            print(f"    No clear today activity detected.")
            return False
        except Exception as e:
            print(f"    Error checking messages: {e}")
            return False

    # ============================================================
    # SMART RUN
    # ============================================================
    async def run_smart(self, encryption_password=None, use_env=True):
        """Run the smart streak bot.

        Priority for loading credentials:
          1. Environment variables (GitHub Secrets) — if use_env=True
          2. Local encrypted files — fallback
        """
        results = {}
        account_a = self.config["accounts"]["user_a"]
        account_b = self.config["accounts"]["user_b"]
        friend_a = account_a["friend_username"]
        friend_b = account_b["friend_username"]
        msgs_a = account_a["messages_pool"]
        msgs_b = account_b["messages_pool"]
        mode_a = account_a["send_mode"]
        mode_b = account_b["send_mode"]
        custom_a = account_a["custom_message"]
        custom_b = account_b["custom_message"]
        enabled_a = account_a["bot_enabled"]
        enabled_b = account_b["bot_enabled"]

        if use_env:
            creds_a = self.load_credentials_from_env("user_a")
            creds_b = self.load_credentials_from_env("user_b")
        else:
            pw = encryption_password or os.environ.get("ENCRYPTION_PASSWORD", "")
            creds_a = self.load_credentials("user_a", pw)
            creds_b = self.load_credentials("user_b", pw)

        msg_a = custom_a if custom_a and mode_a == "custom" else (random.choice(msgs_a) if msgs_a else "\U0001f525")
        msg_b = custom_b if custom_b and mode_b == "custom" else (random.choice(msgs_b) if msgs_b else "\U0001f525")

        # FIX: Only reset today's status if the date has changed — preserve manual marks
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if self.status.get("today", {}).get("date") != today:
            print(f"  [~] New day detected, resetting today's status.")
            self.status["today"] = {
                "date": today,
                "user_a_sent": False,
                "user_b_sent": False,
                "bot_intervened": False,
                "bot_action": "none",
            }
        else:
            # Same day — keep existing sent flags (don't wipe manual marks)
            self.status["today"].setdefault("user_a_sent", False)
            self.status["today"].setdefault("user_b_sent", False)
            self.status["today"].setdefault("bot_intervened", False)
            self.status["today"].setdefault("bot_action", "none")
            print(f"  [~] Same day run. Preserving existing status: a_sent={self.status['today']['user_a_sent']}, b_sent={self.status['today']['user_b_sent']}")

        if enabled_a:
            print(f"\n{'='*50}")
            print(f"[Account A: {creds_a['username']}]")
            # Pass the current known status so the bot doesn't re-send if already marked
            already_a = self.status["today"]["user_a_sent"]
            result_a = await self.check_and_send_message(
                creds_a["username"], creds_a["password"],
                friend_a, msg_a,
                already_sent_in_status=already_a
            )
            results["user_a"] = result_a
            if result_a["action"] == "sent":
                self.status["today"]["bot_intervened"] = True
                self.status["today"]["bot_action"] = "sent_from_a"
                self.status["today"]["user_a_sent"] = True
            elif result_a["action"] == "skipped":
                self.status["today"]["user_a_sent"] = True
        else:
            print(f"\n[Account A: Bot is DISABLED in config]")
            results["user_a"] = {"action": "disabled"}

        await self._close_browser()

        if enabled_b:
            await self._init_browser()
            print(f"\n{'='*50}")
            print(f"[Account B: {creds_b['username']}]")
            already_b = self.status["today"]["user_b_sent"]
            result_b = await self.check_and_send_message(
                creds_b["username"], creds_b["password"],
                friend_b, msg_b,
                already_sent_in_status=already_b
            )
            results["user_b"] = result_b
            if result_b["action"] == "sent":
                self.status["today"]["bot_intervened"] = True
                self.status["today"]["bot_action"] = "sent_from_b"
                self.status["today"]["user_b_sent"] = True
            elif result_b["action"] == "skipped":
                self.status["today"]["user_b_sent"] = True
        else:
            print(f"\n[Account B: Bot is DISABLED in config]")
            results["user_b"] = {"action": "disabled"}

        both_sent = self.status["today"]["user_a_sent"] and self.status["today"]["user_b_sent"]
        if both_sent:
            if datetime.datetime.now().strftime("%Y-%m-%d") != self.status.get("last_update", "")[:10]:
                self.status["streak_count"] = self.status.get("streak_count", 0) + 1

        self.status["last_update"] = datetime.datetime.now().isoformat()
        self._save_json(self.status, self.status_path)

        print(f"\n{'='*50}")
        print(f"STREAK STATUS: Day {self.status['streak_count']}")
        print(f"User A sent: {self.status['today']['user_a_sent']}")
        print(f"User B sent: {self.status['today']['user_b_sent']}")
        print(f"Bot intervened: {self.status['today']['bot_intervened']}")
        print(f"{'='*50}")
        return results

    async def _close_browser(self):
        if self.page:
            await self.page.close()
            self.page = None
        if self.context:
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None

    async def close(self):
        await self._close_browser()
        if hasattr(self, "playwright") and self.playwright:
            await self.playwright.stop()


if __name__ == "__main__":
    import asyncio
    import sys
    suffix = "_CREDS"
    use_env = os.environ.get(f"USER_A{suffix}") is not None
    if not use_env and not os.environ.get("ENCRYPTION_PASSWORD"):
        print("ERROR: Set ENCRYPTION_PASSWORD environment variable (or USER_A_CREDS for env mode)")
        sys.exit(1)
    bot = TikTokStreakBot()
    asyncio.run(bot.run_smart(use_env=use_env))
