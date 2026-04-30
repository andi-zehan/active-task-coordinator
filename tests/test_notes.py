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
import chat_tools


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


from tests._llm_fakes import FakeBlock, FakeResponse, FakeClient, text_block, tool_use  # noqa: F401


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

    def test_analyze_collects_queued_op_and_summary(self):
        client = FakeClient([
            FakeResponse([
                tool_use("create_card", {
                    "board": "alpha", "list": "backlog", "title": "New task",
                    "confidence": "high", "reason": "explicit ask",
                }),
            ]),
            FakeResponse([tool_use("finish", {"summary": "Meeting summary"})]),
        ])
        result = notes.analyze("note text", "Title", model="claude-opus-4-7", client=client)
        self.assertEqual(result["summary"], "Meeting summary")
        self.assertEqual(len(result["operations"]), 1)
        op = result["operations"][0]
        self.assertEqual(op["op"], "create_card")
        self.assertEqual(op["title"], "New task")
        self.assertTrue(result["note_id"].endswith("-title"))

    def test_analyze_archives_note(self):
        client = FakeClient([
            FakeResponse([tool_use("finish", {"summary": ""})]),
        ])
        result = notes.analyze("note body", "T", model="claude-opus-4-7", client=client)
        archived = (notes.NOTES_DIR / f"{result['note_id']}.md").read_text(encoding="utf-8")
        self.assertIn("note body", archived)

    def test_analyze_handles_read_tool_then_finish(self):
        make_board(self.data_dir, "alpha", "Alpha")
        make_card(self.data_dir, "alpha", "backlog", "spec",
            "---\ntitle: Spec\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\nthe spec\n\n\n## Checklist\n\n\n## Comments\n\n")
        client = FakeClient([
            FakeResponse([tool_use("search_cards", {"query": "spec"})]),
            FakeResponse([tool_use("finish", {"summary": "nothing to do"})]),
        ])
        result = notes.analyze("body", "T", model="claude-opus-4-7", client=client)
        self.assertEqual(result["operations"], [])
        # Second call's messages should include the tool_result from the search.
        second_msgs = client.messages.calls[1]["messages"]
        last_user = second_msgs[-1]
        self.assertEqual(last_user["role"], "user")
        self.assertEqual(last_user["content"][0]["type"], "tool_result")

    def test_analyze_no_tool_use_breaks_loop(self):
        # Model produces no tool_use blocks at all and no proposed ops -> raises.
        client = FakeClient([FakeResponse([text_block("I'm confused.")])])
        with self.assertRaises(notes.LLMResponseError):
            notes.analyze("body", "T", model="claude-opus-4-7", client=client)

    def test_analyze_max_turns_with_queued_op_returns_partial(self):
        # Model loops without finishing but did queue an op — return what we have.
        client = FakeClient([
            FakeResponse([tool_use("create_card", {
                "board": "a", "list": "backlog", "title": "X",
                "confidence": "med", "reason": "y",
            })]),
            FakeResponse([tool_use("list_boards", {})]),
            FakeResponse([tool_use("list_boards", {})]),
        ])
        result = notes.analyze("body", "T", model="claude-opus-4-7",
                               client=client, max_turns=3)
        self.assertEqual(len(result["operations"]), 1)
        self.assertEqual(result["summary"], "")

    def test_analyze_passes_model_tools_and_caching(self):
        client = FakeClient([
            FakeResponse([tool_use("finish", {"summary": ""})]),
        ])
        notes.analyze("body", "T", model="claude-sonnet-4-6", client=client)
        kwargs = client.messages.calls[0]
        self.assertEqual(kwargs["model"], "claude-sonnet-4-6")
        self.assertIn("tools", kwargs)
        self.assertTrue(any(t["name"] == "create_card" for t in kwargs["tools"]))
        self.assertTrue(any(t["name"] == "finish" for t in kwargs["tools"]))
        sys_block = kwargs["system"][0]
        self.assertEqual(sys_block.get("cache_control"), {"type": "ephemeral"})
        # The board INDEX is the cached prefix in user content.
        index_block = kwargs["messages"][0]["content"][0]
        self.assertIn("BOARD INDEX", index_block["text"])
        self.assertEqual(index_block.get("cache_control"), {"type": "ephemeral"})


class TestAnalyzeStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"

    def tearDown(self):
        self.tmp.cleanup()

    def test_stream_emits_started_turn_tool_queued_finish_done(self):
        client = FakeClient([
            FakeResponse([tool_use("create_card", {
                "board": "alpha", "list": "backlog", "title": "Build it",
                "confidence": "high", "reason": "explicit",
            })]),
            FakeResponse([tool_use("finish", {"summary": "All clear."})]),
        ])
        events = list(notes.analyze_stream("body", "T",
                                            model="claude-opus-4-7", client=client))
        types = [e["type"] for e in events]
        # First event is started, last is done.
        self.assertEqual(types[0], "started")
        self.assertEqual(types[-1], "done")
        self.assertIn("turn", types)
        self.assertIn("tool", types)
        self.assertIn("queued", types)
        self.assertIn("finish", types)
        # Queued event carries the title.
        queued = next(e for e in events if e["type"] == "queued")
        self.assertEqual(queued["title"], "Build it")
        # Done carries the operations list.
        done = events[-1]
        self.assertEqual(len(done["operations"]), 1)
        self.assertEqual(done["summary"], "All clear.")

    def test_stream_read_tool_emits_result_summary(self):
        make_board(self.data_dir, "alpha", "Alpha")
        client = FakeClient([
            FakeResponse([tool_use("list_boards", {})]),
            FakeResponse([tool_use("finish", {"summary": ""})]),
        ])
        events = list(notes.analyze_stream("body", "T",
                                            model="claude-opus-4-7", client=client))
        result = next(e for e in events if e["type"] == "result")
        self.assertEqual(result["name"], "list_boards")
        self.assertIn("board", result["summary"])

    def test_stream_emits_error_when_model_gives_up(self):
        client = FakeClient([FakeResponse([text_block("idk")])])
        events = list(notes.analyze_stream("body", "T",
                                            model="claude-opus-4-7", client=client))
        self.assertEqual(events[-1]["type"], "error")


