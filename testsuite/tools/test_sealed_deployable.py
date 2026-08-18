#!/usr/bin/env python3
"""SEALED_DEPLOYABLE — den förseglade mängden, och att parbindningen håller.

Egen fil (deepseek äger test_d_deploy.py).

Konstantparet SEALED_MANIFEST/RECEPT_SHA256 band runnern vid v296-ram-komponatet.
På/av-provet deployar tre varianter till, och en ensam konstant kan inte uttrycka
det. Mängden är fortfarande en IDENTITET: okänt recept vägras, och ett känt recept
med FEL manifest vägras lika hårt — parbindningen är själva förseglingen, inte två
kontroller som råkar passera var för sig.

Ingen riggkontakt.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
import d_failclosed as fc  # noqa: E402
from d_failclosed import (  # noqa: E402
    SEALED_DEPLOYABLE,
    SEALED_MANIFEST_SHA256,
    SEALED_RECEPT_SHA256,
    FailClosed,
    sealed_manifest_for,
)
from seal_ledger import file_sha256  # noqa: E402

RECEPT = HERE / "recept"

PAAV = {
    "paav-g-v1": ("e527361b26f1b3fb6c0bc0f87b92dd295b65e89997fccfda9e5f1d74051e2121",
                  "07cee24ed8cca3223c78ad8d4521b99266a5c7f892c16333ce8eed3494f447a8"),
    "paav-f-v1": ("58f220e41c4942a883087e67a6d5e70cfa994157e1eccdea7ac1a0d04d0653be",
                  "1c9692eb4f886bc5112954c3e504f8beea8d5abe0eb6579465a877923dc0b0a9"),
    "paav-o-v1": ("5dde67b3c68155583eacf32993edd393b7dd1cbb18d407fc32db65021c9281ad",
                  "666015d6b65d72abd84a61d09700b345ff9e8b35cbdb0f6ab4f4afef34be1615"),
}


class Mangden(unittest.TestCase):
    def test_exakt_fyra_poster(self):
        """Mängden är förseglad. Växer den utan beslut ska det synas här."""
        self.assertEqual(len(SEALED_DEPLOYABLE), 4)

    def test_v296_star_kvar_oforandrat(self):
        self.assertEqual(sealed_manifest_for(SEALED_RECEPT_SHA256), SEALED_MANIFEST_SHA256)

    def test_varje_paav_par_slas_upp(self):
        for namn, (rec, man) in PAAV.items():
            with self.subTest(namn):
                self.assertEqual(sealed_manifest_for(rec), man)

    def test_shan_ar_filernas_verkliga_byte(self):
        """Mängden ska binda de filer som faktiskt ligger i repot, inte tal någon skrev av."""
        for namn, (rec, man) in PAAV.items():
            with self.subTest(namn):
                rp = RECEPT / f"{namn}.json"
                mp = RECEPT / f"{namn}.manifest.json"
                if not rp.is_file() or not mp.is_file():
                    self.skipTest(f"{namn} inte på plats")
                self.assertEqual(file_sha256(rp)[0], rec, "receptets byte")
                self.assertEqual(file_sha256(mp)[0], man, "manifestets byte")


class Vagringar(unittest.TestCase):
    """Preflighten hashar de riktiga filerna, så binärerna pinnas mot tmp-kopior
    (install_test_pin kräver att de ligger under tmp — aldrig ~/lab)."""

    def setUp(self):
        import shutil
        import tempfile

        self.td = Path(tempfile.mkdtemp(prefix="sealedset-"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.addCleanup(fc.clear_test_pin)
        self.qw = self.td / "qwprogs.so"
        self.mv = self.td / "mvdsv"
        self.qw.write_bytes(b"qwprogs-attrapp")
        self.mv.write_bytes(b"mvdsv-attrapp")
        fc.install_test_pin(self.qw, self.mv)

    def test_okant_recept_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            sealed_manifest_for("a" * 64)
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertIn("står inte i den förseglade mängden", str(cm.exception))

    def test_ratt_recept_fel_manifest_vagras_genom_preflight(self):
        """Kärnan: ett giltigt recept parat med ett ANNAT giltigt manifest.

        Båda byten är förseglade var för sig; kombinationen är det inte. Utan
        parbindningen hade två oberoende "finns i mängden"-kontroller släppt
        igenom vilken korsning som helst.

        Sols punkt 4: testet ska bevisa en faktisk fail-closed-VÄGRAN genom
        preflighten, inte att två strängar är olika. En olikhet visar bara att jag
        kan jämföra; en vägran visar att grinden stänger.
        """
        korsningar = [
            ("paav-g-v1", "paav-f-v1"),
            ("paav-f-v1", "paav-o-v1"),
            ("paav-o-v1", "paav-g-v1"),
        ]
        for rec_namn, man_namn in korsningar:
            rec_p = RECEPT / f"{rec_namn}.json"
            man_p = RECEPT / f"{man_namn}.manifest.json"
            if not rec_p.is_file() or not man_p.is_file():
                self.skipTest("recept/manifest inte på plats")
            with self.subTest(f"{rec_namn} + {man_namn}"):
                with self.assertRaises(FailClosed) as cm:
                    fc._issue_preflight_seal_from_files(
                        manifest_path=man_p,
                        recept_path=rec_p,
                        qwprogs_path=self.qw,
                        mvdsv_path=self.mv,
                    )
                self.assertEqual(cm.exception.gate, "deploy-context")
                self.assertIn("bundet till", str(cm.exception))
                self.assertIsNone(fc.active_deploy_context(), "ingen kontext fick mintas")

    def test_ratt_par_ger_ett_sigill_genom_preflight(self):
        """Motprovet: rätt par ska faktiskt ta sig igenom, annars bevisar vägran inget."""
        rec_p = RECEPT / "paav-g-v1.json"
        man_p = RECEPT / "paav-g-v1.manifest.json"
        if not rec_p.is_file() or not man_p.is_file():
            self.skipTest("recept/manifest inte på plats")
        seal = fc._issue_preflight_seal_from_files(
            manifest_path=man_p,
            recept_path=rec_p,
            qwprogs_path=self.qw,
            mvdsv_path=self.mv,
        )
        self.assertEqual(seal.recept_sha256, PAAV["paav-g-v1"][0])
        self.assertEqual(seal.manifest_sha256, PAAV["paav-g-v1"][1])

    def test_okant_recept_vagras_genom_preflight(self):
        frammande = RECEPT / "komponat-k2-v296-ram.json"
        if not frammande.is_file():
            self.skipTest("k2-receptet saknas")
        with self.assertRaises(FailClosed) as cm:
            fc._issue_preflight_seal_from_files(
                manifest_path=RECEPT / "paav-g-v1.manifest.json",
                recept_path=frammande,
                qwprogs_path=self.qw,
                mvdsv_path=self.mv,
            )
        self.assertIn("står inte i den förseglade mängden", str(cm.exception))

    def test_tomt_och_none_vagras(self):
        for bad in ("", "   ", None):
            with self.subTest(bad=bad):
                with self.assertRaises(FailClosed):
                    sealed_manifest_for(bad)

    def test_versaler_normaliseras_inte_till_nytt_recept(self):
        """Samma sha i versaler är samma recept — inte ett okänt."""
        self.assertEqual(
            sealed_manifest_for(SEALED_RECEPT_SHA256.upper()),
            SEALED_MANIFEST_SHA256,
        )

    def test_inga_dubblerade_manifest(self):
        """Två recept får inte peka på samma manifest — då är bindningen inte unik."""
        man = list(SEALED_DEPLOYABLE.values())
        self.assertEqual(len(man), len(set(man)))


class GateEnum(unittest.TestCase):
    """Sols punkt 3: gate-parametern är sluten."""

    def test_bada_tillatna_etiketterna_gar(self):
        for g in ("crash-detector", "deploy-context"):
            with self.subTest(g):
                with self.assertRaises(FailClosed) as cm:
                    sealed_manifest_for("b" * 64, gate=g)
                self.assertEqual(cm.exception.gate, g)

    def test_okand_gate_vagras(self):
        """En fri sträng hade låtit en anropare döpa om sin egen vägran till något
        en grind längre ut inte känner igen."""
        for g in ("", "whatever", "deploy_context", "DEPLOY-CONTEXT"):
            with self.subTest(g):
                with self.assertRaises(FailClosed) as cm:
                    sealed_manifest_for(SEALED_RECEPT_SHA256, gate=g)
                self.assertEqual(cm.exception.gate, "deploy-context")
                self.assertIn("okänd gate", str(cm.exception))


class MintNSteg(unittest.TestCase):
    """Sols punkt 1: mint_deploy_context tar N steg, inte exakt tre."""

    def steg(self, n):
        return tuple(
            fc.BoundStep(index=i, kind="remove_links", name=f"op{i}", recipe_id="prov", payload_sha256="0" * 64)
            for i in range(1, n + 1)
        )

    def _sigill(self):
        import shutil
        import tempfile

        td = Path(tempfile.mkdtemp(prefix="mint-"))
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        self.addCleanup(fc.clear_test_pin)
        qw, mv = td / "qwprogs.so", td / "mvdsv"
        qw.write_bytes(b"q")
        mv.write_bytes(b"m")
        fc.install_test_pin(qw, mv)
        return fc._issue_preflight_seal_from_files(
            manifest_path=RECEPT / "paav-g-v1.manifest.json",
            recept_path=RECEPT / "paav-g-v1.json",
            qwprogs_path=qw,
            mvdsv_path=mv,
        )

    def tearDown(self):
        fc.release_deploy_context() if hasattr(fc, "release_deploy_context") else None
        fc._active_session = None
        fc._preflight_ticket = None

    def test_ett_steg_gar(self):
        if not (RECEPT / "paav-g-v1.json").is_file():
            self.skipTest("recept saknas")
        ctx = fc.mint_deploy_context(self._sigill(), self.steg(1))
        self.assertEqual(len(ctx.steps), 1)

    def test_tre_steg_gar_fortfarande(self):
        if not (RECEPT / "paav-g-v1.json").is_file():
            self.skipTest("recept saknas")
        ctx = fc.mint_deploy_context(self._sigill(), self.steg(3))
        self.assertEqual(len(ctx.steps), 3)

    def test_noll_steg_vagras(self):
        if not (RECEPT / "paav-g-v1.json").is_file():
            self.skipTest("recept saknas")
        with self.assertRaises(FailClosed) as cm:
            fc.mint_deploy_context(self._sigill(), ())
        self.assertIn("minst ett", str(cm.exception))

    def test_hal_i_stegindex_vagras(self):
        if not (RECEPT / "paav-g-v1.json").is_file():
            self.skipTest("recept saknas")
        trasig = (self.steg(2)[0], fc.BoundStep(index=5, kind="x", name="y", recipe_id="p", payload_sha256="0" * 64))
        with self.assertRaises(FailClosed) as cm:
            fc.mint_deploy_context(self._sigill(), trasig)
        self.assertIn("sammanhängande", str(cm.exception))

    def test_steg_som_borjar_pa_noll_vagras(self):
        """Stegen är 1..N — index 0 är pin-steget och muteras aldrig."""
        if not (RECEPT / "paav-g-v1.json").is_file():
            self.skipTest("recept saknas")
        fran_noll = tuple(
            fc.BoundStep(index=i, kind="x", name=f"o{i}", recipe_id="p", payload_sha256="0" * 64)
            for i in range(0, 2)
        )
        with self.assertRaises(FailClosed):
            fc.mint_deploy_context(self._sigill(), fran_noll)


if __name__ == "__main__":
    unittest.main(verbosity=2)
