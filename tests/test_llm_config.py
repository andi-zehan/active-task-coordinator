#!/usr/bin/env python3
"""Tests for llm_config module."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import llm_config


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".llm-config.json"
        llm_config.CONFIG_PATH = self.path

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_missing_returns_defaults(self):
        cfg = llm_config.load()
        self.assertEqual(cfg["auth_token"], "")
        self.assertEqual(cfg["model"], "claude-opus-4-7")
        self.assertTrue(cfg["base_url"].startswith("https://"))
        self.assertFalse(cfg["tls_verify"])

    def test_save_then_load_roundtrip(self):
        llm_config.save({
            "base_url": "https://example.com",
            "auth_token": "secret-token-xyz",
            "model": "claude-sonnet-4-6",
            "tls_verify": True,
        })
        cfg = llm_config.load()
        self.assertEqual(cfg["base_url"], "https://example.com")
        self.assertEqual(cfg["auth_token"], "secret-token-xyz")
        self.assertEqual(cfg["model"], "claude-sonnet-4-6")
        self.assertTrue(cfg["tls_verify"])

    def test_save_partial_keeps_existing_token(self):
        llm_config.save({"auth_token": "original-token"})
        llm_config.save({"model": "claude-haiku-4-5"})
        cfg = llm_config.load()
        self.assertEqual(cfg["auth_token"], "original-token")
        self.assertEqual(cfg["model"], "claude-haiku-4-5")

    def test_save_explicit_empty_token_clears(self):
        llm_config.save({"auth_token": "original"})
        llm_config.save({"auth_token": ""})
        cfg = llm_config.load()
        self.assertEqual(cfg["auth_token"], "")


class TestMask(unittest.TestCase):
    def test_short_token_fully_masked(self):
        self.assertEqual(llm_config.mask_token("abc"), "***")

    def test_long_token_shows_last_four(self):
        self.assertEqual(llm_config.mask_token("supersecret1234"), "****1234")

    def test_empty_token(self):
        self.assertEqual(llm_config.mask_token(""), "")


class TestPublicView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        llm_config.CONFIG_PATH = Path(self.tmp.name) / ".llm-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_public_view_when_unconfigured(self):
        view = llm_config.public_view()
        self.assertFalse(view["configured"])
        self.assertEqual(view["auth_token"], "")

    def test_public_view_masks_token(self):
        llm_config.save({"auth_token": "supersecret1234"})
        view = llm_config.public_view()
        self.assertTrue(view["configured"])
        self.assertEqual(view["auth_token"], "****1234")
        self.assertNotIn("supersecret", json.dumps(view))


class TestGetClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        llm_config.CONFIG_PATH = Path(self.tmp.name) / ".llm-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_unconfigured_raises(self):
        with self.assertRaises(llm_config.NotConfigured):
            llm_config.get_client()

    def test_configured_returns_client(self):
        llm_config.save({
            "auth_token": "test-token",
            "base_url": "https://example.com",
        })
        client = llm_config.get_client()
        # Anthropic client exposes .messages
        self.assertTrue(hasattr(client, "messages"))


if __name__ == "__main__":
    unittest.main()
