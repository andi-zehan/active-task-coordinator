"""Periodic memory lint pass.

Reads all wiki pages + recent log + new sources, asks the model to identify
contradictions, stale claims, orphan pages, missing cross-references, and
pages outgrowing the size budget. Emits one proposal batch via
memory_ops._do_propose_memory_edits semantics, but tagged kind='lint' so
the proposal review UI can distinguish it from chat-driven ingests.

Skip gates:
  * fingerprint unchanged since last successful lint → nothing to look at
  * len(pending proposals) >= max_pending_proposals → don't pile up
  * lint_enabled is false → user opted out

Triggered from two places:
  * /api/memory/lint manual button
  * the periodic thread spawned from server.py startup
"""
import json
import threading
from datetime import datetime
from pathlib import Path

import memory_config
import memory_context
import memory_ops
import memory_store

# In-memory mutex so manual-trigger + periodic-thread can't race a double-LLM call.
_RUN_LOCK = threading.Lock()


SYSTEM_PROMPT = """You are the memory wiki's lint pass.

Inputs you will receive in the user message:
  * WIKI PAGES — every current wiki page, named.
  * SOURCES — raw inputs the user has dropped since the last lint, named.
  * RECENT LOG — the last few hundred event log lines.

Your job: identify problems and propose corrections by calling exactly one
of two tools:

  * propose_memory_edits(summary, edits, confidence, reason) — when you have
    concrete edits to suggest (create/replace/append on specific pages).
  * finish_lint(summary) — when there is nothing meaningful to change. Use
    this freely; "nothing to do" is the common case and is not a failure.

Look for:
  * Contradictions between pages (e.g. who reports to whom differs).
  * Stale claims contradicted by newer sources.
  * Orphan pages (listed in INDEX but content is empty / has not been
    fleshed out from any source).
  * Missing cross-references (a person/topic on one page that another page
    would benefit from linking to via [[page-name]]).
  * Pages outgrowing the size budget (~200 lines) that could be split.
  * New sources that haven't been integrated into any wiki page yet —
    suggest the appropriate edit to surface their content.

Rules:
  * Wiki pages refer to people, projects, and stakeholders by NAME only.
    Never reference kanban card IDs ([[C-N]]).
  * Cross-link between wiki pages with [[page-name]] (filename without .md).
  * Be conservative: propose edits only when the source material genuinely
    supports them. Speculation belongs in the body of the proposal summary,
    not in wiki page edits.
  * When you propose edits, emit the COMPLETE new content for each affected
    page (or just the appended chunk for append). Don't emit diffs.

When done, call finish_lint(summary)."""


FINISH_LINT_TOOL_DEF = {
    "name": "finish_lint",
    "description": (
        "Call once you've decided whether to propose edits. If you found "
        "nothing to change, call this with a short summary explaining what "
        "you checked. If you proposed edits, call this AFTER propose_memory_edits."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
}


# Only one write tool exposed to lint; no card tools, no reads — everything
# lint needs is in the first user message.
LINT_TOOLS = [
    {
        "name": "propose_memory_edits",
        "description": (
            "Propose memory wiki edits. Queues a single proposal batch — does "
            "not write directly. Each edit specifies a target page (without "
            ".md), an action (create/replace/append), and the new content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "string"},
                            "action": {"type": "string", "enum": ["create", "replace", "append"]},
                            "content": {"type": "string"},
                        },
                        "required": ["page", "action", "content"],
                    },
                },
                "confidence": {"type": "string", "enum": ["high", "med", "low"]},
                "reason": {"type": "string"},
            },
            "required": ["summary", "edits", "confidence", "reason"],
        },
    },
    FINISH_LINT_TOOL_DEF,
]


# --- Skip gates ---

_LAST_LINT_FILE = ".last-lint"


def _last_lint_marker() -> Path:
    return memory_config.get_memory_dir() / _LAST_LINT_FILE


def _read_last_fingerprint() -> str:
    p = _last_lint_marker()
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_last_fingerprint(fp: str) -> None:
    p = _last_lint_marker()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fp, encoding="utf-8")


def should_run() -> tuple[bool, str]:
    """Return (run, reason). reason explains the skip when run is False."""
    cfg = memory_config.load()
    if not cfg["lint_enabled"]:
        return False, "lint_enabled=false"
    pending = len(memory_store.list_proposals())
    if pending >= cfg["max_pending_proposals"]:
        return False, f"{pending} pending proposals (cap {cfg['max_pending_proposals']})"
    fp_now = memory_store.memory_fingerprint()
    fp_prev = _read_last_fingerprint()
    if fp_now == fp_prev and fp_now != "empty":
        return False, "fingerprint unchanged since last lint"
    return True, "ok"


