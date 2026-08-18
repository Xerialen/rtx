#!/usr/bin/env python3
"""facit_lint + forsegla_facit: fullfacit och addendumläge.

v3 passerar; utan cykeldefinition vägras. Addendum med de fyra
kraven passerar (moder_sha = 64-hex + förseglad syskonfil);
ofullständigt addendum vägras. Fullfacitvägen är oförändrad
(nio klausuler, samma CLI-rad).

Hermetiska fixturer. Ingen ~/lab.
"""

from __future__ import annotations

import hashlib
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
ADD_OK = DATA / "komplett-addendum.md"
ADD_M1 = DATA / "m1-addendum1.md"
FORSEGLA = GATES / "forsegla_facit.sh"
FACIT_CLAUSE_IDS = [
    "grund",
    "cykeldefinition",
    "paritet",
    "referensarm",
    "jamforande_dom",
    "kontrollvarden",
    "staende_slutklausul",
    "addendum_regel",
    "domskala",
]
ADDENDUM_KRAV_IDS = ["moder_sha", "tidsstamplad", "paragraf", "beslutsfattare"]


def _seal_parent(d: Path, name: str = "moder.md") -> tuple[Path, str]:
    p = d / name
    p.write_text("# FACIT testharnes\nminimalt.\n", encoding="utf-8")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    side = Path(str(p) + ".sha256")
    side.write_text("%s  %s\n" % (digest, name), encoding="utf-8")
    os.chmod(p, 0o444)
    os.chmod(side, 0o444)
    return p, digest


def _addendum_text(sha: str) -> str:
    return (
        "# ADDENDUM till testharnes-facit (sha %s) — FÖRE armslut\n\n"
        "Tidsstämpel 2026-08-18T18:24:20Z.\n"
        "Tolkning av §8: ersättningsmodell vid otillgänglig pool.\n"
        "Beslut: Fable på ägarens direkta order.\n" % sha
    )


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


