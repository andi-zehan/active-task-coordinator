"""Periodic cleanup: delete done cards older than 14 days, delete orphan notes."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import server

DONE_RETENTION_DAYS = 14
NOTES_DIR = Path(__file__).parent / "notes"


def _list_board_slugs() -> list[str]:
    path = server.DATA_DIR / "_boards-order.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def sweep_done_cards() -> int:
    """Delete cards in 'done' lists whose updated date is > 14 days ago.

    Returns the count of deleted cards.
    """
    cutoff = date.today() - timedelta(days=DONE_RETENTION_DAYS)
    deleted = 0
    for board in _list_board_slugs():
        done_dir = server.DATA_DIR / "boards" / board / "done"
        order_path = done_dir / "_order.json"
        if not done_dir.exists() or not order_path.exists():
            continue
        slugs = json.loads(order_path.read_text(encoding="utf-8"))
        kept = []
        for slug in slugs:
            card = server.read_card(board, "done", slug)
            if card is None:
                continue
            updated = _parse_date(card.get("updated", ""))
            if updated is None:
                # conservative: keep cards we can't date
                kept.append(slug)
                continue
            if updated < cutoff:
                card_path = done_dir / f"{slug}.md"
                card_path.unlink(missing_ok=True)
                deleted += 1
            else:
                kept.append(slug)
        order_path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return deleted


def _collect_referenced_note_ids() -> set[str]:
    """Walk every card across every board and gather note_ids referenced in attachments."""
    referenced = set()
    prefix = "/api/notes/"
    for board in _list_board_slugs():
        for list_slug in server.LISTS:
            order_path = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
            if not order_path.exists():
                continue
            for slug in json.loads(order_path.read_text(encoding="utf-8")):
                card = server.read_card(board, list_slug, slug)
                if card is None:
                    continue
                for att in card.get("attachments") or []:
                    url = att.get("url", "")
                    if url.startswith(prefix):
                        referenced.add(url[len(prefix):])
    return referenced


def sweep_orphan_notes() -> int:
    """Delete notes/ files not referenced by any card attachment.

    Returns the count of deleted notes.
    """
    if not NOTES_DIR.exists():
        return 0
    referenced = _collect_referenced_note_ids()
    deleted = 0
    for note_path in NOTES_DIR.glob("*.md"):
        note_id = note_path.stem
        if note_id not in referenced:
            note_path.unlink()
            deleted += 1
    return deleted


def run_all() -> dict:
    """Run both sweeps. Used by /api/janitor/run and the periodic timer."""
    done = sweep_done_cards()
    orphans = sweep_orphan_notes()
    print(f"janitor: deleted {done} done cards, {orphans} orphan notes", flush=True)
    return {"done_cards_deleted": done, "orphan_notes_deleted": orphans}
