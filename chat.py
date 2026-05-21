"""Chat-sidebar integration: tool-use loop driven by an open conversation.

Differs from notes.analyze_stream in two ways:
- No 'finish' tool — the loop terminates when the model returns a turn
  with no tool_use blocks (i.e., a text-only answer).
- Takes a full message history and returns the appended assistant turn(s)
  in the 'done' event so the caller can grow the history client-side.
"""
import copy
import json

import memory_context
from chat_tools import (
    READ_TOOL_DEFS, WRITE_TOOL_DEFS, READ_TOOLS,
    _WRITE_OP_NAMES, _queue_op,
    _summarize_read_result, reset_read_cache,
)


SYSTEM_PROMPT = """You are an assistant inside a personal kanban app.
You can answer questions about the user's boards and cards, and you
can propose changes (create cards, add comments, tick checklist items,
move cards, update fields, rename cards).

You have a memory wiki (org chart, external stakeholders, work preferences,
…). The wiki's README + INDEX, plus the pages that fit a small context
budget, are loaded into your first user message. INDEX lists every page —
use read_memory_page(name) to load any page that wasn't pre-loaded. Use
the wiki to ground assignment suggestions, recognize stakeholders by name,
and apply user-specific work patterns.

WRITE TOOLS DO NOT EXECUTE. They queue a proposed operation that the
user must confirm before it is applied. When you queue ops, briefly
explain in plain text what you proposed and why so the user can decide.

Use read tools liberally to ground your answers. Prefer:
- list_overdue / list_due_today / list_due_this_week for time questions
- find_by_label / find_by_assignee for filter questions
- search_cards for fuzzy title lookup
- get_card_by_id when you need a card's body, checklist, or comments

Writing cards (when you propose create_card):
- TITLES are short — the action in 5–8 words, imperative voice, no detail.
  Good: "Set up internal communication structure".
  Bad:  "Set up internal communication structure — meetings, frequencies, owners".
  If the title would exceed ~60 characters, contain an em-dash, colon, parenthetical,
  or a comma-separated list of items, the long part belongs in the description.
- The DESCRIPTION carries everything else: background, motivation, scope, mentioned
  people, dates, quoted phrases. Don't try to fit context into the title.
- ENUMERATIONS become checklist items, not prose. When the user describes discrete
  things to do — comma-separated lists, bullet lists, numbered lists, "for each X",
  "covering A, B, C" — put each item on the `checklist`, NOT in the description.
  The description explains WHY the work exists; the checklist captures WHAT to do.
- If a card already exists and the user adds new items to its list, propose
  add_checklist_item ops (one per item). Don't dump them in an add_comment.

Card IDs:
- Every card has a global ID like C-12. List/search tools return ids alongside titles.
- All write tools (except create_card) target a card by its id, e.g.
  tick_checklist(id="C-12", item="..."). The board and list are looked up
  from the id automatically.
- Use rename_card(id, title) to change a card's title.
- Use get_card_by_id(id) to fetch full card details on demand.

When you have answered the user, just stop calling tools and write a
short text response. The conversation continues; you do not need a
'finish' tool.

Refining queued proposals:
- The user may follow up with feedback on ops you previously queued
  ("change the assignee to X", "drop the second one", "split this in two").
  Their message will include a "(Currently proposed: …)" block listing
  what is already queued.
- Treat the refinement as a full re-emit: queue the COMPLETE corrected
  set of write tools again. Anything you don't re-queue is dropped.
- Do not try to "edit" or "delete" previous ops — there are no such tools.
  Just call create_card / add_comment / etc. fresh with the right values.
"""


CHAT_TOOLS = READ_TOOL_DEFS + WRITE_TOOL_DEFS

MAX_TOOL_TURNS = 16


