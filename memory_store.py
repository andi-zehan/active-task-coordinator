"""On-disk model for the memory wiki.

Layout (under memory_config.get_memory_dir()):
    INDEX.md                       — catalog of wiki pages, always loaded
    README.md                      — schema doc (conventions, link syntax)
    log.md                         — append-only event log; rotated at LOG_ROTATE_LINES
    log/YYYY-MM.md                 — rotated archives of log.md
    internal-organization.md       — seed wiki page
    who-is-who.md                  — seed wiki page
    work-preferences.md            — seed wiki page
    _sources/<YYYY-MM-DD-slug>.md  — immutable raw inputs (paste-ins, uploads)
    _proposals/<id>.md             — pending edit batches awaiting review

This module is the *only* module that reads/writes these paths. memory_ops
calls into here; memory_context only reads.
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path

import memory_config

# --- Layout constants ---

INDEX_FILE = "INDEX.md"
README_FILE = "README.md"
LOG_FILE = "log.md"
LOG_ARCHIVE_DIR = "log"
SOURCES_DIR = "_sources"
PROPOSALS_DIR = "_proposals"

# Top-level files reserved by the layout — never treated as wiki pages.
_RESERVED_FILES = {INDEX_FILE, README_FILE, LOG_FILE}

LOG_ROTATE_LINES = 1000

# Slug accepts letters, digits, dot, dash, underscore. Mirrors notes._NOTE_ID_RE.
# Rejects anything with path separators, dot-only names, leading dots.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# --- Seed content ---

_SEED_README = """# Memory schema

This directory is a Karpathy-style LLM wiki for the ATC kanban app. Three layers:

1. **Raw sources** (`_sources/`) — immutable inputs you've dropped in. Never
   edited, only superseded by a newer source.
2. **Wiki pages** (top-level `*.md`) — distilled, compiled views the LLM writes
   and you read. Plain text — never references kanban card IDs.
3. **Schema** (this file + `INDEX.md`) — conventions, plus the catalog used to
   decide which pages get pulled into agent context.

## Operations

- **Ingest**: paste/upload a source → LLM proposes wiki edits → you approve.
- **Query**: chat reads wiki pages by default; useful answers can be filed
  back as new pages.
- **Lint**: scheduled (and manual) sweep for contradictions, stale claims,
  orphan pages, missing cross-references. Emits one proposal batch.

All wiki writes go through the proposal review UI — the LLM never edits a
wiki page directly. Hand-edits in your editor are fine; the LLM re-reads on
the next operation.

## Conventions

- Wiki pages refer to people, projects, and stakeholders by **name only**.
  No `[[C-N]]` card references — cards are ephemeral, memory is not.
- Cross-link wiki pages with `[[page-name]]` (filename without `.md`).
- Keep individual pages under ~200 lines. Lint will propose a split when a
  page outgrows the budget.
"""

_SEED_INDEX = """# INDEX

Catalog of wiki pages. The agent loads pages in this order until its context
budget is hit; pages further down are accessible on demand via `read_memory_page`.

