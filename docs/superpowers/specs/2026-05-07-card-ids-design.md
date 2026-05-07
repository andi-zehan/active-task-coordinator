# Card IDs — Design

Date: 2026-05-07

## Problem

Cards are addressed by `(board, list, slug)` triples derived from titles. This causes three concrete problems:

1. **No way to point at a card.** Inside a description or comment, the user can't link to another card. The `relations:` frontmatter field stores path triples, but those triples break when a card is renamed or moved.
2. **The agent can't rename cards.** `update_field` (chat_tools.py:224) only handles `due | assignee | labels`. There's no rename tool, and even if added, renaming a card today changes its slug, which breaks every reference to it.
3. **Slugs are ambiguous targets for the agent.** The chat agent has to guess slugs from titles via fuzzy search, then pass `(board, list, slug)` to every write tool. Mismatches happen.

## Goal

Give every card a stable, human-friendly identifier that survives renames and list moves, and expose it as the canonical reference everywhere — agent tools, body-text links, and the relations field.

## Decisions

### ID format

Global numeric, prefix `C-`. Examples: `C-1`, `C-12`, `C-43`. One counter across all boards.

Per-board prefixes (Jira-style `DAI-12`) were considered and rejected — the user prefers a single namespace.

### Filename

`<id>.md`. Title-derived slugs are dropped entirely.

```
data/boards/dai-orga/ideas/
  C-1.md
  C-2.md
  C-7.md
```

`_order.json` per list now contains IDs:

```json
["C-12", "C-7", "C-3"]
```

### Frontmatter

New required field `id:`. Existing fields unchanged.

```yaml
---
id: C-12
title: Demo of DFMEA
assignee: Andi
labels: []
due: 2026-05-22
created: 2026-04-24
updated: 2026-05-04
relations: [C-2, C-7]
custom_fields:
attachments: []
---
```

### Counter

Derived from `max(existing id) + 1` at server startup, held in memory under a lock. Single-writer assumption — no cross-machine collision handling. If a duplicate ID ever appears (manual edit, restored backup), lookup picks one arbitrarily; not addressed in this design.

### Link syntax in body text

`[[C-12]]` only. No bare `C-12` auto-linking — avoids false matches in URLs and code samples.

Rendered as a clickable chip showing `C-12: <title>`. Clicking opens the card detail modal. Unknown IDs render as `[[C-99 ?]]` with muted styling.

When the user types `[[` in description or comment editors, a typeahead popup shows matching `C-N — title` suggestions, reusing the relation typeahead infrastructure (index.html:3455).

### Relations field

`relations:` stores IDs only:

```yaml
relations: [C-2, C-7]
```

Renders the same chip style as today, looked up via the in-memory ID index. Bidirectional invariant from instructions.md:171 still applies — explicit on both sides. No automatic backlink derivation in this scope.

### Agent tool surface

Write tools targeting an existing card switch from `(board, list, card)` to a single `id`:

| Tool | Old args | New args |
|---|---|---|
| `add_comment` | `(board, list, card, text, ...)` | `(id, text, ...)` |
| `tick_checklist` | `(board, list, card, item, ...)` | `(id, item, ...)` |
| `add_checklist_item` | `(board, list, card, item, ...)` | `(id, item, ...)` |
| `move_card` | `(board, list, card, target_list, ...)` | `(id, target_list, ...)` |
| `update_field` | `(board, list, card, field, value, ...)` | `(id, field, value, ...)` |
| `rename_card` *(new)* | — | `(id, title, ...)` |
| `create_card` | `(board, list, title, ...)` | unchanged — server assigns ID |

New read tool: `get_card_by_id({id})` returns the same shape as `read_card`.

A server-side helper `resolve_id(id) → (board, list)` runs first in every write-tool implementation. Unknown ID → `{"error": "unknown id: C-99"}`, op not queued.

`_queued_summary_fields` (chat_tools.py:484) includes `id` and the resolved `title`, so proposed-ops UI shows "Add comment to **C-12: Demo of DFMEA**" instead of a slug.

### UI display

ID shown in:

- Card tile on the board view (small chip).
- Card detail modal header, next to the title. Click the chip to copy `[[C-12]]` to clipboard.
- Search results: `C-12 — Demo of DFMEA  (dai-orga/ideas)`.
- Wikilink rendering (above).

### Migration

Manual command — `POST /api/migrate/assign-ids`, triggered by an admin button in the sync settings area. Idempotent.

Per board, walking lists in `LISTS` order and cards in `_order.json` order:

1. For cards without `id:`, allocate the next ID, write it to frontmatter.
2. Rename the file from `<old-slug>.md` to `<id>.md`.
3. Rewrite that list's `_order.json` to use IDs.

After all renames, a second pass builds a `board/list/old-slug → C-N` lookup and rewrites every `relations:` entry. Unresolved entries are left in place and reported.

Body text is **not** scanned for path-shaped strings — existing bodies have no card references, so there's nothing to convert. New `[[C-N]]` links are written going forward.

Refuses to run if `git status` shows uncommitted changes in `data/`. Leaves the migration changes uncommitted so the user can review the diff before committing.

Response shape:

```json
{
  "boards": 5,
  "cards_migrated": 42,
  "relations_converted": 11,
  "relations_unresolved": [{"card": "C-3", "stale": "old/path/foo"}],
  "duration_ms": 187
}
```

## Components touched

| File | Change |
|---|---|
| `server.py` | Read/write card by ID; new ID index; `rename_card` handler; migration endpoint; counter init at startup. |
| `chat_tools.py` | Update tool defs to use `id`; add `rename_card` and `get_card_by_id`; resolver helper; updated queued-summary fields. |
| `notes.py` | Same write-tool surface change (shared via chat_tools.py). |
| `index.html` | Wikilink rendering in `linkifyHtml` (index.html:1875); `[[` typeahead; ID chip on tile + modal header + search results; relations panel uses ID lookup; admin button for migration. |
| `tests/test_server.py`, `tests/test_chat_tools.py` | Coverage per the testing section below. |
| `instructions.md` | Update `relations:` docs to use IDs. |

## Testing

**Server / agent tools** (extend existing test files):

- Card creation assigns sequential IDs (C-1, C-2, …) and writes file as `C-N.md`.
- Rename endpoint changes title; leaves ID and filename intact.
- `move_card` keeps the same ID across lists; `_order.json` updates with the ID.
- Delete does not reuse IDs — next create still goes to max+1.
- Wikilink resolver: known ID → title; unknown ID → null/error.
- Migration endpoint: idempotent on a second run; converts path relations to ID relations; reports unresolved entries; refuses on dirty `data/`.
- ID-targeted write tools return `{"error": "unknown id: C-99"}` and don't queue when the ID is bogus.
- `rename_card` queues with `id` and resolved `title` in summary fields.

**Frontend**: no automated tests in this codebase. Verified manually:

- Open a card with `[[C-2]]` in its description; confirm the chip renders with the target's title and click opens the modal.
- Type `[[` in the description editor; confirm typeahead lists matching cards.
- Confirm tile chip and modal-header chip show the ID; modal-header click copies `[[C-N]]` to clipboard.

## Out of scope

- Cross-machine collision detection / renumbering. Single-writer assumption.
- Per-board ID prefixes.
- Automatic backlinks panel derived from `[[...]]` mentions.
- Cross-board card moves via the agent.
- Rewriting body text during migration.
