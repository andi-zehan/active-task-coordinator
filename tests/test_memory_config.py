#!/usr/bin/env python3
"""Tests for memory_config — per-user memory system config."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import memory_config


class TestMemoryConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.orig_dir = memory_config.CONFIG_DIR
        self.orig_path = memory_config.CONFIG_PATH
        memory_config.CONFIG_DIR = self.tmp_path / ".atc"
        memory_config.CONFIG_PATH = memory_config.CONFIG_DIR / "memory-config.json"

    def tearDown(self):
        memory_config.CONFIG_DIR = self.orig_dir
        memory_config.CONFIG_PATH = self.orig_path
        self.tmp.cleanup()

    def test_load_defaults_when_missing(self):
        cfg = memory_config.load()
        self.assertEqual(cfg["memory_dir"], str(memory_config.DEFAULT_MEMORY_DIR))
        self.assertTrue(cfg["lint_enabled"])
        self.assertEqual(cfg["lint_interval_hours"], 1)
        self.assertEqual(cfg["max_pending_proposals"], 3)

    def test_save_then_load_roundtrip(self):
        target = self.tmp_path / "mem"
        memory_config.save({
            "memory_dir": str(target),
            "lint_enabled": False,
            "lint_interval_hours": 4,
            "max_pending_proposals": 5,
        })
        cfg = memory_config.load()
        self.assertEqual(cfg["memory_dir"], str(target))
        self.assertFalse(cfg["lint_enabled"])
        self.assertEqual(cfg["lint_interval_hours"], 4)
        self.assertEqual(cfg["max_pending_proposals"], 5)

    def test_save_creates_config_dir(self):
        self.assertFalse(memory_config.CONFIG_DIR.exists())
        memory_config.save({"lint_enabled": False})
        self.assertTrue(memory_config.CONFIG_PATH.exists())

    def test_save_unknown_key_ignored(self):
        memory_config.save({"memory_dir": str(self.tmp_path), "rogue": "x"})
        stored = json.loads(memory_config.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("rogue", stored)

    def test_get_memory_dir_expands_user(self):
        memory_config.save({"memory_dir": "~/my-mem"})
        self.assertEqual(memory_config.get_memory_dir(), Path.home() / "my-mem")

    def test_validate_rejects_empty_memory_dir(self):
        with self.assertRaises(memory_config.ValidationError):
            memory_config.validate({"memory_dir": ""})

    def test_validate_rejects_file_at_memory_dir(self):
        f = self.tmp_path / "afile"
        f.write_text("x")
        with self.assertRaises(memory_config.ValidationError):
            memory_config.validate({"memory_dir": str(f)})

    def test_validate_accepts_writable_path(self):
        memory_config.validate({"memory_dir": str(self.tmp_path / "new")})

    def test_validate_rejects_bad_lint_interval(self):
        for bad in (0, -1, 200, "1", True, 1.5):
            with self.assertRaises(memory_config.ValidationError):
                memory_config.validate({"lint_interval_hours": bad})

    def test_validate_accepts_lint_interval_bounds(self):
        memory_config.validate({"lint_interval_hours": 1})
        memory_config.validate({"lint_interval_hours": 168})

    def test_validate_rejects_bad_max_pending(self):
        for bad in (0, -1, 100, "3", True):
            with self.assertRaises(memory_config.ValidationError):
                memory_config.validate({"max_pending_proposals": bad})

    def test_validate_rejects_non_bool_lint_enabled(self):
        with self.assertRaises(memory_config.ValidationError):
            memory_config.validate({"lint_enabled": "yes"})

    def test_public_view_returns_expected_keys(self):
        view = memory_config.public_view()
        self.assertEqual(
            set(view.keys()),
            {"memory_dir", "lint_enabled", "lint_interval_hours", "max_pending_proposals"},
        )

    def test_sanitize_drops_unknown_keys(self):
        out = memory_config.sanitize_user_updates({"lint_enabled": True, "stowaway": 1})
        self.assertEqual(out, {"lint_enabled": True})


if __name__ == "__main__":
    unittest.main()
