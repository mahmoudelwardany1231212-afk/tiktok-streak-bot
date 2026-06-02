import os
import json
import base64
import asyncio
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session as flask_session, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from github import Github, GithubException

from streak_bot import TikTokStreakBot

app = Flask(__name__, template_folder="dashboard/templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())

CONFIG_PATH = Path("config.json")
STATUS_PATH = Path("status.json")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_NAME = os.environ.get("GITHUB_REPO", "")
ENCRYPTION_PASSWORD = os.environ.get("ENCRYPTION_PASSWORD", "")

USE_GITHUB = bool(GITHUB_TOKEN and GITHUB_REPO_NAME)

# ============================================================
# GitHub helpers
# ============================================================
def _get_github():
    if USE_GITHUB:
        g = Github(GITHUB_TOKEN)
        return g.get_repo(GITHUB_REPO_NAME)
    return None


def _read_repo_file(path):
    repo = _get_github()
    if not repo:
        return None
    try:
        content = repo.get_contents(path)
        data = base64.b64decode(content.content).decode("utf-8")
        return json.loads(data)
    except Exception:
        return None


def _write_repo_file(path, data, message="Update via dashboard"):
    repo = _get_github()
    if not repo:
        return False
    content = json.dumps(data, ensure_ascii=False, indent=2).encode()
    try:
        existing = repo.get_contents(path)
        repo.update_file(path, message, content.decode(), existing.sha)
    except GithubException:
        repo.create_file(path, message, content.decode())
    except Exception:
        return False
    return True


def _set_repo_secret(secret_name, secret_value):
    """Store a secret in GitHub Secrets (never in repo files)."""
    repo = _get_github()
    if not repo:
        return False
    try:
        repo.create_secret(secret_name, secret_value)
        return True
    except Exception:
        return False


def _delete_repo_file(path):
    repo = _get_github()
    if not repo:
        return False
    try:
        existing = repo.get_contents(path)
        repo.delete_file(path, f"Remove {path}", existing.sha)
        return True
    except Exception:
        return False


# ============================================================
# Local file helpers
# ============================================================
def _read_local(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_local(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# Config / Status
# ============================================================
def read_config():
    if USE_GITHUB:
        data = _read_repo_file("config.json")
        if data:
            return data
    return _read_local(CONFIG_PATH) or {"accounts": {}}


def write_config(data):
    if USE_GITHUB:
        _write_repo_file("config.json", data, "Update config via dashboard")
    _write_local(CONFIG_PATH, data)


def read_status():
    if USE_GITHUB:
        data = _read_repo_file("status.json")
        if data:
            return data
    return _read_local(STATUS_PATH) or {"streak_count": 0, "today": {}}


def write_status(data):
    if USE_GITHUB:
        _write_repo_file("status.json", data, "Update status via dashboard")
    _write_local(STATUS_PATH, data)


# ============================================================
# Secure credential storage  (GitHub Secrets only — NEVER in repo)
# ============================================================
def save_credential_to_secret(account_key, tiktok_username, tiktok_password):
    """Encrypt TikTok credentials and store as GitHub Secret."""
    bot = TikTokStreakBot()
    encrypted = bot.encrypt_credentials(
        {"username": tiktok_username, "password": tiktok_password},
        ENCRYPTION_PASSWORD
    )
    secret_name = f"{account_key.upper()}_CREDS"
    if USE_GITHUB:
        ok = _set_repo_secret(secret_name, encrypted)
        if ok:
            _delete_repo_file(f"accounts/{account_key}.enc")
            return True
    _write_local(Path("accounts") / f"{account_key}.enc", encrypted)
    return False


def cleanup_legacy_enc_files():
    """Remove any .enc files from the repo (they should be secrets, not files)."""
    if not USE_GITHUB:
        return
    for key in ["user_a", "user_b"]:
        _delete_repo_file(f"accounts/{key}.enc")


def accounts_configured(config):
    accts = config.get("accounts", {})
    a = accts.get("user_a", {})
    b = accts.get("user_b", {})
    return bool(a.get("tiktok_username") and b.get("tiktok_username"))


def secrets_configured():
    """Check if GitHub Secrets exist by trying to read a status that depends on them."""
    if not USE_GITHUB:
        return Path("accounts/user_a.enc").exists()
    for key in ["user_a", "user_b"]:
        try:
            repo = _get_github()
            if repo:
                secret_name = f"{key.upper()}_CREDS"
                try:
                    repo.get_secret(secret_name)
                except Exception:
                    return False
        except Exception:
            return False
    return True


# ============================================================
# Auth
# ============================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "logged_in" not in flask_session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ============================================================
# Routes
# ============================================================
@app.route("/")
def index():
    config = read_config()
    if not accounts_configured(config):
        return redirect(url_for("setup"))
    if "logged_in" in flask_session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    config = read_config()
    if accounts_configured(config) and "logged_in" not in flask_session:
        return redirect(url_for("login_page"))

    if request.method == "POST":
        a_username = request.form.get("a_username", "").strip()
        a_password = request.form.get("a_password", "").strip()
        a_dash_pw = request.form.get("a_dash_pw", "").strip()
        a_friend = request.form.get("a_friend", "").strip()

        b_username = request.form.get("b_username", "").strip()
        b_password = request.form.get("b_password", "").strip()
        b_dash_pw = request.form.get("b_dash_pw", "").strip()
        b_friend = request.form.get("b_friend", "").strip()

        errors = []
        if not a_username or not a_password or not a_dash_pw or not a_friend:
            errors.append("All Account A fields are required")
        if not b_username or not b_password or not b_dash_pw or not b_friend:
            errors.append("All Account B fields are required")
        if a_friend != b_username:
            errors.append("Account A's friend should be Account B's username")
        if b_friend != a_username:
            errors.append("Account B's friend should be Account A's username")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("setup.html", error=True)

        if not ENCRYPTION_PASSWORD:
            flash("ENCRYPTION_PASSWORD not configured on server.", "danger")
            return render_template("setup.html", error=True)

        try:
            # Store TikTok credentials as GitHub Secrets (encrypted)
            save_credential_to_secret("user_a", a_username, a_password)
            save_credential_to_secret("user_b", b_username, b_password)

            # config.json stores only non-sensitive data
            config["accounts"]["user_a"] = {
                "tiktok_username": a_username,
                "dashboard_password_hash": generate_password_hash(a_dash_pw),
                "bot_enabled": True,
                "messages_pool": ["\U0001f525", "\u2764\ufe0f", "\u0633\u062a\u0631\u064a\u0643 \U0001f525", "\U0001f4aa", "\u2705"],
                "send_mode": "random",
                "custom_message": "",
                "friend_username": a_friend,
            }
            config["accounts"]["user_b"] = {
                "tiktok_username": b_username,
                "dashboard_password_hash": generate_password_hash(b_dash_pw),
                "bot_enabled": True,
                "messages_pool": ["\U0001f31f", "\U0001f525", "\U0001f4af", "\u0639\u0627\u0634 \u064a\u0627 \u0635\u0627\u062d\u0628\u064a", "\u2705"],
                "send_mode": "random",
                "custom_message": "",
                "friend_username": b_friend,
            }
            write_config(config)

            cleanup_legacy_enc_files()

            flash("Accounts configured! TikTok passwords encrypted and stored as GitHub Secrets.", "success")
            return redirect(url_for("login_page"))

        except Exception as e:
            flash(f"Error: {e}", "danger")
            return render_template("setup.html", error=True)

    return render_template("setup.html", error=False)


@app.route("/login", methods=["GET", "POST"])
def login_page():
    config = read_config()
    if not accounts_configured(config):
        return redirect(url_for("setup"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        for account_key, account_data in config.get("accounts", {}).items():
            if account_data.get("tiktok_username") == username:
                stored_hash = account_data.get("dashboard_password_hash", "")
                if stored_hash and check_password_hash(stored_hash, password):
                    flask_session["logged_in"] = True
                    flask_session["username"] = username
                    flask_session["account_key"] = account_key
                    flash("Login successful!", "success")
                    return redirect(url_for("dashboard"))
                else:
                    flash("Wrong password!", "danger")
                    return render_template("login.html", error=True)

        flash(f"Account '{username}' not found!", "danger")
        return render_template("login.html", error=True)

    return render_template("login.html", error=False)


@app.route("/dashboard")
@login_required
def dashboard():
    config = read_config()
    status = read_status()
    account_key = flask_session.get("account_key")
    account = config.get("accounts", {}).get(account_key, {})
    partner_key = "user_b" if account_key == "user_a" else "user_a"
    partner = config.get("accounts", {}).get(partner_key, {})

    return render_template(
        "dashboard.html",
        account=account,
        account_key=account_key,
        partner=partner,
        status=status,
        streak_count=status.get("streak_count", 0),
        today=status.get("today", {}),
    )


@app.route("/api/status")
@login_required
def api_status():
    status = read_status()
    config = read_config()
    account_key = flask_session.get("account_key")
    account = config.get("accounts", {}).get(account_key, {})
    return jsonify({
        "status": status,
        "account": {
            "username": account.get("tiktok_username", ""),
            "bot_enabled": account.get("bot_enabled", True),
            "send_mode": account.get("send_mode", "random"),
            "messages_pool": account.get("messages_pool", []),
            "custom_message": account.get("custom_message", ""),
            "friend_username": account.get("friend_username", ""),
        }
    })


@app.route("/api/update_config", methods=["POST"])
@login_required
def api_update_config():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        config = read_config()
        account_key = flask_session.get("account_key")
        if account_key not in config.get("accounts", {}):
            return jsonify({"success": False, "error": "Account not found"}), 404

        account = config["accounts"][account_key]

        if "bot_enabled" in data:
            account["bot_enabled"] = bool(data["bot_enabled"])
        if "send_mode" in data and data["send_mode"] in ["random", "custom"]:
            account["send_mode"] = data["send_mode"]
        if "messages_pool" in data and isinstance(data["messages_pool"], list):
            account["messages_pool"] = [str(m).strip() for m in data["messages_pool"] if str(m).strip()]
        if "custom_message" in data:
            account["custom_message"] = str(data["custom_message"]).strip()
        if "friend_username" in data and data["friend_username"].strip():
            account["friend_username"] = data["friend_username"].strip()

        # Handle password changes — store as GitHub Secrets, never in repo files
        if "tiktok_password" in data and data["tiktok_password"].strip():
            new_pw = data["tiktok_password"].strip()
            username = account.get("tiktok_username", "")
            save_credential_to_secret(account_key, username, new_pw)

        if "dashboard_password" in data and data["dashboard_password"].strip():
            account["dashboard_password_hash"] = generate_password_hash(data["dashboard_password"].strip())

        write_config(config)
        return jsonify({"success": True, "account": {
            "username": account.get("tiktok_username", ""),
            "bot_enabled": account.get("bot_enabled", True),
            "send_mode": account.get("send_mode", "random"),
            "messages_pool": account.get("messages_pool", []),
            "custom_message": account.get("custom_message", ""),
            "friend_username": account.get("friend_username", ""),
        }})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/trigger_bot", methods=["POST"])
@login_required
def api_trigger_bot():
    try:
        if USE_GITHUB:
            repo = _get_github()
            if repo:
                try:
                    workflow = repo.get_workflow("streak.yml")
                    workflow.create_dispatch(repo.default_branch)
                    return jsonify({"success": True, "message": "Bot triggered via GitHub Actions! Check Actions tab."})
                except Exception:
                    pass
        return jsonify({"success": False, "message": "GitHub trigger unavailable"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/run_bot_local", methods=["POST"])
@login_required
def api_run_bot_local():
    try:
        if not ENCRYPTION_PASSWORD:
            return jsonify({"success": False, "error": "ENCRYPTION_PASSWORD not configured"}), 500
        import threading

        def run_bot_thread():
            try:
                bot = TikTokStreakBot()
                asyncio.run(bot.run_smart(use_env=False))
            except Exception as e:
                print(f"Bot run error: {e}")

        thread = threading.Thread(target=run_bot_thread, daemon=True)
        thread.start()
        return jsonify({"success": True, "message": "Bot started locally! Check logs."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/logout")
def logout():
    flask_session.clear()
    flash("Logged out", "info")
    return redirect(url_for("login_page"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
