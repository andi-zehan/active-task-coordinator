---
date: 2026-04-27
topic: Notes-to-Cards Agent
status: design
---

# Notes-to-Cards Agent — Design

## Goal

Let the user paste or type a meeting note (Copilot summary, raw transcript, free-form notes) into the ATC kanban UI and have an LLM-powered agent propose card operations across all boards. The user reviews and approves a list of operations before anything is written. The original note is archived locally and linked back from every card and comment the agent creates.

## Non-goals

- No automatic file-watcher or background daemon.
- No processing of files on disk; input is text typed or pasted into the UI only.
- No modification of the existing `meeting-notes` / `process-inbox` / `kanban` skills used in Claude Code sessions. Those continue to work independently.
- No automatic git sync after apply. The existing dirty indicator and manual sync button remain the user's control.
- Notes are NOT synced to the data repo — they stay on the local machine only.

## User flow

1. User clicks **📝 Process Notes** in the kanban header.
2. Modal opens with a large textarea and an optional title field.
3. User pastes the note, clicks **Analyze**.
4. Server archives the note to `data/notes/YYYY-MM-DD-<slug>.md`, snapshots all boards, calls the LLM, returns a structured list of proposed operations.
5. UI renders the preview: per-op checkbox, type icon, target, summary, confidence badge, reason, inline edit controls. Defaults: `high`/`med` checked, `low` unchecked.
6. User edits / unchecks as needed, clicks **Apply N selected**.
7. Server executes the selected operations using existing card read/write helpers, returns a result summary.
8. Result modal shows successes, skips (with reason), and a link to the archived note. Optional "Sync now" shortcut calls existing `/api/sync/push`.

## Architecture

```
┌──────────── Web UI (index.html) ─────────────┐
│  Header: [⚙ Settings]  [📝 Process Notes]    │
│       │                       │              │
│       ▼                       ▼              │
│  Settings modal          Notes modal         │
│  (token/base/model)      (3-step wizard)     │
└───────────────────┬───────────────────────────┘
                    │ HTTP
┌───────────────────▼───────────────────────────┐
│  server.py                                     │
│  GET  /api/llm-config       (no token leak)   │
│  PUT  /api/llm-config                          │
│  POST /api/llm-config/test                     │
│  POST /api/notes/analyze                       │
│  POST /api/notes/apply                         │
│  GET  /api/notes/:id                           │
│                                                │
│  notes.py (new module)                         │
│   • build_snapshot()                           │
│   • call_llm(snapshot, note, today)            │
│   • apply_operations(ops, note_id)             │
│   • archive_note(text, title) -> note_id       │
└───────────────────┬───────────────────────────┘
                    │ anthropic SDK
                    ▼
              Claude (corp gateway)
```

## Components

### LLM settings (`./.llm-config.json`)

Stored at the **ATC project root**, NOT under `data/`. Added to `.gitignore` of the ATC repo so the token never ends up in git or in the synced data repo.

```json
{
  "base_url": "https://llm-gateway.ve42034x.automotive-wan.com",
  "auth_token": "gAAAA...",
  "model": "claude-opus-4-7",
  "tls_verify": false
}
```

Endpoints:

