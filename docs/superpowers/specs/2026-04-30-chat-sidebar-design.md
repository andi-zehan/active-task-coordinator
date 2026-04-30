# Chat sidebar — design

## Goal

Add a second AI surface to the app: a chat sidebar where the user can ask
questions about their boards/cards and request changes via natural language.
Process Notes stays as a distinct, specialized flow; the chat is everything
else.

## Decisions (from brainstorming)

| Question | Choice |
|---|---|
| Buttons vs chat | **Distinct flows.** Process Notes keeps its wizard. Chat is a separate surface. Tools are shared. |
| Write capability | **Queue-and-confirm.** Chat has the same 6 write tools as Process Notes; queued ops render inline for confirm-then-apply. |
| Conversation persistence | **Ephemeral.** Single in-memory thread per page-load. No server-side history. |
| How queued ops surface | **Inline per-message.** Each assistant turn that queued ops gets its own "Proposed changes" panel directly under the bubble. |
| Layout | **Toggleable sidebar** (`#chat-toggle` in header). 380px panel, fixed right, persisted in `localStorage`. |
| Tool surface | 4 existing read tools + 6 write tools + 5 new bucket/filter reads. No `get_card_history` for now. |
| Token streaming | **Deferred.** Stream events (turn / tool / queued / text-block / done) but not text deltas. |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Browser                                                     │
│  ┌──────────────────────┐    ┌───────────────────────────┐  │
│  │ Existing views       │    │ #chat-sidebar             │  │
│  │ (board/dashboard/…)  │    │  ┌─────────────────────┐  │  │
│  │  + body.chat-open    │◄───│  │ #chat-messages      │  │  │
│  │    padding-right:    │    │  │ user / assistant /  │  │  │
│  │    380px             │    │  │ tool-line / ops-    │  │  │
│  │                      │    │  │ panel               │  │  │
│  │                      │    │  └─────────────────────┘  │  │
│  │                      │    │  ┌─────────────────────┐  │  │
│  │                      │    │  │ #chat-input + Send  │  │  │
│  │                      │    │  └─────────────────────┘  │  │
│  └──────────────────────┘    └───────────────────────────┘  │
│                                                             │
│  chatState = { messages: [], pendingByTurn: {} }            │
└─────────────────┬───────────────────────────────────────────┘
                  │ POST /api/chat (SSE)
                  │ POST /api/notes/apply (existing)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Python server (server.py)                                   │
│  _handle_chat → chat.chat_stream(messages, model, client)   │
│                                                             │
│  notes.py ──┐                                               │
│             ├─► chat_tools.py (shared tool defs + impls)    │
│  chat.py  ──┘                                               │
│                                                             │
│  Existing: notes.apply_operations, server.read_card, …      │
└─────────────────────────────────────────────────────────────┘
```

## Components

### `chat_tools.py` (new, ~250 lines)

Shared building blocks. Both `notes.py` and `chat.py` import from here.

**Helpers (moved from `notes.py`):**
- `_WRITE_OP_NAMES`
- `_queue_op(name, args, queue) → dict`
- `_summarize_read_result(name, args, payload) → str`
- `_queued_summary_fields(name, args) → dict`

**Read-tool implementations (moved from `notes.py`):**
- `_tool_list_boards`
- `_tool_list_cards`
- `_tool_search_cards`
- `_tool_read_card`

**New read-tool implementations:**
- `_tool_list_overdue()` — wraps `_handle_dashboard` bucketing logic, returns the `overdue` bucket only. Excludes cards in the `done` list (matches dashboard behavior).
- `_tool_list_due_today()` — same shape, today bucket.
- `_tool_list_due_this_week()` — same shape, this_week bucket.
- `_tool_find_by_label(label)` — case-insensitive exact match against each card's `labels` array.
- `_tool_find_by_assignee(name)` — case-insensitive exact match against each card's `assignee` field.

**Tool definitions:**
- `READ_TOOL_DEFS` — JSON-schema list for the 4 existing + 5 new read tools.
- `WRITE_TOOL_DEFS` — JSON-schema list for the 6 write tools (same as today's notes write tools).

**Public dispatch tables:**
- `READ_TOOLS = {name: callable, ...}` — used by both consumers.

### `chat.py` (new, ~150 lines)

```python
SYSTEM_PROMPT = """You are an assistant inside a personal kanban app.
You can answer questions about the user's boards and cards, and you
can propose changes (create cards, add comments, tick checklist items,
move cards, update fields).

WRITE TOOLS DO NOT EXECUTE. They queue a proposed operation that the
user must confirm before it is applied. When you queue ops, briefly
explain what you proposed and why so the user can decide.

Use read tools liberally to ground your answers. Prefer:
- list_overdue / list_due_today / list_due_this_week for time questions
- find_by_label / find_by_assignee for filter questions
- search_cards for fuzzy title lookup
- read_card when you need a card's body, checklist, or comments

When you have answered the user, just stop calling tools. The
conversation continues; you do not need a 'finish' tool.
"""

