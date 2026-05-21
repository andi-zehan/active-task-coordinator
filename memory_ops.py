"""Apply-side handlers for memory write ops.

Mirrors notes.apply_operations shape but routes to memory_store. Two layers:

  * `apply_memory_operations(ops)` — runs queued ops from a chat/notes turn.
    Two op types: save_as_source (writes immediately to _sources/) and
    propose_memory_edits (writes a proposal file to _proposals/ — does NOT
    touch wiki pages directly).

  * `apply_proposal(proposal_id, accepted_edit_indices)` — invoked from the
    proposal review UI. Reads the proposal, applies only the accepted edits
    to wiki pages, deletes the proposal file, logs EDIT.

The split keeps wiki page mutations behind a second approval step (review
the diff before any wiki page is rewritten).
"""
import json

import memory_store


# --- Proposal serialization ---

# A proposal file is a JSON document wrapped in a markdown code fence. Plain
# text first (so it renders readably when a human opens the file in an
# editor), then the structured payload. The UI parses the JSON; lint and
# manual review can read the prose.

_PROPOSAL_FENCE = "```json"


def _serialize_proposal(summary: str, edits: list[dict], *, source: str) -> str:
    """Render a proposal as human-readable markdown with embedded JSON payload."""
    edits_summary = "\n".join(
        f"- {e.get('action','?')} `{e.get('page','?')}` ({_count_lines(e.get('content',''))} lines)"
        for e in edits
    )
    payload = {"summary": summary, "source": source, "edits": edits}
    return (
        f"# Memory edit proposal\n\n"
        f"**Source:** {source}\n\n"
        f"**Summary:** {summary}\n\n"
        f"## Edits\n\n{edits_summary}\n\n"
        f"## Payload\n\n"
        f"{_PROPOSAL_FENCE}\n{json.dumps(payload, indent=2)}\n```\n"
    )


def parse_proposal(text: str) -> dict | None:
    """Extract the JSON payload from a proposal file. Returns None on parse failure."""
    start = text.find(_PROPOSAL_FENCE)
    if start == -1:
        return None
    start += len(_PROPOSAL_FENCE)
    end = text.find("```", start)
    if end == -1:
        return None
    try:
        return json.loads(text[start:end].strip())
    except json.JSONDecodeError:
        return None


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


# --- Op dispatch ---

def _do_save_as_source(op: dict) -> dict:
    title = op.get("title", "") or "untitled"
    content = op.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("save_as_source: content is required")
    source_id = memory_store.write_source(title, content)
    memory_store.append_log("INGEST", f"source={source_id}")
    return {"target": f"_sources/{source_id}"}


def _do_propose_memory_edits(op: dict) -> dict:
    """Apply queued wiki edits directly to memory pages.

    Chat shows per-edit diffs + per-edit checkboxes BEFORE Apply, so by the
    time this runs the user has already reviewed the content. Writing a
    proposal file at this point would force a second, redundant review hop
    in the Memory modal — exactly the friction we're removing.

    Lint still goes through a proposal file (memory_lint writes one directly
    via memory_store.write_proposal), because lint has no chat surface to
    host per-edit review.
    """
    edits = op.get("edits", []) or []
    if not isinstance(edits, list) or not edits:
        raise ValueError("propose_memory_edits: edits list is required")
    cleaned = []
    for e in edits:
        if not isinstance(e, dict):
            raise ValueError("propose_memory_edits: each edit must be an object")
        page = e.get("page", "")
        action = e.get("action", "")
        content = e.get("content", "")
        if action not in ("create", "replace", "append"):
            raise ValueError(f"propose_memory_edits: invalid action {action!r}")
        memory_store._validate_slug(page)
        if not isinstance(content, str):
            raise ValueError("propose_memory_edits: content must be a string")
        cleaned.append({"page": page, "action": action, "content": content})

    # Validate every edit's create-vs-exists state before writing anything,
    # so a mid-batch failure can't leave half the wiki rewritten.
    for e in cleaned:
        if e["action"] == "create" and memory_store.read_page(e["page"]) is not None:
            raise ValueError(f"page '{e['page']}' already exists — use replace")

    written = []
    for e in cleaned:
        _apply_edit(e)
        memory_store.append_log(
            "EDIT", f"page={e['page']} action={e['action']} via=chat",
        )
        written.append(e["page"])
    return {"target": "memory/" + ",".join(written)}


_HANDLERS = {
    "save_as_source": _do_save_as_source,
    "propose_memory_edits": _do_propose_memory_edits,
}


