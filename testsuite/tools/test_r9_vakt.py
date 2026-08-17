#!/usr/bin/env python3
"""R9-vakt. Fixture text + injected grind days. No rig."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

import test_lab_guard  # noqa: F401
import r9_vakt as r9

DOM = """# d
## DOM M1-EFTER — STOPP (mätprotokollet) — 2026-08-17 00:35 CEST
## DOM GAP4-SJALVBEVIS — EJ KÖRBAR (STOPP) — 2026-08-16 11:38 CEST
## DOM A1 — GRÖNT — 2026-08-16 00:28 CEST
"""


def _git_init(repo: Path) -> None:
    subprocess.check_call(
        ["git", "init"],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class R9Tests(unittest.TestCase):
    def test_stopp_without_grind_alarms_from_since(self):
        doc = r9.lint(DOM, grind_days=[], since=date(2026, 8, 17))
        self.assertFalse(doc["ok"])
        self.assertEqual([a["punkt"] for a in doc["alarms"]], ["M1-EFTER"])

    def test_same_day_grind_clears(self):
        doc = r9.lint(DOM, grind_days=["2026-08-17"], since=date(2026, 8, 17))
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["alarms"], [])

    def test_older_stopp_ignored_before_since(self):
        doc = r9.lint(DOM, grind_days=[], since=date(2026, 8, 17))
        self.assertNotIn("GAP4-SJALVBEVIS", [a["punkt"] for a in doc["alarms"]])

    def test_cli_never_blocks_when_repo_reachable(self):
        td = tempfile.TemporaryDirectory()
        repo = Path(td.name) / "repo"
        repo.mkdir()
        _git_init(repo)
        p = Path(td.name) / "d.md"
        p.write_text(DOM, encoding="utf-8")
        self.assertEqual(
            r9.main(["--domfil", str(p), "--repo", str(repo), "--since", "2026-08-17"]),
            0,
        )
        td.cleanup()

    def test_repo_spec_local_vs_ssh(self):
        self.assertEqual(r9.split_repo_spec("/tmp/foo"), (None, "/tmp/foo"))
        self.assertEqual(
            r9.split_repo_spec("lanister:~/rtx-toolbox-d"),
            ("lanister", "~/rtx-toolbox-d"),
        )
        self.assertEqual(
            r9.split_repo_spec("ssh://lanister/home/xerial/rtx-toolbox-d"),
            ("lanister", "/home/xerial/rtx-toolbox-d"),
        )

    def test_unreachable_dir_is_repo_onabart(self):
        td = tempfile.TemporaryDirectory()
        repo = Path(td.name) / "not-a-repo"
        repo.mkdir()
        with self.assertRaises(r9.RepoUnreachable) as cm:
            r9.grind_days_from_git(repo)
        self.assertIn("REPO ONÅBART", str(cm.exception))
        p = Path(td.name) / "d.md"
        p.write_text(DOM, encoding="utf-8")
        rc = r9.main(["--domfil", str(p), "--repo", str(repo), "--since", "2026-08-17"])
        self.assertEqual(rc, r9.EXIT_REPO_ONABART)
        self.assertEqual(rc, 3)
        td.cleanup()

    def test_omitted_repo_is_onabart_not_silent(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "d.md"
        p.write_text(DOM, encoding="utf-8")
        rc = r9.main(["--domfil", str(p), "--since", "2026-08-17"])
        self.assertEqual(rc, r9.EXIT_REPO_ONABART)
        td.cleanup()

    def test_omitted_repo_json_is_onabart_not_ok(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "d.md"
        p.write_text("## DOM A1 — GRÖNT — 2026-08-17\n", encoding="utf-8")
        rc = r9.main(["--domfil", str(p), "--json", "--since", "2026-08-17"])
        self.assertEqual(rc, r9.EXIT_REPO_ONABART)
        td.cleanup()

    def test_reachable_empty_repo_is_not_onabart(self):
        td = tempfile.TemporaryDirectory()
        repo = Path(td.name) / "repo"
        repo.mkdir()
        _git_init(repo)
        days = r9.grind_days_from_git(repo)
        self.assertEqual(days, set())
        p = Path(td.name) / "d.md"
        p.write_text(DOM, encoding="utf-8")
        rc = r9.main(["--domfil", str(p), "--repo", str(repo), "--json", "--since", "2026-08-17"])
        self.assertEqual(rc, 0)
        td.cleanup()

    def test_onabart_json_has_no_alarms(self):
        td = tempfile.TemporaryDirectory()
        repo = Path(td.name) / "empty"
        repo.mkdir()
        p = Path(td.name) / "d.md"
        p.write_text(DOM, encoding="utf-8")
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r9.main(
                ["--domfil", str(p), "--repo", str(repo), "--json", "--since", "2026-08-17"]
            )
        self.assertEqual(rc, 3)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["repo"], "ONÅBART")
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["alarms"], [])
        self.assertIsNone(doc["grind_days"])
        td.cleanup()


if __name__ == "__main__":
    unittest.main()
