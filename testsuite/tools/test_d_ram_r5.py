#!/usr/bin/env python3
"""RAM r5 — recipe-specific LED III + offline §4a rescore."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_ram_sjalvbevis import (  # noqa: E402
    FACIT_RAM_R5_SHA256,
    HARDKATALOG_SHA256,
    RAILKATALOG_SHA256,
    attribute_pair,
    catalog_corroborate,
    load_hardkatalog,
    load_recipe_catalog,
    main,
    raw_from_jsonl,
    rescore_kvitton,
)
from d_recipe import load_recipe  # noqa: E402
from verify_d_kvitto import verify  # noqa: E402

LAB_RAIL = Path.home() / "lab" / "ram-v4-sjalvbevis" / "ram-rail-v2"
LAB_PREV = Path.home() / "lab" / "ram-v4-sjalvbevis-prevent" / "ram-prevent"
TD_RAIL = HERE / "testdata" / "ram-r5" / "ram-rail-v2"
TD_PREV = HERE / "testdata" / "ram-r5" / "ram-prevent"
H1_GOAL = [-593.0, -677.0, -16.0]
H1_CELLS = [
    1372, 1319, 1267, 1216, 1191, 1160, 1129, 1096, 1063, 1028, 991, 952,
    909, 859, 806, 702, 670, 638, 4627, 581, 554, 530, 506, 478, 447, 403,
]
RAIL_802_ORIGIN = [-156.04, -774.04, 219.96]


def _live(root_lab: Path, root_td: Path, stem: str, suffix: str) -> Path:
    for root in (root_lab, root_td):
        p = root / f"{stem}{suffix}"
        if p.is_file():
            return p
    raise FileNotFoundError(f"{stem}{suffix}")


def _inject(root_lab: Path, root_td: Path, stem: str) -> dict:
    raw = raw_from_jsonl(_live(root_lab, root_td, stem, ".jsonl"))
    rec = json.loads(_live(root_lab, root_td, stem, ".json").read_text(encoding="utf-8"))
    ast = rec.get("astar") or rec
    raw["astar_before"] = ast.get("before") or {}
    raw["astar_after"] = ast.get("after") or {}
    return raw


def _rail_rows():
    rows, status = load_hardkatalog(HERE / "recept" / "ram-railkatalog.jsonl", expected_sha=RAILKATALOG_SHA256)
    assert status == "ok" and rows, status
    return rows


def _prevent_rows():
    rows, status = load_hardkatalog(HERE / "recept" / "ram-hardkatalog.jsonl", expected_sha=HARDKATALOG_SHA256)
    assert status == "ok" and rows, status
    return rows


def _gates(name: str) -> dict:
    return json.loads((HERE / "recept" / name).read_text(encoding="utf-8"))


class RecipeCatalogLoadTests(unittest.TestCase):
    def test_rail_pin_and_prevent_pin(self):
        rows, status = load_recipe_catalog("ram-rail-v2")
        self.assertEqual(status, "ok")
        self.assertTrue(any(r.get("id") == "RAIL-GK0001" for r in rows))
        rows_p, status_p = load_recipe_catalog("ram-prevent")
        self.assertEqual(status_p, "ok")
        self.assertTrue(any(r.get("id") == "RAM-GK0001" for r in rows_p))
        self.assertEqual(
            RAILKATALOG_SHA256,
            "567d2ca0813adcf0eeadf122f8075965021f1d4dfbceb9d30c69bda6671986f9",
        )
        self.assertEqual(
            HARDKATALOG_SHA256,
            "55a608426226522c45dd217c3741c9b9ff13d2d302e84511ffd810be506ad1fe",
        )
        self.assertEqual(
            FACIT_RAM_R5_SHA256,
            "088ee90971efe5574bc92c29e2491e645f947c0aa50cff01d6fbc408a1f4010e",
        )

    def test_sha_mismatch_unusable(self):
        rows, status = load_recipe_catalog("ram-rail-v2", expected_sha="00" * 32)
        self.assertIsNone(rows)
        self.assertEqual(status, "sha_mismatch")
        rows_p, status_p = load_recipe_catalog("ram-prevent", expected_sha="00" * 32)
        self.assertIsNone(rows_p)
        self.assertEqual(status_p, "sha_mismatch")


class CrossCatalogTests(unittest.TestCase):
    def test_rail_802_hits_rail_gk0001(self):
        hit = catalog_corroborate(
            _rail_rows(),
            recipe_id="ram-rail-v2",
            stratum="H1",
            off_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            on_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            live_cells=H1_CELLS,
            live_goal=H1_GOAL,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "RAIL-GK0001")
        self.assertEqual(hit["cell"], 802)

    def test_prevent_never_uses_rail_catalog(self):
        hit = catalog_corroborate(
            _rail_rows(),
            recipe_id="ram-prevent",
            stratum="H1",
            off_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            on_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            live_cells=H1_CELLS,
            live_goal=H1_GOAL,
        )
        self.assertIsNone(hit)

    def test_rail_never_uses_prevent_catalog(self):
        hit = catalog_corroborate(
            _prevent_rows(),
            recipe_id="ram-rail-v2",
            stratum="H1",
            off_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            on_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            live_cells=H1_CELLS,
            live_goal=H1_GOAL,
        )
        self.assertIsNone(hit)

    def test_knockback_still_never(self):
        hit = catalog_corroborate(
            _rail_rows(),
            recipe_id="ram-rail-v2",
            stratum="K3",
            off_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            on_ev={"ev": "peak_drop_150", "cell": 802, "origin": list(RAIL_802_ORIGIN)},
            live_cells=H1_CELLS,
            live_goal=H1_GOAL,
        )
        self.assertIsNone(hit)


class LiveFormedAttributionTests(unittest.TestCase):
    def test_rail_h1_01_attributes_rail_gk0001(self):
        off_raw = _inject(LAB_RAIL, TD_RAIL, "H1-OFF-01")
        on_raw = _inject(LAB_RAIL, TD_RAIL, "H1-ON-01")
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="H1",
            off_id="H1-OFF-01",
            on_id="H1-ON-01",
            recipe=load_recipe(HERE / "recept" / "ram-rail-v2.json"),
            avsett=_gates("ram-prevent-gates.json")["avsett_drop"],
            clusters=[],
            catalog=_rail_rows(),
            live_goal=H1_GOAL,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(all(result["legs"].values()), result["legs"])
        self.assertEqual(result["post"]["catalog_row"]["id"], "RAIL-GK0001")
        self.assertEqual(result["post"]["catalog_row"]["cell"], 802)

    def test_prevent_live_does_not_take_rail_row(self):
        off_raw = _inject(LAB_PREV, TD_PREV, "H1-OFF-03")
        on_raw = _inject(LAB_PREV, TD_PREV, "H1-ON-03")
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="H1",
            off_id="H1-OFF-03",
            on_id="H1-ON-03",
            recipe=load_recipe(HERE / "recept" / "ram-prevent.json"),
            avsett=_gates("ram-prevent-gates.json")["avsett_drop"],
            clusters=[],
            catalog=_rail_rows(),
            live_goal=H1_GOAL,
        )
        self.assertIsNone(result["post"]["catalog_row"])
        self.assertFalse(result["legs"]["m1"])
        self.assertFalse(result["ok"])

    def test_rail_live_does_not_take_prevent_row(self):
        off_raw = _inject(LAB_RAIL, TD_RAIL, "H1-OFF-01")
        on_raw = _inject(LAB_RAIL, TD_RAIL, "H1-ON-01")
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="H1",
            off_id="H1-OFF-01",
            on_id="H1-ON-01",
            recipe=load_recipe(HERE / "recept" / "ram-rail-v2.json"),
            avsett=_gates("ram-prevent-gates.json")["avsett_drop"],
            clusters=[],
            catalog=_prevent_rows(),
            live_goal=H1_GOAL,
        )
        self.assertIsNone(result["post"]["catalog_row"])
        self.assertFalse(result["legs"]["m1"])


class OfflineRescoreTests(unittest.TestCase):
    def _stage(self, src: Path, stems: list[str]) -> Path:
        td = Path(tempfile.mkdtemp(prefix="ram-r5-rescore-"))
        for stem in stems:
            src_json = src / f"{stem}.json"
            src_jsonl = src / f"{stem}.jsonl"
            if not src_json.is_file():
                src_json = _live(src, src, stem, ".json")
            if not src_jsonl.is_file():
                src_jsonl = _live(src, src, stem, ".jsonl")
            dst_jsonl = td / f"{stem}.jsonl"
            shutil.copy2(src_jsonl, dst_jsonl)
            doc = json.loads(src_json.read_text(encoding="utf-8"))
            doc["raw_pointer"] = str(dst_jsonl)
            (td / f"{stem}.json").write_text(
                json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return td

    def test_rescore_rail_802_ledger(self):
        src = LAB_RAIL if (LAB_RAIL / "H1-OFF-01.json").is_file() else TD_RAIL
        td = self._stage(src, ["H1-OFF-01", "H1-ON-01"])
        before = {p.name: p.read_bytes() for p in td.iterdir()}
        ledger = td / "r5-ledger.jsonl"
        result = rescore_kvitton(td, recipe_id="ram-rail-v2", ledger_path=ledger)
        self.assertTrue(ledger.is_file())
        self.assertEqual(result["header"]["facit_ram_r5_sha256"], FACIT_RAM_R5_SHA256)
        self.assertEqual(result["header"]["prevent_katalog_sha256"], HARDKATALOG_SHA256)
        self.assertEqual(result["header"]["rail_katalog_sha256"], RAILKATALOG_SHA256)
        self.assertEqual(result["header"]["catalog_status"], "ok")
        self.assertEqual(len(result["episodes"]), 1)
        ep = result["episodes"][0]
        self.assertTrue(ep["attributed"], ep)
        self.assertEqual(ep["catalog_row"]["id"], "RAIL-GK0001")
        self.assertEqual(ep["catalog_row"]["cell"], 802)
        self.assertTrue(all(ep["legs"].values()), ep["legs"])
        self.assertEqual(len(ep["off_kvitto_sha256"]), 64)
        self.assertEqual(len(ep["on_kvitto_sha256"]), 64)
        after = {p.name: p.read_bytes() for p in td.iterdir() if p.name != "r5-ledger.jsonl"}
        self.assertEqual(before, after)
        with self.assertRaises(FileExistsError):
            rescore_kvitton(td, recipe_id="ram-rail-v2", ledger_path=ledger)

    def test_rescore_verify_error_refuses(self):
        src = LAB_RAIL if (LAB_RAIL / "H1-OFF-01.json").is_file() else TD_RAIL
        td = self._stage(src, ["H1-OFF-01", "H1-ON-01"])
        bad = json.loads((td / "H1-ON-01.json").read_text(encoding="utf-8"))
        bad["schema"] = "tampered"
        (td / "H1-ON-01.json").write_text(json.dumps(bad, indent=2, sort_keys=True) + "\n")
        self.assertTrue(verify(bad))
        orig_off = (td / "H1-OFF-01.jsonl").read_bytes()
        ledger = td / "refused.jsonl"
        result = rescore_kvitton(td, recipe_id="ram-rail-v2", ledger_path=ledger)
        ep = result["episodes"][0]
        self.assertEqual(ep["refused"], "verify_failed")
        self.assertFalse(ep["attributed"])
        self.assertIsNone(ep["legs"])
        self.assertIsNone(ep["catalog_row"])
        self.assertEqual((td / "H1-OFF-01.jsonl").read_bytes(), orig_off)

    def test_rescore_sha_mismatch_m1_only(self):
        src = LAB_RAIL if (LAB_RAIL / "H1-OFF-01.json").is_file() else TD_RAIL
        td = self._stage(src, ["H1-OFF-01", "H1-ON-01"])
        ledger = td / "mismatch.jsonl"
        result = rescore_kvitton(
            td,
            recipe_id="ram-rail-v2",
            ledger_path=ledger,
            catalog_sha="00" * 32,
        )
        self.assertEqual(result["header"]["catalog_status"], "sha_mismatch")
        ep = result["episodes"][0]
        self.assertFalse(ep["attributed"])
        self.assertIsNone(ep["catalog_row"])
        self.assertFalse(ep["legs"]["m1"])

    def test_cli_rescore_no_port(self):
        src = LAB_RAIL if (LAB_RAIL / "H1-OFF-01.json").is_file() else TD_RAIL
        td = self._stage(src, ["H1-OFF-01", "H1-ON-01"])
        ledger = td / "cli.jsonl"
        rc = main([
            "--rescore",
            "--recipe", "ram-rail-v2",
            "--kvitto-dir", str(td),
            "--ledger", str(ledger),
        ])
        self.assertEqual(rc, 0)
        lines = ledger.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        ep = json.loads(lines[1])
        self.assertEqual(header["schema"], "verktygslada/ram-r5-attribution-ledger/1")
        self.assertEqual(ep["catalog_row"]["id"], "RAIL-GK0001")

    def test_prevent_rescore_never_rail_row(self):
        src = LAB_PREV if (LAB_PREV / "H1-OFF-03.json").is_file() else TD_PREV
        td = self._stage(src, ["H1-OFF-03", "H1-ON-03"])
        ledger = td / "prev.jsonl"
        result = rescore_kvitton(td, recipe_id="ram-prevent", ledger_path=ledger)
        ep = result["episodes"][0]
        self.assertTrue(ep["attributed"], ep)
        self.assertIsNotNone(ep["catalog_row"])
        self.assertTrue(str(ep["catalog_row"]["id"]).startswith("RAM-GK"))
        self.assertFalse(str(ep["catalog_row"]["id"]).startswith("RAIL-"))


if __name__ == "__main__":
    unittest.main()
