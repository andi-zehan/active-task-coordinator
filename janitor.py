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
