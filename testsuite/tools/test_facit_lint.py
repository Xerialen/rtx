#!/usr/bin/env python3
"""facit_lint + forsegla_facit: v3 passerar, utan cykeldefinition vägras.

Hermetiska fixturer. Ingen ~/lab.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401

# testsuite/tools → repo = parents[1]
GATES = HERE.parents[1] / "tools" / "gates"
sys.path.insert(0, str(GATES))
import facit_lint as fl  # noqa: E402

DATA = HERE / "testdata" / "facit"
V3 = DATA / "nattens-v3.md"
V3ADD = DATA / "nattens-v3-addendum.md"
NOCYC = DATA / "utan-cykel.md"
FORSEGLA = GATES / "forsegla_facit.sh"


class FacitLintTests(unittest.TestCase):
    def test_nattens_v3_passerar(self):
        r = fl.lint_path(V3, V3ADD)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["missing"], [])
        self.assertTrue(r["units"]["ok"], r["units"])

    def test_utan_cykeldefinition_vagras(self):
        r = fl.lint_path(NOCYC)
        self.assertFalse(r["ok"])
        self.assertIn("cykeldefinition", r["missing"])

    def test_d2_som_deploy_vagras_grind1(self):
        text = (V3.read_text(encoding="utf-8") + "\n\n"
                + V3ADD.read_text(encoding="utf-8")
                + "\n\nF körs på d2.\n")
        r = fl.lint(text)
        self.assertFalse(r["ok"])
        self.assertTrue(any("d2" in e for e in r["units"]["errors"]))

    def test_cli_exit(self):
        self.assertEqual(fl.main([str(V3), "--addendum", str(V3ADD)]), 0)
        self.assertEqual(fl.main([str(NOCYC)]), 2)


class ForseglaTests(unittest.TestCase):
    def test_vagrar_utan_chmod_vid_brist(self):
        td = tempfile.TemporaryDirectory()
        src = Path(td.name) / "utkast.md"
        src.write_text(NOCYC.read_text(encoding="utf-8"), encoding="utf-8")
        before = src.stat().st_mode
        rc = subprocess.run(
            ["bash", str(FORSEGLA), str(src)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rc.returncode, 2, rc.stderr)
        self.assertFalse((Path(str(src) + ".sha256")).exists())
        self.assertEqual(src.stat().st_mode, before)

    def test_forseglar_komplett_facit(self):
        td = tempfile.TemporaryDirectory()
        fac = Path(td.name) / "facit.md"
        add = Path(td.name) / "facit-addendum.md"
        fac.write_text(V3.read_text(encoding="utf-8"), encoding="utf-8")
        add.write_text(V3ADD.read_text(encoding="utf-8"), encoding="utf-8")
        rc = subprocess.run(
            ["bash", str(FORSEGLA), str(fac), "--addendum", str(add)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rc.returncode, 0, rc.stderr + rc.stdout)
        self.assertIn("FORSEGLAD", rc.stdout)
        self.assertTrue(fac.with_name("facit.md.sha256").exists() or Path(str(fac) + ".sha256").exists())
        kv = Path(str(fac) + ".sha256").read_text(encoding="utf-8")
        self.assertRegex(kv, r"^[0-9a-f]{64}  facit\.md\n$")
        mode = stat.S_IMODE(fac.stat().st_mode)
        self.assertEqual(mode, 0o444)
        td.cleanup()


if __name__ == "__main__":
    unittest.main()
