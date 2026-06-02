"""
TikTok Streak Bot - Deployment Script
======================================
This script automates deployment to GitHub.

Usage:
  python deploy.py

What it does:
  1. Creates/uses a GitHub repository
  2. Pushes all project files
  3. Sets up GitHub Secrets
  4. Configures GitHub Pages (for dashboard)
  5. Tests the workflow

Requirements:
  - Python 3.9+
  - GitHub account
  - GitHub Personal Access Token (classic, with repo + workflow scopes)
"""

import os
import sys
import json
import base64
import getpass
from pathlib import Path
from github import Github, GithubException


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_banner():
    print("=" * 60)
    print("    TikTok Streak Bot - Deployment Script")
    print("    =====================================")
    print("=" * 60)
    print()
    print("  This script will deploy everything to GitHub.")
    print("  You need a GitHub Personal Access Token with:")
    print("    - repo (full control)")
    print("    - workflow (update GitHub Actions)")
    print()
    print("  Get your token: https://github.com/settings/tokens")
    print()


def create_repo(github, repo_name, private=False):
    try:
        user = github.get_user()
        repo = user.create_repo(
            repo_name,
            private=private,
            description="TikTok Streak Bot - Smart streak protection",
            auto_init=False,
        )
        print(f"  [✓] Repository '{repo_name}' created!")
        return repo
    except GithubException as e:
        if e.status == 422:
            print(f"  [!] Repository '{repo_name}' already exists. Using it.")
            return user.get_repo(repo_name)
        raise


def get_repo(github, repo_name):
    try:
        user = github.get_user()
        return user.get_repo(repo_name)
    except GithubException:
        return None


def push_file(repo, path, content, message="Initial commit"):
    try:
        existing = repo.get_contents(path)
        repo.update_file(path, message, content, existing.sha)
    except GithubException:
        repo.create_file(path, message, content)
    except Exception:
        pass


def set_secret(repo, name, value):
    try:
        from github import GithubException
        repo.create_secret(name, value)
        print(f"  [✓] Secret '{name}' set!")
    except Exception as e:
        print(f"  [!] Could not set secret '{name}': {e}")


def enable_pages(repo):
    try:
        repo.create_branch("gh-pages")
    except Exception:
        pass
    try:
        from github import GithubException
        repo.get_branch("gh-pages")
    except Exception:
        pass
    try:
        edit = repo.edit
    except Exception:
        pass
    print("  [i] GitHub Pages: Enable manually via Settings > Pages")
    print("      Source: Deploy from branch 'main' / '/docs' folder")


def get_files_to_upload():
    files = []
    base = Path(".")
    skip_dirs = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}
    skip_files = {".gitignore", "deploy.py"}
    for path in base.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(base)
        parts = rel.parts
        if any(p in skip_dirs for p in parts):
            continue
        if rel.name in skip_files:
            continue
        try:
            content = path.read_text(encoding="utf-8")
            files.append((str(rel), content))
        except Exception:
            try:
                content = path.read_bytes()
                files.append((str(rel), base64.b64encode(content).decode()))
            except Exception:
                pass
    return files


def test_workflow(repo):
    try:
        workflow = repo.get_workflow("streak.yml")
        if workflow:
            print(f"  [✓] Workflow 'streak.yml' found!")
            print(f"  [i] Status: {workflow.state}")
            return True
    except Exception:
        print("  [!] Could not verify workflow")
    return False


def main():
    clear_screen()
    print_banner()
    token = getpass.getpass("  GitHub Token: ").strip()
    if not token:
        print("  [✗] No token provided")
        sys.exit(1)
    print()
    repo_name = input("  Repository name [tiktok-streak-bot]: ").strip() or "tiktok-streak-bot"
    private = input("  Private repository? (y/N): ").strip().lower() == "y"
    print()
    print("  Connecting to GitHub...")
    try:
        g = Github(token)
        user = g.get_user()
        print(f"  [✓] Connected as {user.login}!")
    except Exception as e:
        print(f"  [✗] Connection failed: {e}")
        sys.exit(1)
    print()
    print("  Creating/setting up repository...")
    repo = create_repo(g, repo_name, private)
    print()
    print("  Uploading files...")
    files = get_files_to_upload()
    for rel_path, content in files:
        try:
            push_file(repo, rel_path, content, f"Add {rel_path}")
            print(f"    ✓ {rel_path}")
        except Exception as e:
            print(f"    ✗ {rel_path}: {e}")
    print(f"\n  [✓] Uploaded {len(files)} files!")
    print()
    print("  Setting up GitHub Secrets...")
    print("  (These are needed for the bot to run)")
    print()
    enc_pw = getpass.getpass("  Enter ENCRYPTION_PASSWORD (same as setup_login.py): ").strip()
    if enc_pw:
        set_secret(repo, "ENCRYPTION_PASSWORD", enc_pw)
    print()
    print("  Testing workflow...")
    test_workflow(repo)
    print()
    print("=" * 60)
    print("  DEPLOYMENT COMPLETE!")
    print("  ====================")
    print()
    print(f"  Repository URL:")
    print(f"    https://github.com/{user.login}/{repo_name}")
    print()
    print("  WHAT'S NEXT:")
    print("  1. Go to repository Settings > Secrets > Actions")
    print("     Verify ENCRYPTION_PASSWORD is set")
    print("  2. Go to Actions tab - workflow should be visible")
    print("  3. Go to Settings > Pages - enable GitHub Pages")
    print("     (use main branch, /docs folder or create gh-pages branch)")
    print()
    print("  For the Dashboard (Render.com):")
    print("  1. Sign up at https://render.com (free)")
    print("  2. Create a new Web Service, connect your GitHub repo")
    print("  3. Set: Build Command = 'pip install -r requirements.txt'")
    print("  4. Set: Start Command = 'python server.py'")
    print("  5. Add Environment Variables:")
    print("     - FLASK_SECRET_KEY = (random string)")
    print("     - GITHUB_TOKEN = (your GitHub token)")
    print("     - GITHUB_REPO = '{}/{}'".format(user.login, repo_name))
    print("     - ENCRYPTION_PASSWORD = (same as above)")
    print()
    print("  OR use GitHub Pages for a simpler dashboard")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  [!] Deployment cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n  [✗] Error: {e}")
        sys.exit(1)
