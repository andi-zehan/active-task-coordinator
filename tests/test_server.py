#!/usr/bin/env python3
"""Tests for the kanban board server."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server


class TestSlugify(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(server.slugify("Hello World"), "hello-world")

    def test_special_chars(self):
        self.assertEqual(server.slugify("Fix bug #123!"), "fix-bug-123")

    def test_extra_spaces(self):
        self.assertEqual(server.slugify("  too   many   spaces  "), "too-many-spaces")

    def test_unicode(self):
        self.assertEqual(server.slugify("café latte"), "caf-latte")


class TestFrontmatter(unittest.TestCase):
    def test_parse_basic(self):
        text = "---\ntitle: Test Card\nassignee: Alice\n---\n\nBody here"
        meta, body = server.parse_frontmatter(text)
        self.assertEqual(meta['title'], 'Test Card')
        self.assertEqual(meta['assignee'], 'Alice')
        self.assertEqual(body.strip(), 'Body here')

    def test_parse_list(self):
        text = "---\nlabels: [frontend, urgent]\n---\n\n"
        meta, _ = server.parse_frontmatter(text)
        self.assertEqual(meta['labels'], ['frontend', 'urgent'])

    def test_parse_nested_dict(self):
        text = "---\ncustom_fields:\n  priority: high\n  effort: M\n---\n\n"
        meta, _ = server.parse_frontmatter(text)
        self.assertEqual(meta['custom_fields']['priority'], 'high')
        self.assertEqual(meta['custom_fields']['effort'], 'M')

    def test_parse_attachment_list(self):
        text = "---\nattachments:\n  - name: Doc\n    url: https://example.com\n---\n\n"
        meta, _ = server.parse_frontmatter(text)
        self.assertEqual(len(meta['attachments']), 1)
        self.assertEqual(meta['attachments'][0]['name'], 'Doc')
        self.assertEqual(meta['attachments'][0]['url'], 'https://example.com')

    def test_roundtrip(self):
        original_meta = {
            'title': 'Test',
            'labels': ['a', 'b'],
            'custom_fields': {'priority': 'high'},
        }
        original_body = '\n## Description\n\nHello\n'
        text = server.serialize_frontmatter(original_meta, original_body)
        meta, body = server.parse_frontmatter(text)
        self.assertEqual(meta['title'], 'Test')
        self.assertEqual(meta['labels'], ['a', 'b'])
        self.assertEqual(body, original_body)


class TestDataLayer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(self.tmp) / "data"
        server.ensure_data_dir()

    def tearDown(self):
        server.DATA_DIR = self.orig_data_dir
        shutil.rmtree(self.tmp)

    def test_board_create_and_read(self):
        server.write_board_meta("test-board", {
            "name": "Test Board",
            "description": "A test",
            "color": "#4A90D9"
        })
        boards_order = server.read_json(server.DATA_DIR / "_boards-order.json")
        boards_order.append("test-board")
        server.write_json(server.DATA_DIR / "_boards-order.json", boards_order)

        meta = server.read_board_meta("test-board")
        self.assertEqual(meta['name'], 'Test Board')
        self.assertEqual(meta['slug'], 'test-board')
        for list_name in server.LISTS:
            self.assertTrue((server.DATA_DIR / "boards" / "test-board" / list_name).exists())

    def test_card_create_and_read(self):
        server.write_board_meta("proj", {"name": "Proj", "color": "#000"})
        server.write_card("proj", "ideas", "my-card", {
            "title": "My Card",
            "assignee": "Bob",
            "labels": ["bug"],
            "due": "2026-05-01",
            "created": "2026-04-23",
            "updated": "2026-04-23",
            "relations": [],
            "custom_fields": {},
            "attachments": [],
        }, "\n## Description\n\nTest body\n")
        order_path = server.DATA_DIR / "boards" / "proj" / "ideas" / "_order.json"
        server.write_json(order_path, ["my-card"])

        card = server.read_card("proj", "ideas", "my-card")
        self.assertEqual(card['title'], 'My Card')
        self.assertEqual(card['assignee'], 'Bob')
        self.assertEqual(card['board'], 'proj')
        self.assertEqual(card['list'], 'ideas')
        self.assertIn('Test body', card['body'])

    def test_read_nonexistent_card(self):
        self.assertIsNone(server.read_card("nope", "ideas", "nope"))


if __name__ == '__main__':
    unittest.main()
