#!/usr/bin/env python3
"""Tests for chat_tools module."""

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import chat_tools


def make_card(data_dir, board, lst, slug, body=""):
    list_dir = data_dir / "boards" / board / lst
    list_dir.mkdir(parents=True, exist_ok=True)
    (list_dir / f"{slug}.md").write_text(body, encoding="utf-8")
    order_file = list_dir / "_order.json"
    order = json.loads(order_file.read_text()) if order_file.exists() else []
    order.append(slug)
    order_file.write_text(json.dumps(order), encoding="utf-8")


def make_board(data_dir, slug, name="Test"):
    board_dir = data_dir / "boards" / slug
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "_board.md").write_text(
        f"---\nname: {name}\ncolor: '#000000'\n---\n", encoding="utf-8")
    for lst in ("ideas", "backlog", "in-progress", "done"):
        (board_dir / lst).mkdir(exist_ok=True)
        (board_dir / lst / "_order.json").write_text("[]", encoding="utf-8")
    boards_order = data_dir / "_boards-order.json"
    order = json.loads(boards_order.read_text()) if boards_order.exists() else []
    if slug not in order:
        order.append(slug)
        boards_order.write_text(json.dumps(order), encoding="utf-8")


def card_body(title, **kwargs):
    """Build a frontmatter-only card body."""
    lines = [f"title: {title}", "created: 2026-01-01", "updated: 2026-01-01"]
    for k, v in kwargs.items():
        lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(lines) + "\n---\n\n## Description\n\n\n## Checklist\n\n\n## Comments\n\n"


class BucketToolsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        server.DATA_DIR = self.data_dir
        (self.data_dir / "_boards-order.json").write_text("[]", encoding="utf-8")
        make_board(self.data_dir, "alpha")

    def tearDown(self):
        self.tmp.cleanup()


class TestListOverdue(BucketToolsBase):
    def test_overdue_in_progress_appears(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        make_card(self.data_dir, "alpha", "in-progress", "old-task",
                  card_body("Old", due=past))
        out = chat_tools._tool_list_overdue({})
        self.assertEqual(len(out["cards"]), 1)
        self.assertEqual(out["cards"][0]["s"], "old-task")

    def test_overdue_done_excluded(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        make_card(self.data_dir, "alpha", "done", "finished",
                  card_body("Finished", due=past))
        out = chat_tools._tool_list_overdue({})
        self.assertEqual(out["cards"], [])


class TestListDueToday(BucketToolsBase):
    def test_today_appears(self):
        today = date.today().isoformat()
        make_card(self.data_dir, "alpha", "backlog", "today-task",
                  card_body("Today", due=today))
        out = chat_tools._tool_list_due_today({})
        self.assertEqual(len(out["cards"]), 1)

    def test_tomorrow_does_not(self):
        tmrw = (date.today() + timedelta(days=1)).isoformat()
        make_card(self.data_dir, "alpha", "backlog", "tmrw",
                  card_body("Tmrw", due=tmrw))
        out = chat_tools._tool_list_due_today({})
        self.assertEqual(out["cards"], [])


class TestListDueThisWeek(BucketToolsBase):
    def test_today_excluded_from_this_week(self):
        # 'this_week' bucket explicitly excludes today (today is its own bucket).
        today = date.today().isoformat()
        make_card(self.data_dir, "alpha", "backlog", "today-task",
                  card_body("Today", due=today))
        out = chat_tools._tool_list_due_this_week({})
        self.assertEqual(out["cards"], [])

    def test_within_week_appears_when_room(self):
        # Pick a date that's strictly between today and Sunday-of-this-week.
        today = date.today()
        days_left_in_week = 6 - today.weekday()
        if days_left_in_week == 0:
            self.skipTest("today is Sunday; no room in this_week")
        target = (today + timedelta(days=1)).isoformat()
        make_card(self.data_dir, "alpha", "backlog", "soon",
                  card_body("Soon", due=target))
        out = chat_tools._tool_list_due_this_week({})
        self.assertEqual(len(out["cards"]), 1)


class TestFindByLabel(BucketToolsBase):
    def test_exact_match_case_insensitive(self):
        make_card(self.data_dir, "alpha", "backlog", "u",
                  card_body("U", labels="[urgent]"))
        out = chat_tools._tool_find_by_label({"label": "URGENT"})
        self.assertEqual(len(out["cards"]), 1)

    def test_partial_does_not_match(self):
        make_card(self.data_dir, "alpha", "backlog", "u",
                  card_body("U", labels="[urgent]"))
        out = chat_tools._tool_find_by_label({"label": "urge"})
        self.assertEqual(out["cards"], [])

    def test_done_excluded(self):
        make_card(self.data_dir, "alpha", "done", "u",
                  card_body("U", labels="[urgent]"))
        out = chat_tools._tool_find_by_label({"label": "urgent"})
        self.assertEqual(out["cards"], [])


class TestFindByAssignee(BucketToolsBase):
    def test_exact_match_case_insensitive(self):
        make_card(self.data_dir, "alpha", "backlog", "m",
                  card_body("M", assignee="Maria"))
        out = chat_tools._tool_find_by_assignee({"name": "maria"})
        self.assertEqual(len(out["cards"]), 1)

    def test_no_match(self):
        make_card(self.data_dir, "alpha", "backlog", "m",
                  card_body("M", assignee="Maria"))
        out = chat_tools._tool_find_by_assignee({"name": "Bob"})
        self.assertEqual(out["cards"], [])

    def test_done_excluded(self):
        make_card(self.data_dir, "alpha", "done", "m",
                  card_body("M", assignee="Maria"))
        out = chat_tools._tool_find_by_assignee({"name": "maria"})
        self.assertEqual(out["cards"], [])


if __name__ == "__main__":
    unittest.main()
