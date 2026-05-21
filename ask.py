"""Contextual Ask: chat-style multi-turn dialog seeded with a fixed snapshot.

Three context shapes:

  {"type": "view"}                   → all cards across all boards (TOC)
  {"type": "card", "id": "C-12"}     → one card's body + relations + siblings
  {"type": "memory"}                 → no card snapshot — for wiki work only.
                                       Memory pages are already injected by
                                       memory_context.load_memory_context()
                                       into every Ask request; this context
                                       just skips the kanban TOC so a memory
                                       question doesn't pay for cards it
                                       doesn't need.

The view payload is the same shape as `notes.build_toc()`. The card payload
hand-builds the relevant slice so the model sees the actual checklist /
description / comments without a tool round-trip.

The seed is a single user message with cache_control on the heavy block.
Reused across turns by the client, so multi-turn doesn't re-bill the snapshot.

Apply uses notes.apply_operations(ops, note_id=None) — same path the
briefing flow already exercises.
"""
import json
from datetime import date

import server
from notes import build_toc


SYSTEM_PROMPT = """You are an assistant inside a personal kanban app, answering
a single user question (and follow-ups) about a fixed scope of cards.

The user has pinned a CONTEXT in the first message — either a snapshot of all
cards in their current view, or one specific card and its neighborhood. Treat
that snapshot as the primary source of truth for this conversation. It does
NOT update if the underlying data changes mid-conversation.

You may still call read tools (search_cards, find_by_label, find_by_assignee,
get_card_by_id, etc.) when the snapshot doesn't have what you need — for
example to fetch a card body, or to look outside the pinned scope when the
user explicitly asks. Don't redundantly fetch what's already in the snapshot.

Write tools (create_card, add_comment, tick_checklist, add_checklist_item,
move_card, update_field, rename_card) DO NOT execute. They QUEUE a proposed
operation that the user can apply with one click. The user already sees an
Apply button next to your queued ops — there is no need to ask for permission
or describe an op before queuing it.

When the user's question implies a change ("reassign these", "split this in
two", "mark X done", "create a card for Y"), QUEUE THE OPS IMMEDIATELY in
the same turn as your answer. Don't propose-then-wait — that adds a useless
extra round trip. The Apply button IS the approval step.

Pattern:
- Answer the question in prose (markdown).
- In the same turn, call the relevant write tools to queue concrete ops.
- One short closing line if anything is non-obvious about the proposed set.

Only skip queuing when the question is purely informational (a count, a
summary, "who has the most cards") or when you genuinely lack information
to propose something specific.

Cards in the 'done' list are READ-ONLY. Never propose write operations on
done cards.

Writing cards (when you propose create_card):
- TITLES are short — the action in 5–8 words, imperative voice, no detail.
- DESCRIPTION carries background / motivation / scope / quotes.
- ENUMERATIONS become checklist items, not prose.

Refining queued proposals:
- The user may follow up with feedback on ops you previously queued. Treat
  the refinement as a full re-emit: queue the COMPLETE corrected set of
  write tools again. Anything you don't re-queue is dropped.

When you have answered the user, just stop calling tools and write a short
text response. The conversation continues; you do not need a 'finish' tool.
"""


def _short_card(c: dict) -> dict:
    """Index-style card row: id, list, title, labels, due, assignee."""
    return {
        "id": c.get("id"),
        "l": c.get("list"),
        "title": c.get("title", ""),
        "labels": c.get("labels") or [],
        "due": c.get("due", ""),
        "assignee": c.get("assignee", ""),
    }


def _card_full(card: dict) -> dict:
    """Card with body — for the pinned card in a card-context Ask."""
    return {
        "id": card.get("id"),
        "board": card.get("board"),
        "list": card.get("list"),
        "title": card.get("title", ""),
        "labels": card.get("labels") or [],
        "due": card.get("due", ""),
        "assignee": card.get("assignee", ""),
        "relations": card.get("relations") or [],
        "body": card.get("body", ""),
    }


