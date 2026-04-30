"""Chat-sidebar integration: tool-use loop driven by an open conversation.

Differs from notes.analyze_stream in two ways:
- No 'finish' tool — the loop terminates when the model returns a turn
  with no tool_use blocks (i.e., a text-only answer).
- Takes a full message history and returns the appended assistant turn(s)
  in the 'done' event so the caller can grow the history client-side.
"""
import json

from chat_tools import (
    READ_TOOL_DEFS, WRITE_TOOL_DEFS, READ_TOOLS,
    _WRITE_OP_NAMES, _queue_op,
    _summarize_read_result,
)


SYSTEM_PROMPT = """You are an assistant inside a personal kanban app.
You can answer questions about the user's boards and cards, and you
can propose changes (create cards, add comments, tick checklist items,
move cards, update fields).

WRITE TOOLS DO NOT EXECUTE. They queue a proposed operation that the
user must confirm before it is applied. When you queue ops, briefly
explain in plain text what you proposed and why so the user can decide.

Use read tools liberally to ground your answers. Prefer:
- list_overdue / list_due_today / list_due_this_week for time questions
- find_by_label / find_by_assignee for filter questions
- search_cards for fuzzy title lookup
- read_card when you need a card's body, checklist, or comments

When you have answered the user, just stop calling tools and write a
short text response. The conversation continues; you do not need a
'finish' tool.
"""


CHAT_TOOLS = READ_TOOL_DEFS + WRITE_TOOL_DEFS

MAX_TOOL_TURNS = 16


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
                max_turns: int = MAX_TOOL_TURNS):
    """Run a tool-use loop on top of the given conversation history.

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
    msgs = list(messages)
    appended: list[dict] = []

    for turn in range(1, max_turns + 1):
        yield {"type": "turn", "n": turn}
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=CHAT_TOOLS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
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
