# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project shape

"Flow" (internal name `active-task-coordinator` / `atc`) is a single-user local kanban app: Python stdlib HTTP server + a single static `index.html`. Cards and boards are plain markdown files on disk; the web UI and the LLM-driven flows both edit those same files. There is no database and no build step.

The shipped product is a Windows zip with `setup.bat` (creates a venv from `requirements.txt`) and `start.bat` (runs `server.py` on `:8080`). The browser opens to `http://localhost:8080`.

Runtime deps are intentionally minimal — only `anthropic` and `httpx` (see `requirements.txt`). Everything else is stdlib. Do not introduce a web framework, a frontend toolchain, or a database without strong reason.

## Common commands

```bash
# Run the full test suite (uses unittest; no pytest config exists)
python -m unittest discover tests -v

# Run a single test module / class / case
python -m unittest tests.test_server -v
python -m unittest tests.test_server.TestSlugify -v
python -m unittest tests.test_server.TestSlugify.test_simple -v

# Start the server locally (data dir is auto-resolved — see app_config.py)
python server.py

# One-shot maintenance scripts (run from project root)
python migrate.py                 # assign C-N IDs to legacy cards (refuses if data/ has uncommitted git changes)
python repair_relations.py        # dry-run scan for missing back-references
python repair_relations.py --apply  # write the fixes
```

There is no linter, formatter, or type-checker configured. Don't add one without asking.

## Data model — read `instructions.md` first

`instructions.md` is the authoritative spec for the on-disk layout: folder structure under `data/`, frontmatter fields, body sections (`## Description` / `## Checklist` / `## Comments`), `_order.json` files, and the `[[C-12]]` cross-link syntax. Treat it as ground truth; don't reinvent the format from reading code.

Key invariants the parser/serializer assume:
- Every board has exactly four lists: `ideas`, `backlog`, `in-progress`, `done`.
- Card body has the three section headers in fixed order, even when empty.
- Each list folder owns an `_order.json` with the display order of card slugs; the root has `_boards-order.json`.
- Every card has a global ID `C-N`. Slugs are filenames; IDs are stable identifiers used by the API, the LLM tools, and `[[C-N]]` links.
- `relations` is bidirectional. The server mirrors edits via `_sync_back_relations` (server.py); when editing markdown directly, you must update both sides.

## Architecture

### Server (`server.py`)
Single-file `ThreadingHTTPServer` with one `RequestHandler` and a regex router in `_route()`. All API routes live under `/api/...`; everything else is served as a static file from the project root (mainly `index.html`).

Card IDs are managed by an in-memory index (`_ID_INDEX`, `_NEXT_ID_COUNTER`) seeded by walking `data/boards/**/*.md`. Allocation, lookup, registration, and back-reference syncing all funnel through `next_id`, `resolve_id`, `register_id`, `unregister_id`, `_sync_back_relations`. If you add a code path that creates/moves/deletes cards, it must call these — otherwise the index drifts and the next process restart is the only way to recover.

`parse_frontmatter` / `serialize_frontmatter` in server.py are a hand-rolled YAML-subset (the project does not depend on PyYAML). They support inline lists, indented dicts, and the attachment list shape used by cards. Don't replace them with `yaml.safe_load` — round-tripping is exercised by `tests/test_server.py::TestFrontmatter`.

The data directory is resolved at startup by `_resolve_data_dir()` and exposed as `server.DATA_DIR`. Other modules import `server` and read `server.DATA_DIR` (and `server.LISTS`) directly — there is no separate config object. Tests temporarily reassign `server.DATA_DIR` and call `server.reset_id_index()`.

### Configs — four separate files, four separate purposes
- `app_config.py` → `~/.atc/config.json`: per-user, survives reinstalls. Holds `data_dir` and first-run UI flags.
- `sync_config.py` → `./.sync-config.json` (gitignored): git-sync mode (`off` / `local` / `remote`), remote URL, branch.
- `llm_config.py` → `./.llm-config.json` (gitignored): LLM gateway base URL, auth token, model. `get_client()` reads on every call so token rotation is immediate.
- `memory_config.py` → `~/.atc/memory-config.json`: per-user. Holds `memory_dir` (default `~/.atc/memory/` — outside `data/` on purpose, so it lives in its own git repo), lint cadence, and the pending-proposal cap.

Each config module owns a `load`/`save`/`validate`/`public_view` quartet; `public_view` masks secrets before sending to the browser. Don't merge these — keeping them split is intentional (different lifecycles, different privacy concerns).

