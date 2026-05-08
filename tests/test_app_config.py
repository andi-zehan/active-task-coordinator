#!/usr/bin/env python3
"""Tests for app_config — per-user config (data folder, first-run flags)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_config


class TestAppConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.orig_dir = app_config.CONFIG_DIR
        self.orig_path = app_config.CONFIG_PATH
        app_config.CONFIG_DIR = self.tmp_path / ".atc"
        app_config.CONFIG_PATH = app_config.CONFIG_DIR / "config.json"

    def tearDown(self):
        app_config.CONFIG_DIR = self.orig_dir
        app_config.CONFIG_PATH = self.orig_path
        self.tmp.cleanup()

    def test_load_defaults_when_missing(self):
        cfg = app_config.load()
        self.assertEqual(cfg["data_dir"], str(app_config.DEFAULT_DATA_DIR))
        self.assertFalse(cfg["seen_api_key_prompt"])

    def test_save_then_load_roundtrip(self):
        target = self.tmp_path / "cards"
        app_config.save({"data_dir": str(target), "seen_api_key_prompt": True})
        cfg = app_config.load()
        self.assertEqual(cfg["data_dir"], str(target))
        self.assertTrue(cfg["seen_api_key_prompt"])

    def test_save_creates_config_dir(self):
        self.assertFalse(app_config.CONFIG_DIR.exists())
        app_config.save({"seen_api_key_prompt": True})
        self.assertTrue(app_config.CONFIG_PATH.exists())

    def test_save_unknown_key_ignored(self):
        app_config.save({"data_dir": "/tmp/x", "rogue": "value"})
        stored = json.loads(app_config.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("rogue", stored)

    def test_get_data_dir_returns_path(self):
        target = self.tmp_path / "cards"
        app_config.save({"data_dir": str(target)})
        self.assertEqual(app_config.get_data_dir(), target)

    def test_get_data_dir_expands_user(self):
        app_config.save({"data_dir": "~/my-cards"})
        self.assertEqual(app_config.get_data_dir(), Path.home() / "my-cards")

    def test_validate_accepts_writable_path(self):
        target = self.tmp_path / "ok"
        app_config.validate({"data_dir": str(target)})
        # Validation must not leave the probe file behind.
        self.assertEqual(list(target.iterdir()), [])

    def test_validate_rejects_empty_string(self):
        with self.assertRaises(app_config.ValidationError):
            app_config.validate({"data_dir": ""})

    def test_validate_rejects_non_string(self):
        with self.assertRaises(app_config.ValidationError):
            app_config.validate({"data_dir": 42})

    def test_validate_rejects_existing_file(self):
        f = self.tmp_path / "not-a-dir.txt"
        f.write_text("hi", encoding="utf-8")
        with self.assertRaises(app_config.ValidationError):
            app_config.validate({"data_dir": str(f)})

    def test_validate_rejects_non_bool_flag(self):
        with self.assertRaises(app_config.ValidationError):
            app_config.validate({"seen_api_key_prompt": "yes"})

    def test_sanitize_drops_unknown_keys(self):
        result = app_config.sanitize_user_updates(
            {"data_dir": "/tmp", "rogue": 1}
        )
        self.assertEqual(result, {"data_dir": "/tmp"})

    def test_public_view_excludes_nothing_today(self):
        # All fields are user-facing; this test pins the contract so a future
        # internal-only field doesn't accidentally leak through public_view.
        view = app_config.public_view()
        self.assertEqual(set(view.keys()), {"data_dir", "seen_api_key_prompt"})


class TestResolveDataDirOnImport(unittest.TestCase):
    """Pin the startup behavior of server._resolve_data_dir().

    1. ~/.atc/config.json present  → use what it says.
    2. No config but ./data/boards exists → adopt legacy path AND persist it.
    3. Neither → fall back to default (~/ATC-Data).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.orig_dir = app_config.CONFIG_DIR
        self.orig_path = app_config.CONFIG_PATH
        app_config.CONFIG_DIR = self.tmp_path / ".atc"
        app_config.CONFIG_PATH = app_config.CONFIG_DIR / "config.json"
        # Re-import server with our patched config so _resolve_data_dir()
        # sees the temp config path.
        import importlib
        import server
        self.server = importlib.reload(server)

    def tearDown(self):
        app_config.CONFIG_DIR = self.orig_dir
        app_config.CONFIG_PATH = self.orig_path
        # Restore module-level state so other tests aren't affected.
        import importlib
        import server
        importlib.reload(server)
        self.tmp.cleanup()

    def test_uses_existing_config(self):
        target = self.tmp_path / "explicit"
        app_config.save({"data_dir": str(target)})
        import importlib
        s = importlib.reload(self.server)
        self.assertEqual(s.DATA_DIR, target)

    def test_adopts_legacy_data_dir(self):
        # Project-relative ./data IS present in this repo and contains boards/,
        # so a fresh import (with no config file) should pick it up and persist.
        legacy = Path(self.server.__file__).parent / "data"
        if not (legacy / "boards").exists():
            self.skipTest("legacy ./data/boards not present in this checkout")
        import importlib
        s = importlib.reload(self.server)
        self.assertEqual(s.DATA_DIR, legacy)
        self.assertTrue(app_config.CONFIG_PATH.exists())
        self.assertEqual(app_config.load()["data_dir"], str(legacy))


if __name__ == "__main__":
    unittest.main()
