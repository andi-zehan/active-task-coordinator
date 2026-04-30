# Configurable Data Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user choose how `data/` is synced — `off` (no git), `local` (commit only), `remote` (commit + push) — via the existing settings modal.

**Architecture:** A new `sync_config.py` module owns `.sync-config.json` (load/save/migrate/validate), modeled after `llm_config.py`. A new `data_repo.py` module owns all git operations: `reconcile_repo_state()` plus mode-aware `git_sync_push/pull()`. `server.py` shrinks — its existing helpers (`ensure_data_repo`, `git_sync_push`, `git_sync_pull`, `_has_git_remote`, `_is_empty_placeholder_data_dir`) move to `data_repo.py`, and three new HTTP handlers are added. The settings modal in `index.html` gets a "Data sync" section. The header sync button becomes mode-aware.

**Tech Stack:** Python 3 stdlib (`subprocess`, `pathlib`, `json`), unittest, vanilla JS, plain HTML/CSS.

---

## File Structure

| File | Status | Purpose |
|---|---|---|
| `sync_config.py` | new | Load/save/validate/migrate `.sync-config.json`. `public_view()`, `transition_sets_skip_pull()`. |
| `data_repo.py` | new | All git operations: `reconcile_repo_state`, `git_sync_push`, `git_sync_pull`, `git_test`, `git_status_summary`, helpers. |
| `server.py` | modify | Remove git helpers. Add `_handle_get_sync_config`, `_handle_put_sync_config`, `_handle_test_sync`. Update existing `_handle_sync_*` handlers to delegate to `data_repo`. Update startup. |
| `index.html` | modify | Settings modal gets "Data sync" section. Header sync button reads mode. New "Pull from remote" button in modal. |
| `.gitignore` | modify | Add `.sync-config.json`. |
| `tests/test_sync_config.py` | new | Unit tests for config module. |
| `tests/test_data_repo.py` | new | Integration-ish tests for git operations against a tmp dir + tmp bare remote. |

---

## Task 1: `sync_config.py` — defaults, load, save, public_view

**Files:**
- Create: `sync_config.py`
- Test: `tests/test_sync_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sync_config.py`:

```python
#!/usr/bin/env python3
"""Tests for sync_config module."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import sync_config


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmp.name) / ".sync-config.json"
        sync_config.CONFIG_PATH = self.cfg_path

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_returns_defaults_when_no_file(self):
        cfg = sync_config.load()
        self.assertEqual(cfg["mode"], "remote")
        self.assertEqual(cfg["remote_url"], "")
        self.assertEqual(cfg["branch"], "main")
        self.assertEqual(cfg["skip_next_pull"], False)

    def test_save_then_load_roundtrip(self):
        sync_config.save({"mode": "local", "remote_url": "https://x/y.git", "branch": "trunk"})
        cfg = sync_config.load()
        self.assertEqual(cfg["mode"], "local")
        self.assertEqual(cfg["remote_url"], "https://x/y.git")
        self.assertEqual(cfg["branch"], "trunk")

    def test_save_partial_update_keeps_other_keys(self):
        sync_config.save({"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"})
        sync_config.save({"branch": "develop"})
        cfg = sync_config.load()
        self.assertEqual(cfg["mode"], "remote")
        self.assertEqual(cfg["remote_url"], "https://x/y.git")
        self.assertEqual(cfg["branch"], "develop")

    def test_public_view_matches_load(self):
        sync_config.save({"mode": "local", "remote_url": "https://x/y.git", "branch": "main"})
        view = sync_config.public_view()
        self.assertEqual(view["mode"], "local")
        self.assertEqual(view["remote_url"], "https://x/y.git")
        self.assertEqual(view["branch"], "main")
        self.assertNotIn("skip_next_pull", view)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_sync_config -v`
Expected: ImportError or ModuleNotFoundError for `sync_config`.

- [ ] **Step 3: Create `sync_config.py` with minimal load/save/public_view**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_sync_config -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add sync_config.py tests/test_sync_config.py
git commit -m "feat(sync_config): load/save/public_view scaffold"
```

---

## Task 2: `sync_config.py` — validation

**Files:**
- Modify: `sync_config.py`
- Modify: `tests/test_sync_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_config.py` (above `if __name__ == "__main__":`):

```python
class TestValidation(unittest.TestCase):
    def test_valid_off(self):
        sync_config.validate({"mode": "off", "remote_url": "", "branch": "main"})

    def test_valid_local_no_url_required(self):
        sync_config.validate({"mode": "local", "remote_url": "", "branch": "main"})

    def test_valid_remote_with_url(self):
        sync_config.validate({"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"})

    def test_invalid_mode(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "weird", "remote_url": "", "branch": "main"})

    def test_remote_without_url(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "remote", "remote_url": "", "branch": "main"})

    def test_remote_with_whitespace_url(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "remote", "remote_url": "   ", "branch": "main"})

    def test_empty_branch_rejected(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "local", "remote_url": "", "branch": ""})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_sync_config.TestValidation -v`
Expected: AttributeError for `sync_config.validate`.

- [ ] **Step 3: Add `validate()` to `sync_config.py`**

Append to `sync_config.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_sync_config -v`
Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add sync_config.py tests/test_sync_config.py
git commit -m "feat(sync_config): validate mode/branch/remote_url"
```

---

## Task 3: `sync_config.py` — `transition_sets_skip_pull` + skip_next_pull helpers

