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
        self.assertEqual(data['slug'], 'my-card')
        self.assertEqual(data['assignee'], 'Alice')
        self.assertEqual(data['labels'], ['bug', 'urgent'])

    def test_get_card(self):
        make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {
                'title': 'Get Me',
                'description': 'card body text',
            })
        status, data = make_request_port(8090, 'GET',
            '/api/cards/card-board/ideas/get-me')
        self.assertEqual(status, 200)
        self.assertEqual(data['title'], 'Get Me')
        self.assertIn('card body text', data['body'])

    def test_update_card(self):
        make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/backlog/cards', {
                'title': 'Update Me',
                'description': 'old description',
            })
        status, data = make_request_port(8090, 'PUT',
            '/api/cards/card-board/backlog/update-me', {
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
        make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {
                'title': 'Delete Me',
            })
        status, data = make_request_port(8090, 'DELETE',
            '/api/cards/card-board/ideas/delete-me')
        self.assertEqual(status, 200)
        self.assertEqual(data['deleted'], 'delete-me')
        # Verify it's gone
        status, data = make_request_port(8090, 'GET',
            '/api/cards/card-board/ideas/delete-me')
        self.assertEqual(status, 404)

    def test_move_card(self):
        make_request_port(8090, 'POST',
            '/api/boards/card-board/lists/ideas/cards', {
                'title': 'Move Me',
            })
        status, data = make_request_port(8090, 'PUT',
            '/api/cards/card-board/ideas/move-me/move', {
                'target_list': 'in-progress',
            })
        self.assertEqual(status, 200)
        self.assertEqual(data['to'], 'in-progress')
        # Verify card is in target list
        status, data = make_request_port(8090, 'GET',
            '/api/cards/card-board/in-progress/move-me')
        self.assertEqual(status, 200)
        self.assertEqual(data['title'], 'Move Me')
        # Verify card is gone from source list
        status, data = make_request_port(8090, 'GET',
            '/api/cards/card-board/ideas/move-me')
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

    def test_search_by_assignee(self):
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Task A', 'assignee': 'Charlie'})
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Task B', 'assignee': 'Dana'})
        status, data = make_request_port(8091, 'GET',
            '/api/search?q=charlie')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['title'], 'Task A')

    def test_search_no_results(self):
        make_request_port(8091, 'POST',
            '/api/boards/agg-board/lists/ideas/cards', {
                'title': 'Existing Card'})
        status, data = make_request_port(8091, 'GET',
            '/api/search?q=nonexistent')
        self.assertEqual(status, 200)
        self.assertEqual(data, [])


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
        def _block(type_, **kw):
            return type("B", (), {"type": type_, **kw})()
        def _resp(blocks):
            stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
            return type("R", (), {"content": blocks, "stop_reason": stop})()
        scripted = [
            _resp([_block("tool_use", id="t1", name="create_card", input={
                "board": "alpha", "list": "backlog", "title": "Do thing",
                "confidence": "high", "reason": "explicit",
            })]),
            _resp([_block("tool_use", id="t2", name="finish", input={
                "summary": "Talked about X.",
            })]),
        ]
        class FakeMessages:
            def __init__(self):
                self._queue = list(scripted)
            def create(self, **kwargs):
                return self._queue.pop(0)
        class FakeClient:
            def __init__(self):
                self.messages = FakeMessages()
        # Each request gets a fresh client so the scripted queue restarts.
        import llm_config
        self._orig_get_client = llm_config.get_client
        llm_config.get_client = lambda: FakeClient()

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
        status, body = make_request_port(self.port, "POST", "/api/notes/analyze",
            {"text": "We agreed to do thing.", "title": "Q2"})
        self.assertEqual(status, 200)
        self.assertEqual(body["summary"], "Talked about X.")
        self.assertEqual(len(body["operations"]), 1)
        self.assertTrue(body["note_id"].endswith("-q2"))

    def test_get_note(self):
        status, body = make_request_port(self.port, "POST", "/api/notes/analyze",
            {"text": "body content", "title": "Topic"})
        note_id = body["note_id"]
        # Use raw urlopen because get_note returns text/markdown not JSON
        import urllib.request
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
        _, body = make_request_port(self.port, "POST", "/api/notes/analyze",
            {"text": "x", "title": "T"})
        note_id = body["note_id"]
        ops = body["operations"]
        status, result = make_request_port(self.port, "POST", "/api/notes/apply",
            {"note_id": note_id, "operations": ops})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["applied"]), 1)
        # Card now exists
        status, card = make_request_port(self.port, "GET",
            "/api/cards/alpha/backlog/do-thing")
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main()
