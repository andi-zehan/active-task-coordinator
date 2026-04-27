#!/usr/bin/env python3
"""Tests for notes module."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import notes


def make_card(data_dir: Path, board: str, lst: str, slug: str, body: str = ""):
    """Helper: create a card and add to the list's _order.json."""
    list_dir = data_dir / "boards" / board / lst
    list_dir.mkdir(parents=True, exist_ok=True)
    (list_dir / f"{slug}.md").write_text(body, encoding="utf-8")
    order_file = list_dir / "_order.json"
    order = json.loads(order_file.read_text()) if order_file.exists() else []
    order.append(slug)
    order_file.write_text(json.dumps(order), encoding="utf-8")


def make_board(data_dir: Path, slug: str, name: str = "Test Board"):
    board_dir = data_dir / "boards" / slug
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "_board.md").write_text(
        f"---\nname: {name}\ncolor: '#000000'\n---\n", encoding="utf-8"
    )
    for lst in ("ideas", "backlog", "in-progress", "done"):
        (board_dir / lst).mkdir(exist_ok=True)
        (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
    boards_order = data_dir / "_boards-order.json"
    order = json.loads(boards_order.read_text()) if boards_order.exists() else []
    if slug not in order:
        order.append(slug)
        boards_order.write_text(json.dumps(order), encoding="utf-8")


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir, "alpha", "Alpha Project")
        make_card(self.data_dir, "alpha", "backlog", "draft-spec",
            "---\ntitle: Draft spec\nlabels: [doc]\ndue: 2026-05-01\nassignee: Me\n"
            "created: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\nWrite the spec.\n\n\n"
            "## Checklist\n\n- [ ] Outline\n- [x] Title\n\n\n"
            "## Comments\n\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_includes_board(self):
        snap = notes.build_snapshot()
        self.assertIn("alpha", [b["slug"] for b in snap["boards"]])

    def test_snapshot_includes_card_with_compact_fields(self):
        snap = notes.build_snapshot()
        cards = snap["boards"][0]["cards"]
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual(c["b"], "alpha")
        self.assertEqual(c["l"], "backlog")
        self.assertEqual(c["s"], "draft-spec")
        self.assertEqual(c["title"], "Draft spec")
        self.assertEqual(c["labels"], ["doc"])
        self.assertEqual(c["due"], "2026-05-01")
        self.assertEqual(c["assignee"], "Me")
        self.assertIn("Outline", c["todo"])
        self.assertIn("Title", c["done"])

    def test_snapshot_truncates_description_to_200(self):
        long_desc = "x" * 500
        make_card(self.data_dir, "alpha", "ideas", "long-card",
            f"---\ntitle: Long\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            f"## Description\n\n{long_desc}\n\n\n## Checklist\n\n\n## Comments\n\n")
        snap = notes.build_snapshot()
        all_cards = [c for b in snap["boards"] for c in b["cards"]]
        long = next(c for c in all_cards if c["s"] == "long-card")
        self.assertLessEqual(len(long["desc"]), 200)


if __name__ == "__main__":
    unittest.main()
