# Chat Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a toggleable AI chat sidebar (right side, 380px) that shares Process Notes' tool surface and adds bucket/filter read tools, with inline per-turn proposed-ops panels for queue-and-confirm writes.

**Architecture:** Extract shared tool definitions and helpers from `notes.py` into a new `chat_tools.py`. Add 5 new bucket/filter read tools there. Add a new `chat.py` with a `chat_stream` generator that mirrors `notes.analyze_stream` but terminates on text-only turns instead of via a `finish` tool. Add `POST /api/chat` endpoint streaming SSE. Build sidebar UI in `index.html` reusing the existing SSE reader pattern from Process Notes.

**Tech Stack:** Python 3.14 stdlib `http.server.ThreadingHTTPServer`, Anthropic SDK (corp gateway via `llm_config`), vanilla JS + `fetch` body reader for SSE. No new dependencies.

---

## Spec reference

See `docs/superpowers/specs/2026-04-30-chat-sidebar-design.md` for the full design.

---

## File structure

**New files:**
- `chat_tools.py` — shared tool definitions + implementations (read + write) used by both `notes.py` and `chat.py`
- `chat.py` — chat-specific system prompt, tool list (no `finish`), and `chat_stream` generator
- `tests/test_chat_tools.py`
- `tests/test_chat.py`
- `tests/_llm_fakes.py` — `FakeBlock` / `FakeResponse` / `FakeClient` shared across `test_notes.py`, `test_chat.py`, `test_server.py`

**Modified files:**
- `notes.py` — imports moved helpers from `chat_tools`; keeps notes-specific code
- `server.py` — new `POST /api/chat` endpoint; `apply_operations` route already exists
- `index.html` — add `#chat-sidebar` markup, CSS, JS streaming reader, render functions
- `tests/test_notes.py` — switch to importing from `_llm_fakes`; otherwise unchanged
- `tests/test_server.py` — switch to importing from `_llm_fakes`; add `TestChatEndpoint`

**No changes to:**
- `data_repo.py`, `sync_config.py`, `janitor.py`, `llm_config.py`

---

## Task 1: Extract shared LLM-fakes test helper

**Files:**
- Create: `tests/_llm_fakes.py`
- Modify: `tests/test_notes.py:151-180`
- Modify: `tests/test_server.py:520-549` (the inline FakeClient inside `TestNotesEndpoints.setUp`)

This is a pure refactor with no behavior change. We're moving the `FakeBlock`/`FakeResponse`/`FakeMessages`/`FakeClient` classes out of `tests/test_notes.py` so `tests/test_chat.py` can use them too.

- [ ] **Step 1: Create the shared helpers file**

Create `tests/_llm_fakes.py` with this exact content:

```python
"""Shared scaffolding for tests that drive the Anthropic SDK with scripted responses.

Used by test_notes, test_chat, and test_server.
"""


class FakeBlock:
    """Mimics an Anthropic SDK content block (text or tool_use)."""

    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


def text_block(text):
    return FakeBlock("text", text=text)


def tool_use(name, input_, id_=None):
    return FakeBlock("tool_use", name=name, input=input_, id=id_ or f"tu_{name}")


class FakeResponse:
    def __init__(self, content_blocks):
        self.content = content_blocks
        self.stop_reason = "tool_use" if any(b.type == "tool_use" for b in content_blocks) else "end_turn"


class FakeMessages:
    """Returns a scripted sequence of FakeResponse objects, one per .create() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages.create called more times than scripted")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)
```

- [ ] **Step 2: Update `tests/test_notes.py` to import from `_llm_fakes`**

Replace the inline class definitions (lines 151-180, the block starting `class FakeBlock:` and ending with `class FakeClient:`) with:

```python
from tests._llm_fakes import FakeBlock, FakeResponse, FakeClient, text_block, tool_use  # noqa: F401
```

Also adjust the existing import block at the top (around line 11) — `tests._llm_fakes` requires the parent dir on `sys.path`, which is already set up by `sys.path.insert(0, str(Path(__file__).parent.parent))`.

- [ ] **Step 3: Update `tests/test_server.py` `TestNotesEndpoints.setUp` to use shared fakes**

The current `setUp` constructs ad-hoc `_block`/`_resp` helpers and a custom `FakeMessages`. Replace those (lines roughly 520-549, the block starting `# Stub the LLM client to drive the tool-use loop:` through the `class FakeClient:` definition) with:

```python
        # Stub the LLM client to drive the tool-use loop:
        # turn 1 -> create_card, turn 2 -> finish.
        from tests._llm_fakes import FakeClient, FakeResponse, tool_use
        scripted = [
            FakeResponse([tool_use("create_card", {
                "board": "alpha", "list": "backlog", "title": "Do thing",
                "confidence": "high", "reason": "explicit",
            }, id_="t1")]),
            FakeResponse([tool_use("finish", {
                "summary": "Talked about X.",
            }, id_="t2")]),
        ]
        # Each request gets a fresh client so the scripted queue restarts.
        import llm_config
        self._orig_get_client = llm_config.get_client
        llm_config.get_client = lambda: FakeClient(list(scripted))
```

- [ ] **Step 4: Run all existing tests**

Run: `cd D:/Claude/ATC && python -m unittest discover tests`
Expected: All 133 tests still pass. (Existing count from the last full-suite run.)

- [ ] **Step 5: Commit**

```bash
git add tests/_llm_fakes.py tests/test_notes.py tests/test_server.py
git commit -m "refactor(tests): extract shared LLM fakes into tests/_llm_fakes.py"
```

---

## Task 2: Move shared helpers from `notes.py` to `chat_tools.py`

**Files:**
- Create: `chat_tools.py`
- Modify: `notes.py`

We're moving exactly: `_LIST_ENUM`, `_CONF_ENUM`, `_op_props`, `_tool_list_boards`, `_tool_list_cards`, `_all_card_titles`, `_tool_search_cards`, `_tool_read_card`, `_queue_op`, `_WRITE_OP_NAMES`, `READ_TOOLS`, `_summarize_read_result`, `_queued_summary_fields`, plus the `_parse_checklist` and `_extract_description` helpers (because `_tool_read_card` depends on them).

We are also moving the read-tool definitions and the write-tool definitions out of the `TOOLS` list into separate `READ_TOOL_DEFS` and `WRITE_TOOL_DEFS` lists. `notes.py`'s `TOOLS` becomes `READ_TOOL_DEFS + WRITE_TOOL_DEFS + [FINISH_TOOL_DEF]`.

**Net effect:** `notes.py` shrinks; `chat_tools.py` is new. No behavior change anywhere.

- [ ] **Step 1: Create `chat_tools.py`**

