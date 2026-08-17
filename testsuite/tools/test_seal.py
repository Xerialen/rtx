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
import facit_kalla  # noqa: E402
import seal_ledger as sl  # noqa: E402

SEAL_SH = HERE / "seal.sh"


def kalla_block(**over) -> dict:
    """Ett giltigt källblock. Testerna varierar ETT fält i taget ur det här."""
    b = {
        "schema": facit_kalla.SCHEMA,
        "expected_source": "derived",
        "never_from_judged_run": True,
        "derived_from": ["transformator.py mot dm3-base-full-graph.json"],
    }
    b.update(over)
    return {k: v for k, v in b.items() if v is not ...}


def facit_md(krav: str = "- krav: 4/4", **over) -> str:
    """Ett facit i markdown med källblocket i ett ```json-block."""
    return "# facit\n\n" + krav + "\n\n```json\n" + json.dumps(kalla_block(**over), indent=2) + "\n```\n"


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
        self.facit.write_text(facit_md("- krav: 4/4"), encoding="utf-8")

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
        andra.write_text(facit_md("- krav: 8/8"), encoding="utf-8")
        r2 = json.loads(self.seal(facit=andra).stdout)
        self.assertEqual(r2["prev"], r1["line_sha256"])
        rows = sl.read_index(self.ledger)
        self.assertEqual([x["seal_id"] for x in rows], [r1["seal_id"], r2["seal_id"]])
        self.assertEqual(sl.verify_chain(rows), [])

    def tva_rader(self):
        self.seal()
        andra = self.tmp / "facit-tva.md"
        andra.write_text(facit_md("- krav: 8/8"), encoding="utf-8")
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
        andra.write_text(facit_md("- krav: 8/8"), encoding="utf-8")
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
        self.facit.write_text(facit_md("- krav: 8/8"), encoding="utf-8")
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


class Kontrasignatur(Bas):
    """Variant B: CI producerar sigillet, Sol-sätet verifierar i efterhand."""

    def test_sigillet_ar_deterministiskt_ur_facitbytes_och_head(self):
        a = sl.sigill("a" * 64, "b" * 40)
        self.assertEqual(a, sl.sigill("a" * 64, "b" * 40), "samma indata, samma sigill")
        self.assertNotEqual(a, sl.sigill("c" * 64, "b" * 40), "annat facit")
        self.assertNotEqual(a, sl.sigill("a" * 64, "d" * 40), "annan HEAD")
        self.assertEqual(len(a), 64)

    def test_domanetiketten_skiljer_sigillet_fran_andra_hashar(self):
        """Ett naket sha256(facit||head) hade kunnat förväxlas med vilken annan
        64-hex som helst i projektet. Etiketten gör värdet självbeskrivande."""
        import hashlib

        naket = hashlib.sha256(("a" * 64 + "b" * 40).encode()).hexdigest()
        self.assertNotEqual(sl.sigill("a" * 64, "b" * 40), naket)
        self.assertIn("forsegling-kontrasignatur/1", sl.SIGILL_ALG)

    def test_blanksteg_runt_indata_andrar_inte_sigillet(self):
        self.assertEqual(sl.sigill("  " + "a" * 64 + "\n", " " + "b" * 40 + " "), sl.sigill("a" * 64, "b" * 40))

    def test_tom_indata_vagras(self):
        with self.assertRaises(sl.Vagran):
            sl.sigill("", "b" * 40)
        with self.assertRaises(sl.Vagran):
            sl.sigill("a" * 64, "   ")

    def test_varje_ny_rad_bar_sitt_sigill(self):
        row = json.loads(self.seal().stdout)
        self.assertEqual(row["sigill"], sl.sigill(row["facit_sha256"], row["head"]))
        self.assertEqual(row["sigill_alg"], sl.SIGILL_ALG)
        # Sigillet ligger innanför radhashen, så det går inte att byta i efterhand
        # utan att raden faller.
        self.assertEqual(row["line_sha256"], sl.line_hash(row))

    def test_ci_producerar_samma_sigill_som_raden(self):
        """CI:s producentsteg och seal.sh måste ge samma värde, annars är
        kontrasignaturen bara två verktyg som räknar olika."""
        row = json.loads(self.seal().stdout)
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sl.main(["sigill", "--facit", str(self.facit), "--head", self.head])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), row["sigill"])

    def test_solsatet_instammer_pa_en_hel_liggare(self):
        self.seal()
        rows = sl.read_index(self.ledger)
        avvikelser, okontrasignerade = sl.kontrasignera(rows)
        self.assertEqual(avvikelser, [])
        self.assertEqual(okontrasignerade, [])

    def test_solsatet_ser_en_rad_dar_sigillet_inte_hor_ihop(self):
        self.seal()
        p = sl.index_path(self.ledger)
        rad = json.loads(p.read_text(encoding="utf-8"))
        rad["sigill"] = "f" * 64
        rad["line_sha256"] = sl.line_hash(rad)  # radhashen "lagas" — sigillet syns ändå
        p.write_text(sl.kanonisk(rad) + "\n", encoding="utf-8")
        avvikelser, _ = sl.kontrasignera(sl.read_index(self.ledger))
        self.assertEqual(len(avvikelser), 1)
        self.assertIn("hör inte ihop", avvikelser[0])

    def test_rad_utan_sigill_ar_okontrasignerad_inte_fel(self):
        """Liggaren har rader skrivna före variant B. En verifiering som färgar
        dem röda hade varit röd för alltid och därmed meningslös."""
        gammal = sl.build_row(
            facit=Path("gammalt-facit.md"),
            facit_sha256="a" * 64,
            facit_bytes=1,
            head="b" * 40,
            head_subject="",
            code_paths=["crates"],
            sealed_at="2026-01-01T00:00:00Z",
            sealed_by="sol",
            prev=sl.GENESIS,
            seal_id="gammalt-facit-aaaaaaaaaaaa",
        )
        self.assertNotIn("sigill", gammal)
        avvikelser, okontrasignerade = sl.kontrasignera([gammal])
        self.assertEqual(avvikelser, [])
        self.assertEqual(len(okontrasignerade), 1)
        self.assertIn("före variant B", okontrasignerade[0])

    def test_kontrasignaturen_blockerar_inte(self):
        """Variant B i klartext: verifieringen rapporterar och returnerar 0."""
        self.seal()
        p = sl.index_path(self.ledger)
        rad = json.loads(p.read_text(encoding="utf-8"))
        rad["sigill"] = "f" * 64
        rad["line_sha256"] = sl.line_hash(rad)
        p.write_text(sl.kanonisk(rad) + "\n", encoding="utf-8")

        self.assertEqual(sl.main(["kontrasignatur", "--ledger", str(self.ledger)]), 0)
        # --strict finns för den som VILL ha en grind, och då är det ett eget val.
        self.assertEqual(sl.main(["kontrasignatur", "--ledger", str(self.ledger), "--strict"]), 1)

    def test_strict_pa_en_hel_liggare_ar_gron(self):
        self.seal()
        self.assertEqual(sl.main(["kontrasignatur", "--ledger", str(self.ledger), "--strict"]), 0)


