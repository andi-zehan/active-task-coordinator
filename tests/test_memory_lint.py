#!/usr/bin/env python3
"""Tests for memory_lint — skip gates + lint pass produces proposals."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_config
import memory_lint
import memory_store

from tests._llm_fakes import FakeClient, FakeResponse, text_block, tool_use


class _MemoryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name) / "memory"
        self.orig_get = memory_config.get_memory_dir
        memory_config.get_memory_dir = lambda: self.tmp_path
        # Also stub config.load so lint sees deterministic values.
        self.orig_load = memory_config.load
        memory_config.load = lambda: {
            "memory_dir": str(self.tmp_path),
            "lint_enabled": True,
            "lint_interval_hours": 1,
            "max_pending_proposals": 3,
        }
        memory_store.init_if_missing()

    def tearDown(self):
        memory_config.get_memory_dir = self.orig_get
        memory_config.load = self.orig_load
        self.tmp.cleanup()


class TestShouldRun(_MemoryFixture):
    def test_runs_on_fresh_state(self):
        run, _ = memory_lint.should_run()
        self.assertTrue(run)

    def test_skips_when_disabled(self):
        memory_config.load = lambda: {
            "memory_dir": str(self.tmp_path),
            "lint_enabled": False,
            "lint_interval_hours": 1,
            "max_pending_proposals": 3,
        }
        run, reason = memory_lint.should_run()
        self.assertFalse(run)
        self.assertIn("lint_enabled", reason)

    def test_skips_when_proposals_pile_up(self):
        for _ in range(3):
            memory_store.write_proposal("body", kind="chat")
        run, reason = memory_lint.should_run()
        self.assertFalse(run)
        self.assertIn("pending", reason)

    def test_skips_when_fingerprint_unchanged(self):
        # Pin the marker to the current fingerprint and verify skip.
        memory_lint._write_last_fingerprint(memory_store.memory_fingerprint())
        run, reason = memory_lint.should_run()
        self.assertFalse(run)
        self.assertIn("unchanged", reason)

    def test_reruns_after_edit(self):
        memory_lint._write_last_fingerprint(memory_store.memory_fingerprint())
        time.sleep(0.01)
        memory_store.write_page("internal-organization", "# new\n")
        run, _ = memory_lint.should_run()
        self.assertTrue(run)


class TestLintStream(_MemoryFixture):
    def _run(self, responses):
        client = FakeClient(responses)
        events = list(memory_lint.lint_stream(model="m", client=client))
        return events, client

    def test_clean_pass_produces_no_proposal(self):
        # Lint immediately calls finish_lint with summary, no edits proposed.
        responses = [FakeResponse([tool_use("finish_lint", {"summary": "all good"})])]
        events, _ = self._run(responses)
        types = [e["type"] for e in events]
        self.assertIn("started", types)
        self.assertIn("finish", types)
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertIsNone(done["proposal_id"])
        self.assertEqual(done["edit_count"], 0)
        self.assertEqual(len(memory_store.list_proposals()), 0)
        # Fingerprint should still advance so we don't re-lint immediately.
        self.assertTrue(memory_lint._read_last_fingerprint())

    def test_proposal_pass_writes_proposal_file(self):
        edits = [
            {"page": "internal-organization", "action": "replace",
             "content": "# Org\n\n- anna\n"},
        ]
        responses = [
            FakeResponse([
                text_block("I see an empty org page; suggesting an update."),
                tool_use("propose_memory_edits", {
                    "summary": "Fill org chart from source",
                    "edits": edits,
                    "confidence": "med",
                    "reason": "anna mentioned in recent source",
                }),
            ]),
            FakeResponse([tool_use("finish_lint", {"summary": "1 edit proposed"})]),
        ]
        events, _ = self._run(responses)
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertIsNotNone(done["proposal_id"])
        self.assertEqual(done["edit_count"], 1)
        proposals = memory_store.list_proposals()
        self.assertEqual(len(proposals), 1)
        # Proposal should be tagged 'lint' in its id.
        self.assertTrue(proposals[0]["id"].endswith("-lint"))

    def test_skipped_when_gate_fails(self):
        # Saturate pending proposals so the gate fires.
        for _ in range(3):
            memory_store.write_proposal("body", kind="chat")
        client = FakeClient([])
        events = list(memory_lint.lint_stream(model="m", client=client))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "skipped")
        # FakeClient was never invoked.
        self.assertEqual(client.messages.calls, [])


if __name__ == "__main__":
    unittest.main()
