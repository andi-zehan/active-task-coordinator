# Personal Trello Clone — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only kanban board web app with markdown file storage, a Python server, and a vanilla HTML/CSS/JS frontend.

**Architecture:** Data lives as `.md` files with YAML frontmatter in a `data/boards/` folder hierarchy. A single Python script (`server.py`) serves the frontend and exposes a REST API that reads/writes those files. The frontend is a single `index.html` with embedded CSS and JS — no build step, no framework.

**Tech Stack:** Python 3 standard library (http.server, json, pathlib, re), vanilla HTML/CSS/JS, HTML5 Drag and Drop API.

---

## File Structure

```
server.py                  # Python HTTP server + REST API (~600 lines)
index.html                 # Full frontend: HTML + embedded CSS + JS (~2000 lines)
data/                      # Data root (created on first run)
  _boards-order.json       # Board display order
  boards/                  # One subfolder per board
tests/
  test_server.py           # Server API tests using unittest
```

The frontend is one file because the spec requires "single index.html with embedded CSS and JS." The server is one file because the spec requires "single Python script." Tests live in `tests/test_server.py` and use Python's `unittest` + `urllib` to hit the running server.

---

### Task 1: Project Setup & Data Layer Utilities

**Files:**
- Create: `server.py`
- Create: `tests/test_server.py`

This task builds the core functions for reading/writing markdown cards and board metadata — the foundation everything else uses.

- [ ] **Step 1: Create `server.py` with markdown parsing utilities**

```python
#!/usr/bin/env python3
"""Personal Kanban Board — Local Server"""

import json
import os
import re
from datetime import date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DATA_DIR = Path(__file__).parent / "data"
LISTS = ["ideas", "backlog", "in-progress", "done"]


def slugify(title):
    slug = title.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def parse_frontmatter(text):
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', text, re.DOTALL)
    if not match:
        return {}, text
    raw_meta = match.group(1)
    body = match.group(2)
    meta = {}
    current_key = None
    current_list = None
    current_dict = None
    current_dict_key = None

    for line in raw_meta.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue

        indent = len(line) - len(line.lstrip())

        if indent >= 2 and current_dict_key is not None:
            kv_match = re.match(r'(\w+):\s*(.*)', stripped)
            if kv_match:
                meta[current_dict_key][kv_match.group(1)] = kv_match.group(2).strip('"').strip("'")
                continue

        if indent >= 2 and current_key is not None and stripped.startswith('- '):
            item_text = stripped[2:]
            if current_list is not None:
                if ':' in item_text and not item_text.startswith('http'):
                    item = {}
                    k, v = item_text.split(':', 1)
                    item[k.strip()] = v.strip().strip('"').strip("'")
                    current_list.append(item)
                else:
                    current_list.append(item_text.strip('"').strip("'"))
                continue

        current_dict_key = None

        kv_match = re.match(r'^(\w[\w_]*):\s*(.*)', stripped)
        if kv_match:
            key = kv_match.group(1)
            value = kv_match.group(2).strip()

            if value == '':
                meta[key] = {}
                current_dict_key = key
                current_key = key
                current_list = None
                continue

            if value.startswith('[') and value.endswith(']'):
                inner = value[1:-1]
                if inner.strip():
                    meta[key] = [v.strip().strip('"').strip("'") for v in inner.split(',')]
                else:
                    meta[key] = []
                current_key = key
                current_list = None
                continue

            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            meta[key] = value
            current_key = key
            current_list = None
            continue

        if stripped.startswith('- ') and current_key is not None:
            if not isinstance(meta.get(current_key), list):
                meta[current_key] = []
            current_list = meta[current_key]
            item_text = stripped[2:]
            current_list.append(item_text.strip('"').strip("'"))
            continue

    return meta, body


def serialize_frontmatter(meta, body):
    lines = ['---']
    for key, value in meta.items():
        if isinstance(value, list):
            if not value:
                lines.append(f'{key}: []')
            elif all(isinstance(v, str) for v in value):
                items = ', '.join(value)
                lines.append(f'{key}: [{items}]')
            else:
                lines.append(f'{key}:')
                for item in value:
                    if isinstance(item, dict):
                        first = True
                        for k, v in item.items():
                            if first:
                                lines.append(f'  - {k}: {v}')
                                first = False
                            else:
                                lines.append(f'    {k}: {v}')
                    else:
                        lines.append(f'  - {item}')
        elif isinstance(value, dict):
            lines.append(f'{key}:')
            for k, v in value.items():
                lines.append(f'  {k}: {v}')
        else:
            lines.append(f'{key}: {value}')
    lines.append('---')
    lines.append('')
    return '\n'.join(lines) + body


def read_json(path):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return []


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def read_card(board_slug, list_slug, card_slug):
    card_path = DATA_DIR / "boards" / board_slug / list_slug / f"{card_slug}.md"
    if not card_path.exists():
        return None
    text = card_path.read_text(encoding='utf-8')
    meta, body = parse_frontmatter(text)
    meta['slug'] = card_slug
    meta['board'] = board_slug
    meta['list'] = list_slug
    meta['body'] = body
    return meta


def write_card(board_slug, list_slug, card_slug, meta, body):
    card_dir = DATA_DIR / "boards" / board_slug / list_slug
    card_dir.mkdir(parents=True, exist_ok=True)
    card_path = card_dir / f"{card_slug}.md"
    write_meta = {k: v for k, v in meta.items() if k not in ('slug', 'board', 'list', 'body')}
    card_path.write_text(serialize_frontmatter(write_meta, body), encoding='utf-8')


def read_board_meta(board_slug):
    board_path = DATA_DIR / "boards" / board_slug / "_board.md"
    if not board_path.exists():
        return None
    text = board_path.read_text(encoding='utf-8')
    meta, _ = parse_frontmatter(text)
    meta['slug'] = board_slug
    return meta


def write_board_meta(board_slug, meta):
    board_dir = DATA_DIR / "boards" / board_slug
    board_dir.mkdir(parents=True, exist_ok=True)
    board_path = board_dir / "_board.md"
    write_meta = {k: v for k, v in meta.items() if k != 'slug'}
    board_path.write_text(serialize_frontmatter(write_meta, ''), encoding='utf-8')
    for list_name in LISTS:
        list_dir = board_dir / list_name
        list_dir.mkdir(parents=True, exist_ok=True)
        order_file = list_dir / "_order.json"
        if not order_file.exists():
            write_json(order_file, [])


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    boards_order = DATA_DIR / "_boards-order.json"
    if not boards_order.exists():
        write_json(boards_order, [])
```

- [ ] **Step 2: Create `tests/test_server.py` with data layer tests**

```python
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
```

- [ ] **Step 3: Run tests to verify data layer works**

Run: `python -m pytest tests/test_server.py -v`
Expected: All tests pass (TestSlugify, TestFrontmatter, TestDataLayer).

- [ ] **Step 4: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: add data layer with markdown parsing and card/board read/write"
```

---

### Task 2: HTTP Server & Board API Endpoints

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

Adds the HTTP server class and the board CRUD endpoints (`GET/POST/PUT/DELETE /api/boards`).

- [ ] **Step 1: Write failing tests for board API**

Add to `tests/test_server.py`:

```python
import threading
import urllib.request
import urllib.error
import time


def make_request(method, path, body=None):
    url = f"http://localhost:8089{path}"
    data = json.dumps(body).encode() if body else None
    headers = {'Content-Type': 'application/json'} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode()) if e.read() else {}


class TestBoardAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        server.DATA_DIR = Path(cls.tmp) / "data"
        server.ensure_data_dir()
        cls.httpd = HTTPServer(('localhost', 8089), server.RequestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp)

    def setUp(self):
        if (server.DATA_DIR / "boards").exists():
            shutil.rmtree(server.DATA_DIR / "boards")
        (server.DATA_DIR / "boards").mkdir()
        server.write_json(server.DATA_DIR / "_boards-order.json", [])

    def test_list_boards_empty(self):
        status, data = make_request('GET', '/api/boards')
        self.assertEqual(status, 200)
        self.assertEqual(data, [])

    def test_create_board(self):
        status, data = make_request('POST', '/api/boards', {
            'name': 'My Project',
            'description': 'Test project',
            'color': '#FF0000'
        })
        self.assertEqual(status, 201)
        self.assertEqual(data['slug'], 'my-project')
        status, boards = make_request('GET', '/api/boards')
        self.assertEqual(len(boards), 1)
        self.assertEqual(boards[0]['name'], 'My Project')

    def test_get_board(self):
        make_request('POST', '/api/boards', {'name': 'Proj', 'color': '#000'})
        status, data = make_request('GET', '/api/boards/proj')
        self.assertEqual(status, 200)
        self.assertEqual(data['name'], 'Proj')
        self.assertIn('lists', data)
        self.assertEqual(len(data['lists']), 4)

    def test_update_board(self):
        make_request('POST', '/api/boards', {'name': 'Old Name', 'color': '#000'})
        status, data = make_request('PUT', '/api/boards/old-name', {
            'name': 'New Name', 'color': '#FFF'
        })
        self.assertEqual(status, 200)
        meta = server.read_board_meta('old-name')
        self.assertEqual(meta['name'], 'New Name')

    def test_delete_board(self):
        make_request('POST', '/api/boards', {'name': 'To Delete', 'color': '#000'})
        status, _ = make_request('DELETE', '/api/boards/to-delete')
        self.assertEqual(status, 200)
        status, boards = make_request('GET', '/api/boards')
        self.assertEqual(len(boards), 0)

    def test_get_nonexistent_board(self):
        status, _ = make_request('GET', '/api/boards/nope')
        self.assertEqual(status, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::TestBoardAPI -v`
Expected: FAIL — `RequestHandler` not defined.

- [ ] **Step 3: Implement the HTTP server and board endpoints**

Add to `server.py`, after the data layer functions:

```python
from http.server import HTTPServer


class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_error(self, status, message):
        self._send_json({'error': message}, status)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        query = parse_qs(parsed.query)

        # Static files
        if method == 'GET' and (path == '' or path == '/'):
            return self._serve_file('index.html', 'text/html')

        # API routes
        if not path.startswith('/api/'):
            if method == 'GET':
                return self._serve_file(path.lstrip('/'), self._guess_mime(path))
            return self._send_error(404, 'Not found')

        parts = path[5:].split('/')  # strip /api/

        # /api/boards
        if parts == ['boards']:
            if method == 'GET':
                return self._handle_list_boards()
            if method == 'POST':
                return self._handle_create_board()

        # /api/boards/:board
        if len(parts) == 2 and parts[0] == 'boards':
            board_slug = parts[1]
            if method == 'GET':
                return self._handle_get_board(board_slug)
            if method == 'PUT':
                return self._handle_update_board(board_slug)
            if method == 'DELETE':
                return self._handle_delete_board(board_slug)

        # /api/boards/:board/lists/:list/cards
        if len(parts) == 5 and parts[0] == 'boards' and parts[2] == 'lists' and parts[4] == 'cards':
            board_slug, list_slug = parts[1], parts[3]
            if method == 'GET':
                return self._handle_list_cards(board_slug, list_slug)
            if method == 'POST':
                return self._handle_create_card(board_slug, list_slug)

        # /api/cards/:board/:list/:card
        if len(parts) == 4 and parts[0] == 'cards':
            board_slug, list_slug, card_slug = parts[1], parts[2], parts[3]
            if method == 'GET':
                return self._handle_get_card(board_slug, list_slug, card_slug)
            if method == 'PUT':
                return self._handle_update_card(board_slug, list_slug, card_slug)
            if method == 'DELETE':
                return self._handle_delete_card(board_slug, list_slug, card_slug)

        # /api/cards/:board/:list/:card/move
        if len(parts) == 5 and parts[0] == 'cards' and parts[4] == 'move':
            if method == 'PUT':
                return self._handle_move_card(parts[1], parts[2], parts[3])

        # /api/dashboard
        if parts == ['dashboard'] and method == 'GET':
            return self._handle_dashboard()

        # /api/calendar/:year/:month
        if len(parts) == 3 and parts[0] == 'calendar' and method == 'GET':
            return self._handle_calendar(parts[1], parts[2])

        # /api/search
        if parts == ['search'] and method == 'GET':
            return self._handle_search(query)

        return self._send_error(404, 'Not found')

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def do_PUT(self):
        self._route('PUT')

    def do_DELETE(self):
        self._route('DELETE')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        pass

    def _guess_mime(self, path):
        if path.endswith('.js'):
            return 'application/javascript'
        if path.endswith('.css'):
            return 'text/css'
        if path.endswith('.html'):
            return 'text/html'
        if path.endswith('.json'):
            return 'application/json'
        return 'application/octet-stream'

    def _serve_file(self, filepath, mime):
        fpath = Path(__file__).parent / filepath
        if not fpath.exists():
            return self._send_error(404, 'Not found')
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.end_headers()
        self.wfile.write(fpath.read_bytes())

    # --- Board handlers ---

    def _handle_list_boards(self):
        order = read_json(DATA_DIR / "_boards-order.json")
        boards = []
        for slug in order:
            meta = read_board_meta(slug)
            if meta:
                boards.append(meta)
        self._send_json(boards)

    def _handle_create_board(self):
        body = self._read_body()
        name = body.get('name', '').strip()
        if not name:
            return self._send_error(400, 'Name is required')
        slug = slugify(name)
        board_dir = DATA_DIR / "boards" / slug
        if board_dir.exists():
            return self._send_error(409, 'Board already exists')
        write_board_meta(slug, {
            'name': name,
            'description': body.get('description', ''),
            'color': body.get('color', '#4A90D9'),
        })
        order = read_json(DATA_DIR / "_boards-order.json")
        order.append(slug)
        write_json(DATA_DIR / "_boards-order.json", order)
        self._send_json({'slug': slug, 'name': name}, 201)

    def _handle_get_board(self, board_slug):
        meta = read_board_meta(board_slug)
        if not meta:
            return self._send_error(404, 'Board not found')
        lists = []
        for list_name in LISTS:
            order = read_json(DATA_DIR / "boards" / board_slug / list_name / "_order.json")
            cards = []
            for card_slug in order:
                card = read_card(board_slug, list_name, card_slug)
                if card:
                    card.pop('body', None)
                    cards.append(card)
            lists.append({'name': list_name, 'cards': cards})
        meta['lists'] = lists
        self._send_json(meta)

    def _handle_update_board(self, board_slug):
        meta = read_board_meta(board_slug)
        if not meta:
            return self._send_error(404, 'Board not found')
        body = self._read_body()
        if 'name' in body:
            meta['name'] = body['name']
        if 'description' in body:
            meta['description'] = body['description']
        if 'color' in body:
            meta['color'] = body['color']
        write_board_meta(board_slug, meta)
        self._send_json(meta)

    def _handle_delete_board(self, board_slug):
        board_dir = DATA_DIR / "boards" / board_slug
        if not board_dir.exists():
            return self._send_error(404, 'Board not found')
        shutil.rmtree(board_dir)
        order = read_json(DATA_DIR / "_boards-order.json")
        order = [s for s in order if s != board_slug]
        write_json(DATA_DIR / "_boards-order.json", order)
        self._send_json({'ok': True})
```

Add `import shutil` at the top of `server.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: add HTTP server with board CRUD endpoints"
```

---

### Task 3: Card API Endpoints

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

Adds card CRUD endpoints: create, read, update, delete, and move.

- [ ] **Step 1: Write failing tests for card API**

Add to `tests/test_server.py`:

```python
class TestCardAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        server.DATA_DIR = Path(cls.tmp) / "data"
        server.ensure_data_dir()
        cls.httpd = HTTPServer(('localhost', 8090), server.RequestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp)

    def setUp(self):
        if (server.DATA_DIR / "boards").exists():
            shutil.rmtree(server.DATA_DIR / "boards")
        (server.DATA_DIR / "boards").mkdir()
        server.write_json(server.DATA_DIR / "_boards-order.json", [])
        make_request_port(8090, 'POST', '/api/boards', {'name': 'Test Proj', 'color': '#000'})

    def test_create_card(self):
        status, data = make_request_port(8090, 'POST', '/api/boards/test-proj/lists/ideas/cards', {
            'title': 'My Task',
            'assignee': 'Alice',
            'labels': ['bug'],
            'due': '2026-05-01',
            'description': 'Do the thing',
        })
        self.assertEqual(status, 201)
        self.assertEqual(data['slug'], 'my-task')
        order = server.read_json(server.DATA_DIR / "boards" / "test-proj" / "ideas" / "_order.json")
        self.assertIn('my-task', order)

    def test_get_card(self):
        make_request_port(8090, 'POST', '/api/boards/test-proj/lists/ideas/cards', {
            'title': 'Read Me', 'description': 'Body text',
        })
        status, data = make_request_port(8090, 'GET', '/api/cards/test-proj/ideas/read-me')
        self.assertEqual(status, 200)
        self.assertEqual(data['title'], 'Read Me')
        self.assertIn('Body text', data['body'])

    def test_update_card(self):
        make_request_port(8090, 'POST', '/api/boards/test-proj/lists/ideas/cards', {
            'title': 'Update Me',
        })
        status, data = make_request_port(8090, 'PUT', '/api/cards/test-proj/ideas/update-me', {
            'assignee': 'Bob', 'labels': ['frontend'],
        })
        self.assertEqual(status, 200)
        card = server.read_card('test-proj', 'ideas', 'update-me')
        self.assertEqual(card['assignee'], 'Bob')

    def test_delete_card(self):
        make_request_port(8090, 'POST', '/api/boards/test-proj/lists/ideas/cards', {
            'title': 'Delete Me',
        })
        status, _ = make_request_port(8090, 'DELETE', '/api/cards/test-proj/ideas/delete-me')
        self.assertEqual(status, 200)
        order = server.read_json(server.DATA_DIR / "boards" / "test-proj" / "ideas" / "_order.json")
        self.assertNotIn('delete-me', order)

    def test_move_card(self):
        make_request_port(8090, 'POST', '/api/boards/test-proj/lists/ideas/cards', {
            'title': 'Move Me',
        })
        status, _ = make_request_port(8090, 'PUT', '/api/cards/test-proj/ideas/move-me/move', {
            'target_list': 'in-progress', 'position': 0,
        })
        self.assertEqual(status, 200)
        old_order = server.read_json(server.DATA_DIR / "boards" / "test-proj" / "ideas" / "_order.json")
        new_order = server.read_json(server.DATA_DIR / "boards" / "test-proj" / "in-progress" / "_order.json")
        self.assertNotIn('move-me', old_order)
        self.assertIn('move-me', new_order)
        moved = server.read_card('test-proj', 'in-progress', 'move-me')
        self.assertIsNotNone(moved)

    def test_list_cards(self):
        make_request_port(8090, 'POST', '/api/boards/test-proj/lists/backlog/cards', {'title': 'A'})
        make_request_port(8090, 'POST', '/api/boards/test-proj/lists/backlog/cards', {'title': 'B'})
        status, data = make_request_port(8090, 'GET', '/api/boards/test-proj/lists/backlog/cards')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 2)
```

Also add the port-parameterized helper at the top of the file:

```python
def make_request_port(port, method, path, body=None):
    url = f"http://localhost:{port}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {'Content-Type': 'application/json'} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        return e.code, json.loads(body_bytes.decode()) if body_bytes else {}


