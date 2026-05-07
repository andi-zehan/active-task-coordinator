#!/usr/bin/env python3
"""One-shot CLI: assign global IDs (C-N) to existing cards.

Usage:
    python migrate.py

Refuses to run if `data/` has uncommitted git changes — commit or stash first.
After it runs, review the diff and commit the renamed files yourself.
"""
import json
import subprocess
import sys

import server
import migration


def main() -> int:
    data_dir = server.DATA_DIR
    if not data_dir.exists():
        print(f"error: {data_dir} does not exist", file=sys.stderr)
        return 1

    if (data_dir / ".git").exists():
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=data_dir, timeout=10,
            )
            if r.stdout.strip():
                print(
                    f"error: {data_dir} has uncommitted changes — "
                    "commit or stash first",
                    file=sys.stderr,
                )
                return 2
        except Exception as e:
            print(f"warning: could not check git status: {e}", file=sys.stderr)

    result = migration.assign_ids()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
