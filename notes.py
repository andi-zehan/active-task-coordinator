"""Notes-to-cards integration: snapshot, archive, LLM call, apply."""
import json
import re
from datetime import date
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
