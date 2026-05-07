"""One-shot migration: assign IDs to existing cards, rename files, convert relations."""
import re
import time

import server


def _walk_lists():
    """Yield (board, list, list_dir) for every existing list directory."""
    boards_dir = server.DATA_DIR / "boards"
    if not boards_dir.exists():
        return
    for board_dir in sorted(boards_dir.iterdir()):
        if not board_dir.is_dir():
            continue
        for list_name in server.LISTS:
            list_dir = board_dir / list_name
            if list_dir.exists():
                yield board_dir.name, list_name, list_dir


def _read_order(list_dir):
    p = list_dir / "_order.json"
    if not p.exists():
        return []
    import json
    return json.loads(p.read_text(encoding="utf-8"))


def _write_order(list_dir, order):
    import json
    (list_dir / "_order.json").write_text(json.dumps(order, indent=2), encoding="utf-8")


def assign_ids() -> dict:
    """Assign IDs to every card lacking one, rename files, convert path relations.

    Idempotent: running on an already-migrated repo is a no-op.
    """
    started = time.time()
    cards_migrated = 0

    # Pass 1: per (board, list), assign IDs and rename files in order.
    # Build path_map = {(board, list, old_slug): new_id} so pass 2 can rewrite relations.
    path_map: dict[tuple[str, str, str], str] = {}
    boards_seen = set()
    for board, list_slug, list_dir in _walk_lists():
        boards_seen.add(board)
        order = _read_order(list_dir)
        new_order = []
        for slug in order:
            new_order.append(_migrate_one(board, list_slug, list_dir, slug, path_map))
            if new_order[-1] != slug:
                cards_migrated += 1
        _write_order(list_dir, new_order)

    # Refresh the index now that all renames are in place.
    server.reset_id_index()

    # Pass 2: convert path-style relations across all cards.
    relations_converted = 0
    relations_unresolved: list[dict] = []
    for board, list_slug, list_dir in _walk_lists():
        for card_path in list_dir.glob("*.md"):
            card_id = card_path.stem
            card = server.read_card(board, list_slug, card_id)
            if card is None:
                continue
            rels = card.get("relations") or []
            new_rels = []
            changed = False
            for rel in rels:
                if not isinstance(rel, str):
                    new_rels.append(rel)
                    continue
                if re.match(r"^C-\d+$", rel):
                    new_rels.append(rel)  # already an ID
                    continue
                parts = rel.split("/")
                if len(parts) == 3:
                    key = (parts[0], parts[1], parts[2])
                    if key in path_map:
                        new_rels.append(path_map[key])
                        relations_converted += 1
                        changed = True
                        continue
                relations_unresolved.append({"card": card_id, "stale": rel})
                new_rels.append(rel)
            if changed:
                card["relations"] = new_rels
                meta = {k: v for k, v in card.items()
                        if k not in ("slug", "board", "list", "body")}
                server.write_card(board, list_slug, card_id, meta, card["body"])

    return {
        "boards": len(boards_seen),
        "cards_migrated": cards_migrated,
        "relations_converted": relations_converted,
        "relations_unresolved": relations_unresolved,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _migrate_one(board, list_slug, list_dir, slug, path_map) -> str:
    """Migrate a single card. Returns its (possibly new) slug."""
    card_path = list_dir / f"{slug}.md"
    if not card_path.exists():
        return slug
    text = card_path.read_text(encoding="utf-8")
    meta, body = server.parse_frontmatter(text)
    existing_id = meta.get("id")
    if existing_id and re.match(r"^C-\d+$", existing_id):
        # Already migrated; keep its slug (which should equal the id).
        return existing_id
    card_id = server.next_id()
    meta["id"] = card_id
    new_text = server.serialize_frontmatter(meta, body)
    new_path = list_dir / f"{card_id}.md"
    new_path.write_text(new_text, encoding="utf-8")
    if new_path != card_path:
        card_path.unlink()
    path_map[(board, list_slug, slug)] = card_id
    return card_id
