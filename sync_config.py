"""Load, save, and validate the data-sync configuration.

Config lives at ./.sync-config.json (gitignored, never synced).
Reads the file on every call so changes take effect immediately.
"""
import json
import subprocess
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / ".sync-config.json"

ALLOWED_MODES = ("off", "local", "remote")

DEFAULTS = {
    "mode": "remote",
    "remote_url": "",
    "branch": "main",
    "skip_next_pull": False,
}

# Keys that the HTTP layer is allowed to overwrite via PUT /api/sync/config.
# `skip_next_pull` is internal and is set only by transition logic.
USER_WRITABLE_KEYS = ("mode", "remote_url", "branch")


class ValidationError(Exception):
    """Raised when a config update fails validation."""


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k in DEFAULTS:
                if k in stored:
                    cfg[k] = stored[k]
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def _write(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def save(updates: dict) -> dict:
    """Merge updates into the existing config and write to disk.

    Only keys in DEFAULTS are accepted. Returns the new full config.
    Does NOT validate — call `validate(cfg)` before save when input
    comes from the user.
    """
    cfg = load()
    for k in DEFAULTS:
        if k in updates:
            cfg[k] = updates[k]
    _write(cfg)
    return cfg


def public_view() -> dict:
    """Return the config safe to send to the browser. Hides internal fields."""
    cfg = load()
    return {
        "mode": cfg["mode"],
        "remote_url": cfg["remote_url"],
        "branch": cfg["branch"],
    }


def validate(updates: dict) -> None:
    """Raise ValidationError if `updates` is not a usable config."""
    mode = updates.get("mode")
    if mode not in ALLOWED_MODES:
        raise ValidationError(f"mode must be one of {ALLOWED_MODES}")
    branch = updates.get("branch", "")
    if not isinstance(branch, str) or not branch.strip():
        raise ValidationError("branch must be a non-empty string")
    if mode == "remote":
        url = updates.get("remote_url", "")
        if not isinstance(url, str) or not url.strip():
            raise ValidationError("remote_url is required when mode is 'remote'")


def transition_sets_skip_pull(old: dict, new: dict, data_repo_exists: bool) -> bool:
    """Decide whether the next startup auto-pull should be suppressed.

    The auto-pull is suppressed when:
      * mode flips from 'local' -> 'remote' (local commits may be ahead of origin)
      * mode flips from 'off' -> 'remote' AND data/ already has a repo
      * remote_url changes while staying in 'remote' (new origin may be empty/diverged)
    """
    old_mode, new_mode = old.get("mode"), new.get("mode")
    if new_mode != "remote":
        return False
    if old_mode == "local":
        return True
    if old_mode == "off" and data_repo_exists:
        return True
    if old_mode == "remote" and old.get("remote_url") != new.get("remote_url"):
        return True
    return False


def set_skip_next_pull(value: bool) -> None:
    cfg = load()
    cfg["skip_next_pull"] = bool(value)
    _write(cfg)


def sanitize_user_updates(updates: dict) -> dict:
    """Drop keys the user is not allowed to write directly."""
    return {k: v for k, v in updates.items() if k in USER_WRITABLE_KEYS}


def migrate_defaults(data_dir: Path, fallback_remote_url: str = "") -> dict:
    """Derive first-run config from the current state of `data_dir`.

    Called only when CONFIG_PATH does not yet exist.
    """
    git_dir = data_dir / ".git"
    if not git_dir.exists():
        return {
            "mode": "remote",
            "remote_url": fallback_remote_url,
            "branch": "main",
            "skip_next_pull": False,
        }
    origin = _git_origin_url(data_dir)
    branch = _git_current_branch(data_dir) or "main"
    if origin:
        return {"mode": "remote", "remote_url": origin, "branch": branch, "skip_next_pull": False}
    return {"mode": "local", "remote_url": "", "branch": branch, "skip_next_pull": False}


def _git_origin_url(data_dir: Path) -> str:
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=data_dir, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def _git_current_branch(data_dir: Path) -> str:
    r = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=data_dir, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""