def make_request(method, path, body=None):
    return make_request_port(8089, method, path, body)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::TestCardAPI -v`
Expected: FAIL — card handler methods not defined.

- [ ] **Step 3: Implement card handlers in `server.py`**

Add these methods to the `RequestHandler` class:

```python
    # --- Card handlers ---

    def _handle_list_cards(self, board_slug, list_slug):
        if list_slug not in LISTS:
            return self._send_error(400, f'Invalid list: {list_slug}')
        order = read_json(DATA_DIR / "boards" / board_slug / list_slug / "_order.json")
        cards = []
        for card_slug in order:
            card = read_card(board_slug, list_slug, card_slug)
            if card:
                card.pop('body', None)
                cards.append(card)
        self._send_json(cards)

    def _handle_create_card(self, board_slug, list_slug):
        if list_slug not in LISTS:
            return self._send_error(400, f'Invalid list: {list_slug}')
        board_meta = read_board_meta(board_slug)
        if not board_meta:
            return self._send_error(404, 'Board not found')
        body = self._read_body()
        title = body.get('title', '').strip()
        if not title:
            return self._send_error(400, 'Title is required')
        slug = slugify(title)
        today = date.today().isoformat()
        meta = {
            'title': title,
            'assignee': body.get('assignee', ''),
            'labels': body.get('labels', []),
            'due': body.get('due', ''),
            'created': today,
            'updated': today,
            'relations': body.get('relations', []),
            'custom_fields': body.get('custom_fields', {}),
            'attachments': body.get('attachments', []),
        }
        description = body.get('description', '')
        card_body = f"\n## Description\n\n{description}\n\n## Checklist\n\n\n## Comments\n\n"
        write_card(board_slug, list_slug, slug, meta, card_body)
        order_path = DATA_DIR / "boards" / board_slug / list_slug / "_order.json"
        order = read_json(order_path)
        order.append(slug)
        write_json(order_path, order)
        self._send_json({'slug': slug, 'title': title}, 201)

    def _handle_get_card(self, board_slug, list_slug, card_slug):
        card = read_card(board_slug, list_slug, card_slug)
        if not card:
            return self._send_error(404, 'Card not found')
        self._send_json(card)

    def _handle_update_card(self, board_slug, list_slug, card_slug):
        card = read_card(board_slug, list_slug, card_slug)
        if not card:
            return self._send_error(404, 'Card not found')
        body = self._read_body()
        for key in ['title', 'assignee', 'labels', 'due', 'relations', 'custom_fields', 'attachments']:
            if key in body:
                card[key] = body[key]
        if 'description' in body:
            card['body'] = re.sub(
                r'(## Description\n\n).*?(\n\n## )',
                rf'\g<1>{body["description"]}\g<2>',
                card['body'],
                count=1,
                flags=re.DOTALL,
            )
        if 'checklist' in body:
            card['body'] = re.sub(
                r'(## Checklist\n\n).*?(\n\n## )',
                rf'\g<1>{body["checklist"]}\g<2>',
                card['body'],
                count=1,
                flags=re.DOTALL,
            )
        if 'comment' in body:
            today = date.today().isoformat()
            card['body'] = card['body'].rstrip() + f"\n\n**{today} - Me:**\n{body['comment']}\n"
        card['updated'] = date.today().isoformat()
        write_card(board_slug, list_slug, card_slug, card, card['body'])
        self._send_json(card)

    def _handle_delete_card(self, board_slug, list_slug, card_slug):
        card_path = DATA_DIR / "boards" / board_slug / list_slug / f"{card_slug}.md"
        if not card_path.exists():
            return self._send_error(404, 'Card not found')
        card_path.unlink()
        order_path = DATA_DIR / "boards" / board_slug / list_slug / "_order.json"
        order = read_json(order_path)
        order = [s for s in order if s != card_slug]
        write_json(order_path, order)
        self._send_json({'ok': True})

    def _handle_move_card(self, board_slug, list_slug, card_slug):
        card = read_card(board_slug, list_slug, card_slug)
        if not card:
            return self._send_error(404, 'Card not found')
        body = self._read_body()
        target_list = body.get('target_list', list_slug)
        target_board = body.get('target_board', board_slug)
        position = body.get('position', -1)

        if target_list not in LISTS:
            return self._send_error(400, f'Invalid target list: {target_list}')

        # Remove from source
        src_order_path = DATA_DIR / "boards" / board_slug / list_slug / "_order.json"
        src_order = read_json(src_order_path)
        src_order = [s for s in src_order if s != card_slug]
        write_json(src_order_path, src_order)

        # Move the file
        src_path = DATA_DIR / "boards" / board_slug / list_slug / f"{card_slug}.md"
        dst_dir = DATA_DIR / "boards" / target_board / target_list
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_path = dst_dir / f"{card_slug}.md"
        src_path.rename(dst_path)

        # Add to target
        dst_order_path = dst_dir / "_order.json"
        dst_order = read_json(dst_order_path)
        if position < 0 or position >= len(dst_order):
            dst_order.append(card_slug)
        else:
            dst_order.insert(position, card_slug)
        write_json(dst_order_path, dst_order)

        self._send_json({'ok': True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: add card CRUD and move endpoints"
```

---

### Task 4: Dashboard, Calendar, and Search Endpoints

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

Adds the aggregation endpoints: dashboard (today/this week), calendar (by month), and search.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_server.py`:

```python
class TestAggregationAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        server.DATA_DIR = Path(cls.tmp) / "data"
        server.ensure_data_dir()
        cls.httpd = HTTPServer(('localhost', 8091), server.RequestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()
        # Seed data
        make_request_port(8091, 'POST', '/api/boards', {'name': 'Proj A', 'color': '#000'})
        today = date.today().isoformat()
        future = (date.today() + timedelta(days=3)).isoformat()
        past = (date.today() - timedelta(days=2)).isoformat()
        make_request_port(8091, 'POST', '/api/boards/proj-a/lists/ideas/cards', {
            'title': 'Due Today', 'due': today, 'assignee': 'Alice',
        })
        make_request_port(8091, 'POST', '/api/boards/proj-a/lists/backlog/cards', {
            'title': 'Due This Week', 'due': future, 'labels': ['frontend'],
        })
        make_request_port(8091, 'POST', '/api/boards/proj-a/lists/in-progress/cards', {
            'title': 'Overdue', 'due': past,
        })
        make_request_port(8091, 'POST', '/api/boards/proj-a/lists/done/cards', {
            'title': 'No Due Date',
        })

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp)

    def test_dashboard(self):
        status, data = make_request_port(8091, 'GET', '/api/dashboard')
        self.assertEqual(status, 200)
        self.assertIn('today', data)
        self.assertIn('this_week', data)
        self.assertIn('overdue', data)
        today_titles = [c['title'] for c in data['today']]
        self.assertIn('Due Today', today_titles)
        overdue_titles = [c['title'] for c in data['overdue']]
        self.assertIn('Overdue', overdue_titles)

    def test_calendar(self):
        year = date.today().year
        month = date.today().month
        status, data = make_request_port(8091, 'GET', f'/api/calendar/{year}/{month}')
        self.assertEqual(status, 200)
        self.assertIn('cards', data)
        self.assertTrue(len(data['cards']) >= 1)

    def test_search_by_title(self):
        status, data = make_request_port(8091, 'GET', '/api/search?q=Overdue')
        self.assertEqual(status, 200)
        self.assertTrue(len(data) >= 1)
        self.assertEqual(data[0]['title'], 'Overdue')

    def test_search_by_assignee(self):
        status, data = make_request_port(8091, 'GET', '/api/search?q=Alice')
        self.assertEqual(status, 200)
        self.assertTrue(len(data) >= 1)

    def test_search_no_results(self):
        status, data = make_request_port(8091, 'GET', '/api/search?q=zzzznothing')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::TestAggregationAPI -v`
Expected: FAIL — handler methods not defined.

- [ ] **Step 3: Implement aggregation handlers**

Add to `RequestHandler` class in `server.py`:

```python
    # --- Aggregation handlers ---

    def _get_all_cards(self):
        cards = []
        boards_order = read_json(DATA_DIR / "_boards-order.json")
        for board_slug in boards_order:
            for list_name in LISTS:
                order = read_json(DATA_DIR / "boards" / board_slug / list_name / "_order.json")
                for card_slug in order:
                    card = read_card(board_slug, list_name, card_slug)
                    if card:
                        cards.append(card)
        return cards

    def _handle_dashboard(self):
        all_cards = self._get_all_cards()
        today = date.today()
        week_end = today + timedelta(days=7)
        result = {'today': [], 'this_week': [], 'overdue': []}
        for card in all_cards:
            due = card.get('due', '')
            if not due:
                continue
            try:
                due_date = date.fromisoformat(due)
            except ValueError:
                continue
            card_summary = {k: v for k, v in card.items() if k != 'body'}
            if due_date < today:
                result['overdue'].append(card_summary)
            elif due_date == today:
                result['today'].append(card_summary)
            elif due_date <= week_end:
                result['this_week'].append(card_summary)
        self._send_json(result)

    def _handle_calendar(self, year_str, month_str):
        try:
            year, month = int(year_str), int(month_str)
        except ValueError:
            return self._send_error(400, 'Invalid year/month')
        all_cards = self._get_all_cards()
        cards = []
        for card in all_cards:
            due = card.get('due', '')
            if not due:
                continue
            try:
                due_date = date.fromisoformat(due)
            except ValueError:
                continue
            if due_date.year == year and due_date.month == month:
                card_summary = {k: v for k, v in card.items() if k != 'body'}
                cards.append(card_summary)
        self._send_json({'year': year, 'month': month, 'cards': cards})

    def _handle_search(self, query_params):
        q = query_params.get('q', [''])[0].lower()
        if not q:
            return self._send_json([])
        all_cards = self._get_all_cards()
        results = []
        for card in all_cards:
            searchable = ' '.join([
                card.get('title', ''),
                card.get('assignee', ''),
                card.get('body', ''),
                ' '.join(card.get('labels', [])),
            ]).lower()
            if q in searchable:
                card_summary = {k: v for k, v in card.items() if k != 'body'}
                results.append(card_summary)
        self._send_json(results)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: add dashboard, calendar, and search endpoints"
```

---

### Task 5: Server Main Entry Point

**Files:**
- Modify: `server.py`

Adds the `__main__` block so the server can be launched with `python server.py`.

- [ ] **Step 1: Add main block to `server.py`**

Add at the very bottom of `server.py`:

```python
if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ensure_data_dir()
    print(f"Kanban server running at http://localhost:{port}")
    print(f"Data directory: {DATA_DIR.resolve()}")
    print("Press Ctrl+C to stop.")
    httpd = HTTPServer(('localhost', port), RequestHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()
```

- [ ] **Step 2: Test manually**

Run: `python server.py`
Expected: Prints "Kanban server running at http://localhost:8080" and serves on that port. Ctrl+C stops it.

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat: add server main entry point"
```

---

### Task 6: Frontend — HTML Shell & CSS

**Files:**
- Create: `index.html`

Builds the HTML structure and all CSS for the app. No JS yet — just the visual skeleton with all four views (board, dashboard, calendar, table), the top bar, and the card detail modal.

- [ ] **Step 1: Create `index.html` with HTML and CSS**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kanban Board</title>
<style>
/* --- Reset & Base --- */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f6f8; color: #333; min-width: 1200px; }
button { cursor: pointer; font-family: inherit; }
input, textarea, select { font-family: inherit; font-size: inherit; }

/* --- Top Bar --- */
.topbar {
  display: flex; align-items: center; gap: 16px;
  background: #fff; border-bottom: 1px solid #ddd; padding: 8px 20px; height: 48px;
}
.topbar-title { font-weight: 700; font-size: 18px; color: #333; }
.topbar-board-select {
  padding: 4px 8px; border: 1px solid #ddd; border-radius: 4px;
  background: #fff; font-size: 14px;
}
.topbar-views { display: flex; gap: 4px; margin-left: auto; }
.topbar-views button {
  padding: 6px 12px; border: 1px solid #ddd; border-radius: 4px;
  background: #fff; font-size: 13px; color: #555;
}
.topbar-views button.active { background: #4A90D9; color: #fff; border-color: #4A90D9; }
.topbar-search {
  padding: 4px 10px; border: 1px solid #ddd; border-radius: 4px;
  width: 200px; font-size: 13px;
}
.topbar-new-btn {
  padding: 6px 12px; border: none; border-radius: 4px;
  background: #4A90D9; color: #fff; font-size: 13px; font-weight: 600;
}
.topbar-new-btn:hover { background: #357ABD; }

/* --- Search Results Dropdown --- */
.search-dropdown {
  position: absolute; top: 48px; right: 100px; width: 360px;
  background: #fff; border: 1px solid #ddd; border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-height: 400px; overflow-y: auto;
  z-index: 100; display: none;
}
.search-dropdown.visible { display: block; }
.search-result {
  padding: 10px 14px; border-bottom: 1px solid #f0f0f0; cursor: pointer;
}
.search-result:hover { background: #f5f7fa; }
.search-result-title { font-weight: 600; font-size: 14px; }
.search-result-meta { font-size: 12px; color: #888; margin-top: 2px; }

/* --- Filter Bar --- */
.filter-bar {
  display: flex; align-items: center; gap: 8px; padding: 8px 20px;
  background: #fff; border-bottom: 1px solid #eee; display: none;
}
.filter-bar.visible { display: flex; }
.filter-bar select, .filter-bar input {
  padding: 4px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px;
}
.filter-chips { display: flex; gap: 4px; margin-left: 8px; }
.filter-chip {
  display: flex; align-items: center; gap: 4px;
  padding: 2px 8px; background: #e8f0fe; border-radius: 12px; font-size: 12px;
}
.filter-chip-remove { cursor: pointer; font-weight: bold; color: #666; }

/* --- Board View --- */
.view { display: none; padding: 20px; }
.view.active { display: block; }
.board-columns {
  display: flex; gap: 16px; align-items: flex-start; min-height: calc(100vh - 120px);
}
.board-column {
  flex: 1; min-width: 280px; max-width: 320px;
  background: #ebecf0; border-radius: 8px; padding: 10px;
}
.board-column-header {
  font-weight: 700; font-size: 14px; text-transform: uppercase; color: #555;
  padding: 4px 6px 10px; display: flex; justify-content: space-between; align-items: center;
}
.board-column-count {
  background: #d5d8dc; border-radius: 10px; padding: 1px 8px;
  font-size: 12px; font-weight: 600;
}
.board-cards { display: flex; flex-direction: column; gap: 8px; min-height: 40px; }
.board-card {
  background: #fff; border-radius: 6px; padding: 10px 12px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.1); cursor: pointer; transition: box-shadow 0.15s;
}
.board-card:hover { box-shadow: 0 2px 6px rgba(0,0,0,0.15); }
.board-card.dragging { opacity: 0.5; }
.board-card-title { font-weight: 600; font-size: 14px; margin-bottom: 6px; }
.board-card-labels { display: flex; gap: 4px; margin-bottom: 6px; flex-wrap: wrap; }
.board-card-label {
  width: 32px; height: 6px; border-radius: 3px;
}
.board-card-meta { display: flex; gap: 10px; align-items: center; font-size: 12px; color: #888; }
.board-card-meta .overdue { color: #e74c3c; font-weight: 600; }
.board-card-checklist { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #888; }
.board-card-checklist-bar {
  width: 40px; height: 4px; background: #ddd; border-radius: 2px; overflow: hidden;
}
.board-card-checklist-fill { height: 100%; background: #27ae60; border-radius: 2px; }
.drop-zone { border: 2px dashed #4A90D9; border-radius: 6px; background: #e8f0fe; min-height: 40px; }

/* --- Dashboard View --- */
.dashboard-section { margin-bottom: 24px; }
.dashboard-section-title { font-size: 16px; font-weight: 700; margin-bottom: 12px; color: #333; }
.dashboard-group { margin-bottom: 16px; }
.dashboard-group-title { font-size: 13px; font-weight: 600; color: #888; margin-bottom: 6px; }
.dashboard-cards { display: flex; flex-direction: column; gap: 6px; }
.dashboard-card {
  display: flex; align-items: center; gap: 12px; padding: 10px 14px;
  background: #fff; border-radius: 6px; box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  cursor: pointer;
}
.dashboard-card:hover { box-shadow: 0 2px 6px rgba(0,0,0,0.12); }
.dashboard-card.overdue { border-left: 3px solid #e74c3c; }
.dashboard-card-title { font-weight: 600; font-size: 14px; flex: 1; }
.dashboard-card-meta { font-size: 12px; color: #888; }

/* --- Calendar View --- */
.calendar-header {
  display: flex; align-items: center; gap: 16px; margin-bottom: 16px;
}
.calendar-header button {
  padding: 6px 12px; border: 1px solid #ddd; border-radius: 4px;
  background: #fff; font-size: 14px;
}
.calendar-header button:hover { background: #f0f0f0; }
.calendar-month-title { font-size: 18px; font-weight: 700; }
.calendar-grid {
  display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px;
  background: #ddd; border-radius: 8px; overflow: hidden;
}
.calendar-day-header {
  background: #f5f6f8; padding: 8px; text-align: center;
  font-size: 12px; font-weight: 600; color: #888; text-transform: uppercase;
}
.calendar-day {
  background: #fff; min-height: 100px; padding: 6px; vertical-align: top;
}
.calendar-day.other-month { background: #fafafa; color: #ccc; }
.calendar-day.today { background: #f0f6ff; }
.calendar-day-num { font-size: 12px; font-weight: 600; margin-bottom: 4px; color: #555; }
.calendar-day-card {
  font-size: 11px; padding: 3px 5px; background: #e8f0fe; border-radius: 3px;
  margin-bottom: 2px; cursor: pointer; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis;
}
.calendar-day-card:hover { background: #d0e1f9; }
.calendar-day-card.overdue { background: #fdecea; }

/* --- Table View --- */
.table-wrap { overflow-x: auto; }
.data-table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; }
.data-table th {
  text-align: left; padding: 10px 14px; background: #f5f6f8;
  font-size: 12px; font-weight: 700; text-transform: uppercase; color: #888;
  border-bottom: 2px solid #ddd; cursor: pointer; user-select: none;
}
.data-table th:hover { background: #e8eaed; }
.data-table td { padding: 10px 14px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
.data-table tr { cursor: pointer; }
.data-table tr:hover td { background: #f5f7fa; }
.table-label {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 11px; margin-right: 3px; color: #fff;
}

/* --- Card Detail Modal --- */
.modal-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5); z-index: 200; display: none;
  align-items: flex-start; justify-content: center; padding-top: 60px;
}
.modal-overlay.visible { display: flex; }
.modal {
  background: #fff; border-radius: 8px; width: 680px; max-height: calc(100vh - 120px);
  overflow-y: auto; padding: 24px; position: relative;
}
.modal-close {
  position: absolute; top: 12px; right: 16px;
  background: none; border: none; font-size: 22px; color: #888; cursor: pointer;
}
.modal-close:hover { color: #333; }
.modal-title {
  font-size: 20px; font-weight: 700; margin-bottom: 16px; border: none;
  width: 100%; outline: none; padding: 4px 0;
}
.modal-section { margin-bottom: 16px; }
.modal-section-label {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  color: #888; margin-bottom: 6px;
}
.modal-description {
  min-height: 60px; white-space: pre-wrap; line-height: 1.6;
  padding: 8px; background: #fafafa; border-radius: 4px; font-size: 14px;
}
.modal-description-edit {
  width: 100%; min-height: 100px; padding: 8px;
  border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical;
}
.modal-meta-row { display: flex; gap: 16px; margin-bottom: 8px; align-items: center; }
.modal-meta-label { font-size: 12px; font-weight: 600; color: #888; min-width: 80px; }
.modal-meta-value { font-size: 14px; }
.modal-meta-value input, .modal-meta-value select {
  padding: 4px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px;
}
.modal-checklist-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
.modal-checklist-item input[type="checkbox"] { width: 16px; height: 16px; }
.modal-checklist-item span { font-size: 14px; }
.modal-checklist-item.checked span { text-decoration: line-through; color: #aaa; }
.modal-add-btn {
  padding: 4px 10px; border: 1px dashed #ccc; border-radius: 4px;
  background: none; font-size: 12px; color: #888; margin-top: 4px;
}
.modal-add-btn:hover { border-color: #999; color: #555; }
.modal-labels { display: flex; gap: 4px; flex-wrap: wrap; }
.modal-label-tag {
  padding: 2px 8px; border-radius: 3px; font-size: 12px; color: #fff;
  display: flex; align-items: center; gap: 4px;
}
.modal-label-remove { cursor: pointer; font-weight: bold; }
.modal-relations { display: flex; flex-direction: column; gap: 4px; }
.modal-relation {
  font-size: 13px; color: #4A90D9; cursor: pointer; text-decoration: underline;
}
.modal-relation.broken { color: #e74c3c; text-decoration: line-through; }
.modal-attachments { display: flex; flex-direction: column; gap: 4px; }
.modal-attachment { display: flex; align-items: center; gap: 8px; }
.modal-attachment a { font-size: 13px; color: #4A90D9; }
.modal-comment {
  padding: 10px; background: #fafafa; border-radius: 6px; margin-bottom: 8px;
}
.modal-comment-header { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
.modal-comment-body { font-size: 14px; line-height: 1.5; }
.modal-comment-form { display: flex; gap: 8px; margin-top: 8px; }
.modal-comment-input {
  flex: 1; padding: 8px; border: 1px solid #ddd; border-radius: 4px;
  font-size: 13px; resize: none;
}
.modal-comment-submit {
  padding: 8px 16px; background: #4A90D9; color: #fff; border: none;
  border-radius: 4px; font-size: 13px; font-weight: 600;
}
.modal-comment-submit:hover { background: #357ABD; }

/* --- Label Colors --- */
.label-red { background: #e74c3c; }
.label-orange { background: #e67e22; }
.label-yellow { background: #f1c40f; }
.label-green { background: #27ae60; }
.label-blue { background: #2980b9; }
.label-purple { background: #8e44ad; }
.label-pink { background: #e84393; }
.label-default { background: #95a5a6; }
</style>
</head>
<body>

<!-- Top Bar -->
<div class="topbar">
  <span class="topbar-title">Kanban</span>
  <select class="topbar-board-select" id="boardSelect"></select>
  <div class="topbar-views">
    <button data-view="board" class="active" title="[1]">Board</button>
    <button data-view="dashboard" title="[2]">Dashboard</button>
    <button data-view="calendar" title="[3]">Calendar</button>
    <button data-view="table" title="[4]">Table</button>
  </div>
  <input type="text" class="topbar-search" id="searchInput" placeholder="Search... [/]">
  <button class="topbar-new-btn" id="newCardBtn" title="[N]">+ New Card</button>
</div>

<!-- Search Dropdown -->
<div class="search-dropdown" id="searchDropdown"></div>

<!-- Filter Bar -->
<div class="filter-bar" id="filterBar">
  <label style="font-size:13px;font-weight:600;color:#888;">Filters:</label>
  <select id="filterAssignee"><option value="">Assignee</option></select>
  <select id="filterLabel"><option value="">Label</option></select>
  <select id="filterDue">
    <option value="">Due Date</option>
    <option value="overdue">Overdue</option>
    <option value="today">Today</option>
    <option value="week">This Week</option>
    <option value="none">No Date</option>
  </select>
  <div class="filter-chips" id="filterChips"></div>
</div>

<!-- Board View -->
<div class="view active" id="viewBoard">
  <div class="board-columns" id="boardColumns"></div>
</div>

<!-- Dashboard View -->
<div class="view" id="viewDashboard">
  <div class="dashboard-section" id="dashOverdue">
    <div class="dashboard-section-title" style="color:#e74c3c;">Overdue</div>
    <div class="dashboard-cards" id="dashOverdueCards"></div>
  </div>
  <div class="dashboard-section" id="dashToday">
    <div class="dashboard-section-title">Today</div>
    <div class="dashboard-cards" id="dashTodayCards"></div>
  </div>
  <div class="dashboard-section" id="dashWeek">
    <div class="dashboard-section-title">This Week</div>
    <div class="dashboard-cards" id="dashWeekCards"></div>
  </div>
</div>

<!-- Calendar View -->
<div class="view" id="viewCalendar">
  <div class="calendar-header">
    <button id="calPrev">&larr; Prev</button>
    <span class="calendar-month-title" id="calTitle"></span>
    <button id="calNext">Next &rarr;</button>
  </div>
  <div class="calendar-grid" id="calGrid"></div>
</div>

<!-- Table View -->
<div class="view" id="viewTable">
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th data-sort="title">Title</th>
          <th data-sort="board">Board</th>
          <th data-sort="list">List</th>
          <th data-sort="assignee">Assignee</th>
          <th data-sort="due">Due Date</th>
          <th data-sort="labels">Labels</th>
        </tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>
</div>

<!-- Card Detail Modal -->
<div class="modal-overlay" id="modalOverlay">
  <div class="modal" id="cardModal">
    <button class="modal-close" id="modalClose">&times;</button>
    <input class="modal-title" id="modalTitle" type="text" placeholder="Card title">

    <div class="modal-section">
      <div class="modal-section-label">Description</div>
      <div class="modal-description" id="modalDescView"></div>
      <textarea class="modal-description-edit" id="modalDescEdit" style="display:none;"></textarea>
      <button class="modal-add-btn" id="modalDescToggle">Edit</button>
    </div>

    <div class="modal-section">
      <div class="modal-section-label">Details</div>
      <div class="modal-meta-row">
        <span class="modal-meta-label">Assignee</span>
        <span class="modal-meta-value"><input type="text" id="modalAssignee"></span>
      </div>
      <div class="modal-meta-row">
        <span class="modal-meta-label">Due Date</span>
        <span class="modal-meta-value"><input type="date" id="modalDue"></span>
      </div>
      <div class="modal-meta-row">
        <span class="modal-meta-label">Labels</span>
        <span class="modal-meta-value">
          <div class="modal-labels" id="modalLabels"></div>
          <input type="text" id="modalLabelInput" placeholder="Add label..." style="margin-top:4px;padding:4px 8px;border:1px solid #ddd;border-radius:4px;font-size:12px;">
        </span>
      </div>
    </div>

    <div class="modal-section">
      <div class="modal-section-label">Checklist</div>
      <div id="modalChecklist"></div>
      <button class="modal-add-btn" id="modalAddCheckItem">+ Add item</button>
    </div>

    <div class="modal-section">
      <div class="modal-section-label">Custom Fields</div>
      <div id="modalCustomFields"></div>
      <button class="modal-add-btn" id="modalAddCustomField">+ Add field</button>
    </div>

    <div class="modal-section">
      <div class="modal-section-label">Attachments</div>
      <div class="modal-attachments" id="modalAttachments"></div>
      <button class="modal-add-btn" id="modalAddAttachment">+ Add link</button>
    </div>

    <div class="modal-section">
      <div class="modal-section-label">Relations</div>
      <div class="modal-relations" id="modalRelations"></div>
      <button class="modal-add-btn" id="modalAddRelation">+ Add relation</button>
    </div>

    <div class="modal-section">
      <div class="modal-section-label">Comments</div>
      <div id="modalComments"></div>
      <div class="modal-comment-form">
        <textarea class="modal-comment-input" id="modalCommentInput" rows="2" placeholder="Add a comment..."></textarea>
        <button class="modal-comment-submit" id="modalCommentSubmit">Post</button>
      </div>
    </div>
  </div>
</div>

<script>
// JS will go here in subsequent tasks
</script>
</body>
</html>
```

- [ ] **Step 2: Verify the skeleton renders**

Run: `python server.py`
Open `http://localhost:8080` in a browser. You should see the top bar, empty board columns, and a working (but non-functional) layout. Click the modal close button — nothing will work yet, but the CSS should display correctly.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add frontend HTML shell and CSS styling"
```

---

### Task 7: Frontend — Core State & API Client

**Files:**
- Modify: `index.html` (the `<script>` section)

Adds the JS state management, API client functions, and view switching logic.

- [ ] **Step 1: Add core JS to `index.html`**

Replace the `<script>` section content with:

```javascript
// --- State ---
const state = {
  boards: [],
  currentBoard: null,
  currentView: 'board',
  boardData: null,
  filters: { assignee: '', label: '', due: '' },
  calendarYear: new Date().getFullYear(),
  calendarMonth: new Date().getMonth() + 1,
  modalCard: null,
  sortColumn: 'title',
  sortAsc: true,
};

// --- API Client ---
const api = {
  async get(path) {
    const r = await fetch(`/api${path}`);
    if (!r.ok) throw new Error(`GET ${path}: ${r.status}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(`/api${path}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`POST ${path}: ${r.status}`);
    return r.json();
  },
  async put(path, body) {
    const r = await fetch(`/api${path}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`PUT ${path}: ${r.status}`);
    return r.json();
  },
  async del(path) {
    const r = await fetch(`/api${path}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`DELETE ${path}: ${r.status}`);
    return r.json();
  },
};

// --- Label Colors ---
const LABEL_COLORS = {
  bug: 'label-red', urgent: 'label-red',
  frontend: 'label-blue', backend: 'label-purple',
  design: 'label-pink', feature: 'label-green',
  docs: 'label-orange', testing: 'label-yellow',
};
function labelClass(name) {
  return LABEL_COLORS[name.toLowerCase()] || 'label-default';
}

// --- View Switching ---
function switchView(view) {
  state.currentView = view;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view' + view.charAt(0).toUpperCase() + view.slice(1)).classList.add('active');
  document.querySelectorAll('.topbar-views button').forEach(b => {
    b.classList.toggle('active', b.dataset.view === view);
  });
  const filterBar = document.getElementById('filterBar');
  filterBar.classList.toggle('visible', view === 'board' || view === 'table');
  refreshCurrentView();
}

async function refreshCurrentView() {
  switch (state.currentView) {
    case 'board': await renderBoard(); break;
    case 'dashboard': await renderDashboard(); break;
    case 'calendar': await renderCalendar(); break;
    case 'table': await renderTable(); break;
  }
}

// --- Board Select ---
async function loadBoards() {
  state.boards = await api.get('/boards');
  const sel = document.getElementById('boardSelect');
  sel.innerHTML = state.boards.map(b =>
    `<option value="${b.slug}">${b.name}</option>`
  ).join('');
  if (state.boards.length > 0 && !state.currentBoard) {
    state.currentBoard = state.boards[0].slug;
  }
  if (state.currentBoard) {
    sel.value = state.currentBoard;
  }
}

document.getElementById('boardSelect').addEventListener('change', async (e) => {
  state.currentBoard = e.target.value;
  await refreshCurrentView();
});

document.querySelectorAll('.topbar-views button').forEach(btn => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

// --- Keyboard Shortcuts ---
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  if (e.key === '1') switchView('board');
  else if (e.key === '2') switchView('dashboard');
  else if (e.key === '3') switchView('calendar');
  else if (e.key === '4') switchView('table');
  else if (e.key === '/' ) { e.preventDefault(); document.getElementById('searchInput').focus(); }
  else if (e.key === 'n' || e.key === 'N') showNewCardDialog();
  else if (e.key === 'Escape') closeModal();
});

// --- Placeholder render functions (implemented in subsequent tasks) ---
async function renderBoard() {}
async function renderDashboard() {}
async function renderCalendar() {}
async function renderTable() {}
function showNewCardDialog() {}
function closeModal() {}

// --- Init ---
(async () => {
  await loadBoards();
  await refreshCurrentView();
})();
```

- [ ] **Step 2: Verify view switching works**

Run the server, open the browser. Click Board/Dashboard/Calendar/Table buttons — the active view should switch. Press 1/2/3/4 keys — same behavior. The board select dropdown should be populated (empty if no boards exist yet).

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add core JS state, API client, view switching, keyboard shortcuts"
```

---

### Task 8: Frontend — Board View with Drag-and-Drop

**Files:**
- Modify: `index.html` (the `<script>` section)

Implements the kanban board rendering with card previews and native HTML5 drag-and-drop.

- [ ] **Step 1: Replace the placeholder `renderBoard` function**

```javascript
async function renderBoard() {
  if (!state.currentBoard) {
    document.getElementById('boardColumns').innerHTML = '<p style="padding:20px;color:#888;">No boards yet. Create one first.</p>';
    return;
  }
  state.boardData = await api.get(`/boards/${state.currentBoard}`);
  const container = document.getElementById('boardColumns');
  container.innerHTML = '';

  for (const list of state.boardData.lists) {
    const col = document.createElement('div');
    col.className = 'board-column';
    col.dataset.list = list.name;

    const filteredCards = applyFilters(list.cards);

    col.innerHTML = `
      <div class="board-column-header">
        <span>${formatListName(list.name)}</span>
        <span class="board-column-count">${filteredCards.length}</span>
      </div>
      <div class="board-cards" data-list="${list.name}"></div>
    `;

    const cardsContainer = col.querySelector('.board-cards');
    for (const card of filteredCards) {
      cardsContainer.appendChild(createBoardCard(card));
    }

    setupDropZone(cardsContainer);
    container.appendChild(col);
  }
}

function formatListName(slug) {
  return slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function createBoardCard(card) {
  const el = document.createElement('div');
  el.className = 'board-card';
  el.draggable = true;
  el.dataset.slug = card.slug;
  el.dataset.board = card.board;
  el.dataset.list = card.list;

  const labels = (card.labels || []).map(l =>
    `<div class="board-card-label ${labelClass(l)}" title="${l}"></div>`
  ).join('');

  const isOverdue = card.due && new Date(card.due) < new Date(new Date().toDateString());
  const dueClass = isOverdue ? 'overdue' : '';
  const dueStr = card.due ? formatDate(card.due) : '';

  const checklist = parseChecklistFromBody(card);
  let checklistHtml = '';
  if (checklist.total > 0) {
    const pct = Math.round((checklist.done / checklist.total) * 100);
    checklistHtml = `
      <div class="board-card-checklist">
        <div class="board-card-checklist-bar"><div class="board-card-checklist-fill" style="width:${pct}%"></div></div>
        <span>${checklist.done}/${checklist.total}</span>
      </div>`;
  }

  el.innerHTML = `
    ${labels ? `<div class="board-card-labels">${labels}</div>` : ''}
    <div class="board-card-title">${escHtml(card.title)}</div>
    <div class="board-card-meta">
      ${card.assignee ? `<span>${escHtml(card.assignee)}</span>` : ''}
      ${dueStr ? `<span class="${dueClass}">${dueStr}</span>` : ''}
      ${checklistHtml}
    </div>
  `;

  el.addEventListener('click', () => openCardModal(card.board, card.list, card.slug));
  el.addEventListener('dragstart', (e) => {
    el.classList.add('dragging');
    e.dataTransfer.setData('text/plain', JSON.stringify({
      slug: card.slug, board: card.board, list: card.list,
    }));
    e.dataTransfer.effectAllowed = 'move';
  });
  el.addEventListener('dragend', () => el.classList.remove('dragging'));

  return el;
}

function setupDropZone(container) {
  container.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const afterEl = getDragAfterElement(container, e.clientY);
    const dragging = document.querySelector('.dragging');
    if (!dragging) return;
    if (afterEl) {
      container.insertBefore(dragging, afterEl);
    } else {
      container.appendChild(dragging);
    }
  });

  container.addEventListener('drop', async (e) => {
    e.preventDefault();
    const data = JSON.parse(e.dataTransfer.getData('text/plain'));
    const targetList = container.dataset.list;
    const cards = [...container.querySelectorAll('.board-card')];
    const position = cards.findIndex(c => c.dataset.slug === data.slug);

    if (data.list !== targetList || position !== -1) {
      await api.put(`/cards/${data.board}/${data.list}/${data.slug}/move`, {
        target_list: targetList,
        position: position >= 0 ? position : cards.length,
      });
      await renderBoard();
    }
  });
}

function getDragAfterElement(container, y) {
  const els = [...container.querySelectorAll('.board-card:not(.dragging)')];
  return els.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) {
      return { offset, element: child };
    }
    return closest;
  }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function parseChecklistFromBody(card) {
  const body = card.body || '';
  const checked = (body.match(/- \[x\]/gi) || []).length;
  const unchecked = (body.match(/- \[ \]/g) || []).length;
  return { done: checked, total: checked + unchecked };
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function escHtml(str) {
  const d = document.createElement('div');
  d.textContent = str || '';
  return d.innerHTML;
}

function applyFilters(cards) {
  return cards.filter(card => {
    if (state.filters.assignee && card.assignee !== state.filters.assignee) return false;
    if (state.filters.label && !(card.labels || []).includes(state.filters.label)) return false;
    if (state.filters.due) {
      const today = new Date(new Date().toDateString());
      const weekEnd = new Date(today); weekEnd.setDate(weekEnd.getDate() + 7);
      const due = card.due ? new Date(card.due + 'T00:00:00') : null;
      if (state.filters.due === 'overdue' && (!due || due >= today)) return false;
      if (state.filters.due === 'today' && (!due || due.toDateString() !== today.toDateString())) return false;
      if (state.filters.due === 'week' && (!due || due < today || due > weekEnd)) return false;
      if (state.filters.due === 'none' && due) return false;
    }
    return true;
  });
}
```

- [ ] **Step 2: Verify board rendering and drag-and-drop**

Create a board and some cards via the API (using curl or the test suite), then reload the page. Cards should appear in columns. Drag a card between columns — it should move. Labels, due dates, and checklist progress should render.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add board view rendering with drag-and-drop"
```

---

### Task 9: Frontend — Card Detail Modal

**Files:**
- Modify: `index.html` (the `<script>` section)

Implements the full card detail modal with all fields editable.

- [ ] **Step 1: Implement modal functions**

```javascript
async function openCardModal(board, list, slug) {
  const card = await api.get(`/cards/${board}/${list}/${slug}`);
  state.modalCard = card;
  renderModal(card);
  document.getElementById('modalOverlay').classList.add('visible');
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('visible');
  state.modalCard = null;
}

document.getElementById('modalOverlay').addEventListener('click', (e) => {
  if (e.target === document.getElementById('modalOverlay')) closeModal();
});
document.getElementById('modalClose').addEventListener('click', closeModal);

function renderModal(card) {
  document.getElementById('modalTitle').value = card.title || '';
  renderModalDescription(card);
  renderModalChecklist(card);
  renderModalLabels(card);
  document.getElementById('modalAssignee').value = card.assignee || '';
  document.getElementById('modalDue').value = card.due || '';
  renderModalCustomFields(card);
  renderModalAttachments(card);
  renderModalRelations(card);
  renderModalComments(card);
}

function renderModalDescription(card) {
  const body = card.body || '';
  const descMatch = body.match(/## Description\s*\n\n([\s\S]*?)(?=\n\n## |$)/);
  const desc = descMatch ? descMatch[1].trim() : '';
  document.getElementById('modalDescView').textContent = desc;
  document.getElementById('modalDescEdit').value = desc;
  document.getElementById('modalDescView').style.display = '';
  document.getElementById('modalDescEdit').style.display = 'none';
  document.getElementById('modalDescToggle').textContent = 'Edit';
}

document.getElementById('modalDescToggle').addEventListener('click', () => {
  const view = document.getElementById('modalDescView');
  const edit = document.getElementById('modalDescEdit');
  if (edit.style.display === 'none') {
    view.style.display = 'none';
    edit.style.display = '';
    document.getElementById('modalDescToggle').textContent = 'Save';
    edit.focus();
  } else {
    view.style.display = '';
    edit.style.display = 'none';
    document.getElementById('modalDescToggle').textContent = 'Edit';
    saveCard({ description: edit.value });
  }
});

function renderModalChecklist(card) {
  const container = document.getElementById('modalChecklist');
  const body = card.body || '';
  const checkMatch = body.match(/## Checklist\s*\n\n([\s\S]*?)(?=\n\n## |$)/);
  const checkText = checkMatch ? checkMatch[1].trim() : '';
  const items = checkText.split('\n').filter(l => l.match(/^- \[[ x]\]/i));
  container.innerHTML = '';
  items.forEach((item, i) => {
    const checked = /- \[x\]/i.test(item);
    const text = item.replace(/^- \[[ x]\]\s*/i, '');
    const div = document.createElement('div');
    div.className = 'modal-checklist-item' + (checked ? ' checked' : '');
    div.innerHTML = `
      <input type="checkbox" ${checked ? 'checked' : ''} data-index="${i}">
      <span>${escHtml(text)}</span>
      <button style="margin-left:auto;border:none;background:none;color:#ccc;cursor:pointer;" data-index="${i}">&times;</button>
    `;
    div.querySelector('input').addEventListener('change', (e) => toggleCheckItem(i, e.target.checked));
    div.querySelector('button').addEventListener('click', () => removeCheckItem(i));
    container.appendChild(div);
  });
}

async function toggleCheckItem(index, checked) {
  const card = state.modalCard;
  const body = card.body || '';
  const checkMatch = body.match(/## Checklist\s*\n\n([\s\S]*?)(?=\n\n## |$)/);
  if (!checkMatch) return;
  const lines = checkMatch[1].split('\n');
  let checkIndex = 0;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].match(/^- \[[ x]\]/i)) {
      if (checkIndex === index) {
        lines[i] = checked
          ? lines[i].replace('- [ ]', '- [x]')
          : lines[i].replace(/- \[x\]/i, '- [ ]');
        break;
      }
      checkIndex++;
    }
  }
  const newChecklist = lines.join('\n');
  await saveCard({ checklist: newChecklist });
}

async function removeCheckItem(index) {
  const card = state.modalCard;
  const body = card.body || '';
  const checkMatch = body.match(/## Checklist\s*\n\n([\s\S]*?)(?=\n\n## |$)/);
  if (!checkMatch) return;
  const lines = checkMatch[1].split('\n');
  let checkIndex = 0;
  const newLines = [];
  for (const line of lines) {
    if (line.match(/^- \[[ x]\]/i)) {
      if (checkIndex !== index) newLines.push(line);
      checkIndex++;
    } else {
      newLines.push(line);
    }
  }
  await saveCard({ checklist: newLines.join('\n') });
}

document.getElementById('modalAddCheckItem').addEventListener('click', async () => {
  const text = prompt('Checklist item:');
  if (!text) return;
  const card = state.modalCard;
  const body = card.body || '';
  const checkMatch = body.match(/## Checklist\s*\n\n([\s\S]*?)(?=\n\n## |$)/);
  const existing = checkMatch ? checkMatch[1].trimEnd() : '';
  const newChecklist = existing + (existing ? '\n' : '') + `- [ ] ${text}`;
  await saveCard({ checklist: newChecklist });
});

function renderModalLabels(card) {
  const container = document.getElementById('modalLabels');
  container.innerHTML = (card.labels || []).map(l =>
    `<span class="modal-label-tag ${labelClass(l)}">${escHtml(l)} <span class="modal-label-remove" data-label="${escHtml(l)}">&times;</span></span>`
  ).join('');
  container.querySelectorAll('.modal-label-remove').forEach(btn => {
    btn.addEventListener('click', async () => {
      const newLabels = (state.modalCard.labels || []).filter(l => l !== btn.dataset.label);
      await saveCard({ labels: newLabels });
    });
  });
}

document.getElementById('modalLabelInput').addEventListener('keydown', async (e) => {
  if (e.key === 'Enter') {
    const val = e.target.value.trim();
    if (!val) return;
    const newLabels = [...(state.modalCard.labels || []), val];
    e.target.value = '';
    await saveCard({ labels: newLabels });
  }
});

function renderModalCustomFields(card) {
  const container = document.getElementById('modalCustomFields');
  const fields = card.custom_fields || {};
  container.innerHTML = Object.entries(fields).map(([k, v]) =>
    `<div class="modal-meta-row">
      <span class="modal-meta-label">${escHtml(k)}</span>
      <span class="modal-meta-value">${escHtml(String(v))}</span>
      <button style="border:none;background:none;color:#ccc;cursor:pointer;" data-field="${escHtml(k)}">&times;</button>
    </div>`
  ).join('');
  container.querySelectorAll('button[data-field]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const newFields = { ...(state.modalCard.custom_fields || {}) };
      delete newFields[btn.dataset.field];
      await saveCard({ custom_fields: newFields });
    });
  });
}