### Memory wiki: `memory_store.py`, `memory_ops.py`, `memory_context.py`, `memory_lint.py`
A Karpathy-style LLM wiki layered into ATC. Three concentric files:
- `memory_store.py` — the only module that touches the memory directory. Owns layout (`INDEX.md` + `README.md` + `log.md`, top-level wiki `*.md` pages, `_sources/` immutable raw inputs, `_proposals/` pending edit batches), slug safety, log rotation at 1000 lines, and `memory_fingerprint()` for the lint skip-gate.
- `memory_context.py` — `load_memory_context()` returns Anthropic SDK blocks (README + INDEX + as many wiki pages as fit a 200-line budget, each cached separately). The first page in INDEX order is always loaded even when it busts the budget. `read_memory_page` tool exposes overflow pages on demand. Wired into `chat.py`, `notes.py`, and `briefing.py` so every agentic flow sees the wiki.
- `memory_ops.py` — apply-side for memory write ops. `save_as_source` and `propose_memory_edits` are queued through `chat_tools._queue_op` just like card ops; on `/api/memory/apply` they route here. `propose_memory_edits` never touches a wiki page — it writes a single `_proposals/*.md` for the user to review. Actual page writes happen via `apply_proposal(proposal_id, accepted_indices)` from the proposal modal.
- `memory_lint.py` — periodic (and manual) lint pass. Skip-gates: `lint_enabled=false`, `>= max_pending_proposals`, or fingerprint unchanged since last run. Emits at most one `*-lint.md` proposal per run. A background thread in `server.py` startup calls `run_lint()` on `lint_interval_hours` cadence (default 1h).

Wiki invariants: pages refer to people/projects/stakeholders by **name only** — never `[[C-N]]` card references (cards are ephemeral, memory is not). Cross-link wiki pages with `[[page-name]]`. Reserved files (`README.md`, `INDEX.md`, `log.md`) are excluded from `list_pages()`. All LLM-driven writes flow through the proposal UI; hand-edits in your editor are allowed and the LLM re-reads on next operation.

### LLM flows: `notes.py`, `chat.py`, `chat_tools.py`
Two LLM-driven entry points share one tool kit:
- `notes.analyze_stream` — "Process Notes" button. Uses a `finish` tool to end the loop and emits SSE events.
- `chat.chat_stream` — sidebar chat. No `finish` tool; loop ends when the model returns text-only.

Both use the Anthropic SDK with the **read tools execute immediately, write tools queue** pattern (`chat_tools._queue_op`). The user reviews queued ops before they apply via `/api/notes/apply`. When refining proposals, the model must re-emit the *complete corrected set* — there is no edit/delete tool.

Read-tool results are cached per-batch via a thread-local (`chat_tools._cache`) because `ThreadingHTTPServer` runs requests concurrently — a module-level cache would leak across requests. Always call `reset_read_cache()` at the top of each model turn.

### `data_repo.py` + janitor
`data_repo.py` is the only module that runs `git` against `data/`. `server.py` calls `data_repo.set_data_dir(DATA_DIR)` at import time so this module stays decoupled from the data-dir resolution logic. Sync behavior is mode-aware (`off`/`local`/`remote`); `transition_sets_skip_pull` in `sync_config.py` documents when an auto-pull is suppressed.

`janitor.py` runs once at startup and then every 24h on a daemon thread. It archives `done` cards older than 14 days into `data/_archive/<board>/<YYYY-MM>/` (along with their referenced notes) and deletes orphan notes. When archiving, it strips back-references on live cards via `_sync_back_relations` — so "archived" effectively equals "removed from the live graph".

### Frontend
`index.html` is a single ~170KB file (vanilla HTML/JS/CSS, no bundler). It calls the same `/api/...` endpoints used by tests. `[[C-N]]` rendering, the typeahead picker, and the dashboard buckets (`bucket_cards_by_due` in server.py) are server-driven.

## Testing notes

- Tests use `unittest`, not pytest. Run with `python -m unittest discover tests`.
- `tests/_llm_fakes.py` provides `FakeClient` / `FakeResponse` / `text_block` / `tool_use` for scripting Anthropic SDK responses without network. Use these for any new LLM-related tests.
- HTTP-level tests in `tests/test_server.py` boot a real `HTTPServer` on a random port (see `make_request_port`) — don't try to mock the handler; let it hit the loopback.
- Tests that touch the on-disk layout swap `server.DATA_DIR` to a tempdir and call `server.reset_id_index()` on setUp.

## Conventions worth knowing

- **No silent introduction of dependencies.** Stay on stdlib + the two listed in `requirements.txt`.
- **Frontmatter parser is bespoke.** Extending the schema means extending `parse_frontmatter`/`serialize_frontmatter` and adding a round-trip test.
- **Card IDs are forever.** Migrations rename files (slugs) but never reuse IDs. Deleting a card frees its position in `_order.json`, not its ID.
- **Relations are bidirectional, mirrored server-side.** Direct markdown edits must update both ends; API edits do it automatically.
- **Dates are ISO `YYYY-MM-DD` strings everywhere** (frontmatter `due`/`created`/`updated`, archive bucket folders, dashboard buckets).
- **Release zips are built from `release/build_release.ps1`** (PowerShell, run on Windows) — that script's `$Excludes` list is the source of truth for what ships to end-users.
