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
            payload = json.loads(encrypted_json)
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
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ]
        )
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
        await self.page.goto("https://www.tiktok.com/login/phone-or-email", timeout=60000)
        await self.page.wait_for_timeout(3000)
        try:
            email_tab = self.page.locator("div[class*='tab']:has-text('Use phone / email')")
            if await email_tab.count() > 0:
                await email_tab.click()
                await self.page.wait_for_timeout(2000)
        except Exception:
            pass
        try:
            await self.page.fill("input[name='username']", username)
            await self.page.wait_for_timeout(500)
            await self.page.fill("input[type='password']", password)
            await self.page.wait_for_timeout(500)
            await self.page.click("button[type='submit']")
            await self.page.wait_for_timeout(5000)
        except Exception:
            try:
                await self.page.fill("input[placeholder*='phone']", username)
                await self.page.fill("input[placeholder*='password']", password)
                await self.page.click("button[type='submit']")
                await self.page.wait_for_timeout(5000)
            except Exception as e:
                raise Exception(f"Login failed: {e}")
        await self.page.wait_for_timeout(3000)
        current_url = self.page.url
        if "login" in current_url.lower() or "auth" in current_url.lower():
            raise Exception("Login failed — still on login page. CAPTCHA may be required.")
        print(f"  [+] Logged in successfully. URL: {current_url}")
        return True

    # ============================================================
    # CHECK & SEND MESSAGE
    # ============================================================
    async def check_and_send_message(self, sender_username, sender_password, friend_username, message):
        print(f"  [~] Logging in as {sender_username}...")
        await self.login(sender_username, sender_password)
        print(f"  [~] Navigating to messages...")
        await self.page.goto("https://www.tiktok.com/messages", timeout=30000)
        await self.page.wait_for_timeout(4000)
        try:
            await self.page.wait_for_selector("div[class*='message'], div[class*='dialog']", timeout=10000)
        except Exception:
            pass
        print(f"  [~] Finding conversation with {friend_username}...")
        try:
            conversation = self.page.locator(f"a[href*='{friend_username}']").first
            if await conversation.count() == 0:
                conversation = self.page.locator(f"div[class*='dialog']:has-text('{friend_username}')").first
            if await conversation.count() == 0:
                conversation = self.page.locator(f"div[class*='item']:has-text('{friend_username}')").first
            if await conversation.count() > 0:
                await conversation.click()
                await self.page.wait_for_timeout(3000)
                print(f"  [~] Conversation opened.")
            else:
                print(f"  [!] Could not find conversation with {friend_username}")
        except Exception as e:
            print(f"  [!] Error finding conversation: {e}")
        tiktok_streak = await self._scrape_streak_from_tiktok()
        if tiktok_streak and tiktok_streak > self.status.get("streak_count", 0):
            self.status["streak_count"] = tiktok_streak
            print(f"  [✓] Scraped real streak from TikTok: {tiktok_streak} days")

        print(f"  [~] Checking today's messages...")
        today_sent = await self._check_today_messages(sender_username)
        if today_sent:
            print(f"  [✓] Message already sent today from {sender_username}. Skipping.")
            return {"action": "skipped", "reason": "already_sent"}
        print(f"  [~] Sending message: {message}")
        try:
            textarea = self.page.locator("div[contenteditable='true']").first
            if await textarea.count() == 0:
                textarea = self.page.locator("textarea").first
            if await textarea.count() == 0:
                textarea = self.page.locator("input[type='text']").first
            if await textarea.count() > 0:
                await textarea.click()
                await self.page.wait_for_timeout(500)
                await textarea.fill(message)
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
            fire_idx = page_text.find('\U0001f525')
            if fire_idx >= 0:
                chunk = page_text[max(0,fire_idx-10):fire_idx+30]
                nums = re.findall(r'\d+', chunk)
                if nums:
                    num = int(nums[0])
                    if 1 <= num <= 9999:
                        return num
        except Exception as e:
            print(f"    [!] Streak scrape error: {e}")
        return None

    async def _check_today_messages(self, username):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        try:
            page_text = await self.page.inner_text("body")
            if today in page_text:
                print(f"    Today's date found in conversation.")
                return True
            recent_text = page_text[-2000:] if len(page_text) > 2000 else page_text
            message_indicators = ["sent", "message", today, "pm", "am"]
            matches = sum(1 for ind in message_indicators if ind.lower() in recent_text.lower())
            if matches >= 2:
                print(f"    Recent activity indicators found ({matches}/5).")
                return True
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

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        self.status["today"]["date"] = today
        self.status["today"]["user_a_sent"] = False
        self.status["today"]["user_b_sent"] = False
        self.status["today"]["bot_intervened"] = False
        self.status["today"]["bot_action"] = "none"

        if enabled_a:
            print(f"\n{'='*50}")
            print(f"[Account A: {creds_a['username']}]")
            result_a = await self.check_and_send_message(
                creds_a["username"], creds_a["password"],
                friend_a, msg_a
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
            result_b = await self.check_and_send_message(
                creds_b["username"], creds_b["password"],
                friend_b, msg_b
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
        if both_sent and not self.status["today"]["bot_intervened"]:
            if datetime.datetime.now().strftime("%Y-%m-%d") != self.status.get("last_update", "")[:10]:
                self.status["streak_count"] += 1

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
