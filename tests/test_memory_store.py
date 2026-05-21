#!/usr/bin/env python3
"""Tests for memory_store — on-disk layout, log rotation, slug safety."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_config
import memory_store


class _MemoryFixture(unittest.TestCase):
    """Base: swap memory_config.get_memory_dir() to a tempdir per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name) / "memory"
        self.orig_get = memory_config.get_memory_dir
        memory_config.get_memory_dir = lambda: self.tmp_path

    def tearDown(self):
        memory_config.get_memory_dir = self.orig_get
        self.tmp.cleanup()


class TestInit(_MemoryFixture):
    def test_creates_layout_and_seeds(self):
        created = memory_store.init_if_missing()
        self.assertTrue(created)
        self.assertTrue((self.tmp_path / "README.md").exists())
        self.assertTrue((self.tmp_path / "INDEX.md").exists())
        self.assertTrue((self.tmp_path / "log.md").exists())
        self.assertTrue((self.tmp_path / "_sources").is_dir())
        self.assertTrue((self.tmp_path / "_proposals").is_dir())
        self.assertTrue((self.tmp_path / "log").is_dir())
        self.assertTrue((self.tmp_path / "internal-organization.md").exists())
        self.assertTrue((self.tmp_path / "who-is-who.md").exists())
        self.assertTrue((self.tmp_path / "work-preferences.md").exists())

    def test_idempotent_does_not_overwrite(self):
        memory_store.init_if_missing()
        (self.tmp_path / "internal-organization.md").write_text("CUSTOM", encoding="utf-8")
        created = memory_store.init_if_missing()
        self.assertFalse(created)
        self.assertEqual(
            (self.tmp_path / "internal-organization.md").read_text(encoding="utf-8"),
            "CUSTOM",
        )


class TestPages(_MemoryFixture):
    def setUp(self):
        super().setUp()
        memory_store.init_if_missing()

    def test_list_excludes_reserved_files(self):
        names = {p["name"] for p in memory_store.list_pages()}
        self.assertEqual(names, {"internal-organization", "who-is-who", "work-preferences"})

    def test_read_with_and_without_extension(self):
        a = memory_store.read_page("internal-organization")
        b = memory_store.read_page("internal-organization.md")
        self.assertEqual(a, b)
        self.assertIsNotNone(a)

    def test_read_missing_returns_none(self):
        self.assertIsNone(memory_store.read_page("nope"))

    def test_write_then_read(self):
        memory_store.write_page("new-page", "# hi\n")
        self.assertEqual(memory_store.read_page("new-page"), "# hi\n")

    def test_invalid_slug_rejected(self):
        for bad in ("../escape", "with/slash", ".hidden", "..", "with space", ""):
            with self.assertRaises(memory_store.InvalidSlug):
                memory_store.read_page(bad)

    def test_write_invalid_slug_rejected(self):
        with self.assertRaises(memory_store.InvalidSlug):
            memory_store.write_page("../escape", "x")


class TestSources(_MemoryFixture):
    def setUp(self):
        super().setUp()
        memory_store.init_if_missing()

    def test_write_then_read(self):
        sid = memory_store.write_source("Org chart from Anna", "raw content")
        self.assertTrue(sid.endswith("-org-chart-from-anna"))
        self.assertEqual(memory_store.read_source(sid), "raw content")

    def test_collision_suffix(self):
        s1 = memory_store.write_source("same title", "a")
        s2 = memory_store.write_source("same title", "b")
        self.assertNotEqual(s1, s2)
        self.assertEqual(memory_store.read_source(s1), "a")
        self.assertEqual(memory_store.read_source(s2), "b")

    def test_empty_title_uses_untitled(self):
        sid = memory_store.write_source("", "content")
        self.assertIn("untitled", sid)

    def test_list_sources(self):
        memory_store.write_source("alpha", "1")
        memory_store.write_source("beta", "2")
        ids = {s["id"] for s in memory_store.list_sources()}
        self.assertEqual(len(ids), 2)


