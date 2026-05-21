#!/usr/bin/env python3
"""Tests for memory_context — block budgeting, INDEX ordering, on-demand read."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_config
import memory_context
import memory_store


class _MemoryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name) / "memory"
        self.orig_get = memory_config.get_memory_dir
        memory_config.get_memory_dir = lambda: self.tmp_path

    def tearDown(self):
        memory_config.get_memory_dir = self.orig_get
        self.tmp.cleanup()


class TestLoadMemoryContext(_MemoryFixture):
    def test_empty_dir_returns_empty_list(self):
        # No init_if_missing → no README/INDEX → no blocks.
        self.assertEqual(memory_context.load_memory_context(), [])

    def test_seeded_includes_readme_index_and_pages(self):
        memory_store.init_if_missing()
        blocks = memory_context.load_memory_context()
        texts = [b["text"] for b in blocks]
        self.assertTrue(any(t.startswith("# MEMORY: README") for t in texts))
        self.assertTrue(any(t.startswith("# MEMORY: INDEX") for t in texts))
        # All three seed pages fit easily under the 200-line budget.
        names = [t.split("\n")[0] for t in texts]
        self.assertIn("# MEMORY: internal-organization.md", names)
        self.assertIn("# MEMORY: who-is-who.md", names)
        self.assertIn("# MEMORY: work-preferences.md", names)

    def test_only_last_block_is_cached(self):
        # Anthropic limits cache_control breakpoints to 4 per request. One
        # at the end of memory caches the whole prefix and leaves the rest
        # of the budget for the caller.
        memory_store.init_if_missing()
        blocks = memory_context.load_memory_context()
        self.assertTrue(len(blocks) > 1)
        for b in blocks[:-1]:
            self.assertNotIn("cache_control", b)
        self.assertEqual(blocks[-1].get("cache_control"), {"type": "ephemeral"})

    def test_budget_stops_loading_pages(self):
        memory_store.init_if_missing()
        # Replace one seed page with content far exceeding the budget so the
        # next page in INDEX order should be skipped.
        big = "\n".join(f"line {i}" for i in range(memory_context.LINE_BUDGET + 50))
        memory_store.write_page("internal-organization", big)
        blocks = memory_context.load_memory_context()
        texts = [b["text"] for b in blocks]
        # internal-organization comes first in INDEX; it should be loaded.
        self.assertTrue(any(t.startswith("# MEMORY: internal-organization") for t in texts))
        # who-is-who and work-preferences come after; budget should have been hit.
        self.assertFalse(any(t.startswith("# MEMORY: who-is-who") for t in texts))
        self.assertFalse(any(t.startswith("# MEMORY: work-preferences") for t in texts))

    def test_pages_not_in_index_are_excluded(self):
        memory_store.init_if_missing()
        memory_store.write_page("not-in-index", "# secret\n")
        blocks = memory_context.load_memory_context()
        texts = [b["text"] for b in blocks]
        self.assertFalse(any("not-in-index" in t for t in texts))

    def test_index_order_is_respected(self):
        memory_store.init_if_missing()
        # Rewrite INDEX with a reversed order; verify load order matches.
        (self.tmp_path / "INDEX.md").write_text(
            "# INDEX\n\n"
            "- [work-preferences](work-preferences.md)\n"
            "- [who-is-who](who-is-who.md)\n"
            "- [internal-organization](internal-organization.md)\n",
            encoding="utf-8",
        )
        blocks = memory_context.load_memory_context()
        page_blocks = [b["text"] for b in blocks if b["text"].startswith("# MEMORY: ") and ".md" in b["text"].splitlines()[0]]
        # The README and INDEX blocks come first; page blocks follow in INDEX order.
        self.assertEqual(page_blocks[0].splitlines()[0], "# MEMORY: work-preferences.md")
        self.assertEqual(page_blocks[1].splitlines()[0], "# MEMORY: who-is-who.md")
        self.assertEqual(page_blocks[2].splitlines()[0], "# MEMORY: internal-organization.md")


class TestReadMemoryPageTool(_MemoryFixture):
    def setUp(self):
        super().setUp()
        memory_store.init_if_missing()

    def test_returns_content_for_known_page(self):
        out = memory_context.tool_read_memory_page({"name": "internal-organization"})
        self.assertIn("content", out)
        self.assertIn("Internal organization", out["content"])

    def test_returns_error_for_unknown_page(self):
        out = memory_context.tool_read_memory_page({"name": "ghost"})
        self.assertIn("error", out)

    def test_invalid_slug_returns_error(self):
        out = memory_context.tool_read_memory_page({"name": "../escape"})
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
