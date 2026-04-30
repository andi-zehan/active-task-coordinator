# Configurable Data Sync — Design

**Date:** 2026-04-30
**Status:** Approved, ready for implementation plan

## Problem

`data/` (boards, cards) is currently always treated as a git repository that
auto-pushes to a hard-coded GitHub remote. The user wants three modes:

- **off** — no git activity at all.
- **local** — commit changes locally, never push.
- **remote** — commit and push (today's behavior).

The mode, the remote URL, and the branch must be configurable from the existing
settings modal. Auth is delegated to the user's system git config.

## Modes

The mode controls every git interaction the app performs — both manual pushes
(the header "Sync" button) and the automatic pull on startup.

| Mode     | Startup pull | Sync button | Repo state ensured |
|----------|--------------|-------------|--------------------|
| `off`    | skipped      | greyed out  | none — `data/` is a plain directory |
| `local`  | skipped      | commit only | `git init` if missing |
| `remote` | runs (clone if empty, pull otherwise) | commit + push | `git init` + `origin` set to URL |

If a sync error occurs, the app surfaces the error in the UI but keeps working
locally. There is no auto-degrade between modes.

## Configuration

A new module `sync_config.py`, modeled on `llm_config.py`, manages
`./.sync-config.json` (gitignored).

### Schema

```json
{
  "mode": "remote",
  "remote_url": "https://github.com/.../atc-content.git",
  "branch": "main",
  "skip_next_pull": false
}
```

- `mode` — one of `"off"`, `"local"`, `"remote"`.
- `remote_url` — free-form git URL. Whatever `git remote add` accepts works
  (HTTPS, SSH, file://, ...). Required when `mode == "remote"`.
- `branch` — branch to push/pull. Defaults to `"main"`.
- `skip_next_pull` — internal flag. Set by the `PUT /api/sync/config` handler
  when a config change makes an automatic pull risky (see *Mode transition
  safety*). Not settable directly by the client — `save()` filters this key
  out of any user-supplied updates dict. Cleared on consumption at startup.

### Public API

- `load() -> dict` — reads file, falls back to defaults for missing keys.
- `save(updates: dict) -> dict` — partial update, validated, writes to disk.
- `public_view() -> dict` — same shape; nothing is secret (no tokens stored).
- Validation in `save()`:
  - `mode` must be one of the three values.
  - If `mode == "remote"`, `remote_url` must be a non-empty string.
  - `branch` must be a non-empty string.

### First-run migration

If `.sync-config.json` doesn't exist, defaults are derived from the existing
state of `data/`:

| Existing state | Defaults |
|---|---|
| `data/.git` exists with an `origin` remote | `mode="remote"`, `remote_url=<origin URL>`, `branch=<current branch or "main">` |
| `data/.git` exists, no remote | `mode="local"`, `remote_url=""` |
| `data/` doesn't exist or isn't a repo | `mode="remote"`, `remote_url=ATC_DATA_REPO_URL env var or hardcoded default` |

The first run writes these defaults to disk, so subsequent runs are stable. The
`ATC_DATA_REPO_URL` env var is consulted only for this one-time bootstrap.

## Repo state reconciliation

A new function `reconcile_repo_state(cfg) -> {status, message}` lives in a new
module `data_repo.py`. It is called at startup and after every successful
`PUT /api/sync/config`. It never makes network calls.

| Mode     | Action |
|----------|--------|
| `off`    | If `data/` doesn't exist, create it as a plain directory. Never run any git command. |
| `local`  | Ensure `data/` exists. If it's not a git repo, run `git init` (initial branch = `cfg.branch`). Don't touch any remote. |
| `remote` | Ensure `data/` exists and is a git repo (init if needed). Then ensure `origin` matches `cfg.remote_url`: `git remote add origin <url>` if missing, `git remote set-url origin <url>` if present and different. Do not auto-clone. |

Cloning only happens at startup when `data/` is genuinely absent or empty
(see *Startup behavior*). Reconcile-on-config-change must be safe to invoke
even when `data/` already contains valuable work.

## Push and pull, mode-aware

Both functions live in `data_repo.py` and read `sync_config.load()` first.

### `git_sync_push()`

| Mode | Behavior |
|---|---|
| off | Return `{"status": "skipped", "message": "sync disabled"}`. No git invocation. |
| local | `git add -A` + `git commit -m "<auto msg>"`. No push. Returns `"ok"` with the commit message, or `"no-changes"` if nothing was staged. |
| remote | Today's behavior: add + commit + push to `cfg.branch`. |

The auto-commit message is `sync from <hostname> at <YYYY-MM-DD HH:MM>` for all
modes that commit.

### `git_sync_pull()`

Called from two places: at startup (automatic, subject to `skip_next_pull`),
and from the "Pull from remote" button in the settings modal (manual,
unconditional).

| Mode | Behavior |
|---|---|
| off | Skip entirely. |
| local | Skip — no remote to pull from. |
| remote | If `data/` doesn't exist or is an empty placeholder, clone `cfg.remote_url` into `data/`. Otherwise `git pull origin <cfg.branch>`. |

### `/api/sync/status` response

Returns `{"dirty": <bool>, "mode": "<mode>"}` so the UI can render the header
button's mode-dependent label and disabled state without polling git when sync
is off.

## Startup behavior

In `server.py`, the existing startup block is modified to:

1. Load `sync_config`. On first run, the migration logic produces defaults and
   writes them.
2. Call `reconcile_repo_state(cfg)`. Surface errors to the console; do not
   crash the server.
3. If `cfg.mode == "remote"` and `cfg.skip_next_pull` is false, call
   `git_sync_pull()`. Errors are logged; server still starts.
4. If `cfg.skip_next_pull` is true, skip the pull, log
   `sync: auto-pull skipped after mode change — push your local commits first, then pull manually if desired`,
   and clear the flag (saving back to disk).
5. The janitor (separate concern) still runs on startup and every 24h
   regardless of mode.

With `mode == "off"`, server startup makes zero git invocations. With
`mode == "local"`, it runs at most one idempotent `git init`.

## Mode transition safety

When the user flips remote → local → remote (and possibly makes commits in
local mode), an automatic pull at the next startup could merge stale origin
commits into newer local work. We protect against this with a one-shot
suppression flag.

`PUT /api/sync/config` sets `skip_next_pull = true` when:

- Mode transitions from `local` → `remote`.
- Mode transitions from `off` → `remote` *and* `data/` is non-empty / already
  a repo.
- `remote_url` changes while mode is already `remote`.

The flag is consumed and cleared at the next startup (see step 4 above).

The user's recovery flow after re-enabling remote:

1. Restart (or just reload) — auto-pull is skipped.
2. Click "Sync" in the header — local commits are pushed to the remote.
3. To pull anything new from the remote afterward, click "Pull from remote" in
   the settings modal (explicit, user-initiated).
4. Subsequent restarts behave normally.

We do not attempt to auto-detect divergence, auto-stash, or auto-rebase. Git's
own merge semantics handle conflicts; we just stay out of the way.

## HTTP API

Three new endpoints, modeled on the LLM-config pattern:

### `GET /api/sync/config`

Returns:
```json
{
  "mode": "remote",
  "remote_url": "https://...",
  "branch": "main",
  "git_status": "ok" | "no-repo" | "missing-remote"
}
```

`git_status` is computed at request time (cheap: checks `data/.git` and
`git remote -v`). The UI uses it to render a status line without a separate
test endpoint.

### `PUT /api/sync/config`

Body: `{mode, remote_url, branch}`. Validates → calls `sync_config.save()` →
calls `reconcile_repo_state(new_cfg)` → returns the new public view plus the
reconcile result. Returns 400 with a human-readable message on validation
failure.

### `POST /api/sync/test`

No body. Mode-dependent quick check, no side effects:

| Mode   | Check |
|--------|-------|
| off    | `{"ok": true, "message": "sync disabled"}` |
| local  | `{"ok": <data/.git exists>, "message": "..."}` |
| remote | `git ls-remote <url>` to verify URL reachable and auth works. Returns `{"ok": <bool>, "message": "<git output>"}`. |

### Existing endpoints

`POST /api/sync/push`, `POST /api/sync/pull`, and `GET /api/sync/status` stay.
Their handlers now dispatch on mode (delegate to `data_repo`).

## UI

### Settings modal

Add a new "Data sync" section below the existing LLM section.

- **Mode** — radio group: `Off (no git)` / `Local (commit only)` /
  `Remote (commit + push)`. Selecting "Remote" reveals the URL/branch fields
  and a privacy warning banner; selecting another mode hides them.
- **Remote URL** — text input, shown only when mode == remote. Placeholder is
  the current value or `https://github.com/you/atc-content.git`.
- **Branch** — text input, defaults to `main`.
- **Privacy warning** (red/amber banner, only when mode == remote):
  *"Your cards may contain personal or work-sensitive notes. Make sure the
  remote repository is **private**. ATC will not check this for you."*
- **Repo status line** — small grey text reading from the `git_status` field:
  e.g. `Status: repository OK · remote configured` or
  `Status: not initialized — saving will run \`git init\``.
- **Pull from remote** button — only shown when mode == remote. Calls
  `POST /api/sync/pull`.
- **Save** button — `PUT /api/sync/config`. Toast shows reconcile result.
- **Test** button — `POST /api/sync/test`. Shows ✓/✗ inline.

### Header "Sync" button

Reads mode from `/api/sync/status`:

| Mode   | Idle label    | Click action          | After-success label |
|--------|---------------|-----------------------|---------------------|
| off    | `Sync off`    | disabled              | n/a                 |
| local  | `↻ Commit`    | POST `/api/sync/push` | `✓ Committed`       |
| remote | `↻ Sync`      | POST `/api/sync/push` | `✓ Synced`          |

Dirty indicator stays as today (`↻ Sync needed` / `↻ Commit needed`, depending
on mode).

The notes-wizard's "Sync now" button (`index.html:1286`) gets the same
mode-aware treatment: hidden in off, "Commit" in local, "Sync" in remote.

## Error handling

- `reconcile_repo_state` returns `{status, message}`. Startup logs failures
  to console; settings UI shows them inline next to "Save".
- `git_sync_push` / `git_sync_pull` return
  `{status: "ok"|"no-changes"|"skipped"|"error", message}`. UI renders these.
- `PUT /api/sync/config` validation errors return 400 with a human-readable
  message.
- `git ls-remote` failures show stderr verbatim — auth issues are obvious
  from git's own output.
- All `subprocess.run` calls keep `capture_output=True, text=True` and a 30 s
  timeout so the UI never hangs.

## Testing

- `tests/test_sync_config.py` (unit, new): defaults, save/load roundtrip,
  `public_view`, validation rejects bad input, `skip_next_pull` is set on the
  right transitions and cleared after consumption. Uses a tmp config path via
  monkeypatching.
- `tests/test_data_repo.py` (integration-ish, new): `reconcile_repo_state` for
  each mode against a tmp `data_dir`. Local mode runs `git init`. Remote mode
  adds/updates `origin`. Off mode is a no-op. Uses a local bare repo as the
  "remote" URL so tests don't touch the network.
- No browser/UI tests — manual verification via the running server.

## File layout

| File | Purpose |
|---|---|
| `sync_config.py` | new — load/save/validate/migrate `.sync-config.json`, `public_view()` |
| `data_repo.py` | new — `reconcile_repo_state`, `git_sync_push`, `git_sync_pull`, `_has_git_remote`, `_is_empty_placeholder_data_dir`, `git_url_from_origin`. Moves existing helpers out of `server.py`. |
| `server.py` | shrinks — removes the git helpers, gains three handlers (`_handle_get_sync_config`, `_handle_put_sync_config`, `_handle_test_sync`) and updates the existing three sync handlers to delegate to `data_repo`. |
| `index.html` | settings modal gets the "Data sync" section; header sync button reads mode; new Pull button in modal. |
| `.gitignore` | add `.sync-config.json` |

## Out of scope

- Token management in the UI (auth is the user's responsibility via system git
  config).
- Auto-detecting privacy of the remote repository (warning text only).
- Auto-stash / auto-rebase / divergence detection (delegated to git).
- Multiple remotes or non-`origin` remotes.
- A web-based git history browser or diff viewer.
