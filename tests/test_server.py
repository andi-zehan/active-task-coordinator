#!/usr/bin/env python3
"""Tests for the kanban board server."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import date, timedelta
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server


def make_request_port(port, method, path, body=None):
    """Make an HTTP request to localhost on the given port."""
    url = f"http://localhost:{port}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    try:
        resp = urllib.request.urlopen(req)
        resp_body = resp.read().decode('utf-8')
        return resp.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode('utf-8')
        return e.code, json.loads(resp_body) if resp_body else {}


def parse_sse_events(raw_text):
    """Parse a Server-Sent Events stream into a list of {data: <json>} dicts."""
    events = []
    for block in raw_text.split('\n\n'):
        data_lines = [l[5:].lstrip() for l in block.splitlines() if l.startswith('data:')]
        if not data_lines:
            continue
        try:
            events.append(json.loads('\n'.join(data_lines)))
        except json.JSONDecodeError:
            pass
    return events


def stream_analyze(port, body):
    """POST /api/notes/analyze and return (status, [events], final_done_event_or_None)."""
    url = f"http://localhost:{port}/api/notes/analyze"
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    try:
        resp = urllib.request.urlopen(req)
        events = parse_sse_events(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, [], None
    final = next((e for e in reversed(events) if e.get('type') == 'done'), None)
    return resp.status, events, final


def stream_briefing(port, body, *, path="/api/briefing/generate"):
    """POST a briefing endpoint and return (status, [events], final_done_or_None)."""
    url = f"http://localhost:{port}{path}"
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    try:
        resp = urllib.request.urlopen(req)
        events = parse_sse_events(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, [], None
    final = next((e for e in reversed(events) if e.get('type') == 'done'), None)
    return resp.status, events, final


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


class TestIdIndex(unittest.TestCase):
    """Test the id-index and next-id allocator."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(self.tmp) / "data"
        server.ensure_data_dir()
        server.reset_id_index()

    def tearDown(self):
        server.DATA_DIR = self.orig_data_dir
        shutil.rmtree(self.tmp)

    def _write_card_with_id(self, board, list_slug, card_slug, card_id):
        server.write_board_meta(board, {"name": board, "color": "#000"})
        meta = {
            "id": card_id, "title": card_slug, "labels": [], "due": "",
            "assignee": "", "created": "2026-05-07", "updated": "2026-05-07",
            "relations": [], "custom_fields": {}, "attachments": [],
        }
        server.write_card(board, list_slug, card_slug, meta, "## Description\n\n")
        order_path = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
        order = server.read_json(order_path) or []
        if card_slug not in order:
            order.append(card_slug)
        server.write_json(order_path, order)

    def test_next_id_empty_repo(self):
        self.assertEqual(server.next_id(), "C-1")

    def test_next_id_after_one_card(self):
        self._write_card_with_id("b", "ideas", "C-5", "C-5")
        server.reset_id_index()
        self.assertEqual(server.next_id(), "C-6")

    def test_next_id_increments_within_session(self):
        self.assertEqual(server.next_id(), "C-1")
        self.assertEqual(server.next_id(), "C-2")
        self.assertEqual(server.next_id(), "C-3")

    def test_index_locates_card(self):
        self._write_card_with_id("b", "backlog", "C-7", "C-7")
        server.reset_id_index()
        self.assertEqual(server.resolve_id("C-7"), ("b", "backlog"))

    def test_index_returns_none_for_missing_id(self):
        self.assertIsNone(server.resolve_id("C-99"))

    def test_register_id_updates_index(self):
        # resolve_id verifies the file exists at the cached location, so we
        # must write the card before registering — matching how every real
        # code path uses register_id (after write_card / shutil.move).
        self._write_card_with_id("x", "ideas", "C-42", "C-42")
        server.register_id("C-42", "x", "ideas")
        self.assertEqual(server.resolve_id("C-42"), ("x", "ideas"))

    def test_unregister_id_removes_from_index(self):
        self._write_card_with_id("x", "ideas", "C-42", "C-42")
        server.register_id("C-42", "x", "ideas")
        # Unregister + delete on disk together — matches every real call site
        # (delete_card, archive). resolve_id rescans on a miss, so leaving the
        # file would just re-populate the index.
        server.unregister_id("C-42")
        (server.DATA_DIR / "boards" / "x" / "ideas" / "C-42.md").unlink()
        self.assertIsNone(server.resolve_id("C-42"))

    def test_resolve_id_rescans_when_file_missing(self):
        # The bug this guards against: an LLM tool turn cached a card's
        # location, then a separate flow moved the card. The cached entry
        # points at the old path; resolve_id must notice and rescan.
        self._write_card_with_id("b", "backlog", "C-9", "C-9")
        server.reset_id_index()
        self.assertEqual(server.resolve_id("C-9"), ("b", "backlog"))
        # Move the file on disk WITHOUT going through register_id, simulating
        # the index-vs-disk drift.
        src = server.DATA_DIR / "boards" / "b" / "backlog" / "C-9.md"
        dst_dir = server.DATA_DIR / "boards" / "b" / "in-progress"
        dst_dir.mkdir(parents=True, exist_ok=True)
        src.rename(dst_dir / "C-9.md")
        self.assertEqual(server.resolve_id("C-9"), ("b", "in-progress"))