**Files:**
- Modify: `sync_config.py`
- Modify: `tests/test_sync_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_config.py`:

```python
class TestTransitionSkipPull(unittest.TestCase):
    """skip_next_pull is set when the new config makes an auto-pull risky."""

    def test_local_to_remote_sets_skip(self):
        old = {"mode": "local", "remote_url": "", "branch": "main"}
        new = {"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"}
        self.assertTrue(sync_config.transition_sets_skip_pull(old, new, data_repo_exists=True))

    def test_off_to_remote_with_existing_repo_sets_skip(self):
        old = {"mode": "off", "remote_url": "", "branch": "main"}
        new = {"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"}
        self.assertTrue(sync_config.transition_sets_skip_pull(old, new, data_repo_exists=True))

    def test_off_to_remote_with_no_repo_does_not_set_skip(self):
        old = {"mode": "off", "remote_url": "", "branch": "main"}
        new = {"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"}
        self.assertFalse(sync_config.transition_sets_skip_pull(old, new, data_repo_exists=False))

    def test_remote_url_change_sets_skip(self):
        old = {"mode": "remote", "remote_url": "https://a/x.git", "branch": "main"}
        new = {"mode": "remote", "remote_url": "https://b/y.git", "branch": "main"}
        self.assertTrue(sync_config.transition_sets_skip_pull(old, new, data_repo_exists=True))

    def test_no_change_does_not_set_skip(self):
        cfg = {"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"}
        self.assertFalse(sync_config.transition_sets_skip_pull(cfg, cfg, data_repo_exists=True))

    def test_remote_to_local_does_not_set_skip(self):
        old = {"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"}
        new = {"mode": "local", "remote_url": "https://x/y.git", "branch": "main"}
        self.assertFalse(sync_config.transition_sets_skip_pull(old, new, data_repo_exists=True))

    def test_clear_skip_next_pull(self):
        sync_config.save({"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"})
        sync_config.set_skip_next_pull(True)
        self.assertTrue(sync_config.load()["skip_next_pull"])
        sync_config.set_skip_next_pull(False)
        self.assertFalse(sync_config.load()["skip_next_pull"])


class TestSkipPullNotUserWritable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        sync_config.CONFIG_PATH = Path(self.tmp.name) / ".sync-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_filters_skip_next_pull_when_not_in_user_writable(self):
        # caller goes through a sanitizer; ensure helper exists
        sanitized = sync_config.sanitize_user_updates(
            {"mode": "local", "branch": "main", "skip_next_pull": True, "remote_url": ""}
        )
        self.assertNotIn("skip_next_pull", sanitized)
        self.assertEqual(sanitized["mode"], "local")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_sync_config -v`
Expected: AttributeError for `transition_sets_skip_pull`, `set_skip_next_pull`, `sanitize_user_updates`.

- [ ] **Step 3: Add the three helpers to `sync_config.py`**

Append to `sync_config.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_sync_config -v`
Expected: 19 tests pass.

- [ ] **Step 5: Commit**

```bash
git add sync_config.py tests/test_sync_config.py
git commit -m "feat(sync_config): skip-pull transition logic + sanitizer"
```

---

## Task 4: `sync_config.py` — first-run migration from existing `data/`

**Files:**
- Modify: `sync_config.py`
- Modify: `tests/test_sync_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_config.py`:

```python
import subprocess


def _run_git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


class TestMigration(unittest.TestCase):
    """First-run defaults derived from the current data/ state."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmp.name) / ".sync-config.json"
        sync_config.CONFIG_PATH = self.cfg_path
        self.data_dir = Path(self.tmp.name) / "data"

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_data_dir_falls_back_to_remote_default(self):
        cfg = sync_config.migrate_defaults(self.data_dir, fallback_remote_url="https://fallback/x.git")
        self.assertEqual(cfg["mode"], "remote")
        self.assertEqual(cfg["remote_url"], "https://fallback/x.git")
        self.assertEqual(cfg["branch"], "main")

    def test_repo_with_origin_yields_remote_mode(self):
        self.data_dir.mkdir()
        _run_git(["init", "-b", "main"], self.data_dir)
        _run_git(["remote", "add", "origin", "https://x/y.git"], self.data_dir)
        cfg = sync_config.migrate_defaults(self.data_dir, fallback_remote_url="ignored")
        self.assertEqual(cfg["mode"], "remote")
        self.assertEqual(cfg["remote_url"], "https://x/y.git")
        self.assertEqual(cfg["branch"], "main")

    def test_repo_without_origin_yields_local_mode(self):
        self.data_dir.mkdir()
        _run_git(["init", "-b", "main"], self.data_dir)
        cfg = sync_config.migrate_defaults(self.data_dir, fallback_remote_url="ignored")
        self.assertEqual(cfg["mode"], "local")
        self.assertEqual(cfg["remote_url"], "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_sync_config.TestMigration -v`
Expected: AttributeError for `migrate_defaults`.

- [ ] **Step 3: Add `migrate_defaults()` to `sync_config.py`**

First add `import subprocess` to the top of `sync_config.py` (alongside the existing `import json`).

Then append to `sync_config.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_sync_config -v`
Expected: 22 tests pass.

- [ ] **Step 5: Commit**

```bash
git add sync_config.py tests/test_sync_config.py
git commit -m "feat(sync_config): migrate defaults from existing data/ state"
```