document.getElementById('modalAddCustomField').addEventListener('click', async () => {
  const key = prompt('Field name:');
  if (!key) return;
  const value = prompt('Field value:');
  if (value === null) return;
  const newFields = { ...(state.modalCard.custom_fields || {}), [key]: value };
  await saveCard({ custom_fields: newFields });
});

function renderModalAttachments(card) {
  const container = document.getElementById('modalAttachments');
  const attachments = card.attachments || [];
  container.innerHTML = attachments.map((a, i) =>
    `<div class="modal-attachment">
      <a href="${escHtml(typeof a === 'string' ? a : a.url)}" target="_blank">${escHtml(typeof a === 'string' ? a : (a.name || a.url))}</a>
      <button style="border:none;background:none;color:#ccc;cursor:pointer;" data-index="${i}">&times;</button>
    </div>`
  ).join('');
  container.querySelectorAll('button[data-index]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const newAttach = [...(state.modalCard.attachments || [])];
      newAttach.splice(parseInt(btn.dataset.index), 1);
      await saveCard({ attachments: newAttach });
    });
  });
}

document.getElementById('modalAddAttachment').addEventListener('click', async () => {
  const name = prompt('Link name:');
  if (!name) return;
  const url = prompt('URL:');
  if (!url) return;
  const newAttach = [...(state.modalCard.attachments || []), { name, url }];
  await saveCard({ attachments: newAttach });
});

