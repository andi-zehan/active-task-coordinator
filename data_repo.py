"""Git operations on data/.

Owns: ensure data/ exists, init/clone/push/pull, status check, test (ls-remote).
Reads sync_config to decide what to do for the current mode.
"""
import json
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import sync_config


# Wired in by server.py at import time so this module stays decoupled from
# the server's exact DATA_DIR path constant.
DATA_DIR: Path = None  # set by server.set_data_dir()
GIT_TIMEOUT_SECONDS = 30


def set_data_dir(path: Path) -> None:
    global DATA_DIR
    DATA_DIR = path


# ── helpers ──────────────────────────────────────────────────────────────

def _is_empty_placeholder_data_dir() -> bool:
    if not DATA_DIR.exists():
        return True
    if (DATA_DIR / ".git").exists():
        return False
    files = [p for p in DATA_DIR.rglob("*") if p.is_file() and p.name != ".DS_Store"]
    if not files:
        return True
    if len(files) == 1 and files[0].relative_to(DATA_DIR).as_posix() == "_boards-order.json":
        try:
            return json.loads(files[0].read_text(encoding="utf-8")) == []
        except Exception:
            return False
    return False


def _has_git_remote() -> bool:
    try:
        r = subprocess.run(
            ["git", "remote"], capture_output=True, text=True, cwd=DATA_DIR,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


def _origin_url() -> str:
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True,
            cwd=DATA_DIR, timeout=GIT_TIMEOUT_SECONDS,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _ensure_plain_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    boards_order = DATA_DIR / "_boards-order.json"
    if not boards_order.exists():
        boards_order.write_text("[]", encoding="utf-8")


# ── push / pull ──────────────────────────────────────────────────────────

def git_sync_push() -> dict:
    """Mode-aware push. Returns {status, message}."""
    cfg = sync_config.load()
    mode = cfg["mode"]
    if mode == "off":
        return {"status": "skipped", "message": "sync disabled"}
    if not (DATA_DIR / ".git").exists():
        return {"status": "error", "message": "data/ is not a git repository"}
    try:
        subprocess.run(["git", "add", "-A"], cwd=DATA_DIR, capture_output=True,
                       text=True, timeout=GIT_TIMEOUT_SECONDS)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=DATA_DIR,
                                capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        if not status.stdout.strip():
            return {"status": "no-changes", "message": "Nothing to sync"}
        msg = f"sync from {platform.node()} at {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "commit", "-m", msg], cwd=DATA_DIR, capture_output=True,
                       text=True, timeout=GIT_TIMEOUT_SECONDS)
        if mode == "local":
            return {"status": "ok", "message": f"committed locally: {msg}"}
        # remote mode
        if not _has_git_remote():
            return {"status": "error", "message": "remote mode but no remote configured"}
        r = subprocess.run(["git", "push", "origin", cfg["branch"]], cwd=DATA_DIR,
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        if r.returncode != 0:
            return {"status": "error", "message": f"Push failed: {r.stderr.strip()}"}
        return {"status": "ok", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def reconcile_repo_state(cfg: dict) -> dict:
    """Make data/ match the configured mode. No network calls.

    Off:    ensure data/ exists, no git commands.
    Local:  ensure data/ is a git repo (init if missing). Don't touch remotes.
    Remote: ensure data/ is a git repo, ensure origin matches cfg.remote_url.
    """
    mode = cfg["mode"]
    branch = cfg["branch"]
    if mode == "off":
        _ensure_plain_data_dir()
        return {"status": "ok", "message": "data/ ready (mode=off)"}
    try:
        if not (DATA_DIR / ".git").exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["git", "init", "-b", branch], cwd=DATA_DIR,
                capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
            )
            if r.returncode != 0:
                return {"status": "error", "message": f"git init failed: {r.stderr.strip()}"}
        if mode == "remote":
            current = _origin_url()
            target = cfg["remote_url"]
            if not current:
                r = subprocess.run(
                    ["git", "remote", "add", "origin", target], cwd=DATA_DIR,
                    capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
                )
                if r.returncode != 0:
                    return {"status": "error",
                            "message": f"git remote add failed: {r.stderr.strip()}"}
            elif current != target:
                r = subprocess.run(
                    ["git", "remote", "set-url", "origin", target], cwd=DATA_DIR,
                    capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
                )
                if r.returncode != 0:
                    return {"status": "error",
                            "message": f"git remote set-url failed: {r.stderr.strip()}"}
        return {"status": "ok", "message": f"data/ ready (mode={mode})"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def git_test() -> dict:
    """Quick connectivity check. Returns {ok, message}. No side effects."""
    cfg = sync_config.load()
    mode = cfg["mode"]
    if mode == "off":
        return {"ok": True, "message": "sync disabled"}
    if mode == "local":
        ok = (DATA_DIR / ".git").exists()
        return {
            "ok": ok,
            "message": "local repo present" if ok else "data/ is not a git repository",
        }
    # remote mode
    try:
        r = subprocess.run(
            ["git", "ls-remote", cfg["remote_url"]],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
        )
        if r.returncode == 0:
            return {"ok": True, "message": "remote reachable"}
        return {"ok": False, "message": (r.stderr or r.stdout).strip() or "ls-remote failed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "ls-remote timed out"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def git_status_summary() -> str:
    """Returns one of: 'ok', 'no-repo', 'missing-remote'. Used by /api/sync/config."""
    if not (DATA_DIR / ".git").exists():
        return "no-repo"
    if not _has_git_remote():
        return "missing-remote"
    return "ok"


def git_sync_pull() -> dict:
    """Mode-aware pull. In remote mode, clones if data/ is empty, else pulls."""
    cfg = sync_config.load()
    mode = cfg["mode"]
    if mode != "remote":
        return {"status": "skipped", "message": f"pull skipped (mode={mode})"}
    if not cfg["remote_url"]:
        return {"status": "error", "message": "remote mode but remote_url is empty"}
    # Clone path: data/ doesn't exist or is an empty placeholder.
    if _is_empty_placeholder_data_dir():
        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR)
        r = subprocess.run(
            ["git", "clone", cfg["remote_url"], str(DATA_DIR)],
            capture_output=True, text=True, cwd=DATA_DIR.parent,
            timeout=GIT_TIMEOUT_SECONDS * 4,
        )
        if r.returncode != 0:
            return {"status": "error", "message": f"Clone failed: {r.stderr.strip()}"}
        return {"status": "ok", "message": f"Cloned data repository from {cfg['remote_url']}"}
    # Normal pull path.
    if not (DATA_DIR / ".git").exists():
        return {"status": "error", "message": "data/ exists but is not a git repository"}
    try:
        r = subprocess.run(
            ["git", "pull", "origin", cfg["branch"]], cwd=DATA_DIR,
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS * 2,
        )
        if r.returncode != 0:
            return {"status": "error", "message": f"Pull failed: {r.stderr.strip()}"}
        return {"status": "ok", "message": r.stdout.strip() or "up to date"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
