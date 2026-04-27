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
