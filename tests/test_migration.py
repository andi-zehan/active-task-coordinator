"""Tests for the assign-ids migration."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server
import migration


def _write_legacy_card(board, list_slug, slug, *, title="t", relations=None):
    """Create a card the OLD way: filename = slug, no id in frontmatter."""
    card_dir = server.DATA_DIR / "boards" / board / list_slug
    card_dir.mkdir(parents=True, exist_ok=True)
    rels = relations if relations is not None else []
    rels_str = "[" + ", ".join(rels) + "]"
    frontmatter = (
        "---\n"
        f"title: {title}\n"
        "labels: []\n"
        "due: ''\n"
        "assignee: ''\n"
        "created: '2026-05-07'\n"
        "updated: '2026-05-07'\n"
        f"relations: {rels_str}\n"
        "custom_fields:\n"
        "attachments: []\n"
        "---\n\n## Description\n\n\n## Checklist\n\n\n## Comments\n\n"
    )
    (card_dir / f"{slug}.md").write_text(frontmatter, encoding="utf-8")
    order_path = card_dir / "_order.json"
    order = json.loads(order_path.read_text(encoding="utf-8")) if order_path.exists() else []
    if slug not in order:
        order.append(slug)
    order_path.write_text(json.dumps(order, indent=2), encoding="utf-8")


class TestAssignIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(self.tmp) / "data"
        server.ensure_data_dir()
        server.reset_id_index()
        server.write_board_meta("b1", {"name": "B1", "color": "#000"})
        server.write_board_meta("b2", {"name": "B2", "color": "#000"})

    def tearDown(self):
        server.DATA_DIR = self.orig_data_dir
        shutil.rmtree(self.tmp)

    def test_assigns_sequential_ids(self):
        _write_legacy_card("b1", "backlog", "alpha")
        _write_legacy_card("b1", "backlog", "beta")
        _write_legacy_card("b2", "backlog", "gamma")
        result = migration.assign_ids()
        self.assertEqual(result["cards_migrated"], 3)
        # Files renamed to C-N.md
        files = sorted((server.DATA_DIR / "boards" / "b1" / "backlog").glob("C-*.md"))
        self.assertEqual([f.name for f in files], ["C-1.md", "C-2.md"])

    def test_writes_id_to_frontmatter(self):
        _write_legacy_card("b1", "backlog", "alpha")
        migration.assign_ids()
        card = server.read_card("b1", "backlog", "C-1")
        self.assertEqual(card["id"], "C-1")
        self.assertEqual(card["title"], "t")

    def test_updates_order_json(self):
        _write_legacy_card("b1", "backlog", "alpha")
        _write_legacy_card("b1", "backlog", "beta")
        migration.assign_ids()
        order = json.loads((server.DATA_DIR / "boards" / "b1" / "backlog" / "_order.json").read_text())
        self.assertEqual(order, ["C-1", "C-2"])

    def test_converts_path_relations_to_ids(self):
        _write_legacy_card("b1", "backlog", "alpha")
        _write_legacy_card("b2", "backlog", "gamma", relations=["b1/backlog/alpha"])
        result = migration.assign_ids()
        # alpha gets C-1, gamma gets C-2
        gamma = server.read_card("b2", "backlog", "C-2")
        self.assertEqual(gamma["relations"], ["C-1"])
        self.assertEqual(result["relations_converted"], 1)
        self.assertEqual(result["relations_unresolved"], [])

    def test_unresolved_relation_left_in_place(self):
        _write_legacy_card("b1", "backlog", "alpha", relations=["b9/nope/missing"])
        result = migration.assign_ids()
        card = server.read_card("b1", "backlog", "C-1")
        self.assertEqual(card["relations"], ["b9/nope/missing"])
        self.assertEqual(result["relations_unresolved"],
                         [{"card": "C-1", "stale": "b9/nope/missing"}])

    def test_idempotent_on_already_migrated(self):
        _write_legacy_card("b1", "backlog", "alpha")
        migration.assign_ids()
        result2 = migration.assign_ids()
        self.assertEqual(result2["cards_migrated"], 0)


class TestFoldIdeasIntoBacklog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data_dir = server.DATA_DIR
        server.DATA_DIR = Path(self.tmp) / "data"
        server.ensure_data_dir()
        server.reset_id_index()
        server.write_board_meta("b1", {"name": "B1", "color": "#000"})

    def tearDown(self):
        server.DATA_DIR = self.orig_data_dir
        shutil.rmtree(self.tmp)

    def _order(self, board, list_slug):
        p = server.DATA_DIR / "boards" / board / list_slug / "_order.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def test_moves_ideas_to_end_of_backlog_and_removes_folder(self):
        _write_legacy_card("b1", "backlog", "C-1")
        _write_legacy_card("b1", "ideas", "C-3")
        _write_legacy_card("b1", "ideas", "C-2")
        result = migration.fold_ideas_into_backlog()
        self.assertEqual(result["cards_moved"], 2)
        self.assertEqual(self._order("b1", "backlog"), ["C-1", "C-3", "C-2"])
        self.assertTrue((server.DATA_DIR / "boards" / "b1" / "backlog" / "C-3.md").exists())
        self.assertFalse((server.DATA_DIR / "boards" / "b1" / "ideas").exists())

    def test_card_missing_from_order_is_still_moved(self):
        _write_legacy_card("b1", "ideas", "C-5")
        (server.DATA_DIR / "boards" / "b1" / "ideas" / "_order.json").write_text("[]", encoding="utf-8")
        migration.fold_ideas_into_backlog()
        self.assertEqual(self._order("b1", "backlog"), ["C-5"])

    def test_empty_ideas_folder_is_removed(self):
        ideas = server.DATA_DIR / "boards" / "b1" / "ideas"
        ideas.mkdir()
        (ideas / "_order.json").write_text("[]", encoding="utf-8")
        result = migration.fold_ideas_into_backlog()
        self.assertEqual(result["cards_moved"], 0)
        self.assertFalse(ideas.exists())

    def test_idempotent(self):
        _write_legacy_card("b1", "ideas", "C-1")
        migration.fold_ideas_into_backlog()
        result = migration.fold_ideas_into_backlog()
        self.assertEqual(result["cards_moved"], 0)
        self.assertEqual(self._order("b1", "backlog"), ["C-1"])
