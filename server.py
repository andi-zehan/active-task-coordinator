#!/usr/bin/env python3
"""Personal Kanban Board — Local Server"""

import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import llm_config
import notes
import janitor

DATA_DIR = Path(__file__).parent / "data"
LISTS = ["ideas", "backlog", "in-progress", "done"]
DATA_REPO_URL = os.environ.get('ATC_DATA_REPO_URL', 'https://github.com/andi-zehan/atc-content.git')


def slugify(title):
    slug = title.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug, flags=re.ASCII)
    slug = re.sub(r'[^\x00-\x7f]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def parse_frontmatter(text):
    match = re.match(r'^---[^\S\n]*\n(.*?)\n---[^\S\n]*\n(.*)', text, re.DOTALL)
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

        # Handle continuation lines inside a dict (nested key-value under a key)
        if indent >= 2 and current_dict_key is not None and not stripped.startswith('- '):
            kv_match = re.match(r'(\w+):\s*(.*)', stripped)
            if kv_match:
                meta[current_dict_key][kv_match.group(1)] = kv_match.group(2).strip('"').strip("'")
                continue

        # Handle continuation key-value lines inside a list item dict (e.g. "    url: ...")
        if indent >= 4 and current_list is not None and current_list and isinstance(current_list[-1], dict):
            kv_match = re.match(r'(\w+):\s*(.*)', stripped)
            if kv_match:
                current_list[-1][kv_match.group(1)] = kv_match.group(2).strip('"').strip("'")
                continue

        # Handle list items under a key (indented "- ...")
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
                STRING_FIELDS = {'title', 'assignee', 'due', 'description', 'color', 'name'}
                if key in STRING_FIELDS:
                    meta[key] = ''
                    current_key = key
                    current_list = None
                else:
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
            if ':' in item_text and not item_text.startswith('http'):
                item = {}
                k, v = item_text.split(':', 1)
                item[k.strip()] = v.strip().strip('"').strip("'")
                current_list.append(item)
            else:
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


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the kanban board API."""

    # ── helpers ──────────────────────────────────────────────────────

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({'error': message}, status)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode('utf-8'))

    def log_message(self, format, *args):
        pass  # suppress logging

    # ── static files ─────────────────────────────────────────────────

    def _guess_mime(self, path):
        ext = Path(path).suffix.lower()
        mime_map = {
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.woff': 'font/woff',
            '.woff2': 'font/woff2',
            '.ttf': 'font/ttf',
        }
        return mime_map.get(ext, 'application/octet-stream')

    def _serve_file(self, filepath, mime=None):
        fp = Path(filepath)
        if not fp.exists():
            self._send_error(404, 'File not found')
            return
        if mime is None:
            mime = self._guess_mime(str(fp))
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── routing ──────────────────────────────────────────────────────

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        query = parse_qs(parsed.query)

        # API routes
        if path == '/api/boards' and method == 'GET':
            return self._handle_list_boards()
        if path == '/api/boards' and method == 'POST':
            return self._handle_create_board()

        # /api/boards-order
        if path == '/api/boards-order' and method == 'PUT':
            return self._handle_update_boards_order()

        # /api/boards/:board
        m = re.match(r'^/api/boards/([^/]+)$', path)
        if m:
            board_slug = m.group(1)
            if method == 'GET':
                return self._handle_get_board(board_slug)
            if method == 'PUT':
                return self._handle_update_board(board_slug)
            if method == 'DELETE':
                return self._handle_delete_board(board_slug)

        # /api/boards/:board/lists/:list/cards
        m = re.match(r'^/api/boards/([^/]+)/lists/([^/]+)/cards$', path)
        if m:
            board_slug, list_slug = m.group(1), m.group(2)
            if method == 'GET':
                return self._handle_list_cards(board_slug, list_slug)
            if method == 'POST':
                return self._handle_create_card(board_slug, list_slug)

        # /api/cards/:board/:list/:card/move
        m = re.match(r'^/api/cards/([^/]+)/([^/]+)/([^/]+)/move$', path)
        if m:
            board_slug, list_slug, card_slug = m.group(1), m.group(2), m.group(3)
            if method == 'PUT':
                return self._handle_move_card(board_slug, list_slug, card_slug)

        # /api/cards/:board/:list/:card
        m = re.match(r'^/api/cards/([^/]+)/([^/]+)/([^/]+)$', path)
        if m:
            board_slug, list_slug, card_slug = m.group(1), m.group(2), m.group(3)
            if method == 'GET':
                return self._handle_get_card(board_slug, list_slug, card_slug)
            if method == 'PUT':
                return self._handle_update_card(board_slug, list_slug, card_slug)
            if method == 'DELETE':
                return self._handle_delete_card(board_slug, list_slug, card_slug)

        # /api/dashboard
        if path == '/api/dashboard' and method == 'GET':
            return self._handle_dashboard()

        # /api/calendar/:year/:month
        m = re.match(r'^/api/calendar/(\d{4})/(\d{1,2})$', path)
        if m and method == 'GET':
            return self._handle_calendar(m.group(1), m.group(2))

        # /api/search
        if path == '/api/search' and method == 'GET':
            return self._handle_search(query)

        # /api/sync/push
        if path == '/api/sync/push' and method == 'POST':
            return self._handle_sync_push()

        # /api/sync/pull
        if path == '/api/sync/pull' and method == 'POST':
            return self._handle_sync_pull()

        # /api/sync/status
        if path == '/api/sync/status' and method == 'GET':
            return self._handle_sync_status()

        # /api/llm-config
        if path == '/api/llm-config' and method == 'GET':
            return self._handle_get_llm_config()
        if path == '/api/llm-config' and method == 'PUT':
            return self._handle_put_llm_config()
        if path == '/api/llm-config/test' and method == 'POST':
            return self._handle_test_llm_config()

        # Static files (GET only)
        if method == 'GET':
            base = Path(__file__).parent
            if path in ('', '/'):
                return self._serve_file(base / 'index.html')
            return self._serve_file(base / path.lstrip('/'))

        self._send_error(404, 'Not found')

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

    # ── board handlers ───────────────────────────────────────────────

    def _handle_list_boards(self):
        ensure_data_dir()
        order = read_json(DATA_DIR / "_boards-order.json")
        boards = []
        for slug in order:
            meta = read_board_meta(slug)
            if meta:
                boards.append(meta)
        self._send_json(boards)

    def _handle_create_board(self):
        data = self._read_body()
        name = data.get('name', '').strip()
        if not name:
            return self._send_error(400, 'Board name is required')
        slug = slugify(name)
        meta = {
            'name': name,
            'description': data.get('description', ''),
            'color': data.get('color', '#4A90D9'),
        }
        write_board_meta(slug, meta)
        order = read_json(DATA_DIR / "_boards-order.json")
        if slug not in order:
            order.append(slug)
        write_json(DATA_DIR / "_boards-order.json", order)
        meta['slug'] = slug
        self._send_json(meta, 201)

    def _handle_get_board(self, board_slug):
        meta = read_board_meta(board_slug)
        if not meta:
            return self._send_error(404, 'Board not found')
        lists = {}
        for list_name in LISTS:
            list_dir = DATA_DIR / "boards" / board_slug / list_name
            order_path = list_dir / "_order.json"
            order = read_json(order_path)
            cards = []
            for card_slug in order:
                card = read_card(board_slug, list_name, card_slug)
                if card:
                    summary = {k: v for k, v in card.items() if k != 'body'}
                    cards.append(summary)
            lists[list_name] = cards
        meta['lists'] = lists
        self._send_json(meta)

    def _handle_update_board(self, board_slug):
        meta = read_board_meta(board_slug)
        if not meta:
            return self._send_error(404, 'Board not found')
        data = self._read_body()
        if 'name' in data:
            meta['name'] = data['name']
        if 'description' in data:
            meta['description'] = data['description']
        if 'color' in data:
            meta['color'] = data['color']
        write_board_meta(board_slug, meta)
        meta['slug'] = board_slug
        self._send_json(meta)

    def _handle_delete_board(self, board_slug):
        board_dir = DATA_DIR / "boards" / board_slug
        if not board_dir.exists():
            return self._send_error(404, 'Board not found')
        shutil.rmtree(board_dir)
        order = read_json(DATA_DIR / "_boards-order.json")
        order = [s for s in order if s != board_slug]
        write_json(DATA_DIR / "_boards-order.json", order)
        self._send_json({'deleted': board_slug})

    def _handle_update_boards_order(self):
        new_order = self._read_body()
        if not isinstance(new_order, list):
            return self._send_error(400, 'Expected a JSON array of board slugs')
        write_json(DATA_DIR / "_boards-order.json", new_order)
        self._send_json({'ok': True})

    # ── card handlers ────────────────────────────────────────────────

    def _handle_list_cards(self, board_slug, list_slug):
        list_dir = DATA_DIR / "boards" / board_slug / list_slug
        order_path = list_dir / "_order.json"
        order = read_json(order_path)
        cards = []
        for card_slug in order:
            card = read_card(board_slug, list_slug, card_slug)
            if card:
                summary = {k: v for k, v in card.items() if k != 'body'}
                cards.append(summary)
        self._send_json(cards)

    def _handle_create_card(self, board_slug, list_slug):
        data = self._read_body()
        title = data.get('title', '').strip()
        if not title:
            return self._send_error(400, 'Card title is required')
        slug = slugify(title)
        today_str = str(date.today())
        meta = {
            'title': title,
            'assignee': data.get('assignee', ''),
            'labels': data.get('labels', []),
            'due': data.get('due', ''),
            'created': today_str,
            'updated': today_str,
            'relations': data.get('relations', []),
            'custom_fields': data.get('custom_fields', {}),
            'attachments': data.get('attachments', []),
        }
        description = data.get('description', '')
        body = f"\n## Description\n\n{description}\n\n## Checklist\n\n\n\n## Comments\n\n"
        write_card(board_slug, list_slug, slug, meta, body)
        order_path = DATA_DIR / "boards" / board_slug / list_slug / "_order.json"
        order = read_json(order_path)
        if slug not in order:
            order.append(slug)
        write_json(order_path, order)
        meta['slug'] = slug
        meta['board'] = board_slug
        meta['list'] = list_slug
        self._send_json(meta, 201)

    def _handle_get_card(self, board_slug, list_slug, card_slug):
        card = read_card(board_slug, list_slug, card_slug)
        if not card:
            return self._send_error(404, 'Card not found')
        self._send_json(card)

    def _handle_update_card(self, board_slug, list_slug, card_slug):
        card = read_card(board_slug, list_slug, card_slug)
        if not card:
            return self._send_error(404, 'Card not found')
        data = self._read_body()
        body = card.get('body', '')

        # Update frontmatter fields
        for field in ('title', 'assignee', 'labels', 'due', 'relations',
                       'custom_fields', 'attachments'):
            if field in data:
                card[field] = data[field]

        # Update description section
        if 'description' in data:
            body = re.sub(
                r'(## Description\n\n).*?(\n+## )',
                r'\g<1>' + data['description'] + '\n\n## ',
                body, count=1, flags=re.DOTALL
            )

        # Update checklist section
        if 'checklist' in data:
            body = re.sub(
                r'(## Checklist\n\n).*?(\n+## )',
                r'\g<1>' + data['checklist'] + '\n\n## ',
                body, count=1, flags=re.DOTALL
            )

        # Add comment
        if 'comment' in data:
            today_str = str(date.today())
            comment_text = data['comment']
            body = body.rstrip('\n') + f"\n\n**{today_str} - Me:**\n{comment_text}\n"

        card['updated'] = str(date.today())
        meta = {k: v for k, v in card.items() if k not in ('slug', 'board', 'list', 'body')}
        write_card(board_slug, list_slug, card_slug, meta, body)
        card['body'] = body
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
        self._send_json({'deleted': card_slug})

    def _handle_move_card(self, board_slug, list_slug, card_slug):
        data = self._read_body()
        target_list = data.get('target_list', '').strip()
        if not target_list:
            return self._send_error(400, 'target_list is required')
        target_board = data.get('target_board', board_slug).strip()
        position = data.get('position', -1)

        # Remove from source order
        src_order_path = DATA_DIR / "boards" / board_slug / list_slug / "_order.json"
        src_order = read_json(src_order_path)
        src_order = [s for s in src_order if s != card_slug]
        write_json(src_order_path, src_order)

        # Move the file
        src_path = DATA_DIR / "boards" / board_slug / list_slug / f"{card_slug}.md"
        dst_dir = DATA_DIR / "boards" / target_board / target_list
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_path = dst_dir / f"{card_slug}.md"
        shutil.move(str(src_path), str(dst_path))

        # Insert into target order
        dst_order_path = dst_dir / "_order.json"
        dst_order = read_json(dst_order_path)
        if position < 0 or position >= len(dst_order):
            dst_order.append(card_slug)
        else:
            dst_order.insert(position, card_slug)
        write_json(dst_order_path, dst_order)

        self._send_json({'moved': card_slug, 'from': list_slug, 'to': target_list, 'board': target_board})

    # ── aggregation handlers ─────────────────────────────────────────

    def _get_all_cards(self):
        """Iterate all boards/lists/cards and return a list of card dicts (no body)."""
        cards = []
        boards_dir = DATA_DIR / "boards"
        if not boards_dir.exists():
            return cards
        for board_dir in boards_dir.iterdir():
            if not board_dir.is_dir():
                continue
            board_slug = board_dir.name
            for list_name in LISTS:
                list_dir = board_dir / list_name
                if not list_dir.exists():
                    continue
                order_path = list_dir / "_order.json"
                order = read_json(order_path)
                for card_slug in order:
                    card = read_card(board_slug, list_name, card_slug)
                    if card:
                        summary = {k: v for k, v in card.items() if k != 'body'}
                        cards.append(summary)
        return cards

    def _handle_dashboard(self):
        today_date = date.today()
        week_end = today_date + timedelta(days=6 - today_date.weekday())
        next_week_end = week_end + timedelta(days=7)
        all_cards = self._get_all_cards()
        result = {
            'today': [],
            'this_week': [],
            'next_week': [],
            'later': [],
            'someday': [],
            'overdue': [],
        }
        for card in all_cards:
            due = card.get('due', '')
            if not due:
                result['someday'].append(card)
                continue
            try:
                due_date = date.fromisoformat(due)
            except (ValueError, TypeError):
                result['someday'].append(card)
                continue
            if due_date == today_date:
                result['today'].append(card)
            elif today_date < due_date <= week_end:
                result['this_week'].append(card)
            elif week_end < due_date <= next_week_end:
                result['next_week'].append(card)
            elif due_date > next_week_end:
                result['later'].append(card)
            elif due_date < today_date:
                result['overdue'].append(card)
        self._send_json(result)

    def _handle_calendar(self, year_str, month_str):
        year = int(year_str)
        month = int(month_str)
        all_cards = self._get_all_cards()
        result = []
        for card in all_cards:
            due = card.get('due', '')
            if not due:
                continue
            try:
                due_date = date.fromisoformat(due)
            except (ValueError, TypeError):
                continue
            if due_date.year == year and due_date.month == month:
                result.append(card)
        self._send_json(result)

    def _handle_search(self, query_params):
        q = query_params.get('q', [''])[0].lower().strip()
        if not q:
            return self._send_json([])
        all_cards = self._get_all_cards()
        results = []
        for card in all_cards:
            title = str(card.get('title', '')).lower()
            description = str(card.get('description', '')).lower()
            assignee = str(card.get('assignee', '')).lower()
            labels_raw = card.get('labels', [])
            if not isinstance(labels_raw, list):
                labels_raw = []
            labels = [str(l).lower() for l in labels_raw]
            if (q in title or q in description or q in assignee
                    or any(q in l for l in labels)):
                results.append(card)
        self._send_json(results)

    def _handle_sync_push(self):
        result = git_sync_push()
        self._send_json(result)

    def _handle_sync_pull(self):
        result = git_sync_pull()
        self._send_json(result)

    def _handle_sync_status(self):
        if not (DATA_DIR / '.git').exists():
            return self._send_json({'dirty': False})
        try:
            r = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, cwd=DATA_DIR)
            self._send_json({'dirty': bool(r.stdout.strip())})
        except Exception:
            self._send_json({'dirty': False})

    def _handle_get_llm_config(self):
        self._send_json(llm_config.public_view())

    def _handle_put_llm_config(self):
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            return self._send_error(400, 'invalid json')
        if not isinstance(body, dict):
            return self._send_error(400, 'expected object')
        # Validate model if present
        if 'model' in body and body['model'] not in llm_config.ALLOWED_MODELS:
            return self._send_error(400, f"model must be one of {llm_config.ALLOWED_MODELS}")
        llm_config.save(body)
        self._send_json({'ok': True})

    def _handle_test_llm_config(self):
        try:
            client = llm_config.get_client()
        except llm_config.NotConfigured:
            return self._send_error(400, 'not configured')
        cfg = llm_config.load()
        try:
            client.messages.create(
                model=cfg['model'],
                max_tokens=1,
                messages=[{'role': 'user', 'content': 'hi'}],
            )
            self._send_json({'ok': True})
        except Exception as e:
            self._send_json({'ok': False, 'error': str(e)})


def _is_empty_placeholder_data_dir():
    if not DATA_DIR.exists():
        return True
    if (DATA_DIR / '.git').exists():
        return False

    files = [p for p in DATA_DIR.rglob('*') if p.is_file() and p.name != '.DS_Store']
    if not files:
        return True
    if len(files) == 1 and files[0].relative_to(DATA_DIR).as_posix() == '_boards-order.json':
        try:
            return read_json(files[0]) == []
        except Exception:
            return False
    return False


def ensure_data_repo():
    if (DATA_DIR / '.git').exists():
        return {'status': 'ok', 'message': 'Data repository exists'}
    if not _is_empty_placeholder_data_dir():
        return {
            'status': 'error',
            'message': 'data/ exists but is not a git repository. Move it aside before syncing.'
        }
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    r = subprocess.run(['git', 'clone', DATA_REPO_URL, str(DATA_DIR)],
                       capture_output=True, text=True, cwd=DATA_DIR.parent)
    if r.returncode != 0:
        return {'status': 'error', 'message': f'Clone failed: {r.stderr.strip()}'}
    return {'status': 'ok', 'message': f'Cloned data repository from {DATA_REPO_URL}'}


def _has_git_remote():
    try:
        r = subprocess.run(['git', 'remote'], capture_output=True, text=True, cwd=DATA_DIR)
        return bool(r.stdout.strip())
    except Exception:
        return False


def git_sync_push():
    if not (DATA_DIR / '.git').exists():
        return {'status': 'error', 'message': 'Data directory is not a git repository'}
    try:
        subprocess.run(['git', 'add', '-A'], cwd=DATA_DIR, capture_output=True, text=True)
        status = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, cwd=DATA_DIR)
        if not status.stdout.strip():
            return {'status': 'no-changes', 'message': 'Nothing to sync'}
        hostname = platform.node()
        ts = datetime.now().strftime('%Y-%m-%d %H:%M')
        msg = f"sync from {hostname} at {ts}"
        subprocess.run(['git', 'commit', '-m', msg], cwd=DATA_DIR, capture_output=True, text=True)
        if _has_git_remote():
            r = subprocess.run(['git', 'push', 'origin', 'main'], cwd=DATA_DIR, capture_output=True, text=True)
            if r.returncode != 0:
                r = subprocess.run(['git', 'push', 'origin', 'master'], cwd=DATA_DIR, capture_output=True, text=True)
                if r.returncode != 0:
                    return {'status': 'error', 'message': f'Push failed: {r.stderr.strip()}'}
        return {'status': 'ok', 'message': msg}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def git_sync_pull():
    repo = ensure_data_repo()
    if repo['status'] != 'ok':
        return repo
    if repo['message'].startswith('Cloned data repository'):
        return repo
    if not _has_git_remote():
        return {'status': 'skipped', 'message': 'No remote configured'}
    try:
        r = subprocess.run(['git', 'pull', 'origin', 'main'], cwd=DATA_DIR, capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(['git', 'pull', 'origin', 'master'], cwd=DATA_DIR, capture_output=True, text=True)
        if r.returncode != 0:
            return {'status': 'error', 'message': f'Pull failed: {r.stderr.strip()}'}
        return {'status': 'ok', 'message': r.stdout.strip()}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


if __name__ == '__main__':
    result = git_sync_pull()
    print(f"Data sync pull: {result['status']} — {result['message']}")
    ensure_data_dir()
    server = HTTPServer(('0.0.0.0', 8080), RequestHandler)
    print("Kanban server running on http://localhost:8080")
    server.serve_forever()