---

## Task 5: `data_repo.py` — move existing helpers from `server.py`

**Files:**
- Create: `data_repo.py`
- Modify: `server.py:9, 18, 20, 740-919, 922-925` (move git helpers out)

- [ ] **Step 1: Create `data_repo.py` and copy the existing helpers verbatim**

Create `data_repo.py`:

```python
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
```

- [ ] **Step 2: Add `git_sync_push` to `data_repo.py`**

Append to `data_repo.py`:

```python
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
```

- [ ] **Step 3: Add `git_sync_pull` to `data_repo.py`**

Append to `data_repo.py`:

```python
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
```

- [ ] **Step 4: Replace those functions in `server.py` with imports**

Edit `server.py`:

Replace lines 9 (`import subprocess`) — keep, still used by other code paths if any. Verify by grep first:

```bash
grep -n "subprocess\." server.py | head -20
```

If only the moved git functions used `subprocess`, remove the import; otherwise keep.

Replace `import janitor` block (around line 14-16) with:

```python
import llm_config
import notes
import janitor
import sync_config
import data_repo
```

Delete the function bodies of `_is_empty_placeholder_data_dir`, `ensure_data_repo`, `_has_git_remote`, `git_sync_push`, `git_sync_pull` (lines 838-919 in the original — adjust if line numbers drifted).

Replace `DATA_REPO_URL = os.environ.get(...)` line with:

```python
DATA_REPO_URL_FALLBACK = os.environ.get('ATC_DATA_REPO_URL', 'https://github.com/andi-zehan/atc-content.git')
```

Wire `data_repo.DATA_DIR` near the top, after `DATA_DIR = Path(...) / "data"`:

```python
data_repo.set_data_dir(DATA_DIR)
```

Update the existing `_handle_sync_push` and `_handle_sync_pull` handlers to delegate:

```python
def _handle_sync_push(self):
    self._send_json(data_repo.git_sync_push())

def _handle_sync_pull(self):
    self._send_json(data_repo.git_sync_pull())
```

Update `_handle_sync_status` to also include `mode` (full body):

```python
def _handle_sync_status(self):
    cfg = sync_config.load()
    if cfg["mode"] == "off" or not (DATA_DIR / '.git').exists():
        return self._send_json({'dirty': False, 'mode': cfg["mode"]})
    try:
        r = subprocess.run(['git', 'status', '--porcelain'],
                           capture_output=True, text=True, cwd=DATA_DIR, timeout=10)
        self._send_json({'dirty': bool(r.stdout.strip()), 'mode': cfg["mode"]})
    except Exception:
        self._send_json({'dirty': False, 'mode': cfg["mode"]})
```

Update the startup block (lines ~922-925) — replace:

```python
result = git_sync_pull()
print(f"Data sync pull: {result['status']} — {result['message']}")
ensure_data_dir()
```

with (just the pull/ensure portion — janitor stays as-is below):

```python
result = data_repo.git_sync_pull()
print(f"Data sync pull: {result['status']} — {result['message']}")
ensure_data_dir()
```

Note: `ensure_data_dir()` (line 224) stays in `server.py` — it's about scaffolding `_boards-order.json`, not about git.

- [ ] **Step 5: Run all tests + start server briefly**

```bash
python -m unittest discover tests -v
```

