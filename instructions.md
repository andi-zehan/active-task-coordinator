# Kanban Board — Agent Instructions

This project is a personal kanban board. All data lives as markdown files under `data/`. You can create, update, move, and delete cards and boards by editing these files directly. The web UI reads from the same files.

## Folder Structure

```
data/
  _boards-order.json              # ["board-slug-1", "board-slug-2"]
  boards/
    <board-slug>/
      _board.md                   # Board metadata
      ideas/_order.json           # Card display order for this list
      ideas/<card-slug>.md        # One file per card
      backlog/_order.json
      backlog/<card-slug>.md
      in-progress/_order.json
      in-progress/<card-slug>.md
      done/_order.json
      done/<card-slug>.md
```

Every board has exactly four lists: `ideas`, `backlog`, `in-progress`, `done`.

## Slugs

Slugs are kebab-case identifiers derived from names: lowercase, strip special characters, replace spaces with hyphens. Examples:

- "My Project" → `my-project`
- "Fix bug #123" → `fix-bug-123`

Slugs are used as folder names (boards) and filenames (cards).

## Board Metadata (`_board.md`)

```markdown
---
name: Project Alpha
description: Main product development
color: "#4A90D9"
---
```

- `name`: Display name
- `description`: Optional description
- `color`: Hex color for UI accent

## Card File Format (`<card-slug>.md`)

```markdown
---
title: Implement login page
assignee: Alice
labels: [frontend, urgent]
due: 2026-05-01
created: 2026-04-23
updated: 2026-04-23
relations: [project-beta/backlog/api-auth]
custom_fields:
  priority: high
  effort: M
attachments:
  - name: Design mockup
    url: https://figma.com/file/abc123
---

## Description

Build the login page based on the approved design.

## Checklist

- [x] Create HTML structure
- [ ] Add form validation
- [ ] Connect to auth API

## Comments

**2026-04-23 - Me:**
Talked to backend team, API will be ready by Friday.
```

### Frontmatter Fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string | yes | Card display name |
| `assignee` | string | no | Team member name |
| `labels` | list | no | `[label1, label2]` inline format |
| `due` | date | no | `YYYY-MM-DD` format |
| `created` | date | yes | Set on creation, never change |
| `updated` | date | yes | Set to today on every edit |
| `relations` | list | no | Paths to related cards: `board-slug/list-slug/card-slug` |
| `custom_fields` | map | no | Indented key-value pairs |
| `attachments` | list | no | Each item has `name` and `url` |

### Body Sections

The body has three sections in fixed order, separated by `## ` headers:

1. **`## Description`** — Free-text description
2. **`## Checklist`** — Standard markdown checkboxes (`- [ ]` / `- [x]`)
3. **`## Comments`** — Each comment formatted as `**YYYY-MM-DD - Author:**` followed by text

Sections must always be present even if empty. Keep two blank lines between sections.

## Ordering Files (`_order.json`)

Each list folder has an `_order.json` containing an array of card slugs:

```json
["card-slug-1", "card-slug-2", "card-slug-3"]
```

The root `data/_boards-order.json` contains board slugs in display order:

```json
["project-alpha", "project-beta"]
```

## Common Operations

### Create a board

1. Create folder `data/boards/<slug>/`
2. Create `_board.md` with frontmatter
3. Create four subfolders: `ideas/`, `backlog/`, `in-progress/`, `done/`
4. Create `_order.json` in each subfolder with `[]`
5. Append the slug to `data/_boards-order.json`

### Create a card

1. Generate slug from title
2. Write `<slug>.md` in the target list folder (e.g., `data/boards/my-project/backlog/`)
3. Set `created` and `updated` to today
4. Append the slug to that list's `_order.json`

### Move a card between lists

1. Move the `.md` file from source list folder to target list folder
2. Remove the slug from source `_order.json`
3. Add the slug to target `_order.json` at the desired position

### Update a card

1. Edit the `.md` file (frontmatter or body)
2. Set `updated` to today

### Delete a card

1. Delete the `.md` file
2. Remove the slug from the list's `_order.json`

### Add a comment

Append to the `## Comments` section:

```markdown
**2026-04-23 - Me:**
The comment text goes here.
```

### Toggle a checklist item

Change `- [ ]` to `- [x]` or vice versa in the `## Checklist` section.

### Add a relation

Relations are bidirectional. When linking card A to card B:

1. Add `board-b/list-b/card-b` to card A's `relations` list
2. Add `board-a/list-a/card-a` to card B's `relations` list

## Example: Quick Task Creation

To add a task "Review PR #42" assigned to Bob, due Friday, to the backlog of project "website":

```markdown
---
title: Review PR #42
assignee: Bob
labels: [review]
due: 2026-04-25
created: 2026-04-23
updated: 2026-04-23
relations: []
custom_fields: {}
attachments: []
---

## Description

Review and approve PR #42 for the new header component.

## Checklist

- [ ] Check code quality
- [ ] Run tests locally
- [ ] Approve or request changes

## Comments

```

Save as `data/boards/website/backlog/review-pr-42.md` and add `"review-pr-42"` to `data/boards/website/backlog/_order.json`.
