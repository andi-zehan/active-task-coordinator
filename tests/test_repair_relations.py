#!/usr/bin/env python3
"""Tests for repair_relations.py — back-reference repair script."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import repair_relations


def _write_card(board, lst, card_id, *, relations=None):
    server.write_board_meta(board, {"name": board, "color": "#000"})
    meta = {
        "id": card_id, "title": card_id, "labels": [], "due": "",
        "assignee": "", "created": "2026-05-07", "updated": "2026-05-07",
        "relations": list(relations or []),
        "custom_fields": {}, "attachments": [],
    }
    server.write_card(board, lst, card_id, meta, "## Description\n\n")
    order_path = server.DATA_DIR / "boards" / board / lst / "_order.json"
    order = server.read_json(order_path) or []
    if card_id not in order:
        order.append(card_id)
    server.write_json(order_path, order)


class TestRepairRelations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = server.DATA_DIR
        server.DATA_DIR = Path(self.tmp) / "data"
        server.ensure_data_dir()
        server.reset_id_index()

    def tearDown(self):
        server.DATA_DIR = self.orig
        shutil.rmtree(self.tmp)

    def test_scan_finds_missing_back_reference(self):
        _write_card("b", "ideas", "C-1", relations=["C-2"])
        _write_card("b", "ideas", "C-2")  # missing back-ref to C-1
        result = repair_relations.scan()
        self.assertEqual(result["fixes"], [{"add": "C-1", "to": "C-2"}])
        self.assertEqual(result["unresolved"], [])

    def test_scan_skips_when_back_reference_already_present(self):
        _write_card("b", "ideas", "C-1", relations=["C-2"])
        _write_card("b", "ideas", "C-2", relations=["C-1"])
        result = repair_relations.scan()
        self.assertEqual(result["fixes"], [])

    def test_scan_reports_unresolved_id(self):
        _write_card("b", "ideas", "C-1", relations=["C-99"])
        result = repair_relations.scan()
        self.assertEqual(result["fixes"], [])
        self.assertEqual(result["unresolved"],
                         [{"card": "C-1", "missing": "C-99"}])

    def test_scan_dedupes_when_both_sides_missing(self):
        # Neither side knows about the other — should produce one fix, not two.
        _write_card("b", "ideas", "C-1", relations=["C-2"])
        _write_card("b", "ideas", "C-2")
        result = repair_relations.scan()
        self.assertEqual(len(result["fixes"]), 1)

    def test_scan_ignores_self_reference(self):
        _write_card("b", "ideas", "C-1", relations=["C-1"])
        result = repair_relations.scan()
        self.assertEqual(result["fixes"], [])
        self.assertEqual(result["unresolved"], [])

    def test_apply_writes_back_reference(self):
        _write_card("b", "ideas", "C-1", relations=["C-2"])
        _write_card("b", "ideas", "C-2")
        result = repair_relations.scan()
        written = repair_relations.apply_fixes(result["fixes"])
        self.assertEqual(written, 1)
        c2 = server.read_card("b", "ideas", "C-2")
        self.assertEqual(c2["relations"], ["C-1"])

    def test_apply_is_idempotent(self):
        _write_card("b", "ideas", "C-1", relations=["C-2"])
        _write_card("b", "ideas", "C-2")
        repair_relations.apply_fixes(repair_relations.scan()["fixes"])
        # Second pass should find nothing.
        result = repair_relations.scan()
        self.assertEqual(result["fixes"], [])

    def test_apply_works_across_boards(self):
        _write_card("ba", "ideas", "C-1", relations=["C-2"])
        _write_card("bb", "backlog", "C-2")
        repair_relations.apply_fixes(repair_relations.scan()["fixes"])
        c2 = server.read_card("bb", "backlog", "C-2")
        self.assertEqual(c2["relations"], ["C-1"])


if __name__ == "__main__":
    unittest.main()