- [internal-organization](internal-organization.md) — internal org chart, roles, reporting lines
- [who-is-who](who-is-who.md) — external stakeholders, vendors, partners
- [work-preferences](work-preferences.md) — how I delegate, triage, track work
"""

_SEED_PAGES = {
    "internal-organization.md": (
        "# Internal organization\n\n"
        "_(Empty — drop an org chart into `_sources/` and ask the agent to ingest it,\n"
        "or edit this file directly.)_\n"
    ),
    "who-is-who.md": (
        "# Who is who\n\n"
        "_(External stakeholders: customers, vendors, partners, regulators.\n"
        "Empty for now.)_\n"
    ),
    "work-preferences.md": (
        "# Work preferences\n\n"
        "_(How I delegate, how I triage, what I track. Empty for now.)_\n"
    ),
}


# --- Path resolution and validation ---

def _root() -> Path:
    return memory_config.get_memory_dir()


class InvalidSlug(ValueError):
    """Raised when a page/source/proposal slug fails validation."""


def _validate_slug(slug: str) -> str:
    """Reject anything that could escape the directory or shadow reserved files."""
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise InvalidSlug(f"invalid slug: {slug!r}")
    if slug.startswith(".") or ".." in slug:
        raise InvalidSlug(f"invalid slug: {slug!r}")
    return slug


def _page_filename(name: str) -> str:
    """Accept either 'foo' or 'foo.md'; return canonical 'foo.md'."""
    bare = name[:-3] if name.endswith(".md") else name
    _validate_slug(bare)
    return bare + ".md"


def _within_root(path: Path) -> Path:
    """Defense-in-depth: confirm a resolved path stays inside the memory root."""
    root = _root().resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise InvalidSlug(f"path escapes memory root: {path}") from e
    return resolved


# --- Initialization ---

def init_if_missing() -> bool:
    """Create the memory directory and seed files if absent. Idempotent.

    Returns True if any file was created, False if everything already existed.
    """
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    (root / SOURCES_DIR).mkdir(exist_ok=True)
    (root / PROPOSALS_DIR).mkdir(exist_ok=True)
    (root / LOG_ARCHIVE_DIR).mkdir(exist_ok=True)

    created = False
    seed = {
        README_FILE: _SEED_README,
        INDEX_FILE: _SEED_INDEX,
        LOG_FILE: "",
        **_SEED_PAGES,
    }
    for name, content in seed.items():
        target = root / name
        if not target.exists():
            target.write_text(content, encoding="utf-8")
            created = True
    return created


# --- Wiki pages ---

def list_pages() -> list[dict]:
    """Return [{name, size_lines, mtime}] for every wiki page (top-level .md).

    Excludes README.md, INDEX.md, log.md, and anything inside subdirectories.
    """
    root = _root()
    if not root.exists():
        return []
    out = []
    for p in sorted(root.glob("*.md")):
        if p.name in _RESERVED_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({
            "name": p.stem,
            "size_lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "mtime": p.stat().st_mtime,
        })
    return out


def read_page(name: str) -> str | None:
    """Read a wiki page by name (with or without .md). Returns None if missing."""
    fname = _page_filename(name)
    path = _within_root(_root() / fname)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_page(name: str, content: str) -> None:
    """Create or overwrite a wiki page."""
    fname = _page_filename(name)
    path = _within_root(_root() / fname)
    path.write_text(content, encoding="utf-8")


def read_index() -> str:
    path = _root() / INDEX_FILE
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_readme() -> str:
    path = _root() / README_FILE
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# --- Sources ---

def _source_filename(source_id: str) -> str:
    bare = source_id[:-3] if source_id.endswith(".md") else source_id
    _validate_slug(bare)
    return bare + ".md"


def list_sources() -> list[dict]:
    """Return [{id, size_lines, mtime}] for raw sources."""
    sdir = _root() / SOURCES_DIR
    if not sdir.exists():
        return []
    out = []
    for p in sorted(sdir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({
            "id": p.stem,
            "size_lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "mtime": p.stat().st_mtime,
        })
    return out


def read_source(source_id: str) -> str | None:
    fname = _source_filename(source_id)
    path = _within_root(_root() / SOURCES_DIR / fname)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_source(title: str, content: str) -> str:
    """Write a new source. Returns the assigned id (YYYY-MM-DD-<slug>[-N])."""
    today = datetime.now().strftime("%Y-%m-%d")
    base_slug = _slugify(title) or "untitled"
    base = f"{today}-{base_slug}"
    sdir = _root() / SOURCES_DIR
    sdir.mkdir(parents=True, exist_ok=True)

    source_id = base
    n = 2
    while (sdir / f"{source_id}.md").exists():
        source_id = f"{base}-{n}"
        n += 1

    path = _within_root(sdir / f"{source_id}.md")
    path.write_text(content, encoding="utf-8")
    return source_id


def _slugify(text: str) -> str:
    """Filesystem-safe slug derived from free text (mirrors server.slugify)."""
    s = (text or "").lower().strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.ASCII)
    s = re.sub(r"[^\x00-\x7f]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# --- Proposals ---

def _proposal_filename(proposal_id: str) -> str:
    bare = proposal_id[:-3] if proposal_id.endswith(".md") else proposal_id
    _validate_slug(bare)
    return bare + ".md"


def list_proposals() -> list[dict]:
    """Return [{id, mtime}] for pending proposals, oldest first."""
    pdir = _root() / PROPOSALS_DIR
    if not pdir.exists():
        return []
    files = sorted(pdir.glob("*.md"), key=lambda p: p.stat().st_mtime)
    return [{"id": p.stem, "mtime": p.stat().st_mtime} for p in files]


def read_proposal(proposal_id: str) -> str | None:
    fname = _proposal_filename(proposal_id)
    path = _within_root(_root() / PROPOSALS_DIR / fname)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_proposal(content: str, *, kind: str) -> str:
    """Write a new proposal. `kind` is one of 'chat' | 'lint'. Returns id."""
    if kind not in ("chat", "lint"):
        raise ValueError(f"invalid proposal kind: {kind!r}")
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    proposal_id = f"{stamp}-{kind}"
    pdir = _root() / PROPOSALS_DIR
    pdir.mkdir(parents=True, exist_ok=True)

    # Suffix-on-collision (same second).
    final = proposal_id
    n = 2
    while (pdir / f"{final}.md").exists():
        final = f"{proposal_id}-{n}"
        n += 1

    path = _within_root(pdir / f"{final}.md")
    path.write_text(content, encoding="utf-8")
    return final


def delete_proposal(proposal_id: str) -> bool:
    fname = _proposal_filename(proposal_id)
    path = _within_root(_root() / PROPOSALS_DIR / fname)
    if not path.exists():
        return False
    path.unlink()
    return True


# --- Log ---

_LOG_PREFIXES = {"INGEST", "EDIT", "QUERY", "LINT"}


def append_log(prefix: str, text: str) -> None:
    """Append one line to log.md. Rotates if log exceeds LOG_ROTATE_LINES."""
    if prefix not in _LOG_PREFIXES:
        raise ValueError(f"unknown log prefix: {prefix!r}")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / LOG_FILE

    # Rotate first so the new line lands in a fresh file when needed.
    if log_path.exists():
        try:
            current = log_path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current.count("\n") >= LOG_ROTATE_LINES:
            _rotate_log(log_path, current)

    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
    one_line = text.replace("\n", " ").strip()
    line = f"{stamp} {prefix:7s} {one_line}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


def _rotate_log(log_path: Path, current: str) -> None:
    archive_dir = log_path.parent / LOG_ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    month = datetime.now().strftime("%Y-%m")
    target = archive_dir / f"{month}.md"
    # If a prior rotation already wrote this month's file, append rather than overwrite.
    if target.exists():
        with target.open("a", encoding="utf-8") as f:
            f.write(current)
    else:
        target.write_text(current, encoding="utf-8")
    log_path.write_text("", encoding="utf-8")


def read_recent_log(max_lines: int = 200) -> str:
    """Return the tail of log.md, up to max_lines."""
    log_path = _root() / LOG_FILE
    if not log_path.exists():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


# --- Fingerprint for lint skip-if-unchanged ---

def memory_fingerprint() -> str:
    """Stable hash of (path, mtime_ns, size) over pages + log + sources.

    Used by memory_lint.should_run to skip no-op runs. Excludes proposals
    (since lint writes them — would be a feedback loop) and README/INDEX (rare
    edits, but included so lint reruns when the schema changes).
    """
    root = _root()
    if not root.exists():
        return "empty"
    h = hashlib.sha256()
    targets: list[Path] = []
    # Top-level pages incl. README, INDEX, log.
    targets.extend(sorted(root.glob("*.md")))
    # All sources.
    sdir = root / SOURCES_DIR
    if sdir.exists():
        targets.extend(sorted(sdir.glob("*.md")))
    # Rotated logs.
    ldir = root / LOG_ARCHIVE_DIR
    if ldir.exists():
        targets.extend(sorted(ldir.glob("*.md")))
    for p in targets:
        try:
            st = p.stat()
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        h.update(f"{rel}|{st.st_mtime_ns}|{st.st_size}\n".encode("utf-8"))
    return h.hexdigest()
