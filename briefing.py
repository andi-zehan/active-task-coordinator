"""AI Briefing flow: walk the boards and produce a Top-N focus narrative.

Mirrors the structure of `notes.py` (tool-use loop, SSE-style event yielding,
queued-then-applied write ops) but without note archiving — there is no source
document, just a user-editable prompt. The flow:

  1. analyze_stream(prompt) — drives the model with READ + WRITE tools and a
     `finish` tool. The model writes the briefing as plain text content blocks
     across turns; tool calls run in parallel for fact-finding and queuing
     proposed actions. Events yielded match `notes.analyze_stream` so the
     existing frontend SSE plumbing can be reused verbatim.

  2. refine_stream(...) — re-runs the model with the previous briefing text
     and ops + the user's feedback. Same event shapes; full re-emit semantics.

  3. apply_operations(ops) — delegates to `notes._apply_op` with note_id=None,
     skipping the per-note recording step.
"""
import copy
import json
from datetime import date, datetime

import memory_context
import notes
import server
from chat_tools import (
    READ_TOOL_DEFS, WRITE_TOOL_DEFS, READ_TOOLS,
    _WRITE_OP_NAMES, _queue_op,
    _summarize_read_result, _queued_summary_fields, reset_read_cache,
)


DEFAULT_PROMPT = """Walk through my boards and identify the Top 5 things I should focus on
next. Prioritize:
- Overdue items.
- Cards due today or this week.
- Cards that block other work (referenced in multiple cards' `relations`).
- Chains where finishing one card unblocks several others.

For each of the 5 items, write 1–2 sentences explaining why it's a priority
and how it connects to other cards. Reference cards by their global ID like
[[C-12]] so they render as clickable chips.

Use read tools liberally — list_overdue, list_due_today, list_due_this_week,
get_card_by_id, find_by_label — before forming conclusions. Do NOT include
cards that are already in 'done'.

Once you've narrowed down to your shortlist, call `get_card_by_id` on each
candidate and READ THE `## Comments` SECTION OF THE BODY. Comments are where
the user records the latest status, blockers, decisions, and next steps —
they often change what the card is really about and what should be said in
the briefing. Skipping them produces shallow, out-of-date summaries.

Only propose write-tool ops when the change is clearly justified by what
you found — e.g. a checklist item the user explicitly said was done, a card
the user told you to move, a relation that is plainly wrong. Default to
proposing nothing. If you're unsure whether the user wants a change, leave
it out and mention it in the briefing text instead. A briefing with zero
queued ops is the normal case, not a failure."""


SYSTEM_PROMPT = """You are an executive assistant inside a personal kanban app.

Your job: produce a focused briefing in markdown that helps the user decide
what to work on next. You have:
  - READ tools to inspect boards, search, and pull card details.
  - WRITE tools to QUEUE proposed operations (move/comment/tick/etc.).
    Write tools DO NOT execute; the user reviews queued ops before applying.
  - A `finish` tool to terminate the loop with a 1–2 sentence meta-summary.

Workflow:
- Use read tools liberally to ground the briefing — list_overdue,
  list_due_today, list_due_this_week, find_by_label, find_by_assignee,
  get_card_by_id, search_cards. The BOARD INDEX in the first user message
  is a cached overview; pull card bodies via get_card_by_id when context
  matters.
- For every card you decide to highlight, ALWAYS call `get_card_by_id` and
  read its full body — the `## Comments` section in particular often holds
  the most recent status update, a blocker, or a decision that changes the
  framing. Use that context in the briefing text rather than relying on
  title + due date alone.
- Write the briefing as MARKDOWN inside text content blocks (not tool args).
  Reference cards as [[C-N]] so they render as clickable chips.
- Be conservative about queuing write ops. Only queue an op when the
  evidence makes the change unambiguous (e.g. the user explicitly said a
  task is done, a card is plainly in the wrong list per its own body, a
  relation is clearly stale). If a change is merely plausible or "would
  be nice", DO NOT queue it — surface it as a suggestion in the briefing
  text and let the user ask. Zero queued ops is the expected default.
- When done, call `finish` with a short meta-summary. The full briefing lives
  in your text blocks, not in finish args.

Output style:
- Markdown with section headers (e.g. "## Today's Top 5").
- For each item: a short heading with the [[C-N]] chip, then 1–2 sentences
  on why it's a priority and how it connects to other cards.
- Be specific about urgency (due dates, overdue counts) and dependencies
  (which cards reference which via relations).
- Do NOT include cards in the 'done' list.

Refining:
- The user may follow up with feedback ("focus only on board X", "exclude
  the backlog", "make it shorter"). Treat refinement as a full re-emit:
  re-write the briefing AND re-queue the COMPLETE corrected set of write
  ops. Anything you don't re-queue is dropped.
"""