def _board_siblings(board_slug: str, exclude_id: str) -> list[dict]:
    """Short rows of every card on the same board (minus the pinned one),
    so the model can reason about a card in the context of its peers."""
    siblings = []
    for list_slug in server.LISTS:
        if list_slug == "done":
            continue
        order_file = server.DATA_DIR / "boards" / board_slug / list_slug / "_order.json"
        if not order_file.exists():
            continue
        for card_slug in json.loads(order_file.read_text(encoding="utf-8")):
            c = server.read_card(board_slug, list_slug, card_slug)
            if c is None or c.get("id") == exclude_id:
                continue
            siblings.append(_short_card(c))
    return siblings


def _related_cards(relations: list) -> list[dict]:
    """Resolve each relation id to a short row, skipping unknown ids."""
    out = []
    for rel in (relations or []):
        if isinstance(rel, dict):
            rid = rel.get("id")
        else:
            rid = rel
        if not rid:
            continue
        located = server.resolve_id(rid)
        if located is None:
            continue
        b, l = located
        c = server.read_card(b, l, rid)
        if c is not None:
            out.append(_short_card(c))
    return out


def build_context_payload(context: dict) -> dict:
    """Materialize the context dict into the JSON-serializable payload that
    becomes the cached prefix of the conversation.

    Raises ValueError on unknown / invalid context.
    """
    ctype = context.get("type")
    if ctype == "view":
        # All boards / all live cards. The view name is informational —
        # the snapshot is the same regardless of which view (Kanban /
        # Dashboard / Calendar / Table) the user opened Ask from.
        toc = build_toc()
        return {
            "context_type": "view",
            "view": context.get("view", ""),
            "today": date.today().isoformat(),
            "boards": toc.get("boards", []),
        }
    if ctype == "memory":
        # Intentionally empty. The wiki itself is already in the prompt via
        # memory_context.load_memory_context() (chat.py prepends it to every
        # turn). No need to duplicate it here. We keep a marker so the
        # SYSTEM_PROMPT / label code can branch on it.
        return {
            "context_type": "memory",
            "today": date.today().isoformat(),
        }
    if ctype == "card":
        cid = (context.get("id") or "").strip()
        if not cid:
            raise ValueError("card context missing 'id'")
        located = server.resolve_id(cid)
        if located is None:
            raise ValueError(f"unknown card id: {cid}")
        board, list_slug = located
        card = server.read_card(board, list_slug, cid)
        if card is None:
            raise ValueError(f"card {cid} not found")
        return {
            "context_type": "card",
            "today": date.today().isoformat(),
            "card": _card_full(card),
            "related": _related_cards(card.get("relations") or []),
            "siblings": _board_siblings(board, exclude_id=cid),
        }
    raise ValueError(f"unknown context type: {ctype!r}")


def _label_for(payload: dict) -> str:
    """Human-readable one-liner describing the pinned scope."""
    if payload["context_type"] == "view":
        n = sum(len(b.get("cards") or []) for b in payload.get("boards", []))
        view = payload.get("view") or "current view"
        return f"all cards in {view} ({n})"
    if payload["context_type"] == "card":
        c = payload["card"]
        return f"card {c['id']}: {c['title']}"
    if payload["context_type"] == "memory":
        return "memory wiki"
    return "context"


def build_seed_message(payload: dict, question: str) -> dict:
    """Build the first user message: pinned context (cached) + question.

    For memory context the pinned snapshot is empty (the wiki itself is
    injected by memory_context.load_memory_context()) — we send a one-line
    marker instead of a heavy JSON blob and skip the cache_control breakpoint.
    """
    label = _label_for(payload)
    if payload.get("context_type") == "memory":
        pinned_block = {
            "type": "text",
            "text": (
                f"PINNED CONTEXT ({label}): this conversation is scoped to the "
                f"memory wiki. The wiki pages above are your source of truth; "
                f"do NOT call card read-tools (list_cards / list_overdue / "
                f"find_by_assignee / etc.) unless the user explicitly asks "
                f"about cards. Use save_as_source and propose_memory_edits "
                f"to update the wiki."
            ),
        }
    else:
        pinned_block = {
            "type": "text",
            "text": f"PINNED CONTEXT ({label}):\n" + json.dumps(payload),
            "cache_control": {"type": "ephemeral"},
        }
    return {
        "role": "user",
        "content": [
            pinned_block,
            {
                "type": "text",
                "text": f"USER QUESTION:\n{question}",
            },
        ],
    }