Expected: all tests still pass (we haven't changed behavior yet — just moved code).

Smoke-check that `server.py` imports cleanly:

```bash
python -c "import server"
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add data_repo.py server.py
git commit -m "refactor: move git sync helpers into data_repo.py"
```

---

## Task 6: `data_repo.reconcile_repo_state`

**Files:**
- Modify: `data_repo.py`
- Create: `tests/test_data_repo.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_data_repo.py`:

```python
#!/usr/bin/env python3
"""Tests for data_repo.reconcile_repo_state."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import data_repo
import sync_config


def _run_git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        data_repo.set_data_dir(self.data_dir)
        sync_config.CONFIG_PATH = self.root / ".sync-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_off_mode_creates_plain_dir(self):
        result = data_repo.reconcile_repo_state({"mode": "off", "remote_url": "", "branch": "main"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(self.data_dir.exists())
        self.assertFalse((self.data_dir / ".git").exists())

    def test_local_mode_runs_git_init(self):
        result = data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue((self.data_dir / ".git").exists())

    def test_local_mode_idempotent(self):
        data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        result = data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        self.assertEqual(result["status"], "ok")

    def test_local_mode_does_not_touch_existing_remote(self):
        self.data_dir.mkdir()
        _run_git(["init", "-b", "main"], self.data_dir)
        _run_git(["remote", "add", "origin", "https://preserved/x.git"], self.data_dir)
        data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        r = _run_git(["remote", "get-url", "origin"], self.data_dir)
        self.assertEqual(r.stdout.strip(), "https://preserved/x.git")

    def test_remote_mode_inits_and_adds_origin(self):
        result = data_repo.reconcile_repo_state(
            {"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"}
        )
        self.assertEqual(result["status"], "ok")
        r = _run_git(["remote", "get-url", "origin"], self.data_dir)
        self.assertEqual(r.stdout.strip(), "https://x/y.git")

    def test_remote_mode_updates_existing_origin(self):
        self.data_dir.mkdir()
        _run_git(["init", "-b", "main"], self.data_dir)
        _run_git(["remote", "add", "origin", "https://old/x.git"], self.data_dir)
        result = data_repo.reconcile_repo_state(
            {"mode": "remote", "remote_url": "https://new/y.git", "branch": "main"}
        )
        self.assertEqual(result["status"], "ok")
        r = _run_git(["remote", "get-url", "origin"], self.data_dir)
        self.assertEqual(r.stdout.strip(), "https://new/y.git")

    def test_remote_mode_does_not_clone_or_pull(self):
        # network would be required for clone — make sure we don't try it.
        result = data_repo.reconcile_repo_state(
            {"mode": "remote", "remote_url": "https://nonexistent.invalid/x.git", "branch": "main"}
        )
        self.assertEqual(result["status"], "ok")  # success: only ran init + remote add


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_data_repo -v`
Expected: AttributeError for `reconcile_repo_state`.

- [ ] **Step 3: Implement `reconcile_repo_state` in `data_repo.py`**

Append to `data_repo.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_data_repo -v`
Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add data_repo.py tests/test_data_repo.py
git commit -m "feat(data_repo): reconcile_repo_state — init + remote sync"
```

---

## Task 7: `data_repo.git_test` and `git_status_summary`

**Files:**
- Modify: `data_repo.py`
- Modify: `tests/test_data_repo.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data_repo.py`:

```python
class TestGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        data_repo.set_data_dir(self.data_dir)
        sync_config.CONFIG_PATH = self.root / ".sync-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_off_mode(self):
        sync_config.save({"mode": "off", "remote_url": "", "branch": "main"})
        result = data_repo.git_test()
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "sync disabled")

    def test_local_mode_no_repo(self):
        sync_config.save({"mode": "local", "remote_url": "", "branch": "main"})
        result = data_repo.git_test()
        self.assertFalse(result["ok"])

    def test_local_mode_with_repo(self):
        sync_config.save({"mode": "local", "remote_url": "", "branch": "main"})
        self.data_dir.mkdir()
        _run_git(["init"], self.data_dir)
        result = data_repo.git_test()
        self.assertTrue(result["ok"])

    def test_remote_mode_unreachable_url(self):
        sync_config.save(
            {"mode": "remote", "remote_url": "https://nonexistent.invalid/x.git", "branch": "main"}
        )
        result = data_repo.git_test()
        self.assertFalse(result["ok"])
        # message should contain stderr text from git
        self.assertTrue(result["message"])

    def test_remote_mode_with_local_bare_remote(self):
        # set up a local bare repo to act as a reachable remote
        bare = self.root / "bare.git"
        _run_git(["init", "--bare", str(bare)], self.root)
        sync_config.save({"mode": "remote", "remote_url": str(bare), "branch": "main"})
        result = data_repo.git_test()
        self.assertTrue(result["ok"], msg=result["message"])


class TestGitStatusSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        data_repo.set_data_dir(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_repo(self):
        self.assertEqual(data_repo.git_status_summary(), "no-repo")

    def test_repo_no_remote(self):
        self.data_dir.mkdir()
        _run_git(["init"], self.data_dir)
        self.assertEqual(data_repo.git_status_summary(), "missing-remote")

    def test_repo_with_remote(self):
        self.data_dir.mkdir()
        _run_git(["init"], self.data_dir)
        _run_git(["remote", "add", "origin", "https://x/y.git"], self.data_dir)
        self.assertEqual(data_repo.git_status_summary(), "ok")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_data_repo.TestGitTest tests.test_data_repo.TestGitStatusSummary -v`
Expected: AttributeError for `git_test` and `git_status_summary`.

- [ ] **Step 3: Implement both in `data_repo.py`**

Append to `data_repo.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_data_repo -v`
Expected: 14 tests pass (7 reconcile + 5 git_test + 3 git_status_summary).

- [ ] **Step 5: Commit**

```bash
git add data_repo.py tests/test_data_repo.py
git commit -m "feat(data_repo): git_test (ls-remote) + git_status_summary"
```

---

## Task 8: HTTP handlers — GET/PUT/test endpoints

**Files:**
- Modify: `server.py` (handler additions + route registration)

- [ ] **Step 1: Add the routes**

In `server.py`, find the block around line 374-380 (where `/api/llm-config` routes are registered) and add the three new routes immediately above it:

```python
        # /api/sync/config
        if path == '/api/sync/config' and method == 'GET':
            return self._handle_get_sync_config()
        if path == '/api/sync/config' and method == 'PUT':
            return self._handle_put_sync_config()
        if path == '/api/sync/test' and method == 'POST':
            return self._handle_test_sync()
```

- [ ] **Step 2: Add the handlers**

Add these three handler methods to `RequestHandler` (anywhere in the class — convention: place after `_handle_sync_status`):

```python
def _handle_get_sync_config(self):
    cfg = sync_config.public_view()
    cfg["git_status"] = data_repo.git_status_summary()
    self._send_json(cfg)

def _handle_put_sync_config(self):
    try:
        body = self._read_body()
    except json.JSONDecodeError:
        return self._send_error(400, 'invalid json')
    if not isinstance(body, dict):
        return self._send_error(400, 'expected object')
    updates = sync_config.sanitize_user_updates(body)
    new_cfg = {**sync_config.load(), **updates}
    try:
        sync_config.validate(new_cfg)
    except sync_config.ValidationError as e:
        return self._send_error(400, str(e))
    old_cfg = sync_config.load()
    data_repo_existed = (DATA_DIR / '.git').exists()
    sync_config.save(updates)
    if sync_config.transition_sets_skip_pull(old_cfg, new_cfg, data_repo_existed):
        sync_config.set_skip_next_pull(True)
    reconcile = data_repo.reconcile_repo_state(sync_config.load())
    response = sync_config.public_view()
    response["git_status"] = data_repo.git_status_summary()
    response["reconcile"] = reconcile
    self._send_json(response)

def _handle_test_sync(self):
    self._send_json(data_repo.git_test())
```

- [ ] **Step 3: Smoke-test by curl**

Start the server in one shell: `python server.py`

In another:

```bash
curl -s http://localhost:8080/api/sync/config | python -m json.tool
```

Expected: `{"mode": "...", "remote_url": "...", "branch": "...", "git_status": "..."}`.

```bash
curl -s -X POST http://localhost:8080/api/sync/test | python -m json.tool
```

Expected: `{"ok": true|false, "message": "..."}`.

```bash
curl -s -X PUT http://localhost:8080/api/sync/config \
  -H 'Content-Type: application/json' \
  -d '{"mode":"weird","remote_url":"","branch":"main"}'
```

Expected: HTTP 400 with `mode must be one of ('off', 'local', 'remote')`.

Stop the server.

- [ ] **Step 4: Run unit tests**

Run: `python -m unittest discover tests -v`
Expected: all tests still pass.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(server): /api/sync/config GET/PUT and /api/sync/test"
```

---

## Task 9: Startup wiring — first-run migration + skip_next_pull consumption

**Files:**
- Modify: `server.py:922-925` (and import section)

- [ ] **Step 1: Update startup**

In `server.py`, replace the startup block (currently at the top of `if __name__ == '__main__':`):

```python
if __name__ == '__main__':
    # First-run migration: derive default config from existing data/ state.
    if not sync_config.CONFIG_PATH.exists():
        defaults = sync_config.migrate_defaults(
            DATA_DIR, fallback_remote_url=DATA_REPO_URL_FALLBACK,
        )
        sync_config.save(defaults)
        # migrate_defaults includes skip_next_pull=False so this is fine.
        print(f"sync_config: created defaults (mode={defaults['mode']})", flush=True)

    cfg = sync_config.load()

    # Reconcile data/ to match the config (idempotent, no network).
    rec = data_repo.reconcile_repo_state(cfg)
    print(f"sync: reconcile {rec['status']} — {rec['message']}", flush=True)

    # Auto-pull, unless suppressed by a recent config change.
    if cfg["mode"] == "remote" and not cfg["skip_next_pull"]:
        result = data_repo.git_sync_pull()
        print(f"sync: pull {result['status']} — {result['message']}", flush=True)
    elif cfg["skip_next_pull"]:
        print(
            "sync: auto-pull skipped after mode change — push your local commits "
            "first, then pull manually if desired",
            flush=True,
        )
        sync_config.set_skip_next_pull(False)

    ensure_data_dir()
```

Then keep the existing janitor block and `HTTPServer(...)` startup unchanged below this.

- [ ] **Step 2: Smoke-test the three modes**

For each smoke check, **back up your real `.sync-config.json` first** if it exists:

```bash
mv .sync-config.json .sync-config.json.bak 2>/dev/null
```

Test 1 — first-run migration:

```bash
rm -f .sync-config.json
python server.py &
sleep 2
cat .sync-config.json
# expect mode=remote with the inferred origin URL from data/.git
curl -s http://localhost:8080/api/sync/config
kill %1
```

Test 2 — set mode=off, restart, verify no pull happens:

```bash
curl -s -X PUT http://localhost:8080/api/sync/config \
  -H 'Content-Type: application/json' \
  -d '{"mode":"off","remote_url":"","branch":"main"}' &
# (server is down — re-run after starting)
```

Easier: edit `.sync-config.json` to `{"mode":"off",...}` and run `python server.py 2>&1 | head -20`. Confirm no `sync: pull` line appears.

Test 3 — set mode=local with a `skip_next_pull=true` config, restart, confirm the suppression message and that the flag is cleared:

```bash
python -c 'import json; p=".sync-config.json"; c=json.load(open(p)); c["skip_next_pull"]=True; c["mode"]="remote"; json.dump(c,open(p,"w"))'
python server.py 2>&1 | head -10
# expect: "sync: auto-pull skipped after mode change ..."
python -c 'import json; print(json.load(open(".sync-config.json"))["skip_next_pull"])'
# expect: False
```

Restore your config:

```bash
mv .sync-config.json.bak .sync-config.json 2>/dev/null
```

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat(server): startup wiring for mode-aware sync"
```

---

## Task 10: `.gitignore` + remove dead `ensure_data_repo` reference

**Files:**
- Modify: `.gitignore`
- Modify: `server.py`

- [ ] **Step 1: Add `.sync-config.json` to `.gitignore`**

Edit `.gitignore`. Insert after the `.llm-config.json` line:

```
.sync-config.json
```

- [ ] **Step 2: Remove the now-orphaned `ensure_data_repo` import/call**

`ensure_data_repo` previously lived in `server.py`; if any reference remains (search for it), delete it. The new startup block in Task 9 doesn't call it.

```bash
grep -n "ensure_data_repo" server.py
```

If anything is found, remove those lines.

- [ ] **Step 3: Commit**

```bash
git add .gitignore server.py
git commit -m "chore: gitignore .sync-config.json + drop dead ensure_data_repo refs"
```

---

## Task 11: UI — settings modal "Data sync" section

**Files:**
- Modify: `index.html` (around line 1244, end of LLM settings modal body)

- [ ] **Step 1: Insert the new section into the modal**

Find the closing of the LLM settings modal body. Currently lines 1241-1245:

```html
      <div class="modal-actions" style="display:flex;justify-content:flex-end;gap:8px;">
        <button id="btn-cfg-cancel" class="header-btn">Cancel</button>
        <button id="btn-cfg-save" class="btn-new-card">Save</button>
      </div>
    </div>
  </div>
</div>
```

Insert a new section *before* the action buttons (before line 1241), so the resulting order is: LLM section → Data sync section → action buttons:

```html
      <hr style="border:none;border-top:1px solid #dfe1e6;margin:16px 0;" />
      <h2 style="margin-top:0;">Data sync</h2>

      <div style="margin-bottom:12px;">
        <label class="field-label">Mode</label>
        <div style="display:flex;flex-direction:column;gap:6px;">
          <label style="display:flex;align-items:center;gap:8px;font-size:13px;">
            <input type="radio" name="sync-mode" value="off" /> Off (no git activity)
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:13px;">
            <input type="radio" name="sync-mode" value="local" /> Local (commit only)
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:13px;">
            <input type="radio" name="sync-mode" value="remote" /> Remote (commit + push)
          </label>
        </div>
      </div>

      <div id="sync-remote-fields" hidden>
        <div style="margin-bottom:12px;">
          <label class="field-label">Remote URL</label>
          <input id="sync-remote-url" type="text"
            placeholder="https://github.com/you/atc-content.git"
            style="width:100%;padding:6px 10px;border:1px solid #dfe1e6;border-radius:4px;font-size:14px;" />
        </div>
        <div style="margin-bottom:12px;">
          <label class="field-label">Branch</label>
          <input id="sync-branch" type="text"
            style="width:100%;padding:6px 10px;border:1px solid #dfe1e6;border-radius:4px;font-size:14px;" />
        </div>
        <div id="sync-privacy-warning"
          style="margin-bottom:12px;padding:8px 10px;background:#fff4e5;border:1px solid #ffb84d;border-radius:4px;font-size:13px;color:#7a4a00;">
          ⚠️ Your cards may contain personal or work-sensitive notes. Make sure the remote repository is <strong>private</strong>. ATC will not check this for you.
        </div>
      </div>

      <div style="margin-bottom:12px;font-size:12px;color:#5e6c84;">
        <span id="sync-git-status">Status: …</span>
      </div>

      <div class="modal-actions" style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
        <button id="btn-sync-test" class="header-btn">Test sync</button>
        <button id="btn-sync-pull" class="header-btn" hidden>Pull from remote</button>
        <span id="sync-status-msg" style="font-size:13px;"></span>
      </div>
```

- [ ] **Step 2: Manual visual check**

Reload the app in the browser, open Settings — verify the new section renders below the LLM section. The "Mode" radios are present; the URL/branch fields and privacy warning are hidden because no radio is checked yet (we'll wire that up next task).

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat(ui): settings modal — Data sync section markup"
```

---

## Task 12: UI — wire the Data sync section to the API

**Files:**
- Modify: `index.html` (settings JS, around line 2880-2935)

- [ ] **Step 1: Extend the `cfgEls` object and wire the section**

Find the `cfgEls` declaration (around line 2880):

```javascript
const cfgEls = {
  overlay: document.getElementById('settings-overlay'),
  token: document.getElementById('cfg-token'),
  baseUrl: document.getElementById('cfg-base-url'),
  model: document.getElementById('cfg-model'),
  tls: document.getElementById('cfg-tls-verify'),
  status: document.getElementById('cfg-status'),
};
```

Replace with:

```javascript
const cfgEls = {
  overlay: document.getElementById('settings-overlay'),
  token: document.getElementById('cfg-token'),
  baseUrl: document.getElementById('cfg-base-url'),
  model: document.getElementById('cfg-model'),
  tls: document.getElementById('cfg-tls-verify'),
  status: document.getElementById('cfg-status'),
  syncRemoteFields: document.getElementById('sync-remote-fields'),
  syncRemoteUrl: document.getElementById('sync-remote-url'),
  syncBranch: document.getElementById('sync-branch'),
  syncGitStatus: document.getElementById('sync-git-status'),
  syncStatusMsg: document.getElementById('sync-status-msg'),
  syncPullBtn: document.getElementById('btn-sync-pull'),
};
```

- [ ] **Step 2: Update `openSettings` to also load sync config**

Find `openSettings()` (around line 2889) and replace with:

```javascript
async function openSettings() {
  const [cfg, sync] = await Promise.all([
    api.get('/api/llm-config'),
    api.get('/api/sync/config'),
  ]);
  // LLM
  cfgEls.token.value = '';
  cfgEls.token.placeholder = cfg.auth_token || 'paste token';
  cfgEls.baseUrl.value = cfg.base_url || '';
  cfgEls.model.value = cfg.model || 'claude-opus-4-7';
  cfgEls.tls.checked = !!cfg.tls_verify;
  cfgEls.status.textContent = '';
  // Sync
  document
    .querySelector(`input[name="sync-mode"][value="${sync.mode}"]`)
    .checked = true;
  cfgEls.syncRemoteUrl.value = sync.remote_url || '';
  cfgEls.syncBranch.value = sync.branch || 'main';
  applySyncModeVisibility();
  cfgEls.syncGitStatus.textContent = `Status: ${describeGitStatus(sync.git_status, sync.mode)}`;
  cfgEls.syncStatusMsg.textContent = '';
  cfgEls.overlay.classList.add('visible');
}

function applySyncModeVisibility() {
  const sel = document.querySelector('input[name="sync-mode"]:checked');
  const mode = sel ? sel.value : 'off';
  cfgEls.syncRemoteFields.hidden = (mode !== 'remote');
  cfgEls.syncPullBtn.hidden = (mode !== 'remote');
}

function describeGitStatus(status, mode) {
  if (mode === 'off') return 'sync disabled';
  if (status === 'ok') return 'repository OK · remote configured';
  if (status === 'no-repo') return 'not initialized — saving will run `git init`';
  if (status === 'missing-remote') return 'repository OK · no remote configured';
  return status || 'unknown';
}

document.querySelectorAll('input[name="sync-mode"]').forEach((el) => {
  el.addEventListener('change', applySyncModeVisibility);
});
```

- [ ] **Step 3: Wire Save / Test / Pull buttons**

Find the existing `btn-cfg-save` handler (around line 2911) and replace with this combined save (LLM + sync):

```javascript
document.getElementById('btn-cfg-save').addEventListener('click', async () => {
  // LLM
  const llmBody = {
    base_url: cfgEls.baseUrl.value,
    model: cfgEls.model.value,
    tls_verify: cfgEls.tls.checked,
  };
  if (cfgEls.token.value) llmBody.auth_token = cfgEls.token.value;
  // Sync
  const modeEl = document.querySelector('input[name="sync-mode"]:checked');
  const syncBody = {
    mode: modeEl ? modeEl.value : 'off',
    remote_url: cfgEls.syncRemoteUrl.value.trim(),
    branch: cfgEls.syncBranch.value.trim() || 'main',
  };
  try {
    await api.put('/api/llm-config', llmBody);
    const syncResp = await api.put('/api/sync/config', syncBody);
    if (syncResp.reconcile && syncResp.reconcile.status === 'error') {
      cfgEls.status.textContent = 'Saved (sync warning: ' + syncResp.reconcile.message + ')';
    } else {
      closeSettings();
      refreshNotesButtonState();
      checkSyncStatus();
    }
  } catch (err) {
    cfgEls.status.textContent = 'Save failed: ' + err.message;
  }
});

document.getElementById('btn-sync-test').addEventListener('click', async () => {
  cfgEls.syncStatusMsg.textContent = 'Testing…';
  try {
    const r = await api.post('/api/sync/test');
    cfgEls.syncStatusMsg.textContent = (r.ok ? '✓ ' : '✗ ') + (r.message || '');
  } catch (err) {
    cfgEls.syncStatusMsg.textContent = '✗ ' + err.message;
  }
});

document.getElementById('btn-sync-pull').addEventListener('click', async () => {
  cfgEls.syncStatusMsg.textContent = 'Pulling…';
  try {
    const r = await api.post('/api/sync/pull');
    cfgEls.syncStatusMsg.textContent =
      (r.status === 'ok' ? '✓ ' : '✗ ') + (r.message || r.status);
  } catch (err) {
    cfgEls.syncStatusMsg.textContent = '✗ ' + err.message;
  }
});
```

- [ ] **Step 4: Manual UI test**

Start the server and reload the browser. Open Settings:

1. Verify the current mode is selected and URL/branch populated.
2. Switch radio to "Off" → URL/branch fields hide, privacy warning hides, Pull button hides.
3. Switch back to "Remote" → fields and warning re-appear.
4. Click "Test sync" → status line shows ✓ or ✗.
5. Click "Save" → modal closes (or shows reconcile warning).
6. Reopen Settings → values persist.

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(ui): wire Data sync settings (load/save/test/pull/visibility)"
```

---

## Task 13: UI — header sync button reads mode

**Files:**
- Modify: `index.html` (around line 2837-2875 — header sync button)

- [ ] **Step 1: Update `checkSyncStatus` and the click handler**

Find the existing `checkSyncStatus` (around line 2865) and the click handler above it (around line 2837). Replace both with:

```javascript
let currentSyncMode = 'remote'; // updated by checkSyncStatus

document.getElementById('btn-sync').addEventListener('click', async () => {
  if (currentSyncMode === 'off') return; // disabled
  const btn = document.getElementById('btn-sync');
  const verb = currentSyncMode === 'local' ? 'Committing' : 'Syncing';
  const okLabel = currentSyncMode === 'local' ? '✓ Committed' : '✓ Synced';
  btn.classList.add('syncing');
  btn.textContent = '⟳ ' + verb + '...';
  try {
    const result = await api.post('/api/sync/push');
    btn.classList.remove('syncing');
    if (result.status === 'ok') {
      btn.classList.add('sync-ok');
      btn.textContent = okLabel;
    } else if (result.status === 'no-changes') {
      btn.classList.add('sync-ok');
      btn.textContent = '✓ Up to date';
    } else if (result.status === 'skipped') {
      btn.classList.add('sync-ok');
      btn.textContent = '○ ' + (result.message || 'skipped');
    } else {
      btn.classList.add('sync-err');
      btn.textContent = '✗ ' + (result.message || 'failed');
    }
  } catch (err) {
    btn.classList.remove('syncing');
    btn.classList.add('sync-err');
    btn.textContent = '✗ Sync failed';
  }
  setTimeout(() => {
    btn.classList.remove('sync-ok', 'sync-err');
    checkSyncStatus();
  }, 3000);
});

async function checkSyncStatus() {
  const btn = document.getElementById('btn-sync');
  if (
    btn.classList.contains('syncing') ||
    btn.classList.contains('sync-ok') ||
    btn.classList.contains('sync-err')
  ) return;
  try {
    const result = await api.get('/api/sync/status');
    currentSyncMode = result.mode || 'remote';
    btn.disabled = (currentSyncMode === 'off');
    btn.classList.toggle('sync-dirty', !!result.dirty);
    if (currentSyncMode === 'off') {
      btn.textContent = 'Sync off';
    } else if (currentSyncMode === 'local') {
      btn.textContent = result.dirty ? '↻ Commit needed' : '↻ Commit';
    } else {
      btn.textContent = result.dirty ? '↻ Sync needed' : '↻ Sync';
    }
  } catch { }
}
checkSyncStatus();
setInterval(checkSyncStatus, 10000);
```

- [ ] **Step 2: Apply the same mode awareness to the notes-wizard "Sync now" button**

Find the existing notes-sync click handler (`index.html:3066-3077`):

```javascript
document.getElementById('btn-notes-sync').addEventListener('click', async () => {
  const btn = document.getElementById('btn-notes-sync');
  btn.disabled = true;
  btn.textContent = 'Syncing…';
  try {
    await api.post('/api/sync/push');
    btn.textContent = 'Synced ✓';
  } catch (err) {
    btn.textContent = '✗ ' + err.message;
    btn.disabled = false;
  }
});
```

Replace with:

```javascript
document.getElementById('btn-notes-sync').addEventListener('click', async () => {
  const btn = document.getElementById('btn-notes-sync');
  if (currentSyncMode === 'off') return;
  const verb = currentSyncMode === 'local' ? 'Committing' : 'Syncing';
  const okLabel = currentSyncMode === 'local' ? 'Committed ✓' : 'Synced ✓';
  btn.disabled = true;
  btn.textContent = verb + '…';
  try {
    await api.post('/api/sync/push');
    btn.textContent = okLabel;
  } catch (err) {
    btn.textContent = '✗ ' + err.message;
    btn.disabled = false;
  }
});
```

Also update the show condition at `index.html:3059`:

```javascript
document.getElementById('btn-notes-sync').hidden = applied.length === 0;
```

Replace with:

```javascript
document.getElementById('btn-notes-sync').hidden =
  applied.length === 0 || currentSyncMode === 'off';
```

- [ ] **Step 3: Manual UI test**

1. Set mode=remote in Settings, save → header button reads "↻ Sync".
2. Set mode=local → header button reads "↻ Commit".
3. Set mode=off → header button reads "Sync off" and is disabled.
4. Edit a card to make `data/` dirty → header reads "↻ Commit needed" or "↻ Sync needed" depending on mode.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat(ui): mode-aware header + notes-wizard sync buttons"
```

---

## Task 14: End-to-end manual smoke test (the user's scenario)

This task has no code. It's a final verification that the user's "remote → local → remote" scenario from the spec works correctly.

**Files:** none

- [ ] **Step 1: Start fresh from the current `data/` (real repo with real origin)**

Confirm starting state:

```bash
cat .sync-config.json
# expect: mode=remote, remote_url=<your real GitHub URL>, branch=main
```

- [ ] **Step 2: Make a card change, sync from header**

Open the app, edit a card, click "↻ Sync" in the header. Confirm: the change is committed and pushed (check your remote on GitHub).

- [ ] **Step 3: Switch to mode=local in Settings**

Save. Header button changes to "↻ Commit". Modal status reads "repository OK · remote configured".

- [ ] **Step 4: Make another card change, click "↻ Commit"**

Confirm: the change is committed locally. `git log -1` in `data/` shows the new commit. `git status` is clean. The remote on GitHub does NOT have the new commit.

- [ ] **Step 5: Switch back to mode=remote in Settings**

Save. Open `.sync-config.json`:

```bash
cat .sync-config.json
# expect: skip_next_pull: true
```

- [ ] **Step 6: Restart the server**

```bash
# Ctrl-C the running server, then:
python server.py 2>&1 | head -10
```

Expected output includes the line:
`sync: auto-pull skipped after mode change — push your local commits first, then pull manually if desired`

After startup, `.sync-config.json` should have `skip_next_pull: false` (cleared).

- [ ] **Step 7: Click "↻ Sync" in the header**

The local commit from Step 4 is pushed to the remote. Verify on GitHub.

- [ ] **Step 8: Restart the server again**

Confirm the auto-pull runs normally this time (no suppression message).

- [ ] **Step 9: Final sanity — all unit tests still pass**

```bash
python -m unittest discover tests -v
```

Expected: every test passes.

- [ ] **Step 10: Commit nothing — but record completion**

If you found issues during smoke testing, file them as separate fix commits. Otherwise the feature is done.

```bash
git log --oneline -20
```

You should see ~13 commits from this plan plus the earlier janitor archive commit.

---
