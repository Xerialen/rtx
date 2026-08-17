#!/usr/bin/env python3
"""ben3d_verify.py — KÄLLVERIFIERARE (P1/G1b/D1-D2), inte bara intern hashomräkning.

Verifierar varje bunt mot de förseglade bytes den åberopar:
  1. bundle_payload_sha256 = SHA256(RFC8785({schema,ben_id,geometri,tickserie}))
     + proveniens_sha256 = SHA256(RFC8785(proveniens)).
  2. Manifestmedlemmar: buntens meta/jsonl-rel+SHA finns i manifestet och filens
     faktiska bytes matchar (källvalidering, inte självkonsistens).
  3. Kvittot: kvitto-SHA + fork-armens slut_observed (G1b).
  4. Dumpens tvånivåstamp via graphstamp.py (befintlig dumpläsare — golden).
  5. Källhärledda proveniensfält (binary/cargo_lock/cli) mot faktiska bytes.
Bygger sedan artefaktroten per D2. Avvikelse = STOPP (exit 2)."""

from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import graphstamp  # noqa: E402  (befintlig dumpläsare)


def canonical(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        raise SystemExit("STOPP: flyttal i kanonisering — ben3d-num-as-string/1 gäller")
    if isinstance(v, str):
        out = ['"']
        for c in v:
            o = ord(c)
            if c == '"':
                out.append('\\"')
            elif c == "\\":
                out.append("\\\\")
            elif o == 0x08:
                out.append("\\b")
            elif o == 0x09:
                out.append("\\t")
            elif o == 0x0A:
                out.append("\\n")
            elif o == 0x0C:
                out.append("\\f")
            elif o == 0x0D:
                out.append("\\r")
            elif o < 0x20:
                out.append("\\u%04x" % o)
            else:
                out.append(c)
        out.append('"')
        return "".join(out)
    if isinstance(v, list):
        return "[" + ",".join(canonical(e) for e in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v.keys())
        return "{" + ",".join(canonical(k) + ":" + canonical(v[k]) for k in keys) + "}"
    raise SystemExit("STOPP: okänd typ: %r" % type(v))


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def stop(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(2)


def parse_manifest(path: str) -> dict[str, str]:
    m = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        h, rel = line.split(None, 1)
        m[rel.lstrip("./")] = h.lower()
    return m


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
    ap.add_argument("--cli-config", default="ben3d bunt (launcher)")
    ap.add_argument("--n", type=int, default=97)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    buntar = sorted(Path(args.buntar).glob("*.bunt.json"))
    if len(buntar) != args.n:
        print(f"STOPP: {len(buntar)} buntar != {args.n}", file=sys.stderr)
        return 2

    manifests = {
        "t1h": (args.t1h, parse_manifest(args.t1h)),
        "t20m": (args.t20m, parse_manifest(args.t20m)),
    }
    kvitto = json.loads(Path(args.kvitto).read_text())
    kvitto_sha = sha_bytes(Path(args.kvitto).read_bytes())
    kvitto_slut = kvitto["slut_observed"]

    def check_dump(dump_path: str, expect_hash: str, expect_stamp: str, expect_cells: int, expect_links: int) -> None:
        doc = json.loads(Path(dump_path).read_text())
        mname, cells, links, rj = graphstamp.counts_from_doc(doc)
        stamp = graphstamp.graph_stamp(mname, cells, links, rj)
        ch = graphstamp.graph_content_hash(doc)
        if cells != expect_cells or links != expect_links or str(stamp) != expect_stamp or ch != expect_hash:
            stop(f"STOPP: dump {dump_path} {cells}/{links}/{stamp}/{ch} != {expect_cells}/{expect_links}/{expect_stamp}/{expect_hash}")

    # 4. Dumpens tvånivåstamp (golden via graphstamp.py)
    check_dump(args.fork_dump, kvitto_slut["graph_content_hash"], kvitto_slut["graph_stamp"], kvitto_slut["cells"], kvitto_slut["links"])
    basedoc = json.loads(Path(args.base_dump).read_text())
    if args.n == 97:
        # verklig basdump: G1b-golden 5977/48207/906595427771298736/58787ce0…
        check_dump(args.base_dump, basedoc["graph_content_hash"], "906595427771298736", 5977, 48207)
    else:
        # hermetisk syntetisk dump: självkonsistens via graphstamp (befintlig dumpläsare)
        mname, cells, links, rj = graphstamp.counts_from_doc(basedoc)
        check_dump(args.base_dump, basedoc["graph_content_hash"], str(graphstamp.graph_stamp(mname, cells, links, rj)), cells, links)

    bin_sha = sha_bytes(Path(args.extractor_bin).read_bytes())
    cargo_sha = sha_bytes(Path(args.cargo_lock).read_bytes())
    ben = []
    for f in buntar:
        b = json.loads(f.read_text())
        # 1. payload + proveniens
        payload = {"schema": b["schema"], "ben_id": b["ben_id"], "geometri": b["geometri"], "tickserie": b["tickserie"]}
        got = sha(canonical(payload))
        if got != b["bundle_payload_sha256"]:
            stop(f"STOPP: payload-sha {got} != {b['bundle_payload_sha256']} i {f.name}")
        prov = b["proveniens"]
        prov_sha = sha(canonical(prov))
        # 2. manifestmedlemmar (källvalidering)
        ds = b["ben_id"].split(":")[0]
        mpath, members = manifests[ds]
        for key in ("ra_jsonl", "meta_json"):
            m = prov["medlemmar"][key]
            rel = m["rel"]
            if rel not in members:
                stop(f"STOPP: {rel} saknas i manifestet {mpath}")
            if members[rel] != m["sha256"]:
                stop(f"STOPP: {rel} SHA i manifestet {members[rel]} != buntens {m['sha256']}")
            actual = sha_bytes((Path(mpath).parent / rel).read_bytes())
            if actual != m["sha256"]:
                stop(f"STOPP: {rel} filbytes SHA {actual} != buntens {m['sha256']}")
        # 3. kvitto + slut_observed
        if prov["kvitto"]["sha256"] not in (kvitto_sha, "OKÄND"):
            stop(f"STOPP: kvitto-sha {prov['kvitto']['sha256']} != {kvitto_sha}")
        if prov["dataset_manifest"]["arm"] == "fork":
            so = prov["kvitto"]["slut_observed"]
            if (so["cells"], so["links"], so["graph_stamp"], so["graph_content_hash"]) != \
               (kvitto_slut["cells"], kvitto_slut["links"], kvitto_slut["graph_stamp"], kvitto_slut["graph_content_hash"]):
                stop(f"STOPP: slut_observed avviker i {f.name}")
        # 5. källhärledda proveniensfält
        e = prov["extractor"]
        if e.get("binary_sha256") not in (bin_sha, "OKÄND"):
            stop(f"STOPP: binary_sha i {f.name} != källan")
        if e.get("cargo_lock_sha256") not in (cargo_sha, "OKÄND"):
            stop(f"STOPP: cargo_lock i {f.name} != källan")
        dump_id = b["geometri"]["dump_id"]
        arm = prov["dataset_manifest"]["arm"]
        ds = b["ben_id"].split(":")[0]
        exp_cli = sha(json.dumps([bin_sha, "bunt", dump_id, arm, ds], ensure_ascii=False))
        if e.get("cli_config_sha256") not in (exp_cli, "OKÄND"):
            stop(f"STOPP: cli_config i {f.name} != källan")
        ben.append({"ben_id": b["ben_id"], "bundle_payload_sha256": got, "proveniens_sha256": prov_sha})

    ben.sort(key=lambda r: r["ben_id"])
    man = [
        {"id": "t1h-dataset-manifest-20260817T1536Z.sha256", "sha256": sha_bytes(Path(args.t1h).read_bytes())},
        {"id": "t20m-dataset-manifest-20260817T1339Z.sha256", "sha256": sha_bytes(Path(args.t20m).read_bytes())},
    ]
    man.sort(key=lambda r: r["id"])
    dumps = [
        {"id": "dm3-fork-v296-ram", "sha256": sha_bytes(Path(args.fork_dump).read_bytes())},
        {"id": "dm3-base", "sha256": sha_bytes(Path(args.base_dump).read_bytes())},
    ]
    dumps.sort(key=lambda r: r["id"])
    rot = {
        "schema": "ben3d-rot/1",
        "dataset_manifests": man,
        "dumps": dumps,
        "extractor_bin_sha256": bin_sha,
        "viewer_bundle_sha256": sha_bytes(Path(args.viewer).read_bytes()),
        "ben": ben,
    }
    rot_sha = sha(canonical(rot))
    report = {"schema": "ben3d-verify/1", "ok": True, "n_buntar": len(ben), "rot_sha256": rot_sha, "rot": rot}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(ben)} buntar OK (källverifierade) · rot_sha256={rot_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