class TestProposals(_MemoryFixture):
    def setUp(self):
        super().setUp()
        memory_store.init_if_missing()

    def test_write_kind_in_id(self):
        pid = memory_store.write_proposal("body", kind="lint")
        self.assertTrue(pid.endswith("-lint"))

    def test_invalid_kind(self):
        with self.assertRaises(ValueError):
            memory_store.write_proposal("body", kind="bogus")

    def test_read_and_delete(self):
        pid = memory_store.write_proposal("body", kind="chat")
        self.assertEqual(memory_store.read_proposal(pid), "body")
        self.assertTrue(memory_store.delete_proposal(pid))
        self.assertIsNone(memory_store.read_proposal(pid))
        self.assertFalse(memory_store.delete_proposal(pid))

    def test_list_proposals_returns_all(self):
        memory_store.write_proposal("a", kind="chat")
        memory_store.write_proposal("b", kind="lint")
        self.assertEqual(len(memory_store.list_proposals()), 2)


class TestLog(_MemoryFixture):
    def setUp(self):
        super().setUp()
        memory_store.init_if_missing()

    def test_append_writes_line(self):
        memory_store.append_log("INGEST", "source=foo.md")
        log = (self.tmp_path / "log.md").read_text(encoding="utf-8")
        self.assertIn("INGEST", log)
        self.assertIn("source=foo.md", log)
        self.assertTrue(log.endswith("\n"))

    def test_append_rejects_unknown_prefix(self):
        with self.assertRaises(ValueError):
            memory_store.append_log("BOGUS", "x")

    def test_newlines_in_text_collapsed(self):
        memory_store.append_log("EDIT", "line1\nline2")
        log = (self.tmp_path / "log.md").read_text(encoding="utf-8")
        self.assertEqual(len(log.strip().splitlines()), 1)

    def test_rotation_at_threshold(self):
        log_path = self.tmp_path / "log.md"
        # Pre-fill with LOG_ROTATE_LINES lines so the next append triggers rotation.
        log_path.write_text("\n".join(f"old line {i}" for i in range(memory_store.LOG_ROTATE_LINES)) + "\n",
                            encoding="utf-8")
        memory_store.append_log("LINT", "fresh")
        # Archive should now hold the old content; live log holds only the fresh line.
        archives = list((self.tmp_path / "log").glob("*.md"))
        self.assertEqual(len(archives), 1)
        archive_text = archives[0].read_text(encoding="utf-8")
        self.assertIn("old line 0", archive_text)
        fresh = log_path.read_text(encoding="utf-8")
        self.assertNotIn("old line", fresh)
        self.assertIn("LINT", fresh)
        self.assertIn("fresh", fresh)

    def test_read_recent_tail(self):
        for i in range(10):
            memory_store.append_log("QUERY", f"q{i}")
        tail = memory_store.read_recent_log(max_lines=3)
        lines = tail.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("q9", lines[-1])


class TestFingerprint(_MemoryFixture):
    def setUp(self):
        super().setUp()
        memory_store.init_if_missing()

    def test_stable_when_nothing_changes(self):
        f1 = memory_store.memory_fingerprint()
        f2 = memory_store.memory_fingerprint()
        self.assertEqual(f1, f2)

    def test_changes_on_page_edit(self):
        f1 = memory_store.memory_fingerprint()
        # Force mtime to advance (Windows mtime resolution can be coarse).
        time.sleep(0.01)
        memory_store.write_page("internal-organization", "# changed\n")
        f2 = memory_store.memory_fingerprint()
        self.assertNotEqual(f1, f2)

    def test_changes_on_new_source(self):
        f1 = memory_store.memory_fingerprint()
        memory_store.write_source("x", "raw")
        f2 = memory_store.memory_fingerprint()
        self.assertNotEqual(f1, f2)

    def test_unchanged_when_only_proposals_change(self):
        f1 = memory_store.memory_fingerprint()
        memory_store.write_proposal("body", kind="lint")
        f2 = memory_store.memory_fingerprint()
        self.assertEqual(f1, f2)

    def test_empty_dir_returns_marker(self):
        # Point at a non-existent dir; fingerprint should not crash.
        memory_config.get_memory_dir = lambda: self.tmp_path / "does-not-exist"
        self.assertEqual(memory_store.memory_fingerprint(), "empty")


if __name__ == "__main__":
    unittest.main()
