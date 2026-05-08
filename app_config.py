"""Per-user app config (data folder location, first-run flags).

Lives at ~/.atc/config.json so it survives reinstalls and is independent of
the project directory. Distinct from sync_config.py (which configures a
specific data repo).
"""
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".atc"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Default to ~/Documents/Flow — standard office-app location, discoverable in
# Explorer, easy to point a user at. Falls back to ~/Flow if Documents is
# missing for some reason.
def _default_data_dir() -> Path:
    docs = Path.home() / "Documents"
    if docs.is_dir():
        return docs / "Flow"
    return Path.home() / "Flow"


DEFAULT_DATA_DIR = _default_data_dir()

DEFAULTS = {
    "data_dir": str(DEFAULT_DATA_DIR),
    "seen_api_key_prompt": False,
}

USER_WRITABLE_KEYS = ("data_dir", "seen_api_key_prompt")


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

    Returns the new full config. Does NOT validate — call `validate()`
    first when input comes from the user.
    """
    cfg = load()
    for k in DEFAULTS:
        if k in updates:
            cfg[k] = updates[k]
    _write(cfg)
    return cfg


def get_data_dir() -> Path:
    return Path(load()["data_dir"]).expanduser()


def public_view() -> dict:
    cfg = load()
    return {
        "data_dir": cfg["data_dir"],
        "seen_api_key_prompt": bool(cfg["seen_api_key_prompt"]),
    }


def validate(updates: dict) -> None:
    """Raise ValidationError if updates can't be applied safely."""
    if "data_dir" in updates:
        raw = updates["data_dir"]
        if not isinstance(raw, str) or not raw.strip():
            raise ValidationError("data_dir must be a non-empty string")
        path = Path(raw).expanduser()
        # Refuse paths that point at a file rather than a directory.
        if path.exists() and not path.is_dir():
            raise ValidationError(f"{path} exists but is not a directory")
        # Probe writability: create the directory if missing, then write+delete
        # a marker file. Surfaces permission errors before the user restarts.
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".atc-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            raise ValidationError(f"data_dir is not writable: {e}")
    if "seen_api_key_prompt" in updates:
        if not isinstance(updates["seen_api_key_prompt"], bool):
            raise ValidationError("seen_api_key_prompt must be a boolean")


def sanitize_user_updates(updates: dict) -> dict:
    return {k: v for k, v in updates.items() if k in USER_WRITABLE_KEYS}
