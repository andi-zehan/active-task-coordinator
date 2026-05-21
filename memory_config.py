"""Per-user memory-system config (memory dir, lint cadence, proposal cap).

Lives at ~/.atc/memory-config.json so it survives reinstalls and is independent
of the data dir (memory is its own repo by design — gitignored from data/).

Distinct from app_config.py (cards/boards) and sync_config.py (data-repo git).
"""
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".atc"
CONFIG_PATH = CONFIG_DIR / "memory-config.json"

DEFAULT_MEMORY_DIR = Path.home() / ".atc" / "memory"

DEFAULTS = {
    "memory_dir": str(DEFAULT_MEMORY_DIR),
    "lint_enabled": True,
    "lint_interval_hours": 1,
    "max_pending_proposals": 3,
}

USER_WRITABLE_KEYS = (
    "memory_dir", "lint_enabled", "lint_interval_hours", "max_pending_proposals",
)


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
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def save(updates: dict) -> dict:
    """Merge updates into the existing config and write to disk.

    Returns the new full config. Does NOT validate — call `validate()` first
    when input comes from the user.
    """
    cfg = load()
    for k in DEFAULTS:
        if k in updates:
            cfg[k] = updates[k]
    _write(cfg)
    return cfg


def get_memory_dir() -> Path:
    return Path(load()["memory_dir"]).expanduser()


def public_view() -> dict:
    cfg = load()
    return {
        "memory_dir": cfg["memory_dir"],
        "lint_enabled": bool(cfg["lint_enabled"]),
        "lint_interval_hours": int(cfg["lint_interval_hours"]),
        "max_pending_proposals": int(cfg["max_pending_proposals"]),
    }


def validate(updates: dict) -> None:
    """Raise ValidationError if updates can't be applied safely."""
    if "memory_dir" in updates:
        raw = updates["memory_dir"]
        if not isinstance(raw, str) or not raw.strip():
            raise ValidationError("memory_dir must be a non-empty string")
        path = Path(raw).expanduser()
        if path.exists() and not path.is_dir():
            raise ValidationError(f"{path} exists but is not a directory")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".atc-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            raise ValidationError(f"memory_dir is not writable: {e}")
    if "lint_enabled" in updates:
        if not isinstance(updates["lint_enabled"], bool):
            raise ValidationError("lint_enabled must be a boolean")
    if "lint_interval_hours" in updates:
        v = updates["lint_interval_hours"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1 or v > 168:
            raise ValidationError("lint_interval_hours must be an integer in [1, 168]")
    if "max_pending_proposals" in updates:
        v = updates["max_pending_proposals"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1 or v > 50:
            raise ValidationError("max_pending_proposals must be an integer in [1, 50]")


def sanitize_user_updates(updates: dict) -> dict:
    return {k: v for k, v in updates.items() if k in USER_WRITABLE_KEYS}
