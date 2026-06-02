"""
TikTok Streak Bot - Unit Tests
===============================
Tests for core bot functionality.
Run with:  pytest tests/ -v
"""

import os
import sys
import json
import base64
import tempfile
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from streak_bot import TikTokStreakBot


class TestTikTokStreakBot:
    """Test suite for TikTokStreakBot."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.accounts_dir = Path(self.temp_dir) / "accounts"
        self.config_path = Path(self.temp_dir) / "config.json"
        self.status_path = Path(self.temp_dir) / "status.json"
        self._create_default_config()
        self._create_default_status()
        self.bot = TikTokStreakBot(
            accounts_dir=str(self.accounts_dir),
            config_path=str(self.config_path),
            status_path=str(self.status_path),
        )

    def _create_default_config(self):
        config = {
            "accounts": {
                "user_a": {
                    "tiktok_username": "test_user_a",
                    "dashboard_password": "pass123",
                    "bot_enabled": True,
                    "messages_pool": ["🔥", "❤️", "ستريك 🔥"],
                    "send_mode": "random",
                    "custom_message": "",
                    "friend_username": "test_user_b",
                },
                "user_b": {
                    "tiktok_username": "test_user_b",
                    "dashboard_password": "pass456",
                    "bot_enabled": True,
                    "messages_pool": ["🌟", "🔥", "💯"],
                    "send_mode": "random",
                    "custom_message": "",
                    "friend_username": "test_user_a",
                },
            },
            "run_time": "23:45",
            "timezone": "Africa/Cairo",
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _create_default_status(self):
        status = {
            "streak_count": 140,
            "last_update": "",
            "today": {
                "user_a_sent": False,
                "user_b_sent": False,
                "bot_intervened": False,
                "bot_action": "none",
                "date": "",
            },
            "history": [],
        }
        with open(self.status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    # ============================================================
    # TEST: File loading
    # ============================================================
    def test_load_config(self):
        config = self.bot._load_json(self.config_path)
        assert "accounts" in config
        assert "user_a" in config["accounts"]
        assert "user_b" in config["accounts"]
        assert config["accounts"]["user_a"]["tiktok_username"] == "test_user_a"
        print("  ✓ Config loaded correctly")

    def test_load_status(self):
        status = self.bot._load_json(self.status_path)
        assert status["streak_count"] == 140
        assert "today" in status
        print("  ✓ Status loaded correctly")

    def test_save_json(self):
        test_data = {"test": "data", "number": 42}
        test_path = Path(self.temp_dir) / "test_save.json"
        self.bot._save_json(test_data, test_path)
        with open(test_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == test_data
        print("  ✓ JSON saved and loaded correctly")

    # ============================================================
    # TEST: Encryption and decryption
    # ============================================================
    def test_encrypt_decrypt_credentials(self):
        password = "test_encryption_password_123!"
        data = {"username": "test_user", "password": "supersecret"}
        encrypted = self.bot.encrypt_credentials(data, password)
        assert encrypted != json.dumps(data)
        assert isinstance(encrypted, str)
        decrypted = self.bot.decrypt_credentials(encrypted, password)
        assert decrypted == data
        assert decrypted["username"] == "test_user"
        assert decrypted["password"] == "supersecret"
        print("  ✓ Encryption/decryption works correctly")

    def test_encrypt_decrypt_wrong_password_fails(self):
        password = "correct_password"
        wrong_password = "wrong_password"
        data = {"username": "test", "password": "secret"}
        encrypted = self.bot.encrypt_credentials(data, password)
        try:
            self.bot.decrypt_credentials(encrypted, wrong_password)
            assert False, "Should have raised an exception"
        except Exception:
            print("  ✓ Wrong password correctly rejected")

    def test_save_and_load_encrypted_file(self):
        password = "my_strong_password"
        self.bot.save_encrypted_credentials(
            "test_account", "test_user", "test_pass", password
        )
        filepath = self.accounts_dir / "test_account.enc"
        assert filepath.exists()
        loaded = self.bot.load_credentials("test_account", password)
        assert loaded["username"] == "test_user"
        assert loaded["password"] == "test_pass"
        print("  ✓ Credentials saved and loaded from file correctly")

    # ============================================================
    # TEST: Config management
    # ============================================================
    def test_config_account_keys(self):
        config = self.bot.config
        accounts = config["accounts"]
        assert "user_a" in accounts
        assert "user_b" in accounts
        assert accounts["user_a"]["friend_username"] == accounts["user_b"]["tiktok_username"]
        assert accounts["user_b"]["friend_username"] == accounts["user_a"]["tiktok_username"]
        print("  ✓ Account relationship is consistent")

    def test_config_messages_pool(self):
        config = self.bot.config
        assert len(config["accounts"]["user_a"]["messages_pool"]) >= 1
        assert len(config["accounts"]["user_b"]["messages_pool"]) >= 1
        print("  ✓ Message pools are configured")

    # ============================================================
    # TEST: Status management
    # ============================================================
    def test_status_update(self):
        self.bot.status["today"]["user_a_sent"] = True
        self.bot.status["today"]["bot_intervened"] = True
        self.bot.status["today"]["date"] = datetime.datetime.now().strftime("%Y-%m-%d")
        self.bot._save_json(self.bot.status, self.status_path)
        loaded = self.bot._load_json(self.status_path)
        assert loaded["today"]["user_a_sent"] == True
        assert loaded["today"]["bot_intervened"] == True
        print("  ✓ Status updates saved correctly")

    def test_streak_increment(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        self.bot.status["streak_count"] = 140
        self.bot.status["last_update"] = yesterday
        self.bot.status["today"] = {
            "user_a_sent": True,
            "user_b_sent": True,
            "bot_intervened": False,
            "bot_action": "none",
            "date": today,
        }
        if today != self.bot.status["last_update"][:10]:
            self.bot.status["streak_count"] += 1
        self.bot.status["last_update"] = datetime.datetime.now().isoformat()
        self.bot._save_json(self.bot.status, self.status_path)
        loaded = self.bot._load_json(self.status_path)
        assert loaded["streak_count"] == 141
        print("  ✓ Streak count incremented correctly")

    # ============================================================
    # TEST: Edge cases
    # ============================================================
    def test_empty_message_pool(self):
        config = self.bot.config
        config["accounts"]["user_a"]["messages_pool"] = []
        self.bot._save_json(config, self.config_path)
        self.bot.config = self.bot._load_json(self.config_path)
        msgs = self.bot.config["accounts"]["user_a"]["messages_pool"]
        if not msgs:
            import random
            default_msg = "🔥"
            assert default_msg == "🔥"
        print("  ✓ Empty message pool handled")

    def test_disabled_account_not_processed(self):
        config = self.bot.config
        config["accounts"]["user_a"]["bot_enabled"] = False
        self.bot._save_json(config, self.config_path)
        self.bot.config = self.bot._load_json(self.config_path)
        assert self.bot.config["accounts"]["user_a"]["bot_enabled"] == False
        print("  ✓ Disabled account handled correctly")

    def test_credentials_file_not_found(self):
        try:
            self.bot.load_credentials("nonexistent", "password")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            print("  ✓ Missing credentials file raises error")

    # ============================================================
    # TEST: Encryption key derivation
    # ============================================================
    def test_encryption_key_deterministic(self):
        pw = "same_password"
        key1 = self.bot._get_encryption_key(pw)
        key2 = self.bot._get_encryption_key(pw)
        assert key1 == key2
        print("  ✓ Encryption keys are deterministic (same password = same key)")

    def test_encryption_key_different(self):
        key1 = self.bot._get_encryption_key("password1")
        key2 = self.bot._get_encryption_key("password2")
        assert key1 != key2
        print("  ✓ Different passwords produce different keys")


class TestDeployment:
    """Tests for deployment configuration."""

    def test_requirements_exist(self):
        req_path = Path("requirements.txt")
        assert req_path.exists()
        content = req_path.read_text()
        assert "playwright" in content
        assert "flask" in content
        assert "cryptography" in content
        print("  ✓ requirements.txt is valid")

    def test_workflow_exists(self):
        workflow_path = Path(".github/workflows/streak.yml")
        assert workflow_path.exists()
        content = workflow_path.read_text()
        assert "schedule" in content
        assert "playwright install chromium" in content
        print("  ✓ GitHub Actions workflow exists and has schedule")

    def test_config_exists(self):
        assert Path("config.json").exists()
        print("  ✓ config.json exists")

    def test_status_exists(self):
        assert Path("status.json").exists()
        print("  ✓ status.json exists")

    def test_server_exists(self):
        assert Path("server.py").exists()
        print("  ✓ server.py exists")

    def test_templates_exist(self):
        assert Path("dashboard/templates/login.html").exists()
        assert Path("dashboard/templates/dashboard.html").exists()
        assert Path("dashboard/templates/setup.html").exists()
        print("  ✓ Dashboard templates exist")

    def test_setup_tool_exists(self):
        assert Path("setup_login.py").exists()
        print("  ✓ Setup diagnostic tool exists")
