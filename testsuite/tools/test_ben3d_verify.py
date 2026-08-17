"""Hermetiska fixturtester för ben3d_verify (FIXVARV 2: fullt fail-closed)."""

from __future__ import annotations
import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ben3d"))
sys.path.insert(0, str(HERE))
from ben3d_verify import canonical, sha  # noqa: E402
import graphstamp  # noqa: E402


def _sha_b(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class CanonicalTests(unittest.TestCase):
    def test_rfc8785_vektorer(self):
        self.assertEqual(canonical({"b": 1, "a": "x\"y", "c": [True, None, "n\n"]}),
                         '{"a":"x\\"y","b":1,"c":[true,null,"n\\n"]}')


class VerifyTests(unittest.TestCase):
    def test_kallverifiering_och_stopp(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-verify-"))
        cells = [[0, 0, 0], [32, 0, 0]]
        links = [{"from": 0, "to_cell": 1, "kind": "walk", "T": 1}]
        base_doc = {"cells": cells, "links": links, "cell_ids": [0, 1]}
        base_doc["graph_content_hash"] = graphstamp.graph_content_hash(base_doc)
        fork_doc = dict(base_doc)
        fork_doc["graph_content_hash"] = graphstamp.graph_content_hash(fork_doc)
        (td / "base.json").write_text(json.dumps(fork_doc))
        (td / "fork.json").write_text(json.dumps(fork_doc))
        mname, nc, nl, rj = graphstamp.counts_from_doc(fork_doc)
        kvitto = {"slut_observed": {"cells": nc, "links": nl, "graph_stamp": str(graphstamp.graph_stamp(mname, nc, nl, rj)),
                                    "graph_content_hash": fork_doc["graph_content_hash"]}}
        (td / "kvitto.json").write_text(json.dumps(kvitto))
        meta = json.dumps({"utfall": "fall", "ben": "in_vast", "cykel": 1})
        jsonl = '{"t":1.5,"wall":0,"players":[{"ent":1,"origin":[0,0,0]}]}\n'
        (td / "in_vast_meta.json").write_text(meta)
        (td / "in_vast.jsonl").write_text(jsonl)
        manifest = f"{_sha_b(meta.encode())}  in_vast_meta.json\n{_sha_b(jsonl.encode())}  in_vast.jsonl\n"
        (td / "m.sha256").write_text(manifest)
        for f in ["bin", "viewer.html", "Cargo.lock"]:
            (td / f).write_bytes(b"x")
        bin_sha = _sha_b((td / "bin").read_bytes())
        cargo_sha = _sha_b((td / "Cargo.lock").read_bytes())
        kvitto_sha = _sha_b((td / "kvitto.json").read_bytes())
        fork_sha = _sha_b((td / "fork.json").read_bytes())
        cli_sha = sha(json.dumps([bin_sha, "bunt", "dm3-fork-v296-ram", "fork", "t1h"]))

        buntar = td / "buntar"
        buntar.mkdir()
        for name, ben_id in [("a", "t1h:fork:c001:in_vast"), ("b", "t1h:fork:c002:in_vast")]:
            b = {
                "schema": "ben3d-bunt/1", "ben_id": ben_id,
                "geometri": {"dump_id": "dm3-fork-v296-ram", "dump_sha256": fork_sha, "dump_schema": "qw-nav-graph/1", "arm": "fork", "cells": 2, "links": 1},
                "tickserie": {"farg2_observerad_bana": [{"raw_row_index": 0, "t": "1.5", "x": "0.0", "y": "0.0", "z": "0.0", "cell": 0}],
                              "farg1": [[[0, 0]]], "farg1_formel": "x", "farg1_koder": {"klass": ["a"], "banded_step": ["a"], "chain_entry_blocked": ["a"], "speed_source": ["a", "b"]},
                              "farg3": {"klass": "okänd", "skal": "s", "antal": 0}},
                "proveniens": {
                    "dataset_manifest": {"id": "m.sha256", "sha256": _sha_b(manifest.encode()), "session": "T1h", "arm": "fork", "cycle_id": "c001", "ben_typ": "in_vast"},
                    "medlemmar": {"ra_jsonl": {"rel": "in_vast.jsonl", "sha256": _sha_b(jsonl.encode())},
                                  "meta_json": {"rel": "in_vast_meta.json", "sha256": _sha_b(meta.encode())}},
                    "grafdump": {"id": "dm3-fork-v296-ram", "path": str(td/"fork.json"), "schema": "qw-nav-graph/1", "byte_sha256": fork_sha, "map": "dm3", "cells": 2, "links": 1,
                                 "graph_stamp": str(graphstamp.graph_stamp(mname, nc, nl, rj)), "graph_content_hash": fork_doc["graph_content_hash"]},
                    "kvitto": {"medlem": "kvitto", "sha256": kvitto_sha, "slut_observed": kvitto["slut_observed"]},
                    "extractor": {"commit": "x", "motor_crate_commit": "x", "cargo_lock_sha256": cargo_sha, "binary_sha256": bin_sha, "cli_config_sha256": cli_sha, "restore_schema": "qw-nav-graph/1", "dump_schema": "qw-nav-graph/1"},
                    "matt": {"measure_id": "k", "falls_measure_id": "f", "qwprogs_sha256": "x", "cvarvarden": {}},
                    "viewer": {"commit": "x", "bundle_schema": "ben3d-bunt/1"},
                    "bundle_payload_sha256": "x",
                    "farg1_policy": {"regel": "x", "sj_okand": 0, "non_sj_rackhall": 1, "speed_source": "x"},
                    "main_arm": None,
                },
            }
            payload = {"schema": b["schema"], "ben_id": b["ben_id"], "geometri": b["geometri"], "tickserie": b["tickserie"]}
            b["bundle_payload_sha256"] = sha(canonical(payload))
            b["proveniens"]["bundle_payload_sha256"] = b["bundle_payload_sha256"]
            (buntar / f"{name}.bunt.json").write_text(json.dumps(b))

        args = [sys.executable, str(HERE / "ben3d" / "ben3d_verify.py"),
                "--buntar", str(buntar), "--t1h", str(td / "m.sha256"), "--t20m", str(td / "m.sha256"),
                "--fork-dump", str(td / "fork.json"), "--base-dump", str(td / "base.json"),
                "--kvitto", str(td / "kvitto.json"), "--extractor-bin", str(td / "bin"),
                "--viewer", str(td / "viewer.html"), "--cargo-lock", str(td / "Cargo.lock"), "--n", "2"]
        r = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 buntar OK", r.stdout)
        # korrupt meta-SHA -> källvalidering STOPP
        b = json.loads((buntar / "a.bunt.json").read_text())
        b["proveniens"]["medlemmar"]["meta_json"]["sha256"] = "0" * 64
        (buntar / "a.bunt.json").write_text(json.dumps(b))
        r2 = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r2.returncode, 2)


if __name__ == "__main__":
    unittest.main()