def _with_memory_prepended(messages: list) -> list:
    """Prepend memory wiki blocks to the first user message in the history.

    Idempotent across turns: only prepends when the first user message hasn't
    already been augmented (detected by the presence of a "# MEMORY:" block).
    Returns a new list — does not mutate the caller's history.
    """
    blocks = memory_context.load_memory_context()
    if not blocks:
        return list(messages)
    out = [copy.deepcopy(m) for m in messages]
    for m in out:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        # The first user message in a chat history can be a plain string or
        # a list of content blocks. Normalize to list-of-blocks so we can
        # prepend uniformly.
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        elif not isinstance(content, list):
            return out
        # Skip if memory is already present (e.g. continuation of an existing
        # conversation where chat_stream was called previously).
        already = any(
            isinstance(b, dict) and b.get("type") == "text"
            and isinstance(b.get("text"), str) and b["text"].startswith("# MEMORY:")
            for b in content
        )
        if not already:
            m["content"] = blocks + content
        else:
            m["content"] = content
        return out
    return out


def _block_to_dict(block) -> dict:
    """Normalize an SDK content block into a plain dict for the next assistant turn."""
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    return {"type": btype or "unknown"}


def chat_stream(messages: list, *, model: str, client,
                max_turns: int = MAX_TOOL_TURNS,
                system_prompt: str | None = None):
    """Run a tool-use loop on top of the given conversation history.

    `system_prompt` overrides the default chat SYSTEM_PROMPT — the contextual
    Ask flow uses this to swap in its own pinned-context prompt while reusing
    the same loop, tools, and SSE event shape.

    Yields events. Event shapes (all dicts have a 'type' key):
      {"type": "started"}
      {"type": "turn",   "n": int}
      {"type": "tool",   "name": str, "args": dict}
      {"type": "result", "name": str, "summary": str}
      {"type": "queued", "op": str, "title"|"text"|...: ...}
      {"type": "text",   "text": str}
      {"type": "done",   "messages_appended": [...assistant blocks...],
                         "proposed_operations": [...]}
      {"type": "error",  "message": str}

    The caller should append `messages_appended` to its own history before
    sending the next user message.
    """
    yield {"type": "started"}

    proposed_ops: list[dict] = []
    msgs = _with_memory_prepended(messages)
    appended: list[dict] = []
    sys_text = system_prompt if system_prompt is not None else SYSTEM_PROMPT

    for turn in range(1, max_turns + 1):
        yield {"type": "turn", "n": turn}
        reset_read_cache()
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=CHAT_TOOLS,
            system=[
                {"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}},
            ],
            messages=msgs,
        )

        assistant_blocks = [_block_to_dict(b) for b in response.content]
        msgs.append({"role": "assistant", "content": assistant_blocks})
        appended.append({"role": "assistant", "content": assistant_blocks})

        for b in response.content:
            if getattr(b, "type", "") == "text":
                yield {"type": "text", "text": getattr(b, "text", "")}

        tool_use_blocks = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if not tool_use_blocks:
            break

        tool_results = []
        for block in tool_use_blocks:
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            tool_id = getattr(block, "id", "")
            yield {"type": "tool", "name": name, "args": args}
            try:
                if name in READ_TOOLS:
                    payload = READ_TOOLS[name](args)
                    yield {"type": "result", "name": name,
                           "summary": _summarize_read_result(name, args, payload)}
                elif name in _WRITE_OP_NAMES:
                    payload = _queue_op(name, args, proposed_ops)
                    # Send full args so the UI has every field needed to apply.
                    yield {"type": "queued", "op": name, "args": args}
                else:
                    payload = {"error": f"unknown tool '{name}'"}
                    yield {"type": "result", "name": name,
                           "summary": f"unknown tool '{name}'"}
            except (KeyError, ValueError, TypeError) as e:
                payload = {"error": str(e)}
                yield {"type": "result", "name": name, "summary": f"error: {e}"}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": json.dumps(payload),
            })

        msgs.append({"role": "user", "content": tool_results})
        appended.append({"role": "user", "content": tool_results})

    yield {"type": "done",
           "messages_appended": appended,
           "proposed_operations": proposed_ops}
