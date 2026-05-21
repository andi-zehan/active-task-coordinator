#!/usr/bin/env python3
"""Tests for ask.build_context_payload — focus on the memory context type
not pulling in any card snapshot."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import ask
import server


class TestMemoryContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(self.tmp.name) / "data"
        server.ensure_data_dir()
        # Populate one board so we'd notice if cards leaked into the payload.
        (server.DATA_DIR / "_boards-order.json").write_text(
            json.dumps(["b1"]), encoding="utf-8")
        bdir = server.DATA_DIR / "boards" / "b1"
        bdir.mkdir(parents=True)
        (bdir / "board.json").write_text(json.dumps({"name": "B1"}), encoding="utf-8")
        for lst in server.LISTS:
            (bdir / lst).mkdir()
            (bdir / lst / "_order.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        server.DATA_DIR = self.orig_data_dir
        self.tmp.cleanup()

    def test_payload_has_no_card_data(self):
        payload = ask.build_context_payload({"type": "memory"})
        self.assertEqual(payload["context_type"], "memory")
        self.assertNotIn("boards", payload)
        self.assertNotIn("card", payload)
        self.assertNotIn("siblings", payload)

    def test_label_is_memory_wiki(self):
        payload = ask.build_context_payload({"type": "memory"})
        self.assertEqual(ask._label_for(payload), "memory wiki")

    def test_seed_message_skips_cache_breakpoint_and_omits_json(self):
        # The memory seed message is a short marker, not a JSON dump, and
        # carries no cache_control breakpoint (the wiki itself, loaded by
        # memory_context, owns the only memory breakpoint).
        payload = ask.build_context_payload({"type": "memory"})
        msg = ask.build_seed_message(payload, "update the orgchart")
        blocks = msg["content"]
        self.assertEqual(blocks[0]["type"], "text")
        self.assertIn("memory wiki", blocks[0]["text"])
        self.assertNotIn("cache_control", blocks[0])
        # No serialized board / card data leaked into the pinned block.
        self.assertNotIn("boards", blocks[0]["text"])
        self.assertNotIn("\"id\":", blocks[0]["text"])
        self.assertIn("update the orgchart", blocks[1]["text"])


if __name__ == "__main__":
    unittest.main()