- `GET /api/llm-config` — returns config with `auth_token` masked (e.g. `"****...XYZ"`) and a `configured: true|false` boolean.
- `PUT /api/llm-config` — accepts the four fields; missing `auth_token` keeps the existing one (so the user doesn't have to retype on edits to other fields).
- `POST /api/llm-config/test` — sends a 1-token call (`max_tokens: 1`, single-word user message), returns `{ok: true}` or `{ok: false, error: "..."}`.

The Anthropic SDK is initialized **fresh on each LLM call** by re-reading this file. Token rotation is immediate; no server restart.

When the config is missing or `auth_token` is empty, the **Process Notes** button is disabled with a tooltip "Configure API token in Settings".

### Note archive (`notes/`)

- Lives at the **ATC project root** (`./notes/`), NOT under `data/`. Added to the ATC `.gitignore` and never pushed to the data repo. Local-only by design — meeting notes can contain sensitive content.
- Filename: `notes/YYYY-MM-DD-<slug>.md`. Slug derived from the optional title (default: `untitled-HHMMSS`). Collisions get a `-2`, `-3` suffix.
- File format:

```markdown
---
date: 2026-04-27
title: Q2 Planning Meeting
applied_ops:
  - { op: create_card, target: dai-projects/backlog/draft-api-spec, at: "2026-04-27T14:32:01" }
  - { op: add_comment, target: maia/in-progress/vendor-eval, at: "2026-04-27T14:32:01" }
---

[full pasted text, unmodified]
```

- `applied_ops` is appended to on every successful Apply call (a single note can be re-applied if the user reopens the result and runs more ops later — out of scope for v1 but the format supports it).
- Served by `GET /api/notes/:id` as `text/markdown`. The id is the filename without `.md`.
- Because notes live outside `data/`, writing them does NOT trigger the git dirty indicator.

### Operation schema

Operations the LLM may propose:

| op                  | required fields                                                         |
|---------------------|-------------------------------------------------------------------------|
| `create_card`       | `board`, `list`, `title`, `description?`, `checklist?`, `due?`, `assignee?`, `labels?` |
| `add_comment`       | `board`, `list`, `card`, `text`                                          |
| `tick_checklist`    | `board`, `list`, `card`, `item` (substring match against existing items) |
| `add_checklist_item`| `board`, `list`, `card`, `item`                                          |
| `move_card`         | `board`, `list`, `card`, `target_list`                                   |
| `update_field`      | `board`, `list`, `card`, `field` (one of `due`/`assignee`/`labels`), `value` |

Every operation also has:

- `confidence`: `"high" | "med" | "low"`
- `reason`: short string explaining why the LLM proposed it.

`source_note_id` is added by the server (not the LLM) when applying.

### LLM call

A single Anthropic `messages.create` call with **prompt caching**:

1. *System prompt (cache_control: ephemeral)*: schema description, rules, output format. Static across all calls.
2. *User content block 1 (cache_control: ephemeral)*: compact board snapshot.
3. *User content block 2 (no cache)*: today's date, note id, pasted text.

Snapshot per card:

```json
{ "b": "dai-projects", "l": "backlog", "s": "draft-api-spec",
  "title": "...", "labels": ["api"], "due": "2026-05-01",
  "assignee": "Me", "todo": ["..."], "done": ["..."],
  "desc": "first 200 chars..." }
```

No comments, no attachments — keeps the snapshot small and the cache effective.

Output is a single JSON object:

```json
{
  "summary": "...",
  "operations": [ {...}, {...} ]
}
```

Strict JSON via the SDK's response handling. If parsing fails, the server returns a 502 with the raw response so the user can see what happened.

### API endpoints

```
GET    /api/llm-config              -> {configured, base_url, model, tls_verify, auth_token: "****"}
PUT    /api/llm-config              -> {ok: true}
POST   /api/llm-config/test         -> {ok: true} | {ok: false, error}

POST   /api/notes/analyze
       body: {text, title?}
       -> {note_id, summary, operations: [...]}

POST   /api/notes/apply
       body: {note_id, operations: [...]}   (only the ops the user wants)
       -> {applied: [...], skipped: [{op, reason}]}

GET    /api/notes/:id               -> raw markdown (text/markdown)
```

### Conflict handling on apply

For each operation, before executing:

- If target board/list/card no longer exists → skip with `reason: "target missing"`.
- If `tick_checklist` item not found in checklist → skip with `reason: "checklist item not found"`.
- If `move_card` target list doesn't exist → skip.

Other operations in the batch still execute. The result lists each skip individually.

### Source linking

- **Created cards**: `attachments: [{name: "Source note: <title>", url: "/api/notes/<note_id>"}]`.
- **Added comments**: trailing line `_(from [<title>](/api/notes/<note_id>))_`.
- **Other ops** (tick, move, update): no inline link; the audit trail lives in the note's `applied_ops` frontmatter.

### Sync behavior

- Apply writes files under `data/boards/` → existing `git status --porcelain` flips dirty → existing UI indicator lights up.
- Writes under `notes/` are local-only and do not affect sync state.
- The result modal shows: `Data is now out of sync.` with a `[Sync now]` button that POSTs to `/api/sync/push` when `data/boards/` was touched. No automatic push.

### Retention / cleanup

A periodic janitor runs to keep things tidy. Two rules:

1. **Done cards**: any card in the `done` list whose `updated` date is more than 14 days in the past is deleted (file removed, slug pulled from that list's `_order.json`).
2. **Orphan notes**: a note in `notes/` is deleted when no card across any board still references it via `attachments[].url == /api/notes/<note_id>`. This naturally happens when all cards linked to a note have been deleted (typically after the 14-day done sweep).

Implementation:

- New module `janitor.py` with `sweep_done_cards()` and `sweep_orphan_notes()`.
- Triggered by:
  - Server startup (one pass).
  - A new endpoint `POST /api/janitor/run` (manual trigger from the UI for debugging / on-demand).
  - A simple in-process timer that re-runs every 24 hours while the server is up.
- Each sweep logs to stdout: `janitor: deleted N done cards, M orphan notes`.
- The sweep is **conservative**:
  - A done card is deleted only if `updated` parses cleanly AND is more than 14 days old. Cards without a parseable `updated` are skipped with a warning.
  - A note is deleted only if its filename (the `note_id`) appears in NO card attachment URL across the entire `data/boards/` tree.
- Comments inside cards that link to a note (the `_(from [title](/api/notes/<id>))_` line) do **not** count as a reference for orphan detection — only `attachments[].url` does. This is intentional: if the only thing left pointing at a note is a comment on a soon-to-be-deleted done card, the note can be cleaned up too.

## UI changes (`index.html`)

Two new header buttons: **⚙ Settings** and **📝 Process Notes**. Both open modals.

### Settings modal

- Password-masked input for token (placeholder shows `****...XYZ` when one is saved; empty input means "keep existing").
- Text input for base URL.
- Dropdown for model: `claude-opus-4-7` (default), `claude-sonnet-4-6`, `claude-haiku-4-5`.
- Checkbox for "Disable TLS verification" (default checked, since the corp gateway needs it).
- **Test connection** button. Disabled save button until at least the token is configured once.

### Notes modal — Step 1 (Paste)

Large textarea (~25 rows, monospace), optional title input, **Cancel** / **Analyze** buttons. Analyze shows a spinner; the call may take 10–30 s for a busy board snapshot.

### Notes modal — Step 2 (Preview)

Top: archived note id + summary line.
List of operations, each row:

- Checkbox (default checked for high/med, unchecked for low)
- Type icon (➕ create, 💬 comment, ✅ tick, ➕✓ add-item, ➡ move, ✏ update)
- Target (`board / list / card`)
- One-line summary of the op
- Confidence badge (color-coded)
- Reason (smaller text)
- **Edit** button → inline form to tweak fields before apply

Bottom: **← Back** / **Apply N selected**.

### Notes modal — Step 3 (Result)

- Counts of applied / skipped per op type.
- List of skipped ops with their skip reason.
- Link to the archived note.
- Optional **Sync now** button.

## Files added / modified

**New:**
- `notes.py` — snapshot, LLM call, apply, archive helpers.
- `janitor.py` — done-card and orphan-note cleanup.
- `notes/` directory at project root (created on first analyze; gitignored).
- `./.llm-config.json` (created on first save; gitignored).

**Modified:**
- `server.py` — seven new route handlers (LLM config, notes analyze/apply/get, janitor run), import `notes` and `janitor`, kick off janitor on startup + 24h timer.
- `index.html` — two header buttons, two modals, JS for the wizard.
- `.gitignore` — add `.llm-config.json` and `notes/`.
- `requirements.txt` (or equivalent install instructions) — add `anthropic`.

## Out of scope (v1)

- File upload / drag-drop. Paste only.
- Re-running an analysis on a previously archived note.
- Editing the snapshot the LLM sees (e.g. excluding archived boards).
- Bulk import of historical notes.
- Notifications when sync is needed (relies on existing dirty indicator).
- Any change to existing skills.

## Risks

- **Token leakage**: token is stored on disk plain-text. Gitignored, but a user who copies the project folder takes the token with them. Acceptable for a personal local app; documented in the settings modal.
- **TLS verify off**: required by the corp gateway. Limited to the LLM client only, not the kanban server.
- **Snapshot size growth**: at hundreds of cards across many boards the snapshot may approach token limits. v1 acceptable; mitigation (per-board scope toggle, snapshot trimming) deferred.
- **LLM proposes wrong target card**: mitigated by preview-then-apply with confidence scoring; the user is always in the loop.
- **Janitor data loss**: a recently-completed done card the user wanted to keep is deleted after 14 days. Mitigation: `done` is for completed work; users who want to keep something move it back to another list. The 14-day window is generous and the data repo's git history preserves deleted cards.
- **Note retention surprise**: a user who manually edits a card to remove an attachment may find the note silently disappears at the next sweep. Acceptable: that's the contract.
