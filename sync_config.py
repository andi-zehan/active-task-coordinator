"""Load, save, and validate the data-sync configuration.

Config lives at ./.sync-config.json (gitignored, never synced).
Reads the file on every call so changes take effect immediately.
"""
import json
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