Create `chat_tools.py` with this content (it's the moved code, plus the split tool defs):

```python
"""Shared tool definitions and implementations for the LLM-driven flows.

Used by both Process Notes (notes.py) and the Chat sidebar (chat.py).
"""
import difflib
import json
import re

import server


_LIST_ENUM = ["ideas", "backlog", "in-progress", "done"]
_CONF_ENUM = ["high", "med", "low"]


# --- Body parsing helpers (used by _tool_read_card) ---

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


# --- Tool-def schema builder ---

def _op_props(extra: dict, *, target_card: bool, with_confidence: bool = True) -> dict:
    base = {
        "board": {"type": "string"},
        "list": {"type": "string", "enum": _LIST_ENUM},
    }
    if target_card:
        base["card"] = {"type": "string"}
    base.update(extra)
    if with_confidence:
        base["confidence"] = {"type": "string", "enum": _CONF_ENUM}
        base["reason"] = {"type": "string"}
    return base


# --- Read-tool definitions (existing) ---

READ_TOOL_DEFS = [
    {
        "name": "list_boards",
        "description": "List all boards with name and card count.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_cards",
        "description": "List cards on a board (optionally filtered to one list). Returns slug, title, labels, due, assignee. Call read_card for the body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string"},
                "list": {"type": "string", "enum": _LIST_ENUM},
            },
            "required": ["board"],
        },
    },
    {
        "name": "search_cards",
        "description": "Title-similarity search across all boards. ALWAYS call before create_card so you don't duplicate work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_card",
        "description": "Full card: description, checklist with checked state, comments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string"},
                "list": {"type": "string", "enum": _LIST_ENUM},
                "slug": {"type": "string"},
            },
            "required": ["board", "list", "slug"],
        },
    },
]

# --- Write-tool definitions ---

WRITE_TOOL_DEFS = [
    {
        "name": "create_card",
        "description": "Propose a new card. Queued for user confirmation, not created immediately.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({
                "title": {"type": "string"},
                "description": {"type": "string"},
                "checklist": {"type": "array", "items": {"type": "string"}},
                "due": {"type": "string", "description": "YYYY-MM-DD"},
                "assignee": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            }, target_card=False),
            "required": ["board", "list", "title", "confidence", "reason"],
        },
    },
    {
        "name": "add_comment",
        "description": "Propose adding a comment to an existing card.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({"text": {"type": "string"}}, target_card=True),
            "required": ["board", "list", "card", "text", "confidence", "reason"],
        },
    },
    {
        "name": "tick_checklist",
        "description": "Propose marking an existing checklist item done. 'item' is matched as a case-insensitive substring.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({"item": {"type": "string"}}, target_card=True),
            "required": ["board", "list", "card", "item", "confidence", "reason"],
        },
    },
    {
        "name": "add_checklist_item",
        "description": "Propose adding a new item to a card's checklist.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({"item": {"type": "string"}}, target_card=True),
            "required": ["board", "list", "card", "item", "confidence", "reason"],
        },
    },
    {
        "name": "move_card",
        "description": "Propose moving a card to a different list.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({
                "target_list": {"type": "string", "enum": _LIST_ENUM},
            }, target_card=True),
            "required": ["board", "list", "card", "target_list", "confidence", "reason"],
        },
    },
    {
        "name": "update_field",
        "description": "Propose updating due, assignee, or labels on an existing card.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({
                "field": {"type": "string", "enum": ["due", "assignee", "labels"]},
                "value": {"description": "string for due/assignee, array of strings for labels"},
            }, target_card=True),
            "required": ["board", "list", "card", "field", "value", "confidence", "reason"],
        },
    },
]


# --- Read-tool implementations ---

def _tool_list_boards(_args: dict) -> dict:
    boards_order_path = server.DATA_DIR / "_boards-order.json"
    if not boards_order_path.exists():
        return {"boards": []}
    out = []
    for slug in json.loads(boards_order_path.read_text(encoding="utf-8")):
        meta = server.read_board_meta(slug)
        if meta is None:
            continue
        count = 0
        for list_slug in server.LISTS:
            order = server.DATA_DIR / "boards" / slug / list_slug / "_order.json"
            if order.exists():
                count += len(json.loads(order.read_text(encoding="utf-8")))
        out.append({"slug": slug, "name": meta.get("name", slug), "card_count": count})
    return {"boards": out}


def _tool_list_cards(args: dict) -> dict:
    board = args["board"]
    if server.read_board_meta(board) is None:
        return {"error": f"board '{board}' not found"}
    only = args.get("list")
    lists = [only] if only else server.LISTS
    out = []
    for list_slug in lists:
        order = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
        if not order.exists():
            continue
        for slug in json.loads(order.read_text(encoding="utf-8")):
            card = server.read_card(board, list_slug, slug)
            if card is None:
                continue
            out.append({
                "l": list_slug,
                "s": slug,
                "title": card.get("title", ""),
                "labels": card.get("labels") or [],
                "due": card.get("due", ""),
                "assignee": card.get("assignee", ""),
            })
    return {"cards": out}


def _all_card_titles() -> list[tuple[str, str, str, str]]:
    """Return [(board, list, slug, title), ...] for every card."""
    out = []
    boards_order_path = server.DATA_DIR / "_boards-order.json"
    if not boards_order_path.exists():
        return out
    for board in json.loads(boards_order_path.read_text(encoding="utf-8")):
        for list_slug in server.LISTS:
            order = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
            if not order.exists():
                continue
            for slug in json.loads(order.read_text(encoding="utf-8")):
                card = server.read_card(board, list_slug, slug)
                if card is None:
                    continue
                out.append((board, list_slug, slug, card.get("title", "")))
    return out


def _tool_search_cards(args: dict) -> dict:
    query = args["query"].lower()
    limit = max(1, min(int(args.get("limit", 8)), 25))
    cards = _all_card_titles()
    titles = [t.lower() for (_b, _l, _s, t) in cards]
    matches = []
    fuzzy = difflib.get_close_matches(query, titles, n=limit, cutoff=0.4)
    seen = set()
    for low_title in fuzzy:
        for i, t in enumerate(titles):
            if t == low_title and i not in seen:
                seen.add(i)
                b, l, s, title = cards[i]
                matches.append({"b": b, "l": l, "s": s, "title": title})
                break
    for i, t in enumerate(titles):
        if i in seen or query not in t:
            continue
        if len(matches) >= limit:
            break
        seen.add(i)
        b, l, s, title = cards[i]
        matches.append({"b": b, "l": l, "s": s, "title": title})
    return {"matches": matches[:limit]}


def _tool_read_card(args: dict) -> dict:
    card = server.read_card(args["board"], args["list"], args["slug"])
    if card is None:
        return {"error": "card not found"}
    todo, done = _parse_checklist(card.get("body", ""))
    return {
        "title": card.get("title", ""),
        "labels": card.get("labels") or [],
        "due": card.get("due", ""),
        "assignee": card.get("assignee", ""),
        "description": _extract_description(card.get("body", "")),
        "checklist_todo": todo,
        "checklist_done": done,
        "body": card.get("body", ""),
    }


# --- Write-tool dispatch (queues, doesn't execute) ---

_WRITE_OP_NAMES = {
    "create_card", "add_comment", "tick_checklist",
    "add_checklist_item", "move_card", "update_field",
}


def _queue_op(name: str, args: dict, queue: list) -> dict:
    op = {"op": name, **args}
    queue.append(op)
    return {"queued": True, "op": name, "index": len(queue) - 1}


READ_TOOLS = {
    "list_boards": _tool_list_boards,
    "list_cards": _tool_list_cards,
    "search_cards": _tool_search_cards,
    "read_card": _tool_read_card,
}


# --- Event-summary helpers (used by the streaming loops in notes.py and chat.py) ---

def _summarize_read_result(name: str, args: dict, payload: dict) -> str:
    """Short human-readable description of what a read tool returned."""
    if "error" in payload:
        return f"error: {payload['error']}"
    if name == "list_boards":
        n = len(payload.get("boards", []))
        return f"{n} board(s)"
    if name == "list_cards":
        n = len(payload.get("cards", []))
        scope = args.get("board", "?")
        if args.get("list"):
            scope += "/" + args["list"]
        return f"{n} card(s) in {scope}"
    if name == "search_cards":
        n = len(payload.get("matches", []))
        return f"{n} match(es) for '{args.get('query', '')}'"
    if name == "read_card":
        return f"{args.get('board','?')}/{args.get('list','?')}/{args.get('slug','?')}"
    return ""


def _queued_summary_fields(name: str, args: dict) -> dict:
    """Fields to surface in a 'queued' event so the UI can show what's been proposed."""
    if name == "create_card":
        return {"board": args.get("board", ""), "list": args.get("list", ""),
                "title": args.get("title", "")}
    if name == "add_comment":
        text = args.get("text", "")
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "text": text[:60]}
    if name in ("tick_checklist", "add_checklist_item"):
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "item": args.get("item", "")}
    if name == "move_card":
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "target_list": args.get("target_list", "")}
    if name == "update_field":
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "field": args.get("field", "")}
    return {}
```

- [ ] **Step 2: Update `notes.py` to import the moved code**

Open `notes.py`. Replace the file (or apply targeted deletes + a new import block) so it looks like this — keep the existing implementations of `build_toc`, `_slugify`, `archive_note`, `read_note`, `LLMResponseError`, `SYSTEM_PROMPT`, `analyze_stream`, `analyze`, all `_do_*` functions, `_record_in_note`, `apply_operations`, `_block_to_dict`, `_today_iso`, `_append_to_order`, `_remove_from_order`, `_build_card_body`. Only the moved blocks change.

Top of file becomes:

```python
"""Notes-to-cards integration: snapshot, archive, LLM call, apply."""
import json
import re
from datetime import date, datetime
from pathlib import Path

import server
from chat_tools import (
    READ_TOOL_DEFS, WRITE_TOOL_DEFS, READ_TOOLS,
    _WRITE_OP_NAMES, _queue_op,
    _summarize_read_result, _queued_summary_fields,
    _parse_checklist, _extract_description,
)
```

Delete the moved definitions from `notes.py`:
- `_parse_checklist` (lines 13-32)
- `_extract_description` (lines 35-47)
- `_LIST_ENUM`, `_CONF_ENUM` (lines 162-163)
- `_op_props` (lines 166-178)
- `TOOLS = [` ... entire 10-tool list (lines 180-300) — **replace** with the next bullet
- `_tool_list_boards`, `_tool_list_cards`, `_all_card_titles`, `_tool_search_cards`, `_tool_read_card` (lines 303-416)
- `_WRITE_OP_NAMES`, `_queue_op`, `READ_TOOLS` (lines 418-430)
- `_summarize_read_result`, `_queued_summary_fields` (lines 772-812)

Add the new `TOOLS` list — place it where the old one was:

```python
FINISH_TOOL_DEF = {
    "name": "finish",
    "description": "Call once you have proposed every op the note warrants. Provide a 1-2 sentence summary of the meeting.",
    "input_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
}

TOOLS = READ_TOOL_DEFS + WRITE_TOOL_DEFS + [FINISH_TOOL_DEF]
```

Keep `difflib` removed from `notes.py` imports (only `chat_tools` uses it now).

- [ ] **Step 3: Run the full test suite to confirm nothing broke**

Run: `cd D:/Claude/ATC && python -m unittest discover tests`
Expected: All 133 tests pass. (Same count as before — pure refactor.)

If anything fails with `AttributeError: module 'notes' has no attribute '_tool_list_boards'` style errors, that's a test that referenced a moved symbol via `notes._tool_list_boards`. Fix by changing the test to reference `chat_tools._tool_list_boards`.

- [ ] **Step 4: Commit**

```bash
git add chat_tools.py notes.py tests/
git commit -m "refactor: extract shared LLM tools into chat_tools.py"
```

---

## Task 3: New read tools — bucket queries

**Files:**
- Modify: `chat_tools.py` (add 3 functions + 3 tool defs)
- Modify: `server.py` (extract dashboard bucket logic into a reusable helper)
- Create: `tests/test_chat_tools.py` (start the file, add tests)

The new tools `list_overdue`, `list_due_today`, `list_due_this_week` should reuse the same bucketing logic that `_handle_dashboard` uses. We extract that logic into a `server.bucket_cards_by_due()` function so both consumers stay in sync.

- [ ] **Step 1: Refactor `_handle_dashboard` to use a new `bucket_cards_by_due` helper**

Open `server.py`. Find `_handle_dashboard` (around line 681). Add this module-level helper above it (after the imports near the top of the file, e.g. after the `LISTS` constant):

```python
def bucket_cards_by_due(cards, today=None):
    """Group cards into dashboard buckets by their due date.

    Cards in the 'done' list are skipped entirely.

    Returns: {today, this_week, next_week, later, someday, overdue}.
    """
    from datetime import date, timedelta
    if today is None:
        today = date.today()
    week_end = today + timedelta(days=6 - today.weekday())
    next_week_end = week_end + timedelta(days=7)
    result = {
        'today': [], 'this_week': [], 'next_week': [],
        'later': [], 'someday': [], 'overdue': [],
    }
    for card in cards:
        if card.get('list') == 'done':
            continue
        due = card.get('due', '')
        if not due:
            result['someday'].append(card)
            continue
        try:
            due_date = date.fromisoformat(due)
        except (ValueError, TypeError):
            result['someday'].append(card)
            continue
        if due_date == today:
            result['today'].append(card)
        elif today < due_date <= week_end:
            result['this_week'].append(card)
        elif week_end < due_date <= next_week_end:
            result['next_week'].append(card)
        elif due_date > next_week_end:
            result['later'].append(card)
        elif due_date < today:
            result['overdue'].append(card)
    return result
```

Replace the body of `_handle_dashboard` with:

```python
    def _handle_dashboard(self):
        all_cards = self._get_all_cards()
        result = bucket_cards_by_due(all_cards)
        self._send_json(result)
```

- [ ] **Step 2: Run dashboard tests to confirm refactor works**

Run: `cd D:/Claude/ATC && python -m unittest tests.test_server.TestAggregationAPI -v`
Expected: All 6 aggregation tests pass (including `test_dashboard` and `test_dashboard_excludes_done_cards`).

- [ ] **Step 3: Add the 3 new read-tool definitions to `chat_tools.py`**

In `chat_tools.py`, append to `READ_TOOL_DEFS`:

```python
    {
        "name": "list_overdue",
        "description": "List all overdue cards (due date in the past, not in 'done' list). Returns board, list, slug, title, due, assignee, labels.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_due_today",
        "description": "List all cards due today (not in 'done' list).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_due_this_week",
        "description": "List all cards due this week (today is excluded — use list_due_today for that). Excludes 'done' list.",
        "input_schema": {"type": "object", "properties": {}},
    },
```

- [ ] **Step 4: Add the 3 new read-tool implementations in `chat_tools.py`**

Add this near the existing read-tool implementations (e.g., after `_tool_read_card`):

```python
def _all_cards_with_path() -> list[dict]:
    """Return every card as a dict including board/list/slug, suitable for bucketing."""
    out = []
    boards_order_path = server.DATA_DIR / "_boards-order.json"
    if not boards_order_path.exists():
        return out
    for board in json.loads(boards_order_path.read_text(encoding="utf-8")):
        for list_slug in server.LISTS:
            order = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
            if not order.exists():
                continue
            for slug in json.loads(order.read_text(encoding="utf-8")):
                card = server.read_card(board, list_slug, slug)
                if card is None:
                    continue
                out.append(card)  # already has board/list/slug from server.read_card
    return out


def _bucketed_card_summaries(bucket_name: str) -> list[dict]:
    buckets = server.bucket_cards_by_due(_all_cards_with_path())
    return [
        {
            "b": c.get("board"),
            "l": c.get("list"),
            "s": c.get("slug"),
            "title": c.get("title", ""),
            "due": c.get("due", ""),
            "assignee": c.get("assignee", ""),
            "labels": c.get("labels") or [],
        }
        for c in buckets.get(bucket_name, [])
    ]


def _tool_list_overdue(_args: dict) -> dict:
    return {"cards": _bucketed_card_summaries("overdue")}


def _tool_list_due_today(_args: dict) -> dict:
    return {"cards": _bucketed_card_summaries("today")}


def _tool_list_due_this_week(_args: dict) -> dict:
    return {"cards": _bucketed_card_summaries("this_week")}
```

Then update the `READ_TOOLS` dispatch dict at the end of the file:

```python
READ_TOOLS = {
    "list_boards": _tool_list_boards,
    "list_cards": _tool_list_cards,
    "search_cards": _tool_search_cards,
    "read_card": _tool_read_card,
    "list_overdue": _tool_list_overdue,
    "list_due_today": _tool_list_due_today,
    "list_due_this_week": _tool_list_due_this_week,
}
```

Also extend `_summarize_read_result` so the new tools get a useful summary in the UI event log:

```python
    if name in ("list_overdue", "list_due_today", "list_due_this_week"):
        n = len(payload.get("cards", []))
        return f"{n} card(s)"
```

(Insert that branch above the `if name == "read_card":` line.)

- [ ] **Step 5: Write failing tests for the new bucket tools**

Create `tests/test_chat_tools.py`:

```python
#!/usr/bin/env python3
"""Tests for chat_tools module."""

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import chat_tools


def make_card(data_dir, board, lst, slug, body=""):
    list_dir = data_dir / "boards" / board / lst
    list_dir.mkdir(parents=True, exist_ok=True)
    (list_dir / f"{slug}.md").write_text(body, encoding="utf-8")
    order_file = list_dir / "_order.json"
    order = json.loads(order_file.read_text()) if order_file.exists() else []
    order.append(slug)
    order_file.write_text(json.dumps(order), encoding="utf-8")


def make_board(data_dir, slug, name="Test"):
    board_dir = data_dir / "boards" / slug
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "_board.md").write_text(
        f"---\nname: {name}\ncolor: '#000000'\n---\n", encoding="utf-8")
    for lst in ("ideas", "backlog", "in-progress", "done"):
        (board_dir / lst).mkdir(exist_ok=True)
        (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
    boards_order = data_dir / "_boards-order.json"
    order = json.loads(boards_order.read_text()) if boards_order.exists() else []
    if slug not in order:
        order.append(slug)
        boards_order.write_text(json.dumps(order), encoding="utf-8")


def card_body(title, **kwargs):
    """Build a frontmatter-only card body."""
    lines = [f"title: {title}", "created: 2026-01-01", "updated: 2026-01-01"]
    for k, v in kwargs.items():
        lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(lines) + "\n---\n\n## Description\n\n\n## Checklist\n\n\n## Comments\n\n"


class BucketToolsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir, "alpha")

    def tearDown(self):
        self.tmp.cleanup()


class TestListOverdue(BucketToolsBase):
    def test_overdue_in_progress_appears(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        make_card(self.data_dir, "alpha", "in-progress", "old-task",
                  card_body("Old", due=past))
        out = chat_tools._tool_list_overdue({})
        self.assertEqual(len(out["cards"]), 1)
        self.assertEqual(out["cards"][0]["s"], "old-task")

    def test_overdue_done_excluded(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        make_card(self.data_dir, "alpha", "done", "finished",
                  card_body("Finished", due=past))
        out = chat_tools._tool_list_overdue({})
        self.assertEqual(out["cards"], [])


class TestListDueToday(BucketToolsBase):
    def test_today_appears(self):
        today = date.today().isoformat()
        make_card(self.data_dir, "alpha", "backlog", "today-task",
                  card_body("Today", due=today))
        out = chat_tools._tool_list_due_today({})
        self.assertEqual(len(out["cards"]), 1)

    def test_tomorrow_does_not(self):
        tmrw = (date.today() + timedelta(days=1)).isoformat()
        make_card(self.data_dir, "alpha", "backlog", "tmrw",
                  card_body("Tmrw", due=tmrw))
        out = chat_tools._tool_list_due_today({})
        self.assertEqual(out["cards"], [])


class TestListDueThisWeek(BucketToolsBase):
    def test_today_excluded_from_this_week(self):
        # 'this_week' bucket explicitly excludes today (today is its own bucket).
        today = date.today().isoformat()
        make_card(self.data_dir, "alpha", "backlog", "today-task",
                  card_body("Today", due=today))
        out = chat_tools._tool_list_due_this_week({})
        self.assertEqual(out["cards"], [])

    def test_within_week_appears_when_room(self):
        # Pick a date that's strictly between today and Sunday-of-this-week.
        today = date.today()
        days_left_in_week = 6 - today.weekday()  # 0=Mon, 6=Sun
        if days_left_in_week == 0:
            self.skipTest("today is Sunday; no room in this_week")
        target = (today + timedelta(days=1)).isoformat()
        make_card(self.data_dir, "alpha", "backlog", "soon",
                  card_body("Soon", due=target))
        out = chat_tools._tool_list_due_this_week({})
        self.assertEqual(len(out["cards"]), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run the tests, confirm they pass**

Run: `cd D:/Claude/ATC && python -m unittest tests.test_chat_tools -v`
Expected: 5 tests pass (one may be skipped if today is Sunday).

- [ ] **Step 7: Run full suite to confirm no regressions**

Run: `cd D:/Claude/ATC && python -m unittest discover tests`
Expected: All previous tests still pass plus 5 new ones.

- [ ] **Step 8: Commit**

```bash
git add chat_tools.py server.py tests/test_chat_tools.py
git commit -m "feat(chat_tools): add list_overdue/list_due_today/list_due_this_week tools"
```

---

## Task 4: New read tools — find_by_label and find_by_assignee

**Files:**
- Modify: `chat_tools.py`
- Modify: `tests/test_chat_tools.py`

- [ ] **Step 1: Add tool defs**

In `chat_tools.py`, append to `READ_TOOL_DEFS`:

```python
    {
        "name": "find_by_label",
        "description": "Find all cards with a given label (exact match, case-insensitive). Excludes 'done' list.",
        "input_schema": {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
        },
    },
    {
        "name": "find_by_assignee",
        "description": "Find all cards assigned to the given person (exact match, case-insensitive). Excludes 'done' list.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
```

- [ ] **Step 2: Add implementations**

In `chat_tools.py`, after the bucket-based tools:

```python
def _tool_find_by_label(args: dict) -> dict:
    target = args["label"].strip().lower()
    out = []
    for card in _all_cards_with_path():
        if card.get("list") == "done":
            continue
        labels = [str(l).lower() for l in (card.get("labels") or [])]
        if target in labels:
            out.append({
                "b": card.get("board"),
                "l": card.get("list"),
                "s": card.get("slug"),
                "title": card.get("title", ""),
                "due": card.get("due", ""),
                "assignee": card.get("assignee", ""),
                "labels": card.get("labels") or [],
            })
    return {"cards": out}


def _tool_find_by_assignee(args: dict) -> dict:
    target = args["name"].strip().lower()
    out = []
    for card in _all_cards_with_path():
        if card.get("list") == "done":
            continue
        if (card.get("assignee") or "").strip().lower() == target:
            out.append({
                "b": card.get("board"),
                "l": card.get("list"),
                "s": card.get("slug"),
                "title": card.get("title", ""),
                "due": card.get("due", ""),
                "assignee": card.get("assignee", ""),
                "labels": card.get("labels") or [],
            })
    return {"cards": out}
```

Update `READ_TOOLS`:

```python
READ_TOOLS = {
    "list_boards": _tool_list_boards,
    "list_cards": _tool_list_cards,
    "search_cards": _tool_search_cards,
    "read_card": _tool_read_card,
    "list_overdue": _tool_list_overdue,
    "list_due_today": _tool_list_due_today,
    "list_due_this_week": _tool_list_due_this_week,
    "find_by_label": _tool_find_by_label,
    "find_by_assignee": _tool_find_by_assignee,
}
```

Extend `_summarize_read_result`:

```python
    if name == "find_by_label":
        n = len(payload.get("cards", []))
        return f"{n} card(s) with label '{args.get('label', '')}'"
    if name == "find_by_assignee":
        n = len(payload.get("cards", []))
        return f"{n} card(s) assigned to '{args.get('name', '')}'"
```

(Insert above the `read_card` branch.)

- [ ] **Step 3: Add tests in `tests/test_chat_tools.py`**

Append:

```python
class TestFindByLabel(BucketToolsBase):
    def test_exact_match_case_insensitive(self):
        make_card(self.data_dir, "alpha", "backlog", "u",
                  card_body("U", labels="[urgent]"))
        out = chat_tools._tool_find_by_label({"label": "URGENT"})
        self.assertEqual(len(out["cards"]), 1)

    def test_partial_does_not_match(self):
        make_card(self.data_dir, "alpha", "backlog", "u",
                  card_body("U", labels="[urgent]"))
        out = chat_tools._tool_find_by_label({"label": "urge"})
        self.assertEqual(out["cards"], [])

    def test_done_excluded(self):
        make_card(self.data_dir, "alpha", "done", "u",
                  card_body("U", labels="[urgent]"))
        out = chat_tools._tool_find_by_label({"label": "urgent"})
        self.assertEqual(out["cards"], [])


class TestFindByAssignee(BucketToolsBase):
    def test_exact_match_case_insensitive(self):
        make_card(self.data_dir, "alpha", "backlog", "m",
                  card_body("M", assignee="Maria"))
        out = chat_tools._tool_find_by_assignee({"name": "maria"})
        self.assertEqual(len(out["cards"]), 1)

    def test_no_match(self):
        make_card(self.data_dir, "alpha", "backlog", "m",
                  card_body("M", assignee="Maria"))
        out = chat_tools._tool_find_by_assignee({"name": "Bob"})
        self.assertEqual(out["cards"], [])

    def test_done_excluded(self):
        make_card(self.data_dir, "alpha", "done", "m",
                  card_body("M", assignee="Maria"))
        out = chat_tools._tool_find_by_assignee({"name": "maria"})
        self.assertEqual(out["cards"], [])
```

- [ ] **Step 4: Run tests**

Run: `cd D:/Claude/ATC && python -m unittest tests.test_chat_tools -v`
Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add chat_tools.py tests/test_chat_tools.py
git commit -m "feat(chat_tools): add find_by_label and find_by_assignee tools"
```

---

## Task 5: Make `apply_operations` tolerant of `note_id=None`

**Files:**
- Modify: `notes.py` (`_do_create_card`, `_record_in_note`, `apply_operations`)
- Modify: `tests/test_notes.py` (add a test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_notes.py` inside the `TestApply` class:

```python
    def test_apply_with_no_note_id_skips_attachment_and_recording(self):
        ops = [{"op": "create_card", "board": "alpha", "list": "backlog",
                "title": "Chat-created"}]
        result = notes.apply_operations(ops, None)
        self.assertEqual(len(result["applied"]), 1)
        card = server.read_card("alpha", "backlog", "chat-created")
        self.assertIsNotNone(card)
        # No source-note attachment when note_id is None.
        self.assertEqual(card.get("attachments") or [], [])
```

- [ ] **Step 2: Run the test, confirm it fails**

Run: `cd D:/Claude/ATC && python -m unittest tests.test_notes.TestApply.test_apply_with_no_note_id_skips_attachment_and_recording -v`
Expected: FAIL — currently `_do_create_card` always adds the attachment, and `_record_in_note` is always called.

- [ ] **Step 3: Update `_do_create_card`**

In `notes.py`, change `_do_create_card`. The current `meta` dict always includes:

```python
        "attachments": [
            {"name": f"Source note: {note_id}", "url": f"{NOTE_URL_PREFIX}{note_id}"}
        ],
```

Change to:

```python
        "attachments": (
            [{"name": f"Source note: {note_id}", "url": f"{NOTE_URL_PREFIX}{note_id}"}]
            if note_id else []
        ),
```

- [ ] **Step 4: Update `_do_add_comment` to skip the source link when `note_id` is None**

Currently it always builds `note_link = f"_(from [meeting note]({NOTE_URL_PREFIX}{note_id}))_"`. Change the body composition:

```python
def _do_add_comment(op: dict, note_id) -> dict:
    board, list_slug, card_slug = op["board"], op["list"], op["card"]
    card = server.read_card(board, list_slug, card_slug)
    if card is None:
        raise ValueError("target card missing")
    body = card["body"]
    today = _today_iso()
    if note_id:
        note_link = f"_(from [meeting note]({NOTE_URL_PREFIX}{note_id}))_"
        new_comment = f"\n**{today} - Agent:**\n{op['text']}\n\n{note_link}\n"
    else:
        new_comment = f"\n**{today} - Agent:**\n{op['text']}\n"
    body = body.rstrip() + "\n" + new_comment
    card["updated"] = today
    server.write_card(board, list_slug, card_slug, card, body)
    return {"target": f"{board}/{list_slug}/{card_slug}"}
```

- [ ] **Step 5: Update `apply_operations` to skip `_record_in_note` when `note_id` is None**

In `apply_operations`, find the line `_record_in_note(note_id, op, outcome["target"])` and wrap it:

```python
            outcome = handler(op, note_id)
            applied.append({"op": op["op"], "target": outcome["target"]})
            if note_id:
                _record_in_note(note_id, op, outcome["target"])
```

- [ ] **Step 6: Run the new test plus all existing notes tests**

Run: `cd D:/Claude/ATC && python -m unittest tests.test_notes -v`
Expected: All previous tests pass plus the new one.

- [ ] **Step 7: Commit**

```bash
git add notes.py tests/test_notes.py
git commit -m "feat(notes): apply_operations accepts note_id=None for chat use"
```

---

## Task 6: Build `chat.py` — system prompt, tool list, chat_stream generator

**Files:**
- Create: `chat.py`
- Create: `tests/test_chat.py`

- [ ] **Step 1: Write `chat.py` with the system prompt and the generator**

Create `chat.py`:

```python
"""Chat-sidebar integration: tool-use loop driven by an open conversation.

Differs from notes.analyze_stream in two ways:
- No 'finish' tool — the loop terminates when the model returns a turn
  with no tool_use blocks (i.e., a text-only answer).
- Takes a full message history and returns the appended assistant turn(s)
  in the 'done' event so the caller can grow the history client-side.
"""
import json

from chat_tools import (
    READ_TOOL_DEFS, WRITE_TOOL_DEFS, READ_TOOLS,
    _WRITE_OP_NAMES, _queue_op,
    _summarize_read_result, _queued_summary_fields,
)


SYSTEM_PROMPT = """You are an assistant inside a personal kanban app.
You can answer questions about the user's boards and cards, and you
can propose changes (create cards, add comments, tick checklist items,
move cards, update fields).

WRITE TOOLS DO NOT EXECUTE. They queue a proposed operation that the
user must confirm before it is applied. When you queue ops, briefly
explain in plain text what you proposed and why so the user can decide.

Use read tools liberally to ground your answers. Prefer:
- list_overdue / list_due_today / list_due_this_week for time questions
- find_by_label / find_by_assignee for filter questions
- search_cards for fuzzy title lookup
- read_card when you need a card's body, checklist, or comments

When you have answered the user, just stop calling tools and write a
short text response. The conversation continues; you do not need a
'finish' tool.
"""


CHAT_TOOLS = READ_TOOL_DEFS + WRITE_TOOL_DEFS

MAX_TOOL_TURNS = 16


def _block_to_dict(block) -> dict:
    """Normalize an SDK content block into a plain dict for the next assistant turn."""
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    return {"type": btype or "unknown"}


def chat_stream(messages: list, *, model: str, client,
                max_turns: int = MAX_TOOL_TURNS):
    """Run a tool-use loop on top of the given conversation history.

    Yields events. Event shapes (all dicts have a 'type' key):
      {"type": "started"}
      {"type": "turn",   "n": int}
      {"type": "tool",   "name": str, "args": dict}
      {"type": "result", "name": str, "summary": str}
      {"type": "queued", "op": str, "title"|"text"|...: ...}
      {"type": "text",   "text": str}
      {"type": "done",   "messages_appended": [...assistant blocks...],
                         "proposed_operations": [...]}
      {"type": "error",  "message": str}

    The caller should append `messages_appended` to its own history before
    sending the next user message.
    """
    yield {"type": "started"}

    proposed_ops: list[dict] = []
    # Local copy so we don't mutate the caller's list.
    msgs = list(messages)
    appended: list[dict] = []

    for turn in range(1, max_turns + 1):
        yield {"type": "turn", "n": turn}
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=CHAT_TOOLS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            ],
            messages=msgs,
        )

        assistant_blocks = [_block_to_dict(b) for b in response.content]
        msgs.append({"role": "assistant", "content": assistant_blocks})
        appended.append({"role": "assistant", "content": assistant_blocks})

        # Emit text blocks so the UI can render them progressively.
        for b in response.content:
            if getattr(b, "type", "") == "text":
                yield {"type": "text", "text": getattr(b, "text", "")}

        tool_use_blocks = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if not tool_use_blocks:
            # Text-only turn = "I'm done answering". End the loop.
            break

        tool_results = []
        for block in tool_use_blocks:
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            tool_id = getattr(block, "id", "")
            yield {"type": "tool", "name": name, "args": args}
            try:
                if name in READ_TOOLS:
                    payload = READ_TOOLS[name](args)
                    yield {"type": "result", "name": name,
                           "summary": _summarize_read_result(name, args, payload)}
                elif name in _WRITE_OP_NAMES:
                    payload = _queue_op(name, args, proposed_ops)
                    yield {"type": "queued", "op": name,
                           **_queued_summary_fields(name, args)}
                else:
                    payload = {"error": f"unknown tool '{name}'"}
                    yield {"type": "result", "name": name,
                           "summary": f"unknown tool '{name}'"}
            except (KeyError, ValueError, TypeError) as e:
                payload = {"error": str(e)}
                yield {"type": "result", "name": name, "summary": f"error: {e}"}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": json.dumps(payload),
            })

        msgs.append({"role": "user", "content": tool_results})
        appended.append({"role": "user", "content": tool_results})

    yield {"type": "done",
           "messages_appended": appended,
           "proposed_operations": proposed_ops}
