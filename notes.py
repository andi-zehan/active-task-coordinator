"""Notes-to-cards integration: snapshot, archive, LLM call, apply."""
import json
import re
from datetime import date, datetime
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
