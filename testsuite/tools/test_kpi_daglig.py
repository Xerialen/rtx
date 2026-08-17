#!/usr/bin/env python3
"""KPI-daglig. Fixtures only — no ~/lab, no real herdr."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

import test_lab_guard  # noqa: F401
import kpi_daglig as kpi

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "kpi_config.json").read_text(encoding="utf-8"))

DOM = """# d
## DOM RAM-V2-KOD — GODKÄND (kodnivån) — 2026-08-16 20:43 CEST
## DOM I1 — GRÖNT — 2026-08-15 23:38 CEST
## DOM T20M-PRELIM — STOPP — 2026-08-17 08:19 CEST
"""


def _git_repo() -> tuple[tempfile.TemporaryDirectory, Path]:
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    env = os.environ.copy()
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "testsuite" / "tools").mkdir(parents=True)
    (root / "crates" / "rtx-game").mkdir(parents=True)
    (root / "testsuite" / "tools" / "r1_vakt.py").write_text("x\n", encoding="utf-8")
    (root / "crates" / "rtx-game" / "lib.rs").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "feat: mix"], check=True, env=env)
    return td, root


class KpiTests(unittest.TestCase):
    def test_k1_days_since_last_pass(self):
        row = kpi.k1(CFG, DOM, date(2026, 8, 17))
        self.assertEqual(row["status"], "MÄTT")
        self.assertEqual(row["value"], 1)
        self.assertEqual(row["last_date"], "2026-08-16")
        self.assertEqual(row["last_punkt"], "RAM-V2-KOD")
        self.assertFalse(row["alarm"])
        row2 = kpi.k1(CFG, DOM, date(2026, 8, 18))
        self.assertEqual(row2["value"], 2)
        self.assertTrue(row2["alarm"])

    def test_k2_k4_k6_unmeasured(self):
        for fn, kid in ((kpi.k2, "K2"), (kpi.k4, "K4"), (kpi.k6, "K6")):
            row = fn(CFG) if kid != "K2" else fn(CFG, DOM)
            self.assertEqual(row["status"], "OMÄTT", kid)
            self.assertIsNone(row["value"])
            self.assertTrue(row["why"])

    def test_k3_classifies_git_paths(self):
        td, repo = _git_repo()
        row = kpi.k3(CFG, repo, date.today())
        self.assertEqual(row["status"], "MÄTT")
        self.assertEqual(row["n_apparat_files"], 1)
        self.assertEqual(row["n_produkt_files"], 1)
        self.assertAlmostEqual(row["value"], 0.5)
        self.assertTrue(row["alarm"])  # 0.5 >= 0.4
        td.cleanup()

    def test_k3_without_repo_is_unmeasured(self):
        row = kpi.k3(CFG, None, date(2026, 8, 17))
        self.assertEqual(row["status"], "OMÄTT")

    def test_k5_idle_flag_without_inventing_hours(self):
        td, repo = _git_repo()
        row = kpi.k5(CFG, repo, date.today())
        self.assertEqual(row["status"], "OMÄTT")
        self.assertFalse(row["idle_3d"])
        self.assertFalse(row["alarm"])
        td.cleanup()

    def test_k7_k8_from_worklogs(self):
        td = tempfile.TemporaryDirectory()
        wl = Path(td.name)
        (wl / "2026-08-17-handoff11.md").write_text("h\n", encoding="utf-8")
        (wl / "2026-08-17-handoff10.md").write_text("h\n", encoding="utf-8")
        (wl / "grok-u2-rapport.md").write_text("r\n", encoding="utf-8")
        k8 = kpi.k8(CFG, wl, date(2026, 8, 17))
        self.assertEqual(k8["status"], "MÄTT")
        self.assertEqual(k8["value"], 2)
        self.assertFalse(k8["alarm"])
        self.assertEqual(k8["fable_share"]["status"], "OMÄTT")
        git_td, repo = _git_repo()
        k7 = kpi.k7(CFG, wl, repo, date(2026, 8, 17))
        # ceremony: *handoff* matches 2 files; *rapport* does not have the date in name
        self.assertEqual(k7["status"], "MÄTT")
        self.assertEqual(k7["n_ceremony"], 2)
        git_td.cleanup()
        td.cleanup()

    def test_dry_run_and_larm_file(self):
        td = tempfile.TemporaryDirectory()
        out = Path(td.name) / "kpi"
        dom = Path(td.name) / "dom.md"
        dom.write_text("## DOM I1 — GRÖNT — 2026-08-10 23:38 CEST\n", encoding="utf-8")
        doc = kpi.compute(
            cfg=CFG, as_of=date(2026, 8, 17),
            dom_text=dom.read_text(encoding="utf-8"),
            repo=None, worklogs=None,
        )
        self.assertIn("K1", doc["alarms"])
        jsonl, larm = kpi.write_outputs(doc, out)
        self.assertTrue(jsonl.is_file())
        self.assertIsNotNone(larm)
        self.assertTrue(larm.is_file())
        line = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(line["schema"], "kpi-daglig/1")
        self.assertEqual(line["kpis"]["K2"]["status"], "OMÄTT")
        td.cleanup()

    def test_cli_dry(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "d.md"
        p.write_text(DOM, encoding="utf-8")
        rc = kpi.main(["--domfil", str(p), "--config", str(HERE / "kpi_config.json"),
                       "--as-of", "2026-08-17", "--dry"])
        self.assertEqual(rc, 0)
        td.cleanup()


if __name__ == "__main__":
    unittest.main()
