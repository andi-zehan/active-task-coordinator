#!/usr/bin/env python3
"""Tests for chat module."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import chat
from tests._llm_fakes import FakeClient, FakeResponse, text_block, tool_use


class TestChatStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_only_response_terminates(self):
        client = FakeClient([FakeResponse([text_block("hello")])])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "hi"}],
            model="claude-opus-4-7", client=client,
        ))
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "started")
        self.assertIn("text", types)
        self.assertEqual(types[-1], "done")
        text_evt = next(e for e in events if e["type"] == "text")
        self.assertEqual(text_evt["text"], "hello")

    def test_tool_then_text(self):
        client = FakeClient([
            FakeResponse([tool_use("list_boards", {})]),
            FakeResponse([text_block("you have N boards")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "how many boards?"}],
            model="claude-opus-4-7", client=client,
        ))
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "started")
        self.assertIn("tool", types)
        self.assertIn("result", types)
        self.assertIn("text", types)
        self.assertEqual(types[-1], "done")

    def test_queued_write_op_appears_in_done(self):
        client = FakeClient([
            FakeResponse([tool_use("create_card", {
                "board": "alpha", "list": "backlog", "title": "X",
                "confidence": "high", "reason": "y",
            })]),
            FakeResponse([text_block("queued it for you")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "make X"}],
            model="claude-opus-4-7", client=client,
        ))
        queued = [e for e in events if e["type"] == "queued"]
        self.assertEqual(len(queued), 1)
        # queued event carries the full args dict so the UI can apply it as-is.
        self.assertEqual(queued[0]["op"], "create_card")
        self.assertEqual(queued[0]["args"]["title"], "X")
        self.assertEqual(queued[0]["args"]["board"], "alpha")
        self.assertEqual(queued[0]["args"]["list"], "backlog")
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(len(done["proposed_operations"]), 1)
        self.assertEqual(done["proposed_operations"][0]["op"], "create_card")

    def test_max_turns_terminates(self):
        client = FakeClient([
            FakeResponse([tool_use("list_boards", {})]) for _ in range(10)
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "spin"}],
            model="claude-opus-4-7", client=client, max_turns=3,
        ))
        self.assertEqual(events[-1]["type"], "done")

    def test_tool_error_recovers(self):
        # list_cards with no 'board' arg → KeyError caught and reported.
        client = FakeClient([
            FakeResponse([tool_use("list_cards", {})]),
            FakeResponse([text_block("recovered")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "broken call"}],
            model="claude-opus-4-7", client=client,
        ))
        result_evts = [e for e in events if e["type"] == "result"]
        self.assertTrue(any("error" in r["summary"] for r in result_evts))
        self.assertEqual(events[-1]["type"], "done")

    def test_done_messages_appended_grows_history(self):
        client = FakeClient([
            FakeResponse([tool_use("list_boards", {})]),
            FakeResponse([text_block("done")]),
        ])
        events = list(chat.chat_stream(
            [{"role": "user", "content": "hi"}],
            model="claude-opus-4-7", client=client,
        ))
        done = events[-1]
        # assistant tool-call turn, user tool_result turn, assistant text turn
        roles = [m["role"] for m in done["messages_appended"]]
        self.assertEqual(roles, ["assistant", "user", "assistant"])


if __name__ == "__main__":
    unittest.main()
