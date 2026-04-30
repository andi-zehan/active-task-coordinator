"""Notes-to-cards integration: snapshot, archive, LLM call, apply."""
import copy
import json
import re
from datetime import date, datetime
from pathlib import Path

import server
from chat_tools import (
    READ_TOOL_DEFS, WRITE_TOOL_DEFS, READ_TOOLS,
    _WRITE_OP_NAMES, _queue_op,
    _summarize_read_result, _queued_summary_fields,
)

NOTES_DIR = Path(__file__).parent / "notes"


def build_toc() -> dict:
    """Lightweight index: boards + cards with slug/title/labels/due/assignee only.

    Used as the cached prefix for the tool-use loop. The model fetches
    description, checklist, and comments on demand via read_card.
    """
    boards_order_path = server.DATA_DIR / "_boards-order.json"
    if not boards_order_path.exists():
        return {"boards": [], "today": date.today().isoformat()}
    board_slugs = json.loads(boards_order_path.read_text(encoding="utf-8"))

    boards = []
    for board_slug in board_slugs:
        board_meta = server.read_board_meta(board_slug)
        if board_meta is None:
            continue
        cards = []
        for list_slug in server.LISTS:
            order_file = server.DATA_DIR / "boards" / board_slug / list_slug / "_order.json"
            if not order_file.exists():
                continue
            for card_slug in json.loads(order_file.read_text(encoding="utf-8")):
                card = server.read_card(board_slug, list_slug, card_slug)
                if card is None:
                    continue
                cards.append({
                    "l": list_slug,
                    "s": card_slug,
                    "title": card.get("title", ""),
                    "labels": card.get("labels") or [],
                    "due": card.get("due", ""),
                    "assignee": card.get("assignee", ""),
                })
        boards.append({
            "slug": board_slug,
            "name": board_meta.get("name", board_slug),
            "cards": cards,
        })
    return {"boards": boards, "today": date.today().isoformat()}


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


class LLMResponseError(Exception):
    """Raised when the model fails to produce a usable result."""


SYSTEM_PROMPT = """You turn meeting notes into kanban card operations using tools.

You will receive:
1. A board INDEX listing every board and its cards (slug, title, labels, due, assignee).
2. A meeting note.

Workflow:
- Use search_cards before create_card so you don't duplicate existing work.
- Use read_card to fetch a card's description, checklist, and comments when you need them to decide what op to propose.
- Propose ops by calling the write tools (create_card, add_comment, tick_checklist, add_checklist_item, move_card, update_field). These are queued, not executed — the user reviews them before anything is written.
- When you have proposed every op the note warrants, call finish with a 1-2 sentence summary.

Rules:
- Only reference boards, lists, and cards that exist (per the INDEX or read_card).
- For new cards, default to list 'backlog' unless the note clearly implies another stage.
- confidence: 'high' = explicit, 'med' = strongly implied, 'low' = speculative.
- reason: cite the specific phrase or fact in the note that motivated the op.
"""


FINISH_TOOL_DEF = {
    "name": "finish",
    "description": "Call once you have proposed every op the note warrants. Provide a 1-2 sentence summary of the meeting.",
    "input_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
}

def _build_notes_tools() -> list[dict]:
    """Assemble the notes-flavored TOOLS list with a notes-specific override.

    Deep-copies the shared defs so mutating them here doesn't affect chat.
    """
    tools = [copy.deepcopy(t) for t in (READ_TOOL_DEFS + WRITE_TOOL_DEFS)]
    for t in tools:
        if t["name"] == "list_boards":
            t["description"] = (
                "List all boards with name and card count. "
                "The INDEX in the first user message already covers this; "
                "call only if you need a fresh view."
            )
    return tools + [FINISH_TOOL_DEF]


TOOLS = _build_notes_tools()


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


def analyze_stream(body: str, title: str, *, model: str, client,
                   max_turns: int = MAX_TOOL_TURNS):
    """Run the tool-use loop, yielding one event dict per significant step.

    Event shapes (all dicts have a 'type' key):
      {"type": "started",  "note_id": str}
      {"type": "turn",     "n": int}
      {"type": "tool",     "name": str, "args": dict}        # before invocation
      {"type": "result",   "name": str, "summary": str}      # short summary of payload
      {"type": "queued",   "op": str, "title"|"text"|...: ...}  # write tool queued
      {"type": "finish",   "summary": str}
      {"type": "done",     "note_id": str, "summary": str, "operations": [...]}
      {"type": "error",    "message": str}                   # terminal

    The 'done' event carries the same dict that the old analyze() returned.
    """
    note_id = archive_note(body, title)
    yield {"type": "started", "note_id": note_id}

    toc = build_toc()
    proposed_ops: list[dict] = []
    summary = ""
    finished = False

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "BOARD INDEX:\n" + json.dumps(toc),
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
    ]

    for turn in range(1, max_turns + 1):
        yield {"type": "turn", "n": turn}
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=TOOLS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            ],
            messages=messages,
        )

        assistant_blocks = [_block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": assistant_blocks})

        tool_use_blocks = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if not tool_use_blocks:
            break

        tool_results = []
        for block in tool_use_blocks:
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            tool_id = getattr(block, "id", "")
            yield {"type": "tool", "name": name, "args": args}
            try:
                if name == "finish":
                    summary = args.get("summary", "")
                    finished = True
                    payload = {"ok": True}
                    yield {"type": "finish", "summary": summary}
                elif name in READ_TOOLS:
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

        messages.append({"role": "user", "content": tool_results})

        if finished:
            break

    if not finished and not proposed_ops:
        yield {"type": "error",
               "message": "Model exited without proposing operations or calling finish."}
        return

    yield {"type": "done", "note_id": note_id,
           "summary": summary, "operations": proposed_ops}


def analyze(body: str, title: str, *, model: str, client,
            max_turns: int = MAX_TOOL_TURNS) -> dict:
    """Non-streaming wrapper around analyze_stream.

    Returns {"note_id": str, "summary": str, "operations": [...]}.
    Raises LLMResponseError if the model produces no useful output.
    """
    last_done = None
    for event in analyze_stream(body, title, model=model, client=client, max_turns=max_turns):
        if event["type"] == "done":
            last_done = event
        elif event["type"] == "error":
            raise LLMResponseError(event["message"])
    if last_done is None:
        raise LLMResponseError("analyze produced no result")
    return {"note_id": last_done["note_id"], "summary": last_done["summary"],
            "operations": last_done["operations"]}