class AddendumLageTests(unittest.TestCase):
    def test_huvud_kanner_igen_addendum(self):
        self.assertTrue(fl.is_addendum(ADD_OK.read_text(encoding="utf-8")))
        self.assertTrue(fl.is_addendum(ADD_M1.read_text(encoding="utf-8")))
        self.assertFalse(fl.is_addendum(V3.read_text(encoding="utf-8")))
        self.assertFalse(fl.is_addendum(""))

    def test_komplett_addendum_passerar(self):
        td = tempfile.TemporaryDirectory()
        d = Path(td.name)
        parent, digest = _seal_parent(d)
        add = d / "addendum.md"
        add.write_text(_addendum_text(digest), encoding="utf-8")
        r = fl.lint_path(add)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mode"], "addendum")
        self.assertEqual(r["missing"], [])
        self.assertEqual(
            [k["id"] for k in r["addendum_krav"]], ADDENDUM_KRAV_IDS
        )
        self.assertTrue(all(k["ok"] for k in r["addendum_krav"]))
        sha_krav = next(k for k in r["addendum_krav"] if k["id"] == "moder_sha")
        self.assertEqual(sha_krav["cited"], [digest])
        self.assertEqual(sha_krav["parent"], str(parent))
        td.cleanup()

    def test_m1_addendum1_kort_prefix_vagras(self):
        """Kort prefix (1b501a1c…) räcker inte — deepseeks rek."""
        r = fl.lint_path(ADD_M1)
        self.assertEqual(r["mode"], "addendum")
        self.assertFalse(r["ok"], r)
        self.assertIn("moder_sha", r["missing"])

    def test_kort_sha_i_rubrik_vagras(self):
        r = fl.lint_path(ADD_OK)
        self.assertEqual(r["mode"], "addendum")
        self.assertFalse(r["ok"], r)
        self.assertIn("moder_sha", r["missing"])

    def test_full_sha_utan_forseglad_moder_vagras(self):
        td = tempfile.TemporaryDirectory()
        fake = "ab" * 32
        add = Path(td.name) / "addendum.md"
        add.write_text(_addendum_text(fake), encoding="utf-8")
        r = fl.lint_path(add)
        self.assertFalse(r["ok"], r)
        self.assertIn("moder_sha", r["missing"])
        sha_krav = next(k for k in r["addendum_krav"] if k["id"] == "moder_sha")
        self.assertEqual(sha_krav["cited"], [fake])
        self.assertIsNone(sha_krav["parent"])
        td.cleanup()

    def test_full_sha_moder_inte_0444_vagras(self):
        td = tempfile.TemporaryDirectory()
        d = Path(td.name)
        parent, digest = _seal_parent(d)
        os.chmod(parent, 0o644)
        add = d / "addendum.md"
        add.write_text(_addendum_text(digest), encoding="utf-8")
        r = fl.lint_path(add)
        self.assertFalse(r["ok"], r)
        self.assertIn("moder_sha", r["missing"])
        td.cleanup()

    def test_full_sha_utan_sidecar_vagras(self):
        td = tempfile.TemporaryDirectory()
        d = Path(td.name)
        parent, digest = _seal_parent(d)
        os.chmod(Path(str(parent) + ".sha256"), 0o644)
        Path(str(parent) + ".sha256").unlink()
        add = d / "addendum.md"
        add.write_text(_addendum_text(digest), encoding="utf-8")
        r = fl.lint_path(add)
        self.assertFalse(r["ok"], r)
        self.assertIn("moder_sha", r["missing"])
        td.cleanup()

    def test_addendum_saknar_varje_krav(self):
        td = tempfile.TemporaryDirectory()
        d = Path(td.name)
        _parent, digest = _seal_parent(d)
        base = _addendum_text(digest)
        mutants = {
            "moder_sha": base.replace("sha " + digest, "utan hash"),
            "tidsstamplad": base.replace("2026-08-18T18:24:20Z", "någon gång"),
            "paragraf": base.replace("§8", "en klausul"),
            "beslutsfattare": base.replace(
                "Beslut: Fable på ägarens direkta order.", "ingen namngiven."
            ),
        }
        for krav, text in mutants.items():
            with self.subTest(krav=krav):
                r = fl.lint_addendum(text, search_dir=d)
                self.assertFalse(r["ok"], r)
                self.assertIn(krav, r["missing"])
                self.assertEqual(r["mode"], "addendum")
        td.cleanup()

    def test_utan_addendumhuvud_gar_fullfacitvagen(self):
        text = ADD_OK.read_text(encoding="utf-8").replace(
            "# ADDENDUM till", "# FACIT till", 1
        )
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "inte-addendum.md"
        p.write_text(text, encoding="utf-8")
        r = fl.lint_path(p)
        self.assertEqual(r.get("mode"), "facit")
        self.assertFalse(r["ok"])
        self.assertIn("cykeldefinition", r["missing"])
        td.cleanup()

    def test_cli_addendum_exit_och_meddelande(self):
        import io
        from contextlib import redirect_stderr

        td = tempfile.TemporaryDirectory()
        d = Path(td.name)
        _parent, digest = _seal_parent(d)
        add = d / "addendum.md"
        add.write_text(_addendum_text(digest), encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            rc = fl.main([str(add)])
        self.assertEqual(rc, 0)
        self.assertIn("OK: addendum", err.getvalue())
        self.assertNotIn("nio klausuler", err.getvalue())

        bad = d / "bad.md"
        bad.write_text("# ADDENDUM tomt\n", encoding="utf-8")
        err2 = io.StringIO()
        with redirect_stderr(err2):
            rc2 = fl.main([str(bad)])
        self.assertEqual(rc2, 2)
        self.assertIn("STOPP: addendum ofullständigt", err2.getvalue())
        self.assertIn("saknar krav:", err2.getvalue())
        td.cleanup()

    def test_addendum_plus_addendumflagga_vagras(self):
        import io
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            rc = fl.main([str(ADD_OK), "--addendum", str(ADD_M1)])
        self.assertEqual(rc, 2)
        self.assertIn("addendumläge tar inte --addendum", err.getvalue())


class AddendumForseglaTests(unittest.TestCase):
    def test_forseglar_komplett_addendum(self):
        td = tempfile.TemporaryDirectory()
        d = Path(td.name)
        _parent, digest = _seal_parent(d)
        src = d / "addendum.md"
        src.write_text(_addendum_text(digest), encoding="utf-8")
        rc = subprocess.run(
            ["bash", str(FORSEGLA), str(src)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rc.returncode, 0, rc.stderr + rc.stdout)
        self.assertIn("FORSEGLAD", rc.stdout)
        sidecar = Path(str(src) + ".sha256")
        self.assertTrue(sidecar.exists())
        kv = sidecar.read_text(encoding="utf-8")
        self.assertRegex(kv, r"^[0-9a-f]{64}  addendum\.md\n$")
        self.assertEqual(stat.S_IMODE(src.stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o444)
        td.cleanup()

    def test_vagrar_ofullstandigt_addendum_utan_chmod(self):
        td = tempfile.TemporaryDirectory()
        src = Path(td.name) / "addendum.md"
        src.write_text("# ADDENDUM saknar allt\n", encoding="utf-8")
        before = src.stat().st_mode
        rc = subprocess.run(
            ["bash", str(FORSEGLA), str(src)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rc.returncode, 2, rc.stderr)
        self.assertFalse(Path(str(src) + ".sha256").exists())
        self.assertEqual(src.stat().st_mode, before)
        td.cleanup()

    def test_addendum_flagga_forseglar_inte_sidofil(self):
        """Piggyback: --addendum på addendumfil får inte 0444:a junk."""
        td = tempfile.TemporaryDirectory()
        src = Path(td.name) / "addendum.md"
        junk = Path(td.name) / "junk.md"
        src.write_text(ADD_OK.read_text(encoding="utf-8"), encoding="utf-8")
        junk.write_text("skräp utan krav\n", encoding="utf-8")
        src_mode = src.stat().st_mode
        junk_mode = junk.stat().st_mode
        rc = subprocess.run(
            ["bash", str(FORSEGLA), str(src), "--addendum", str(junk)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rc.returncode, 2, rc.stderr)
        self.assertFalse(Path(str(src) + ".sha256").exists())
        self.assertFalse(Path(str(junk) + ".sha256").exists())
        self.assertEqual(src.stat().st_mode, src_mode)
        self.assertEqual(junk.stat().st_mode, junk_mode)
        td.cleanup()


class FullfacitVagOforandradTests(unittest.TestCase):
    def test_nio_klausuler_samma_id_och_utfall(self):
        r = fl.lint_path(V3, V3ADD)
        self.assertEqual(r.get("mode"), "facit")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["missing"], [])
        self.assertEqual([c["id"] for c in r["clauses"]], FACIT_CLAUSE_IDS)
        self.assertTrue(all(c["ok"] for c in r["clauses"]))
        self.assertTrue(r["units"]["ok"])

    def test_lint_funktionen_rors_inte_av_addendumlage(self):
        """lint() är nioklausulmotorn; addendumtext där ska fortfarande fällas."""
        r = fl.lint(ADD_OK.read_text(encoding="utf-8"))
        self.assertFalse(r["ok"])
        self.assertIn("cykeldefinition", r["missing"])
        self.assertNotIn("mode", r)

    def test_cli_fullfacit_meddelande_oforandrat(self):
        import io
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            rc = fl.main([str(V3), "--addendum", str(V3ADD)])
        self.assertEqual(rc, 0)
        self.assertEqual(err.getvalue().strip(), "OK: nio klausuler + grind-1")


if __name__ == "__main__":
    unittest.main()
