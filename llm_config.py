"""Load, save, and mask the LLM gateway configuration.

Config lives at ./.llm-config.json (gitignored, never synced).
A fresh client is built on every call so token rotation is immediate.
"""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / ".llm-config.json"

DEFAULTS = {
    "base_url": "https://llm-gateway.ve42034x.automotive-wan.com",
    "auth_token": "",
    "model": "claude-opus-4-7",
    "tls_verify": False,
}

ALLOWED_MODELS = ("claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5")


def load() -> dict:
    """Return the current config, falling back to DEFAULTS for missing keys."""
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


def save(updates: dict) -> dict:
    """Merge updates into the existing config and write to disk.

    A missing 'auth_token' key in updates means 'keep existing token'.
    An explicit empty-string 'auth_token' clears the token.
    Returns the new full config (with token in cleartext for in-process use).
    """
    cfg = load()
    for key in DEFAULTS:
        if key in updates:
            cfg[key] = updates[key]
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def mask_token(token: str) -> str:
    """Return a display-safe form of the token: '****' + last 4 chars, or '***' if too short."""
    if not token:
        return ""
    if len(token) <= 4:
        return "*" * len(token)
    return "****" + token[-4:]


def public_view() -> dict:
    """Return the config safe to send to the browser (token masked)."""
    cfg = load()
    return {
        "configured": bool(cfg["auth_token"]),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "tls_verify": cfg["tls_verify"],
        "auth_token": mask_token(cfg["auth_token"]),
    }
