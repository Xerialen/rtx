#!/usr/bin/env python3
"""Offline tests for fixa (no rig)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import fixa
from d_kvitto import WEST_SHELF_OFF
from d_recipe import REGISTERED_IDS, load_recipe, on_expected


class FakeCtl:
    def __init__(self, reply: dict):
        self.reply = reply
        self.cmds: list[str] = []

    def request(self, cmd: str) -> dict:
        self.cmds.append(cmd)
        return {"ok": True, "data": self.reply}

    def close(self) -> None:
        pass


class FixaTests(unittest.TestCase):
    def test_unknown_recipe_refused(self):
        self.assertEqual(fixa.main(["--recept", "other", "--dry-run", "--port", "27996"]), 2)

    def test_ram_recipes_are_cli_wired(self):
        """P5: ram-rail / ram-prevent must not die as 'west-shelf is the only recipe'."""
        self.assertIn("ram-rail", REGISTERED_IDS)
        self.assertIn("ram-rail-v2", REGISTERED_IDS)
        self.assertIn("ram-prevent", REGISTERED_IDS)
        text = Path(fixa.__file__).read_text(encoding="utf-8")
        self.assertNotIn("west-shelf is the only recipe", text)

    def test_ram_rail_v2_drops_east_not_638(self):
        rec = load_recipe(Path(fixa.__file__).resolve().parent / "recept" / "ram-rail-v2.json")
        self.assertEqual(rec["id"], "ram-rail-v2")
        self.assertIsNone(rec.get("on_expected"))
        self.assertEqual(len(rec["drops"]), 6)
        for d in rec["drops"]:
            self.assertEqual(d["to"][0], -288.0)
            self.assertNotEqual(d["to"], [-352.0, -672.0, -16.0])

    def test_ra_port_refused(self):
        self.assertEqual(fixa.main(["--recept", "west-shelf", "--dry-run", "--port", "27990"]), 2)

    def test_apply_without_on_expected_refused(self):
        recipe = dict(load_recipe())
        recipe["on_expected"] = None
        with self.assertRaises(ValueError):
            on_expected(recipe)

    def test_apply_without_lock_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "no-lock"
            with self.assertRaises(SystemExit) as ctx:
                fixa.require_lock(27996, lock)
            self.assertIn("hold the lock", str(ctx.exception))

    def test_run_fixa_command_shape(self):
        ctl = FakeCtl({"outcome": "dry_run_ok", "recipe": "west-shelf"})
        fixa.run_fixa(ctl, recipe_id="west-shelf", mode="dry-run", from_cell=1, to_cell=2)
        self.assertEqual(ctl.cmds, ["fixa west-shelf dry-run 1 2"])

    def test_apply_command_sends_lock_token(self):
        ctl = FakeCtl({"outcome": "applied", "recipe": "west-shelf"})
        fixa.run_fixa(
            ctl,
            recipe_id="west-shelf",
            mode="apply",
            from_cell=None,
            to_cell=None,
            lock_token="fable",
        )
        self.assertEqual(ctl.cmds, ["fixa west-shelf apply lock fable"])

    def test_dry_run_command_omits_lock_token(self):
        ctl = FakeCtl({"outcome": "dry_run_ok", "recipe": "west-shelf"})
        fixa.run_fixa(
            ctl, recipe_id="west-shelf", mode="dry-run", from_cell=None, to_cell=None
        )
        self.assertEqual(ctl.cmds, ["fixa west-shelf dry-run"])
        self.assertNotIn("lock", ctl.cmds[0])

    def test_kvitto_uses_fixture_on_expected_not_observed(self):
        on = {
            **WEST_SHELF_OFF,
            "cells": 5981,
            "links": 48215,
            "graph_stamp": "123",
            "graph_content_hash": "ab" * 32,
        }
        recipe = {
            "id": "west-shelf",
            "map": "dm3",
            "taxonomy_class": "carve_origin",
            "evidence": "test",
            "off": dict(WEST_SHELF_OFF),
            "on_expected": on,
        }
        observed = dict(on)
        reply = {
            "outcome": "applied",
            "map": "dm3",
            "cells": observed["cells"],
            "links": observed["links"],
            "rj_links": 0,
            "stamp": observed["graph_stamp"],
            "content_hash": observed["graph_content_hash"],
            "astar_before": {"found": True, "cells": [1, 2], "links": [9], "cost": 1.0, "mask_links": []},
            "astar_after": {"found": True, "cells": [1, 3], "links": [8], "cost": 0.5, "mask_links": []},
            "astar_next_best": {"found": True, "cells": [1, 4], "links": [7], "cost": 0.8, "mask_links": [8]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.json"
            doc = fixa.write_apply_kvitto(
                path=path,
                recipe=recipe,
                reply=reply,
                lock_owner="fable",
                lock_path=Path(tmp) / "lock",
                issued_at="2026-08-16T08:00:00+00:00",
                started_at="2026-08-16T08:05:00+00:00",
                ended_at="2026-08-16T08:10:00+00:00",
                host="127.0.0.1",
                ctl_port=27996,
                game_port=27591,
                commit="10d8a54",
                binary_sha256="cd" * 32,
                seed=1,
                stratum={"id": "fixa-apply"},
                raw_pointer=str(Path(tmp) / "raw.jsonl"),
            )
        self.assertEqual(doc["stamps"]["on"]["expected"], on)
        self.assertEqual(doc["stamps"]["on"]["observed"], on)
        self.assertIs(recipe["on_expected"], on)


if __name__ == "__main__":
    unittest.main()