```

- [ ] **Step 2: Write `tests/test_chat.py`**

Create `tests/test_chat.py`:

```python
#!/usr/bin/env python3
"""Tests for chat module."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import chat
from tests._llm_fakes import FakeClient, FakeResponse, text_block, tool_use


def _setup_empty_data(case):
    case.tmp = tempfile.TemporaryDirectory()
    case.data_dir = Path(case.tmp.name)
    server.DATA_DIR = case.data_dir
    (case.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")


class TestChatStream(unittest.TestCase):
    def setUp(self):
        _setup_empty_data(self)

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_only_response_terminates(self):
        client = FakeClient([FakeResponse([text_block("hello")])])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "hi"}],
            model="claude-opus-4-7", client=client,
        ))
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "started")
        self.assertIn("text", types)
        self.assertEqual(types[-1], "done")
        text_evt = next(e for e in events if e["type"] == "text")
        self.assertEqual(text_evt["text"], "hello")

    def test_tool_then_text(self):
        client = FakeClient([
            FakeResponse([tool_use("list_boards", {})]),
            FakeResponse([text_block("you have N boards")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "how many boards?"}],
            model="claude-opus-4-7", client=client,
        ))
        types = [e["type"] for e in events]
        # started, turn(1), tool, result, turn(2), text, done
        self.assertEqual(types[0], "started")
        self.assertIn("tool", types)
        self.assertIn("result", types)
        self.assertIn("text", types)
        self.assertEqual(types[-1], "done")

    def test_queued_write_op_appears_in_done(self):
        client = FakeClient([
            FakeResponse([tool_use("create_card", {
                "board": "alpha", "list": "backlog", "title": "X",
                "confidence": "high", "reason": "y",
            })]),
            FakeResponse([text_block("queued it for you")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "make X"}],
            model="claude-opus-4-7", client=client,
        ))
        queued = [e for e in events if e["type"] == "queued"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["title"], "X")
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(len(done["proposed_operations"]), 1)
        self.assertEqual(done["proposed_operations"][0]["op"], "create_card")

    def test_max_turns_terminates(self):
        # Model keeps calling list_boards forever; loop should cap and emit done.
        client = FakeClient([
            FakeResponse([tool_use("list_boards", {})]) for _ in range(10)
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "spin"}],
            model="claude-opus-4-7", client=client, max_turns=3,
        ))
        self.assertEqual(events[-1]["type"], "done")

    def test_tool_error_recovers(self):
        client = FakeClient([
            FakeResponse([tool_use("list_cards", {})]),  # missing required 'board'
            FakeResponse([text_block("recovered")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "broken call"}],
            model="claude-opus-4-7", client=client,
        ))
        result_evts = [e for e in events if e["type"] == "result"]
        self.assertTrue(any("error" in r["summary"] for r in result_evts))
        self.assertEqual(events[-1]["type"], "done")

    def test_done_messages_appended_grows_history(self):
        client = FakeClient([
            FakeResponse([tool_use("list_boards", {})]),
            FakeResponse([text_block("done")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "hi"}],
            model="claude-opus-4-7", client=client,
        ))
        done = events[-1]
        # Two assistant turns + one tool_result user turn = 3 appended messages.
        roles = [m["role"] for m in done["messages_appended"]]
        self.assertEqual(roles, ["assistant", "user", "assistant"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the new tests**

Run: `cd D:/Claude/ATC && python -m unittest tests.test_chat -v`
Expected: 6 tests pass.

- [ ] **Step 4: Commit**

```bash
git add chat.py tests/test_chat.py
git commit -m "feat(chat): chat_stream tool-use loop with text-terminated turns"
```

---

## Task 7: Add `POST /api/chat` SSE endpoint

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Wire the route**

Find the request-routing block in `server.py` (around line 394 where `/api/notes/analyze` is wired). Add:

```python
        # /api/chat
        if path == '/api/chat' and method == 'POST':
            return self._handle_chat()
```

- [ ] **Step 2: Add the handler**

After `_handle_notes_analyze` in `server.py`, add `_handle_chat`. It mirrors the SSE pattern:

```python
    def _handle_chat(self):
        """Stream the chat tool-use loop as Server-Sent Events."""
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            return self._send_error(400, 'invalid json')
        messages = body.get('messages', [])
        if not isinstance(messages, list) or not messages:
            return self._send_error(400, 'messages list required')
        try:
            client = llm_config.get_client()
        except llm_config.NotConfigured:
            return self._send_error(400, 'LLM not configured')
        cfg = llm_config.load()

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        def emit(event):
            line = f"data: {json.dumps(event)}\n\n".encode('utf-8')
            try:
                self.wfile.write(line)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                raise

        try:
            import chat
            for event in chat.chat_stream(messages, model=cfg['model'], client=client):
                emit(event)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            try:
                emit({"type": "error", "message": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
```

- [ ] **Step 3: Add endpoint tests**

In `tests/test_server.py`, add a new test class at the end (before the `if __name__` block). Reuse the existing `parse_sse_events` and `stream_analyze`-style helpers — they're already defined at module level.

```python
class TestChatEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")

        from tests._llm_fakes import FakeClient, FakeResponse, text_block, tool_use
        scripted = [
            FakeResponse([text_block("hi from the model")]),
        ]
        import llm_config
        self._orig_get_client = llm_config.get_client
        llm_config.get_client = lambda: FakeClient(list(scripted))

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

    def test_chat_streams_sse_content_type(self):
        url = f"http://localhost:{self.port}/api/chat"
        body = {"messages": [{"role": "user", "content": "hi"}]}
        req = urllib.request.Request(url,
            data=json.dumps(body).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as r:
            self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
            r.read()

    def test_chat_returns_done_event(self):
        url = f"http://localhost:{self.port}/api/chat"
        body = {"messages": [{"role": "user", "content": "hi"}]}
        req = urllib.request.Request(url,
            data=json.dumps(body).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as r:
            events = parse_sse_events(r.read().decode('utf-8'))
        self.assertEqual(events[0]["type"], "started")
        self.assertEqual(events[-1]["type"], "done")
        text = next(e for e in events if e["type"] == "text")
        self.assertEqual(text["text"], "hi from the model")

    def test_chat_rejects_empty_messages(self):
        status, body = make_request_port(self.port, "POST", "/api/chat", {"messages": []})
        self.assertEqual(status, 400)
        self.assertIn("messages", body.get("error", ""))
```

(`HTTPServer`, `threading`, `urllib.request`, `tempfile`, `Path`, `make_request_port`, `parse_sse_events` are all imported elsewhere in this file — no new imports needed.)

- [ ] **Step 4: Run the new endpoint tests**

Run: `cd D:/Claude/ATC && python -m unittest tests.test_server.TestChatEndpoint -v`
Expected: 3 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd D:/Claude/ATC && python -m unittest discover tests`
Expected: All tests still pass.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): POST /api/chat streams chat tool-use loop as SSE"
```

---

## Task 8: Add the chat sidebar markup, CSS, and toggle button

**Files:**
- Modify: `index.html`

This task is structural only — markup + CSS + the toggle wiring. Streaming + rendering happens in Task 9.

- [ ] **Step 1: Add the toggle button to the header**

In `index.html`, find the header row that contains the "Process Notes" button. Search for `id="btn-process-notes"`. Right after that button, add:

```html
<button id="btn-chat-toggle" class="header-btn">💬 Chat</button>
```

- [ ] **Step 2: Add the sidebar markup at the end of `<body>`**

Find the closing `</body>` tag. Just before it, add:

```html
<aside id="chat-sidebar" hidden>
  <div class="chat-header">
    <span class="chat-title">Chat</span>
    <button id="btn-chat-clear" class="chat-icon-btn" title="Clear conversation">🗑</button>
  </div>
  <div id="chat-messages" class="chat-messages"></div>
  <div class="chat-input-row">
    <textarea id="chat-input" rows="2" placeholder="Ask anything…"></textarea>
    <button id="btn-chat-send" class="btn-new-card">Send</button>
  </div>
</aside>
```

- [ ] **Step 3: Add the CSS**

Add to the existing `<style>` block (anywhere reasonable — group with the dashboard styles):

```css
/* ── Chat sidebar ─────────────────────────────────────────────── */
#chat-sidebar {
  position: fixed;
  top: 0;
  right: 0;
  width: 380px;
  height: 100vh;
  background: #fff;
  border-left: 1px solid #dfe1e6;
  display: flex;
  flex-direction: column;
  z-index: 50;
  box-shadow: -2px 0 8px rgba(0,0,0,0.06);
}
#chat-sidebar[hidden] { display: none; }
body.chat-open { padding-right: 380px; }
.chat-header {
  padding: 12px 16px;
  border-bottom: 1px solid #dfe1e6;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}
.chat-icon-btn {
  background: none;
  border: none;
  font-size: 16px;
  cursor: pointer;
  color: #5e6c84;
  padding: 2px 6px;
}
.chat-icon-btn:hover { color: #172b4d; }
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.chat-msg-user {
  align-self: flex-end;
  background: #0052cc;
  color: #fff;
  padding: 8px 12px;
  border-radius: 12px 12px 2px 12px;
  max-width: 82%;
  font-size: 13px;
  white-space: pre-wrap;
}
.chat-msg-assistant {
  align-self: flex-start;
  background: #f4f5f7;
  color: #172b4d;
  padding: 8px 12px;
  border-radius: 12px 12px 12px 2px;
  max-width: 88%;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-tool-line {
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 11px;
  color: #5e6c84;
  margin: 2px 0;
}
.chat-status {
  font-size: 11px;
  color: #5e6c84;
  font-style: italic;
}
.chat-error {
  color: #bf2600;
  background: #ffebe6;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
}
.chat-ops-panel {
  margin-top: 8px;
  padding: 8px;
  background: #fff;
  border: 1px solid #dfe1e6;
  border-radius: 6px;
}
.chat-ops-panel .panel-title {
  font-weight: 600;
  font-size: 12px;
  margin-bottom: 6px;
  color: #172b4d;
}
.chat-ops-panel.applied { background: #e3fcef; border-color: #abf5d1; }
.chat-input-row {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid #dfe1e6;
  align-items: flex-end;
}
#chat-input {
  flex: 1;
  resize: none;
  font-family: inherit;
  font-size: 13px;
  padding: 6px 10px;
  border: 1px solid #dfe1e6;
  border-radius: 4px;
  box-sizing: border-box;
}
@media (max-width: 1100px) {
  body.chat-open { padding-right: 320px; }
  #chat-sidebar { width: 320px; }
}
```

- [ ] **Step 4: Wire toggle + clear + localStorage**

In the existing JS section (anywhere with the other top-level event handlers — search for `btn-process-notes` to find the spot), add:

```javascript
/* ================================================================
   Chat Sidebar — toggle, clear, state
   ================================================================ */
const CHAT_OPEN_KEY = 'atc-chat-open';
const chatState = {
  messages: [],            // Anthropic message history (sent on every turn)
  pendingByTurn: {},       // turnIndex → list of unapplied ops
  pendingApplyNote: '',    // prefix to prepend to next user message
  inflight: false,         // true while a /api/chat request is open
  turnCounter: 0,          // increments per outgoing user message
};

function setChatOpen(open) {
  const sidebar = document.getElementById('chat-sidebar');
  if (open) {
    sidebar.hidden = false;
    document.body.classList.add('chat-open');
  } else {
    sidebar.hidden = true;
    document.body.classList.remove('chat-open');
  }
  localStorage.setItem(CHAT_OPEN_KEY, open ? '1' : '0');
}

function toggleChat() {
  const isOpen = !document.getElementById('chat-sidebar').hidden;
  setChatOpen(!isOpen);
}

function clearChat() {
  chatState.messages = [];
  chatState.pendingByTurn = {};
  chatState.pendingApplyNote = '';
  chatState.turnCounter = 0;
  document.getElementById('chat-messages').innerHTML = '';
}

document.getElementById('btn-chat-toggle').addEventListener('click', toggleChat);
document.getElementById('btn-chat-clear').addEventListener('click', () => {
  if (chatState.messages.length === 0 || confirm('Clear conversation?')) {
    clearChat();
  }
});

// Restore open state on load.
if (localStorage.getItem(CHAT_OPEN_KEY) === '1') {
  setChatOpen(true);
}
```

- [ ] **Step 5: Verify visually**

Manual check (do this even though tests don't cover it):
1. Restart the server (`Stop-Process` the running `server.py`, then `python server.py`).
2. Reload the browser.
3. Click "💬 Chat" — sidebar appears on the right, main view reflows.
4. Click the trash icon — confirms (or no-op if empty).
5. Reload the page with the sidebar open — it stays open.
6. Click toggle to close — reload — stays closed.

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(ui): chat sidebar markup, CSS, and toggle wiring"
```

---

## Task 9: Wire the chat send + SSE streaming + render

**Files:**
- Modify: `index.html`

This task adds the streaming reader and the per-event renderers. The proposed-ops panel UI is added in Task 10.

- [ ] **Step 1: Add the streaming reader**

Add inside the same chat JS section as Task 8's wiring:

```javascript
async function streamChat(messages, onEvent) {
  const r = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify({ messages }),
  });
  if (!r.ok) throw new Error(`POST /api/chat: ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let last = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const dataLines = block.split('\n')
        .filter(l => l.startsWith('data:'))
        .map(l => l.slice(5).trimStart());
      if (!dataLines.length) continue;
      let parsed;
      try { parsed = JSON.parse(dataLines.join('\n')); }
      catch { continue; }
      onEvent(parsed);
      if (parsed.type === 'done') last = parsed;
      if (parsed.type === 'error') throw new Error(parsed.message || 'chat error');
    }
  }
  return last;
}
```

- [ ] **Step 2: Add render helpers for chat messages**

```javascript
function renderUserMessage(text) {
  const el = document.createElement('div');
  el.className = 'chat-msg-user';
  el.textContent = text;
  document.getElementById('chat-messages').appendChild(el);
  scrollChatToBottom();
}

function renderAssistantBubble() {
  const wrap = document.createElement('div');
  wrap.className = 'chat-msg-assistant';
  // Internal structure: a status line, an array of tool lines, prose, ops panel.
  wrap.innerHTML = `
    <div class="chat-status">Thinking…</div>
    <div class="chat-tools"></div>
    <div class="chat-text"></div>
    <div class="chat-ops"></div>
  `;
  document.getElementById('chat-messages').appendChild(wrap);
  scrollChatToBottom();
  return wrap;
}

function setBubbleStatus(bubble, text) {
  bubble.querySelector('.chat-status').textContent = text;
}

function clearBubbleStatus(bubble) {
  bubble.querySelector('.chat-status').remove();
}

function appendToolLine(bubble, name, args) {
  const tools = bubble.querySelector('.chat-tools');
  const line = document.createElement('div');
  line.className = 'chat-tool-line';
  line.textContent = `→ ${name}` + (Object.keys(args).length ? ` ${JSON.stringify(args)}` : '');
  tools.appendChild(line);
  scrollChatToBottom();
  return line;
}

function appendToolResultSummary(toolLine, summary) {
  toolLine.textContent += `  ${summary}`;
}

function appendAssistantText(bubble, text) {
  const prose = bubble.querySelector('.chat-text');
  // If there's already prose, separate with a blank line.
  if (prose.textContent) prose.textContent += '\n\n';
  prose.textContent += text;
  scrollChatToBottom();
}

function appendErrorLine(bubble, message) {
  const err = document.createElement('div');
  err.className = 'chat-error';
  err.textContent = message;
  bubble.appendChild(err);
  scrollChatToBottom();
}

function scrollChatToBottom() {
  const el = document.getElementById('chat-messages');
  el.scrollTop = el.scrollHeight;
}
```

- [ ] **Step 3: Wire send**

```javascript
async function sendChatMessage() {
  if (chatState.inflight) return;
  const inputEl = document.getElementById('chat-input');
  const text = inputEl.value.trim();
  if (!text) return;

  // Combine pending apply-note (if any) with the typed text for what we
  // SEND to the model, but only render the user's actual text in the log.
  let outgoing = text;
  if (chatState.pendingApplyNote) {
    outgoing = chatState.pendingApplyNote.trim() + '\n\n' + text;
    chatState.pendingApplyNote = '';
  }

  chatState.turnCounter += 1;
  const turnIndex = chatState.turnCounter;

  renderUserMessage(text);
  inputEl.value = '';
  chatState.messages.push({ role: 'user', content: outgoing });

  const bubble = renderAssistantBubble();
  bubble.dataset.turnIndex = String(turnIndex);
  chatState.inflight = true;
  document.getElementById('btn-chat-send').disabled = true;

  // Track the most-recent tool line so 'result' events can append to it.
  let lastToolLine = null;

  try {
    const done = await streamChat(chatState.messages, (ev) => {
      switch (ev.type) {
        case 'started':
          setBubbleStatus(bubble, 'Working…');
          break;
        case 'turn':
          setBubbleStatus(bubble, `Turn ${ev.n}…`);
          break;
        case 'tool':
          lastToolLine = appendToolLine(bubble, ev.name, ev.args);
          break;
        case 'result':
          if (lastToolLine) appendToolResultSummary(lastToolLine, ev.summary);
          break;
        case 'queued':
          if (!chatState.pendingByTurn[turnIndex]) chatState.pendingByTurn[turnIndex] = [];
          chatState.pendingByTurn[turnIndex].push(ev);
          renderOpsPanelForTurn(bubble, turnIndex);
          break;
        case 'text':
          appendAssistantText(bubble, ev.text);
          break;
        case 'done':
          clearBubbleStatus(bubble);
          break;
      }
    });
    if (done && Array.isArray(done.messages_appended)) {
      chatState.messages.push(...done.messages_appended);
    }
  } catch (err) {
    appendErrorLine(bubble, err.message);
    // Don't commit a half-broken assistant turn into history.
  } finally {
    chatState.inflight = false;
    document.getElementById('btn-chat-send').disabled = false;
    inputEl.focus();
  }
}

document.getElementById('btn-chat-send').addEventListener('click', sendChatMessage);
document.getElementById('chat-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  }
});

// Stub for Task 10 — overwritten there.
function renderOpsPanelForTurn(bubble, turnIndex) { /* filled in next task */ }
```

- [ ] **Step 4: Manual smoke test**

Restart the server, reload the page.

Test 1: Open chat. Type "list my boards" — model should call `list_boards`, then answer in text. The tool line and the answer should both appear.

Test 2: Type "what's overdue?" — model uses `list_overdue` and reports.

Test 3: Type "draft a card titled 'test'" — model calls `create_card`. The `queued` event fires but no UI shows yet (panel rendering is Task 10). Verify in browser devtools / network tab that the SSE stream has a `queued` event.

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(ui): chat send + SSE streaming + per-event render"
```

---

## Task 10: Inline proposed-ops panel + apply flow

**Files:**
- Modify: `index.html`

We extract the existing Process Notes op-row rendering into a shared function and use it both places. We also implement `renderOpsPanelForTurn` (stubbed in Task 9) and the apply call.

- [ ] **Step 1: Find the existing `renderOps` from Process Notes and extract a shared variant**

Find the `renderOps` function (around line 3162 in current `index.html`, used by Process Notes step 2). Right above it, add a shared variant that takes the container directly:

```javascript
function renderOpsRows(ops, container) {
  container.innerHTML = '';
  if (!ops.length) {
    container.innerHTML = '<p style="color:#5e6c84;font-size:13px;">No operations proposed.</p>';
    return;
  }
  ops.forEach((op, i) => {
    const div = document.createElement('div');
    div.className = 'op-row';
    const checked = op.confidence !== 'low' ? 'checked' : '';
    const target = op.card
      ? `${op.board || ''} / ${op.list || ''} / ${op.card}`
      : `${op.board || ''} / ${op.list || ''}`;
    const summary = op.op === 'create_card' ? `"${escHtml(op.title || '')}"`
      : op.op === 'add_comment' ? `"${escHtml((op.text || '').slice(0, 80))}"`
      : op.op === 'tick_checklist' ? `tick "${escHtml(op.item || '')}"`
      : op.op === 'add_checklist_item' ? `add "${escHtml(op.item || '')}"`
      : op.op === 'move_card' ? `→ ${escHtml(op.target_list || '')}`
      : op.op === 'update_field' ? `${escHtml(op.field || '')} = ${escHtml(JSON.stringify(op.value))}`
      : '';
    div.innerHTML = `
      <label>
        <input type="checkbox" data-i="${i}" ${checked} />
        <strong>${escHtml(op.op || '')}</strong>
        <span class="conf conf-${op.confidence || 'med'}">${escHtml(op.confidence || '')}</span>
        <span class="target">${escHtml(target)}</span>
        <span class="summary">${summary}</span>
        <div class="reason">${escHtml(op.reason || '')}</div>
      </label>`;
    container.appendChild(div);
  });
}
```

Then change the existing `renderOps` to delegate:

```javascript
function renderOps(ops) {
  renderOpsRows(ops, document.getElementById('notes-ops'));
}
```

- [ ] **Step 2: Replace the stub `renderOpsPanelForTurn`**

Delete the stub from Task 9 and replace with:

```javascript
function renderOpsPanelForTurn(bubble, turnIndex) {
  const ops = chatState.pendingByTurn[turnIndex] || [];
  let panel = bubble.querySelector('.chat-ops-panel');
  if (!panel) {
    panel = document.createElement('div');
    panel.className = 'chat-ops-panel';
    panel.innerHTML = `
      <div class="panel-title">Proposed changes (<span class="chat-ops-count">0</span>)</div>
      <div class="chat-ops-rows"></div>
      <button class="chat-ops-apply btn-new-card" style="margin-top:6px;">Apply selected</button>
    `;
    bubble.querySelector('.chat-ops').appendChild(panel);
    panel.querySelector('.chat-ops-apply').addEventListener('click', () => {
      applyOpsForTurn(bubble, turnIndex);
    });
  }
  panel.querySelector('.chat-ops-count').textContent = ops.length;
  renderOpsRows(ops, panel.querySelector('.chat-ops-rows'));
}

async function applyOpsForTurn(bubble, turnIndex) {
  const panel = bubble.querySelector('.chat-ops-panel');
  if (!panel) return;
  const allOps = chatState.pendingByTurn[turnIndex] || [];
  const checks = panel.querySelectorAll('input[type=checkbox]');
  const selected = [];
  checks.forEach(c => { if (c.checked) selected.push(allOps[parseInt(c.dataset.i)]); });
  if (!selected.length) return;

  const applyBtn = panel.querySelector('.chat-ops-apply');
  applyBtn.disabled = true;
  applyBtn.textContent = 'Applying…';

  let result;
  try {
    result = await api.post('/api/notes/apply', {
      note_id: null,
      operations: selected,
    });
  } catch (err) {
    applyBtn.disabled = false;
    applyBtn.textContent = 'Apply selected';
    appendErrorLine(bubble, 'Apply failed: ' + err.message);
    return;
  }

  const applied = result.applied || [];
  const skipped = result.skipped || [];

  panel.classList.add('applied');
  panel.querySelector('.chat-ops-rows').innerHTML =
    `<div style="font-size:12px;color:#172b4d;">✓ Applied ${applied.length} of ${selected.length}` +
    (skipped.length ? ` — ${skipped.length} skipped` : '') + `</div>` +
    (skipped.length ? `<ul style="font-size:12px;color:#bf2600;margin:4px 0 0 14px;">` +
       skipped.map(s => `<li>${escHtml(s.op && s.op.op || '')}: ${escHtml(s.reason || '')}</li>`).join('') +
       `</ul>` : '');
  applyBtn.remove();

  // Build the system-style note for the next outgoing user message.
  if (applied.length) {
    const summaries = applied.map(a => describeAppliedOp(a)).join('; ');
    const note = `(Applied: ${summaries})`;
    chatState.pendingApplyNote = chatState.pendingApplyNote
      ? chatState.pendingApplyNote + ' ' + note
      : note;
  }

  // Clear this turn's pending so it can't be re-applied.
  delete chatState.pendingByTurn[turnIndex];

  // Refresh affected views.
  if (typeof refreshCurrentView === 'function') refreshCurrentView();
}

function describeAppliedOp(applied) {
  // applied = {op: 'create_card', target: 'alpha/backlog/new-thing'}
  return `${applied.op} ${applied.target}`;
}
```

- [ ] **Step 3: Verify Process Notes still works**

Manual:
1. Restart server, reload.
2. Open Process Notes, paste any note, click Analyze, click Apply.
3. The op-row layout in step 2 should still render normally. (We just refactored — same UI.)

- [ ] **Step 4: Verify chat apply works**

1. Open chat, type "create a card titled X in the alpha board's backlog".
2. Model proposes `create_card`.
3. Proposed-ops panel renders inline in the assistant bubble.
4. Check the box, click "Apply selected".
5. Panel turns green, shows "✓ Applied 1 of 1".
6. Switch to the board view — card exists.
7. Back in chat, type "what did I just create?". The outgoing message should silently include the `(Applied: …)` prefix; the model should be able to confirm.

- [ ] **Step 5: Run the test suite once more**

Run: `cd D:/Claude/ATC && python -m unittest discover tests`
Expected: All tests still pass (no test changes in this task — pure UI).

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(ui): chat inline proposed-ops panel + apply flow"
```

---

## Task 11: Polish — error handling, refresh-current-view check

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Verify `refreshCurrentView` is reachable from chat scope**

Search `index.html` for `function refreshCurrentView`. Confirm it exists at module scope (not nested inside another function). If it's nested, the apply-success view refresh won't fire — extract it.

If `refreshCurrentView` does not exist by that exact name, search for the function called by `btn-notes-apply` (around the existing apply handler — search for `'/api/notes/apply'`). Whatever function it calls (e.g., `renderCurrentView`, `loadCurrentView`), use that name in `applyOpsForTurn` instead of `refreshCurrentView`. The plan's stub is defensive — `if (typeof X === 'function')` — but verify the actual symbol exists and matches.

- [ ] **Step 2: Add error styling for "AI not configured"**

The server returns 400 with `{"error": "LLM not configured"}` if the user hasn't set up the gateway. The `streamChat` function will see `r.ok === false` and throw `POST /api/chat: 400`. Improve the error in `streamChat`:

```javascript
async function streamChat(messages, onEvent) {
  const r = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify({ messages }),
  });
  if (!r.ok) {
    let detail = '';
    try {
      const body = await r.json();
      detail = body.error || '';
    } catch {}
    throw new Error(detail
      ? `${detail}`
      : `POST /api/chat: ${r.status}`);
  }
  // ... (rest unchanged)
```

- [ ] **Step 3: Test the error path**

Manual: temporarily clear the LLM auth token in the Settings modal. Send a chat message. Verify the bubble shows "LLM not configured" instead of "POST /api/chat: 400". Restore the token afterwards.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "fix(ui): chat error messages surface server detail"
```

---

## Task 12: End-to-end verification + final commit

- [ ] **Step 1: Run the full test suite**

Run: `cd D:/Claude/ATC && python -m unittest discover tests`
Expected: All tests pass. Count should be roughly 133 (existing) + 5 (Task 3) + 6 (Task 4) + 6 (Task 6) + 3 (Task 7) + 1 (Task 5) = 154.

If any fail, fix before proceeding.

- [ ] **Step 2: End-to-end smoke test (manual)**

Restart the server. Reload the browser.

1. Click 💬 Chat — sidebar appears.
2. "What boards do I have?" → tool call visible, text answer below.
3. "What's due today?" → list_due_today, text answer.
4. "Find cards with label 'urgent'" → find_by_label, list.
5. "Draft a card called 'test the chat' in alpha/backlog" → create_card queued, panel inline, click Apply, confirm green check.
6. Switch to the board view — card visible.
7. Back to chat: ask "what did I just create?" — model should mention 'test the chat' (because of the pending-apply prefix).
8. Click trash icon — confirm — chat clears.
9. Reload — sidebar still open (localStorage), chat empty.
10. Toggle off — reload — sidebar closed.

Process Notes regression check:
1. Click Process Notes, paste a note, Analyze, Apply — same as before.
2. Verify ops applied and view refreshes.

- [ ] **Step 3: Push**

If everything works:

```bash
git push
```

- [ ] **Step 4: Update memory if anything surprising came up**

If the implementation revealed something non-obvious worth remembering for future work — file structure decisions, surprising failure modes — write it to memory. Otherwise skip.

---

## Self-review notes

Reading the plan with fresh eyes:

- **Spec coverage:** All 7 spec sections are covered: shared tool extraction (Task 2), 5 new read tools (Tasks 3-4), `apply_operations(note_id=None)` (Task 5), `chat.py` (Task 6), `/api/chat` SSE (Task 7), sidebar UI + toggle + render + apply (Tasks 8-10), error handling (Task 11). Tests at every step. Out-of-scope items in the spec (token streaming, stop button, multi-thread history, browser tests) are correctly absent from the plan.
- **Placeholder scan:** No "TBD", no "implement appropriately", no "similar to". One stub function `renderOpsPanelForTurn` in Task 9 is explicitly replaced in Task 10 — that's intentional sequencing, not a placeholder.
- **Type consistency:** `chat_stream` returns `done.messages_appended` and `done.proposed_operations` consistently across the spec, the Python code, the tests, and the JS consumer. Tool names match across `READ_TOOL_DEFS`, `READ_TOOLS` dispatch, `_summarize_read_result`, and the system prompt.
- **One open verification step:** Task 11 Step 1 asks the implementer to verify `refreshCurrentView` exists by that name. I checked the codebase before writing the plan and saw it referenced in the existing Process Notes apply handler — but defensively flagged it because the worktree may have changed. Acceptable.
