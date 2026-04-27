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


class TestArchiveNote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_creates_dir_and_file(self):
        note_id = notes.archive_note("Hello world", "Q2 Planning")
        self.assertTrue(notes.NOTES_DIR.exists())
        self.assertTrue((notes.NOTES_DIR / f"{note_id}.md").exists())

    def test_archive_filename_format(self):
        note_id = notes.archive_note("body", "Q2 Planning")
        # YYYY-MM-DD-slug
        self.assertRegex(note_id, r"^\d{4}-\d{2}-\d{2}-q2-planning$")

    def test_archive_default_title(self):
        note_id = notes.archive_note("body", "")
        self.assertRegex(note_id, r"^\d{4}-\d{2}-\d{2}-untitled-\d{6}$")

    def test_archive_preserves_body(self):
        note_id = notes.archive_note("Line1\nLine2\n", "Title")
        text = (notes.NOTES_DIR / f"{note_id}.md").read_text(encoding="utf-8")
        self.assertIn("Line1\nLine2", text)

    def test_archive_writes_frontmatter(self):
        note_id = notes.archive_note("body", "Title")
        text = (notes.NOTES_DIR / f"{note_id}.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("title: Title", text)
        self.assertIn("applied_ops: []", text)

    def test_archive_collision_suffix(self):
        notes.archive_note("a", "Same Title")
        second = notes.archive_note("b", "Same Title")
        self.assertTrue(second.endswith("-2"))


class TestReadNote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_existing(self):
        note_id = notes.archive_note("body text", "Title")
        text = notes.read_note(note_id)
        self.assertIn("body text", text)

    def test_read_missing_returns_none(self):
        self.assertIsNone(notes.read_note("nonexistent"))

    def test_read_rejects_path_traversal(self):
        self.assertIsNone(notes.read_note("../server"))
        self.assertIsNone(notes.read_note("foo/bar"))


class FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text, "type": "text"})()]


class FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeMessage(self._response_text)


class FakeClient:
    def __init__(self, response_text):
        self.messages = FakeMessages(response_text)


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"

    def tearDown(self):
        self.tmp.cleanup()

    def test_analyze_parses_json_response(self):
        fake_response = json.dumps({
            "summary": "Meeting summary",
            "operations": [
                {"op": "create_card", "board": "alpha", "list": "backlog",
                 "title": "New task", "confidence": "high", "reason": "explicit ask"}
            ],
        })
        client = FakeClient(fake_response)
        result = notes.analyze("note text", "Title", model="claude-opus-4-7", client=client)
        self.assertEqual(result["summary"], "Meeting summary")
        self.assertEqual(len(result["operations"]), 1)
        self.assertEqual(result["operations"][0]["title"], "New task")
        self.assertTrue(result["note_id"].endswith("-title"))

    def test_analyze_archives_note(self):
        client = FakeClient('{"summary": "", "operations": []}')
        result = notes.analyze("note body", "T", model="claude-opus-4-7", client=client)
        archived = (notes.NOTES_DIR / f"{result['note_id']}.md").read_text(encoding="utf-8")
        self.assertIn("note body", archived)

    def test_analyze_extracts_json_from_fenced_block(self):
        fenced = '```json\n{"summary": "x", "operations": []}\n```'
        client = FakeClient(fenced)
        result = notes.analyze("body", "T", model="claude-opus-4-7", client=client)
        self.assertEqual(result["summary"], "x")

    def test_analyze_invalid_json_raises(self):
        client = FakeClient("this is not json at all")
        with self.assertRaises(notes.LLMResponseError):
            notes.analyze("body", "T", model="claude-opus-4-7", client=client)

    def test_analyze_passes_model_and_caching(self):
        client = FakeClient('{"summary": "", "operations": []}')
        notes.analyze("body", "T", model="claude-sonnet-4-6", client=client)
        kwargs = client.messages.last_kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-4-6")
        # System prompt cached
        sys_block = kwargs["system"][0] if isinstance(kwargs["system"], list) else None
        self.assertIsNotNone(sys_block)
        self.assertEqual(sys_block.get("cache_control"), {"type": "ephemeral"})


if __name__ == "__main__":
    unittest.main()
