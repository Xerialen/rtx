#!/usr/bin/env python3
"""Enhetstester för transformator.py.

Två sorters test:

* **Syntetiska** — små grafer där varje regel går att se med blotta ögat.
  De pinnar semantiken: uppslagsordning, id-kompaktering, T-återuppståndelsen,
  ankarkontrollerna och vad som får bli en vägran.
* **Mot basdumpen** — reproducerar de tre förseglade delstamparna och kör
  komponatet. De hoppas över när dumpen inte finns, så sviten går att köra
  var som helst; men de är själva domen över semantiken.

Ingen riggkontakt.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
import graphstamp  # noqa: E402
import transformator as tr  # noqa: E402

BAS = Path("/home/xerial/lab/toolbox/dm3-base-full-graph.json")
RECEPT = HERE / "recept"


def graf(cells, links, map_name="dm3") -> tr.Graf:
    return tr.Graf(map_name, cells, links)


def lank(frm, to, kind="walk", t=1, **params):
    rec = {"from": frm, "to_cell": to, "kind": kind, "T": t}
    rec.update({k: v for k, v in params.items() if v is not None})
    return rec


class Uppslag(unittest.TestCase):
    """`nearest` / `cell_within` mot motorns loopar."""

    def test_nearest_tar_narmaste(self):
        g = graf([[0, 0, 0], [64, 0, 0], [32, 0, 0]], [])
        self.assertEqual(g.nearest((30, 0, 0)), 2)

    def test_nearest_forsta_minimipunkten_vinner_vid_lika(self):
        # Två celler i SAMMA gridkolumn på exakt samma avstånd. Motorn jämför med
        # strikt `<`, så den som besöks först behålls, och inom en kolumn är det
        # insättningsordningen. Ett `<=` här hade gett cell 1.
        g = graf([[4, 0, 0], [12, 0, 0]], [])
        self.assertEqual(g.nearest((8, 0, 0)), 0)

    def test_nearest_besoker_kolumner_inte_cell_id(self):
        # Samma avstånd, men i olika kolumner: cell 1 ligger i frågans egen kolumn
        # och hittas på radie 0, cell 0 först på radie 1. Kolumnordningen vinner
        # över cell-id — det är därför uppslaget måste spegla loopen och inte
        # bara "minsta avstånd".
        g = graf([[-16, 0, 0], [16, 0, 0]], [])
        self.assertEqual(g.nearest((0, 0, 0)), 1)

    def test_nearest_ger_none_pa_tom_graf(self):
        self.assertIsNone(graf([], []).nearest((0, 0, 0)))

    def test_cell_within_ar_bundet(self):
        g = graf([[0, 0, 0]], [])
        self.assertEqual(g.cell_within((6, 0, 0), 8.0, 8.0), 0)
        self.assertIsNone(g.cell_within((9, 0, 0), 8.0, 8.0), "utanför horisontalgränsen")
        self.assertIsNone(g.cell_within((0, 0, 9), 8.0, 8.0), "utanför vertikalgränsen")
        self.assertEqual(g.cell_within((0, 0, 9), 8.0, 48.0), 0, "vidare vertikalfönster tar den")


class RemoveOp(unittest.TestCase):
    def bas(self) -> tr.Graf:
        return graf(
            [[0, 0, 0], [32, 0, 0], [64, 0, 0]],
            [lank(0, 1), lank(0, 2, "step"), lank(1, 2)],
        )

    def test_ankaret_maste_halla(self):
        g = self.bas()
        fore = json.dumps(g.links)
        with self.assertRaises(tr.Vagran) as cm:
            tr.op_remove_links(g, {"name": "x", "links": [{"id": 1, "from": 0, "to": 2, "kind": "walk"}]})
        self.assertIn("ankaret håller inte", str(cm.exception))
        self.assertEqual(json.dumps(g.links), fore, "en vägran får inte mutera grafen")

    def test_okant_och_dubblerat_id_vagras(self):
        g = self.bas()
        with self.assertRaises(tr.Vagran):
            tr.op_remove_links(g, {"name": "x", "links": [{"id": 9, "from": 0, "to": 1, "kind": "walk"}]})
        with self.assertRaises(tr.Vagran) as cm:
            g.remove_links_by_id([0, 0])
        self.assertIn("dubblerat", str(cm.exception))

    def test_id_kompakteras(self):
        g = self.bas()
        tr.op_remove_links(g, {"name": "x", "links": [{"id": 0, "from": 0, "to": 1, "kind": "walk"}]})
        self.assertEqual(len(g.links), 2)
        # Gamla id 1 sitter nu på 0, gamla 2 på 1 — inget hål lämnas.
        self.assertEqual((g.links[0]["from"], g.links[0]["to_cell"]), (0, 2))
        self.assertEqual((g.links[1]["from"], g.links[1]["to_cell"]), (1, 2))

    def test_rensade_lankar_aterupp_star_i_adjacensen(self):
        """Motorns remove bygger om adjacensen från noll: T=0 blir T=1.

        Det är inte en modellförenkling utan `remove_links_by_id` -> `push_link`
        (mod.rs:640-646) plus att `rebuild_derived` inte kör teleport-rensningen om.
        Basdumptestet nedan är beviset: K2-ON:s förseglade nivå-2 reproduceras
        bara med det här beteendet.
        """
        g = graf([[0, 0, 0], [32, 0, 0], [64, 0, 0]], [lank(0, 1), lank(0, 2, "step", t=0), lank(1, 2)])
        bevis = tr.op_remove_links(g, {"name": "x", "links": [{"id": 0, "from": 0, "to": 1, "kind": "walk"}]})
        self.assertEqual(bevis["T0_fore"], 1)
        self.assertEqual(bevis["T0_efter"], 0)
        self.assertTrue(all(l["T"] == 1 for l in g.links))
        self.assertIn("traverserbara igen", bevis["not"])


class PlanLinkOp(unittest.TestCase):
    """Här avgörs V296-deltat."""

    def bas(self) -> tr.Graf:
        # Två celler långt isär, inga länkar mellan dem, och grannar tillräckligt
        # långt bort att snappen är avgjord med marginal.
        return graf([[96, -568, 296], [128, -704, 328], [0, 0, 0]], [lank(2, 0, "walk")])

    def op(self, **extra) -> dict:
        d = {
            "op": "plan_link",
            "name": "v296",
            "from": [107.0, -582.0, 296.0],
            "takeoff": [92.0, -588.0, 296.0],
            "tgt": [138.1, -701.0, 328.0],
            "v_req": 320.0,
            "gain": 5.5,
            "carried": True,
        }
        d.update(extra)
        return d

    def test_planterar_en_lank_och_ingen_cell(self):
        g = self.bas()
        celler, lankar = len(g.cells), len(g.links)
        bevis = tr.op_plan_link(g, self.op())
        self.assertEqual(len(g.cells) - celler, 0, "PlanLink-vägen har ingen plant_cell")
        self.assertEqual(len(g.links) - lankar, 1, "push_speed_jump lägger alltid till en länk")
        self.assertEqual(bevis["d_celler"], 0)
        self.assertEqual(g.links[-1]["kind"], "speedjump")
        self.assertEqual(g.links[-1]["T"], 1)

    def test_uteslutna_lasningar_ar_belagda_inte_pastadda(self):
        g = self.bas()
        bevis = tr.op_plan_link(g, self.op())
        self.assertEqual(bevis["befintliga_lankar_from_till_tgt"], [])
        self.assertIn("utesluten", bevis["uteslutna_lasningar"]["+0/+0 (carried-cert av befintlig länk)"])

    def test_befintlig_lank_rapporteras_i_stallet_for_att_gommas(self):
        """Finns länken redan är +0/+0 INTE utesluten — då ska beviset säga det."""
        g = self.bas()
        g.insert_link(0, 1, "speedjump")
        bevis = tr.op_plan_link(g, self.op())
        self.assertEqual(len(bevis["befintliga_lankar_from_till_tgt"]), 1)
        self.assertIn("EJ utesluten", bevis["uteslutna_lasningar"]["+0/+0 (carried-cert av befintlig länk)"])

    def test_ankaret_ar_korskontroll_inte_indata(self):
        g = self.bas()
        with self.assertRaises(tr.Vagran) as cm:
            tr.op_plan_link(g, self.op(anchor={"from_cell": 0, "to_cell": 99}))
        self.assertIn("korskontroll", str(cm.exception))

    def test_tunn_marginal_vagras_pga_trunkeringen(self):
        """Dumpen skriver cellorigin med int(). Är två celler nästan lika nära
        avgör inte datat vilken motorn väljer — då är en härledd stamp värdelös."""
        g = graf([[96, -568, 296], [97, -568, 296], [128, -704, 328]], [])
        with self.assertRaises(tr.Vagran) as cm:
            tr.op_plan_link(g, self.op())
        self.assertIn("trunkeringsfelet", str(cm.exception))

    def test_params_syns_i_niva2_men_inte_i_den_motorjamforbara(self):
        g = self.bas()
        tr.op_plan_link(g, self.op())
        ident = g.identitet([])
        self.assertNotEqual(
            ident["graph_content_hash"],
            ident["graph_content_hash_utan_params"],
            "carried/v_req/gain måste ändra receptets egen nivå-2",
        )
        # Den params-fria är den en motordump kan jämföras mot: carried finns inte
        # i Cmd::PlanLink och når därför aldrig motorns graf.
        utan = graphstamp.graph_content_hash(g._doc(False))
        self.assertEqual(ident["graph_content_hash_utan_params"], utan)

    def test_params_serialiseras_som_korskontraktet_kraver(self):
        """F1: gain 5.5 -> '5.50', v_req 320 -> '320'. Divergerar den bryts hashen."""
        rad = graphstamp.format_l_post(
            1167, 1191, "speedjump", 1,
            graphstamp.link_param_fields({"carried": True, "v_req": 320.0, "gain": 5.5}),
        )
        self.assertEqual(rad, "L\t1167\t1191\tspeedjump\t1\tcarried=1\tv_req=320\tgain=5.50")


class ShelfPatchOp(unittest.TestCase):
    def bas(self) -> tr.Graf:
        return graf([[0, 0, 0], [-72, -16, -144]], [])

    def test_celler_appendas_i_tabellordning(self):
        g = graf([[0, 0, 0]], [])
        tr.op_shelf_patch(g, {
            "name": "x", "no_auto_walk": True, "snap_z": 128.03125,
            "cells": [[-360, -784, 128.03125], [-360, -752, 128.03125]], "drops": [],
        })
        self.assertEqual(len(g.cells), 3)
        self.assertEqual(g.cells[1], [-360, -784, 128.03125])
        self.assertEqual(g.cells[2], [-360, -752, 128.03125])

    def test_befintlig_cell_hoppas_over(self):
        g = graf([[-360, -784, 128]], [])
        bevis = tr.op_shelf_patch(g, {
            "name": "x", "no_auto_walk": True, "snap_z": 128.03125,
            "cells": [[-360, -784, 128.03125]], "drops": [],
        })
        self.assertEqual(len(g.cells), 1)
        self.assertEqual(bevis["hoppade_celler"][0]["cell"], 0)

    def test_identiskt_drop_hoppas_over(self):
        g = graf([[0, 0, 0], [-72, -16, -144]], [lank(0, 1, "drop")])
        bevis = tr.op_shelf_patch(g, {
            "name": "x", "cells": [], "drops": [{"from": [0, 0, 0], "to": [-72, -16, -144]}],
        })
        self.assertEqual(len(g.links), 1)
        self.assertEqual(len(bevis["nya_drops"]), 0)
        self.assertEqual(len(bevis["hoppade_drops"]), 1)

    def test_auto_walk_vagras_i_stallet_for_att_gissas(self):
        g = graf([[0, 0, 0]], [])
        with self.assertRaises(tr.Vagran) as cm:
            tr.op_shelf_patch(g, {"name": "x", "cells": [[64, 0, 0]], "drops": []})
        self.assertIn("auto-Walk", str(cm.exception))

    def test_drop_ankare_maste_halla(self):
        g = self.bas()
        with self.assertRaises(tr.Vagran) as cm:
            tr.op_shelf_patch(g, {
                "name": "x", "cells": [],
                "drops": [{"from": [0, 0, 0], "to": [-72, -16, -144], "to_cell": 7}],
            })
        self.assertIn("ankare", str(cm.exception))

    def test_orackbart_drop_vagras(self):
        g = graf([[0, 0, 0]], [])
        with self.assertRaises(tr.Vagran) as cm:
            tr.op_shelf_patch(g, {
                "name": "x", "cells": [], "drops": [{"from": [0, 0, 0], "to": [9000, 9000, 0]}],
            })
        self.assertIn("träffar ingen cell", str(cm.exception))


class Korning(unittest.TestCase):
    def bas(self) -> tr.Graf:
        return graf([[0, 0, 0], [32, 0, 0]], [lank(0, 1), lank(1, 0)])

    def pin(self, g: tr.Graf) -> dict:
        i = g.identitet([])
        return {k: i[k] for k in ("cells", "links", "rj_links", "graph_stamp")}

    def test_pinfel_stoppar_allt(self):
        g = self.bas()
        recept = {"id": "x", "base": {"cells": 4711, "links": 2, "rj_links": 0}, "ops": []}
        with self.assertRaises(tr.Vagran) as cm:
            tr.kor_recept(g, recept, [])
        self.assertIn("Inget appliceras", str(cm.exception))

    def test_expect_som_inte_stammer_stoppar_kedjan(self):
        g = self.bas()
        recept = {
            "id": "x", "base": self.pin(g),
            "ops": [{
                "op": "remove_links", "name": "r",
                "links": [{"id": 0, "from": 0, "to": 1, "kind": "walk"}],
                "expect": {"d_links": -2},
            }],
        }
        with self.assertRaises(tr.Vagran) as cm:
            tr.kor_recept(g, recept, [])
        self.assertIn("d_links", str(cm.exception))

    def test_op_utan_expect_markeras_som_harledd(self):
        g = self.bas()
        recept = {
            "id": "x", "base": self.pin(g),
            "ops": [{"op": "remove_links", "name": "r",
                     "links": [{"id": 0, "from": 0, "to": 1, "kind": "walk"}]}],
        }
        man = tr.kor_recept(g, recept, [])
        self.assertTrue(man["steg"][1]["harledd"])
        self.assertEqual(man["steg"][1]["d_links"], -1)

    def test_okand_op_art_vagras(self):
        g = self.bas()
        recept = {"id": "x", "base": self.pin(g), "ops": [{"op": "trolla", "name": "?"}]}
        with self.assertRaises(tr.Vagran) as cm:
            tr.kor_recept(g, recept, [])
        self.assertIn("okänd art", str(cm.exception))

    def test_kallgrafen_lamnas_orord(self):
        g = self.bas()
        recept = {
            "id": "x", "base": self.pin(g),
            "ops": [{"op": "remove_links", "name": "r",
                     "links": [{"id": 0, "from": 0, "to": 1, "kind": "walk"}]}],
        }
        tr.kor_recept(g, recept, [])
        self.assertEqual(len(g.links), 2, "kor_recept arbetar på en kopia")

    def test_status_foljer_med_fran_receptet(self):
        g = self.bas()
        man = tr.kor_recept(g, {"id": "x", "status": "EJ-DEPLOY", "status_skal": "dom",
                                "base": self.pin(g), "ops": []}, [])
        self.assertEqual(man["status"], "EJ-DEPLOY")
        self.assertEqual(man["status_skal"], "dom")

    def test_status_utan_uppgift_ar_okand_inte_deploybar(self):
        g = self.bas()
        man = tr.kor_recept(g, {"id": "x", "base": self.pin(g), "ops": []}, [])
        self.assertEqual(man["status"], "OKAND", "tystnad får aldrig läsas som godkänd")

    def test_manifestet_ar_kanoniskt_och_stabilt(self):
        g = self.bas()
        recept = {"id": "x", "base": self.pin(g), "ops": []}
        a = tr.kanonisk_json(tr.kor_recept(g, recept, []))
        b = tr.kanonisk_json(tr.kor_recept(g, recept, []))
        self.assertEqual(a, b, "manifestet måste vara byte-stabilt för att kunna förseglas")


class Korskontroll(unittest.TestCase):
    """Nivå-1-krockar mellan två komponat mot samma bas."""

    def steg(self, index, name, cells, links, stamp, hash_):
        return {
            "index": index,
            "name": name,
            "identitet": {
                "cells": cells,
                "links": links,
                "graph_stamp": stamp,
                "graph_content_hash_utan_params": hash_,
            },
        }

    def test_krock_mot_annat_komponats_slutstamp_flaggas(self):
        mitt = [self.steg(0, "pin", 5977, 48207, "1", "a"), self.steg(2, "rail", 5983, 48214, "77", "b")]
        annat = {"recept_id": "k2-varianten",
                 "steg": [self.steg(0, "pin", 5977, 48207, "1", "a"),
                          self.steg(4, "prevent", 5983, 48214, "77", "c")]}
        w = tr.korskontrollera_manifest(mitt, annat)
        self.assertEqual(len(w), 1)
        self.assertIn("SLUTSTAMPEN", w[0])
        self.assertIn("k2-varianten", w[0])

    def test_samma_graf_ar_ingen_falla(self):
        """Samma nivå-1 OCH samma nivå-2 är samma graf — inget att varna för."""
        mitt = [self.steg(0, "pin", 5977, 48207, "1", "a"), self.steg(1, "x", 5983, 48214, "77", "b")]
        annat = {"recept_id": "annat",
                 "steg": [self.steg(0, "pin", 5977, 48207, "1", "a"),
                          self.steg(1, "y", 5983, 48214, "77", "b")]}
        self.assertEqual(tr.korskontrollera_manifest(mitt, annat), [])

    def test_pinsteget_jamfors_inte(self):
        """Båda komponaten pinnar samma bas — det är meningen, inte en krock."""
        mitt = [self.steg(0, "pin", 5977, 48207, "1", "a")]
        annat = {"recept_id": "annat", "steg": [self.steg(0, "pin", 5977, 48207, "1", "a")]}
        self.assertEqual(tr.korskontrollera_manifest(mitt, annat), [])

    def test_manifestsokvagen_ligger_bredvid_receptet(self):
        self.assertEqual(
            tr.manifestsokvag("recept/komponat-v296-ram.json").name,
            "komponat-v296-ram.manifest.json",
        )


@unittest.skipUnless(BAS.exists(), f"behöver basdumpen {BAS}")
class MotBasdumpen(unittest.TestCase):
    """Domen över semantiken: reproducerar de tre förseglade delstamparna?"""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(BAS.read_text(encoding="utf-8"))
        cls.bas = tr.Graf.from_dump(cls.doc)
        cls.reg = graphstamp.load_register()

    def test_basdumpens_egen_identitet(self):
        i = self.bas.identitet(self.reg)
        self.assertEqual((i["cells"], i["links"], i["rj_links"]), (5977, 48207, 0))
        self.assertEqual(i["graph_stamp"], "906595427771298736")
        self.assertEqual(
            i["graph_content_hash_utan_params"],
            "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9",
        )

    def test_link_ids_permuteras_till_motorns_id_rymd(self):
        """Dumpen listar länkar i cellordning; ankarkontrollen kräver motorns id."""
        l = self.bas.links[10447]
        self.assertEqual((l["from"], l["to_cell"], l["kind"]), (1416, 1461, "walk"))
        l = self.bas.links[10446]
        self.assertEqual((l["from"], l["to_cell"], l["kind"]), (1416, 1459, "walk"))

    def test_de_tre_forseglade_delstamparna_reproduceras(self):
        fel, utfall = tr.validera(self.bas, RECEPT, self.reg)
        for u in utfall:
            self.assertTrue(u["ok"], f"{u['recept']}: {u['avvikelser']}")
        self.assertEqual(fel, 0)

    def test_k2_niva2_kraver_att_de_15_aterupp_star(self):
        """Diskriminerande: med T bevarat blir K2-ON en annan graf.

        Om det här testet någonsin blir grönt åt båda håll har dumpen tappat
        sina T-flaggor och nivå-2 säger inget om adjacensen längre.
        """
        d = json.loads((RECEPT / "haz1462-k2.json").read_text(encoding="utf-8"))
        recept = {"id": "k2", "base": d["off"],
                  "ops": [{"op": "remove_links", "name": "k2", "links": d["remove_links"]}]}
        original = tr.T_ATERUPPSTAR
        try:
            tr.T_ATERUPPSTAR = True
            med = tr.kor_recept(self.bas, recept, self.reg)["slut"]
            tr.T_ATERUPPSTAR = False
            utan = tr.kor_recept(self.bas, recept, self.reg)["slut"]
        finally:
            tr.T_ATERUPPSTAR = original
        vantat = d["on_expected"]["graph_content_hash"]
        self.assertEqual(med["graph_content_hash_utan_params"], vantat)
        self.assertNotEqual(utan["graph_content_hash_utan_params"], vantat)

    def test_komponatet_ger_5983_48214_och_v296_plus_0_plus_1(self):
        recept = json.loads((RECEPT / "komponat-k2-v296-ram.json").read_text(encoding="utf-8"))
        man = tr.kor_recept(self.bas, recept, self.reg)
        v296 = next(s for s in man["steg"] if s["name"] == "v296-vasthoppet")
        self.assertTrue(v296["harledd"], "V296-deltat får inte komma ur ett expect i receptet")
        self.assertEqual((v296["d_cells"], v296["d_links"]), (0, 1))
        self.assertEqual(v296["bevis"]["from"]["cell"], 1167)
        self.assertEqual(v296["bevis"]["tgt"]["cell"], 1191)
        slut = man["slut"]
        self.assertEqual((slut["cells"], slut["links"]), (5983, 48214))
        self.assertEqual(slut["graph_stamp"], "15510284848814560699")

    def test_link_vid_cert_48131_ar_gift(self):
        """Fixturens cert-id pekar inte på 1167->1191 i basen — det får inte bli ankare.

        Id:t måste läsas i motorns id-rymd (via `link_ids`), inte som en position i
        dumpens array: array-plats 48131 är en helt annan länk. Båda är swims, så
        giftslutsatsen står — men id-rymderna får inte blandas ihop.
        """
        l = self.bas.links[48131]
        self.assertEqual((l["from"], l["to_cell"], l["kind"]), (5968, 5965, "swim"))
        self.assertNotEqual((l["from"], l["to_cell"]), (1167, 1191))
        arrayplats = self.doc["links"][48131]
        self.assertNotEqual(
            (arrayplats["from"], arrayplats["to_cell"]),
            (l["from"], l["to_cell"]),
            "arrayplats och motor-id är olika saker; testet finns för att hålla dem isär",
        )

    def test_inget_komponat_landar_pa_5983_48213_fallan(self):
        """Den förbjudna fällan: 5983/48213 är rail-ON:s FNV och skulle uppstå ur
        en felräknad V296 (+0/+0). Ingen av op-listorna får hamna där."""
        for namn in ("komponat-k2-v296-ram", "komponat-v296-ram"):
            with self.subTest(namn):
                recept = json.loads((RECEPT / f"{namn}.json").read_text(encoding="utf-8"))
                slut = tr.kor_recept(self.bas, recept, self.reg)["slut"]
                self.assertNotEqual((slut["cells"], slut["links"]), (5983, 48213))

    def test_deploykomponatets_slutbild_ar_ett_eget_namn(self):
        """Slutbilden får inte dela nivå-1 med någon känd graf i registret."""
        recept = json.loads((RECEPT / "komponat-v296-ram.json").read_text(encoding="utf-8"))
        slut = tr.kor_recept(self.bas, recept, self.reg)["slut"]
        self.assertEqual((slut["cells"], slut["links"]), (5983, 48216))
        self.assertIsNone(slut["kollision"], "slutbilden får inte landa på ett registrerat ON-namn")

    def test_k2_komponatets_slutbild_ar_registrerad_som_krock(self):
        """K2-komponatets SLUT delar nivå-1 med deploy-komponatets mellansteg.

        Den krocken hittade transformatorns egen korskontroll; grok2 skrev in den i
        kollisionsregistret (581e140). Att den nu ger en registerträff är rätt utfall
        — testet finns för att posten inte ska försvinna igen utan att någon märker det.
        """
        recept = json.loads((RECEPT / "komponat-k2-v296-ram.json").read_text(encoding="utf-8"))
        slut = tr.kor_recept(self.bas, recept, self.reg)["slut"]
        self.assertEqual((slut["cells"], slut["links"]), (5983, 48214))
        self.assertIsNotNone(slut["kollision"], "krocken måste vara registrerad")
        alias = " ".join(slut["kollision"].get("aliases") or [])
        self.assertIn("deploy-v296-ram", alias)
        self.assertIn("komponat-k2-v296-ram", alias)

    def test_deploykomponatet_utan_k2(self):
        """Op-listan Xerial beslutade om efter DOM M1-EFTER-OFF."""
        recept = json.loads((RECEPT / "komponat-v296-ram.json").read_text(encoding="utf-8"))
        man = tr.kor_recept(self.bas, recept, self.reg)
        self.assertEqual(man["status"], "DEPLOY-KANDIDAT")
        v296 = next(s for s in man["steg"] if s["name"] == "v296-vasthoppet")
        self.assertTrue(v296["harledd"])
        self.assertEqual((v296["d_cells"], v296["d_links"]), (0, 1))
        self.assertEqual((v296["identitet"]["cells"], v296["identitet"]["links"]), (5977, 48208))
        slut = man["slut"]
        self.assertEqual((slut["cells"], slut["links"]), (5983, 48216))
        self.assertEqual(slut["graph_stamp"], "11908727279900740725")
        self.assertIsNone(slut["kollision"], "slutbilden får inte landa på ett registrerat ON-namn")

    def test_k2_komponatet_ar_markt_ej_deploy(self):
        recept = json.loads((RECEPT / "komponat-k2-v296-ram.json").read_text(encoding="utf-8"))
        self.assertEqual(recept["status"], "EJ-DEPLOY")
        self.assertIn("M1-EFTER-OFF", recept["status_skal"])

    def test_railsteget_krockar_med_k2_komponatets_slutstamp(self):
        """Den fällan finns inte i registret och ingen av op-listorna ser den ensam.

        Deploy-komponatets steg 2 (efter rail) är 5983/48214 — exakt samma nivå-1
        som K2-komponatets FÄRDIGA bild. En grind som läser counts/FNV kan alltså
        inte skilja ett halvapplicerat deploy-komponat från ett färdigt K2-komponat.
        Nivå-2 skiljer dem, och varningen finns för att någon ska läsa rätt kolumn.
        """
        deploy = tr.kor_recept(
            self.bas, json.loads((RECEPT / "komponat-v296-ram.json").read_text(encoding="utf-8")), self.reg
        )
        k2 = tr.kor_recept(
            self.bas, json.loads((RECEPT / "komponat-k2-v296-ram.json").read_text(encoding="utf-8")), self.reg
        )
        rail = next(s for s in deploy["steg"] if s["name"] == "ram-rail-v2")
        self.assertEqual(rail["identitet"]["graph_stamp"], k2["slut"]["graph_stamp"])
        self.assertNotEqual(
            rail["identitet"]["graph_content_hash_utan_params"],
            k2["slut"]["graph_content_hash_utan_params"],
        )
        w = tr.korskontrollera_manifest(deploy["steg"], k2)
        self.assertEqual(len(w), 1)
        self.assertIn("SLUTSTAMPEN", w[0])

    def test_committade_manifest_ar_verktygets_utdata(self):
        """Den committade filen måste vara byte för byte det verktyget skriver.

        deepseeks korsreview av 2232fcc, punkt (iv): regenereringen var stabil, men
        den committade filen bar fält `kor_recept` inte emitterade, så "byte-stabil"
        var ett påstående ingen kunde pröva. Nu går skriv- och testvägen genom
        `bygg_manifest`, och det här testet binder repot till den. Går det sönder är
        rättningen att köra om verktyget och committa utdatat — aldrig att handredigera
        manifestet.
        """
        for namn in ("komponat-v296-ram", "komponat-k2-v296-ram"):
            with self.subTest(namn):
                receptvag = RECEPT / f"{namn}.json"
                recept = json.loads(receptvag.read_text(encoding="utf-8"))
                blob = tr.kanonisk_json(
                    tr.bygg_manifest(self.bas, recept, self.reg, receptvag, tr.DEFAULT_BAS)
                )
                self.assertEqual(
                    blob,
                    tr.manifestsokvag(receptvag).read_bytes(),
                    f"{namn}.manifest.json har drivit från verktygets utdata",
                )

    def test_komponatet_ar_deterministiskt(self):
        recept = json.loads((RECEPT / "komponat-k2-v296-ram.json").read_text(encoding="utf-8"))
        a = tr.kanonisk_json(tr.kor_recept(self.bas, recept, self.reg))
        b = tr.kanonisk_json(tr.kor_recept(self.bas, recept, self.reg))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
