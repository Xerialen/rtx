"""Hermetiska fixturtester för ben3d_verify (FIXVARV 1: källverifierare).
Ingen socket, ingen ~/lab-åtkomst."""

from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ben3d"))
sys.path.insert(0, str(HERE))
from ben3d_verify import canonical, sha  # noqa: E402
import graphstamp  # noqa: E402


def _sha_b(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def _dump(cells, links, path):
    doc = {"schema": "qw-nav-graph/1", "map": "dm3", "grid": 32.0,
           "cells": cells, "links": links,
           "cell_ids": list(range(len(cells))), "link_ids": list(range(len(links))),
           "graph_content_hash": graphstamp.graph_content_hash(
               {"cells": cells, "links": links, "cell_ids": list(range(len(cells)))})}
    path.write_text(json.dumps(doc))
    return doc


class CanonicalTests(unittest.TestCase):
    def test_rfc8785_vektorer(self):
        self.assertEqual(canonical({"b": 1, "a": "x\"y", "c": [True, None, "n\n"]}),
                         '{"a":"x\\"y","b":1,"c":[true,null,"n\\n"]}')

    def test_flyttal_vagras(self):
        with self.assertRaises(SystemExit):
            canonical({"a": 1.5})


class VerifyTests(unittest.TestCase):
    def test_kallverifiering_och_stopp(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-verify-"))
        cells = [[0, 0, 0], [32, 0, 0]]
        links = [{"from": 0, "to_cell": 1, "kind": "walk", "T": 1}]
        fork = _dump(cells, links, td / "fork.json")
        base = _dump(cells, links, td / "base.json")
        mname, nc, nl, rj = graphstamp.counts_from_doc(fork)
        kvitto = {"slut_observed": {"cells": nc, "links": nl, "graph_stamp": str(graphstamp.graph_stamp(mname, nc, nl, rj)),
                                    "graph_content_hash": fork["graph_content_hash"]}}
        (td / "kvitto.json").write_text(json.dumps(kvitto))

        meta = json.dumps({"utfall": "fall", "ben": "in_vast", "cykel": 1})
        jsonl = '{"t":1.5,"wall":0,"players":[{"ent":1,"origin":[0,0,0]}]}\n'
        (td / "in_vast_meta.json").write_text(meta)
        (td / "in_vast.jsonl").write_text(jsonl)
        manifest = f"{_sha_b(meta.encode())}  in_vast_meta.json\n{_sha_b(jsonl.encode())}  in_vast.jsonl\n"
        (td / "m.sha256").write_text(manifest)

        buntar = td / "buntar"
        buntar.mkdir()
        for name, ben_id in [("a", "t1h:fork:c001:in_vast"), ("b", "t1h:fork:c002:in_vast")]:
            b = {
                "schema": "ben3d-bunt/1", "ben_id": ben_id,
                "geometri": {"dump_id": "dm3-fork-v296-ram", "dump_sha256": _sha_b(fork.__str__().encode()), "dump_schema": "qw-nav-graph/1", "arm": "fork", "cells": 2, "links": 1},
                "tickserie": {"farg2_observerad_bana": [{"raw_row_index": 0, "t": "1.5", "x": "0.0", "y": "0.0", "z": "0.0", "cell": 0}],
                              "farg1": [], "farg3": {"klass": "okänd", "skal": "s", "antal": 0}},
                "proveniens": {
                    "dataset_manifest": {"id": "m.sha256", "sha256": _sha_b(manifest.encode()), "session": "T1h", "arm": "fork", "cycle_id": "c001", "ben_typ": "in_vast"},
                    "medlemmar": {"ra_jsonl": {"rel": "in_vast.jsonl", "sha256": _sha_b(jsonl.encode())},
                                  "meta_json": {"rel": "in_vast_meta.json", "sha256": _sha_b(meta.encode())}},
                    "grafdump": {"id": "dm3-fork-v296-ram", "path": str(fork), "schema": "qw-nav-graph/1", "byte_sha256": _sha_b((td/"fork.json").read_bytes()), "map": "dm3", "cells": 2, "links": 1,
                                 "graph_stamp": str(graphstamp.graph_stamp(mname, nc, nl, rj)), "graph_content_hash": fork["graph_content_hash"]},
                    "kvitto": {"medlem": "kvitto", "sha256": _sha_b((td/"kvitto.json").read_bytes()), "slut_observed": kvitto["slut_observed"]},
                    "extractor": {"commit": "x", "motor_crate_commit": "x", "cargo_lock_sha256": "OKÄND", "binary_sha256": "OKÄND", "cli_config_sha256": "OKÄND", "restore_schema": "qw-nav-graph/1", "dump_schema": "qw-nav-graph/1"},
                    "matt": {"measure_id": "k", "falls_measure_id": "f", "qwprogs_sha256": "x", "cvarvarden": {}},
                    "viewer": {"commit": "x", "bundle_schema": "ben3d-bunt/1"},
                    "farg1_policy": {"regel": "x", "sj_okand": 0, "non_sj_rackhall": 1},
                    "main_arm": None,
                },
            }
            payload = {"schema": b["schema"], "ben_id": b["ben_id"], "geometri": b["geometri"], "tickserie": b["tickserie"]}
            b["bundle_payload_sha256"] = sha(canonical(payload))
            (buntar / f"{name}.bunt.json").write_text(json.dumps(b))

        args = [sys.executable, str(HERE / "ben3d" / "ben3d_verify.py"),
                "--buntar", str(buntar), "--t1h", str(td / "m.sha256"), "--t20m", str(td / "m.sha256"),
                "--fork-dump", str(td / "fork.json"), "--base-dump", str(td / "base.json"),
                "--kvitto", str(td / "kvitto.json"), "--extractor-bin", str(td / "bin"),
                "--viewer", str(td / "viewer.html"), "--cargo-lock", str(td / "Cargo.lock"), "--n", "2"]
        for f in ["bin", "viewer.html", "Cargo.lock"]:
            (td / f).write_bytes(b"x")
        r = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 buntar OK", r.stdout)
        # korrupta en meta-SHA -> källvalidering STOPP
        b = json.loads((buntar / "a.bunt.json").read_text())
        b["proveniens"]["medlemmar"]["meta_json"]["sha256"] = "0" * 64
        (buntar / "a.bunt.json").write_text(json.dumps(b))
        r2 = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r2.returncode, 2)
        self.assertIn("STOPP", r2.stderr)


if __name__ == "__main__":
    unittest.main()
