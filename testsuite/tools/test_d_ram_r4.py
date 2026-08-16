#!/usr/bin/env python3
"""RAM r4 §4a LED III — härdkatalog as second corroboration source."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_ram_sjalvbevis import (  # noqa: E402
    HARDKATALOG_SHA256,
    attribute_pair,
    catalog_corroborate,
    load_hardkatalog,
    raw_from_jsonl,
)
from d_recipe import load_recipe  # noqa: E402

LAB = Path.home() / "lab" / "ram-v3-sjalvbevis"
TD = HERE / "testdata" / "ram-r4"
CATALOG = HERE / "recept" / "ram-hardkatalog.jsonl"
H2_GOAL = [-593.0, -677.0, -16.0]
H2_CELLS = [
    1372, 1319, 1267, 1216, 1191, 1160, 1129, 1096, 1063, 1028, 991, 952,
    909, 859, 806, 702, 670, 638, 4627, 581, 554, 530, 506, 478, 447, 403,
]


def _live(stem: str, suffix: str) -> Path:
    for root in (LAB, TD):
        p = root / f"{stem}{suffix}"
        if p.is_file():
            return p
    raise FileNotFoundError(stem + suffix)


def _inject(stem: str) -> dict:
    raw = raw_from_jsonl(_live(stem, ".jsonl"))
    rec = json.loads(_live(stem, ".astar.json").read_text(encoding="utf-8"))
    ast = rec.get("astar") or rec
    raw["astar_before"] = ast.get("before") or {}
    raw["astar_after"] = ast.get("after") or {}
    return raw


def _catalog():
    rows, status = load_hardkatalog(CATALOG)
    assert status == "ok" and rows, status
    return rows


class HardkatalogLoadTests(unittest.TestCase):
    def test_pinned_sha_matches(self):
        rows, status = load_hardkatalog(CATALOG)
        self.assertEqual(status, "ok")
        self.assertEqual(len(rows), 18)
        self.assertEqual(HARDKATALOG_SHA256, (
            "55a608426226522c45dd217c3741c9b9ff13d2d302e84511ffd810be506ad1fe"
        ))

    def test_sha_mismatch_unusable(self):
        rows, status = load_hardkatalog(CATALOG, expected_sha="00" * 32)
        self.assertIsNone(rows)
        self.assertEqual(status, "sha_mismatch")


class CatalogMatchTests(unittest.TestCase):
    def test_mixed_704_hit(self):
        hit = catalog_corroborate(
            _catalog(),
            recipe_id="ram-prevent",
            stratum="H2",
            off_ev={"ev": "bot_stall", "cell": 704, "origin": [-279.47, -623.77, -16.0]},
            on_ev={"ev": "bot_stall", "cell": 704, "origin": [-279.48, -623.97, -16.0]},
            live_cells=H2_CELLS,
            live_goal=H2_GOAL,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "RAM-GK0006")
        self.assertEqual(hit["cell"], 704)

    def test_635_off_only_never(self):
        hit = catalog_corroborate(
            _catalog(),
            recipe_id="ram-prevent",
            stratum="H1",
            off_ev={"ev": "peak_drop_150", "cell": 635, "origin": [-352.0, -777.5, -6.1]},
            on_ev={"ev": "peak_drop_150", "cell": 635, "origin": [-352.0, -777.5, -6.1]},
            live_cells=H2_CELLS,
            live_goal=H2_GOAL,
        )
        self.assertIsNone(hit)

    def test_knockback_never(self):
        hit = catalog_corroborate(
            _catalog(),
            recipe_id="ram-rail-v2",
            stratum="K3",
            off_ev={"ev": "bot_stall", "cell": 636, "origin": [-360.0, -728.5, 128.0]},
            on_ev={"ev": "bot_stall", "cell": 636, "origin": [-360.0, -728.5, 128.0]},
            live_cells=H2_CELLS,
            live_goal=H2_GOAL,
        )
        self.assertIsNone(hit)
        hit2 = catalog_corroborate(
            _catalog(),
            recipe_id="ram-prevent",
            stratum="K3",
            off_ev={"ev": "bot_stall", "cell": 704, "origin": [-279.5, -623.8, -16.0]},
            on_ev={"ev": "bot_stall", "cell": 704, "origin": [-279.5, -623.8, -16.0]},
            live_cells=H2_CELLS,
            live_goal=H2_GOAL,
        )
        self.assertIsNone(hit2)

    def test_cell_unknown_path(self):
        origin = [-279.47, -623.77, -16.0]
        hit = catalog_corroborate(
            _catalog(),
            recipe_id="ram-prevent",
            stratum="H2",
            off_ev={"ev": "bot_stall", "cell": None, "origin": origin},
            on_ev={"ev": "bot_stall", "cell": None, "origin": list(origin)},
            live_cells=H2_CELLS,
            live_goal=H2_GOAL,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["via"], "catalog_cell_unknown")

    def test_cell_unknown_rejects_nearest_substitution(self):
        # one arm still stamped — must not snap to catalog cell 704
        hit = catalog_corroborate(
            _catalog(),
            recipe_id="ram-prevent",
            stratum="H2",
            off_ev={"ev": "bot_stall", "cell": None, "origin": [-279.47, -623.77, -16.0]},
            on_ev={"ev": "bot_stall", "cell": 704, "origin": [-279.48, -623.97, -16.0]},
            live_cells=H2_CELLS,
            live_goal=H2_GOAL,
        )
        self.assertIsNone(hit)


class LiveFormedAttributionTests(unittest.TestCase):
    def test_h2_mixed_catalog_attributes_without_m1(self):
        off_raw, on_raw = _inject("H2-OFF-04"), _inject("H2-ON-04")
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="H2",
            off_id="H2-OFF-04",
            on_id="H2-ON-04",
            recipe=load_recipe(HERE / "recept" / "ram-prevent.json"),
            avsett=json.loads((HERE / "recept" / "ram-prevent-gates.json").read_text())["avsett_drop"],
            clusters=[],
            off_receipt=json.loads(_live("H2-OFF-04", ".astar.json").read_text()),
            on_receipt=json.loads(_live("H2-ON-04", ".astar.json").read_text()),
            catalog=_catalog(),
            live_goal=H2_GOAL,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(all(result["legs"].values()), result["legs"])
        self.assertEqual(result["post"]["catalog_row"]["id"], "RAM-GK0006")
        self.assertEqual(result["post"]["catalog_row"]["cell"], 704)

    def test_h1_635_off_only_does_not_attribute_via_catalog(self):
        off_raw = _inject("H1-OFF-03")
        # Inject a synthetic ON 635 so LED I could pass — catalog row is OFF-only.
        on_raw = {
            "events": [{
                "ev": "peak_drop_150",
                "cell": 635,
                "origin": [-351.97, -780.30, -12.82],
                "t": 2.6856,
            }],
            "astar_before": off_raw.get("astar_before"),
            "astar_after": off_raw.get("astar_after"),
        }
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="H1",
            off_id="H1-OFF-03",
            on_id="H1-ON-635fake",
            recipe=load_recipe(HERE / "recept" / "ram-prevent.json"),
            avsett=json.loads((HERE / "recept" / "ram-prevent-gates.json").read_text())["avsett_drop"],
            clusters=[],
            catalog=_catalog(),
            live_goal=H2_GOAL,
        )
        self.assertFalse(result["legs"]["m1"], result)
        self.assertIsNone(result["post"]["catalog_row"])
        self.assertFalse(result["ok"])

    def test_knockback_live_not_catalog(self):
        off_raw, on_raw = _inject("K3-OFF-01"), _inject("K3-ON-01")
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="K3",
            off_id="K3-OFF-01",
            on_id="K3-ON-01",
            recipe=load_recipe(HERE / "recept" / "ram-rail-v2.json"),
            avsett=None,
            clusters=[],
            catalog=_catalog(),
            live_goal=None,
        )
        self.assertIsNone(result["post"]["catalog_row"])
        self.assertFalse(result["legs"]["m1"])


if __name__ == "__main__":
    unittest.main()
