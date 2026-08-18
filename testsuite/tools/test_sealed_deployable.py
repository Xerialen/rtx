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
    "paav-g-v1": ("98e9612048d4837df2dc7f165c9bf57537fcbda31f8b92a333812c5e3b224739",
                  "07cee24ed8cca3223c78ad8d4521b99266a5c7f892c16333ce8eed3494f447a8"),
    "paav-f-v1": ("96becff3a9cede458b9f6ada261f87e9fadfe6cb9f7dca04b81f26bd1cf57a41",
                  "1c9692eb4f886bc5112954c3e504f8beea8d5abe0eb6579465a877923dc0b0a9"),
    "paav-o-v1": ("12f28cf1e3189a2450d357b92f950112eb02cae15331ee5738b21e7e91fb2698",
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
    def test_okant_recept_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            sealed_manifest_for("a" * 64)
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertIn("står inte i den förseglade mängden", str(cm.exception))

    def test_ratt_recept_fel_manifest_vagras(self):
        """Kärnan: ett giltigt recept parat med ett ANNAT giltigt manifest.

        Båda byten är förseglade var för sig; kombinationen är det inte. Utan
        parbindningen hade två oberoende 'finns i mängden'-kontroller släppt
        igenom vilken korsning som helst.
        """
        g_rec = PAAV["paav-g-v1"][0]
        f_man = PAAV["paav-f-v1"][1]
        self.assertNotEqual(sealed_manifest_for(g_rec), f_man)
        # och v296:s manifest hör inte till paav-g heller
        self.assertNotEqual(sealed_manifest_for(g_rec), SEALED_MANIFEST_SHA256)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
