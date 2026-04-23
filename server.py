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
