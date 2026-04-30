#!/usr/bin/env python3
"""Tests for sync_config module."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import sync_config


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmp.name) / ".sync-config.json"
        sync_config.CONFIG_PATH = self.cfg_path

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_returns_defaults_when_no_file(self):
        cfg = sync_config.load()
        self.assertEqual(cfg["mode"], "remote")
        self.assertEqual(cfg["remote_url"], "")
        self.assertEqual(cfg["branch"], "main")
        self.assertEqual(cfg["skip_next_pull"], False)

    def test_save_then_load_roundtrip(self):
        sync_config.save({"mode": "local", "remote_url": "https://x/y.git", "branch": "trunk"})
        cfg = sync_config.load()
        self.assertEqual(cfg["mode"], "local")
        self.assertEqual(cfg["remote_url"], "https://x/y.git")
        self.assertEqual(cfg["branch"], "trunk")

    def test_save_partial_update_keeps_other_keys(self):
        sync_config.save({"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"})
        sync_config.save({"branch": "develop"})
        cfg = sync_config.load()
        self.assertEqual(cfg["mode"], "remote")
        self.assertEqual(cfg["remote_url"], "https://x/y.git")
        self.assertEqual(cfg["branch"], "develop")

    def test_public_view_matches_load(self):
        sync_config.save({"mode": "local", "remote_url": "https://x/y.git", "branch": "main"})
        view = sync_config.public_view()
        self.assertEqual(view["mode"], "local")
        self.assertEqual(view["remote_url"], "https://x/y.git")
        self.assertEqual(view["branch"], "main")
        self.assertNotIn("skip_next_pull", view)


class TestValidation(unittest.TestCase):
    def test_valid_off(self):
        sync_config.validate({"mode": "off", "remote_url": "", "branch": "main"})

    def test_valid_local_no_url_required(self):
        sync_config.validate({"mode": "local", "remote_url": "", "branch": "main"})

    def test_valid_remote_with_url(self):
        sync_config.validate({"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"})

    def test_invalid_mode(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "weird", "remote_url": "", "branch": "main"})

    def test_remote_without_url(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "remote", "remote_url": "", "branch": "main"})

    def test_remote_with_whitespace_url(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "remote", "remote_url": "   ", "branch": "main"})

    def test_empty_branch_rejected(self):
        with self.assertRaises(sync_config.ValidationError):
            sync_config.validate({"mode": "local", "remote_url": "", "branch": ""})


if __name__ == "__main__":
    unittest.main()