function renderModalRelations(card) {
  const container = document.getElementById('modalRelations');
  const relations = card.relations || [];
  container.innerHTML = relations.map(r =>
    `<span class="modal-relation" data-relation="${escHtml(r)}">${escHtml(r)}</span>`
  ).join('');
}

document.getElementById('modalAddRelation').addEventListener('click', async () => {
  const rel = prompt('Related card path (board/list/card):');
  if (!rel) return;
  const newRels = [...(state.modalCard.relations || []), rel];
  await saveCard({ relations: newRels });
});

function renderModalComments(card) {
  const container = document.getElementById('modalComments');
  const body = card.body || '';
  const commMatch = body.match(/## Comments\s*\n\n([\s\S]*?)$/);
  const commText = commMatch ? commMatch[1].trim() : '';
  if (!commText) {
    container.innerHTML = '<p style="color:#aaa;font-size:13px;">No comments yet.</p>';
    return;
  }
  const comments = commText.split(/\n\n(?=\*\*)/);
  container.innerHTML = comments.map(c => {
    const headerMatch = c.match(/^\*\*(.*?)\*\*/);
    const header = headerMatch ? headerMatch[1] : '';
    const body = c.replace(/^\*\*.*?\*\*\n?/, '').trim();
    return `<div class="modal-comment">
      <div class="modal-comment-header">${escHtml(header)}</div>
      <div class="modal-comment-body">${escHtml(body)}</div>
    </div>`;
  }).join('');
}

document.getElementById('modalCommentSubmit').addEventListener('click', async () => {
  const input = document.getElementById('modalCommentInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  await saveCard({ comment: text });
});

// Save card field changes
async function saveCard(updates) {
  const card = state.modalCard;
  if (!card) return;
  const updated = await api.put(`/cards/${card.board}/${card.list}/${card.slug}`, updates);
  state.modalCard = updated;
  renderModal(updated);
  await refreshCurrentView();
}

// Save title on blur
document.getElementById('modalTitle').addEventListener('blur', async () => {
  const newTitle = document.getElementById('modalTitle').value.trim();
  if (newTitle && state.modalCard && newTitle !== state.modalCard.title) {
    await saveCard({ title: newTitle });
  }
});

// Save assignee on blur
document.getElementById('modalAssignee').addEventListener('blur', async () => {
  const val = document.getElementById('modalAssignee').value.trim();
  if (state.modalCard && val !== state.modalCard.assignee) {
    await saveCard({ assignee: val });
  }
});

// Save due date on change
document.getElementById('modalDue').addEventListener('change', async () => {
  const val = document.getElementById('modalDue').value;
  if (state.modalCard) {
    await saveCard({ due: val });
  }
});
```

- [ ] **Step 2: Verify modal works**

Click a card on the board — the modal should open showing all card fields. Edit the title, assignee, due date. Toggle a checklist item. Add a comment. Add and remove labels. Close with X or Escape.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add card detail modal with full editing"
```

---

### Task 10: Frontend — Dashboard View

**Files:**
- Modify: `index.html` (the `<script>` section)

Implements the dashboard showing overdue, today, and this week cards grouped by board.

- [ ] **Step 1: Replace the placeholder `renderDashboard` function**

```javascript
async function renderDashboard() {
  const data = await api.get('/dashboard');

  renderDashSection('dashOverdueCards', data.overdue || [], true);
  renderDashSection('dashTodayCards', data.today || [], false);
  renderDashSection('dashWeekCards', data.this_week || [], false);

  document.getElementById('dashOverdue').style.display = (data.overdue || []).length ? '' : 'none';
}

function renderDashSection(containerId, cards, isOverdue) {
  const container = document.getElementById(containerId);
  if (cards.length === 0) {
    container.innerHTML = '<p style="color:#aaa;font-size:13px;padding:4px;">Nothing here.</p>';
    return;
  }
  const grouped = {};
  for (const card of cards) {
    const boardName = card.board;
    if (!grouped[boardName]) grouped[boardName] = [];
    grouped[boardName].push(card);
  }
  container.innerHTML = '';
  for (const [boardName, boardCards] of Object.entries(grouped)) {
    const group = document.createElement('div');
    group.className = 'dashboard-group';
    group.innerHTML = `<div class="dashboard-group-title">${escHtml(boardName)}</div>`;
    const cardsDiv = document.createElement('div');
    cardsDiv.className = 'dashboard-cards';
    for (const card of boardCards) {
      const el = document.createElement('div');
      el.className = 'dashboard-card' + (isOverdue ? ' overdue' : '');
      el.innerHTML = `
        <span class="dashboard-card-title">${escHtml(card.title)}</span>
        <span class="dashboard-card-meta">
          ${card.assignee ? escHtml(card.assignee) + ' · ' : ''}
          ${card.due ? formatDate(card.due) : ''}
          · ${formatListName(card.list)}
        </span>
      `;
      el.addEventListener('click', () => openCardModal(card.board, card.list, card.slug));
      cardsDiv.appendChild(el);
    }
    group.appendChild(cardsDiv);
    container.appendChild(group);
  }
}
```

- [ ] **Step 2: Verify dashboard renders**

Switch to Dashboard view. Cards due today, this week, and overdue should appear grouped by board. Click a card — the modal should open.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add dashboard view with overdue/today/week sections"
```

---

### Task 11: Frontend — Calendar View

**Files:**
- Modify: `index.html` (the `<script>` section)

Implements the monthly calendar grid with cards on their due dates.

- [ ] **Step 1: Replace the placeholder `renderCalendar` function**

```javascript
async function renderCalendar() {
  const { calendarYear: year, calendarMonth: month } = state;
  const data = await api.get(`/calendar/${year}/${month}`);

  const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('calTitle').textContent = `${monthNames[month - 1]} ${year}`;

  const firstDay = new Date(year, month - 1, 1);
  const lastDay = new Date(year, month, 0);
  const startDow = firstDay.getDay();
  const daysInMonth = lastDay.getDate();

  const todayStr = new Date().toISOString().split('T')[0];
  const cardsByDay = {};
  for (const card of (data.cards || [])) {
    if (!card.due) continue;
    const day = parseInt(card.due.split('-')[2], 10);
    if (!cardsByDay[day]) cardsByDay[day] = [];
    cardsByDay[day].push(card);
  }

  const grid = document.getElementById('calGrid');
  grid.innerHTML = '';

  const dayHeaders = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  for (const dh of dayHeaders) {
    const hdr = document.createElement('div');
    hdr.className = 'calendar-day-header';
    hdr.textContent = dh;
    grid.appendChild(hdr);
  }

  // Previous month padding
  const prevMonth = new Date(year, month - 1, 0);
  for (let i = startDow - 1; i >= 0; i--) {
    const cell = document.createElement('div');
    cell.className = 'calendar-day other-month';
    cell.innerHTML = `<div class="calendar-day-num">${prevMonth.getDate() - i}</div>`;
    grid.appendChild(cell);
  }

  // Current month days
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const cell = document.createElement('div');
    cell.className = 'calendar-day' + (dateStr === todayStr ? ' today' : '');
    cell.innerHTML = `<div class="calendar-day-num">${d}</div>`;
    const dayCards = cardsByDay[d] || [];
    for (const card of dayCards) {
      const isOverdue = new Date(card.due + 'T00:00:00') < new Date(new Date().toDateString());
      const cardEl = document.createElement('div');
      cardEl.className = 'calendar-day-card' + (isOverdue ? ' overdue' : '');
      cardEl.textContent = card.title;
      cardEl.addEventListener('click', (e) => {
        e.stopPropagation();
        openCardModal(card.board, card.list, card.slug);
      });
      cell.appendChild(cardEl);
    }
    grid.appendChild(cell);
  }

  // Next month padding
  const totalCells = startDow + daysInMonth;
  const remaining = (7 - (totalCells % 7)) % 7;
  for (let i = 1; i <= remaining; i++) {
    const cell = document.createElement('div');
    cell.className = 'calendar-day other-month';
    cell.innerHTML = `<div class="calendar-day-num">${i}</div>`;
    grid.appendChild(cell);
  }
}

document.getElementById('calPrev').addEventListener('click', () => {
  state.calendarMonth--;
  if (state.calendarMonth < 1) { state.calendarMonth = 12; state.calendarYear--; }
  renderCalendar();
});

document.getElementById('calNext').addEventListener('click', () => {
  state.calendarMonth++;
  if (state.calendarMonth > 12) { state.calendarMonth = 1; state.calendarYear++; }
  renderCalendar();
});
```

- [ ] **Step 2: Verify calendar renders**

Switch to Calendar view. Navigate between months. Cards with due dates should appear on the correct day cells. Click a card — modal opens.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add calendar view with monthly grid"
```

---

### Task 12: Frontend — Table View

**Files:**
- Modify: `index.html` (the `<script>` section)

Implements the sortable, filterable table view.

- [ ] **Step 1: Replace the placeholder `renderTable` function**

```javascript
async function renderTable() {
  let allCards = [];
  for (const board of state.boards) {
    const boardData = await api.get(`/boards/${board.slug}`);
    for (const list of boardData.lists) {
      for (const card of list.cards) {
        allCards.push(card);
      }
    }
  }

  allCards = applyFilters(allCards);

  // Sort
  allCards.sort((a, b) => {
    let va = a[state.sortColumn] || '';
    let vb = b[state.sortColumn] || '';
    if (Array.isArray(va)) va = va.join(', ');
    if (Array.isArray(vb)) vb = vb.join(', ');
    va = String(va).toLowerCase();
    vb = String(vb).toLowerCase();
    if (va < vb) return state.sortAsc ? -1 : 1;
    if (va > vb) return state.sortAsc ? 1 : -1;
    return 0;
  });

  const tbody = document.getElementById('tableBody');
  tbody.innerHTML = allCards.map(card => {
    const labels = (card.labels || []).map(l =>
      `<span class="table-label ${labelClass(l)}">${escHtml(l)}</span>`
    ).join('');
    const isOverdue = card.due && new Date(card.due) < new Date(new Date().toDateString());
    return `<tr data-board="${escHtml(card.board)}" data-list="${escHtml(card.list)}" data-slug="${escHtml(card.slug)}">
      <td>${escHtml(card.title)}</td>
      <td>${escHtml(card.board)}</td>
      <td>${formatListName(card.list)}</td>
      <td>${escHtml(card.assignee || '')}</td>
      <td style="${isOverdue ? 'color:#e74c3c;font-weight:600;' : ''}">${card.due ? formatDate(card.due) : ''}</td>
      <td>${labels}</td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('tr').forEach(row => {
    row.addEventListener('click', () => {
      openCardModal(row.dataset.board, row.dataset.list, row.dataset.slug);
    });
  });
}