class TestBidirectionalRelations(unittest.TestCase):
    """Adding/removing a relation on one card mirrors onto the other."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(self.tmp) / "data"
        server.ensure_data_dir()
        server.reset_id_index()

    def tearDown(self):
        server.DATA_DIR = self.orig_data_dir
        shutil.rmtree(self.tmp)

    def _write_card(self, board, list_slug, card_id, *, relations=None):
        server.write_board_meta(board, {"name": board, "color": "#000"})
        meta = {
            "id": card_id, "title": card_id, "labels": [], "due": "",
            "assignee": "", "created": "2026-05-07", "updated": "2026-05-07",
            "relations": list(relations or []),
            "custom_fields": {}, "attachments": [],
        }
        server.write_card(board, list_slug, card_id, meta, "## Description\n\n")
        order_path = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
        order = server.read_json(order_path) or []
        if card_id not in order:
            order.append(card_id)
        server.write_json(order_path, order)
        server.register_id(card_id, board, list_slug)

    def test_adding_relation_appears_on_other_card(self):
        self._write_card("b", "ideas", "C-1")
        self._write_card("b", "ideas", "C-2")
        server._sync_back_relations("C-1", [], ["C-2"])
        other = server.read_card("b", "ideas", "C-2")
        self.assertEqual(other["relations"], ["C-1"])

    def test_removing_relation_removes_back_reference(self):
        self._write_card("b", "ideas", "C-1", relations=["C-2"])
        self._write_card("b", "ideas", "C-2", relations=["C-1"])
        server._sync_back_relations("C-1", ["C-2"], [])
        other = server.read_card("b", "ideas", "C-2")
        self.assertEqual(other["relations"], [])

    def test_adding_does_not_duplicate_existing_back_reference(self):
        self._write_card("b", "ideas", "C-1")
        self._write_card("b", "ideas", "C-2", relations=["C-1"])
        server._sync_back_relations("C-1", [], ["C-2"])
        other = server.read_card("b", "ideas", "C-2")
        self.assertEqual(other["relations"], ["C-1"])

    def test_unresolved_id_is_skipped(self):
        self._write_card("b", "ideas", "C-1")
        # No card C-99 exists; sync must not raise.
        server._sync_back_relations("C-1", [], ["C-99"])
        # And no spurious file was created.
        self.assertIsNone(server.resolve_id("C-99"))

    def test_self_reference_is_ignored(self):
        self._write_card("b", "ideas", "C-1")
        server._sync_back_relations("C-1", [], ["C-1"])
        card = server.read_card("b", "ideas", "C-1")
        # We did not re-add C-1 onto itself via the back-sync path.
        self.assertEqual(card["relations"], [])

    def test_works_across_boards_and_lists(self):
        self._write_card("ba", "ideas", "C-1")
        self._write_card("bb", "backlog", "C-2")
        server._sync_back_relations("C-1", [], ["C-2"])
        other = server.read_card("bb", "backlog", "C-2")
        self.assertEqual(other["relations"], ["C-1"])

    def test_back_reference_bumps_updated_date(self):
        self._write_card("b", "ideas", "C-1")
        self._write_card("b", "ideas", "C-2")
        # C-2 was written with updated=2026-05-07; the sync should set it to today.
        server._sync_back_relations("C-1", [], ["C-2"])
        other = server.read_card("b", "ideas", "C-2")
        self.assertEqual(other["updated"], str(date.today()))

    def test_deleting_card_clears_back_references(self):
        self._write_card("b", "ideas", "C-1", relations=["C-2"])
        self._write_card("b", "ideas", "C-2", relations=["C-1"])
        # Simulate the delete handler's sync step.
        server._sync_back_relations("C-1", ["C-2"], [])
        other = server.read_card("b", "ideas", "C-2")
        self.assertEqual(other["relations"], [])

    def test_delete_with_no_relations_is_noop(self):
        self._write_card("b", "ideas", "C-1")
        self._write_card("b", "ideas", "C-2")
        server._sync_back_relations("C-1", [], [])
        other = server.read_card("b", "ideas", "C-2")
        self.assertEqual(other["relations"], [])

    def test_update_card_handler_writes_back_reference(self):
        """End-to-end via _handle_update_card: editing C-1 must mutate C-2 on disk."""
        self._write_card("b", "ideas", "C-1")
        self._write_card("b", "ideas", "C-2")
        # Simulate the slice of _handle_update_card relevant to relations:
        card = server.read_card("b", "ideas", "C-1")
        old_relations = list(card.get("relations") or [])
        card["relations"] = ["C-2"]
        card["updated"] = str(date.today())
        meta = {k: v for k, v in card.items() if k not in ("slug", "board", "list", "body")}
        server.write_card("b", "ideas", "C-1", meta, card["body"])
        server._sync_back_relations("C-1", old_relations, card["relations"])

        self.assertEqual(server.read_card("b", "ideas", "C-1")["relations"], ["C-2"])
        self.assertEqual(server.read_card("b", "ideas", "C-2")["relations"], ["C-1"])


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


class TestBoardAPI(unittest.TestCase):
    """Test board API endpoints on port 8089."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(cls.tmp) / "data"
        server.ensure_data_dir()
        cls.server = HTTPServer(('127.0.0.1', 8089), server.RequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        server.DATA_DIR = cls.orig_data_dir
        shutil.rmtree(cls.tmp)

    def setUp(self):
        # Clean data between tests
        if server.DATA_DIR.exists():
            shutil.rmtree(server.DATA_DIR)
        server.ensure_data_dir()

    def test_list_boards_empty(self):
        status, data = make_request_port(8089, 'GET', '/api/boards')
        self.assertEqual(status, 200)
        self.assertEqual(data, [])

    def test_create_board(self):
        status, data = make_request_port(8089, 'POST', '/api/boards', {
            'name': 'My Project',
            'description': 'A test board',
            'color': '#FF0000'
        })
        self.assertEqual(status, 201)
        self.assertEqual(data['name'], 'My Project')
        self.assertEqual(data['slug'], 'my-project')
        self.assertEqual(data['color'], '#FF0000')
        # Verify it shows in listing
        status, boards = make_request_port(8089, 'GET', '/api/boards')
        self.assertEqual(len(boards), 1)
        self.assertEqual(boards[0]['name'], 'My Project')

    def test_get_board(self):
        make_request_port(8089, 'POST', '/api/boards', {'name': 'Test Board'})
        status, data = make_request_port(8089, 'GET', '/api/boards/test-board')
        self.assertEqual(status, 200)
        self.assertEqual(data['name'], 'Test Board')
        self.assertIn('lists', data)
        for list_name in server.LISTS:
            self.assertIn(list_name, data['lists'])

    def test_update_board(self):
        make_request_port(8089, 'POST', '/api/boards', {'name': 'Old Name'})
        status, data = make_request_port(8089, 'PUT', '/api/boards/old-name', {
            'name': 'New Name',
            'color': '#00FF00'
        })
        self.assertEqual(status, 200)
        self.assertEqual(data['name'], 'New Name')
        self.assertEqual(data['color'], '#00FF00')

    def test_delete_board(self):
        make_request_port(8089, 'POST', '/api/boards', {'name': 'Delete Me'})
        status, data = make_request_port(8089, 'DELETE', '/api/boards/delete-me')
        self.assertEqual(status, 200)
        self.assertEqual(data['deleted'], 'delete-me')
        status, boards = make_request_port(8089, 'GET', '/api/boards')
        self.assertEqual(len(boards), 0)

    def test_get_nonexistent_board(self):
        status, data = make_request_port(8089, 'GET', '/api/boards/no-such-board')
        self.assertEqual(status, 404)


class TestCardAPI(unittest.TestCase):
    """Test card API endpoints on port 8090."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(cls.tmp) / "data"
        server.ensure_data_dir()
        cls.server = HTTPServer(('127.0.0.1', 8090), server.RequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        server.DATA_DIR = cls.orig_data_dir
        shutil.rmtree(cls.tmp)

    def setUp(self):
        if server.DATA_DIR.exists():
            shutil.rmtree(server.DATA_DIR)
        server.ensure_data_dir()
        # Create a board for card tests
        make_request_port(8090, 'POST', '/api/boards', {'name': 'Card Board'})
        server.reset_id_index()

    def test_create_card(self):
        status, data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {
                'title': 'My Card',
                'assignee': 'Alice',
                'labels': ['bug', 'urgent'],
                'due': '2026-05-01',
            })
        self.assertEqual(status, 201)
        self.assertEqual(data['title'], 'My Card')
        # Slug is now the ID after migration
        self.assertEqual(data['slug'], data['id'])
        self.assertEqual(data['assignee'], 'Alice')
        self.assertEqual(data['labels'], ['bug', 'urgent'])

    def test_create_card_assigns_id(self):
        status, data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {'title': 'First'})
        self.assertEqual(status, 201)
        self.assertEqual(data['id'], 'C-1')

        status, data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {'title': 'Second'})
        self.assertEqual(status, 201)
        self.assertEqual(data['id'], 'C-2')

    def test_create_card_writes_id_to_frontmatter(self):
        status, data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {'title': 'Has ID'})
        # Filename is the ID, not a slug.
        path = server.DATA_DIR / "boards" / "card-board" / "ideas" / f"{data['id']}.md"
        self.assertTrue(path.exists())
        # Slug field on the response equals the ID (filename).
        self.assertEqual(data['slug'], data['id'])
        # _order.json holds the ID.
        order = server.read_json(server.DATA_DIR / "boards" / "card-board" / "ideas" / "_order.json")
        self.assertIn(data['id'], order)

    def test_get_card(self):
        status, create_data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {
                'title': 'Get Me',
                'description': 'card body text',
            })
        card_id = create_data['id']
        status, data = make_request_port(8090, 'GET',
            f'/api/cards/card-board/ideas/{card_id}')
        self.assertEqual(status, 200)
        self.assertEqual(data['title'], 'Get Me')
        self.assertIn('card body text', data['body'])

    def test_update_card(self):
        status, create_data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/backlog/cards', {
                'title': 'Update Me',
                'description': 'old description',
            })
        card_id = create_data['id']
        status, data = make_request_port(8090, 'PUT',
            f'/api/cards/card-board/backlog/{card_id}', {
                'assignee': 'Bob',
                'description': 'new description',
                'comment': 'looks good',
            })
        self.assertEqual(status, 200)
        self.assertEqual(data['assignee'], 'Bob')
        self.assertIn('new description', data['body'])
        self.assertIn('looks good', data['body'])
        self.assertEqual(data['updated'], str(date.today()))

    def test_delete_card(self):
        status, create_data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {
                'title': 'Delete Me',
            })
        card_id = create_data['id']
        status, data = make_request_port(8090, 'DELETE',
            f'/api/cards/card-board/ideas/{card_id}')
        self.assertEqual(status, 200)
        self.assertEqual(data['deleted'], card_id)
        # Verify it's gone
        status, data = make_request_port(8090, 'GET',
            f'/api/cards/card-board/ideas/{card_id}')
        self.assertEqual(status, 404)

    def test_move_card(self):
        status, create_data = make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {
                'title': 'Move Me',
            })
        card_id = create_data['id']
        status, data = make_request_port(8090, 'PUT',
            f'/api/cards/card-board/ideas/{card_id}/move', {
                'target_list': 'in-progress',
            })
        self.assertEqual(status, 200)
        self.assertEqual(data['to'], 'in-progress')
        # Verify card is in target list
        status, data = make_request_port(8090, 'GET',
            f'/api/cards/card-board/in-progress/{card_id}')
        self.assertEqual(status, 200)
        self.assertEqual(data['title'], 'Move Me')
        # Verify card is gone from source list
        status, data = make_request_port(8090, 'GET',
            f'/api/cards/card-board/ideas/{card_id}')
        self.assertEqual(status, 404)

    def test_list_cards(self):
        make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {'title': 'Card A'})
        make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {'title': 'Card B'})
        status, data = make_request_port(8090, 'GET',
            '/api/boards/card-board/lists/ideas/cards')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 2)
        titles = [c['title'] for c in data]
        self.assertIn('Card A', titles)
        self.assertIn('Card B', titles)


class TestAggregationAPI(unittest.TestCase):
    """Test aggregation API endpoints on port 8091."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(cls.tmp) / "data"
        server.ensure_data_dir()
        cls.server = HTTPServer(('127.0.0.1', 8091), server.RequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        server.DATA_DIR = cls.orig_data_dir
        shutil.rmtree(cls.tmp)

    def setUp(self):
        if server.DATA_DIR.exists():
            shutil.rmtree(server.DATA_DIR)
        server.ensure_data_dir()
        # Create a board for aggregation tests
        make_request_port(8091, 'POST', '/api/boards', {'name': 'Agg Board'})

    def test_dashboard(self):
        today_str = str(date.today())
        days_until_sunday = 6 - date.today().weekday()
        this_week_str = str(date.today() + timedelta(days=1)) if days_until_sunday > 0 else None
        next_week_str = str(date.today() + timedelta(days=days_until_sunday + 1))
        later_str = str(date.today() + timedelta(days=days_until_sunday + 8))
        past_str = str(date.today() - timedelta(days=2))
        # Card due today
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Today Card', 'due': today_str})
        # Card due this week
        if this_week_str:
            make_request_port(8091, 'POST',
                '/api/boards/agg-board/lists/backlog/cards', {
                    'title': 'Week Card', 'due': this_week_str})
        # Card due next week
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/backlog/cards', {
                'title': 'Next Week Card', 'due': next_week_str})
        # Card due later than next week
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/backlog/cards', {
                'title': 'Later Card', 'due': later_str})
        # Card with no due date
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/backlog/cards', {
                'title': 'Someday Card'})
        # Overdue card
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/in-progress/cards', {
                'title': 'Overdue Card', 'due': past_str})
        status, data = make_request_port(8091, 'GET', '/api/dashboard')
        self.assertEqual(status, 200)
        self.assertEqual(len(data['today']), 1)
        self.assertEqual(data['today'][0]['title'], 'Today Card')
        this_week_titles = [c['title'] for c in data['this_week']]
        self.assertEqual(len(data['this_week']), 1 if this_week_str else 0)
        if this_week_str:
            self.assertIn('Week Card', this_week_titles)
        self.assertNotIn('Next Week Card', this_week_titles)
        self.assertEqual(len(data['next_week']), 1)
        self.assertEqual(data['next_week'][0]['title'], 'Next Week Card')
        self.assertEqual(len(data['later']), 1)
        self.assertEqual(data['later'][0]['title'], 'Later Card')
        self.assertEqual(len(data['someday']), 1)
        self.assertEqual(data['someday'][0]['title'], 'Someday Card')
        self.assertEqual(len(data['overdue']), 1)
        self.assertEqual(data['overdue'][0]['title'], 'Overdue Card')

    def test_dashboard_excludes_done_cards(self):
        today_str = str(date.today())
        past_str = str(date.today() - timedelta(days=2))
        # Done card with today's due date — must not appear in 'today'.
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/done/cards', {
                'title': 'Done Today Card', 'due': today_str})
        # Done card with no due date — must not appear in 'someday'.
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/done/cards', {
                'title': 'Done No-Due Card'})
        # Done card overdue — must not appear in 'overdue'.
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/done/cards', {
                'title': 'Done Overdue Card', 'due': past_str})
        _, data = make_request_port(8091, 'GET', '/api/dashboard')
        for bucket in ('today', 'this_week', 'next_week', 'later', 'someday', 'overdue'):
            titles = [c['title'] for c in data[bucket]]
            for t in titles:
                self.assertFalse(t.startswith('Done '),
                                 f"Done card '{t}' leaked into {bucket}")

    def test_calendar(self):
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'May Card', 'due': '2026-05-15'})
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'June Card', 'due': '2026-06-01'})
        status, data = make_request_port(8091, 'GET', '/api/calendar/2026/5')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['title'], 'May Card')

    def test_search_by_title(self):
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Login Bug Fix'})
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Dashboard Feature'})
        status, data = make_request_port(8091, 'GET',
            '/api/search?q=login')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['title'], 'Login Bug Fix')

    def test_search_by_description(self):
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Task A', 'description': 'investigate the cache layer'})
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Task B', 'description': 'rewrite the loader'})
        status, data = make_request_port(8091, 'GET',
            '/api/search?q=cache')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['title'], 'Task A')

    def test_search_by_card_id(self):
        status_create, created = make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Some Task'})
        self.assertEqual(status_create, 201)
        cid = created['id'].lower()
        status, data = make_request_port(8091, 'GET',
            '/api/search?q=' + cid)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(data), 1)
        self.assertTrue(any(d['id'] == created['id'] for d in data))

    def test_search_does_not_match_assignee(self):
        # Per spec: search is title + description + id only.
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Task A', 'assignee': 'Charlie'})
        status, data = make_request_port(8091, 'GET',
            '/api/search?q=charlie')
        self.assertEqual(status, 200)
        self.assertEqual(data, [])

    def test_search_no_results(self):
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Existing Card'})
        status, data = make_request_port(8091, 'GET',
            '/api/search?q=nonexistent')
        self.assertEqual(status, 200)
        self.assertEqual(data, [])


