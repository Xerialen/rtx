"""Golden-test mot befintlig dumpläsare (B3): ben3d restore (Rust, motorns
rtx-nav) vs graphstamp.py (befintlig Python-dumpläsare). Hermetisk tmp,
ingen socket, ingen ~/lab-åtkomst."""

from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graphstamp  # noqa: E402

BIN = HERE.parent.parent / "target" / "debug" / "ben3d"


class GoldenTests(unittest.TestCase):
    def test_restore_matches_graphstamp(self):
        if not BIN.is_file():
            self.skipTest("ben3d-binär ej byggd")
        td = Path(tempfile.mkdtemp(prefix="ben3d-golden-"))
        doc = {
            "schema": "qw-nav-graph/1", "map": "dm3", "grid": 32.0,
            "cells": [[0, 0, 0], [32, 0, 0], [0, 32, 0]],
            "cell_ids": [0, 1, 2],
            "links": [
                {"from": 0, "to_cell": 1, "kind": "walk", "T": 1},
                {"from": 1, "to_cell": 0, "kind": "walk", "T": 1},
                {"from": 0, "to_cell": 2, "kind": "speedjump", "T": 0},  # pruned
            ],
            "link_ids": [0, 1, 2],
        }
        doc["graph_content_hash"] = graphstamp.graph_content_hash(doc)
        dump = td / "g.json"
        dump.write_text(json.dumps(doc))
        r = subprocess.run([str(BIN), "restore", str(dump)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = dict(line.split(" ", 1) for line in r.stdout.splitlines() if " " in line)
        mname, cells, links, rj = graphstamp.counts_from_doc(doc)
        want_stamp = str(graphstamp.graph_stamp(mname, cells, links, rj))
        want_hash = graphstamp.graph_content_hash(doc)
        self.assertEqual(out["nivå-1"], want_stamp, "Rust-stamp != graphstamp.py (golden)")
        self.assertEqual(out["nivå-2"], want_hash, "Rust-nivå-2 != graphstamp.py (golden)")
        self.assertEqual(out["cells"], str(cells))
        self.assertEqual(out["links"], str(links))


if __name__ == "__main__":
    unittest.main()
