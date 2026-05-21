#!/usr/bin/env python3
"""Tests for memory_ops — apply-side handlers for memory write ops + proposals."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_config
import memory_ops
import memory_store


class _MemoryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name) / "memory"
        self.orig_get = memory_config.get_memory_dir
        memory_config.get_memory_dir = lambda: self.tmp_path
        memory_store.init_if_missing()

    def tearDown(self):
        memory_config.get_memory_dir = self.orig_get
        self.tmp.cleanup()

    def _log_text(self) -> str:
        return memory_store.read_recent_log(max_lines=100)


class TestSaveAsSource(_MemoryFixture):
    def test_writes_source_and_logs_ingest(self):
        op = {"op": "save_as_source", "title": "Org chart",
              "content": "ceo > anna > brent"}
        result = memory_ops.apply_memory_operations([op])
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["skipped"]), 0)
        target = result["applied"][0]["target"]
        self.assertTrue(target.startswith("_sources/"))
        source_id = target.split("/", 1)[1]
        self.assertEqual(memory_store.read_source(source_id), "ceo > anna > brent")
        self.assertIn("INGEST", self._log_text())
        self.assertIn(source_id, self._log_text())

    def test_empty_content_skipped(self):
        op = {"op": "save_as_source", "title": "x", "content": "  "}
        result = memory_ops.apply_memory_operations([op])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)


class TestProposeMemoryEdits(_MemoryFixture):
    def test_apply_writes_pages_directly(self):
        # Chat-driven propose_memory_edits applies straight to wiki pages —
        # no proposal file, no second-stage review. Per-edit review happens
        # in the chat panel before Apply.
        op = {
            "op": "propose_memory_edits",
            "summary": "Add anna and brent",
            "edits": [
                {"page": "internal-organization", "action": "replace",
                 "content": "# Org\n\n- ceo\n- anna\n- brent\n"},
            ],
        }
        result = memory_ops.apply_memory_operations([op])
        self.assertEqual(len(result["applied"]), 1)
        target = result["applied"][0]["target"]
        self.assertTrue(target.startswith("memory/"))
        # No proposal file written.
        self.assertEqual(memory_store.list_proposals(), [])
        # Wiki page was updated.
        page = memory_store.read_page("internal-organization")
        self.assertIn("anna", page.lower())
        # EDIT log line recorded.
        self.assertIn("via=chat", self._log_text())

    def test_partial_batch_is_atomic_on_create_conflict(self):
        # If one edit in the batch would fail (create when page exists),
        # nothing in the batch is written — avoids leaving the wiki half-edited.
        op = {
            "op": "propose_memory_edits", "summary": "mixed",
            "edits": [
                {"page": "new-page", "action": "create", "content": "fresh"},
                {"page": "internal-organization", "action": "create", "content": "X"},
            ],
        }
        result = memory_ops.apply_memory_operations([op])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)
        # Neither edit landed.
        self.assertIsNone(memory_store.read_page("new-page"))
        self.assertNotIn("X", memory_store.read_page("internal-organization"))

    def test_no_edits_skipped(self):
        op = {"op": "propose_memory_edits", "summary": "nothing", "edits": []}
        result = memory_ops.apply_memory_operations([op])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_invalid_action_skipped(self):
        op = {
            "op": "propose_memory_edits", "summary": "bad",
            "edits": [{"page": "p", "action": "delete", "content": ""}],
        }
        result = memory_ops.apply_memory_operations([op])
        self.assertEqual(result["applied"], [])

    def test_unsafe_page_slug_rejected(self):
        op = {
            "op": "propose_memory_edits", "summary": "evil",
            "edits": [{"page": "../etc/passwd", "action": "create", "content": "x"}],
        }
        result = memory_ops.apply_memory_operations([op])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)


class TestUnknownOp(_MemoryFixture):
    def test_unknown_op_skipped(self):
        result = memory_ops.apply_memory_operations([{"op": "not_a_real_op"}])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)


class TestApplyProposal(_MemoryFixture):
    def _queue_proposal(self, edits):
        # apply_proposal is the lint review path — chat no longer writes
        # proposal files. Mirror what memory_lint.lint_stream does: serialize
        # the edits and write a proposal file via memory_store directly.
        body = memory_ops._serialize_proposal("test batch", edits, source="lint")
        return memory_store.write_proposal(body, kind="lint")

    def test_apply_all_writes_pages_and_deletes_proposal(self):
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "replace",
             "content": "# Org\n\n- anna\n"},
            {"page": "new-page", "action": "create",
             "content": "# brand new\n"},
        ])
        result = memory_ops.apply_proposal(pid, [0, 1])
        self.assertEqual(len(result["applied"]), 2)
        self.assertIn("anna", memory_store.read_page("internal-organization"))
        self.assertEqual(memory_store.read_page("new-page"), "# brand new\n")
        self.assertIsNone(memory_store.read_proposal(pid))
        log = self._log_text()
        self.assertEqual(log.count("EDIT"), 2)

    def test_partial_apply_drops_rejected(self):
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "replace", "content": "ACCEPTED"},
            {"page": "who-is-who", "action": "replace", "content": "REJECTED"},
        ])
        result = memory_ops.apply_proposal(pid, [0])
        self.assertEqual(len(result["applied"]), 1)
        self.assertIn("ACCEPTED", memory_store.read_page("internal-organization"))
        # Rejected edit was never written — original seed content remains.
        self.assertIn("Who is who", memory_store.read_page("who-is-who"))
        self.assertIsNone(memory_store.read_proposal(pid))

    def test_apply_empty_dismisses_without_writing(self):
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "replace", "content": "X"},
        ])
        result = memory_ops.apply_proposal(pid, [])
        self.assertEqual(result["applied"], [])
        self.assertIsNone(memory_store.read_proposal(pid))
        self.assertNotIn("\nX", memory_store.read_page("internal-organization"))

    def test_apply_out_of_range_index_skipped(self):
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "replace", "content": "X"},
        ])
        result = memory_ops.apply_proposal(pid, [5])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_create_existing_skipped(self):
        # Seed page already exists → create should be skipped.
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "create", "content": "X"},
        ])
        result = memory_ops.apply_proposal(pid, [0])
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_append_concatenates(self):
        original = memory_store.read_page("internal-organization")
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "append", "content": "extra line"},
        ])
        memory_ops.apply_proposal(pid, [0])
        new = memory_store.read_page("internal-organization")
        self.assertTrue(new.startswith(original.rstrip("\n")))
        self.assertIn("extra line", new)

    def test_edit_overrides_replace_content(self):
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "replace",
             "content": "ORIGINAL"},
            {"page": "who-is-who", "action": "replace",
             "content": "ORIGINAL"},
        ])
        # Override edit 0 with new text, leave edit 1 at the proposal's value.
        result = memory_ops.apply_proposal(pid, [0, 1], {"0": "USER EDITED"})
        self.assertEqual(len(result["applied"]), 2)
        self.assertIn("USER EDITED", memory_store.read_page("internal-organization"))
        self.assertNotIn("ORIGINAL", memory_store.read_page("internal-organization"))
        self.assertIn("ORIGINAL", memory_store.read_page("who-is-who"))

    def test_edit_overrides_ignored_for_rejected_edits(self):
        pid = self._queue_proposal([
            {"page": "internal-organization", "action": "replace", "content": "X"},
        ])
        # Override is for index 0, but we don't accept it — nothing written.
        result = memory_ops.apply_proposal(pid, [], {"0": "ignored"})
        self.assertEqual(result["applied"], [])
        self.assertNotIn("ignored", memory_store.read_page("internal-organization"))

    def test_edit_overrides_must_be_dict(self):
        pid = self._queue_proposal([
            {"page": "p", "action": "create", "content": "c"},
        ])
        with self.assertRaises(ValueError):
            memory_ops.apply_proposal(pid, [0], "not a dict")

    def test_unknown_proposal_raises(self):
        with self.assertRaises(ValueError):
            memory_ops.apply_proposal("does-not-exist", [])


class TestDismissProposal(_MemoryFixture):
    def test_dismiss_removes_file(self):
        # Write a lint-style proposal directly (chat no longer writes proposal files).
        body = memory_ops._serialize_proposal("x", [
            {"page": "p", "action": "create", "content": "c"},
        ], source="lint")
        pid = memory_store.write_proposal(body, kind="lint")
        self.assertTrue(memory_ops.dismiss_proposal(pid))
        self.assertFalse(memory_ops.dismiss_proposal(pid))


class TestManualWritePage(_MemoryFixture):
    def test_manual_write_logs_edit(self):
        memory_ops.manual_write_page("internal-organization", "# hand edit\n")
        self.assertEqual(memory_store.read_page("internal-organization"), "# hand edit\n")
        log = self._log_text()
        self.assertIn("EDIT", log)
        self.assertIn("via=manual", log)


class TestParseProposal(_MemoryFixture):
    def test_roundtrip(self):
        body = memory_ops._serialize_proposal("round", [
            {"page": "p", "action": "create", "content": "x"},
        ], source="lint")
        memory_store.write_proposal(body, kind="lint")
        pid = memory_store.list_proposals()[0]["id"]
        raw = memory_store.read_proposal(pid)
        parsed = memory_ops.parse_proposal(raw)
        self.assertEqual(parsed["summary"], "round")
        self.assertEqual(parsed["edits"][0]["page"], "p")

    def test_garbage_returns_none(self):
        self.assertIsNone(memory_ops.parse_proposal("nothing here"))


class TestSplitMemoryOps(_MemoryFixture):
    def test_partition(self):
        ops = [
            {"op": "create_card", "title": "x"},
            {"op": "save_as_source", "title": "y", "content": "z"},
            {"op": "add_comment", "id": "C-1", "text": "n"},
            {"op": "propose_memory_edits", "summary": "s",
             "edits": [{"page": "p", "action": "create", "content": "c"}]},
        ]
        card_ops, mem_ops = memory_ops.split_memory_ops(ops)
        self.assertEqual([o["op"] for o in card_ops], ["create_card", "add_comment"])
        self.assertEqual([o["op"] for o in mem_ops], ["save_as_source", "propose_memory_edits"])

    def test_pure_memory_batch(self):
        ops = [{"op": "save_as_source", "title": "y", "content": "z"}]
        card_ops, mem_ops = memory_ops.split_memory_ops(ops)
        self.assertEqual(card_ops, [])
        self.assertEqual(len(mem_ops), 1)


if __name__ == "__main__":
    unittest.main()
