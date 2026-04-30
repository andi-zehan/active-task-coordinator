"""Shared tool definitions and implementations for the LLM-driven flows.

Used by both Process Notes (notes.py) and the Chat sidebar (chat.py).
"""
import difflib
import json
import re


_LIST_ENUM = ["ideas", "backlog", "in-progress", "done"]
_CONF_ENUM = ["high", "med", "low"]


# --- Body parsing helpers (used by _tool_read_card) ---

def _parse_checklist(body: str) -> tuple[list[str], list[str]]:
    """Return (open_items, done_items) from a card body's '## Checklist' section."""
    todo, done = [], []
    in_checklist = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_checklist = stripped == "## Checklist"
            continue
        if not in_checklist:
            continue
        m = re.match(r"-\s*\[(\s|x|X)\]\s*(.*)", stripped)
        if not m:
            continue
        text = m.group(2).strip()
        if m.group(1).lower() == "x":
            done.append(text)
        else:
            todo.append(text)
    return todo, done


def _extract_description(body: str) -> str:
    """Return the text under '## Description', truncated to 200 chars."""
    desc_lines = []
    in_desc = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_desc = stripped == "## Description"
            continue
        if in_desc and stripped:
            desc_lines.append(stripped)
    text = " ".join(desc_lines)
    return text[:200]


# --- Tool-def schema builder ---

def _op_props(extra: dict, *, target_card: bool, with_confidence: bool = True) -> dict:
    base = {
        "board": {"type": "string"},
        "list": {"type": "string", "enum": _LIST_ENUM},
    }
    if target_card:
        base["card"] = {"type": "string"}
    base.update(extra)
    if with_confidence:
        base["confidence"] = {"type": "string", "enum": _CONF_ENUM}
        base["reason"] = {"type": "string"}
    return base


# --- Read-tool definitions ---

READ_TOOL_DEFS = [
    {
        "name": "list_boards",
        "description": "List all boards with name and card count.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_cards",
        "description": "List cards on a board (optionally filtered to one list). Returns slug, title, labels, due, assignee. Call read_card for the body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string"},
                "list": {"type": "string", "enum": _LIST_ENUM},
            },
            "required": ["board"],
        },
    },
    {
        "name": "search_cards",
        "description": "Title-similarity search across all boards. ALWAYS call before create_card so you don't duplicate work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_card",
        "description": "Full card: description, checklist with checked state, comments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string"},
                "list": {"type": "string", "enum": _LIST_ENUM},
                "slug": {"type": "string"},
            },
            "required": ["board", "list", "slug"],
        },
    },
]

# --- Write-tool definitions ---

WRITE_TOOL_DEFS = [
    {
        "name": "create_card",
        "description": "Propose a new card. Queued for user confirmation, not created immediately.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({
                "title": {"type": "string"},
                "description": {"type": "string"},
                "checklist": {"type": "array", "items": {"type": "string"}},
                "due": {"type": "string", "description": "YYYY-MM-DD"},
                "assignee": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            }, target_card=False),
            "required": ["board", "list", "title", "confidence", "reason"],
        },
    },
    {
        "name": "add_comment",
        "description": "Propose adding a comment to an existing card.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({"text": {"type": "string"}}, target_card=True),
            "required": ["board", "list", "card", "text", "confidence", "reason"],
        },
    },
    {
        "name": "tick_checklist",
        "description": "Propose marking an existing checklist item done. 'item' is matched as a case-insensitive substring.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({"item": {"type": "string"}}, target_card=True),
            "required": ["board", "list", "card", "item", "confidence", "reason"],
        },
    },
    {
        "name": "add_checklist_item",
        "description": "Propose adding a new item to a card's checklist.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({"item": {"type": "string"}}, target_card=True),
            "required": ["board", "list", "card", "item", "confidence", "reason"],
        },
    },
    {
        "name": "move_card",
        "description": "Propose moving a card to a different list.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({
                "target_list": {"type": "string", "enum": _LIST_ENUM},
            }, target_card=True),
            "required": ["board", "list", "card", "target_list", "confidence", "reason"],
        },
    },
    {
        "name": "update_field",
        "description": "Propose updating due, assignee, or labels on an existing card.",
        "input_schema": {
            "type": "object",
            "properties": _op_props({
                "field": {"type": "string", "enum": ["due", "assignee", "labels"]},
                "value": {"description": "string for due/assignee, array of strings for labels"},
            }, target_card=True),
            "required": ["board", "list", "card", "field", "value", "confidence", "reason"],
        },
    },
]


# --- Read-tool implementations ---

def _tool_list_boards(_args: dict) -> dict:
    import server
    boards_order_path = server.DATA_DIR / "_boards-order.json"
    if not boards_order_path.exists():
        return {"boards": []}
    out = []
    for slug in json.loads(boards_order_path.read_text(encoding="utf-8")):
        meta = server.read_board_meta(slug)
        if meta is None:
            continue
        count = 0
        for list_slug in server.LISTS:
            order = server.DATA_DIR / "boards" / slug / list_slug / "_order.json"
            if order.exists():
                count += len(json.loads(order.read_text(encoding="utf-8")))
        out.append({"slug": slug, "name": meta.get("name", slug), "card_count": count})
    return {"boards": out}


