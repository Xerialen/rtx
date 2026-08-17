#!/usr/bin/env python3
"""ben3d_verify.py — KÄLLVERIFIERARE (P1/G1b/D1-D2), fullt fail-closed.

Verifierar varje bunt mot de förseglade bytes den åberopar (ingen OKÄND för kända
fält). För fork binds kvittot till rokdeploy-kvittot; för MAIN binds kvittot till
basdumpens G1b-identitet (INTE fork-kvittots slut_observed). Kontrollerar dataset-
id/SHA och dump-id/SHA mot de faktiska argumenten samt P1-schemat. STOPP = exit 2."""

from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import graphstamp  # noqa: E402


def canonical(v) -> str:
    if v is None: return "null"
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v, int): return str(v)
    if isinstance(v, float): raise SystemExit("STOPP: flyttal — num-as-string gäller")
    if isinstance(v, str):
        out = ['"']
        for c in v:
            o = ord(c)
            if c == '"': out.append('\\"')
            elif c == "\\": out.append("\\\\")
            elif o == 0x08: out.append("\\b")
            elif o == 0x09: out.append("\\t")
            elif o == 0x0A: out.append("\\n")
            elif o == 0x0C: out.append("\\f")
            elif o == 0x0D: out.append("\\r")
            elif o < 0x20: out.append("\\u%04x" % o)
            else: out.append(c)
        out.append('"')
        return "".join(out)
    if isinstance(v, list): return "[" + ",".join(canonical(e) for e in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v.keys())
        return "{" + ",".join(canonical(k) + ":" + canonical(v[k]) for k in keys) + "}"
    raise SystemExit("STOPP: okänd typ %r" % type(v))


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def stop(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(2)


def git_head() -> str:
    import subprocess
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def parse_manifest(path: str) -> dict[str, str]:
    m = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line: continue
        h, rel = line.split(None, 1)
        m[rel.lstrip("./")] = h.lower()
    return m


P1_KEYS = ["dataset_manifest", "medlemmar", "grafdump", "kvitto", "extractor", "matt", "viewer", "farg1_policy", "bundle_payload_sha256"]

FORK_IDENT = {"cells": 5983, "links": 48216, "graph_stamp": "11908727279900740725",
              "graph_content_hash": "cd800200cad72431e0cbfe0a2fc947bd94309e334103d6cc0abd076155ecf051"}
BASE_IDENT = {"cells": 5977, "links": 48207, "graph_stamp": "906595427771298736",
              "graph_content_hash": "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buntar", required=True)
    ap.add_argument("--t1h", required=True)
    ap.add_argument("--t20m", required=True)
    ap.add_argument("--fork-dump", required=True)
    ap.add_argument("--base-dump", required=True)
    ap.add_argument("--kvitto", required=True)
    ap.add_argument("--extractor-bin", required=True)
    ap.add_argument("--viewer", required=True)
    ap.add_argument("--cargo-lock", required=True)
    ap.add_argument("--revision", default=None, help="ren byggrevision (override; default: h-index.json build_revision)")
    ap.add_argument("--index", default=None, help="h-index.json (default: <buntar>/h-index.json)")
    ap.add_argument("--n", type=int, default=97)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    buntar = sorted(Path(args.buntar).glob("*.bunt.json"))
    if len(buntar) != args.n:
        stop(f"STOPP: {len(buntar)} buntar != {args.n}")
    index_path = Path(args.index) if args.index else (Path(args.buntar) / "h-index.json")
    build_rev = ""
    if index_path.is_file():
        build_rev = json.loads(index_path.read_text()).get("build_revision", "")
    revision = args.revision or build_rev or git_head()
    if not revision:
        stop("STOPP: ingen byggrevision (--revision/h-index.build_revision/git HEAD)")
    # viewerns inbäddade VIEWER_COMMIT måste matcha byggrevisionen
    viewer_text = Path(args.viewer).read_text()
    import re as _re
    m = _re.search(r'const VIEWER_COMMIT = "([0-9a-f]{40})"', viewer_text)
    if not m or m.group(1) != revision:
        stop(f"STOPP: viewer VIEWER_COMMIT {m.group(1) if m else '?'} != byggrevision {revision}")

    manifests = {"t1h": (args.t1h, parse_manifest(args.t1h)), "t20m": (args.t20m, parse_manifest(args.t20m))}
    kvitto = json.loads(Path(args.kvitto).read_text())
    kvitto_sha = sha_bytes(Path(args.kvitto).read_bytes())
    fork_dump_sha = sha_bytes(Path(args.fork_dump).read_bytes())
    base_dump_sha = sha_bytes(Path(args.base_dump).read_bytes())

    def check_dump(p, cells, links, stamp, h):
        doc = json.loads(Path(p).read_text())
        mname, c, l, rj = graphstamp.counts_from_doc(doc)
        s = graphstamp.graph_stamp(mname, c, l, rj)
        ch = graphstamp.graph_content_hash(doc)
        if c != cells or l != links or str(s) != stamp or ch != h:
            stop(f"STOPP: dump {p} {c}/{l}/{s}/{ch} != {cells}/{links}/{stamp}/{h}")

    kvitto_slut = kvitto["slut_observed"]
    # dump-tvånivåstamp via graphstamp.py (golden). Fork binds till KVITTOTS slut_observed;
    # basdumpen till BASE_IDENT endast vid verklig körning (n=97), annars självkonsistens.
    check_dump(args.fork_dump, kvitto_slut["cells"], kvitto_slut["links"], kvitto_slut["graph_stamp"], kvitto_slut["graph_content_hash"])
    if args.n == 97:
        check_dump(args.base_dump, BASE_IDENT["cells"], BASE_IDENT["links"], BASE_IDENT["graph_stamp"], BASE_IDENT["graph_content_hash"])
    else:
        bd = json.loads(Path(args.base_dump).read_text())
        ch = graphstamp.graph_content_hash(bd)
        if ch != bd["graph_content_hash"]:
            stop(f"STOPP: basdump självkonsistens {ch} != {bd['graph_content_hash']}")

    bin_sha = sha_bytes(Path(args.extractor_bin).read_bytes())
    cargo_sha = sha_bytes(Path(args.cargo_lock).read_bytes())

    ben = []
    for f in buntar:
        b = json.loads(f.read_text())
        payload = {"schema": b["schema"], "ben_id": b["ben_id"], "geometri": b["geometri"], "tickserie": b["tickserie"]}
        got = sha(canonical(payload))
        if got != b["bundle_payload_sha256"]:
            stop(f"STOPP: payload-sha {got} != {b['bundle_payload_sha256']} i {f.name}")
        prov = b["proveniens"]
        prov_sha = sha(canonical(prov))
        # P1-schema
        for k in P1_KEYS:
            if k not in prov:
                stop(f"STOPP: P1-schema saknar {k} i {f.name}")
        # dataset-id/SHA mot faktiska manifest
        ds = b["ben_id"].split(":")[0]
        mpath, members = manifests[ds]
        if prov["dataset_manifest"]["id"] != Path(mpath).name:
            stop(f"STOPP: dataset-id {prov['dataset_manifest']['id']} != {Path(mpath).name}")
        if prov["dataset_manifest"]["sha256"] != sha_bytes(Path(mpath).read_bytes()):
            stop(f"STOPP: dataset-sha i {f.name} != faktisk manifestbyte-sha")
        # medlemmar (källvalidering)
        for key in ("ra_jsonl", "meta_json"):
            m = prov["medlemmar"][key]
            if m["rel"] not in members:
                stop(f"STOPP: {m['rel']} saknas i manifestet")
            if members[m["rel"]] != m["sha256"]:
                stop(f"STOPP: {m['rel']} SHA i manifestet != buntens")
            if sha_bytes((Path(mpath).parent / m["rel"]).read_bytes()) != m["sha256"]:
                stop(f"STOPP: {m['rel']} filbytes SHA != buntens")
        # dump-id/SHA mot faktiska argument
        arm = prov["dataset_manifest"]["arm"]
        dump_id = b["geometri"]["dump_id"]
        dump_sha = b["geometri"]["dump_sha256"]
        if arm == "fork":
            if dump_id != "dm3-fork-v296-ram" or dump_sha != fork_dump_sha:
                stop(f"STOPP: fork-dump id/sha i {f.name} != faktisk")
        else:
            if dump_id != "dm3-base" or dump_sha != base_dump_sha:
                stop(f"STOPP: main-dump id/sha i {f.name} != faktisk basdump")
        # kvitto: fork -> rokdeploy-kvitto; main -> basdumpens G1b-identitet
        so = prov["kvitto"]["slut_observed"]
        if arm == "fork":
            if prov["kvitto"]["sha256"] != kvitto_sha:
                stop(f"STOPP: kvitto-sha i {f.name} != faktisk")
            if (so["cells"], so["links"], so["graph_stamp"], so["graph_content_hash"]) != \
               (kvitto_slut["cells"], kvitto_slut["links"], kvitto_slut["graph_stamp"], kvitto_slut["graph_content_hash"]):
                stop(f"STOPP: fork slut_observed avviker i {f.name}")
        else:
            if prov["kvitto"]["sha256"] != base_dump_sha:
                stop(f"STOPP: main-kvitto-sha i {f.name} != basdumpens byte-sha")
            if (so["cells"], so["links"], so["graph_stamp"], so["graph_content_hash"]) != \
               (BASE_IDENT["cells"], BASE_IDENT["links"], BASE_IDENT["graph_stamp"], BASE_IDENT["graph_content_hash"]):
                stop(f"STOPP: main slut_observed avviker i {f.name}")
        # kända fält får INTE vara OKÄND
        e = prov["extractor"]
        if e.get("binary_sha256") != bin_sha:
            stop(f"STOPP: binary_sha256 i {f.name} != faktisk ({e.get('binary_sha256')})")
        if e.get("cargo_lock_sha256") != cargo_sha:
            stop(f"STOPP: cargo_lock_sha256 i {f.name} != faktisk")
        exp_cli = sha(json.dumps([bin_sha, "bunt", dump_id, arm, ds], ensure_ascii=False))
        if e.get("cli_config_sha256") != exp_cli:
            stop(f"STOPP: cli_config_sha256 i {f.name} != faktisk")
        if e.get("commit") != revision:
            stop(f"STOPP: extractor.commit {e.get('commit')} != byggrevision {revision} i {f.name}")
        if prov["viewer"].get("commit") != revision:
            stop(f"STOPP: viewer.commit {prov['viewer'].get('commit')} != byggrevision {revision} i {f.name}")
        ben.append({"ben_id": b["ben_id"], "bundle_payload_sha256": got, "proveniens_sha256": prov_sha})

    ben.sort(key=lambda r: r["ben_id"])
    man = [
        {"id": "t1h-dataset-manifest-20260817T1536Z.sha256", "sha256": sha_bytes(Path(args.t1h).read_bytes())},
        {"id": "t20m-dataset-manifest-20260817T1339Z.sha256", "sha256": sha_bytes(Path(args.t20m).read_bytes())},
    ]
    man.sort(key=lambda r: r["id"])
    dumps = [
        {"id": "dm3-fork-v296-ram", "sha256": fork_dump_sha},
        {"id": "dm3-base", "sha256": base_dump_sha},
    ]
    dumps.sort(key=lambda r: r["id"])
    rot = {"schema": "ben3d-rot/1", "dataset_manifests": man, "dumps": dumps,
           "extractor_bin_sha256": bin_sha, "viewer_bundle_sha256": sha_bytes(Path(args.viewer).read_bytes()), "ben": ben}
    rot_sha = sha(canonical(rot))
    report = {"schema": "ben3d-verify/1", "ok": True, "n_buntar": len(ben), "rot_sha256": rot_sha, "rot": rot}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(ben)} buntar OK (källverifierade, fail-closed) · rot_sha256={rot_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