class TestBuildToc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir, "alpha", "Alpha Project")
        make_card(self.data_dir, "alpha", "backlog", "draft",
            "---\ntitle: Draft\nlabels: [doc]\ndue: 2026-05-01\nassignee: Me\n"
            "created: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\nlong text that should be omitted from the toc.\n\n\n"
            "## Checklist\n\n- [ ] hidden\n\n\n## Comments\n\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_toc_omits_description_and_checklist(self):
        toc = notes.build_toc()
        card = toc["boards"][0]["cards"][0]
        self.assertEqual(card["title"], "Draft")
        self.assertEqual(card["labels"], ["doc"])
        self.assertEqual(card["due"], "2026-05-01")
        self.assertNotIn("desc", card)
        self.assertNotIn("todo", card)
        self.assertNotIn("done", card)


class TestReadTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir, "alpha", "Alpha")
        make_board(self.data_dir, "beta", "Beta")
        make_card(self.data_dir, "alpha", "backlog", "draft-spec",
            "---\ntitle: Draft spec\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\nWrite the spec.\n\n\n"
            "## Checklist\n\n- [ ] Outline\n- [x] Title\n\n\n"
            "## Comments\n\n")
        make_card(self.data_dir, "beta", "in-progress", "ship-it",
            "---\ntitle: Ship it\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n\n## Comments\n\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_boards_returns_card_counts(self):
        out = chat_tools._tool_list_boards({})
        names = {b["slug"]: b for b in out["boards"]}
        self.assertEqual(names["alpha"]["card_count"], 1)
        self.assertEqual(names["beta"]["card_count"], 1)

    def test_list_cards_filters_by_list(self):
        out = chat_tools._tool_list_cards({"board": "alpha", "list": "backlog"})
        self.assertEqual(len(out["cards"]), 1)
        self.assertEqual(out["cards"][0]["s"], "draft-spec")

    def test_list_cards_unknown_board(self):
        self.assertIn("error", chat_tools._tool_list_cards({"board": "ghost"}))

    def test_search_cards_finds_substring(self):
        out = chat_tools._tool_search_cards({"query": "spec"})
        slugs = [m["s"] for m in out["matches"]]
        self.assertIn("draft-spec", slugs)

    def test_read_card_returns_split_checklist(self):
        out = chat_tools._tool_read_card({"board": "alpha", "list": "backlog", "slug": "draft-spec"})
        self.assertIn("Outline", out["checklist_todo"])
        self.assertIn("Title", out["checklist_done"])
        self.assertEqual(out["title"], "Draft spec")

    def test_read_card_missing(self):
        out = chat_tools._tool_read_card({"board": "alpha", "list": "backlog", "slug": "nope"})
        self.assertIn("error", out)


class TestApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        notes.NOTES_DIR = Path(self.tmp.name) / "notes"
        make_board(self.data_dir, "alpha")
        self.note_id = notes.archive_note("body", "Test")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_create_card(self):
        ops = [{"op": "create_card", "board": "alpha", "list": "backlog",
                "title": "New thing", "description": "Do it", "checklist": ["step 1"],
                "due": "2026-05-10", "assignee": "Me", "labels": ["a"]}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["skipped"], [])
        card = server.read_card("alpha", "backlog", "new-thing")
        self.assertIsNotNone(card)
        self.assertEqual(card["title"], "New thing")
        # source attachment present
        self.assertTrue(any(a["url"].endswith(self.note_id) for a in card.get("attachments", [])))
        # in _order.json
        order = json.loads((self.data_dir / "boards/alpha/backlog/_order.json").read_text())
        self.assertIn("new-thing", order)

    def test_apply_add_comment(self):
        make_card(self.data_dir, "alpha", "in-progress", "do-stuff",
            "---\ntitle: Do stuff\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n\n## Comments\n\n")
        ops = [{"op": "add_comment", "board": "alpha", "list": "in-progress",
                "card": "do-stuff", "text": "Vendor confirmed pricing."}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        card = server.read_card("alpha", "in-progress", "do-stuff")
        self.assertIn("Vendor confirmed pricing.", card["body"])
        self.assertIn(self.note_id, card["body"])  # source link in comment

    def test_apply_tick_checklist(self):
        make_card(self.data_dir, "alpha", "backlog", "task",
            "---\ntitle: T\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n"
            "## Checklist\n\n- [ ] Confirm hotel\n- [ ] Book flight\n\n\n"
            "## Comments\n\n")
        ops = [{"op": "tick_checklist", "board": "alpha", "list": "backlog",
                "card": "task", "item": "hotel"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        card = server.read_card("alpha", "backlog", "task")
        self.assertIn("- [x] Confirm hotel", card["body"])
        self.assertIn("- [ ] Book flight", card["body"])

    def test_apply_skip_missing_target(self):
        ops = [{"op": "add_comment", "board": "alpha", "list": "backlog",
                "card": "does-not-exist", "text": "x"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("missing", result["skipped"][0]["reason"])

    def test_apply_skip_tick_item_not_found(self):
        make_card(self.data_dir, "alpha", "backlog", "task2",
            "---\ntitle: T\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n- [ ] Item A\n\n\n## Comments\n\n")
        ops = [{"op": "tick_checklist", "board": "alpha", "list": "backlog",
                "card": "task2", "item": "nonexistent"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_apply_move_card(self):
        make_card(self.data_dir, "alpha", "backlog", "movable",
            "---\ntitle: M\ncreated: 2026-04-20\nupdated: 2026-04-20\n---\n\n"
            "## Description\n\n\n\n## Checklist\n\n\n## Comments\n\n")
        ops = [{"op": "move_card", "board": "alpha", "list": "backlog",
                "card": "movable", "target_list": "in-progress"}]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 1)
        self.assertIsNone(server.read_card("alpha", "backlog", "movable"))
        self.assertIsNotNone(server.read_card("alpha", "in-progress", "movable"))

    def test_apply_records_in_note_frontmatter(self):
        ops = [{"op": "create_card", "board": "alpha", "list": "backlog", "title": "X"}]
        notes.apply_operations(ops, self.note_id)
        archived = (notes.NOTES_DIR / f"{self.note_id}.md").read_text()
        self.assertIn("create_card", archived)
        self.assertIn("alpha/backlog/x", archived)

    def test_apply_continues_on_partial_failure(self):
        ops = [
            {"op": "create_card", "board": "alpha", "list": "backlog", "title": "Good"},
            {"op": "add_comment", "board": "alpha", "list": "backlog", "card": "ghost", "text": "x"},
            {"op": "create_card", "board": "alpha", "list": "ideas", "title": "Also good"},
        ]
        result = notes.apply_operations(ops, self.note_id)
        self.assertEqual(len(result["applied"]), 2)
        self.assertEqual(len(result["skipped"]), 1)


if __name__ == "__main__":
    unittest.main()