# --- Build the user message ---

def _build_lint_input() -> str:
    """Stringify the lint context. Single text block — no need to split."""
    parts = ["# WIKI PAGES\n"]
    for p in memory_store.list_pages():
        content = memory_store.read_page(p["name"]) or ""
        parts.append(f"\n## {p['name']}.md ({p['size_lines']} lines)\n\n{content}")
    parts.append("\n\n# SOURCES\n")
    sources = memory_store.list_sources()
    if not sources:
        parts.append("\n_(none)_\n")
    for s in sources:
        content = memory_store.read_source(s["id"]) or ""
        parts.append(f"\n## {s['id']} ({s['size_lines']} lines)\n\n{content}")
    parts.append("\n\n# RECENT LOG\n\n")
    parts.append(memory_store.read_recent_log(max_lines=200) or "_(empty)_")
    return "".join(parts)


# --- The lint loop ---

MAX_TURNS = 4  # lint is one-pass; allow a couple turns for retries / split calls


def lint_stream(*, model: str, client):
    """Yield events: started, skipped, tool, queued, finish, done, error."""
    run, reason = should_run()
    if not run:
        yield {"type": "skipped", "reason": reason}
        return

    if not _RUN_LOCK.acquire(blocking=False):
        yield {"type": "skipped", "reason": "another lint is already running"}
        return

    try:
        yield {"type": "started",
               "at": datetime.now().isoformat(timespec="seconds")}

        input_text = _build_lint_input()
        messages = [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": input_text,
                "cache_control": {"type": "ephemeral"},
            }],
        }]

        proposed_edits: list[dict] = []
        finish_summary = ""
        finished = False

        for turn in range(1, MAX_TURNS + 1):
            yield {"type": "turn", "n": turn}
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                tools=LINT_TOOLS,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=messages,
            )

            blocks_out = []
            tool_results = []
            for b in response.content:
                btype = getattr(b, "type", "")
                if btype == "text":
                    blocks_out.append({"type": "text", "text": getattr(b, "text", "")})
                elif btype == "tool_use":
                    name = getattr(b, "name", "")
                    args = getattr(b, "input", {}) or {}
                    tool_id = getattr(b, "id", "")
                    blocks_out.append({"type": "tool_use", "id": tool_id,
                                       "name": name, "input": args})
                    yield {"type": "tool", "name": name, "args": args}
                    if name == "propose_memory_edits":
                        edits = args.get("edits", []) or []
                        proposed_edits.extend(edits)
                        payload = {"queued": True, "edit_count": len(edits)}
                        yield {"type": "queued", "edit_count": len(edits)}
                    elif name == "finish_lint":
                        finish_summary = args.get("summary", "")
                        finished = True
                        payload = {"ok": True}
                        yield {"type": "finish", "summary": finish_summary}
                    else:
                        payload = {"error": f"unknown tool '{name}'"}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(payload),
                    })
            messages.append({"role": "assistant", "content": blocks_out})
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            if finished:
                break

        # Materialize one proposal file iff there are edits — otherwise lint is a no-op pass.
        proposal_id = None
        if proposed_edits:
            try:
                body = memory_ops._serialize_proposal(
                    finish_summary or "Lint pass", proposed_edits, source="lint",
                )
                proposal_id = memory_store.write_proposal(body, kind="lint")
                memory_store.append_log(
                    "LINT",
                    f"proposal={proposal_id} edit_count={len(proposed_edits)}",
                )
            except (ValueError, OSError) as e:
                yield {"type": "error", "message": f"failed to write proposal: {e}"}
                return
        else:
            memory_store.append_log("LINT", f"clean summary={finish_summary[:80]}")

        # Even on a no-op pass, advance the fingerprint so we don't re-run
        # against the same state on every tick.
        try:
            _write_last_fingerprint(memory_store.memory_fingerprint())
        except OSError:
            pass

        yield {"type": "done",
               "proposal_id": proposal_id,
               "edit_count": len(proposed_edits),
               "summary": finish_summary}
    finally:
        _RUN_LOCK.release()


def run_lint(*, model: str, client) -> dict:
    """Non-streaming wrapper. Returns the final 'done' event or 'skipped' marker."""
    last = None
    for ev in lint_stream(model=model, client=client):
        last = ev
    return last or {"type": "error", "message": "lint produced no events"}