def apply_memory_operations(operations: list[dict]) -> dict:
    """Run queued memory ops. Mirrors notes.apply_operations contract."""
    applied = []
    skipped = []
    for op in operations:
        handler = _HANDLERS.get(op.get("op"))
        if handler is None:
            skipped.append({"op": op, "reason": f"unknown memory op '{op.get('op')}'"})
            continue
        try:
            outcome = handler(op)
            applied.append({"op": op["op"], "target": outcome["target"]})
        except (ValueError, KeyError) as e:
            skipped.append({"op": op, "reason": str(e)})
    return {"applied": applied, "skipped": skipped}


# --- Proposal review apply ---

def apply_proposal(proposal_id: str, accepted_indices: list[int],
                   edit_overrides: dict | None = None) -> dict:
    """Apply selected edits from a proposal and delete the proposal file.

    `accepted_indices` lists which edits to apply (by their 0-based index in
    the proposal's `edits` array). Edits not in the list are silently
    dropped. An empty list dismisses the proposal without writing.

    `edit_overrides` maps "<index>" -> replacement content string. Used by
    the Proposals tab when the user has tweaked the proposed text in the
    inline textarea before applying. Indices not present fall back to the
    proposal's original content. Keys are strings to survive JSON transit.

    Returns {"applied": [{page, action}], "skipped": [...]}.
    """
    raw = memory_store.read_proposal(proposal_id)
    if raw is None:
        raise ValueError(f"unknown proposal: {proposal_id}")
    parsed = parse_proposal(raw)
    if parsed is None:
        raise ValueError(f"proposal {proposal_id} could not be parsed")
    edits = parsed.get("edits", []) or []
    if not isinstance(accepted_indices, list):
        raise ValueError("accepted_indices must be a list")
    overrides = edit_overrides or {}
    if not isinstance(overrides, dict):
        raise ValueError("edit_overrides must be an object")

    applied = []
    skipped = []
    for i in accepted_indices:
        if not isinstance(i, int) or i < 0 or i >= len(edits):
            skipped.append({"index": i, "reason": "out of range"})
            continue
        edit = dict(edits[i])
        # Override key is the index as a string (JSON object keys are strings);
        # also accept the int form for in-process callers / tests.
        override = overrides.get(str(i), overrides.get(i))
        if isinstance(override, str):
            edit["content"] = override
        try:
            _apply_edit(edit)
            applied.append({"page": edit["page"], "action": edit["action"]})
            memory_store.append_log(
                "EDIT",
                f"page={edit['page']} action={edit['action']} via=proposal:{proposal_id}",
            )
        except (ValueError, KeyError, OSError) as e:
            skipped.append({"index": i, "page": edit.get("page", ""), "reason": str(e)})

    # Whether or not all edits applied, the proposal has been reviewed —
    # delete it so it doesn't linger as "pending". Skipped edits surface in
    # the response; the user can re-trigger the source/lint if needed.
    memory_store.delete_proposal(proposal_id)
    return {"applied": applied, "skipped": skipped}


def _apply_edit(edit: dict) -> None:
    page = edit["page"]
    action = edit["action"]
    content = edit.get("content", "")
    existing = memory_store.read_page(page)
    if action == "create":
        if existing is not None:
            raise ValueError(f"page '{page}' already exists — use replace")
        memory_store.write_page(page, content)
    elif action == "replace":
        memory_store.write_page(page, content)
    elif action == "append":
        base = existing or ""
        sep = "" if base.endswith("\n") or not base else "\n"
        memory_store.write_page(page, base + sep + content)
    else:
        raise ValueError(f"invalid action: {action}")


def dismiss_proposal(proposal_id: str) -> bool:
    """Drop a proposal without applying anything. Returns True if removed."""
    if memory_store.read_proposal(proposal_id) is None:
        return False
    memory_store.delete_proposal(proposal_id)
    return True


def split_memory_ops(operations: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partition a mixed op list into (card_ops, memory_ops).

    Used by every apply handler (/api/notes/apply, /api/ask/apply,
    /api/briefing/apply) so memory ops queued in the same turn as card ops
    get routed to apply_memory_operations instead of being silently rejected
    by notes.apply_operations (which doesn't know about them).
    """
    from chat_tools import MEMORY_WRITE_OP_NAMES
    card_ops, mem_ops = [], []
    for op in operations:
        if op.get("op") in MEMORY_WRITE_OP_NAMES:
            mem_ops.append(op)
        else:
            card_ops.append(op)
    return card_ops, mem_ops


def manual_write_page(page: str, content: str) -> None:
    """User-driven edit (textarea save). Bypasses proposals. Logs EDIT."""
    memory_store.write_page(page, content)
    memory_store.append_log("EDIT", f"page={page} via=manual")
