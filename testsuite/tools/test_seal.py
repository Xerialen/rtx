#!/usr/bin/env python3
"""seal.sh + seal_ledger.py — radformat, O_EXCL-disciplin, facit-före-kod-grinden.

Allt sker i tmp: egna git-repon, egna liggarkataloger. Ingen riggkontakt, och
inget som rör ~/lab (GOTCHAS 176/178).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
import seal_ledger as sl  # noqa: E402

SEAL_SH = HERE / "seal.sh"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    ).stdout.strip()


class Bas(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seal-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "kod"
        (self.repo / "crates").mkdir(parents=True)
        (self.repo / "testsuite").mkdir()
        git(self.repo.parent, "init", "-q", "kod")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "test")
        (self.repo / "crates" / "a.rs").write_text("fn main() {}\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "bas\n\nAgent: opus5")
        self.head = git(self.repo, "rev-parse", "HEAD")

        self.ledger = self.tmp / "ledger"
        self.facit = self.tmp / "facit-prov.md"
        self.facit.write_text("# facit\n\n- krav: 4/4\n", encoding="utf-8")

    def seal(self, facit=None, head=None, ledger=None, extra=()):
        cmd = [
            str(SEAL_SH),
            "--facit", str(facit or self.facit),
            "--head", head or self.head,
            "--ledger", str(ledger or self.ledger),
            "--by", "opus5",
            "--code-repo", str(self.repo),
            *extra,
        ]
        return subprocess.run(cmd, capture_output=True, text=True)


class Radformatet(Bas):
    def test_raden_ar_kanonisk_och_hashar_sig_sjalv(self):
        r = self.seal()
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(r.stdout.strip())
        self.assertEqual(row["schema"], sl.SCHEMA)
        self.assertEqual(row["facit_sha256"], sl.file_sha256(self.facit)[0])
        self.assertEqual(row["facit_bytes"], self.facit.stat().st_size)
        self.assertEqual(row["head"], self.head)
        self.assertEqual(row["sealed_by"], "opus5")
        self.assertEqual(row["prev"], sl.GENESIS, "första raden pekar på genesis")
        self.assertEqual(row["line_sha256"], sl.line_hash(row))
        self.assertEqual(row["code_paths"], ["crates", "testsuite"])
        # Kanonisk form: sorterade nycklar, inga blanksteg — annars går raden inte
        # att räkna om byte för byte hos någon annan.
        self.assertEqual(r.stdout.strip(), sl.kanonisk(row))

    def test_line_hash_utesluter_bara_sig_sjalvt(self):
        row = {"schema": sl.SCHEMA, "a": 1, "line_sha256": "x"}
        self.assertEqual(sl.line_hash(row), sl.line_hash({"schema": sl.SCHEMA, "a": 1}))
        self.assertNotEqual(sl.line_hash(row), sl.line_hash({"schema": sl.SCHEMA, "a": 2}))

    def test_seal_id_ar_namn_plus_innehall(self):
        sha = "a" * 64
        self.assertEqual(sl.seal_id_for(Path("facit-d-r2.md"), sha), "facit-d-r2-aaaaaaaaaaaa")
        self.assertEqual(sl.seal_id_for(Path("x.json"), sha), "x-aaaaaaaaaaaa")
        # Sökvägsseparatorer får aldrig in i ett filnamn.
        self.assertNotIn("/", sl.seal_id_for(Path("a b/c.md"), sha))


class Kedjan(Bas):
    def test_andra_raden_pekar_pa_den_forsta(self):
        r1 = json.loads(self.seal().stdout)
        andra = self.tmp / "facit-tva.md"
        andra.write_text("# facit 2\n", encoding="utf-8")
        r2 = json.loads(self.seal(facit=andra).stdout)
        self.assertEqual(r2["prev"], r1["line_sha256"])
        rows = sl.read_index(self.ledger)
        self.assertEqual([x["seal_id"] for x in rows], [r1["seal_id"], r2["seal_id"]])
        self.assertEqual(sl.verify_chain(rows), [])

    def tva_rader(self):
        self.seal()
        andra = self.tmp / "facit-tva.md"
        andra.write_text("# facit 2\n", encoding="utf-8")
        self.seal(facit=andra)
        p = sl.index_path(self.ledger)
        return p, p.read_text(encoding="utf-8").splitlines()

    def test_en_andrad_rad_faller_pa_sin_egen_hash(self):
        """Radhashen och kedjelänken fångar OLIKA saker, och det är avsiktligt.

        Ändras ett fält utan att `line_sha256` räknas om faller raden på sin egen
        hash — men `prev` i nästa rad pekar fortfarande rätt, så kedjelänken är
        hel. Två detektorer för två angrepp.
        """
        p, rader = self.tva_rader()
        manipulerad = json.loads(rader[0])
        manipulerad["facit_sha256"] = "0" * 64
        rader[0] = sl.kanonisk(manipulerad)
        p.write_text("\n".join(rader) + "\n", encoding="utf-8")

        fel = sl.verify_chain(sl.read_index(self.ledger))
        self.assertEqual(len(fel), 1, fel)
        self.assertIn("line_sha256 stämmer inte", fel[0])

    def test_en_omraknad_rad_bryter_kedjelanken_i_stallet(self):
        """Räknar den som ändrar också om radhashen håller raden — men då stämmer
        inte längre nästa rads `prev`. Det är kedjelänkens uppgift."""
        p, rader = self.tva_rader()
        manipulerad = json.loads(rader[0])
        manipulerad["facit_sha256"] = "0" * 64
        manipulerad["line_sha256"] = sl.line_hash(manipulerad)
        rader[0] = sl.kanonisk(manipulerad)
        p.write_text("\n".join(rader) + "\n", encoding="utf-8")

        fel = sl.verify_chain(sl.read_index(self.ledger))
        self.assertEqual(len(fel), 1, fel)
        self.assertIn("kedjan är bruten", fel[0])

    def test_en_bortklippt_rad_syns(self):
        p, rader = self.tva_rader()
        p.write_text(rader[1] + "\n", encoding="utf-8")
        fel = sl.verify_chain(sl.read_index(self.ledger))
        self.assertTrue(any("kedjan är bruten" in f for f in fel), fel)

    def test_verify_som_kommando(self):
        self.seal()
        rc = sl.main(["verify", "--ledger", str(self.ledger)])
        self.assertEqual(rc, 0)

    def test_append_pa_en_bruten_kedja_vagras(self):
        self.seal()
        p = sl.index_path(self.ledger)
        rad = json.loads(p.read_text(encoding="utf-8"))
        rad["sealed_by"] = "nagon-annan"
        p.write_text(sl.kanonisk(rad) + "\n", encoding="utf-8")
        andra = self.tmp / "facit-tva.md"
        andra.write_text("# facit 2\n", encoding="utf-8")
        r = self.seal(facit=andra)
        self.assertEqual(r.returncode, 2)
        self.assertIn("trasig innan vi ens börjat", r.stderr)


class OExcl(Bas):
    def test_samma_facit_kan_inte_forseglas_tva_ganger(self):
        self.assertEqual(self.seal().returncode, 0)
        r = self.seal()
        self.assertEqual(r.returncode, 2)
        self.assertIn("redan förseglat", r.stderr)
        self.assertEqual(len(sl.read_index(self.ledger)), 1, "indexet fick ingen andra rad")

    def test_kvittot_ar_skrivskyddat_och_skrivs_en_gang(self):
        self.seal()
        row = json.loads(sl.index_path(self.ledger).read_text(encoding="utf-8"))
        kvitto = sl.seal_path(self.ledger, row["seal_id"])
        self.assertTrue(kvitto.is_file())
        self.assertEqual(json.loads(kvitto.read_text(encoding="utf-8")), row)
        self.assertEqual(kvitto.stat().st_mode & 0o222, 0, "kvittot ska inte vara skrivbart")

    def test_andrat_innehall_ar_en_ny_forsegling(self):
        """Samma filnamn, andra bytes — nytt seal_id, ny rad. Det är en revision,
        inte en överskrivning, och båda står kvar."""
        r1 = json.loads(self.seal().stdout)
        self.facit.write_text("# facit\n\n- krav: 8/8\n", encoding="utf-8")
        r2 = json.loads(self.seal().stdout)
        self.assertNotEqual(r1["seal_id"], r2["seal_id"])
        self.assertEqual(len(sl.read_index(self.ledger)), 2)
        self.assertTrue(sl.seal_path(self.ledger, r1["seal_id"]).is_file())


class FacitForeKod(Bas):
    def test_ocommitterad_kod_vagras(self):
        (self.repo / "crates" / "b.rs").write_text("fn b() {}\n")
        r = self.seal()
        self.assertEqual(r.returncode, 1)
        self.assertIn("facit förseglas FÖRE kod", r.stderr)
        self.assertIn("crates/b.rs", r.stderr)
        self.assertFalse(sl.index_path(self.ledger).exists(), "ingenting skrevs")

    def test_ocommitterad_andring_i_spårad_fil_vagras(self):
        (self.repo / "crates" / "a.rs").write_text("fn main() { /* nytt */ }\n")
        r = self.seal()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FÖRE kod", r.stderr)

    def test_smuts_utanfor_kodvagarna_stoppar_inte(self):
        """Facitet självt ligger ofta i ett annat repo och ska inte behöva vara
        committat för att kunna förseglas — det är KODEN som ska vara det."""
        (self.repo / "anteckningar.txt").write_text("kladd\n")
        self.assertEqual(self.seal().returncode, 0)

    def test_egna_kodvagar_gar_att_ange(self):
        (self.repo / "crates" / "b.rs").write_text("fn b() {}\n")
        r = self.seal(extra=["--code-path", "testsuite"])
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(r.stdout)
        self.assertEqual(row["code_paths"], ["testsuite"], "raden bokför vad som kontrollerades")


class Grindar(Bas):
    def test_okand_head_vagras(self):
        r = self.seal(head="0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(r.returncode, 1)
        self.assertIn("okänd commit", r.stderr)

    def test_saknat_facit_vagras(self):
        r = self.seal(facit=self.tmp / "finns-inte.md")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ingen fil", r.stderr)

    def test_saknad_flagga_ar_anvandningsfel(self):
        r = subprocess.run(
            [str(SEAL_SH), "--facit", str(self.facit), "--head", self.head, "--ledger", str(self.ledger)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("saknar --by", r.stderr)

    def test_help_beskriver_grinden(self):
        r = subprocess.run([str(SEAL_SH), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("FACIT FÖRSEGLAS FÖRE KOD", r.stdout)
        self.assertIn("--ledger", r.stdout)

    def test_head_ref_loses_upp_till_full_sha(self):
        r = self.seal(head="HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(r.stdout)
        self.assertEqual(row["head"], self.head)
        self.assertEqual(row["head_subject"], "bas")


if __name__ == "__main__":
    unittest.main(verbosity=2)
