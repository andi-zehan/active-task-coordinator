# Notes-to-Cards Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a UI button that lets the user paste/type a meeting note, calls the corporate Anthropic gateway to propose card operations across all kanban boards, lets the user review and approve them, and applies the approved subset. Includes a janitor that auto-deletes done cards older than 14 days and orphan notes.

**Architecture:** Two new Python modules (`notes.py`, `janitor.py`) imported by `server.py`. Seven new HTTP endpoints (LLM config CRUD/test, notes analyze/apply/get, janitor run). Notes archived to `./notes/` (gitignored, local-only). LLM config in `./.llm-config.json` (gitignored). UI gets two new header buttons that open modals: settings and a 3-step notes wizard.

**Tech Stack:** Python 3 stdlib `http.server`, `anthropic` SDK (corp gateway via `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`), `httpx` for the SDK's HTTP client (set `verify=False` for the gateway's self-signed cert), vanilla JS in `index.html`, `unittest` for tests.

**Spec:** `docs/superpowers/specs/2026-04-27-notes-to-cards-design.md`

---

## File Structure

**New files:**
- `notes.py` — board snapshot, LLM call, operation execution, note archive helpers.
- `janitor.py` — sweep done cards older than 14 days, sweep orphan notes.
- `llm_config.py` — load/save/mask `./.llm-config.json`, build SDK client.
- `tests/test_notes.py` — unit tests for snapshot, archive, apply.
- `tests/test_janitor.py` — unit tests for the two sweep functions.
- `tests/test_llm_config.py` — unit tests for config load/save/mask.
- `notes/` — auto-created on first analyze; gitignored.
- `./.llm-config.json` — auto-created on first save; gitignored.

**Modified:**
- `server.py` — import new modules; add seven route handlers; kick off janitor on startup + 24 h timer thread.
- `index.html` — two header buttons (`⚙ Settings`, `📝 Process Notes`), two modals, JS for the wizard.
- `.gitignore` — add `.llm-config.json` and `notes/`.
- `requirements.txt` — create with `anthropic` and `httpx` (the project currently has none; SDK is the first external dep).

**Boundaries:**
- `llm_config.py` knows nothing about notes or kanban. It loads JSON, masks tokens, hands back a configured `anthropic.Anthropic` client.
- `notes.py` knows about kanban (uses `server.read_card`, `server.write_card`, etc.) and about the LLM (uses `llm_config.get_client`). It is the integration layer.
- `janitor.py` knows about kanban file layout and `notes/` directory. It does not touch the LLM.
- `server.py` is wiring only: parse request, call into one of the new modules, return JSON.

---

## Task 1: Add `anthropic` SDK dependency and gitignore entries

**Files:**
- Create: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Create `requirements.txt`**

Create `D:\Claude\ATC\requirements.txt` with:

```
anthropic>=0.40.0
httpx>=0.27.0
```

- [ ] **Step 2: Add new gitignore entries**

Open `D:\Claude\ATC\.gitignore`. It currently contains:

```
data/
__pycache__/
*.pyc
.superpowers/
```

Append two lines so it becomes:

```
data/
__pycache__/
*.pyc
.superpowers/
.llm-config.json
notes/
```

- [ ] **Step 3: Install the dependency**

Run: `pip install -r requirements.txt`
Expected output: `Successfully installed anthropic-... httpx-...`

- [ ] **Step 4: Verify the import works**

Run: `python -c "import anthropic; import httpx; print(anthropic.__version__, httpx.__version__)"`
Expected: two version strings printed, no traceback.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore
git commit -m "chore: add anthropic SDK dep and gitignore entries for notes-to-cards"
```

---

## Task 2: `llm_config.py` — load and mask config

**Files:**
- Create: `llm_config.py`
- Test: `tests/test_llm_config.py`

- [ ] **Step 1: Write the failing tests**

Create `D:\Claude\ATC\tests\test_llm_config.py`:

```python
#!/usr/bin/env python3
"""Tests for llm_config module."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import llm_config


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".llm-config.json"
        llm_config.CONFIG_PATH = self.path

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_missing_returns_defaults(self):
        cfg = llm_config.load()
        self.assertEqual(cfg["auth_token"], "")
        self.assertEqual(cfg["model"], "claude-opus-4-7")
        self.assertTrue(cfg["base_url"].startswith("https://"))
        self.assertFalse(cfg["tls_verify"])

    def test_save_then_load_roundtrip(self):
        llm_config.save({
            "base_url": "https://example.com",
            "auth_token": "secret-token-xyz",
            "model": "claude-sonnet-4-6",
            "tls_verify": True,
        })
        cfg = llm_config.load()
        self.assertEqual(cfg["base_url"], "https://example.com")
        self.assertEqual(cfg["auth_token"], "secret-token-xyz")
        self.assertEqual(cfg["model"], "claude-sonnet-4-6")
        self.assertTrue(cfg["tls_verify"])

    def test_save_partial_keeps_existing_token(self):
        llm_config.save({"auth_token": "original-token"})
        llm_config.save({"model": "claude-haiku-4-5"})
        cfg = llm_config.load()
        self.assertEqual(cfg["auth_token"], "original-token")
        self.assertEqual(cfg["model"], "claude-haiku-4-5")

    def test_save_explicit_empty_token_clears(self):
        llm_config.save({"auth_token": "original"})
        llm_config.save({"auth_token": ""})
        cfg = llm_config.load()
        self.assertEqual(cfg["auth_token"], "")


class TestMask(unittest.TestCase):
    def test_short_token_fully_masked(self):
        self.assertEqual(llm_config.mask_token("abc"), "***")

    def test_long_token_shows_last_four(self):
        self.assertEqual(llm_config.mask_token("supersecret1234"), "****1234")

    def test_empty_token(self):
        self.assertEqual(llm_config.mask_token(""), "")


class TestPublicView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        llm_config.CONFIG_PATH = Path(self.tmp.name) / ".llm-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_public_view_when_unconfigured(self):
        view = llm_config.public_view()
        self.assertFalse(view["configured"])
        self.assertEqual(view["auth_token"], "")

    def test_public_view_masks_token(self):
        llm_config.save({"auth_token": "supersecret1234"})
        view = llm_config.public_view()
        self.assertTrue(view["configured"])
        self.assertEqual(view["auth_token"], "****1234")
        self.assertNotIn("supersecret", json.dumps(view))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_llm_config -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_config'`.

- [ ] **Step 3: Implement `llm_config.py`**

Create `D:\Claude\ATC\llm_config.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_llm_config -v`
Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add llm_config.py tests/test_llm_config.py
git commit -m "feat: llm_config module — load/save/mask gateway settings"
```

---

## Task 3: `llm_config.get_client()` — build configured Anthropic client

**Files:**
- Modify: `llm_config.py`
- Test: `tests/test_llm_config.py`

- [ ] **Step 1: Write the failing test**

Append to `D:\Claude\ATC\tests\test_llm_config.py` before the `if __name__` line:

```python
class TestGetClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        llm_config.CONFIG_PATH = Path(self.tmp.name) / ".llm-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_unconfigured_raises(self):
        with self.assertRaises(llm_config.NotConfigured):
            llm_config.get_client()

    def test_configured_returns_client(self):
        llm_config.save({
            "auth_token": "test-token",
            "base_url": "https://example.com",
        })
        client = llm_config.get_client()
        # Anthropic client exposes .messages
        self.assertTrue(hasattr(client, "messages"))
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_llm_config.TestGetClient -v`
Expected: FAIL with `AttributeError: module 'llm_config' has no attribute 'NotConfigured'`.

- [ ] **Step 3: Implement `get_client` and `NotConfigured`**

In `D:\Claude\ATC\llm_config.py`, add at the top (after imports):

```python
import httpx
import anthropic


class NotConfigured(Exception):
    """Raised when an LLM call is attempted without an auth token configured."""
```

Add at the bottom of the file:

```python
def get_client():
    """Build a fresh Anthropic client from the current config.

    Reads the config file on every call so token rotation is immediate.
    Returns an anthropic.Anthropic instance configured for the corp gateway.
    """
    cfg = load()
    if not cfg["auth_token"]:
        raise NotConfigured("LLM auth token not configured")

    http_client = httpx.Client(verify=cfg["tls_verify"])
    return anthropic.Anthropic(
        base_url=cfg["base_url"],
        auth_token=cfg["auth_token"],
        http_client=http_client,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_llm_config -v`
Expected: all 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add llm_config.py tests/test_llm_config.py
git commit -m "feat: llm_config.get_client builds gateway-configured Anthropic client"
```

---

## Task 4: `notes.py` — board snapshot

**Files:**
- Create: `notes.py`
- Test: `tests/test_notes.py`

- [ ] **Step 1: Write the failing test**

Create `D:\Claude\ATC\tests\test_notes.py`:

```python
#!/usr/bin/env python3
"""Tests for notes module."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import notes


