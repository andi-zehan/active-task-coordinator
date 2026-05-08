#!/usr/bin/env python3
"""One-shot CLI: repair missing back-references in card relations.

Walks every live card. For each `C-X` in card B's relations, ensures `C-B` is
in card X's relations — adding it if missing. Idempotent. Skips self-references
and unresolved IDs (those are reported separately).

Usage:
    python repair_relations.py            # dry run, prints what would change
    python repair_relations.py --apply    # actually write the fixes
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import server


def scan() -> dict:
    """Return {fixes: [...], unresolved: [...]} without modifying anything.

    fixes:      list of {add: "C-A", to: "C-B"} — back-ref that needs writing
    unresolved: list of {card: "C-B", missing: "C-X"} — relation points nowhere
    """
    server.reset_id_index()
    fixes: list[dict] = []
    unresolved: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    boards_dir = server.DATA_DIR / "boards"
    if not boards_dir.exists():
        return {"fixes": fixes, "unresolved": unresolved}

    for board_dir in sorted(boards_dir.iterdir()):
        if not board_dir.is_dir():
            continue
        for list_name in server.LISTS:
            list_dir = board_dir / list_name
            if not list_dir.exists():
                continue
            order_path = list_dir / "_order.json"
            if not order_path.exists():
                continue
            slugs = json.loads(order_path.read_text(encoding="utf-8"))
            for slug in slugs:
                card_b = server.read_card(board_dir.name, list_name, slug)
                if card_b is None:
                    continue
                b_id = card_b.get("id") or slug
                for x_id in card_b.get("relations") or []:
                    if x_id == b_id:
                        continue
                    loc = server.resolve_id(x_id)
                    if loc is None:
                        unresolved.append({"card": b_id, "missing": x_id})
                        continue
                    x_board, x_list = loc
                    card_x = server.read_card(x_board, x_list, x_id)
                    if card_x is None:
                        unresolved.append({"card": b_id, "missing": x_id})
                        continue
                    if b_id in (card_x.get("relations") or []):
                        continue
                    pair = (x_id, b_id)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    fixes.append({"add": b_id, "to": x_id})
    return {"fixes": fixes, "unresolved": unresolved}


def apply_fixes(fixes: list[dict]) -> int:
    """Write back-references onto disk. Returns count actually written."""
    written = 0
    today_str = str(date.today())
    for fix in fixes:
        x_id = fix["to"]
        b_id = fix["add"]
        loc = server.resolve_id(x_id)
        if loc is None:
            continue
        x_board, x_list = loc
        card_x = server.read_card(x_board, x_list, x_id)
        if card_x is None:
            continue
        rels = list(card_x.get("relations") or [])
        if b_id in rels:
            continue
        rels.append(b_id)
        card_x["relations"] = rels
        card_x["updated"] = today_str
        meta = {k: v for k, v in card_x.items()
                if k not in ("slug", "board", "list", "body")}
        server.write_card(x_board, x_list, x_id, meta, card_x.get("body", ""))
        written += 1
    return written


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    data_dir = server.DATA_DIR
    if not data_dir.exists():
        print(f"error: {data_dir} does not exist", file=sys.stderr)
        return 1

    if apply and (data_dir / ".git").exists():
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=data_dir, timeout=10,
            )
            if r.stdout.strip():
                print(
                    f"error: {data_dir} has uncommitted changes — "
                    "commit or stash first",
                    file=sys.stderr,
                )
                return 2
        except Exception as e:
            print(f"warning: could not check git status: {e}", file=sys.stderr)

    result = scan()
    fixes = result["fixes"]
    unresolved = result["unresolved"]

    print(f"missing back-references: {len(fixes)}")
    for fix in fixes:
        print(f"  add {fix['add']} to {fix['to']}.relations")
    if unresolved:
        print(f"\nunresolved relation IDs: {len(unresolved)}")
        for u in unresolved:
            print(f"  {u['card']}.relations contains {u['missing']} (no such card)")

    if not apply:
        if fixes:
            print(f"\ndry run — re-run with --apply to write {len(fixes)} fix(es).")
        else:
            print("\nnothing to fix.")
        return 0

    written = apply_fixes(fixes)
    print(f"\nwrote {written} back-reference(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
