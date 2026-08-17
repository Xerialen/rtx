#!/usr/bin/env python3
"""Offline-tester för dump-L-poster + stamp. Ingen riggkontakt."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
import graphstamp  # noqa: E402

# Kontrakt §8.4 — oförändrade /1-minifixturer (inga params).
MINI_BOTH_T1 = (
    "C\t10\t0\t0\t0\n"
    "C\t11\t32\t0\t0\n"
    "L\t10\t11\twalk\t1\n"
    "L\t11\t10\twalk\t1"
)
MINI_BOTH_T1_SHA = "6d8af07e9580a26c19959861e21d295b95995d903fada013c4c4e54e142beeaf"
MINI_ONE_T0 = (
    "C\t10\t0\t0\t0\n"
    "C\t11\t32\t0\t0\n"
    "L\t10\t11\twalk\t1\n"
    "L\t11\t10\twalk\t0"
)
MINI_ONE_T0_SHA = "6819c5bea29a4d690db502c8ef3186154dacb254548de82aa7a5ecd883a76c02"

BASE_DUMP = Path.home() / "lab/toolbox/dm3-base-full-graph.json"
BASE_NIVA2 = "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9"


def _mini_doc(t_back: int, extra: dict | None = None) -> dict:
    back = {"from": 11, "to_cell": 10, "kind": "walk", "T": t_back}
    if extra:
        back.update(extra)
    return {
        "schema": "qw-nav-graph/1",
        "map": "dm3",
        "cell_ids": [10, 11],
        "cells": [[0, 0, 0], [32, 0, 0]],
        "links": [
            {"from": 10, "to_cell": 11, "kind": "walk", "T": 1},
            back,
        ],
        "rj_links": 0,
    }


class InventoryCompatTests(unittest.TestCase):
    def test_mini_fixtures_unchanged(self):
        doc = _mini_doc(1)
        inv = graphstamp.canonical_inventory(doc)
        self.assertEqual(inv.decode("utf-8"), MINI_BOTH_T1)
        self.assertEqual(graphstamp.graph_content_hash(doc), MINI_BOTH_T1_SHA)

        doc0 = _mini_doc(0)
        inv0 = graphstamp.canonical_inventory(doc0)
        self.assertEqual(inv0.decode("utf-8"), MINI_ONE_T0)
        self.assertEqual(graphstamp.graph_content_hash(doc0), MINI_ONE_T0_SHA)

    def test_missing_params_omitted_same_bytes(self):
        bare = _mini_doc(1)
        with_nulls = _mini_doc(
            1, {"carried": None, "v_req": None, "gain": None}
        )
        # Nycklar med null räknas som saknade.
        self.assertEqual(
            graphstamp.canonical_inventory(bare),
            graphstamp.canonical_inventory(with_nulls),
        )
        self.assertEqual(
            graphstamp.graph_content_hash(bare), MINI_BOTH_T1_SHA
        )
        self.assertEqual(
            graphstamp.graph_content_hash(with_nulls), MINI_BOTH_T1_SHA
        )

    def test_params_change_hash(self):
        bare = _mini_doc(1)
        flagged = _mini_doc(
            1, {"carried": True, "v_req": 320.0, "gain": 5.5}
        )
        inv = graphstamp.canonical_inventory(flagged).decode("utf-8")
        self.assertIn("L\t11\t10\twalk\t1\tcarried=1\tv_req=320\tgain=5.50", inv)
        self.assertNotEqual(
            graphstamp.graph_content_hash(bare),
            graphstamp.graph_content_hash(flagged),
        )
        # Den andra L-posten (utan params) är oförändrad.
        self.assertIn("L\t10\t11\twalk\t1\n", inv)

    def test_partial_params_named_not_positional(self):
        only_gain = _mini_doc(1, {"gain": 5.5})
        only_carried = _mini_doc(1, {"carried": True})
        inv_g = graphstamp.canonical_inventory(only_gain).decode("utf-8")
        inv_c = graphstamp.canonical_inventory(only_carried).decode("utf-8")
        self.assertIn("gain=5.50", inv_g)
        self.assertNotIn("carried=", inv_g)
        self.assertNotIn("v_req=", inv_g)
        self.assertIn("carried=1", inv_c)
        self.assertNotIn("gain=", inv_c)
        self.assertNotEqual(
            graphstamp.graph_content_hash(only_gain),
            graphstamp.graph_content_hash(only_carried),
        )

    def test_explicit_carried_false_is_not_missing(self):
        bare = _mini_doc(1)
        off = _mini_doc(1, {"carried": False})
        inv = graphstamp.canonical_inventory(off).decode("utf-8")
        self.assertIn("carried=0", inv)
        self.assertNotEqual(
            graphstamp.graph_content_hash(bare),
            graphstamp.graph_content_hash(off),
        )

    def test_dump_link_record_passthrough(self):
        rec = graphstamp.dump_link_record(
            1167,
            {
                "to_cell": 1191,
                "kind": "SpeedJump",
                "carried": True,
                "v_req": 320,
                "gain": 5.5,
            },
            t=1,
        )
        self.assertEqual(rec["from"], 1167)
        self.assertEqual(rec["to_cell"], 1191)
        self.assertEqual(rec["kind"], "speedjump")
        self.assertEqual(rec["T"], 1)
        self.assertTrue(rec["carried"])
        self.assertEqual(rec["v_req"], 320)
        self.assertEqual(rec["gain"], 5.5)
        bare = graphstamp.dump_link_record(
            1167, {"to_cell": 1191, "kind": "walk"}, t=1
        )
        self.assertNotIn("carried", bare)
        self.assertNotIn("v_req", bare)
        self.assertNotIn("gain", bare)

    @unittest.skipUnless(BASE_DUMP.is_file(), "basdump saknas på den här maskinen")
    def test_real_base_dump_hash_unchanged(self):
        doc = json.loads(BASE_DUMP.read_text(encoding="utf-8"))
        self.assertEqual(graphstamp.graph_content_hash(doc), BASE_NIVA2)
        # Inga params i dagens dump → L-poster utan suffix.
        inv = graphstamp.canonical_inventory(doc)
        self.assertNotIn(b"carried=", inv)
        self.assertNotIn(b"v_req=", inv)
        self.assertNotIn(b"gain=", inv)


class StampFnvTests(unittest.TestCase):
    def test_known_fnv(self):
        self.assertEqual(graphstamp.graph_stamp("dm3", 5977, 48207, 0), 906595427771298736)
        self.assertEqual(graphstamp.graph_stamp("dm3", 5977, 48205, 0), 14344244513446609626)
        self.assertEqual(graphstamp.graph_stamp("dm3", 5983, 48213, 0), 8774822664048001128)
        self.assertEqual(graphstamp.graph_stamp("dm3", 5983, 48214, 0), 15510284848814560699)
        self.assertEqual(graphstamp.graph_stamp("dm3", 5978, 48208, 0), 13090435456435551592)


class KollisionTests(unittest.TestCase):
    def setUp(self):
        self.reg = graphstamp.load_register()

    def test_register_has_the_named_collisions(self):
        keys = {(e["cells"], e["links"]) for e in self.reg}
        self.assertEqual(keys, {
            (5977, 48207), (5983, 48213), (5977, 48205), (5983, 48214),
        })

    def test_warns_on_base_counts(self):
        hit = graphstamp.match_kollision(5977, 48207, 0, 906595427771298736, self.reg)
        self.assertIsNotNone(hit)
        text = graphstamp.warn_kollision(hit)
        self.assertIn("VARNING:", text)
        self.assertIn("bas", text)
        self.assertIn("K2+prevent", text)
        self.assertIn("K3-retype", text)

    def test_warns_on_rail_on_counts(self):
        hit = graphstamp.match_kollision(5983, 48213, 0, 8774822664048001128, self.reg)
        self.assertIsNotNone(hit)
        text = graphstamp.warn_kollision(hit)
        self.assertIn("rail-ON", text)
        self.assertIn("komponat-med-fel-V296", text)

    def test_warns_on_k2_on_counts(self):
        hit = graphstamp.match_kollision(5977, 48205, 0, 14344244513446609626, self.reg)
        self.assertIsNotNone(hit)
        text = graphstamp.warn_kollision(hit)
        self.assertIn("K2-ON", text)
        self.assertIn("K2+V296-om-+0/+0", text)

    def test_warns_on_5983_48214_halv_vs_k2slut(self):
        hit = graphstamp.match_kollision(5983, 48214, 0, 15510284848814560699, self.reg)
        self.assertIsNotNone(hit)
        text = graphstamp.warn_kollision(hit)
        self.assertIn("VARNING:", text)
        self.assertIn("5983/48214", text)
        self.assertIn("15510284848814560699", text)
        self.assertIn("HALVAPPLICERAT", text)
        self.assertIn("deploy-v296-ram op2", text)
        self.assertIn("komponat-k2-v296-ram SLUT", text)
        self.assertIn("EJ-DEPLOY", text)
        self.assertIn("nivå-2", text)
        self.assertIn("cab98ac2", text)
        self.assertIn("7a98d6e9", text)

    def test_no_warn_on_planlink_counts(self):
        # V296-apply +0/+1 efter K2: 5977/48206 — inte i registret.
        stamp = graphstamp.graph_stamp("dm3", 5977, 48206, 0)
        self.assertIsNone(
            graphstamp.match_kollision(5977, 48206, 0, stamp, self.reg)
        )

    def test_cli_warns_plaintext_on_stderr(self):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = graphstamp.main(["--cells", "5977", "--links", "48207"])
        self.assertEqual(rc, 0)
        self.assertIn("nivå-1 906595427771298736", buf_out.getvalue())
        self.assertIn("VARNING:", buf_err.getvalue())
        self.assertIn("5977/48207", buf_err.getvalue())

    def test_cli_dump_hash_and_collision(self):
        doc = {
            "schema": "qw-nav-graph/1",
            "map": "dm3",
            "cell_ids": list(range(5977)),
            "cells": [[0, 0, 0]] * 5977,
            "links": [
                {"from": 0, "to_cell": 1, "kind": "walk", "T": 1}
            ]
            * 48207,
            "rj_links": 0,
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fake.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                rc = graphstamp.main([str(path)])
        self.assertEqual(rc, 0)
        out = buf_out.getvalue()
        self.assertIn("cells 5977", out)
        self.assertIn("links 48207", out)
        self.assertIn("nivå-1 906595427771298736", out)
        self.assertIn("nivå-2 ", out)
        self.assertIn("VARNING:", buf_err.getvalue())
        # Inventeringen är inte basens — nivå-2 ska INTE vara gyllene.
        niva2 = [ln for ln in out.splitlines() if ln.startswith("nivå-2 ")][0]
        self.assertNotIn(BASE_NIVA2, niva2)

    def test_cli_no_warn_unknown_counts(self):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = graphstamp.main(["--cells", "1", "--links", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf_err.getvalue(), "")
        self.assertTrue(buf_out.getvalue().startswith("nivå-1 "))

    def test_cli_warns_on_5983_48214(self):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = graphstamp.main(["--cells", "5983", "--links", "48214"])
        self.assertEqual(rc, 0)
        self.assertIn("nivå-1 15510284848814560699", buf_out.getvalue())
        self.assertIn("VARNING:", buf_err.getvalue())
        self.assertIn("HALVAPPLICERAT", buf_err.getvalue())


class TwoGraphsSameCountsDifferentParams(unittest.TestCase):
    def test_v296_flag_only_differs_at_niva2(self):
        cells = [[96, -568, 296], [128, -704, 328]]
        ids = [1167, 1191]
        hop = {"from": 1167, "to_cell": 1191, "kind": "speedjump", "T": 1}
        off = {
            "map": "dm3",
            "cell_ids": ids,
            "cells": cells,
            "links": [dict(hop)],
            "rj_links": 0,
        }
        on = {
            "map": "dm3",
            "cell_ids": ids,
            "cells": cells,
            "links": [
                dict(hop, carried=True, v_req=320.0, gain=5.5)
            ],
            "rj_links": 0,
        }
        c_off = graphstamp.counts_from_doc(off)
        c_on = graphstamp.counts_from_doc(on)
        self.assertEqual(c_off, c_on)
        self.assertEqual(
            graphstamp.graph_stamp(*c_off),
            graphstamp.graph_stamp(*c_on),
        )
        self.assertNotEqual(
            graphstamp.graph_content_hash(off),
            graphstamp.graph_content_hash(on),
        )


if __name__ == "__main__":
    unittest.main()