class Kallkravet(Bas):
    """Vakten: observed blir ALDRIG expected."""

    SEALED_AT = "2026-08-17T12:00:00Z"

    def granska(self, block, sealed_at=None):
        p = self.tmp / "f.md"
        p.write_text("# facit\n\n```json\n" + json.dumps(block) + "\n```\n", encoding="utf-8")
        return facit_kalla.granska(p, sealed_at or self.SEALED_AT)

    def test_facit_utan_kallblock_vagras(self):
        p = self.tmp / "naket.md"
        p.write_text("# facit\n\n- krav: 4/4\n", encoding="utf-8")
        r = self.seal(facit=p)
        self.assertEqual(r.returncode, 2)
        self.assertIn("saknar källblock", r.stderr)
        self.assertFalse(sl.index_path(self.ledger).exists(), "ingenting skrevs")

    def test_harledda_varden_gar_igenom(self):
        block, noter = self.granska(kalla_block())
        self.assertEqual(block["expected_source"], "derived")
        self.assertTrue(any("derived_from" in n for n in noter))

    def test_harledd_utan_kalla_vagras(self):
        with self.assertRaises(facit_kalla.Vagran) as cm:
            self.granska(kalla_block(derived_from=[]))
        self.assertIn("derived_from", str(cm.exception))

    def test_matt_fore_forseglingen_gar_igenom(self):
        block, noter = self.granska(
            kalla_block(
                expected_source="pre-measured",
                derived_from=...,
                measured_at="2026-08-17T09:00:00Z",
                measured_by="kimi tbx-d4",
            )
        )
        self.assertEqual(block["expected_source"], "pre-measured")
        self.assertTrue(any("measured_at" in n for n in noter))

    def test_matt_EFTER_forseglingen_vagras(self):
        """Kärnan i vakten. En mätning daterad efter förseglingen ÄR observed som
        blivit expected — det finns ingen annan läsning."""
        with self.assertRaises(facit_kalla.Vagran) as cm:
            self.granska(
                kalla_block(
                    expected_source="pre-measured",
                    derived_from=...,
                    measured_at="2026-08-17T12:00:01Z",
                    measured_by="kimi",
                )
            )
        self.assertIn("observed som blivit expected", str(cm.exception))

    def test_matt_exakt_vid_forseglingen_vagras_ocksa(self):
        with self.assertRaises(facit_kalla.Vagran):
            self.granska(
                kalla_block(
                    expected_source="pre-measured",
                    derived_from=...,
                    measured_at=self.SEALED_AT,
                    measured_by="kimi",
                )
            )

    def test_vilken_tidsstampel_som_helst_i_framtiden_vagras(self):
        """Hängslet: regeln gäller varje `*_at`-fält, inte bara det som råkar heta
        measured_at. Ett facit kan datera sin källa under vilket namn som helst."""
        with self.assertRaises(facit_kalla.Vagran) as cm:
            self.granska(kalla_block(kvitto_skrivet_at="2026-08-17T12:00:01Z"))
        self.assertIn("nyare än förseglingen", str(cm.exception))

    def test_tidsstampel_utan_tidszon_vagras(self):
        with self.assertRaises(facit_kalla.Vagran) as cm:
            self.granska(kalla_block(nagot_at="2026-08-17T09:00:00"))
        self.assertIn("saknar tidszon", str(cm.exception))

    def test_never_from_judged_run_maste_pastas(self):
        for v in (False, None, "true", ...):
            with self.subTest(v=v):
                with self.assertRaises(facit_kalla.Vagran) as cm:
                    self.granska(kalla_block(never_from_judged_run=v))
                self.assertIn("never_from_judged_run", str(cm.exception))

    def test_okand_kalla_vagras(self):
        with self.assertRaises(facit_kalla.Vagran) as cm:
            self.granska(kalla_block(expected_source="observed"))
        self.assertIn("expected_source", str(cm.exception))

    def test_none_ar_ett_uttryckligt_pastaende(self):
        """Ett rent kontrakt utan förväntade värden får förseglas — men det ska
        stå, inte utelämnas."""
        block, _ = self.granska(kalla_block(expected_source="none", derived_from=...))
        self.assertEqual(block["expected_source"], "none")

    def test_json_facit_bar_blocket_som_toppnyckel(self):
        p = self.tmp / "manifest.json"
        p.write_text(json.dumps({"schema": "komponat-manifest/1", facit_kalla.NYCKEL: kalla_block()}), "utf-8")
        block, _ = facit_kalla.granska(p, self.SEALED_AT)
        self.assertEqual(block["expected_source"], "derived")

    def test_tva_olika_kallblock_vagras(self):
        p = self.tmp / "tvetydig.md"
        p.write_text(
            "```json\n" + json.dumps(kalla_block()) + "\n```\n"
            "```json\n" + json.dumps(kalla_block(expected_source="none", derived_from=...)) + "\n```\n",
            encoding="utf-8",
        )
        with self.assertRaises(facit_kalla.Vagran) as cm:
            facit_kalla.granska(p, self.SEALED_AT)
        self.assertIn("flera OLIKA källblock", str(cm.exception))

    def test_pinnade_kallor_kontrolleras(self):
        kalla = self.tmp / "kvitto.json"
        kalla.write_text('{"n": 1}', encoding="utf-8")
        import hashlib

        sha = hashlib.sha256(kalla.read_bytes()).hexdigest()
        block, noter = self.granska(kalla_block(sources=[{"path": str(kalla), "sha256": sha}]))
        self.assertTrue(any("pinnade" in n for n in noter))

        kalla.write_text('{"n": 2}', encoding="utf-8")
        with self.assertRaises(facit_kalla.Vagran) as cm:
            self.granska(kalla_block(sources=[{"path": str(kalla), "sha256": sha}]))
        self.assertIn("har ändrats", str(cm.exception))

    def test_pinnad_kalla_som_saknas_vagras(self):
        with self.assertRaises(facit_kalla.Vagran) as cm:
            self.granska(kalla_block(sources=[{"path": str(self.tmp / "borta.json"), "sha256": "0" * 64}]))
        self.assertIn("finns inte", str(cm.exception))

    def test_raden_bokfor_vad_facitet_pastod(self):
        row = json.loads(self.seal().stdout)
        self.assertEqual(row["expected_source"], "derived")
        self.assertTrue(row["kalla_noter"])
        # Innanför radhashen, så påståendet inte går att byta i efterhand.
        self.assertEqual(row["line_sha256"], sl.line_hash(row))


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
