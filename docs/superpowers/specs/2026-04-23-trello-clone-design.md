# Personal Trello Clone — Design Spec

## Overview

A personal, local-only kanban board application for project management and daily task tracking. Single-user, no authentication. Tracks work across multiple projects and team members, but only the owner views and updates the data.

Data is stored as markdown files so it can be manipulated directly by coding agents (e.g., Claude Code).

## Data Layer

### Folder Structure

```
data/
├── boards/
│   ├── project-alpha/
│   │   ├── _board.md              # Board metadata (name, description, accent color)
│   │   ├── ideas/
│   │   │   ├── _order.json        # ["card-slug-1", "card-slug-2"]
│   │   │   ├── card-slug-1.md
│   │   │   └── card-slug-2.md
│   │   ├── backlog/
│   │   │   ├── _order.json
│   │   │   └── ...
│   │   ├── in-progress/
│   │   │   ├── _order.json
│   │   │   └── ...
│   │   └── done/
│   │       ├── _order.json
│   │       └── ...
│   └── project-beta/
│       └── ...
└── _boards-order.json             # Board display order
```

### Fixed Columns

Every board uses the same four columns:

1. Ideas
2. Backlog
3. In Progress
4. Done

### Card File Format

Each card is a `.md` file with YAML frontmatter:

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

- **Card slug** = filename without `.md`, derived from the title on creation (kebab-case).
- **Relations** use relative paths: `board-slug/list-slug/card-slug`. Bidirectional — if card A lists card B in its relations, card B should list card A.
- **Checklists** use standard markdown checkboxes.
- **Comments** are inline in the card body, formatted as bold date/author lines.
- **Attachments** are links only — no file storage managed by the app.

### Ordering

- `_order.json` in each list folder contains an array of card slugs defining display order.
- `_boards-order.json` at the data root contains an array of board slugs defining board display order.
- When a card is created, its slug is appended to the relevant `_order.json`.
- When a card is moved between lists, it is removed from the source `_order.json` and inserted into the target `_order.json` at the specified position.

### Board Metadata

`_board.md` contains:

```markdown
---
name: Project Alpha
description: Main product development
color: "#4A90D9"
---
```

## Local Server

A single Python script (`server.py`) using only the standard library (`http.server`, `json`, `os`, `pathlib`). No external dependencies.

### Responsibilities

- Serve the static frontend files (HTML/CSS/JS)
- Expose a REST API for reading and writing the markdown data

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/boards` | List all boards (reads `_boards-order.json` and each `_board.md`) |
| `POST` | `/api/boards` | Create a new board |
| `GET` | `/api/boards/:board` | Get board details and all its cards |
| `PUT` | `/api/boards/:board` | Update board metadata |
| `DELETE` | `/api/boards/:board` | Delete a board and all its cards. Stale relations in other cards are left as-is (they render as "not found" in the UI). |
| `GET` | `/api/boards/:board/lists/:list/cards` | List cards in a specific list |
| `POST` | `/api/boards/:board/lists/:list/cards` | Create a new card |
| `GET` | `/api/cards/:board/:list/:card` | Read a single card |
| `PUT` | `/api/cards/:board/:list/:card` | Update a card |
| `DELETE` | `/api/cards/:board/:list/:card` | Delete a card. Removes it from `_order.json`. Stale relations in other cards are left as-is. |
| `PUT` | `/api/cards/:board/:list/:card/move` | Move a card to another list/position |
| `GET` | `/api/dashboard` | Cards due today and this week, across all boards |
| `GET` | `/api/calendar/:year/:month` | Cards with due dates in a given month |
| `GET` | `/api/search?q=...` | Search cards by title, description, assignee, labels |

### Launch

```bash
python server.py
```

Runs on `http://localhost:8080`. Serves from the project root, reads/writes the `data/` folder.

## Frontend

### Architecture

A single `index.html` file with embedded CSS and JS. No build step, no framework, no dependencies. Vanilla HTML/CSS/JS only.

### Navigation

- **Top bar:** App name, board selector dropdown, view switcher (Board / Dashboard / Calendar / Table)
- Keyboard shortcut hints shown on hover

### Views

#### Kanban Board

- Four columns: Ideas / Backlog / In Progress / Done
- Card preview shows: title (bold), colored label dots, assignee name, due date (red if overdue), checklist progress bar
- Drag-and-drop within and between columns using native HTML5 Drag and Drop API
- Click a card to open the detail modal

#### Dashboard

- Two sections: "Today" and "This Week"
- Shows cards due in each window, grouped by board name
- Overdue cards highlighted in red
- Click a card to open the detail modal

#### Calendar

- Monthly grid layout
- Cards displayed on their due date cell
- Click a date cell to see all cards due that day
- Month navigation (previous / next)

#### Table

- One row per card across all boards (or filtered to current board)
- Columns: title, board, list, assignee, due date, labels
- Sortable by clicking column headers
- Filterable by board, assignee, label

### Card Detail Modal

Opens when a card is clicked in any view. Displays and allows editing of all fields:

- Title (inline editable)
- Description (toggle between rendered markdown and textarea for editing)
- Checklist (clickable checkboxes, add/remove items)
- Labels (add/remove from a palette)
- Assignee (dropdown or text input)
- Due date (date picker)
- Custom fields (key-value pairs, add/remove)
- Attachments (list of links, add/remove)
- Relations (list of linked cards, add/remove by search)
- Comments (rendered list, add new comment form at bottom)

### Search & Filtering

**Global search** (activated with `/` shortcut):
- Searches card title, description, assignee, and labels across all boards
- Results appear as a dropdown list
- Click a result to open the card detail modal
- In-memory filtering — no indexing needed given the small dataset

**Filters** (available in Board and Table views):
- By assignee (dropdown of known assignees)
- By label (multi-select)
- By due date (overdue / today / this week / no date)
- Filters combine with AND logic
- Active filters shown as removable chips above content

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `N` | Create new card in current board |
| `1` | Switch to Board view |
| `2` | Switch to Dashboard view |
| `3` | Switch to Calendar view |
| `4` | Switch to Table view |
| `/` | Focus search |
| `Esc` | Close modal / clear search |

## Visual Design

- Clean, minimal, light theme
- White cards on light gray column backgrounds
- Label colors as small colored dots/bars on card previews
- Each board has a subtle accent color (defined in `_board.md`)
- Card hover: subtle shadow lift
- Card dragging: slight opacity, drop zone highlighted
- Overdue dates rendered in red
- Desktop-focused, minimum viable width ~1200px
- No dark mode, no custom themes, no background images

## Out of Scope

- Multi-user / collaboration / real-time sync
- Authentication / login
- Automation rules (Butler-style)
- File storage for attachments
- Timeline / Gantt view
- Dark mode / custom themes
- Cloud hosting
- Mobile responsive layout