FINISH_TOOL_DEF = {
    "name": "finish",
    "description": (
        "Call once the briefing is written in text content blocks. Queuing "
        "write ops is optional — only do so when clearly justified (most "
        "briefings queue zero). Provide a 1–2 sentence meta-summary (the "
        "briefing itself is your text, not the summary)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
}


def _build_briefing_tools() -> list[dict]:
    """READ + WRITE + finish, deep-copied so per-flow tweaks don't bleed."""
    tools = [copy.deepcopy(t) for t in (READ_TOOL_DEFS + WRITE_TOOL_DEFS)]
    return tools + [FINISH_TOOL_DEF]


TOOLS = _build_briefing_tools()
MAX_TOOL_TURNS = 16


def _new_briefing_id() -> str:
    return "briefing-" + datetime.now().strftime("%Y-%m-%d-%H%M%S")


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


def _drive_loop(messages: list, *, model: str, client, max_turns: int):
    """Shared tool-use loop body. Yields events; returns (text, summary, ops, finished)
    via a final dict event of type '__return__' so callers can grab the state.
    """
    proposed_ops: list[dict] = []
    text_chunks: list[str] = []
    summary = ""
    finished = False

    for turn in range(1, max_turns + 1):
        yield {"type": "turn", "n": turn}
        reset_read_cache()
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=TOOLS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=messages,
        )

        assistant_blocks = [_block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": assistant_blocks})

        # Stream text blocks as they arrive AND collect them into the briefing.
        for b in response.content:
            if getattr(b, "type", "") == "text":
                t = getattr(b, "text", "") or ""
                if t:
                    text_chunks.append(t)
                    yield {"type": "text", "text": t}

        tool_use_blocks = [b for b in response.content
                           if getattr(b, "type", "") == "tool_use"]
        if not tool_use_blocks:
            break

        tool_results = []
        for block in tool_use_blocks:
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            tool_id = getattr(block, "id", "")
            yield {"type": "tool", "name": name, "args": args}
            try:
                if name == "finish":
                    summary = args.get("summary", "")
                    finished = True
                    payload = {"ok": True}
                    yield {"type": "finish", "summary": summary}
                elif name in READ_TOOLS:
                    payload = READ_TOOLS[name](args)
                    yield {"type": "result", "name": name,
                           "summary": _summarize_read_result(name, args, payload)}
                elif name in _WRITE_OP_NAMES:
                    payload = _queue_op(name, args, proposed_ops)
                    yield {"type": "queued", "op": name,
                           **_queued_summary_fields(name, args)}
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

        messages.append({"role": "user", "content": tool_results})

        if finished:
            break

    # Sentinel event so the caller can pluck the final state out of the generator.
    yield {"type": "__return__",
           "text": "".join(text_chunks),
           "summary": summary,
           "operations": proposed_ops,
           "finished": finished}


def analyze_stream(user_prompt: str, *, model: str, client,
                   max_turns: int = MAX_TOOL_TURNS):
    """Drive the briefing tool-use loop. Yields the same event shapes as
    `notes.analyze_stream`, plus 'text' events as the model emits text blocks.

      {"type": "started",  "briefing_id": str}
      {"type": "turn",     "n": int}
      {"type": "tool",     "name": str, "args": dict}
      {"type": "result",   "name": str, "summary": str}
      {"type": "queued",   "op": str, ...}
      {"type": "text",     "text": str}     # incremental briefing markdown
      {"type": "finish",   "summary": str}
      {"type": "done",     "briefing_id": str, "summary": str,
                           "text": str, "operations": [...]}
      {"type": "error",    "message": str}
    """
    briefing_id = _new_briefing_id()
    yield {"type": "started", "briefing_id": briefing_id}

    toc = notes.build_toc()

    first_user_blocks = memory_context.load_memory_context() + [
        {
            "type": "text",
            "text": "BOARD INDEX:\n" + json.dumps(toc),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                f"BRIEFING_ID: {briefing_id}\n"
                f"TODAY: {date.today().strftime('%A %Y-%m-%d')}\n\n"
                f"USER REQUEST:\n{user_prompt}"
            ),
        },
    ]
    messages = [{"role": "user", "content": first_user_blocks}]

    final = None
    for ev in _drive_loop(messages, model=model, client=client, max_turns=max_turns):
        if ev.get("type") == "__return__":
            final = ev
            continue
        yield ev

    if final is None:
        yield {"type": "error", "message": "loop terminated without producing state"}
        return

    if not final["text"] and not final["operations"] and not final["finished"]:
        yield {"type": "error",
               "message": "Model exited without producing a briefing or proposing operations."}
        return

    yield {"type": "done",
           "briefing_id": briefing_id,
           "summary": final["summary"],
           "text": final["text"],
           "operations": final["operations"]}