CHAT_TOOLS = READ_TOOL_DEFS + WRITE_TOOL_DEFS

def chat_stream(messages, *, model, client, max_turns=16):
    """Yield events as the model processes the conversation.

    Event shapes (all dicts have a 'type' key):
      {"type": "started"}
      {"type": "turn",   "n": int}
      {"type": "tool",   "name": str, "args": dict}
      {"type": "result", "name": str, "summary": str}
      {"type": "queued", "op": str, "title"|"text"|...: ...}
      {"type": "text",   "text": str}              # an assistant text block
      {"type": "done",   "messages_appended": [...assistant blocks...],
                         "proposed_operations": [...]}
      {"type": "error",  "message": str}
    """
```

The loop terminates when the model returns a turn with no `tool_use` blocks.
That's the natural "I'm done" signal in chat (unlike notes, which uses an
explicit `finish` tool because it has nothing to "say back").

### `notes.py` (modified)

- Removes the moved helpers, imports them from `chat_tools` instead.
- Keeps its own `TOOLS` (includes `finish`), `SYSTEM_PROMPT` (notes-flavored),
  `analyze_stream`, `analyze`, `apply_operations`, `archive_note`, `read_note`.
- Public API unchanged. Existing tests should pass with at most a couple of
  import updates if any test referenced moved internals directly.

### `server.py` (modified, +~40 lines)

- New endpoint `POST /api/chat` → `_handle_chat()`.
- Reuses the SSE-emit pattern from `_handle_notes_analyze`:
  - `Content-Type: text/event-stream; charset=utf-8`
  - `Connection: close`
  - One `data: {json}\n\n` block per event.
- Request shape:
  ```json
  { "messages": [ {role, content}, ... ] }
  ```
- The browser is responsible for trimming history (out of scope for v1; keep
  full history until something breaks).
- `apply_operations(operations, note_id)` becomes tolerant of `note_id=None`:
  when `None`, skip the source-note attachment on `_do_create_card` and skip
  the `_record_in_note` call. This is the ~5-line change that lets chat reuse
  the existing apply endpoint.

### `index.html` (modified, +~250 lines)

**Markup:**
- New `#chat-sidebar` div: fixed right, full height, 380px wide, hidden by
  default. Contains `#chat-messages` (flex:1, scrolling) and `#chat-input`
  + `#btn-chat-send`.
- New `#btn-chat-toggle` button in the existing header next to "Process Notes".

**State:**
```js
const chatState = {
  messages: [],          // Anthropic message history
  pendingByTurn: {},     // turnIndex → array of unapplied ops
};
```

**Wiring:**
- `#btn-chat-toggle` toggles `body.classList` for `chat-open`, persists in
  `localStorage.atc-chat-open`. Restored on page load.
- `body.chat-open` adds `padding-right: 380px`. All views reflow naturally;
  existing dashboard/board breakpoints already handle narrower widths.

**Streaming reader:** mirrors `streamNotesAnalyze` from the Process Notes UI.
Reuses the same SSE parsing pattern (`fetch` + `body.getReader()` + manual
`data:` parsing).

**Rendering per event:**
- `started` / `turn(n)` — update a small status pill on the latest assistant
  bubble; no separate line in the log.
- `tool` → render a collapsed `🔍 search_cards "pricing"` line inside the
  bubble. Clickable to expand args.
- `result` → append `→ <summary>` to the matching tool line.
- `queued` → push op onto `pendingByTurn[currentTurnIndex]`, refresh the
  proposed-ops panel for that turn.
- `text` → append the text block to the bubble's prose area.
- `done` → seal the bubble, append normalized assistant blocks to
  `chatState.messages`, re-enable input.
- `error` → render an error line in the bubble; **do not** commit the assistant
  message to `chatState.messages` (so the next user message doesn't re-send a
  half-broken turn). Re-enable input.

**Proposed-ops panel:** extracted from the Process Notes step-2 renderer into
a shared `renderOpsPanel(ops, container, onApply)` function. Same checkbox
+ confidence + summary + reason layout. After Apply, panel collapses to a
one-line summary "✓ Applied 3 of 5".

**Apply flow:**
1. POST to existing `/api/notes/apply` with `{note_id: null, operations: [...]}`.
   The chat sidebar does NOT call `archive_note` — there's no note body to
   archive. `note_id=null` is the signal to `apply_operations` to skip both
   the source-note attachment on created cards and the `_record_in_note`
   frontmatter update.
2. On success, panel collapses. A pending-prefix string is stored on
   `chatState.pendingApplyNote`:
   ```
   (Applied: created card 'New thing' in alpha/backlog;
    added comment to beta/spec)
   ```
   On the user's next send, the prefix is prepended to their typed text
   (separated by a blank line) and `pendingApplyNote` is cleared. The user
   sees their typed text in the chat log, not the prefix — only the API
   call sees the combined string. This way the model learns the state
   changed without polluting the visible transcript.

   If multiple panels are applied between user messages, the prefixes
   concatenate.

   If the user reloads the page before sending, the pending prefix is lost.
   That's acceptable — ephemeral chat already loses everything on reload.
