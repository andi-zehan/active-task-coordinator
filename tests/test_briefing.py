#!/usr/bin/env python3
"""Tests for the AI Briefing flow."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import briefing
import notes
from tests._llm_fakes import FakeClient, FakeResponse, text_block, tool_use


class TestBriefingAnalyzeStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        server.reset_id_index()

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_then_finish_returns_briefing(self):
        # Model writes the briefing as a text block, then calls finish.
        client = FakeClient([
            FakeResponse([
                text_block("## Today's Top 5\n\n1. Ship release"),
                tool_use("finish", {"summary": "five priorities identified"}),
            ]),
        ])
        events = list(briefing.analyze_stream(
            "What should I work on?", model="claude-opus-4-7", client=client,
        ))
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "started")
        self.assertIn("text", types)
        self.assertIn("finish", types)
        self.assertEqual(types[-1], "done")
        done = events[-1]
        self.assertTrue(done["briefing_id"].startswith("briefing-"))
        self.assertEqual(done["summary"], "five priorities identified")
        self.assertIn("Top 5", done["text"])
        self.assertEqual(done["operations"], [])

    def test_text_chunks_concatenate_across_turns(self):
        # If the model emits text across two turns (e.g. between tool calls),
        # the final 'done' text should contain both chunks in order.
        client = FakeClient([
            FakeResponse([
                text_block("Part one. "),
                tool_use("list_overdue", {}),
            ]),
            FakeResponse([
                text_block("Part two."),
                tool_use("finish", {"summary": "done"}),
            ]),
        ])
        events = list(briefing.analyze_stream(
            "brief me", model="claude-opus-4-7", client=client,
        ))
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["text"], "Part one. Part two.")

    def test_queued_write_op_is_captured(self):
        client = FakeClient([
            FakeResponse([
                text_block("Suggested cleanup:"),
                tool_use("create_card", {
                    "board": "alpha", "list": "backlog", "title": "Follow-up",
                    "confidence": "high", "reason": "from briefing",
                }),
                tool_use("finish", {"summary": "one cleanup"}),
            ]),
        ])
        events = list(briefing.analyze_stream(
            "do a sweep", model="claude-opus-4-7", client=client,
        ))
        queued = [e for e in events if e["type"] == "queued"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["op"], "create_card")
        done = events[-1]
        self.assertEqual(len(done["operations"]), 1)
        self.assertEqual(done["operations"][0]["op"], "create_card")
        self.assertEqual(done["operations"][0]["title"], "Follow-up")

    def test_event_order_started_turn_tool_result_finish_done(self):
        client = FakeClient([
            FakeResponse([
                tool_use("list_overdue", {}),
            ]),
            FakeResponse([
                text_block("nothing overdue"),
                tool_use("finish", {"summary": "ok"}),
            ]),
        ])
        events = list(briefing.analyze_stream(
            "any overdue?", model="claude-opus-4-7", client=client,
        ))
        types = [e["type"] for e in events]
        # Must have: started, then turn(s), then tool/result pairs interleaved
        # with text, ending in finish + done.
        self.assertEqual(types[0], "started")
        self.assertEqual(types[-1], "done")
        self.assertIn("turn", types)
        self.assertIn("tool", types)
        self.assertIn("result", types)
        self.assertIn("finish", types)

    def test_empty_response_yields_error(self):
        # Model produces no text, no ops, no finish — analyze_stream must
        # surface this as an error rather than a happy 'done'.
        client = FakeClient([FakeResponse([])])
        events = list(briefing.analyze_stream(
            "empty", model="claude-opus-4-7", client=client,
        ))
        self.assertEqual(events[-1]["type"], "error")

    def test_tool_error_does_not_terminate_loop(self):
        # find_by_label with no 'label' arg → KeyError caught and reported.
        client = FakeClient([
            FakeResponse([tool_use("find_by_label", {})]),
            FakeResponse([
                text_block("recovered"),
                tool_use("finish", {"summary": "ok"}),
            ]),
        ])
        events = list(briefing.analyze_stream(
            "labels?", model="claude-opus-4-7", client=client,
        ))
        result_evts = [e for e in events if e["type"] == "result"]
        self.assertTrue(any("error" in r["summary"] for r in result_evts))
        self.assertEqual(events[-1]["type"], "done")


class TestBriefingRefineStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        server.reset_id_index()

    def tearDown(self):
        self.tmp.cleanup()

    def test_refine_replaces_text_and_ops(self):
        client = FakeClient([
            FakeResponse([
                text_block("Updated briefing focused on board X."),
                tool_use("finish", {"summary": "narrowed scope"}),
            ]),
        ])
        events = list(briefing.refine_stream(
            "briefing-2026-05-09-120000",
            current_ops=[{"op": "create_card", "board": "old", "list": "backlog",
                          "title": "stale"}],
            current_text="Original briefing text.",
            feedback="focus only on board X",
            model="claude-opus-4-7", client=client,
        ))
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["briefing_id"], "briefing-2026-05-09-120000")
        self.assertIn("board X", done["text"])
        # No re-emitted ops -> ops list is empty.
        self.assertEqual(done["operations"], [])

    def test_refine_handles_empty_previous_state(self):
        # User refines on a briefing that proposed nothing — must not crash
        # on the "(none — start fresh.)" path.
        client = FakeClient([
            FakeResponse([
                text_block("fresh briefing"),
                tool_use("finish", {"summary": "ok"}),
            ]),
        ])
        events = list(briefing.refine_stream(
            "briefing-test", current_ops=[], current_text="",
            feedback="redo from scratch",
            model="claude-opus-4-7", client=client,
        ))
        self.assertEqual(events[-1]["type"], "done")


class TestBriefingApplyOperations(unittest.TestCase):
    """apply_operations must run ops via notes._apply_op without recording into a note."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text('["alpha"]', encoding="utf-8")
        # Bootstrap one minimal board so create_card has a target.
        server.write_board_meta("alpha", {"name": "Alpha", "color": "#000"})
        for lst in server.LISTS:
            (self.data_dir / "boards" / "alpha" / lst).mkdir(parents=True, exist_ok=True)
            (self.data_dir / "boards" / "alpha" / lst / "_order.json").write_text(
                "[]", encoding="utf-8"
            )
        server.reset_id_index()

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_create_card_succeeds_without_note(self):
        result = briefing.apply_operations([{
            "op": "create_card",
            "board": "alpha",
            "list": "backlog",
            "title": "From briefing",
            "description": "auto",
            "checklist": [],
            "confidence": "high",
            "reason": "test",
        }])
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["applied"][0]["op"], "create_card")
        # Card file exists on disk; no source-note attachment because note_id=None.
        applied_target = result["applied"][0]["target"]
        # target == "alpha/backlog/C-1"
        _, _, card_id = applied_target.split("/")
        card = server.read_card("alpha", "backlog", card_id)
        self.assertIsNotNone(card)
        self.assertEqual(card["title"], "From briefing")
        self.assertEqual(card.get("attachments") or [], [])

    def test_apply_skips_unknown_op(self):
        result = briefing.apply_operations([{"op": "invent_an_op"}])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("unknown op", result["skipped"][0]["reason"])


