"""Shared helper: load memory wiki into Anthropic SDK content blocks.

Called by chat.py, notes.py, briefing.py so every agentic flow benefits from
the same context (internal-organization, who-is-who, work-preferences, …).

Budget strategy: always include README + INDEX, then greedily include wiki
pages in INDEX order until a soft line budget is hit. Pages that didn't fit
remain reachable via the read_memory_page tool below.

Caching strategy: Anthropic caps requests at 4 cache_control breakpoints
total. Memory uses ONE breakpoint, attached to the last memory block. That
caches the whole memory prefix together; any page edit busts the whole
cache (which is fine — wiki edits are rare). Leaves 3 breakpoints for the
system prompt + per-flow context blocks (BOARD INDEX, note text, etc.).
"""
import re

import memory_store

# Soft line budget for inline memory. Claude Code recommends ~200 lines for
# CLAUDE.md adherence; we apply the same heuristic to wiki content. Pages
# beyond this stay reachable via read_memory_page.
LINE_BUDGET = 200


def load_memory_context() -> list[dict]:
    """Return a list of Anthropic content blocks ready to prepend to a user message.

    Always-loaded blocks come first (README, INDEX). Then as many wiki pages
    as fit the budget, in the order they appear in INDEX.md. Each block has
    ephemeral cache_control so the prefix stays in the prompt cache when
    individual pages change.

    Returns an empty list if memory has not been initialized yet.
    """
    readme = memory_store.read_readme()
    index = memory_store.read_index()
    if not readme and not index:
        return []

    blocks: list[dict] = []
    used_lines = 0

    if readme:
        blocks.append(_block("# MEMORY: README\n\n" + readme))
        used_lines += _count_lines(readme)
    if index:
        blocks.append(_block("# MEMORY: INDEX\n\n" + index))
        used_lines += _count_lines(index)

    ordered_pages = _pages_in_index_order(index)
    pages_loaded = 0
    for name in ordered_pages:
        content = memory_store.read_page(name)
        if content is None:
            continue
        page_lines = _count_lines(content)
        # Always include the first page even if it alone would exceed the
        # budget — INDEX ordering is the user's curation of "what matters
        # most." From page 2 onward, the soft budget applies.
        if pages_loaded > 0 and used_lines + page_lines > LINE_BUDGET:
            break
        blocks.append(_block(f"# MEMORY: {name}.md\n\n{content}"))
        used_lines += page_lines
        pages_loaded += 1

    # Anthropic caps cache_control at 4 per request. One breakpoint at the
    # end of memory caches the whole prefix; we leave the budget free for
    # the caller (system prompt, BOARD INDEX, etc.).
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}

    return blocks


def _block(text: str) -> dict:
    return {"type": "text", "text": text}


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


_INDEX_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")


def _pages_in_index_order(index_text: str) -> list[str]:
    """Extract wiki page slugs from INDEX.md in the order they appear.

    Looks for markdown links of the form [label](slug.md). Pages not listed
    in INDEX are intentionally excluded — the index is the curation surface.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _INDEX_LINK_RE.finditer(index_text):
        target = m.group(2)
        if "/" in target:  # ignore links into subdirs (e.g. _sources/, log/)
            continue
        slug = target[:-3]  # strip .md
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


# --- Tool def + impl for on-demand page loading ---

READ_MEMORY_TOOL_DEF = {
    "name": "read_memory_page",
    "description": (
        "Read a wiki page from memory by name (without .md). Use this when "
        "INDEX.md lists a page that wasn't included in your initial context "
        "and you need its content to answer or to ground a proposal."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
}


def tool_read_memory_page(args: dict) -> dict:
    name = args.get("name", "")
    try:
        content = memory_store.read_page(name)
    except memory_store.InvalidSlug as e:
        return {"error": str(e)}
    if content is None:
        return {"error": f"unknown memory page: {name}"}
    return {"name": name, "content": content}
