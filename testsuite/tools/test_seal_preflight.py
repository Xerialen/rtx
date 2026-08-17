#!/usr/bin/env python3
"""U5b seal-preflight. Tmp repo + facit with kalla block. No rig, no token."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import seal_preflight as sp

KALLA = {
    "schema": "facit-kalla/1",
    "expected_source": "derived",
    "never_from_judged_run": True,
    "derived_from": ["fixture"],
}


def _git(repo: Path, *args: str, env=None) -> str:
    e = os.environ.copy()
    e.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
    )
    if env:
        e.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=e,
    ).stdout.strip()


def _repo() -> tuple[tempfile.TemporaryDirectory, Path]:
    td = tempfile.TemporaryDirectory()
    root = Path(td.name) / "kod"
    root.mkdir()
    (root / "crates").mkdir()
    (root / "testsuite").mkdir()
    _git(root.parent, "init", "-q", "kod")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "crates" / "a.rs").write_text("fn x() {}\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "bas")
    return td, root


class SealPreflightTests(unittest.TestCase):
    def test_no_facit_change_is_quiet(self):
        td, repo = _repo()
        ledger = Path(td.name) / "ledger"
        doc = sp.run(repo=repo, ledger=ledger, by="ci", base=None)
        self.assertEqual(doc["changed"], [])
        self.assertEqual(doc["verify"], [])
        self.assertEqual(sp.main(["--repo", str(repo), "--ledger", str(ledger)]), 0)
        td.cleanup()

    def test_changed_facit_is_sealed_into_ledger(self):
        td, repo = _repo()
        facit = repo / "testsuite" / "prov-facit.md"
        facit.write_text(
            "# facit\n\n```json\n" + json.dumps(KALLA, indent=2) + "\n```\n",
            encoding="utf-8",
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "nytt facit")
        ledger = Path(td.name) / "ledger"
        doc = sp.run(repo=repo, ledger=ledger, by="ci", base="HEAD~1")
        self.assertEqual(doc["changed"], ["testsuite/prov-facit.md"])
        self.assertEqual(doc["actions"][0]["status"], "sealed")
        self.assertEqual(doc["verify"], [])
        idx = ledger / "forsegling.jsonl"
        self.assertTrue(idx.is_file())
        row = json.loads(idx.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["sealed_by"], "ci")
        self.assertNotIn("token", row)
        # Second run: same bytes → already
        doc2 = sp.run(repo=repo, ledger=ledger, by="ci", base="HEAD~1")
        self.assertEqual(doc2["actions"][0]["status"], "already")
        td.cleanup()

    def test_kod_file_without_kalla_is_ignored(self):
        td, repo = _repo()
        (repo / "testsuite" / "notes.md").write_text("# not facit\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "notes")
        ledger = Path(td.name) / "ledger"
        doc = sp.run(repo=repo, ledger=ledger, by="ci", base="HEAD~1")
        self.assertEqual(doc["changed"], [])
        td.cleanup()


if __name__ == "__main__":
    unittest.main()