class TestNotesApplyStillRecords(unittest.TestCase):
    """Regression: the notes flow must keep writing applied_ops into the note file
    after the _apply_op refactor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text('["alpha"]', encoding="utf-8")
        server.write_board_meta("alpha", {"name": "Alpha", "color": "#000"})
        for lst in server.LISTS:
            (self.data_dir / "boards" / "alpha" / lst).mkdir(parents=True, exist_ok=True)
            (self.data_dir / "boards" / "alpha" / lst / "_order.json").write_text(
                "[]", encoding="utf-8"
            )
        server.reset_id_index()
        # Park a synthetic note file so _record_in_note has something to write to.
        notes.NOTES_DIR.mkdir(parents=True, exist_ok=True)
        self.note_path = notes.NOTES_DIR / "n-test.md"
        self.note_path.write_text(
            "---\ntitle: t\napplied_ops: []\n---\n\nbody\n", encoding="utf-8"
        )

    def tearDown(self):
        if self.note_path.exists():
            self.note_path.unlink()
        self.tmp.cleanup()

    def test_record_in_note_runs_for_notes_flow(self):
        notes.apply_operations(
            [{
                "op": "create_card",
                "board": "alpha",
                "list": "backlog",
                "title": "From note",
                "description": "x",
                "checklist": [],
                "confidence": "high",
                "reason": "test",
            }],
            note_id="n-test",
        )
        text = self.note_path.read_text(encoding="utf-8")
        # The applied_ops list should have been populated.
        self.assertIn("applied_ops:", text)
        self.assertIn("create_card", text)


if __name__ == "__main__":
    unittest.main()