def _tool_list_cards(args: dict) -> dict:
    import server
    board = args["board"]
    if server.read_board_meta(board) is None:
        return {"error": f"board '{board}' not found"}
    only = args.get("list")
    lists = [only] if only else server.LISTS
    out = []
    for list_slug in lists:
        order = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
        if not order.exists():
            continue
        for slug in json.loads(order.read_text(encoding="utf-8")):
            card = server.read_card(board, list_slug, slug)
            if card is None:
                continue
            out.append({
                "l": list_slug,
                "s": slug,
                "title": card.get("title", ""),
                "labels": card.get("labels") or [],
                "due": card.get("due", ""),
                "assignee": card.get("assignee", ""),
            })
    return {"cards": out}


def _all_card_titles() -> list[tuple[str, str, str, str]]:
    """Return [(board, list, slug, title), ...] for every card."""
    import server
    out = []
    boards_order_path = server.DATA_DIR / "_boards-order.json"
    if not boards_order_path.exists():
        return out
    for board in json.loads(boards_order_path.read_text(encoding="utf-8")):
        for list_slug in server.LISTS:
            order = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
            if not order.exists():
                continue
            for slug in json.loads(order.read_text(encoding="utf-8")):
                card = server.read_card(board, list_slug, slug)
                if card is None:
                    continue
                out.append((board, list_slug, slug, card.get("title", "")))
    return out


def _tool_search_cards(args: dict) -> dict:
    query = args["query"].lower()
    limit = max(1, min(int(args.get("limit", 8)), 25))
    cards = _all_card_titles()
    titles = [t.lower() for (_b, _l, _s, t) in cards]
    matches = []
    fuzzy = difflib.get_close_matches(query, titles, n=limit, cutoff=0.4)
    seen = set()
    for low_title in fuzzy:
        for i, t in enumerate(titles):
            if t == low_title and i not in seen:
                seen.add(i)
                b, l, s, title = cards[i]
                matches.append({"b": b, "l": l, "s": s, "title": title})
                break
    for i, t in enumerate(titles):
        if i in seen or query not in t:
            continue
        if len(matches) >= limit:
            break
        seen.add(i)
        b, l, s, title = cards[i]
        matches.append({"b": b, "l": l, "s": s, "title": title})
    return {"matches": matches[:limit]}


def _tool_read_card(args: dict) -> dict:
    import server
    card = server.read_card(args["board"], args["list"], args["slug"])
    if card is None:
        return {"error": "card not found"}
    todo, done = _parse_checklist(card.get("body", ""))
    return {
        "title": card.get("title", ""),
        "labels": card.get("labels") or [],
        "due": card.get("due", ""),
        "assignee": card.get("assignee", ""),
        "description": _extract_description(card.get("body", "")),
        "checklist_todo": todo,
        "checklist_done": done,
        "body": card.get("body", ""),
    }


# --- Write-tool dispatch (queues, doesn't execute) ---

_WRITE_OP_NAMES = {
    "create_card", "add_comment", "tick_checklist",
    "add_checklist_item", "move_card", "update_field",
}


def _queue_op(name: str, args: dict, queue: list) -> dict:
    op = {"op": name, **args}
    queue.append(op)
    return {"queued": True, "op": name, "index": len(queue) - 1}


READ_TOOLS = {
    "list_boards": _tool_list_boards,
    "list_cards": _tool_list_cards,
    "search_cards": _tool_search_cards,
    "read_card": _tool_read_card,
}


# --- Event-summary helpers (used by the streaming loops in notes.py and chat.py) ---

def _summarize_read_result(name: str, args: dict, payload: dict) -> str:
    """Short human-readable description of what a read tool returned."""
    if "error" in payload:
        return f"error: {payload['error']}"
    if name == "list_boards":
        n = len(payload.get("boards", []))
        return f"{n} board(s)"
    if name == "list_cards":
        n = len(payload.get("cards", []))
        scope = args.get("board", "?")
        if args.get("list"):
            scope += "/" + args["list"]
        return f"{n} card(s) in {scope}"
    if name == "search_cards":
        n = len(payload.get("matches", []))
        return f"{n} match(es) for '{args.get('query', '')}'"
    if name == "read_card":
        return f"{args.get('board','?')}/{args.get('list','?')}/{args.get('slug','?')}"
    return ""


def _queued_summary_fields(name: str, args: dict) -> dict:
    """Fields to surface in a 'queued' event so the UI can show what's been proposed."""
    if name == "create_card":
        return {"board": args.get("board", ""), "list": args.get("list", ""),
                "title": args.get("title", "")}
    if name == "add_comment":
        text = args.get("text", "")
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "text": text[:60]}
    if name in ("tick_checklist", "add_checklist_item"):
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "item": args.get("item", "")}
    if name == "move_card":
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "target_list": args.get("target_list", "")}
    if name == "update_field":
        return {"board": args.get("board", ""), "card": args.get("card", ""),
                "field": args.get("field", "")}
    return {}