3. On per-op failure (existing apply returns `{applied, skipped}`), failed
   ops are highlighted red in the panel with the skip reason; successful
   ops collapse. The applied portion still contributes to the pending prefix.

## Data flow — sending a message

1. User types in `#chat-input`, presses Enter.
2. `chatState.messages.push({role: "user", content: text})`.
3. POST `{messages}` → `/api/chat`, open SSE reader.
4. Server validates LLM is configured (else 400 JSON error, browser shows
   "AI not configured").
5. Server runs `chat_stream`, emits events.
6. Browser handles events as described above.
7. On `done`, browser commits the assistant turn to history, re-enables input.

## Error handling

- **LLM not configured:** server responds 400 JSON before opening the stream;
  browser shows "AI not configured — check Settings".
- **Network drop mid-stream:** browser shows "Connection lost", drops the
  half-formed assistant message, re-enables input.
- **Tool dispatch error inside the loop:** caught in `chat_stream`, emitted
  as `result` with `summary: "error: ..."`; the model sees the error in its
  `tool_result` content and can recover.
- **Apply fails:** per-op failures shown inline in the panel; successful
  ops are still applied.
- **max_turns reached:** loop exits, `done` still emitted; browser shows
  banner "⚠ Stopped after 16 turns".

## Out of scope for v1

- Stop/cancel button (cancellation = real work; defer until needed).
- Token-by-token streaming (we stream blocks, not tokens — see brainstorm
  rationale: tool events already give immediate feedback; word-by-word
  prose growth is small marginal gain at meaningful added complexity).
- Conversation export, search, threading, multi-thread history.
- `get_card_history` tool.
- Automated browser tests.

## Testing

### `tests/test_chat_tools.py` (new, ~6 tests)

- `test_list_overdue_returns_overdue_excludes_done` — past-due card in
  `done` does not appear; past-due card in `in-progress` does.
- `test_list_due_today` — today returns; tomorrow does not.
- `test_list_due_this_week` — Friday returns; next Monday does not. Edge:
  if today is Friday, today's cards stay in `today`, not `this_week`
  (mirror dashboard logic).
- `test_find_by_label_exact_match_case_insensitive` — `"URGENT"` matches
  `"urgent"`; `"urge"` does not.
- `test_find_by_assignee_case_insensitive`.
- `test_moved_helpers_unchanged` — sanity check that `_queue_op` and
  `_summarize_read_result` behave identically after the move.

### `tests/test_chat.py` (new, ~5 tests)

Uses the existing `FakeClient` / `FakeResponse` scaffolding from
`tests/test_notes.py` (extracted into a small `tests/_llm_fakes.py` shared
helper).

- `test_chat_stream_text_only_terminates` — model returns one turn with no
  tool_use, just text → loop ends, `done` fires.
- `test_chat_stream_tool_then_text` — turn 1 read_card, turn 2 text. Verify
  event sequence: `started → turn(1) → tool → result → turn(2) → text → done`.
- `test_chat_stream_queued_write_op` — model calls `create_card`, then text.
  `queued` fires; `done.proposed_operations` contains the op.
- `test_chat_stream_max_turns` — model loops with tool calls, hits cap,
  `done` still emitted with whatever was generated.
- `test_chat_stream_tool_error_recovers` — read tool raises ValueError →
  `result` with `summary: "error: ..."`; model can continue.

### `tests/test_server.py` (additions)

- `TestChatEndpoint.test_chat_streams_sse` — `Content-Type: text/event-stream`,
  parseable event blocks.
- `TestChatEndpoint.test_chat_returns_done_event` — full happy path through
  the SSE plumbing using a scripted FakeClient.

### `tests/test_notes.py` and `tests/test_server.py` regression

Both should pass unchanged. If the `chat_tools` extraction breaks a few
test imports that referenced `notes._tool_list_boards` etc. directly, the
fix is a one-line module change per import.

## Implementation order (rough)

1. Extract shared helpers from `notes.py` into `chat_tools.py`. Run all
   existing tests; nothing should change.
2. Add the 5 new read tools in `chat_tools.py` + their tests.
3. Make `notes.apply_operations` tolerant of `note_id=None`.
4. Build `chat.py` (`SYSTEM_PROMPT`, `CHAT_TOOLS`, `chat_stream`) + tests.
5. Add `/api/chat` endpoint + tests.
6. Build the sidebar UI (markup, CSS, toggle, streaming reader).
7. Wire the per-turn proposed-ops panel (extract `renderOpsPanel` from
   the Process Notes code; reuse from both surfaces).
8. Manual smoke test end-to-end.