def refine_stream(briefing_id: str, current_ops: list, current_text: str,
                  feedback: str, *, model: str, client,
                  max_turns: int = MAX_TOOL_TURNS):
    """Re-run the briefing flow with the previous briefing + ops + feedback.

    The model is told to fully re-emit both the briefing text AND the corrected
    set of write ops. The caller replaces its local copies with the new
    `text` and `operations` from the 'done' event.
    """
    yield {"type": "started", "briefing_id": briefing_id}

    toc = notes.build_toc()

    if current_ops:
        proposals_block = (
            "PREVIOUSLY PROPOSED OPS (you queued these last turn):\n"
            + "\n".join(f"  {i+1}. {notes._describe_op_for_prompt(op)}"
                        for i, op in enumerate(current_ops))
        )
    else:
        proposals_block = "PREVIOUSLY PROPOSED OPS: (none — start fresh.)"

    prev_text_block = (
        f"PREVIOUS BRIEFING TEXT:\n{current_text}"
        if current_text else
        "PREVIOUS BRIEFING TEXT: (none — generate from scratch.)"
    )

    first_user_blocks = memory_context.load_memory_context() + [
        {
            "type": "text",
            "text": "BOARD INDEX:\n" + json.dumps(toc),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                f"BRIEFING_ID: {briefing_id}\n"
                f"TODAY: {date.today().strftime('%A %Y-%m-%d')}\n\n"
                f"{prev_text_block}\n\n"
                f"{proposals_block}\n\n"
                f"USER FEEDBACK:\n{feedback}\n\n"
                f"Re-write the briefing AND re-emit the COMPLETE "
                f"corrected set of write-tool calls. Anything you don't "
                f"re-queue is dropped. Then call finish."
            ),
        },
    ]
    messages = [{"role": "user", "content": first_user_blocks}]

    final = None
    for ev in _drive_loop(messages, model=model, client=client, max_turns=max_turns):
        if ev.get("type") == "__return__":
            final = ev
            continue
        yield ev

    if final is None:
        yield {"type": "error", "message": "loop terminated without producing state"}
        return

    yield {"type": "done",
           "briefing_id": briefing_id,
           "summary": final["summary"],
           "text": final["text"],
           "operations": final["operations"]}


def apply_operations(operations: list) -> dict:
    """Apply briefing ops directly via notes._apply_op. No note recording."""
    return notes.apply_operations(operations, note_id=None)