// Column sort
document.querySelectorAll('.data-table th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (state.sortColumn === col) {
      state.sortAsc = !state.sortAsc;
    } else {
      state.sortColumn = col;
      state.sortAsc = true;
    }
    renderTable();
  });
});
```

- [ ] **Step 2: Verify table renders**

Switch to Table view. All cards should appear in a sortable table. Click column headers to sort. Click a row — modal opens.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add table view with sorting"
```

---

### Task 13: Frontend — Search & Filters

**Files:**
- Modify: `index.html` (the `<script>` section)

Implements global search with dropdown results and filter bar functionality.

- [ ] **Step 1: Implement search and filter logic**

```javascript
// --- Search ---
let searchTimeout = null;
document.getElementById('searchInput').addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  const q = e.target.value.trim();
  if (!q) {
    document.getElementById('searchDropdown').classList.remove('visible');
    return;
  }
  searchTimeout = setTimeout(async () => {
    const results = await api.get(`/search?q=${encodeURIComponent(q)}`);
    const dropdown = document.getElementById('searchDropdown');
    if (results.length === 0) {
      dropdown.innerHTML = '<div class="search-result"><span class="search-result-title">No results found</span></div>';
    } else {
      dropdown.innerHTML = results.map(card =>
        `<div class="search-result" data-board="${escHtml(card.board)}" data-list="${escHtml(card.list)}" data-slug="${escHtml(card.slug)}">
          <div class="search-result-title">${escHtml(card.title)}</div>
          <div class="search-result-meta">${escHtml(card.board)} · ${formatListName(card.list)}${card.assignee ? ' · ' + escHtml(card.assignee) : ''}</div>
        </div>`
      ).join('');
      dropdown.querySelectorAll('.search-result[data-slug]').forEach(el => {
        el.addEventListener('click', () => {
          openCardModal(el.dataset.board, el.dataset.list, el.dataset.slug);
          dropdown.classList.remove('visible');
          document.getElementById('searchInput').value = '';
        });
      });
    }
    dropdown.classList.add('visible');
  }, 250);
});

document.getElementById('searchInput').addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.getElementById('searchDropdown').classList.remove('visible');
    e.target.blur();
  }
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-dropdown') && !e.target.closest('.topbar-search')) {
    document.getElementById('searchDropdown').classList.remove('visible');
  }
});

// --- Filters ---
async function populateFilterOptions() {
  const assignees = new Set();
  const labels = new Set();
  for (const board of state.boards) {
    const boardData = await api.get(`/boards/${board.slug}`);
    for (const list of boardData.lists) {
      for (const card of list.cards) {
        if (card.assignee) assignees.add(card.assignee);
        for (const l of (card.labels || [])) labels.add(l);
      }
    }
  }
  const assigneeSel = document.getElementById('filterAssignee');
  assigneeSel.innerHTML = '<option value="">All Assignees</option>' +
    [...assignees].sort().map(a => `<option value="${escHtml(a)}">${escHtml(a)}</option>`).join('');

  const labelSel = document.getElementById('filterLabel');
  labelSel.innerHTML = '<option value="">All Labels</option>' +
    [...labels].sort().map(l => `<option value="${escHtml(l)}">${escHtml(l)}</option>`).join('');
}

document.getElementById('filterAssignee').addEventListener('change', (e) => {
  state.filters.assignee = e.target.value;
  updateFilterChips();
  refreshCurrentView();
});

document.getElementById('filterLabel').addEventListener('change', (e) => {
  state.filters.label = e.target.value;
  updateFilterChips();
  refreshCurrentView();
});

document.getElementById('filterDue').addEventListener('change', (e) => {
  state.filters.due = e.target.value;
  updateFilterChips();
  refreshCurrentView();
});

function updateFilterChips() {
  const container = document.getElementById('filterChips');
  container.innerHTML = '';
  if (state.filters.assignee) addChip(container, 'Assignee: ' + state.filters.assignee, () => {
    state.filters.assignee = ''; document.getElementById('filterAssignee').value = '';
    updateFilterChips(); refreshCurrentView();
  });
  if (state.filters.label) addChip(container, 'Label: ' + state.filters.label, () => {
    state.filters.label = ''; document.getElementById('filterLabel').value = '';
    updateFilterChips(); refreshCurrentView();
  });
  if (state.filters.due) addChip(container, 'Due: ' + state.filters.due, () => {
    state.filters.due = ''; document.getElementById('filterDue').value = '';
    updateFilterChips(); refreshCurrentView();
  });
}

function addChip(container, text, onRemove) {
  const chip = document.createElement('span');
  chip.className = 'filter-chip';
  chip.innerHTML = `${escHtml(text)} <span class="filter-chip-remove">&times;</span>`;
  chip.querySelector('.filter-chip-remove').addEventListener('click', onRemove);
  container.appendChild(chip);
}
```

