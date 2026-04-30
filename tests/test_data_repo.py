#!/usr/bin/env python3
"""Tests for data_repo.reconcile_repo_state."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import data_repo
import sync_config


def _run_git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        data_repo.set_data_dir(self.data_dir)
        sync_config.CONFIG_PATH = self.root / ".sync-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_off_mode_creates_plain_dir(self):
        result = data_repo.reconcile_repo_state({"mode": "off", "remote_url": "", "branch": "main"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(self.data_dir.exists())
        self.assertFalse((self.data_dir / ".git").exists())

    def test_local_mode_runs_git_init(self):
        result = data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue((self.data_dir / ".git").exists())

    def test_local_mode_idempotent(self):
        data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        result = data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        self.assertEqual(result["status"], "ok")

    def test_local_mode_does_not_touch_existing_remote(self):
        self.data_dir.mkdir()
        _run_git(["init", "-b", "main"], self.data_dir)
        _run_git(["remote", "add", "origin", "https://preserved/x.git"], self.data_dir)
        data_repo.reconcile_repo_state({"mode": "local", "remote_url": "", "branch": "main"})
        r = _run_git(["remote", "get-url", "origin"], self.data_dir)
        self.assertEqual(r.stdout.strip(), "https://preserved/x.git")

    def test_remote_mode_inits_and_adds_origin(self):
        result = data_repo.reconcile_repo_state(
            {"mode": "remote", "remote_url": "https://x/y.git", "branch": "main"}
        )
        self.assertEqual(result["status"], "ok")
        r = _run_git(["remote", "get-url", "origin"], self.data_dir)
        self.assertEqual(r.stdout.strip(), "https://x/y.git")

    def test_remote_mode_updates_existing_origin(self):
        self.data_dir.mkdir()
        _run_git(["init", "-b", "main"], self.data_dir)
        _run_git(["remote", "add", "origin", "https://old/x.git"], self.data_dir)
        result = data_repo.reconcile_repo_state(
            {"mode": "remote", "remote_url": "https://new/y.git", "branch": "main"}
        )
        self.assertEqual(result["status"], "ok")
        r = _run_git(["remote", "get-url", "origin"], self.data_dir)
        self.assertEqual(r.stdout.strip(), "https://new/y.git")

    def test_remote_mode_does_not_clone_or_pull(self):
        # network would be required for clone — make sure we don't try it.
        result = data_repo.reconcile_repo_state(
            {"mode": "remote", "remote_url": "https://nonexistent.invalid/x.git", "branch": "main"}
        )
        self.assertEqual(result["status"], "ok")  # success: only ran init + remote add


class TestGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        data_repo.set_data_dir(self.data_dir)
        sync_config.CONFIG_PATH = self.root / ".sync-config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_off_mode(self):
        sync_config.save({"mode": "off", "remote_url": "", "branch": "main"})
        result = data_repo.git_test()
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "sync disabled")

    def test_local_mode_no_repo(self):
        sync_config.save({"mode": "local", "remote_url": "", "branch": "main"})
        result = data_repo.git_test()
        self.assertFalse(result["ok"])

    def test_local_mode_with_repo(self):
        sync_config.save({"mode": "local", "remote_url": "", "branch": "main"})
        self.data_dir.mkdir()
        _run_git(["init"], self.data_dir)
        result = data_repo.git_test()
        self.assertTrue(result["ok"])

    def test_remote_mode_unreachable_url(self):
        sync_config.save(
            {"mode": "remote", "remote_url": "https://nonexistent.invalid/x.git", "branch": "main"}
        )
        result = data_repo.git_test()
        self.assertFalse(result["ok"])
        self.assertTrue(result["message"])

    def test_remote_mode_with_local_bare_remote(self):
        bare = self.root / "bare.git"
        _run_git(["init", "--bare", str(bare)], self.root)
        sync_config.save({"mode": "remote", "remote_url": str(bare), "branch": "main"})
        result = data_repo.git_test()
        self.assertTrue(result["ok"], msg=result["message"])


class TestGitStatusSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        data_repo.set_data_dir(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_repo(self):
        self.assertEqual(data_repo.git_status_summary(), "no-repo")

    def test_repo_no_remote(self):
        self.data_dir.mkdir()
        _run_git(["init"], self.data_dir)
        self.assertEqual(data_repo.git_status_summary(), "missing-remote")

    def test_repo_with_remote(self):
        self.data_dir.mkdir()
        _run_git(["init"], self.data_dir)
        _run_git(["remote", "add", "origin", "https://x/y.git"], self.data_dir)
        self.assertEqual(data_repo.git_status_summary(), "ok")


if __name__ == "__main__":
    unittest.main()