def make_card(data_dir: Path, board: str, lst: str, slug: str, body: str = ""):
    """Helper: create a card and add to the list's _order.json."""
    list_dir = data_dir / "boards" / board / lst
    list_dir.mkdir(parents=True, exist_ok=True)
    (list_dir / f"{slug}.md").write_text(body, encoding="utf-8")
    order_file = list_dir / "_order.json"
    order = json.loads(order_file.read_text()) if order_file.exists() else []
    order.append(slug)
    order_file.write_text(json.dumps(order), encoding="utf-8")


def make_board(data_dir: Path, slug: str, name: str = "Test Board"):
    board_dir = data_dir / "boards" / slug
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "_board.md").write_text(
        f"---\nname: {name}\ncolor: '#000000'\n---\n", encoding="utf-8"
    )
    for lst in ("ideas", "backlog", "in-progress", "done"):
        (board_dir / lst).mkdir(exist_ok=True)
        (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
    boards_order = data_dir / "_boards-order.json"
    order = json.loads(boards_order.read_text()) if boards_order.exists() else []
    if slug not in order:
        order.append(slug)
        boards_order.write_text(json.dumps(order), encoding="utf-8")


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir, "alpha", "Alpha Project")
        make_card(self.data_dir, "alpha", "backlog", "draft-spec",
            "---\ntitle: Draft spec\nlabels: [doc]\ndue: 2026-05-01\nassignee: Me\n"
            "created: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\nWrite the spec.\n\n\n"
            "## Checklist\n\n- [ ] Outline\n- [x] Title\n\n\n"
            "## Comments\n\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_includes_board(self):
        snap = notes.build_snapshot()
        self.assertIn("alpha", [b["slug"] for b in snap["boards"]])

    def test_snapshot_includes_card_with_compact_fields(self):
        snap = notes.build_snapshot()
        cards = snap["boards"][0]["cards"]
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual(c["b"], "alpha")
        self.assertEqual(c["l"], "backlog")
        self.assertEqual(c["s"], "draft-spec")
        self.assertEqual(c["title"], "Draft spec")
        self.assertEqual(c["labels"], ["doc"])
        self.assertEqual(c["due"], "2026-05-01")
        self.assertEqual(c["assignee"], "Me")
        self.assertIn("Outline", c["todo"])
        self.assertIn("Title", c["done"])

    def test_snapshot_truncates_description_to_200(self):
        long_desc = "x" * 500
        make_card(self.data_dir, "alpha", "ideas", "long-card",
            f"---\ntitle: Long\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            f"## Description\n\n{long_desc}\n\n\n## Checklist\n\n\n## Comments\n\n")
        snap = notes.build_snapshot()
        all_cards = [c for b in snap["boards"] for c in b["cards"]]
        long = next(c for c in all_cards if c["s"] == "long-card")
        self.assertLessEqual(len(long["desc"]), 200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_notes -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notes'`.

- [ ] **Step 3: Implement the snapshot**

Create `D:\Claude\ATC\notes.py`:

```python
"""Notes-to-cards integration: snapshot, archive, LLM call, apply."""
import json
import re
from datetime import date
from pathlib import Path

import server
import llm_config

NOTES_DIR = Path(__file__).parent / "notes"


def _parse_checklist(body: str) -> tuple[list[str], list[str]]:
    """Return (open_items, done_items) from a card body's '## Checklist' section."""
    todo, done = [], []
    in_checklist = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_checklist = stripped == "## Checklist"
            continue
        if not in_checklist:
            continue
        m = re.match(r"-\s*\[(\s|x|X)\]\s*(.*)", stripped)
        if not m:
            continue
        text = m.group(2).strip()
        if m.group(1).lower() == "x":
            done.append(text)
        else:
            todo.append(text)
    return todo, done


def _extract_description(body: str) -> str:
    """Return the text under '## Description', truncated to 200 chars."""
    desc_lines = []
    in_desc = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_desc = stripped == "## Description"
            continue
        if in_desc and stripped:
            desc_lines.append(stripped)
    text = " ".join(desc_lines)
    return text[:200]


def build_snapshot() -> dict:
    """Build a compact JSON snapshot of all boards and their cards.

    Used as the user-content cache prefix for the LLM call.
    """
    boards_order_path = server.DATA_DIR / "_boards-order.json"
    if not boards_order_path.exists():
        return {"boards": []}
    board_slugs = json.loads(boards_order_path.read_text(encoding="utf-8"))

    boards = []
    for board_slug in board_slugs:
        board_meta = server.read_board_meta(board_slug)
        if board_meta is None:
            continue
        cards = []
        for list_slug in server.LISTS:
            list_dir = server.DATA_DIR / "boards" / board_slug / list_slug
            order_file = list_dir / "_order.json"
            if not order_file.exists():
                continue
            slugs = json.loads(order_file.read_text(encoding="utf-8"))
            for card_slug in slugs:
                card = server.read_card(board_slug, list_slug, card_slug)
                if card is None:
                    continue
                todo, done = _parse_checklist(card.get("body", ""))
                cards.append({
                    "b": board_slug,
                    "l": list_slug,
                    "s": card_slug,
                    "title": card.get("title", ""),
                    "labels": card.get("labels") or [],
                    "due": card.get("due", ""),
                    "assignee": card.get("assignee", ""),
                    "todo": todo,
                    "done": done,
                    "desc": _extract_description(card.get("body", "")),
                })
        boards.append({
            "slug": board_slug,
            "name": board_meta.get("name", board_slug),
            "cards": cards,
        })
    return {"boards": boards, "today": date.today().isoformat()}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_notes -v`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add notes.py tests/test_notes.py
git commit -m "feat: notes.build_snapshot — compact board snapshot for LLM"
```

---

## Task 5: `notes.py` — archive a pasted note

**Files:**
- Modify: `notes.py`
- Test: `tests/test_notes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notes.py` before `if __name__`:

```python
class TestArchiveNote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_creates_dir_and_file(self):
        note_id = notes.archive_note("Hello world", "Q2 Planning")
        self.assertTrue(notes.NOTES_DIR.exists())
        self.assertTrue((notes.NOTES_DIR / f"{note_id}.md").exists())

    def test_archive_filename_format(self):
        note_id = notes.archive_note("body", "Q2 Planning")
        # YYYY-MM-DD-slug
        self.assertRegex(note_id, r"^\d{4}-\d{2}-\d{2}-q2-planning$")

    def test_archive_default_title(self):
        note_id = notes.archive_note("body", "")
        self.assertRegex(note_id, r"^\d{4}-\d{2}-\d{2}-untitled-\d{6}$")

    def test_archive_preserves_body(self):
        note_id = notes.archive_note("Line1\nLine2\n", "Title")
        text = (notes.NOTES_DIR / f"{note_id}.md").read_text(encoding="utf-8")
        self.assertIn("Line1\nLine2", text)

    def test_archive_writes_frontmatter(self):
        note_id = notes.archive_note("body", "Title")
        text = (notes.NOTES_DIR / f"{note_id}.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("title: Title", text)
        self.assertIn("applied_ops: []", text)

    def test_archive_collision_suffix(self):
        notes.archive_note("a", "Same Title")
        second = notes.archive_note("b", "Same Title")
        self.assertTrue(second.endswith("-2"))


class TestReadNote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_existing(self):
        note_id = notes.archive_note("body text", "Title")
        text = notes.read_note(note_id)
        self.assertIn("body text", text)

    def test_read_missing_returns_none(self):
        self.assertIsNone(notes.read_note("nonexistent"))

    def test_read_rejects_path_traversal(self):
        self.assertIsNone(notes.read_note("../server"))
        self.assertIsNone(notes.read_note("foo/bar"))
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_notes -v`
Expected: FAIL with `AttributeError: module 'notes' has no attribute 'archive_note'`.

- [ ] **Step 3: Implement archive and read**

In `D:\Claude\ATC\notes.py`, add at the top after imports:

```python
from datetime import datetime
```

Add at the bottom of the file:

```python
def _slugify(text: str) -> str:
    """Convert title to a filename-safe slug. Mirrors server.slugify."""
    return server.slugify(text)


def archive_note(body: str, title: str) -> str:
    """Save a pasted note to notes/<note_id>.md. Returns the note_id."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    if title.strip():
        slug = _slugify(title)
        display_title = title
    else:
        slug = "untitled-" + datetime.now().strftime("%H%M%S")
        display_title = "Untitled"

    base = f"{today}-{slug}"
    note_id = base
    n = 2
    while (NOTES_DIR / f"{note_id}.md").exists():
        note_id = f"{base}-{n}"
        n += 1

    frontmatter = (
        "---\n"
        f"date: {today}\n"
        f"title: {display_title}\n"
        "applied_ops: []\n"
        "---\n\n"
    )
    (NOTES_DIR / f"{note_id}.md").write_text(frontmatter + body, encoding="utf-8")
    return note_id


_NOTE_ID_RE = re.compile(r"^[\w\-.]+$")


def read_note(note_id: str) -> str | None:
    """Return the raw markdown of an archived note, or None if missing/invalid."""
    if not _NOTE_ID_RE.match(note_id):
        return None
    path = NOTES_DIR / f"{note_id}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_notes -v`
Expected: 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add notes.py tests/test_notes.py
git commit -m "feat: notes.archive_note + read_note for local note storage"
```

---

## Task 6: `notes.py` — LLM call

**Files:**
- Modify: `notes.py`
- Test: `tests/test_notes.py`

This task tests with a stubbed client (no real network call).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notes.py` before `if __name__`:

```python
class FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text, "type": "text"})()]


class FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeMessage(self._response_text)


class FakeClient:
    def __init__(self, response_text):
        self.messages = FakeMessages(response_text)


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"

    def tearDown(self):
        self.tmp.cleanup()

    def test_analyze_parses_json_response(self):
        fake_response = json.dumps({
            "summary": "Meeting summary",
            "operations": [
                {"op": "create_card", "board": "alpha", "list": "backlog",
                 "title": "New task", "confidence": "high", "reason": "explicit ask"}
            ],
        })
        client = FakeClient(fake_response)
        result = notes.analyze("note text", "Title", model="claude-opus-4-7", client=client)
        self.assertEqual(result["summary"], "Meeting summary")
        self.assertEqual(len(result["operations"]), 1)
        self.assertEqual(result["operations"][0]["title"], "New task")
        self.assertTrue(result["note_id"].endswith("-title"))

    def test_analyze_archives_note(self):
        client = FakeClient('{"summary": "", "operations": []}')
        result = notes.analyze("note body", "T", model="claude-opus-4-7", client=client)
        archived = (notes.NOTES_DIR / f"{result['note_id']}.md").read_text(encoding="utf-8")
        self.assertIn("note body", archived)

    def test_analyze_extracts_json_from_fenced_block(self):
        fenced = '```json\n{"summary": "x", "operations": []}\n```'
        client = FakeClient(fenced)
        result = notes.analyze("body", "T", model="claude-opus-4-7", client=client)
        self.assertEqual(result["summary"], "x")

    def test_analyze_invalid_json_raises(self):
        client = FakeClient("this is not json at all")
        with self.assertRaises(notes.LLMResponseError):
            notes.analyze("body", "T", model="claude-opus-4-7", client=client)

    def test_analyze_passes_model_and_caching(self):
        client = FakeClient('{"summary": "", "operations": []}')
        notes.analyze("body", "T", model="claude-sonnet-4-6", client=client)
        kwargs = client.messages.last_kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-4-6")
        # System prompt cached
        sys_block = kwargs["system"][0] if isinstance(kwargs["system"], list) else None
        self.assertIsNotNone(sys_block)
        self.assertEqual(sys_block.get("cache_control"), {"type": "ephemeral"})
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_notes.TestAnalyze -v`
Expected: FAIL with `AttributeError: module 'notes' has no attribute 'analyze'`.

- [ ] **Step 3: Implement `analyze` and the prompt**

Add to `D:\Claude\ATC\notes.py`:

```python
class LLMResponseError(Exception):
    """Raised when the LLM response can't be parsed as the expected JSON."""


SYSTEM_PROMPT = """You are an assistant that turns meeting notes into kanban card operations.

You will receive:
1. A snapshot of all kanban boards and their cards (compact JSON).
2. A meeting note (free-form text).

Produce a JSON object with this exact shape:

{
  "summary": "1-2 sentence summary of the meeting",
  "operations": [ ... ]
}

Each operation is one of:

- {"op": "create_card", "board": "<slug>", "list": "ideas|backlog|in-progress|done",
   "title": "...", "description": "...", "checklist": ["..."], "due": "YYYY-MM-DD",
   "assignee": "...", "labels": ["..."], "confidence": "high|med|low", "reason": "..."}

- {"op": "add_comment", "board": "<slug>", "list": "<slug>", "card": "<slug>",
   "text": "...", "confidence": "...", "reason": "..."}

- {"op": "tick_checklist", "board": "<slug>", "list": "<slug>", "card": "<slug>",
   "item": "<substring of an existing checklist item>",
   "confidence": "...", "reason": "..."}

- {"op": "add_checklist_item", "board": "<slug>", "list": "<slug>", "card": "<slug>",
   "item": "...", "confidence": "...", "reason": "..."}

- {"op": "move_card", "board": "<slug>", "list": "<slug>", "card": "<slug>",
   "target_list": "ideas|backlog|in-progress|done",
   "confidence": "...", "reason": "..."}

- {"op": "update_field", "board": "<slug>", "list": "<slug>", "card": "<slug>",
   "field": "due|assignee|labels", "value": <appropriate type>,
   "confidence": "...", "reason": "..."}

Rules:
- Only reference boards, lists, and cards that exist in the snapshot.
- For new cards, choose a list that fits the work's stage. Default to 'backlog'.
- Set confidence honestly: 'high' = explicit, 'med' = strongly implied, 'low' = speculative.
- Always include reason — what in the note made you propose this op.
- Output ONLY the JSON object. No prose, no markdown fences.
"""


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of the model's text response."""
    text = text.strip()
    # Strip ```json ... ``` fences if present
    fence = re.match(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMResponseError(f"Could not parse LLM response as JSON: {e}\n---\n{text[:500]}")


def analyze(body: str, title: str, *, model: str, client) -> dict:
    """Archive the note, snapshot the boards, call the LLM, return parsed result.

    Args:
        body: pasted note text.
        title: optional user-supplied title.
        model: Anthropic model ID.
        client: an Anthropic-compatible client (real or fake-for-tests).

    Returns:
        {"note_id": str, "summary": str, "operations": [...]}.
    """
    note_id = archive_note(body, title)
    snapshot = build_snapshot()

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "BOARD SNAPSHOT:\n" + json.dumps(snapshot),
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"NOTE_ID: {note_id}\n"
                            f"TODAY: {date.today().isoformat()}\n\n"
                            f"MEETING NOTE:\n{body}"
                        ),
                    },
                ],
            },
        ],
    )

    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    parsed = _extract_json(text)
    parsed["note_id"] = note_id
    parsed.setdefault("summary", "")
    parsed.setdefault("operations", [])
    return parsed
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_notes -v`
Expected: 17 tests pass.

- [ ] **Step 5: Commit**

```bash
git add notes.py tests/test_notes.py
git commit -m "feat: notes.analyze — LLM call with prompt caching, JSON output"
```

---

## Task 7: `notes.py` — apply operations

**Files:**
- Modify: `notes.py`
- Test: `tests/test_notes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notes.py`:

```python
class TestApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"
        make_board(self.data_dir, "alpha")
        self.note_id = notes.archive_note("body", "Test")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_create_card(self):
        ops = [{"op": "create_card", "board": "alpha", "list": "backlog",
                "title": "New thing", "description": "Do it", "checklist": ["step 1"],
                "due": "2026-05-10", "assignee": "Me", "labels": ["a"]}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["skipped"], [])
        card = server.read_card("alpha", "backlog", "new-thing")
        self.assertIsNotNone(card)
        self.assertEqual(card["title"], "New thing")
        # source attachment present
        self.assertTrue(any(a["url"].endswith(self.note_id) for a in card.get("attachments", [])))
        # in _order.json
        order = json.loads((self.data_dir / "boards/alpha/backlog/_order.json").read_text())
        self.assertIn("new-thing", order)

    def test_apply_add_comment(self):
        make_card(self.data_dir, "alpha", "in-progress", "do-stuff",
            "---\ntitle: Do stuff\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n\n## Comments\n\n")
        ops = [{"op": "add_comment", "board": "alpha", "list": "in-progress",
                "card": "do-stuff", "text": "Vendor confirmed pricing."}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        card = server.read_card("alpha", "in-progress", "do-stuff")
        self.assertIn("Vendor confirmed pricing.", card["body"])
        self.assertIn(self.note_id, card["body"])  # source link in comment

    def test_apply_tick_checklist(self):
        make_card(self.data_dir, "alpha", "backlog", "task",
            "---\ntitle: T\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n"
            "## Checklist\n\n- [ ] Confirm hotel\n- [ ] Book flight\n\n\n"
            "## Comments\n\n")
        ops = [{"op": "tick_checklist", "board": "alpha", "list": "backlog",
                "card": "task", "item": "hotel"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        card = server.read_card("alpha", "backlog", "task")
        self.assertIn("- [x] Confirm hotel", card["body"])
        self.assertIn("- [ ] Book flight", card["body"])

    def test_apply_skip_missing_target(self):
        ops = [{"op": "add_comment", "board": "alpha", "list": "backlog",
                "card": "does-not-exist", "text": "x"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("missing", result["skipped"][0]["reason"])

    def test_apply_skip_tick_item_not_found(self):
        make_card(self.data_dir, "alpha", "backlog", "task2",
            "---\ntitle: T\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n- [ ] Item A\n\n\n## Comments\n\n")
        ops = [{"op": "tick_checklist", "board": "alpha", "list": "backlog",
                "card": "task2", "item": "nonexistent"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_apply_move_card(self):
        make_card(self.data_dir, "alpha", "backlog", "movable",
            "---\ntitle: M\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n\n## Comments\n\n")
        ops = [{"op": "move_card", "board": "alpha", "list": "backlog",
                "card": "movable", "target_list": "in-progress"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        self.assertIsNone(server.read_card("alpha", "backlog", "movable"))
        self.assertIsNotNone(server.read_card("alpha", "in-progress", "movable"))

    def test_apply_records_in_note_frontmatter(self):
        ops = [{"op": "create_card", "board": "alpha", "list": "backlog", "title": "X"}]
        notes.apply_operations(ops, self.note_id)
        archived = (notes.NOTES_DIR / f"{self.note_id}.md").read_text()
        self.assertIn("create_card", archived)
        self.assertIn("alpha/backlog/x", archived)

    def test_apply_continues_on_partial_failure(self):
        ops = [
            {"op": "create_card", "board": "alpha", "list": "backlog", "title": "Good"},
            {"op": "add_comment", "board": "alpha", "list": "backlog", "card": "ghost", "text": "x"},
            {"op": "create_card", "board": "alpha", "list": "ideas", "title": "Also good"},
        ]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 2)
        self.assertEqual(len(result["skipped"]), 1)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_notes.TestApply -v`
Expected: FAIL with `AttributeError: module 'notes' has no attribute 'apply_operations'`.

- [ ] **Step 3: Implement `apply_operations`**

Add to `D:\Claude\ATC\notes.py`:

```python
NOTE_URL_PREFIX = "/api/notes/"


def _today_iso() -> str:
    return date.today().isoformat()


def _append_to_order(board: str, list_slug: str, card_slug: str) -> None:
    order_path = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
    order = json.loads(order_path.read_text(encoding="utf-8")) if order_path.exists() else []
    if card_slug not in order:
        order.append(card_slug)
    order_path.write_text(json.dumps(order, indent=2), encoding="utf-8")


def _remove_from_order(board: str, list_slug: str, card_slug: str) -> None:
    order_path = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
    if not order_path.exists():
        return
    order = json.loads(order_path.read_text(encoding="utf-8"))
    order = [s for s in order if s != card_slug]
    order_path.write_text(json.dumps(order, indent=2), encoding="utf-8")


def _build_card_body(description: str, checklist: list[str]) -> str:
    desc = description or ""
    items = "\n".join(f"- [ ] {item}" for item in (checklist or []))
    return (
        f"## Description\n\n{desc}\n\n\n"
        f"## Checklist\n\n{items}\n\n\n"
        f"## Comments\n\n"
    )


def _do_create_card(op: dict, note_id: str) -> dict:
    board = op["board"]
    list_slug = op["list"]
    if list_slug not in server.LISTS:
        raise ValueError(f"invalid list '{list_slug}'")
    if server.read_board_meta(board) is None:
        raise ValueError("target board missing")
    title = op["title"]
    slug = server.slugify(title)
    base_slug = slug
    n = 2
    while (server.DATA_DIR / "boards" / board / list_slug / f"{slug}.md").exists():
        slug = f"{base_slug}-{n}"
        n += 1
    today = _today_iso()
    meta = {
        "title": title,
        "created": today,
        "updated": today,
        "labels": op.get("labels") or [],
        "due": op.get("due", ""),
        "assignee": op.get("assignee", ""),
        "relations": [],
        "custom_fields": {},
        "attachments": [
            {"name": f"Source note: {note_id}", "url": f"{NOTE_URL_PREFIX}{note_id}"}
        ],
    }
    body = _build_card_body(op.get("description", ""), op.get("checklist") or [])
    server.write_card(board, list_slug, slug, meta, body)
    _append_to_order(board, list_slug, slug)
    return {"target": f"{board}/{list_slug}/{slug}"}


def _do_add_comment(op: dict, note_id: str) -> dict:
    board, list_slug, card_slug = op["board"], op["list"], op["card"]
    card = server.read_card(board, list_slug, card_slug)
    if card is None:
        raise ValueError("target card missing")
    body = card["body"]
    today = _today_iso()
    note_link = f"_(from [meeting note]({NOTE_URL_PREFIX}{note_id}))_"
    new_comment = f"\n**{today} - Agent:**\n{op['text']}\n\n{note_link}\n"
    body = body.rstrip() + "\n" + new_comment
    card["updated"] = today
    server.write_card(board, list_slug, card_slug, card, body)
    return {"target": f"{board}/{list_slug}/{card_slug}"}


def _do_tick_checklist(op: dict, note_id: str) -> dict:
    board, list_slug, card_slug = op["board"], op["list"], op["card"]
    card = server.read_card(board, list_slug, card_slug)
    if card is None:
        raise ValueError("target card missing")
    needle = op["item"].lower()
    new_lines = []
    matched = False
    for line in card["body"].splitlines():
        m = re.match(r"(\s*)-\s*\[\s\]\s*(.+)$", line)
        if m and not matched and needle in m.group(2).lower():
            new_lines.append(f"{m.group(1)}- [x] {m.group(2)}")
            matched = True
        else:
            new_lines.append(line)
    if not matched:
        raise ValueError("checklist item not found")
    card["updated"] = _today_iso()
    server.write_card(board, list_slug, card_slug, card, "\n".join(new_lines))
    return {"target": f"{board}/{list_slug}/{card_slug}"}


def _do_add_checklist_item(op: dict, note_id: str) -> dict:
    board, list_slug, card_slug = op["board"], op["list"], op["card"]
    card = server.read_card(board, list_slug, card_slug)
    if card is None:
        raise ValueError("target card missing")
    new_lines = []
    inserted = False
    in_checklist = False
    lines = card["body"].splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_checklist and not inserted:
                # Insert before leaving the section
                new_lines.append(f"- [ ] {op['item']}")
                inserted = True
            in_checklist = stripped == "## Checklist"
        new_lines.append(line)
    if in_checklist and not inserted:
        new_lines.append(f"- [ ] {op['item']}")
        inserted = True
    if not inserted:
        raise ValueError("no checklist section found")
    card["updated"] = _today_iso()
    server.write_card(board, list_slug, card_slug, card, "\n".join(new_lines))
    return {"target": f"{board}/{list_slug}/{card_slug}"}


def _do_move_card(op: dict, note_id: str) -> dict:
    board, list_slug, card_slug = op["board"], op["list"], op["card"]
    target = op["target_list"]
    if target not in server.LISTS:
        raise ValueError(f"invalid target_list '{target}'")
    card = server.read_card(board, list_slug, card_slug)
    if card is None:
        raise ValueError("target card missing")
    today = _today_iso()
    src = server.DATA_DIR / "boards" / board / list_slug / f"{card_slug}.md"
    dst_dir = server.DATA_DIR / "boards" / board / target
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{card_slug}.md"
    card["updated"] = today
    server.write_card(board, target, card_slug, card, card["body"])
    src.unlink(missing_ok=True)
    _remove_from_order(board, list_slug, card_slug)
    _append_to_order(board, target, card_slug)
    return {"target": f"{board}/{target}/{card_slug}"}


def _do_update_field(op: dict, note_id: str) -> dict:
    board, list_slug, card_slug = op["board"], op["list"], op["card"]
    field = op["field"]
    if field not in ("due", "assignee", "labels"):
        raise ValueError(f"field '{field}' not updatable")
    card = server.read_card(board, list_slug, card_slug)
    if card is None:
        raise ValueError("target card missing")
    card[field] = op["value"]
    card["updated"] = _today_iso()
    server.write_card(board, list_slug, card_slug, card, card["body"])
    return {"target": f"{board}/{list_slug}/{card_slug}"}


_HANDLERS = {
    "create_card": _do_create_card,
    "add_comment": _do_add_comment,
    "tick_checklist": _do_tick_checklist,
    "add_checklist_item": _do_add_checklist_item,
    "move_card": _do_move_card,
    "update_field": _do_update_field,
}


def _record_in_note(note_id: str, op: dict, target: str) -> None:
    """Append a one-line entry to the note's frontmatter applied_ops list."""
    path = NOTES_DIR / f"{note_id}.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    stamp = datetime.now().isoformat(timespec="seconds")
    entry = f"  - {{op: {op['op']}, target: {target}, at: '{stamp}'}}\n"
    if "applied_ops: []" in text:
        text = text.replace("applied_ops: []", "applied_ops:\n" + entry.rstrip("\n"))
    else:
        text = text.replace("applied_ops:\n", "applied_ops:\n" + entry, 1)
    path.write_text(text, encoding="utf-8")


def apply_operations(operations: list[dict], note_id: str) -> dict:
    """Run each operation. Skip ones whose target is gone. Always continue."""
    applied = []
    skipped = []
    for op in operations:
        handler = _HANDLERS.get(op.get("op"))
        if handler is None:
            skipped.append({"op": op, "reason": f"unknown op '{op.get('op')}'"})
            continue
        try:
            outcome = handler(op, note_id)
            applied.append({"op": op["op"], "target": outcome["target"]})
            _record_in_note(note_id, op, outcome["target"])
        except (ValueError, KeyError, FileNotFoundError) as e:
            skipped.append({"op": op, "reason": str(e)})
    return {"applied": applied, "skipped": skipped}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_notes -v`
Expected: 25 tests pass.

- [ ] **Step 5: Commit**

```bash
git add notes.py tests/test_notes.py
git commit -m "feat: notes.apply_operations — execute LLM-proposed card ops"
```

---

## Task 8: `janitor.py` — sweep done cards older than 14 days

**Files:**
- Create: `janitor.py`
- Test: `tests/test_janitor.py`

- [ ] **Step 1: Write the failing tests**

Create `D:\Claude\ATC\tests\test_janitor.py`:

```python
#!/usr/bin/env python3
"""Tests for janitor module."""

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import janitor


def make_board(data_dir: Path, slug: str = "alpha"):
    board_dir = data_dir / "boards" / slug
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "_board.md").write_text("---\nname: A\ncolor: '#000'\n---\n", encoding="utf-8")
    for lst in ("ideas", "backlog", "in-progress", "done"):
        (board_dir / lst).mkdir(exist_ok=True)
        (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
    boards_order = data_dir / "_boards-order.json"
    order = json.loads(boards_order.read_text()) if boards_order.exists() else []
    if slug not in order:
        order.append(slug)
        boards_order.write_text(json.dumps(order), encoding="utf-8")


def make_card(data_dir: Path, board: str, lst: str, slug: str, updated: str,
              attachments: list[dict] | None = None):
    list_dir = data_dir / "boards" / board / lst
    list_dir.mkdir(parents=True, exist_ok=True)
    att_lines = ""
    if attachments:
        att_lines = "attachments:\n" + "".join(
            f"  - name: {a['name']}\n    url: {a['url']}\n" for a in attachments
        )
    else:
        att_lines = "attachments: []\n"
    (list_dir / f"{slug}.md").write_text(
        f"---\ntitle: {slug}\ncreated: 2026-01-01\nupdated: {updated}\n{att_lines}---\n\n"
        f"## Description\n\n\n\n## Checklist\n\n\n## Comments\n\n",
        encoding="utf-8",
    )
    order_file = list_dir / "_order.json"
    order = json.loads(order_file.read_text()) if order_file.exists() else []
    if slug not in order:
        order.append(slug)
        order_file.write_text(json.dumps(order), encoding="utf-8")


class TestSweepDoneCards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_done_card_deleted(self):
        old = (date.today() - timedelta(days=15)).isoformat()
        make_card(self.data_dir, "alpha", "done", "old-task", old)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 1)
        self.assertFalse((self.data_dir / "boards/alpha/done/old-task.md").exists())
        order = json.loads((self.data_dir / "boards/alpha/done/_order.json").read_text())
        self.assertNotIn("old-task", order)

    def test_recent_done_card_kept(self):
        recent = (date.today() - timedelta(days=7)).isoformat()
        make_card(self.data_dir, "alpha", "done", "recent-task", recent)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.data_dir / "boards/alpha/done/recent-task.md").exists())

    def test_exactly_14_days_kept(self):
        edge = (date.today() - timedelta(days=14)).isoformat()
        make_card(self.data_dir, "alpha", "done", "edge-task", edge)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)

    def test_15_days_deleted(self):
        edge = (date.today() - timedelta(days=15)).isoformat()
        make_card(self.data_dir, "alpha", "done", "edge-task", edge)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 1)

    def test_card_in_other_list_not_touched(self):
        old = (date.today() - timedelta(days=30)).isoformat()
        make_card(self.data_dir, "alpha", "backlog", "old-backlog", old)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.data_dir / "boards/alpha/backlog/old-backlog.md").exists())

    def test_invalid_updated_date_skipped(self):
        make_card(self.data_dir, "alpha", "done", "bad-date", "not-a-date")
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.data_dir / "boards/alpha/done/bad-date.md").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_janitor -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'janitor'`.

- [ ] **Step 3: Implement `sweep_done_cards`**

Create `D:\Claude\ATC\janitor.py`:

```python
"""Periodic cleanup: delete done cards older than 14 days, delete orphan notes."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import server

DONE_RETENTION_DAYS = 14
NOTES_DIR = Path(__file__).parent / "notes"


def _list_board_slugs() -> list[str]:
    path = server.DATA_DIR / "_boards-order.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def sweep_done_cards() -> int:
    """Delete cards in 'done' lists whose updated date is > 14 days ago.

    Returns the count of deleted cards.
    """
    cutoff = date.today() - timedelta(days=DONE_RETENTION_DAYS)
    deleted = 0
    for board in _list_board_slugs():
        done_dir = server.DATA_DIR / "boards" / board / "done"
        order_path = done_dir / "_order.json"
        if not done_dir.exists() or not order_path.exists():
            continue
        slugs = json.loads(order_path.read_text(encoding="utf-8"))
        kept = []
        for slug in slugs:
            card = server.read_card(board, "done", slug)
            if card is None:
                continue
            updated = _parse_date(card.get("updated", ""))
            if updated is None:
                # conservative: keep cards we can't date
                kept.append(slug)
                continue
            if updated < cutoff:
                card_path = done_dir / f"{slug}.md"
                card_path.unlink(missing_ok=True)
                deleted += 1
            else:
                kept.append(slug)
        order_path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return deleted
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_janitor -v`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add janitor.py tests/test_janitor.py
git commit -m "feat: janitor.sweep_done_cards — drop done cards older than 14d"
```

---

## Task 9: `janitor.py` — sweep orphan notes

**Files:**
- Modify: `janitor.py`
- Test: `tests/test_janitor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_janitor.py`:

```python
class TestSweepOrphanNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir)
        self.notes_dir = Path(self.tmp.name) / "notes"
        self.notes_dir.mkdir()
        janitor.NOTES_DIR = self.notes_dir

    def tearDown(self):
        self.tmp.cleanup()

    def _write_note(self, note_id: str):
        (self.notes_dir / f"{note_id}.md").write_text(
            f"---\ndate: 2026-04-01\ntitle: x\napplied_ops: []\n---\n\nbody\n",
            encoding="utf-8",
        )

    def test_unreferenced_note_deleted(self):
        self._write_note("2026-04-01-orphan")
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 1)
        self.assertFalse((self.notes_dir / "2026-04-01-orphan.md").exists())

    def test_referenced_note_kept(self):
        self._write_note("2026-04-01-keepme")
        make_card(self.data_dir, "alpha", "backlog", "linked", "2026-04-25",
            attachments=[{"name": "src", "url": "/api/notes/2026-04-01-keepme"}])
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.notes_dir / "2026-04-01-keepme.md").exists())

    def test_only_attachment_links_count(self):
        self._write_note("2026-04-01-comment-only")
        # link is in body comment, not attachments -> still orphan
        list_dir = self.data_dir / "boards/alpha/backlog"
        (list_dir / "comment-card.md").write_text(
            "---\ntitle: c\ncreated: 2026-04-01\nupdated: 2026-04-25\nattachments: []\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n\n"
            "## Comments\n\nSee [note](/api/notes/2026-04-01-comment-only)\n",
            encoding="utf-8",
        )
        order = json.loads((list_dir / "_order.json").read_text())
        order.append("comment-card")
        (list_dir / "_order.json").write_text(json.dumps(order), encoding="utf-8")
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 1)
        self.assertFalse((self.notes_dir / "2026-04-01-comment-only.md").exists())

    def test_empty_notes_dir_no_error(self):
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 0)

    def test_missing_notes_dir_no_error(self):
        janitor.NOTES_DIR = self.notes_dir / "does-not-exist"
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 0)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_janitor.TestSweepOrphanNotes -v`
Expected: FAIL with `AttributeError: module 'janitor' has no attribute 'sweep_orphan_notes'`.

- [ ] **Step 3: Implement `sweep_orphan_notes`**

Add to `D:\Claude\ATC\janitor.py`:

```python
def _collect_referenced_note_ids() -> set[str]:
    """Walk every card across every board and gather note_ids referenced in attachments."""
    referenced = set()
    prefix = "/api/notes/"
    for board in _list_board_slugs():
        for list_slug in server.LISTS:
            order_path = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
            if not order_path.exists():
                continue
            for slug in json.loads(order_path.read_text(encoding="utf-8")):
                card = server.read_card(board, list_slug, slug)
                if card is None:
                    continue
                for att in card.get("attachments") or []:
                    url = att.get("url", "")
                    if url.startswith(prefix):
                        referenced.add(url[len(prefix):])
    return referenced


def sweep_orphan_notes() -> int:
    """Delete notes/ files not referenced by any card attachment.

    Returns the count of deleted notes.
    """
    if not NOTES_DIR.exists():
        return 0
    referenced = _collect_referenced_note_ids()
    deleted = 0
    for note_path in NOTES_DIR.glob("*.md"):
        note_id = note_path.stem
        if note_id not in referenced:
            note_path.unlink()
            deleted += 1
    return deleted


def run_all() -> dict:
    """Run both sweeps. Used by /api/janitor/run and the periodic timer."""
    done = sweep_done_cards()
    orphans = sweep_orphan_notes()
    print(f"janitor: deleted {done} done cards, {orphans} orphan notes", flush=True)
    return {"done_cards_deleted": done, "orphan_notes_deleted": orphans}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_janitor -v`
Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add janitor.py tests/test_janitor.py
git commit -m "feat: janitor.sweep_orphan_notes + run_all"
```

---

## Task 10: `server.py` — wire LLM config endpoints

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Open `D:\Claude\ATC\tests\test_server.py`. Find an existing in-process test class that boots the server (look for `HTTPServer` or `make_request_port` usage). At the end of the file, before `if __name__`, add:

```python
class TestLLMConfigEndpoints(unittest.TestCase):
    def setUp(self):
        import llm_config
        self.tmp = tempfile.TemporaryDirectory()
        llm_config.CONFIG_PATH = Path(self.tmp.name) / ".llm-config.json"
        self.server = HTTPServer(('127.0.0.1', 0), server.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.tmp.cleanup()

    def test_get_unconfigured(self):
        status, body = make_request_port(self.port, "GET", "/api/llm-config")
        self.assertEqual(status, 200)
        self.assertFalse(body["configured"])
        self.assertEqual(body["auth_token"], "")

    def test_put_then_get_masks_token(self):
        status, _ = make_request_port(self.port, "PUT", "/api/llm-config", {
            "auth_token": "supersecret1234",
            "model": "claude-sonnet-4-6",
        })
        self.assertEqual(status, 200)
        status, body = make_request_port(self.port, "GET", "/api/llm-config")
        self.assertTrue(body["configured"])
        self.assertEqual(body["auth_token"], "****1234")
        self.assertEqual(body["model"], "claude-sonnet-4-6")

    def test_put_partial_keeps_token(self):
        make_request_port(self.port, "PUT", "/api/llm-config", {"auth_token": "tok-abcd"})
        make_request_port(self.port, "PUT", "/api/llm-config", {"model": "claude-haiku-4-5"})
        status, body = make_request_port(self.port, "GET", "/api/llm-config")
        self.assertEqual(body["auth_token"], "****abcd")
        self.assertEqual(body["model"], "claude-haiku-4-5")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_server.TestLLMConfigEndpoints -v`
Expected: FAIL with 404s on `/api/llm-config`.

- [ ] **Step 3: Wire the routes**

In `D:\Claude\ATC\server.py`, find the imports block (lines 1–13) and add at the end:

```python
import llm_config
import notes
import janitor
```

Find the `_route` method (around line 293). Locate the section that handles `/api/sync/status` (around line 367). After that block, before the `# Static files (GET only)` block, insert:

```python
        # /api/llm-config
        if path == '/api/llm-config' and method == 'GET':
            return self._handle_get_llm_config()
        if path == '/api/llm-config' and method == 'PUT':
            return self._handle_put_llm_config()
        if path == '/api/llm-config/test' and method == 'POST':
            return self._handle_test_llm_config()
```

Find a logical place to add new handler methods (alongside the other `_handle_*` methods). Add:

```python
    def _handle_get_llm_config(self):
        self._send_json(llm_config.public_view())

    def _handle_put_llm_config(self):
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            return self._send_error(400, 'invalid json')
        if not isinstance(body, dict):
            return self._send_error(400, 'expected object')
        # Validate model if present
        if 'model' in body and body['model'] not in llm_config.ALLOWED_MODELS:
            return self._send_error(400, f"model must be one of {llm_config.ALLOWED_MODELS}")
        llm_config.save(body)
        self._send_json({'ok': True})

    def _handle_test_llm_config(self):
        try:
            client = llm_config.get_client()
        except llm_config.NotConfigured:
            return self._send_error(400, 'not configured')
        cfg = llm_config.load()
        try:
            client.messages.create(
                model=cfg['model'],
                max_tokens=1,
                messages=[{'role': 'user', 'content': 'hi'}],
            )
            self._send_json({'ok': True})
        except Exception as e:
            self._send_json({'ok': False, 'error': str(e)})
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_server.TestLLMConfigEndpoints -v`
Expected: 3 tests pass. Also run the full suite to make sure nothing else broke:

Run: `python -m unittest discover tests -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: server endpoints for LLM config get/put/test"
```

---

## Task 11: `server.py` — wire notes endpoints

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py` before `if __name__`:

```python
class TestNotesEndpoints(unittest.TestCase):
    def setUp(self):
        import notes as notes_mod
        self.notes_mod = notes_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        notes_mod.NOTES_DIR = Path(self.tmp.name) / "notes"
        # Stub the LLM client
        fake_response_text = json.dumps({
            "summary": "Talked about X.",
            "operations": [
                {"op": "create_card", "board": "alpha", "list": "backlog",
                 "title": "Do thing", "confidence": "high", "reason": "explicit"}
            ],
        })
        class FakeClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    return type("R", (), {
                        "content": [type("B", (), {"text": fake_response_text, "type": "text"})()]
                    })()
        # Make get_client return our fake
        import llm_config
        self._orig_get_client = llm_config.get_client
        llm_config.get_client = lambda: FakeClient()

        # Set up an alpha board
        board_dir = self.data_dir / "boards" / "alpha"
        board_dir.mkdir(parents=True)
        (board_dir / "_board.md").write_text(
            "---\nname: Alpha\ncolor: '#000'\n---\n", encoding="utf-8")
        for lst in ("ideas", "backlog", "in-progress", "done"):
            (board_dir / lst).mkdir()
            (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
        (self.data_dir / "_boards-order.json").write_text('["alpha"]', encoding="utf-8")

        self.server = HTTPServer(('127.0.0.1', 0), server.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        import llm_config
        llm_config.get_client = self._orig_get_client
        self.tmp.cleanup()

    def test_analyze_returns_operations(self):
        status, body = make_request_port(self.port, "POST", "/api/notes/analyze",
            {"text": "We agreed to do thing.", "title": "Q2"})
        self.assertEqual(status, 200)
        self.assertEqual(body["summary"], "Talked about X.")
        self.assertEqual(len(body["operations"]), 1)
        self.assertTrue(body["note_id"].endswith("-q2"))

    def test_get_note(self):
        status, body = make_request_port(self.port, "POST", "/api/notes/analyze",
            {"text": "body content", "title": "Topic"})
        note_id = body["note_id"]
        # Use raw urlopen because get_note returns text/markdown not JSON
        import urllib.request
        with urllib.request.urlopen(f"http://localhost:{self.port}/api/notes/{note_id}") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("body content", r.read().decode("utf-8"))

    def test_get_note_404(self):
        import urllib.request, urllib.error
        try:
            urllib.request.urlopen(f"http://localhost:{self.port}/api/notes/nonexistent")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_apply_creates_card(self):
        _, body = make_request_port(self.port, "POST", "/api/notes/analyze",
            {"text": "x", "title": "T"})
        note_id = body["note_id"]
        ops = body["operations"]
        status, result = make_request_port(self.port, "POST", "/api/notes/apply",
            {"note_id": note_id, "operations": ops})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["applied"]), 1)
        # Card now exists
        status, card = make_request_port(self.port, "GET",
            "/api/cards/alpha/backlog/do-thing")
        self.assertEqual(status, 200)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_server.TestNotesEndpoints -v`
Expected: FAIL with 404s on `/api/notes/...`.

- [ ] **Step 3: Wire the routes**

In `D:\Claude\ATC\server.py`, in `_route`, after the `/api/llm-config` block added in Task 10, add:

```python
        # /api/notes/analyze
        if path == '/api/notes/analyze' and method == 'POST':
            return self._handle_notes_analyze()

        # /api/notes/apply
        if path == '/api/notes/apply' and method == 'POST':
            return self._handle_notes_apply()

        # /api/notes/:id
        m = re.match(r'^/api/notes/([\w\-.]+)$', path)
        if m and method == 'GET':
            return self._handle_get_note(m.group(1))

        # /api/janitor/run
        if path == '/api/janitor/run' and method == 'POST':
            return self._handle_janitor_run()
```

Add the four handler methods alongside the others:

```python
    def _handle_notes_analyze(self):
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            return self._send_error(400, 'invalid json')
        text = body.get('text', '').strip()
        if not text:
            return self._send_error(400, 'text is required')
        title = body.get('title', '')
        try:
            client = llm_config.get_client()
        except llm_config.NotConfigured:
            return self._send_error(400, 'LLM not configured')
        cfg = llm_config.load()
        try:
            result = notes.analyze(text, title, model=cfg['model'], client=client)
        except notes.LLMResponseError as e:
            return self._send_error(502, str(e))
        self._send_json(result)

    def _handle_notes_apply(self):
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            return self._send_error(400, 'invalid json')
        note_id = body.get('note_id', '')
        operations = body.get('operations', [])
        if not note_id or not isinstance(operations, list):
            return self._send_error(400, 'note_id and operations required')
        result = notes.apply_operations(operations, note_id)
        self._send_json(result)

    def _handle_get_note(self, note_id):
        text = notes.read_note(note_id)
        if text is None:
            return self._send_error(404, 'note not found')
        body = text.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/markdown; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_janitor_run(self):
        result = janitor.run_all()
        self._send_json(result)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m unittest tests.test_server.TestNotesEndpoints -v`
Expected: 4 tests pass.

Run the full suite: `python -m unittest discover tests -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: server endpoints for notes analyze/apply/get and janitor run"
```

---

## Task 12: `server.py` — kick off janitor on startup and every 24 h

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Find the server startup block**

Open `D:\Claude\ATC\server.py`. Find the bottom of the file where `HTTPServer` is started (look for `if __name__ == '__main__':` or the equivalent main block). Read the surrounding code to see how startup is currently wired (`ensure_data_dir`, `ensure_data_repo`, etc.).

- [ ] **Step 2: Add janitor startup**

In the same main block, after `ensure_data_dir()` and any `ensure_data_repo()` call, add:

```python
    # Run janitor once on startup, then every 24 hours in a background thread
    try:
        janitor.run_all()
    except Exception as e:
        print(f"janitor: startup sweep failed: {e}", flush=True)

    def _periodic_janitor():
        import time
        while True:
            time.sleep(24 * 60 * 60)
            try:
                janitor.run_all()
            except Exception as e:
                print(f"janitor: periodic sweep failed: {e}", flush=True)

    import threading
    threading.Thread(target=_periodic_janitor, daemon=True).start()
```

- [ ] **Step 3: Smoke-test the server start**

Run: `python server.py &` (or in a separate terminal)
Expected: prints `janitor: deleted N done cards, M orphan notes` once on startup, then serves normally.
Stop the server with Ctrl-C (or `kill %1`).

- [ ] **Step 4: Verify the full test suite still passes**

Run: `python -m unittest discover tests -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat: run janitor on startup and every 24h"
```

---

## Task 13: `index.html` — Settings modal

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Locate the header and existing modal pattern**

Open `D:\Claude\ATC\index.html`. Find:
1. The header element (search for `<header` or for the existing sync button).
2. An existing modal (search for `class="modal"` or similar) to mirror its CSS class names.
3. Where event handlers are wired (search for `addEventListener` near header buttons).

- [ ] **Step 2: Add the Settings button to the header**

Inside the header, alongside the sync button, add:

```html
<button id="btn-settings" class="header-btn" title="LLM settings">⚙</button>
```

- [ ] **Step 3: Add the Settings modal markup**

Just before `</body>`, add (mirror existing modal CSS classes — adjust `class="modal"` if your project uses different names):

```html
<div id="modal-settings" class="modal" hidden>
  <div class="modal-content">
    <h2>LLM Settings</h2>
    <label>API token
      <input id="cfg-token" type="password" placeholder="****ABCD (saved)" />
    </label>
    <label>Base URL
      <input id="cfg-base-url" type="text" />
    </label>
    <label>Model
      <select id="cfg-model">
        <option value="claude-opus-4-7">Opus 4.7</option>
        <option value="claude-sonnet-4-6">Sonnet 4.6</option>
        <option value="claude-haiku-4-5">Haiku 4.5</option>
      </select>
    </label>
    <label>
      <input id="cfg-tls-verify" type="checkbox" />
      Verify TLS (uncheck for corp gateway)
    </label>
    <div class="modal-actions">
      <button id="btn-test-conn">Test connection</button>
      <span id="cfg-status"></span>
    </div>
    <div class="modal-actions">
      <button id="btn-cfg-cancel">Cancel</button>
      <button id="btn-cfg-save">Save</button>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Add the JS for Settings**

Add near the other UI scripts (or in a new `<script>` block at the end of the body):

```javascript
const cfgEls = {
  modal: document.getElementById('modal-settings'),
  token: document.getElementById('cfg-token'),
  baseUrl: document.getElementById('cfg-base-url'),
  model: document.getElementById('cfg-model'),
  tls: document.getElementById('cfg-tls-verify'),
  status: document.getElementById('cfg-status'),
};

async function openSettings() {
  const r = await fetch('/api/llm-config');
  const cfg = await r.json();
  cfgEls.token.value = '';
  cfgEls.token.placeholder = cfg.auth_token || 'paste token';
  cfgEls.baseUrl.value = cfg.base_url;
  cfgEls.model.value = cfg.model;
  cfgEls.tls.checked = cfg.tls_verify;
  cfgEls.status.textContent = '';
  cfgEls.modal.hidden = false;
}

document.getElementById('btn-settings').addEventListener('click', openSettings);
document.getElementById('btn-cfg-cancel').addEventListener('click', () => {
  cfgEls.modal.hidden = true;
});
document.getElementById('btn-cfg-save').addEventListener('click', async () => {
  const body = {
    base_url: cfgEls.baseUrl.value,
    model: cfgEls.model.value,
    tls_verify: cfgEls.tls.checked,
  };
  if (cfgEls.token.value) body.auth_token = cfgEls.token.value;
  const r = await fetch('/api/llm-config', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  if (r.ok) {
    cfgEls.modal.hidden = true;
    refreshNotesButtonState();
  } else {
    const err = await r.json();
    cfgEls.status.textContent = 'Save failed: ' + (err.error || r.status);
  }
});
document.getElementById('btn-test-conn').addEventListener('click', async () => {
  cfgEls.status.textContent = 'Testing...';
  const r = await fetch('/api/llm-config/test', {method: 'POST'});
  const body = await r.json();
  cfgEls.status.textContent = body.ok ? '✓ OK' : '✗ ' + (body.error || 'failed');
});

async function refreshNotesButtonState() {
  const r = await fetch('/api/llm-config');
  const cfg = await r.json();
  const btn = document.getElementById('btn-process-notes');
  if (!btn) return;
  btn.disabled = !cfg.configured;
  btn.title = cfg.configured ? 'Process meeting notes' : 'Configure API token in Settings';
}
```

- [ ] **Step 5: Manually verify the Settings modal**

Start the server: `python server.py`
Open `http://localhost:8000` (or whatever port the server uses).
Click ⚙. Modal should open. Paste a token, click Save. Re-open: token field should be empty but placeholder should show `****` + last 4 chars.

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(ui): settings modal for LLM gateway config"
```

---

## Task 14: `index.html` — Process Notes wizard

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Add the Process Notes header button**

In the header, alongside the Settings button, add:

```html
<button id="btn-process-notes" class="header-btn" disabled title="Configure API token in Settings">📝 Process Notes</button>
```

- [ ] **Step 2: Add the wizard modal markup**

Before `</body>`, add:

```html
<div id="modal-notes" class="modal" hidden>
  <div class="modal-content modal-wide">
    <!-- Step 1 -->
    <div id="notes-step-1">
      <h2>Process Notes — Paste</h2>
      <label>Title (optional)
        <input id="notes-title" type="text" placeholder="e.g. Q2 Planning" />
      </label>
      <label>Note text
        <textarea id="notes-text" rows="20" style="width:100%;font-family:monospace;"></textarea>
      </label>
      <div class="modal-actions">
        <button id="btn-notes-cancel">Cancel</button>
        <button id="btn-notes-analyze">Analyze</button>
        <span id="notes-spinner" hidden>Analyzing…</span>
      </div>
    </div>
    <!-- Step 2 -->
    <div id="notes-step-2" hidden>
      <h2>Preview</h2>
      <p id="notes-summary"></p>
      <div id="notes-ops"></div>
      <div class="modal-actions">
        <button id="btn-notes-back">← Back</button>
        <button id="btn-notes-apply">Apply selected</button>
      </div>
    </div>
    <!-- Step 3 -->
    <div id="notes-step-3" hidden>
      <h2>Result</h2>
      <div id="notes-result"></div>
      <div class="modal-actions">
        <button id="btn-notes-sync" hidden>Sync now</button>
        <button id="btn-notes-close">Close</button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add the wizard JS**

Append:

```javascript
let currentNoteId = null;
let currentOps = [];

function showStep(n) {
  for (let i = 1; i <= 3; i++) {
    document.getElementById(`notes-step-${i}`).hidden = (i !== n);
  }
}

document.getElementById('btn-process-notes').addEventListener('click', () => {
  document.getElementById('notes-text').value = '';
  document.getElementById('notes-title').value = '';
  showStep(1);
  document.getElementById('modal-notes').hidden = false;
});

document.getElementById('btn-notes-cancel').addEventListener('click', () => {
  document.getElementById('modal-notes').hidden = true;
});

document.getElementById('btn-notes-analyze').addEventListener('click', async () => {
  const text = document.getElementById('notes-text').value.trim();
  if (!text) return;
  const title = document.getElementById('notes-title').value;
  document.getElementById('notes-spinner').hidden = false;
  try {
    const r = await fetch('/api/notes/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, title}),
    });
    if (!r.ok) {
      const err = await r.json();
      alert('Analyze failed: ' + (err.error || r.status));
      return;
    }
    const result = await r.json();
    currentNoteId = result.note_id;
    currentOps = result.operations;
    document.getElementById('notes-summary').textContent =
      `Note: ${currentNoteId} — ${result.summary}`;
    renderOps(currentOps);
    showStep(2);
  } finally {
    document.getElementById('notes-spinner').hidden = true;
  }
});

function renderOps(ops) {
  const container = document.getElementById('notes-ops');
  container.innerHTML = '';
  ops.forEach((op, i) => {
    const div = document.createElement('div');
    div.className = 'op-row';
    const checked = op.confidence !== 'low' ? 'checked' : '';
    const target = op.card
      ? `${op.board} / ${op.list} / ${op.card}`
      : `${op.board} / ${op.list}`;
    const summary = op.op === 'create_card' ? `"${op.title}"`
      : op.op === 'add_comment' ? `"${(op.text || '').slice(0, 80)}"`
      : op.op === 'tick_checklist' ? `tick "${op.item}"`
      : op.op === 'add_checklist_item' ? `add "${op.item}"`
      : op.op === 'move_card' ? `→ ${op.target_list}`
      : op.op === 'update_field' ? `${op.field} = ${JSON.stringify(op.value)}`
      : '';
    div.innerHTML = `
      <label>
        <input type="checkbox" data-i="${i}" ${checked} />
        <strong>${op.op}</strong>
        <span class="conf conf-${op.confidence}">${op.confidence || ''}</span>
        <span class="target">${target}</span>
        <span class="summary">${summary}</span>
        <div class="reason">${op.reason || ''}</div>
      </label>`;
    container.appendChild(div);
  });
}

document.getElementById('btn-notes-back').addEventListener('click', () => showStep(1));

document.getElementById('btn-notes-apply').addEventListener('click', async () => {
  const checks = document.querySelectorAll('#notes-ops input[type=checkbox]');
  const selected = [];
  checks.forEach(c => { if (c.checked) selected.push(currentOps[parseInt(c.dataset.i)]); });
  const r = await fetch('/api/notes/apply', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({note_id: currentNoteId, operations: selected}),
  });
  const result = await r.json();
  const out = document.getElementById('notes-result');
  out.innerHTML = `
    <p>✓ Applied ${result.applied.length} operation(s).</p>
    ${result.skipped.length ? `<p>✗ Skipped ${result.skipped.length}:</p>
      <ul>${result.skipped.map(s => `<li>${s.op.op}: ${s.reason}</li>`).join('')}</ul>` : ''}
    <p>Note kept at <a href="/api/notes/${currentNoteId}" target="_blank">/api/notes/${currentNoteId}</a></p>
  `;
  // Show sync button if there were any successful applies
  document.getElementById('btn-notes-sync').hidden = result.applied.length === 0;
  showStep(3);
  // Refresh the board UI if the existing app exposes a refresh function
  if (typeof refreshBoards === 'function') refreshBoards();
});

document.getElementById('btn-notes-sync').addEventListener('click', async () => {
  await fetch('/api/sync/push', {method: 'POST'});
  document.getElementById('btn-notes-sync').textContent = 'Synced ✓';
  document.getElementById('btn-notes-sync').disabled = true;
});

document.getElementById('btn-notes-close').addEventListener('click', () => {
  document.getElementById('modal-notes').hidden = true;
});

// Initial state: enable/disable Process Notes button based on config
refreshNotesButtonState();
```

- [ ] **Step 4: Add minimal styles for the wizard**

Find the existing `<style>` block and append:

```css
.modal-wide { max-width: 800px; }
.op-row { border-top: 1px solid #e0e0e0; padding: 6px 0; }
.op-row label { display: block; cursor: pointer; }
.conf { font-size: 11px; padding: 1px 6px; border-radius: 3px; margin-left: 6px; }
.conf-high { background: #d4edda; color: #155724; }
.conf-med { background: #fff3cd; color: #856404; }
.conf-low { background: #f8d7da; color: #721c24; }
.target { color: #555; margin-left: 8px; }
.summary { margin-left: 8px; }
.reason { color: #777; font-size: 12px; margin-left: 24px; }
.header-btn { margin-left: 6px; }
.header-btn[disabled] { opacity: 0.5; cursor: not-allowed; }
```

- [ ] **Step 5: Manual end-to-end smoke test**

Start the server: `python server.py`
1. Click ⚙, paste your real token, Test connection → ✓.
2. Save. The 📝 Process Notes button should enable.
3. Click 📝, paste a short meeting note ("Bob will draft the API spec by Friday."), Analyze.
4. Preview should show at least one `create_card` op.
5. Click Apply. Result modal should show success and a link.
6. Open the link → see the archived note. Open the kanban board → see the new card with a "Source note" attachment.

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(ui): process-notes wizard — paste, preview, apply"
```

---

## Task 15: Final verification

**Files:** none

- [ ] **Step 1: Run the full test suite**

Run: `python -m unittest discover tests -v`
Expected: all tests pass; no warnings about missing modules.

- [ ] **Step 2: Verify gitignore is doing its job**

Run: `git status`
Expected: `notes/` and `.llm-config.json` should NOT appear in untracked files (assuming they exist locally from your manual smoke test).

- [ ] **Step 3: Verify the data repo is unaffected by note writes**

Run: `git -C data status --porcelain`
Expected: clean (the note archive lives outside `data/`).

- [ ] **Step 4: Verify the Process Notes button reflects token presence**

With a token saved, button enabled. Open `.llm-config.json`, blank the `auth_token` value, refresh the page → button disabled with the configure tooltip.

- [ ] **Step 5: Manual janitor sanity check**

Run: `curl -X POST http://localhost:8000/api/janitor/run`
Expected: `{"done_cards_deleted": 0, "orphan_notes_deleted": 0}` (or non-zero if you have stale data).

- [ ] **Step 6: Final commit if anything was tweaked**

If any small fixes were needed during smoke testing, commit them with a `fix:` prefix.