- [ ] **Step 2: Verify search and filters**

Type in the search box — results should appear. Click a result — modal opens. Set filters — board and table views should filter. Filter chips appear and can be removed.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add search dropdown and filter bar with chips"
```

---

### Task 14: Frontend — New Card & New Board Dialogs

**Files:**
- Modify: `index.html` (the `<script>` section)

Implements the new card creation dialog and board creation.

- [ ] **Step 1: Implement dialog functions**

```javascript
function showNewCardDialog() {
  if (!state.currentBoard) {
    showNewBoardDialog();
    return;
  }
  const title = prompt('Card title:');
  if (!title) return;
  const list = prompt('List (ideas, backlog, in-progress, done):', 'ideas');
  if (!list) return;
  createNewCard(title, list);
}

async function createNewCard(title, list) {
  try {
    await api.post(`/boards/${state.currentBoard}/lists/${list}/cards`, { title });
    await refreshCurrentView();
  } catch (e) {
    alert('Failed to create card: ' + e.message);
  }
}

function showNewBoardDialog() {
  const name = prompt('Board name:');
  if (!name) return;
  const color = prompt('Accent color (hex):', '#4A90D9');
  createNewBoard(name, color || '#4A90D9');
}

async function createNewBoard(name, color) {
  try {
    const result = await api.post('/boards', { name, color });
    state.currentBoard = result.slug;
    await loadBoards();
    await refreshCurrentView();
    await populateFilterOptions();
  } catch (e) {
    alert('Failed to create board: ' + e.message);
  }
}

