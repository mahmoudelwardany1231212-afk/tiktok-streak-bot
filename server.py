import os
import json
import base64
import asyncio
import schedule
import time
import threading
from pathlib import Path
from functools import wraps

from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session as flask_session, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from github import Github, GithubException

from streak_bot import TikTokStreakBot

app = Flask(__name__, template_folder="dashboard/templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.environ.get("FORCE_HTTPS", "0") == "1",
)

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
# Security
# ============================================================
RATE_LIMIT = {}
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = timedelta(minutes=15)
RATE_LIMIT_BLOCK = timedelta(minutes=15)

ACCOUNT_LOCKOUT = {}
ACCOUNT_LOCKOUT_MAX = 10
ACCOUNT_LOCKOUT_DURATION = timedelta(minutes=30)

IDLE_TIMEOUT = timedelta(hours=1)

AUDIT_PATH = Path("audit.json")
MAX_AUDIT_ENTRIES = 100


def get_client_ip():
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_rate_limited(ip):
    now = datetime.now()
    entry = RATE_LIMIT.get(ip)
    if entry and entry.get("locked_until"):
        if now < entry["locked_until"]:
            remaining = int((entry["locked_until"] - now).total_seconds() // 60)
            return True, remaining
        RATE_LIMIT.pop(ip, None)
    if entry and now - entry["first_attempt"] > RATE_LIMIT_WINDOW:
        RATE_LIMIT.pop(ip, None)
    return False, 0


def record_failed_attempt(ip):
    now = datetime.now()
    entry = RATE_LIMIT.get(ip)
    if not entry or now - entry["first_attempt"] > RATE_LIMIT_WINDOW:
        RATE_LIMIT[ip] = {"attempts": 1, "first_attempt": now, "locked_until": None}
    else:
        entry["attempts"] += 1
        if entry["attempts"] >= RATE_LIMIT_MAX:
            entry["locked_until"] = now + RATE_LIMIT_BLOCK


def reset_rate_limit(ip):
    RATE_LIMIT.pop(ip, None)


def is_account_locked(username):
    now = datetime.now()
    entry = ACCOUNT_LOCKOUT.get(username)
    if entry and entry.get("locked_until"):
        if now < entry["locked_until"]:
            remaining = int((entry["locked_until"] - now).total_seconds() // 60)
            return True, remaining
        ACCOUNT_LOCKOUT.pop(username, None)
    return False, 0


def record_failed_login(username):
    now = datetime.now()
    entry = ACCOUNT_LOCKOUT.get(username)
    if not entry:
        ACCOUNT_LOCKOUT[username] = {"attempts": 1, "locked_until": None}
    else:
        entry["attempts"] += 1
        if entry["attempts"] >= ACCOUNT_LOCKOUT_MAX:
            entry["locked_until"] = now + ACCOUNT_LOCKOUT_DURATION


def reset_account_lock(username):
    ACCOUNT_LOCKOUT.pop(username, None)


def read_audit():
    try:
        with open(AUDIT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def write_audit(log):
    with open(AUDIT_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def log_audit(action, username, ip, details=""):
    log = read_audit()
    log.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "username": username,
        "ip": ip,
        "details": details,
    })
    if len(log) > MAX_AUDIT_ENTRIES:
        log = log[-MAX_AUDIT_ENTRIES:]
    write_audit(log)


def get_user_audit(username, limit=20):
    log = read_audit()
    return [e for e in log if e["username"] == username][-limit:]


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
# Secure credential storage
# ============================================================
def save_credential_to_secret(account_key, tiktok_username, tiktok_password):
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
    if not USE_GITHUB:
        return
    for key in ["user_a", "user_b"]:
        _delete_repo_file(f"accounts/{key}.enc")


def accounts_configured(config):
    accts = config.get("accounts", {})
    a = accts.get("user_a", {})
    b = accts.get("user_b", {})
    return bool(a.get("tiktok_username") and b.get("tiktok_username"))


# ============================================================
# Auth
# ============================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "logged_in" not in flask_session:
            return redirect(url_for("login_page"))
        if not flask_session.get("permanent", False):
            last_active = flask_session.get("last_active")
            if last_active:
                try:
                    last = datetime.fromisoformat(last_active)
                    if datetime.now() - last > IDLE_TIMEOUT:
                        flask_session.clear()
                        flash("Session expired due to inactivity. Please login again.", "info")
                        return redirect(url_for("login_page"))
                except Exception:
                    pass
        flask_session["last_active"] = datetime.now().isoformat()
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
    accts = config.get("accounts", {})
    a = accts.get("user_a", {})
    b = accts.get("user_b", {})

    missing_a = not a.get("tiktok_username")
    missing_b = not b.get("tiktok_username")

    if not missing_a and not missing_b:
        if "logged_in" not in flask_session:
            return redirect(url_for("login_page"))
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        tiktok_username = request.form.get("tiktok_username", "").strip()
        tiktok_password = request.form.get("tiktok_password", "").strip()
        friend_username = request.form.get("friend_username", "").strip()

        errors = []
        if not tiktok_username:
            errors.append("TikTok Username is required")
        if not tiktok_password:
            errors.append("TikTok Password is required")
        if not friend_username:
            errors.append("Friend's TikTok Username is required")

        if missing_a and missing_b:
            account_key = "user_a"
        elif missing_a:
            account_key = "user_a"
        else:
            account_key = "user_b"

        if account_key == "user_b":
            other = accts.get("user_a", {})
            if other.get("tiktok_username") != friend_username:
                errors.append(f"Friend's username should be {other.get('tiktok_username')} (the first account)")
            if other.get("friend_username") != tiktok_username:
                errors.append(f"The first account set their friend as {other.get('friend_username')}, not {tiktok_username}")
        elif account_key == "user_a" and not missing_b:
            other = accts.get("user_b", {})
            if other.get("tiktok_username") != friend_username:
                errors.append(f"Friend's username should be {other.get('tiktok_username')} (the existing account)")
            if other.get("friend_username") != tiktok_username:
                errors.append(f"The existing account set their friend as {other.get('friend_username')}, not {tiktok_username}")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("setup.html", error=True,
                                   missing_a=missing_a, missing_b=missing_b,
                                   account_key=account_key,
                                   a_username=a.get("tiktok_username", ""),
                                   b_username=b.get("tiktok_username", ""))

        if not ENCRYPTION_PASSWORD:
            flash("ENCRYPTION_PASSWORD not configured on server.", "danger")
            return render_template("setup.html", error=True,
                                   missing_a=missing_a, missing_b=missing_b,
                                   account_key=account_key,
                                   a_username=a.get("tiktok_username", ""),
                                   b_username=b.get("tiktok_username", ""))

        try:
            save_credential_to_secret(account_key, tiktok_username, tiktok_password)

            config["accounts"][account_key] = {
                "tiktok_username": tiktok_username,
                "dashboard_password_hash": generate_password_hash(tiktok_password),
                "bot_enabled": True,
                "messages_pool": ["🔥", "❤️", "ستريك 🔥", "💪", "✅"],
                "send_mode": "random",
                "custom_message": "",
                "friend_username": friend_username,
            }
            write_config(config)

            cleanup_legacy_enc_files()

            account_label = "Account 1" if account_key == "user_a" else "Account 2"
            flash(f"{account_label} configured! TikTok password encrypted and stored as GitHub Secret.", "success")

            config = read_config()
            recheck_a = not config.get("accounts", {}).get("user_a", {}).get("tiktok_username")
            recheck_b = not config.get("accounts", {}).get("user_b", {}).get("tiktok_username")
            if recheck_a or recheck_b:
                return redirect(url_for("setup"))
            return redirect(url_for("login_page"))

        except Exception as e:
            flash(f"Error: {e}", "danger")
            return render_template("setup.html", error=True,
                                   missing_a=missing_a, missing_b=missing_b,
                                   account_key=account_key,
                                   a_username=a.get("tiktok_username", ""),
                                   b_username=b.get("tiktok_username", ""))

    return render_template("setup.html", error=False,
                           missing_a=missing_a, missing_b=missing_b,
                           a_username=a.get("tiktok_username", ""),
                           b_username=b.get("tiktok_username", ""))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    config = read_config()
    if not accounts_configured(config):
        return redirect(url_for("setup"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        remember = request.form.get("remember_me") == "on"
        ip = get_client_ip()

        limited, remaining = is_rate_limited(ip)
        if limited:
            log_audit("rate_limited", username, ip, f"IP blocked for {remaining} min")
            flash(f"Too many attempts. Try again in {remaining} minutes.", "danger")
            return render_template("login.html", error=True)

        locked, rem = is_account_locked(username)
        if locked:
            log_audit("account_locked", username, ip, f"Locked for {rem} min")
            flash(f"Account locked due to too many failed attempts. Try again in {rem} minutes.", "danger")
            return render_template("login.html", error=True)

        log_audit("login_attempt", username, ip)

        found = False
        for account_key, account_data in config.get("accounts", {}).items():
            if account_data.get("tiktok_username") == username:
                found = True
                stored_hash = account_data.get("dashboard_password_hash", "")
                if stored_hash and check_password_hash(stored_hash, password):
                    reset_rate_limit(ip)
                    reset_account_lock(username)
                    flask_session.permanent = remember
                    flask_session["logged_in"] = True
                    flask_session["username"] = username
                    flask_session["account_key"] = account_key
                    flask_session["last_active"] = datetime.now().isoformat()
                    log_audit("login_success", username, ip)
                    flash("Login successful!", "success")
                    return redirect(url_for("dashboard"))
                else:
                    record_failed_attempt(ip)
                    record_failed_login(username)
                    log_audit("login_failure", username, ip, "Wrong password")
                    flash("Wrong password!", "danger")
                    return render_template("login.html", error=True)

        if not found:
            record_failed_attempt(ip)
            log_audit("login_failure", username, ip, "Account not found")
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

        password_changed = False
        if "tiktok_password" in data and data["tiktok_password"].strip():
            new_pw = data["tiktok_password"].strip()
            username = account.get("tiktok_username", "")
            save_credential_to_secret(account_key, username, new_pw)
            account["dashboard_password_hash"] = generate_password_hash(new_pw)
            password_changed = True
            log_audit("password_changed", username, get_client_ip())

        write_config(config)
        return jsonify({"success": True, "password_changed": password_changed, "account": {
            "username": account.get("tiktok_username", ""),
            "bot_enabled": account.get("bot_enabled", True),
            "send_mode": account.get("send_mode", "random"),
            "messages_pool": account.get("messages_pool", []),
            "custom_message": account.get("custom_message", ""),
            "friend_username": account.get("friend_username", ""),
        }})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/audit")
@login_required
def api_audit():
    username = flask_session.get("username", "")
    entries = get_user_audit(username, limit=30)
    return jsonify({"entries": entries})


@app.route("/api/trigger_bot", methods=["POST"])
@login_required
def api_trigger_bot():
    ip = get_client_ip()
    log_audit("manual_trigger", flask_session.get("username", ""), ip)
    if not ENCRYPTION_PASSWORD:
        return jsonify({"success": False, "message": "الباسورد مشفرة مش موجودة"}), 500
    def run():
        try:
            bot = TikTokStreakBot()
            asyncio.run(bot.run_smart(use_env=False))
        except Exception as e:
            print(f"Bot failed: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"success": True, "message": "البوت شغال!"})


@app.route("/api/run_bot_local", methods=["POST"])
@login_required
def api_run_bot_local():
    try:
        if not ENCRYPTION_PASSWORD:
            return jsonify({"success": False, "error": "ENCRYPTION_PASSWORD not configured"}), 500

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
    username = flask_session.get("username", "unknown")
    ip = get_client_ip()
    log_audit("logout", username, ip)
    flask_session.clear()
    flash("Logged out", "info")
    return redirect(url_for("login_page"))


# ============================================================
# Scheduled bot — runs daily at 11:45 PM Cairo
# ============================================================
def run_bot_scheduled():
    with app.app_context():
        print(f"[Scheduler] Running bot at {datetime.now()}")
        try:
            bot = TikTokStreakBot()
            asyncio.run(bot.run_smart(use_env=False))
            print(f"[Scheduler] Bot finished successfully")
        except Exception as e:
            print(f"[Scheduler] Bot failed: {e}")

schedule.every().day.at("23:45").do(run_bot_scheduled)
threading.Thread(target=lambda: (
    schedule.run_all(),
    [time.sleep(30) for _ in iter(lambda: (schedule.run_pending(), False)[1], True)]
), daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
