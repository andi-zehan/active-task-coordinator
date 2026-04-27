#!/usr/bin/env python3
"""Tests for janitor module."""

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import janitor


def make_board(data_dir: Path, slug: str = "alpha"):
    board_dir = data_dir / "boards" / slug
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "_board.md").write_text("---\nname: A\ncolor: '#000'\n---\n", encoding="utf-8")
    for lst in ("ideas", "backlog", "in-progress", "done"):
        (board_dir / lst).mkdir(exist_ok=True)
        (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
    boards_order = data_dir / "_boards-order.json"
    order = json.loads(boards_order.read_text()) if boards_order.exists() else []
    if slug not in order:
        order.append(slug)
        boards_order.write_text(json.dumps(order), encoding="utf-8")


def make_card(data_dir: Path, board: str, lst: str, slug: str, updated: str,
              attachments: list[dict] | None = None):
    list_dir = data_dir / "boards" / board / lst
    list_dir.mkdir(parents=True, exist_ok=True)
    att_lines = ""
    if attachments:
        att_lines = "attachments:\n" + "".join(
            f"  - name: {a['name']}\n    url: {a['url']}\n" for a in attachments
        )
    else:
        att_lines = "attachments: []\n"
    (list_dir / f"{slug}.md").write_text(
        f"---\ntitle: {slug}\ncreated: 2026-01-01\nupdated: {updated}\n{att_lines}---\n\n"
        f"## Description\n\n\n\n## Checklist\n\n\n## Comments\n\n",
        encoding="utf-8",
    )
    order_file = list_dir / "_order.json"
    order = json.loads(order_file.read_text()) if order_file.exists() else []
    if slug not in order:
        order.append(slug)
        order_file.write_text(json.dumps(order), encoding="utf-8")


class TestSweepDoneCards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_done_card_deleted(self):
        old = (date.today() - timedelta(days=15)).isoformat()
        make_card(self.data_dir, "alpha", "done", "old-task", old)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 1)
        self.assertFalse((self.data_dir / "boards/alpha/done/old-task.md").exists())
        order = json.loads((self.data_dir / "boards/alpha/done/_order.json").read_text())
        self.assertNotIn("old-task", order)

    def test_recent_done_card_kept(self):
        recent = (date.today() - timedelta(days=7)).isoformat()
        make_card(self.data_dir, "alpha", "done", "recent-task", recent)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.data_dir / "boards/alpha/done/recent-task.md").exists())

    def test_exactly_14_days_kept(self):
        edge = (date.today() - timedelta(days=14)).isoformat()
        make_card(self.data_dir, "alpha", "done", "edge-task", edge)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)

    def test_15_days_deleted(self):
        edge = (date.today() - timedelta(days=15)).isoformat()
        make_card(self.data_dir, "alpha", "done", "edge-task", edge)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 1)

    def test_card_in_other_list_not_touched(self):
        old = (date.today() - timedelta(days=30)).isoformat()
        make_card(self.data_dir, "alpha", "backlog", "old-backlog", old)
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.data_dir / "boards/alpha/backlog/old-backlog.md").exists())

    def test_invalid_updated_date_skipped(self):
        make_card(self.data_dir, "alpha", "done", "bad-date", "not-a-date")
        deleted = janitor.sweep_done_cards()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.data_dir / "boards/alpha/done/bad-date.md").exists())


class TestSweepOrphanNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir)
        self.notes_dir = Path(self.tmp.name) / "notes"
        self.notes_dir.mkdir()
        janitor.NOTES_DIR = self.notes_dir

    def tearDown(self):
        self.tmp.cleanup()

    def _write_note(self, note_id: str):
        (self.notes_dir / f"{note_id}.md").write_text(
            f"---\ndate: 2026-04-01\ntitle: x\napplied_ops: []\n---\n\nbody\n",
            encoding="utf-8",
        )

    def test_unreferenced_note_deleted(self):
        self._write_note("2026-04-01-orphan")
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 1)
        self.assertFalse((self.notes_dir / "2026-04-01-orphan.md").exists())

    def test_referenced_note_kept(self):
        self._write_note("2026-04-01-keepme")
        make_card(self.data_dir, "alpha", "backlog", "linked", "2026-04-25",
            attachments=[{"name": "src", "url": "/api/notes/2026-04-01-keepme"}])
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 0)
        self.assertTrue((self.notes_dir / "2026-04-01-keepme.md").exists())

    def test_only_attachment_links_count(self):
        self._write_note("2026-04-01-comment-only")
        # link is in body comment, not attachments -> still orphan
        list_dir = self.data_dir / "boards/alpha/backlog"
        (list_dir / "comment-card.md").write_text(
            "---\ntitle: c\ncreated: 2026-04-01\nupdated: 2026-04-25\nattachments: []\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n\n"
            "## Comments\n\nSee [note](/api/notes/2026-04-01-comment-only)\n",
            encoding="utf-8",
        )
        order = json.loads((list_dir / "_order.json").read_text())
        order.append("comment-card")
        (list_dir / "_order.json").write_text(json.dumps(order), encoding="utf-8")
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 1)
        self.assertFalse((self.notes_dir / "2026-04-01-comment-only.md").exists())

    def test_empty_notes_dir_no_error(self):
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 0)

    def test_missing_notes_dir_no_error(self):
        janitor.NOTES_DIR = self.notes_dir / "does-not-exist"
        deleted = janitor.sweep_orphan_notes()
        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()