document.getElementById('newCardBtn').addEventListener('click', showNewCardDialog);

// Add board creation option to board select
const boardSelectEl = document.getElementById('boardSelect');
const origLoadBoards = loadBoards;
loadBoards = async function() {
  await origLoadBoards();
  const opt = document.createElement('option');
  opt.value = '__new__';
  opt.textContent = '+ New Board...';
  boardSelectEl.appendChild(opt);
};

boardSelectEl.addEventListener('change', async (e) => {
  if (e.target.value === '__new__') {
    showNewBoardDialog();
    if (state.currentBoard) boardSelectEl.value = state.currentBoard;
    return;
  }
  state.currentBoard = e.target.value;
  await refreshCurrentView();
});
```

- [ ] **Step 2: Verify creation flows**

Press N — new card dialog appears. Select "+ New Board..." from dropdown — board creation dialog appears. Created boards and cards show up immediately.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add new card and new board creation dialogs"
```

---

### Task 15: Frontend — Init & Wiring

**Files:**
- Modify: `index.html` (the `<script>` section)

Updates the init function to wire everything together and handle empty states.

- [ ] **Step 1: Update the init block**

Replace the existing init block with:

```javascript
// --- Init ---
(async () => {
  await loadBoards();
  await populateFilterOptions();
  await refreshCurrentView();
})();
```

- [ ] **Step 2: Full end-to-end test**

Run: `python server.py`
Open: `http://localhost:8080`

Test the following flow:
1. Create a new board via the dropdown
2. Create several cards with N key, assigning different team members and due dates
3. Drag cards between columns
4. Open a card, edit description, add checklist items, toggle them, add labels, add a comment
5. Switch to Dashboard — verify cards appear under Today/This Week/Overdue
6. Switch to Calendar — verify cards appear on correct dates
7. Switch to Table — sort by different columns
8. Use search to find a card
9. Apply filters and verify they work
10. Add a relation between two cards

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: wire up init, complete frontend integration"
```

---

### Task 16: Git Init & Final Commit

**Files:**
- Create: `.gitignore`

Initialize the git repository and make the final commit with all files.

- [ ] **Step 1: Create `.gitignore`**

```
data/
__pycache__/
*.pyc
.superpowers/
```

- [ ] **Step 2: Initialize git and commit**

```bash
git init
git add .gitignore server.py index.html tests/test_server.py docs/
git commit -m "feat: personal kanban board — markdown-backed, single-file frontend, Python server"
```

- [ ] **Step 3: Verify everything works**

Run: `python server.py`
Open: `http://localhost:8080`
Confirm the app is fully functional.