class TestAppConfigEndpoints(unittest.TestCase):
    def setUp(self):
        import app_config
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.orig_cfg_dir = app_config.CONFIG_DIR
        self.orig_cfg_path = app_config.CONFIG_PATH
        app_config.CONFIG_DIR = self.tmp_path / ".atc"
        app_config.CONFIG_PATH = app_config.CONFIG_DIR / "config.json"
        self.server = HTTPServer(('127.0.0.1', 0), server.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        import app_config
        self.server.shutdown()
        self.thread.join()
        app_config.CONFIG_DIR = self.orig_cfg_dir
        app_config.CONFIG_PATH = self.orig_cfg_path
        self.tmp.cleanup()

    def test_get_returns_defaults(self):
        status, body = make_request_port(self.port, "GET", "/api/app-config")
        self.assertEqual(status, 200)
        self.assertIn("data_dir", body)
        self.assertIn("seen_api_key_prompt", body)
        self.assertIn("active_data_dir", body)

    def test_put_changes_data_dir(self):
        target = str(self.tmp_path / "new-cards")
        status, body = make_request_port(self.port, "PUT", "/api/app-config",
                                          {"data_dir": target})
        self.assertEqual(status, 200)
        self.assertEqual(body["data_dir"], target)
        self.assertTrue(body["requires_restart"])

    def test_put_seen_flag_does_not_require_restart(self):
        status, body = make_request_port(self.port, "PUT", "/api/app-config",
                                          {"seen_api_key_prompt": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["seen_api_key_prompt"])
        self.assertFalse(body["requires_restart"])

    def test_put_rejects_unwritable_path(self):
        # A file (not a directory) is rejected by the validator.
        f = self.tmp_path / "blocker.txt"
        f.write_text("x", encoding="utf-8")
        status, body = make_request_port(self.port, "PUT", "/api/app-config",
                                          {"data_dir": str(f)})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_put_rejects_non_object_body(self):
        status, _ = make_request_port(self.port, "PUT", "/api/app-config", [1, 2])
        self.assertEqual(status, 400)

    def test_put_drops_unknown_keys(self):
        status, body = make_request_port(self.port, "PUT", "/api/app-config",
                                          {"rogue": "value"})
        self.assertEqual(status, 200)
        self.assertNotIn("rogue", body)


class TestLLMConfigEndpoints(unittest.TestCase):
    def setUp(self):
        import llm_config
        self.tmp = tempfile.TemporaryDirectory()
        llm_config.CONFIG_PATH = Path(self.tmp.name) / ".llm-config.json"
        self.server = HTTPServer(('127.0.0.1', 0), server.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.tmp.cleanup()

    def test_get_unconfigured(self):
        status, body = make_request_port(self.port, "GET", "/api/llm-config")
        self.assertEqual(status, 200)
        self.assertFalse(body["configured"])
        self.assertEqual(body["auth_token"], "")

    def test_put_then_get_masks_token(self):
        status, _ = make_request_port(self.port, "PUT", "/api/llm-config", {
            "auth_token": "supersecret1234",
            "model": "claude-sonnet-4-6",
        })
        self.assertEqual(status, 200)
        status, body = make_request_port(self.port, "GET", "/api/llm-config")
        self.assertTrue(body["configured"])
        self.assertEqual(body["auth_token"], "****1234")
        self.assertEqual(body["model"], "claude-sonnet-4-6")

    def test_put_partial_keeps_token(self):
        make_request_port(self.port, "PUT", "/api/llm-config", {"auth_token": "tok-abcd"})
        make_request_port(self.port, "PUT", "/api/llm-config", {"model": "claude-haiku-4-5"})
        status, body = make_request_port(self.port, "GET", "/api/llm-config")
        self.assertEqual(body["auth_token"], "****abcd")
        self.assertEqual(body["model"], "claude-haiku-4-5")


class TestNotesEndpoints(unittest.TestCase):
    def setUp(self):
        import notes as notes_mod
        self.notes_mod = notes_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        notes_mod.NOTES_DIR = Path(self.tmp.name) / "notes"
        # Stub the LLM client to drive the tool-use loop:
        # turn 1 -> create_card, turn 2 -> finish.
        from tests._llm_fakes import FakeClient, FakeResponse, tool_use
        scripted = [
            FakeResponse([tool_use("create_card", {
                "board": "alpha", "list": "backlog", "title": "Do thing",
                "confidence": "high", "reason": "explicit",
            }, id_="t1")]),
            FakeResponse([tool_use("finish", {
                "summary": "Talked about X.",
            }, id_="t2")]),
        ]
        # Each request gets a fresh client so the scripted queue restarts.
        import llm_config
        self._orig_get_client = llm_config.get_client
        llm_config.get_client = lambda: FakeClient(list(scripted))

        # Set up an alpha board
        board_dir = self.data_dir / "boards" / "alpha"
        board_dir.mkdir(parents=True)
        (board_dir / "_board.md").write_text(
            "---\nname: Alpha\ncolor: '#000'\n---\n", encoding="utf-8")
        for lst in ("ideas", "backlog", "in-progress", "done"):
            (board_dir / lst).mkdir()
            (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
        (self.data_dir / "_boards-order.json").write_text('["alpha"]', encoding="utf-8")

        self.server = HTTPServer(('127.0.0.1', 0), server.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        import llm_config
        llm_config.get_client = self._orig_get_client
        self.tmp.cleanup()

    def test_analyze_returns_operations(self):
        status, events, done = stream_analyze(self.port,
            {"text": "We agreed to do thing.", "title": "Q2"})
        self.assertEqual(status, 200)
        self.assertIsNotNone(done)
        self.assertEqual(done["summary"], "Talked about X.")
        self.assertEqual(len(done["operations"]), 1)
        self.assertTrue(done["note_id"].endswith("-q2"))
        # Stream included intermediate events.
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "started")
        self.assertIn("turn", types)
        self.assertIn("queued", types)

    def test_analyze_streams_sse_content_type(self):
        url = f"http://localhost:{self.port}/api/notes/analyze"
        req = urllib.request.Request(url,
            data=json.dumps({"text": "x", "title": "T"}).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as r:
            self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
            r.read()  # drain so the server thread can finish

    def test_get_note(self):
        _, _, done = stream_analyze(self.port,
            {"text": "body content", "title": "Topic"})
        note_id = done["note_id"]
        with urllib.request.urlopen(f"http://localhost:{self.port}/api/notes/{note_id}") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("body content", r.read().decode("utf-8"))

    def test_get_note_404(self):
        import urllib.request, urllib.error
        try:
            urllib.request.urlopen(f"http://localhost:{self.port}/api/notes/nonexistent")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_apply_creates_card(self):
        _, _, done = stream_analyze(self.port, {"text": "x", "title": "T"})
        note_id = done["note_id"]
        ops = done["operations"]
        status, result = make_request_port(self.port, "POST", "/api/notes/apply",
            {"note_id": note_id, "operations": ops})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["applied"]), 1)
        # Extract the assigned id from the response
        target = result["applied"][0]["target"]
        card_id = target.split("/")[-1]
        # Card now exists
        status, card = make_request_port(self.port, "GET",
            f"/api/cards/alpha/backlog/{card_id}")
        self.assertEqual(status, 200)

    def test_apply_accepts_null_note_id(self):
        # Chat sidebar sends note_id: null. Endpoint must accept it.
        ops = [{"op": "create_card", "board": "alpha", "list": "backlog",
                "title": "From chat"}]
        status, result = make_request_port(self.port, "POST", "/api/notes/apply",
            {"note_id": None, "operations": ops})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["applied"]), 1)
        # Extract the assigned id from the response
        target = result["applied"][0]["target"]
        card_id = target.split("/")[-1]
        # Created card has no source-note attachment.
        _, card = make_request_port(self.port, "GET",
            f"/api/cards/alpha/backlog/{card_id}")
        self.assertEqual(card.get("attachments") or [], [])


class TestBriefingEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        # Bootstrap a minimal alpha board so create_card can succeed.
        board_dir = self.data_dir / "boards" / "alpha"
        board_dir.mkdir(parents=True)
        (board_dir / "_board.md").write_text(
            "---\nname: Alpha\ncolor: '#000'\n---\n", encoding="utf-8")
        for lst in ("ideas", "backlog", "in-progress", "done"):
            (board_dir / lst).mkdir()
            (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
        (self.data_dir / "_boards-order.json").write_text('["alpha"]', encoding="utf-8")
        server.reset_id_index()

        from tests._llm_fakes import FakeClient, FakeResponse, text_block, tool_use
        # Two-turn script: text + tool_use(create_card) → text + finish.
        scripted = [
            FakeResponse([
                text_block("## Today's Top 5\n\n1. Card alpha"),
                tool_use("create_card", {
                    "board": "alpha", "list": "backlog", "title": "Follow up",
                    "confidence": "med", "reason": "from briefing",
                }, id_="b1"),
            ]),
            FakeResponse([
                text_block("\n2. Wrap-up"),
                tool_use("finish", {"summary": "five priorities."}, id_="b2"),
            ]),
        ]
        import llm_config
        self._orig_get_client = llm_config.get_client
        llm_config.get_client = lambda: FakeClient(list(scripted))

        self.server = HTTPServer(('127.0.0.1', 0), server.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        import llm_config
        llm_config.get_client = self._orig_get_client
        self.tmp.cleanup()

    def test_generate_returns_text_and_operations(self):
        status, events, done = stream_briefing(self.port, {"prompt": "Brief me."})
        self.assertEqual(status, 200)
        self.assertIsNotNone(done)
        self.assertTrue(done["briefing_id"].startswith("briefing-"))
        self.assertEqual(done["summary"], "five priorities.")
        self.assertIn("Top 5", done["text"])
        self.assertIn("Wrap-up", done["text"])
        self.assertEqual(len(done["operations"]), 1)
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "started")
        self.assertIn("turn", types)
        self.assertIn("text", types)
        self.assertIn("queued", types)

    def test_generate_streams_sse_content_type(self):
        url = f"http://localhost:{self.port}/api/briefing/generate"
        req = urllib.request.Request(
            url, data=json.dumps({"prompt": "x"}).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as r:
            self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
            r.read()

    def test_generate_rejects_empty_prompt(self):
        status, _ = make_request_port(
            self.port, "POST", "/api/briefing/generate", {"prompt": "  "})
        self.assertEqual(status, 400)

    def test_refine_rejects_missing_briefing_id(self):
        status, _ = make_request_port(
            self.port, "POST", "/api/briefing/refine",
            {"feedback": "shorter", "current_ops": [], "current_text": ""})
        self.assertEqual(status, 400)

    def test_refine_rejects_missing_feedback(self):
        status, _ = make_request_port(
            self.port, "POST", "/api/briefing/refine",
            {"briefing_id": "briefing-x", "current_ops": [], "current_text": ""})
        self.assertEqual(status, 400)

    def test_apply_creates_card_without_note(self):
        # First generate, then apply the queued op.
        _, _, done = stream_briefing(self.port, {"prompt": "do it"})
        self.assertIsNotNone(done)
        ops = done["operations"]
        status, result = make_request_port(
            self.port, "POST", "/api/briefing/apply", {"operations": ops})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["applied"]), 1)
        target = result["applied"][0]["target"]
        card_id = target.split("/")[-1]
        # Card exists with no source-note attachment.
        status, card = make_request_port(
            self.port, "GET", f"/api/cards/alpha/backlog/{card_id}")
        self.assertEqual(status, 200)
        self.assertEqual(card.get("attachments") or [], [])

    def test_apply_rejects_non_list_operations(self):
        status, _ = make_request_port(
            self.port, "POST", "/api/briefing/apply", {"operations": "nope"})
        self.assertEqual(status, 400)


class TestChatEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")

        from tests._llm_fakes import FakeClient, FakeResponse, text_block
        scripted = [
            FakeResponse([text_block("hi from the model")]),
        ]
        import llm_config
        self._orig_get_client = llm_config.get_client
        llm_config.get_client = lambda: FakeClient(list(scripted))

        self.server = HTTPServer(('127.0.0.1', 0), server.RequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        import llm_config
        llm_config.get_client = self._orig_get_client
        self.tmp.cleanup()

    def test_chat_streams_sse_content_type(self):
        url = f"http://localhost:{self.port}/api/chat"
        body = {"messages": [{"role": "user", "content": "hi"}]}
        req = urllib.request.Request(url,
            data=json.dumps(body).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as r:
            self.assertIn("text/event-stream", r.headers.get("Content-Type", ""))
            r.read()

    def test_chat_returns_done_event(self):
        url = f"http://localhost:{self.port}/api/chat"
        body = {"messages": [{"role": "user", "content": "hi"}]}
        req = urllib.request.Request(url,
            data=json.dumps(body).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as r:
            events = parse_sse_events(r.read().decode('utf-8'))
        self.assertEqual(events[0]["type"], "started")
        self.assertEqual(events[-1]["type"], "done")
        text = next(e for e in events if e["type"] == "text")
        self.assertEqual(text["text"], "hi from the model")

    def test_chat_rejects_empty_messages(self):
        status, body = make_request_port(self.port, "POST", "/api/chat", {"messages": []})
        self.assertEqual(status, 400)
        self.assertIn("messages", body.get("error", ""))


class TestMemoryEndpoints(unittest.TestCase):
    """End-to-end HTTP tests for /api/memory/* against a loopback server."""

    PORT = 8092

    @classmethod
    def setUpClass(cls):
        import memory_config, memory_store
        cls.tmp = tempfile.mkdtemp()
        cls.mem_path = Path(cls.tmp) / "memory"
        cls.orig_get = memory_config.get_memory_dir
        memory_config.get_memory_dir = lambda: cls.mem_path
        memory_store.init_if_missing()

        cls.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(cls.tmp) / "data"
        server.ensure_data_dir()

        cls.server = HTTPServer(('127.0.0.1', cls.PORT), server.RequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        import memory_config
        cls.server.shutdown()
        server.DATA_DIR = cls.orig_data_dir
        memory_config.get_memory_dir = cls.orig_get
        shutil.rmtree(cls.tmp)

    def setUp(self):
        # Wipe everything except the seed layout between tests.
        import memory_store
        for sub in ("_proposals", "_sources"):
            d = self.mem_path / sub
            if d.exists():
                for f in d.glob("*.md"):
                    f.unlink()
        # Rewrite seed pages so each test gets a known state.
        for name, body in {
            "internal-organization.md": "# Internal organization\n\n_(empty)_\n",
            "who-is-who.md": "# Who is who\n\n_(empty)_\n",
            "work-preferences.md": "# Work preferences\n\n_(empty)_\n",
        }.items():
            (self.mem_path / name).write_text(body, encoding="utf-8")
        # Clear the log between tests too.
        (self.mem_path / "log.md").write_text("", encoding="utf-8")

    def test_list_pages_returns_seed(self):
        status, body = make_request_port(self.PORT, "GET", "/api/memory/pages")
        self.assertEqual(status, 200)
        names = {p["name"] for p in body["pages"]}
        self.assertEqual(names, {"internal-organization", "who-is-who", "work-preferences"})
        self.assertIn("INDEX", body["index"])

    def test_get_page_known_and_unknown(self):
        status, body = make_request_port(self.PORT, "GET", "/api/memory/page/internal-organization")
        self.assertEqual(status, 200)
        self.assertIn("Internal organization", body["content"])
        status, _ = make_request_port(self.PORT, "GET", "/api/memory/page/no-such-page")
        self.assertEqual(status, 404)

    def test_put_page_writes_and_logs(self):
        status, body = make_request_port(self.PORT, "PUT", "/api/memory/page/internal-organization",
                                          {"content": "# hand edited\n"})
        self.assertEqual(status, 200)
        self.assertTrue(body["saved"])
        # Confirm via GET.
        _, get_body = make_request_port(self.PORT, "GET", "/api/memory/page/internal-organization")
        self.assertEqual(get_body["content"], "# hand edited\n")

    def test_apply_save_as_source(self):
        status, body = make_request_port(
            self.PORT, "POST", "/api/memory/apply",
            {"operations": [{"op": "save_as_source", "title": "orgchart",
                             "content": "anna > brent"}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["applied"]), 1)
        # Source should be listable.
        _, listing = make_request_port(self.PORT, "GET", "/api/memory/sources")
        self.assertEqual(len(listing["sources"]), 1)

    def test_chat_apply_writes_pages_directly(self):
        # Chat-driven propose_memory_edits applies straight to pages — no
        # proposal file. Per-edit review happens in the chat panel via
        # checkboxes before the apply call.
        op = {
            "op": "propose_memory_edits", "summary": "from chat",
            "edits": [
                {"page": "internal-organization", "action": "replace",
                 "content": "# org\n\n- anna\n"},
            ],
        }
        status, body = make_request_port(self.PORT, "POST", "/api/memory/apply",
                                          {"operations": [op]})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["applied"]), 1)
        self.assertTrue(body["applied"][0]["target"].startswith("memory/"))
        # No proposal file created — chat is the review surface.
        _, listing = make_request_port(self.PORT, "GET", "/api/memory/proposals")
        self.assertEqual(listing["proposals"], [])
        # Page actually got updated.
        _, page = make_request_port(self.PORT, "GET", "/api/memory/page/internal-organization")
        self.assertIn("anna", page["content"])

    def test_lint_proposal_review_flow(self):
        # Lint still uses proposals (no chat surface to host the review).
        # Simulate by writing a proposal file directly, then exercise
        # GET /api/memory/proposal/:id and POST /apply.
        import memory_ops, memory_store
        body = memory_ops._serialize_proposal("lint pass", [
            {"page": "internal-organization", "action": "replace",
             "content": "# org\n\n- anna\n"},
            {"page": "who-is-who", "action": "replace", "content": "rejected"},
        ], source="lint")
        pid = memory_store.write_proposal(body, kind="lint")

        # Fetch full proposal — should include current+proposed for each edit.
        status, detail = make_request_port(self.PORT, "GET", f"/api/memory/proposal/{pid}")
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["edits"]), 2)
        for e in detail["edits"]:
            self.assertIn("current", e)
            self.assertIn("proposed", e)

        # Apply only the first edit; second is dropped.
        status, applied = make_request_port(
            self.PORT, "POST", f"/api/memory/proposal/{pid}/apply",
            {"accepted_indices": [0]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(applied["applied"]), 1)
        _, page1 = make_request_port(self.PORT, "GET", "/api/memory/page/internal-organization")
        self.assertIn("anna", page1["content"])
        _, page2 = make_request_port(self.PORT, "GET", "/api/memory/page/who-is-who")
        self.assertNotIn("rejected", page2["content"])
        # Proposal is gone after apply.
        status, _ = make_request_port(self.PORT, "GET", f"/api/memory/proposal/{pid}")
        self.assertEqual(status, 404)

    def test_dismiss_proposal_removes_without_writing(self):
        import memory_ops, memory_store
        body = memory_ops._serialize_proposal("x", [
            {"page": "internal-organization", "action": "replace", "content": "X"},
        ], source="lint")
        pid = memory_store.write_proposal(body, kind="lint")
        status, body_resp = make_request_port(self.PORT, "POST", f"/api/memory/proposal/{pid}/dismiss", {})
        self.assertEqual(status, 200)
        self.assertTrue(body_resp["dismissed"])
        _, page = make_request_port(self.PORT, "GET", "/api/memory/page/internal-organization")
        self.assertNotIn("\nX\n", page["content"])

    def test_ask_apply_routes_mixed_batch(self):
        # Mixed batch: one memory op + one bogus card op. Memory op should
        # apply; card op lands in skipped — but we proved memory ops are
        # actually dispatched (not silently dropped as before).
        ops = [
            {"op": "save_as_source", "title": "via-ask", "content": "anna > brent"},
            {"op": "add_comment", "id": "C-99999", "text": "x"},  # unknown id
        ]
        status, body = make_request_port(self.PORT, "POST", "/api/ask/apply", {"operations": ops})
        self.assertEqual(status, 200)
        applied_ops = [a["op"] for a in body["applied"]]
        self.assertIn("save_as_source", applied_ops)
        # Source should exist on disk.
        _, listing = make_request_port(self.PORT, "GET", "/api/memory/sources")
        self.assertTrue(any("via-ask" in s["id"] for s in listing["sources"]))

    def test_get_memory_config_includes_pending_count(self):
        status, body = make_request_port(self.PORT, "GET", "/api/memory/config")
        self.assertEqual(status, 200)
        self.assertEqual(body["pending_proposals"], 0)
        # Write a lint proposal and re-check.
        import memory_ops, memory_store
        raw = memory_ops._serialize_proposal("x", [
            {"page": "p1", "action": "create", "content": "c"},
        ], source="lint")
        memory_store.write_proposal(raw, kind="lint")
        _, body = make_request_port(self.PORT, "GET", "/api/memory/config")
        self.assertEqual(body["pending_proposals"], 1)


if __name__ == '__main__':
    unittest.main()
